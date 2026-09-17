"use client";

import Link from "next/link";
import { RecentActivity, StrategyCards, TodaySummary } from "@/components/dashboard-summary";
import { MockBadge } from "@/components/mock-badge";
import { EquityCurve } from "@/components/equity-curve";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { DASHBOARD_MOCK, dashboardSource } from "@/lib/dashboard-source";

/** The integrated dashboard: where A and B stand right now, and nothing more.
 *
 *  Market, system and broker mode are not repeated here - the global header already shows
 *  them. Every strategy figure is mock and is composed in lib/dashboard-source.ts from the
 *  mocks the detail screens render. */
export default function DashboardPage() {
  const snapshot = useApi(dashboardSource.snapshot);

  const header = <PageHeader title="대시보드" description="Strategy A와 Strategy B의 현재 상태와 성과를 확인합니다."
    actions={DASHBOARD_MOCK ? <MockBadge label="DEMO DATA"/> : undefined}/>;

  if (snapshot.loading) return <>{header}<LoadingState/></>;
  if (!snapshot.data) return <>{header}<ErrorState message={snapshot.error || "대시보드 요약 조회 실패"} retry={snapshot.refresh}/></>;

  const data = snapshot.data;
  return <div className="trading-screen">
    {header}
    {DASHBOARD_MOCK && <p className="mb-5 text-xs text-muted">전략 성과 수치는 화면 검토용 예시입니다. 시장 · 시스템 · 운영 모드는 상단 헤더에서 확인합니다.</p>}
    <StrategyCards snapshot={data}/>
    <section aria-labelledby="dashboard-curve-title" className="mb-7">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="dashboard-curve-title" className="font-semibold">전략 자산 추이</h2>
        <Link href="/strategy-compare" className="btn-action-secondary-compact">A/B 상세 비교</Link>
      </div>
      <EquityCurve curve={data.equity_curve} baseline={data.a.initial_capital_krw} labelA="Strategy A" labelB="Strategy B" height={180}/>
    </section>
    <TodaySummary snapshot={data}/>
    <RecentActivity events={data.events}/>
  </div>;
}
