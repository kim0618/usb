"use client";

import { orderAmounts } from "@/lib/crypto-executions";

const krwText = (value: number) => `₩${Math.round(value).toLocaleString("ko-KR")}`;
const usdtText = (value: number) =>
  `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`;
const qtyText = (value: string) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 8 }) : value;
};

/** The selected quantity, what it puts up, and what it is worth.
 *
 *  "내가 거는 금액" is the margin (notional / leverage): it moves with the leverage.
 *  "이번 주문 규모" is the notional (qty x reference price): leverage does not change it.
 *  Both in KRW at the account's existing rate. The fee is still computed (`orderAmounts`, and
 *  the server's own preview) but is not shown here. One component for both
 *  screens, each feeding its own account's quantity, price and rate. USDT appears only when no
 *  KRW rate is available. */
export function OrderAmountSummary({ qty, price, leverage, takerRate, krwPerUsdt, unit, testId }: {
  qty: string | null | undefined;
  price: string | null | undefined;
  leverage: string | null | undefined;
  takerRate: string | null | undefined;
  krwPerUsdt: string | null | undefined;
  unit: string;
  testId: string;
}) {
  const amounts = orderAmounts({ qty, price, leverage, takerRate, krwPerUsdt });
  return (
    <div className="mb-3 rounded-lg border border-line px-3 py-2" data-testid={testId}
      data-notional-usdt={amounts?.notional_usdt}>
      <div className="flex items-baseline justify-between gap-2 text-[11px]">
        <span className="text-muted">선택 수량</span>
        <span className="font-semibold tabular-nums text-foreground" data-testid={`${testId}-qty`}>
          {qty ? `${qtyText(qty)} ${unit}` : "-"}</span>
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-muted">내가 거는 금액</span>
        <span className="text-sm font-semibold tabular-nums text-foreground" data-testid={`${testId}-margin`}>
          {amounts?.margin_usdt == null ? "-"
            : amounts.margin_krw != null ? krwText(amounts.margin_krw)
            : usdtText(amounts.margin_usdt)}
        </span>
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-muted">이번 주문 규모</span>
        <span className="text-base font-bold tabular-nums text-foreground" data-testid={`${testId}-notional`}>
          {amounts == null ? "-"
            : amounts.notional_krw != null ? krwText(amounts.notional_krw)
            : usdtText(amounts.notional_usdt)}
        </span>
      </div>
    </div>
  );
}

export type LiquidationSide = {
  /** Server figure. "0" means the margin covers the whole position: no liquidation above zero. */
  price?: string | null;
  /** (liquidation - mark) / mark, from the server. */
  distancePct?: string | null;
  /** Shown instead of a dash when the side is not an entry (a reduce on a held position). */
  note?: string | null;
};

const distanceText = (value: string | null | undefined) => {
  const n = value == null ? NaN : Number(value);
  if (!Number.isFinite(n)) return "";
  const pct = (n * 100).toFixed(1);
  return n > 0 ? `+${pct}%` : `${pct}%`;
};

/** Where this order would be liquidated, per side, before it is sent.
 *
 *  Every figure is the server's (the LIVE formula estimate, or the PAPER engine's own figure on a
 *  clone); this component only formats it. A side without a figure is a dash, never a number
 *  made up here. `formatPrice` is the screen's own price formatter, so the digits match the
 *  rest of the ticket. */
export function LiquidationEstimate({ sides, formatPrice, footnote, testId }: {
  sides: Partial<Record<"LONG" | "SHORT", LiquidationSide>>;
  formatPrice: (value: string | null | undefined) => string;
  footnote: string;
  testId: string;
}) {
  const cell = (side: "LONG" | "SHORT") => {
    const row = sides[side];
    if (row?.note) return <span className="text-[11px] text-muted">{row.note}</span>;
    const value = row?.price;
    if (value == null || value === "") return <span>-</span>;
    if (Number(value) === 0) return <span className="text-[11px]">청산 없음</span>;
    return (
      <>
        <span className="font-semibold">{formatPrice(value)}</span>
        <span className="ml-1 text-[10px] text-muted">{distanceText(row?.distancePct)}</span>
      </>
    );
  };
  return (
    <div className="mb-3 rounded-lg border border-line px-3 py-2" data-testid={testId}>
      <div className="mb-1 text-[11px] text-muted">예상 강제청산가</div>
      <div className="grid grid-cols-2 gap-2 text-sm tabular-nums">
        {(["LONG", "SHORT"] as const).map(side => (
          <div key={side} className="min-w-0" data-testid={`${testId}-${side}`}>
            <div className={`text-[10px] font-bold ${side === "LONG" ? "text-success" : "text-danger"}`}>{side}</div>
            <div className="truncate text-warning">{cell(side)}</div>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[10px] leading-snug text-muted">{footnote}</p>
    </div>
  );
}
