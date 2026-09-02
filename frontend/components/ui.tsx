"use client";

import type { ReactNode } from "react";
import type { RuntimeMode } from "@/types/api";

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description: string; actions?: ReactNode }) {
  return <header className="mb-7 flex flex-col gap-4 md:flex-row md:items-end md:justify-between"><div>{eyebrow && <p className="label mb-2">{eyebrow}</p>}<h1 className="text-2xl font-semibold tracking-tight text-white">{title}</h1><p className="mt-2 max-w-3xl text-sm text-muted">{description}</p></div>{actions && <div className="flex shrink-0 gap-2">{actions}</div>}</header>;
}
export function StatusBadge({ value, label, showRaw = false }: { value: string; label?: string; showRaw?: boolean }) {
  const key = value.toUpperCase(); const tone = key === "NORMAL" || key === "APPROVE" || key === "FILLED" || key === "PASS" || key === "REGULAR" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : key === "HALTED" || key === "CRITICAL" || key === "REJECT" || key === "REJECTED" ? "border-red-500/30 bg-red-500/10 text-red-300" : key === "SAFE_MODE" || key === "WARNING" || key === "ERROR" ? "border-amber-500/30 bg-amber-500/10 text-amber-300" : "border-slate-600 bg-slate-800/70 text-slate-300";
  return <span title={label && label !== value ? value : undefined} className={`inline-flex whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-bold tracking-wide ${tone}`}>{label ?? value}{showRaw && label && label !== value && <small className="ml-1.5 font-mono font-normal opacity-70">{value}</small>}</span>;
}
export function RuntimeBanner({ mode }: { mode: RuntimeMode }) {
  if (mode === "NORMAL") return null;
  return <div className={`border-b px-5 py-2.5 text-center text-sm font-semibold ${mode === "HALTED" ? "border-red-700 bg-red-950/90 text-red-200" : "border-amber-700 bg-amber-950/90 text-amber-100"}`}>{mode === "HALTED" ? "중지 — 자동 전략 Action이 중단되었습니다." : "안전 모드 — 신규 진입과 추가매수가 차단되었습니다."}</div>;
}
export function MetricCard({ label, value, detail, accent }: { label: string; value: ReactNode; detail?: string; accent?: "danger" | "warning" }) {
  return <div className={`panel p-4 ${accent === "danger" ? "border-red-800/80" : accent === "warning" ? "border-amber-800/80" : ""}`}><p className="label">{label}</p><div className="mt-3 text-2xl font-semibold tracking-tight text-white">{value}</div>{detail && <p className="mt-2 text-xs text-muted">{detail}</p>}</div>;
}
export function EmptyState({ title, description, size = "compact" }: { title: string; description?: string; size?: "compact" | "medium" }) { return <div className={`panel px-6 text-center ${size === "medium" ? "py-10" : "py-6"}`} data-empty-size={size}><p className="text-sm font-medium text-slate-300">{title}</p>{description && <p className="mt-2 text-xs text-muted">{description}</p>}</div>; }
export function LoadingState() { return <div className="grid animate-pulse gap-4 md:grid-cols-3">{[0,1,2].map(i => <div className="panel h-28 bg-slate-900/60" key={i}/>)}</div>; }
export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  const missingResearch = message.toLowerCase().includes("research analysis not found");
  if (missingResearch) return <EmptyState title="아직 불러온 GPT 분석 결과가 없습니다." description="위 입력창에 ChatGPT 분석 JSON을 붙여넣으면 종목별 순위와 근거가 표시됩니다."/>;
  return <div className="panel border-red-900/70 p-6"><p className="font-semibold text-red-300">Backend에 연결할 수 없습니다</p><p className="mt-2 text-sm text-slate-400">{message}</p>{retry && <button className="btn-muted mt-4" onClick={retry}>다시 시도</button>}</div>;
}

export function Modal({ open, title, children, onClose }: { open: boolean; title: string; children: ReactNode; onClose: () => void }) {
  if (!open) return null; return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" role="dialog" aria-modal="true" aria-label={title} onMouseDown={onClose}><div className="panel max-h-[85vh] w-full max-w-2xl overflow-auto p-5" onMouseDown={e => e.stopPropagation()}><div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-semibold">{title}</h2><button className="btn-muted px-2.5 py-1.5" onClick={onClose} aria-label="닫기">✕</button></div>{children}</div></div>;
}
