"use client";

import { GatePanel, PerformanceTable, PortfolioPanel } from "@/components/ae-operations";
import { StrategyTabs } from "@/components/section-tabs";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { strategiesApi } from "@/lib/strategies";

async function load() {
  const [rows, board, portfolio] = await Promise.all([
    strategiesApi.list(), strategiesApi.performance(), strategiesApi.portfolio(),
  ]);
  return { rows, board, portfolio };
}

/** Strategy A and Strategy E side by side, from their own paper books, plus the A+E portfolio view.
 *
 *  Before 2026-09-27 this route compared A with the closed Strategy B from a mock module. It now
 *  reads GET /strategies/performance and /strategies/portfolio; the old A/B components stay in the
 *  repository for their tests but no screen renders them. */
export default function StrategyComparePage() {
  const state = useApi(load, 60_000);
  const header = <PageHeader title="A/E 성과 비교" description="공식 Paper(V1)만 게이트와 합산에 쓰입니다. 레거시(V0) 기록은 맨 아래에 참고용으로 따로 둡니다."/>;
  if (state.loading) return <><StrategyTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><StrategyTabs/>{header}<ErrorState message={state.error || "A/E 성과 조회 실패"} retry={state.refresh}/></>;
  const { rows, board, portfolio } = state.data;
  return <>
    <StrategyTabs/>
    {header}
    <PerformanceTable board={board} rows={rows} book="official"/>
    <GatePanel gate={board.gate} rows={rows}/>
    <PortfolioPanel view={portfolio} rows={rows}/>
    <PerformanceTable board={board} rows={rows} book="legacy"/>
  </>;
}
