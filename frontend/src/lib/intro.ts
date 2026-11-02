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
 * The animation waits, on its plain ink field, until the wordmark images
 * and the web fonts are loaded and decoded (<html data-intro-ready>): on a
 * real network they arrive a few hundred milliseconds after first paint,
 * and starting without them made the logo pop in halfway through its rise.
 * It never waits longer than READY_CAP_MS.
 *
 * Skipped entirely for anyone who has asked for reduced motion.
 */
const READY_CAP_MS = 1200;

export const INTRO_INIT_SCRIPT = `
(function () {
  try {
    var root = document.documentElement;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    root.dataset.intro = "play";

    var done = false;
    var started = 0;
    function finish() {
      if (done) return;
      done = true;
      delete root.dataset.intro;
      delete root.dataset.introReady;
      document.removeEventListener("animationend", onEnd);
      document.removeEventListener("animationstart", onStart);
      document.removeEventListener("pointerdown", skip, true);
      document.removeEventListener("keydown", skip, true);
    }
    function onEnd(event) {
      if (event.animationName === "pw-intro-out") finish();
    }
    // The moment the ink starts to lift, cue the page underneath (the
    // sign-in pouches fly in on it). Fired by the animation itself, so it
    // is in step with what is on screen, however slowly the page loaded.
    function onStart(event) {
      if (event.animationName === "pw-curtain-up" && root.dataset.intro === "play") root.dataset.intro = "reveal";
    }
    function skip() {
      // Once the flock is already leaving (1.6s in), let it finish:
      // switching to the quick exit then would snap it back down first.
      if (root.dataset.intro === "play" && (!started || Date.now() - started < 1600)) root.dataset.intro = "out";
    }
    document.addEventListener("animationend", onEnd);
    document.addEventListener("animationstart", onStart);
    document.addEventListener("pointerdown", skip, true);
    document.addEventListener("keydown", skip, true);

    // Start the clock only once the logo can actually be drawn.
    var go = false;
    function begin() {
      if (go || done) return;
      go = true;
      started = Date.now();
      root.dataset.introReady = "1";
      // Never let the splash outstay its welcome, whatever happens to the
      // animation events (a background tab, a stylesheet that is late).
      setTimeout(finish, 4500);
    }
    setTimeout(begin, ${READY_CAP_MS});
    function whenLoaded() {
      var waits = [];
      var images = document.querySelectorAll(".pw-intro-logo img");
      for (var i = 0; i < images.length; i++) {
        var img = images[i];
        waits.push(img.decode ? img.decode().catch(function () {}) : Promise.resolve());
      }
      if (document.fonts && document.fonts.ready) waits.push(document.fonts.ready);
      Promise.all(waits).then(begin, begin);
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", whenLoaded);
    } else {
      whenLoaded();
    }
  } catch (e) {}
})();
`;
