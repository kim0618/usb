import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CHART_HISTORY_PAGE, CHART_INITIAL_BARS, useChartHistory } from "@/components/crypto-terminal-layout";
import { chartBarsToCandles, cryptoApi, mergeCandles } from "@/lib/crypto-paper";
import { kstCrosshairLabel, kstTickLabel, prependedBars } from "@/components/crypto-candle-chart";
import type { ChartBar, ChartHistoryResponse, HistoryTimeframe } from "@/lib/crypto-paper";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const bar = (start_ms: number, close = "1"): ChartBar => ({
  start_ms, open: "1", high: "2", low: "0.5", close, volume: "3", confirmed: true,
});
const response = (timeframe: HistoryTimeframe, bars: ChartBar[], has_more = true): ChartHistoryResponse => ({
  timeframe, bucket_ms: 60_000, source: "BYBIT_PUBLIC_KLINE", source_interval: "1",
  bars, has_more, next_before_ms: bars[0]?.start_ms ?? null,
});

describe("chart V2 history", () => {
  it("uses bounded initial histories appropriate to every timeframe", () => {
    expect(CHART_INITIAL_BARS).toEqual({ "1m": 2880, "10m": 2016, "1h": 2160, "4h": 2190, "1d": 2000 });
    expect(Math.max(...Object.values(CHART_INITIAL_BARS))).toBeLessThan(10_000);
  });

  it("sorts, deduplicates and lets the newest page replace a timestamp", () => {
    const parsed = chartBarsToCandles([bar(60_000), bar(0), bar(60_000, "9")]);
    expect(parsed.map(item => item.time)).toEqual([0, 60]);
    expect(parsed[1].close).toBe(9);
    expect(mergeCandles(parsed, chartBarsToCandles([bar(60_000, "10"), bar(120_000)]))
      .map(item => [item.time, item.close])).toEqual([[0, 1], [60, 10], [120, 1]]);
  });

  it("drops a stale timeframe response after switching", async () => {
    let resolveMinute!: (value: ChartHistoryResponse) => void;
    let resolveHour!: (value: ChartHistoryResponse) => void;
    vi.spyOn(cryptoApi, "chartHistory").mockImplementation((frame) => new Promise(resolve => {
      if (frame === "1m") resolveMinute = resolve;
      else resolveHour = resolve;
    }));
    const { result, rerender } = renderHook(({ frame }) => useChartHistory(true, frame),
      { initialProps: { frame: "1m" as HistoryTimeframe } });
    rerender({ frame: "1h" });
    await act(async () => { resolveMinute(response("1m", [bar(60_000)])); });
    expect(result.current.candles).toEqual([]);
    await act(async () => { resolveHour(response("1h", [bar(3_600_000)], false)); });
    await waitFor(() => expect(result.current.candles.map(item => item.time)).toEqual([3600]));
  });

  it("prepends one locked page without duplicates and records end-of-history", async () => {
    vi.spyOn(cryptoApi, "chartHistory")
      .mockResolvedValueOnce(response("1m", [bar(60_000), bar(120_000)]))
      .mockResolvedValueOnce(response("1m", [bar(0), bar(60_000)], false));
    const { result } = renderHook(() => useChartHistory(true, "1m"));
    await waitFor(() => expect(result.current.candles).toHaveLength(2));
    await act(async () => { await result.current.loadEarlier(); });
    expect(result.current.candles.map(item => item.time)).toEqual([0, 60, 120]);
    expect(result.current.end).toBe(true);
    expect(cryptoApi.chartHistory).toHaveBeenLastCalledWith("1m", CHART_HISTORY_PAGE, 60_000);
  });

  it("detects pure prepend so the chart can preserve its logical range", () => {
    expect(prependedBars([10, 20, 30], [-10, 0, 10, 20, 30])).toBe(2);
    expect(prependedBars([10, 20, 30], [0, 10, 25, 30])).toBe(0);
  });

  /** 2026-10-01 00:00 UTC, which is 09:00 the same morning in KST. On the daily and 4 h views
   *  every tick would otherwise read "09:00" and no bar could be told from another. */
  const dayStart = Date.UTC(2026, 9, 1) / 1000;

  it("labels day and month ticks with a date and time ticks with the KST clock", () => {
    expect(kstTickLabel(false)(dayStart, 2)).toBe("10. 01.");
    expect(kstTickLabel(false)(dayStart, 1)).toBe("26년 10월");
    expect(kstTickLabel(false)(dayStart, 0)).toBe("2026년");
    expect(kstTickLabel(false)(dayStart, 3)).toBe("09:00");
    expect(kstTickLabel(true)(dayStart, 4)).toBe("09:00:00");
  });

  it("always carries the date in the crosshair reading", () => {
    expect(kstCrosshairLabel(false)(dayStart)).toBe("10. 01. 09:00");
    expect(kstCrosshairLabel(true)(dayStart)).toBe("10. 01. 09:00:00");
  });
});
