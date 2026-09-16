"use client";

import { useEffect, useState } from "react";
import { EmptyState } from "@/components/ui";
import { api } from "@/lib/api";
import { formatStrategyPhase } from "@/lib/display";
import type { EntryFact } from "@/lib/entry-board";
import { tradingDate } from "@/lib/format";
import {
  candidateDetail, candidateFacts, statusMeta, summaryPnlLabel, summaryResultLabel, summaryTone,
} from "@/lib/strategy-history";
import type {
  StrategyEvaluationCandidate, StrategyEvaluationDetail, StrategyEvaluationSummary,
} from "@/types/api";

function StatusChip({ status }: { status: StrategyEvaluationCandidate["final_status"] }) {
  const meta = statusMeta(status);
  return <span data-evaluation-status={status} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-bold tone-${meta.tone}`}><span aria-hidden="true">{meta.icon}</span>{meta.label}</span>;
}

function FactList({ facts }: { facts: EntryFact[] }) {
  if (facts.length === 0) return <p className="text-xs text-muted">기록된 관측값이 없습니다.</p>;
  return <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs sm:grid-cols-[auto_1fr_auto_1fr]">
    {facts.map(([label, value]) => <div key={label} className="contents"><dt className="text-muted">{label}</dt><dd className="text-foreground">{value}</dd></div>)}
  </dl>;
}

function CandidateRows({ detail }: { detail: StrategyEvaluationDetail }) {
  if (detail.candidates.length === 0) return <p className="px-4 py-3 text-xs text-muted">이 날짜의 채택 후보 기록이 없습니다.</p>;
  return <div className="space-y-2 px-4 py-3">
    {detail.source === "LEGACY_STATE" && <p className="text-xs text-muted">이 세션은 후보별 평가 기록 도입 이전이라, 저장된 전략 상태에서 복원 가능한 값만 표시합니다.</p>}
    <table className="w-full text-left text-xs">
      <caption className="sr-only">{tradingDate(detail.trading_date)} 후보별 평가 결과</caption>
      <thead><tr><th scope="col">순위</th><th scope="col">종목</th><th scope="col">결과</th><th scope="col">사유</th><th scope="col">단계</th></tr></thead>
      <tbody>{detail.candidates.map(row => <tr key={row.scanner_candidate_id} className="align-top">
        <td className="whitespace-nowrap">{row.rank == null ? "—" : `#${row.rank}`}</td>
        <td className="whitespace-nowrap font-bold text-foreground">{row.symbol}</td>
        <td><StatusChip status={row.final_status}/></td>
        <td>
          <p className="text-foreground-secondary">{candidateDetail(row)}</p>
          {row.final_reason && <p className="font-mono text-[10px] text-muted">{row.final_reason}</p>}
          <div className="mt-1"><FactList facts={candidateFacts(row)}/></div>
        </td>
        <td className="whitespace-nowrap text-muted">{row.last_phase ? formatStrategyPhase(row.last_phase) : "—"}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function DayRow({ summary, open, detail, loading, error, onToggle }: {
  summary: StrategyEvaluationSummary; open: boolean; detail: StrategyEvaluationDetail | null;
  loading: boolean; error: string | null; onToggle: () => void;
}) {
  const panelId = `evaluation-${summary.trading_date}`;
  return <>
    <tr className={open ? "bg-surface-alt" : ""}>
      <td className="whitespace-nowrap">
        <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={panelId} className="font-semibold text-foreground hover:text-primary">
          <span aria-hidden="true" className="mr-1 text-muted">{open ? "▾" : "▸"}</span>{tradingDate(summary.trading_date)}
        </button>
      </td>
      <td>{summary.approved_count}</td>
      <td>{summary.premarket_pass_count}</td>
      <td>{summary.approved_count - summary.gap_rejected_count - summary.volume_rejected_count}</td>
      <td>{summary.or_ready_count}</td>
      <td>{summary.signal_count}</td>
      <td>{summary.filled_count}</td>
      <td><span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-bold tone-${summaryTone(summary)}`}>{summaryResultLabel(summary)}</span></td>
      <td className="whitespace-nowrap">{summaryPnlLabel(summary)}</td>
    </tr>
    {open && <tr id={panelId}><td colSpan={9} className="border-t border-line-subtle p-0">
      {loading && <p className="px-4 py-3 text-xs text-muted" role="status">후보별 결과를 불러오는 중…</p>}
      {error && <p className="px-4 py-3 text-xs text-danger" role="alert">{error}</p>}
      {detail && <CandidateRows detail={detail}/>}
    </td></tr>}
  </>;
}

/** Finished entry sessions, newest first. Every number is a stored outcome count;
 *  this screen never re-derives a reason, a gap, or a threshold of its own. */
export function StrategyEvaluationHistory() {
  const [summaries, setSummaries] = useState<StrategyEvaluationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, StrategyEvaluationDetail>>({});
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let active = true;
    api.strategyEvaluations().then(data => { if (active) setSummaries(data); })
      .catch(() => { if (active) setError("전략 평가 이력 조회 실패"); });
    return () => { active = false; };
  }, []);

  const toggle = (day: string) => {
    if (open === day) { setOpen(null); return; }
    setOpen(day); setDetailError(null);
    if (details[day]) return;
    setDetailLoading(true);
    api.strategyEvaluationDetail(day)
      .then(data => setDetails(current => ({ ...current, [day]: data })))
      .catch(() => setDetailError("후보별 평가 결과 조회 실패"))
      .finally(() => setDetailLoading(false));
  };

  return <section aria-labelledby="strategy-evaluation-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id="strategy-evaluation-title" className="font-semibold">날짜별 전략 평가</h2>
      <p className="text-xs text-muted">채택 후보가 어느 단계에서 제외됐는지, 장 종료 후 확정된 기록입니다.</p>
    </div>
    {error && <p className="mb-2 text-xs text-danger" role="alert">{error}</p>}
    {summaries === null && !error && <p className="text-xs text-muted" role="status">불러오는 중…</p>}
    {summaries !== null && summaries.length === 0 && <EmptyState title="아직 평가된 진입 세션이 없습니다." description="채택 후보가 있는 진입 세션이 종료되면 날짜별 결과가 이곳에 쌓입니다."/>}
    {summaries !== null && summaries.length > 0 && <div className="table-wrap"><table>
      <caption className="sr-only">최근 진입 세션의 후보 단계별 통과 수와 결과</caption>
      <thead><tr>
        <th scope="col">날짜</th><th scope="col">채택</th><th scope="col">프리마켓 통과</th>
        <th scope="col" title="Gap·거래량 기준으로 제외되지 않은 후보 수입니다.">게이트 잔존</th>
        <th scope="col">OR 완성</th><th scope="col">신호</th><th scope="col">체결</th>
        <th scope="col">결과</th><th scope="col">손익</th>
      </tr></thead>
      <tbody>{summaries.map(summary => <DayRow key={summary.trading_date} summary={summary}
        open={open === summary.trading_date} detail={details[summary.trading_date] ?? null}
        loading={detailLoading && open === summary.trading_date}
        error={open === summary.trading_date ? detailError : null}
        onToggle={() => toggle(summary.trading_date)}/>)}</tbody>
    </table></div>}
  </section>;
}
