"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { ApiError, api, errorMessage, streamChat } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  ChatSetup,
  ChatStatus,
  Conversation,
  Dashboard,
  ResultCard,
  Suggestion,
} from "@/types/api";

/** One line in the transcript. Streaming assistant turns are `pending`. */
export interface Turn {
  id: string;
  role: "USER" | "ASSISTANT";
  content: string;
  pending?: boolean;
  /** What the assistant is looking up right now, if anything. */
  activity?: string | null;
  /** Lookups that ran for this turn, in order — shown once the answer lands. */
  used?: { name: string; label: string }[];
  /** Things worth acting on that the lookups turned up. Ephemeral: they are
   *  not persisted, so a reopened conversation shows prose only. */
  cards?: ResultCard[];
  error?: string | null;
}

interface ChatContextValue {
  /** Render the launcher at all. */
  available: boolean;
  /** Accept a message. False means configured-but-keyless: show the setup
   *  state rather than a composer that could only ever 503. */
  enabled: boolean;
  /** Administrators only; null for everyone else. Never contains the key. */
  setup: ChatSetup | null;
  /** False until /chat/status has answered once for this session. */
  statusLoaded: boolean;
  open: boolean;
  setOpen: (open: boolean) => void;
  turns: Turn[];
  suggestions: Suggestion[];
  /** The caller's own dashboard figures, for the opening line. Fetched once
   *  per session, lazily, the first time the drawer is opened - and never on
   *  a page that does not show the assistant. */
  dashboard: Dashboard | null;
  busy: boolean;
  conversationId: string | null;
  history: Conversation[];
  send: (message: string) => void;
  stop: () => void;
  reset: () => void;
  load: (id: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  refreshHistory: () => Promise<void>;
}

const ChatContext = createContext<ChatContextValue | null>(null);

let localId = 0;
const nextId = () => `local-${++localId}`;

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [status, setStatus] = useState<ChatStatus>({ available: false, enabled: false, suggestions: [], setup: null });
  // So Admin > Settings can show a skeleton until the answer is in, instead of
  // asking /chat/status a second time for itself.
  const [statusLoaded, setStatusLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [history, setHistory] = useState<Conversation[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // A different person means a different scope. Nothing from the previous
  // session may survive the switch — not on screen, not in the next request.
  // Derived as the value changes rather than in an effect, so the stale
  // transcript never gets a frame to render in.
  const userId = user?.id ?? null;
  const [lastUserId, setLastUserId] = useState(userId);

  if (lastUserId !== userId) {
    setLastUserId(userId);
    setStatus({ available: false, enabled: false, suggestions: [], setup: null });
    setStatusLoaded(false);
    setTurns([]);
    setConversationId(null);
    setHistory([]);
    setDashboard(null);
    setBusy(false);
  }

  // Cutting a live stream is a side effect on something outside React, so it
  // happens here rather than in render. The cleanup fires as `userId`
  // changes, which is exactly when the old stream stops being anyone's.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [userId]);

  // Whether the assistant exists at all is a server decision (a key has to be
  // configured), so ask once per signed-in session rather than assuming.
  useEffect(() => {
    if (!userId) return;
    let active = true;
    const controller = new AbortController();

    async function ask() {
      try {
        const next = await api.chat.status(controller.signal);
        if (active) setStatus(next);
      } catch {
        /* off is the safe default; the launcher simply will not render */
      } finally {
        if (active) setStatusLoaded(true);
      }
    }

    void ask();
    return () => {
      active = false;
      controller.abort();
    };
  }, [userId]);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.chat.conversations());
    } catch {
      /* the past-conversations list is a convenience, not the feature */
    }
  }, []);

  // One request, the first time somebody opens the assistant, reused for the
  // rest of the session. It is the same endpoint the dashboard page calls, so
  // the shape is already familiar; what it buys is an opening line that knows
  // what is on your plate instead of a generic hello.
  useEffect(() => {
    if (!open || !status.enabled || dashboard !== null) return;
    let active = true;

    async function loadContext() {
      try {
        const next = await api.dashboard();
        if (active) setDashboard(next);
      } catch {
        /* the greeting simply stays generic - not worth surfacing */
      }
    }

    void loadContext();
    return () => {
      active = false;
    };
  }, [open, status.enabled, dashboard]);

  // Only while the drawer is open: nobody needs this list behind a closed panel.
  useEffect(() => {
    if (!open || !status.enabled) return;
    let active = true;

    async function load() {
      try {
        const rows = await api.chat.conversations();
        if (active) setHistory(rows);
      } catch {
        /* as above */
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [open, status.enabled]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setTurns((current) =>
      current.map((turn) =>
        turn.pending
          ? {
              ...turn,
              pending: false,
              activity: null,
              content: turn.content || "Stopped.",
            }
          : turn,
      ),
    );
  }, []);

  const send = useCallback(
    (message: string) => {
      const text = message.trim();
      if (!text || busy) return;

      const controller = new AbortController();
      abortRef.current = controller;
      setBusy(true);

      const replyId = nextId();
      setTurns((current) => [
        ...current,
        { id: nextId(), role: "USER", content: text },
        { id: replyId, role: "ASSISTANT", content: "", pending: true, used: [] },
      ]);

      const patch = (change: Partial<Turn>) =>
        setTurns((current) =>
          current.map((turn) => (turn.id === replyId ? { ...turn, ...change } : turn)),
        );

      void (async () => {
        const used: { name: string; label: string }[] = [];
        const cards: ResultCard[] = [];
        let text_ = "";

        try {
          for await (const event of streamChat(text, conversationId, controller.signal)) {
            switch (event.type) {
              case "start":
                // Deliberately NOT adopted here. A turn that fails is rolled
                // back server-side, taking a brand-new conversation with it -
                // so holding this id would make the next message 404 against
                // a row that no longer exists. `done` is when it is real.
                break;
              case "delta":
                text_ += event.text;
                patch({ content: text_, activity: null });
                break;
              case "tool":
                used.push({ name: event.name, label: event.label });
                patch({ activity: event.label, used: [...used] });
                break;
              case "cards":
                cards.push(...event.items);
                patch({ cards: [...cards] });
                break;
              case "error":
                patch({ pending: false, activity: null, error: event.message });
                break;
              case "done":
                setConversationId(event.conversation_id);
                patch({ pending: false, activity: null, content: text_ });
                void refreshHistory();
                break;
            }
          }
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
          patch({
            pending: false,
            activity: null,
            error:
              error instanceof ApiError && error.status === 429
                ? error.message
                : errorMessage(error),
          });
        } finally {
          if (abortRef.current === controller) {
            abortRef.current = null;
            setBusy(false);
          }
        }
      })();
    },
    [busy, conversationId, refreshHistory],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setTurns([]);
    setConversationId(null);
  }, []);

  const load = useCallback(async (id: string) => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    try {
      const detail = await api.chat.conversation(id);
      setConversationId(detail.id);
      setTurns(
        detail.messages.map((message) => ({
          id: message.id,
          role: message.role,
          content: message.content,
        })),
      );
    } catch {
      setConversationId(null);
      setTurns([]);
    }
  }, []);

  const remove = useCallback(
    async (id: string) => {
      try {
        await api.chat.remove(id);
      } catch {
        return;
      }
      setHistory((current) => current.filter((row) => row.id !== id));
      if (conversationId === id) {
        setConversationId(null);
        setTurns([]);
      }
    },
    [conversationId],
  );

  const value = useMemo<ChatContextValue>(
    () => ({
      available: status.available,
      enabled: status.enabled,
      setup: status.setup,
      statusLoaded,
      open,
      setOpen,
      turns,
      suggestions: status.suggestions,
      dashboard,
      busy,
      conversationId,
      history,
      send,
      stop,
      reset,
      load,
      remove,
      refreshHistory,
    }),
    [
      status,
      statusLoaded,
      dashboard,
      open,
      turns,
      busy,
      conversationId,
      history,
      send,
      stop,
      reset,
      load,
      remove,
      refreshHistory,
    ],
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat(): ChatContextValue {
  const value = useContext(ChatContext);
  if (!value) throw new Error("useChat must be used inside <ChatProvider>.");
  return value;
}
