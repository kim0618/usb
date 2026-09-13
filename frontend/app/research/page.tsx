"use client";

import { useState } from "react";
import { AnalysisTabs } from "@/components/section-tabs";
import { useToast } from "@/components/toast";
import { EmptyState, ErrorState, InfoTooltip, LoadingState, Modal, PageHeader, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api";
import { evidenceScoreLabel, evidenceScoreTone, fundamentalLabel, fundamentalTone, researchCatalystLabel, researchCatalystTone, researchMomentumLabel, researchMomentumTone, researchOverallLabel, researchOverallTone, researchRiskLabel, researchRiskTone } from "@/lib/display";
import { etTime, kstTime } from "@/lib/format";
import type { ResearchHistoryItem } from "@/types/api";

const HELP = {
  catalyst: "뉴스, 실적, 이벤트 등 단기 주가를 움직일 직접적인 재료의 강도입니다.",
  momentum: "단기 가격 움직임과 지속 가능성을 평가한 점수입니다.",
  safety: "단기 매매 위험을 반대로 표현한 안전 점수입니다. 높을수록 안전합니다.",
  overall: "촉매, 기업 체력, 모멘텀, 안전도 등을 종합한 GPT 분석 점수입니다.",
  evidence: "공식자료, 뉴스, IR 등 근거의 구조적 커버리지입니다.",
  fundamental: "실적, 경쟁력, 재무 및 사업 지속성을 바탕으로 평가한 기업 체력입니다.",
} as const;

function Score({ value, label, tone }: { value: number; label: (score: number) => string; tone: (score: number) => string }) {
  return <span className={`whitespace-nowrap tone-text-${tone(value)}`}>{value}<span className="hidden xl:inline"> · {label(value)}</span></span>;
}

function HistoryRow({ item, onActivate }: { item: ResearchHistoryItem; onActivate: (item: ResearchHistoryItem) => void }) {
  return <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line px-3 py-2"><div className="text-xs text-muted"><span className="font-semibold text-foreground">Analysis #{item.id}</span> · 분석 {kstTime(item.analysis_at)} · 저장 {kstTime(item.imported_at)} · 후보 {item.candidate_count}개<div className="mt-1">{item.candidates.map(candidate => `#${candidate.gpt_rank} ${candidate.symbol}`).join(" · ") || "후보 없음"}</div></div><div className="flex items-center gap-2">{item.is_active ? <StatusBadge value="ACTIVE" label="이 ScannerRun의 현재 활성 분석" tone="success"/> : <><StatusBadge value="INACTIVE" label="비활성 분석"/><button className="btn-action-secondary-compact" onClick={() => onActivate(item)}>이 분석 사용</button></>}</div></div>;
}

export default function ResearchPage() {
  const result = useApi(api.research); const history = useApi(api.researchHistory); const toast = useToast();
  const [raw, setRaw] = useState(""); const [busy, setBusy] = useState(false); const [showImport, setShowImport] = useState(false);
  const [pendingActivation, setPendingActivation] = useState<ResearchHistoryItem | null>(null);
  const noAnalysis = Boolean(result.error?.toLowerCase().includes("research analysis not found"));
  const currentRunId = result.data?.analysis.scanner_run_id;
  const currentHistory = history.data?.filter(item => item.scanner_run_id === currentRunId) ?? [];
  const otherHistory = history.data?.filter(item => item.scanner_run_id !== currentRunId) ?? [];
  const otherGroups = otherHistory.reduce<Record<string, ResearchHistoryItem[]>>((groups, item) => {
    (groups[item.trading_date] ||= []).push(item); return groups;
  }, {});
  async function importJson() {
    if (!raw.trim()) { toast("GPT JSON을 입력하세요.", true); return; }
    setBusy(true);
    try { const response = await api.importResearch(raw); toast(response.activated ? `새 분석 #${response.analysis_id}이 저장되어 현재 거래 기준으로 설정되었습니다.` : `새 분석 #${response.analysis_id}이 저장되었습니다. 현재 거래 기준은 Analysis #${response.active_analysis_id}입니다. 새 분석을 사용하려면 활성화하세요.`); setRaw(""); setShowImport(false); await Promise.all([result.refresh(), history.refresh()]); }
    catch (error) { toast(error instanceof ApiError ? error.message : "불러오기에 실패했습니다.", true); }
    finally { setBusy(false); }
  }
  const importArea = <section className="panel mb-5 p-4 sm:p-5"><div><h2 className="font-semibold">분석 결과 입력</h2><p className="mt-1 text-xs text-muted">ChatGPT 분석 JSON을 붙여넣은 뒤 형식을 검증하고 적용합니다.</p></div><label className="mb-2 mt-3 block text-xs font-semibold text-foreground-secondary" htmlFor="gpt-json">GPT 분석 JSON</label><textarea id="gpt-json" className="input h-32 resize-y overflow-auto font-mono text-xs leading-5" value={raw} onChange={event => setRaw(event.target.value)} placeholder="ChatGPT가 반환한 JSON을 붙여넣으세요."/><div className="mt-3 flex justify-end"><button className="btn-action-primary" disabled={busy || !raw.trim()} onClick={() => void importJson()}>검증 후 적용</button></div></section>;
  return <><AnalysisTabs/>{result.loading ? <LoadingState/> : result.error && !noAnalysis && !result.data ? <ErrorState message={result.error} retry={result.refresh}/> : result.data ? <>
    <PageHeader title="GPT 분석" description="불러온 GPT 분석 결과를 확인하고 후보별 평가를 비교합니다." actions={<button className="btn-action-primary" aria-expanded={showImport} onClick={() => setShowImport(value => !value)}>분석 결과 입력</button>}/>
    <p className="mb-4 text-xs text-muted">현재 거래 기준 · Analysis #{result.data.analysis.id} · {result.data.analysis.trading_date.slice(5).replace("-", "/")} 분석 기준 · 분석 완료 {kstTime(result.data.analysis.analysis_at)} <span title="미국 시장 기준 시각">({etTime(result.data.analysis.analysis_at)})</span> · {result.data.analysis.provider} · {result.data.analysis.model}</p>
    {history.data && history.data.length > 0 && <section className="panel mb-5 p-4"><h2 className="font-semibold">{result.data.analysis.trading_date.slice(5).replace("-", "/")} 분석 이력</h2><p className="mt-1 text-xs text-muted">활성 표시는 현재 ScannerRun 안에서 거래에 사용하는 분석을 뜻합니다.</p><div className="mt-3 space-y-2">{currentHistory.map(item => <HistoryRow key={item.id} item={item} onActivate={setPendingActivation}/>)}</div>{otherHistory.length > 0 && <details className="mt-4"><summary className="cursor-pointer text-xs font-semibold text-foreground-secondary">다른 거래일 분석 이력 {otherHistory.length}건</summary><div className="mt-3 space-y-4">{Object.entries(otherGroups).map(([tradingDate, items]) => <section key={tradingDate}><h3 className="mb-2 text-xs font-semibold text-muted">{tradingDate} ScannerRun 분석</h3><div className="space-y-2">{items.map(item => <HistoryRow key={item.id} item={item} onActivate={setPendingActivation}/>)}</div></section>)}</div></details>}</section>}
    {showImport && importArea}<div className="table-wrap"><table className="analysis-table"><thead><tr><th>GPT 순위</th><th>종목</th><th>Quant 순위</th><th>촉매 강도<InfoTooltip label="촉매 강도" text={HELP.catalyst}/></th><th>모멘텀<InfoTooltip label="모멘텀" text={HELP.momentum}/></th><th>안전도<InfoTooltip label="안전도" text={HELP.safety}/></th><th>종합 판단<InfoTooltip label="종합 판단" text={HELP.overall}/></th><th>근거 신뢰도<InfoTooltip label="근거 신뢰도" text={HELP.evidence}/></th><th>기업 체력<InfoTooltip label="기업 체력" text={HELP.fundamental}/></th><th>분석 상태</th></tr></thead><tbody>{result.data.candidates.map(candidate => <tr key={candidate.symbol}><td className="font-bold text-primary">#{candidate.gpt_rank}</td><td className="font-bold text-foreground">{candidate.symbol}</td><td className="text-foreground-secondary">{candidate.quant_rank == null ? "—" : `#${candidate.quant_rank}`}</td><td><Score value={candidate.catalyst_score} label={researchCatalystLabel} tone={researchCatalystTone}/></td><td><Score value={candidate.momentum_score} label={researchMomentumLabel} tone={researchMomentumTone}/></td><td><Score value={candidate.risk_score} label={researchRiskLabel} tone={researchRiskTone}/></td><td><Score value={candidate.overall_score} label={researchOverallLabel} tone={researchOverallTone}/></td><td><Score value={candidate.evidence_confidence} label={evidenceScoreLabel} tone={evidenceScoreTone}/></td><td><Score value={candidate.fundamental_score} label={fundamentalLabel} tone={fundamentalTone}/></td><td><StatusBadge value="COMPLETED" label="분석 완료"/></td></tr>)}</tbody></table></div>
  </> : <><PageHeader title="GPT 분석" description="분석 결과 JSON을 입력하면 후보별 평가와 순위가 표시됩니다."/>{importArea}<EmptyState title="아직 불러온 분석 결과가 없습니다." description="위 입력 영역에서 GPT 분석 JSON을 검증하고 적용하세요."/></>}
    <Modal open={pendingActivation !== null} title={pendingActivation ? `Analysis #${pendingActivation.id} 거래 기준 변경` : "거래 기준 변경"} onClose={() => !busy && setPendingActivation(null)}>{pendingActivation && <><p className="text-sm text-foreground-secondary">Analysis #{pendingActivation.id}를 거래 기준으로 변경하시겠습니까?</p><p className="mt-2 text-xs text-muted">Scanner 거래일 {pendingActivation.trading_date} · GPT 후보 {pendingActivation.candidate_count}개</p>{pendingActivation.approved_symbols.length ? <div className="mt-4"><p className="text-xs font-semibold text-foreground-secondary">현재 승인</p><ul className="mt-2 list-disc pl-5 text-sm text-foreground">{pendingActivation.approved_symbols.map(symbol => <li key={symbol}>{symbol}</li>)}</ul></div> : <p className="mt-4 rounded-lg border border-warning bg-warning-soft p-3 text-sm text-warning">이 분석에는 승인된 종목이 없습니다. 활성화하면 현재 진입 후보가 0개가 됩니다.</p>}<p className="mt-4 text-sm text-foreground-secondary">전환 후 다음 Entry Runtime은 이 분석의 승인 종목만 사용합니다.</p><div className="mt-5 flex justify-end gap-2"><button className="btn-muted" disabled={busy} onClick={() => setPendingActivation(null)}>취소</button><button className="btn-action-primary" disabled={busy} onClick={() => void activate(pendingActivation.id)}>거래 기준으로 변경</button></div></>}</Modal>
  </>;

  async function activate(id: number) {
    setBusy(true);
    try { await api.activateResearch(id); toast(`Analysis #${id}을 현재 거래 기준으로 설정했습니다.`); setPendingActivation(null); await Promise.all([result.refresh(), history.refresh()]); }
    catch (error) { toast(error instanceof ApiError ? error.message : "활성 분석 변경에 실패했습니다.", true); }
    finally { setBusy(false); }
  }
}
