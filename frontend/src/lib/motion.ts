"use client";

import { useGSAP } from "@gsap/react";
import gsap from "gsap";

/**
 * The one place GSAP is set up.
 *
 * Imported only by code that is already split out of the shared layout chunk
 * (the dashboard page and the lazily-loaded assistant panel), so a page that
 * never shows either does not download GSAP at all. Everything else in the
 * portal moves with the CSS keyframes in globals.css, which cost nothing.
 *
 * Rules every animation here follows:
 *   - transform and opacity only, so nothing leaves the compositor;
 *   - one entrance, never a loop;
 *   - nothing at all for somebody who has asked for reduced motion - the
 *     elements are simply in their final state, because that is where they
 *     start in the DOM.
 */
gsap.registerPlugin(useGSAP);

gsap.defaults({ ease: "power3.out", duration: 0.4 });

export { gsap, useGSAP };

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Fade-and-rise the direct `[data-enter]` children of `scope`, once, on
 * mount. Cleaned up by useGSAP's context when the component unmounts, and
 * `clearProps` hands the inline styles back so hover transforms keep working.
 */
export function useEnterStagger(
  scope: React.RefObject<HTMLElement | null>,
  dependencies: unknown[] = [],
) {
  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      const targets = gsap.utils.toArray<HTMLElement>("[data-enter]");
      if (targets.length === 0) return;
      gsap.from(targets, {
        opacity: 0,
        y: 10,
        duration: 0.45,
        stagger: 0.05,
        clearProps: "opacity,transform",
      });
    },
    { scope, dependencies },
  );
}
