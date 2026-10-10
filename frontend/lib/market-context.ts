/** Manual Market Context V1: the shapes the panel API returns, and nothing but formatting.
 *
 *  Three rules this module keeps, because they are the ones a display layer usually breaks.
 *
 *  1. **A null stays a null.** Every quantity the backend could not observe arrives as `null` with
 *     a state word beside it, and the formatters render that as `-` plus the reason. Never `0`:
 *     zero is a real reading of a depth band and of a flow window, and it has to stay
 *     distinguishable from "not observed".
 *  2. **No derived judgement, and no roll-up.** Nothing here combines two layers into a third
 *     reading, ranks one side against the other, or turns a ratio into a word like "pressure".
 *     There is no function that returns an overall state, because there is no overall state: the
 *     backend publishes `rollup: {exists: false}` and this module renders the five layers side by
 *     side.
 *  3. **Colour is never the only carrier of a state.** Every chip renders its word. `stateTone`
 *     exists to add a cue, not to replace the text, and the component tests assert the word.
 *
 *  The panel talks to its own backend on its own port, separate from the terminal API, so the order
 *  path and this viewer cannot share a process. `NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL` points at it.
 */

export const MARKET_CONTEXT_BASE_URL = (
  process.env.NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL || "http://127.0.0.1:8012"
).replace(/\/$/, "");

/** Whether a backend was actually pointed at, as opposed to the development default above.
 *
 *  The default is right for a preview on the machine running the service and wrong everywhere
 *  else: `127.0.0.1:8012` in a production build is the *operator's* laptop, so a panel shipped
 *  without this flag would poll nothing once a second and show dashes. The deployed route reads
 *  this to decide whether the panel exists at all, which keeps a build with no service
 *  configured identical to the screen that is deployed today. */
export const MARKET_CONTEXT_CONFIGURED =
  (process.env.NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL ?? "").trim().length > 0;

/** The collector samples once a second, so this is the fastest poll that can show a new row.
 *  The structure layer is recomputed on the backend once a minute and the derivatives read is
 *  floored at 5 s there, so a 1 s poll costs one journal read and nothing else. */
export const MARKET_CONTEXT_POLL_MS = 1_000;

/** Section 4 of the frozen contract. `UNAVAILABLE` is deliberately not one of these: it means the
 *  layer does not exist for this instrument, which is a different statement from missing data. */
export type LayerState = "LIVE" | "STALE" | "PARTIAL" | "UNKNOWN";
export type LayerStateOrAbsent = LayerState | "UNAVAILABLE";
export type Coverage = "COMPLETE" | "PARTIAL" | "UNKNOWN";
export type WallState = "OK" | "NONE" | "PARTIAL" | "STALE" | "UNKNOWN";
export type VanishState = "CONSUMED_CANDIDATE" | "CANCEL_LIKE" | "UNKNOWN";
export type LayerName = "PRICE" | "LIQUIDITY" | "FLOW" | "DERIVATIVES" | "AUXILIARY";
/** The forward journal's writer state. `REFUSED` and `AUTHORITY_LOST` are the single-writer
 *  lock's two ways of failing closed; both leave the panel itself fully serving. */
export type JournalStatus =
  | "OFF" | "WAITING" | "WRITING" | "STALE" | "ERROR" | "REFUSED" | "AUTHORITY_LOST";
export type Side = "ASK" | "BID";

export const MISSING = "-";

export class MarketContextError extends Error {}

// --------------------------------------------------------------------------- PRICE

export interface NearestLevel {
  side: "RESISTANCE" | "SUPPORT";
  price: number | null;
  distance_pct: number | null;
  lifecycle: string | null;
  origin_kind: string | null;
  strength: number | null;
  touches: number | null;
  confirm_tf: string | null;
  sources: string | null;
  level_id: number | null;
  available: boolean;
  unavailable_reason: string | null;
}

export interface StructureSeries {
  anchor_ms: number;
  series_first_ms: number;
  series_last_open_ms: number;
  series_rows: number;
  publish_from_ms: number;
  publish_to_ms: number;
  published_minutes: number;
  computed_at_ms: number;
  recompute_ms: number;
  fetch_ms: number;
  anchor_rule: string;
  theta_1h: number;
  theta_5m: number;
  repaint_violations: number;
  vanished_levels: number;
  venue: string;
}

export interface PriceLayer {
  state: LayerStateOrAbsent;
  reasons: string[];
  symbol: string;
  series: StructureSeries | null;
  row: {
    bar_open_ms: number; bar_close_ms: number; bar_age_ms: number; close: number;
    trend_structure: string; last_high_label: string | null; last_low_label: string | null;
    active_level_count: number; published_prices: number[];
    atr_5m: number | null; atr_1h: number | null;
    vwap_z: number | null; vwap_position: string | null;
  } | null;
  levels: { resistance: NearestLevel; support: NearestLevel } | null;
  events: {
    breakout_state: string; breakout_level_id: number | null; breakout_at_ms: number | null;
    breakout_age_min: number | null; breakout_confirm_tf: string | null;
    retest_state: string; retest_direction: string | null; retest_level_id: number | null;
    failed_break_state: string;
    sweep_direction: string | null; sweep_at_ms: number | null; sweep_age_min: number | null;
    sweep_level_id: number | null;
  } | null;
  range: { state: string; high: number | null; low: number | null;
           position: number | null } | null;
  prev_session: {
    high: number | null; low: number | null;
    high_distance_pct: number | null; low_distance_pct: number | null;
    in_level_book: boolean; note: string;
  } | null;
  note: string;
}

// --------------------------------------------------------------------------- LIQUIDITY

export interface Wall {
  price: string | null;
  qty_btc: string | null;
  notional_usdt: string | null;
  distance_bps: string | null;
  distance_pct: string | null;
  multiple: string | null;
  coverage: Coverage;
  persistence_ms: number | null;
  own_persistence_ms: number | null;
  carried_persistence_ms: number | null;
  persistence_source: "OWN" | "CARRIED" | null;
  continuity_status: "CARRIED" | "NEW" | null;
  not_carried_reason: string | null;
  carried_members: number | null;
  bin_low: string | null;
  bin_high: string | null;
  bin_members: number | null;
  bin_candidate_notional_usdt: string | null;
  bin_candidate_notional_is_lower_bound: boolean | null;
  values_as_of: string | null;
  generation: number | null;
  first_seen_ms: number | null;
}

export interface DepthBand {
  band_pct: string;
  coverage: Coverage;
  notional: string | null;
  qty: string | null;
  observed_notional: string | null;
  observed_qty: string | null;
  is_lower_bound: boolean;
  levels: number | null;
  price_low: string | null;
  price_high: string | null;
  imbalance_usdt: string | null;
}

export interface LiquiditySide {
  side: Side;
  wall_state: WallState;
  wall_state_reason: string | null;
  nearest_wall: Wall | null;
  coverage: Coverage;
  candidates_total: number | null;
  walls_selected: number | null;
  walls_shown: number | null;
  walls: Wall[];
  depth: DepthBand[];
}

export interface LiquidityLayer {
  state: LayerStateOrAbsent;
  reasons: string[];
  symbol: string;
  sides: Record<Side, LiquiditySide> | null;
  book: {
    mid: string | null; best_bid: string | null; best_ask: string | null;
    spread_bps: string | null;
    observed_low: string | null; observed_high: string | null;
    observed_low_pct: string | null; observed_high_pct: string | null;
    observed_symmetric_pct: string | null; snapshot_limit: number | null;
  } | null;
  band_imbalance: {
    band_pct: string; coverage: Coverage; notional_bid: string | null;
    notional_ask: string | null; is_lower_bound: boolean;
  } | null;
  coverage: {
    bands: Array<Record<string, unknown>> | null;
    complete_bands: string[] | null; partial_bands: string[] | null;
    lower_bound_marker: string | null; lower_bounds_identical: boolean | null;
  } | null;
  walls: {
    coverage: Coverage; verified_by: string | null; unverified_reason: string | null;
    source: string | null; candidate_count: number | null; walls_selected: number | null;
    carried: number | null; rule: string | null; continuity_rule: string | null;
    filter_min_notional_usdt: string | null;
  } | null;
  journal: {
    root: string | null; session_id: string | null; session_age_ms: number | null;
    session_ended: boolean | null; sample_receive_ms: number | null;
    journal_age_ms: number | null; feed_state: string | null; depth_age_ms: number | null;
    book_state: string | null; exchange: string | null; symbol: string | null;
    /** The collector's resnapshot generation. What makes one reading's walls comparable with the
     *  previous reading's, and `null` on a session with no state checkpoint. */
    book_generation: number | null;
  } | null;
  presence_base_rate: {
    ask_pct: number; bid_pct: number; mean_per_side_when_present: number; window: string;
  } | null;
  observed_range_note: string;
  presence_note: string;
}

// --------------------------------------------------------------------------- FLOW

export interface FlowWindow {
  window: string;
  state: LayerState;
  coverage: Coverage;
  coverage_reason: string | null;
  coverage_age_ms: number | null;
  trades: number | null;
  buy_btc: string | null;
  sell_btc: string | null;
  buy_usdt: string | null;
  sell_usdt: string | null;
  net_btc: string | null;
  net_usdt: string | null;
  imbalance_usdt: string | null;
  imbalance_btc: string | null;
  is_lower_bound: boolean | null;
}

export interface VanishedWall {
  state: VanishState;
  reason: string;
  side: Side;
  bin_low: string;
  bin_high: string;
  price: string | null;
  notional_usdt: string | null;
  last_seen_ms: number;
  observed_at_ms: number | null;
  path_samples: number;
  mid_path_touched_bin: boolean | null;
  order_identity_proven: false;
}

export interface FlowLayer {
  state: LayerStateOrAbsent;
  reasons: string[];
  symbol: string;
  windows: Record<string, FlowWindow> | null;
  aggressor_rule: string | null;
  clock: string | null;
  trade_stream: {
    connected: boolean; age_ms: number | null; last_receive_ms: number | null;
    stale_ms: number | null; state: LayerState;
    age_measured_from: string; journal_state: LayerState;
  } | null;
  vanished: {
    this_reading: VanishedWall[]; recent: VanishedWall[];
    totals: Record<string, number>; tracked_bins: number; path_samples: number;
  } | null;
  absorption: {
    state: "ABSORPTION_CANDIDATE" | "NONE"; reason: string | null; side?: Side;
    window?: string; aggressive_usdt?: string; wall_notional_usdt?: string | null;
    wall_price?: string | null; bin_low?: string | null; bin_high?: string | null;
    wall_persistence_ms?: number | null; order_identity_proven: false; note: string;
  } | null;
  vanish_base_rate: {
    ask_touched_bin_pct: number; bid_touched_bin_pct: number; window: string;
  } | null;
  vanish_note: string;
  absorption_note: string;
  imbalance_note?: string | null;
}

// --------------------------------------------------------------------------- DERIVATIVES

export interface OiChangeWindow {
  available: boolean;
  unavailable_reason: string | null;
  base?: string | null;
  base_pct?: string | null;
  value_usdt?: string | null;
  from_bucket_ms?: number;
  to_bucket_ms?: number;
}

export interface DerivativesLayer {
  state: LayerStateOrAbsent;
  reasons: string[];
  symbol: string;
  read_at_ms?: number;
  open_interest: {
    base: string | null; venue_time_ms: number | null; age_ms: number | null;
    unit: string; available: boolean; unavailable_reason: string | null;
  } | null;
  open_interest_change: {
    available: boolean; unavailable_reason: string | null;
    windows: Record<string, OiChangeWindow | null>;
    period: string; buckets: number; newest_bucket_ms?: number;
    newest_sum_open_interest?: string | null;
    newest_sum_open_interest_value?: string | null;
    source?: string;
  } | null;
  funding: {
    last_rate: string | null; last_rate_pct: string | null; interest_rate: string | null;
    next_funding_time_ms: number | null; venue_time_ms: number | null; age_ms: number | null;
    available: boolean; unavailable_reason: string | null;
  } | null;
  basis: {
    mark_price: string | null; index_price: string | null;
    estimated_settle_price: string | null; basis_usdt: string | null;
    premium_pct: string | null; formula: string; venue_time_ms: number | null;
    age_ms: number | null; available: boolean; unavailable_reason: string | null;
  } | null;
  note: string;
}

// --------------------------------------------------------------------------- AUXILIARY

export interface AuxiliaryLayer {
  state: LayerStateOrAbsent;
  /** False for a non-BTC instrument: the rows are absent, not empty. */
  rendered: boolean;
  symbol: string;
  reasons: string[];
  c1: {
    weight: "auxiliary"; state: LayerState; reasons: string[];
    root: string; root_exists: boolean;
    last_decision_age_ms?: number | null; stale_ms?: number;
    engine_state: Record<string, unknown> | null; engine_state_age_ms: number | null;
    latest_signal: Record<string, unknown> | null; latest_signal_age_ms: number | null;
    signal_records: number; latest_c1x: Record<string, unknown> | null; c1x_records: number;
    read_mode: string; contract_evaluated: false;
  } | null;
  directional: {
    weight: "weak_auxiliary"; state: LayerState; reasons: string[]; artifact: string;
    verdict: string | null; usable_horizons: number[] | null; weak_horizons: number[] | null;
    horizons: Record<string, {
      horizon_min: number; samples: number | null; first: string | null; last: string | null;
      buckets: Record<string, { auc: number | null; brier_skill: number | null;
                                base_rate: number | null;
                                calibration_error: number | null }>;
    }> | null;
    bounds: Array<{ study: string; record: string; finding: string }>;
    computed_for_now: false;
    inference_run?: false;
    note: string;
  } | null;
  note: string;
}

// --------------------------------------------------------------------------- the payload

export interface MarketContextPayload {
  panel_version: string;
  mode: string;
  contract: {
    contract_version: string; contract_path: string; contract_sha256: string | null;
    contract_sha256_recorded: string | null; agrees: boolean;
  };
  server_time_ms: number;
  symbol: string;
  supported_symbols: string[];
  symbol_support: Record<string, { supported: boolean; reason: string | null }>;
  layer_order: LayerName[];
  layer_states: Record<LayerName, LayerStateOrAbsent>;
  layers: {
    PRICE: PriceLayer; LIQUIDITY: LiquidityLayer; FLOW: FlowLayer;
    DERIVATIVES: DerivativesLayer; AUXILIARY: AuxiliaryLayer;
  };
  /** Always `{exists: false}`. Published rather than omitted so that "there is no joint reading"
   *  is a statement from the backend and not an absence this screen could quietly fill in. */
  rollup: { exists: false; reason: string; measurement: string; note: string };
  freshness_bounds: Record<string, number>;
  notes: Record<string, string>;
  missing_value: string;
  panel: {
    started_ms: number; anchor_ms: number; anchor_days: number; polls: number;
    structure_recomputes: number;
  };
  journal: {
    enabled: boolean; root: string | null; root_env: string; records_written: number;
    symbols_written?: string[];
    last_written_ms: number | null; last_path: string | null; last_error: string | null;
    skipped_as_duplicate: number; record: string; schema_version: number; cadence: string;
    linked_to_orders: false; read_back_by_the_panel: false; note: string;
    wrote_this_poll: boolean;
    /** The single-writer lock's verdict, as one word off a closed list. Optional on the type so
     *  a panel served by an older build still renders; the component falls back to the `enabled`
     *  flag it used before this field existed. */
    status?: JournalStatus;
    states?: JournalStatus[];
    stale_after_ms?: number;
    writer?: {
      writable: boolean; session_id: string; refusal: JournalStatus | null;
      blocked_writes: number; single_writer: true; fail_closed: true;
      /** Always `false`. The journal losing its lock stops the tape and nothing else - every
       *  route still answers and every layer still renders. */
      blocks_the_panel: false;
      recovery: string;
      lock: {
        path: string; held: boolean; pid: number | null; session_id: string | null;
        acquired_ms: number | null; inode: number | null; lock_version: string;
        holder: Record<string, unknown> | null;
      } | null;
    };
  };
  cost: { elapsed_ms: number; structure_recompute_ms: number | null };
}

export interface PanelQuery {
  symbol: string;
  minNotionalUsdt?: string;
}

export async function fetchPanel(query: PanelQuery,
                                 signal?: AbortSignal): Promise<MarketContextPayload> {
  const params = new URLSearchParams({ symbol: query.symbol });
  if (query.minNotionalUsdt) params.set("min_notional_usdt", query.minNotionalUsdt);
  const response = await fetch(`${MARKET_CONTEXT_BASE_URL}/api/market-context/panel?${params}`,
                               { signal, cache: "no-store" });
  if (!response.ok) {
    throw new MarketContextError(`패널 응답 ${response.status}`);
  }
  return (await response.json()) as MarketContextPayload;
}

// --------------------------------------------------------------------------- formatting

export function num(value: string | number | null | undefined, digits = 2): string {
  if (value == null) return MISSING;
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return MISSING;
  return parsed.toLocaleString("en-US", { minimumFractionDigits: digits,
                                          maximumFractionDigits: digits });
}

export function usdt(value: string | number | null | undefined, digits = 0): string {
  return value == null ? MISSING : `${num(value, digits)} USDT`;
}

export function btc(value: string | number | null | undefined): string {
  return value == null ? MISSING : `${num(value, 3)} BTC`;
}

export function price(value: string | number | null | undefined): string {
  return value == null ? MISSING : num(value, 1);
}

/** A percentage with its sign kept. The sign is the whole content of a distance: a level 0.1%
 *  above and one 0.1% below are different places to be. */
export function signedPct(value: string | number | null | undefined, digits = 3): string {
  if (value == null) return MISSING;
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return MISSING;
  const sign = parsed > 0 ? "+" : "";
  return `${sign}${num(parsed, digits)}%`;
}

export function pct(value: string | number | null | undefined, digits = 3): string {
  return value == null ? MISSING : `${num(value, digits)}%`;
}

export function bps(value: string | number | null | undefined): string {
  return value == null ? MISSING : `${num(value, 2)} bp`;
}

export function duration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return MISSING;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

export function clockKst(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return MISSING;
  return new Date(ms).toLocaleTimeString("ko-KR", { hour12: false, timeZone: "Asia/Seoul" });
}

/** Tone for a layer state. An **addition** to the word, never a replacement for it: section 4 of
 *  the contract forbids colour from carrying a state by itself, and the component always renders
 *  `STATE_LABELS[state]` beside this class. */
export function stateTone(state: LayerStateOrAbsent): string {
  switch (state) {
    case "LIVE": return "tone-success";
    case "PARTIAL": return "tone-warning";
    case "STALE": return "tone-danger";
    default: return "tone-neutral";
  }
}

export const STATE_LABELS: Record<LayerStateOrAbsent, string> = {
  LIVE: "LIVE", STALE: "STALE", PARTIAL: "PARTIAL", UNKNOWN: "UNKNOWN",
  UNAVAILABLE: "UNAVAILABLE",
};

export const LAYER_LABELS: Record<LayerName, string> = {
  PRICE: "PRICE 가격구조", LIQUIDITY: "LIQUIDITY 호가", FLOW: "FLOW 체결",
  DERIVATIVES: "DERIVATIVES 파생", AUXILIARY: "AUXILIARY 참고",
};

export const WALL_STATE_LABELS: Record<WallState, string> = {
  OK: "벽 있음", NONE: "벽 없음 (완전관측)", PARTIAL: "하한값 (부분관측)",
  STALE: "저널 정지", UNKNOWN: "미관측",
};

export function wallStateTone(state: WallState): string {
  switch (state) {
    case "OK": return "tone-success";
    // NONE is not success and not a warning: it is a complete reading that found nothing, which
    // is information. Neutral keeps it from reading as either good news or a fault.
    case "NONE": return "tone-neutral";
    case "PARTIAL": return "tone-warning";
    case "STALE": return "tone-danger";
    default: return "tone-neutral";
  }
}

export const VANISH_LABELS: Record<VanishState, string> = {
  CONSUMED_CANDIDATE: "소비 후보", CANCEL_LIKE: "취소로 보임", UNKNOWN: "판정 불가",
};

export const SIDE_LABELS: Record<Side, string> = {
  ASK: "ASK 매도 (mid 위)", BID: "BID 매수 (mid 아래)",
};

export const COVERAGE_LABELS: Record<Coverage, string> = {
  COMPLETE: "COMPLETE", PARTIAL: "PARTIAL 하한", UNKNOWN: "UNKNOWN",
};

export function coverageTone(coverage: Coverage): string {
  return coverage === "COMPLETE" ? "tone-success"
    : coverage === "PARTIAL" ? "tone-warning" : "tone-neutral";
}

/** Why a layer is not LIVE, in the operator's language. An unmapped code is shown raw rather than
 *  swallowed: a reason nobody has translated yet still has to reach the screen. */
export const REASON_LABELS: Record<string, string> = {
  NOT_CALIBRATED_FOR_SYMBOL: "이 심볼로 캘리브레이션되지 않음",
  COLLECTOR_IS_BTC_ONLY: "수집기가 BTCUSDT 전용",
  RESEARCH_IS_BTC_ONLY: "연구가 BTCUSDT 전용",
  LAST_CLOSED_BAR_OLDER_THAN_GRACE: "마지막 확정봉이 허용 지연 초과",
  REPAINT_AUDIT_NOT_CLEAN: "repaint 감사 미통과",
  NO_SERIES_YET: "시리즈 미적재",
  JOURNAL_OLDER_THAN_FRESHNESS_BOUND: "저널이 신선도 상한 초과",
  JOURNAL_NOT_READABLE: "저널을 읽을 수 없음",
  TRADE_STREAM_NOT_CONNECTED: "체결 스트림 미연결",
  TRADE_STREAM_OLDER_THAN_FRESHNESS_BOUND: "체결 스트림 신선도 미달",
  SESSION_ENDED: "수집기 세션 종료됨",
  JOURNAL_STALE: "저널이 갱신되지 않음",
  SAMPLE_NOT_CURRENT: "샘플이 현재가 아님",
  NO_DERIVED_SAMPLE: "수집기 샘플 없음",
  BOOK_UNSYNCED: "책 재동기화 중",
  MS_V0_ROOT_NOT_SET: "MS_V0_ROOT 미설정",
  ROOT_NOT_FOUND: "저널 디렉터리 없음",
  NO_SESSION_RECORDED: "기록된 세션 없음",
  C1_STORE_EMPTY_OR_ABSENT: "C1 저장소 비어 있음",
  C1_ENGINE_LAST_DECIDED_OLDER_THAN_FRESHNESS_BOUND: "C1 엔진 최근 판단이 오래됨",
  C1_STATE_HAS_NO_LAST_DECISION: "C1 상태에 최근 판단 없음",
  ARTIFACT_NOT_READABLE: "동결 아티팩트를 읽을 수 없음",
  VENUE_TIMESTAMP_OLDER_THAN_FRESHNESS_BOUND: "거래소 타임스탬프가 오래됨",
  NO_PUBLISHED_LEVEL_ON_THIS_SIDE: "이 방향에 발행된 레벨 없음",
  NOT_OBSERVED: "미관측",
  NO_BREAKOUT_RECORDED: "기록된 돌파 없음",
  NO_SWEEP_RECORDED: "기록된 스윕 없음",
  NO_PREVIOUS_SESSION: "전일 세션 없음",
  NO_SIGNAL_RECORDED: "기록된 시그널 없음",
  FETCH_FAILED: "읽기 실패",
  NOT_ENOUGH_CLOSED_BUCKETS: "확정 버킷 부족",
  HISTORY_NOT_ASCENDING: "거래소 응답 순서 비정상",
  BUCKET_HAS_NO_VALUE: "버킷에 값 없음",
  NO_HISTORY: "이력 없음",
  NOT_READ_YET: "아직 읽지 않음",
  VIEWER_UNREADABLE: "뷰어를 읽을 수 없음",
  NO_SERIES_YET_FOR_THIS_SYMBOL: "이 심볼의 시리즈 없음",
  NO_CANDIDATE_QUALIFIES_UNDER_LM_WALL_V2: "lm-wall.v2 기준 통과 후보 없음",
  WALL_SET_PARTIAL: "벽 집합이 부분 관측",
  WALL_SET_UNKNOWN: "벽 집합 복원 불가",
  NO_READING: "판독 없음",
  "signals.jsonl:FILE_NOT_FOUND": "signals.jsonl 없음",
  "c1x.jsonl:FILE_NOT_FOUND": "c1x.jsonl 없음",
  NO_PREVIOUS_READING: "이전 판독 없음",
  SESSION_BOUNDARY: "세션 경계",
  RESNAPSHOT_GENERATION_CHANGED: "재스냅샷으로 generation 변경",
  BOOK_NOT_SYNCED: "책 미동기",
  PATH_GAP_WIDER_THAN_SAMPLE_INTERVAL: "가격 경로에 공백",
  SAME_SAMPLE_AS_PREVIOUS_READING: "이전과 동일 샘플",
  NO_MID_PATH_BETWEEN_READINGS: "두 판독 사이 mid 경로 없음",
  MID_PATH_REACHED_THE_BIN: "mid 경로가 bin에 닿음",
  MID_PATH_NEVER_REACHED_THE_BIN: "mid 경로가 bin에 닿지 않음",
  FLOW_WINDOW_NOT_COMPLETE: "플로우 창이 COMPLETE 아님",
  FLOW_WINDOW_HAS_NO_USDT_TOTALS: "플로우 창에 USDT 합계 없음",
  NO_QUALIFYING_WALL_ON_ASK: "ASK에 기준 통과 벽 없음",
  NO_QUALIFYING_WALL_ON_BID: "BID에 기준 통과 벽 없음",
  WALL_HAS_NO_MEASURED_NOTIONAL: "벽 명목값 미측정",
  AGGRESSIVE_FLOW_BELOW_WALL_NOTIONAL: "공격적 체결이 벽 명목 미달",
  WALL_NOT_PRESENT_IN_PREVIOUS_READING: "이전 판독에 벽 없음",
  AGGRESSIVE_FLOW_AT_OR_ABOVE_WALL_NOTIONAL_AND_WALL_STILL_PRESENT:
    "공격적 체결 ≥ 벽 명목이고 벽 유지",
};

export function reasonLabel(code: string): string {
  if (REASON_LABELS[code]) return REASON_LABELS[code];
  const [head, ...rest] = code.split(":");
  if (rest.length && REASON_LABELS[head]) return `${REASON_LABELS[head]} (${rest.join(":")})`;
  return code;
}

export function reasonText(reasons: string[] | null | undefined): string {
  if (!reasons || reasons.length === 0) return "";
  return reasons.map(reasonLabel).join(" · ");
}

/** The trend label as the R1 engine publishes it, rendered as a structural word.
 *
 *  Deliberately not translated into a direction. `BULLISH` here is a statement about the sequence
 *  of swing highs and lows, not a recommendation, and the panel shows the swing labels beside it
 *  so the reader can see what the word is made of. */
export const TREND_LABELS: Record<string, string> = {
  BULLISH: "고점·저점 상승", BEARISH: "고점·저점 하락", NEUTRAL_STRUCTURE: "구조 혼재",
  UNDEFINED: "구조 미확정",
};

export function trendLabel(code: string | null | undefined): string {
  if (!code) return MISSING;
  return TREND_LABELS[code] ? `${code} (${TREND_LABELS[code]})` : code;
}

export const RANGE_LABELS: Record<string, string> = {
  IN_RANGE: "레인지 내부", NO_RANGE: "레인지 없음", RANGE_BROKEN_UP: "레인지 상방 이탈",
  RANGE_BROKEN_DOWN: "레인지 하방 이탈",
};

export function rangeLabel(code: string | null | undefined): string {
  if (!code) return MISSING;
  return RANGE_LABELS[code] ?? code;
}

export const RETEST_LABELS: Record<string, string> = {
  NONE: "없음", PENDING: "재방문 대기", RETESTED: "재방문 확인", BROKEN: "돌파 유지",
  FAILED: "되돌림", EXPIRED: "만료",
};

export function retestLabel(code: string | null | undefined): string {
  if (!code) return MISSING;
  return RETEST_LABELS[code] ?? code;
}

// --------------------------------------------------------------------------- structure replay
//
// The preview has to be checked near resistance, near support, on a breakout and inside a range,
// and the market does not produce those on request. These two reads turn that into a navigation
// over minutes the panel has already published, rather than a wait.
//
// Only the structure layer is replayed, and the payload says so. The book and flow layers have no
// reassembled history in this viewer and the derivatives endpoints have none at this resolution;
// four live layers beside one historical one under a single timestamp is the worst thing a
// context panel can do, so the replay response carries no other layer at all.

export type StructureCase =
  | "NEAR_RESISTANCE" | "NEAR_SUPPORT" | "BREAKOUT_FRESH" | "IN_RANGE"
  | "RETEST_OPEN" | "FAILED_BREAK" | "NO_LEVEL_ABOVE" | "NO_LEVEL_BELOW";

export interface StructureCaseSample {
  info_ts: number;
  close: number;
  res_price: number | null;
  res_distance_pct: number | null;
  sup_price: number | null;
  sup_distance_pct: number | null;
  range_state: string;
  retest_state: string;
  breakout_age_min: number | null;
  failed_break_state: string;
}

export interface StructureStates {
  available: boolean;
  unavailable_reason: string | null;
  published_minutes?: number;
  publish_from_ms?: number;
  publish_to_ms?: number;
  cases: Record<StructureCase, {
    minutes: number;
    first: StructureCaseSample | null;
    samples: StructureCaseSample[];
    last: StructureCaseSample | null;
  }>;
}

export interface StructureReplay {
  panel_version: string;
  mode: "READ_ONLY_STRUCTURE_REPLAY";
  replay: {
    is_replay: true; info_ts: number; layers_replayed: string[];
    layers_not_replayed: string[]; reason: string;
  };
  symbol: string;
  server_time_ms: number;
  layers: { PRICE: PriceLayer };
  notes: Record<string, string>;
}

export const CASE_LABELS: Record<StructureCase, string> = {
  NEAR_RESISTANCE: "저항 근처", NEAR_SUPPORT: "지지 근처", BREAKOUT_FRESH: "돌파 직후",
  IN_RANGE: "레인지 내부", RETEST_OPEN: "재방문 진행", FAILED_BREAK: "실패 돌파",
  NO_LEVEL_ABOVE: "위쪽 레벨 없음", NO_LEVEL_BELOW: "아래쪽 레벨 없음",
};

export async function fetchStructureStates(signal?: AbortSignal): Promise<StructureStates> {
  const response = await fetch(
    `${MARKET_CONTEXT_BASE_URL}/api/market-context/structure/states`,
    { signal, cache: "no-store" });
  if (!response.ok) throw new MarketContextError(`상태 색인 응답 ${response.status}`);
  return (await response.json()) as StructureStates;
}

export async function fetchStructureReplay(infoTs: number,
                                           signal?: AbortSignal): Promise<StructureReplay> {
  const response = await fetch(
    `${MARKET_CONTEXT_BASE_URL}/api/market-context/structure/replay?info_ts=${infoTs}`,
    { signal, cache: "no-store" });
  if (!response.ok) throw new MarketContextError(`재생 응답 ${response.status}`);
  return (await response.json()) as StructureReplay;
}
