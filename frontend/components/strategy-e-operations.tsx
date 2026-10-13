"use client";

/** Strategy E's operating blocks: today's decision, holdings and the daily PnL table. They answer in
 *  the operator's words; raw enums and ledger internals stay on the API and the detail screens. */

import { useState } from "react";
import { SignedMoneyValue } from "@/components/daily-performance";
import { StrategyTrades } from "@/components/strategy-runtime";
import { EmptyState } from "@/components/ui";
import { formatKrw, formatUsd, usdToDisplayKrw } from "@/lib/format";
import { decisionOf, type EntryCondition } from "@/lib/strategy-e-decision";
import type { DailyView, StrategyAccount, StrategyPosition, StrategyStatus, StrategyTrade } from "@/lib/strategies";
import { STRATEGY_E } from "@/lib/strategies";

const kstTime = (value: string | null | undefined) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(date).map(x => [x.type, x.value]));
  return `${p.month}/${p.day} ${p.hour === "24" ? "00" : p.hour}:${p.minute} KST`;
};

const CONDITION_TONE: Readonly<Record<EntryCondition, string>> = {
  "충족": "tone-success", "미충족": "tone-neutral", "판단 불가": "tone-warning",
};

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><dt className="label">{label}</dt><dd className="mt-1 text-sm font-semibold text-foreground">{children}</dd></div>;
}

export function TodayDecision({ status, account, decidedAt }: {
  status: StrategyStatus; account: StrategyAccount; decidedAt: string | null;
}) {
  const d = decisionOf(status, account);
  return <section aria-labelledby="e-decision-title" className="mb-7">
    <h2 id="e-decision-title" className="mb-3 font-semibold">오늘 결정</h2>
    <div className="panel p-5" data-e-decision="">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4">
        <Item label="진입 조건">
          <span data-condition={d.condition}
            className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs font-bold ${CONDITION_TONE[d.condition]}`}>{d.condition}</span>
        </Item>
        <Item label="선정 종목">{d.selected == null ? "-" : d.selected.length ? d.selected.join(", ") : "없음"}</Item>
        <Item label="결정 시각">{kstTime(decidedAt)}</Item>
        <Item label="진입 여부">{d.entry}</Item>
      </dl>
      <p className="mt-4 text-sm text-foreground-secondary" data-reason="">{d.reason}</p>
    </div>
  </section>;
}

export function Holdings({ positions }: { positions: StrategyPosition[] }) {
  return <section aria-labelledby="e-positions-title" className="mb-7">
    <h2 id="e-positions-title" className="mb-3 font-semibold">현재 보유 종목</h2>
    {!positions.length ? <EmptyState title="보유 중인 포지션이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>종목</th><th>수량</th><th>평균단가</th><th>투자금</th><th>진입 시각</th></tr></thead>
      <tbody>{positions.map(p => <tr key={`${p.strategy_id}-${p.symbol}`}>
        <td className="font-bold text-foreground">{p.symbol}</td>
        <td className="tabular-nums">{p.quantity ?? "-"}</td>
        <td className="tabular-nums">{formatUsd(p.average_price)}</td>
        <td className="tabular-nums">{formatUsd(p.cost_basis)}
          <span className="mt-0.5 block text-xs text-muted">{formatKrw(usdToDisplayKrw(p.cost_basis))}</span></td>
        <td>{kstTime(p.opened_at)}</td>
      </tr>)}</tbody></table></div>}
  </section>;
}

export function DailyPnl({ daily, trades }: { daily: DailyView | null; trades: StrategyTrade[] }) {
  const [open, setOpen] = useState(false);
  const rows = (daily?.rows || []).filter(r => r.strategies[STRATEGY_E]);
  return <section aria-labelledby="e-daily-title">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 id="e-daily-title" className="font-semibold">일별 손익</h2>
      <button type="button" className="btn-action-secondary-compact" aria-expanded={open} aria-controls="e-trades-detail"
        onClick={() => setOpen(v => !v)}>{open ? "거래 내역 닫기" : "거래 내역 보기"}</button>
    </div>
    {!rows.length ? <EmptyState title="아직 일별 손익 기록이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>날짜</th><th>일 손익</th><th>거래</th><th>자산</th></tr></thead>
      <tbody>{rows.map(r => { const s = r.strategies[STRATEGY_E]; return <tr key={r.session}>
        <td className="font-medium text-foreground">{r.session.slice(5).replace("-", "/")}</td>
        <td><SignedMoneyValue value={s.pnl}/></td>
        <td>{s.trades}건</td>
        <td>{s.equity == null ? "-" : <>{formatUsd(s.equity)}
          <span className="mt-0.5 block text-xs text-muted">{formatKrw(usdToDisplayKrw(s.equity))}</span></>}</td>
      </tr>; })}</tbody></table></div>}
    {open && <div id="e-trades-detail" className="mt-5"><StrategyTrades trades={trades}/></div>}
  </section>;
}
