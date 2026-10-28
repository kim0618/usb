"use client";

/** The dashboard's strategy card: one shape for every strategy, the same twelve fields in the same
 *  order. A value the ledger does not record is "-", never a computed stand-in.
 *
 *  Money is USD (two decimals) with KRW beneath it, converted by lib/fx's single fixed rate (the
 *  one the page header prints). The state is one of RUNNING / WAITING / ISSUE, folded by the
 *  backend from the raw statuses, which remain on the card for every other screen. */

import Link from "next/link";
import { EmptyState } from "@/components/ui";
import { formatKrw, usdToDisplayKrw } from "@/lib/format";
import type { StrategyBundle } from "@/components/strategy-runtime";
import type { DashboardBlock } from "@/lib/strategies";
import { STATUS_LABEL, STRATEGY_I_TAB, type IGuardDecision, type IPaperView, type IStatus } from "@/lib/strategy-i";

const STATE_DOT: Readonly<Record<DashboardBlock["status"], string>> = {
  RUNNING: "bg-primary", WAITING: "bg-foreground-muted", ISSUE: "bg-danger",
};

const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

function amount(value: string | null | undefined): number | null {
  return value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
}

/** USD and KRW stacked; signed amounts keep their +/- and the shared PnL tone. */
function Money({ value, signed = false }: { value: string | null | undefined; signed?: boolean }) {
  const n = amount(value);
  if (n == null) return <span className="text-foreground-secondary">-</span>;
  const krw = usdToDisplayKrw(n);
  const sign = (x: number) => (x > 0 ? "+" : "");
  const tone = !signed ? "" : n < 0 ? "text-danger" : n > 0 ? "text-success" : "text-foreground-secondary";
  return <span className={tone}>
    <span className="block">{signed ? sign(n) : ""}{usd2.format(n)}</span>
    <span className={`mt-0.5 block text-xs font-medium ${tone || "text-muted"}`}>
      {krw == null ? "-" : `${signed ? sign(krw) : ""}${formatKrw(krw)}`}
    </span>
  </span>;
}

/** "10/13 09:32 KST" from an ISO timestamp; "-" when there is none. */
export function kstStamp(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(date).map(p => [p.type, p.value]));
  return `${parts.month}/${parts.day} ${parts.hour === "24" ? "00" : parts.hour}:${parts.minute} KST`;
}

function periodLine(block: DashboardBlock | undefined): string {
  if (!block) return "-";
  const shadow = block.session_kind === "관찰";
  const day = block.operating_day == null ? "-" : `${shadow ? "관찰" : "운영"} ${block.operating_day}일째`;
  const count = block.session_count == null ? "-" : shadow ? `${block.session_count}세션` : `${block.session_kind} ${block.session_count}세션`;
  return `${day} · ${count}`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="min-w-0">
    <dt className="label">{label}</dt>
    <dd className="mt-1 min-h-[2.75rem] break-words text-base font-semibold tabular-nums text-foreground">{children}</dd>
  </div>;
}

type CardFrameProps = {
  strategyId: string; title: string; href?: string; mode: string; state: DashboardBlock["status"] | undefined;
  period: string; reason: string | null; note?: string | null;
  initial: string | null; equity: string | null; sessionPnl: string | null; net: string | null;
  trades: string; decision: string; lastRunAt: string | null | undefined;
};

/** The card's one layout, shared by the registry strategies and Strategy I. */
function CardFrame(p: CardFrameProps) {
  const { state } = p;
  return <article className="panel flex h-full flex-col p-5" data-strategy={p.strategyId}
    data-state={state || "UNKNOWN"} data-dashboard-card="">
    <header className="flex items-start justify-between gap-3">
      <h3 className="min-w-0 text-lg font-semibold text-foreground">
        {p.href ? <Link href={p.href} className="hover:text-primary">{p.title}</Link> : p.title}
      </h3>
      <span className="shrink-0 rounded-full border border-line px-2.5 py-1 text-[11px] font-bold tracking-wide text-foreground-secondary"
        data-mode="">{p.mode}</span>
    </header>

    <div className="mt-4" data-status-block="">
      <p className="flex items-center gap-2 text-sm font-semibold text-foreground" data-status={state || ""}>
        <span aria-hidden className={`inline-block h-2.5 w-2.5 rounded-full ${state ? STATE_DOT[state] : "bg-foreground-muted"}`}/>
        {state ?? "-"}
      </p>
      <p className="mt-1 text-xs text-muted" data-period="">{p.period}</p>
      {/* Reserved on every card so a healthy card is exactly as tall as one with an issue. */}
      <div className="mt-1 min-h-4 text-xs text-danger">
        {state === "ISSUE" && p.reason && <p data-reason="">{p.reason}</p>}
        {state !== "ISSUE" && p.note && <p className="text-muted" data-note="">{p.note}</p>}
      </div>
    </div>

    <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-3">
      <Field label="초기 자본"><Money value={p.initial}/></Field>
      <Field label="현재 자산"><Money value={p.equity}/></Field>
      <Field label="최근 세션 손익"><Money signed value={p.sessionPnl}/></Field>
      <Field label="공식 누적 손익"><Money signed value={p.net}/></Field>
      <Field label="공식 거래 수">{p.trades}</Field>
      <Field label="최근 결정">{p.decision}</Field>
    </dl>

    <p className="mt-auto pt-5 text-xs text-muted" data-last-run="">마지막 실행 {kstStamp(p.lastRunAt)}</p>
  </article>;
}

export function DashboardStrategyCard({ bundle, href }: { bundle: StrategyBundle; href?: string }) {
  const { row, account, card } = bundle;
  const block = card?.dashboard;
  // H before its paper launch has no capital book: money and trade fields are "-", with the reason on
  // the API's `na`. Once launched, its card carries the H paper account like A's and E's.
  const noBook = card?.forward != null && card?.initial_equity == null;
  const settled = account.books ? (account.books[account.evidence_status || ""]?.sessions ?? 0) > 0 : true;
  const official = card?.official;
  const trades = noBook || !card ? "-" : String(official?.trades ?? card.trades);
  // A book that has recorded no official trade has an official PnL of zero, which is a fact and
  // not a missing figure. Legacy (V0) rows never reach this field: `official` is the V1 book.
  // A strategy with no capital book at all (H) stays a dash.
  const officialTrades = official?.trades ?? card?.trades;
  const net = noBook ? null : (official?.net_pnl ?? card?.net_pnl ?? (officialTrades === 0 ? "0" : null));

  return <CardFrame strategyId={row.strategy_id} title={row.display_name} href={href} mode={block?.mode ?? "-"}
    state={block?.status} period={periodLine(block)} reason={block?.status_reason ?? null}
    initial={noBook ? null : account.initial_equity} equity={noBook ? null : account.current_equity}
    sessionPnl={noBook || !settled ? null : account.today_pnl} net={net} trades={trades}
    decision={block?.last_decision || "-"} lastRunAt={block?.last_run_at}/>;
}

/** Strategy I's card values, in the same fields and the same RUNNING / WAITING / ISSUE fold the
 *  registry cards get from GET /strategies/cards. Every figure is the I API's own: before the first
 *  rebalance there is no account, so equity and session PnL stay a dash. A book with no trade has a
 *  cumulative PnL of zero, the same rule the registry cards follow. */
export type IDashboardSummary = {
  state: DashboardBlock["status"]; period: string; reason: string | null; note: string | null;
  initial: string | null; equity: string | null; sessionPnl: string | null; net: string | null;
  trades: string; decision: string; lastRunAt: string | null;
};

/** Pre-launch, no run yet and a late as-of are all waiting states; only a failed or refused run is an issue. */
const I_STATE: Readonly<Record<IStatus, DashboardBlock["status"]>> = {
  NOT_LAUNCHED: "WAITING", LAUNCHED_NO_RUN: "WAITING", RUNNING: "RUNNING", DATA_DELAYED: "WAITING",
  INPUT_BLOCKED: "ISSUE", ERROR: "ISSUE",
};

/** A guard SKIP after the last completed run: the timer held the run back under host pressure (not an error). */
export function heldGuardSkip(view: IPaperView): IGuardDecision | null {
  const skip = view.guard?.last_skip;
  if (!skip?.at) return null;
  const done = view.last_run?.completed_at;
  return !done || Date.parse(skip.at) > Date.parse(done) ? skip : null;
}

export function iDashboardSummary(view: IPaperView): IDashboardSummary {
  const state = I_STATE[view.status] ?? "ISSUE";
  const a = view.account ?? null;
  const preLaunch = view.launch_signal != null && (!view.last_run || view.last_run.as_of < view.launch_signal);
  // rebalances are returned whole (trades are capped at 200), so the trade count is summed from them
  const tradeCount = view.rebalances ? view.rebalances.reduce((n, r) => n + r.trade_count, 0) : null;
  const latest = view.rebalances?.[0];
  const delay = view.flags?.data_delay;
  return {
    state,
    period: a ? `운영 ${a.operating_sessions}세션` : preLaunch ? `시작 신호일 ${view.launch_signal} 전` : STATUS_LABEL[view.status] ?? "-",
    reason: state === "ISSUE" ? view.reason || STATUS_LABEL[view.status] || view.status : null,
    note: state === "ISSUE" ? null : heldGuardSkip(view) ? "호스트 부하 보호로 실행 보류"
      : delay?.delayed ? `데이터 지연 · 마지막 평가 ${delay.as_of}` : null,
    initial: view.initial_capital ?? null,
    equity: a ? String(a.nav) : null,
    sessionPnl: a ? String(a.day_pnl) : null,
    net: a ? String(a.total_pnl) : tradeCount === 0 ? "0" : null,
    trades: tradeCount == null ? "-" : String(tradeCount),
    decision: latest ? `${latest.signal} 신호 · ${latest.holdings}종목` : "-",
    lastRunAt: view.last_run?.completed_at ?? null,
  };
}

/** Strategy I's card. I is not in the strategy registry, so it reads its own paper API
 *  (/api/v1/strategy-i/paper) and is drawn in the same frame. A guard SKIP is a neutral note, never ISSUE. */
export function DashboardStrategyICard({ view, error, href }: { view: IPaperView | null; error?: string | null; href?: string }) {
  if (!view) {
    return <CardFrame strategyId={STRATEGY_I_TAB.strategy_id} title="Strategy I · MOM12-2" href={href} mode="PAPER" state="ISSUE"
      period="-" reason={error || "Strategy I 상태 조회 실패"} initial={null} equity={null} sessionPnl={null} net={null}
      trades="-" decision="-" lastRunAt={null}/>;
  }
  const s = iDashboardSummary(view);
  return <CardFrame strategyId={view.strategy_id} title={view.display_name} href={href} mode="PAPER" state={s.state}
    period={s.period} reason={s.reason} note={s.note} initial={s.initial} equity={s.equity} sessionPnl={s.sessionPnl}
    net={s.net} trades={s.trades} decision={s.decision} lastRunAt={s.lastRunAt}/>;
}

export function DashboardStrategyCards({ bundles, hrefs = {}, children }: {
  bundles: StrategyBundle[]; hrefs?: Record<string, string>; children?: React.ReactNode;
}) {
  if (!bundles.length && !children) return <EmptyState title="운영 중인 전략이 없습니다." description="전략 레지스트리에 활성 전략이 없습니다."/>;
  return <section aria-label="전략 현황" className="grid gap-4 lg:grid-cols-2">
    {bundles.map(bundle => <DashboardStrategyCard key={bundle.row.strategy_id} bundle={bundle} href={hrefs[bundle.row.strategy_id]}/>)}
    {children}
  </section>;
}
