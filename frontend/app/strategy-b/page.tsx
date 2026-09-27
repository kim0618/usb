"use client";

import { ClosedStrategyNotice } from "@/components/ae-operations";
import { StrategyTabs } from "@/components/section-tabs";
import { PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { strategiesApi } from "@/lib/strategies";

const STRATEGY_B = "STRATEGY_B";

/** Strategy B's old result screen. B is closed research, so this route no longer renders B's mock
 *  performance; it points to the research history instead. */
export default function StrategyBClosedResultPage() {
  const rows = useApi(strategiesApi.list, 300_000);
  const row = rows.data?.find(item => item.strategy_id === STRATEGY_B);
  return <>
    <StrategyTabs/>
    <PageHeader title={row ? `${row.display_name} · CLOSED` : "종결된 전략"}/>
    <ClosedStrategyNotice row={row} fallbackId={STRATEGY_B}/>
  </>;
}
