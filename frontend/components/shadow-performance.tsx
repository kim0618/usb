import { formatR, PERFORMANCE_PERIODS, rTone, STRATEGY_DESCRIPTIONS, winRate, type PerformancePeriod } from "@/lib/shadow-performance";
import type { ShadowVariant } from "@/types/api";

export function StrategyComparison({ variants, period, loading, onPeriodChange }: { variants: ShadowVariant[]; period: PerformancePeriod; loading: boolean; onPeriodChange: (period: PerformancePeriod) => void }) {
  const periodLabel = PERFORMANCE_PERIODS.find(item => item.value === period)?.label ?? "선택 기간";
  const hasResults = variants.some(item => item.candidate_paths > 0);
  return <section aria-labelledby="strategy-comparison-title">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 id="strategy-comparison-title" className="font-semibold">전략 비교</h2>
      <div className="flex items-center gap-2">
        {loading && <span className="text-xs text-muted" role="status">불러오는 중…</span>}
        <div className="inline-flex rounded-lg border border-line bg-surface-alt p-1" aria-label="성과 기간">
          {PERFORMANCE_PERIODS.map(option => <button key={option.value} type="button" aria-pressed={period === option.value} onClick={() => onPeriodChange(option.value)} className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${period === option.value ? "bg-primary-soft text-primary" : "text-muted hover:text-foreground"}`}>{option.label}</button>)}
        </div>
      </div>
    </div>
    {hasResults ? <div className="table-wrap"><table>
      <caption className="sr-only">선택 기간 A부터 E까지 전략 성과 비교</caption>
      <thead><tr><th>전략</th><th>설정</th><th>매매</th><th>미진입</th><th>승</th><th>패</th><th>승률</th><th title="각 거래의 리스크 단위(R) 기준 성과를 합산한 값입니다.">누적 R</th><th title="한 번의 종료 매매당 평균 R 성과입니다.">평균 R</th><th>익일 보유</th></tr></thead>
      <tbody>{variants.map(strategy => <tr className={strategy.control ? "bg-primary-soft" : ""} key={strategy.variant}>
        <td><span className="font-bold text-foreground">{strategy.variant}</span>{strategy.control && <span className="ml-2 inline-flex rounded-full border border-primary bg-primary-soft px-2 py-0.5 text-[10px] font-semibold text-primary">기준 전략</span>}</td>
        <td className="whitespace-nowrap">{STRATEGY_DESCRIPTIONS[strategy.variant] ?? strategy.variant}</td>
        <td>{strategy.trades}</td><td>{strategy.no_trade}</td><td>{strategy.wins}</td><td>{strategy.losses}</td><td>{winRate(strategy)}</td>
        <td className={`font-semibold ${rTone(strategy.net_r)}`}>{formatR(strategy.net_r)}</td><td className={rTone(strategy.avg_net_r)}>{formatR(strategy.avg_net_r)}</td><td>{strategy.overnight}</td>
      </tr>)}</tbody>
    </table></div> : <p className="rounded-xl border border-line bg-panel px-4 py-6 text-center text-sm text-muted">{periodLabel} 전략 성과 데이터가 없습니다.</p>}
  </section>;
}
