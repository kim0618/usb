"use client";

import { useCallback, useEffect, useRef } from "react";
import {
  CandlestickSeries, CrosshairMode, HistogramSeries, createChart,
} from "lightweight-charts";
import type {
  AutoscaleInfo, IChartApi, IPriceLine, ISeriesApi,
} from "lightweight-charts";
import type { ChartOverlay, ChartPoint } from "@/lib/crypto-paper";
import { CONTEXT_KINDS } from "@/lib/crypto-paper";
import { isTailUpdate, isWhitespace, visible15sBars } from "@/lib/crypto-paper";

/** Every chart label is read in KST. Without a formatter the library labels ticks in UTC, which
 *  put "04:30" under a 13:30 candle. */
const kst = (time: number, options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour12: false, ...options })
    .format(new Date(time * 1000));

/** Axis labels in KST. `tickMarkType` is the library's own answer to "is this tick the start of a
 *  year, a month, a day, or a time within a day", so an hourly, 4-hourly or daily chart gets a
 *  date where it needs one instead of the same `09:00` on every candle. */
export const kstTickLabel = (seconds: boolean) => (time: number, tickMarkType?: number) => {
  if (tickMarkType === 0) return kst(time, { year: "numeric" });
  if (tickMarkType === 1) return kst(time, { year: "2-digit", month: "short" });
  if (tickMarkType === 2) return kst(time, { month: "2-digit", day: "2-digit" });
  return kst(time, { hour: "2-digit", minute: "2-digit",
                     ...(seconds ? { second: "2-digit" as const } : {}) });
};

/** The crosshair reading. It is the one place that always carries the date, because a bar on a
 *  daily chart and a bar at the far left of a panned minute chart are both ambiguous without it. */
export const kstCrosshairLabel = (seconds: boolean) => (time: number) =>
  kst(time, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
              ...(seconds ? { second: "2-digit" as const } : {}) });

/** Two display profiles. The 15 s one trades the whole-history overview for readable candles:
 *  wider bars, the last N of them in view, the price scale fitted to those, volume kept small.
 *  Every value is set on each profile switch, so nothing from one timeframe survives into another. */
const PROFILES = {
  // `barSpacing` is also what decides how much history the minute views open on, because the
  // first draw places a window of `clientWidth / barSpacing` candles out of the ones loaded. At
  // 6 px a 1m chart opened on 120 candles on a 722 px canvas: two hours out of the 2,880 behind
  // it, which read as the chart having no history rather than as a starting zoom. At 3 px the
  // same canvas opens on about four hours and a 1,600 px desktop on about five, and the candles
  // still have bodies. Zooming and panning from there are untouched, and so is where a view
  // that has already been moved sits.
  minute: { barSpacing: 3, minBarSpacing: 0.5, rightOffset: 3, priceTop: 0.08, priceBottom: 0.28, volumeTop: 0.8,
    minPriceSpanTicks: 0 },
  // In ticks, not USDT: 100 ticks is the 10 USDT BTC always had (tick 0.10), and on a sub-dollar
  // HOT symbol a fixed 10 USDT opened the 15 s axis at about -6..+6 around a 0.10 price.
  seconds: { barSpacing: 7, minBarSpacing: 2, rightOffset: 4, priceTop: 0.1, priceBottom: 0.2, volumeTop: 0.84,
    minPriceSpanTicks: 100 },
} as const;

/** How many one-minute-or-wider candles the first draw puts in view, by chart width.
 *
 *  Named and exported for the same reason `VISIBLE_15S_BARS` is: this is the number an operator
 *  reads as "how much history does this chart have", and at 6 px per candle it came out at 120
 *  on a 722 px canvas, which is two hours of the 2,880 loaded behind it. The floor is for the
 *  first draw on a canvas that has not been measured yet, where `clientWidth` is 0.
 *
 *  It is the opening window only. Zoom and pan own the viewport afterwards, and `heldViewport`
 *  is what keeps their work. */
export const visibleMinuteBars = (chartWidthPx: number) =>
  Math.max(60, Math.floor(chartWidthPx / PROFILES.minute.barSpacing));

/** The narrowest price range a profile opens on, in the instrument's own price units. */
export const minPriceSpan = (seconds: boolean, minMove: number) =>
  (seconds ? PROFILES.seconds : PROFILES.minute).minPriceSpanTicks * minMove;

/** Autoscale that never zooms tighter than `minSpan` (price units), centred on the candles. Without it a
 *  few flat 15 s candles, right after a restart or in a quiet minute, fill the whole pane and a
 *  0.1 USDT tick looks like a crash. */
export function widenPriceRange<T extends { priceRange: { minValue: number; maxValue: number } | null } | null>(
  info: T, minSpan: number,
): T {
  if (!info?.priceRange || minSpan <= 0) return info;
  const { minValue, maxValue } = info.priceRange;
  if (maxValue - minValue >= minSpan) return info;
  const mid = (minValue + maxValue) / 2;
  return { ...info, priceRange: { minValue: mid - minSpan / 2, maxValue: mid + minSpan / 2 } };
}

/** How each kind of line is drawn. The account's own lines (mark, entry, liquidation) are as they
 *  were. The BTC context lines are thinner in meaning and drawn quieter: resistance and support
 *  as solid hairlines in the sell/buy colours, the nearest walls dotted and fainter, so a wall
 *  never reads as heavier than a level. */
export const OVERLAY_STYLE: Record<ChartOverlay["kind"], { color: string; lineStyle: 0 | 1 | 2 }> = {
  MARK: { color: "rgba(148,163,184,0.85)", lineStyle: 2 },
  ENTRY: { color: "#38bdf8", lineStyle: 0 },
  LIQUIDATION: { color: "#f59e0b", lineStyle: 0 },
  // V3: a reference annotation, fainter and dotted, so a level never reads as a wall.
  // Wall-clock references only: a faint grey dotted line, never in a role colour.
  RESISTANCE: { color: "rgba(148,163,184,0.4)", lineStyle: 1 },
  SUPPORT: { color: "rgba(148,163,184,0.4)", lineStyle: 1 },
  // Walls: the role sets the hue (upper = red, lower = teal) and `wallShade` the step. These are
  // the defaults for a line with no order.
  ASK_WALL: { color: "rgba(244,63,94,0.35)", lineStyle: 0 },
  BID_WALL: { color: "rgba(20,184,166,0.35)", lineStyle: 0 },
};

/** A wall line's colours by how near it is on its side: 1 darkest, 2 middle, 3 lightest. The step
 *  is distance from the price, never size, so it holds when the walls are chosen by size. */
const WALL_RGB = { ASK_WALL: "244,63,94", BID_WALL: "20,184,166" } as const;
const WALL_LINE_ALPHA = { 1: 0.95, 2: 0.6, 3: 0.35 } as const;
const WALL_TAG_ALPHA = { 1: 1, 2: 0.8, 3: 0.6 } as const;
export function wallShade(kind: ChartOverlay["kind"], order: 1 | 2 | 3 | undefined):
  { line: string; tag: string } | null {
  if (kind !== "ASK_WALL" && kind !== "BID_WALL") return null;
  const step = order ?? 3;
  return { line: `rgba(${WALL_RGB[kind]},${WALL_LINE_ALPHA[step]})`,
           tag: `rgba(${WALL_RGB[kind]},${WALL_TAG_ALPHA[step]})` };
}

/** Vertical room one price-axis label needs, in CSS px. Two labels closer than this overprint. */
export const LABEL_GAP_PX = 16;

/** Which context labels to show, given where every label would sit (y in px, null = off the pane).
 *
 *  The last price comes first, then the account's own lines, then resistance/support, then walls.
 *  A context line whose label would land within `LABEL_GAP_PX` of one already placed keeps its
 *  line but loses its label: the price and the position are never the ones that give way. Lines
 *  that are not context lines are always shown and only take part as obstacles. */
export function contextLabelsShown(lastPriceY: number | null,
  lines: { id: string; kind: ChartOverlay["kind"]; y: number | null }[]): Set<string> {
  const rank = (kind: ChartOverlay["kind"]) =>
    kind === "RESISTANCE" || kind === "SUPPORT" ? 2 : kind === "ASK_WALL" || kind === "BID_WALL" ? 3 : 1;
  const placed: number[] = lastPriceY == null ? [] : [lastPriceY];
  const shown = new Set<string>();
  const ordered = [...lines].sort((a, b) => rank(a.kind) - rank(b.kind));
  for (const line of ordered) {
    if (line.y == null) continue;
    if (!CONTEXT_KINDS.has(line.kind)) {
      placed.push(line.y);
      continue;
    }
    if (placed.every(y => Math.abs(y - (line.y as number)) >= LABEL_GAP_PX)) {
      placed.push(line.y);
      shown.add(line.id);
    }
  }
  return shown;
}

/** Height of one context tag on the chart, in CSS px; tags closer than this are pushed apart. */
export const TAG_HEIGHT_PX = 15;

export type ContextTag = { id: string; kind: ChartOverlay["kind"]; text: string; top: number;
  off: "UP" | "DOWN" | null; order?: 1 | 2 | 3 };

/** Where each context line's name tag goes on the chart's left edge.
 *
 *  A line on the pane gets its tag just above the line. Resistance or support off the pane gets
 *  its tag on the top or bottom edge with an arrow, because the price scale is never widened to
 *  bring a level into view; a wall off the pane gets nothing (it is not near the price). Tags are
 *  then pushed apart top to bottom so none overprints another; the order never changes, so a tag
 *  stays on the same side of its neighbours as its line. */
export function contextTags(lines: { id: string; kind: ChartOverlay["kind"]; y: number | null; tag: string;
  order?: 1 | 2 | 3 }[],
  paneHeight: number): ContextTag[] {
  if (paneHeight <= 0) return [];
  const placed: ContextTag[] = [];
  for (const line of lines) {
    if (!CONTEXT_KINDS.has(line.kind) || line.y == null) continue;
    const level = line.kind === "RESISTANCE" || line.kind === "SUPPORT";
    if (line.y < 0 || line.y > paneHeight) {
      if (!level) continue;
      const up = line.y < 0;
      placed.push({ id: line.id, kind: line.kind, text: `${line.tag} ${up ? "↑" : "↓"}`,
                    top: up ? 0 : paneHeight - TAG_HEIGHT_PX, off: up ? "UP" : "DOWN" });
    } else {
      // Just above its line, so the tag names the line without covering it.
      placed.push({ id: line.id, kind: line.kind, text: line.tag, top: line.y - TAG_HEIGHT_PX - 1, off: null,
                    order: line.order });
    }
  }
  placed.sort((x, y) => x.top - y.top);
  for (let i = 0; i < placed.length; i++) {
    const floor = i === 0 ? 0 : placed[i - 1].top + TAG_HEIGHT_PX;
    placed[i].top = Math.max(placed[i].top, floor);
  }
  // A stack pushed past the bottom is shifted back up as a block, still in order.
  const overflow = placed.length ? placed[placed.length - 1].top + TAG_HEIGHT_PX - paneHeight : 0;
  if (overflow > 0) {
    for (let i = placed.length - 1; i >= 0; i--) {
      const ceiling = i === placed.length - 1 ? paneHeight - TAG_HEIGHT_PX : placed[i + 1].top - TAG_HEIGHT_PX;
      placed[i].top = Math.max(0, Math.min(placed[i].top, ceiling));
    }
  }
  return placed;
}

const TAG_COLOR: Partial<Record<ChartOverlay["kind"], string>> = {
  RESISTANCE: "rgba(148,163,184,0.8)", SUPPORT: "rgba(148,163,184,0.8)",
};

/** Candlestick chart, isolated in its own client component.
 *
 *  lightweight-charts drives an imperative canvas and touches `window` on construction, so it is
 *  created inside an effect and never during render: Next renders this tree on the server too,
 *  and a chart built at module scope would fail there. The whole library is loaded dynamically by
 *  the parent for the same reason and to keep it out of the initial bundle.
 *
 *  Data flows in through `setData`/`update` rather than by recreating the chart, so panning and
 *  zooming survive the 1 Hz refresh. A chart that reset its viewport every second would be
 *  unusable for exactly the person this screen is for.
 */
export function CandleChart({ candles, overlays, className = "h-[300px]",
  seriesKey = "default", seconds = false, onPriceRange, onNeedMoreHistory,
  priceFormat = { precision: 1, minMove: 0.1 } }: {
  candles: ChartPoint[];
  /** The instrument's price grid. BTC's tick is 0.10; a HOT symbol's can be 0.000001, and the
   *  default one decimal would draw every candle and label at 0.0. */
  priceFormat?: { precision: number; minMove: number };
  overlays: ChartOverlay[];
  /** Height is a Tailwind class rather than a number so the chart can grow on a wide screen
   *  without this component learning about breakpoints. `autoSize` follows the container. */
  className?: string;
  /** Identity of the series (the timeframe). A new key replaces the data and refits; the same
   *  key only appends or amends the newest candles. */
  seriesKey?: string;
  /** Show seconds on the time axis (the 15 s view). */
  seconds?: boolean;
  /** Called near the left edge; the owner supplies loading/end locks. */
  onNeedMoreHistory?: () => void;
  /** The price range currently on screen, reported whenever it may have changed. */
  onPriceRange?: (range: { from: number; to: number } | null) => void;
}) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const priceSeries = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lines = useRef<Map<string, IPriceLine>>(new Map());
  // What each line was asked to be, and whether its label is currently suppressed, so the
  // label pass can toggle labels without rebuilding the line.
  const lineSpecs = useRef<Map<string, { kind: ChartOverlay["kind"]; price: number; title: string; tag?: string; order?: 1 | 2 | 3 }>>(new Map());
  const tagLayer = useRef<HTMLDivElement | null>(null);
  const labelHidden = useRef<Set<string>>(new Set());
  const fitted = useRef(false);
  const drawn = useRef<{ key: string; times: number[] } | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  /** Publish what is actually on screen as data attributes (bars in view, bar spacing, price
   *  span). Read by tests and by the layout measurements; nothing in the app depends on them. */
  const secondsRef = useRef(seconds);
  secondsRef.current = seconds;
  // Read by the autoscale provider, which is created once with the chart.
  const minMoveRef = useRef(priceFormat.minMove);
  minMoveRef.current = priceFormat.minMove;
  const historyListener = useRef(onNeedMoreHistory);
  historyListener.current = onNeedMoreHistory;
  const rangeListener = useRef(onPriceRange);
  rangeListener.current = onPriceRange;
  /** Hide the label of a context line that would overprint the price or a higher line's label. */
  const declutter = useCallback(() => {
    const series = priceSeries.current;
    if (!series) return;
    const data = series.data();
    const lastBar = data.length > 0 ? data[data.length - 1] as { close?: number } : null;
    const lastY = lastBar?.close != null ? series.priceToCoordinate(lastBar.close) : null;
    // `priceToCoordinate` answers for any price, on the pane or not, so "on the pane" is read
    // from the coordinate against the pane's height. (The scale's visible range is not it: that
    // range leaves out the scale margins, where a line is still drawn.)
    const paneHeight = chart.current?.paneSize(0).height ?? 0;
    const at = [...lineSpecs.current.entries()].map(([id, spec]) =>
      ({ id, kind: spec.kind, tag: spec.tag ?? "", order: spec.order,
         y: series.priceToCoordinate(spec.price) as number | null }));
    const entries = at.map(entry => ({ ...entry,
      y: entry.y != null && entry.y >= 0 && entry.y <= paneHeight ? entry.y : null }));
    const shown = contextLabelsShown(lastY, entries);
    const marks: string[] = [];
    for (const { id, kind, y } of entries) {
      if (!CONTEXT_KINDS.has(kind)) continue;
      const hide = !shown.has(id);
      marks.push(`${id}:${y == null ? "off" : hide ? "line" : "label"}`);
      if (hide === labelHidden.current.has(id)) continue;
      const spec = lineSpecs.current.get(id);
      lines.current.get(id)?.applyOptions(hide
        ? { axisLabelVisible: false, title: "" }
        : { axisLabelVisible: true, title: spec?.title ?? "" });
      if (hide) labelHidden.current.add(id);
      else labelHidden.current.delete(id);
    }
    if (box.current) box.current.dataset.contextLines = marks.sort().join("|");
    // Name tags, drawn as plain text nodes (no markup from data) in a click-through layer.
    const layer = tagLayer.current;
    if (layer) {
      const tags = contextTags(at, paneHeight);
      const key = tags.map(t => `${t.id}:${Math.round(t.top)}:${t.text}:${t.order ?? ""}`).join("|");
      if (layer.dataset.key !== key) {
        layer.dataset.key = key;
        layer.replaceChildren(...tags.map(tag => {
          const node = document.createElement("span");
          node.textContent = tag.text;
          node.dataset.tag = tag.id;
          node.dataset.off = tag.off ?? "";
          node.dataset.order = tag.order ? String(tag.order) : "";
          node.className = "absolute left-1 whitespace-nowrap rounded bg-surface px-1 text-[10px] font-medium leading-[15px] tabular-nums opacity-90";
          node.style.top = `${Math.round(tag.top)}px`;
          node.style.color = wallShade(tag.kind, tag.order)?.tag ?? TAG_COLOR[tag.kind] ?? "";
          return node;
        }));
      }
    }
  }, []);

  const report = useCallback(() => {
    const api = chart.current;
    const node = box.current;
    if (!api || !node) return;
    const range = api.timeScale().getVisibleLogicalRange();
    const prices = priceSeries.current?.priceScale().getVisibleRange();
    // Points actually on screen, not the right-hand margin the logical range also spans.
    const count = priceSeries.current?.data().length ?? 0;
    node.dataset.visibleBars = range
      ? String(Math.max(0, Math.min(count - 1, Math.floor(range.to)) - Math.max(0, Math.ceil(range.from)) + 1))
      : "";
    node.dataset.barSpacing = String(api.timeScale().options().barSpacing);
    // Where the view sits: its right edge against the newest bar's index (live edge when >=).
    node.dataset.rangeTo = range ? range.to.toFixed(1) : "";
    node.dataset.lastIndex = String(count - 1);
    node.dataset.priceSpan = prices ? (prices.to - prices.from).toFixed(2) : "";
    if (range && range.from <= 20) historyListener.current?.();
    node.dataset.profile = secondsRef.current ? "15s" : "minute";
    rangeListener.current?.(prices ? { from: prices.from, to: prices.to } : null);
    declutter();
  }, [declutter]);

  useEffect(() => {
    let disposed = false;
    const node = holder.current;
    if (!node) return;
    // Captured for the cleanup: the ref object itself outlives the effect, and reading
    // `.current` during teardown is what the lint rule is warning about.
    const priceLines = lines.current;
    const specs = lineSpecs.current;
    const hidden = labelHidden.current;

    (async () => {
      // The library is imported at the top of this module rather than awaited here, which is
      // what collapses the old three-request chain into one. Both end up in the same
      // `next/dynamic` chunk, so by the time this effect runs there is nothing left to fetch.
      // The function stays async: the body below still awaits nothing, but the early returns
      // and the disposal flag are written against a suspended effect and are left as they are.
      if (disposed || !holder.current) return;

      const created = createChart(holder.current, {
        autoSize: true,
        layout: {
          background: { color: "transparent" },
          textColor: "rgba(148,163,184,0.9)",
          attributionLogo: false,
        },
        grid: {
          vertLines: { color: "rgba(148,163,184,0.10)" },
          horzLines: { color: "rgba(148,163,184,0.10)" },
        },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: "rgba(148,163,184,0.25)", scaleMargins: { top: 0.08, bottom: 0.28 } },
        timeScale: {
          borderColor: "rgba(148,163,184,0.25)",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 3,
        },
        localization: { locale: "ko-KR", timeFormatter: kstCrosshairLabel(false) },
        handleScroll: true,
        handleScale: true,
      });

      const price = created.addSeries(CandlestickSeries, {
        upColor: "#16a34a", downColor: "#dc2626",
        borderUpColor: "#16a34a", borderDownColor: "#dc2626",
        wickUpColor: "#16a34a", wickDownColor: "#dc2626",
        priceFormat: { type: "price", precision: priceFormat.precision, minMove: priceFormat.minMove },
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => widenPriceRange(original(),
          minPriceSpan(secondsRef.current, minMoveRef.current)),
      });

      // Volume shares the pane but lives on its own scale pinned to the bottom quarter, so a
      // volume spike cannot squash the candles.
      const volume = created.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      created.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

      chart.current = created;
      priceSeries.current = price;
      volumeSeries.current = volume;
      created.timeScale().subscribeVisibleLogicalRangeChange(() => report());
    })();

    return () => {
      disposed = true;
      priceLines.clear();
      specs.clear();
      hidden.clear();
      chart.current?.remove();
      chart.current = null;
      priceSeries.current = null;
      volumeSeries.current = null;
      fitted.current = false;
      drawn.current = null;
    };
  }, [report]);

  // The chart is built once; the price grid follows the instrument on screen.
  useEffect(() => {
    priceSeries.current?.applyOptions({
      priceFormat: { type: "price", precision: priceFormat.precision, minMove: priceFormat.minMove } });
  }, [priceFormat.precision, priceFormat.minMove]);

  useEffect(() => {
    const price = priceSeries.current;
    const volume = volumeSeries.current;
    if (!price || !volume || candles.length === 0) return;
    // Whitespace keeps the slot on the time axis and draws nothing in it.
    const bar = (point: ChartPoint) => isWhitespace(point) ? { time: point.time as never } : ({
      time: point.time as never, open: point.open, high: point.high, low: point.low, close: point.close,
    });
    const vol = (point: ChartPoint) => isWhitespace(point) ? { time: point.time as never } : ({
      time: point.time as never, value: point.volume,
      color: point.close >= point.open ? "rgba(22,163,74,0.45)" : "rgba(220,38,38,0.45)",
    });
    const previous = drawn.current;
    const times = candles.map(candle => candle.time);
    // Incremental when this is the same series and only its tail moved: the newest candle
    // amended, or one or two appended. Anything else - a new timeframe, a gap backfilled - is a
    // full replace. `update` keeps the viewport and costs one bar instead of the whole series.
    const n = previous?.times.length ?? 0;
    if (isTailUpdate(previous, seriesKey, times)) {
      for (let index = n - 1; index < candles.length; index++) {
        price.update(bar(candles[index]));
        volume.update(vol(candles[index]));
      }
    } else {
      const profile = seconds ? PROFILES.seconds : PROFILES.minute;
      const api = chart.current;
      const visible = api?.timeScale().getVisibleLogicalRange() ?? null;
      api?.applyOptions({
        timeScale: { secondsVisible: seconds, barSpacing: profile.barSpacing, minBarSpacing: profile.minBarSpacing,
                     rightOffset: profile.rightOffset, tickMarkFormatter: kstTickLabel(seconds) },
        localization: { timeFormatter: kstCrosshairLabel(seconds) },
      });
      price.priceScale().applyOptions({ scaleMargins: { top: profile.priceTop, bottom: profile.priceBottom } });
      api?.priceScale("volume").applyOptions({ scaleMargins: { top: profile.volumeTop, bottom: 0 } });
      price.setData(candles.map(bar));
      volume.setData(candles.map(vol));
      const held = heldViewport(previous, seriesKey, visible, times);
      if (held) api?.timeScale().setVisibleLogicalRange(held);
      // Place the view on the first draw and whenever the series changes. After that the
      // viewport belongs to whoever is panning it.
      if (!fitted.current || (previous != null && previous.key !== seriesKey)) {
        const width = holder.current?.clientWidth ?? 0;
        const bars = seconds ? visible15sBars(width) : visibleMinuteBars(width);
        api?.timeScale().setVisibleLogicalRange({ from: candles.length - bars, to: candles.length - 1 + profile.rightOffset });
        fitted.current = true;
      }
    }
    report();
    drawn.current = { key: seriesKey, times };
  }, [candles, seriesKey, seconds, report]);

  useEffect(() => {
    const price = priceSeries.current;
    if (!price) return;
    const wanted = new Set(overlays.map(overlay => overlay.id));
    for (const [id, line] of lines.current) {
      if (!wanted.has(id)) {
        price.removePriceLine(line);
        lines.current.delete(id);
        lineSpecs.current.delete(id);
        labelHidden.current.delete(id);
      }
    }
    for (const overlay of overlays) {
      const existing = lines.current.get(overlay.id);
      const style = OVERLAY_STYLE[overlay.kind];
      // A label the last pass suppressed stays suppressed until the next pass decides again.
      const hidden = labelHidden.current.has(overlay.id);
      const options = {
        price: overlay.price, color: wallShade(overlay.kind, overlay.order)?.line ?? style.color,
        lineWidth: 1 as const,
        lineStyle: style.lineStyle, axisLabelVisible: !hidden, title: hidden ? "" : overlay.label,
      };
      lineSpecs.current.set(overlay.id, { kind: overlay.kind, price: overlay.price, title: overlay.label,
                                          tag: overlay.tag, order: overlay.order });
      if (existing) existing.applyOptions(options);
      else lines.current.set(overlay.id, price.createPriceLine(options));
    }
    declutter();
  }, [overlays, declutter]);

  return (
    <div ref={box} className={`relative w-full ${className}`} data-testid="candle-chart">
      <div ref={holder} className="absolute inset-0" />
      {/* Context line names (BTC only), on the chart's left edge: inside the chart box, so they
          never move anything around it, and click-through, so they never take a pan or a zoom. */}
      {/* z-[3]: the library's canvases are z-index 1 and 2 inside the holder, and an unstacked layer
          would sit under them. */}
      <div ref={tagLayer} data-testid="context-tags" className="pointer-events-none absolute inset-0 z-[3] overflow-hidden" />
      {candles.length === 0 && (
        <p className="absolute inset-0 flex items-center justify-center text-sm text-muted">
          차트 데이터를 받는 중입니다.
        </p>
      )}
    </div>
  );
}

/** The logical range a full replace has to hand back, or null when the view is to be placed afresh.
 *
 *  `barSpacing` belongs to the profile a full replace re-applies, so the chart re-zooms to one
 *  screen of candles unless the range it was showing is put back. That was done only for a
 *  prepend, which left every other full replace of the same series throwing the viewport away.
 *  The merge that does it is a tail that moved by more than two bars, which is neither a tail
 *  update nor a prepend: production answered the 1m tail poll with 502 or 504 twenty times today
 *  and left four gaps of 141 s to 567 s in it, and a backgrounded tab throttles the same timer to
 *  minutes. An operator who had zoomed out to read two days of minutes was put back on two hours
 *  of them, which from the chair is indistinguishable from the chart having lost its history.
 *
 *  `prepended` is zero when nothing was prepended, so one call both holds the view still and
 *  shifts it over the bars that arrived in front of it. */
export function heldViewport(previous: { key: string; times: number[] } | null, key: string,
                             visible: { from: number; to: number } | null,
                             times: number[]): { from: number; to: number } | null {
  if (visible == null || previous == null || previous.key !== key) return null;
  const prepended = prependedBars(previous.times, times);
  const appended = times.length - previous.times.length - prepended;
  // A view whose right edge was sitting on the newest bar keeps sitting on it, which is what
  // `update` does on a tail update; a view the operator had pulled back stays where they left
  // it. Without the first half of that, a chart left at the live edge would fall one gap further
  // behind it every time this branch ran.
  const live = visible.to >= previous.times.length - 1;
  const shift = prepended + (live ? Math.max(0, appended) : 0);
  return { from: visible.from + shift, to: visible.to + shift };
}

/** Number of bars prepended to the same series, or zero when this is not a pure prepend. */
export function prependedBars(previous: number[], next: number[]): number {
  if (previous.length === 0 || next.length <= previous.length) return 0;
  const offset = next.indexOf(previous[0]);
  if (offset <= 0 || offset + previous.length > next.length) return 0;
  return previous.every((time, index) => next[offset + index] === time) ? offset : 0;
}
