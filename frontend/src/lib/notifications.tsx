"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api } from "@/lib/api";

interface NotificationsContextValue {
  unread: number;
  /** Re-poll now, e.g. straight after marking something read. */
  refresh: () => void;
}

const NotificationsContext = createContext<NotificationsContextValue>({
  unread: 0,
  refresh: () => {},
});

/** Plan v3 s10: a bell with an unread count on a 60-second poll. */
const POLL_MS = 60_000;

export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const [unread, setUnread] = useState(0);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    async function poll() {
      try {
        // This endpoint also tops up the lazily generated reference
        // follow-up reminders, which is why polling it matters.
        const { unread: count } = await api.notifications.unreadCount(controller.signal);
        if (active) setUnread(count);
      } catch {
        /* a failed poll is not worth surfacing; the next one will do */
      }
    }

    void poll();
    const timer = window.setInterval(poll, POLL_MS);

    return () => {
      active = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [nonce]);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);
  // Memoised like every other provider in the app: this one is mounted around
  // the WHOLE portal, so a fresh object on each render re-rendered every
  // consumer - the sidebar, the topbar and the page under them - once a minute.
  const value = useMemo(() => ({ unread, refresh }), [unread, refresh]);

  return (
    <NotificationsContext.Provider value={value}>
      {children}
    </NotificationsContext.Provider>
  );
}

export function useNotifications(): NotificationsContextValue {
  return useContext(NotificationsContext);
}
