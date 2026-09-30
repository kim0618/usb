import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LiveArmPanel, LiveLeveragePanel } from "@/components/crypto-live-terminal";
import { ARM_CONFIRMATION } from "@/lib/crypto-live";
import type { LiveAccount, LiveArmState, LiveLeverageOptions } from "@/lib/crypto-live";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const arm = (overrides: Partial<LiveArmState> = {}): LiveArmState => ({
  armed: false, armed_by: null, armed_at_ms: null, expires_at_ms: null, remaining_s: null,
  ttl_s: 900, arm_count: 0, disarm_count: 0, last_disarm_reason: "BOOT", last_disarm_ms: null,
  operator_note: null, confirmation_phrase: ARM_CONFIRMATION, env_armed: false,
  role: "MANUAL_ONLY", available: true, capability: true,
  ...overrides,
});

const options = (overrides: Partial<LiveLeverageOptions> = {}): LiveLeverageOptions => ({
  symbol: "BTCUSDT", current: "10", margin_type: "CROSSED", max_leverage: 150,
  options: [1, 2, 3, 5, 10, 20, 50], brackets: [], notional_coef: null,
  authority: "binance GET /fapi/v1/leverageBracket",
  margin_type_note: "마진 모드는 Binance에서 설정한 값을 그대로 표시합니다.",
  ...overrides,
});

const account = (overrides: Partial<LiveAccount> = {}): LiveAccount => ({
  symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1_790_639_000_000, age_ms: 900,
  stale: false, source: "BINANCE_LIVE",
  balance: {
    asset: "USDT", wallet_balance: "374.48970275", available_balance: "374.41481086",
    margin_balance: "374.48970275", unrealized_pnl: "0", max_withdraw: "374.41481086",
    initial_margin: "0", maint_margin: "0", account_wallet_usd: "374.29",
    account_available_usd: "374.21", account_margin_usd: "374.29", account_unrealized_usd: "0",
    usd_valuation_ratio: "0.9995", update_time_ms: 1_790_639_000_000, balance_source: "binance",
  },
  position: {
    symbol: "BTCUSDT", side: null, position_side: "BOTH", qty: "0", signed_qty: "0",
    entry_price: null, break_even_price: null, mark_price: "83394.90", unrealized_pnl: "0",
    liquidation_price: null, isolated_margin: null, notional: "0", initial_margin: "0",
    maint_margin: "0", adl: 0, update_time_ms: null, is_flat: true,
  },
  symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "20",
                   max_notional: "100000000", is_auto_add_margin: false },
  position_mode: { dual_side: false, mode: "ONE_WAY" },
  commission: { symbol: "BTCUSDT", maker: "0.000200", taker: "0.000500", source: "binance" },
  mark: { symbol: "BTCUSDT", mark_price: "83394.90", index_price: "83400.00",
          last_funding_rate: "0.00007207", next_funding_time_ms: null },
  book: { best_bid: "83394.80", best_ask: "83395.00", bid_qty: "2.1", ask_qty: "1.4",
          spread: "0.20" },
  filters: null, krw: null, position_krw: null, krw_per_usdt: null, krw_note: "",
  gates: { armed: false, env_flag: true, client_armed: false,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  stream: null,
  ...overrides,
});


describe("the arm panel", () => {
  it("starts disarmed and does not arm on a single click", () => {
    const onArm = vi.fn();
    render(<LiveArmPanel arm={arm()} onArm={onArm} onDisarm={vi.fn()} />);
    expect(screen.getByTestId("live-arm-panel")).toHaveTextContent("해제됨");
    fireEvent.click(screen.getByTestId("live-arm"));
    expect(onArm).not.toHaveBeenCalled();
  });

  it("will not submit until the confirmation phrase is typed exactly", () => {
    const onArm = vi.fn();
    render(<LiveArmPanel arm={arm()} onArm={onArm} onDisarm={vi.fn()} />);
    fireEvent.click(screen.getByTestId("live-arm"));

    const input = screen.getByTestId("live-arm-input");
    expect(screen.getByTestId("live-arm-submit")).toBeDisabled();

    fireEvent.change(input, { target: { value: "arm live trading" } });
    expect(screen.getByTestId("live-arm-submit")).toBeDisabled();

    fireEvent.change(input, { target: { value: ARM_CONFIRMATION } });
    expect(screen.getByTestId("live-arm-submit")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("live-arm-submit"));
    expect(onArm).toHaveBeenCalledWith(ARM_CONFIRMATION);
  });

  it("cannot be armed at all when the deployment has no capability", () => {
    render(<LiveArmPanel arm={arm({ capability: false })} onArm={vi.fn()} onDisarm={vi.fn()} />);
    expect(screen.getByTestId("live-arm")).toBeDisabled();
    expect(screen.getByTestId("live-arm-no-capability")).toBeInTheDocument();
  });

  it("shows the remaining window while armed and offers a disarm", () => {
    const onDisarm = vi.fn();
    render(<LiveArmPanel onArm={vi.fn()} onDisarm={onDisarm}
      arm={arm({ armed: true, armed_by: "SESSION", remaining_s: 185 })} />);
    expect(screen.getByTestId("live-arm-panel")).toHaveTextContent("무장됨");
    expect(screen.getByTestId("live-arm-remaining")).toHaveTextContent("3분 5초 남음");
    fireEvent.click(screen.getByTestId("live-disarm"));
    expect(onDisarm).toHaveBeenCalled();
  });

  it("says when the process was armed by its environment rather than by a click", () => {
    render(<LiveArmPanel onArm={vi.fn()} onDisarm={vi.fn()}
      arm={arm({ armed: true, armed_by: "ENV", env_armed: true })} />);
    expect(screen.getByTestId("live-arm-by-env")).toBeInTheDocument();
    // Nothing on this screen can take away an environment arm; the button would lie.
    expect(screen.getByTestId("live-disarm")).toBeDisabled();
  });

  it("always states the three ways the window closes", () => {
    render(<LiveArmPanel arm={arm()} onArm={vi.fn()} onDisarm={vi.fn()} />);
    const note = screen.getByTestId("live-arm-note");
    expect(note).toHaveTextContent("제한 시간");
    expect(note).toHaveTextContent("재시작");
    expect(note).toHaveTextContent("AUTO");
  });
});


describe("the leverage panel", () => {
  it("offers only the values the account's bracket table allows", () => {
    render(<LiveLeveragePanel account={account()} options={options({ max_leverage: 10,
      options: [1, 2, 3, 5, 10] })} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-leverage-10")).toBeInTheDocument();
    expect(screen.queryByTestId("live-leverage-20")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-leverage-50")).not.toBeInTheDocument();
  });

  it("marks the value Binance currently reports, not the one last clicked", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    // The account fixture says 20x; the options payload says current 10x. The account is the
    // one Binance answered most recently, and it wins.
    expect(screen.getByTestId("live-leverage-20")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("live-leverage-10")).toHaveAttribute("aria-pressed", "false");
  });

  it("sends the chosen value and does not repaint until the caller refreshes", () => {
    const onSelect = vi.fn();
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("live-leverage-5"));
    expect(onSelect).toHaveBeenCalledWith(5);
    // Still 20x on screen: the panel shows what Binance said, never what was requested.
    expect(screen.getByTestId("live-leverage-current")).toHaveTextContent("현재 20x");
  });

  it("refuses to change leverage while a position is open", () => {
    const open = account({
      position: { ...account().position!, is_flat: false, side: "LONG", qty: "0.001",
                  notional: "83.39", initial_margin: "4.17", liquidation_price: "45120.10" },
    });
    render(<LiveLeveragePanel account={open} options={options()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-leverage-5")).toBeDisabled();
    expect(screen.getByTestId("live-leverage-locked")).toBeInTheDocument();
  });

  it("shows Binance's own margin and liquidation figures for an open position", () => {
    const open = account({
      position: { ...account().position!, is_flat: false, side: "LONG", qty: "0.001",
                  notional: "83.39", initial_margin: "4.17", liquidation_price: "45120.10" },
    });
    render(<LiveLeveragePanel account={open} options={options()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-risk-notional")).toHaveTextContent("83.39");
    expect(screen.getByTestId("live-risk-margin")).toHaveTextContent("4.17");
    expect(screen.getByTestId("live-risk-liq")).toHaveTextContent("45,120.1");
  });

  it("does not invent a liquidation price while the account is flat", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-risk-liq")).toHaveTextContent("-");
    expect(screen.getByTestId("live-leverage-panel"))
      .toHaveTextContent("포지션 생성 후 Binance가 산출");
  });

  it("says leverage is a margin setting and not an exposure multiplier", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    const note = screen.getByTestId("live-leverage-sizing-note");
    expect(note).toHaveTextContent("증거금 설정");
    expect(note).toHaveTextContent("0.001 BTC");
  });

  it("states that the margin mode is read only here", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-margin-readonly-note")).toHaveTextContent("Binance");
  });

  it("shows the available balance so the margin figure has something to sit against", () => {
    render(<LiveLeveragePanel account={account()} options={options()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-risk-available")).toHaveTextContent("374.41");
  });
});
