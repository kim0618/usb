/** Liquidity Map V1 preview: the shapes the preview API returns, and nothing but formatting.
 *
 *  Two rules this module keeps, because they are the ones a display layer usually breaks:
 *
 *  1. **A null stays a null.** Every quantity the backend could not observe arrives as `null`
 *     with a coverage word beside it, and the formatters here render that as a dash plus the
 *     reason - never as `0`, and never as the most recent value that happened to be in state.
 *     Zero is a real reading of this book (a band with nothing resting in it) and has to stay
 *     distinguishable from "not observed".
 *  2. **No derived judgement.** Nothing here combines two numbers into a third, ranks a side
 *     against the other or turns a ratio into a word like "pressure". The preview shows the
 *     collector's figures; direction is the operator's call and the next step's problem.
 *
 *  The preview talks to its own backend on its own port, separate from the terminal API, so that
 *  the order path and this viewer cannot share a process. `NEXT_PUBLIC_LIQUIDITY_MAP_BASE_URL`
 *  points at it.
 */

export const LIQUIDITY_MAP_BASE_URL = (
  process.env.NEXT_PUBLIC_LIQUIDITY_MAP_BASE_URL || "http://127.0.0.1:8011"
).replace(/\/$/, "");

/** The collector samples once a second, so this is the fastest poll that can ever show a new
 *  row. Anything quicker re-reads the same sample and only costs the journal more reads. */
export const LIQUIDITY_MAP_POLL_MS = 1_000;

export type Coverage = "COMPLETE" | "PARTIAL" | "UNKNOWN";
export type FeedState = "LIVE" | "STALE" | "SYNCING" | "NO_DATA";
export type Side = "ASK" | "BID";

export interface BandDepth {
  band_pct: string;
  coverage: Coverage;
  qty: string | null;
  notional: string | null;
  observed_qty: string | null;
  observed_notional: string | null;
  is_lower_bound: boolean;
  levels: number | null;
  price_low: string | null;
  price_high: string | null;
  imbalance_btc: string | null;
  imbalance_usdt: string | null;
}

/** One wall as the frozen `lm-wall.v2` rule selected it. A price bin, represented by its
 *  largest-notional member, with the bin's own membership carried so that one structure is never
 *  drawn as nine lines. `bin_candidate_notional_usdt` is a lower bound over qualifying candidates
 *  only and is never the bin's liquidity. */
export interface WallV2 {
  side: Side;
  price: string;
  qty_btc: string | null;
  notional_usdt: string | null;
  multiple: string | null;
  local_average: string | null;
  neighbours: number | null;
  distance_bps: string;
  /** The span R4 was applied to: the longer of the two below. */
  observed_persistence_ms: number;
  /** The candidate's own span, which a resnapshot resets to zero. */
  own_persistence_ms: number;
  /** The span over the continuity-proven life, or null when nothing was proven. */
  carried_persistence_ms: number | null;
  persistence_source: "OWN" | "CARRIED";
  continuity_status: "CARRIED" | "NEW";
  not_carried_reason: string | null;
  continuity_first_seen_ms: number | null;
  carried_members: number;
  continuity_refreshes: number | null;
  continuity_proof: string | null;
  first_seen_ms: number | null;
  generation: number | null;
  coverage: Coverage;
  values_as_of: string;
  bin_low: string | null;
  bin_high: string | null;
  bin_members: number;
  bin_candidate_notional_usdt: string | null;
  bin_candidate_notional_is_lower_bound: boolean;
  persistence_is_sampled_span: boolean;
  order_identity_proven: boolean;
}

/** Why candidates were not selected, counted per rule. Published so that "no walls" can never be
 *  confused with "nothing was looked for". */
export interface SelectionView {
  considered: number;
  passed: number;
  rejected: Record<string, number>;
  bins: number;
  grouped_away: number;
  values_as_of: string;
}

export interface WallRuleView {
  rule_version: string;
  identity: { rule_version: string; status: string; rule_sha256: string | null;
              recorded_sha256: string | null; sha256_agrees: boolean | null };
  is_frozen_not_tunable: boolean;
  changes_data_contract: boolean;
  min_notional_usdt: string;
  min_multiple: string;
  min_distance_bps: string;
  min_persistence_ms: number;
  bin_width_usdt: string;
  bin_width_ticks: number;
  bin_rule: string;
  bin_representative: string;
  evaluation_order: string;
  v0_rule: { min_multiple: string; min_neighbours: number; neighbours_per_side: number;
             band_pct: string; bin_rule: string };
}

export interface StateFileView {
  path: string;
  present: boolean;
  usable: boolean;
  unusable_reason: string | null;
  state_version: string | null;
  written_ms: number | null;
  age_ms: number | null;
  stale_ms: number;
  bytes: number;
  seq: number | null;
  active_count: number | null;
  items: number;
  truncated: boolean;
  is_authority: boolean;
  authority: string;
}

/** The observed interval, stated before anything is read off it. Three of the four contract bands
 *  are permanently PARTIAL on a `limit=1000` snapshot, so every value they carry is a lower bound
 *  and the unobserved region outside the interval is `null` - never a zero. */
export interface CoverageBand {
  band_pct: string;
  coverage: Coverage;
  bid_coverage: Coverage;
  ask_coverage: Coverage;
  is_lower_bound: boolean;
  qty_bid: string | null;
  qty_ask: string | null;
  observed_qty_bid: string | null;
  observed_qty_ask: string | null;
  observed_notional_bid: string | null;
  observed_notional_ask: string | null;
}

export interface CoverageView {
  observed_low_pct: string | null;
  observed_high_pct: string | null;
  observed_symmetric_pct: string | null;
  known_low: string | null;
  known_high: string | null;
  snapshot_limit: number;
  bands: CoverageBand[];
  complete_bands: string[];
  partial_bands: string[];
  lower_bound_marker: string;
  lower_bounds_identical: boolean;
  unobserved_is_null_not_zero: boolean;
  observed_range_note: string;
  lower_bound_note: string;
  identical_bounds_note: string;
  scope_note: string;
}

/** The collector's resnapshot policy, reported rather than restated: a viewer printing its own
 *  idea of the policy would keep printing it after the collector's changed. */
export interface ResnapshotView {
  available: boolean;
  unavailable_reason: string | null;
  stale?: boolean;
  policy?: string;
  fixed_interval_polling?: boolean;
  protected_band_bps?: string;
  coverage_margin_bps?: string | null;
  coverage_trigger_bps?: string;
  coverage_cooldown_s?: number;
  coverage_cooldown_remaining_s?: number | null;
  safety_refresh_s?: number;
  snapshot_age_s?: number | null;
  coverage_refreshes?: number;
  safety_refreshes?: number;
  refreshes_rejected?: number;
  resyncs?: number;
  generation?: number;
  pending_reason?: string | null;
  cost_note?: string;
  /** V1.3: a voluntary refresh is staged on a second book and swapped in at an identical update
   *  id, so it never rolls the live book back and a failed attempt costs nothing. */
  install?: string;
  refreshes_applied?: number;
  refresh_failures_consecutive?: number;
  refresh_retry_backoff_s?: number;
  refresh_retry_in_s?: number | null;
  refresh_deadline_ms?: number;
  failed_refresh_consumes_cooldown?: boolean;
  refresh_in_progress?: {
    trigger: string; state: string; outcome: string | null; failure: string | null;
    snapshot_update_id: number | null; round_trip_ms: number | null;
    buffered_at_snapshot: number; replayed_frames: number;
    discarded_older_than_snapshot: number; attachment: string | null;
    frames_after_snapshot: number; elapsed_ms: number | null;
  } | null;
  note: string;
}

/** The HARD/SOFT ledger the collector publishes, plus this viewer's own frozen rule identity.
 *
 *  The three counts are read together or not at all. A reader shown only `wall_carried_total`
 *  cannot tell a book whose walls genuinely rested from a rule that is carrying everything, and
 *  on a ladder the two look the same. */
export interface ContinuityView {
  available: boolean;
  unavailable_reason: string | null;
  rule: {
    rule_version: string;
    identity: { status: string; rule_sha256: string | null; recorded_sha256: string | null;
                sha256_agrees: boolean | null };
    hard_carries_nothing: boolean;
    soft_requires_all_gates: string[];
    soft_window_max_ms: number;
    /** v4: the one case in which that ceiling does not apply, and what it depends on. */
    soft_window_exemption?: string;
    soft_window_exemption_requires?: string[];
    wall_identity_rule: string;
    member_identity_rule: string;
    changes_v2_thresholds: boolean;
    can_only_extend_a_span: boolean;
  };
  note: string;
  active_carried_in_view: number;
  candidates_in_view: number;
  proof?: string;
  window_max_ms?: number;
  gates_required?: string[];
  active_carried?: number | null;
  soft_refreshes?: number | null;
  /** How many of them passed S4 by the replayed-chain exemption rather than by the ceiling. */
  soft_window_exempt?: number | null;
  hard_transitions?: number | null;
  wall_carried_total?: number | null;
  wall_ended_total?: number | null;
  wall_unknown_total?: number | null;
  proofs_superseded?: number | null;
  pending_proof?: Record<string, unknown> | null;
  last_transition?: {
    refresh_type: "HARD" | "SOFT" | null;
    continuity_reason: string | null;
    cause: string | null;
    generation_from: number | null;
    generation_to: number | null;
    receive_ms: number | null;
    candidates_before: number | null;
    /** How many arrived already carrying a proven history. */
    carried_entering: number | null;
    /** And how much of it this transition destroyed: all of it on HARD, and on SOFT only the
     *  carried candidates that did not carry through. Zero on a healthy refresh. */
    carried_lost: number | null;
    eligible: number | null;
    wall_carried: number | null;
    wall_ended: number | null;
    wall_unknown: number | null;
    carried_truncated: boolean | null;
    overlap_check: Record<string, unknown> | null;
    gates: Record<string, boolean> | null;
    window_ms: number | null;
    refresh_trigger: string | null;
    /** Whether the new generation's book was built by replaying every intervening frame onto a
     *  staged copy, or by installing the snapshot over the live book. */
    basis: string | null;
    replayed_frames: number | null;
    chain_preserved: boolean | null;
    /** Which of WITHIN_MAX / EXEMPT_REPLAYED_CHAIN / EXCEEDED_MAX / NOT_MEASURED decided S4. A
     *  carry that used the exemption must never look like one that was inside the ceiling. */
    window_verdict: string | null;
    window_exempt: boolean | null;
  } | null;
}

export interface WallRow {
  side: Side;
  price: string;
  qty_btc: string | null;
  notional_usdt: string | null;
  distance: string | null;
  distance_pct: string | null;
  distance_bps: string | null;
  multiple: string | null;
  local_average: string | null;
  neighbours: number | null;
  first_seen_ms: number | null;
  observed_persistence_ms: number | null;
  persistence_rule: string;
  row_persistence_ms: number | null;
  row_samples: number | null;
  coverage: Coverage;
  generation: number | null;
  continuity_status: "CARRIED" | "NEW";
  continuity_first_seen_ms: number | null;
  carried_persistence_ms: number | null;
  continuity_samples: number | null;
  continuity_refreshes: number | null;
  continuity_origin_generation: number | null;
  persistence_is_sampled_span: boolean;
  order_identity_proven: boolean;
}

export interface SideView {
  side: Side;
  coverage: Coverage;
  nearest_wall: WallV2 | null;
  nearest_unavailable_reason: string | null;
  /** What V0 qualified: the superset, mostly ordinary book. */
  candidates_total: number;
  /** What the frozen rule called a wall. */
  walls_selected: number;
  /** What survived the operator's notional filter - the only one of the three they can move. */
  walls_shown: number;
  selection: SelectionView;
  walls: WallV2[];
  depth: BandDepth[];
}

export interface FlowWindow {
  window: string;
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
  observed_buy_btc: string | null;
  observed_sell_btc: string | null;
  observed_buy_usdt: string | null;
  observed_sell_usdt: string | null;
  is_lower_bound: boolean;
  imbalance_btc: string | null;
  imbalance_usdt: string | null;
}

export interface LiquiditySnapshot {
  preview: { version: string; mode: string; scope: string; sample_interval_s: number };
  source: {
    root: string;
    session_id: string | null;
    session_started_ms: number | null;
    session_age_ms: number | null;
    session_ended: boolean;
    collector_version: string | null;
    symbol: string | null;
    exchange: string | null;
    seq: number | null;
    sample_index: number | null;
    sample_receive_ms: number | null;
    server_time_ms: number;
    journal_age_ms: number | null;
    wall_stream_lag_ms: number | null;
    unavailable_reason?: string;
    read_cost: Record<string, number>;
    contract: { contract_version?: string; sha256_agrees?: boolean | null } | null;
  };
  quality: {
    state: FeedState;
    reasons: string[];
    sample_is_current: boolean;
    book_state: string;
    book_coverage: Coverage;
    trade_state: FeedState;
    depth_age_ms: number | null;
    trade_age_ms: number | null;
    journal_age_ms: number | null;
    lag_ms: number | null;
    lag_state: string;
    generation: number | null;
    levels: number | null;
    last_invalidation: string | null;
    journal_stale_ms: number;
    depth_stale_ms: number;
    trade_stale_ms: number;
  };
  price: {
    best_bid: string | null;
    best_ask: string | null;
    spread: string | null;
    spread_bps: string | null;
    mid: string | null;
    mid_rule: string;
    mark: null;
    mark_unavailable_reason: string;
    known_low: string | null;
    known_high: string | null;
    known_low_pct: string | null;
    known_high_pct: string | null;
  };
  sides: Record<Side, SideView>;
  flow: {
    windows: Record<string, FlowWindow>;
    aggressor_rule: string;
    clock: string;
    trade_stream: { connected: boolean; age_ms: number | null; last_receive_ms: number | null; stale_ms: number };
    imbalance_note: string;
  };
  coverage: CoverageView;
  resnapshot: ResnapshotView;
  continuity: ContinuityView;
  candidates: Record<Side, WallRow[]>;
  walls: {
    coverage: Coverage;
    verified_by: string | null;
    unverified_reason: string | null;
    source: string;
    values_as_of: string;
    candidate_count: number;
    walls_selected: number;
    rejected: Record<string, number>;
    truncated: boolean;
    carried: number;
    continuity_rule: ContinuityView["rule"];
    authority_active: number | null;
    authority_age_ms: number | null;
    reconstructed_at_authority: number | null;
    missing_count: number | null;
    scanned_bytes: number;
    scanned_records: number;
    tail_records: number;
    tail_bytes: number;
    tail_complete: boolean;
    transitions_applied: number;
    state_file: StateFileView | null;
    state_file_note: string;
    filter: { min_notional_usdt: string; is_display_filter_not_rule: boolean; applies_after: string };
    rule: WallRuleView;
    rule_note: string;
    v0_rule_note: string;
    no_verdict_note: string;
  };
  overlay: {
    renderable: boolean;
    suppressed_reason: string | null;
    mid: string | null;
    axis_low: string | null;
    axis_high: string | null;
    axis_rule?: string;
    best_bid?: string | null;
    best_ask?: string | null;
    sell_wall: OverlayWall | null;
    buy_wall: OverlayWall | null;
    walls_suppressed_reason: string | null;
  };
  telemetry: Array<{ seq: number | null; receive_ms: number | null; event: string | null;
                     stream: string | null; reason: string | null;
                     refresh_type?: "HARD" | "SOFT" | null }>;
}

export interface OverlayWall {
  price: string;
  qty_btc: string | null;
  notional_usdt: string | null;
  distance_bps: string;
  bin_low: string | null;
  bin_high: string | null;
  bin_members: number;
  coverage: Coverage;
}

export class LiquidityMapError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = "LiquidityMapError";
  }
}

/** The only query the screen may send. The wall rule is frozen and deliberately not a
 *  parameter: two multiple floors, one frozen and one adjustable, would leave a screen showing
 *  fewer walls unable to say which of them removed something. */
export interface SnapshotQuery {
  minNotionalUsdt?: string;
  wallLimit?: number;
}

export async function fetchSnapshot(query: SnapshotQuery = {},
                                    signal?: AbortSignal): Promise<LiquiditySnapshot> {
  const params = new URLSearchParams();
  if (query.minNotionalUsdt) params.set("min_notional_usdt", query.minNotionalUsdt);
  if (query.wallLimit) params.set("wall_limit", String(query.wallLimit));
  const suffix = params.toString() ? `?${params}` : "";
  let response: Response;
  try {
    response = await fetch(`${LIQUIDITY_MAP_BASE_URL}/api/liquidity-map/snapshot${suffix}`,
                           { signal, cache: "no-store" });
  } catch (error) {
    if ((error as Error)?.name === "AbortError") throw error;
    throw new LiquidityMapError(0, "NETWORK_ERROR",
      "Preview API(기본 127.0.0.1:8011)에 연결할 수 없습니다.");
  }
  const body = await response.json().catch(() => null) as
    (LiquiditySnapshot & { error?: { code?: string; message?: string } }) | null;
  if (!response.ok) {
    throw new LiquidityMapError(response.status, body?.error?.code || "HTTP_ERROR",
      body?.error?.message || `HTTP ${response.status}`);
  }
  if (!body) throw new LiquidityMapError(response.status, "EMPTY_BODY", "빈 응답입니다.");
  return body;
}

// --------------------------------------------------------------------------- formatting

/** The dash a missing value renders as. One character, used everywhere, never a zero. */
export const MISSING = "-";

/** The marker a lower bound carries. A PARTIAL band's number is the smallest it could be, and
 *  the only honest way to print it is with the inequality attached. */
export const LOWER_BOUND = "\u2265";

/** A value whose coverage makes it a lower bound rather than a quantity. */
export function lowerBound(text: string, isLowerBound: boolean): string {
  return isLowerBound && text !== MISSING ? `${LOWER_BOUND} ${text}` : text;
}

/** Distance from mid in basis points. Basis points rather than percent because the whole
 *  observable interval is about 15 of them, and a percent with three decimals reads as noise. */
export function bps(value: string | null | undefined): string {
  return value == null ? MISSING : `${num(value, 2)} bp`;
}

export function seconds(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? MISSING : duration(value * 1000);
}

export function num(value: string | number | null | undefined, digits = 2): string {
  if (value == null || value === "") return MISSING;
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return MISSING;
  return parsed.toLocaleString("en-US", { minimumFractionDigits: digits,
                                          maximumFractionDigits: digits });
}

export function btc(value: string | null | undefined): string {
  return value == null ? MISSING : `${num(value, 3)} BTC`;
}

export function usdt(value: string | null | undefined): string {
  return value == null ? MISSING : `${num(value, 0)} USDT`;
}

export function pct(value: string | null | undefined, digits = 4): string {
  return value == null ? MISSING : `${num(value, digits)}%`;
}

export function ratio(value: string | null | undefined): string {
  return value == null ? MISSING : num(value, 4);
}

export function multipleLabel(value: string | null | undefined): string {
  return value == null ? MISSING : `${num(value, 1)}x`;
}

export function duration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return MISSING;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function bytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

/** Coverage decides the tone of a value, not the value itself. PARTIAL is a warning because the
 *  number beside it is a lower bound, and reading a lower bound as a quantity is the mistake
 *  this whole vocabulary exists to prevent. */
export function coverageTone(coverage: Coverage): string {
  return coverage === "COMPLETE" ? "tone-success"
    : coverage === "PARTIAL" ? "tone-warning" : "tone-neutral";
}

export function feedTone(state: FeedState): string {
  return state === "LIVE" ? "tone-success"
    : state === "SYNCING" ? "tone-info"
    : state === "STALE" ? "tone-danger" : "tone-neutral";
}

export const FEED_LABELS: Record<FeedState, string> = {
  LIVE: "LIVE", STALE: "STALE", SYNCING: "SYNCING", NO_DATA: "NO DATA",
};

/** Why the screen is not LIVE, in the operator's language. An unmapped code is shown raw rather
 *  than swallowed: a reason nobody has translated yet still has to reach the screen. */
export const REASON_LABELS: Record<string, string> = {
  NO_DERIVED_SAMPLE: "collector 샘플 없음",
  SESSION_ENDED: "collector 세션 종료됨",
  JOURNAL_STALE: "저널이 갱신되지 않음",
  BOOK_UNSYNCED: "책 재동기화 중",
  BOOK_STALE: "책 수신 지연",
  BOOK_CROSSED_BOOK: "best bid/ask 교차",
  BOOK_NOT_FRESH: "책 신선도 미달",
  TRADE_STREAM_DISCONNECTED: "거래 스트림 끊김",
  TRADE_STREAM_STALE: "거래 스트림 정적",
  SAMPLE_NOT_CURRENT: "샘플이 현재가 아님",
  NOT_LIVE: "LIVE 아님",
  MS_V0_ROOT_NOT_SET: "MS_V0_ROOT 미설정",
  ROOT_NOT_FOUND: "저널 디렉터리 없음",
  NO_SESSION_RECORDED: "기록된 세션 없음",
  // Why the collector's state checkpoint was not used.
  STATE_FILE_ABSENT: "collector 상태 파일 없음",
  STATE_FILE_UNREADABLE: "상태 파일을 읽을 수 없음",
  STATE_FILE_SHAPE_UNKNOWN: "상태 파일 형식 불일치",
  STATE_FILE_CLAIMS_AUTHORITY: "상태 파일이 권위를 주장함",
  STATE_FILE_FROM_ANOTHER_SESSION: "이전 세션의 상태 파일",
  STATE_FILE_STALE: "상태 파일이 갱신되지 않음",
  NO_POLICY_IN_STATE_FILE: "상태 파일에 정책 없음",
  // Why a staged refresh was abandoned. Every one of these left the live book untouched.
  LIVE_BOOK_NOT_USABLE: "책이 사용 불가 상태",
  STAGING_SNAPSHOT_REJECTED: "스냅샷 거절됨",
  STAGING_REPLAY_GAP: "재생 중 연속성 끊김",
  STAGING_BEHIND_LIVE_CHAIN: "재생해도 현재 체인에 못 미침",
  REFRESH_BUFFER_OVERFLOW: "갱신 버퍼 초과",
  REFRESH_DEADLINE_EXCEEDED: "갱신 제한시간 초과",
  LIVE_BOOK_FAULTED: "책에 결함 발생",
  REST_READ_FAILED: "REST 읽기 실패",
  REFRESH_SUPERSEDED: "갱신이 대체됨",
  REPLAYED_CHAIN_SAME_UPDATE_ID: "동일 update id로 체인 재생",
  SNAPSHOT_INSTALLED_ON_LIVE_BOOK: "스냅샷을 책에 직접 설치",
  FIRST_FRAME_STRADDLES_SNAPSHOT_ID: "첫 프레임이 스냅샷 id를 포함",
  FIRST_FRAME_IS_IMMEDIATE_SUCCESSOR: "첫 프레임이 바로 다음 이벤트",
  // Why nothing is carried, and why a transition was HARD.
  COLLECTOR_PUBLISHES_NO_LEDGER: "collector가 연속성 원장을 내지 않음",
  NO_EARLIER_OBSERVATION: "이어받을 이전 관측 없음",
  SOFT_ALL_GATES_PASSED: "게이트 5개 전부 통과",
  HARD_NOT_A_VOLUNTARY_REFRESH: "자발 갱신이 아님",
  HARD_STREAM_DISCONTINUITY: "스트림 연속성 끊김",
  HARD_SNAPSHOT_NOT_NEWER: "스냅샷이 현재 책보다 새롭지 않음",
  HARD_REST_WINDOW_EXCEEDS_SAMPLE_INTERVAL: "REST 창이 샘플 간격 초과",
  HARD_SNAPSHOT_OVERLAP_INCONSISTENT: "스냅샷 구간 겹침 불일치",
  HARD_PROOF_SUPERSEDED: "증명이 다음 설치로 무효화됨",
  HARD_PROOF_DID_NOT_MATCH_GENERATION: "증명이 generation과 불일치",
  HARD_BOOK_NOT_FRESH_AT_SAMPLE: "샘플 시점에 책이 신선하지 않음",
  HARD_CONTINUITY_INTERRUPTED: "연속성 중단",
  RECONNECT: "재접속",
  STALE: "수신 정적",
  CROSSED_BOOK: "best bid/ask 교차",
  LEVEL_OVERFLOW: "레벨 초과",
  QUEUE_OVERFLOW: "버퍼 초과",
  SNAPSHOT_REJECTED: "스냅샷 거절",
  shutdown: "collector 종료",
};

/** The five gates a voluntary refresh has to pass before anything may be carried. */
export const GATE_LABELS: Record<string, string> = {
  VOLUNTARY: "자발 갱신",
  CONTINUITY: "스트림 연속",
  NEWER: "스냅샷 최신",
  BOUNDED_WINDOW: "REST 창 상한",
  OVERLAP: "구간 겹침",
};

export function gateLabel(code: string): string {
  return GATE_LABELS[code] ?? code;
}

/** How S4 was satisfied, or how it failed. Named on screen rather than folded into the gate
 *  chip, because "통과" on a window of 485 ms with a 300 ms ceiling is only honest if the screen
 *  also says the ceiling did not apply and why. */
export const WINDOW_VERDICT_LABELS: Record<string, string> = {
  WITHIN_MAX: "상한 이내",
  EXEMPT_REPLAYED_CHAIN: "면제 (체인 재생)",
  EXCEEDED_MAX: "상한 초과",
  NOT_MEASURED: "측정 불가",
};

export function windowVerdictLabel(code: string | null | undefined): string {
  if (!code) return MISSING;
  return WINDOW_VERDICT_LABELS[code] ?? code;
}

/** Why the frozen rule refused a candidate. Each one is a count on screen, because a threshold
 *  nobody can see is a threshold that silently empties a panel. */
export const REJECT_LABELS: Record<string, string> = {
  BELOW_MIN_NOTIONAL: "금액 미달",
  BELOW_MIN_MULTIPLE: "국소 배수 미달",
  INSIDE_MIN_DISTANCE: "mid 1bp 안쪽",
  BELOW_MIN_PERSISTENCE: "관측 시간 부족",
  FIELD_MISSING_OR_UNPARSEABLE: "값 없음",
  NO_USABLE_MID: "mid 없음",
};

export function rejectLabel(code: string): string {
  return REJECT_LABELS[code] ?? code;
}

/** Where the resting set came from. The journal reconstruction is the fallback, and saying so is
 *  the difference between a cheap read and an expensive one the operator cannot see. */
export const WALL_SOURCE_LABELS: Record<string, string> = {
  COLLECTOR_STATE_CHECKPOINT: "collector 상태 체크포인트",
  COLLECTOR_STATE_CHECKPOINT_STALE: "상태 체크포인트 (갱신 중단)",
  JOURNAL_RECONSTRUCTION: "저널 역방향 복원",
};

export const VALUES_AS_OF_LABELS: Record<string, string> = {
  CHECKPOINT_CURRENT: "현재 샘플 값",
  JOURNAL_OPEN_ROW: "후보 개시 시점 값",
};

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code;
}

/** The backend joins several reason codes with "|" when it suppresses something. Translating the
 *  joined string as a whole would leave the raw codes on screen, so it is split first; anything
 *  with no translation still comes through as its code rather than being dropped. */
export function reasonText(reason: string | null | undefined): string {
  if (!reason) return "";
  return reason.split("|").map(code => reasonLabel(code.trim())).join(" · ");
}

/** Where a price sits on the overlay axis, as a percentage from the top.
 *
 *  Returns null when the axis cannot place it: no axis, a degenerate axis, or a price outside the
 *  snapshot's known interval. A clamped position would draw the line at the edge and look like a
 *  reading, which is the one thing the overlay must not do. */
export function axisPosition(price: string | null | undefined, low: string | null | undefined,
                             high: string | null | undefined): number | null {
  if (price == null || low == null || high == null) return null;
  const p = Number(price), l = Number(low), h = Number(high);
  if (![p, l, h].every(Number.isFinite) || h <= l) return null;
  if (p < l || p > h) return null;
  return ((h - p) / (h - l)) * 100;
}
