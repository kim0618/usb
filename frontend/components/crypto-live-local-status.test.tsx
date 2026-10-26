/** PAPER LOCAL BINANCE STATUS V1: the PAPER screen learns whether LIVE can be offered from the
 *  local route and never asks for the full status, which reads the whole Binance account. The
 *  LIVE screen keeps the full status and its polls. Every API call is a spy; nothing reaches a
 *  server. */
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountSourceSwitch, useBinanceLive } from "@/components/crypto-live-terminal";
import { liveApi } from "@/lib/crypto-live";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

function spies(available = true) {
  return {
    status: vi.spyOn(liveApi, "status").mockImplementation(
      async (symbol?: string) => ({ available, symbol, blockers: [] }) as never),
    local: vi.spyOn(liveApi, "localStatus").mockImplementation(
      async (symbol?: string) => ({ available, symbol, symbols: ["BTCUSDT"] }) as never),
    account: vi.spyOn(liveApi, "account").mockImplementation(
      async (symbol: string) => ({ symbol }) as never),
    arm: vi.spyOn(liveApi, "armState").mockResolvedValue({} as never),
    sizing: vi.spyOn(liveApi, "sizing").mockImplementation(async (symbol: string) => ({ symbol }) as never),
    card: vi.spyOn(liveApi, "positionCard").mockImplementation(
      async (symbol: string) => ({ symbol }) as never),
    guard: vi.spyOn(liveApi, "exitGuard").mockResolvedValue({} as never),
    perf: vi.spyOn(liveApi, "performance").mockResolvedValue({} as never),
    leverage: vi.spyOn(liveApi, "leverageOptions").mockImplementation(
      async (symbol: string) => ({ symbol }) as never),
  };
}

describe("PAPER screen", () => {
  it("asks the local route once on mount and never the full status", async () => {
    const spy = spies();
    const { result } = renderHook(() => useBinanceLive(false, "BTCUSDT"));
    await tick(30_000);
    expect(spy.local).toHaveBeenCalledTimes(1);
    expect(spy.local).toHaveBeenCalledWith("BTCUSDT");
    expect(spy.status).not.toHaveBeenCalled();
    expect(result.current.available).toBe(true);
    for (const name of ["account", "arm", "sizing", "card", "guard", "perf", "leverage"] as const) {
      expect(spy[name], name).not.toHaveBeenCalled();
    }
  });

  it("tab changes, the HOT symbol's included, never ask the full status", async () => {
    const spy = spies();
    const { rerender } = renderHook(({ symbol }) => useBinanceLive(false, symbol),
      { initialProps: { symbol: "BTCUSDT" } });
    await tick(1);
    for (const symbol of ["ETHUSDT", "IRYSUSDT", "BTCUSDT"]) {
      rerender({ symbol });
      await tick(5_000);
    }
    expect(spy.local.mock.calls.map(call => call[0])).toEqual(
      ["BTCUSDT", "ETHUSDT", "IRYSUSDT", "BTCUSDT"]);
    expect(spy.status).not.toHaveBeenCalled();
  });

  it("an answer about a symbol no longer on screen is dropped", async () => {
    const spy = spies();
    spy.local.mockImplementation(async () => ({ available: true, symbol: "ETHUSDT" }) as never);
    const { result } = renderHook(() => useBinanceLive(false, "BTCUSDT"));
    await tick(1);
    expect(result.current.available).toBe(false);
  });
});

describe("switching between PAPER and LIVE", () => {
  it("LIVE asks the full status and starts its polls; PAPER again uses the local route", async () => {
    const spy = spies();
    const { rerender } = renderHook(({ enabled }) => useBinanceLive(enabled, "BTCUSDT"),
      { initialProps: { enabled: false } });
    await tick(1);
    expect(spy.local).toHaveBeenCalledTimes(1);
    expect(spy.status).not.toHaveBeenCalled();

    rerender({ enabled: true });
    await tick(1);
    expect(spy.status).toHaveBeenCalledTimes(1);
    expect(spy.status).toHaveBeenCalledWith("BTCUSDT");
    expect(spy.account).toHaveBeenCalled();
    expect(spy.local).toHaveBeenCalledTimes(1);

    rerender({ enabled: false });
    await tick(1);
    expect(spy.local).toHaveBeenCalledTimes(2);
    expect(spy.status).toHaveBeenCalledTimes(1);
  });

  it("LIVE mount keeps the full status and the account poll, and never the local route", async () => {
    const spy = spies();
    const { result } = renderHook(() => useBinanceLive(true, "BTCUSDT"));
    await tick(6_000);
    expect(spy.status).toHaveBeenCalledTimes(1);
    expect(spy.local).not.toHaveBeenCalled();
    expect(spy.account.mock.calls.length).toBeGreaterThanOrEqual(3);   // the 2 s poll
    expect(spy.arm).toHaveBeenCalled();
    expect(result.current.status?.blockers).toEqual([]);
  });
});

function Switch({ enabled }: { enabled: boolean }) {
  const live = useBinanceLive(enabled, "BTCUSDT");
  return <AccountSourceSwitch value="PAPER" onChange={() => {}} available={live.available} />;
}

describe("the account switch on PAPER", () => {
  it("offers BINANCE LIVE when the server has a key", async () => {
    spies(true);
    render(<Switch enabled={false} />);
    await tick(1);
    expect(screen.getByTestId("source-BINANCE_LIVE")).not.toBeDisabled();
    expect(screen.queryByTestId("live-unavailable-note")).toBeNull();
  });

  it("disables BINANCE LIVE and explains why when the server has none", async () => {
    spies(false);
    render(<Switch enabled={false} />);
    await tick(1);
    expect(screen.getByTestId("source-BINANCE_LIVE")).toBeDisabled();
    expect(screen.getByTestId("live-unavailable-note")).toBeInTheDocument();
  });
});
