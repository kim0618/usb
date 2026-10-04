import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CHART_HISTORY_PAGE, CHART_HISTORY_POLL_MS, CHART_INITIAL_BARS, useChartHistory,
} from "@/components/crypto-terminal-layout";
import { chartBarsToCandles, cryptoApi, mergeCandles } from "@/lib/crypto-paper";
import {
  heldViewport, kstCrosshairLabel, kstTickLabel, prependedBars,
} from "@/components/crypto-candle-chart";
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
    vi.spyOn(cryptoApi, "chartHistory").mockImplementation((_symbol, frame) => new Promise(resolve => {
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
    expect(cryptoApi.chartHistory).toHaveBeenLastCalledWith("BTCUSDT", "1m", CHART_HISTORY_PAGE, 60_000);
  });

  it("starts the series over when the symbol changes and keeps the timeframe", async () => {
    // The bug this guards: with the series keyed on the timeframe alone, switching symbol on
    // 1m left the previous instrument's candles on screen and merged the new ones into them.
    vi.spyOn(cryptoApi, "chartHistory")
      .mockResolvedValueOnce(response("1m", [bar(60_000), bar(120_000)], false))
      .mockResolvedValueOnce(response("1m", [bar(180_000)], false));
    const { result, rerender } = renderHook(({ symbol }) => useChartHistory(true, "1m", symbol),
      { initialProps: { symbol: "BTCUSDT" } });
    await waitFor(() => expect(result.current.candles).toHaveLength(2));
    rerender({ symbol: "ETHUSDT" });
    // The previous symbol's candles are gone immediately, not after the new fetch lands.
    expect(result.current.candles).toEqual([]);
    await waitFor(() => expect(result.current.candles.map(item => item.time)).toEqual([180]));
    expect(cryptoApi.chartHistory).toHaveBeenLastCalledWith(
      "ETHUSDT", "1m", expect.anything(), null, expect.anything());
  });

  it("retries the initial history instead of leaving the screen on the execution feed", async () => {
    vi.useFakeTimers();
    const history = vi.spyOn(cryptoApi, "chartHistory")
      .mockRejectedValueOnce(new Error("CHART_SOURCE_UNAVAILABLE"))
      .mockResolvedValueOnce(response("1m", [bar(60_000), bar(120_000)]));
    const { result } = renderHook(() => useChartHistory(true, "1m"));
    await act(async () => { await Promise.resolve(); });
    // The regression this pins. The seed was asked for once, so a 502 from this route left the
    // series empty with `loading` already false: the 1m screen fell back to the execution feed's
    // 120 one-minute bars, said nothing about it, and stayed there until the page was reloaded.
    expect(result.current.candles).toEqual([]);
    expect(result.current.loading).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(CHART_HISTORY_POLL_MS); });
    expect(result.current.candles.map(item => item.time)).toEqual([60, 120]);
    expect(result.current.loading).toBe(false);
    expect(history).toHaveBeenNthCalledWith(2, "BTCUSDT", "1m", CHART_INITIAL_BARS["1m"], null,
                                            expect.anything());
  });

  it("detects pure prepend so the chart can preserve its logical range", () => {
    expect(prependedBars([10, 20, 30], [-10, 0, 10, 20, 30])).toBe(2);
    expect(prependedBars([10, 20, 30], [0, 10, 25, 30])).toBe(0);
  });

  it("hands the viewport back after every full replace of the same series", () => {
    const drawn = { key: "BTCUSDT|1m", times: [0, 60, 120, 180, 240] };
    const pulledBack = { from: 1, to: 2 };
    const atLiveEdge = { from: 2, to: 4 };

    // The regression this pins. A tail that moved by more than two bars is neither a tail update
    // nor a prepend, so it takes the full-replace branch, which re-applies `barSpacing`. With no
    // range handed back the chart re-zoomed to one screen of candles and threw away an operator's
    // zoom-out: a 1m view of two days came back showing two hours of them.
    const jumped = [...drawn.times, 300, 360, 420];
    expect(heldViewport(drawn, "BTCUSDT|1m", pulledBack, jumped)).toEqual(pulledBack);
    // A view that was sitting on the newest bar follows the bars that arrived, the way a tail
    // update does, instead of falling one gap further behind the live edge each time.
    expect(heldViewport(drawn, "BTCUSDT|1m", atLiveEdge, jumped)).toEqual({ from: 5, to: 7 });

    // A prepended page shifts the range over the bars that arrived in front of it.
    const prepended = [-120, -60, ...drawn.times];
    expect(heldViewport(drawn, "BTCUSDT|1m", pulledBack, prepended)).toEqual({ from: 3, to: 4 });

    // A new symbol or timeframe is a new series: its view is placed afresh, not inherited.
    expect(heldViewport(drawn, "ETHUSDT|1m", pulledBack, jumped)).toBeNull();
    expect(heldViewport(drawn, "BTCUSDT|1h", pulledBack, jumped)).toBeNull();
    // Nothing drawn yet, or no range to read, is placed afresh as well.
    expect(heldViewport(null, "BTCUSDT|1m", pulledBack, jumped)).toBeNull();
    expect(heldViewport(drawn, "BTCUSDT|1m", null, jumped)).toBeNull();
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

describe("chart history across a symbol change", () => {
  it("does not ask about the previous symbol through a stale callback", async () => {
    // Found in the isolated preview: switching to SOL sent one `chart-history?symbol=BTCUSDT`
    // 346 ms after the click, because the chart holds `loadEarlier` across renders and calls it
    // when its visible range resets. The response was discarded downstream, so nothing wrong
    // reached the screen - but a read about an instrument nobody is looking at should not be
    // sent, and relying on the downstream guard is how the next change breaks it.
    const spy = vi.spyOn(cryptoApi, "chartHistory")
      .mockResolvedValue(response("1m", [bar(60_000), bar(120_000)]));
    const { result, rerender } = renderHook(({ symbol }) => useChartHistory(true, "1m", symbol),
      { initialProps: { symbol: "BTCUSDT" } });
    await waitFor(() => expect(result.current.candles).toHaveLength(2));
    const stale = result.current.loadEarlier;
    rerender({ symbol: "SOLUSDT" });
    spy.mockClear();
    await act(async () => { await stale(); });
    const symbols = spy.mock.calls.map(call => call[0]);
    expect(symbols).not.toContain("BTCUSDT");
  });
});
