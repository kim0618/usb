"use client";

import { ResearchHistory } from "@/components/ae-operations";
import { StrategyTabs } from "@/components/section-tabs";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { strategiesApi } from "@/lib/strategies";

/** Every strategy the registry knows, active or closed. Closed research stays listed here so the
 *  same idea is not tried again unknowingly; it never appears among the operating tabs or cards. */
export default function StrategyHistoryPage() {
  const rows = useApi(strategiesApi.list, 300_000);
  const header = <PageHeader title="연구 이력" description="연구 판정과 운영 상태를 따로 봅니다. 종결된 전략의 연구 기록은 삭제하지 않습니다."/>;
  if (rows.loading) return <><StrategyTabs/>{header}<LoadingState/></>;
  if (!rows.data) return <><StrategyTabs/>{header}<ErrorState message={rows.error || "전략 목록 조회 실패"} retry={rows.refresh}/></>;
  return <><StrategyTabs/>{header}<ResearchHistory rows={rows.data}/></>;
}
