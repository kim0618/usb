import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MobilePositionCard, PositionStrip } from "@/components/crypto-terminal-layout";
import { LivePositionPanel, liveHalfClose } from "@/components/crypto-live-terminal";
import { halfCloseQty } from "@/lib/crypto-executions";
import { cryptoApi } from "@/lib/crypto-paper";
import type { CryptoState } from "@/lib/crypto-paper";
import { liveApi } from "@/lib/crypto-live";
import type { LiveAccount } from "@/lib/crypto-live";
import paperTerminal from "@/components/__fixtures__/crypto-terminal-paper.json";

/** HALF CLOSE V1 on the screen side: half the held size floored to the step, sent as a CLOSE on
 *  the held side of the account on screen, never as an entry, never turned into a full close. */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const paper = paperTerminal as unknown as Record<string, Record<string, unknown>>;
function held(side: "LONG" | "SHORT", qty: string, over: Partial<CryptoState> = {}): CryptoState {
  const state = { ...structuredClone(paper.BTCUSDT.state as CryptoState), ...over };
  state.account = { ...state.account!, position_side: side, position_qty: qty,
                    position_signed_qty: side === "LONG" ? qty : `-${qty}`, avg_entry: "84000" };
  return state;
}
const runNow = (run: () => Promise<unknown>) => { void run(); };

describe("half close quantity", () => {
  it("floors half the held size to the step, in whole steps", () => {
    expect(halfCloseQty({ qty: "0.107", step: "0.001" })).toEqual({ qty: "0.053", reason: null });
    expect(halfCloseQty({ qty: "0.100", step: "0.001" }).qty).toBe("0.050");
    expect(halfCloseQty({ qty: "-0.100", step: "0.001" }).qty).toBe("0.050");
    expect(halfCloseQty({ qty: "129697", step: "1" }).qty).toBe("64848");
  });

  it("has no HALF below the minimum quantity or the minimum notional", () => {
    expect(halfCloseQty({ qty: "0.001", step: "0.001", minQty: "0.001" }).qty).toBeNull();
    expect(halfCloseQty({ qty: "0.003", step: "0.001", minQty: "0.002" }).qty).toBeNull();
    const notional = halfCloseQty({ qty: "0.002", step: "0.001", minQty: "0.001", minNotional: "100", price: "84000" });
    expect(notional.qty).toBeNull();
    expect(notional.reason).toContain("최소 100 USDT");
  });
});

describe("PAPER position area", () => {
  it.each(["LONG", "SHORT"] as const)("%s: HALF sends a CLOSE of half on the held side, FULL the whole", side => {
    const spy = vi.spyOn(cryptoApi, "order").mockResolvedValue({ state: held(side, "0.050") });
    const live = vi.spyOn(liveApi, "order");
    render(<MobilePositionCard state={held(side, "0.100")} openedMs={null} nowMs={0}
      onAction={runNow} busy={false} />);
    fireEvent.click(screen.getByTestId("mobile-half-close"));
    expect(spy).toHaveBeenLastCalledWith("BTCUSDT", { side, intent: "CLOSE", qty: "0.050" });
    fireEvent.click(screen.getByTestId("mobile-close-button"));
    expect(spy).toHaveBeenLastCalledWith("BTCUSDT", { side, intent: "CLOSE", qty: "0.100" });
    expect(spy.mock.calls.every(([, body]) => body.intent === "CLOSE")).toBe(true);
    expect(live).not.toHaveBeenCalled();
  });

  it("the wide strip carries the same two buttons, the odd size floored", () => {
    const spy = vi.spyOn(cryptoApi, "order").mockResolvedValue({ state: held("LONG", "0.054") });
    render(<PositionStrip state={held("LONG", "0.107")} openedMs={null} nowMs={0} onAction={runNow} busy={false} />);
    expect(screen.getByTestId("position-strip-half-close")).toHaveTextContent("0.053");
    fireEvent.click(screen.getByTestId("position-strip-half-close"));
    expect(spy).toHaveBeenCalledWith("BTCUSDT", { side: "LONG", intent: "CLOSE", qty: "0.053" });
  });

  it("a half below the minimum turns HALF off and says why; FULL still works; no order", () => {
    const spy = vi.spyOn(cryptoApi, "order").mockResolvedValue({ state: held("LONG", "0") });
    render(<MobilePositionCard state={held("LONG", "0.001")} openedMs={null} nowMs={0}
      onAction={runNow} busy={false} />);
    expect(screen.getByTestId("mobile-half-close")).toBeDisabled();
    expect(screen.getByTestId("mobile-half-close-blocked")).toHaveTextContent("최소 주문 수량");
    fireEvent.click(screen.getByTestId("mobile-half-close"));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByTestId("mobile-close-button")).toBeEnabled();
  });
});

function liveAccount(side: "LONG" | "SHORT", qty: string): LiveAccount {
  return {
    symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1, age_ms: 900, stale: false,
    source: "BINANCE_LIVE", balance: null,
    position: { symbol: "BTCUSDT", side, position_side: "BOTH", qty, signed_qty: side === "LONG" ? qty : `-${qty}`,
      entry_price: "84000", break_even_price: "84000", mark_price: "84000", unrealized_pnl: "0",
      liquidation_price: null, isolated_margin: null, notional: "1000", initial_margin: "50",
      maint_margin: "4", adl: 0, update_time_ms: 1, is_flat: false },
    symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "20", max_notional: null,
                     is_auto_add_margin: false },
    position_mode: { dual_side: false, mode: "ONE_WAY" },
    commission: { symbol: "BTCUSDT", maker: "0.0002", taker: "0.0005", source: "binance" },
    mark: { symbol: "BTCUSDT", mark_price: "84000", index_price: "84000", last_funding_rate: "0", next_funding_time_ms: null },
    book: { best_bid: "83999.9", best_ask: "84000.1", bid_qty: "1", ask_qty: "1", spread: "0.2" },
    filters: { market_qty_step: "0.001", market_min_qty: "0.001", min_notional: "100" },
    krw: null, position_krw: null, krw_per_usdt: "1420", krw_note: "",
    gates: { armed: true, env_flag: true, client_armed: true, env_flag_name: "X" }, stream: null,
  } as unknown as LiveAccount;
}

describe("LIVE position area", () => {
  it.each(["LONG", "SHORT"] as const)("%s: HALF hands the floored half to the LIVE close path", side => {
    const half = vi.fn();
    const full = vi.fn();
    const paperOrder = vi.spyOn(cryptoApi, "order");
    render(<LivePositionPanel account={liveAccount(side, "0.015")} onClose={full} onHalfClose={half} />);
    fireEvent.click(screen.getByTestId("live-position-half-close"));
    expect(half).toHaveBeenCalledWith("0.007");
    fireEvent.click(screen.getByTestId("live-position-full-close"));
    expect(full).toHaveBeenCalledTimes(1);
    expect(paperOrder).not.toHaveBeenCalled();
  });

  it("uses Binance's market step, minimum and MIN_NOTIONAL at the close price", () => {
    // 0.002 held: half is 0.001 BTC = 84 USDT, under the 100 USDT minimum.
    expect(liveHalfClose(liveAccount("LONG", "0.002")).qty).toBeNull();
    render(<LivePositionPanel account={liveAccount("LONG", "0.002")} onClose={vi.fn()} onHalfClose={vi.fn()} />);
    expect(screen.getByTestId("live-position-half-close")).toBeDisabled();
    expect(screen.getByTestId("live-position-half-close-blocked")).toHaveTextContent("최소 100 USDT");
    expect(liveHalfClose(liveAccount("SHORT", "0.107")).qty).toBe("0.053");
  });

  it("the LIVE hook sends HALF as a sized CLOSE (the server plans it reduceOnly)", async () => {
    const posted: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/binance/order")) posted.push(JSON.parse(String(init?.body)));
      return { ok: true, status: 200, json: async () => ({}) };
    }));
    await liveApi.order("BTCUSDT", { side: "LONG", intent: "CLOSE", qty: "0.007" });
    expect(posted).toEqual([{ side: "LONG", intent: "CLOSE", qty: "0.007", symbol: "BTCUSDT" }]);
  });
});
