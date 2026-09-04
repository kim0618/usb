"use client";

import type { Decision } from "@/types/api";

export function ResearchDecisionControl({ decision, busy = false, approveDisabled = false, onDecide }: { decision?: Decision | null; busy?: boolean; approveDisabled?: boolean; onDecide: (decision: Decision) => void }) {
  return <div>
    <p className="text-xs font-semibold text-foreground">최종 결정</p>
    <div className="mt-2 grid grid-cols-2 gap-2" role="group" aria-label="최종 결정 선택">
      <button type="button" aria-pressed={decision === "APPROVE"} disabled={busy || approveDisabled || decision === "APPROVE"} className="btn-success-soft" onClick={() => onDecide("APPROVE")}>{decision === "APPROVE" ? "✓ 채택" : "채택"}</button>
      <button type="button" aria-pressed={decision === "REJECT"} disabled={busy || decision === "REJECT"} className="btn-danger-soft" onClick={() => onDecide("REJECT")}>{decision === "REJECT" ? "✓ 거절" : "거절"}</button>
    </div>
    <p className="mt-2 text-xs leading-5 text-muted">채택은 즉시 매수가 아닙니다. Strategy/Risk 조건을 통과한 경우에만 SimulationBroker 진입 대상이 됩니다. 실제 주문은 전략 및 리스크 조건을 추가로 통과해야 합니다.</p>
  </div>;
}
