/**
 * The PouchWale intro that plays on every full page load: opening the site,
 * or refreshing any page, the sign-in page included. Moving between pages
 * inside the portal (including the step from sign-in to the dashboard) is
 * not a page load, so it does not replay there.
 *
 * It is switched on by this script in <head>, before first paint, the same
 * way the theme is, so the page underneath never shows first. CSS shows the
 * splash only while <html data-intro> is set; this script clears it when the
 * exit animation ends (or on a tap, which skips straight to the exit).
 *
 * Skipped entirely for anyone who has asked for reduced motion.
 */
export const INTRO_INIT_SCRIPT = `
(function () {
  try {
    var root = document.documentElement;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    root.dataset.intro = "play";

    var done = false;
    function finish() {
      if (done) return;
      done = true;
      delete root.dataset.intro;
      document.removeEventListener("animationend", onEnd);
      document.removeEventListener("pointerdown", skip, true);
      document.removeEventListener("keydown", skip, true);
    }
    function onEnd(event) {
      if (event.animationName === "pw-intro-out") finish();
    }
    var started = Date.now();
    function skip() {
      // Once the curtain is already rising (1.6s in), let it finish:
      // switching to the quick exit then would snap it back down first.
      if (root.dataset.intro === "play" && Date.now() - started < 1550) root.dataset.intro = "out";
    }
    document.addEventListener("animationend", onEnd);
    document.addEventListener("pointerdown", skip, true);
    document.addEventListener("keydown", skip, true);
    // Never let the splash outstay its welcome, whatever happens to the
    // animation events (a background tab, a stylesheet that is late).
    setTimeout(finish, 4000);
  } catch (e) {}
})();
`;
