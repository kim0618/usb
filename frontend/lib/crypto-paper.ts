/** Crypto paper terminal client.
 *
 *  Deliberately does not import `@/lib/fx`: that fixed rate belongs to the equity screens.
 *  Every KRW figure here is converted by the backend with the run's own fixed rate, which is
 *  pinned once at run start and travels in the run config. */

export const CRYPTO_API_BASE = (process.env.NEXT_PUBLIC_CRYPTO_API_BASE || "http://127.0.0.1:8100").replace(/\/$/, "");

export type Mode = "MANUAL" | "AUTO" | "AUTO_STOPPING" | "EMERGENCY";

export type CryptoQuote = {
  ts_ms: number; best_bid: string | null; best_ask: string | null; spread: string | null;
  mid: string | null; mark_price: string; last_price: string | null; index_price: string | null;
  funding_rate: string | null; next_funding_time_ms: number | null;
  bid_depth_top5: string; ask_depth_top5: string;
};

export type CryptoAccount = {
  starting_capital_usdt: string; wallet_balance: string; available_balance: string;
  used_margin: string; realized_pnl: string; unrealized_pnl: string; equity: string;
  cumulative_fees: string; cumulative_funding_paid: string;
  position_side: "LONG" | "SHORT" | null; position_qty: string; position_signed_qty: string;
  avg_entry: string | null; leverage: string | null; entry_notional: string;
  mark_notional: string; maintenance_margin: string; margin_ratio: string | null;
  liquidation_price: string | null; risk_tier: number | null;
  capital_base_usdt: string; reset_count: number; last_reset_ts_ms: number | null;
};

export type CryptoState = {
  recovery: Recovery | null;
  run_id: string; engine_version: string; leverage: string; server_time_ms: number;
  started_at_ms: number | null; ledger_event_count: number; input_record_count: number;
  liquidation_count: number; funding_grid_mismatches: number; starting_capital_krw: string;
  state: {
    mode: Mode; modes: Mode[]; can_open_new_position: boolean;
    new_entry_blocked_reason: string | null; auto_available: boolean;
    auto_unavailable_reason: string;
  };
  quote: CryptoQuote | null;
  account: CryptoAccount | null;
  krw: Record<string, string> | null;
  fees: { version: string; taker_rate: string; maker_rate: string; source: string; effective_date: string; basis: string };
  fx: { krw_per_usdt: string; source: string; asof_utc: string };
  slippage: { model: string; bps: string };
  /** Tape archive: closed segments plus the active file. Absent on a replay engine. */
  storage?: {
    segments: number; segment_bytes: number; active_bytes: number; ledger_bytes: number;
    total_bytes: number; records: number; compressed_segments: number;
  } | null;
  feed: {
    connected: boolean; connects: number; reconnects: number; book_gaps: number;
    book_resyncs: number; malformed: number; messages: number; last_message_ms: number | null;
    last_error: string | null; book_ready: boolean;
  };
  paper_source?: "PAPER_MANUAL" | "PAPER_C1_AUTO";
  c1_auto?: {
    enabled: boolean; active_signal_id: string | null; active_trade_id: string | null;
    enabled_at: number | null; disabled_at: number | null; source: "PAPER_C1_AUTO";
    leverage: "10"; has_position: boolean; entry_at: number | null;
    benchmark_at: number | null;
  } | null;
};

export type LedgerEvent = { seq: number; ts_ms: number; event_type: string } & Record<string, unknown>;

export type Recovery = {
  restored: boolean; tape_records: number; ledger_events: number; ledger_offset_bytes: number;
  ledger_tail_rewritten_bytes: number; torn_writes_repaired: { path: string; dropped_bytes: number }[];
  ledger_schema_version: string | null; position_signed_qty: string; mode: Mode;
  /** FULL_REPLAY re-derived every event from the tape; CHECKPOINT trusted an attested ledger
   *  prefix and replayed only what came after it. */
  source?: string; checkpoint_segment?: number | null;
};

export type Performance = {
  trades: number; wins: number; losses: number; win_rate: string | null;
  gross_pnl: string; fees: string; funding: string; net_pnl: string;
  avg_win: string | null; avg_loss: string | null; expectancy: string | null;
  profit_factor: string | null; payoff_ratio: string | null;
  max_drawdown: string; max_drawdown_fraction: string | null;
  capital_resets?: number;
  /** Since the last ACCOUNT_RESET, or since RUN_START when there has not been one. Computed by
   *  the backend from the anchors the reset event recorded; this file must not subtract its
   *  own. */
  current_segment?: {
    since_ts_ms: number | null; reset_count: number;
    starting_capital_usdt: string; starting_capital_krw: string | null;
    current_segment_realized_pnl: string; current_segment_fees: string;
    current_segment_funding: string; current_segment_net_pnl: string;
    trades: number; wins: number; win_rate: string | null; max_drawdown: string;
  };
  longest_losing_streak: number; hold_ms_median: number | null; hold_ms_total: number;
  max_adverse_excursion: string; max_favourable_excursion: string; liquidations: number;
  ending_equity: string; starting_capital: string; source: string; run_id: string;
  krw: Record<string, string>;
  by_origin: Record<string, OriginBucket>;
  by_leverage: Record<string, OriginBucket>;
  by_side: Record<string, OriginBucket>;
  reconciliation: Record<string, string | boolean>;
};

export type OriginBucket = { trades: number; wins: number; net_pnl: string; fees: string; funding: string };

export type TradeRow = {
  index: number; side: "LONG" | "SHORT"; origin: string; leverage: string;
  opened_ts_ms: number; closed_ts_ms: number; hold_ms: number; entry_price: string;
  exit_price: string; qty: string; gross_pnl: string; fees: string; funding: string;
  net_pnl: string; max_adverse_excursion: string; max_favourable_excursion: string;
  liquidated: boolean; exits: number; is_win: boolean;
};
export type ChartBar = { start_ms: number; open: string; high: string; low: string; close: string; volume: string; confirmed: boolean };
export type HistoryTimeframe = "1m" | "10m" | "1h" | "4h" | "1d";
export type ChartHistoryResponse = {
  timeframe: HistoryTimeframe; bucket_ms: number;
  source: "BYBIT_PUBLIC_KLINE"; source_interval: string;
  bars: ChartBar[]; has_more: boolean; next_before_ms: number | null;
};

/** Price PnL and trading costs, apart. Every figure is the paper engine's: confirmed amounts are
 *  folded from the ledger, the expected-close amounts are read off a clone that was actually sent
 *  the full CLOSE. `*_pnl` fields are the signed effect on PnL, so this file never negates a cost
 *  or adds a column up itself. */
export type OpenPositionPnl = {
  position_open: true; side: "LONG" | "SHORT"; qty: string; leverage: string;
  quote_ts_ms: number; mark_price: string;
  unrealized_pnl: string; unrealized_pct_of_margin?: string | null;
  entry_fee: string; partial_exit_fee: string; partial_realized_pnl: string;
  funding: string; funding_pnl: string; entry_slippage: string;
  segment_realized_pnl: string; segment_net_pnl: string;
  close_feasible: boolean; close_reject_code: string | null; close_reject_message: string | null;
  expected_close_fill_price: string | null; expected_close_reference_price: string | null;
  expected_close_fee: string | null; expected_close_slippage: string | null;
  expected_close_slippage_pnl: string | null;
  expected_close_spread_cost: string | null; expected_close_depth_cost: string | null;
  expected_segment_net_if_closed: string | null; expected_position_net_if_closed: string | null;
  /** Money fields in won at the run's fixed rate, converted by the backend. */
  krw?: Partial<Record<keyof OpenPositionPnl, string | null>>;
};
export type PositionPnlPreview = { position_open: false } | OpenPositionPnl;

export type TradeBreakdown = {
  index: number; side: "LONG" | "SHORT"; closed_ts_ms: number;
  gross_realized_pnl: string; entry_fee: string; exit_fee: string; funding: string;
  funding_pnl: string; entry_slippage: string; exit_slippage: string; slippage: string;
  slippage_pnl: string; net_realized_pnl: string; liquidated: boolean; exits: number;
  krw?: Partial<Record<keyof TradeBreakdown, string | null>>;
};

/** Enter-then-close-at-once on a clone of the engine, per side. Nothing here is derived on the
 *  client: fills, fees, slippage (cost positive), the round-trip cost and the breakeven all come
 *  from the backend. A refused side carries the stage, the engine's code and the safe size. */
export type OrderPreviewSide = {
  side: OrderSide; feasible: boolean; qty?: string; leverage?: string;
  quote_ts_ms?: number; mark_price?: string; best_bid?: string | null; best_ask?: string | null;
  reject_stage?: string; reject_code?: string; reject_message?: string; safe_max_qty?: string;
  notional?: string; required_margin?: string;
  entry_fill_price?: string; entry_fee?: string; entry_slippage?: string; entry_slippage_pnl?: string;
  exit_fill_price?: string; exit_fee?: string; exit_slippage?: string; exit_slippage_pnl?: string;
  round_trip_cost?: string; immediate_round_trip_net?: string;
  breakeven_exit_fill_price?: string; breakeven_mark_price?: string;
  breakeven_move?: string; breakeven_move_pct?: string; breakeven_basis?: string;
  krw?: Partial<Record<string, string | null>>;
};
export type OrderPreview = {
  run_id: string; server_time_ms: number; feed_connected: boolean; feed_last_message_ms?: number | null;
  quote_ts_ms: number | null; krw_per_usdt: string; sides: Partial<Record<OrderSide, OrderPreviewSide>>;
};
export type OrderPreviewParams = { long_qty?: string; short_qty?: string; notional_usdt?: string };

/** The open position on the newest feed tick, from a clone; polled fast while a position is
 *  open. The ledger and the tape keep their own 1 Hz rhythm. */
export type LivePnl = {
  position_open: boolean; side?: OrderSide; qty?: string; leverage?: string;
  quote_ts_ms?: number; mark_price?: string; unrealized_pnl?: string;
  unrealized_pct_of_margin?: string | null; close_feasible?: boolean;
  close_reject_code?: string | null; close_reject_message?: string | null;
  expected_close_fill_price?: string | null; expected_close_fee?: string | null;
  expected_close_slippage_pnl?: string | null; expected_position_net_if_closed?: string | null;
  expected_segment_net_if_closed?: string | null;
  krw: Partial<Record<string, string | null>>; server_time_ms: number; feed_connected: boolean;
  feed_last_message_ms?: number | null;
};

export type PnlBreakdown = {
  run_id: string; krw_per_usdt: string; position: PositionPnlPreview;
  trades: TradeBreakdown[]; trade_total: number;
};

/** Quick-size presets. Every figure is computed by the paper engine and arrives as a decimal
 *  string; this file must never derive a size, a margin or a liquidation price of its own.
 *  The affordable size depends on how far the order walks the book, so there is no formula to
 *  copy here even if we wanted one. */
export type SizePreset = {
  label: string; fraction: string; qty: string; feasible: boolean;
  fill_price: string | null; notional: string | null; fee: string | null;
  reserved_margin: string | null; required_total: string | null;
  liquidation_price: string | null; margin_ratio: string | null; risk_tier: number | null;
  resulting_qty: string | null; resulting_avg_entry: string | null; available_after: string | null;
  reject_code: string | null; reject_message: string | null;
  /** Both halves of the round trip, checked on one snapshot. `feasible` requires both. */
  entry_feasible: boolean; exit_feasible: boolean; exit_fill_price: string | null;
  exit_reject_code: string | null; exit_reject_message: string | null;
  /** The book this was priced on. A preview, never a promise. */
  quote_ts_ms: number | null; best_bid: string | null; best_ask: string | null;
  mark_price: string | null; bid_depth: string | null; ask_depth: string | null;
};

export type SideSizing = {
  side: string; leverage: string; max_qty: string; max_feasible: boolean;
  reject_code: string | null; reject_message: string | null;
  max_definition?: string;
  quote_ts_ms?: number | null; best_bid?: string | null; best_ask?: string | null;
  mark_price?: string | null; entry_depth?: string | null; exit_depth?: string | null;
  instrument: { qty_step: string; min_order_qty: string; min_notional_value: string; max_mkt_order_qty: string };
  presets: SizePreset[];
};

export type CryptoSizing = {
  run_id: string; leverage: string; quote_ts_ms: number | null; mark_price: string | null;
  available_balance: string; sides: Partial<Record<OrderSide, SideSizing>>;
};

export type OrderSide = "LONG" | "SHORT";

export const PRESET_LABELS = ["25%", "HALF", "75%", "MAX"] as const;
export type PresetLabel = (typeof PRESET_LABELS)[number];

export class CryptoApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); this.name = "CryptoApiError"; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${CRYPTO_API_BASE}${path}`, {
      ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store",
    });
  } catch {
    throw new CryptoApiError(0, "NETWORK_ERROR", "Paper Terminal API에 연결할 수 없습니다.");
  }
  const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
  if (!response.ok) throw new CryptoApiError(response.status, body?.error?.code || "HTTP_ERROR", body?.error?.message || `HTTP ${response.status}`);
  return body as T;
}

export const cryptoApi = {
  state: () => request<CryptoState>("/api/crypto/state"),
  chart: (limit = 120) => request<{ bars: ChartBar[] }>(`/api/crypto/chart?limit=${limit}`),
  chartHistory: (timeframe: HistoryTimeframe, limit: number, beforeMs?: number | null,
                 signal?: AbortSignal) => {
    const query = new URLSearchParams({ timeframe, limit: String(limit) });
    if (beforeMs != null) query.set("before_ms", String(beforeMs));
    return request<ChartHistoryResponse>(`/api/crypto/chart-history?${query.toString()}`, { signal });
  },
  ledger: (limit = 60) => request<{ total: number; events: LedgerEvent[] }>(`/api/crypto/ledger?limit=${limit}`),
  performance: () => request<Performance>("/api/crypto/performance"),
  trades: (limit = 50) => request<{ total: number; trades: TradeRow[] }>(`/api/crypto/trades?limit=${limit}`),
  order: (body: { side: "LONG" | "SHORT"; intent: "OPEN" | "CLOSE"; qty?: string; notional_usdt?: string }) =>
    request<{ state: CryptoState }>("/api/crypto/order", { method: "POST", body: JSON.stringify(body) }),
  leverage: (leverage: string) =>
    request<{ state: CryptoState }>("/api/crypto/leverage", { method: "POST", body: JSON.stringify({ leverage }) }),
  mode: (action: string, confirmed = false) =>
    request<{ state: CryptoState }>("/api/crypto/mode", { method: "POST", body: JSON.stringify({ action, confirmed }) }),
  emergencyClose: () => request<{ state: CryptoState }>("/api/crypto/emergency-close", { method: "POST" }),
  sizing: () => request<CryptoSizing>("/api/crypto/sizing"),
  pnlBreakdown: () => request<PnlBreakdown>("/api/crypto/pnl-breakdown"),
  orderPreview: (params: OrderPreviewParams, signal?: AbortSignal) => {
    const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "") as [string, string][]);
    return request<OrderPreview>(`/api/crypto/order-preview?${query.toString()}`, { signal });
  },
  live: (signal?: AbortSignal) => request<LivePnl>("/api/crypto/live", { signal }),
  candles15s: (sinceMs?: number | null, signal?: AbortSignal) =>
    request<Candles15sResponse>(`/api/crypto/candles-15s${sinceMs != null ? `?since_ms=${sinceMs}` : ""}`, { signal }),
  reset: (targetKrw?: string) => request<{ state: CryptoState }>("/api/crypto/reset", {
    method: "POST", body: JSON.stringify(targetKrw ? { target_krw: targetKrw } : {}) }),
  c1Auto: (enabled: boolean) => request<CryptoState["c1_auto"]>("/api/crypto/paper/c1-auto", {
    method: "POST", body: JSON.stringify({ enabled }) }),
};

/** Decimal strings arrive from the backend and stay strings until the moment they are shown. */
export const num = (value: string | null | undefined) => value == null || value === "" ? null : Number(value);

/** Rounding can leave a negative zero ("-0원", "-0.0000 USDT"). Every money formatter below
 *  rounds first and prints the rounded value, so a figure that rounds to zero prints as zero. */
const roundedTo = (amount: number, digits: number) => {
  const rounded = Number(amount.toFixed(digits));
  return rounded === 0 ? 0 : rounded;
};
export const usdt = (value: string | null | undefined, digits = 2) => {
  const amount = num(value);
  return amount == null ? "-" : `${roundedTo(amount, digits).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })} USDT`;
};
export const signedUsdt = (value: string | null | undefined, digits = 4) => {
  const amount = num(value);
  if (amount == null) return "-";
  const rounded = roundedTo(amount, digits);
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })} USDT`;
};
export const krw = (value: string | null | undefined) => {
  const amount = num(value);
  return amount == null ? "-" : `${roundedTo(amount, 0).toLocaleString("ko-KR")}원`;
};
export const signedKrw = (value: string | null | undefined) => {
  const amount = num(value);
  if (amount == null) return "-";
  const rounded = roundedTo(amount, 0);
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("ko-KR")}원`;
};
/** A cost the backend sends as a positive amount, shown as the deduction it is. Zero stays "0". */
export const costKrw = (value: string | null | undefined) => {
  const amount = num(value);
  if (amount == null) return "-";
  return roundedTo(amount, 0) === 0 ? "0원" : `-${krw(value)}`;
};
export const costUsdt = (value: string | null | undefined, digits = 4) => {
  const amount = num(value);
  if (amount == null) return "-";
  return roundedTo(amount, digits) === 0 ? usdt("0", digits) : `-${usdt(value, digits)}`;
};
export const price = (value: string | null | undefined, digits = 1) => {
  const amount = num(value);
  return amount == null ? "-" : amount.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
};
export const qty = (value: string | null | undefined) => {
  const amount = num(value);
  return amount == null ? "-" : amount.toLocaleString("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
};
export const rateBps = (value: string | null | undefined) => {
  const amount = num(value);
  return amount == null ? "-" : `${(amount * 10_000).toFixed(2)} bp`;
};
export const toneClass = (value: string | null | undefined) => {
  const amount = num(value);
  if (amount == null || amount === 0) return "text-foreground";
  return amount > 0 ? "text-success" : "text-danger";
};
export const clockKst = (ms: number | null | undefined) => ms == null ? "-" :
  new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(ms));

/** Human duration for a hold time. Minutes below an hour, hours above it. */
export const duration = (ms: number | null | undefined) => {
  if (ms == null) return "-";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  return `${hours}시간 ${minutes % 60}분`;
};

export const percent = (value: string | null | undefined, digits = 1) => {
  const amount = num(value);
  return amount == null ? "-" : `${(amount * 100).toFixed(digits)}%`;
};

export const MODE_LABELS: Record<Mode, string> = {
  MANUAL: "수동", AUTO: "자동", AUTO_STOPPING: "자동 정지 중", EMERGENCY: "비상",
};

export const EVENT_LABELS: Record<string, string> = {
  RUN_START: "런 시작", MODE_CHANGE: "모드 변경", LEVERAGE_CHANGE: "레버리지 변경",
  ORDER_SUBMITTED: "주문 접수", ORDER_REJECTED: "주문 거부", FILL: "체결",
  POSITION_OPEN: "포지션 진입", POSITION_INCREASE: "포지션 증가", POSITION_REDUCE: "부분 청산",
  POSITION_CLOSE: "포지션 종료", FEE: "수수료", FUNDING: "펀딩", LIQUIDATION: "강제청산",
  ACCOUNT_RESET: "가상계좌 초기화",
};

/** Feed staleness in seconds, or null when nothing has arrived yet. */
export const feedAgeSeconds = (state: Pick<CryptoState, "feed" | "server_time_ms">) =>
  state.feed.last_message_ms == null ? null : Math.max(0, (state.server_time_ms - state.feed.last_message_ms) / 1000);

export const CHART_WIDTH = 760;
export const CHART_HEIGHT = 200;

/** Inline SVG geometry. The project has no chart dependency and this screen adds none. */
export function chartGeometry(bars: ChartBar[]) {
  const closes = bars.map(bar => Number(bar.close)).filter(Number.isFinite);
  if (closes.length < 2) return null;
  const low = Math.min(...closes);
  const high = Math.max(...closes);
  const span = high - low || 1;
  const pad = span * 0.08;
  const top = high + pad;
  const bottom = low - pad;
  const scaleY = (value: number) => CHART_HEIGHT - ((value - bottom) / (top - bottom)) * CHART_HEIGHT;
  const scaleX = (index: number) => (index / (closes.length - 1)) * CHART_WIDTH;
  const path = closes.map((value, index) => `${index === 0 ? "M" : "L"}${scaleX(index).toFixed(2)},${scaleY(value).toFixed(2)}`).join(" ");
  return {
    path, high, low, top, bottom, first: closes[0], last: closes[closes.length - 1],
    rising: closes[closes.length - 1] >= closes[0],
    lastY: scaleY(closes[closes.length - 1]),
    startMs: bars[0].start_ms, endMs: bars[bars.length - 1].start_ms,
  };
}

/** Why the engine refused a size, in the operator's language. Unknown codes fall through to the
 *  code itself rather than to a vague sentence: a code that reaches the screen unrecognised is a
 *  bug worth seeing, not worth smoothing over. */
export const REJECT_LABELS: Record<string, string> = {
  INSUFFICIENT_MARGIN: "증거금 부족",
  NOTIONAL_BELOW_MINIMUM: "최소 주문 금액 미달",
  QTY_BELOW_MINIMUM: "최소 주문 수량 미달",
  QTY_NOT_POSITIVE: "주문 가능 수량 없음",
  QTY_OFF_GRID: "수량 단위 불일치",
  QTY_ABOVE_MARKET_MAXIMUM: "시장가 최대 수량 초과",
  NO_LIQUIDITY: "호가 잔량 부족",
  NO_QUOTE: "시세 없음",
  REVERSE_NOT_ALLOWED: "반대 포지션 보유 중",
  RISK_LIMIT_EXCEEDED: "리스크 한도 초과",
  LEVERAGE_ABOVE_TIER: "구간 최대 레버리지 초과",
  NEW_ENTRY_BLOCKED_EMERGENCY: "비상 모드 · 신규 진입 차단",
  NEW_ENTRY_BLOCKED_AUTO_STOPPING: "자동 정지 중 · 신규 진입 차단",
  RUN_NOT_CONFIGURED: "런 미설정",
  RESET_BLOCKED_OPEN_POSITION: "포지션 보유 중 · 청산 후 초기화",
  RESET_TARGET_INVALID: "초기화 금액이 올바르지 않습니다",
};

export const rejectLabel = (code: string | null | undefined, message?: string | null) =>
  code == null ? (message || "사용 불가") : (REJECT_LABELS[code] || code);

/** A MAX-sized entry at high leverage leaves almost no room before liquidation. The threshold is
 *  a display warning only; the engine's own limits are what actually refuse an order. */
export const HIGH_RISK_LEVERAGE = 20;

export const isHighRisk = (leverage: string | null | undefined, preset: string | null) =>
  preset === "MAX" && (num(leverage) ?? 0) >= HIGH_RISK_LEVERAGE;

// ------------------------------------------------------------------ candles

/** The feed publishes 1m bars and nothing else, so every other timeframe on the screen is folded
 *  from those. Doing it here rather than asking the server for each interval keeps one definition
 *  of a bar: whatever the chart shows at 5m is exactly the 1m bars the ledger was priced against.
 *
 *  A bucket is keyed by floor(start / span), which is what Bybit's own kline boundaries use, so a
 *  15m candle here starts on the same wall-clock minute it would on the exchange. */
export const TIMEFRAMES = [1, 3, 5, 15] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];
export const TIMEFRAME_LABELS: Record<Timeframe, string> = { 1: "1m", 3: "3m", 5: "5m", 15: "15m" };

/** What the chart can show. "15s" is built by the backend from the realtime public trade stream
 *  on its own connection; it is a live view only, never research data, and it exists only from
 *  the moment that stream started. The minute timeframes fold the exchange's 1m klines. */
export const CHART_TIMEFRAMES = ["15s", "1m", "10m", "1h", "4h", "1d"] as const;
export type ChartTimeframe = (typeof CHART_TIMEFRAMES)[number];
export const chartTimeframeLabel = (value: ChartTimeframe) => value;

export type Candle15s = {
  start_ms: number; end_ms: number; open: string; high: string; low: string; close: string;
  volume: string; trade_count: number; confirmed: boolean; partial: boolean;
};
export type Candle15sStatus =
  | "CONNECTED" | "CONNECTED_WAITING_FOR_TRADE" | "STALE" | "RECONNECTING" | "DISCONNECTED" | "DISABLED";
export type Candles15sResponse = {
  timeframe: "15s"; status: Candle15sStatus;
  candles: Candle15s[]; current: Candle15s | null; server_time_ms: number;
  coverage_from_ms?: number | null; history_size?: number;
  feed?: { connected: boolean; last_message_ms: number | null; avg_lag_ms: number | null };
};

/** True when `times` is the same series as what is drawn with only its tail moved: the newest
 *  candle amended, or at most two appended. The chart then `update`s those bars instead of
 *  replacing the whole series (which would also cost the operator their zoom). */
export function isTailUpdate(previous: { key: string; times: number[] } | null, key: string, times: number[]) {
  const n = previous?.times.length ?? 0;
  return previous != null && previous.key === key && n > 0
    && times.length >= n && times.length - n <= 2
    && times[0] === previous.times[0] && times[n - 1] === previous.times[n - 1];
}

/** A slot on the time axis with no candle: a 15 s window in which nothing traded. */
export type WhitespacePoint = { time: number; whitespace: true };
export type ChartPoint = Candle | WhitespacePoint;
export const isWhitespace = (point: ChartPoint): point is WhitespacePoint => "whitespace" in point;

const BUCKET_15S_MS = 15_000;
const MAX_WHITESPACE_RUN = 240; // an hour of empty slots at most; a longer hole is not drawn to scale

/** Backend 15 s rows into chart points. A parse for drawing, not a calculation.
 *
 *  A window with no trades has no candle - the backend never invents one - but the chart would
 *  otherwise butt the neighbours together and a quiet minute would vanish from the time axis. So
 *  the empty windows become whitespace: the axis keeps real time and nothing is drawn there. */
export function candles15sToChart(rows: Candle15s[]): ChartPoint[] {
  const out: ChartPoint[] = [];
  let previous: number | null = null;
  for (const row of rows) {
    if (previous != null) {
      const missing = Math.round((row.start_ms - previous) / BUCKET_15S_MS) - 1;
      for (let k = 1; k <= Math.min(missing, MAX_WHITESPACE_RUN); k++) {
        out.push({ time: (previous + k * BUCKET_15S_MS) / 1000, whitespace: true });
      }
    }
    out.push({
      time: row.start_ms / 1000, open: Number(row.open), high: Number(row.high), low: Number(row.low),
      close: Number(row.close), volume: Number(row.volume), confirmed: row.confirmed,
    });
    previous = row.start_ms;
  }
  return out;
}

/** What the 15 s note under the timeframe row says. Connection health and a quiet market are
 *  different things: only a real connection problem is shown as a warning. */
export function candles15sNote(status: Candle15sStatus | null, candleCount: number,
                               coverageFrom: number | null): { text: string; warn: boolean } {
  const unrelated = "주문·체결과 무관";
  if (status === "RECONNECTING") return { text: `15초봉 재연결 중 · ${unrelated}`, warn: true };
  if (status === "DISCONNECTED") return { text: `15초봉 연결 끊김 · ${unrelated}`, warn: true };
  if (status === "STALE") return { text: `15초봉 수신 지연 · ${unrelated}`, warn: true };
  if (status === "DISABLED") return { text: `15초봉 비활성 · ${unrelated}`, warn: true };
  const since = coverageFrom ? ` · ${clockKst(coverageFrom)} 이후` : "";
  if (candleCount < 4) return { text: `15초봉 기록 수집 중${since}`, warn: false };
  if (status === "CONNECTED_WAITING_FOR_TRADE") return { text: `15초봉 · 실시간 · 체결 대기${since}`, warn: false };
  return { text: `15초봉 · 실시간 체결 기준${since} · 연구 정본 아님`, warn: false };
}

/** How many 15 s candles the default view shows, by chart width: enough to read the last few
 *  minutes, few enough that each candle has a body. The operator can still zoom and pan. */
export const VISIBLE_15S_BARS = { narrow: 40, wide: 60 } as const;
export const visible15sBars = (chartWidthPx: number) =>
  chartWidthPx < 640 ? VISIBLE_15S_BARS.narrow : VISIBLE_15S_BARS.wide;

/** Overlay lines (entry, liquidation) outside the price range the chart is showing right now.
 *  The chart scales to the candles in view, so such a line is off the canvas; saying where it is
 *  keeps its meaning without letting it flatten the candles. `range` is the chart's own visible
 *  price range, so zooming and panning are accounted for. */
export function offscreenOverlays(overlays: ChartOverlay[], range: { from: number; to: number } | null) {
  if (range == null) return [];
  const low = Math.min(range.from, range.to);
  const high = Math.max(range.from, range.to);
  return overlays
    .filter(overlay => overlay.kind !== "MARK" && (overlay.price > high || overlay.price < low))
    .map(overlay => ({ ...overlay, direction: overlay.price > high ? "UP" as const : "DOWN" as const }));
}

export type Candle = {
  time: number;          // seconds, the unit lightweight-charts expects
  open: number; high: number; low: number; close: number; volume: number;
  confirmed: boolean;
};

/** Parse server-bucketed history for drawing, dropping invalid and duplicate timestamps. */
export function chartBarsToCandles(bars: ChartBar[]): Candle[] {
  const byTime = new Map<number, Candle>();
  for (const row of bars) {
    const candle = {
      time: row.start_ms / 1000, open: Number(row.open), high: Number(row.high),
      low: Number(row.low), close: Number(row.close), volume: Number(row.volume),
      confirmed: row.confirmed,
    };
    if ([candle.time, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)) {
      byTime.set(candle.time, { ...candle, volume: Number.isFinite(candle.volume) ? candle.volume : 0 });
    }
  }
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

/** Merge initial, lazy-loaded and live refresh pages without duplicate timestamps. */
export function mergeCandles(current: Candle[], incoming: Candle[]): Candle[] {
  const byTime = new Map(current.map(row => [row.time, row]));
  for (const row of incoming) byTime.set(row.time, row);
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

/** Fold 1m bars into `minutes`-wide candles.
 *
 *  Open comes from the first bar of the bucket and close from the last, high and low from the
 *  extremes, volume from the sum. A bucket is confirmed only when every bar in it is: a 15m
 *  candle built from a still-forming minute is still forming, and painting it as final would put
 *  a candle on screen that later changes shape. */
export function aggregateCandles(bars: ChartBar[], minutes: Timeframe): Candle[] {
  const span = minutes * 60_000;
  const buckets = new Map<number, Candle>();
  const ordered = [...bars].sort((a, b) => a.start_ms - b.start_ms);

  for (const bar of ordered) {
    const open = Number(bar.open), high = Number(bar.high);
    const low = Number(bar.low), close = Number(bar.close), volume = Number(bar.volume);
    if (![open, high, low, close].every(Number.isFinite)) continue;
    const start = Math.floor(bar.start_ms / span) * span;
    const existing = buckets.get(start);
    if (existing === undefined) {
      buckets.set(start, {
        time: start / 1000, open, high, low, close,
        volume: Number.isFinite(volume) ? volume : 0,
        confirmed: bar.confirmed,
      });
      continue;
    }
    existing.high = Math.max(existing.high, high);
    existing.low = Math.min(existing.low, low);
    existing.close = close;
    existing.volume += Number.isFinite(volume) ? volume : 0;
    existing.confirmed = existing.confirmed && bar.confirmed;
  }
  return [...buckets.values()].sort((a, b) => a.time - b.time);
}

/** Volume bars coloured by the candle's direction, which is the only reading of "buying or
 *  selling pressure" the public feed actually supports. */
export function volumeSeries(candles: Candle[], up: string, down: string) {
  return candles.map(candle => ({
    time: candle.time, value: candle.volume,
    color: candle.close >= candle.open ? up : down,
  }));
}

export type ChartOverlay = { id: string; price: number; label: string; kind: "ENTRY" | "MARK" | "LIQUIDATION" };

/** Price lines the chart draws over the candles.
 *
 *  Only lines that correspond to a real number in the account are produced. Target and stop are
 *  deliberately absent: there is no strategy in this system yet, so any level drawn for them
 *  would be invented, and an invented line on a price chart reads exactly like a real one.
 *  The return type is a list so those can be appended later without changing the chart. */
export function positionOverlays(state: Pick<CryptoState, "account" | "quote">): ChartOverlay[] {
  const account = state.account;
  const overlays: ChartOverlay[] = [];
  const mark = num(state.quote?.mark_price);
  if (mark != null) overlays.push({ id: "mark", price: mark, label: "Mark", kind: "MARK" });
  if (!account || account.position_side === null) return overlays;
  const entry = num(account.avg_entry);
  if (entry != null) overlays.push({ id: "entry", price: entry, label: "진입", kind: "ENTRY" });
  const liquidation = num(account.liquidation_price);
  if (liquidation != null && liquidation > 0) {
    overlays.push({ id: "liquidation", price: liquidation, label: "청산", kind: "LIQUIDATION" });
  }
  return overlays;
}

/** Return on the margin actually committed, which is what a leveraged operator reads as "how am
 *  I doing" - not return on notional, which at 10x looks ten times calmer than the position is. */
export const positionReturnPct = (state: Pick<CryptoState, "account">) => {
  const account = state.account;
  if (!account || account.position_side === null) return null;
  const margin = num(account.used_margin);
  const unrealized = num(account.unrealized_pnl);
  if (margin == null || unrealized == null || margin === 0) return null;
  return (unrealized / margin) * 100;
};

export const holdingDuration = (openedMs: number | null | undefined, nowMs: number) => {
  if (openedMs == null) return null;
  const seconds = Math.max(0, Math.floor((nowMs - openedMs) / 1000));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return h > 0 ? `${h}시간 ${m}분` : m > 0 ? `${m}분 ${s}초` : `${s}초`;
};

/** The one place that decides whether the screen may be traded on.
 *
 *  DISCONNECTED means the socket is down or no quote has arrived; the engine refuses orders
 *  outright in that state. STALE means the socket is up but ticks stopped, which the engine
 *  cannot see, so the screen blocks entries itself. Only LIVE arms LONG and SHORT. Closing is
 *  allowed in all three: reducing risk on a bad screen is the safer mistake. */
export type FeedStatus = "LIVE" | "STALE" | "DISCONNECTED";
export const FEED_STATUS_LABELS: Record<FeedStatus, string> = {
  LIVE: "실시간", STALE: "시세 지연", DISCONNECTED: "연결 끊김",
};
export const STALE_AFTER_SECONDS = 5;

export function feedStatus(state: Pick<CryptoState, "feed" | "quote" | "server_time_ms">): FeedStatus {
  if (!state.feed.connected || state.quote === null) return "DISCONNECTED";
  const age = feedAgeSeconds(state);
  return age == null || age > STALE_AFTER_SECONDS ? "STALE" : "LIVE";
}

export const canEnter = (status: FeedStatus) => status === "LIVE";

/** Age of the book a PnL preview was priced on, judged by the same STALE contract as the feed.
 *  A stale or disconnected terminal withholds the preview; no second threshold is introduced. */
/** Same STALE contract as `feedStatus`, for a route that carries its own clocks: the server's
 *  time against the server's own last receive time. The exchange timestamp is not used for age,
 *  because the two clocks can differ by seconds. */
export function tickAgeSeconds(body: { server_time_ms: number; feed_last_message_ms?: number | null }) {
  return body.feed_last_message_ms == null ? null
    : Math.max(0, (body.server_time_ms - body.feed_last_message_ms) / 1000);
}
export function tickFreshness(body: { server_time_ms: number; feed_last_message_ms?: number | null; feed_connected: boolean }): FeedStatus {
  const age = tickAgeSeconds(body);
  if (!body.feed_connected || age == null) return "DISCONNECTED";
  return age > STALE_AFTER_SECONDS ? "STALE" : "LIVE";
}

export function previewFreshness(state: Pick<CryptoState, "feed" | "quote" | "server_time_ms">,
                                 preview: { quote_ts_ms: number }) {
  const ageSeconds = Math.max(0, Math.round((state.server_time_ms - preview.quote_ts_ms) / 1000));
  const feed = feedStatus(state);
  const status: FeedStatus = feed !== "LIVE" ? feed : ageSeconds > STALE_AFTER_SECONDS ? "STALE" : "LIVE";
  return { status, ageSeconds };
}

/** When the open position was entered, read back from the ledger.
 *
 *  The account snapshot does not carry the entry timestamp and the engine is not being changed
 *  for a display field, so it is recovered from the events the screen already holds: the most
 *  recent POSITION_OPEN with no POSITION_CLOSE after it. Events arrive newest first. */
export function positionOpenedMs(events: LedgerEvent[]): number | null {
  for (const event of events) {
    if (event.event_type === "POSITION_CLOSE") return null;
    if (event.event_type === "POSITION_OPEN") return event.ts_ms;
  }
  return null;
}

/** The virtual account a reset restores. Mirrors DEFAULT_STARTING_CAPITAL_KRW on the backend;
 *  the button sends no amount, so the backend's constant is what actually applies. This is here
 *  only so the confirmation can name the figure the operator is about to get. */
export const RESET_CAPITAL_KRW = 10_000_000;

export const resetCapitalLabel = () => `${RESET_CAPITAL_KRW.toLocaleString("ko-KR")}원`;

/** One line describing a reset, for the ledger: "9,431,200원 → 10,000,000원". */
export const describeReset = (event: LedgerEvent) => {
  const before = event.before_equity_krw as string | undefined;
  const after = event.after_equity_krw as string | undefined;
  return `${krw(before)} → ${krw(after)}`;
};

/** What the header's "현재 손익" is measured from, for the label's tooltip. */
export const resetScopeNote = (performance?: { current_segment?: { reset_count: number } } | null) =>
  (performance?.current_segment?.reset_count ?? 0) > 0
    ? "마지막 가상계좌 초기화 이후"
    : "런 시작 이후";
