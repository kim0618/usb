import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  AccountSourceSwitch, LiveAccountCards, LiveBadge, LiveBlockedPanel, LiveBookStrip,
  LiveMarketHeader, LiveOrderTicket, LivePositionPanel,
} from "@/components/crypto-live-terminal";
import { liveApi, liveBlockerLabel } from "@/lib/crypto-live";
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

describe("the LIVE screen", () => {
  it("says it is a real account and that orders need arming", () => {
    render(<LiveBadge account={account()} />);
    expect(screen.getByTestId("live-badge")).toHaveTextContent("BINANCE LIVE");
    expect(screen.getByTestId("live-badge")).toHaveTextContent("실계좌");
    // The badge used to name the environment variable. It now names the condition the operator
    // can act on: orders go out only while a manual window is armed, and that window closes on
    // a timeout, a disarm or a restart.
    expect(screen.getByTestId("live-badge")).toHaveTextContent("무장");
  });

  it("does not look connected while the account cannot be read", () => {
    // Found by rendering the real blocked screen: the badge was reporting snapshot freshness,
    // so a refused account showed a healthy "synced 0s ago".
    const blocked = account({ ready: false, blockers: [{ code: "BINANCE_AUTH_FAILED", message: "거부" }] });
    render(<LiveBadge account={blocked} />);
    expect(screen.getByTestId("live-badge")).toHaveTextContent("연결 안 됨");
    expect(screen.getByTestId("live-badge")).not.toHaveTextContent("동기화");
  });

  it("shows Binance's own balance, leverage and margin mode", () => {
    render(<LiveMarketHeader account={account()} />);
    expect(screen.getByTestId("live-mark-price")).toHaveTextContent("83,500.0");
    expect(screen.getByTestId("live-wallet")).toHaveTextContent("1,000.00 USDT");
    expect(screen.getByTestId("live-available")).toHaveTextContent("875.00 USDT");
    expect(screen.getByTestId("live-margin-mode")).toHaveTextContent("교차 (Cross) · 10x");
  });

  it("shows the USDT balance as the headline and the USD valuation as a footnote", () => {
    // The top-level total is Binance's USD valuation; showing it under a USDT label made an idle
    // wallet look like it was moving.
    render(<LiveMarketHeader account={account()} />);
    expect(screen.getByTestId("live-wallet")).toHaveTextContent("1,000.00 USDT");
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

  it("shows Binance's own book beside the chart, and says the chart is not Binance's", () => {
    // Found by rendering the real LIVE screen: the chart strip prints Bybit's bid/ask, which on a
    // LIVE screen reads as the book the order would hit.
    render(<LiveBookStrip account={account()} />);
    expect(screen.getByTestId("live-best-bid")).toHaveTextContent("83,499.9");
    expect(screen.getByTestId("live-best-ask")).toHaveTextContent("83,500.1");
    expect(screen.getByTestId("live-chart-source-note")).toHaveTextContent("Bybit");
  });

  it("says a flat account is flat instead of drawing an empty position", () => {
    const flat = account({ position: { ...account().position!, is_flat: true, side: null, qty: "0" } });
    render(<LivePositionPanel account={flat} />);
    expect(screen.getByTestId("live-position-panel")).toHaveTextContent("보유 포지션이 없습니다");
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

describe("the LIVE order ticket", () => {
  it("asks for confirmation before it calls anything", () => {
    const onOrder = vi.fn();
    render(<LiveOrderTicket account={account()} onOrder={onOrder} />);
    fireEvent.click(screen.getByTestId("live-long"));
    expect(onOrder).not.toHaveBeenCalled();
    expect(screen.getByTestId("live-confirm")).toHaveTextContent("실계좌");
    fireEvent.click(screen.getByTestId("live-submit"));
    // The exchange minimum. An untouched size box is what a mis-click sends, so the default is
    // the smallest order Binance accepts rather than a multiple of it.
    expect(onOrder).toHaveBeenCalledWith({ side: "LONG", intent: "OPEN", qty: "0.001" });
  });

  it("closes the position Binance reports, not a size typed into the box", () => {
    const onOrder = vi.fn();
    render(<LiveOrderTicket account={account()} onOrder={onOrder} />);
    fireEvent.change(screen.getByTestId("live-qty-input"), { target: { value: "9.999" } });
    fireEvent.click(screen.getByTestId("live-close"));
    fireEvent.click(screen.getByTestId("live-submit"));
    expect(onOrder).toHaveBeenCalledWith({ side: "LONG", intent: "CLOSE", qty: undefined });
  });

  it("cannot close a flat account", () => {
    const flat = account({ position: { ...account().position!, is_flat: true, side: null, qty: "0" } });
    render(<LiveOrderTicket account={flat} onOrder={vi.fn()} />);
    expect(screen.getByTestId("live-close")).toBeDisabled();
  });

  it("shows the refusal the backend returns instead of hiding it", () => {
    render(<LiveOrderTicket account={account()} onOrder={vi.fn()}
      error="실주문 잠금 · BINANCE_LIVE_TRADING_ENABLED=false" />);
    expect(screen.getByTestId("live-order-error")).toHaveTextContent("실주문 잠금");
  });

  it("shows the round-trip cost the backend priced on Binance's book", () => {
    render(<LiveOrderTicket account={account()} onOrder={vi.fn()} preview={{
      source: "BINANCE_LIVE", symbol: "BTCUSDT", krw_per_usdt: null, mark_price: "83500.00",
      fetched_at_ms: 1, sides: { LONG: { side: "LONG", feasible: true,
        entry_fill_price: "83500.10", round_trip_cost: "0.0700",
        breakeven_mark_price: "83533.50" } },
    }} />);
    expect(screen.getByTestId("live-preview")).toHaveTextContent("83,500.1");
    expect(screen.getByTestId("live-preview")).toHaveTextContent("0.0700 USDT");
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
