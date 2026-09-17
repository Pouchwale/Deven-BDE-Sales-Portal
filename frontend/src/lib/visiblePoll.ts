/**
 * Run `tick` every `intervalMs`, but only while the tab is visible.
 *
 * A plain `setInterval` kept polling from background tabs: a portal left open
 * in a tab all day asked the server for the bell count every minute (and, on
 * the SAP pages, for the sync status every 30 s) with nobody looking. Browsers
 * only throttle that, they do not stop it.
 *
 * While hidden the timer is cleared. When the tab comes back, `tick` runs once
 * straight away - so what the person sees is current - and the interval
 * restarts from there. The first tick is the caller's job, as before.
 *
 * Returns the cleanup for a `useEffect`.
 */
export function pollWhileVisible(tick: () => void, intervalMs: number): () => void {
  let timer: number | null = null;

  const start = () => {
    if (timer === null) timer = window.setInterval(tick, intervalMs);
  };
  const stop = () => {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  };
  const onVisibilityChange = () => {
    if (document.visibilityState === "hidden") {
      stop();
    } else if (timer === null) {
      tick();
      start();
    }
  };

  if (document.visibilityState !== "hidden") start();
  document.addEventListener("visibilitychange", onVisibilityChange);

  return () => {
    document.removeEventListener("visibilitychange", onVisibilityChange);
    stop();
  };
}
