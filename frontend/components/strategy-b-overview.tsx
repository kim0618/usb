import { Money } from "@/components/money";
import { MetricCard, StatusBadge } from "@/components/ui";
import { formatMarketSession } from "@/lib/display";
import { etTime, moneyText } from "@/lib/format";
import { RUN_STATE_LABELS, numericToneClass } from "@/lib/strategy-b";
import type { StrategyAccount, StrategySummary } from "@/types/strategy-b";

/** Same stroke language as the Strategy A trading summary icons. Decorative only. */
const SummaryIcon = ({ type }: { type: "wallet" | "layers" | "cash" | "trend" | "pulse" }) => {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (type === "wallet") return <svg viewBox="0 0 24 24" {...common}><path d="M4 7.5h15a1 1 0 0 1 1 1v10H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2h12v4"/><path d="M16 12h4v3h-4a1.5 1.5 0 0 1 0-3Z"/></svg>;
  if (type === "layers") return <svg viewBox="0 0 24 24" {...common}><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></svg>;
  if (type === "cash") return <svg viewBox="0 0 24 24" {...common}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9a2 2 0 0 1-2 2v2a2 2 0 0 1 2 2M17 9a2 2 0 0 0 2 2v2a2 2 0 0 0-2 2"/><circle cx="12" cy="12" r="2.5"/></svg>;
  if (type === "trend") return <svg viewBox="0 0 24 24" {...common}><path d="M4 18V6M4 18h16M7 15l4-4 3 2 5-6"/><path d="M16 7h3v3"/></svg>;
  return <svg viewBox="0 0 24 24" {...common}><path d="M3 12h4l2-6 4 12 2-6h6"/><circle cx="12" cy="12" r="9" opacity=".35"/></svg>;
};

/** The account row of the Strategy B trading screen, laid out like Strategy A's.
 *  Cumulative return and the rest of the period statistics live on the performance
 *  screen; an operating screen answers what is held and what today did. */
export function StrategyBTradingOverview({ summary, account }: { summary: StrategySummary; account: StrategyAccount }) {
  return <>
    <section aria-label="전략 B 계좌 요약" className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <MetricCard accent="primary" icon={<SummaryIcon type="wallet"/>} label="현재 자산" meta="계좌"
        value={<Money krw={account.equity_krw} size="card"/>} detail={`초기 자본 ${moneyText(summary.initial_capital_krw)}`}/>
      <MetricCard accent="indigo" icon={<SummaryIcon type="layers"/>} label="투자 중" meta="포지션"
        value={<Money krw={account.invested_krw} size="card"/>} detail={`보유 ${summary.open_positions}종목`}/>
      <MetricCard accent="gold" icon={<SummaryIcon type="cash"/>} label="보유 현금" meta="주문 가능"
        value={<Money krw={account.cash_krw} size="card"/>} detail="신규 진입에 사용 가능한 금액"/>
      <MetricCard accent="green" icon={<SummaryIcon type="trend"/>} label="평가 손익" meta="평가"
        value={<span className={numericToneClass(account.unrealized_pnl_krw)}><Money krw={account.unrealized_pnl_krw} signed size="card"/></span>}
        detail="보유 포지션의 미실현 손익"/>
      <MetricCard accent="bluegreen" icon={<SummaryIcon type="pulse"/>} label="오늘 실현 손익" meta="당일"
        value={<span className={numericToneClass(account.realized_pnl_krw)}><Money krw={account.realized_pnl_krw} signed size="card"/></span>}
        detail="당일 종료된 거래의 합계"/>
    </section>
    <div className="mb-7 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-line bg-surface-alt px-4 py-2.5 text-xs text-muted">
      <span>전략 상태</span><StatusBadge value={summary.status} label={RUN_STATE_LABELS[summary.status]}/>
      <span className="hidden h-4 w-px bg-line sm:block" aria-hidden="true"/>
      <span>시장 세션</span><StatusBadge value={summary.market_session} label={formatMarketSession(summary.market_session)}/>
      <span className="hidden h-4 w-px bg-line sm:block" aria-hidden="true"/>
      <span>실시간 감시 <span className="font-medium tabular-nums text-foreground-secondary">{summary.realtime_pool.toLocaleString()}종목</span> · 후보 <span className="font-medium tabular-nums text-foreground-secondary">{summary.candidates}종목</span></span>
      <span className="hidden h-4 w-px bg-line sm:block" aria-hidden="true"/>
      <span>기준 시각 <span className="font-medium tabular-nums text-foreground-secondary">{etTime(summary.as_of)}</span></span>
    </div>
  </>;
}
