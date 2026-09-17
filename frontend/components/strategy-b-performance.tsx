import type { ReactNode } from "react";
import { EquityCurve } from "@/components/equity-curve";
import { Money } from "@/components/money";
import { EmptyState, InfoTooltip } from "@/components/ui";
import { numericToneClass, plainPct, setupBreakdown, setupLabel, signedPct, signedR } from "@/lib/strategy-b";
import { dailyRows } from "@/lib/strategy-compare";
import type { EquityPoint, StrategyPerformance, StrategyTrade } from "@/types/strategy-b";

const dateLabel = (date: string) => date.slice(5).replace("-", "/");

const StatCell = ({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: string; hint?: string }) =>
  <div className="bg-surface p-4">
    <p className="label">{label}{hint && <InfoTooltip label={label} text={hint}/>}</p>
    <div className={`mt-2 text-lg font-bold tabular-nums ${tone ?? "text-foreground"}`}>{value}</div>
  </div>;

/** Period performance of Strategy B. The same figures the compare screen reads. */
export function StrategyBPerformance({ performance }: { performance: StrategyPerformance }) {
  return <section aria-labelledby="strategy-b-performance-title" className="mb-7">
    <h2 id="strategy-b-performance-title" className="mb-3 font-semibold">전략 성과</h2>
    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-line bg-line shadow-panel sm:grid-cols-3 lg:grid-cols-5">
      <StatCell label="총 수익률" value={signedPct(performance.total_return_pct)} tone={numericToneClass(performance.total_return_pct)}/>
      <StatCell label="순손익" value={<Money krw={performance.net_pnl_krw} signed/>} tone={numericToneClass(performance.net_pnl_krw)}/>
      <StatCell label="매매 횟수" value={`${performance.trades}회`}/>
      <StatCell label="승률" value={plainPct(performance.win_rate_pct, 1)}/>
      <StatCell label="Profit Factor" value={performance.profit_factor.toFixed(2)} hint="총이익을 총손실로 나눈 값입니다."/>
      <StatCell label="기대값" value={signedR(performance.expectancy_r)} tone={numericToneClass(performance.expectancy_r)} hint="매매 1회당 기대 수익을 리스크 단위(R)로 나타냅니다."/>
      <StatCell label="최대 낙폭" value={plainPct(performance.max_drawdown_pct, 1)} tone={numericToneClass(performance.max_drawdown_pct)} hint="기간 중 고점 대비 최대 하락폭입니다."/>
      <StatCell label="평균 R" value={signedR(performance.average_r)} tone={numericToneClass(performance.average_r)}/>
      <StatCell label="평균 이익" value={<Money krw={performance.average_win_krw}/>} tone="text-success"/>
      <StatCell label="평균 손실" value={<Money krw={performance.average_loss_krw}/>} tone="text-danger"/>
    </div>
  </section>;
}

/** Strategy B's own equity line, drawn by the same chart the comparison screen uses. */
export function StrategyBEquityCurve({ curve, baseline }: { curve: EquityPoint[]; baseline: number }) {
  if (curve.length === 0) return null;
  return <section aria-labelledby="strategy-b-curve-title" className="mb-7">
    <h2 id="strategy-b-curve-title" className="mb-3 font-semibold">자산 곡선</h2>
    <EquityCurve curve={curve} baseline={baseline} labelA="Strategy A" labelB="Strategy B" only="B" height={200}/>
  </section>;
}

/** Daily result of the same period, read off the same curve so the two cannot disagree. */
export function StrategyBDailyPnl({ curve, baseline }: { curve: EquityPoint[]; baseline: number }) {
  const rows = dailyRows(curve, baseline, baseline);
  if (rows.length === 0) return null;
  return <section aria-labelledby="strategy-b-daily-title" className="mb-7">
    <h2 id="strategy-b-daily-title" className="mb-3 font-semibold">일별 손익</h2>
    <div className="table-wrap relative"><table className="analysis-table">
      <caption className="sr-only">전략 B의 일별 수익률과 손익</caption>
      <thead><tr><th scope="col">날짜</th><th scope="col" className="text-right">수익률</th><th scope="col" className="text-right">손익</th><th scope="col" className="text-right">자산</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={row.date} data-b-daily-row={row.date}>
        <td className="whitespace-nowrap font-medium text-foreground">{dateLabel(row.date)}</td>
        <td className={`text-right tabular-nums ${numericToneClass(row.b_return_pct)}`}>{signedPct(row.b_return_pct)}</td>
        <td className={`text-right tabular-nums ${numericToneClass(row.b_pnl_krw)}`}><Money krw={row.b_pnl_krw} signed size="cell"/></td>
        <td className="text-right tabular-nums text-foreground"><Money krw={curve[index].b_equity_krw} size="cell"/></td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

/** Which setup produced the result, grouped from the closed trades themselves. */
export function StrategyBSetupPerformance({ trades }: { trades: StrategyTrade[] }) {
  const rows = setupBreakdown(trades);
  return <section aria-labelledby="strategy-b-setup-title" className="mb-7">
    <h2 id="strategy-b-setup-title" className="mb-3 font-semibold">셋업별 성과</h2>
    {rows.length === 0 ? <EmptyState title="집계할 거래가 없습니다."/> : <div className="table-wrap relative"><table className="analysis-table">
      <caption className="sr-only">전략 B의 셋업별 성과</caption>
      <thead><tr><th scope="col">셋업</th><th scope="col" className="text-right">매매</th><th scope="col" className="text-right">승</th><th scope="col" className="text-right">승률</th><th scope="col" className="text-right">순손익</th><th scope="col" className="text-right">평균 R</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.setup} data-setup-row={row.setup}>
        <td className="whitespace-nowrap font-medium text-foreground" title={row.setup}>{setupLabel(row.setup)}</td>
        <td className="text-right tabular-nums">{row.trades}건</td>
        <td className="text-right tabular-nums">{row.wins}건</td>
        <td className="text-right tabular-nums">{plainPct((row.wins / row.trades) * 100, 1)}</td>
        <td className={`text-right tabular-nums ${numericToneClass(row.net_pnl_krw)}`}><Money krw={row.net_pnl_krw} signed size="cell"/></td>
        <td className={`text-right tabular-nums ${numericToneClass(row.average_r)}`}>{signedR(row.average_r)}</td>
      </tr>)}</tbody>
    </table></div>}
    <p className="mt-2 text-xs text-muted">당일 종료된 거래를 셋업별로 집계합니다. V1은 당일 고가 돌파와 첫 눌림목 두 가지 셋업만 사용합니다.</p>
  </section>;
}
