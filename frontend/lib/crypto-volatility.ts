/** The last hour's realised range, per instrument, for the tab strip.
 *
 *  Read-only and market-only. Nothing here reads an account, sizes an order, or decides which
 *  symbol is selected: it answers one question - how far did this instrument travel in the last
 *  60 minutes - and the tab strip prints the answer beside the symbol's name. The selection is
 *  still the operator's alone, which is the whole reason HOT is a label and not an action.
 *
 *  The range is deliberately not a return. A symbol that opened and closed the hour at the same
 *  price after a 2% round trip is the volatile one, and an hourly return would call it flat.
 */
import type { ChartBar } from "@/lib/crypto-paper";

/** One minute, the bucket the window is built from. Matches the backend's `1m` timeframe. */
export const VOLATILITY_BUCKET_MS = 60_000;
/** Sixty buckets. "최근 1시간" is this many completed minutes, not "an hour of wall clock". */
export const VOLATILITY_WINDOW_BARS = 60;
/** How many bars to ask for. The window needs 60 *confirmed* ones, and the newest bar in a
 *  response is normally the minute in progress, so 60 would be exactly enough only when the
 *  request lands on a bucket boundary. The margin also absorbs a provider gap of a few minutes
 *  without the badge dropping to `--`. Still one provider page, so it costs no extra upstream
 *  read over asking for 61. */
export const VOLATILITY_REQUEST_BARS = 64;
/** The refresh cadence. A 1 m bucket cannot change more often than once a minute, so this is
 *  already far faster than the data; it is 10 s so a newly closed minute shows up promptly. */
export const VOLATILITY_POLL_MS = 10_000;
/** How far behind the window may fall before the figure stops being presented as current.
 *
 *  Three buckets. The newest *confirmed* bar is by construction up to one bucket behind the
 *  clock, so one bucket of slack is normal and two is the provider being late; past three the
 *  number on screen is no longer "the last hour" and the tab says `--` instead. This is a
 *  freshness bound on a read, not a threshold on volatility - no figure is computed from it. */
export const VOLATILITY_STALE_MS = 3 * VOLATILITY_BUCKET_MS;
/** Decimals shown, and therefore the precision HOT is decided at. See `hotSymbols`. */
export const VOLATILITY_DECIMALS = 2;

export type VolatilityDirection = "UP" | "DOWN" | "FLAT";

/** Why a symbol has no figure. Each one is a distinct fact about the data, never a fallback
 *  value: a symbol with too little history shows `--`, it does not show an hour computed from
 *  40 minutes. */
export type VolatilityUnknownReason =
  | "NO_CONFIRMED_BAR"   // nothing completed came back at all
  | "INCOMPLETE_WINDOW"  // the 60 buckets ending at the newest one are not all present
  | "NO_REFERENCE_PRICE" // the window's first open is not a usable positive number
  | "STALE";             // the window ends too far in the past to be called "the last hour"

export type SymbolVolatility =
  | {
      symbol: string;
      state: "COMPLETE";
      /** `(high - low) / reference * 100`, in percent. */
      rangePct: number;
      direction: VolatilityDirection;
      /** The window's own figures, kept so a reading can be rechecked against raw candles
       *  without re-deriving which minutes it covered. */
      referencePrice: number;
      high: number;
      low: number;
      close: number;
      windowStartMs: number;
      windowEndMs: number;
    }
  | { symbol: string; state: "UNKNOWN"; reason: VolatilityUnknownReason; confirmedBars: number };

type Bar = { startMs: number; open: number; high: number; low: number; close: number };

/** The confirmed 1 m bars from a history response, deduplicated by bucket.
 *
 *  `confirmed` is the server's own flag and is trusted rather than recomputed from the local
 *  clock: it is the same flag the chart draws from, so the tab and the candles cannot disagree
 *  about which minute has closed. A cached response can carry a flag that is a few seconds
 *  behind, which only ever withholds a bar that has just closed - `VOLATILITY_REQUEST_BARS`
 *  covers that, and withholding is the safe direction.
 */
function confirmedBars(bars: readonly ChartBar[]): Bar[] {
  const byBucket = new Map<number, Bar>();
  for (const row of bars) {
    if (!row.confirmed) continue;
    const bar = {
      startMs: Number(row.start_ms), open: Number(row.open), high: Number(row.high),
      low: Number(row.low), close: Number(row.close),
    };
    const finite = [bar.startMs, bar.open, bar.high, bar.low, bar.close].every(Number.isFinite);
    // A bucket that is not on a minute boundary is not a 1 m bar, whatever it is labelled. It is
    // dropped rather than snapped, because snapping would merge two provider rows into one.
    if (finite && bar.startMs % VOLATILITY_BUCKET_MS === 0) byBucket.set(bar.startMs, bar);
  }
  return [...byBucket.values()].sort((left, right) => left.startMs - right.startMs);
}

/** The last hour's range for one instrument, from its 1 m history.
 *
 *  The window is defined by *time*, not by counting rows: the 60 consecutive minute buckets
 *  ending at the newest confirmed one. Every one of them has to be present. Taking "the last 60
 *  rows" instead would silently stretch the window across a provider gap and label 70 minutes
 *  of travel as an hour's, which is the one error here that produces a plausible number.
 */
export function symbolRange1h(symbol: string, bars: readonly ChartBar[]): SymbolVolatility {
  const confirmed = confirmedBars(bars);
  const unknown = (reason: VolatilityUnknownReason): SymbolVolatility =>
    ({ symbol, state: "UNKNOWN", reason, confirmedBars: confirmed.length });
  if (confirmed.length === 0) return unknown("NO_CONFIRMED_BAR");

  const windowEndMs = confirmed[confirmed.length - 1].startMs;
  const windowStartMs = windowEndMs - (VOLATILITY_WINDOW_BARS - 1) * VOLATILITY_BUCKET_MS;
  const byBucket = new Map(confirmed.map(bar => [bar.startMs, bar]));
  const window: Bar[] = [];
  for (let index = 0; index < VOLATILITY_WINDOW_BARS; index += 1) {
    const bar = byBucket.get(windowStartMs + index * VOLATILITY_BUCKET_MS);
    if (bar === undefined) return unknown("INCOMPLETE_WINDOW");
    window.push(bar);
  }

  const referencePrice = window[0].open;
  if (!(referencePrice > 0)) return unknown("NO_REFERENCE_PRICE");
  const high = Math.max(...window.map(bar => bar.high));
  const low = Math.min(...window.map(bar => bar.low));
  const close = window[window.length - 1].close;
  return {
    symbol, state: "COMPLETE",
    rangePct: ((high - low) / referencePrice) * 100,
    // Direction is the sign of the move and nothing more. No band, no "near enough to flat"
    // tolerance: FLAT means the last confirmed close equals the window's first open exactly.
    direction: close > referencePrice ? "UP" : close < referencePrice ? "DOWN" : "FLAT",
    referencePrice, high, low, close, windowStartMs, windowEndMs,
  };
}

/** Drop a reading that has stopped being about the last hour.
 *
 *  Applied after the figure is computed, so a stale reading becomes `--` rather than a number
 *  with a quiet caveat. The age measured is from the *end* of the window's last bucket, which is
 *  the first moment that bucket was complete.
 */
export function withFreshness(row: SymbolVolatility, nowMs: number,
                              staleMs: number = VOLATILITY_STALE_MS): SymbolVolatility {
  if (row.state !== "COMPLETE") return row;
  const closedAtMs = row.windowEndMs + VOLATILITY_BUCKET_MS;
  if (nowMs - closedAtMs <= staleMs) return row;
  return { symbol: row.symbol, state: "UNKNOWN", reason: "STALE",
           confirmedBars: VOLATILITY_WINDOW_BARS };
}

/** The figure as it is shown, and as HOT is decided.
 *
 *  `null` - no reading yet - prints the same `--` as a reading that could not be computed. The
 *  tab is not in a position to claim anything about the last hour in either case, and giving the
 *  two states different text would put the distinction on screen without making it useful. */
export function rangeLabel(row: SymbolVolatility | null | undefined): string {
  return row?.state === "COMPLETE" ? `${row.rangePct.toFixed(VOLATILITY_DECIMALS)}%` : "--";
}

export const DIRECTION_ARROW: Record<VolatilityDirection, string> = {
  UP: "↑", DOWN: "↓", FLAT: "↔",
};
export const DIRECTION_WORD: Record<VolatilityDirection, string> = {
  UP: "상승", DOWN: "하락", FLAT: "보합",
};

/** Which symbols carry HOT: the largest range, at the precision the screen shows.
 *
 *  Ties are decided on the *displayed* figure rather than on the raw double, and every symbol
 *  that ties gets the badge. Two reasons, in this order:
 *
 *  1. It is the only policy an operator can check. Two tabs reading `0.72%` with HOT on one of
 *     them is a verdict whose reason is not on screen, and the difference that produced it -
 *     0.7249 against 0.7251 - is not a difference anyone is trading on.
 *  2. It cannot hide the real maximum. Rounding is monotonic, so the largest rounded value
 *     always contains the largest raw value; the set can only ever be *wider* than the exact
 *     one, never pointed at the wrong symbol.
 *
 *  A symbol with no figure is not ranked. UNKNOWN is the absence of a measurement, and ranking
 *  it - at zero, or at all - would let missing data win or lose a comparison it took no part in.
 */
export function hotSymbols(rows: readonly SymbolVolatility[]): Set<string> {
  const ranked = rows.filter((row): row is Extract<SymbolVolatility, { state: "COMPLETE" }> =>
    row.state === "COMPLETE");
  if (ranked.length === 0) return new Set();
  const shown = (row: Extract<SymbolVolatility, { state: "COMPLETE" }>) =>
    Number(row.rangePct.toFixed(VOLATILITY_DECIMALS));
  const top = Math.max(...ranked.map(shown));
  return new Set(ranked.filter(row => shown(row) === top).map(row => row.symbol));
}
