"use client";

import { useEffect, type RefObject } from "react";

/** How far the cursor's influence reaches, and how far a pouch drifts. */
const REACH_PX = 280;
const DRIFT_PX = 18;

/**
 * Pouches drift gently away from the cursor, as if pushed by the air it
 * moves. Each ".pw-react" layer inside `ref` gets --mx/--my; the CSS eases
 * it there (globals.css), and lets it settle back when the cursor leaves.
 *
 * Desktop only (a real hover and a fine pointer), and off for anyone who
 * has asked for reduced motion. One measurement pass per animation frame.
 * `ready`: the panel is on screen (the page shows a spinner until then).
 */
export function usePouchMagnet(ref: RefObject<HTMLElement | null>, ready: boolean) {
  useEffect(() => {
    const host = ref.current;
    if (!ready || !host) return;
    if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let frame = 0;
    let px = 0;
    let py = 0;
    let inside = false;

    function apply() {
      frame = 0;
      host!.querySelectorAll<HTMLElement>(".pw-react").forEach((layer) => {
        // Measure the entrance wrapper, not the layer itself, so a pouch's
        // own drift never feeds back into where it thinks it is.
        const box = (layer.parentElement ?? layer).getBoundingClientRect();
        const dx = box.left + box.width / 2 - px;
        const dy = box.top + box.height / 2 - py;
        const distance = Math.hypot(dx, dy) || 1;
        const strength = inside ? Math.max(0, 1 - distance / REACH_PX) : 0;
        const push = strength * strength * DRIFT_PX;
        layer.style.setProperty("--mx", `${((dx / distance) * push).toFixed(2)}px`);
        layer.style.setProperty("--my", `${((dy / distance) * push).toFixed(2)}px`);
      });
    }

    function schedule() {
      if (!frame) frame = window.requestAnimationFrame(apply);
    }
    function onMove(event: PointerEvent) {
      px = event.clientX;
      py = event.clientY;
      inside = true;
      schedule();
    }
    function onLeave() {
      inside = false;
      schedule();
    }

    host.addEventListener("pointermove", onMove);
    host.addEventListener("pointerleave", onLeave);
    return () => {
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [ref, ready]);
}
