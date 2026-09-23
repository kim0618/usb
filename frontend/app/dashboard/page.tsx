"use client";

import Link from "next/link";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { EquitySparkline, StrategyCards } from "@/components/strategy-runtime";
import { useApi } from "@/hooks/use-api";
import { dashboardStrategies, STRATEGY_A, STRATEGY_E } from "@/lib/strategies";

const DETAIL_HREF: Readonly<Record<string, string>> = {
  [STRATEGY_A]: "/trading",
  [STRATEGY_E]: "/strategy-e",
};

/** The operating dashboard: every enabled strategy in the registry, each with its own account.
 *
 *  Figures come from the per-strategy API and are never summed across strategies. A strategy with
 *  no trade yet says so on its card instead of showing a zero. */
export default function DashboardPage() {
  const state = useApi(dashboardStrategies, 60_000);
  const header = <PageHeader title="대시보드" description="운영 중인 전략의 현재 상태와 각자의 성과를 확인합니다."/>;

  if (state.loading) return <>{header}<LoadingState/></>;
  if (!state.data) return <>{header}<ErrorState message={state.error || "전략 현황 조회 실패"} retry={state.refresh}/></>;

  return <div className="trading-screen">
    {header}
    <StrategyCards bundles={state.data} hrefs={DETAIL_HREF}/>
    <section aria-labelledby="dashboard-curve-title" className="mb-7">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="dashboard-curve-title" className="font-semibold">전략 자산 추이</h2>
        <Link href="/strategy-e" className="btn-action-secondary-compact">Strategy E 상세</Link>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {state.data.map(({ row, equity }) => <div key={row.strategy_id} className="panel p-4" data-equity={row.strategy_id}>
          <p className="label mb-2">{row.display_name}</p>
          <EquitySparkline equity={equity}/>
        </div>)}
      </div>
    </section>
  </div>;
}
