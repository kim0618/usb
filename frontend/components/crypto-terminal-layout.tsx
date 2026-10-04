"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  CHART_TIMEFRAMES, Candle15s, ChartTimeframe, CryptoState, FEED_STATUS_LABELS, FeedStatus,
  Performance, aggregateCandles, candles15sNote, candles15sToChart, chartBarsToCandles,
  chartTimeframeLabel, isWhitespace, mergeCandles,
  offscreenOverlays, cryptoApi, feedStatus, holdingDuration, krw,
  num, positionOverlays, price, qty, resetCapitalLabel, resetScopeNote,
  clockKst, costKrw, costUsdt, percent, previewFreshness, rejectLabel, signedKrw, signedUsdt,
  tickFreshness, toneClass, usdt,
} from "@/lib/crypto-paper";
import { DEFAULT_SYMBOL, baseAsset as baseAssetOf } from "@/lib/crypto-symbols";
import type { Candle, Candle15sStatus, ChartBar, ChartOverlay, ChartPoint, CryptoAccount,
  HistoryTimeframe,
  LivePnl, OpenPositionPnl, PositionPnlPreview } from "@/lib/crypto-paper";
import { activeChip, c1Api, c1xNote, groupDetail, researchNote, toChartMarkers }
  from "@/lib/crypto-c1";
import type { C1Marker, C1State, MarkKind } from "@/lib/crypto-c1";

/** One sentence naming the other symbols that are holding part of the wallet, or undefined on
 *  a run whose symbols each own their cash.
 *
 *  The paper wallet is shared the way a Binance futures wallet is: margin posted on one
 *  instrument is margin another cannot also spend. That makes the paper screen as tight as the
 *  live one, and it makes "주문가능" on a flat symbol a number this screen cannot explain by
 *  itself. */
export function sharedWalletNote(account: CryptoAccount | null | undefined): string | undefined {
  const cash = account?.cash;
  if (!cash) return undefined;
  const others = Object.entries(cash.used_margin_by_symbol)
    .filter(([, margin]) => Number(margin) > 0);
  if (others.length === 0) return "세 심볼이 지갑 하나를 함께 씁니다.";
  const held = others.map(([symbol, margin]) => `${symbol} ${margin}`).join(", ");
  return `세 심볼이 지갑 하나를 함께 씁니다. 현재 증거금: ${held}`;
}

/** The signal engine decides once a minute on the backend; a faster poll than this would only
 *  re-fetch the same answer. */
const C1_POLL_MS = 15_000;
/** How many events to hold on the client. C1 produced about 460 a year over the research window,
 *  and the deepest chart history is 2,000 daily candles, so this covers more history than any
 *  timeframe can show. */
const C1_MARKER_LIMIT = 2000;

/** The charting library is ~190KB of canvas code that touches `window` on construction. Loading
 *  it dynamically with SSR off keeps it out of the server render and off the initial payload of
 *  every other screen in the dashboard. */
/** The C1 signal layer, polled on its own cadence.
 *
 *  Separate from the terminal state poll: the signal engine runs on the backend on the contract's
 *  1m grid, so there is nothing a faster poll could show. 15 s is enough to pick up a new event
 *  within the minute it belongs to, and the whole layer is skipped when the backend says it is
 *  off, so a terminal without it pays nothing.
 */
export function useC1Signals(enabled = true) {
  const [state, setState] = useState<C1State | null>(null);
  const [markers, setMarkers] = useState<C1Marker[]>([]);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The signal series only changes when a new event opens, which C1 does on 0.7% of bars. The
    // state poll carries the count, so the series itself is re-fetched when that count moves and
    // not four times a minute - which is what makes it affordable to ask for the whole history
    // rather than a recent slice, and that in turn is what keeps markers on screen when the
    // operator pans the chart back past the newest few hundred events.
    let known = -1;
    const poll = async () => {
      try {
        const next = await c1Api.state(controller.signal);
        if (!alive) return;
        setState(next);
        if (next.enabled && next.signals_total !== known) {
          const body = await c1Api.markers(null, null, C1_MARKER_LIMIT, controller.signal);
          if (!alive) return;
          setMarkers(body.markers ?? []);
          known = next.signals_total;
        }
      } catch {
        // A signal layer that is unreachable must not break the terminal it sits beside.
      }
      if (alive) timer = setTimeout(poll, C1_POLL_MS);
    };
    void poll();
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [enabled]);
  return { state, markers };
}

const CandleChart = dynamic(
  () => import("@/components/crypto-candle-chart").then(module => module.CandleChart),
  { ssr: false, loading: () => <div className="h-[260px] w-full animate-pulse rounded-lg bg-surface-alt sm:h-[300px] xl:h-[520px]" /> },
);

const STATUS_TONE: Record<FeedStatus, string> = {
  LIVE: "text-success", STALE: "text-warning", DISCONNECTED: "text-danger",
};

export function FeedDot({ status }: { status: FeedStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-semibold ${STATUS_TONE[status]}`}
      data-testid="feed-status" data-status={status}>
      <span aria-hidden="true">●</span>{FEED_STATUS_LABELS[status]}
    </span>
  );
}

/** Everything needed to decide "is this tradable and how am I doing", in one band above the
 *  chart. On a phone this is the whole first screen, so nothing that is not a decision input
 *  belongs here. */
export function MarketHeader({ state, performance, onAction, busy }: {
  state: CryptoState;
  performance: Performance | null;
  onAction?: (run: () => Promise<unknown>) => void;
  busy?: boolean;
}) {
  const status = feedStatus(state);
  const account = state.account;
  const krwFigures = state.krw;
  const segmentPnl = performance?.current_segment?.current_segment_net_pnl ?? null;
  const changePct = (() => {
    const mark = num(state.quote?.mark_price);
    const open = num(state.quote?.index_price);
    return mark == null || open == null || open === 0 ? null : ((mark - open) / open) * 100;
  })();

  return (
    <header className="mb-2 sm:mb-3" data-testid="market-header">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-bold tracking-wide text-foreground"
            data-testid="market-header-symbol">{state.symbol ?? DEFAULT_SYMBOL}</span>
          <span className="text-[10px] font-medium text-muted">무기한</span>
        </div>
        <FeedDot status={status} />
      </div>
      <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3">
        <span className="text-[28px] font-bold leading-none tabular-nums text-foreground sm:text-4xl"
          data-testid="mark-price">{price(state.quote?.mark_price)}</span>
        {changePct != null && (
          <span className={`text-xs font-semibold tabular-nums ${changePct >= 0 ? "text-success" : "text-danger"}`}>
            {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}% <span className="font-normal text-muted">vs Index</span>
          </span>
        )}
      </div>

      {/* KRW first: the operator funded this in won and reads results in won. USDT stays as the
          secondary figure because it is what the engine actually settles in. */}
      <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 sm:mt-2.5 sm:grid-cols-4 sm:gap-y-1.5" data-testid="account-compact">
        <CompactFigure label="자산" value={krw(krwFigures?.equity)} sub={usdt(account?.equity)} />
        {/* On a shared wallet this is the *wallet's* free balance, not this symbol's. With a
            position open on another instrument the figure is lower than this screen's own
            position explains, and without saying so it reads as a bug. The title names which
            symbols are holding the difference. */}
        <CompactFigure label="주문가능" value={krw(krwFigures?.available_balance)}
          sub={usdt(account?.available_balance)}
          title={sharedWalletNote(account)} />
        <CompactFigure label="미실현" value={signedKrw(krwFigures?.unrealized_pnl)}
          sub={signedUsdt(account?.unrealized_pnl)} tone={toneClass(account?.unrealized_pnl)} />
        {/* Since the last reset, not since the run began. A reset moves the capital line, so
            the lifetime figure here would tell an operator the reset did not happen. The
            lifetime number is still in the performance disclosure, labelled as such. */}
        <CompactFigure label="현재 손익" title={resetScopeNote(performance)}
          value={signedKrw(segmentPnl ? String(Number(segmentPnl) * Number(state.fx.krw_per_usdt)) : krwFigures?.realized_pnl)}
          sub={signedUsdt(segmentPnl ?? account?.realized_pnl)}
          tone={toneClass(segmentPnl ?? account?.realized_pnl)} />
      </dl>
      {onAction && <ResetControl state={state} onAction={onAction} busy={busy ?? false} />}
    </header>
  );
}

function CompactFigure({ label, value, sub, tone, title }: {
  label: string; value: string; sub: string; tone?: string; title?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[10px] text-muted" title={title}>{label}</dt>
      <dd className={`truncate text-[13px] font-semibold tabular-nums ${tone || "text-foreground"}`}>{value}</dd>
      <dd className="truncate text-[10px] tabular-nums text-muted">{sub}</dd>
    </div>
  );
}

export function TimeframeTabs({ value, onChange }: {
  value: ChartTimeframe; onChange: (next: ChartTimeframe) => void;
}) {
  return (
    <div className="flex gap-0.5 sm:gap-1" role="group" aria-label="차트 주기">
      {CHART_TIMEFRAMES.map(frame => (
        <button key={frame} type="button" aria-pressed={value === frame}
          data-testid={`timeframe-${frame}`}
          className={`h-8 min-w-[38px] rounded-md px-1.5 text-xs font-semibold transition-colors sm:min-w-[44px] sm:px-2 ${
            value === frame
              ? "bg-primary-soft text-primary"
              : "text-foreground-secondary hover:bg-surface-hover"}`}
          onClick={() => onChange(frame)}>
          {chartTimeframeLabel(frame)}
        </button>
      ))}
    </div>
  );
}

/** The open 15 s candle is re-read twice a second: a candle that only moves once a second looks
 *  stepped. Each read is incremental (a few hundred bytes); the finalized history is never re-sent. */
export const CANDLES_15S_POLL_MS = 500;

/** 15 s candles while that timeframe is on screen: the full kept history once, then only what
 *  is new since the last finalized candle plus the open one. Stops when another timeframe is
 *  chosen or the tab is hidden. Runs on its own timer, apart from the 1 s account poll and the
 *  333 ms live PnL, so none of them waits on another. */
export function use15sCandles(enabled: boolean, symbol: string = DEFAULT_SYMBOL) {
  const [finalized, setFinalized] = useState<Candle15s[]>([]);
  const [current, setCurrent] = useState<Candle15s | null>(null);
  const [status, setStatus] = useState<Candle15sStatus | null>(null);
  const [coverageFrom, setCoverageFrom] = useState<number | null>(null);
  const lastFinal = useRef<number | null>(null);
  useEffect(() => {
    if (!enabled) return;
    // A symbol change starts the series over. The incremental cursor is the whole reason this
    // cannot be a filter on arrival: `lastFinal` would carry the previous instrument's last
    // finalized timestamp into the new symbol's request, and the server would answer with only
    // the candles after it - a chart with a hole in it, silently.
    let stopped = false;
    let timer: number | undefined;
    lastFinal.current = null;
    setFinalized([]);
    setCurrent(null);
    setStatus(null);
    setCoverageFrom(null);
    const tick = async () => {
      if (stopped) return;
      if (typeof document === "undefined" || !document.hidden) {
        try {
          const body = await cryptoApi.candles15s(symbol, lastFinal.current);
          if (stopped) return;
          if (body.symbol != null && body.symbol !== symbol) return;
          if (body.candles.length > 0) {
            const tail = body.candles[body.candles.length - 1].start_ms;
            setFinalized(previous => lastFinal.current == null ? body.candles
              : [...previous, ...body.candles.filter(row => row.start_ms > (previous.at(-1)?.start_ms ?? -1))].slice(-960));
            lastFinal.current = tail;
          }
          setCurrent(body.current);
          setStatus(body.status);
          setCoverageFrom(body.coverage_from_ms ?? null);
        } catch {
          if (!stopped) setStatus("DISCONNECTED");
        }
      }
      if (!stopped) timer = window.setTimeout(tick, CANDLES_15S_POLL_MS);
    };
    void tick();
    return () => { stopped = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [enabled, symbol]);
  const rows = current && current.start_ms > (finalized.at(-1)?.start_ms ?? -1) ? [...finalized, current] : finalized;
  return { candles: candles15sToChart(rows), status, coverageFrom };
}

export const CHART_INITIAL_BARS: Record<HistoryTimeframe, number> = {
  "1m": 2880, "10m": 2016, "1h": 2160, "4h": 2190, "1d": 2000,
};
export const CHART_HISTORY_PAGE = 500;
export const CHART_HISTORY_POLL_MS = 15_000;

/** Paged chart history. Requests are generation-checked as well as aborted, so a late response
 *  from the previous timeframe - or the previous *symbol* - can never replace the selected
 *  series.
 *
 *  The series key is `symbol|timeframe`, not `timeframe`. With `timeframe` alone, switching the
 *  symbol while staying on 1m left `series.key === timeframe` true, so the previous
 *  instrument's candles kept rendering and the newly fetched ones were *merged into* them -
 *  two instruments' prices in one series, which looks like a gap or a crash rather than a bug.
 *  Changing the symbol therefore resets the generation, empties the series and re-seeds, and
 *  the chosen timeframe is deliberately preserved across the change. */
export function useChartHistory(enabled: boolean, timeframe: HistoryTimeframe,
                                symbol: string = DEFAULT_SYMBOL) {
  const seriesKey = `${symbol}|${timeframe}`;
  const [series, setSeries] = useState<{ key: string; candles: Candle[] }>(
    { key: seriesKey, candles: [] });
  const [loading, setLoading] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [end, setEnd] = useState(false);
  const generation = useRef(0);
  const before = useRef<number | null>(null);
  const loadingEarlierRef = useRef(false);
  const hasMore = useRef(true);
  /** The series key the hook is currently about.
   *
   *  `loadEarlier` is handed to the chart, which calls it from a visible-range callback it
   *  holds across renders. On a symbol change the chart's range resets and it asks for more
   *  history *through the closure it already had*, which still names the previous instrument -
   *  observed in the preview as one `chart-history?symbol=BTCUSDT` read 346 ms after switching
   *  to SOL. The response was discarded (the generation and the series key both changed), so
   *  nothing wrong reached the screen, but the request should not be sent at all: it is a read
   *  about an instrument nobody is looking at, and the next person to touch this code should
   *  not have to rediscover that the guard is downstream.
   */
  const activeKey = useRef(seriesKey);

  useEffect(() => {
    if (!enabled) return;
    const ownGeneration = ++generation.current;
    const controller = new AbortController();
    let stopped = false;
    let timer: number | undefined;
    before.current = null;
    hasMore.current = true;
    loadingEarlierRef.current = false;
    activeKey.current = seriesKey;
    setSeries({ key: seriesKey, candles: [] });
    setLoading(true);
    setLoadingEarlier(false);
    setEnd(false);

    const current = () => !stopped && generation.current === ownGeneration;
    const refreshTail = async () => {
      try {
        const body = await cryptoApi.chartHistory(symbol, timeframe, 3, null, controller.signal);
        if (current()) setSeries(previous => ({ key: seriesKey,
          candles: mergeCandles(previous.key === seriesKey ? previous.candles : [], chartBarsToCandles(body.bars)) }));
      } catch { /* history remains usable when a refresh fails */ }
      if (current()) timer = window.setTimeout(refreshTail, CHART_HISTORY_POLL_MS);
    };

    void cryptoApi.chartHistory(symbol, timeframe, CHART_INITIAL_BARS[timeframe], null,
                                controller.signal)
      .then(body => {
        if (!current()) return;
        setSeries({ key: seriesKey, candles: chartBarsToCandles(body.bars) });
        before.current = body.next_before_ms;
        hasMore.current = body.has_more;
        setEnd(!body.has_more);
        timer = window.setTimeout(refreshTail, CHART_HISTORY_POLL_MS);
      })
      .catch(() => { /* the short execution-feed fallback remains on screen */ })
      .finally(() => { if (current()) setLoading(false); });

    return () => { stopped = true; controller.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [enabled, timeframe, symbol, seriesKey]);

  const loadEarlier = useCallback(async () => {
    if (!enabled || loadingEarlierRef.current || !hasMore.current || before.current == null) return;
    // A call through a closure from the previous symbol or timeframe. Refused before the
    // request, not after the response.
    if (activeKey.current !== seriesKey) return;
    const ownGeneration = generation.current;
    const cursor = before.current;
    loadingEarlierRef.current = true;
    setLoadingEarlier(true);
    try {
      const body = await cryptoApi.chartHistory(symbol, timeframe, CHART_HISTORY_PAGE, cursor);
      if (generation.current !== ownGeneration) return;
      setSeries(previous => ({ key: seriesKey,
        candles: mergeCandles(previous.key === seriesKey ? previous.candles : [], chartBarsToCandles(body.bars)) }));
      before.current = body.next_before_ms;
      hasMore.current = body.has_more && body.next_before_ms !== cursor;
      setEnd(!hasMore.current);
    } catch { /* retry when the visible range asks again */ }
    finally {
      if (generation.current === ownGeneration) setLoadingEarlier(false);
      loadingEarlierRef.current = false;
    }
  }, [enabled, timeframe, symbol, seriesKey]);

  return { candles: series.key === seriesKey ? series.candles : [], loading, loadingEarlier, end, loadEarlier };
}

/** Best bid, ask and spread on one line. They matter to a market order but they are not the
 *  headline, so they get a line rather than three cards. */
export function QuoteStrip({ state }: { state: CryptoState }) {
  const quote = state.quote;
  const spreadBps = quote?.spread && quote.mid
    ? (Number(quote.spread) / Number(quote.mid)) * 10_000 : null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] tabular-nums"
      data-testid="quote-strip">
      <span className="text-muted">Bid <span className="font-semibold text-success" data-testid="best-bid">
        {price(quote?.best_bid)}</span></span>
      <span className="text-muted">Ask <span className="font-semibold text-danger" data-testid="best-ask">
        {price(quote?.best_ask)}</span></span>
      <span className="text-muted">Spread <span className="font-semibold text-foreground-secondary" data-testid="spread">
        {price(quote?.spread, 2)}</span>{spreadBps != null && ` · ${spreadBps.toFixed(2)}bp`}</span>
    </div>
  );
}

/** The signal strip above the chart.
 *
 *  Deliberately a line and not a card. With no active signal it is one muted phrase, because no
 *  signal is the normal state - C1 fires on 0.7% of bars - and a large empty panel would cost the
 *  phone layout more than the information is worth.
 */
export function C1SignalStrip({ state, selected, selectedKind = "C1" }: {
  state: C1State | null; selected: C1Marker[]; selectedKind?: MarkKind;
}) {
  const chip = activeChip(state);
  if (!chip) return null;
  const note = researchNote(state);
  const active = (state?.active ?? []).length > 0;
  // The diagnostic's tally only earns a line once it has something to report or the operator is
  // looking at one; otherwise the quiet state stays a single phrase.
  const diagnostic = (active || selected.length > 0) ? c1xNote(state?.c1x) : null;
  return (
    <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px]"
      data-testid="c1-signal-strip">
      <span className={active ? "font-semibold text-[#a855f7]" : "text-muted"}
        data-testid="c1-active-chip">{chip.text}</span>
      {chip.detail && <span className="text-muted" data-testid="c1-active-detail">{chip.detail}</span>}
      {state?.mode === "FIXTURE" && (
        <span className="rounded bg-warning-soft px-1 text-warning" data-testid="c1-fixture-badge">
          미리보기 고정데이터
        </span>
      )}
      {selected.length > 0 && (
        <span className="text-foreground-secondary" data-testid="c1-marker-detail">
          {groupDetail(selected, selectedKind)}
        </span>
      )}
      {diagnostic && (
        <span className="w-full text-muted" data-testid="c1x-note">{diagnostic}</span>
      )}
      {/* The verdict line only appears when there is something to qualify. With no signal the
          strip is a single muted phrase, which is what the quiet state should cost on a phone -
          C1 fires on 0.7% of bars, so quiet is almost always the state. */}
      {note && (active || selected.length > 0) && (
        <span className="w-full text-muted" data-testid="c1-research-note">{note}</span>
      )}
    </div>
  );
}

export function ChartSection({ state, bars, timeframe, onTimeframe, overlays: given, note,
  c1, c1Markers = [] }: {
  state: CryptoState;
  bars: ChartBar[];
  timeframe: ChartTimeframe;
  onTimeframe: (next: ChartTimeframe) => void;
  /** The signal layer's state, for the strip above the chart. Omitted on a screen that does not
   *  show signals. */
  c1?: C1State | null;
  /** The signal series. One series for every timeframe: this component folds it onto the
   *  timeframe on screen and never asks the backend to recompute it per timeframe. */
  c1Markers?: C1Marker[];
  /** The price lines to draw. Omitted on the paper screen, where the account on the state *is*
   *  the account being charted. The LIVE screen passes Binance's own lines, because the state
   *  handed in here is the paper terminal's - it supplies the candles and nothing else - and
   *  deriving the position lines from it drew the wrong account's entry on a real position. */
  overlays?: ChartOverlay[];
  /** One line above the chart, for a caller whose candles and position come from different
   *  places and who has to say so. */
  note?: string;
}) {
  // The instrument being charted, taken from the snapshot this section is rendering.
  //
  // Not a prop with a default: both hooks below default to BTCUSDT when handed nothing, and
  // that is exactly what happened here - every tab drew BTCUSDT candles while the header, the
  // position and the order panel were all correct about ETH or SOL. It had no visual signature
  // and was found by tracing the requests the page actually made in the isolated preview, not
  // by looking at the screen.
  const symbol = state.symbol ?? DEFAULT_SYMBOL;
  const fast = use15sCandles(timeframe === "15s", symbol);
  const historyFrame: HistoryTimeframe = timeframe === "15s" ? "1m" : timeframe;
  const history = useChartHistory(timeframe !== "15s", historyFrame, symbol);
  const fallback = timeframe === "1m" ? aggregateCandles(bars, 1) : [];
  const candles: ChartPoint[] = timeframe === "15s" ? fast.candles
    : (history.candles.length > 0 ? history.candles : fallback);
  const overlays = given ?? positionOverlays(state);
  const real = candles.filter(point => !isWhitespace(point)).length;
  const secondsNote = timeframe === "15s" ? candles15sNote(fast.status, real, fast.coverageFrom) : null;
  // The chart scales to the candles in view; an entry or liquidation line far from them is off
  // the canvas, so the section says where it is. The range comes from the chart itself.
  const [priceRange, setPriceRange] = useState<{ from: number; to: number } | null>(null);
  // The chart reports after every draw; only a real change may re-render, or a draw would
  // trigger a render that triggers a draw.
  const onPriceRange = useCallback((next: { from: number; to: number } | null) => {
    setPriceRange(previous => {
      const same = previous === next || (previous != null && next != null
        && previous.from.toFixed(2) === next.from.toFixed(2) && previous.to.toFixed(2) === next.to.toFixed(2));
      return same ? previous : next;
    });
  }, []);
  const offscreen = offscreenOverlays(overlays, priceRange);
  // Signals folded onto the candles on screen. Recomputed when the timeframe changes, which
  // re-buckets the same events; it never re-evaluates the condition at another timeframe.
  const chartMarkers = useMemo(() => toChartMarkers(c1Markers, timeframe), [c1Markers, timeframe]);
  // A click reports which mark was hit as well as which signals, because an entry mark and a
  // diagnostic can sit on the same candle and they read differently.
  const [picked, setPicked] = useState<{ ids: string[]; kind: MarkKind }>({ ids: [], kind: "C1" });
  const onMarkerClick = useCallback(
    (ids: string[], kind: MarkKind) => setPicked({ ids, kind }), []);
  const selected = useMemo(
    () => timeframe === "15s" ? []
      : c1Markers.filter(marker => picked.ids.includes(marker.signal_id)),
    [c1Markers, picked, timeframe]);
  return (
    <section aria-label="시세 차트" className="panel overflow-hidden p-2 sm:p-4">
      {timeframe !== "15s" && (history.loading || history.loadingEarlier || history.end) && (
        <p className="mb-1 text-[10px] text-muted" data-testid="chart-history-state">
          {history.loading ? "기록 불러오는 중" : history.loadingEarlier ? "과거 불러오는 중" : "전체 기록"}
        </p>
      )}
      {/* Above the quote strip, not below it. The strip is the *candle* feed's bid and ask, and
          on a screen whose position figures come from somewhere else that has to be read
          before the numbers rather than after them. */}
      {note && (
        <p className="mb-1 text-[10px] text-muted" data-testid="chart-source-note">{note}</p>
      )}
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1 sm:mb-2">
        <TimeframeTabs value={timeframe} onChange={onTimeframe} />
        <QuoteStrip state={state} />
      </div>
      {secondsNote && (
        <p className={`mb-1 text-[10px] ${secondsNote.warn ? "text-warning" : "text-muted"}`}
          data-testid="candles-15s-note">
          {secondsNote.text}
        </p>
      )}
      {offscreen.length > 0 && (
        <p className="mb-1 text-[10px] text-muted" data-testid="offscreen-overlays">
          {offscreen.map(line => `${line.label} ${price(String(line.price))} ${line.direction === "UP" ? "↑" : "↓"} 화면 밖`).join(" · ")}
        </p>
      )}
      {/* Shorter on a phone so the order panel arrives sooner; taller where there is room. On a
          desktop the order panel fills the right column and a short chart would waste it. */}
      {c1 !== undefined && (
        <C1SignalStrip state={c1} selected={selected} selectedKind={picked.kind} />
      )}
      <CandleChart candles={candles} overlays={overlays} markers={chartMarkers}
        // The symbol is part of the series identity. With the timeframe alone, switching
        // instruments on the same timeframe kept the same series and the chart *amended* it
        // with the new prices instead of replacing them - two instruments in one line.
        onMarkerClick={onMarkerClick} seriesKey={`${symbol}|${timeframe}`}
        seconds={timeframe === "15s"} onPriceRange={onPriceRange}
        onNeedMoreHistory={timeframe === "15s" ? undefined : history.loadEarlier} className="h-[200px] sm:h-[280px] xl:h-[520px]" />
    </section>
  );
}

/** Flat is one line, not a card. An empty position is the normal state and should cost almost
 *  no vertical space on a phone. */
/** Why the total is what it is: price PnL first, then every cost that stands between it and the
 *  money an operator would keep. Each line is a figure the paper engine produced - the ledger's
 *  confirmed fees and funding, and a clone's actual full CLOSE on the current book for the exit
 *  fee and the fill. Won come from the backend at the run's fixed rate. Nothing is added up,
 *  negated or converted here; the two totals come from the backend too.
 *
 *  Freshness reuses the feed contract (STALE after 5 s): a preview priced on a book the terminal
 *  itself calls stale is withheld rather than shown as if it were current. */
export function PositionPnlBreakdown({ preview, state }: { preview: PositionPnlPreview; state: CryptoState }) {
  if (!preview.position_open) return null;
  const won = preview.krw ?? {};
  const freshness = previewFreshness(state, preview);
  const available = preview.close_feasible && freshness.status === "LIVE";
  const partial = preview.partial_exit_fee !== "0" || preview.partial_realized_pnl !== "0";
  const cost = (key: keyof OpenPositionPnl) => costKrw(won[key] ?? null);
  const costUsd = (key: keyof OpenPositionPnl) => costUsdt(preview[key] as string | null, 4);
  const lines: { label: string; value: string; sub: string; tone: string | null; note?: string }[] = [
    { label: "포지션 손익 (Mark 기준 미실현)", value: signedKrw(won.unrealized_pnl), sub: signedUsdt(preview.unrealized_pnl), tone: preview.unrealized_pnl },
    { label: "진입 수수료 (확정)", value: cost("entry_fee"), sub: costUsd("entry_fee"), tone: null },
  ];
  if (partial) {
    lines.push({ label: "부분청산 실현손익", value: signedKrw(won.partial_realized_pnl), sub: signedUsdt(preview.partial_realized_pnl), tone: preview.partial_realized_pnl });
    lines.push({ label: "부분청산 수수료 (확정)", value: cost("partial_exit_fee"), sub: costUsd("partial_exit_fee"), tone: null });
  }
  lines.push({ label: "현재까지 Funding", value: signedKrw(won.funding_pnl), sub: signedUsdt(preview.funding_pnl), tone: preview.funding_pnl });
  if (available) {
    lines.push({ label: "예상 청산 수수료", value: cost("expected_close_fee"), sub: costUsd("expected_close_fee"), tone: null });
    lines.push({ label: "예상 체결비용 (슬리피지·스프레드)", value: signedKrw(won.expected_close_slippage_pnl),
      sub: signedUsdt(preview.expected_close_slippage_pnl), tone: preview.expected_close_slippage_pnl,
      note: `Mark ${price(preview.mark_price)} → 예상 체결 ${price(preview.expected_close_fill_price)}` });
  }
  const unavailableReason = freshness.status !== "LIVE"
    ? `${FEED_STATUS_LABELS[freshness.status]} (${freshness.status === "STALE" ? "STALE market data" : "DISCONNECTED"})`
    : preview.close_reject_code === "NO_LIQUIDITY"
      ? "청산 가능한 호가 깊이 부족"
      : rejectLabel(preview.close_reject_code, preview.close_reject_message);
  return (
    <div className="mt-3 border-t border-line pt-2" data-testid="position-pnl-breakdown">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <p className="text-[11px] font-semibold text-foreground-secondary">손익 분해</p>
        <p className="text-[10px] text-muted" data-testid="preview-quote-age">
          현재 호가 기준 · {freshness.ageSeconds == null ? "-" : `${freshness.ageSeconds}초 전`}
        </p>
      </div>
      <dl className="space-y-1 text-[11px]">
        {lines.map(line => (
          <div key={line.label}>
            <div className="flex justify-between gap-3">
              <dt className="text-muted">{line.label}</dt>
              <dd className={`shrink-0 whitespace-nowrap text-right tabular-nums ${line.tone == null ? "text-foreground-secondary" : toneClass(line.tone)}`}>
                {line.value}<span className="ml-1.5 text-[10px] text-muted">{line.sub}</span>
              </dd>
            </div>
            {line.note && <p className="text-right text-[10px] text-muted">{line.note}</p>}
          </div>
        ))}
      </dl>
      {available ? (
        <div className="mt-2 space-y-1 border-t border-line pt-2 text-xs">
          <div className="flex justify-between gap-3 font-semibold">
            <span>청산 시 예상 순손익</span>
            <span className={`shrink-0 whitespace-nowrap text-right tabular-nums ${toneClass(preview.expected_position_net_if_closed)}`}
              data-testid="position-net-if-closed">
              {signedKrw(won.expected_position_net_if_closed)}
              <span className="ml-1.5 text-[10px] font-normal text-muted">{signedUsdt(preview.expected_position_net_if_closed)}</span>
            </span>
          </div>
          <div className="flex justify-between gap-3 text-[11px]">
            <span className="text-muted">청산 시 현재 구간 총손익 <span className="text-[10px]">(이전 거래 포함)</span></span>
            <span className={`shrink-0 whitespace-nowrap text-right tabular-nums font-medium ${toneClass(preview.expected_segment_net_if_closed)}`}
              data-testid="segment-net-if-closed">
              {signedKrw(won.expected_segment_net_if_closed)}
              <span className="ml-1.5 text-[10px] font-normal text-muted">{signedUsdt(preview.expected_segment_net_if_closed)}</span>
            </span>
          </div>
          <p className="text-[10px] text-muted">
            예상값은 {clockKst(preview.quote_ts_ms)} 호가로 전량 시장가 청산을 엔진에 시험 체결한 결과입니다. 실제 체결은 그 순간의 호가를 따릅니다.
          </p>
        </div>
      ) : (
        <p className="mt-2 rounded bg-warning-soft px-2 py-1 text-[11px] text-warning" data-testid="close-preview-unavailable">
          미리보기 불가: {unavailableReason}
        </p>
      )}
    </div>
  );
}

/** The two numbers a futures trader watches, side by side and large, and never confused:
 *  price PnL at mark, and what closing everything right now would actually leave after the exit
 *  fee and the book. Driven by the fast live route when it answers, by the 1 s figures otherwise.
 *  Every value is the backend's; the percentage is its fraction printed as a percent. */
export function LivePnlHeadline({ state, live, preview }: {
  state: CryptoState; live?: LivePnl | null; preview?: PositionPnlPreview | null;
}) {
  const account = state.account;
  const opened = preview?.position_open ? preview : null;
  const usingLive = live?.position_open === true;
  const unrealizedKrw = usingLive ? live!.krw.unrealized_pnl : state.krw?.unrealized_pnl;
  const unrealized = usingLive ? live!.unrealized_pnl : account?.unrealized_pnl;
  const pct = usingLive ? live!.unrealized_pct_of_margin : opened?.unrealized_pct_of_margin ?? null;
  const netKrw = usingLive ? live!.krw.expected_position_net_if_closed : opened?.krw?.expected_position_net_if_closed;
  const net = usingLive ? live!.expected_position_net_if_closed : opened?.expected_position_net_if_closed;
  const feasible = usingLive ? live!.close_feasible : opened?.close_feasible;
  const status = usingLive ? tickFreshness(live!) : opened ? previewFreshness(state, opened).status : "LIVE";
  const netShown = status === "LIVE" && feasible === true && net != null;
  return (
    <div className="mb-3 grid grid-cols-2 gap-2" data-testid="live-pnl">
      <div className="rounded-lg bg-surface-alt px-3 py-2">
        <p className="text-[11px] text-muted">현재 포지션 손익</p>
        <p className={`text-lg font-bold tabular-nums leading-tight sm:text-xl ${toneClass(unrealized)}`}
          data-testid="live-unrealized">{signedKrw(unrealizedKrw ?? null)}</p>
        <p className={`text-[11px] tabular-nums ${toneClass(unrealized)}`}>
          {pct != null ? `${Number(pct) > 0 ? "+" : ""}${percent(pct, 2)} ` : ""}
          <span className="text-muted" data-testid="position-upnl">{signedUsdt(unrealized ?? null)}</span>
        </p>
      </div>
      <div className="rounded-lg bg-surface-alt px-3 py-2">
        <p className="text-[11px] text-muted">청산 시 예상 순손익</p>
        {netShown ? (
          <>
            <p className={`text-lg font-bold tabular-nums leading-tight sm:text-xl ${toneClass(net)}`}
              data-testid="live-net-if-closed">{signedKrw(netKrw ?? null)}</p>
            <p className="text-[11px] text-muted">수수료·체결비용 포함</p>
          </>
        ) : (
          <p className="mt-1 text-[11px] text-warning" data-testid="live-net-unavailable">
            미리보기 불가{status !== "LIVE" ? ` · ${status === "STALE" ? "시세 지연" : "연결 끊김"}` : ""}
          </p>
        )}
      </div>
      <p className="col-span-2 text-right text-[10px] text-muted" data-testid="live-source">
        {usingLive ? "실시간 호가 기준" : "1초 갱신 기준"}
      </p>
    </div>
  );
}

/** The open position at the top of a phone screen, before the chart: what is held, the two PnL
 *  figures, entry and mark, and CLOSE - so checking or leaving a position never needs a scroll.
 *  CLOSE sends exactly the order the order panel's CLOSE sends, through the same action runner
 *  (which disables while a request is in flight). The rest of the numbers fold under a toggle. */
export function MobilePositionCard({ state, openedMs, nowMs, preview, live, onAction, busy }: {
  state: CryptoState; openedMs: number | null; nowMs: number; preview?: PositionPnlPreview | null;
  live?: LivePnl | null; onAction: (run: () => Promise<unknown>) => void; busy: boolean;
}) {
  const [detail, setDetail] = useState(false);
  const account = state.account;
  if (!account || account.position_side === null) return null;
  const long = account.position_side === "LONG";
  const held = holdingDuration(openedMs, nowMs);
  return (
    <section className="panel mb-2 p-3" data-testid="mobile-position-card" aria-label="현재 포지션">
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
        <span className={`rounded px-2 py-0.5 font-bold ${long ? "bg-success-soft text-success" : "bg-danger-soft text-danger"}`}
          data-testid="mobile-position-side">{account.position_side}</span>
        <span className="font-semibold tabular-nums text-foreground-secondary">{num(account.leverage)?.toString() ?? "-"}x</span>
        <span className="tabular-nums text-foreground-secondary">
          {qty(account.position_qty)} {baseAssetOf(state.symbol ?? DEFAULT_SYMBOL)}</span>
        {held && <span className="ml-auto text-[11px] text-muted">{held} 보유</span>}
      </div>
      <LivePnlHeadline state={state} live={live} preview={preview} />
      <dl className="mb-2 grid grid-cols-2 gap-x-3 text-[11px]">
        <div className="flex justify-between gap-2"><dt className="text-muted">진입가</dt>
          <dd className="tabular-nums text-foreground-secondary">{price(account.avg_entry)}</dd></div>
        <div className="flex justify-between gap-2"><dt className="text-muted">Mark</dt>
          <dd className="tabular-nums text-foreground-secondary">{price(live?.position_open ? live.mark_price : state.quote?.mark_price)}</dd></div>
      </dl>
      <button type="button" className="btn-muted h-11 w-full text-sm font-bold" data-testid="mobile-close-button"
        disabled={busy || state.quote === null}
        onClick={() => onAction(() => cryptoApi.order(state.symbol ?? DEFAULT_SYMBOL,
          { side: account.position_side!, intent: "CLOSE", qty: account.position_qty }))}>
        CLOSE · 전량 청산
      </button>
      <button type="button" className="mt-1.5 flex w-full items-center justify-between py-1 text-[11px] font-medium text-foreground-secondary"
        aria-expanded={detail} data-testid="mobile-position-detail-toggle" onClick={() => setDetail(open => !open)}>
        손익/비용 상세 <span aria-hidden="true">{detail ? "▲" : "▼"}</span>
      </button>
      {detail && (
        <div data-testid="mobile-position-detail">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
            {([["청산가", price(account.liquidation_price)], ["사용 마진", usdt(account.used_margin)],
               ["마진 비율", account.margin_ratio == null ? "-" : `${Number(account.margin_ratio).toFixed(2)}x`]] as const)
              .map(([label, value]) => (
                <div key={label} className="flex justify-between gap-2"><dt className="text-muted">{label}</dt>
                  <dd className="tabular-nums text-foreground-secondary">{value}</dd></div>))}
          </dl>
          {preview?.position_open && <PositionPnlBreakdown preview={preview} state={state} />}
        </div>
      )}
    </section>
  );
}

export function PositionStrip({ state, openedMs, nowMs, preview, live }: {
  state: CryptoState; openedMs: number | null; nowMs: number; preview?: PositionPnlPreview | null;
  live?: LivePnl | null;
}) {
  const account = state.account;
  if (!account || account.position_side === null) {
    return <p className="px-1 py-2 text-xs text-muted" data-testid="position-strip">현재 포지션 없음</p>;
  }
  const long = account.position_side === "LONG";
  const held = holdingDuration(openedMs, nowMs);
  const rows: [string, string][] = [
    ["수량", `${qty(account.position_qty)} ${baseAssetOf(state.symbol ?? DEFAULT_SYMBOL)}`],
    ["진입가", price(account.avg_entry)],
    ["Mark", price(state.quote?.mark_price)],
    ["청산가", price(account.liquidation_price)],
    ["사용 마진", usdt(account.used_margin)],
    ["마진 비율", account.margin_ratio == null ? "-" : `${Number(account.margin_ratio).toFixed(2)}x`],
  ];
  return (
    <section className="panel p-3 sm:p-4" data-testid="position-strip">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`rounded px-2 py-0.5 text-xs font-bold ${long ? "bg-success-soft text-success" : "bg-danger-soft text-danger"}`}
            data-testid="position-side">{long ? "LONG" : "SHORT"}</span>
          <span className="text-xs font-semibold tabular-nums text-foreground-secondary">
            {num(account.leverage)?.toString() ?? "-"}x
          </span>
          {held && <span className="text-[11px] text-muted">{held} 보유</span>}
        </div>

      </div>
      <LivePnlHeadline state={state} live={live} preview={preview} />
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-2">
            <dt className="text-muted">{label}</dt>
            <dd className="tabular-nums text-foreground-secondary">{value}</dd>
          </div>
        ))}
      </dl>
      {preview?.position_open && <PositionPnlBreakdown preview={preview} state={state} />}
    </section>
  );
}

/** Operational and historical detail, closed by default.
 *
 *  Nothing is deleted: the run's provenance, the ledger and the session's performance are what
 *  make the numbers auditable, and an operator does occasionally need them. They just have no
 *  claim on the first screen of a trading terminal. */
/** Restore the virtual balance.
 *
 *  The confirmation says what the button does not do, because that is the part a person is
 *  right to be nervous about: the trade record, the fees and the performance history all stay.
 *  Refused while a position is open, and the position is never closed on the operator's behalf,
 *  so the button simply goes flat-only and says why.
 */
export function ResetControl({ state, onAction, busy }: {
  state: CryptoState;
  onAction: (run: () => Promise<unknown>) => void;
  busy: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const open = state.account != null && state.account.position_side !== null;

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 sm:mt-2" data-testid="reset-control">
      <button type="button" className="btn-muted h-7 px-2.5 text-[11px] sm:h-8 sm:px-3"
        disabled={busy || open} data-testid="reset-button"
        title={open ? "포지션 청산 후 초기화 가능" : undefined}
        onClick={() => setAsking(true)}>
        가상계좌 초기화
      </button>
      {open && <span className="text-[10px] text-muted" data-testid="reset-blocked-note">
        포지션 청산 후 초기화 가능
      </span>}
      {state.account && state.account.reset_count > 0 && (
        <span className="text-[10px] text-muted" data-testid="reset-count-note">
          초기화 {state.account.reset_count}회 · 기록 보존
        </span>
      )}

      {asking && !open && (
        <div role="dialog" aria-modal="true" aria-labelledby="crypto-reset-title"
          data-testid="reset-dialog"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-xl border border-line bg-surface p-5">
            <h2 id="crypto-reset-title" className="text-sm font-semibold text-foreground">
              가상계좌 초기화
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-foreground-secondary">
              현재 가상잔고를 {resetCapitalLabel()}으로 초기화합니다.
              기존 거래 및 성과 기록은 삭제되지 않습니다. 초기화 시점만 기록됩니다.
            </p>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <button type="button" className="btn-muted h-10" data-testid="reset-cancel"
                onClick={() => setAsking(false)}>취소</button>
              <button type="button" className="btn-danger h-10 text-xs font-bold"
                data-testid="reset-confirm" disabled={busy}
                onClick={() => { setAsking(false);
                  onAction(() => cryptoApi.reset(state.symbol ?? DEFAULT_SYMBOL)); }}>
                {resetCapitalLabel()}으로 초기화
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** The AUTO state, explained where the rest of the run's documentation lives. */
export function AutoNote({ reason }: { reason: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface-alt p-3" data-testid="auto-detail">
      <p className="text-xs font-semibold text-foreground-secondary">AUTO · 준비중</p>
      <p className="mt-1 text-[11px] text-muted">
        전략 판단 로직이 아직 없습니다. 진입·청산을 스스로 결정할 근거가 없으므로 엔진이
        AUTO 전환을 거부합니다 ({reason}). 수동 주문만 가능합니다.
      </p>
    </div>
  );
}

export function Disclosure({ title, children, testId, defaultOpen = false }: {
  title: string; children: ReactNode; testId: string; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-t border-line" data-testid={testId}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        data-testid={`${testId}-toggle`}
        className="flex w-full items-center justify-between py-2 text-left text-[13px] font-semibold text-foreground-secondary hover:text-foreground sm:py-3 sm:text-sm">
        {title}
        <span aria-hidden="true" className="text-xs text-muted">{open ? "▲" : "▼"}</span>
      </button>
      {open && <div className="pb-4 sm:pb-5">{children}</div>}
    </section>
  );
}
