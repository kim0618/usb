import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LivePositionCard } from "@/components/crypto-live-terminal";
import { MobilePositionCard } from "@/components/crypto-terminal-layout";
import type { LivePositionCard as LivePositionCardData } from "@/lib/crypto-live";
import type { CryptoState } from "@/lib/crypto-paper";

/** The LIVE position card.
 *
 *  It looks like the paper card on purpose and shares none of its arithmetic. The tests below
 *  check both halves of that: the same reading order and controls, and every figure taken from
 *  the server payload rather than worked out here.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const OPENED = 1_790_700_000_000;
const NOW = OPENED + 3_725_000;      // 1시간 2분

const card = (over: Partial<LivePositionCardData> = {}): LivePositionCardData => ({
  open: true, available: true, symbol: "BTCUSDT", fetched_at_ms: NOW, age_ms: 800,
  side: "LONG", qty: "0.015", leverage: "10",
  entry_price: "82666.66", break_even_price: "82700.12", mark_price: "83400.00",
  liquidation_price: "75100.10", unrealized_pnl: "11.00", notional: "1251.00",
  initial_margin: "125.10",
  opened_at_ms: OPENED, opened_source: "BINANCE_USER_TRADES_WALKBACK",
  commission_paid: "0.6200", realized_since_open: "0", funding_income: "-0.3000",
  close: { feasible: true, qty: "0.015",
           basis: "CLOSE_ENTIRE_POSITION_AT_MARKET_ON_THIS_BOOK_NOW",
           exit_fill_price: "83399.90", exit_fee: "0.6255", gross_pnl: "11.0000",
           fee_rate: "0.0005", reference_price: "83400.00", reject_code: null,
           reject_message: null },
  net_if_closed: "9.4545", net_basis: "CLOSE_ENTIRE_POSITION_AT_MARKET_ON_THIS_BOOK_NOW",
  net_complete: true, krw: { unrealized_pnl: "15301", net_if_closed: "13152.7" },
    krw_per_usdt: "1391",
  ...over,
});

const show = (over: Partial<LivePositionCardData> = {}, props: Record<string, unknown> = {}) =>
  render(<LivePositionCard card={card(over)} nowMs={NOW} onClose={vi.fn()} {...props} />);


describe("the LIVE position card", () => {
  it("shows a LONG with its side, leverage, size and hold time", () => {
    show();
    expect(screen.getByTestId("live-card-side")).toHaveTextContent("LONG");
    expect(screen.getByTestId("live-card-leverage")).toHaveTextContent("10x");
    expect(screen.getByTestId("live-card-qty")).toHaveTextContent("0.015 BTC");
    expect(screen.getByTestId("live-card-held")).toHaveTextContent("1시간 2분 보유");
  });

  it("shows a SHORT the same way", () => {
    show({ side: "SHORT", qty: "0.004" });
    expect(screen.getByTestId("live-card-side")).toHaveTextContent("SHORT");
    expect(screen.getByTestId("live-card-qty")).toHaveTextContent("0.004 BTC");
  });

  it("is absent entirely while the account is flat", () => {
    render(<LivePositionCard card={{ open: false }} nowMs={NOW} onClose={vi.fn()} />);
    expect(screen.queryByTestId("live-position-card")).not.toBeInTheDocument();
  });

  it("is absent before the first read", () => {
    render(<LivePositionCard card={null} nowMs={NOW} onClose={vi.fn()} />);
    expect(screen.queryByTestId("live-position-card")).not.toBeInTheDocument();
  });

  it("shows entry and Binance's mark", () => {
    show();
    expect(screen.getByTestId("live-card-entry")).toHaveTextContent("82,666.7");
    expect(screen.getByTestId("live-card-mark")).toHaveTextContent("83,400.0");
  });

  it("prints the server's unrealized figure rather than deriving one", () => {
    show({ unrealized_pnl: "11.00" });
    expect(screen.getByTestId("live-card-unrealized")).toHaveTextContent("+11.0000 USDT");
  });

  it("prints the server's close-now net rather than subtracting fees itself", () => {
    show();
    expect(screen.getByTestId("live-card-net")).toHaveTextContent("+9.4545 USDT");
  });

  it("says so when the book cannot absorb the close instead of showing a net", () => {
    show({ net_complete: false, net_if_closed: null,
           close: { feasible: false, qty: "0.015", basis: "x", reject_code: "NO_LIQUIDITY",
                    reject_message: "visible depth covers 0.004 of 0.015" } });
    expect(screen.queryByTestId("live-card-net")).not.toBeInTheDocument();
    expect(screen.getByTestId("live-card-net-unavailable")).toHaveTextContent("visible depth");
  });

  it("shows a dash instead of a hold time the server could not establish", () => {
    // positionRisk.updateTime is the last change, not the open. A wrong duration is worse than
    // none, so the server says unknown and the card says so.
    show({ opened_at_ms: null, opened_source: "UNKNOWN", net_complete: false });
    expect(screen.getByTestId("live-card-held")).toHaveTextContent("보유 시간 -");
  });

  it("shows a dash for a liquidation price Binance does not publish", () => {
    show({ liquidation_price: null });
    fireEvent.click(screen.getByTestId("live-card-detail-toggle"));
    const detail = screen.getByTestId("live-card-detail");
    expect(detail).toHaveTextContent("청산가");
    expect(detail.textContent).toContain("-");
  });

  it("folds the cost detail away until it is asked for", () => {
    show();
    expect(screen.queryByTestId("live-card-detail")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("live-card-detail-toggle"));
    const detail = screen.getByTestId("live-card-detail");
    expect(detail).toHaveTextContent("75,100.1");
    expect(detail).toHaveTextContent("0.6255");
    expect(detail).toHaveTextContent("-0.3000");
  });

  it("offers CLOSE and calls only the handler it was given", () => {
    const onClose = vi.fn();
    show({}, { onClose });
    const button = screen.getByTestId("live-card-close");
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent("CLOSE · 전량 청산");
    fireEvent.click(button);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("disables CLOSE while a request is in flight", () => {
    show({}, { busy: true });
    expect(screen.getByTestId("live-card-close")).toBeDisabled();
  });

  it("carries no market-hours vocabulary", () => {
    show();
    fireEvent.click(screen.getByTestId("live-card-detail-toggle"));
    const rendered = screen.getByTestId("live-position-card");
    for (const word of ["거래가능시간", "장 시작", "장 마감", "시장 오픈", "시장 종료", "정규장"]) {
      expect(rendered).not.toHaveTextContent(word);
    }
  });
});


describe("the PAPER position card is untouched", () => {
  const state = (): CryptoState => ({
    account: { equity: "9530.39", wallet_balance: "9530.39", available_balance: "9400.00",
      unrealized_pnl: "11.00", realized_pnl: "2125.50", position_side: "LONG",
      position_qty: "0.015", avg_entry: "82666.66", liquidation_price: "75100.10",
      used_margin: "125.10", margin_ratio: "0.05", cumulative_fees: "1.0",
      cumulative_funding: "0" },
    quote: { mark_price: "83400.00", index_price: "83410.00", best_bid: "83399.90",
      best_ask: "83400.10", ts_ms: NOW, feed_status: "LIVE" },
    leverage: "10", krw: null, fx: { krw_per_usdt: "1391.0" },
    state: { mode: "MANUAL", can_open_new_position: true, auto_unavailable_reason: "" },
    server_time_ms: NOW, recovery: null, run: null,
  } as unknown as CryptoState);

  it("still renders with its own testids and its own CLOSE", () => {
    const onAction = vi.fn();
    render(<MobilePositionCard state={state()} openedMs={OPENED} nowMs={NOW}
      onAction={onAction} busy={false} />);
    expect(screen.getByTestId("mobile-position-card")).toBeInTheDocument();
    expect(screen.getByTestId("mobile-position-side")).toHaveTextContent("LONG");
    fireEvent.click(screen.getByTestId("mobile-close-button"));
    expect(onAction).toHaveBeenCalledTimes(1);
    // The two cards are separate components; the LIVE one is not in this tree.
    expect(screen.queryByTestId("live-position-card")).not.toBeInTheDocument();
  });
});

describe("the LIVE mobile card KRW hierarchy", () => {
  it("shows positive KRW first and authoritative USDT second", () => {
    show();
    expect(screen.getByTestId("live-card-unrealized-krw")).toHaveTextContent("+15,301원");
    expect(screen.getByTestId("live-card-unrealized")).toHaveTextContent("+11.0000 USDT");
    expect(screen.getByTestId("live-card-net-krw")).toHaveTextContent("+13,153원");
    expect(screen.getByTestId("live-card-net")).toHaveTextContent("+9.4545 USDT");
  });

  it("uses loss colors and comma formatting for negative KRW", () => {
    show({ unrealized_pnl: "-11", net_if_closed: "-9",
      krw: { unrealized_pnl: "-1234567", net_if_closed: "-987654" } });
    expect(screen.getByTestId("live-card-unrealized-krw")).toHaveTextContent("-1,234,567원");
    expect(screen.getByTestId("live-card-net-krw")).toHaveTextContent("-987,654원");
    expect(screen.getByTestId("live-card-unrealized-krw").className).toContain("danger");
  });

  it("renders zero neutrally and long values without wrapping at mobile width", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    show({ unrealized_pnl: "0", net_if_closed: "0",
      krw: { unrealized_pnl: "0", net_if_closed: "1234567890" } });
    expect(screen.getByTestId("live-card-unrealized-krw")).toHaveTextContent("0원");
    expect(screen.getByTestId("live-card-net-krw")).toHaveTextContent("+1,234,567,890원");
    expect(screen.getByTestId("live-card-net-krw")).toHaveClass("whitespace-nowrap");
  });

  it("shows a dash for both null and string zero liquidation prices", () => {
    for (const liquidation_price of [null, "0"] as const) {
      const view = show({ liquidation_price });
      fireEvent.click(screen.getByTestId("live-card-detail-toggle"));
      expect(screen.getByTestId("live-card-detail")).toHaveTextContent("청산가-");
      view.unmount();
    }
  });
});
