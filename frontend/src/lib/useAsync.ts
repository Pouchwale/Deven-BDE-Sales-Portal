"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  /** True only until the FIRST load settles. Later loads keep the previous
   *  data on screen instead of flashing a skeleton on every keystroke. */
  loading: boolean;
  reload: () => void;
}

interface InternalState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Load data on mount and whenever `deps` change.
 *
 * Aborts the in-flight request on unmount and on every re-run, so a fast
 * sequence of filter changes cannot land an older response over a newer one.
 */
export function useAsync<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): AsyncState<T> {
  const [state, setState] = useState<InternalState<T>>({
    data: null,
    error: null,
    loading: true,
  });
  const [nonce, setNonce] = useState(0);

  // Held in a ref so a caller passing an inline arrow does not re-trigger the
  // fetch on every render. Written in its own effect — never during render —
  // and declared BEFORE the fetching effect, because effects run in
  // declaration order, so the ref is already fresh when the fetch reads it.
  const loaderRef = useRef(loader);
  useEffect(() => {
    loaderRef.current = loader;
  });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (active) setState({ data: result, error: null, loading: false });
      })
      .catch((cause: unknown) => {
        if (!active) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setState((current) => ({
          data: current.data,
          error: cause instanceof Error ? cause : new Error(String(cause)),
          loading: false,
        }));
      });

    return () => {
      active = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { ...state, reload };
}

/** Debounce a fast-changing value, so typing does not fire a request per key. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);

  return debounced;
}
