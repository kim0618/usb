"use client";

import { useState } from "react";
import { DailyPnl } from "@/components/daily-pnl";
import { StrategyPicker, StrategyRecord } from "@/components/strategy-record";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { isArchived, strategiesApi, type DailyView } from "@/lib/strategies";

/** An archived strategy's column, picker entry and trade lines are left out. Its session P&L was
 *  0 for every official session before it was archived, so the backend's per-session total is
 *  unchanged by dropping it and is shown as sent. */
function withoutArchived(view: DailyView): DailyView {
  return { ...view,
    strategies: view.strategies.filter(s => !isArchived(s.strategy_id)),
    rows: view.rows.map(row => ({ ...row, trades: row.trades.filter(t => !isArchived(t.strategy_id)) })) };
}

/** The strategy screen. One page, no tab bar: how much each session made, then one strategy's
 *  own record when you pick it. Comparison, gates and valuation research are not here. */
export default function StrategyScreen() {
  const state = useApi(() => strategiesApi.daily().then(withoutArchived), 60_000);
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
