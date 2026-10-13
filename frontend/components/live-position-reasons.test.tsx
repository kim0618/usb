import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LivePositionCard, LivePositionPanel } from "./crypto-live-terminal";
import type { LiveAccount, LivePositionCard as CardData } from "../lib/crypto-live";

afterEach(cleanup);

const account = {
  symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1, age_ms: 900, stale: false, source: "BINANCE_LIVE",
  position: { symbol: "BTCUSDT", side: "LONG", position_side: "BOTH", qty: "0.108", signed_qty: "0.108",
    entry_price: "83913.1", break_even_price: "83955.0", mark_price: "83437.2", unrealized_pnl: "-51.39",
    liquidation_price: "79899.0", isolated_margin: "0", notional: "9011.2", initial_margin: "450.56",
    maint_margin: "36.04", adl: 1, update_time_ms: 1, is_flat: false },
  symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "20", max_notional: "1", is_auto_add_margin: false },
  mark: { symbol: "BTCUSDT", mark_price: "83437.2", index_price: "83437", last_funding_rate: "0.0001", next_funding_time_ms: null },
  position_krw: { unrealized_pnl: "-68924", notional: "12084043" }, krw_per_usdt: "1341",
} as unknown as LiveAccount;

const card = (over: Partial<CardData> = {}): CardData => ({
  open: true, available: true, symbol: "BTCUSDT", fetched_at_ms: 1, age_ms: 900, side: "LONG", qty: "0.108",
  leverage: "20", entry_price: "83913.1", break_even_price: "83955.0", mark_price: "83437.2",
  liquidation_price: "79899.0", unrealized_pnl: "-51.39", notional: "9011.2", exposure: "9011.2",
  initial_margin: "450.56", maint_margin: "36.04", opened_at_ms: 1_791_339_778_021,
  opened_source: "BINANCE_USER_TRADES_WALKBACK", commission_paid: "4.53", realized_since_open: "0",
  funding_income: "-1.86",
  close: { feasible: true, qty: "0.108", basis: "x", exit_fill_price: "83328.8", exit_fee: "4.50",
           reject_code: null, reject_message: null },
  net_if_closed: "-73.99", net_complete: true,
  krw: { unrealized_pnl: "-68924", net_if_closed: "-99215" }, krw_per_usdt: "1341", ...over,
} as CardData);

const NOW = 1_791_339_778_021 + 145 * 3_600_000 + 13 * 60_000;

describe("LIVE position card wording says which thing is missing", () => {
  it("a healthy card shows holding time, net, and no fallback text", () => {
    render(<LivePositionCard card={card()} nowMs={NOW} onClose={() => {}}/>);
    expect(screen.getByTestId("live-card-held")).toHaveTextContent("145시간 13분 보유");
    expect(screen.getByTestId("live-card-net")).toHaveTextContent("-73.9900 USDT");
    expect(screen.queryByTestId("live-card-net-unavailable")).not.toBeInTheDocument();
  });

  it("the wide panel without a card says the detail is still loading, not that the book is missing", () => {
    render(<LivePositionPanel account={account} card={null}/>);
    const note = screen.getByTestId("live-position-net-unavailable");
    expect(note).toHaveTextContent("포지션 상세 조회 중");
    expect(note).not.toHaveTextContent("호가");
    expect(screen.getByTestId("live-position-entry")).toHaveTextContent("83,913.1");   // snapshot figures still show
  });

  it("a card that arrived but could not price the close says the book is unavailable", () => {
    const noBook = card({ net_if_closed: null, net_complete: false,
      close: { feasible: false, qty: "0.108", basis: "x", reject_code: "NO_QUOTE", reject_message: null } as never });
    render(<LivePositionPanel account={account} card={noBook}/>);
    expect(screen.getByTestId("live-position-net-unavailable")).toHaveTextContent("실시간 호가 확인 불가");
  });

  it("a server refusal is shown in the server's own words", () => {
    const refused = card({ net_if_closed: null, net_complete: false,
      close: { feasible: false, qty: "0.108", basis: "x", reject_code: "NO_LIQUIDITY",
               reject_message: "호가가 수량을 소화하지 못합니다." } as never });
    render(<LivePositionPanel account={account} card={refused}/>);
    expect(screen.getByTestId("live-position-net-unavailable")).toHaveTextContent("호가가 수량을 소화하지 못합니다.");
  });

  it("an unknown opening fill is its own reason and the hold time stays a dash", () => {
    const unknown = card({ opened_at_ms: null, opened_source: "UNKNOWN", net_if_closed: null, net_complete: false });
    render(<LivePositionCard card={unknown} nowMs={NOW} onClose={() => {}}/>);
    expect(screen.getByTestId("live-card-net-unavailable")).toHaveTextContent("진입 시각을 확인할 수 없어");
    expect(screen.getByTestId("live-card-held")).toHaveTextContent("보유 시간 -");
  });

  it("an account-level refusal of the card is shown as the server's message", () => {
    const refused = card({ available: false, net_if_closed: null, net_complete: false,
      reject_code: "COMMISSION_UNAVAILABLE", reject_message: "계정 수수료율을 읽지 못해 청산 손익을 계산하지 않습니다." });
    render(<LivePositionCard card={refused} nowMs={NOW} onClose={() => {}}/>);
    expect(screen.getByTestId("live-card-net-unavailable")).toHaveTextContent("계정 수수료율을 읽지 못해");
  });
});
