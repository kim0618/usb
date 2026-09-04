"use client";

import Link from "next/link";
import { useState } from "react";
import { AnalysisTabs } from "@/components/section-tabs";
import { useToast } from "@/components/toast";
import { EmptyState, ErrorState, InfoTooltip, LoadingState, MetricCard, Modal, PageHeader, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api";
import { formatDecisionStatus, quantScoreLabel, quantScoreTone, rvolLabel, rvolTone, signedMetricTone } from "@/lib/display";
import { compactUsd, kstTime as etTime, multiple, number, score, signedPercent } from "@/lib/format";
import type { Candidate } from "@/types/api";

const COLUMN_HELP = {
  score: "거래량, 시장 대비 강도, 거래 규모, 최근 흐름을 결합한 정량 종합점수입니다.",
  rvol: "평균 거래량 대비 현재 거래량 수준을 배수로 나타냅니다.",
  relativeStrength: "시장 벤치마크와 비교해 종목이 얼마나 강하거나 약하게 움직였는지 나타냅니다.",
  dollarVolume: "가격과 거래량을 기반으로 계산한 거래대금 규모입니다.",
  momentum: "최근 가격 움직임의 상승·하락 강도를 나타냅니다.",
} as const;

async function copy(text: string) {
  if (!navigator.clipboard) throw new Error("Clipboard is unavailable");
  await navigator.clipboard.writeText(text);
}

export default function CandidatesPage() {
  const scan = useApi(api.scanner);
  const research = useApi(api.research);
  const toast = useToast();
  const [prompt, setPrompt] = useState<string | null>(null);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadPrompt(preview: boolean, symbol?: string) {
    setBusy(true);
    try {
      const result = await api.prompt(symbol);
      if (preview) setPrompt(result.prompt);
      else {
        await copy(result.prompt);
        toast(symbol ? "상세 GPT 프롬프트를 복사했습니다." : "GPT 분석 프롬프트를 복사했습니다.");
      }
    } catch (error) {
      toast(error instanceof ApiError ? error.message : "프롬프트를 가져오지 못했습니다.", true);
    } finally {
      setBusy(false);
    }
  }

  if (scan.loading) return <><AnalysisTabs/><LoadingState/></>;
  if (!scan.data) return <><AnalysisTabs/><PageHeader title="오늘의 후보 종목" description="정량 분석 결과 중 상위 8개 종목입니다."/>{scan.error?.toLowerCase().includes("completed scanner run") ? <EmptyState title="아직 완료된 종목 분석이 없습니다." description="Scanner 분석이 완료되면 오늘의 TOP8 후보 종목이 여기에 표시됩니다."/> : <ErrorState message={scan.error || "Scanner 조회 실패"} retry={scan.refresh}/>}</>;

  const s = scan.data;
  const showCompany = s.top8.some(candidate => Boolean(candidate.company_name?.trim()));
  return <>
    <AnalysisTabs/>
    <PageHeader title="오늘의 후보 종목" description="정량 분석 결과 중 상위 8개 종목입니다." actions={<><button className="btn-action-secondary" disabled={busy} onClick={() => void loadPrompt(true)}>미리보기</button><button className="btn-action-primary" disabled={busy} onClick={() => void loadPrompt(false)}>GPT 프롬프트 복사</button></>}/>
    <p className="mb-5 flex flex-wrap gap-x-2 gap-y-1 text-xs text-muted"><span>기준 거래일 {s.run.trading_date}</span><span aria-hidden="true">·</span><span>분석 완료 {etTime(s.run.completed_at)}</span><span aria-hidden="true">·</span><span>전체 후보 {s.run.candidate_count}개</span><span aria-hidden="true">·</span><span>TOP{s.run.top8_count}</span><span aria-hidden="true">·</span><span>{s.run.score_version}</span></p>
    {s.top8.length ? <div className="table-wrap"><table><thead><tr>
      <th>순위</th><th>종목</th>{showCompany && <th>회사명</th>}
      <th>최근 흐름<InfoTooltip label="최근 흐름" text={COLUMN_HELP.momentum}/></th>
      <th>거래량 강도<InfoTooltip label="거래량 강도" text={COLUMN_HELP.rvol}/></th>
      <th>시장 대비<InfoTooltip label="시장 대비" text={COLUMN_HELP.relativeStrength}/></th>
      <th>종합 점수<InfoTooltip label="종합 점수" text={COLUMN_HELP.score}/></th>
      <th>거래 규모<InfoTooltip label="거래 규모" text={COLUMN_HELP.dollarVolume}/></th>
      <th>분석 상태</th>
    </tr></thead><tbody>{s.top8.map(candidate => {
      const researched = research.data?.candidates.find(item => item.symbol === candidate.symbol);
      const decision = researched?.human_decision?.decision;
      const status = decision ? formatDecisionStatus(decision) : researched ? "분석 완료" : "분석 전";
      return <tr key={candidate.symbol} className="cursor-pointer" onClick={() => setSelected(candidate)}>
        <td className={candidate.rank === 1 ? "font-semibold text-primary" : undefined}>#{candidate.rank}</td>
        <td className="font-bold text-foreground">{candidate.symbol}</td>
        {showCompany && <td className="max-w-56"><span className="block truncate" title={candidate.company_name || undefined}>{candidate.company_name || "-"}</span></td>}
        <td className={`whitespace-nowrap tone-text-${signedMetricTone(candidate.raw_metrics.momentum)}`}>{signedPercent(candidate.raw_metrics.momentum)}</td>
        <td className={`whitespace-nowrap tone-text-${rvolTone(candidate.raw_metrics.rvol)}`}>{multiple(candidate.raw_metrics.rvol)}<span className="hidden xl:inline"> · {rvolLabel(candidate.raw_metrics.rvol)}</span></td>
        <td className={`whitespace-nowrap tone-text-${signedMetricTone(candidate.raw_metrics.relative_strength)}`}>{signedPercent(candidate.raw_metrics.relative_strength)}</td>
        <td className={`whitespace-nowrap tone-text-${candidate.quant_score == null ? "neutral" : quantScoreTone(candidate.quant_score)}`}>{score(candidate.quant_score)}{candidate.quant_score != null && <span className="hidden xl:inline"> · {quantScoreLabel(candidate.quant_score)}</span>}</td>
        <td className="whitespace-nowrap">{compactUsd(candidate.raw_metrics.dollar_volume)}</td>
        <td><StatusBadge value={decision || (researched ? "RESEARCHED" : "PENDING")} label={status}/></td>
      </tr>;
    })}</tbody></table></div> : <EmptyState title="아직 완료된 종목 분석이 없습니다." description="Scanner 분석이 완료되면 오늘의 TOP8 후보 종목이 여기에 표시됩니다."/>}
    <div className="mt-5 flex justify-end"><Link href="/research" className="btn-action-secondary">GPT 분석 열기</Link></div>
    <Modal open={!!prompt} title="GPT 분석 프롬프트" onClose={() => setPrompt(null)}><pre className="max-h-[60vh] whitespace-pre-wrap rounded-lg bg-surface-alt p-4 text-xs leading-5 text-foreground-secondary">{prompt}</pre><button className="btn-action-primary mt-4" onClick={() => prompt && copy(prompt).then(() => toast("GPT 분석 프롬프트를 복사했습니다."))}>복사</button></Modal>
    <Modal open={!!selected} title={`${selected?.symbol || ""} · Quant 상세`} onClose={() => setSelected(null)}>{selected && <div className="space-y-5"><div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><MetricCard label="순위" value={`#${selected.rank}`}/><MetricCard label="점수" value={score(selected.quant_score)}/><MetricCard label="최근 종가" value={number(selected.latest_close)}/><MetricCard label="거래량" value={number(selected.latest_volume, 0)}/></div>{[["원본 지표", selected.raw_metrics], ["정규화 지표", selected.normalized_metrics], ["점수 기여도", selected.contributions]].map(([title, values]) => <section key={title as string}><h3 className="label mb-2">{title as string}</h3><div className="grid grid-cols-2 gap-2 rounded-lg bg-surface-alt p-3">{Object.entries(values as Record<string, number>).map(([key, value]) => <div key={key} className="flex justify-between text-xs"><span className="text-muted">{key}</span><span>{number(value, 4)}</span></div>)}</div></section>)}<button className="btn-action-primary" onClick={() => void loadPrompt(false, selected.symbol)}>상세 GPT 프롬프트 복사</button></div>}</Modal>
  </>;
}
