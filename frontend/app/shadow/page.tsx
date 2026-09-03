"use client";

import { useEffect, useState } from "react";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { StrategyComparison } from "@/components/shadow-performance";
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

  if (loading && !summary) return <LoadingState />;
  if (!summary) return <ErrorState message={error || "전략 성과 요약 조회 실패"} retry={() => setRequestKey(value => value + 1)} />;

  return <>
    <PageHeader title="전략 성과" />
    {error && summary && <p className="mb-3 text-right text-xs text-danger" role="alert">{error}</p>}
    <StrategyComparison variants={summary.variants} period={period} loading={loading} onPeriodChange={setPeriod} />
  </>;
}
