/** The C1 signal layer on the client: fetch it, and place it on whichever chart is on screen.
 *
 *  Two rules this module exists to enforce.
 *
 *  One signal series. The backend produces C1 events on the contract's 1m decision grid and this
 *  module never recomputes them: switching to the 4 h chart re-buckets the same events onto 4 h
 *  candles, it does not re-evaluate the condition at 4 h. A signal belongs to a candle, and which
 *  candle that is depends on the timeframe; the signal itself does not.
 *
 *  One marker per signal lifecycle. The engine already collapses a run of true bars into one
 *  event, and this module collapses nothing further except visually: several events inside one
 *  high-timeframe candle become a single "C1 x2" marker, because two arrows cannot share a slot.
 */
import { CRYPTO_API_BASE, type ChartTimeframe } from "@/lib/crypto-paper";

export type C1Direction = "LONG";

/** The premium-normalization diagnostic attached to a signal.
 *
 *  Not an exit. Every priced field is named for what *would* have happened, and nothing on this
 *  type may be rendered with the words exit, sell or close. */
export type C1xRecord = {
  c1x_event_id: string;
  signal_id: string;
  parent_signal_id: string;
  display_seq?: number;
  status: "NOT_TRIGGERED" | "CONFIRM_1" | "CONFIRM_2" | "TRIGGERED" | "EXPIRED_MAX_HOLD";
  confirmation_count: number;
  triggered_at_ms: number | null;
  executable_at_ms: number | null;
  observed_price: number | null;
  premium_value: number | null;
  premium_bucket: number;
  premium_boundary: number | null;
  hypothetical_exit_price: number | null;
  holding_minutes: number | null;
  net_if_exited: number | null;
  e0_net: number | null;
  e0_status: "PENDING" | "SETTLED";
  delta_net: number | null;
  censored_by_max_hold: boolean;
  is_exit: false;
  meaning: string;
};

export type C1Marker = {
  signal_id: string;
  display_seq?: number;
  strategy: string;
  direction: C1Direction;
  triggered_at_ms: number;
  signal_bar_ms: number;
  signal_price: number | null;
  planned_exit_at_ms: number;
  horizon_min: number;
  state: string;
  status: string;
  net_return: number | null;
  entry_price: number | null;
  exit_price: number | null;
  c1x?: C1xRecord | null;
};

export type C1Contract = {
  document: string;
  sha256: string;
  research_cell?: string;
  research_verdict?: string;
  cost_scenario?: string;
  official_horizon_min?: number;
  observation_horizons_min?: number[];
  overlap_policy?: string;
  research_oos?: {
    verdict?: string; gate?: string; net_vip0_base_bp?: number; n_eff?: number; days?: number;
  };
};

/** The forward tally for the diagnostic. `forward_sample_sufficient` is the important field:
 *  while it is false no comparison here means anything, and the UI has to say so. */
export type C1xSummary = {
  id: string;
  meaning: string;
  is_exit: false;
  benchmark: string;
  benchmark_horizon_min: number;
  c1_signals: number;
  c1x_triggered: number;
  c1x_censored: number;
  c1x_pending: number;
  paired_with_e0: number;
  trigger_rate?: number;
  holding_minutes_median?: number;
  c1x_net_mean_bp?: number;
  e0_net_mean_bp?: number;
  paired_delta_mean_bp?: number;
  forward_sample_sufficient: boolean;
  research?: { status?: string; winner_preservation?: number; delta_vs_e0_mean_bp?: number;
               events?: number };
};

export type C1State = {
  enabled: boolean;
  ready: boolean;
  mode?: string;
  fixture_note?: string;
  error?: string | null;
  direction?: C1Direction;
  direction_contract?: string;
  contract: C1Contract;
  places_orders?: boolean;
  last_decided_at_ms?: number | null;
  last_bar?: {
    bar_ms: number; state: string; eligible: boolean; missing: string[];
    features?: Record<string, unknown>;
  } | null;
  active: {
    signal: C1Marker & Record<string, unknown>;
    shadow: Record<string, unknown> | null;
    c1x: C1xRecord | null;
  }[];
  signals_total: number;
  c1x?: C1xSummary;
  shadow_summary?: Record<string, unknown>;
  server_time_ms?: number;
};

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${CRYPTO_API_BASE}${path}`, { cache: "no-store", signal });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(`C1 ${response.status}`);
  return body as T;
}

export const c1Api = {
  state: (signal?: AbortSignal) => get<C1State>("/api/crypto/c1/state", signal),
  markers: (fromMs?: number | null, toMs?: number | null, limit = 500, signal?: AbortSignal) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (fromMs != null) query.set("from_ms", String(fromMs));
    if (toMs != null) query.set("to_ms", String(toMs));
    return get<{ enabled: boolean; markers: C1Marker[] }>(
      `/api/crypto/c1/markers?${query.toString()}`, signal).then(body => ({
        ...body, markers: withDisplaySequences(body.markers ?? []),
      }));
  },
};

/** Backward-compatible UI numbering for a running backend/old fixture that predates
 *  `display_seq`. Identity and pairing still come from signal_id/parent_signal_id. */
export function withDisplaySequences(markers: C1Marker[]): C1Marker[] {
  const ordered = [...new Map(markers.map(row => [row.signal_id, row])).values()]
    .sort((a, b) => a.triggered_at_ms - b.triggered_at_ms || a.signal_id.localeCompare(b.signal_id));
  const derived = new Map(ordered.map((row, index) => [row.signal_id, index + 1]));
  return markers.map(marker => {
    const displaySeq = marker.display_seq ?? derived.get(marker.signal_id)!;
    return {...marker, display_seq: displaySeq,
      c1x: marker.c1x ? {...marker.c1x, display_seq: marker.c1x.display_seq ?? displaySeq,
        parent_signal_id: marker.c1x.parent_signal_id ?? marker.signal_id} : marker.c1x};
  });
}

const displaySeqOf = (marker: C1Marker) => marker.display_seq ?? 0;

/** Candle width per timeframe, in milliseconds. The same boundaries the backend's chart history
 *  folds on, so a marker lands on the candle the operator is looking at. */
export const BUCKET_MS: Record<ChartTimeframe, number> = {
  "15s": 15_000, "1m": 60_000, "10m": 600_000, "1h": 3_600_000,
  "4h": 14_400_000, "1d": 86_400_000,
};

/** A marker as the chart library wants it. `time` is in seconds, on the candle's own boundary. */
export type SeriesMarkerSpec = {
  time: number;
  position: "belowBar" | "aboveBar";
  color: string;
  // The library offers circle, square, arrowUp and arrowDown; there is no diamond, so the
  // diagnostic uses a square, which is the nearest shape that cannot be read as a direction.
  shape: "arrowUp" | "arrowDown" | "square" | "circle";
  text: string;
  id: string;
  size?: number;
};

export type MarkKind = "C1" | "C1x";
export type C1ChartMarker = SeriesMarkerSpec & {
  signalIds: string[]; count: number; kind: MarkKind;
};

const LONG_COLOR = "#a855f7";
/** Muted next to the entry mark on purpose: the diagnostic is secondary to the signal. */
const C1X_COLOR = "#38bdf8";

/** The candle a signal belongs to on this timeframe.
 *
 *  Keyed off the *signal bar* rather than the trigger instant. They are one minute apart - the
 *  decision is taken at that bar's close - and on a 1m chart using the close would push every
 *  arrow one candle to the right of the bar whose data produced it.
 */
export const bucketOf = (marker: C1Marker, timeframe: ChartTimeframe) => {
  const span = BUCKET_MS[timeframe];
  return Math.floor(marker.signal_bar_ms / span) * span;
};

/** The candle a triggered diagnostic belongs to: the bar it was confirmed on. */
export const c1xBucketOf = (marker: C1Marker, timeframe: ChartTimeframe) => {
  const at = marker.c1x?.triggered_at_ms;
  if (at == null) return null;
  const span = BUCKET_MS[timeframe];
  // The trigger instant is a bar *close*; the candle it belongs to is the one that opened a
  // minute earlier, exactly as the entry mark keys off its signal bar rather than its trigger.
  return Math.floor((at - 60_000) / span) * span;
};

/** Triggered diagnostics only. A pending or censored one has no point on the time axis. */
export const triggeredC1x = (markers: C1Marker[]) =>
  markers.filter(marker => marker.c1x?.status === "TRIGGERED"
    && marker.c1x?.triggered_at_ms != null);

/** Collapse the signal series onto the candles of one timeframe.
 *
 *  Deduplicated by `signal_id` first, so the same event arriving twice - a poll overlapping a
 *  lazy-history page - cannot double a marker. Markers are returned in ascending time, which the
 *  chart library requires.
 */
export function toChartMarkers(markers: C1Marker[], timeframe: ChartTimeframe,
                               visibleFrom?: number | null): C1ChartMarker[] {
  // 15 s is an execution-detail view, not a C1 decision timeframe. Keep polling and retaining
  // the same signal records for the status strip, but do not project either lifecycle marker
  // onto its candles.
  if (timeframe === "15s") return [];
  markers = withDisplaySequences(markers);
  const unique = new Map<string, C1Marker>();
  for (const marker of markers) {
    if (!unique.has(marker.signal_id)) unique.set(marker.signal_id, marker);
  }
  const rows = [...unique.values()].filter(marker =>
    visibleFrom == null || marker.signal_bar_ms >= visibleFrom);

  // The two kinds are grouped separately and never folded into each other. One candle can hold
  // an entry mark and a diagnostic mark at once and they mean different things, so a combined
  // "x2" over the pair would be a sentence the chart cannot say.
  const entries = group(rows, marker => bucketOf(marker, timeframe), "C1");
  const diagnostics = group(triggeredC1x(rows),
                            marker => c1xBucketOf(marker, timeframe), "C1x");
  return [...entries, ...diagnostics].sort((a, b) => a.time - b.time);
}

function group(rows: C1Marker[], bucketFor: (marker: C1Marker) => number | null,
               kind: MarkKind): C1ChartMarker[] {
  const byBucket = new Map<number, C1Marker[]>();
  for (const marker of rows) {
    const bucket = bucketFor(marker);
    if (bucket == null) continue;
    const held = byBucket.get(bucket);
    if (held) held.push(marker);
    else byBucket.set(bucket, [marker]);
  }
  const buckets = [...byBucket.keys()].sort((a, b) => a - b);
  // Two labels a few candles apart overlap and become unreadable, which is what the daily chart
  // does on a phone when a cluster and a later signal sit on neighbouring days. When any pair is
  // that close the whole set drops to the short label: the mark already carries the meaning (an
  // arrow under the candle is an entry, a square above it is the diagnostic), and the strip and
  // the click detail carry the rest.
  const out: C1ChartMarker[] = [];
  for (const bucket of buckets) {
    const members = byBucket.get(bucket)!.sort((a, b) => a.signal_bar_ms - b.signal_bar_ms);
    const many = members.length > 1;
    out.push({
      time: Math.floor(bucket / 1000),
      // The entry mark sits under the candle so its arrow points at it. The diagnostic sits
      // above, as a square: it is not a direction, and an arrow there would read as one.
      position: kind === "C1" ? "belowBar" : "aboveBar",
      color: kind === "C1" ? LONG_COLOR : C1X_COLOR,
      shape: kind === "C1" ? "arrowUp" : "square",
      text: kind === "C1"
        ? (many ? `C1 x${members.length}` : `C1 #${displaySeqOf(members[0])}`)
        : (many ? `C1x x${members.length}` : `C1x #${displaySeqOf(members[0])}`),
      id: `${kind}:${members.map(marker => marker.signal_id).join("+")}`,
      size: kind === "C1" ? 1 : 0.8,
      signalIds: members.map(marker => marker.signal_id),
      count: members.length,
      kind,
    });
  }
  return out;
}

const kstTime = (ms: number) =>
  new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour12: false,
    hour: "2-digit", minute: "2-digit" }).format(new Date(ms));

const kstDateTime = (ms: number) =>
  new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour12: false,
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(ms));

const percent = (value: number) => {
  const bp = value * 10_000;
  return `${bp >= 0 ? "+" : ""}${(bp / 100).toFixed(2)}%`;
};

/** The two or three lines an entry mark shows when the operator asks about it.
 *
 *  The 4 h figure is a **benchmark**, not a planned exit, and the wording says so. Nothing in
 *  this layer closes a position, so "종료" or "청산" here would describe something that does not
 *  happen; `4H 기준` is the fixed yardstick each signal is measured against.
 */
export function markerDetail(marker: C1Marker): string[] {
  const head = `C1 #${displaySeqOf(marker)} · ${marker.direction}`;
  const when = kstTime(marker.signal_bar_ms);
  const hours = marker.horizon_min / 60;
  if (marker.net_return != null) {
    return [head, when, `${hours}H 기준 ${percent(marker.net_return)}`];
  }
  return [head, when, `${hours}H 기준 ${kstTime(marker.planned_exit_at_ms)}`];
}

/** The diagnostic's detail lines (section 11 of the brief).
 *
 *  Written so it cannot be mistaken for an exit: it names itself Premium Normalization, the
 *  result is labelled hypothetical, and the benchmark line says 기준 rather than 종료. The delta
 *  only appears once the 4 h benchmark has actually finished.
 */
export function c1xDetail(marker: C1Marker): string[] {
  const record = marker.c1x;
  if (!record || record.status !== "TRIGGERED" || record.triggered_at_ms == null) return [];
  const lines = [
    `C1x #${displaySeqOf(marker)} · Premium Normalization`,
    `C1       ${kstTime(marker.signal_bar_ms)}`,
    `C1x      ${kstTime(record.triggered_at_ms)}`,
  ];
  if (record.holding_minutes != null) {
    lines.push(`경과      ${record.holding_minutes}분`);
  }
  if (record.net_if_exited != null) {
    lines.push(`C1x 가정  ${percent(record.net_if_exited)}`);
  }
  if (record.e0_status === "SETTLED" && record.e0_net != null) {
    lines.push(`4H 기준   ${percent(record.e0_net)}`);
    if (record.delta_net != null) lines.push(`차이      ${percent(record.delta_net)}`);
  } else {
    lines.push("4H 기준   집계 중");
  }
  return lines;
}

/** One line for whatever the operator clicked, entry marks and diagnostics alike. */
export function markDetail(marker: C1Marker, kind: MarkKind): string {
  return kind === "C1x" ? c1xDetail(marker).join(" · ") : markerDetail(marker).join(" · ");
}

/** One line for a group, which may be several events inside one candle. */
export function groupDetail(markers: C1Marker[], kind: MarkKind = "C1"): string {
  if (markers.length === 1) return markDetail(markers[0], kind);
  return [...markers]
    .sort((a, b) => displaySeqOf(a) - displaySeqOf(b))
    .map(marker => markDetail(marker, kind))
    .join(" / ");
}

/** The chip beside the chart: the active signal, or nothing worth a card. */
export function activeChip(state: C1State | null): { text: string; detail: string } | null {
  if (!state?.enabled) return null;
  const rows = state.active ?? [];
  if (rows.length === 0) return { text: "C1 · 신호 없음", detail: "" };
  const newest = rows[0].signal;
  const remaining = Math.max(0, newest.planned_exit_at_ms - (state.server_time_ms ?? Date.now()));
  const minutes = Math.round(remaining / 60_000);
  // "4H 기준", never "종료" or "청산 예정": 4 h is the benchmark each signal is scored against
  // and nothing here ends a position at that instant.
  const diagnostic = newest.c1x?.status === "TRIGGERED" && newest.c1x.triggered_at_ms != null
    ? ` · C1x ${kstTime(newest.c1x.triggered_at_ms)}` : "";
  return {
    text: `최근 C1 #${newest.display_seq ?? state.signals_total} · ${kstTime(newest.signal_bar_ms)}`,
    detail: `추적 중 ${rows.length}건 · 4H 기준 ${kstTime(newest.planned_exit_at_ms)}`
      + (minutes > 0 ? ` (${Math.floor(minutes / 60)}시간 ${minutes % 60}분 남음)` : "")
      + diagnostic,
  };
}

/** How the research describes this signal, for the disclosure that has to say it is not
 *  validated. Quoted from the backend, which quotes the frozen cells file. */
export function researchNote(state: C1State | null): string | null {
  const oos = state?.contract?.research_oos;
  if (!oos) return null;
  const net = oos.net_vip0_base_bp;
  return `D5.2 ${state?.contract?.research_cell ?? "C1-EVENT-240m-LONG"}: ${oos.verdict ?? "WEAK"}`
    + (net != null ? ` · OOS 순수익 ${net >= 0 ? "+" : ""}${net.toFixed(2)}bp` : "")
    + (oos.n_eff != null ? ` · N_eff ${oos.n_eff.toFixed(0)}` : "")
    + ` · 게이트 ${oos.gate ?? "CASE_C"} (검증 통과 아님)`;
}


/** What the diagnostic has recorded so far, for the disclosure beside the chart.
 *
 *  It always leads with the count, and when the forward sample is not yet large enough it says
 *  that instead of quoting a comparison. E2 needed 1,603 events for an interval that still
 *  crossed zero; a handful of forward pairs cannot say anything, and a screen that printed a
 *  mean over six of them would be inviting a conclusion the data does not support.
 */
export function c1xNote(summary: C1xSummary | undefined | null): string | null {
  if (!summary) return null;
  const head = `C1x 진단 ${summary.c1x_triggered}건 / C1 ${summary.c1_signals}건`;
  const research = summary.research?.winner_preservation;
  const caveat = research != null
    ? ` · E2 ${summary.research?.status ?? "INCONCLUSIVE"} (큰 수익 보존 ${(research * 100).toFixed(1)}%)`
    : "";
  if (!summary.forward_sample_sufficient) {
    return `${head} · 전방 표본 부족, 비교 판단 보류${caveat}`;
  }
  const delta = summary.paired_delta_mean_bp;
  return `${head} · 4H 기준 대비 ${delta != null ? `${delta >= 0 ? "+" : ""}${delta.toFixed(2)}bp` : "집계 중"}${caveat}`;
}
