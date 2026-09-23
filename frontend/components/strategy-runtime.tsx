"use client";

/** Registry-driven strategy views: one card shape for every strategy, plus E's own status panels.
 *
 *  A card never mixes two strategies' money, and a strategy with no trade yet says so in words
 *  instead of printing a zero. Strategy E additionally separates its provisional and official
 *  books; the card labels which book the figures come from. */

import Link from "next/link";
import { CumulativePerformance, SignedMoneyValue, UsdCardValue } from "@/components/daily-performance";
import { EmptyState, MetricCard, StatusBadge } from "@/components/ui";
import { formatKrw, formatUsd, dash, etTime, usdToDisplayKrw } from "@/lib/format";
import {
  bootstrapLabel, emptyLabel, evidenceLabel, OFFICIAL, PROVISIONAL, STRATEGY_E,
  type BootstrapState, type StrategyAccount, type StrategyEquity, type StrategyPosition,
  type StrategyRow, type StrategyStatus, type StrategyTrade, type UniverseState,
} from "@/lib/strategies";

const RUNTIME_TONE: Readonly<Record<string, "success" | "warning" | "danger" | "neutral">> = {
  RUNNING: "success", POSITION_OPEN: "success", DECIDED: "success", COMPLETE: "neutral",
  WAITING: "neutral", NO_DECISION: "warning", NO_SESSION_YET: "neutral",
  NO_ACTIVE_SIM_BROKER: "warning", ERROR: "danger",
};

export type StrategyBundle = { row: StrategyRow; status: StrategyStatus; account: StrategyAccount; equity: StrategyEquity };

/** Every money figure on these screens renders through Strategy A's own formatters (USD primary,
 *  KRW secondary through lib/fx's single fixed rate) and Strategy A's own PnL tone. */
function usd(value: string | null | undefined) {
  return formatUsd(value);
}

/** One card per strategy: what it is, what it is doing, and its own money. */
export function StrategyCard({ bundle, href }: { bundle: StrategyBundle; href?: string }) {
  const { row, status, account } = bundle;
  const empty = emptyLabel(account);
  // A book with no session has no PnL to show; "-" and the reason below, never a converted zero.
  const books = account.books;
  const settled = books ? (books[account.evidence_status || ""]?.sessions ?? 0) > 0 : true;
  const runtime = status.runtime_status || row.runtime_status || "UNKNOWN";
  return <article className="panel p-5" data-strategy={row.strategy_id}>
    <header className="mb-4 flex flex-wrap items-start justify-between gap-2">
      <div>
        <h3 className="text-lg font-semibold text-foreground">
          {href ? <Link href={href} className="hover:text-primary">{row.display_name}</Link> : row.display_name}
        </h3>
        <p className="mt-1 text-xs text-muted">{row.version} · {row.mode} · {row.market_data_source}</p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <StatusBadge value={runtime} tone={RUNTIME_TONE[runtime] ?? "neutral"}/>
        {row.evidence_status && <StatusBadge value={row.evidence_status} label={evidenceLabel(row.evidence_status)}
          tone={row.evidence_status === OFFICIAL ? "success" : "warning"}/>}
      </div>
    </header>
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm sm:grid-cols-3">
      <Field label="초기 자본" value={<UsdCardValue value={account.initial_equity}/>}/>
      <Field label="현재 자산" value={<UsdCardValue value={account.current_equity}/>}/>
      <Field label="오늘 손익" value={<SignedMoneyValue value={settled ? account.today_pnl : null}/>}/>
      <Field label="누적 수익" value={<CumulativePerformance pnl={settled ? account.total_pnl : null}
        baseline={account.initial_equity} note={settled ? undefined : undefined}/>}/>
      <Field label="보유 포지션" value={String(account.open_positions)}/>
      <Field label="오늘 청산" value={account.closed_trades_today == null ? "-" : String(account.closed_trades_today)}/>
      <Field label="최근 결정" value={status.last_decision ? status.last_decision.slice(0, 12) : "-"}/>
      <Field label="세션" value={dash(status.session)}/>
      <Field label="갱신" value={status.last_update ? etTime(status.last_update) : "-"}/>
    </dl>
    {empty && <p className="mt-4 rounded-lg border border-line bg-surface-alt px-3 py-2 text-xs text-foreground-secondary"
      data-empty-reason="">{empty}</p>}
  </article>;
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return <div><dt className="label">{label}</dt><dd className="mt-0.5 font-medium tabular-nums text-foreground">{value}</dd></div>;
}

export function StrategyCards({ bundles, hrefs = {} }: { bundles: StrategyBundle[]; hrefs?: Record<string, string> }) {
  if (!bundles.length) return <EmptyState title="운영 중인 전략이 없습니다." description="전략 레지스트리에 활성 전략이 없습니다."/>;
  return <section aria-label="전략 현황" className="mb-7 grid gap-4 lg:grid-cols-2">
    {bundles.map(bundle => <StrategyCard key={bundle.row.strategy_id} bundle={bundle} href={hrefs[bundle.row.strategy_id]}/>)}
  </section>;
}

/** Strategy E's decision-frame counts, exactly as the session record wrote them. */
export function StrategyEStatus({ status }: { status: StrategyStatus }) {
  const d = status.detail || {};
  const cards: Array<[string, string, string | undefined]> = [
    ["Runtime", status.runtime_status || "-", status.session ? `세션 ${status.session}` : undefined],
    ["Evidence", evidenceLabel(status.evidence_status), status.evidence_status || undefined],
    ["Market source", d.market_data_source || "-", "premarket · RVOL · H5 · 체결"],
    ["RVOL threshold", d.rvol_threshold == null ? "-" : d.rvol_threshold.toFixed(1), "동결 규칙에서 읽음"],
    ["Canonical universe", num(d.canonical_universe), "D-1 적격 종목"],
    ["RVOL ready", num(d.rvol_ready), "분모 보유"],
    ["RVOL missing", num(d.rvol_missing), "이력 부족 → H5 UNKNOWN"],
    ["Market data unavailable", num(d.market_data_unavailable), "소스 거부 → 선택 불가"],
    ["H5 true", num(d.h5_true), "후보"],
    ["H5 unknown", num(d.h5_unknown), "판정 불가"],
    ["Selected", d.selected ? String(d.selected.length) : "-", d.selected?.length ? d.selected.join(", ") : "최대 3"],
  ];
  return <section aria-labelledby="e-status-title" className="mb-7">
    <h2 id="e-status-title" className="mb-3 font-semibold">E-MAX V1 상태</h2>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map(([label, value, detail]) => <MetricCard key={label} label={label} value={value} detail={detail}/>)}
    </div>
    {d.no_decision_reason && <p className="mt-3 rounded-lg border border-warning bg-surface-alt px-3 py-2 text-xs tone-warning">
      NO_DECISION · {d.no_decision_reason}</p>}
  </section>;
}

function num(value: number | null | undefined) {
  return value == null ? "-" : value.toLocaleString();
}

/** The RVOL bootstrap, with no ETA invented by the browser. */
export function BootstrapProgress({ state }: { state?: BootstrapState }) {
  if (!state || !state.available) {
    return <EmptyState title="RVOL 부트스트랩 상태 없음" description={state?.reason || "수집기 보고가 아직 없습니다."}/>;
  }
  const ready = state.ready ?? 0;
  const total = state.symbols ?? 0;
  const percent = state.percent ?? (total ? (100 * ready) / total : 0);
  return <section aria-labelledby="bootstrap-title" className="panel mb-7 p-5" data-bootstrap-status={state.status}>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id="bootstrap-title" className="font-semibold">RVOL Bootstrap</h2>
      <StatusBadge value={state.status} tone={state.status === "COMPLETE" ? "success" : "warning"}/>
    </div>
    <p className="text-2xl font-bold tabular-nums text-foreground">{bootstrapLabel(state)}</p>
    <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-surface-alt" role="progressbar"
      aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100} aria-label="RVOL 부트스트랩 진행률">
      <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}/>
    </div>
    <dl className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
      <Field label="Target" value={state.target_sessions ? `${state.target_sessions} sessions` : "-"}/>
      <Field label="이력 소진" value={num(state.history_exhausted)}/>
      <Field label="Last update" value={state.last_update ? etTime(state.last_update) : "-"}/>
      <Field label="Estimated completion" value={state.estimated_completion ? etTime(state.estimated_completion) : "-"}/>
    </dl>
    {!state.estimated_completion && <p className="mt-3 text-xs text-muted">{state.estimate_note || "완료 예정 시각은 런타임이 제공할 때만 표시합니다."}</p>}
  </section>;
}

export function UniversePanel({ universe }: { universe?: UniverseState }) {
  if (!universe) return null;
  const ready = universe.status === "READY";
  return <section className="panel mb-7 p-5" data-universe-status={universe.status}>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-semibold">Canonical universe</h2>
      <StatusBadge value={universe.status || "UNKNOWN"} tone={ready ? "success" : "danger"}/>
    </div>
    <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
      <Field label="Target session" value={dash(universe.target_session)}/>
      <Field label="As-of (D-1)" value={dash(universe.asof_session)}/>
      <Field label="Symbols" value={num(universe.symbol_count)}/>
      <Field label="Source" value={dash(universe.source)}/>
    </dl>
    {!ready && <p className="mt-3 rounded-lg border border-danger bg-surface-alt px-3 py-2 text-xs tone-danger">
      {universe.identity || universe.reason || "D-1 정합성 미확인"} · E는 NO_DECISION으로 닫히고 A는 계속 실행됩니다.</p>}
  </section>;
}

/** Positions carry their strategy: A / NVDA and E / NVDA are two rows, never one. */
export function StrategyPositions({ positions }: { positions: StrategyPosition[] }) {
  if (!positions.length) return <EmptyState title="보유 포지션 없음"/>;
  return <table className="data-table"><thead><tr>
    <th scope="col">전략</th><th scope="col">종목</th><th scope="col">수량</th>
    <th scope="col">평균가</th><th scope="col">진입</th><th scope="col">증거</th>
  </tr></thead><tbody>
    {positions.map(position => <tr key={`${position.strategy_id}-${position.symbol}`}
      data-position-key={`${position.strategy_id}/${position.symbol}`}>
      <td>{position.strategy_id === STRATEGY_E ? "E" : "A"}</td>
      <td className="font-semibold">{position.symbol}</td>
      <td className="tabular-nums">{dash(position.quantity)}</td>
      <td className="tabular-nums">{usd(position.average_price)}</td>
      <td>{position.opened_at ? etTime(position.opened_at) : "-"}</td>
      <td>{position.evidence_status ? evidenceLabel(position.evidence_status) : "-"}</td>
    </tr>)}
  </tbody></table>;
}

export function StrategyTrades({ trades }: { trades: StrategyTrade[] }) {
  if (!trades.length) return <EmptyState title="청산된 거래 없음" description="거래가 생기면 세션별로 기록됩니다."/>;
  return <table className="data-table"><thead><tr>
    <th scope="col">세션</th><th scope="col">종목</th><th scope="col">상태</th>
    <th scope="col">진입</th><th scope="col">청산</th><th scope="col">손익</th><th scope="col">증거</th>
  </tr></thead><tbody>
    {trades.map(trade => <tr key={`${trade.session}-${trade.symbol}-${trade.evidence_status}`}>
      <td>{dash(trade.session)}</td>
      <td className="font-semibold">{trade.symbol}</td>
      <td>{dash(trade.status)}</td>
      <td className="tabular-nums">{usd(trade.entry_price)}</td>
      <td className="tabular-nums">{usd(trade.exit_price)}</td>
      <td className="tabular-nums"><SignedMoneyValue value={trade.net_pnl}/></td>
      <td>{trade.evidence_status ? evidenceLabel(trade.evidence_status) : "-"}</td>
    </tr>)}
  </tbody></table>;
}

/** Provisional and official equity, side by side and never concatenated. */
export function EvidenceBooks({ equity }: { equity: StrategyEquity }) {
  const books = equity.books || {};
  return <section aria-labelledby="books-title" className="mb-7">
    <h2 id="books-title" className="mb-3 font-semibold">증거 장부</h2>
    <div className="grid gap-4 sm:grid-cols-2">
      {[PROVISIONAL, OFFICIAL].map(status => {
        const book = books[status];
        const last = book?.points?.[book.points.length - 1];
        return <div key={status} className="panel p-4" data-book={status}>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">{evidenceLabel(status)}</h3>
            {equity.current_book === status && <StatusBadge value="CURRENT" tone="success"/>}
          </div>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Field label="세션" value={String(book?.points?.length ?? 0)}/>
            <Field label="자산" value={usd(last?.equity ?? book?.baseline ?? null)}/>
          </dl>
          {!book?.points?.length && <p className="mt-3 text-xs text-muted">
            {status === OFFICIAL ? "아직 official paper 거래 없음" : "PROVISIONAL · RVOL 부트스트랩 중"}</p>}
        </div>;
      })}
    </div>
    <p className="mt-3 text-xs text-muted">{equity.note || "두 장부는 합산하지 않습니다."}</p>
  </section>;
}

/** A compact USD equity line. The existing EquityCurve draws the KRW mock series (a_equity_krw /
 *  b_equity_krw) for Strategy B's screens, so it is left to those screens rather than fed converted
 *  figures; real A/E equity is USD and is drawn as it is stored. */
export function EquitySparkline({ equity, height = 64 }: { equity: StrategyEquity; height?: number }) {
  const points = (equity.points || []).filter(p => p.equity != null);
  if (points.length < 2) {
    return <p className="text-xs text-muted" data-equity-empty="">자산 곡선을 그릴 기록이 아직 없습니다.</p>;
  }
  const values = points.map(p => Number(p.equity));
  const min = Math.min(...values, Number(equity.baseline ?? values[0]));
  const max = Math.max(...values, Number(equity.baseline ?? values[0]));
  const span = max - min || 1;
  const width = 320;
  const last = points[points.length - 1].equity;
  const path = values.map((value, i) => {
    const x = (i / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * height;
    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return <figure className="m-0" data-equity-points={points.length}>
    <svg viewBox={`0 0 ${width} ${height}`} className="h-16 w-full" role="img"
      aria-label={`${equity.strategy_id} 자산 추이 ${points.length}일`} preserveAspectRatio="none">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke"
        className="text-primary"/>
    </svg>
    <figcaption className="mt-1 flex justify-between text-[11px] text-muted">
      <span>{points[0].date}</span>
      <span>{formatUsd(last)} <span className="text-muted">≈&nbsp;{formatKrw(usdToDisplayKrw(last))}</span></span>
    </figcaption>
  </figure>;
}
