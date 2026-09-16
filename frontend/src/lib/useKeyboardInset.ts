"use client";

import { useEffect, useState } from "react";

/** Below this, the change is browser-chrome noise rather than a keyboard. */
const NOISE_FLOOR = 24;

/**
 * How many pixels the on-screen keyboard is covering, or 0.
 *
 * The problem this solves is specific and easy to miss on a desktop: when a
 * mobile keyboard opens, `window.innerHeight` does not change and neither do
 * `vh` or `dvh` units. Only the *visual* viewport shrinks. So a panel pinned
 * with `bottom: 0` stays anchored to the layout viewport and the keyboard
 * lands on top of the text field the person just tapped — they type into a
 * box they cannot see.
 *
 * `visualViewport` is the primitive that actually knows. The inset is the gap
 * between the bottom of the layout viewport and the bottom of the visible
 * one; anything anchored to the bottom edge lifts by that much.
 *
 * Returns 0 on desktop, on any browser without the API, and whenever the
 * keyboard is closed — so a caller can apply it unconditionally.
 */
export function useKeyboardInset(active: boolean): number {
  const [inset, setInset] = useState(0);

  // Reset as `active` flips, in render rather than in an effect: an effect
  // would leave one frame where a reopened panel is still lifted by the last
  // keyboard height.
  const [wasActive, setWasActive] = useState(active);
  if (wasActive !== active) {
    setWasActive(active);
    if (!active && inset !== 0) setInset(0);
  }

  useEffect(() => {
    // Only while the thing that cares is on screen. A listener that runs for
    // the life of the session, to answer a question nobody is asking, is a
    // listener that fires on every scroll of every page.
    if (!active) return;

    const viewport = typeof window !== "undefined" ? window.visualViewport : null;
    if (!viewport) return;

    const measure = () => {
      // offsetTop matters: the visual viewport can also be scrolled up inside
      // the layout viewport when a field near the bottom takes focus.
      const covered = window.innerHeight - viewport.height - viewport.offsetTop;
      setInset(covered > NOISE_FLOOR ? Math.round(covered) : 0);
    };

    // Deferred rather than called here: a synchronous measure would be a
    // setState inside the effect body, and layout has not necessarily
    // settled at that point anyway.
    const first = requestAnimationFrame(measure);

    viewport.addEventListener("resize", measure);
    viewport.addEventListener("scroll", measure);
    return () => {
      cancelAnimationFrame(first);
      viewport.removeEventListener("resize", measure);
      viewport.removeEventListener("scroll", measure);
    };
  }, [active]);

  return inset;
}
