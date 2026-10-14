"use client";

import Link from "next/link";
import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusBadge } from "@/components/ui";
import { TradingTabs } from "@/components/section-tabs";
import { useApi } from "@/hooks/use-api";
import { formatSignedUsd, formatUsd, kstDate, kstTime } from "@/lib/format";
import {
  byRecentDecision, decisionReason, horizonProgress, latestDecision, nextEvaluation, observationState,
} from "@/lib/h-trading";
import {
  H_APPROVE, H_REJECT, H_WATCH, strategiesApi, type HAutonomyView, type HForwardView, type HPaperView,
} from "@/lib/strategies";

const DECISION_TONE: Readonly<Record<string, "success" | "warning" | "danger" | "neutral">> = {
  [H_APPROVE]: "success", [H_WATCH]: "warning", [H_REJECT]: "neutral",
};

const decisionBadge = (decision: string | null) =>
  decision ? <StatusBadge value={decision} tone={DECISION_TONE[decision] ?? "neutral"}/> : <span>-</span>;

const monthDay = (session: string) => session.slice(5).replace("-", "/");

/** Strategy H inside the Trading menu: is the forward shadow running, what is it watching, what did
 *  it last decide, how much evaluation has accumulated, and - once the H paper book is launched -
 *  what the paper account holds. Contract, launch and D7 detail stay on the research screen
 *  (/strategy-h). Before the paper launch H holds no capital, so there is no asset or cash card. */
export default function TradingHPage() {
  const state = useApi(strategiesApi.forward, 60_000);
  const paperState = useApi(strategiesApi.paper, 60_000);
  const paper = paperState.data?.paper?.launched ? paperState.data : null;
  const header = <PageHeader title={paper ? "Strategy H · Paper + Forward Shadow" : "Strategy H · Forward Shadow"}
    description={paper ? "가상 계좌 · 실제 주문 없음" : "미래 데이터 관찰 중 · 실제 주문 없음"}
    actions={<Link href="/strategy-h" className="btn-action-secondary-compact">연구 상세 보기</Link>}/>;

  if (state.loading) return <><TradingTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><TradingTabs/>{header}<ErrorState message={state.error || "Strategy H 상태 조회 실패"} retry={state.refresh}/></>;

  const view = state.data;
  return <div className="trading-screen">
    <TradingTabs/>
    {header}
    {paper && <PaperAccount view={paper}/>}
    <Kpis view={view}/>
    {paperState.data?.autonomy && <AutoOps auto={paperState.data.autonomy}/>}
    {paper && <PaperPositions view={paper}/>}
    <Watching view={view}/>
    <RecentDecisions view={view} paper={paper != null}/>
    {paper && <PaperFills view={paper}/>}
    <ForwardProgress view={view}/>
  </div>;
}

const money = (value: string | null | undefined) => value == null ? "-" : formatUsd(value);
const signed = (value: string | null | undefined) => value == null ? "-" : formatSignedUsd(value);
const pct = (value: string | null | undefined) => value == null ? "-" : `${(Number(value) * 100).toFixed(1)}%`;

const ACTION_LABEL: Readonly<Record<string, string>> = {
  BUY: "BUY", SELL_TP1: "TP1 50% 매도", SELL_TP2: "TP2 전량 매도", SELL_BEAR: "Bear 이탈 매도",
  SELL_DECISION_DOWNGRADE: "판정 하향 매도", SELL_INVALIDATION: "Thesis 무효 매도",
  BUY_PENDING: "진입 대기", WAITING_SLOT: "슬롯 대기", CLOSE: "청산 예정", NONE: "없음",
};

/** H's own paper account: never summed with A or E, never mixed with the forward shadow. */
function PaperAccount({ view }: { view: HPaperView }) {
  const a = view.account;
  return <section aria-label="H paper 계좌" className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-h-paper-account="">
    <MetricCard accent="primary" label="현재 자산" value={money(a.current_equity)} detail={`초기 자본 ${money(a.initial_equity)}`}/>
    <MetricCard accent="bluegreen" label="현금" value={money(a.cash)} detail={`포지션 ${money(a.positions_value)}`}/>
    <MetricCard accent="green" label="누적 손익" value={signed(a.total_pnl)}
      detail={`실현 ${signed(a.realized_pnl)} · 미실현 ${signed(a.unrealized_pnl)}`}/>
    <MetricCard accent="gold" label="거래 수" value={String(a.trades ?? 0)}
      detail={`보유 ${a.open_positions ?? 0}종목 · 승률 ${pct(a.win_rate)}`}/>
  </section>;
}

const stamp = (value: string | null | undefined) => value ? kstTime(value) : "-";
const QUEUE_LABEL: ReadonlyArray<[string, string]> = [
  ["QUEUED", "대기"], ["RUNNING", "평가 중"], ["DATA_NOT_READY", "데이터 대기"], ["METHOD_NOT_READY", "평가법 대기"],
  ["FAILED", "실패"],
];
const EVAL_LABEL: Record<string, string> = {
  APPROVE: "APPROVE", WATCH: "WATCH", REJECT: "REJECT", NOT_DECISION_ELIGIBLE: "판정 불가(기존 결정 유지)",
};

/** When H last evaluated by itself and what its candidate queue holds. Read from the server's runtime
 *  files (the timers' own runs), so "H가 안 돈 것 같다" can be checked on screen. Evaluation is code
 *  only (no AI model), so there is no model, authorization or budget to show. */
function AutoOps({ auto }: { auto: HAutonomyView }) {
  const t = auto.timestamps;
  const counts = auto.counts ?? {};
  const last = auto.latest_evaluation;
  const lastResult = last ? `${last.ticker} ${EVAL_LABEL[last.final_decision ?? ""] ?? last.status}` : "-";
  return <section aria-labelledby="h-auto-title" className="mb-7" data-h-auto="">
    <h2 id="h-auto-title" className="mb-3 font-semibold">자동 평가</h2>
    <div className="panel p-5">
      {auto.error ? <p className="text-sm text-foreground-secondary">상태 조회 실패: {auto.error}</p> : <>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4">
        <div><dt className="label">마지막 자동 실행</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground" data-h-auto-last="">{stamp(t?.last_auto_run_at)}</dd></div>
        <div><dt className="label">마지막 자동 평가</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{stamp(t?.evaluation_last_run_at)}</dd></div>
        <div><dt className="label">평가 결과</dt><dd className="mt-1 text-sm font-semibold text-foreground" data-h-auto-result="">{lastResult}</dd></div>
        <div><dt className="label">최신 유니버스</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{t?.latest_universe?.as_of ?? "-"}</dd></div>
        <div><dt className="label">최신 가격 세션</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{t?.latest_price_session ?? "-"}</dd></div>
        <div><dt className="label">SEC 갱신</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{stamp(t?.sec_refresh_at)}</dd></div>
        <div><dt className="label">후보 큐 갱신</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{stamp(t?.research_queue_last_run_at)}</dd></div>
        <div><dt className="label">다음 자동 실행</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground" data-h-auto-next="">{stamp(t?.next_auto_run_at)}</dd></div>
      </dl>
      <p className="mt-4 text-sm text-foreground-secondary" data-h-auto-queue="">
        후보 큐 {QUEUE_LABEL.map(([k, label]) => `${label} ${counts[k] ?? 0}`).join(" · ")}
      </p></>}
    </div>
  </section>;
}

function PaperPositions({ view }: { view: HPaperView }) {
  const rows = view.positions;
  const pending = view.account.pending_entries ?? [];
  return <section aria-labelledby="h-positions-title" className="mb-7">
    <h2 id="h-positions-title" className="mb-3 font-semibold">보유 포지션</h2>
    {!rows.length ? <EmptyState title={pending.length ? `진입 대기 ${pending.map(p => p.ticker).join(", ")}` : "보유 포지션이 없습니다."}/>
      : <div className="table-wrap"><table>
      <thead><tr><th>종목</th><th>진입가</th><th>현재가</th><th>수익률</th><th>TP1</th><th>TP2</th><th>Bear</th><th>실현</th><th>미실현</th></tr></thead>
      <tbody>{rows.map(row => { const back = row.mark_price && row.entry_price
        ? (Number(row.mark_price) / Number(row.entry_price) - 1) : null; return <tr key={row.position_id}>
        <td className="font-bold text-foreground">{row.ticker}{row.position_status === "PARTIAL" ? " · 50%" : ""}</td>
        <td>{money(row.entry_price)}</td><td>{money(row.mark_price)}</td>
        <td>{back == null ? "-" : `${(back * 100).toFixed(1)}%`}</td>
        <td>{money(row.tp1)}</td><td>{money(row.tp2)}</td>
        <td>{row.bear == null ? "평가 안 함" : money(row.bear)}</td>
        <td>{signed(row.realized_pnl)}</td><td>{signed(row.unrealized_pnl)}</td>
      </tr>; })}</tbody></table></div>}
  </section>;
}

function PaperFills({ view }: { view: HPaperView }) {
  const rows = view.fills.slice(0, 10);
  return <section aria-labelledby="h-fills-title" className="mb-7">
    <h2 id="h-fills-title" className="mb-3 font-semibold">최근 체결</h2>
    {!rows.length ? <EmptyState title="아직 체결이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>세션</th><th>종목</th><th>체결</th><th>수량</th><th>체결가</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.event_id}>
        <td>{monthDay(row.session)}</td><td className="font-bold text-foreground">{row.ticker}</td>
        <td>{ACTION_LABEL[row.trade_action] ?? row.trade_action}</td><td>{row.quantity}</td><td>{money(row.fill_price)}</td>
      </tr>)}</tbody></table></div>}
  </section>;
}

function Kpis({ view }: { view: HForwardView }) {
  const counts = view.decision_counts;
  const recent = latestDecision(view.rows);
  return <section aria-label="관찰 요약" className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
    <MetricCard accent="primary" label="관찰 종목" value={String(view.rows.length)}/>
    <MetricCard accent="gold" label="WATCH" value={String(counts[H_WATCH] ?? 0)}/>
    <MetricCard accent="indigo" label="REJECT" value={String(counts[H_REJECT] ?? 0)}/>
    <MetricCard accent="green" label="APPROVE" value={String(counts[H_APPROVE] ?? 0)}/>
    <MetricCard accent="bluegreen" label="최근 결정" value={recent ?? "-"}/>
  </section>;
}

function Watching({ view }: { view: HForwardView }) {
  const rows = byRecentDecision(view.rows);
  return <section aria-labelledby="h-watch-title" className="mb-7">
    <h2 id="h-watch-title" className="mb-3 font-semibold">현재 관찰 종목</h2>
    {!rows.length ? <EmptyState title="현재 관찰 중인 종목이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>종목</th><th>현재 상태</th><th>결정일</th><th>다음 평가</th></tr></thead>
      <tbody>{rows.map(row => { const next = nextEvaluation(row); return <tr key={row.ticker}>
        <td className="font-bold text-foreground">{row.ticker}</td>
        <td>{decisionBadge(row.decision)}</td>
        <td>{row.decision_time ? kstDate(row.decision_time) : "-"}</td>
        <td>{next ? `${next.horizon} · ${monthDay(next.session)}` : "-"}</td>
      </tr>; })}</tbody></table></div>}
  </section>;
}

function RecentDecisions({ view, paper = false }: { view: HForwardView; paper?: boolean }) {
  const rows = byRecentDecision(view.rows.filter(row => row.decision));
  return <section aria-labelledby="h-decisions-title" className="mb-7">
    <h2 id="h-decisions-title" className="mb-3 font-semibold">최근 결정</h2>
    {!rows.length ? <EmptyState title="아직 기록된 결정이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>결정</th><th>종목</th><th>결정 시각</th><th>현재 상태</th><th>사유</th>{paper && <th>Paper 조치</th>}</tr></thead>
      <tbody>{rows.map(row => <tr key={row.ticker}>
        <td>{decisionBadge(row.decision)}</td>
        <td className="font-bold text-foreground">{row.ticker}</td>
        <td>{kstTime(row.decision_time)}</td>
        <td>{observationState(row)}</td>
        <td className="text-foreground-secondary">{decisionReason(row)}</td>
        {paper && <td>{paperAction(view, row)}</td>}
      </tr>)}</tbody></table></div>}
  </section>;
}

/** What the paper book did with the issuer's current decision. A decision from before the paper
 *  launch was never a paper signal, so it reads "관찰만" rather than a missing action. */
function paperAction(view: HForwardView, row: HForwardView["rows"][number]): string {
  const key = `${row.ticker}|${row.thesis_version ?? ""}|${row.decision_time ?? ""}`;
  const action = view.paper_actions?.[key];
  return action ? (ACTION_LABEL[action] ?? action) : "관찰만";
}

function ForwardProgress({ view }: { view: HForwardView }) {
  const progress = horizonProgress(view.maturity);
  const reached = Object.values(view.evaluation.sample_reached || {});
  const done = reached.length > 0 && reached.every(Boolean);
  return <section aria-labelledby="h-forward-title">
    <h2 id="h-forward-title" className="mb-3 font-semibold">Forward 평가</h2>
    <div className="panel p-5" data-h-forward="">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4">
        {progress.map(p => <div key={p.horizon}>
          <dt className="label">{p.horizon}</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums text-foreground">{p.matured} / {p.total}</dd>
          <dd className="mt-0.5 text-xs text-muted">대기 {p.pending}</dd>
        </div>)}
      </dl>
      <p className="mt-4 text-sm text-foreground-secondary">{done ? "평가 표본이 모두 쌓였습니다." : "표본이 쌓이는 중입니다. 평가 완료 전에는 판정이 보류되는 것이 정상입니다."}</p>
    </div>
  </section>;
}
