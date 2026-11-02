/**
 * The PouchWale intro that plays once when the site is first opened.
 *
 * Whether it plays is decided by this script in <head>, before first paint,
 * the same way the theme is - so a visitor who has already seen it in this
 * tab never gets a flash of the splash, and one who has not never sees the
 * page underneath first. CSS shows the splash only while <html data-intro>
 * is set; this script clears it when the exit animation ends (or on a tap,
 * which skips straight to the exit).
 *
 * Skipped entirely for anyone who has asked for reduced motion.
 */
const SEEN_KEY = "pw-intro-seen";

export const INTRO_INIT_SCRIPT = `
(function () {
  try {
    var root = document.documentElement;
    if (sessionStorage.getItem(${JSON.stringify(SEEN_KEY)})) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    sessionStorage.setItem(${JSON.stringify(SEEN_KEY)}, "1");
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
    function skip() {
      if (root.dataset.intro === "play") root.dataset.intro = "out";
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
