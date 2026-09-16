"use client";

import { useCallback, useSyncExternalStore } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "bde_portal_theme";
const EVENT = "bde-portal-theme-change";

/**
 * Applied by an inline script in the document head before first paint, so
 * there is no flash of the wrong theme.
 */
export const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem(${JSON.stringify(STORAGE_KEY)});
    var prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    var theme = stored === "light" || stored === "dark" ? stored : (prefersDark ? "dark" : "light");
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.dataset.theme = theme;
  } catch (e) {}
})();
`;

/* --------------------------------------------------------------- store */
// The <html> class is the single source of truth — the init script above
// already set it. React SUBSCRIBES to that rather than keeping a second copy
// in state, which is what useSyncExternalStore is for and what avoids
// setting state from an effect on mount.

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

function getSnapshot(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function getServerSnapshot(): Theme {
  // The server cannot know; the init script corrects the DOM before paint.
  return "light";
}

function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* a browser blocking site data should not break the toggle */
  }
  window.dispatchEvent(new Event(EVENT));
}

/* ------------------------------------------------------------ provider */
// Kept as a component so the app's provider tree reads consistently, even
// though the store needs no React context.
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

export function useTheme(): { theme: Theme; toggle: () => void; setTheme: (t: Theme) => void } {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setTheme = useCallback((next: Theme) => applyTheme(next), []);
  const toggle = useCallback(
    () => applyTheme(getSnapshot() === "dark" ? "light" : "dark"),
    [],
  );

  return { theme, toggle, setTheme };
}
