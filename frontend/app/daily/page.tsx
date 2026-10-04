"use client";

import { useState } from "react";
import { DailyPnl } from "@/components/daily-pnl";
import { StrategyPicker, StrategyRecord } from "@/components/strategy-record";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { strategiesApi } from "@/lib/strategies";

/** The strategy screen. One page, no tab bar: how much each session made, then one strategy's
 *  own record when you pick it. Comparison, gates and valuation research are not here. */
export default function StrategyScreen() {
  const state = useApi(() => strategiesApi.daily(), 60_000);
  const [picked, setPicked] = useState<string | null>(null);
  const header = <PageHeader title="전략"/>;
  if (state.loading) return <>{header}<LoadingState/></>;
  if (!state.data) return <>{header}<ErrorState message={state.error || "조회 실패"} retry={state.refresh}/></>;
  return <>
    {header}
    <DailyPnl view={state.data}/>
    <StrategyPicker strategies={state.data.strategies} picked={picked} onPick={setPicked}/>
    {picked && <StrategyRecord key={picked} strategyId={picked}
      label={state.data.strategies.find(s => s.strategy_id === picked)?.display_name || picked}/>}
  </>;
}
