/** BTC Manual Trading Market Context UI V1: the four short lines under the BTC chart.
 *
 *  The backend (`app.crypto.btc_context_ui_v1`, its own process) has already reduced every source
 *  to display figures and a single `live` flag per section, and publishes `null` wherever a value
 *  is not current. This module only fetches that payload with a GET and turns it into text: no
 *  threshold, no state vocabulary and no second reading of the numbers lives here.
 */

/** Where the strip's API is. Empty in a build that was not pointed at one, in which case the
 *  terminal mounts no strip at all (see `crypto-terminal-context-slot`). */
export const BTC_CONTEXT_BASE = (process.env.NEXT_PUBLIC_BTC_CONTEXT_BASE ?? "").trim().replace(/\/$/, "");

export const BTC_CONTEXT_SYMBOL = "BTCUSDT";
/** How often the strip asks. The backend reads the collector's file on each request and serves the
 *  venue figures from its own cache, so this sets screen freshness only. */
export const BTC_CONTEXT_POLL_MS = 5_000;
/** A request that takes longer than this is abandoned; the next poll tries again. */
export const BTC_CONTEXT_TIMEOUT_MS = 4_000;
/** Past this since the last good answer, the strip shows dashes rather than an old reading. */
export const BTC_CONTEXT_HOLD_MS = 15_000;

export const MISSING = "--";

export type BtcContextPayload = {
  symbol: string;
  server_time_ms: number;
  structure: { live: boolean; resistance_pct: string | null; support_pct: string | null };
  liquidity: { live: boolean; ask: string | null; bid: string | null };
  flow: { live: boolean; window: string; buy_pct: number | null; sell_pct: number | null };
  derivatives: {
    live: boolean; oi_direction: "UP" | "DOWN" | "FLAT" | null; oi_change_pct: string | null;
    oi_window: string; oi_value_usdt: string | null; funding_pct: string | null; basis_pct: string | null;
  };
};

/** One GET of the strip. Throws on any transport, status or shape failure; the caller shows dashes. */
export async function fetchBtcContext(base: string = BTC_CONTEXT_BASE,
  signal?: AbortSignal): Promise<BtcContextPayload> {
  const response = await fetch(`${base}/snapshot`, { method: "GET", cache: "no-store", signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body = await response.json() as BtcContextPayload;
  if (!body || body.symbol !== BTC_CONTEXT_SYMBOL || !body.structure || !body.liquidity
    || !body.flow || !body.derivatives) throw new Error("unexpected payload");
  return body;
}

/** A rate the backend already rounded, with its sign written out: "-0.0014" -> "-0.0014%". */
function signed(value: string | null): string {
  if (value == null) return MISSING;
  const n = Number(value);
  if (!Number.isFinite(n)) return MISSING;
  const text = value.replace(/^[+-]/, "");
  if (n > 0) return `+${text}%`;
  if (n < 0) return `-${text}%`;
  return `${text}%`;
}

function plainPct(value: string | null): string {
  if (value == null) return MISSING;
  return Number.isFinite(Number(value)) ? `${value}%` : MISSING;
}

/** USDT notional as a short figure: 7705535139 -> "7.71B". */
export function compactUsd(value: string | null): string {
  if (value == null) return MISSING;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return MISSING;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toFixed(0);
}

const ARROW: Record<string, string> = { UP: "↑", DOWN: "↓", FLAT: "→" };

export type BtcContextLines = {
  structure: string; liquidity: string; flow: string; oi: string; rates: string;
  live: { structure: boolean; liquidity: boolean; flow: boolean; derivatives: boolean };
};

/** The strip's text. `null` (never answered, or the last answer is too old) is all dashes. */
export function contextLines(payload: BtcContextPayload | null): BtcContextLines {
  const s = payload?.structure, l = payload?.liquidity, f = payload?.flow, d = payload?.derivatives;
  const oiArrow = d?.oi_direction ? ARROW[d.oi_direction] ?? MISSING : MISSING;
  return {
    structure: `R ${plainPct(s?.resistance_pct ?? null)} / S ${plainPct(s?.support_pct ?? null)}`,
    liquidity: `ASK ${l?.ask ?? MISSING} / BID ${l?.bid ?? MISSING}`,
    flow: `BUY ${f?.buy_pct ?? MISSING} / SELL ${f?.sell_pct ?? MISSING}`,
    oi: d?.oi_value_usdt == null && d?.oi_direction == null
      ? `OI ${MISSING}`
      : `OI ${oiArrow} ${compactUsd(d?.oi_value_usdt ?? null)}`,
    rates: `FUND ${signed(d?.funding_pct ?? null)} · BASIS ${signed(d?.basis_pct ?? null)}`,
    live: {
      structure: Boolean(s?.live), liquidity: Boolean(l?.live),
      flow: Boolean(f?.live), derivatives: Boolean(d?.live),
    },
  };
}
