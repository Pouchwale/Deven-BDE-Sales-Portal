"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Whether the desktop sidebar is hidden, remembered per browser.
 *
 * Deliberately `useSyncExternalStore` rather than `useState` + an effect.
 * `localStorage` does not exist while the page is rendered on the server, so
 * the two obvious approaches both go wrong: seeding state from it during
 * render hydrates one layout and then swaps to another, and reading it in an
 * effect means a `setState` on every mount, which is both a wasted render and
 * a lint error. This hook is the primitive React provides for exactly that
 * shape - a value the server cannot know, read once, without a flash.
 *
 * The preference is per-viewer and never leaves the browser. It is a
 * convenience, so every access is wrapped: a private window, cleared site
 * data, or a browser set to block storage all throw here, and the answer in
 * every one of those cases is "not collapsed" rather than a crash.
 */
const KEY = "bde_portal_nav_collapsed";

/** Cached so getSnapshot returns a stable value between store changes. */
let snapshot: boolean | null = null;
let listeners: Array<() => void> = [];

function read(): boolean {
  if (snapshot === null) {
    try {
      snapshot = window.localStorage.getItem(KEY) === "1";
    } catch {
      snapshot = false;
    }
  }
  return snapshot;
}

function subscribe(onChange: () => void): () => void {
  listeners = [...listeners, onChange];
  return () => {
    listeners = listeners.filter((listener) => listener !== onChange);
  };
}

/** The sidebar is always expanded in server-rendered HTML. */
function serverSnapshot(): boolean {
  return false;
}

export function useNavCollapsed(): [boolean, () => void] {
  const collapsed = useSyncExternalStore(subscribe, read, serverSnapshot);

  const toggle = useCallback(() => {
    snapshot = !read();
    try {
      window.localStorage.setItem(KEY, snapshot ? "1" : "0");
    } catch {
      // Not remembering the choice is a far smaller problem than throwing
      // inside a click handler.
    }
    for (const listener of listeners) listener();
  }, []);

  return [collapsed, toggle];
}
