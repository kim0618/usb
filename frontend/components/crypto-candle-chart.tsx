"use client";

import { useCallback, useEffect, useRef } from "react";
import type { AutoscaleInfo, IChartApi, IPriceLine, ISeriesApi } from "lightweight-charts";
import type { ChartOverlay, ChartPoint } from "@/lib/crypto-paper";
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
  minute: { barSpacing: 6, minBarSpacing: 0.5, rightOffset: 3, priceTop: 0.08, priceBottom: 0.28, volumeTop: 0.8,
    minPriceSpan: 0 },
  seconds: { barSpacing: 7, minBarSpacing: 2, rightOffset: 4, priceTop: 0.1, priceBottom: 0.2, volumeTop: 0.84,
    minPriceSpan: 10 },
} as const;

/** Autoscale that never zooms tighter than `minSpan` (USDT), centred on the candles. Without it a
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
export function CandleChart({ candles, overlays, className = "h-[300px]", seriesKey = "default", seconds = false,
  onPriceRange, onNeedMoreHistory }: {
  candles: ChartPoint[];
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
  const fitted = useRef(false);
  const drawn = useRef<{ key: string; times: number[] } | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  /** Publish what is actually on screen as data attributes (bars in view, bar spacing, price
   *  span). Read by tests and by the layout measurements; nothing in the app depends on them. */
  const secondsRef = useRef(seconds);
  secondsRef.current = seconds;
  const historyListener = useRef(onNeedMoreHistory);
  historyListener.current = onNeedMoreHistory;
  const rangeListener = useRef(onPriceRange);
  rangeListener.current = onPriceRange;
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
    node.dataset.priceSpan = prices ? (prices.to - prices.from).toFixed(2) : "";
    if (range && range.from <= 20) historyListener.current?.();
    node.dataset.profile = secondsRef.current ? "15s" : "minute";
    rangeListener.current?.(prices ? { from: prices.from, to: prices.to } : null);
  }, []);

  useEffect(() => {
    let disposed = false;
    const node = holder.current;
    if (!node) return;
    // Captured for the cleanup: the ref object itself outlives the effect, and reading
    // `.current` during teardown is what the lint rule is warning about.
    const priceLines = lines.current;

    (async () => {
      const lib = await import("lightweight-charts");
      if (disposed || !holder.current) return;

      const created = lib.createChart(holder.current, {
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
        crosshair: { mode: lib.CrosshairMode.Normal },
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

      const price = created.addSeries(lib.CandlestickSeries, {
        upColor: "#16a34a", downColor: "#dc2626",
        borderUpColor: "#16a34a", borderDownColor: "#dc2626",
        wickUpColor: "#16a34a", wickDownColor: "#dc2626",
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => widenPriceRange(original(),
          (secondsRef.current ? PROFILES.seconds : PROFILES.minute).minPriceSpan),
      });

      // Volume shares the pane but lives on its own scale pinned to the bottom quarter, so a
      // volume spike cannot squash the candles.
      const volume = created.addSeries(lib.HistogramSeries, {
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
      chart.current?.remove();
      chart.current = null;
      priceSeries.current = null;
      volumeSeries.current = null;
      fitted.current = false;
      drawn.current = null;
    };
  }, [report]);

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
      const prepended = previous?.key === seriesKey ? prependedBars(previous.times, times) : 0;
      api?.applyOptions({
        timeScale: { secondsVisible: seconds, barSpacing: profile.barSpacing, minBarSpacing: profile.minBarSpacing,
                     rightOffset: profile.rightOffset, tickMarkFormatter: kstTickLabel(seconds) },
        localization: { timeFormatter: kstCrosshairLabel(seconds) },
      });
      price.priceScale().applyOptions({ scaleMargins: { top: profile.priceTop, bottom: profile.priceBottom } });
      api?.priceScale("volume").applyOptions({ scaleMargins: { top: profile.volumeTop, bottom: 0 } });
      price.setData(candles.map(bar));
      volume.setData(candles.map(vol));
      if (prepended > 0 && visible) {
        api?.timeScale().setVisibleLogicalRange({ from: visible.from + prepended, to: visible.to + prepended });
      }
      // Place the view on the first draw and whenever the series changes. After that the
      // viewport belongs to whoever is panning it.
      if (!fitted.current || (previous != null && previous.key !== seriesKey)) {
        const width = holder.current?.clientWidth ?? 0;
        const bars = seconds ? visible15sBars(width) : Math.max(60, Math.floor(width / profile.barSpacing));
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
    const colors: Record<ChartOverlay["kind"], string> = {
      MARK: "rgba(148,163,184,0.85)", ENTRY: "#38bdf8", LIQUIDATION: "#f59e0b",
    };
    const wanted = new Set(overlays.map(overlay => overlay.id));
    for (const [id, line] of lines.current) {
      if (!wanted.has(id)) {
        price.removePriceLine(line);
        lines.current.delete(id);
      }
    }
    for (const overlay of overlays) {
      const existing = lines.current.get(overlay.id);
      const options = {
        price: overlay.price, color: colors[overlay.kind], lineWidth: 1 as const,
        lineStyle: overlay.kind === "MARK" ? 2 : 0, axisLabelVisible: true, title: overlay.label,
      };
      if (existing) existing.applyOptions(options);
      else lines.current.set(overlay.id, price.createPriceLine(options));
    }
  }, [overlays]);

  return (
    <div ref={box} className={`relative w-full ${className}`} data-testid="candle-chart">
      <div ref={holder} className="absolute inset-0" />
      {candles.length === 0 && (
        <p className="absolute inset-0 flex items-center justify-center text-sm text-muted">
          차트 데이터를 받는 중입니다.
        </p>
      )}
    </div>
  );
}

/** Number of bars prepended to the same series, or zero when this is not a pure prepend. */
export function prependedBars(previous: number[], next: number[]): number {
  if (previous.length === 0 || next.length <= previous.length) return 0;
  const offset = next.indexOf(previous[0]);
  if (offset <= 0 || offset + previous.length > next.length) return 0;
  return previous.every((time, index) => next[offset + index] === time) ? offset : 0;
}
