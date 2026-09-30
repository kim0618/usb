import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { LiveOrderTicket, LiveQuickSize, useBinanceLive } from "@/components/crypto-live-terminal";
import { livePresetQty, liveTradeGate } from "@/lib/crypto-live";
import type { LiveAccount, LiveSideSizing, LiveSizing } from "@/lib/crypto-live";

/** Quick sizes on the LIVE ticket.
 *
 *  The property under test throughout is that this side of the wire never sizes anything. Every
 *  quantity asserted below is one the fixture's server put there, and the tests that matter most
 *  are the ones checking what a press does *not* do: no order, no arm, no leverage change.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const preset = (label: string, qty: string, over: Partial<LiveSideSizing["presets"][0]> = {}) => ({
  label, fraction: "0.25", qty, feasible: true, notional: "166.64",
  required_margin: "16.66", entry_fee: "0.083", required_total: "16.74",
  available_after: "357.44", entry_fill_price: "83320.00", exit_fill_price: "83318.00",
  reject_code: null, reject_message: null, ...over,
});

const side = (over: Partial<LiveSideSizing> = {}): LiveSideSizing => ({
  side: "LONG", leverage: "10", available_balance: "374.18", local_max_qty: "0.01",
  max_qty: "0.010", max_feasible: true, reject_code: null, reject_message: null,
  max_definition: "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT_WITHIN_LOCAL_CEILING",
  instrument: { qty_step: "0.001", min_qty: "0.001", smallest_orderable_qty: "0.001",
                min_notional: "50", market_max_qty: "120" },
  presets: [preset("25%", "0.002"), preset("HALF", "0.005"), preset("75%", "0.007"),
            preset("MAX", "0.010")],
  ...over,
});

const sizing = (over: Partial<LiveSizing> = {}): LiveSizing => ({
  source: "BINANCE_LIVE", available: true, symbol: "BTCUSDT", fetched_at_ms: 1, age_ms: 900,
  sides: { LONG: side(), SHORT: side({ side: "SHORT" }) },
  ...over,
});

const account = (over: Partial<LiveAccount> = {}): LiveAccount => ({
  symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1, age_ms: 900, stale: false,
  source: "BINANCE_LIVE",
  balance: { asset: "USDT", wallet_balance: "374.25", available_balance: "374.18",
    margin_balance: "374.25", unrealized_pnl: "0", max_withdraw: "374.25", initial_margin: "0",
    maint_margin: "0", account_wallet_usd: "374.1", account_available_usd: "374.0",
    account_margin_usd: "374.1", account_unrealized_usd: "0", usd_valuation_ratio: "0.9998",
    update_time_ms: 1, balance_source: "binance" },
  position: { symbol: "BTCUSDT", side: null, position_side: "BOTH", qty: "0", signed_qty: "0",
    entry_price: null, break_even_price: null, mark_price: "83320.00", unrealized_pnl: "0",
    liquidation_price: null, isolated_margin: null, notional: "0", initial_margin: "0",
    maint_margin: "0", adl: 0, update_time_ms: null, is_flat: true },
  symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "10",
                   max_notional: "230000000", is_auto_add_margin: false },
  position_mode: { dual_side: false, mode: "ONE_WAY" },
  commission: { symbol: "BTCUSDT", maker: "0.0002", taker: "0.0005", source: "binance" },
  mark: { symbol: "BTCUSDT", mark_price: "83320.00", index_price: "83321.00",
          last_funding_rate: "0.0001", next_funding_time_ms: null },
  book: { best_bid: "83318.20", best_ask: "83318.30", bid_qty: "13.9", ask_qty: "1.4",
          spread: "0.10" },
  filters: null, krw: null, position_krw: null, krw_per_usdt: null, krw_note: "",
  gates: { armed: true, env_flag: true, client_armed: true,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  stream: null, ...over,
});


describe("picking a quick size", () => {
  it("offers the four presets the server sent", () => {
    render(<LiveQuickSize sizing={sizing()} onPick={vi.fn()} />);
    for (const label of ["25%", "HALF", "75%", "MAX"]) {
      expect(screen.getByTestId(`live-preset-${label}`)).toBeEnabled();
    }
  });

  it("hands over the server's quantity, not one it worked out", () => {
    const onPick = vi.fn();
    render(<LiveQuickSize sizing={sizing()} onPick={onPick} />);
    fireEvent.click(screen.getByTestId("live-preset-25%"));
    expect(onPick).toHaveBeenCalledWith("0.002");
    fireEvent.click(screen.getByTestId("live-preset-MAX"));
    expect(onPick).toHaveBeenCalledWith("0.010");
  });

  it("takes the smaller of the two sides, because one box feeds both buttons", () => {
    // SHORT's book is thinner here. Handing over LONG's size would fill a field that the SHORT
    // button cannot honour.
    const lopsided = sizing({ sides: {
      LONG: side(), SHORT: side({ side: "SHORT",
        presets: [preset("25%", "0.001"), preset("HALF", "0.003"), preset("75%", "0.004"),
                  preset("MAX", "0.006")] }) } });
    const onPick = vi.fn();
    render(<LiveQuickSize sizing={lopsided} onPick={onPick} />);
    fireEvent.click(screen.getByTestId("live-preset-MAX"));
    expect(onPick).toHaveBeenCalledWith("0.006");
    expect(livePresetQty(lopsided, "HALF").qty).toBe("0.003");
  });

  it("disables a preset either side refuses, and says why", () => {
    const refused = sizing({ sides: {
      LONG: side({ presets: [preset("25%", "0.001", { feasible: false,
        reject_code: "NOTIONAL_BELOW_MINIMUM", reject_message: "명목 83.3이 최소 100 미만입니다." }),
        preset("HALF", "0.005"), preset("75%", "0.007"), preset("MAX", "0.010")] }),
      SHORT: side({ side: "SHORT" }) } });
    const onPick = vi.fn();
    render(<LiveQuickSize sizing={refused} onPick={onPick} />);
    expect(screen.getByTestId("live-preset-25%")).toBeDisabled();
    expect(screen.getByTestId("live-preset-25%"))
      .toHaveAttribute("title", "명목 83.3이 최소 100 미만입니다.");
    fireEvent.click(screen.getByTestId("live-preset-25%"));
    expect(onPick).not.toHaveBeenCalled();
    expect(screen.getByTestId("live-preset-HALF")).toBeEnabled();
  });

  it("disables everything while the ladder has not arrived", () => {
    render(<LiveQuickSize sizing={null} onPick={vi.fn()} />);
    expect(screen.getByTestId("live-preset-MAX")).toBeDisabled();
    expect(screen.getByTestId("live-quick-size-unavailable")).toBeInTheDocument();
  });

  it("disables everything when the server could not compute a size, and shows its reason", () => {
    render(<LiveQuickSize onPick={vi.fn()} sizing={sizing({ available: false, sides: undefined,
      reject_code: "BINANCE_AUTH_FAILED", reject_message: "인증이 거부됐습니다." })} />);
    expect(screen.getByTestId("live-quick-size-unavailable")).toHaveTextContent("인증이 거부됐습니다.");
    expect(screen.getByTestId("live-preset-MAX")).toBeDisabled();
  });

  it("refuses to size off a stale account", () => {
    render(<LiveQuickSize sizing={sizing()} onPick={vi.fn()} stale />);
    expect(screen.getByTestId("live-quick-size-unavailable")).toHaveTextContent("지연");
    expect(screen.getByTestId("live-preset-MAX")).toBeDisabled();
  });
});


describe("the quick size row on the ticket", () => {
  const ticket = (props: Record<string, unknown> = {}) => {
    const acct = (props.account as LiveAccount) ?? account();
    return render(<LiveOrderTicket account={acct} gate={liveTradeGate(acct, null)}
      onOrder={vi.fn()} sizing={sizing()} {...props} />);
  };

  it("writes the chosen size into the quantity box and nothing else", () => {
    const onOrder = vi.fn();
    ticket({ onOrder });
    fireEvent.click(screen.getByTestId("live-preset-HALF"));
    expect(screen.getByTestId("live-qty-input")).toHaveValue("0.005");
    // A size is not an order: no confirmation opened, nothing sent.
    expect(screen.queryByTestId("live-confirm")).not.toBeInTheDocument();
    expect(onOrder).not.toHaveBeenCalled();
  });

  it("stays usable while the screen is 거래불가, because choosing a size is not trading", () => {
    const locked = account({ gates: { armed: false, env_flag: true, client_armed: false,
                                      env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" } });
    ticket({ account: locked });
    expect(screen.getByTestId("live-preset-MAX")).toBeEnabled();
    fireEvent.click(screen.getByTestId("live-preset-MAX"));
    expect(screen.getByTestId("live-qty-input")).toHaveValue("0.010");
    // The order buttons still answer to the gate.
    expect(screen.getByTestId("live-long")).toBeDisabled();
    expect(screen.getByTestId("live-short")).toBeDisabled();
  });

  it("sends the picked size when the order is then confirmed", () => {
    const onOrder = vi.fn();
    ticket({ onOrder });
    fireEvent.click(screen.getByTestId("live-preset-75%"));
    fireEvent.click(screen.getByTestId("live-long"));
    fireEvent.click(screen.getByTestId("live-submit"));
    expect(onOrder).toHaveBeenCalledWith({ side: "LONG", intent: "OPEN", qty: "0.007" });
  });

  it("leaves CLOSE alone - it still closes the reported position, not a preset", () => {
    const held = account({ position: { ...account().position!, is_flat: false, side: "LONG",
                                       qty: "0.003" } });
    const onOrder = vi.fn();
    ticket({ account: held, onOrder });
    fireEvent.click(screen.getByTestId("live-preset-MAX"));
    fireEvent.click(screen.getByTestId("live-close"));
    fireEvent.click(screen.getByTestId("live-submit"));
    expect(onOrder).toHaveBeenCalledWith({ side: "LONG", intent: "CLOSE", qty: undefined });
  });
});


describe("the sizing client", () => {
  it("reads the ladder and touches no write endpoint", async () => {
    const paths: string[] = [];
    const methods: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      paths.push(String(url));
      methods.push(init?.method || "GET");
      return { ok: true, json: async () => (String(url).includes("/sizing") ? sizing()
        : String(url).includes("/status") ? { available: true } : {}) };
    }));
    const { result } = renderHook(() => useBinanceLive(true));
    await waitFor(() => expect(result.current.sizing).not.toBeNull());
    expect(paths.some(path => path.includes("/api/crypto/binance/sizing"))).toBe(true);
    // Reading sizes must never reach the order, arm or leverage write paths.
    expect(paths.some(path => path.includes("/binance/order"))).toBe(false);
    expect(paths.some(path => path.includes("/binance/disarm"))).toBe(false);
    const writes = paths.filter((_, index) => methods[index] === "POST");
    expect(writes).toHaveLength(0);
  });

  it("re-prices the ladder after a leverage change, because margin per coin moved", async () => {
    let sizingReads = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url).includes("/sizing")) sizingReads += 1;
      return { ok: true, json: async () => (String(url).includes("/sizing") ? sizing()
        : String(url).includes("/status") ? { available: true } : {}) };
    }));
    const { result } = renderHook(() => useBinanceLive(true));
    await waitFor(() => expect(sizingReads).toBeGreaterThan(0));
    const before = sizingReads;
    await act(async () => { await result.current.changeLeverage(10); });
    expect(sizingReads).toBeGreaterThan(before);
  });
});
