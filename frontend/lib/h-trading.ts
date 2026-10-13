/** Strategy H's operating summary, read from the forward view the research screen already uses.
 *
 *  Labelling and counting only: no decision, maturity or verdict is recomputed. Internal codes
 *  (clauses, blockers, contract ids, digests) never become screen text. */

import { H_APPROVE, H_REJECT, H_WATCH, type HCohortRow, type HMaturity } from "@/lib/strategies";

const PRIORITY: Readonly<Record<string, number>> = { [H_APPROVE]: 0, [H_WATCH]: 1, [H_REJECT]: 2 };

/** Newest decision time first; APPROVE, then WATCH, then REJECT among equals; ticker last. */
export function byRecentDecision(rows: readonly HCohortRow[]): HCohortRow[] {
  return [...rows].sort((a, b) =>
    String(b.decision_time || "").localeCompare(String(a.decision_time || ""))
    || (PRIORITY[a.decision ?? ""] ?? 9) - (PRIORITY[b.decision ?? ""] ?? 9)
    || a.ticker.localeCompare(b.ticker));
}

export function latestDecision(rows: readonly HCohortRow[]): string | null {
  return byRecentDecision(rows.filter(row => row.decision))[0]?.decision ?? null;
}

/** The soonest unmatured horizon of one issuer, e.g. { horizon: "5D", session: "2026-10-09" }. */
export function nextEvaluation(row: HCohortRow): { horizon: string; session: string } | null {
  const pending = Object.entries(row.forward || {})
    .filter(([, outcome]) => outcome.state === "PENDING" && outcome.maturity_session)
    .map(([horizon, outcome]) => ({ horizon, session: outcome.maturity_session as string }))
    .sort((a, b) => a.session.localeCompare(b.session));
  return pending[0] ?? null;
}

/** "관찰 중" while any horizon is still to come, "평가 완료" once every one has matured. */
export function observationState(row: HCohortRow): string {
  const outcomes = Object.values(row.forward || {});
  if (!outcomes.length) return "관찰 중";
  return outcomes.every(outcome => outcome.state === "MATURED") ? "평가 완료" : "관찰 중";
}

/** A short sentence for a decision, from counts of the clauses that fired. */
export function decisionReason(row: HCohortRow): string {
  if (row.decision === H_APPROVE) return "승인 조건을 충족했습니다";
  if (row.decision === H_REJECT) {
    return row.reject_fired?.length ? `기각 조건 ${row.reject_fired.length}개에 해당합니다` : "승인 대상이 아닙니다";
  }
  if (row.decision === H_WATCH) {
    return row.approve_blockers?.length ? `승인 조건 ${row.approve_blockers.length}개가 아직 충족되지 않았습니다` : "관찰을 유지합니다";
  }
  return "-";
}

export type HorizonProgress = { horizon: string; matured: number; total: number; pending: number };

export function horizonProgress(maturity: HMaturity): HorizonProgress[] {
  return Object.entries(maturity).map(([horizon, m]) => ({
    horizon, matured: m.matured, pending: m.pending,
    total: m.matured + m.pending + m.incomplete + m.no_baseline,
  }));
}
