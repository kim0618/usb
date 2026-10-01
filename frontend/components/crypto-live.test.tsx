import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  AccountSourceSwitch, LiveAccountCards, LiveAutoExit, LiveAuthorityNote, LiveBlockedPanel, LiveBookStrip,
  LiveMarketHeader, LiveOrderTicket, LivePerformanceSummary, LivePositionPanel,
} from "@/components/crypto-live-terminal";
import { liveApi, liveBlockerLabel, liveTradeGate } from "@/lib/crypto-live";
import type { LiveAccount } from "@/lib/crypto-live";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const account = (overrides: Partial<LiveAccount> = {}): LiveAccount => ({
  symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1_790_639_000_000, age_ms: 1_200,
  stale: false, source: "BINANCE_LIVE",
  balance: {
    asset: "USDT", wallet_balance: "1000.00000000", available_balance: "875.00000000",
    margin_balance: "1012.50000000", unrealized_pnl: "12.50000000",
    max_withdraw: "875.00000000", initial_margin: "125.00000000", maint_margin: "5.00000000",
    account_wallet_usd: "999.40000000", account_available_usd: "874.47500000",
    account_margin_usd: "1011.89250000", account_unrealized_usd: "12.49250000",
    usd_valuation_ratio: "0.9994", update_time_ms: 1_790_638_000_000,
    balance_source: "binance GET /fapi/v3/account assets[USDT]",
  },
  position: {
    symbol: "BTCUSDT", side: "LONG", position_side: "BOTH", qty: "0.015", signed_qty: "0.015",
    entry_price: "82666.66666667", break_even_price: "82700.12", mark_price: "83500.00000000",
    unrealized_pnl: "12.50000000", liquidation_price: "75100.10", isolated_margin: "0",
    notional: "1252.50000000", initial_margin: "125.25", maint_margin: "5.01", adl: 2,
    update_time_ms: 1_790_638_000_000, is_flat: false,
  },
  symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "10",
                   max_notional: "10000000", is_auto_add_margin: false },
  position_mode: { dual_side: false, mode: "ONE_WAY" },
  commission: { symbol: "BTCUSDT", maker: "0.000200", taker: "0.000400", source: "binance" },
  mark: { symbol: "BTCUSDT", mark_price: "83500.00000000", index_price: "83510.00000000",
          last_funding_rate: "0.0001", next_funding_time_ms: 1_790_640_000_000 },
  book: { best_bid: "83499.90", best_ask: "83500.10", bid_qty: "3.482", ask_qty: "1.686",
          spread: "0.20" },
  filters: null, krw: null, position_krw: null, krw_per_usdt: null,
  krw_note: "표시용 환산입니다.",
  gates: { armed: false, env_flag: false, client_armed: false,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  stream: null,
  ...overrides,
});

describe("the account switch", () => {
  it("defaults to PAPER and cannot select LIVE while the backend reports no key", () => {
    const onChange = vi.fn();
    render(<AccountSourceSwitch value="PAPER" onChange={onChange} available={false} />);
    expect(screen.getByTestId("source-PAPER")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("source-BINANCE_LIVE")).toBeDisabled();
    expect(screen.getByTestId("live-unavailable-note")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("source-BINANCE_LIVE"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("switches to LIVE when a key is configured", () => {
    const onChange = vi.fn();
    render(<AccountSourceSwitch value="PAPER" onChange={onChange} available />);
    fireEvent.click(screen.getByTestId("source-BINANCE_LIVE"));
    expect(onChange).toHaveBeenCalledWith("BINANCE_LIVE");
  });
});

describe("the trade gate", () => {
  it("is shut on arrival and names arming as the one thing missing", () => {
    const ready = account({ gates: { armed: false, env_flag: true, client_armed: false,
                                     env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" } });
    const gate = liveTradeGate(ready, null);
    expect(gate.tradable).toBe(false);
    expect(gate.reasons.map(reason => reason.code)).toContain("NOT_ARMED");
  });

  it("names the server setting, not the click, when the deployment cannot trade at all", () => {
    // The two failures need different remedies: one is a file on the server, one is a button
    // on this screen. Collapsing them sent operators to edit something they did not need to.
    const gate = liveTradeGate(account(), null);
    expect(gate.reasons.map(reason => reason.code)).toEqual(["LIVE_TRADING_DISABLED"]);
  });

  it("opens only when the account is readable, current and armed", () => {
    const gate = liveTradeGate(account({
      gates: { armed: true, env_flag: true, client_armed: true,
               env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" } }), null);
    expect(gate.tradable).toBe(true);
    expect(gate.reasons).toHaveLength(0);
  });

  it("does not report a healthy connection while the account cannot be read", () => {
    // Found by rendering the real blocked screen: the badge was reporting snapshot freshness,
    // so a refused account showed a healthy "synced 0s ago".
    const blocked = account({ ready: false,
      blockers: [{ code: "BINANCE_AUTH_FAILED", message: "거부" }] });
    const gate = liveTradeGate(blocked, null);
    expect(gate.connected).toBe(false);
    expect(gate.reasons.map(reason => reason.code)).toContain("BINANCE_AUTH_FAILED");
  });
});

describe("the LIVE screen", () => {
  it("shows Binance's own balance, leverage and margin mode", () => {
    render(<LiveMarketHeader account={account()} />);
    expect(screen.getByTestId("live-mark-price")).toHaveTextContent("83,500.0");
    expect(screen.getByTestId("live-wallet")).toHaveTextContent("1,000.00 USDT");
    expect(screen.getByTestId("live-available")).toHaveTextContent("875.00 USDT");
    expect(screen.getByTestId("live-margin-mode")).toHaveTextContent("교차 (Cross) · 10x");
  });

  it("shows the USDT balance as the headline and the USD valuation as a footnote", () => {
    // The top-level total is Binance's USD valuation; showing it under a USDT label made an idle
    // wallet look like it was moving. The reconciliation itself is prose, so it moved to the
    // disclosure at the bottom rather than sitting under the price all day.
    render(<LiveMarketHeader account={account()} />);
    expect(screen.getByTestId("live-wallet")).toHaveTextContent("1,000.00 USDT");
    render(<LiveAuthorityNote account={account()} />);
    const note = screen.getByTestId("live-usd-valuation");
    expect(note).toHaveTextContent("999.400000 USD");
    expect(note).toHaveTextContent("1,000.000000 USDT");
  });

  it("shows the account's own commission rate rather than a paper constant", () => {
    render(<LiveAccountCards account={account()} />);
    expect(screen.getByTestId("live-taker")).toHaveTextContent("0.0400%");
  });

  it("shows the position with Binance's liquidation price", () => {
    render(<LivePositionPanel account={account()} />);
    expect(screen.getByTestId("live-position-side")).toHaveTextContent("LONG");
    expect(screen.getByTestId("live-position-panel")).toHaveTextContent("75,100.1");
    expect(screen.getByTestId("live-position-pnl")).toHaveTextContent("+12.5000 USDT");
  });

  it("shows only Binance quote numbers beside the chart", () => {
    render(<LiveBookStrip account={account()} />);
    expect(screen.getByTestId("live-best-bid")).toHaveTextContent("83,499.9");
    expect(screen.getByTestId("live-best-ask")).toHaveTextContent("83,500.1");
    expect(screen.queryByTestId("live-chart-source-note")).not.toBeInTheDocument();
  });

  it("renders no position card for a flat account", () => {
    const flat = account({ position: { ...account().position!, is_flat: true, side: null, qty: "0" } });
    render(<LivePositionPanel account={flat} />);
    expect(screen.queryByTestId("live-position-panel")).not.toBeInTheDocument();
  });

  it("names every blocker in the operator's language", () => {
    render(<LiveBlockedPanel blockers={[
      { code: "HEDGE_MODE_UNSUPPORTED", message: "계정이 Hedge Mode입니다." },
      { code: "SOMETHING_NEW", message: "unmapped" }]} />);
    expect(screen.getByTestId("live-blocked")).toHaveTextContent("Hedge Mode 계정");
    // An unmapped code falls through to itself rather than to a vague sentence.
    expect(screen.getByTestId("live-blocked")).toHaveTextContent("SOMETHING_NEW");
    expect(liveBlockerLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});

/** The tradable account: the same fixture with the server reporting both gates open. */
const live = (overrides: Partial<LiveAccount> = {}): LiveAccount => account({
  gates: { armed: true, env_flag: true, client_armed: true,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  ...overrides,
});
const ticket = (acct: LiveAccount, props: Record<string, unknown> = {}) =>
  render(<LiveOrderTicket account={acct} gate={liveTradeGate(acct, null)}
    onOrder={vi.fn()} {...props} />);

describe("the LIVE order ticket", () => {
  it("asks for confirmation before it calls anything", () => {
    const onOrder = vi.fn();
    ticket(live(), { onOrder });
    fireEvent.click(screen.getByTestId("live-long"));
    expect(onOrder).not.toHaveBeenCalled();
    expect(screen.getByTestId("live-confirm")).toHaveTextContent("실계좌");
    fireEvent.click(screen.getByTestId("live-submit"));
    // The exchange minimum. An untouched size box is what a mis-click sends, so the default is
    // the smallest order Binance accepts rather than a multiple of it.
    expect(onOrder).toHaveBeenCalledWith({ side: "LONG", intent: "OPEN", qty: "0.001" });
  });

  it("will not open a position while the screen says 거래불가", () => {
    // The server refuses it too. The button is disabled so the refusal is not the way the
    // operator finds out.
    const onOrder = vi.fn();
    ticket(account(), { onOrder });
    expect(screen.getByTestId("live-long")).toBeDisabled();
    expect(screen.getByTestId("live-short")).toBeDisabled();
    fireEvent.click(screen.getByTestId("live-long"));
    expect(onOrder).not.toHaveBeenCalled();
    expect(screen.getByTestId("live-order-locked")).toBeInTheDocument();
  });

  it("closes the position Binance reports, not a size typed into the box", () => {
    const onOrder = vi.fn();
    ticket(live(), { onOrder });
    fireEvent.change(screen.getByTestId("live-qty-input"), { target: { value: "9.999" } });
    fireEvent.click(screen.getByTestId("live-close"));
    fireEvent.click(screen.getByTestId("live-submit"));
    expect(onOrder).toHaveBeenCalledWith({ side: "LONG", intent: "CLOSE", qty: undefined });
  });

  it("never greys out CLOSE on a real position - it offers the activation instead", () => {
    // Reducing risk is the one action that must not dead-end. With the window lapsed the press
    // opens the dialog rather than sending an order the server would refuse.
    const onOrder = vi.fn(); const onActivate = vi.fn();
    ticket(account(), { onOrder, onActivate });
    expect(screen.getByTestId("live-close")).toBeEnabled();
    fireEvent.click(screen.getByTestId("live-close"));
    expect(onOrder).not.toHaveBeenCalled();
    expect(screen.queryByTestId("live-confirm")).not.toBeInTheDocument();
    expect(onActivate).toHaveBeenCalled();
  });

  it("cannot close a flat account", () => {
    ticket(live({ position: { ...account().position!, is_flat: true, side: null, qty: "0" } }));
    expect(screen.getByTestId("live-close")).toBeDisabled();
  });

  it("does not repeat the lock state the bar already shows", () => {
    ticket(live());
    expect(screen.getByTestId("live-order-ticket")).not.toHaveTextContent("실주문 잠금");
    expect(screen.getByTestId("live-order-ticket")).not.toHaveTextContent("무장");
  });

  it("shows the refusal the backend returns instead of hiding it", () => {
    ticket(live(), { error: "실주문 잠금 · BINANCE_LIVE_TRADING_ENABLED=false" });
    expect(screen.getByTestId("live-order-error")).toHaveTextContent("실주문 잠금");
  });

  it("shows the expected entry cost priced by the backend", () => {
    render(<LiveOrderTicket account={live()} gate={liveTradeGate(live(), null)} onOrder={vi.fn()} preview={{
      source: "BINANCE_LIVE", symbol: "BTCUSDT", krw_per_usdt: null, mark_price: "83500.00",
      fetched_at_ms: 1, sides: { LONG: { side: "LONG", feasible: true,
        entry_fill_price: "83500.10", expected_entry_total_cost: "0.0700",
        breakeven_mark_price: "83533.50" } },
    }} />);
    expect(screen.getByTestId("live-preview")).toHaveTextContent("83,500.1");
    expect(screen.getByTestId("live-preview")).toHaveTextContent("0.0700 USDT");
  });
});

describe("the LIVE auto exit panel", () => {
  it("accepts positive KRW thresholds and displays the armed state", () => {
    const save = vi.fn();
    const { rerender } = render(<LiveAutoExit guard={null} onSave={save}
      onDisable={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("익절"), { target: { value: "100000" } });
    fireEvent.change(screen.getByLabelText("손절"), { target: { value: "50000" } });
    fireEvent.click(screen.getByRole("button", { name: "자동청산 켜기" }));
    expect(save).toHaveBeenCalledWith("100000", "50000");

    rerender(<LiveAutoExit guard={{
      state: "ARMED", enabled: true, symbol: "BTCUSDT", side: "LONG",
      position_qty: "0.01", opened_at_ms: 1, take_profit_krw: "100000",
      stop_loss_krw: "50000", current_net_usdt: "24", current_net_krw: "32480",
      created_at_ms: 1, updated_at_ms: 2, last_error: null,
    }} onSave={save} onDisable={vi.fn()} />);
    expect(screen.getByTestId("live-auto-exit")).toHaveTextContent("ON");
    expect(screen.getByTestId("exit-current-net")).toHaveTextContent("+32,480원");
    expect(screen.getByTestId("live-auto-exit")).toHaveTextContent("+100,000원");
    expect(screen.getByTestId("live-auto-exit")).toHaveTextContent("-50,000원");
  });
});


describe("the LIVE client", () => {
  it("never sends a credential and asks the binance namespace", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ available: false, ready: false, blockers: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    await liveApi.status();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/crypto/binance/status");
    expect(JSON.stringify(init)).not.toContain("BINANCE_API");
  });

  it("surfaces the backend's refusal code on an order", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ error: { code: "LIVE_TRADING_DISABLED", message: "실주문이 잠겨 있습니다." } }) }));
    await expect(liveApi.order({ side: "LONG", intent: "OPEN", qty: "0.002" }))
      .rejects.toMatchObject({ code: "LIVE_TRADING_DISABLED" });
  });
});

describe("the LIVE performance summary", () => {
  const performance = (cumulative: string | null, today: string | null) => ({
    available: true, has_trades: true, first_trade_kst_date: "2026-09-29", running_day: 3,
    cumulative_net_usdt: "1", today_net_usdt: "1", cumulative_net_krw: cumulative,
    today_net_krw: today, krw_per_usdt: "1400", classification_complete: true,
    last_error: null, last_calculated_ms: 2,
  });

  it("formats signed KRW and keeps long mobile values unbroken", () => {
    render(<LivePerformanceSummary performance={performance("18420", "-1234567890")} />);
    expect(screen.getByTestId("live-performance-summary")).toHaveTextContent("2026.09.29 · 3일째");
    expect(screen.getByTestId("live-performance-cumulative")).toHaveTextContent("+18,420원");
    expect(screen.getByTestId("live-performance-today")).toHaveTextContent("-1,234,567,890원");
    expect(screen.getByTestId("live-performance-today")).toHaveClass("whitespace-nowrap");
  });

  it("renders neutral zero and hides before the first trade", () => {
    const view = render(<LivePerformanceSummary performance={performance("0", "0")} />);
    expect(screen.getByTestId("live-performance-cumulative")).toHaveTextContent("0원");
    view.rerender(<LivePerformanceSummary performance={{ available: true, has_trades: false,
      classification_complete: true, last_error: null, last_calculated_ms: 2 }} />);
    expect(screen.queryByTestId("live-performance-summary")).not.toBeInTheDocument();
  });
});
