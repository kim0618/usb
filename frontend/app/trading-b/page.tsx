"use client";

import { ClosedStrategyNotice } from "@/components/ae-operations";
import { TradingTabs } from "@/components/section-tabs";
import { PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { strategiesApi } from "@/lib/strategies";

const STRATEGY_B = "STRATEGY_B";

/** Strategy B was closed on 2026-09-23 (NO_GO). This route used to render B's operating screen
 *  from a mock module, which read as a running strategy. It now says B is closed and shows no
 *  figure. B's research code, docs and artifacts are untouched. */
export default function StrategyBClosedTradingPage() {
  const rows = useApi(strategiesApi.list, 300_000);
  const row = rows.data?.find(item => item.strategy_id === STRATEGY_B);
  return <>
    <TradingTabs/>
    <PageHeader title={row ? `${row.display_name} · CLOSED` : "종결된 전략"}/>
    <ClosedStrategyNotice row={row} fallbackId={STRATEGY_B}/>
  </>;
}
