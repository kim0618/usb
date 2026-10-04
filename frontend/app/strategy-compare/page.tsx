"use client";

import { useState } from "react";
import Link from "next/link";
import { GatePanel, PerformanceTable, PortfolioPanel, StrategyBookPanel, StrategySelector } from "@/components/ae-operations";
import { HCohortTable, HEvaluationPanel, HSummary } from "@/components/h-forward";
import { StrategyTabs } from "@/components/section-tabs";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { ALL_STRATEGIES, STRATEGY_A, STRATEGY_E, STRATEGY_H, strategiesApi } from "@/lib/strategies";

async function load() {
  const [rows, board, portfolio] = await Promise.all([
    strategiesApi.list(), strategiesApi.performance(), strategiesApi.portfolio(),
  ]);
  return { rows, board, portfolio };
}

/** Every operating strategy side by side, each from its own book, plus the A+E portfolio view.
 *
 *  The selector is ALL / A / E / H and drives the whole screen, not only the metrics table. Under
 *  ALL the strategies get a column each, A+E additionally get their Combined column, and the
 *  portfolio simulation is shown; selecting one strategy narrows to it and opens its own book
 *  (holdings, closed trades, equity) inline, so comparing A with E does not mean leaving this page.
 *  Strategy H brings its decision panels instead of a book, because it holds no capital, and is
 *  never summed with A or E.
 *
 *  Before 2026-09-27 this route compared A with the closed Strategy B from a mock module. It now
 *  reads GET /strategies/performance and /strategies/portfolio; the old A/B components stay in the
 *  repository for their tests but no screen renders them. */
export default function StrategyComparePage() {
  const state = useApi(load, 60_000);
  const [strategy, setStrategy] = useState<string>(ALL_STRATEGIES);
  const header = <PageHeader title="전략 성과 비교"
    description="A·E는 공식 Paper(V1) 장부로, H는 forward shadow 결정으로 봅니다. 레거시(V0) 기록은 맨 아래에 참고용으로 따로 둡니다."/>;
  if (state.loading) return <><StrategyTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><StrategyTabs/>{header}<ErrorState message={state.error || "전략 성과 조회 실패"} retry={state.refresh}/></>;
  const { rows, board, portfolio } = state.data;
  const all = strategy === ALL_STRATEGIES;
  const showH = all || strategy === STRATEGY_H;
  const h = board.h_forward;
  // A capital-book strategy selected on its own: show that strategy's own book below its column.
  const selected = all ? undefined : rows.find(r => r.strategy_id === strategy);
  const book = selected && selected.strategy_id !== STRATEGY_H ? selected : undefined;
  return <>
    <StrategyTabs/>
    {header}
    <StrategySelector rows={rows} value={strategy} onChange={setStrategy}/>
    <PerformanceTable board={board} rows={rows} book="official" strategy={strategy}/>
    {book && <StrategyBookPanel key={book.strategy_id} row={book}/>}
    {showH && h && <>
      <HSummary view={h}/>
      <HEvaluationPanel evaluation={h.evaluation}/>
      <HCohortTable rows={h.rows}/>
      <p className="mb-7 text-xs text-muted">
        종목별 D3~D6 근거와 결정 이력은{" "}
        <Link href="/strategy-h" className="text-primary hover:underline">H 포워드 섀도 화면</Link>에 있습니다.
      </p>
    </>}
    <GatePanel gate={board.gate} rows={rows} board={board} strategy={strategy}/>
    {all && <PortfolioPanel view={portfolio} rows={rows}/>}
    <PerformanceTable board={board} rows={rows} book="legacy" strategy={strategy}/>
  </>;
}
