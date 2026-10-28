"use client";

import Link from "next/link";
import { ErrorState, LoadingState } from "@/components/ui";
import { DashboardStrategyCards, DashboardStrategyICard } from "@/components/dashboard-strategy-card";
import { useApi } from "@/hooks/use-api";
import { ApiError } from "@/lib/api";
import { dashboardStrategies, STRATEGY_E, STRATEGY_H } from "@/lib/strategies";
import { strategyIApi, STRATEGY_I_TAB, type IPaperView } from "@/lib/strategy-i";

const DETAIL_HREF: Readonly<Record<string, string>> = {
  [STRATEGY_E]: "/strategy-e",
  [STRATEGY_H]: "/trading-h",
};

/** The screens the 전략 menu used to hold. Their routes and data are unchanged; this row is how
 *  they are reached now. Strategy H's research detail is linked from Trading H. */
const ANALYSIS_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: "/strategy-compare", label: "전략 성과 비교" },
  { href: "/daily", label: "전략별 일별 손익" },
  { href: "/strategy-history", label: "연구 이력" },
];

/** Strategy I is not in the registry, so its card reads its own paper API. A failure there becomes
 *  that card's ISSUE and never blanks the registry cards. */
async function loadDashboard() {
  const [bundles, i] = await Promise.all([
    dashboardStrategies(),
    strategyIApi.paper().then(
      (view): { view: IPaperView | null; error: string | null } => ({ view, error: null }),
      (e: unknown) => ({ view: null, error: e instanceof ApiError ? e.message : "Strategy I 상태 조회 실패" })),
  ]);
  return { bundles, i };
}

/** The operating dashboard: every enabled strategy in the registry, each with its own account.
 *
 *  Figures come from the per-strategy API and are never summed across strategies. Every card has
 *  the same twelve fields; a value the ledger does not record is "-". The equity curves and the
 *  strategy comparison live on their own screen (/strategy-compare), not here. Strategy I follows
 *  the registry cards in the same grid. */
export default function DashboardPage() {
  const state = useApi(loadDashboard, 60_000);

  if (state.loading) return <LoadingState/>;
  if (!state.data) return <ErrorState message={state.error || "전략 현황 조회 실패"} retry={state.refresh}/>;

  return <div className="trading-screen">
    <DashboardStrategyCards bundles={state.data.bundles} hrefs={DETAIL_HREF}>
      <DashboardStrategyICard view={state.data.i.view} error={state.data.i.error} href={STRATEGY_I_TAB.href}/>
    </DashboardStrategyCards>
    <nav aria-label="전략 분석" className="mt-6 flex flex-wrap gap-2" data-analysis-links="">
      {ANALYSIS_LINKS.map(link => <Link key={link.href} href={link.href} className="btn-action-secondary-compact">{link.label}</Link>)}
    </nav>
  </div>;
}
