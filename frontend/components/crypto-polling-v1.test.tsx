/** CRYPTO UI POLLING OPTIMIZATION V1: cadences, LIVE/PAPER isolation, hidden-tab pause, the chart
 *  seed and its backoff. Every API call is a spy; nothing reaches a server. */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PAPER_POLL_MS, useCryptoTerminal } from "@/components/crypto-paper-terminal";
import { useBinanceLive } from "@/components/crypto-live-terminal";
import { useHotSlot } from "@/components/crypto-hot-coins";
import {
  CHART_HISTORY_PAGE, CHART_HISTORY_POLL_MS, CHART_INITIAL_BARS, CHART_SEED_RETRY_MAX_MS, chartSeedRetryMs,
  useChartHistory,
} from "@/components/crypto-terminal-layout";
import { cryptoApi } from "@/lib/crypto-paper";
import { liveApi } from "@/lib/crypto-live";
import { visiblePoll } from "@/lib/visible-poll";

let hidden = false;
const setHidden = (value: boolean) => {
  hidden = value;
  document.dispatchEvent(new Event("visibilitychange"));
};

beforeEach(() => {
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  vi.useFakeTimers();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

// ------------------------------------------------------------------ visiblePoll

describe("visiblePoll", () => {
  it("runs at once, then once per period", async () => {
    const task = vi.fn();
    const stop = visiblePoll(task, 1_000);
    expect(task).toHaveBeenCalledTimes(1);
    await tick(3_000);
    expect(task).toHaveBeenCalledTimes(4);
    stop();
  });

  it("asks nothing while hidden and reads at once on return", async () => {
    const task = vi.fn();
    const stop = visiblePoll(task, 1_000);
    setHidden(true);
    await tick(10_000);
    expect(task).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);              // no timer left ticking while hidden
    setHidden(false);
    expect(task).toHaveBeenCalledTimes(2);           // immediate read on visible
    await tick(1_000);
    expect(task).toHaveBeenCalledTimes(3);
    stop();
  });

  it("keeps exactly one timer through any number of visibility flips", async () => {
    const task = vi.fn();
    const stop = visiblePoll(task, 1_000);
    for (let i = 0; i < 10; i++) {
      setHidden(true);
      setHidden(false);
      await act(async () => { await Promise.resolve(); });
    }
    expect(vi.getTimerCount()).toBe(1);
    const before = task.mock.calls.length;
    await tick(10_000);
    expect(task.mock.calls.length - before).toBe(10);   // one per period, not one per flip
    stop();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("never overlaps a slow task", async () => {
    let inflight = 0; let most = 0;
    const stop = visiblePoll(async () => {
      inflight++; most = Math.max(most, inflight);
      await new Promise(resolve => setTimeout(resolve, 2_500));
      inflight--;
    }, 1_000);
    setHidden(true); setHidden(false);               // a return while the first run is in flight
    await tick(10_000);
    expect(most).toBe(1);
    stop();
  });

  it("starts no read when created hidden", async () => {
    hidden = true;
    const task = vi.fn();
    const stop = visiblePoll(task, 1_000);
    await tick(5_000);
    expect(task).not.toHaveBeenCalled();
    setHidden(false);
    expect(task).toHaveBeenCalledTimes(1);
    stop();
  });
});

// ------------------------------------------------------------------ PAPER terminal cadences

function paperSpies(position: "LONG" | null = null) {
  const state = vi.spyOn(cryptoApi, "state").mockImplementation(async () => ({
    symbol: "BTCUSDT", account: { position_side: position },
  }) as never);
  const ledger = vi.spyOn(cryptoApi, "ledger").mockResolvedValue({ events: [] } as never);
  const performance = vi.spyOn(cryptoApi, "performance").mockResolvedValue({} as never);
  const trades = vi.spyOn(cryptoApi, "trades").mockResolvedValue({ trades: [] } as never);
  const sizing = vi.spyOn(cryptoApi, "sizing").mockResolvedValue({ sides: {} } as never);
  const breakdown = vi.spyOn(cryptoApi, "pnlBreakdown").mockResolvedValue({} as never);
  const chart = vi.spyOn(cryptoApi, "chart").mockResolvedValue({ bars: [] } as never);
  return { state, ledger, performance, trades, sizing, breakdown, chart };
}

describe("PAPER terminal cadences", () => {
  it("reads state every second and the rest at their own periods", async () => {
    const spy = paperSpies();
    renderHook(() => useCryptoTerminal("BTCUSDT"));
    await tick(30_000);
    // first read + one per period over 30 s
    expect(spy.state).toHaveBeenCalledTimes(1 + 30_000 / PAPER_POLL_MS.state);
    expect(spy.sizing).toHaveBeenCalledTimes(1 + 30_000 / PAPER_POLL_MS.sizing);
    expect(spy.ledger).toHaveBeenCalledTimes(1 + 30_000 / PAPER_POLL_MS.history);
    expect(spy.performance).toHaveBeenCalledTimes(1 + 30_000 / PAPER_POLL_MS.history);
    expect(spy.trades).toHaveBeenCalledTimes(1 + 30_000 / PAPER_POLL_MS.history);
    expect(spy.chart).toHaveBeenCalledTimes(1 + 30_000 / PAPER_POLL_MS.chart);
  });

  it("reads the PnL breakdown once on a flat account, every 5 s with a position", async () => {
    const flat = paperSpies(null);
    const { unmount } = renderHook(() => useCryptoTerminal("BTCUSDT"));
    await tick(30_000);
    expect(flat.breakdown).toHaveBeenCalledTimes(1);
    unmount();
    vi.restoreAllMocks();

    const open = paperSpies("LONG");
    renderHook(() => useCryptoTerminal("BTCUSDT"));
    await tick(30_000);
    expect(open.breakdown.mock.calls.length).toBeGreaterThanOrEqual(30_000 / PAPER_POLL_MS.breakdown);
  });

  it("reads the breakdown once more when the position closes, then stops", async () => {
    let side: "LONG" | null = "LONG";
    const spy = paperSpies();
    spy.state.mockImplementation(async () => ({ symbol: "BTCUSDT", account: { position_side: side } }) as never);
    renderHook(() => useCryptoTerminal("BTCUSDT"));
    await tick(10_000);
    side = null;
    await tick(10_000);
    const afterClose = spy.breakdown.mock.calls.length;
    await tick(30_000);
    expect(spy.breakdown.mock.calls.length).toBe(afterClose);
  });

  it("the LIVE screen polls no paper-account route", async () => {
    const spy = paperSpies();
    renderHook(() => useCryptoTerminal("BTCUSDT", { paper: false }));
    await tick(30_000);
    expect(spy.state).toHaveBeenCalled();             // the LIVE chart still draws from it
    expect(spy.chart).toHaveBeenCalled();
    for (const name of ["ledger", "performance", "trades", "sizing", "breakdown"] as const) {
      expect(spy[name], name).not.toHaveBeenCalled();
    }
  });

  it("switching to PAPER reads everything at once", async () => {
    const spy = paperSpies();
    const { rerender } = renderHook(({ paper }) => useCryptoTerminal("BTCUSDT", { paper }),
      { initialProps: { paper: false } });
    await tick(3_000);
    rerender({ paper: true });
    await act(async () => { await Promise.resolve(); });
    expect(spy.ledger).toHaveBeenCalledTimes(1);
    expect(spy.sizing).toHaveBeenCalledTimes(1);
    expect(spy.breakdown).toHaveBeenCalledTimes(1);
  });

  it("a hidden tab reads nothing, and coming back reads at once", async () => {
    const spy = paperSpies();
    renderHook(() => useCryptoTerminal("BTCUSDT"));
    await tick(1);
    const before = spy.state.mock.calls.length;
    setHidden(true);
    await tick(60_000);
    expect(spy.state.mock.calls.length).toBe(before);
    expect(spy.sizing.mock.calls.length).toBe(1);
    setHidden(false);
    await act(async () => { await Promise.resolve(); });
    expect(spy.state.mock.calls.length).toBe(before + 1);
    expect(spy.sizing.mock.calls.length).toBe(2);
  });
});

// ------------------------------------------------------------------ LIVE terminal

describe("LIVE terminal", () => {
  function liveSpies() {
    return {
      status: vi.spyOn(liveApi, "status").mockResolvedValue({ available: true } as never),
      account: vi.spyOn(liveApi, "account").mockResolvedValue({ symbol: "BTCUSDT" } as never),
      arm: vi.spyOn(liveApi, "armState").mockResolvedValue({} as never),
      sizing: vi.spyOn(liveApi, "sizing").mockResolvedValue({ symbol: "BTCUSDT" } as never),
      card: vi.spyOn(liveApi, "positionCard").mockResolvedValue({ symbol: "BTCUSDT" } as never),
      guard: vi.spyOn(liveApi, "exitGuard").mockResolvedValue({} as never),
      perf: vi.spyOn(liveApi, "performance").mockResolvedValue({} as never),
      leverage: vi.spyOn(liveApi, "leverageOptions").mockResolvedValue({ symbol: "BTCUSDT" } as never),
    };
  }

  it("makes no signed read while the tab is hidden", async () => {
    const spy = liveSpies();
    renderHook(() => useBinanceLive(true, "BTCUSDT"));
    await tick(1);
    const account = spy.account.mock.calls.length;
    const sizing = spy.sizing.mock.calls.length;
    setHidden(true);
    await tick(60_000);
    expect(spy.account.mock.calls.length).toBe(account);
    expect(spy.sizing.mock.calls.length).toBe(sizing);
    setHidden(false);
    await act(async () => { await Promise.resolve(); });
    expect(spy.account.mock.calls.length).toBe(account + 1);
  });

  it("the PAPER screen polls no LIVE route", async () => {
    const spy = liveSpies();
    renderHook(() => useBinanceLive(false, "BTCUSDT"));
    await tick(30_000);
    for (const name of ["account", "arm", "sizing", "card", "guard", "perf", "leverage"] as const) {
      expect(spy[name], name).not.toHaveBeenCalled();
    }
  });
});

// ------------------------------------------------------------------ HOT

describe("HOT lock poll", () => {
  it("names the account on screen and stops while hidden", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
      urls.push(String(input));
      return new Response(JSON.stringify({
        main: "BTCUSDT", hot: null, lock: { state: "ALLOWED", code: "NO_ACTIVE_HOT_SYMBOL", symbol: null, message: "" },
        scanning: false, held: [], permitted: ["BTCUSDT"], paper: null, server_time_ms: 0,
      }), { status: 200, headers: { "content-type": "application/json" } });
    });
    renderHook(() => useHotSlot(undefined, undefined, "PAPER"));
    await tick(1);
    expect(urls.length).toBe(1);
    expect(urls[0]).toMatch(/\/api\/crypto\/hot\?source=PAPER$/);
    setHidden(true);
    await tick(60_000);
    expect(urls.length).toBe(1);
  });
});

// ------------------------------------------------------------------ chart seed

describe("chart seed", () => {
  it("is one page on every timeframe", () => {
    expect(new Set(Object.values(CHART_INITIAL_BARS))).toEqual(new Set([CHART_HISTORY_PAGE]));
  });

  it("backs off 15 s, 30 s, 60 s, then every 2 minutes", () => {
    expect([1, 2, 3, 4, 5, 9].map(chartSeedRetryMs))
      .toEqual([CHART_HISTORY_POLL_MS, 30_000, 60_000, CHART_SEED_RETRY_MAX_MS, CHART_SEED_RETRY_MAX_MS,
                CHART_SEED_RETRY_MAX_MS]);
  });

  it("retries a failing seed on the backoff, not every 15 s", async () => {
    const history = vi.spyOn(cryptoApi, "chartHistory").mockRejectedValue(new Error("504"));
    renderHook(() => useChartHistory(true, "1m"));
    await tick(0);
    expect(history).toHaveBeenCalledTimes(1);
    expect(history).toHaveBeenLastCalledWith("BTCUSDT", "1m", 500, null, expect.anything());
    // Reads at 0, 15, 45, 105 and 225 s: waits of 15, 30, 60 and 120 s.
    await tick(15_000);
    expect(history).toHaveBeenCalledTimes(2);
    await tick(29_999);
    expect(history).toHaveBeenCalledTimes(2);
    await tick(1);
    expect(history).toHaveBeenCalledTimes(3);
    await tick(60_000);
    expect(history).toHaveBeenCalledTimes(4);
    await tick(120_000);
    expect(history).toHaveBeenCalledTimes(5);
  });

  it("pauses the tail while hidden", async () => {
    const history = vi.spyOn(cryptoApi, "chartHistory").mockResolvedValue({
      timeframe: "1m", bucket_ms: 60_000, source: "x", source_interval: "1", bars: [], has_more: false,
      next_before_ms: null,
    } as never);
    renderHook(() => useChartHistory(true, "1m"));
    await tick(CHART_HISTORY_POLL_MS * 3 + 10);
    const seen = history.mock.calls.length;
    expect(seen).toBeGreaterThanOrEqual(3);
    setHidden(true);
    await tick(CHART_HISTORY_POLL_MS * 10);
    expect(history.mock.calls.length).toBe(seen);
  });
});
