"use client";

import { useEffect } from "react";

/** How far into the intro the flock would start to leave; we land instead. */
const LAND_AT_MS = 1600;
/** Must finish before the pink trail does (land + 190 + 650ms), because the
 *  intro is removed the moment the trail ends. */
const GLIDE_MS = 760;
/** The sign-in pouches' own offset shadow, in their own pixels. */
const SHADOW_PX = 7;

/**
 * On the sign-in page, turns the intro's ending into a handover: each pouch
 * that also floats on the page glides from where it hovers in the intro to
 * exactly where its twin rests on the page (position, size, tilt, shadow),
 * the ink lifts away behind it, and when the intro ends the page's own
 * pouch - held still and hidden until then - takes over and keeps floating.
 *
 * Pouches with no twin on this layout (the phone shows fewer) and the logo
 * still fly off upward as on every other page. If the page is not ready in
 * time (a slow sign-in check), it does nothing and the intro ends as usual.
 */
export function IntroHandoff() {
  useEffect(() => {
    const root = document.documentElement;
    if (root.dataset.intro !== "play") return;

    // The intro's own clock: its CSS animations started with the first paint.
    const clock = document
      .getAnimations()
      .find((a) => (a as CSSAnimation).animationName === "pw-curtain-up");
    const elapsed = typeof clock?.currentTime === "number" ? clock.currentTime : null;
    if (elapsed === null || elapsed > LAND_AT_MS - 50) return;

    const timer = window.setTimeout(land, LAND_AT_MS - elapsed);
    return () => window.clearTimeout(timer);

    function land() {
      if (root.dataset.intro !== "play") return; // a tap already skipped it

      // Only the layout actually on screen (the other is display:none).
      const twins = new Map<string, HTMLElement>();
      document.querySelectorAll<HTMLElement>(".pw-pouch[data-pouch]").forEach((el) => {
        if (el.getClientRects().length > 0 && el.dataset.pouch) twins.set(el.dataset.pouch, el);
      });
      if (twins.size === 0) return;

      // The form column fades up into place on load; if a slow sign-in
      // check means it is still moving, settle it first or every pouch
      // would land a few pixels off.
      document.getAnimations().forEach((a) => {
        if ((a as CSSAnimation).animationName === "fade-up") a.finish();
      });

      root.dataset.intro = "land";

      document.querySelectorAll<HTMLElement>(".pw-fly[data-pouch]").forEach((flyer) => {
        const twin = flyer.dataset.pouch ? twins.get(flyer.dataset.pouch) : undefined;
        const exit = flyer.querySelector<HTMLElement>(".pw-fly-exit");
        const art = flyer.querySelector<HTMLElement>(".pw-fly-art");
        if (!twin || !exit || !art) return;

        flyer.classList.add("pw-fly--land");

        // Centres are rotation-proof (both turn about their centre); sizes
        // come from layout widths, which ignore the tilt.
        const from = flyer.getBoundingClientRect();
        const to = twin.getBoundingClientRect();
        const dx = to.left + to.width / 2 - (from.left + from.width / 2);
        const dy = to.top + to.height / 2 - (from.top + from.height / 2);
        const scale = twin.offsetWidth / flyer.offsetWidth;
        const turn =
          parseFloat(getComputedStyle(twin).getPropertyValue("--r")) -
          parseFloat(getComputedStyle(flyer).getPropertyValue("--r"));
        const shadow = getComputedStyle(twin).getPropertyValue("--shadow").trim() || "transparent";
        const offset = SHADOW_PX / scale;

        const timing: KeyframeAnimationOptions = {
          duration: GLIDE_MS,
          easing: "cubic-bezier(0.65, 0, 0.35, 1)",
          fill: "forwards",
        };
        exit.animate(
          [
            { transform: "none" },
            { transform: `translate(${dx}px, ${dy}px) scale(${scale}) rotate(${turn}deg)` },
          ],
          timing,
        );
        art.animate(
          [
            { filter: `drop-shadow(0 0 0 ${shadow})` },
            { filter: `drop-shadow(${offset}px ${offset}px 0 ${shadow})` },
          ],
          timing,
        );

        // Take over from the float's resting pose, so the hand-off is exact.
        twin.style.animationDelay = "0s";
      });
    }
  }, []);

  return null;
}
