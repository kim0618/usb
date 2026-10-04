"use client";

import Link from "next/link";
import { DailyPnl } from "@/components/daily-pnl";
import { StrategyTabs } from "@/components/section-tabs";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { strategiesApi } from "@/lib/strategies";

/** The operating landing screen: how much each session made or lost, and the trades behind it.
 *
 *  Everything heavier - the metric comparison, the frozen gate, the portfolio simulation, H's
 *  valuation cohort - is one click away rather than on this page, because it answers a different
 *  question than "오늘 얼마". */
export default function DailyPage() {
  const state = useApi(() => strategiesApi.daily(), 60_000);
  const header = <PageHeader title="일별 손익"
    description="세션마다 전략별로 얼마가 들어오고 나갔는지만 봅니다. 거래가 있던 날은 눌러서 내역을 펼칩니다."
    actions={<Link href="/strategy-compare" className="btn-action-secondary-compact">상세 분석</Link>}/>;
  if (state.loading) return <><StrategyTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><StrategyTabs/>{header}<ErrorState message={state.error || "일별 손익 조회 실패"} retry={state.refresh}/></>;
  return <><StrategyTabs/>{header}<DailyPnl view={state.data}/></>;
}
