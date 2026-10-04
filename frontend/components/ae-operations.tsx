"use client";

/** The operating views: the A/E/H performance board, the frozen A/E paper gate, the 50/50 A+E
 *  portfolio simulation, and the research history that says which strategies are closed.
 *
 *  Every figure is the backend's (GET /strategies/performance, /portfolio, /strategies). Nothing is
 *  recomputed here: a missing value renders "N/A" with the backend's reason as its title, a zero
 *  count renders 0, and no strategy name is typed into this file.
 *
 *  Strategy H joined the board in H-V2-D7. Its column comes from the same pure calculator A's and
 *  E's do, so none of their figures moved; what H does *not* join is the Combined column and the
 *  portfolio simulation, because both sum initial equities and H has no capital book. The gate panel
 *  stays A/E: the frozen gate contract does not list H, and H's own state is shown beside it. */

import Link from "next/link";
import { pnlTone } from "@/components/daily-performance";
import { EquitySparkline, StrategyPositions, StrategyTrades } from "@/components/strategy-runtime";
import { EmptyState, LoadingState, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { formatSignedUsd, formatUsd } from "@/lib/format";
import {
  ALL_STRATEGIES, LIFECYCLE_LABELS, OPERATION_LABELS, PAPER_MODES, RESEARCH_LABELS,
  STRATEGY_A, STRATEGY_E, STRATEGY_H, selectorOptions, strategyLabel,
  strategiesApi,
  type GateResult, type PerformanceBoard, type PortfolioView, type StrategyMetrics, type StrategyRow,
} from "@/lib/strategies";
import { HBoardRow } from "@/components/h-forward";

const NA = "N/A";

function pct(value: string | null | undefined, signed = false): string | null {
  if (value == null) return null;
  const n = Number(value) * 100;
  return `${signed && n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function ratio(value: string | null | undefined): string | null {
  return value == null ? null : Number(value).toFixed(2);
}

function holding(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  if (seconds < 3600) return `${Math.round(seconds / 60)}분`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}시간`;
  return `${(seconds / 86400).toFixed(1)}일`;
}

function Cell({ value, reason, tone }: { value: string | null; reason?: string; tone?: string }) {
  if (value == null) return <td className="text-right text-muted" title={reason} data-na={reason || ""}>{NA}</td>;
  return <td className={`text-right tabular-nums ${tone ?? ""}`}>{value}</td>;
}

function signTone(value: string | null | undefined) {
  return value == null ? "" : pnlTone(value);
}

type Row = { label: string; key: keyof StrategyMetrics; render: (m: StrategyMetrics) => string | null; signed?: boolean };

const METRIC_ROWS: Row[] = [
  { label: "Trades", key: "trades", render: m => String(m.trades) },
  { label: "Gross PnL", key: "gross_pnl", render: m => m.gross_pnl == null ? null : formatSignedUsd(m.gross_pnl), signed: true },
  { label: "Costs", key: "costs", render: m => m.costs == null ? null : formatUsd(m.costs) },
  { label: "Net PnL", key: "net_pnl", render: m => m.net_pnl == null ? null : formatSignedUsd(m.net_pnl), signed: true },
  { label: "Return", key: "return", render: m => pct(m.return, true), signed: true },
  { label: "Win Rate", key: "win_rate", render: m => pct(m.win_rate) },
  { label: "PF", key: "pf", render: m => ratio(m.pf) },
  { label: "Expectancy", key: "expectancy", render: m => m.expectancy == null ? null : formatSignedUsd(m.expectancy), signed: true },
  { label: "MDD", key: "mdd", render: m => pct(m.mdd) },
  { label: "Avg MFE", key: "avg_mfe", render: m => m.avg_mfe },
  { label: "Avg MAE", key: "avg_mae", render: m => m.avg_mae },
  { label: "Avg Holding", key: "avg_holding_seconds", render: m => holding(m.avg_holding_seconds) },
];

/** A, E and the A+E combined column for one book, each value exactly as the backend sent it.
 *  ``official`` is ACCOUNTING_V1 from the official start (what the gate reads); ``legacy`` is the V0
 *  rows as recorded, shown for reference and never mixed with official. */
export function PerformanceTable({ board, rows, book = "official", strategy = ALL_STRATEGIES }: {
  board: PerformanceBoard; rows: StrategyRow[]; book?: "official" | "legacy";
  /** ALL, or one strategy id. ALL shows every operating strategy plus the A+E Combined column. */
  strategy?: string;
}) {
  const official = book === "official";
  const all = strategy === ALL_STRATEGIES;
  // Which strategies have a column is the registry's answer, not a list typed here.
  const operating = rows.filter(r => r.enabled && PAPER_MODES.includes(r.mode) && board.strategies[r.strategy_id])
    .filter(r => all || r.strategy_id === strategy);
  const columns: Array<{ id: string; label: string; metrics: StrategyMetrics }> = [
    ...operating.map(row => ({ id: row.strategy_id, label: row.short_name || row.display_name,
                               metrics: board.strategies[row.strategy_id][book] })),
    // Combined is A+E only. It is omitted when one strategy is selected, and when H is that one.
    ...(all && board.strategies[STRATEGY_A] && board.strategies[STRATEGY_E]
      ? [{ id: "COMBINED", label: "Combined", metrics: official ? board.combined : board.legacy_combined }]
      : []),
  ];
  const showH = official && board.h_forward && (all || strategy === STRATEGY_H);
  const shows = (id: string) => columns.some(column => column.id === id);
  const hasCombined = shows("COMBINED");
  const clock = board.paper_clock;
  const titleId = official ? "ae-performance-title" : "ae-legacy-title";
  return <section aria-labelledby={titleId} className="mb-7 min-w-0" data-book={book}>
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id={titleId} className="font-semibold">{official ? "Official Paper · 전략별 성과" : "Legacy Paper (V0) · 참고용"}</h2>
      <p className="text-xs text-muted">{official
        ? `ACCOUNTING_V1 · ${clock.official_paper_start ? `${clock.official_paper_start}부터` : "공식 시작 전"} · 통화 ${board.currency}`
        : "ACCOUNTING_V0 · 공식 평가·게이트·합산에서 제외"}</p>
    </div>
    {official && clock.status === "NOT_STARTED" && <p className="mb-3 rounded-lg border border-line bg-surface-alt px-3 py-2 text-xs text-foreground-secondary" data-clock="NOT_STARTED">
      공식 Paper 시계가 아직 시작되지 않았습니다. V1 회계와 E 비용 계약이 배포·확인된 뒤 첫 세션부터 집계합니다.</p>}
    <div className="table-wrap relative">
      <table className="analysis-table min-w-[480px]">
        <caption className="sr-only">{official ? "공식 전략별 성과와 합산" : "레거시 전략별 성과와 합산"}</caption>
        <thead><tr><th scope="col">Metric</th>{columns.map(c => <th key={c.id} scope="col" className="text-right" data-column={c.id}>{c.label}</th>)}</tr></thead>
        <tbody>
          {METRIC_ROWS.map(row => <tr key={row.label}>
            <th scope="row" className="font-medium">{row.label}</th>
            {columns.map(c => <Cell key={c.id} value={row.render(c.metrics)} reason={c.metrics.na?.[row.key as string]}
              tone={row.signed ? signTone(c.metrics[row.key] as string | null) : undefined}/>)}
          </tr>)}
          <tr><th scope="row" className="font-medium">Operating Sessions</th>
            {columns.map(c => <Cell key={c.id} value={String(c.metrics.operating_sessions)}/>)}</tr>
          {showH && board.h_forward
            && <HBoardRow counts={board.h_forward.decision_counts} maturity={board.h_forward.maturity}/>}
        </tbody>
      </table>
    </div>
    {/* A note is shown only when the thing it describes is on screen: under a single strategy the
        Combined, E-book and H-cohort sentences describe columns that are not there. */}
    <ul className="mt-3 space-y-1 text-xs text-muted">
      {official ? <>
        {hasCombined && <li>Combined는 두 공식 장부의 거래와 일별 손익을 더한 회계 합계입니다. 배분 규칙이 아닙니다.</li>}
        {(shows(STRATEGY_A) || shows(STRATEGY_E)) && <li>MFE·MAE는 두 원장 모두 장중 경로를 기록하지 않아 N/A입니다.</li>}
        {shows(STRATEGY_E) && <li>E는 {board.books[STRATEGY_E] || "-"} 장부만 집계합니다.</li>}
        {hasCombined && board.combined_definition && <li>{board.combined_definition}</li>}
        {shows(STRATEGY_H) && board.h_forward && <li>
          H는 자본 장부가 없어 금액 지표가 N/A이고 Combined·포트폴리오 합산에 들어가지 않습니다.
          결정 상태는 APPROVE {board.h_forward.decision_counts.APPROVE ?? 0} ·
          WATCH {board.h_forward.decision_counts.WATCH ?? 0} ·
          REJECT {board.h_forward.decision_counts.REJECT ?? 0}이며 포지션 0은 정상입니다.
        </li>}
        {shows(STRATEGY_E) && Object.entries(board.excluded).map(([name, info]) => <li key={name}>{name}: 거래 {info.trades}건 · 세션 {info.sessions} · {info.reason}</li>)}
      </> : <li className="tone-warning" data-accounting-warning="">
        V0 순손익은 체결가에 이미 들어간 스프레드·슬리피지를 비용으로 한 번 더 뺀 기록 그대로입니다. 원본은 수정하지 않으며,
        V1 재산정값은 원장(ledger)의 recomputed_v1_net_pnl에 따로 있습니다.</li>}
    </ul>
  </section>;
}

/** ALL / A / E / H. One strategy at a time, or every operating one side by side. */
export function StrategySelector({ rows, value, onChange }: {
  rows: StrategyRow[]; value: string; onChange: (id: string) => void;
}) {
  const options = selectorOptions(rows);
  return <nav aria-label="전략 선택" className="mb-5 flex gap-2 overflow-x-auto" data-strategy-selector={value}>
    {options.map(option => {
      const active = option.id === value;
      return <button key={option.id} type="button" onClick={() => onChange(option.id)}
        aria-pressed={active} data-strategy-option={option.id}
        className={`inline-flex h-9 shrink-0 items-center justify-center rounded-lg border px-3.5 text-sm font-semibold transition-colors focus-visible:ring-2 focus-visible:ring-primary ${active ? "border-primary bg-primary-soft text-primary" : "border-line bg-surface text-foreground-secondary hover:border-primary hover:bg-primary-soft hover:text-primary"}`}>
        {option.label}
      </button>;
    })}
  </nav>;
}

const VERDICT_TONE: Readonly<Record<string, "success" | "warning" | "danger">> = { PASS: "success", INCONCLUSIVE: "warning", FAIL: "danger" };

/** The frozen paper gate per strategy: the verdict, and which condition holds it back. */
export function GatePanel({ gate, rows, board, strategy = ALL_STRATEGIES }: {
  gate: Record<string, GateResult>; rows: StrategyRow[]; board?: PerformanceBoard; strategy?: string;
}) {
  const h = board?.h_forward;
  const shown = Object.values(gate).filter(r => strategy === ALL_STRATEGIES || r.strategy_id === strategy);
  if (!shown.length) return null;
  return <section aria-labelledby="ae-gate-title" className="mb-7">
    <h2 id="ae-gate-title" className="mb-1 font-semibold">Paper 평가 게이트</h2>
    <p className="mb-3 text-xs text-muted">결과를 보기 전에 고정한 기준(AE_PAPER_EVALUATION_GATE_V1)입니다. 결과에 따라 바꾸지 않습니다.</p>
    {h && <p className="mb-3 rounded-lg border border-line bg-surface-alt px-3 py-2 text-xs text-foreground-secondary"
      data-gate-excluded={STRATEGY_H}>
      Strategy H는 이 게이트의 대상이 아닙니다(동결 계약에 H 항목이 없습니다). H는 {h.contract.contract_id} 아래에서
      {" "}{h.evaluation.state} · {h.evaluation.verdict}이며, 21D·63D 표본이 찰 때까지 INCONCLUSIVE가 정상입니다.
    </p>}
    <div className="grid gap-4 xl:grid-cols-2">
      {shown.map(result => {
        const row = rows.find(r => r.strategy_id === result.strategy_id);
        return <article key={result.strategy_id} className="panel min-w-0 p-4" data-gate={result.strategy_id}>
          <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold">{row ? row.display_name : result.strategy_id}</h3>
            <StatusBadge value={result.verdict} tone={VERDICT_TONE[result.verdict]}/>
          </header>
          {result.reasons.length > 0 && <p className="mb-2 text-xs text-foreground-secondary">{result.reasons.join(" · ")}</p>}
          <div className="table-wrap relative"><table className="analysis-table text-xs">
            <thead><tr><th scope="col">조건</th><th scope="col" className="text-right">값</th><th scope="col">기준</th><th scope="col">상태</th></tr></thead>
            <tbody>{result.conditions.map(c => <tr key={c.condition}>
              <th scope="row" className="font-medium">{c.condition}</th>
              <td className="text-right tabular-nums">{c.value ?? NA}</td>
              <td className="whitespace-nowrap">{c.threshold}</td>
              <td className="whitespace-nowrap"><StatusBadge value={c.status} tone={c.status === "PASS" ? "success" : c.status === "FAIL" ? "danger" : "neutral"}/></td>
            </tr>)}</tbody>
          </table></div>
        </article>;
      })}
    </div>
  </section>;
}

function overlapText(o?: { both: number; either: number }) {
  return o ? `${o.both} / ${o.either}일` : NA;
}

/** The 50/50 risk-budget simulation and the live exposure, labelled as a simulation throughout. */
export function PortfolioPanel({ view, rows }: { view: PortfolioView; rows: StrategyRow[] }) {
  const b = view.baseline;
  const name = (id: string) => rows.find(r => r.strategy_id === id)?.short_name || id;
  const stats: Array<[string, string, string | undefined]> = [
    ["Risk budget", Object.entries(b.risk_budget).map(([id, w]) => `${name(id)} ${pct(w)}`).join(" · "), "배분 규칙으로 쓰지 않음"],
    ["공통 운영 기간", b.window ? `${b.window.start} ~ ${b.window.end}` : NA, `${b.days}일`],
    ["Combined return (sim)", pct(b.combined_return, true) ?? NA, "50/50 일별 수익률 합성"],
    ["Combined MDD (sim)", pct(b.combined_mdd) ?? NA, undefined],
    ["A/E 상관계수", b.correlation ?? NA, b.na.correlation],
    ["손실일 겹침", overlapText(b.losing_day_overlap), "둘 다 손실 / 어느 한쪽 손실"],
    ["수익일 겹침", overlapText(b.winning_day_overlap), "둘 다 수익 / 어느 한쪽 수익"],
    ["종목 겹침", `${b.symbol_overlap.both} / ${b.symbol_overlap.either}종목`, b.symbol_overlap.symbols?.join(", ") || undefined],
    ["섹터 겹침", NA, b.na.sector_overlap],
    ["합산 Net PnL (실제 장부)", view.combined.net_pnl == null ? NA : formatSignedUsd(view.combined.net_pnl), undefined],
    ["합산 MDD (실제 장부)", pct(view.combined.mdd) ?? NA, undefined],
  ];
  return <section aria-labelledby="ae-portfolio-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id="ae-portfolio-title" className="font-semibold">A+E 포트폴리오 · {view.book === "official" ? "Official (V1)" : "Legacy (V0)"}</h2>
      <StatusBadge value={b.mode} label="SIMULATION BASELINE" tone="neutral"/>
    </div>
    <dl className="panel grid gap-x-6 gap-y-3 p-4 text-sm sm:grid-cols-2 xl:grid-cols-3">
      {stats.map(([label, value, detail]) => <div key={label} data-portfolio-stat={label}>
        <dt className="label">{label}</dt>
        <dd className="mt-0.5 font-medium tabular-nums text-foreground">{value}</dd>
        {detail && <dd className="text-[11px] text-muted">{detail}</dd>}
      </div>)}
    </dl>
    <p className="mt-2 text-xs text-muted">{b.definition}</p>
    <h3 className="mb-2 mt-5 text-sm font-semibold">현재 노출</h3>
    {view.exposure.per_symbol.length === 0
      ? <EmptyState title="보유 포지션 없음" description="두 전략 모두 현재 보유 종목이 없습니다."/>
      : <div className="table-wrap relative"><table className="analysis-table min-w-[420px]">
          <thead><tr><th scope="col">종목</th><th scope="col" className="text-right">합계 수량</th><th scope="col" className="text-right">원가</th><th scope="col">전략별</th></tr></thead>
          <tbody>{view.exposure.per_symbol.map(s => <tr key={s.symbol}>
            <th scope="row">{s.symbol}</th><td className="text-right tabular-nums">{s.quantity}</td>
            <td className="text-right tabular-nums">{formatUsd(s.cost_basis)}</td>
            <td>{Object.entries(s.by_strategy).map(([id, v]) => `${name(id)} ${v.quantity}`).join(" · ")}</td>
          </tr>)}</tbody>
        </table></div>}
    <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
      {Object.entries(view.cash_usage).map(([id, c]) => <div key={id} className="panel p-3" data-cash={id}>
        <dt className="label">{name(id)} 현금 사용</dt>
        <dd className="mt-0.5 tabular-nums">{c.cash_share == null ? NA : `현금 ${pct(c.cash_share)}`}</dd>
        {c.note && <dd className="text-muted">{c.note}</dd>}
      </div>)}
    </dl>
  </section>;
}

/** One strategy's own book, inline on the comparison screen: what it holds now, what it closed and
 *  how its equity moved.
 *
 *  It fetches per strategy id, so two strategies' rows can never end up in one table, and it is
 *  mounted with a key so selecting another strategy refetches instead of showing the previous one.
 *  A strategy with no capital book (Strategy H) says so rather than rendering empty tables. */
export function StrategyBookPanel({ row }: { row: StrategyRow }) {
  const id = row.strategy_id;
  const state = useApi(() => Promise.all([
    strategiesApi.positions(id), strategiesApi.trades(id, 50), strategiesApi.equity(id),
  ]), 60_000);
  const label = row.short_name || row.display_name;
  if (state.loading) return <LoadingState/>;
  if (!state.data) {
    return <EmptyState title={`${label} 장부 조회 실패`} description={state.error || undefined}/>;
  }
  const [positions, trades, equity] = state.data;
  return <section aria-labelledby="book-title" className="mb-7 min-w-0" data-strategy-book={id}>
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id="book-title" className="font-semibold">{strategyLabel(row)} · 자체 장부</h2>
      <p className="text-xs text-muted">{equity.source}{equity.note ? ` · ${equity.note}` : ""}</p>
    </div>
    <div className="grid gap-4 xl:grid-cols-2">
      <div className="panel min-w-0 p-4">
        <h3 className="mb-2 text-sm font-semibold">보유 포지션</h3>
        <div className="table-wrap relative"><StrategyPositions positions={positions}/></div>
      </div>
      <div className="panel min-w-0 p-4">
        <h3 className="mb-2 text-sm font-semibold">자산 추이</h3>
        {equity.points.length ? <EquitySparkline equity={equity}/>
          : <EmptyState title="자산 곡선 없음" description={equity.note || "기록된 일별 자산이 없습니다."}/>}
      </div>
    </div>
    <div className="panel mt-4 min-w-0 p-4">
      <h3 className="mb-2 text-sm font-semibold">최근 거래</h3>
      <div className="table-wrap relative"><StrategyTrades trades={trades}/></div>
    </div>
  </section>;
}

/** Research history: every strategy the registry lists, research verdict beside operating state. */
export function ResearchHistory({ rows }: { rows: StrategyRow[] }) {
  return <div className="table-wrap relative">
    <table className="analysis-table min-w-[560px]">
      <caption className="sr-only">전략 연구 이력</caption>
      <thead><tr><th scope="col">전략</th><th scope="col">Research</th><th scope="col">Operations</th><th scope="col">종결일</th><th scope="col">메모</th></tr></thead>
      <tbody>{rows.map(row => {
        const closed = row.research_lifecycle === "CLOSED";
        return <tr key={row.strategy_id} data-history={row.strategy_id}>
          <th scope="row"><span className="font-semibold">{row.display_name}</span><span className="block text-[11px] text-muted">{row.variant_label || row.version}</span></th>
          <td><StatusBadge value={row.research_lifecycle || "-"} label={RESEARCH_LABELS[row.research_lifecycle || ""] || row.research_lifecycle || "-"}
            tone={closed ? "neutral" : "success"}/></td>
          <td><StatusBadge value={row.lifecycle || "-"}
            label={LIFECYCLE_LABELS[row.lifecycle || ""] || (closed ? "RETIRED" : "ACTIVE")}
            tone={closed ? "neutral" : "success"}/></td>
          <td className="tabular-nums">{row.closed_on || "-"}</td>
          <td className="text-xs text-foreground-secondary">{row.note}{row.closeout && <span className="block text-muted">{row.closeout}</span>}</td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}

/** The experiments that are not strategies, and so have no registry row of their own.
 *
 *  Strategy A's exit-rule variants (`/shadow`) are the only one: five exit rules replayed over A's
 *  own entries, confusingly labelled A to E like the strategies themselves. The screen is kept
 *  reachable rather than deleted, but it is not a tab, because the live paper runtime writes no
 *  `shadow_trades` row and the table is empty. */
export function ExperimentHistory() {
  return <section aria-labelledby="experiment-title" className="mt-7">
    <h2 id="experiment-title" className="mb-1 font-semibold">전략이 아닌 실험</h2>
    <p className="mb-3 text-xs text-muted">레지스트리에 전략으로 올라가지 않는 비교 실험입니다. 기록은 지우지 않습니다.</p>
    <div className="panel p-4" data-experiment="A_EXIT_VARIANTS">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-semibold">A 청산규칙 변형 (A~E)</span>
        <StatusBadge value="NO_DATA" label="기록 없음" tone="neutral"/>
      </div>
      <p className="text-sm text-foreground-secondary">
        Strategy A가 실제로 잡은 진입을 청산 규칙만 바꿔 다시 돌린 비교입니다
        (당일청산·ATR 1.5 / Day2 허용·ATR 1.0·1.5·2.0 / 당일청산·구조손절).
      </p>
      <p className="mt-1 text-xs text-muted">
        여기서의 A~E는 청산 변형 이름이고 Strategy A·E와는 다른 것입니다. 운영 런타임이 shadow 기록을
        쓰지 않아 표가 비어 있어 전략 탭에서는 내렸습니다. 화면과 데이터는 그대로 있습니다.
      </p>
      <p className="mt-3">
        <Link href="/shadow" className="btn-action-secondary-compact">섀도 변형 화면 열기</Link>
      </p>
    </div>
  </section>;
}

/** What a closed strategy's old screen shows now: that it is closed, and where the record lives.
 *  No figure is rendered, so a closed strategy can never read as a running one. */
export function ClosedStrategyNotice({ row, fallbackId }: { row?: StrategyRow; fallbackId: string }) {
  return <section aria-label="종결된 전략" className="panel p-6" data-closed-strategy={row?.strategy_id || fallbackId}>
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <StatusBadge value="CLOSED" label="CLOSED · 연구 종결" tone="neutral"/>
      {row?.closed_on && <span className="text-xs text-muted">{row.closed_on}</span>}
    </div>
    <p className="text-sm text-foreground">{row ? `${strategyLabel(row)}는 운영 전략이 아닙니다.` : "이 전략은 운영 전략이 아닙니다."}</p>
    {row?.note && <p className="mt-1 text-sm text-foreground-secondary">{row.note}</p>}
    {row?.closeout && <p className="mt-2 text-xs text-muted">종결 기록: {row.closeout}</p>}
    <p className="mt-4 flex flex-wrap gap-2">
      <Link href="/strategy-history" className="btn-action-secondary-compact">연구 이력 보기</Link>
      <Link href="/dashboard" className="btn-action-secondary-compact">운영 대시보드</Link>
    </p>
  </section>;
}

export { OPERATION_LABELS };
