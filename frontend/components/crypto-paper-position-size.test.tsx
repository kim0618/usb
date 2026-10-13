import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MobilePositionCard, PositionStrip } from "@/components/crypto-terminal-layout";
import { LivePositionCard } from "@/components/crypto-live-terminal";
import type { CryptoState } from "@/lib/crypto-paper";
import type { LivePositionCard as LiveCardData } from "@/lib/crypto-live";
import { blockedRequests } from "../vitest.setup";

/** FUTURES PAPER POSITION SIZE PARITY.
 *
 *  The LIVE card always printed "포지션 규모" (server `exposure` = abs(positionRisk.notional),
 *  KRW over USDT); the PAPER card did not, because neither paper render path passed the shell a
 *  `top` block. Paper now prints its engine's `mark_notional` (abs(qty) x mark) in the same block.
 *
 *  The mark_notional figures below are what `Position.notional_at(mark)` returned in a dry run of
 *  the backend paper account at mark 120,000 (LONG 0.25, SHORT -0.25, partial close to -0.10,
 *  add-on to 0.40, the same 0.25 at 20x). Nothing in this file talks to a server: `fetch` is
 *  stubbed and every test asserts no request was even attempted.
 */

const fetchStub = vi.fn(() => Promise.reject(new TypeError("no network in this test")));
beforeEach(() => { vi.stubGlobal("fetch", fetchStub); fetchStub.mockClear(); });
afterEach(() => {
  expect(fetchStub).not.toHaveBeenCalled();
  expect(blockedRequests).toEqual([]);
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});

const MARK = "120000.0";

const paper = (account: Partial<NonNullable<CryptoState["account"]>> = {}): CryptoState => ({
  run_id: "paper-size", engine_version: "d4.1", leverage: "10", server_time_ms: 1_000_500,
  started_at_ms: 1_000_000, ledger_event_count: 3, input_record_count: 3, liquidation_count: 0,
  funding_grid_mismatches: 0, starting_capital_krw: "10000000",
  state: { mode: "MANUAL", modes: ["MANUAL"], can_open_new_position: true, new_entry_blocked_reason: null,
    auto_available: false, auto_unavailable_reason: "AUTO_NOT_READY" },
  quote: { ts_ms: 1_000_000, best_bid: "119999.9", best_ask: "120000.1", spread: "0.2", mid: "120000.0",
    mark_price: MARK, last_price: "120000.0", index_price: "120000.0", funding_rate: "0.0001",
    next_funding_time_ms: 1_100_000, bid_depth_top5: "12", ask_depth_top5: "9" },
  account: { starting_capital_usdt: "7457", wallet_balance: "7457", available_balance: "4507",
    used_margin: "2950.00", realized_pnl: "0", unrealized_pnl: "500", equity: "7957", cumulative_fees: "0",
    cumulative_funding_paid: "0", position_side: "LONG", position_qty: "0.25", position_signed_qty: "0.25",
    avg_entry: "118000.0", leverage: "10", entry_notional: "29500.00", mark_notional: "30000.00",
    maintenance_margin: "150", margin_ratio: "50", liquidation_price: "106800.0", risk_tier: 1,
    capital_base_usdt: "7457", reset_count: 0, last_reset_ts_ms: null, ...account },
  krw: { equity: "10670337", available_balance: "6043887", realized_pnl: "0", unrealized_pnl: "670500",
    used_margin: "3955950", wallet_balance: "10000000" },
  fees: { version: "v", taker_rate: "0.00055", maker_rate: "0.0002", source: "s", effective_date: "d", basis: "b" },
  fx: { krw_per_usdt: "1341.00", source: "upbit", asof_utc: "t" }, slippage: { model: "NONE", bps: "0" },
  feed: { connected: true, connects: 1, reconnects: 0, book_gaps: 0, book_resyncs: 0, malformed: 0,
    messages: 1, last_message_ms: 1_000_400, last_error: null, book_ready: true },
  recovery: null,
});

const SHORT = { position_side: "SHORT" as const, position_qty: "0.25", position_signed_qty: "-0.25" };
const FLAT = { position_side: null, position_qty: "0", position_signed_qty: "0", avg_entry: null,
  leverage: null, used_margin: "0", entry_notional: "0", mark_notional: "0", liquidation_price: null };

const mobile = (state: CryptoState) =>
  <MobilePositionCard state={state} openedMs={null} nowMs={1} onAction={() => {}} busy={false} />;
const desktop = (state: CryptoState) => <PositionStrip state={state} openedMs={null} nowMs={1} />;

describe("paper 포지션 규모", () => {
  it("shows a LONG's size on the phone card, KRW over USDT", () => {
    render(mobile(paper()));
    expect(screen.getByTestId("mobile-position-card")).toHaveTextContent("포지션 규모");
    expect(screen.getByTestId("mobile-position-exposure-krw")).toHaveTextContent("40,230,000원");
    expect(screen.getByTestId("mobile-position-exposure")).toHaveTextContent("30,000.00 USDT");
  });

  it("shows a SHORT's size as a positive figure, direction only on the badge", () => {
    render(mobile(paper(SHORT)));
    expect(screen.getByTestId("mobile-position-side")).toHaveTextContent("SHORT");
    expect(screen.getByTestId("mobile-position-exposure")).toHaveTextContent("30,000.00 USDT");
    expect(screen.getByTestId("mobile-position-exposure").textContent).not.toContain("-");
    expect(screen.getByTestId("mobile-position-exposure-krw").textContent).not.toContain("-");
  });

  it("shrinks with a partial close on the next snapshot", () => {
    const { rerender } = render(desktop(paper(SHORT)));
    expect(screen.getByTestId("position-strip-exposure")).toHaveTextContent("30,000.00 USDT");
    rerender(desktop(paper({ ...SHORT, position_qty: "0.10", position_signed_qty: "-0.10",
      used_margin: "1180.00", mark_notional: "12000.00" })));
    expect(screen.getByTestId("position-strip-exposure")).toHaveTextContent("12,000.00 USDT");
    expect(screen.getByTestId("position-strip-exposure-krw")).toHaveTextContent("16,092,000원");
  });

  it("grows with an additional entry on the next snapshot", () => {
    const { rerender } = render(mobile(paper()));
    rerender(mobile(paper({ position_qty: "0.40", position_signed_qty: "0.40",
      used_margin: "4720.00", mark_notional: "48000.00" })));
    expect(screen.getByTestId("mobile-position-exposure")).toHaveTextContent("48,000.00 USDT");
    expect(screen.getByTestId("mobile-position-exposure-krw")).toHaveTextContent("64,368,000원");
  });

  it("is not divided by leverage: 10x and 20x hold the same size", () => {
    const { rerender } = render(desktop(paper()));
    const at10 = screen.getByTestId("position-strip-exposure").textContent;
    rerender(desktop(paper({ leverage: "20", used_margin: "1475.00" })));
    expect(screen.getByTestId("position-strip-exposure").textContent).toBe(at10);
    expect(at10).toBe("30,000.00 USDT");
    // The margin is the number leverage changes, and it is a separate fact.
    expect(screen.getByTestId("position-strip")).toHaveTextContent("1,475.00 USDT");
  });

  it("converts to KRW at the run's fixed rate, the rate every paper won figure uses", () => {
    const state = paper();
    render(mobile({ ...state, fx: { ...state.fx, krw_per_usdt: "1400.00" } }));
    expect(screen.getByTestId("mobile-position-exposure-krw")).toHaveTextContent("42,000,000원");
  });

  it("adds no size card while flat, on either layout", () => {
    const { container, unmount } = render(mobile(paper(FLAT)));
    expect(container).toBeEmptyDOMElement();
    unmount();
    render(desktop(paper(FLAT)));
    expect(screen.getByTestId("position-strip")).toHaveTextContent("현재 포지션 없음");
    expect(screen.queryByText("포지션 규모")).toBeNull();
    expect(screen.queryByTestId("position-strip-exposure-block")).toBeNull();
  });

  it("renders on the desktop strip as well as the phone card", () => {
    render(desktop(paper()));
    expect(screen.getByTestId("position-strip")).toHaveTextContent("포지션 규모");
    expect(screen.getByTestId("position-strip-exposure-krw")).toHaveTextContent("40,230,000원");
    expect(screen.getByTestId("position-strip-exposure")).toHaveTextContent("30,000.00 USDT");
  });
});

describe("same block, same place as LIVE", () => {
  const liveCard: LiveCardData = {
    open: true, available: true, symbol: "BTCUSDT", side: "LONG", qty: "0.25", leverage: "10",
    entry_price: "118000.0", mark_price: MARK, unrealized_pnl: "500", notional: "30000.00",
    exposure: "30000.00", exposure_basis: "positionRisk.notional",
    krw: { exposure: "40230000" }, krw_per_usdt: "1341.00",
  };

  it("prints the identical size text and markup for the same position", () => {
    const { unmount } = render(<LivePositionCard card={liveCard} nowMs={1} onClose={vi.fn()} />);
    const live = screen.getByTestId("live-card-exposure-block");
    const liveHtml = live.innerHTML.replace(/live-card/g, "X");
    const liveText = live.textContent;
    // Directly under the header row, before the PnL headline, on the LIVE card.
    expect(live.previousElementSibling?.querySelector("[data-testid='position-source']")).not.toBeNull();
    unmount();

    render(mobile(paper()));
    const block = screen.getByTestId("mobile-position-exposure-block");
    expect(block.textContent).toBe(liveText);
    expect(block.innerHTML.replace(/mobile-position/g, "X")).toBe(liveHtml);
    expect(block.previousElementSibling?.querySelector("[data-testid='position-source']")).not.toBeNull();
  });

  it("leaves the LIVE block as it was: KRW over USDT, hidden when the server has no exposure", () => {
    const { unmount } = render(<LivePositionCard card={liveCard} nowMs={1} onClose={vi.fn()} />);
    expect(screen.getByTestId("live-card-exposure-krw")).toHaveTextContent("40,230,000원");
    expect(screen.getByTestId("live-card-exposure")).toHaveTextContent("30,000.00 USDT");
    unmount();
    render(<LivePositionCard card={{ ...liveCard, exposure: null }} nowMs={1} onClose={vi.fn()} />);
    expect(screen.queryByTestId("live-card-exposure-block")).toBeNull();
  });
});
