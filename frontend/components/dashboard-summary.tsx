import Link from "next/link";
import type { ReactNode } from "react";
import { MockBadge } from "@/components/mock-badge";
import { Money } from "@/components/money";
import { StatusBadge } from "@/components/ui";
import { etTime, moneyText } from "@/lib/format";
import { RUN_STATE_LABELS, numericToneClass, signedPct } from "@/lib/strategy-b";
import type { DashboardSnapshot, DashboardStrategySnapshot, StrategyEvent } from "@/types/dashboard";

const seriesColor = (strategy: "A" | "B") => strategy === "A" ? "var(--series-a)" : "var(--series-b)";

function SeriesDot({ strategy }: { strategy: "A" | "B" }) {
  return <span aria-hidden="true" className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: seriesColor(strategy) }}/>;
}

function Figure({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return <div>
    <dt className="text-xs text-muted">{label}</dt>
    <dd className={`mt-1 text-lg font-bold tabular-nums ${tone ?? "text-foreground"}`}>{value}</dd>
  </div>;
}

/** One strategy card. A and B use this same component at the same size on purpose: the
 *  screen states both and ranks neither. */
function StrategyCard({ snapshot, markSource }: { snapshot: DashboardStrategySnapshot; markSource: boolean }) {
  return <section aria-labelledby={`strategy-card-${snapshot.strategy}`} data-strategy-card={snapshot.strategy} className="panel flex flex-col p-5">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <p id={`strategy-card-${snapshot.strategy}`} className="label flex items-center gap-2"><SeriesDot strategy={snapshot.strategy}/>STRATEGY {snapshot.strategy}</p>
        <p className="mt-1.5 text-sm font-semibold tracking-wide text-foreground-secondary">{snapshot.name}</p>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <StatusBadge value={snapshot.status} label={RUN_STATE_LABELS[snapshot.status]}/>
        {markSource && snapshot.source === "MOCK" && <MockBadge/>}
      </div>
    </div>
    <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4">
      <Figure label="현재 자산" value={<Money krw={snapshot.current_equity_krw}/>}/>
      <Figure label="누적 수익률" value={signedPct(snapshot.total_return_pct)} tone={numericToneClass(snapshot.total_return_pct)}/>
      <Figure label="오늘 손익" value={<Money krw={snapshot.today_pnl_krw} signed/>} tone={numericToneClass(snapshot.today_pnl_krw)}/>
      <Figure label="보유 포지션" value={`${snapshot.open_positions}종목`}/>
    </dl>
    <p className="mt-4 border-t border-line-subtle pt-3 text-xs text-muted">초기 자본 {moneyText(snapshot.initial_capital_krw)}</p>
    <Link href={snapshot.href} className="btn-action-secondary mt-4 w-full">전략 {snapshot.strategy} 보기</Link>
  </section>;
}

export function StrategyCards({ snapshot }: { snapshot: DashboardSnapshot }) {
  // While both sides are mock the screen says so once, in the header. A card carries its
  // own marker only when the two differ, so the live one can never be read as demo data.
  const markSource = snapshot.a.source !== snapshot.b.source;
  return <div className="mb-7 grid gap-3 lg:grid-cols-2">
    <StrategyCard snapshot={snapshot.a} markSource={markSource}/>
    <StrategyCard snapshot={snapshot.b} markSource={markSource}/>
  </div>;
}

function TodayRow({ label, a, b, toneA, toneB }: { label: string; a: ReactNode; b: ReactNode; toneA?: string; toneB?: string }) {
  return <div className="p-4" data-today-row={label}>
    <p className="label">{label}</p>
    <dl className="mt-3 space-y-2">
      <div className="flex items-center justify-between gap-3">
        <dt className="flex items-center gap-2 text-xs text-muted"><SeriesDot strategy="A"/>전략 A</dt>
        <dd className={`text-right text-sm font-bold tabular-nums ${toneA ?? "text-foreground"}`}>{a}</dd>
      </div>
      <div className="flex items-center justify-between gap-3">
        <dt className="flex items-center gap-2 text-xs text-muted"><SeriesDot strategy="B"/>전략 B</dt>
        <dd className={`text-right text-sm font-bold tabular-nums ${toneB ?? "text-foreground"}`}>{b}</dd>
      </div>
    </dl>
  </div>;
}

/** The session at a glance. Deliberately three rows: anything heavier belongs on
 *  /strategy-compare, which is one click away. */
export function TodaySummary({ snapshot }: { snapshot: DashboardSnapshot }) {
  const { a, b } = snapshot;
  return <section aria-labelledby="dashboard-today-title" className="mb-7">
    <h2 id="dashboard-today-title" className="mb-3 font-semibold">오늘 요약</h2>
    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-line bg-line shadow-panel sm:grid-cols-3">
      <div className="bg-surface"><TodayRow label="오늘 손익" a={<Money krw={a.today_pnl_krw} signed/>} b={<Money krw={b.today_pnl_krw} signed/>} toneA={numericToneClass(a.today_pnl_krw)} toneB={numericToneClass(b.today_pnl_krw)}/></div>
      <div className="bg-surface"><TodayRow label="보유 포지션" a={`${a.open_positions}종목`} b={`${b.open_positions}종목`}/></div>
      <div className="bg-surface"><TodayRow label="오늘 완료 거래" a={`${a.closed_trades_today}건`} b={`${b.closed_trades_today}건`}/></div>
    </div>
  </section>;
}

export function RecentActivity({ events }: { events: StrategyEvent[] }) {
  if (events.length === 0) return null;
  return <section aria-labelledby="dashboard-activity-title" className="mb-7">
    <h2 id="dashboard-activity-title" className="mb-3 font-semibold">최근 전략 이벤트</h2>
    <ol className="panel divide-y divide-line-subtle">
      {events.map(event => <li key={event.id} data-event={event.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
        <span className="w-28 shrink-0 text-xs tabular-nums text-muted">{etTime(event.at)}</span>
        <span className="flex items-center gap-1.5 text-xs font-bold text-foreground-secondary"><SeriesDot strategy={event.strategy}/>{event.strategy}</span>
        <span className="text-sm font-bold text-foreground">{event.symbol}</span>
        <span className="text-sm text-foreground-secondary">{event.message}</span>
      </li>)}
    </ol>
  </section>;
}
