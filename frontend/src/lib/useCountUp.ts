"use client";

import { useEffect, useRef, useState } from "react";

/** Long enough to read as movement, short enough that nobody waits for it. */
const DURATION_MS = 620;

/** Ease-out cubic — the JS twin of the cubic-bezier(0.16, 1, 0.3, 1) the CSS
 *  animations use, so a counting number settles on the same beat as the card
 *  it sits in. */
const ease = (t: number) => 1 - (1 - t) ** 3;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Count from wherever the number currently is to `target`.
 *
 * Re-runs on every change, not just on mount, so a value that updates after a
 * reload animates from the old number rather than snapping. Somebody who has
 * asked for less motion gets the number and no animation — the CSS
 * `prefers-reduced-motion` block cannot reach a value driven by JavaScript.
 */
export function useCountUp(target: number, enabled = true): number {
  const [value, setValue] = useState(() => (enabled ? 0 : target));
  const displayed = useRef(0);

  useEffect(() => {
    const from = displayed.current;

    if (!enabled || prefersReducedMotion() || from === target) {
      displayed.current = target;
      setValue(target);
      return;
    }

    let frame = 0;
    const start = performance.now();

    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / DURATION_MS);
      const next = Math.round(from + (target - from) * ease(progress));
      displayed.current = next;
      setValue(next);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, enabled]);

  return value;
}
