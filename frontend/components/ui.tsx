"use client";

import { useEffect, type ReactNode } from "react";
import type { RuntimeMode } from "@/types/api";
import type { SemanticTone } from "@/lib/display";
import { formatKrw, formatSignedKrw } from "@/lib/format";

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return <header className="mb-7 flex flex-col gap-4 md:flex-row md:items-end md:justify-between"><div>{eyebrow && <p className="label mb-2">{eyebrow}</p>}<h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>{description && <p className="mt-2 max-w-3xl text-sm text-muted">{description}</p>}</div>{actions && <div className="flex shrink-0 gap-2">{actions}</div>}</header>;
}
export function StatusBadge({ value, label, showRaw = false, tone }: { value: string; label?: string; showRaw?: boolean; tone?: SemanticTone }) {
  const key = value.toUpperCase(); const inferredTone = key === "NORMAL" || key === "APPROVE" || key === "FILLED" || key === "PASS" || key === "REGULAR" || key === "CONNECTED" ? "success" : key === "HALTED" || key === "CRITICAL" || key === "REJECT" || key === "REJECTED" ? "danger" : key === "SAFE_MODE" || key === "WARNING" || key === "ERROR" ? "warning" : "neutral";
  return <span title={label && label !== value ? value : undefined} className={`inline-flex whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-bold tracking-wide tone-${tone ?? inferredTone}`}>{label ?? value}{showRaw && label && label !== value && <small className="ml-1.5 font-mono font-normal opacity-70">{value}</small>}</span>;
}

export function ConvertedKrw({ amount, signed = false }: { amount?: number | string | null; signed?: boolean }) {
  if (amount == null) return null;
  return <span className="money-converted block text-xs font-medium text-muted">환산 약 {signed ? formatSignedKrw(amount) : formatKrw(amount)}</span>;
}
export function RuntimeBanner({ mode }: { mode: RuntimeMode }) {
  if (mode === "NORMAL") return null;
  return <div className={`border-b px-5 py-2.5 text-center text-sm font-semibold ${mode === "HALTED" ? "tone-danger" : "tone-warning"}`}>{mode === "HALTED" ? "중지 - 자동 전략 Action이 중단되었습니다." : "안전 모드 - 신규 진입과 추가매수가 차단되었습니다."}</div>;
}
export function MetricCard({ label, value, detail, meta, accent, icon }: { label: string; value: ReactNode; detail?: string; meta?: string; accent?: "primary" | "blue" | "indigo" | "gold" | "green" | "bluegreen" | "danger" | "warning"; icon?: ReactNode }) {
  const accentClass = accent === "danger" ? "border-danger" : accent === "warning" ? "border-warning" : accent ? `metric-card metric-card-${accent}` : "";
  return <div className={`panel relative flex min-h-36 flex-col overflow-hidden p-4 ${accentClass}`}><div className="flex items-start justify-between gap-3"><p className="label pt-0.5">{label}</p>{meta && <span className="shrink-0 rounded-md border border-line bg-surface-alt px-2 py-1 text-[10px] font-bold tracking-wide text-foreground-secondary">{meta}</span>}</div><div className="metric-card-value relative z-10 mt-4 text-2xl font-bold tracking-tight text-foreground">{value}</div>{icon && <span aria-hidden="true" className="metric-card-icon absolute bottom-11 right-3 grid h-10 w-10 place-items-center rounded-lg [&>svg]:h-5 [&>svg]:w-5">{icon}</span>}{detail && <p className="metric-card-helper relative z-10 mt-auto border-t border-line-subtle pt-3 text-xs font-medium text-foreground-secondary">{detail}</p>}</div>;
}
export function EmptyState({ title, description, size = "compact" }: { title: string; description?: string; size?: "compact" | "medium" }) { return <div className={`panel px-6 text-center ${size === "medium" ? "py-10" : "py-6"}`} data-empty-size={size}><p className="text-sm font-medium text-foreground-secondary">{title}</p>{description && <p className="mt-2 text-xs text-muted">{description}</p>}</div>; }
export function LoadingState() { return <div className="grid animate-pulse gap-4 md:grid-cols-3">{[0,1,2].map(i => <div className="panel h-28 bg-surface-alt" key={i}/>)}</div>; }
export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  const missingResearch = message.toLowerCase().includes("research analysis not found");
  if (missingResearch) return <EmptyState title="아직 불러온 GPT 분석 결과가 없습니다." description="위 입력창에 ChatGPT 분석 JSON을 붙여넣으면 종목별 순위와 근거가 표시됩니다."/>;
  return <div className="panel border-danger p-6"><p className="font-semibold text-danger">Backend에 연결할 수 없습니다</p><p className="mt-2 text-sm text-foreground-secondary">{message}</p>{retry && <button className="btn-muted mt-4" onClick={retry}>다시 시도</button>}</div>;
}

export function Modal({ open, title, children, onClose }: { open: boolean; title: string; children: ReactNode; onClose: () => void }) {
  if (!open) return null; return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" role="dialog" aria-modal="true" aria-label={title} onMouseDown={onClose}><div className="panel max-h-[85vh] w-full max-w-2xl overflow-auto p-5" onMouseDown={e => e.stopPropagation()}><div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-semibold">{title}</h2><button className="btn-muted px-2.5 py-1.5" onClick={onClose} aria-label="닫기">✕</button></div>{children}</div></div>;
}

export function Drawer({ open, title, children, footer, onClose }: { open: boolean; title: string; children: ReactNode; footer?: ReactNode; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);
  if (!open) return null;
  return <div className="fixed inset-0 z-50 bg-black/65" role="presentation" onMouseDown={onClose}>
    <aside className="ml-auto flex h-full w-full flex-col border-l border-line bg-panel shadow-2xl sm:max-w-[640px]" role="dialog" aria-modal="true" aria-label={title} onMouseDown={event => event.stopPropagation()}>
      <div className="flex shrink-0 items-center justify-between border-b border-line px-5 py-4"><h2 className="text-lg font-semibold text-foreground">{title}</h2><button className="btn-muted px-2.5 py-1.5" onClick={onClose} aria-label="상세 닫기">✕</button></div>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
      {footer && <div className="shrink-0 border-t border-line bg-surface-alt px-5 py-4">{footer}</div>}
    </aside>
  </div>;
}
