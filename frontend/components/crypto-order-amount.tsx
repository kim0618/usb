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
