"use client";

import { useEffect, useState } from "react";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { StrategyTabs } from "@/components/section-tabs";
import { StrategyComparison } from "@/components/shadow-performance";
import { StrategyEvaluationHistory } from "@/components/strategy-evaluation-history";
import { api } from "@/lib/api";
import { periodRange, type PerformancePeriod } from "@/lib/shadow-performance";
import type { ShadowSummary } from "@/types/api";

export default function ShadowPage() {
  const [period, setPeriod] = useState<PerformancePeriod>("30d");
  const [summary, setSummary] = useState<ShadowSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [requestKey, setRequestKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(null);
    api.shadow(periodRange(period)).then(data => { if (active) setSummary(data); }).catch(() => { if (active) setError("전략 성과 요약 조회 실패"); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [period, requestKey]);

  // The evaluation history answers for finished entry sessions and is independent of the
  // shadow variant summary, so a failed summary never hides it.
  if (loading && !summary) return <><StrategyTabs /><PageHeader title="전략 성과" /><StrategyEvaluationHistory /><LoadingState /></>;
  if (!summary) return <><StrategyTabs /><PageHeader title="전략 성과" /><StrategyEvaluationHistory /><ErrorState message={error || "전략 성과 요약 조회 실패"} retry={() => setRequestKey(value => value + 1)} /></>;

  return <>
    <StrategyTabs />
    <PageHeader title="전략 성과" />
    <StrategyEvaluationHistory />
    {error && summary && <p className="mb-3 text-right text-xs text-danger" role="alert">{error}</p>}
    <StrategyComparison variants={summary.variants} period={period} loading={loading} onPeriodChange={setPeriod} />{summary.excluded_periods?.map(excluded => <p key={excluded.start} className="mt-2 text-xs text-muted">시스템 검증 기간({excluded.start} ~ {excluded.end})은 전략 성과 집계에서 제외됩니다.</p>)}
  </>;
}
