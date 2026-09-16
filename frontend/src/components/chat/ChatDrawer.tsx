"use client";

import { Clock, Plus, Trash2, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { AssistantMark } from "@/components/chat/AssistantMark";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatEmptyState } from "@/components/chat/ChatEmptyState";
import { ChatUnconfigured } from "@/components/chat/ChatUnconfigured";
import { MessageList } from "@/components/chat/MessageList";
import { Button } from "@/components/ui/Button";
import {
  composerPlaceholder,
  orderSuggestions,
  workloadLine,
} from "@/lib/assistantContext";
import { useChat } from "@/lib/chat";
import { useKeyboardInset } from "@/lib/useKeyboardInset";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import type { User } from "@/types/api";

/** Matches --animate-slide-out-right. */
const EXIT_MS = 200;

export function ChatDrawer({ user }: { user: User }) {
  const {
    open,
    setOpen,
    enabled,
    setup,
    turns,
    suggestions,
    busy,
    send,
    stop,
    reset,
    history,
    load,
    remove,
    dashboard,
  } = useChat();
  const [showHistory, setShowHistory] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const pathname = usePathname();

  // On a phone the sheet is pinned to the bottom edge, and the keyboard would
  // otherwise cover the very input it just opened for. Zero on desktop.
  const keyboardInset = useKeyboardInset(open);

  // Reordering only - the list arrives already filtered to this role, and
  // the tool dispatcher re-checks every call regardless.
  const ordered = useMemo(
    () => orderSuggestions(suggestions, pathname),
    [suggestions, pathname],
  );
  const workload = useMemo(() => workloadLine(dashboard), [dashboard]);
  const placeholder = useMemo(() => composerPlaceholder(pathname), [pathname]);

  // Same derived-on-prop-change pattern as Modal: the panel outlives `open`
  // by one exit animation so closing is a movement, not a disappearance.
  const [previousOpen, setPreviousOpen] = useState(open);
  const [closing, setClosing] = useState(false);

  if (previousOpen !== open) {
    setPreviousOpen(open);
    setClosing(!open);
    if (!open) setShowHistory(false);
  }

  useEffect(() => {
    if (!closing) return;
    const timer = window.setTimeout(() => setClosing(false), EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [closing]);

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    // Clicking anywhere outside closes it, the way every panel of this shape
    // behaves. `mousedown` rather than `click`: a click that STARTS inside
    // the panel and drifts out - selecting an answer to copy it - must not
    // count as leaving.
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      // The launcher owns its own toggle; letting this fire too would close
      // and immediately reopen.
      if ((target as Element).closest?.('[aria-label="Ask the assistant"]')) return;
      setOpen(false);
    };

    document.addEventListener("keydown", onKeyDown);
    // Capture phase, so a stopPropagation deeper in the page cannot swallow it.
    document.addEventListener("mousedown", onPointerDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown, true);
    };
  }, [open, setOpen]);

  const mounted = open || closing;
  if (!mounted || typeof document === "undefined") return null;

  return createPortal(
    <>
      {/* Mobile only. On a wider screen the page behind stays live and
          clickable - dimming it would make the assistant modal, and reading
          the answer against the numbers it came from is the whole point. */}
      <div
        className={cn(
          "fixed inset-0 z-40 bg-black/35 backdrop-blur-[1px] sm:hidden",
          closing ? "animate-fade-out" : "animate-fade-in",
        )}
        onMouseDown={() => setOpen(false)}
      />

      <aside
        ref={panelRef}
        role="dialog"
        aria-label="Portal assistant"
        style={
          // Only the phone layout is bottom-anchored; above `sm` the panel is
          // full-height at the side and the keyboard is irrelevant.
          keyboardInset > 0
            ? ({ "--keyboard-inset": `${keyboardInset}px` } as React.CSSProperties)
            : undefined
        }
        className={cn(
          "fixed z-50 flex flex-col border-line bg-surface card-shadow-lg",
          // Phone: a bottom sheet that leaves the top of the page visible, so
          // the assistant never buries where you were. Tablet and up: the
          // side panel, which can sit beside the content instead of over it.
          "inset-x-0 bottom-[var(--keyboard-inset,0px)] top-[12dvh] rounded-t-2xl border-x border-t",
          "sm:bottom-0",
          "sm:inset-y-0 sm:left-auto sm:right-0 sm:top-0 sm:w-[400px]",
          "sm:rounded-none sm:border-x-0 sm:border-t-0 sm:border-l",
          closing
            ? "animate-slide-out-down sm:animate-slide-out-right"
            : "animate-slide-in-up sm:animate-slide-in-right",
        )}
      >
        {/* Phone only: the affordance that says "this is a sheet". */}
        <div className="flex justify-center pt-2 sm:hidden" aria-hidden>
          <span className="h-1 w-9 rounded-full bg-line-strong" />
        </div>

        {/* ------------------------------------------------------- header */}
        <header className="flex items-center gap-2 border-b border-line px-3.5 py-3">
          <div
            className={cn(
              "grid size-7 place-items-center rounded-lg",
              "bg-brand-50 text-brand-600 dark:bg-brand-950 dark:text-brand-300",
            )}
          >
            <AssistantMark className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13.5px] font-semibold text-content">Assistant</p>
            <p className="truncate text-[11.5px] text-subtle">Answers from your own data</p>
          </div>

          <Button
            variant="ghost"
            size="icon"
            // Nothing to list and nothing to reset until it can answer.
            className={cn(
              !enabled && "hidden",
              showHistory && "bg-surface-hover text-content",
            )}
            aria-label={showHistory ? "Back to chat" : "Past conversations"}
            title="Past conversations"
            onClick={() => setShowHistory((value) => !value)}
          >
            <Clock className="size-4" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className={cn(!enabled && "hidden")}
            aria-label="New conversation"
            title="New conversation"
            onClick={() => {
              reset();
              setShowHistory(false);
            }}
          >
            <Plus className="size-4" aria-hidden />
          </Button>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={() => setOpen(false)}>
            <X className="size-4" aria-hidden />
          </Button>
        </header>

        {/* -------------------------------------------------------- body */}
        {showHistory ? (
          <div className="flex-1 overflow-y-auto px-3 py-3">
            {history.length === 0 ? (
              <p className="px-1 py-6 text-center text-[13px] text-subtle">
                No past conversations yet.
              </p>
            ) : (
              <ul className="space-y-1">
                {history.map((conversation, index) => (
                  <li
                    key={conversation.id}
                    style={{ animationDelay: `${index * 35}ms` }}
                    className="stagger group flex items-center gap-1"
                  >
                    <button
                      type="button"
                      onClick={() => {
                        void load(conversation.id);
                        setShowHistory(false);
                      }}
                      className={cn(
                        "min-w-0 flex-1 rounded-lg px-2.5 py-2 text-left transition-colors",
                        "hover:bg-surface-hover",
                      )}
                    >
                      <span className="block truncate text-[13px] text-content">
                        {conversation.title ?? "Untitled"}
                      </span>
                      <span className="block text-[11px] text-subtle">
                        {formatRelative(conversation.updated_at)}
                      </span>
                    </button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Delete conversation"
                      className="opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
                      onClick={() => void remove(conversation.id)}
                    >
                      <Trash2 className="size-3.5" aria-hidden />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : !enabled ? (
          <div className="flex-1 overflow-y-auto">
            <ChatUnconfigured setup={setup} />
          </div>
        ) : turns.length === 0 ? (
          <div className="flex-1 overflow-y-auto">
            <ChatEmptyState
              name={user.name}
              suggestions={ordered}
              workload={workload}
              onPick={send}
            />
          </div>
        ) : (
          <MessageList turns={turns} role={user.role} onNavigate={() => setOpen(false)} />
        )}

        {/* No composer without a key: every send would be a 503, and a dead
            input is a worse answer than an explanation. */}
        {!showHistory && enabled ? (
          <ChatComposer
            busy={busy}
            onSend={send}
            onStop={stop}
            placeholder={placeholder}
          />
        ) : null}
      </aside>
    </>,
    document.body,
  );
}
