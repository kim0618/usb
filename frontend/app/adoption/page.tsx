"use client";

import { useState, type ReactNode } from "react";
import { AnalysisTabs } from "@/components/section-tabs";
import { ResearchDecisionControl } from "@/components/research-decision";
import { useToast } from "@/components/toast";
import { Drawer, EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api";
import { evidenceScoreLabel, evidenceScoreTone, formatDecisionStatus, formatQuoteMissingReason, researchCatalystLabel, researchCatalystTone, researchMomentumLabel, researchMomentumTone, researchRiskLabel, researchRiskTone, rvolLabel, rvolTone, signedMetricTone, type SemanticTone } from "@/lib/display";
import { formatUsd, multiple, signedPercent } from "@/lib/format";
import type { AdoptionClassification, AdoptionItem, ExtendedMarketQuote, ResearchDetail } from "@/types/api";

const status = (value: AdoptionClassification): { label: string; tone: SemanticTone } => value === "ADOPTION_CANDIDATE" ? { label: "채택 후보", tone: "success" } : value === "REVIEW_REQUIRED" ? { label: "검토 필요", tone: "warning" } : { label: "제외", tone: "danger" };
const deltaTone = (item: AdoptionItem): SemanticTone => item.rank_direction === "UP" ? "success" : item.rank_direction === "DOWN" ? "warning" : "neutral";
/** Backend `recommendation_rank` only. A missing rank renders as `-`; the frontend never derives one from row position. */
const rankLabel = (value: number | null | undefined): string => Number.isFinite(value) ? `#${value}` : "-";
/** Table summary cell: at most two backend phrases plus an explicit count of the hidden rest. */
const summaryCell = (values: string[]): string => values.length ? `${values.slice(0, 2).join(" · ")}${values.length > 2 ? ` · 외 ${values.length - 2}건` : ""}` : "—";
function Section({ title, children }: { title: string; children: ReactNode }) { return <section><h3 className="label mb-2">{title}</h3>{children}</section>; }
function List({ values, empty }: { values: string[]; empty: string }) { return values.length ? <ul className="list-disc space-y-1 pl-5 text-sm text-foreground-secondary">{values.map(value => <li key={value}>{value}</li>)}</ul> : <p className="text-sm text-muted">{empty}</p>; }
/** Compact display cell for one backend-provided market value. The frontend never recomputes a price or a return. */
function Metric({ label, value, tone = "neutral" }: { label: string; value: string; tone?: SemanticTone }) {
  return <div className="rounded-lg border border-line p-3"><p className="text-xs text-muted">{label}</p><p className={`mt-1 whitespace-nowrap text-sm font-semibold tone-text-${tone}`}>{value}</p></div>;
}
/** Extended-session observation: a price only when the backend actually observed a bar, otherwise the typed reason. */
function ExtendedMetric({ label, quote }: { label: string; quote: ExtendedMarketQuote | undefined }) {
  if (!quote || quote.price == null) return <Metric label={label} value={formatQuoteMissingReason(quote?.reason)}/>;
  const change = quote.return_pct == null ? "" : ` · ${signedPercent(quote.return_pct)}`;
  return <Metric label={label} value={`${formatUsd(quote.price)}${change}`} tone={quote.return_pct == null ? "neutral" : signedMetricTone(quote.return_pct)}/>;
}

export default function AdoptionPage() {
  const result = useApi(api.adoption); const toast = useToast();
  const [showExcluded, setShowExcluded] = useState(false); const [selected, setSelected] = useState<AdoptionItem | null>(null);
  const [detail, setDetail] = useState<ResearchDetail | null>(null); const [loadingDetail, setLoadingDetail] = useState(false); const [busy, setBusy] = useState(false);
  const noAnalysis = Boolean(result.error?.toLowerCase().includes("research analysis not found"));
  const approved = result.data?.items.filter(item => item.human_decision?.decision === "APPROVE").length ?? 0;
  const visible = result.data?.items.filter(item => showExcluded || item.classification !== "EXCLUDED") ?? [];
  async function open(item: AdoptionItem) { setSelected(item); setDetail(null); setLoadingDetail(true); try { setDetail(await api.researchDetail(item.analysis_id, item.symbol)); } catch (error) { toast(error instanceof Error ? error.message : "상세 조회 실패", true); } finally { setLoadingDetail(false); } }
  async function decide(decision: "APPROVE" | "REJECT") {
    if (!selected || !result.data) return;
    setBusy(true); try { await api.decide(selected.analysis_id, selected.symbol, decision); toast(`${selected.symbol} 결정을 ${formatDecisionStatus(decision)}으로 변경했습니다.`); await result.refresh(); setDetail(await api.researchDetail(selected.analysis_id, selected.symbol)); }
    catch (error) { toast(error instanceof ApiError && error.status === 409 ? "최대 2개 종목까지 채택할 수 있습니다." : error instanceof Error ? error.message : "결정 저장 실패", true); } finally { setBusy(false); }
  }
  return <><AnalysisTabs/>{result.loading ? <LoadingState/> : result.error && !noAnalysis ? <ErrorState message={result.error} retry={result.refresh}/> : !result.data ? <><PageHeader title="채택 후보" description="GPT 분석이 완료되면 최종 검토할 후보가 표시됩니다."/><EmptyState title="아직 채택 후보가 없습니다." description="먼저 GPT 분석 결과를 입력하고 적용하세요."/></> : <>
    <PageHeader title="채택 후보" description="Quant와 GPT 분석을 함께 비교해 최종 검토가 필요한 종목을 보여줍니다."/>
    <div className="mb-4 rounded-lg border border-line bg-surface-alt px-3 py-2"><p className="text-xs text-muted">채택 후보 {result.data.counts.adoption_candidate}개 · 검토 필요 {result.data.counts.review_required}개 · 최종 채택 {approved}/2 · {result.data.filter_version}</p><p className="mt-1 text-xs text-muted">순위는 검토 순서이며 채택 결정이 아닙니다. 최종 채택은 최대 2개까지 가능합니다.</p></div>
    {result.data.counts.adoption_candidate === 0 && <p className="mb-4 rounded-lg border border-line bg-surface-alt p-3 text-sm text-muted">{result.data.counts.review_required ? "채택 후보는 없지만 검토가 필요한 종목이 있습니다." : "현재 기준을 충족한 채택 후보가 없습니다."}</p>}
    <div className="mb-3 flex justify-end"><button className="btn-action-secondary-compact" aria-pressed={showExcluded} onClick={() => setShowExcluded(value => !value)}>{showExcluded ? "제외 숨기기" : `제외 ${result.data.counts.excluded}개 보기`}</button></div>
    <div className="table-wrap"><table className="analysis-table"><thead><tr><th className="whitespace-nowrap">순위</th><th>상태</th><th>종목</th><th>Quant → GPT</th><th>순위 변화</th><th>핵심 강점</th><th>핵심 주의</th><th>상세</th><th>최종 결정</th></tr></thead><tbody>{visible.map(item => { const meta=status(item.classification); return <tr key={item.symbol}><td className="whitespace-nowrap font-bold text-primary">{rankLabel(item.recommendation_rank)}</td><td><StatusBadge value={item.classification} label={meta.label} tone={meta.tone}/></td><td><p className="font-bold text-foreground">{item.symbol}</p>{item.company_name && <p className="text-xs text-muted">{item.company_name}</p>}</td><td className="whitespace-nowrap">{item.quant_rank == null ? "—" : `#${item.quant_rank}`} → #{item.gpt_rank}</td><td><StatusBadge value={item.rank_direction} label={item.rank_delta_label} tone={deltaTone(item)}/></td><td className="hidden min-w-48 text-xs text-foreground-secondary lg:table-cell">{summaryCell(item.strengths)}</td><td className="hidden min-w-48 text-xs text-foreground-secondary lg:table-cell">{summaryCell(item.warnings)}</td><td><button className="btn-action-secondary-compact whitespace-nowrap" onClick={() => void open(item)}>상세보기</button></td><td><StatusBadge value={item.human_decision?.decision || "UNDECIDED"} label={formatDecisionStatus(item.human_decision?.decision)}/></td></tr>})}</tbody></table></div>
  </>}
  <Drawer open={selected !== null} title={`${selected?.symbol ?? "채택 후보"} · 채택 검토`} onClose={() => { setSelected(null); setDetail(null); }} footer={selected && <ResearchDecisionControl decision={detail?.human_decision?.decision ?? selected.human_decision?.decision} busy={busy} approveDisabled={approved >= 2 && (detail?.human_decision?.decision ?? selected.human_decision?.decision) !== "APPROVE"} onDecide={decision => void decide(decision)}/>}>{loadingDetail ? <LoadingState/> : selected && detail && <Detail item={selected} detail={detail}/>}</Drawer>
  </>;
}

function Detail({ item, detail }: { item: AdoptionItem; detail: ResearchDetail }) {
  const meta = status(item.classification);
  const company = detail.company_summary?.trim();
  return <div className="space-y-7">
    <header><p className="text-sm font-bold text-primary">순위 {rankLabel(item.recommendation_rank)}</p>
      <h2 className="mt-1 text-xl font-semibold text-foreground">{item.symbol}{item.company_name && <span className="text-base font-normal text-muted"> · {item.company_name}</span>}</h2>
      <div className="mt-2 flex flex-wrap items-center gap-2"><StatusBadge value={item.classification} label={meta.label} tone={meta.tone}/><span className="text-sm text-foreground-secondary">Quant {item.quant_rank == null ? "—" : `#${item.quant_rank}`} → GPT #{item.gpt_rank}</span><StatusBadge value={item.rank_direction} label={item.rank_delta_label} tone={deltaTone(item)}/></div></header>
    {company && <Section title="회사 설명"><p className="line-clamp-2 text-sm leading-6 text-foreground-secondary" title={company}>{company}</p></Section>}
    <Section title="판단 요약"><div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[["촉매", detail.catalyst_score, researchCatalystLabel, researchCatalystTone],["모멘텀", detail.momentum_score, researchMomentumLabel, researchMomentumTone],["안전도", detail.risk_score, researchRiskLabel, researchRiskTone],["근거 신뢰도", detail.evidence_confidence, evidenceScoreLabel, evidenceScoreTone]].map(([label,value,labeler,toner]) => <div className="rounded-lg border border-line p-3" key={String(label)}><p className="text-xs text-muted">{String(label)}</p><p className={`mt-1 font-semibold tone-text-${(toner as (v:number)=>string)(value as number)}`}>{String(value)} · {(labeler as (v:number)=>string)(value as number)}</p></div>)}</div></Section>
    <Section title="단기 가격 상태"><div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Metric label="전일 종가" value={formatUsd(detail.previous_close)}/>
      <Metric label="전일 고가" value={formatUsd(detail.previous_high)}/>
      <Metric label="전일 등락" value={signedPercent(detail.previous_return_pct)} tone={detail.previous_return_pct == null ? "neutral" : signedMetricTone(detail.previous_return_pct)}/>
      <Metric label="거래량 강도" value={detail.rvol == null ? "-" : `${multiple(detail.rvol)} · ${rvolLabel(detail.rvol)}`} tone={detail.rvol == null ? "neutral" : rvolTone(detail.rvol)}/>
      <Metric label="시장 대비" value={signedPercent(item.relative_strength)} tone={item.relative_strength == null ? "neutral" : signedMetricTone(item.relative_strength)}/>
      <Metric label="최근 흐름" value={signedPercent(item.momentum)} tone={item.momentum == null ? "neutral" : signedMetricTone(item.momentum)}/>
      <ExtendedMetric label="프리마켓" quote={detail.premarket}/>
      <ExtendedMetric label="애프터마켓" quote={detail.postmarket}/>
    </div><p className="mt-2 text-xs text-muted">Scanner snapshot과 Backend가 관측한 값만 표시합니다. 실시간 현재가는 제공되지 않습니다.</p></Section>
    <Section title="채택 이유"><p className="whitespace-pre-wrap text-sm leading-6 text-foreground-secondary">{detail.catalyst_summary}</p><div className="mt-3"><List values={item.strengths} empty="표시할 핵심 강점이 없습니다."/></div></Section>
    <Section title="순위 변화 이유"><p className="text-sm leading-6 text-foreground-secondary">{item.rank_explanation}</p></Section>
    <Section title="위험 / 주의"><List values={item.warnings} empty="추가 주의 항목이 없습니다."/><p className="mt-3 whitespace-pre-wrap rounded-lg border border-danger bg-danger-soft p-3 text-sm leading-6 text-foreground-secondary">{detail.risk_summary}</p></Section>
  </div>;
}
