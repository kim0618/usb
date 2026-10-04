"use client";

/** Strategy H's forward shadow: the decision cohort, the thesis levels and the forward outcomes.
 *
 *  Every figure is the backend's (GET /strategies/STRATEGY_H_V2/forward). Nothing is recomputed here
 *  and nothing is invented:
 *
 *  - a refused Bear leg renders "N/A" with the refusal reason as its title, never 0;
 *  - a horizon that has not matured renders PENDING, never the latest price;
 *  - zero positions is a correct state for H and is shown as a state, not as an error;
 *  - no money figure is shown, because H has no capital book. */

import Link from "next/link";
import { EmptyState, MetricCard, StatusBadge } from "@/components/ui";
import { dash, etTime } from "@/lib/format";
import {
  H_APPROVE, H_REJECT, H_WATCH,
  type HCohortRow, type HEvaluation, type HForwardView, type HIssuerView, type HMaturity, type HOutcome,
} from "@/lib/strategies";

const NA = "N/A";

const DECISION_TONE: Readonly<Record<string, "success" | "warning" | "danger" | "neutral">> = {
  [H_APPROVE]: "success", [H_WATCH]: "warning", [H_REJECT]: "danger",
};

const STATE_TONE: Readonly<Record<string, "success" | "warning" | "danger" | "neutral">> = {
  MATURED: "success", PENDING: "neutral", INCOMPLETE: "warning", NO_BASELINE: "danger",
};

function pct(value: number | null | undefined, signed = true): string | null {
  if (value == null) return null;
  const n = value * 100;
  return `${signed && n > 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function price(value: number | null | undefined): string | null {
  return value == null ? null : `$${value.toFixed(2)}`;
}

/** A cell that is allowed to be empty: the reason travels in the title, so "N/A" is never bare. */
function Cell({ value, reason, className = "text-right tabular-nums" }: {
  value: string | null; reason?: string | null; className?: string;
}) {
  if (value == null) {
    return <td className="text-right text-muted" title={reason ?? undefined} data-na={reason || ""}>{NA}</td>;
  }
  return <td className={className}>{value}</td>;
}

function Bool({ value }: { value: boolean | null | undefined }) {
  if (value == null) return <td className="text-center text-muted">-</td>;
  return <td className="text-center">{value ? "YES" : "no"}</td>;
}

/** The launch facts: which contract, which session, how many issuers in each state. */
export function HSummary({ view }: { view: HForwardView }) {
  const counts = view.decision_counts;
  const launch = view.launch;
  const cards: Array<[string, string, string | undefined]> = [
    ["Launch", launch.status === "LAUNCHED" ? dash(launch.baseline_session) : "NOT LAUNCHED",
      launch.status === "LAUNCHED" ? `baseline 세션 · 결정 ${launch.decision_session}` : launch.reason],
    ["H Paper Positions", "0", view.position_rule.sizing_contract === "NOT_DEFINED"
      ? "sizing 계약 미동결 · APPROVE라도 포지션 없음" : "APPROVE 진입 후보만 생성"],
    ["H Watchlist", String(counts[H_WATCH] ?? 0), "관찰 후보 · 포지션 없음"],
    ["H Rejected", String(counts[H_REJECT] ?? 0), "비선정 · 포지션 없음"],
    ["H Approved", String(counts[H_APPROVE] ?? 0), "진입 후보가 될 수 있는 유일한 상태"],
    ["D5 Contract", view.contract.d5_contract, `D6 ${view.contract.d6_contract}`],
  ];
  return <section aria-labelledby="h-summary-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id="h-summary-title" className="font-semibold">Strategy H · Forward Shadow</h2>
      <p className="text-xs text-muted">
        {view.contract.contract_id} · 연구 {view.contract.research_build} · 관찰 {view.contract.forward_observation}
        {" · "}가격 {view.price_store.source} ({view.price_store.sessions_observed}세션)
      </p>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {cards.map(([label, value, detail]) => <MetricCard key={label} label={label} value={value} detail={detail}/>)}
    </div>
    {/* The contract's own reasoning is English and belongs in the title, not mid-sentence on a
        Korean screen; the visible line says the same thing in the screen's language. */}
    <p className="mt-3 rounded-lg border border-line bg-surface-alt px-3 py-2 text-xs text-foreground-secondary"
      data-position-rule={view.position_rule.sizing_contract} title={view.position_rule.why}>
      WATCH·REJECT는 포지션을 만들지 않는다. APPROVE가 0인 것은 오류가 아니다.
      {view.position_rule.sizing_contract === "NOT_DEFINED"
        ? " A는 손절(1R), E는 당일 청산을 전제로 수량을 정하는데 D6은 진입가·손절·청산을 만들지 않는다. 그래서 APPROVE는 진입 후보로만 기록하고, sizing 계약이 따로 동결되기 전에는 포지션을 열지 않는다."
        : ` sizing 계약 ${view.position_rule.sizing_contract}에 따라 APPROVE가 진입 후보가 된다.`}
    </p>
  </section>;
}

/** D7's own state. H is not under the A/E paper gate and borrows none of its verdicts. */
export function HEvaluationPanel({ evaluation }: { evaluation: HEvaluation }) {
  return <section aria-labelledby="h-eval-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id="h-eval-title" className="font-semibold">D7 평가 상태</h2>
      <span className="flex gap-1.5">
        <StatusBadge value={evaluation.state} tone={evaluation.state === "RUNNING" ? "success" : "neutral"}/>
        <StatusBadge value={evaluation.verdict} tone="warning"/>
      </span>
    </div>
    <p className="mb-3 text-xs text-muted">{evaluation.note}</p>
    <div className="table-wrap relative"><table className="analysis-table text-xs min-w-[460px]">
      <caption className="sr-only">호라이즌별 만기 도달 현황</caption>
      <thead><tr>
        <th scope="col">Horizon</th><th scope="col">만기 세션</th>
        <th scope="col" className="text-right">MATURED</th><th scope="col" className="text-right">PENDING</th>
        <th scope="col" className="text-right">INCOMPLETE</th><th scope="col">표본</th>
      </tr></thead>
      <tbody>{Object.entries(evaluation.maturity).map(([horizon, row]) => <tr key={horizon} data-horizon={horizon}>
        <th scope="row" className="font-medium">{horizon}</th>
        <td className="tabular-nums">{dash(row.maturity_session)}</td>
        <td className="text-right tabular-nums">{row.matured}</td>
        <td className="text-right tabular-nums">{row.pending}</td>
        <td className="text-right tabular-nums">{row.incomplete}</td>
        <td>{evaluation.sample_reached[horizon] == null ? "-"
          : <StatusBadge value={evaluation.sample_reached[horizon] ? "REACHED" : "PENDING"}
              tone={evaluation.sample_reached[horizon] ? "success" : "neutral"}/>}</td>
      </tr>)}</tbody>
    </table></div>
    <ul className="mt-3 space-y-1 text-xs text-muted">
      <li>사전등록 질문: {evaluation.contract_id}</li>
      {Object.entries(evaluation.sample_needed).map(([key, value]) => <li key={key}>{key}: {value}</li>)}
    </ul>
  </section>;
}

/** The cohort: one row per issuer, decision first, then price against the thesis levels. */
export function HCohortTable({ rows, detailHref }: {
  rows: HCohortRow[]; detailHref?: (ticker: string) => string;
}) {
  if (!rows.length) {
    return <EmptyState title="H 코호트가 비어 있습니다." description="launch 스냅샷이 아직 기록되지 않았습니다."/>;
  }
  return <section aria-labelledby="h-cohort-title" className="mb-7 min-w-0">
    <h2 id="h-cohort-title" className="mb-3 font-semibold">H 코호트 · 결정과 밸류에이션</h2>
    <div className="table-wrap relative"><table className="analysis-table min-w-[1040px] text-xs">
      <caption className="sr-only">Strategy H 종목별 결정, 목표가, 현재가 거리</caption>
      <thead><tr>
        <th scope="col">종목</th><th scope="col">Decision</th>
        <th scope="col">Gap / Conf</th><th scope="col">Method / Window</th><th scope="col">Val. Conf</th>
        <th scope="col" className="text-right">Current</th>
        <th scope="col" className="text-right">Bear</th>
        <th scope="col" className="text-right">TP1</th><th scope="col" className="text-right">TP2</th>
        <th scope="col" className="text-right">TP1 거리</th><th scope="col" className="text-right">TP2 거리</th>
        <th scope="col">Key Binding Clause</th>
      </tr></thead>
      <tbody>{rows.map(row => <tr key={row.ticker} data-h-row={row.ticker} data-decision={row.decision || ""}>
        <th scope="row" className="whitespace-nowrap font-semibold">
          {detailHref ? <Link href={detailHref(row.ticker)} className="hover:text-primary">{row.ticker}</Link> : row.ticker}
          <span className="block text-[10px] font-normal text-muted">{row.cohort_tag}</span>
        </th>
        <td><StatusBadge value={row.decision || "-"} tone={DECISION_TONE[row.decision || ""] ?? "neutral"}/>
          <span className="mt-1 block text-[10px] text-muted">{row.position_reason}</span></td>
        <td className="whitespace-nowrap">{dash(row.d4_expectation_gap)} / {dash(row.d4_gap_confidence)}</td>
        <td className="whitespace-nowrap">{dash(row.valuation_method)}
          <span className="block text-[10px] text-muted">{dash(row.valuation_window)}</span></td>
        <td>{row.valuation_confidence}
          {row.range_complete === false && <span className="block text-[10px] tone-warning"
            title="범위에 다리가 하나 없다. 신뢰도와 함께 읽으면 모순일 수 있다(D5-D2R 선언 한계)">range 불완전</span>}</td>
        <Cell value={price(row.current_price)} reason={`세션 ${dash(row.current_price_session)}`}/>
        <Cell value={price(row.bear)} reason={row.bear_na_reason}/>
        <Cell value={price(row.tp1)}/>
        <Cell value={price(row.tp2)}/>
        <Cell value={pct(row.tp1_distance)} reason="현재가에서 TP1까지"/>
        <Cell value={pct(row.tp2_distance)} reason="현재가에서 TP2까지"/>
        <td className="font-mono text-[10px] text-foreground-secondary">{dash(row.key_binding_clause)}</td>
      </tr>)}</tbody>
    </table></div>
    <ul className="mt-3 space-y-1 text-xs text-muted">
      <li>Bear가 N/A인 종목은 하방 다리가 거부된 것이다(NEGATIVE_IMPLIED_EQUITY). 0으로 읽지 않는다.</li>
      <li>TP1·TP2는 결정 세션(2026-09-16) 기준 절대 가격이고, 거리는 현재가에서 다시 계산한 값이다.</li>
    </ul>
  </section>;
}

/** Forward outcomes per issuer and horizon. PENDING is the normal state right after launch. */
export function HForwardOutcomes({ rows, horizons }: { rows: HCohortRow[]; horizons: number[] }) {
  const keys = horizons.map(h => `${h}D`);
  return <section aria-labelledby="h-forward-title" className="mb-7 min-w-0">
    <h2 id="h-forward-title" className="mb-1 font-semibold">Forward 관찰</h2>
    <p className="mb-3 text-xs text-muted">
      baseline 세션 이후 정확히 1·5·21·63 거래일에만 성립한다. 미도달은 PENDING이며 마지막 가격으로 대체하지 않는다.
    </p>
    <div className="table-wrap relative"><table className="analysis-table min-w-[820px] text-xs">
      <caption className="sr-only">종목별 호라이즌 성과</caption>
      <thead><tr>
        <th scope="col">종목</th>
        {keys.map(key => <th key={key} scope="col" className="text-center">{key}</th>)}
        <th scope="col">결정 이후 가격변화 (forward 아님)</th>
      </tr></thead>
      <tbody>{rows.map(row => <tr key={row.ticker} data-forward-row={row.ticker}>
        <th scope="row" className="font-semibold">{row.ticker}</th>
        {keys.map(key => {
          const outcome: HOutcome | undefined = row.forward[key];
          if (!outcome) return <td key={key} className="text-center text-muted">-</td>;
          return <td key={key} className="text-center" data-outcome-state={outcome.state}>
            <StatusBadge value={outcome.state} tone={STATE_TONE[outcome.state] ?? "neutral"}/>
            {outcome.state === "MATURED"
              ? <span className="mt-1 block tabular-nums">{pct(outcome.excess_return)} vs SPY</span>
              : <span className="mt-1 block text-[10px] text-muted">{dash(outcome.maturity_session)}</span>}
          </td>;
        })}
        <td className="tabular-nums text-foreground-secondary" title={row.pre_launch_drift.note}>
          {pct(row.pre_launch_drift.return_since_decision) ?? NA}
        </td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

/** One issuer in full: the four research legs, the decision history and every horizon. */
export function HIssuerDetail({ issuer }: { issuer: HIssuerView }) {
  const forward = Object.entries(issuer.forward);
  return <section aria-labelledby="h-detail-title" className="mb-7 min-w-0" data-h-detail={issuer.ticker}>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id="h-detail-title" className="font-semibold">{issuer.ticker} 상세</h2>
      <span className="flex flex-wrap gap-1.5">
        <StatusBadge value={issuer.decision || "-"} tone={DECISION_TONE[issuer.decision || ""] ?? "neutral"}/>
        <StatusBadge value={issuer.cohort_tag || "-"} tone="neutral"/>
      </span>
    </div>
    <dl className="panel mb-4 grid gap-x-6 gap-y-3 p-4 text-sm sm:grid-cols-2 xl:grid-cols-4">
      {([
        ["CIK", dash(issuer.cik)],
        ["Security ID", dash(issuer.security_id)],
        ["Thesis version", dash(issuer.thesis_version)],
        ["결정 세션", dash(issuer.pre_launch_drift.decision_session)],
        ["결정일 종가", price(issuer.decision_close) ?? NA],
        ["Baseline 종가", price(issuer.pre_launch_drift.baseline_close) ?? NA],
        ["현재가", `${price(issuer.current_price) ?? NA} (${dash(issuer.current_price_session)})`],
        ["D4 Expectation Gap", `${dash(issuer.d4_expectation_gap)} / ${dash(issuer.d4_gap_confidence)}`],
        ["Valuation", `${dash(issuer.valuation_method)} · ${dash(issuer.valuation_window)}`],
        ["Valuation confidence", dash(issuer.valuation_confidence)],
        ["TP1 / TP2", `${price(issuer.tp1) ?? NA} / ${price(issuer.tp2) ?? NA}`],
        ["Bear", issuer.bear == null ? `${NA} · ${dash(issuer.bear_na_reason)}` : price(issuer.bear)!],
        ["결정 시 TP1 상승률", pct(issuer.tp1_upside_at_decision) ?? NA],
        ["결정 시 TP2 상승률", pct(issuer.tp2_upside_at_decision) ?? NA],
        ["Key binding clause", dash(issuer.key_binding_clause)],
        ["포지션", `없음 · ${issuer.position_reason}`],
      ] as Array<[string, string]>).map(([label, value]) => <div key={label} data-h-field={label}>
        <dt className="label">{label}</dt>
        <dd className="mt-0.5 font-medium tabular-nums text-foreground">{value}</dd>
      </div>)}
    </dl>

    <h3 className="mb-2 text-sm font-semibold">결정 근거</h3>
    <ul className="panel mb-4 space-y-1.5 p-4 text-xs text-foreground-secondary">
      {issuer.reject_fired.length > 0 && <li><span className="label">REJECT 발동</span> {issuer.reject_fired.join(", ")}</li>}
      {issuer.approve_blockers.length > 0 && <li><span className="label">APPROVE 차단</span> {issuer.approve_blockers.join(", ")}</li>}
      {issuer.watch_matched.length > 0 && <li><span className="label">WATCH 해당</span> {issuer.watch_matched.join(", ")}</li>}
    </ul>

    <h3 className="mb-2 text-sm font-semibold">Forward outcomes</h3>
    <div className="table-wrap relative mb-4"><table className="analysis-table min-w-[720px] text-xs">
      <caption className="sr-only">{issuer.ticker} 호라이즌별 결과</caption>
      <thead><tr>
        <th scope="col">Horizon</th><th scope="col">만기 세션</th><th scope="col">상태</th>
        <th scope="col" className="text-right">종목</th><th scope="col" className="text-right">SPY</th>
        <th scope="col" className="text-right">초과</th>
        <th scope="col" className="text-right">MFE</th><th scope="col" className="text-right">MAE</th>
        <th scope="col" className="text-center">TP1</th><th scope="col" className="text-center">TP2</th>
        <th scope="col" className="text-center">Bear</th>
      </tr></thead>
      <tbody>{forward.map(([key, outcome]) => <tr key={key} data-detail-horizon={key}>
        <th scope="row" className="font-medium">{key}</th>
        <td className="tabular-nums">{dash(outcome.maturity_session)}</td>
        <td><StatusBadge value={outcome.state} tone={STATE_TONE[outcome.state] ?? "neutral"}/></td>
        <Cell value={pct(outcome.security_return)} reason={outcome.reason}/>
        <Cell value={pct(outcome.benchmark_return)} reason={outcome.reason}/>
        <Cell value={pct(outcome.excess_return)} reason={outcome.reason}/>
        <Cell value={pct(outcome.mfe)} reason={outcome.reason}/>
        <Cell value={pct(outcome.mae)} reason={outcome.reason}/>
        <Bool value={outcome.tp1_hit}/>
        <Bool value={outcome.tp2_hit}/>
        <Bool value={outcome.bear_breach}/>
      </tr>)}</tbody>
    </table></div>

    <h3 className="mb-2 text-sm font-semibold">결정 이력 · thesis 버전</h3>
    <div className="table-wrap relative"><table className="analysis-table min-w-[620px] text-xs">
      <caption className="sr-only">{issuer.ticker} 결정 이력</caption>
      <thead><tr>
        <th scope="col">기록</th><th scope="col">시각</th><th scope="col">이전</th><th scope="col">결정</th>
        <th scope="col">원인</th><th scope="col">thesis</th>
      </tr></thead>
      <tbody>{issuer.decision_history.map((row, index) => <tr key={index} data-history-row={index}>
        <td>{dash(row.record as string)}</td>
        <td className="whitespace-nowrap">{etTime(row.decision_time as string)}</td>
        <td>{dash(row.previous_decision as string)}</td>
        <td>{dash(row.decision as string)}</td>
        <td>{dash(row.cause as string)}</td>
        <td className="font-mono text-[10px]">{dash(row.thesis_version as string)}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="mt-3 text-xs text-muted">
      과거 thesis는 덮어쓰지 않는다. 결정이 바뀌면 새 행이 추가되고 thesis 버전이 새로 생긴다.
      가격이 움직였다는 이유만으로는 결정이 바뀌지 않는다.
    </p>
  </section>;
}

/** Maturity counts, for the combined A/E/H board where H's column has no money to show. */
export function HBoardRow({ counts, maturity }: { counts: Record<string, number>; maturity: HMaturity }) {
  return <>
    <tr data-h-board="decisions">
      <th scope="row" className="font-medium">H Decisions</th>
      <td colSpan={99} className="text-xs text-foreground-secondary">
        APPROVE {counts[H_APPROVE] ?? 0} · WATCH {counts[H_WATCH] ?? 0} · REJECT {counts[H_REJECT] ?? 0}
      </td>
    </tr>
    <tr data-h-board="maturity">
      <th scope="row" className="font-medium">H Horizons</th>
      <td colSpan={99} className="text-xs text-foreground-secondary">
        {Object.entries(maturity).map(([key, row]) => `${key} ${row.matured}/${row.matured + row.pending + row.incomplete}`).join(" · ")}
      </td>
    </tr>
  </>;
}
