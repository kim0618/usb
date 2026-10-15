/** Manual terminal executions and order amounts, shared by the PAPER and the LIVE screens.
 *
 *  Shared code, never shared data. Every function here takes the account it is about as an
 *  argument - the PAPER ledger's executions or the LIVE mirror's, the PAPER leverage or
 *  Binance's - so neither screen can reach the other's figures through this module.
 */

export type ExecutionMode = "PAPER" | "LIVE";

export type ExecutionEventType =
  "INITIAL_ENTRY" | "ADD_ENTRY" | "PARTIAL_CLOSE" | "FULL_CLOSE" | "POSITION_FLIP";

/** One fill as the backend classified it from the position before and after it. */
export type Execution = {
  /** Order in which the fills happened on this account and symbol, 1-based. */
  seq: number;
  ts_ms: number;
  mode: ExecutionMode;
  symbol: string;
  /** PAPER run id or LIVE account fingerprint: the account this fill belongs to. */
  account: string | null;
  /** The position side the fill acted on (a close of a LONG is LONG). */
  side: "LONG" | "SHORT";
  event_type: ExecutionEventType;
  price: string;
  qty: string;
  leverage: string | null;
};

export type ExecutionsAnswer = {
  mode: ExecutionMode;
  symbol: string;
  account: string | null;
  executions: Execution[];
};

export const EXECUTION_LABELS: Record<ExecutionEventType, string> = {
  INITIAL_ENTRY: "진입",
  ADD_ENTRY: "추가",
  PARTIAL_CLOSE: "부분청산",
  FULL_CLOSE: "청산",
  POSITION_FLIP: "전환",
};

/** A marker on the price series. `time` is in seconds, already snapped to a candle. */
export type ChartMarker = {
  id: string;
  time: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle";
  color: string;
  text: string;
};

const ENTRY_TYPES = new Set<ExecutionEventType>(["INITIAL_ENTRY", "ADD_ENTRY", "POSITION_FLIP"]);

/** The executions that belong on this chart: this mode, this symbol, this account.
 *
 *  Filtered here rather than trusted from the request, because the cost of a wrong marker is an
 *  operator reading their position against a fill that happened on another account. Duplicates
 *  (the same fill delivered twice across a reload or a refetch) collapse on (account, seq). */
export function executionsFor(executions: readonly Execution[] | null | undefined,
                              filter: { mode: ExecutionMode; symbol: string; account?: string | null }):
  Execution[] {
  if (!executions) return [];
  const seen = new Set<string>();
  const kept: Execution[] = [];
  for (const row of executions) {
    if (row.mode !== filter.mode || row.symbol !== filter.symbol) continue;
    if (filter.account != null && row.account !== filter.account) continue;
    const key = `${row.account ?? ""}|${row.seq}`;
    if (seen.has(key)) continue;
    seen.add(key);
    kept.push(row);
  }
  return kept.sort((a, b) => a.seq - b.seq);
}

const compactQty = (value: string) => {
  const n = Number(value);
  return Number.isFinite(n) ? String(Number(n.toPrecision(8))) : value;
};

/** Executions as chart markers, each placed on the candle it happened in.
 *
 *  `candleTimes` are the series' bar times in seconds, ascending. A fill is put on the last bar
 *  that starts at or before it; a fill older than the first bar in view is left off (the chart
 *  cannot place it, and pinning it to the first bar would draw it at the wrong time). */
export function executionMarkers(executions: readonly Execution[],
                                 candleTimes: readonly number[]): ChartMarker[] {
  if (candleTimes.length === 0) return [];
  const markers: ChartMarker[] = [];
  for (const row of executions) {
    const at = Math.floor(row.ts_ms / 1000);
    const time = barAtOrBefore(candleTimes, at);
    if (time == null) continue;
    const buying = (row.side === "LONG") === ENTRY_TYPES.has(row.event_type);
    const entry = ENTRY_TYPES.has(row.event_type);
    const leverage = row.leverage ? ` ${Number(row.leverage)}x` : "";
    markers.push({
      id: `${row.mode}:${row.account ?? ""}:${row.seq}`,
      time,
      position: buying ? "belowBar" : "aboveBar",
      shape: entry ? (buying ? "arrowUp" : "arrowDown") : "circle",
      color: entry ? (row.side === "LONG" ? "#16a34a" : "#dc2626") : "#f59e0b",
      text: `#${row.seq} ${row.side} ${EXECUTION_LABELS[row.event_type]} ${compactQty(row.qty)} @ ${row.price}${leverage}`,
    });
  }
  return markers;
}

function barAtOrBefore(times: readonly number[], at: number): number | null {
  if (at < times[0]) return null;
  let low = 0;
  let high = times.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (times[middle] <= at) low = middle;
    else high = middle - 1;
  }
  return times[low];
}

/** What a quantity costs at this account's price, leverage and taker fee, in USDT and KRW.
 *
 *  notional = qty x price, margin = notional / leverage, fee = notional x taker rate. Display
 *  only: the order route prices the real fill and re-checks the size. Every input comes from the
 *  account on screen; `null` anywhere means the figure is not shown rather than guessed. */
export type OrderAmounts = {
  notional_usdt: number;
  /** Null when the leverage is unknown. The notional never depends on it. */
  margin_usdt: number | null;
  fee_usdt: number;
  notional_krw: number | null;
  margin_krw: number | null;
  fee_krw: number | null;
};

export function orderAmounts({ qty, price, leverage, takerRate, krwPerUsdt }: {
  qty: string | number | null | undefined;
  price: string | number | null | undefined;
  leverage: string | number | null | undefined;
  takerRate: string | number | null | undefined;
  krwPerUsdt: string | number | null | undefined;
}): OrderAmounts | null {
  const q = positive(qty);
  const p = positive(price);
  const l = positive(leverage);
  if (q == null || p == null) return null;
  const rate = nonNegative(takerRate);
  const fx = positive(krwPerUsdt);
  const notional = q * p;
  const margin = l == null ? null : notional / l;
  const fee = rate == null ? 0 : notional * rate;
  return {
    notional_usdt: notional, margin_usdt: margin, fee_usdt: fee,
    notional_krw: fx == null ? null : notional * fx,
    margin_krw: fx == null || margin == null ? null : margin * fx,
    fee_krw: fx == null ? null : fee * fx,
  };
}

function positive(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function nonNegative(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** The quantity a notional-mode size stands for at a price, floored to the instrument step. */
export function qtyFromNotional(notional: string, price: string | number | null | undefined,
                                step: string | null | undefined): string | null {
  const n = positive(notional);
  const p = positive(price);
  if (n == null || p == null) return null;
  const s = positive(step);
  const raw = n / p;
  if (s == null) return String(raw);
  const decimals = (String(step).split(".")[1] ?? "").length;
  return (Math.floor(raw / s + 1e-9) * s).toFixed(decimals);
}
