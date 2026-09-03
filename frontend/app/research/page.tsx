"use client";

import { useState, type ReactNode } from "react";
import { AnalysisTabs } from "@/components/section-tabs";
import { useToast } from "@/components/toast";
import { Drawer, EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components/ui";
import { ResearchDecisionControl } from "@/components/research-decision";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api";
import { formatDecisionStatus, formatProfile } from "@/lib/display";
import { compactUsd, etTime, multiple } from "@/lib/format";
import type { Candidate, ResearchDetail } from "@/types/api";

const EVIDENCE_HELP = "출처 품질과 주장 커버리지를 기준으로 계산된 구조적 근거 점수이며, 해당 주장이 사실일 확률을 의미하지 않습니다.";

function Section({ title, children }: { title: string; children: ReactNode }) { return <section><h3 className="label mb-2">{title}</h3>{children}</section>; }

export default function ResearchPage() {
  const result = useApi(api.research); const toast = useToast();
  const [raw, setRaw] = useState(""); const [busy, setBusy] = useState(false); const [showImport, setShowImport] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null); const [detail, setDetail] = useState<ResearchDetail | null>(null); const [quant, setQuant] = useState<Candidate | null>(null);
  const [detailTradingDate, setDetailTradingDate] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false); const [detailError, setDetailError] = useState<string | null>(null);
  const noAnalysis = Boolean(result.error?.toLowerCase().includes("research analysis not found"));

  async function importJson() { if (!raw.trim()) { toast("GPT JSON을 입력하세요.", true); return; } setBusy(true); try { const response = await api.importResearch(raw); toast(`${response.candidate_count}개 후보 분석을 불러왔습니다.`); setRaw(""); setShowImport(false); await result.refresh(); } catch (error) { toast(error instanceof ApiError ? error.message : "불러오기에 실패했습니다.", true); } finally { setBusy(false); } }
  async function openDetail(symbol: string) {
    if (!result.data) return;
    setSelectedSymbol(symbol); setDetail(null); setQuant(null); setDetailTradingDate(null); setDetailError(null); setDetailLoading(true);
    try { const [candidateDetail, scanner] = await Promise.all([api.researchDetail(result.data.analysis.id, symbol), api.scannerRun(result.data.analysis.scanner_run_id)]); setDetail(candidateDetail); setQuant(scanner.candidates.find(candidate => candidate.candidate_id === candidateDetail.scanner_candidate_id) ?? null); setDetailTradingDate(scanner.run.trading_date); }
    catch (error) { setDetailError(error instanceof Error ? error.message : "상세 조회 실패"); } finally { setDetailLoading(false); }
  }
  async function decide(symbol: string, decision: "APPROVE" | "REJECT") {
    if (!result.data) return;
    if (detail?.symbol === symbol && detail.human_decision?.decision === decision) return;
    if (decision === "REJECT" && result.data.candidates.find(candidate => candidate.symbol === symbol)?.human_decision?.decision === "APPROVE" && !window.confirm("기존 APPROVE를 REJECT로 변경할까요?")) return;
    setBusy(true); try { await api.decide(result.data.analysis.id, symbol, decision); toast(`${symbol} 결정을 ${formatDecisionStatus(decision)}으로 변경했습니다.`); await result.refresh(); if (selectedSymbol === symbol) setDetail(await api.researchDetail(result.data.analysis.id, symbol)); }
    catch (error) { toast(error instanceof ApiError && error.status === 409 ? "최대 2개 종목까지 채택할 수 있습니다." : error instanceof Error ? error.message : "결정 저장 실패", true); } finally { setBusy(false); }
  }
  const importArea = <section className="panel mb-6 p-5"><h2 className="font-semibold">분석 결과 입력</h2><label className="mb-2 mt-3 block text-xs font-semibold text-foreground-secondary" htmlFor="gpt-json">GPT 분석 JSON</label><textarea id="gpt-json" className="input h-36 resize-y overflow-auto font-mono text-xs leading-5" value={raw} onChange={event => setRaw(event.target.value)} placeholder="ChatGPT가 반환한 JSON을 붙여넣으세요."/><div className="mt-3 flex justify-end"><button className="btn-primary" disabled={busy || !raw.trim()} onClick={() => void importJson()}>검증 후 적용</button></div></section>;
  const closeDrawer = () => { setSelectedSymbol(null); setDetail(null); setQuant(null); setDetailTradingDate(null); setDetailError(null); };

  return <><AnalysisTabs/>{result.loading ? <LoadingState/> : result.error && !noAnalysis && !result.data ? <ErrorState message={result.error} retry={result.refresh}/> : result.data ? <>
    <header className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h1 className="text-lg font-semibold text-foreground">GPT 분석 결과</h1><p className="mt-1 text-xs text-muted">분석 {result.data.candidates.length}종목 · 채택 {result.data.candidates.filter(candidate => candidate.human_decision?.decision === "APPROVE").length}개 · 분석 완료 {etTime(result.data.analysis.analysis_at)} · {result.data.analysis.provider} · {result.data.analysis.model}</p></div><button className="btn-action-primary" aria-expanded={showImport} onClick={() => setShowImport(value => !value)}>분석 결과 입력</button></header>
    {showImport && importArea}<div className="table-wrap"><table><thead><tr><th>GPT 순위</th><th>종목</th><th>Quant 순위</th><th>GPT 종합</th><th>재료</th><th>펀더멘털</th><th>모멘텀</th><th>위험</th><th><span title={EVIDENCE_HELP}>근거 점수 ⓘ</span></th><th>작업</th><th>최종 결정</th></tr></thead><tbody>{result.data.candidates.map(candidate => <tr key={candidate.symbol}><td className="font-bold text-primary">#{candidate.gpt_rank}</td><td><button className="rounded font-bold text-foreground underline decoration-line underline-offset-4 hover:text-primary" onClick={() => void openDetail(candidate.symbol)}>{candidate.symbol}</button></td><td>#{candidate.quant_rank}</td><td>{candidate.overall_score}</td><td>{candidate.catalyst_score}</td><td>{candidate.fundamental_score}</td><td>{candidate.momentum_score}</td><td>{candidate.risk_score}</td><td>{candidate.evidence_confidence}</td><td><button className="btn-action-secondary-compact whitespace-nowrap" onClick={() => void openDetail(candidate.symbol)}>상세보기</button></td><td><StatusBadge value={candidate.human_decision?.decision || "UNDECIDED"} label={formatDecisionStatus(candidate.human_decision?.decision)}/></td></tr>)}</tbody></table></div>
  </> : <><header className="mb-4"><h1 className="text-lg font-semibold text-foreground">GPT 분석 결과</h1><p className="mt-1 text-xs text-muted">아직 GPT 분석 결과가 없습니다.</p></header>{importArea}<EmptyState title="아직 GPT 분석 결과가 없습니다." description="분석 JSON을 불러오면 종목별 순위와 근거가 표시됩니다."/></>}
    <Drawer open={selectedSymbol !== null} title={`${selectedSymbol ?? "GPT 분석"} · 종목 분석`} onClose={closeDrawer} footer={detail && <ResearchDecisionControl decision={detail.human_decision?.decision} busy={busy} onDecide={decision => void decide(detail.symbol, decision)}/>}>
      {detailLoading ? <div aria-label="상세 분석 불러오는 중" className="animate-pulse space-y-3"><div className="h-24 rounded-xl bg-surface-alt"/><div className="h-40 rounded-xl bg-surface-alt"/><div className="h-56 rounded-xl bg-surface-alt"/></div> : detailError ? <ErrorState message={detailError} retry={() => selectedSymbol && void openDetail(selectedSymbol)}/> : detail && <CandidateDrawer detail={detail} quant={quant} tradingDate={detailTradingDate}/>}
    </Drawer>
  </>;
}

function CandidateDrawer({ detail, quant, tradingDate }: { detail: ResearchDetail; quant: Candidate | null; tradingDate: string | null }) {
  const unavailable = "데이터 미제공";
  return <div className="space-y-7">
    <Section title="회사 정보"><div className="flex flex-wrap items-start justify-between gap-3"><div><h4 className="text-xl font-semibold text-foreground">{detail.symbol}</h4><p className="mt-1 text-sm text-foreground-secondary">{quant?.company_name || "회사 정보 없음"}</p></div><div className="flex gap-2"><span className="rounded-full border border-line bg-surface-alt px-2.5 py-1 text-xs font-semibold">Quant {detail.quant_rank == null ? "순위 없음" : `#${detail.quant_rank}`}</span><span className="rounded-full border border-primary bg-primary-soft px-2.5 py-1 text-xs font-semibold text-primary">GPT #{detail.gpt_rank}</span></div></div><div className="mt-3 grid grid-cols-3 gap-2 text-sm"><div><p className="text-xs text-muted">거래소</p><p className="mt-1">정보 없음</p></div><div><p className="text-xs text-muted">업종</p><p className="mt-1">정보 없음</p></div><div><p className="text-xs text-muted">시가총액</p><p className="mt-1">{compactUsd(quant?.market_cap)}</p></div></div><h4 className="mt-4 text-sm font-semibold text-foreground">회사 개요</h4><p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground-secondary">{detail.company_summary || "정보 없음"}</p></Section>
    <Section title="최근 주가 흐름"><p className="mb-3 text-xs text-muted">ScannerRun 일봉 snapshot · 기준 거래일 {tradingDate || "정보 없음"}</p><div className="divide-y divide-line rounded-lg border border-line px-3 text-sm">{[["전일 종가", quant?.latest_close == null ? unavailable : compactUsd(quant.latest_close)], ["전일 등락", unavailable], ["전일 고가 / 저가", unavailable], ["전일 거래량", quant?.latest_volume == null ? unavailable : quant.latest_volume.toLocaleString()], ["평균 대비 거래량", quant?.raw_metrics.rvol == null ? unavailable : multiple(quant.raw_metrics.rvol)], ["애프터마켓", unavailable], ["프리마켓", unavailable]].map(([label, value]) => <div className="flex items-center justify-between gap-4 py-2.5" key={label}><span className="text-muted">{label}</span><span className={value === unavailable ? "text-muted" : "font-medium text-foreground"}>{value}</span></div>)}</div></Section>
    <Section title="주요 재료"><Narrative value={detail.catalyst_summary}/></Section>
    <Section title="위험 요인"><div className="rounded-lg border border-danger bg-danger-soft p-3"><Narrative value={detail.risk_summary}/></div></Section>
    <Section title="주의 / 무효화 조건"><Narrative value={detail.invalidation_summary}/></Section>
    <Section title="근거 자료"><div className="mb-3 flex items-center gap-2"><span className="text-sm font-semibold text-foreground">근거 점수 {detail.evidence_confidence}</span><span className="text-xs text-muted" title={EVIDENCE_HELP}>ⓘ 점수 안내</span></div>{detail.sources.length ? <div className="space-y-2">{detail.sources.map((source, index) => <a key={`${source.url}-${index}`} href={source.url} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-line p-3 hover:border-primary"><div className="flex flex-wrap items-center gap-2"><StatusBadge value={source.source_type}/><span className="text-xs text-muted">{source.domain}</span>{source.published_at && <span className="text-xs text-muted">{etTime(source.published_at)}</span>}</div><p className="mt-2 text-sm font-medium text-foreground">{source.title}</p><p className="mt-1 text-xs text-muted">주장: {source.claim}</p></a>)}</div> : <p className="text-sm text-muted">등록된 출처가 없습니다.</p>}</Section>
    <Section title="전략 참고"><div className="grid grid-cols-2 gap-3">{[["재료 지속성", detail.catalyst_duration], ["트레일링 성향", detail.trailing_profile], ["익일 보유 적합성", detail.overnight_suitability]].map(([label, value]) => <div key={label}><p className="text-xs text-muted">{label}</p><p className="mt-1">{formatProfile(value)}</p></div>)}</div></Section>
    {detail.unknown_fields.length > 0 && <Section title="확인되지 않은 항목"><ul className="list-disc space-y-1 pl-5 text-sm text-warning">{detail.unknown_fields.map(field => <li key={field}>{field}</li>)}</ul></Section>}
  </div>;
}

function Narrative({ value }: { value: string }) { return <p className="whitespace-pre-wrap text-sm leading-6 text-foreground-secondary">{value || "정보 없음"}</p>; }
