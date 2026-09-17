"use client";

import { MockBadge } from "@/components/mock-badge";
import { StrategyTabs } from "@/components/section-tabs";
import { StrategyCompareView } from "@/components/strategy-compare";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { STRATEGY_B_MOCK, strategyCompareSource } from "@/lib/strategy-b-source";

/** Strategy A vs Strategy B over one common period, demo state.
 *
 *  The figures come from lib/strategy-b-source.ts (mock module). The common period is a
 *  stored field, so the real screen can replace it with the Backend's own calculation. */
export default function StrategyComparePage() {
  const comparison = useApi(strategyCompareSource.comparison);
  const header = <PageHeader title="전략 A/B 비교" description="동일 기간 · 동일 초기 자본 기준 성과 비교"
    actions={STRATEGY_B_MOCK ? <MockBadge/> : undefined}/>;

  if (comparison.loading) return <><StrategyTabs/>{header}<LoadingState/></>;
  if (!comparison.data) return <><StrategyTabs/>{header}<ErrorState message={comparison.error || "전략 비교 조회 실패"} retry={comparison.refresh}/></>;

  return <>
    <StrategyTabs/>
    {header}
    <StrategyCompareView comparison={comparison.data}/>
  </>;
}
