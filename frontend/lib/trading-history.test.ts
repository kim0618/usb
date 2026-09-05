import { describe, expect, it } from "vitest";
import { composeTradingHistory, historySideLabel, tradingHistoryEventLabel } from "./trading-history";
import type { Fill, Order, Trade } from "@/types/api";

const order = (overrides: Partial<Order> = {}): Order => ({ order_id: "O1", broker_type: "SIM", symbol: "S04", side: "BUY", requested_quantity: "120", filled_quantity: "120", status: "FILLED", rejection_reason: null, reference_price: "58.10", submitted_at: "2026-09-03T14:57:00Z", completed_at: "2026-09-03T14:58:00Z", execution_version: "v1", ...overrides });
const fill = (overrides: Partial<Fill> = {}): Fill => ({ fill_id: "F1", order_id: "O1", symbol: "S04", fill_price: "58.20", quantity: "120", spread_cost: "1", slippage_cost: "2", commission: "3", fx_cost: "0", total_cost: "6", filled_at: "2026-09-03T14:58:00Z", ...overrides });
const trade = (overrides: Partial<Trade> = {}): Trade => ({ id: "T1", symbol: "S04", variant: "C", control: true, status: "CLOSED", entry: "56.40", exit: "58.20", gross_pnl: "222", net_pnl: "216", gross_r: "1.40", net_r: "1.34", total_cost: "6", exit_reason: "TRAILING_STOP", holding_duration: 1, ambiguous_count: 0, source: "SIMULATION", ...overrides });

describe("Stage 9.11.2 unified trading history mapper", () => {
  it("combines Order, Fill, and Trade without mutating sources", () => { const source = order(); const rows = composeTradingHistory([source], [fill()], [trade()]); expect(rows).toHaveLength(3); expect(source).toEqual(order()); });
  it("maps the three event labels", () => expect(tradingHistoryEventLabel).toEqual({ ORDER: "주문", FILL: "체결", TRADE: "청산" }));
  it("maps Order fields", () => expect(composeTradingHistory([order()], [], [])[0]).toMatchObject({ eventType: "ORDER", timestamp: "2026-09-03T14:57:00Z", side: "BUY", quantity: "120", price: "58.10", result: "체결 완료" }));
  it("maps Fill fields", () => expect(composeTradingHistory([], [fill()], [])[0]).toMatchObject({ eventType: "FILL", timestamp: "2026-09-03T14:58:00Z", side: null, quantity: "120", price: "58.20", result: "체결 완료" }));
  it("maps Trade fields without inferred side or quantity", () => expect(composeTradingHistory([], [], [trade()])[0]).toMatchObject({ eventType: "TRADE", timestamp: null, side: null, quantity: null, price: "58.20" }));
  it("sorts valid mixed timestamps descending", () => expect(composeTradingHistory([order()], [fill()], []).map(row => row.eventType)).toEqual(["FILL", "ORDER"]));
  it("places timestamp-less Trades after timestamped events", () => expect(composeTradingHistory([order()], [], [trade()]).map(row => row.eventType)).toEqual(["ORDER", "TRADE"]));
  it("uses a deterministic id fallback when timestamps match", () => expect(composeTradingHistory([order({ order_id: "B" }), order({ order_id: "A" })], [], []).map(row => row.id)).toEqual(["ORDER:A", "ORDER:B"]));
  it("uses a deterministic fallback for invalid timestamps", () => expect(composeTradingHistory([order({ order_id: "B", submitted_at: "bad" }), order({ order_id: "A", submitted_at: "" })], [], []).map(row => row.id)).toEqual(["ORDER:A", "ORDER:B"]));
  it("maps BUY and SELL through the existing display mapper", () => { expect(historySideLabel(composeTradingHistory([order()], [], [])[0])).toBe("매수"); expect(historySideLabel(composeTradingHistory([order({ side: "SELL" })], [], [])[0])).toBe("매도"); });
  it("does not infer a Fill side from its Order", () => expect(historySideLabel(composeTradingHistory([order()], [fill()], [])[0])).toBe("-"));
  it("falls back to an unknown Order status", () => expect(composeTradingHistory([order({ status: "BROKER_HELD" })], [], [])[0].result).toBe("BROKER_HELD"));
  it("formats a positive Trade result from provided values", () => expect(composeTradingHistory([], [], [trade()])[0].result).toBe("+216 · +1.34R"));
  it("formats a negative Trade result", () => expect(composeTradingHistory([], [], [trade({ net_pnl: "-84", net_r: "-0.72" })])[0].result).toBe("-84 · -0.72R"));
  it("does not recompute Trade results from entry and exit", () => expect(composeTradingHistory([], [], [trade({ entry: "1", exit: "999", net_pnl: "7", net_r: "0.1" })])[0].result).toBe("+7 · +0.1R"));
});
