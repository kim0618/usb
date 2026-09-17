"use client";

import { MockBadge } from "@/components/mock-badge";
import { TradingTabs } from "@/components/section-tabs";
import { StrategyBTradingOverview } from "@/components/strategy-b-overview";
import { StrategyBPositions, StrategyBTrades } from "@/components/strategy-b-portfolio";
import { StrategyBScanner } from "@/components/strategy-b-scanner";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { STRATEGY_B_MOCK, strategyBSource } from "@/lib/strategy-b-source";

/** Strategy B's operating screen: what it is watching, what it holds, what it closed today.
 *
 *  Results analysis lives on /strategy-b. Every figure comes from lib/strategy-b-source.ts,
 *  which resolves the mock module; no Backend call, no Kiwoom connection, no socket and no
 *  order path is reachable from here. */
export default function TradingBPage() {
  const summary = useApi(strategyBSource.summary);
  const account = useApi(strategyBSource.account);
  const candidates = useApi(strategyBSource.candidates);
  const positions = useApi(strategyBSource.positions);
  const trades = useApi(strategyBSource.trades);

  const header = <PageHeader title="전략 B · 실시간 모멘텀" actions={STRATEGY_B_MOCK ? <MockBadge/> : undefined}/>;

  if (summary.loading || account.loading) return <><TradingTabs/>{header}<LoadingState/></>;
  if (!summary.data || !account.data) return <><TradingTabs/>{header}<ErrorState message={summary.error || account.error || "전략 B 운용 상태 조회 실패"} retry={summary.refresh}/></>;

  return <div className="trading-screen">
    <TradingTabs/>
    {header}
    <StrategyBTradingOverview summary={summary.data} account={account.data}/>
    <StrategyBScanner candidates={candidates.data ?? []}/>
    <StrategyBPositions positions={positions.data ?? []}/>
    <StrategyBTrades trades={trades.data ?? []}/>
  </div>;
}
