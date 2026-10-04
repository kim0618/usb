"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  FEED_LABELS, LIQUIDITY_MAP_BASE_URL, LIQUIDITY_MAP_POLL_MS, LOWER_BOUND, LiquidityMapError,
  MISSING, VALUES_AS_OF_LABELS, WALL_SOURCE_LABELS,
  axisPosition, bps, btc, bytes, coverageTone, duration, feedTone, fetchSnapshot, gateLabel,
  lowerBound, multipleLabel, num, pct, ratio, reasonLabel, reasonText, rejectLabel, seconds,
  usdt, windowVerdictLabel,
} from "@/lib/liquidity-map";
import type {
  BandDepth, ContinuityView, Coverage, FlowWindow, LiquiditySnapshot, Side, SideView,
  SnapshotQuery, WallV2,
} from "@/lib/liquidity-map";

/** Liquidity Map V1, isolated preview.
 *
 *  This screen reads the Market Structure V0 journal through its own backend and shows it. It
 *  holds no order control, renders no LONG/SHORT verdict and computes no score, and it is not
 *  linked from the dashboard navigation: it is reachable only by its own URL, so it cannot get
 *  in the way of the manual order path or the position card.
 *
 *  The display rule that shapes every panel below: **a value is shown with its coverage, or it is
 *  not shown.** The +-0.1% band is COMPLETE on a `limit=1000` snapshot and the +-0.25%, +-0.5%
 *  and +-1% bands are not, so those three carry an explicit lower bound rather than a number that
 *  looks canonical. A dash plus a reason is the correct reading of an unobserved quantity; a zero
 *  is not, because this book really does have empty bands and the two must stay apart.
 */

const SIDE_LABEL: Record<Side, string> = { ASK: "SELL SIDE (ASK)", BID: "BUY SIDE (BID)" };
const SIDE_HINT: Record<Side, string> = {
  ASK: "mid 위쪽 - 매도 잔량",
  BID: "mid 아래쪽 - 매수 잔량",
};
const FLOW_ORDER = ["5s", "15s", "60s"] as const;

/** Presets for how much of the selected set to draw, measured on the resting candidate set
 *  (2026-10-04: 93 candidates above 200k USDT, 43 above 300k, 17 above 500k, 3 above 1M). These
 *  move the *view*; what counts as a wall is the frozen rule and is not on this screen. */
const NOTIONAL_PRESETS = ["250000", "500000", "1000000", "2000000"] as const;

// --------------------------------------------------------------------------- data

export function useLiquidityPreview(query: SnapshotQuery) {
  const [snapshot, setSnapshot] = useState<LiquiditySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polls, setPolls] = useState(0);
  const key = `${query.minNotionalUsdt ?? ""}|${query.wallLimit ?? ""}`;
  const latest = useRef(query);
  latest.current = query;

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await fetchSnapshot(latest.current, controller.signal);
        if (!alive) return;
        setSnapshot(next);
        setError(null);
        setPolls(count => count + 1);
      } catch (caught) {
        if (!alive || (caught as Error)?.name === "AbortError") return;
        // The last snapshot is deliberately kept on screen *and* the error is shown: the quality
        // strip already carries the journal age, so the operator can see both that the poll is
        // failing and how old the figures on screen are.
        setError(caught instanceof LiquidityMapError ? caught.message : String(caught));
      }
      if (alive) timer = setTimeout(poll, LIQUIDITY_MAP_POLL_MS);
    };
    poll();
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [key]);

  return { snapshot, error, polls };
}

// --------------------------------------------------------------------------- small pieces

function Chip({ value, tone, title }: { value: string; tone: string; title?: string }) {
  return <span title={title}
    className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-bold tracking-wide ${tone}`}>
    {value}</span>;
}

function CoverageChip({ coverage, title }: { coverage: Coverage; title?: string }) {
  return <Chip value={coverage} tone={coverageTone(coverage)} title={title} />;
}

function Field({ label, value, sub, mono = true, wrap = false }:
  { label: string; value: ReactNode; sub?: ReactNode; mono?: boolean; wrap?: boolean }) {
  // Truncation is the default because a long number must not widen its column. A field whose
  // whole value is the point - a price range, say - opts out and wraps instead, since a range cut
  // off at "84,924..." is not a reading of anything.
  return <div className="min-w-0">
    <p className="label">{label}</p>
    <p className={`mt-1 text-sm font-semibold text-foreground ${wrap ? "break-words" : "truncate"} ${mono ? "font-mono" : ""}`}>{value}</p>
    {sub != null && <p className="mt-0.5 text-[11px] leading-4 text-muted">{sub}</p>}
  </div>;
}

function Panel({ title, chip, children, note, testId, className = "" }:
  { title: string; chip?: ReactNode; children: ReactNode; note?: ReactNode; testId?: string;
    className?: string }) {
  return <section className={`panel p-4 ${className}`} data-testid={testId}>
    <header className="mb-3 flex flex-wrap items-center gap-2">
      <h2 className="text-sm font-semibold tracking-tight text-foreground">{title}</h2>
      {chip}
    </header>
    {children}
    {note != null && <p className="mt-3 border-t border-line-subtle pt-2 text-[11px] leading-4 text-muted">{note}</p>}
  </section>;
}

// --------------------------------------------------------------------------- quality

export function QualityStrip({ snapshot, error, polls }:
  { snapshot: LiquiditySnapshot | null; error: string | null; polls: number }) {
  const quality = snapshot?.quality;
  const state = quality?.state ?? "NO_DATA";
  const source = snapshot?.source;
  // The book and trade chips describe the sample, not the present. When the sample is not current
  // they are shown as what they are - a reading from a sample that stopped being updated.
  const current = quality?.sample_is_current !== false;
  const asOf = current ? "" : " · 마지막 샘플";
  return <section className="panel p-4" data-testid="lm-quality">
    <div className="flex flex-wrap items-center gap-2">
      <Chip value={FEED_LABELS[state]} tone={feedTone(state)} />
      <CoverageChip coverage={quality?.book_coverage ?? "UNKNOWN"}
        title="가장 좋은 밴드의 coverage. 밴드별 값은 각 패널에 있습니다." />
      <Chip value={`책 ${quality?.book_state ?? MISSING}${asOf}`} tone="tone-neutral" />
      <Chip value={`거래 ${FEED_LABELS[quality?.trade_state ?? "NO_DATA"]}${asOf}`}
        tone={feedTone(quality?.trade_state ?? "NO_DATA")} />
      <span className="ml-auto text-[11px] text-muted" data-testid="lm-poll-count">
        {LIQUIDITY_MAP_POLL_MS / 1000}초 폴링 · {polls}회
      </span>
    </div>
    {(quality?.reasons?.length ?? 0) > 0 && <ul className="mt-3 flex flex-wrap gap-1.5"
      data-testid="lm-quality-reasons">
      {quality!.reasons.map(code => <li key={code}
        className="rounded-md border border-warning bg-warning-soft px-2 py-0.5 text-[11px] font-medium text-warning">
        {reasonLabel(code)}</li>)}
    </ul>}
    {error && <p className="mt-3 rounded-md border border-danger bg-danger-soft px-2.5 py-1.5 text-xs font-medium text-danger"
      data-testid="lm-error">{error}</p>}
    <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-line-subtle pt-3 sm:grid-cols-3 xl:grid-cols-6">
      <Field label="저널 나이" value={duration(quality?.journal_age_ms)}
        sub={`기준 ${duration(quality?.journal_stale_ms)}`} />
      <Field label="책 수신 나이" value={duration(quality?.depth_age_ms)}
        sub={`stale ${duration(quality?.depth_stale_ms)}`} />
      <Field label="거래 수신 나이" value={duration(quality?.trade_age_ms)}
        sub={`stale ${duration(quality?.trade_stale_ms)}`} />
      <Field label="거래소 지연" value={quality?.lag_ms == null ? MISSING : `${quality.lag_ms}ms`}
        sub={`lag ${quality?.lag_state ?? MISSING}`} />
      <Field label="generation" value={quality?.generation ?? MISSING}
        sub={quality?.last_invalidation ? `무효화 ${quality.last_invalidation}` : "무효화 없음"} />
      <Field label="보유 레벨" value={quality?.levels == null ? MISSING : num(quality.levels, 0)}
        sub={`샘플 #${source?.sample_index ?? MISSING}`} />
    </dl>
  </section>;
}

// --------------------------------------------------------------------------- price

export function PriceCard({ snapshot }: { snapshot: LiquiditySnapshot | null }) {
  const price = snapshot?.price;
  return <Panel title="현재가" testId="lm-price"
    chip={<Chip value={snapshot?.source.symbol ?? "BTCUSDT"} tone="tone-neutral" />}
    note={<>mid = (best bid + best ask) / 2. <span data-testid="lm-mark-note">mark는 표시하지
      않습니다: {price?.mark_unavailable_reason ?? "V0 계약에 mark price 출처가 없습니다."}</span></>}>
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      <Field label="best bid" value={num(price?.best_bid, 1)} />
      <Field label="best ask" value={num(price?.best_ask, 1)} />
      <Field label="spread" value={num(price?.spread, 2)} sub={`${num(price?.spread_bps, 2)} bp`} />
      <Field label="mid" value={num(price?.mid, 2)} />
      <Field label="mark" value={<span className="text-muted" data-testid="lm-mark">{MISSING}</span>}
        sub="계약 미보유" />
      <Field label="알려진 구간" wrap
        value={`${num(price?.known_low, 1)} ~ ${num(price?.known_high, 1)}`}
        sub={`${pct(price?.known_low_pct, 3)} ~ ${pct(price?.known_high_pct, 3)}`} />
    </dl>
  </Panel>;
}

// --------------------------------------------------------------------------- overlay

/** The chart overlay: the snapshot's known interval, with the current price and the nearest
 *  wall on each side drawn on it.
 *
 *  The axis is the known interval and not a round +-1%, because outside that interval the book is
 *  unobserved rather than thin, and an axis wider than the data invites reading empty space as an
 *  empty book. For the same reason the COMPLETE region is shaded and the rest is labelled: the
 *  shading is the part of this picture that is a measurement.
 *
 *  Wall lines appear only when the candidate set was proven complete. If it was not, they are
 *  withheld and the reason is printed, because a "nearest wall" drawn from a set that might be
 *  missing an older, larger wall is wrong in exactly the direction that matters.
 */
export function LadderOverlay({ snapshot }: { snapshot: LiquiditySnapshot | null }) {
  const overlay = snapshot?.overlay;
  const price = snapshot?.price;
  const low = overlay?.axis_low ?? null;
  const high = overlay?.axis_high ?? null;
  const midPos = axisPosition(overlay?.mid ?? null, low, high);
  const sellPos = axisPosition(overlay?.sell_wall?.price ?? null, low, high);
  const buyPos = axisPosition(overlay?.buy_wall?.price ?? null, low, high);
  // The one region whose depth figures are canonical: +-0.1% of mid, which is inside the
  // snapshot bounds. It is shaded so the picture says where measurement stops.
  const mid = Number(overlay?.mid ?? NaN);
  const completeTop = axisPosition(Number.isFinite(mid) ? String(mid * 1.001) : null, low, high);
  const completeBottom = axisPosition(Number.isFinite(mid) ? String(mid * 0.999) : null, low, high);

  if (!overlay?.renderable) {
    return <Panel title="차트 overlay" testId="lm-overlay"
      chip={<Chip value="표시 중지" tone="tone-danger" />}>
      <p className="py-10 text-center text-sm text-foreground-secondary" data-testid="lm-overlay-suppressed">
        좌표를 신뢰할 수 없어 선을 그리지 않습니다.
        <span className="mt-1 block text-xs text-muted">
          {reasonText(overlay?.suppressed_reason) || "데이터 없음"}</span>
      </p>
    </Panel>;
  }

  // `self-start` so the panel ends with the ladder instead of stretching to the height of the
  // two side panels beside it and showing a tall empty box.
  return <Panel title="차트 overlay" testId="lm-overlay" className="xl:self-start"
    chip={<Chip value="알려진 구간 축" tone="tone-info" title={overlay.axis_rule} />}
    note="축은 limit=1000 스냅샷이 실제로 알고 있는 가격 구간입니다. 음영 밖은 '얇다'가 아니라 '관측 없음'입니다.">
    <div className="relative h-80 w-full overflow-hidden rounded-lg border border-line bg-surface-alt xl:h-[34rem]"
      data-testid="lm-overlay-canvas">
      {completeTop != null && completeBottom != null && <div
        className="absolute inset-x-0 border-y border-dashed border-success/40 bg-success-soft/40"
        style={{ top: `${completeTop}%`, height: `${Math.max(0, completeBottom - completeTop)}%` }}
        data-testid="lm-overlay-complete-band">
        <span className="absolute left-2 top-1 text-[10px] font-bold tracking-wide text-success">±0.1% COMPLETE</span>
      </div>}

      {sellPos != null && <div className="absolute inset-x-0 border-t-2 border-dashed border-danger"
        style={{ top: `${sellPos}%` }} data-testid="lm-overlay-sell-line">
        <span className="absolute right-2 -top-5 rounded border border-danger bg-danger-soft px-1.5 py-0.5 text-[10px] font-bold text-danger">
          SELL wall {num(overlay.sell_wall?.price, 1)} · {btc(overlay.sell_wall?.qty_btc ?? null)}
        </span>
      </div>}

      {midPos != null && <div className="absolute inset-x-0 border-t-2 border-primary"
        style={{ top: `${midPos}%` }} data-testid="lm-overlay-mid-line">
        <span className="absolute left-2 -top-5 rounded border border-primary bg-primary-soft px-1.5 py-0.5 text-[10px] font-bold text-primary">
          현재가 mid {num(overlay.mid, 2)}
        </span>
      </div>}

      {buyPos != null && <div className="absolute inset-x-0 border-t-2 border-dashed border-success"
        style={{ top: `${buyPos}%` }} data-testid="lm-overlay-buy-line">
        <span className="absolute right-2 top-1 rounded border border-success bg-success-soft px-1.5 py-0.5 text-[10px] font-bold text-success">
          BUY wall {num(overlay.buy_wall?.price, 1)} · {btc(overlay.buy_wall?.qty_btc ?? null)}
        </span>
      </div>}

      <span className="absolute left-2 top-1 font-mono text-[10px] text-muted">
        {num(high, 1)} ({pct(price?.known_high_pct, 3)})</span>
      <span className="absolute bottom-1 left-2 font-mono text-[10px] text-muted">
        {num(low, 1)} ({pct(price?.known_low_pct, 3)})</span>
    </div>
    {overlay.walls_suppressed_reason && <p className="mt-2 text-[11px] font-medium text-warning"
      data-testid="lm-overlay-walls-suppressed">
      wall 선 미표시: {overlay.walls_suppressed_reason}</p>}
  </Panel>;
}

// --------------------------------------------------------------------------- depth

function DepthRow({ band }: { band: BandDepth }) {
  const canonical = band.coverage === "COMPLETE";
  return <tr className="border-t border-line-subtle" data-testid={`lm-depth-${band.band_pct}`}>
    <th scope="row" className="py-2 pr-2 text-left font-mono text-xs font-semibold text-foreground">
      ±{band.band_pct}%</th>
    <td className="py-2 pr-2"><CoverageChip coverage={band.coverage} /></td>
    {/* A PARTIAL band's number is the smallest it could be, so it is printed with the
        inequality attached rather than with a separate word beside it: the marker travels with
        the value wherever the value is copied or read out of context.

        The unit lives in the column header, not in the cell. At 390px a cell carrying both the
        marker and the unit wrapped onto three lines, and a figure broken across three lines is
        harder to read than the same figure behind a horizontal scroll. */}
    <td className="whitespace-nowrap py-2 pr-2 text-right font-mono text-xs">
      {canonical ? num(band.qty, 3)
        : <span className="text-warning">{lowerBound(num(band.observed_qty, 3), true)}</span>}
    </td>
    <td className="whitespace-nowrap py-2 text-right font-mono text-xs">
      {canonical ? num(band.notional, 0)
        : <span className="text-warning">{lowerBound(num(band.observed_notional, 0), true)}</span>}
    </td>
  </tr>;
}

/** One selected wall. `bin_members` is shown whenever a bin held more than one qualifying level,
 *  because the alternative - drawing the same structure as several lines - is the misread the bin
 *  rule exists to prevent, and silently collapsing them would hide that it happened. */
function WallLine({ wall }: { wall: WallV2 }) {
  return <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-t border-line-subtle py-1.5"
    data-testid="lm-wall-row">
    <span className="font-mono text-xs font-semibold text-foreground">{num(wall.price, 1)}</span>
    <span className="font-mono text-[11px] text-primary">{bps(wall.distance_bps)}</span>
    <span className="font-mono text-[11px] text-foreground-secondary">{btc(wall.qty_btc)}</span>
    <span className="font-mono text-[11px] text-foreground-secondary">{usdt(wall.notional_usdt)}</span>
    {wall.bin_members > 1 && <span
      className="rounded-sm border border-line px-1 text-[10px] font-semibold text-muted"
      data-testid="lm-wall-bin"
      title={`${num(wall.bin_low, 1)}~${num(wall.bin_high, 1)} 구간의 적격 레벨 ${wall.bin_members}개를 한 벽으로 묶었습니다. 구간 합계 ${LOWER_BOUND} ${usdt(wall.bin_candidate_notional_usdt)} (적격 후보만)`}>
      {wall.bin_members}레벨</span>}
    {wall.continuity_status === "CARRIED" && <span
      className="rounded-sm border border-primary px-1 text-[10px] font-semibold text-primary"
      data-testid="lm-wall-carried"
      title={`SOFT 갱신 ${wall.continuity_refreshes ?? MISSING}회를 넘어 관측이 이어졌습니다. 이 generation의 자체 구간 ${duration(wall.own_persistence_ms)}, 증명된 구간 ${duration(wall.carried_persistence_ms)}. 동일 side·동일 가격이 갱신된 스냅샷에 존재함을 증명한 것이며 주문 동일성 증명은 아닙니다.`}>
      이어받음</span>}
    <span className="ml-auto font-mono text-[11px] text-muted">
      {multipleLabel(wall.multiple)} · {duration(wall.observed_persistence_ms)}</span>
  </li>;
}

export function SidePanel({ side, view, wallCoverage, sessionAgeMs }:
  { side: Side; view: SideView | undefined; wallCoverage: Coverage;
    sessionAgeMs?: number | null }) {
  const nearest = view?.nearest_wall ?? null;
  const tone = side === "ASK" ? "tone-danger" : "tone-success";
  const rejected = Object.entries(view?.selection?.rejected ?? {})
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1]);
  return <Panel title={SIDE_LABEL[side]} testId={`lm-side-${side}`}
    chip={<><Chip value={SIDE_HINT[side]} tone={tone} /><CoverageChip coverage={wallCoverage} /></>}
    note={<>벽 판정은 동결 규칙 lm-wall.v2입니다. 지속시간은 샘플 관측 구간이며 주문 동일성 증명이
      아닙니다.
      {sessionAgeMs != null && <span data-testid={`lm-session-cap-${side}`}> 관측은 세션 경계를
        넘지 않으므로 지속시간은 세션 나이 {duration(sessionAgeMs)}를 넘을 수 없습니다.</span>}</>}>

    <div className="rounded-lg border border-line bg-surface-alt p-3" data-testid={`lm-nearest-${side}`}>
      <p className="label">가장 가까운 wall</p>
      {nearest ? <dl className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="가격" value={num(nearest.price, 1)} sub={bps(nearest.distance_bps)} />
        <Field label="잔량" value={btc(nearest.qty_btc)} sub={usdt(nearest.notional_usdt)} />
        <Field label="관측 지속" value={duration(nearest.observed_persistence_ms)}
          /* `break-keep` because this sub is Korean prose in a narrow column, and the default
             breaks a two-syllable word across two lines at 390px. */
          sub={nearest.continuity_status === "CARRIED"
            ? <span className="break-keep">SOFT 갱신 {nearest.continuity_refreshes ?? MISSING}회
              이어받음 · 자체 {duration(nearest.own_persistence_ms)}</span>
            : `이웃 ${nearest.neighbours ?? MISSING}개 평균의 ${multipleLabel(nearest.multiple)}`} />
      </dl> : <p className="mt-2 text-xs font-medium text-warning" data-testid={`lm-nearest-${side}-missing`}>
        {MISSING} {view?.nearest_unavailable_reason ?? "규칙을 통과한 벽이 없습니다"}</p>}
      {nearest && <p className="mt-2 text-[11px] text-muted">
        이웃 평균 {btc(nearest.local_average)} · coverage {nearest.coverage}
        {nearest.bin_members > 1 && <> · {num(nearest.bin_low, 1)}~{num(nearest.bin_high, 1)} 구간
          {nearest.bin_members}레벨 합계 {LOWER_BOUND} {usdt(nearest.bin_candidate_notional_usdt)}</>}
      </p>}
    </div>

    <h3 className="label mt-4">{side === "ASK" ? "ask" : "bid"} depth</h3>
    <div className="mt-1 overflow-x-auto">
      <table className="w-full min-w-[17rem] text-left">
        <thead><tr className="text-[10px] uppercase tracking-wide text-muted">
          <th scope="col" className="pb-1 pr-2 font-semibold">밴드</th>
          <th scope="col" className="pb-1 pr-2 font-semibold">coverage</th>
          <th scope="col" className="pb-1 pr-2 text-right font-semibold">수량 BTC</th>
          <th scope="col" className="pb-1 text-right font-semibold">금액 USDT</th>
        </tr></thead>
        <tbody>{(view?.depth ?? []).map(band => <DepthRow band={band} key={band.band_pct} />)}</tbody>
      </table>
    </div>

    {/* Three counts, because they answer three different questions. A screen showing nothing
        because the zoom is high must not look like a market with no walls in it, and a rule that
        refused everything must say on what ground. */}
    <div className="mt-4 flex items-baseline justify-between gap-2">
      <h3 className="label">표시 wall</h3>
      <span className="text-[11px] text-muted" data-testid={`lm-wall-count-${side}`}>
        {view?.walls_shown ?? 0} / 규칙 통과 {view?.walls_selected ?? 0} / V0 후보 {view?.candidates_total ?? 0}</span>
    </div>
    {(view?.walls?.length ?? 0) > 0
      ? <ul className="mt-1">{view!.walls.map(wall =>
          <WallLine wall={wall} key={`${wall.side}-${wall.price}`} />)}</ul>
      : <p className="mt-1 py-3 text-center text-xs text-muted" data-testid={`lm-wall-empty-${side}`}>
          표시할 wall이 없습니다 (V0 후보 {view?.candidates_total ?? 0}개)</p>}
    {rejected.length > 0 && <p className="mt-2 text-[11px] leading-4 text-muted"
      data-testid={`lm-reject-${side}`}>
      규칙이 제외한 후보: {rejected.map(([code, count]) => `${rejectLabel(code)} ${count}`).join(" · ")}
    </p>}
  </Panel>;
}

// --------------------------------------------------------------------------- flow

function FlowCell({ window: flow }: { window: FlowWindow | undefined }) {
  if (!flow) return <td className="py-2 text-xs text-muted">{MISSING}</td>;
  const canonical = flow.coverage === "COMPLETE";
  const buy = canonical ? flow.buy_btc : flow.observed_buy_btc;
  const sell = canonical ? flow.sell_btc : flow.observed_sell_btc;
  return <>
    <td className="py-2 pr-2"><CoverageChip coverage={flow.coverage}
      title={flow.coverage_reason ?? undefined} /></td>
    <td className="py-2 pr-2 text-right font-mono text-xs text-success">{btc(buy)}</td>
    <td className="py-2 pr-2 text-right font-mono text-xs text-danger">{btc(sell)}</td>
    <td className="py-2 pr-2 text-right font-mono text-xs">{btc(canonical ? flow.net_btc : null)}</td>
    <td className="py-2 pr-2 text-right font-mono text-xs">{ratio(flow.imbalance_btc)}</td>
    <td className="py-2 text-right font-mono text-xs text-muted">{flow.trades ?? MISSING}</td>
  </>;
}

export function FlowPanel({ snapshot }: { snapshot: LiquiditySnapshot | null }) {
  const flow = snapshot?.flow;
  const stream = flow?.trade_stream;
  // The panel's own badge follows the backend's trade state rather than re-deriving freshness
  // from the sample: a sample nobody is updating cannot report a live stream, and deriving it
  // here would reintroduce exactly that claim.
  const state = snapshot?.quality.trade_state ?? "NO_DATA";
  const label = state === "LIVE" ? "LIVE"
    : stream?.connected === false ? "DISCONNECTED" : FEED_LABELS[state];
  return <Panel title="FLOW (공격적 체결)" testId="lm-flow"
    chip={<Chip value={label} tone={feedTone(state)} />}
    note={<>{flow?.aggressor_rule ?? ""} · 창은 수신 단조시계 기준. {flow?.imbalance_note ?? ""}</>}>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[22rem] text-left">
        <thead><tr className="text-[10px] uppercase tracking-wide text-muted">
          <th scope="col" className="pb-1 pr-2 font-semibold">창</th>
          <th scope="col" className="pb-1 pr-2 font-semibold">coverage</th>
          <th scope="col" className="pb-1 pr-2 text-right font-semibold">BUY</th>
          <th scope="col" className="pb-1 pr-2 text-right font-semibold">SELL</th>
          <th scope="col" className="pb-1 pr-2 text-right font-semibold">net</th>
          <th scope="col" className="pb-1 pr-2 text-right font-semibold">imbalance</th>
          <th scope="col" className="pb-1 text-right font-semibold">건수</th>
        </tr></thead>
        <tbody>{FLOW_ORDER.map(label => <tr className="border-t border-line-subtle" key={label}
          data-testid={`lm-flow-${label}`}>
          <th scope="row" className="py-2 pr-2 text-left font-mono text-xs font-semibold">{label}</th>
          <FlowCell window={flow?.windows?.[label]} />
        </tr>)}</tbody>
      </table>
    </div>
    <p className="mt-2 text-[11px] text-muted" data-testid="lm-flow-freshness">
      마지막 체결 수신 {duration(stream?.age_ms)} 전 · stale 기준 {duration(stream?.stale_ms)}
      {FLOW_ORDER.some(label => flow?.windows?.[label]?.coverage === "PARTIAL")
        && " · PARTIAL 창은 하한 관측값만 있습니다"}
    </p>
  </Panel>;
}

// --------------------------------------------------------------------------- coverage

/** What the book was actually observed to reach, stated before anything is read off it.
 *
 *  This panel exists because of one specific misreading. A `limit=1000` snapshot reaches about
 *  +-0.15% of mid, so three of the four contract bands are **permanently** PARTIAL and their
 *  observed totals are identical - the same levels summed three times. Three equal numbers beside
 *  three different band labels reads as a stuck value, and empty space on a chart reads as empty
 *  book. Neither is what the data says, so the observed interval is a headline figure here, every
 *  PARTIAL number carries its inequality, and the reason the wider bands agree is printed.
 */
export function CoveragePanel({ snapshot }: { snapshot: LiquiditySnapshot | null }) {
  const coverage = snapshot?.coverage;
  const symmetric = coverage?.observed_symmetric_pct ?? null;
  return <Panel title="관측 범위" testId="lm-coverage"
    chip={<Chip value={`limit=${coverage?.snapshot_limit ?? 1000}`} tone="tone-neutral" />}
    note={<>{coverage?.observed_range_note ?? ""} {coverage?.lower_bound_note ?? ""}</>}>

    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Field label="관측 반경" value={symmetric == null ? MISSING : `±${pct(symmetric, 3)}`}
        sub="양쪽 모두 관측된 범위" />
      <Field label="관측 구간" wrap
        value={`${pct(coverage?.observed_low_pct, 3)} ~ ${pct(coverage?.observed_high_pct, 3)}`}
        sub={`${num(coverage?.known_low, 1)} ~ ${num(coverage?.known_high, 1)}`} />
      {/* Wrapping, not truncated: the whole list is the reading. "±0.25% ±0.5% ..." cut short
          says nothing about which bands are affected. */}
      <Field label="COMPLETE 밴드" wrap
        value={coverage?.complete_bands?.length
          ? coverage.complete_bands.map(band => `±${band}%`).join(" ") : MISSING}
        sub="정규 값 사용 가능" />
      <Field label="PARTIAL 밴드" wrap
        value={coverage?.partial_bands?.length
          ? coverage.partial_bands.map(band => `±${band}%`).join(" ") : MISSING}
        sub={`${LOWER_BOUND} 하한값만`} />
    </dl>

    <div className="mt-3 overflow-x-auto">
      <table className="w-full min-w-[18rem] text-left">
        <thead><tr className="text-[10px] uppercase tracking-wide text-muted">
          <th scope="col" className="pb-1 pr-2 font-semibold">밴드</th>
          <th scope="col" className="pb-1 pr-2 font-semibold">coverage</th>
          <th scope="col" className="pb-1 pr-2 text-right font-semibold">bid USDT</th>
          <th scope="col" className="pb-1 text-right font-semibold">ask USDT</th>
        </tr></thead>
        <tbody>{(coverage?.bands ?? []).map(band => {
          const canonical = band.coverage === "COMPLETE";
          return <tr className="border-t border-line-subtle" key={band.band_pct}
            data-testid={`lm-coverage-${band.band_pct}`}>
            <th scope="row" className="py-2 pr-2 text-left font-mono text-xs font-semibold text-foreground">
              ±{band.band_pct}%</th>
            <td className="py-2 pr-2"><CoverageChip coverage={band.coverage} /></td>
            {[band.observed_notional_bid, band.observed_notional_ask].map((value, index) =>
              <td key={index} className={`whitespace-nowrap py-2 ${index === 0 ? "pr-2" : ""} text-right font-mono text-xs ${canonical ? "" : "text-warning"}`}>
                {lowerBound(num(value, 0), !canonical)}</td>)}
          </tr>;
        })}</tbody>
      </table>
    </div>

    {coverage?.lower_bounds_identical && <p
      className="mt-2 text-[11px] leading-4 text-warning" data-testid="lm-coverage-identical">
      {coverage.identical_bounds_note}</p>}
    <p className="mt-2 text-[11px] leading-4 text-muted" data-testid="lm-coverage-null-note">
      관측 구간 밖은 유동성 0이 아니라 미관측이며 {MISSING}로 표시합니다.</p>
  </Panel>;
}

// --------------------------------------------------------------------------- resnapshot

/** The collector's resnapshot policy, reported rather than restated.
 *
 *  The margin figure is the one worth watching: the snapshot's bounds are fixed prices, so the
 *  room the +-0.1% band has left erodes one-for-one with mid, and it starts at only about 4 bps.
 *  What makes acting on that expensive is not the API - a `limit=1000` read is weight 20 against
 *  2,400 a minute - but that every resnapshot increments the book generation and ends every wall
 *  observation as UNKNOWN. So the policy is stated here together with what it costs.
 */
export function ResnapshotPanel({ snapshot }: { snapshot: LiquiditySnapshot | null }) {
  const policy = snapshot?.resnapshot;
  const margin = policy?.coverage_margin_bps ?? null;
  const trigger = Number(policy?.coverage_trigger_bps ?? NaN);
  const tight = margin != null && Number.isFinite(trigger) && Number(margin) < trigger * 3;
  return <Panel title="재스냅샷 정책" testId="lm-resnapshot"
    chip={<Chip value={policy?.fixed_interval_polling === false ? "고정 폴링 없음" : "정책 미확인"}
      tone={policy?.fixed_interval_polling === false ? "tone-success" : "tone-neutral"} />}
    /* `cost_note` is in the payload and says the same thing, but it is the collector's own
       English sentence and the note beside it is Korean. The screen shows one of them. */
    note={policy?.note ?? ""}>
    {policy?.available ? <>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="±0.1% 여유" value={margin == null ? MISSING : bps(margin)}
          sub={`트리거 ${bps(policy.coverage_trigger_bps)}`} />
        <Field label="스냅샷 나이" value={seconds(policy.snapshot_age_s)}
          sub={`안전 갱신 ${seconds(policy.safety_refresh_s)}`} />
        <Field label="구간 갱신" value={num(policy.coverage_refreshes, 0)}
          sub={policy.coverage_cooldown_remaining_s
            ? `쿨다운 ${seconds(policy.coverage_cooldown_remaining_s)} 남음`
            : `쿨다운 ${seconds(policy.coverage_cooldown_s)}`} />
        <Field label="설치 / 거절"
          value={`${num(policy.refreshes_applied, 0)} / ${num(policy.refreshes_rejected, 0)}`}
          sub={`안전 갱신 ${num(policy.safety_refreshes, 0)} · generation ${policy.generation ?? MISSING}`} />
      </dl>

      {policy.install && <p className="mt-3 text-[11px] leading-4 text-muted"
        data-testid="lm-resnapshot-install">
        자발 갱신은 별도 책에 스냅샷을 깔고 왕복 중 도착한 프레임을 재생한 뒤, 두 책의 update id가
        같아졌을 때만 교체합니다. 실패해도 기존 책은 그대로이고 쿨다운도 소모하지 않습니다
        (재시도 {seconds(policy.refresh_retry_backoff_s)}).
      </p>}

      {policy.refresh_in_progress && <p className="mt-2 text-xs font-medium text-primary"
        data-testid="lm-resnapshot-staging">
        갱신 준비 중: {reasonLabel(policy.refresh_in_progress.trigger)} ·
        버퍼 {num(policy.refresh_in_progress.buffered_at_snapshot, 0)} ·
        재생 {num(policy.refresh_in_progress.replayed_frames, 0)}
        {policy.refresh_in_progress.attachment &&
          <> · {reasonLabel(policy.refresh_in_progress.attachment)}</>}
      </p>}

      {(policy.refresh_failures_consecutive ?? 0) > 0 && <p
        className="mt-2 rounded-md border border-warning bg-warning-soft px-2.5 py-1.5 text-xs font-medium text-warning"
        data-testid="lm-resnapshot-failures">
        연속 실패 {num(policy.refresh_failures_consecutive, 0)}회.
        {policy.refresh_retry_in_s != null &&
          <> 다음 시도까지 {seconds(policy.refresh_retry_in_s)}.</>} 기존 책은 그대로이며
        갱신이 설치되지 않는 동안 ±0.1% 밴드는 보호되지 않습니다.
      </p>}
      {tight && <p className="mt-3 rounded-md border border-warning bg-warning-soft px-2.5 py-1.5 text-xs font-medium text-warning"
        data-testid="lm-resnapshot-tight">
        관측 구간이 ±0.1% 약속 경계에 접근했습니다. 트리거에 닿으면 재스냅샷이 1회 요청되고 그
        시점에 모든 wall 관측이 UNKNOWN으로 끝납니다.</p>}
      {policy.pending_reason && <p className="mt-3 text-xs font-medium text-primary"
        data-testid="lm-resnapshot-pending">재스냅샷 요청됨: {policy.pending_reason}</p>}
      {policy.stale && <p className="mt-3 text-xs font-medium text-warning"
        data-testid="lm-resnapshot-stale">정책 수치가 마지막 상태 파일 기준입니다.</p>}
    </> : <p className="text-xs text-muted" data-testid="lm-resnapshot-missing">
      {MISSING} {reasonLabel(policy?.unavailable_reason ?? "STATE_FILE_ABSENT")}</p>}
  </Panel>;
}

// --------------------------------------------------------------------------- continuity

/** The HARD/SOFT ledger: what the last generation transition did to the wall observations.
 *
 *  This panel exists because of a specific way the screen could mislead. A resnapshot used to
 *  reset every wall's observed span, so the longest span anything could show was the gap between
 *  two refreshes, and on a trending market that is five minutes. The fix lets a *proven*
 *  observation continue, and the risk the fix introduces is the opposite one: a span on screen
 *  that nothing earned. So the three counts are drawn together - carried, ended, unknown, with
 *  the number of candidates the transition started from - and the five gates are drawn as gates,
 *  passed or not. A carry nobody can audit from the screen is a carry nobody should believe.
 */
export function ContinuityPanel({ snapshot }: { snapshot: LiquiditySnapshot | null }) {
  const ledger: ContinuityView | undefined = snapshot?.continuity;
  const last = ledger?.last_transition ?? null;
  const soft = last?.refresh_type === "SOFT";
  // Drawn in the frozen rule's own order (S1 to S5), taken from the order the collector
  // publishes rather than from the object's key order: the state file is canonical JSON with
  // sorted keys, so iterating the map draws the gates alphabetically and the screen stops
  // matching the document that defines them.
  const gateResults = last?.gates ?? {};
  const gateOrder = (ledger?.gates_required ?? ledger?.rule?.soft_requires_all_gates
                     ?? Object.keys(gateResults));
  const gates: Array<[string, boolean]> = Object.keys(gateResults).length === 0 ? []
    : gateOrder.filter(gate => gate in gateResults).map(gate => [gate, gateResults[gate]]);
  const overlap = (last?.overlap_check ?? {}) as Record<string, unknown>;
  const compared = typeof overlap.levels_compared === "number" ? overlap.levels_compared : null;
  const identical = typeof overlap.levels_identical === "number" ? overlap.levels_identical : null;
  const drifted = ledger?.rule?.identity?.sha256_agrees === false;
  return <Panel title="관측 연속성 (HARD / SOFT)" testId="lm-continuity"
    chip={<><Chip value={last?.refresh_type ?? "전환 없음"}
      tone={soft ? "tone-info" : last ? "tone-warning" : "tone-neutral"} />
      <Chip value={ledger?.rule?.rule_version ?? "규칙 미확인"}
        tone={drifted ? "tone-danger" : "tone-neutral"}
        title={ledger?.rule?.wall_identity_rule} /></>}
    note={ledger?.note ?? ""}>
    {ledger?.available ? <>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="이어받은 벽" value={num(ledger.active_carried, 0)}
          sub={`이 화면 후보 ${num(ledger.candidates_in_view, 0)}개 중 ${num(ledger.active_carried_in_view, 0)}개`} />
        <Field label="SOFT / HARD" value={`${num(ledger.soft_refreshes, 0)} / ${num(ledger.hard_transitions, 0)}`}
          sub={`창 상한 ${duration(ledger.window_max_ms)} · 면제 ${num(ledger.soft_window_exempt, 0)}건`} />
        <Field label="마지막 전환"
          value={last?.refresh_type ?? MISSING}
          sub={reasonLabel(last?.continuity_reason ?? "")} />
        {/* The window and how it was judged, together. A 485 ms read under a 300 ms ceiling is
            only honestly a pass if the screen also says the ceiling did not apply, and why. */}
        <Field label="REST 창" value={duration(last?.window_ms)}
          sub={!last ? MISSING : `${windowVerdictLabel(last.window_verdict)} · ${
            last.refresh_trigger ? reasonLabel(last.refresh_trigger)
              : last.cause ? reasonLabel(last.cause) : MISSING}`} />
        {/* A replayed chain means every event between the snapshot and the swap was applied
            individually, so the window the rule bounds contains no unenumerated event. */}
        <Field label="설치 방식"
          value={last?.chain_preserved === true ? "체인 재생" : last?.basis ? "스냅샷 설치" : MISSING}
          sub={last?.replayed_frames == null ? (last?.basis ? reasonLabel(last.basis) : MISSING)
            : `프레임 ${num(last.replayed_frames, 0)}개 재생 · update id 불변`} />
      </dl>

      {/* v4. Said in a sentence rather than only as a chip, because the exemption is the one
          place where a gate reads "통과" on a number that is over its own ceiling. */}
      {last?.window_exempt === true && <p className="mt-3 rounded-md border border-line bg-surface-alt px-2.5 py-1.5 text-[11px] leading-4 text-muted"
        data-testid="lm-continuity-window-exempt">
        이 전환의 REST 창 {duration(last.window_ms)}은 상한 {duration(ledger.window_max_ms)}을
        넘었지만, 스냅샷과 swap 사이의 프레임을 전부 개별 재생하고 동일한 update id에서 교체했으므로
        창이 가리는 구간이 없습니다. {ledger.rule?.rule_version ?? ""}가 이 경우에만 상한을
        면제합니다.
      </p>}

      {/* The three counts of the last transition, and the number they have to add up to. A
          reader who sees only the carries cannot tell a book whose walls rested from a rule
          carrying everything, so this never shows one of them alone. */}
      {last && <dl className="mt-3 grid grid-cols-2 gap-3 rounded-lg border border-line bg-surface-alt p-3 sm:grid-cols-4"
        data-testid="lm-continuity-counts">
        <Field label="이어받음" value={num(last.wall_carried, 0)} />
        <Field label="종료 (관측됨)" value={num(last.wall_ended, 0)} />
        <Field label="UNKNOWN (미관측)" value={num(last.wall_unknown, 0)} />
        <Field label="전환 전 후보" value={num(last.candidates_before, 0)}
          sub={`generation ${last.generation_from ?? MISSING} → ${last.generation_to ?? MISSING}`} />
      </dl>}

      {gates.length > 0 && <div className="mt-3" data-testid="lm-continuity-gates">
        <p className="label">SOFT 게이트 (전부 통과해야 이어받습니다)</p>
        <ul className="mt-1.5 flex flex-wrap gap-1.5">
          {gates.map(([gate, held]) => <li key={gate}>
            <Chip value={`${gateLabel(gate)} ${held ? "통과" : "실패"}`}
              tone={held ? "tone-success" : "tone-danger"} title={gate} />
          </li>)}
        </ul>
      </div>}

      {compared != null && <p className="mt-3 text-[11px] leading-4 text-muted"
        data-testid="lm-continuity-overlap">
        스냅샷 겹침 검사 {overlap.passed === true ? "통과" : "실패"} · 비교 레벨 {num(compared, 0)}개
        {identical != null && <> · 수량 동일 {num(identical, 0)}개</>}
        {" "}(겹침 구간은 게이트이고 레벨 일치율은 게이트가 아닙니다. 스냅샷이 적용된 델타보다
        새롭기 때문에 겹침 안의 레벨은 달라질 수 있습니다.)
      </p>}

      {(last?.carried_lost ?? 0) > 0 && <p className="mt-3 rounded-md border border-warning bg-warning-soft px-2.5 py-1.5 text-xs font-medium text-warning"
        data-testid="lm-continuity-lost">
        이 전환이 이어받은 상태였던 벽 {num(last?.carried_lost, 0)}개의 관측 이력을 종료했습니다.
        {last?.cause && <> 원인: {reasonLabel(last.cause)}.</>}
      </p>}

      {(ledger.proofs_superseded ?? 0) > 0 && <p className="mt-2 text-xs font-medium text-warning"
        data-testid="lm-continuity-superseded">
        샘플 사이에 설치가 2회 일어나 증명 {num(ledger.proofs_superseded, 0)}건이 무효화되었습니다.
      </p>}

      {last?.carried_truncated === true && <p className="mt-2 text-xs font-medium text-warning"
        data-testid="lm-continuity-truncated">
        이어받은 목록이 상한에서 잘렸습니다. 개수는 정확하고 목록은 일부입니다.
      </p>}

      {drifted && <p className="mt-3 rounded-md border border-danger bg-danger-soft px-2.5 py-1.5 text-xs font-medium text-danger"
        data-testid="lm-continuity-drift">
        연속성 규칙 문서가 동결 해시와 다릅니다. 화면의 이어받음 판정을 신뢰할 수 없습니다.
      </p>}
    </> : <p className="text-xs text-muted" data-testid="lm-continuity-missing">
      {MISSING} {reasonLabel(ledger?.unavailable_reason ?? "COLLECTOR_PUBLISHES_NO_LEDGER")}
    </p>}
  </Panel>;
}

// --------------------------------------------------------------------------- wall set + filter

export function WallSetPanel({ snapshot, query, onQuery }:
  { snapshot: LiquiditySnapshot | null; query: SnapshotQuery;
    onQuery: (next: SnapshotQuery) => void }) {
  const walls = snapshot?.walls;
  const rule = walls?.rule;
  const state = walls?.state_file ?? null;
  const [draft, setDraft] = useState(query.minNotionalUsdt ?? "500000");
  useEffect(() => { setDraft(query.minNotionalUsdt ?? "500000"); }, [query.minNotionalUsdt]);
  const apply = useCallback(() => onQuery({ ...query, minNotionalUsdt: draft }), [draft, onQuery, query]);
  const agrees = rule?.identity.sha256_agrees;

  return <Panel title="wall 규칙과 집합" testId="lm-wallset"
    chip={<><CoverageChip coverage={walls?.coverage ?? "UNKNOWN"} />
      <Chip value={rule?.rule_version ?? "lm-wall.v2"}
        tone={agrees === true ? "tone-success" : agrees === false ? "tone-danger" : "tone-neutral"}
        title={agrees === true ? "동결 문서와 sha256 일치"
          : agrees === false ? "동결 문서가 해시와 불일치합니다" : "동결 문서를 찾을 수 없습니다"} />
    </>}
    note={<>{walls?.rule_note ?? ""} {walls?.no_verdict_note ?? ""}</>}>

    {/* The rule first, because every count below is a count of what it did. */}
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5" data-testid="lm-rule">
      <Field label="최소 금액" value={usdt(rule?.min_notional_usdt)} sub="R1" />
      <Field label="국소 배수" value={multipleLabel(rule?.min_multiple)}
        sub={`R2 · V0 ${multipleLabel(rule?.v0_rule.min_multiple)}`} />
      <Field label="최소 거리" value={bps(rule?.min_distance_bps)} sub="R3 · 호가 최상단 제외" />
      <Field label="최소 관측" value={duration(rule?.min_persistence_ms)} sub="R4" />
      <Field label="가격 구간" value={usdt(rule?.bin_width_usdt)}
        sub={`R5 · ${rule?.bin_width_ticks ?? MISSING}틱`} />
    </dl>
    <p className="mt-2 text-[11px] leading-4 text-muted" data-testid="lm-rule-frozen">
      규칙은 동결 문서이며 화면에서 바꿀 수 없습니다. 데이터 계약은 바뀌지 않았습니다
      (V0 후보 규칙 그대로 기록). {agrees === false && <span className="font-semibold text-danger">
        동결 문서가 기록된 해시와 다릅니다.</span>}
    </p>

    <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line-subtle pt-3 sm:grid-cols-4">
      <Field label="V0 후보" value={num(walls?.candidate_count, 0)}
        sub={walls?.truncated ? "collector 목록 일부 생략" : "전부 수신"} />
      <Field label="규칙 통과" value={num(walls?.walls_selected, 0)}
        sub={`표시 ${LOWER_BOUND} ${usdt(walls?.filter.min_notional_usdt)}`} />
      <Field label="집합 출처" mono={false}
        value={WALL_SOURCE_LABELS[walls?.source ?? ""] ?? (walls?.source ?? MISSING)}
        sub={VALUES_AS_OF_LABELS[walls?.values_as_of ?? ""] ?? walls?.values_as_of ?? MISSING} />
      <Field label="이번 폴 읽기" value={bytes(walls?.scanned_bytes)}
        sub={`체크포인트 ${bytes(state?.bytes)} · 꼬리 ${num(walls?.tail_records, 0)}행`} />
    </dl>

    {state != null && <p className="mt-2 text-[11px] leading-4 text-muted" data-testid="lm-state-file">
      상태 파일 {state.usable ? "사용" : `미사용 (${reasonLabel(state.unusable_reason ?? "")})`} ·
      나이 {duration(state.age_ms)} (기준 {duration(state.stale_ms)}) · seq {state.seq ?? MISSING} ·
      활성 {state.active_count ?? MISSING}개 · 권위는 {state.authority}. {walls?.state_file_note}
    </p>}

    {walls?.coverage !== "COMPLETE" && <p
      className="mt-3 rounded-md border border-warning bg-warning-soft px-2.5 py-1.5 text-xs font-medium text-warning"
      data-testid="lm-wallset-partial">
      후보 집합을 완전하다고 증명하지 못했습니다. 가장 가까운 wall과 차트 선은 표시하지 않습니다.
      {walls?.missing_count != null && <> 미복원 {num(walls.missing_count, 0)}개.</>}
    </p>}
    {walls?.tail_complete === false && <p className="mt-2 text-xs font-medium text-warning"
      data-testid="lm-tail-incomplete">
      체크포인트 이후 꼬리를 끝까지 읽지 못했습니다.</p>}

    <div className="mt-4 border-t border-line-subtle pt-3">
      <label className="label" htmlFor="lm-min-notional">표시 범위 (최소 금액, USDT)</label>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input id="lm-min-notional" inputMode="numeric" value={draft}
          onChange={event => setDraft(event.target.value.replace(/[^0-9]/g, ""))}
          onKeyDown={event => { if (event.key === "Enter") apply(); }}
          className="w-32 rounded-md border border-line bg-surface px-2 py-1 font-mono text-xs text-foreground"
          data-testid="lm-min-notional" />
        <button type="button" className="btn-muted px-2.5 py-1 text-xs" onClick={apply}
          data-testid="lm-filter-apply">적용</button>
        <span className="flex flex-wrap gap-1">
          {NOTIONAL_PRESETS.map(preset => <button type="button" key={preset}
            onClick={() => onQuery({ ...query, minNotionalUsdt: preset })}
            aria-pressed={query.minNotionalUsdt === preset}
            className={`rounded-md border px-2 py-1 text-[11px] font-semibold ${
              query.minNotionalUsdt === preset ? "tone-info" : "border-line text-muted"}`}>
            {num(preset, 0)}</button>)}
        </span>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        이 값은 보이는 양만 좁히고 규칙은 바꾸지 않습니다. 규칙을 통과한 벽 수는 위에 그대로
        남습니다. 2026-10-04 실측 분포: 200k 이상 93개 · 300k 43개 · 500k 17개 · 1M 3개.
      </p>
    </div>
  </Panel>;
}

// --------------------------------------------------------------------------- source

export function SourceDisclosure({ snapshot }: { snapshot: LiquiditySnapshot | null }) {
  const source = snapshot?.source;
  return <details className="panel p-4" data-testid="lm-source">
    <summary className="cursor-pointer text-sm font-semibold text-foreground">출처와 읽기 비용</summary>
    <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Field label="세션" value={source?.session_id?.slice(0, 8) ?? MISSING}
        sub={source?.session_ended ? "종료됨" : "진행 중"} />
      <Field label="계약" value={source?.contract?.contract_version ?? MISSING}
        sub={source?.contract?.sha256_agrees ? "sha256 일치" : "sha256 불일치"} />
      <Field label="collector" value={source?.collector_version ?? MISSING}
        sub={source?.exchange ?? MISSING} />
      <Field label="저널 seq" value={source?.seq == null ? MISSING : num(source.seq, 0)}
        sub={`wall 지연 ${duration(source?.wall_stream_lag_ms)}`} />
    </dl>
    <p className="mt-3 break-all text-[11px] text-muted">루트 {source?.root ?? MISSING}</p>
    <p className="mt-1 text-[11px] text-muted">
      preview {snapshot?.preview.version ?? MISSING} · {snapshot?.preview.mode ?? MISSING} ·
      API {LIQUIDITY_MAP_BASE_URL}</p>
    {(snapshot?.telemetry?.length ?? 0) > 0 && <ul className="mt-3 border-t border-line-subtle pt-2"
      data-testid="lm-telemetry">
      {snapshot!.telemetry.slice(0, 6).map(item => <li key={item.seq}
        className="flex flex-wrap gap-2 py-0.5 font-mono text-[11px] text-muted">
        <span>#{item.seq}</span><span className="text-foreground-secondary">{item.event}</span>
        <span>{item.stream ?? ""}</span><span>{item.reason ?? ""}</span></li>)}
    </ul>}
  </details>;
}

// --------------------------------------------------------------------------- page body

export function LiquidityMapPreview() {
  // 500,000 USDT, unchanged: the operator's zoom. The rule's own floor sits below it, so the
  // screen can always say how many walls exist beyond the ones it is drawing.
  const [query, setQuery] = useState<SnapshotQuery>({ minNotionalUsdt: "500000", wallLimit: 12 });
  const { snapshot, error, polls } = useLiquidityPreview(query);
  const wallCoverage = snapshot?.walls.coverage ?? "UNKNOWN";
  const sides = useMemo(() => (["ASK", "BID"] as Side[]), []);

  // No horizontal padding of its own: the app shell's `main` already pads, and adding a second
  // gutter costs 32px of a 390px screen. Measured at 390px, the page scrolls to exactly the
  // viewport width.
  return <div className="mx-auto w-full max-w-[1400px] space-y-4">
    <header>
      <p className="label">US-B CRYPTO · 격리 PREVIEW</p>
      <h1 className="mt-1 text-xl font-semibold tracking-tight text-foreground">Liquidity Map V1.2</h1>
      <p className="mt-2 max-w-3xl text-xs leading-5 text-foreground-secondary">
        Market Structure V0 저널을 읽기만 합니다. 방향 판정·score·주문 경로가 없고, 네비게이션에
        연결되지 않은 별도 주소입니다. coverage가 COMPLETE가 아닌 값은 숫자 대신 하한 또는 {MISSING}로
        표시합니다.
      </p>
    </header>

    <QualityStrip snapshot={snapshot} error={error} polls={polls} />
    <PriceCard snapshot={snapshot} />

    {/* `grid-cols-1` rather than a bare `grid`: a grid with no template sizes its implicit
        column to the widest item's min-content, and the depth tables inside push that past the
        viewport. `minmax(0, 1fr)` is what stops a child from widening its own column, and
        without it the page scrolled 30px sideways at 390px. */}
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      <LadderOverlay snapshot={snapshot} />
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
        {sides.map(side => <SidePanel key={side} side={side} view={snapshot?.sides?.[side]}
          wallCoverage={wallCoverage} sessionAgeMs={snapshot?.source.session_age_ms} />)}
      </div>
    </div>

    {/* `items-start` so the shorter panel keeps its own height instead of stretching to the
        taller one and printing a block of empty surface under its last line. */}
    <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
      <CoveragePanel snapshot={snapshot} />
      <ResnapshotPanel snapshot={snapshot} />
    </div>
    <ContinuityPanel snapshot={snapshot} />
    <FlowPanel snapshot={snapshot} />
    <WallSetPanel snapshot={snapshot} query={query} onQuery={setQuery} />
    <SourceDisclosure snapshot={snapshot} />
  </div>;
}
