"use client";

import Link from "next/link";
import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusBadge } from "@/components/ui";
import { TradingTabs } from "@/components/section-tabs";
import { useApi } from "@/hooks/use-api";
import { kstDate, kstTime } from "@/lib/format";
import {
  byRecentDecision, decisionReason, horizonProgress, latestDecision, nextEvaluation, observationState,
} from "@/lib/h-trading";
import { H_APPROVE, H_REJECT, H_WATCH, strategiesApi, type HForwardView } from "@/lib/strategies";

const DECISION_TONE: Readonly<Record<string, "success" | "warning" | "danger" | "neutral">> = {
  [H_APPROVE]: "success", [H_WATCH]: "warning", [H_REJECT]: "neutral",
};

const decisionBadge = (decision: string | null) =>
  decision ? <StatusBadge value={decision} tone={DECISION_TONE[decision] ?? "neutral"}/> : <span>-</span>;

const monthDay = (session: string) => session.slice(5).replace("-", "/");

/** Strategy H inside the Trading menu: is the forward shadow running, what is it watching, what did
 *  it last decide, how much evaluation has accumulated. Contract, launch and D7 detail stay on the
 *  research screen (/strategy-h). H holds no capital, so there is no asset or cash card here. */
export default function TradingHPage() {
  const state = useApi(strategiesApi.forward, 60_000);
  const header = <PageHeader title="Strategy H · Forward Shadow" description="미래 데이터 관찰 중 · 실제 주문 없음"
    actions={<Link href="/strategy-h" className="btn-action-secondary-compact">연구 상세 보기</Link>}/>;

  if (state.loading) return <><TradingTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><TradingTabs/>{header}<ErrorState message={state.error || "Strategy H 상태 조회 실패"} retry={state.refresh}/></>;

  const view = state.data;
  return <div className="trading-screen">
    <TradingTabs/>
    {header}
    <Kpis view={view}/>
    <Watching view={view}/>
    <RecentDecisions view={view}/>
    <ForwardProgress view={view}/>
  </div>;
}

function Kpis({ view }: { view: HForwardView }) {
  const counts = view.decision_counts;
  const recent = latestDecision(view.rows);
  return <section aria-label="관찰 요약" className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
    <MetricCard accent="primary" label="관찰 종목" value={String(view.rows.length)}/>
    <MetricCard accent="gold" label="WATCH" value={String(counts[H_WATCH] ?? 0)}/>
    <MetricCard accent="indigo" label="REJECT" value={String(counts[H_REJECT] ?? 0)}/>
    <MetricCard accent="green" label="APPROVE" value={String(counts[H_APPROVE] ?? 0)}/>
    <MetricCard accent="bluegreen" label="최근 결정" value={recent ?? "-"}/>
  </section>;
}

function Watching({ view }: { view: HForwardView }) {
  const rows = byRecentDecision(view.rows);
  return <section aria-labelledby="h-watch-title" className="mb-7">
    <h2 id="h-watch-title" className="mb-3 font-semibold">현재 관찰 종목</h2>
    {!rows.length ? <EmptyState title="현재 관찰 중인 종목이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>종목</th><th>현재 상태</th><th>결정일</th><th>다음 평가</th></tr></thead>
      <tbody>{rows.map(row => { const next = nextEvaluation(row); return <tr key={row.ticker}>
        <td className="font-bold text-foreground">{row.ticker}</td>
        <td>{decisionBadge(row.decision)}</td>
        <td>{row.decision_time ? kstDate(row.decision_time) : "-"}</td>
        <td>{next ? `${next.horizon} · ${monthDay(next.session)}` : "-"}</td>
      </tr>; })}</tbody></table></div>}
  </section>;
}

function RecentDecisions({ view }: { view: HForwardView }) {
  const rows = byRecentDecision(view.rows.filter(row => row.decision));
  return <section aria-labelledby="h-decisions-title" className="mb-7">
    <h2 id="h-decisions-title" className="mb-3 font-semibold">최근 결정</h2>
    {!rows.length ? <EmptyState title="아직 기록된 결정이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>결정</th><th>종목</th><th>결정 시각</th><th>현재 상태</th><th>사유</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.ticker}>
        <td>{decisionBadge(row.decision)}</td>
        <td className="font-bold text-foreground">{row.ticker}</td>
        <td>{kstTime(row.decision_time)}</td>
        <td>{observationState(row)}</td>
        <td className="text-foreground-secondary">{decisionReason(row)}</td>
      </tr>)}</tbody></table></div>}
  </section>;
}

function ForwardProgress({ view }: { view: HForwardView }) {
  const progress = horizonProgress(view.maturity);
  const reached = Object.values(view.evaluation.sample_reached || {});
  const done = reached.length > 0 && reached.every(Boolean);
  return <section aria-labelledby="h-forward-title">
    <h2 id="h-forward-title" className="mb-3 font-semibold">Forward 평가</h2>
    <div className="panel p-5" data-h-forward="">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4">
        {progress.map(p => <div key={p.horizon}>
          <dt className="label">{p.horizon}</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums text-foreground">{p.matured} / {p.total}</dd>
          <dd className="mt-0.5 text-xs text-muted">대기 {p.pending}</dd>
        </div>)}
      </dl>
      <p className="mt-4 text-sm text-foreground-secondary">{done ? "평가 표본이 모두 쌓였습니다." : "표본이 쌓이는 중입니다. 평가 완료 전에는 판정이 보류되는 것이 정상입니다."}</p>
    </div>
  </section>;
}
