import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LiveLeveragePanel, LiveOrderTicket, LivePositionPanel } from "@/components/crypto-live-terminal";
import { liveLeverageValidity, liveTradeGate } from "@/lib/crypto-live";
import type { LiveAccount, LiveLeverageOptions, LiveSideSizing, LiveSizing } from "@/lib/crypto-live";

/** LIVE symbol leverage cap V1, on screen.
 *
 *  The row shows every step and enables only those at or below this symbol's own maximum, read
 *  from its own bracket table. A setting above the maximum is named and blocks entries; the
 *  screen never changes it - only a press of a valid step does, through the existing write.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const SIREN = "SIRENUSDT";

const account = (symbol: string, leverage: string, held?: { side: "LONG" | "SHORT"; qty: string }): LiveAccount => ({
  symbol, ready: true, blockers: [], fetched_at_ms: 1, age_ms: 900, stale: false, source: "BINANCE_LIVE",
  balance: { asset: "USDT", wallet_balance: "147.78458089", available_balance: "147.75502801",
    margin_balance: "147.78", unrealized_pnl: "0", max_withdraw: "147.75", initial_margin: "0",
    maint_margin: "0", account_wallet_usd: "147.7", account_available_usd: "147.7",
    account_margin_usd: "147.7", account_unrealized_usd: "0", usd_valuation_ratio: "1",
    update_time_ms: 1, balance_source: "binance" },
  position: held
    ? { symbol, side: held.side, position_side: "BOTH", qty: held.qty,
        signed_qty: held.side === "LONG" ? held.qty : `-${held.qty}`, entry_price: "0.0306",
        break_even_price: "0.0306", mark_price: "0.0306", unrealized_pnl: "0",
        liquidation_price: null, isolated_margin: null, notional: "1438", initial_margin: "143",
        maint_margin: "7", adl: 0, update_time_ms: 1, is_flat: false }
    : { symbol, side: null, position_side: "BOTH", qty: "0", signed_qty: "0", entry_price: null,
        break_even_price: null, mark_price: "0.0306", unrealized_pnl: "0", liquidation_price: null,
        isolated_margin: null, notional: "0", initial_margin: "0", maint_margin: "0", adl: 0,
        update_time_ms: null, is_flat: true },
  symbol_config: { symbol, margin_type: "CROSSED", leverage, max_notional: "10000", is_auto_add_margin: false },
  position_mode: { dual_side: false, mode: "ONE_WAY" },
  commission: { symbol, maker: "0.0002", taker: "0.0005", source: "binance" },
  mark: { symbol, mark_price: "0.0306", index_price: "0.0306", last_funding_rate: "0.0001",
          next_funding_time_ms: null },
  book: { best_bid: "0.03059", best_ask: "0.03061", bid_qty: "100000", ask_qty: "100000", spread: "0.00002" },
  filters: { market_min_qty: "1", min_qty: "1", qty_step: "1", market_qty_step: "1" },
  krw: null, position_krw: null, krw_per_usdt: "1340", krw_note: "",
  gates: { armed: true, env_flag: true, client_armed: true, env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  stream: null,
} as unknown as LiveAccount);

const options = (symbol: string, max: number, current: number): LiveLeverageOptions => {
  const ladder = [1, 2, 3, 5, 10, 20, 50, 100];
  const valid = current <= max;
  return {
    symbol, current: String(current), margin_type: "CROSSED", max_leverage: max,
    options: ladder.filter(value => value <= max), brackets: [], notional_coef: null,
    authority: "binance GET /fapi/v1/leverageBracket", margin_type_note: "", candidates: ladder,
    current_valid: valid,
    current_invalid_message: valid ? null : `현재 심볼(${symbol})은 최대 ${max}x입니다. 레버리지를 다시 선택하세요.`,
  };
};

const side = (over: Partial<LiveSideSizing> = {}): LiveSideSizing => ({
  side: "LONG", leverage: "10", available_balance: "147.75", local_max_qty: null,
  max_qty: "47900", max_feasible: true, reject_code: null, reject_message: null,
  max_definition: "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT",
  instrument: { qty_step: "1", min_qty: "1", smallest_orderable_qty: "164", min_notional: "5",
                market_max_qty: "5000000" },
  presets: ["25%", "HALF", "75%", "MAX"].map((label, i) => ({
    label, fraction: "0.25", qty: String(11975 * (i + 1)), feasible: true, notional: "366",
    required_margin: "36.6", entry_fee: "0.18", required_total: "36.8", available_after: "110",
    entry_fill_price: "0.03061", exit_fill_price: "0.03059", reject_code: null, reject_message: null })),
  ...over,
} as LiveSideSizing);

const sizing = (symbol: string): LiveSizing => ({
  source: "BINANCE_LIVE", available: true, symbol, fetched_at_ms: 1, age_ms: 900,
  sides: { LONG: side(), SHORT: side({ side: "SHORT" }) },
} as LiveSizing);

const enabledSteps = () => [1, 2, 3, 5, 10, 20, 50, 100]
  .filter(value => (screen.getByTestId(`live-leverage-${value}`) as HTMLButtonElement).disabled === false);

describe("the leverage row follows the symbol's own maximum", () => {
  it("1. BTC max 20 enables 1/2/3/5/10/20", () => {
    render(<LiveLeveragePanel account={account("BTCUSDT", "20")} options={options("BTCUSDT", 20, 20)} onSelect={vi.fn()} />);
    expect(enabledSteps()).toEqual([1, 2, 3, 5, 10, 20]);
    expect(screen.getByTestId("live-leverage-max")).toHaveTextContent("BTCUSDT 최대 20x");
  });

  it("2. SIREN max 10 disables 20/50/100 and says so", () => {
    render(<LiveLeveragePanel account={account(SIREN, "10")} options={options(SIREN, 10, 10)} onSelect={vi.fn()} />);
    expect(enabledSteps()).toEqual([1, 2, 3, 5, 10]);
    expect(screen.getByTestId("live-leverage-20")).toHaveAttribute("title", "SIRENUSDT 최대 10x");
    expect(screen.queryByTestId("live-leverage-invalid")).not.toBeInTheDocument();
  });

  it("a symbol with max 50 enables up to 50", () => {
    render(<LiveLeveragePanel account={account("ZEREBROUSDT", "20")} options={options("ZEREBROUSDT", 50, 20)} onSelect={vi.fn()} />);
    expect(enabledSteps()).toEqual([1, 2, 3, 5, 10, 20, 50]);
  });
});

describe("3-4. a setting above the maximum is named, blocks entries, and is never changed by the screen", () => {
  it("SIREN at 20x: warning, 20x shown as the current setting but disabled, nothing written", () => {
    const onSelect = vi.fn();
    render(<LiveLeveragePanel account={account(SIREN, "20")} options={options(SIREN, 10, 20)} onSelect={onSelect} />);
    expect(screen.getByTestId("live-leverage-invalid"))
      .toHaveTextContent("현재 심볼(SIRENUSDT)은 최대 10x입니다. 레버리지를 다시 선택하세요.");
    expect(screen.getByTestId("live-leverage-20")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("live-leverage-20")).toBeDisabled();
    expect(onSelect).not.toHaveBeenCalled();                 // no automatic leverage change
    fireEvent.click(screen.getByTestId("live-leverage-20"));
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("live-leverage-10"));  // the operator's own choice
    expect(onSelect).toHaveBeenCalledWith(10);
  });

  it("SIREN at 20x: LONG and SHORT are disabled with the reason; at 10x they open again", () => {
    const acct = account(SIREN, "20");
    const check = liveLeverageValidity(acct, options(SIREN, 10, 20));
    expect(check).toMatchObject({ max: 10, current: 20, invalid: true });
    const onOrder = vi.fn();
    const { unmount } = render(<LiveOrderTicket account={acct} gate={liveTradeGate(acct, null)} onOrder={onOrder}
      sizing={sizing(SIREN)} leverageInvalid={check.message} />);
    expect(screen.getByTestId("live-long")).toBeDisabled();
    expect(screen.getByTestId("live-short")).toBeDisabled();
    expect(screen.getByTestId("live-order-leverage-invalid")).toHaveTextContent("최대 10x");
    fireEvent.click(screen.getByTestId("live-long"));
    expect(onOrder).not.toHaveBeenCalled();
    unmount();

    const ten = account(SIREN, "10");
    const ok = liveLeverageValidity(ten, options(SIREN, 10, 10));
    expect(ok.invalid).toBe(false);
    render(<LiveOrderTicket account={ten} gate={liveTradeGate(ten, null)} onOrder={onOrder}
      sizing={sizing(SIREN)} leverageInvalid={ok.message} />);
    expect(screen.getByTestId("live-long")).toBeEnabled();
    expect(screen.getByTestId("live-short")).toBeEnabled();
    expect(screen.queryByTestId("live-order-leverage-invalid")).not.toBeInTheDocument();
  });
});

describe("6-7. a symbol switch never carries the previous table", () => {
  it("AGT's 20x table on screen says nothing about SIREN", () => {
    const siren = account(SIREN, "20");
    expect(liveLeverageValidity(siren, options("AGTUSDT", 20, 20))).toMatchObject({ max: null, invalid: false });
    render(<LiveLeveragePanel account={siren} options={options("AGTUSDT", 20, 20)} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("live-leverage-max")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-leverage-20")).not.toBeInTheDocument();
    expect(screen.getByText("구간표를 읽는 중입니다.")).toBeInTheDocument();
  });

  it("SIREN then another HOT: each uses its own maximum", () => {
    const { unmount } = render(<LiveLeveragePanel account={account(SIREN, "10")} options={options(SIREN, 10, 10)} onSelect={vi.fn()} />);
    expect(enabledSteps()).toEqual([1, 2, 3, 5, 10]);
    unmount();
    render(<LiveLeveragePanel account={account("ZEREBROUSDT", "10")} options={options("ZEREBROUSDT", 50, 10)} onSelect={vi.fn()} />);
    expect(enabledSteps()).toEqual([1, 2, 3, 5, 10, 20, 50]);
    expect(screen.getByTestId("live-leverage-max")).toHaveTextContent("ZEREBROUSDT 최대 50x");
  });
});

describe("9-10. closes are not held back by an invalid setting", () => {
  it("CLOSE and the opposite-side reduce stay available; the same-side add does not", () => {
    const held = account(SIREN, "20", { side: "LONG", qty: "47015" });
    const check = liveLeverageValidity(held, options(SIREN, 10, 20));
    render(<LiveOrderTicket account={held} gate={liveTradeGate(held, null)} onOrder={vi.fn()}
      sizing={sizing(SIREN)} leverageInvalid={check.message} />);
    expect(screen.getByTestId("live-close")).toBeEnabled();
    expect(screen.getByTestId("live-short")).toBeEnabled();   // SHORT on a LONG is a reduceOnly reduce
    expect(screen.getByTestId("live-long")).toBeDisabled();   // an add needs a valid leverage
  });

  it("HALF CLOSE and FULL CLOSE on the position panel are enabled", () => {
    const held = account(SIREN, "20", { side: "LONG", qty: "47015" });
    const onHalf = vi.fn();
    const onFull = vi.fn();
    render(<LivePositionPanel account={held} onClose={onFull} onHalfClose={onHalf} />);
    expect(screen.getByTestId("live-position-half-close")).toBeEnabled();
    expect(screen.getByTestId("live-position-full-close")).toBeEnabled();
    fireEvent.click(screen.getByTestId("live-position-half-close"));
    expect(onHalf).toHaveBeenCalledWith("23507");
  });
});

describe("unchanged", () => {
  it("11. BTC at 20x is valid and tradable", () => {
    const btc = account("BTCUSDT", "20");
    const check = liveLeverageValidity(btc, options("BTCUSDT", 20, 20));
    expect(check.invalid).toBe(false);
    render(<LiveOrderTicket account={btc} gate={liveTradeGate(btc, null)} onOrder={vi.fn()}
      sizing={sizing("BTCUSDT")} leverageInvalid={check.message} />);
    expect(screen.getByTestId("live-long")).toBeEnabled();
  });

  it("unknown is not invalid: nothing is blocked before the table arrives", () => {
    expect(liveLeverageValidity(account(SIREN, "20"), null)).toMatchObject({ max: null, invalid: false, message: null });
  });
});
