"use client";

import { MockBadge } from "@/components/mock-badge";
import { StrategyTabs } from "@/components/section-tabs";
import {
  StrategyBDailyPnl, StrategyBEquityCurve, StrategyBPerformance, StrategyBSetupPerformance,
} from "@/components/strategy-b-performance";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { STRATEGY_B_MOCK, strategyBSource } from "@/lib/strategy-b-source";

/** Strategy B's result screen: how the strategy performed.
 *
 *  The realtime scanner, open positions and today's trades are the operating view and live
 *  on /trading-b. Figures come from lib/strategy-b-source.ts (mock module). */
export default function StrategyBPerformancePage() {
  const performance = useApi(strategyBSource.performance);
  const summary = useApi(strategyBSource.summary);
  const trades = useApi(strategyBSource.trades);
  const curve = useApi(strategyBSource.equityCurve);

  const header = <PageHeader title="전략 B · 성과" description="실시간 운용 화면은 트레이딩 영역의 전략 B 탭에서 확인합니다."
    actions={STRATEGY_B_MOCK ? <MockBadge/> : undefined}/>;

  if (performance.loading) return <><StrategyTabs/>{header}<LoadingState/></>;
  if (!performance.data) return <><StrategyTabs/>{header}<ErrorState message={performance.error || "전략 B 성과 조회 실패"} retry={performance.refresh}/></>;

  return <>
    <StrategyTabs/>
    {header}
    <StrategyBPerformance performance={performance.data}/>
    <StrategyBEquityCurve curve={curve.data ?? []} baseline={summary.data?.initial_capital_krw ?? 0}/>
    <StrategyBSetupPerformance trades={trades.data ?? []}/>
    <StrategyBDailyPnl curve={curve.data ?? []} baseline={summary.data?.initial_capital_krw ?? 0}/>
  </>;
}
