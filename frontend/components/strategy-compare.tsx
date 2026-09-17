"use client";

import { useState } from "react";
import { EquityCurve } from "@/components/equity-curve";
import { Money } from "@/components/money";
import { InfoTooltip } from "@/components/ui";
import { formatUsd, moneyText } from "@/lib/format";
import { holdingTime, numericToneClass, signedPct } from "@/lib/strategy-b";
import { comparisonMetrics, dailyRows } from "@/lib/strategy-compare";
import type { StrategyComparison, SymbolComparison } from "@/types/strategy-b";

const periodLabel = (date: string) => date.replace(/-/g, ".");
const dateLabel = (date: string) => date.slice(5).replace("-", "/");

/** Both strategies over the same period, side by side. The screen states the figures and
 *  stops there: no winner, no ranking, no recommendation is rendered anywhere below. */
function ComparisonKpi({ comparison }: { comparison: StrategyComparison }) {
  const rows = comparisonMetrics(comparison);
  return <section aria-labelledby="comparison-kpi-title" className="mb-7">
    <h2 id="comparison-kpi-title" className="mb-3 font-semibold">핵심 지표</h2>
    <div className="table-wrap relative"><table className="analysis-table">
      <caption className="sr-only">전략 A와 전략 B의 공통 기간 핵심 지표</caption>
      <thead><tr>
        <th scope="col">지표</th>
        <th scope="col" className="text-right">전략 A<span className="block text-[10px] font-normal normal-case tracking-normal text-muted">{comparison.a.label}</span></th>
        <th scope="col" className="text-right">전략 B<span className="block text-[10px] font-normal normal-case tracking-normal text-muted">{comparison.b.label}</span></th>
      </tr></thead>
      <tbody>{rows.map(row => <tr key={row.label} data-kpi-row={row.label}>
        <td className="whitespace-nowrap text-foreground-secondary">{row.label}{row.hint && <InfoTooltip label={row.label} text={row.hint}/>}</td>
        <td className="text-right font-medium tabular-nums text-foreground">{row.money ? <Money krw={row.money.a} signed={row.money.signed} size="cell"/> : row.a}</td>
        <td className="text-right font-medium tabular-nums text-foreground">{row.money ? <Money krw={row.money.b} signed={row.money.signed} size="cell"/> : row.b}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

function DailyCompare({ comparison }: { comparison: StrategyComparison }) {
  const [open, setOpen] = useState(false);
  const rows = dailyRows(comparison.equity_curve, comparison.a.initial_capital_krw, comparison.b.initial_capital_krw);
  return <section aria-labelledby="daily-compare-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id="daily-compare-title" className="font-semibold">일별 손익 비교</h2>
      <button type="button" className="btn-action-secondary-compact" aria-expanded={open} aria-controls="daily-compare-table" onClick={() => setOpen(value => !value)}>{open ? "일별 비교 닫기" : "일별 비교 보기"}</button>
    </div>
    {open && <div id="daily-compare-table" className="table-wrap relative"><table className="analysis-table">
      <caption className="sr-only">공통 기간의 일별 수익률과 손익</caption>
      <thead><tr>
        <th scope="col">날짜</th>
        <th scope="col" className="text-right">A 수익률</th><th scope="col" className="text-right">A 손익</th>
        <th scope="col" className="text-right">B 수익률</th><th scope="col" className="text-right">B 손익</th>
      </tr></thead>
      <tbody>{rows.map(row => <tr key={row.date} data-daily-row={row.date}>
        <td className="whitespace-nowrap font-medium text-foreground">{dateLabel(row.date)}</td>
        <td className={`text-right tabular-nums ${numericToneClass(row.a_return_pct)}`}>{signedPct(row.a_return_pct)}</td>
        <td className={`text-right tabular-nums ${numericToneClass(row.a_pnl_krw)}`}><Money krw={row.a_pnl_krw} signed size="cell"/></td>
        <td className={`text-right tabular-nums ${numericToneClass(row.b_return_pct)}`}>{signedPct(row.b_return_pct)}</td>
        <td className={`text-right tabular-nums ${numericToneClass(row.b_pnl_krw)}`}><Money krw={row.b_pnl_krw} signed size="cell"/></td>
      </tr>)}</tbody>
    </table></div>}
    {!open && <p className="text-xs text-muted">공통 기간 {rows.length}거래일의 일별 수익률과 손익을 자산 곡선과 같은 값으로 비교합니다.</p>}
  </section>;
}

function SymbolCompare({ entry }: { entry: SymbolComparison }) {
  const sides = [
    { key: "A" as const, label: "전략 A", side: entry.a },
    { key: "B" as const, label: "전략 B", side: entry.b },
  ];
  const facts: Array<[string, (side: SymbolComparison["a"]) => string, boolean]> = [
    ["진입가", side => formatUsd(side.entry_usd), false],
    ["진입 시각", side => `${side.entry_time} ET`, false],
    ["청산가", side => formatUsd(side.exit_usd), false],
    ["수익률", side => signedPct(side.return_pct), true],
    ["보유 시간", side => holdingTime(side.holding_minutes), false],
  ];
  return <div className="table-wrap relative"><table className="analysis-table">
    <caption className="sr-only">{entry.symbol} 종목의 전략별 매매 비교</caption>
    <thead><tr><th scope="col">{entry.symbol}</th>{sides.map(item => <th key={item.key} scope="col" className="text-right">{item.label}</th>)}</tr></thead>
    <tbody>{facts.map(([label, pick, toned]) => <tr key={label} data-symbol-fact={label}>
      <td className="whitespace-nowrap text-foreground-secondary">{label}</td>
      {sides.map(item => <td key={item.key} className={`text-right tabular-nums ${toned ? numericToneClass(item.side.return_pct) : "text-foreground"}`}>{pick(item.side)}</td>)}
    </tr>)}</tbody>
  </table></div>;
}

function SameSymbols({ comparison }: { comparison: StrategyComparison }) {
  const [symbol, setSymbol] = useState(comparison.same_symbols[0]?.symbol ?? "");
  const entry = comparison.same_symbols.find(item => item.symbol === symbol) ?? comparison.same_symbols[0] ?? null;
  return <section aria-labelledby="same-symbol-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id="same-symbol-title" className="font-semibold">같은 종목 비교</h2>
      <div className="flex flex-wrap gap-1" aria-label="비교 종목 선택">
        {comparison.same_symbols.map(item => <button key={item.symbol} type="button" aria-pressed={item.symbol === entry?.symbol} onClick={() => setSymbol(item.symbol)} className={`btn-compact ${item.symbol === entry?.symbol ? "btn-compact-active" : ""}`}>{item.symbol}</button>)}
      </div>
    </div>
    {entry ? <SymbolCompare entry={entry}/> : <p className="text-xs text-muted">두 전략이 같이 매매한 종목이 없습니다.</p>}
    <p className="mt-2 text-xs text-muted">공통 기간 중 두 전략이 모두 매매한 종목만 표시합니다.</p>
  </section>;
}

export function StrategyCompareView({ comparison }: { comparison: StrategyComparison }) {
  return <>
    <section aria-label="공통 기간" className="mb-7 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-surface-alt px-4 py-3">
      <p className="label">COMMON PERIOD</p>
      <p className="text-sm font-semibold tabular-nums text-foreground">{periodLabel(comparison.period_start)} ~ {periodLabel(comparison.period_end)}</p>
      <p className="text-xs text-muted">초기 자본 {moneyText(comparison.a.initial_capital_krw)}</p>
    </section>
    <ComparisonKpi comparison={comparison}/>
    <section aria-labelledby="equity-curve-title" className="mb-7">
      <h2 id="equity-curve-title" className="mb-3 font-semibold">자산 곡선</h2>
      <EquityCurve curve={comparison.equity_curve} baseline={comparison.a.initial_capital_krw} labelA="Strategy A" labelB="Strategy B"/>
    </section>
    <DailyCompare comparison={comparison}/>
    <SameSymbols comparison={comparison}/>
  </>;
}
