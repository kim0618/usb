import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  AccountPanel, BookPanel, LedgerTable, OrderTicket, PerformancePanel, PositionPanel, PriceChart,
  PriceHeadline, RunFooter, SourceBanner, TradeHistory, describe as describeEvent,
} from "@/components/crypto-paper-terminal";
import {
  chartGeometry, cryptoApi, duration, feedAgeSeconds, isHighRisk, percent, rateBps, rejectLabel,
  signedUsdt, toneClass,
} from "@/lib/crypto-paper";
import type {
  ChartBar, CryptoSizing, CryptoState, Performance, SideSizing, SizePreset, TradeBreakdown, TradeRow,
} from "@/lib/crypto-paper";

const baseState = (overrides: Partial<CryptoState> = {}): CryptoState => ({
  run_id: "paper-test", engine_version: "d3.1", leverage: "10", server_time_ms: 1_000_500,
  started_at_ms: 1_000_000, ledger_event_count: 9, input_record_count: 12, liquidation_count: 0,
  funding_grid_mismatches: 0, starting_capital_krw: "1000000",
  state: {
    mode: "MANUAL", modes: ["MANUAL", "AUTO", "AUTO_STOPPING", "EMERGENCY"],
    can_open_new_position: true, new_entry_blocked_reason: null, auto_available: false,
    auto_unavailable_reason: "AUTO_NOT_READY",
  },
  quote: {
    ts_ms: 1_000_000, best_bid: "86000.0", best_ask: "86000.1", spread: "0.1", mid: "86000.05",
    mark_price: "86000.05", last_price: "86000.0", index_price: "86010.2",
    funding_rate: "0.0001", next_funding_time_ms: 1_100_000,
    bid_depth_top5: "12.345", ask_depth_top5: "9.876",
  },
  account: {
    starting_capital_usdt: "744.047619", wallet_balance: "744.047619",
    available_balance: "744.047619", used_margin: "0", realized_pnl: "0", unrealized_pnl: "0",
    equity: "744.047619", cumulative_fees: "0", cumulative_funding_paid: "0",
    position_side: null, position_qty: "0", position_signed_qty: "0", avg_entry: null,
    leverage: null, entry_notional: "0", mark_notional: "0", maintenance_margin: "0",
    margin_ratio: null, liquidation_price: null, risk_tier: null,
    capital_base_usdt: "744.047619", reset_count: 0, last_reset_ts_ms: null,
  },
  krw: { equity: "1000000", available_balance: "1000000", realized_pnl: "0",
         unrealized_pnl: "0", used_margin: "0", wallet_balance: "1000000" },
  fees: { version: "bybit-usdt-perp-nonvip-2026-09", taker_rate: "0.00055", maker_rate: "0.0002",
          source: "assumed", effective_date: "2026-09-09", basis: "ASSUMED_PUBLIC_NON_VIP" },
  fx: { krw_per_usdt: "1344.00", source: "upbit KRW-USDT", asof_utc: "2026-09-23T03:24:54Z" },
  slippage: { model: "NONE", bps: "0" },
  feed: { connected: true, connects: 1, reconnects: 0, book_gaps: 0, book_resyncs: 0,
          malformed: 0, messages: 4210, last_message_ms: 1_000_200, last_error: null,
          book_ready: true },
  recovery: null,
  ...overrides,
});

const sizePreset = (label: string, fraction: string, qty: string,
                   overrides: Partial<SizePreset> = {}): SizePreset => ({
  label, fraction, qty, feasible: true,
  fill_price: "86000.1", notional: String(Number(qty) * 86000.1),
  fee: String(Number(qty) * 86000.1 * 0.00055),
  reserved_margin: String(Number(qty) * 86000.1 / 10),
  required_total: String(Number(qty) * 86000.1 / 10 + Number(qty) * 86000.1 * 0.00055),
  liquidation_price: "77656.3", margin_ratio: "3.1", risk_tier: 1,
  resulting_qty: qty, resulting_avg_entry: "86000.1", available_after: "0.5",
  reject_code: null, reject_message: null,
  entry_feasible: true, exit_feasible: true, exit_fill_price: "86000.0",
  exit_reject_code: null, exit_reject_message: null,
  quote_ts_ms: 1_000_000, best_bid: "86000.0", best_ask: "86000.1",
  mark_price: "86000.05", bid_depth: "12.345", ask_depth: "9.876",
  ...overrides,
});

const sideSizing = (side: string, presets: SizePreset[]): SideSizing => ({
  side, leverage: "10", max_qty: presets[presets.length - 1].qty,
  max_feasible: presets[presets.length - 1].feasible,
  reject_code: null, reject_message: null,
  instrument: { qty_step: "0.001", min_order_qty: "0.001", min_notional_value: "5",
                max_mkt_order_qty: "150.000" },
  max_definition: "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT",
  quote_ts_ms: 1_000_000, best_bid: "86000.0", best_ask: "86000.1",
  mark_price: "86000.05", entry_depth: "9.876", exit_depth: "12.345",
  presets,
});

const baseSizing = (overrides: Partial<CryptoSizing> = {}): CryptoSizing => ({
  run_id: "paper-test", leverage: "10", quote_ts_ms: 1_000_000, mark_price: "86000.05",
  available_balance: "744.047619",
  sides: {
    LONG: sideSizing("LONG", [
      sizePreset("25%", "0.25", "0.002"), sizePreset("HALF", "0.50", "0.004"),
      sizePreset("75%", "0.75", "0.006"), sizePreset("MAX", "1.00", "0.008"),
    ]),
    SHORT: sideSizing("SHORT", [
      sizePreset("25%", "0.25", "0.002"), sizePreset("HALF", "0.50", "0.004"),
      sizePreset("75%", "0.75", "0.006"), sizePreset("MAX", "1.00", "0.008"),
    ]),
  },
  ...overrides,
});

const withPosition = (side: "LONG" | "SHORT" = "LONG"): CryptoState => {
  const state = baseState();
  return {
    ...state,
    account: {
      ...state.account!, position_side: side, position_qty: "0.010",
      position_signed_qty: side === "LONG" ? "0.010" : "-0.010", avg_entry: "86000.1",
      leverage: "10", entry_notional: "860.001", mark_notional: "860.0005",
      used_margin: "86.0001", maintenance_margin: "2.838", margin_ratio: "30.3",
      liquidation_price: side === "LONG" ? "77659.2" : "94312.5", risk_tier: 1,
      unrealized_pnl: side === "LONG" ? "-0.0005" : "0.0005",
    },
  };
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("price and book", () => {
  it("shows the mark price as the headline because that is what the account values with", () => {
    render(<PriceHeadline state={baseState()} />);
    expect(screen.getByTestId("mark-price")).toHaveTextContent("86,000.1");
    expect(screen.getByText(/Last 86,000.0/)).toBeInTheDocument();
  });

  it("calls the feed stale when nothing has arrived for more than five seconds", () => {
    const fresh = baseState();
    expect(feedAgeSeconds(fresh)).toBeCloseTo(0.3);
    render(<PriceHeadline state={{ ...fresh, server_time_ms: 1_010_000 }} />);
    expect(screen.getByText("시세 지연")).toBeInTheDocument();
  });

  it("puts bid, ask and spread on screen with the spread also in basis points", () => {
    render(<BookPanel state={baseState()} />);
    expect(screen.getByTestId("best-bid")).toHaveTextContent("86,000.0");
    expect(screen.getByTestId("best-ask")).toHaveTextContent("86,000.1");
    expect(screen.getByTestId("spread")).toHaveTextContent("0.10");
    expect(within(screen.getByTestId("book-panel")).getByText("0.01 bp")).toBeInTheDocument();
  });

  it("draws nothing rather than a misleading line when there is one bar or none", () => {
    const bar = (close: string, start: number): ChartBar => ({
      start_ms: start, open: close, high: close, low: close, close, volume: "1", confirmed: true });
    expect(chartGeometry([])).toBeNull();
    expect(chartGeometry([bar("1", 0)])).toBeNull();
    const geometry = chartGeometry([bar("100", 0), bar("110", 60_000)]);
    expect(geometry?.rising).toBe(true);
    render(<PriceChart bars={[]} />);
    expect(screen.getByText(/차트 데이터를 받는 중/)).toBeInTheDocument();
  });
});

describe("account and position", () => {
  it("shows equity and available in USDT with the run's own KRW conversion", () => {
    render(<AccountPanel state={baseState()} />);
    expect(screen.getByTestId("equity")).toHaveTextContent("744.05 USDT");
    expect(screen.getByTestId("available")).toHaveTextContent("744.05 USDT");
    // Equity and available are both the full balance while flat, so both carry the KRW line.
    expect(screen.getAllByText("≈ 1,000,000원")).toHaveLength(2);
    expect(screen.getByText("시작 1,000,000원")).toBeInTheDocument();
  });

  it("names the rate that converted the KRW figures so the shell's own rate is not read as this one", () => {
    render(<AccountPanel state={baseState()} />);
    expect(screen.getByTestId("fx-note")).toHaveTextContent("1 USDT = 1,344원");
    expect(screen.getByTestId("fx-note")).toHaveTextContent("주식 화면 환율");
  });

  it("colours profit and loss and leaves zero neutral", () => {
    expect(toneClass("1")).toBe("text-success");
    expect(toneClass("-1")).toBe("text-danger");
    expect(toneClass("0")).toBe("text-foreground");
    expect(toneClass(null)).toBe("text-foreground");
    expect(signedUsdt("1.5")).toBe("+1.5000 USDT");
    expect(signedUsdt("-1.5")).toBe("-1.5000 USDT");
  });

  it("says there is no position instead of drawing an empty one", () => {
    render(<PositionPanel state={baseState()} />);
    expect(screen.getByTestId("position-panel")).toHaveTextContent("보유 포지션이 없습니다");
  });

  it("shows side, entry, mark and liquidation price for an open position", () => {
    render(<PositionPanel state={withPosition("SHORT")} />);
    expect(screen.getByTestId("position-side")).toHaveTextContent("SHORT");
    expect(screen.getByTestId("liquidation-price")).toHaveTextContent("94,312.5");
    const panel = within(screen.getByTestId("position-panel"));
    expect(panel.getByText("진입가").nextElementSibling).toHaveTextContent("86,000.1");
    expect(panel.getByText("Mark").nextElementSibling).toHaveTextContent("86,000.1");
    expect(panel.getByText("수량").nextElementSibling).toHaveTextContent("0.010 BTC");
    expect(panel.getByText("리스크 티어 1")).toBeInTheDocument();
  });
});

describe("order ticket", () => {
  const noop = () => {};

  it("marks MANUAL mode and keeps AUTO to a single line, not a panel", () => {
    render(<OrderTicket state={baseState()} onAction={noop} busy={false} error={null} />);
    expect(screen.getByText("수동 모드")).toBeInTheDocument();
    // AUTO cannot be switched on until a strategy exists, so it is a status line carrying the
    // engine's reason. A large disabled control would only compete with the ones that work; the
    // full explanation lives in the run detail disclosure.
    const auto = screen.getByTestId("auto-note");
    expect(auto).toHaveTextContent("준비중");
    expect(within(auto).getByTitle("AUTO_NOT_READY")).toBeInTheDocument();
    expect(screen.queryByTestId("auto-button")).not.toBeInTheDocument();
  });

  it("sends a LONG at the entered quantity and a SHORT at the same size", () => {
    const actions: Array<() => Promise<unknown>> = [];
    render(<OrderTicket state={baseState()} busy={false} error={null}
      onAction={run => { actions.push(run); }} />);
    fireEvent.change(screen.getByLabelText("주문 크기"), { target: { value: "0.005" } });
    fireEvent.click(screen.getByTestId("long-button"));
    fireEvent.click(screen.getByTestId("short-button"));
    expect(actions).toHaveLength(2);
  });

  it("switches sizing between quantity and notional", () => {
    render(<OrderTicket state={baseState()} onAction={noop} busy={false} error={null} />);
    const notional = screen.getByRole("button", { name: "명목 (USDT)" });
    expect(notional).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(notional);
    expect(notional).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "수량 (BTC)" })).toHaveAttribute("aria-pressed", "false");
  });

  it("disables CLOSE while flat and enables it once a position exists", () => {
    const { rerender } = render(
      <OrderTicket state={baseState()} onAction={noop} busy={false} error={null} />);
    expect(screen.getByTestId("close-button")).toBeDisabled();
    rerender(<OrderTicket state={withPosition()} onAction={noop} busy={false} error={null} />);
    expect(screen.getByTestId("close-button")).toBeEnabled();
  });

  it("blocks new entries when the state machine says they are blocked", () => {
    const blocked = baseState();
    blocked.state = { ...blocked.state, mode: "EMERGENCY", can_open_new_position: false,
                      new_entry_blocked_reason: "NEW_ENTRY_BLOCKED_EMERGENCY" };
    render(<OrderTicket state={blocked} onAction={noop} busy={false} error={null} />);
    expect(screen.getByTestId("long-button")).toBeDisabled();
    expect(screen.getByTestId("short-button")).toBeDisabled();
    expect(screen.getByTestId("emergency-release")).toBeInTheDocument();
  });

  it("requires the confirmation tick before EMERGENCY CLOSE can be pressed", () => {
    render(<OrderTicket state={withPosition()} onAction={noop} busy={false} error={null} />);
    expect(screen.getByTestId("emergency-button")).toBeDisabled();
    fireEvent.click(screen.getByTestId("emergency-confirm"));
    expect(screen.getByTestId("emergency-button")).toBeEnabled();
  });

  it("locks the leverage selector while a position is open", () => {
    render(<OrderTicket state={withPosition()} onAction={noop} busy={false} error={null} />);
    expect(screen.getByRole("button", { name: "20x" })).toBeDisabled();
    expect(screen.getByText(/레버리지 변경 불가/)).toBeInTheDocument();
  });

  it("blocks new entries while the feed is stale but still lets the position be closed", () => {
    // Socket still open, ticks stopped: the quote object is present but old. Entry must be
    // refused anyway, because a fill would be priced off a book that has stopped moving.
    const stale = { ...withPosition(), server_time_ms: 1_010_000 };
    render(<OrderTicket state={stale} onAction={noop} busy={false} error={null} />);
    expect(screen.getByTestId("long-button")).toBeDisabled();
    expect(screen.getByTestId("short-button")).toBeDisabled();
    expect(screen.getByTestId("close-button")).toBeEnabled();
    expect(screen.getByTestId("stale-entry-block")).toBeInTheDocument();
  });

  it("refuses to arm the buttons when there is no live quote", () => {
    render(<OrderTicket state={{ ...baseState(), quote: null }} onAction={noop} busy={false}
      error={null} />);
    expect(screen.getByTestId("long-button")).toBeDisabled();
    expect(screen.getByTestId("short-button")).toBeDisabled();
  });

  it("shows the engine's rejection code rather than a generic failure", () => {
    render(<OrderTicket state={baseState()} onAction={noop} busy={false}
      error="QTY_OFF_GRID · quantity 0.0015 is not on the 0.001 grid" />);
    expect(screen.getByTestId("order-error")).toHaveTextContent("QTY_OFF_GRID");
  });
});

describe("ledger", () => {
  it("describes each event in the terms the operator acted in", () => {
    expect(describeEvent({ seq: 1, ts_ms: 0, event_type: "FILL", side: "LONG", fill_qty: "0.01",
      fill_price: "86000.1", levels_consumed: 1 })).toContain("LONG 0.010 @ 86,000.1");
    expect(describeEvent({ seq: 2, ts_ms: 0, event_type: "FUNDING", direction: "PAID",
      amount_paid: "0.0086", funding_rate: "0.0001" })).toContain("PAID");
    expect(describeEvent({ seq: 3, ts_ms: 0, event_type: "ORDER_REJECTED", code: "QTY_OFF_GRID",
      message: "nope" })).toBe("QTY_OFF_GRID · nope");
    expect(describeEvent({ seq: 4, ts_ms: 0, event_type: "LIQUIDATION", mark_price: "70000",
      computed_liquidation_price: "70010" })).toContain("계산 청산가");
  });

  it("renders the rows it is given and says so when there are none", () => {
    const { rerender } = render(<LedgerTable events={[]} />);
    expect(screen.getByText("원장이 비어 있습니다.")).toBeInTheDocument();
    rerender(<LedgerTable events={[
      { seq: 2, ts_ms: 1_000, event_type: "FEE", amount: "0.000473", liquidity: "TAKER",
        fee_rate: "0.00055", cost_class: "CASH_SEPARATE" },
      { seq: 1, ts_ms: 900, event_type: "RUN_START", starting_capital_usdt: "744.05",
        risk_tier_count: 35 },
    ]} />);
    const table = within(screen.getByTestId("ledger-table"));
    expect(table.getByText("수수료")).toBeInTheDocument();
    expect(table.getByText(/CASH_SEPARATE/)).toBeInTheDocument();
    expect(table.getByText(/리스크 티어 35개/)).toBeInTheDocument();
  });
});

describe("run provenance", () => {
  it("shows the fee assumption, the fixed rate and their sources on screen", () => {
    render(<RunFooter state={baseState()} />);
    expect(screen.getByText(/Taker 5.50 bp/)).toBeInTheDocument();
    expect(screen.getByText(/ASSUMED_PUBLIC_NON_VIP/)).toBeInTheDocument();
    expect(screen.getByText(/upbit KRW-USDT/)).toBeInTheDocument();
    expect(screen.getByText("1 USDT = 1,344원")).toBeInTheDocument();
    expect(screen.getByText(/호가 소모는 별도로 항상 적용/)).toBeInTheDocument();
  });

  it("converts a decimal rate to basis points", () => {
    expect(rateBps("0.00055")).toBe("5.50 bp");
    expect(rateBps(null)).toBe("-");
  });
});

const performance = (overrides: Partial<Performance> = {}): Performance => ({
  trades: 4, wins: 3, losses: 1, win_rate: "0.75",
  gross_pnl: "12.5", fees: "1.2", funding: "-0.3", net_pnl: "11.6",
  avg_win: "5.1", avg_loss: "-3.7", expectancy: "2.9", profit_factor: "4.13",
  payoff_ratio: "1.38",
  max_drawdown: "3.7", max_drawdown_fraction: "0.0049", longest_losing_streak: 1,
  hold_ms_median: 5_400_000, hold_ms_total: 21_600_000,
  max_adverse_excursion: "-8.5", max_favourable_excursion: "14.0", liquidations: 0,
  ending_equity: "755.6", starting_capital: "744.0", source: "LIVE_PAPER", run_id: "paper-d4",
  krw: { net_pnl: "15590", gross_pnl: "16800", fees: "1612", funding: "-403",
         max_drawdown: "4973", ending_equity: "1015526" },
  by_origin: { MANUAL: { trades: 4, wins: 3, net_pnl: "11.6", fees: "1.2", funding: "-0.3" } },
  by_leverage: { "10": { trades: 4, wins: 3, net_pnl: "11.6", fees: "1.2", funding: "-0.3" } },
  by_side: { LONG: { trades: 3, wins: 3, net_pnl: "15.3", fees: "0.9", funding: "-0.3" },
             SHORT: { trades: 1, wins: 0, net_pnl: "-3.7", fees: "0.3", funding: "0" } },
  reconciliation: { gross_pnl_matches: true, fees_match: true, funding_matches: true },
  ...overrides,
});

const tradeRow = (overrides: Partial<TradeRow> = {}): TradeRow => ({
  index: 1, side: "LONG", origin: "MANUAL", leverage: "10",
  opened_ts_ms: 1_000_000, closed_ts_ms: 1_005_400_000, hold_ms: 5_400_000,
  entry_price: "86000.1", exit_price: "86500.0", qty: "0.010",
  gross_pnl: "4.999", fees: "0.95", funding: "0", net_pnl: "4.049",
  max_adverse_excursion: "-1.2", max_favourable_excursion: "6.3",
  liquidated: false, exits: 1, is_win: true, ...overrides,
});

describe("session performance", () => {
  it("shows every required figure once trades exist", () => {
    render(<PerformancePanel performance={performance()} />);
    const panel = within(screen.getByTestId("performance-panel"));
    expect(panel.getByText("4건")).toBeInTheDocument();
    expect(panel.getByText("75.0%")).toBeInTheDocument();
    expect(panel.getByText("+11.6000 USDT")).toBeInTheDocument();
    expect(panel.getByText("4.13")).toBeInTheDocument();          // profit factor
    expect(panel.getByText("최장 연패 1회")).toBeInTheDocument();
    expect(panel.getByText(/1시간 30분/)).toBeInTheDocument();     // median hold
    expect(panel.getByText("강제청산 0회")).toBeInTheDocument();
    expect(panel.getByText(/≈ \+15,590원/)).toBeInTheDocument();
  });

  it("says the figures reconcile against the engine's own running totals", () => {
    render(<PerformancePanel performance={performance()} />);
    expect(screen.getByTestId("performance-source")).toHaveTextContent("일치");
  });

  it("reports a mismatch rather than hiding it", () => {
    render(<PerformancePanel performance={performance({
      reconciliation: { gross_pnl_matches: true, fees_match: false, funding_matches: true } })} />);
    expect(screen.getByTestId("performance-source")).toHaveTextContent("불일치");
  });

  it("says there is nothing to measure yet instead of printing zeros", () => {
    render(<PerformancePanel performance={performance({ trades: 0 })} />);
    expect(screen.getByTestId("performance-panel")).toHaveTextContent("아직 완결된 거래가 없습니다");
  });

  it("leaves profit factor blank while nothing has lost", () => {
    render(<PerformancePanel performance={performance({ profit_factor: null, losses: 0,
                                                        avg_loss: null })} />);
    const panel = within(screen.getByTestId("performance-panel"));
    expect(panel.getByText("Profit Factor").nextElementSibling).toHaveTextContent("-");
  });
});

describe("trade history", () => {
  it("lists each round trip with its hold time and excursions", () => {
    render(<TradeHistory trades={[tradeRow(), tradeRow({ index: 2, side: "SHORT", is_win: false,
                                                          net_pnl: "-3.7" })]} />);
    const table = within(screen.getByTestId("trade-history"));
    // Both rows share the same entry price, so the count is the assertion.
    expect(table.getAllByText("86,000.1")).toHaveLength(2);
    expect(table.getByText("+4.0490 USDT")).toBeInTheDocument();
    expect(table.getByText("-3.7000 USDT")).toBeInTheDocument();
    expect(table.getAllByText("1시간 30분")).toHaveLength(2);
    expect(table.getByText("SHORT")).toBeInTheDocument();
  });

  it("marks a liquidated round trip instead of filing it under its reason", () => {
    render(<TradeHistory trades={[tradeRow({ liquidated: true, origin: "LIQUIDATION" })]} />);
    expect(within(screen.getByTestId("trade-history")).getByText("강제청산")).toBeInTheDocument();
  });

  it("opens a per-trade cost breakdown: gross, entry fee, exit fee, funding, slippage, net", () => {
    const breakdown: TradeBreakdown = {
      index: 1, side: "LONG", closed_ts_ms: 1_005_400_000, gross_realized_pnl: "4.999",
      entry_fee: "0.516", exit_fee: "0.519", funding: "-0.085", funding_pnl: "0.085",
      entry_slippage: "0.001", exit_slippage: "0.002", slippage: "0.003", slippage_pnl: "-0.003",
      net_realized_pnl: "4.049", liquidated: false, exits: 1,
      krw: { gross_realized_pnl: "6718.66", entry_fee: "693.5", exit_fee: "697.5", funding_pnl: "114.2",
        slippage_pnl: "-4.0", net_realized_pnl: "5441.86" },
    };
    render(<TradeHistory trades={[tradeRow()]} breakdowns={[breakdown]} />);
    expect(screen.queryByTestId("trade-detail-1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("trade-detail-toggle-1"));
    const detail = screen.getByTestId("trade-detail-1");
    ["Gross Realized PnL", "Entry Fee", "Exit Fee", "Funding", "Slippage", "Net Realized PnL"]
      .forEach(label => expect(detail).toHaveTextContent(label));
    expect(detail).toHaveTextContent("-0.5160 USDT");
    expect(detail).toHaveTextContent("-0.5190 USDT");
    expect(screen.getByTestId("trade-detail-net-1")).toHaveTextContent("+4.0490 USDT");
    expect(detail).toHaveTextContent("Gross에 포함");
    expect(screen.getByTestId("trade-detail-net-1")).toHaveTextContent("+5,442원");
    expect(detail).toHaveTextContent("-694원");
  });

  it("offers no detail toggle when the server sent no breakdown", () => {
    render(<TradeHistory trades={[tradeRow()]} />);
    expect(screen.queryByTestId("trade-detail-toggle-1")).not.toBeInTheDocument();
  });

  it("says the history is empty rather than drawing a headerless table", () => {
    render(<TradeHistory trades={[]} />);
    expect(screen.getByTestId("trade-history")).toHaveTextContent("완결된 거래가 없습니다");
  });
});

describe("source and recovery banner", () => {
  it("always names the data source so a replay can never look like live", () => {
    render(<SourceBanner state={baseState()} />);
    expect(screen.getByTestId("source-banner")).toHaveTextContent("실시간 페이퍼");
    expect(screen.getByTestId("source-banner")).toHaveTextContent("합성 호가");
  });

  it("shows nothing about recovery on a fresh run", () => {
    render(<SourceBanner state={baseState()} />);
    expect(screen.queryByTestId("restored-badge")).toBeNull();
    expect(screen.queryByTestId("torn-badge")).toBeNull();
  });

  it("says the session was restored and how much it picked up", () => {
    render(<SourceBanner state={baseState({ recovery: {
      restored: true, tape_records: 1372, ledger_events: 5, ledger_offset_bytes: 3215,
      ledger_tail_rewritten_bytes: 0, torn_writes_repaired: [],
      ledger_schema_version: "crypto.paper.ledger.v2", position_signed_qty: "0.002",
      mode: "MANUAL" } })} />);
    const badge = screen.getByTestId("restored-badge");
    expect(badge).toHaveTextContent("복구된 세션");
    expect(badge).toHaveTextContent("원장 5건");
    expect(badge).toHaveTextContent("입력 1372건");
  });

  it("surfaces a repaired torn write rather than swallowing it", () => {
    render(<SourceBanner state={baseState({ recovery: {
      restored: true, tape_records: 10, ledger_events: 3, ledger_offset_bytes: 100,
      ledger_tail_rewritten_bytes: 40,
      torn_writes_repaired: [{ path: "input.jsonl", dropped_bytes: 42 }],
      ledger_schema_version: "crypto.paper.ledger.v2", position_signed_qty: "0",
      mode: "MANUAL" } })} />);
    expect(screen.getByTestId("torn-badge")).toHaveTextContent("잘린 기록 1건 복구");
  });
});

describe("formatting helpers", () => {
  it("prints minutes under an hour and hours above it", () => {
    expect(duration(59_000)).toBe("0분");
    expect(duration(5_400_000)).toBe("1시간 30분");
    expect(duration(null)).toBe("-");
  });

  it("turns a decimal fraction into a percentage", () => {
    expect(percent("0.75")).toBe("75.0%");
    expect(percent("0.0049", 2)).toBe("0.49%");
    expect(percent(null)).toBe("-");
  });
});

describe("quick position size", () => {
  const noop = () => {};

  it("offers exactly the four presets with MAX last", () => {
    render(<OrderTicket state={baseState()} sizing={baseSizing()} onAction={noop} busy={false}
      error={null} />);
    ["25%", "HALF", "75%", "MAX"].forEach(label =>
      expect(screen.getByTestId(`preset-${label}`)).toBeInTheDocument());
  });

  it("keeps the direct quantity and notional input available", () => {
    render(<OrderTicket state={baseState()} sizing={baseSizing()} onAction={noop} busy={false}
      error={null} />);
    // The presets are an addition, not a replacement.
    expect(screen.getByLabelText("주문 크기")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "수량 (BTC)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "명목 (USDT)" })).toBeInTheDocument();
  });

  it("shows the engine's quantity, notional, margin and liquidation price once a preset is picked", () => {
    render(<OrderTicket state={baseState()} sizing={baseSizing()} onAction={noop} busy={false}
      error={null} />);
    fireEvent.click(screen.getByTestId("preset-HALF"));
    // The size table now sits under the preview's [상세], closed by default on a phone.
    fireEvent.click(screen.getByTestId("order-preview-detail-toggle"));
    const readout = screen.getByTestId("preset-readout");
    expect(within(readout).getByText("수량 (BTC)")).toBeInTheDocument();
    expect(within(readout).getByText("명목 (USDT)")).toBeInTheDocument();
    expect(within(readout).getByText("필요 마진")).toBeInTheDocument();
    // Entry and exit are both priced now, and neither is the mark.
    expect(within(readout).getByText("예상 진입가")).toBeInTheDocument();
    expect(within(readout).getByText("즉시 청산가")).toBeInTheDocument();
    expect(within(readout).getByText("강제 청산가")).toBeInTheDocument();
    expect(screen.getByTestId("preset-LONG-수량 (BTC)")).toHaveTextContent("0.004");
    expect(screen.getByTestId("preset-LONG-강제 청산가")).toHaveTextContent("77,656.3");
    expect(screen.getByTestId("preset-LONG-예상 진입가")).toHaveTextContent("86,000.1");
    expect(screen.getByTestId("preset-LONG-즉시 청산가")).toHaveTextContent("86,000.0");
    expect(screen.getByTestId("preset-basis")).toHaveTextContent("현재 호가 기준");
  });

  it("sends the engine's quantity for the side that was clicked, with no confirmation step", () => {
    const sent: unknown[] = [];
    // The order call is mocked, the way the mobile card's CLOSE test mocks it. It used to be
    // invoked for real with `fetch` unstubbed and the rejection swallowed by `.catch(() => {})`,
    // which is harmless on a machine with nothing on port 8100 and is not harmless otherwise:
    // with a paper terminal running there - the ordinary state of a machine being used to look
    // at this screen - running the suite opened a 0.008 BTC position on it. Found that way, by
    // reading a ledger that had five events in it and no explanation.
    const spy = vi.spyOn(cryptoApi, "order").mockResolvedValue({ state: baseState() });
    render(<OrderTicket state={baseState()} sizing={baseSizing()} busy={false} error={null}
      onAction={run => { sent.push(run); void run(); }} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    fireEvent.click(screen.getByTestId("long-button"));
    // One click, one order: the press itself dispatched the action.
    expect(sent).toHaveLength(1);
    expect(spy).toHaveBeenCalledWith("BTCUSDT",
                                     { side: "LONG", intent: "OPEN", qty: "0.008" });
  });

  it("resolves a preset per side rather than reusing one side's quantity for both", () => {
    const lopsided = baseSizing();
    lopsided.sides.SHORT = sideSizing("SHORT", [
      sizePreset("25%", "0.25", "0.001"), sizePreset("HALF", "0.50", "0.002"),
      sizePreset("75%", "0.75", "0.003"), sizePreset("MAX", "1.00", "0.004"),
    ]);
    render(<OrderTicket state={baseState()} sizing={lopsided} onAction={noop} busy={false}
      error={null} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    // A thinner bid side means a smaller SHORT for the same preset, and the buttons say so.
    expect(screen.getByTestId("long-button")).toHaveTextContent("0.008");
    expect(screen.getByTestId("short-button")).toHaveTextContent("0.004");
  });

  it("keeps the armed leverage and preset visible at all times", () => {
    render(<OrderTicket state={baseState()} sizing={baseSizing()} onAction={noop} busy={false}
      error={null} />);
    expect(screen.getByTestId("order-arming")).toHaveTextContent("10x");
    expect(screen.getByTestId("order-arming")).toHaveTextContent("직접 입력");
    fireEvent.click(screen.getByTestId("preset-75%"));
    expect(screen.getByTestId("order-arming")).toHaveTextContent("75%");
  });

  it("disables a preset the engine refused and says why", () => {
    const short = baseSizing();
    short.sides.LONG = sideSizing("LONG", [
      sizePreset("25%", "0.25", "0.002"), sizePreset("HALF", "0.50", "0.004"),
      sizePreset("75%", "0.75", "0.006"),
      sizePreset("MAX", "1.00", "0", { feasible: false, reject_code: "INSUFFICIENT_MARGIN",
                                        reject_message: "requires 900 but available is 700" }),
    ]);
    render(<OrderTicket state={baseState()} sizing={short} onAction={noop} busy={false}
      error={null} />);
    expect(screen.getByTestId("preset-MAX")).toBeDisabled();
    expect(screen.getByTestId("preset-MAX")).toHaveAttribute("title", "증거금 부족");
  });

  it("blocks the side the engine refused while leaving the other side armed", () => {
    const held = baseSizing();
    held.sides.SHORT = sideSizing("SHORT", ["25%", "HALF", "75%", "MAX"].map(label =>
      sizePreset(label, "0", "0", { feasible: false, reject_code: "REVERSE_NOT_ALLOWED",
                                    reject_message: "position is LONG" })));
    render(<OrderTicket state={baseState()} sizing={held} onAction={noop} busy={false}
      error={null} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    expect(screen.getByTestId("short-button")).toBeDisabled();
    expect(screen.getByTestId("long-button")).toBeEnabled();
    fireEvent.click(screen.getByTestId("order-preview-detail-toggle"));
    expect(screen.getByTestId("preset-reject-SHORT")).toHaveTextContent("반대 포지션 보유 중");
  });

  it("marks MAX at high leverage as HIGH RISK", () => {
    const { rerender } = render(<OrderTicket state={baseState({ leverage: "20" })}
      sizing={baseSizing()} onAction={noop} busy={false} error={null} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    expect(screen.getByTestId("high-risk-badge")).toBeInTheDocument();

    // 75% at the same leverage is not the warning case, and neither is MAX at 10x.
    fireEvent.click(screen.getByTestId("preset-75%"));
    expect(screen.queryByTestId("high-risk-badge")).not.toBeInTheDocument();
    rerender(<OrderTicket state={baseState({ leverage: "10" })} sizing={baseSizing()}
      onAction={noop} busy={false} error={null} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    expect(screen.queryByTestId("high-risk-badge")).not.toBeInTheDocument();
  });

  it("can be cleared back to direct entry", () => {
    render(<OrderTicket state={baseState()} sizing={baseSizing()} onAction={noop} busy={false}
      error={null} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    expect(screen.queryByLabelText("주문 크기")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("preset-clear"));
    expect(screen.getByLabelText("주문 크기")).toBeInTheDocument();
  });

  it("waits for the engine instead of guessing a size before sizing arrives", () => {
    render(<OrderTicket state={baseState()} sizing={null} onAction={noop} busy={false}
      error={null} />);
    // No sizing yet means no preset may be armed: a guessed MAX is the one number worth nothing.
    ["25%", "HALF", "75%", "MAX"].forEach(label =>
      expect(screen.getByTestId(`preset-${label}`)).toBeDisabled());
  });

  it("still blocks every entry path while the feed is stale", () => {
    render(<OrderTicket state={{ ...baseState(), server_time_ms: 1_010_000 }}
      sizing={baseSizing()} onAction={noop} busy={false} error={null} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    expect(screen.getByTestId("long-button")).toBeDisabled();
    expect(screen.getByTestId("short-button")).toBeDisabled();
  });

  it("names the engine's reason codes in the operator's language", () => {
    expect(rejectLabel("INSUFFICIENT_MARGIN")).toBe("증거금 부족");
    expect(rejectLabel("REVERSE_NOT_ALLOWED")).toBe("반대 포지션 보유 중");
    // An unrecognised code is shown as itself rather than smoothed into a vague sentence.
    expect(rejectLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });

  it("treats MAX from 20x upward as the high risk combination", () => {
    expect(isHighRisk("20", "MAX")).toBe(true);
    expect(isHighRisk("50", "MAX")).toBe(true);
    expect(isHighRisk("10", "MAX")).toBe(false);
    expect(isHighRisk("50", "75%")).toBe(false);
    expect(isHighRisk("50", null)).toBe(false);
  });
});
