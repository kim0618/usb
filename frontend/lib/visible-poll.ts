/** A poll loop that stops while the page is hidden.
 *
 *  The crypto terminal ran a dozen timers that kept asking the server while the tab sat in the
 *  background - the 1 s account poll, the LIVE account's signed reads, the HOT lock, the chart
 *  tails - for a screen nobody was looking at. This is the one loop they share:
 *
 *  * `task` runs at once, then again `everyMs` after the previous run *started*, never two at a
 *    time: a slow response lowers the rate instead of stacking requests.
 *  * A hidden page stops the loop. Nothing is asked and no timer is left ticking.
 *  * Becoming visible again runs `task` at once and restarts the period from there, so the screen
 *    is current the moment it is looked at.
 *  * `everyMs` may be a function, read before every wait, for a period that depends on state.
 *
 *  Exactly one timer exists per loop at any moment; the returned function stops it for good.
 */
export function pageHidden(): boolean {
  return typeof document !== "undefined" && document.hidden;
}

export function visiblePoll(task: () => unknown, everyMs: number | (() => number)): () => void {
  let stopped = false;
  let running = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const period = () => (typeof everyMs === "function" ? everyMs() : everyMs);
  const clear = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  const schedule = (delayMs: number) => {
    clear();
    if (stopped || pageHidden()) return;
    timer = setTimeout(() => { void run(); }, Math.max(0, delayMs));
  };
  const run = async () => {
    timer = undefined;
    if (stopped || running || pageHidden()) return;
    running = true;
    const started = Date.now();
    try {
      await task();
    } catch {
      // The task owns its errors; a throw must not end the loop.
    } finally {
      running = false;
    }
    schedule(period() - (Date.now() - started));
  };
  const onVisibility = () => {
    if (stopped) return;
    if (pageHidden()) { clear(); return; }
    clear();
    void run();
  };
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisibility);
  void run();
  return () => {
    stopped = true;
    clear();
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibility);
  };
}
