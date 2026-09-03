import { formatOrderSide, formatOrderStatus, formatTradeStatus } from "@/lib/display";
import { signedDecimal } from "@/lib/format";
import type { Fill, Order, Trade } from "@/types/api";

export type TradingHistoryEventType = "ORDER" | "FILL" | "TRADE";

interface TradingHistoryRowBase {
  id: string;
  timestamp: string | null;
  symbol: string;
  side: string | null;
  quantity: string | null;
  price: string | null;
  status: string;
  result: string;
}

export type TradingHistoryRow =
  | (TradingHistoryRowBase & { eventType: "ORDER"; rawSource: Order })
  | (TradingHistoryRowBase & { eventType: "FILL"; rawSource: Fill })
  | (TradingHistoryRowBase & { eventType: "TRADE"; rawSource: Trade });

export const tradingHistoryEventLabel: Readonly<Record<TradingHistoryEventType, string>> = {
  ORDER: "주문",
  FILL: "체결",
  TRADE: "청산",
};

export function composeTradingHistory(orders: Order[], fills: Fill[], trades: Trade[]): TradingHistoryRow[] {
  const rows: TradingHistoryRow[] = [
    ...orders.map((order): TradingHistoryRow => ({
      id: `ORDER:${order.order_id}`,
      eventType: "ORDER",
      timestamp: order.submitted_at,
      symbol: order.symbol,
      side: order.side,
      quantity: order.requested_quantity,
      price: order.reference_price,
      status: order.status,
      result: formatOrderStatus(order.status),
      rawSource: order,
    })),
    ...fills.map((fill): TradingHistoryRow => ({
      id: `FILL:${fill.fill_id}`,
      eventType: "FILL",
      timestamp: fill.filled_at,
      symbol: fill.symbol,
      side: null,
      quantity: fill.quantity,
      price: fill.fill_price,
      status: "FILLED",
      result: "체결 완료",
      rawSource: fill,
    })),
    ...trades.map((trade): TradingHistoryRow => ({
      id: `TRADE:${trade.id}`,
      eventType: "TRADE",
      // The frozen Trade API does not expose entry_at or exit_at. Do not invent one.
      timestamp: null,
      symbol: trade.symbol,
      side: null,
      quantity: null,
      price: trade.exit,
      status: trade.status,
      result: `${signedDecimal(trade.net_pnl)} · ${signedDecimal(trade.net_r, "R")}`,
      rawSource: trade,
    })),
  ];

  return rows.sort((left, right) => {
    const leftTime = timestampValue(left.timestamp);
    const rightTime = timestampValue(right.timestamp);
    if (leftTime !== rightTime) return rightTime - leftTime;
    return left.id.localeCompare(right.id);
  });
}

function timestampValue(timestamp: string | null): number {
  if (!timestamp) return Number.NEGATIVE_INFINITY;
  const value = Date.parse(timestamp);
  return Number.isNaN(value) ? Number.NEGATIVE_INFINITY : value;
}

export const historySideLabel = (row: TradingHistoryRow) => row.side ? formatOrderSide(row.side) : "-";
export const historyStatusLabel = (row: TradingHistoryRow) => row.eventType === "TRADE" ? formatTradeStatus(row.status) : row.result;
