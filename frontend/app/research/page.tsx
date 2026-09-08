"use client";

import { useState } from "react";
import { AnalysisTabs } from "@/components/section-tabs";
import { useToast } from "@/components/toast";
import { EmptyState, ErrorState, InfoTooltip, LoadingState, PageHeader, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api";
import { evidenceScoreLabel, evidenceScoreTone, fundamentalLabel, fundamentalTone, researchCatalystLabel, researchCatalystTone, researchMomentumLabel, researchMomentumTone, researchOverallLabel, researchOverallTone, researchRiskLabel, researchRiskTone } from "@/lib/display";
import { etTime, kstTime } from "@/lib/format";

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

export default function ResearchPage() {
  const result = useApi(api.research); const toast = useToast();
  const [raw, setRaw] = useState(""); const [busy, setBusy] = useState(false); const [showImport, setShowImport] = useState(false);
  const noAnalysis = Boolean(result.error?.toLowerCase().includes("research analysis not found"));
  async function importJson() {
    if (!raw.trim()) { toast("GPT JSON을 입력하세요.", true); return; }
    setBusy(true);
    try { const response = await api.importResearch(raw); toast(`${response.candidate_count}개 후보 분석을 불러왔습니다.`); setRaw(""); setShowImport(false); await result.refresh(); }
    catch (error) { toast(error instanceof ApiError ? error.message : "불러오기에 실패했습니다.", true); }
    finally { setBusy(false); }
  }
  const importArea = <section className="panel mb-5 p-4 sm:p-5"><div><h2 className="font-semibold">분석 결과 입력</h2><p className="mt-1 text-xs text-muted">ChatGPT 분석 JSON을 붙여넣은 뒤 형식을 검증하고 적용합니다.</p></div><label className="mb-2 mt-3 block text-xs font-semibold text-foreground-secondary" htmlFor="gpt-json">GPT 분석 JSON</label><textarea id="gpt-json" className="input h-32 resize-y overflow-auto font-mono text-xs leading-5" value={raw} onChange={event => setRaw(event.target.value)} placeholder="ChatGPT가 반환한 JSON을 붙여넣으세요."/><div className="mt-3 flex justify-end"><button className="btn-action-primary" disabled={busy || !raw.trim()} onClick={() => void importJson()}>검증 후 적용</button></div></section>;
  return <><AnalysisTabs/>{result.loading ? <LoadingState/> : result.error && !noAnalysis && !result.data ? <ErrorState message={result.error} retry={result.refresh}/> : result.data ? <>
    <PageHeader title="GPT 분석" description="불러온 GPT 분석 결과를 확인하고 후보별 평가를 비교합니다." actions={<button className="btn-action-primary" aria-expanded={showImport} onClick={() => setShowImport(value => !value)}>분석 결과 입력</button>}/>
    <p className="mb-4 text-xs text-muted">분석 {result.data.candidates.length}종목 · 분석 완료 {kstTime(result.data.analysis.analysis_at)} <span title="미국 시장 기준 시각">({etTime(result.data.analysis.analysis_at)})</span> · {result.data.analysis.provider} · {result.data.analysis.model}</p>
    {showImport && importArea}<div className="table-wrap"><table className="analysis-table"><thead><tr><th>GPT 순위</th><th>종목</th><th>Quant 순위</th><th>촉매 강도<InfoTooltip label="촉매 강도" text={HELP.catalyst}/></th><th>모멘텀<InfoTooltip label="모멘텀" text={HELP.momentum}/></th><th>안전도<InfoTooltip label="안전도" text={HELP.safety}/></th><th>종합 판단<InfoTooltip label="종합 판단" text={HELP.overall}/></th><th>근거 신뢰도<InfoTooltip label="근거 신뢰도" text={HELP.evidence}/></th><th>기업 체력<InfoTooltip label="기업 체력" text={HELP.fundamental}/></th><th>분석 상태</th></tr></thead><tbody>{result.data.candidates.map(candidate => <tr key={candidate.symbol}><td className="font-bold text-primary">#{candidate.gpt_rank}</td><td className="font-bold text-foreground">{candidate.symbol}</td><td className="text-foreground-secondary">{candidate.quant_rank == null ? "—" : `#${candidate.quant_rank}`}</td><td><Score value={candidate.catalyst_score} label={researchCatalystLabel} tone={researchCatalystTone}/></td><td><Score value={candidate.momentum_score} label={researchMomentumLabel} tone={researchMomentumTone}/></td><td><Score value={candidate.risk_score} label={researchRiskLabel} tone={researchRiskTone}/></td><td><Score value={candidate.overall_score} label={researchOverallLabel} tone={researchOverallTone}/></td><td><Score value={candidate.evidence_confidence} label={evidenceScoreLabel} tone={evidenceScoreTone}/></td><td><Score value={candidate.fundamental_score} label={fundamentalLabel} tone={fundamentalTone}/></td><td><StatusBadge value="COMPLETED" label="분석 완료"/></td></tr>)}</tbody></table></div>
  </> : <><PageHeader title="GPT 분석" description="분석 결과 JSON을 입력하면 후보별 평가와 순위가 표시됩니다."/>{importArea}<EmptyState title="아직 불러온 분석 결과가 없습니다." description="위 입력 영역에서 GPT 분석 JSON을 검증하고 적용하세요."/></>}</>;
}
