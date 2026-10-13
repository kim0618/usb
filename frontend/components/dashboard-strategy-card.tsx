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

export function DashboardStrategyCard({ bundle, href }: { bundle: StrategyBundle; href?: string }) {
  const { row, account, card } = bundle;
  const block = card?.dashboard;
  const state = block?.status;
  // H has no capital book: its money and trade fields are "-", with the reason on the API's `na`.
  const noBook = card?.forward != null;
  const settled = account.books ? (account.books[account.evidence_status || ""]?.sessions ?? 0) > 0 : true;
  const official = card?.official;
  const trades = noBook || !card ? "-" : String(official?.trades ?? card.trades);
  const net = noBook ? null : (official?.net_pnl ?? card?.net_pnl ?? null);

  return <article className="panel flex h-full flex-col p-5" data-strategy={row.strategy_id}
    data-state={state || "UNKNOWN"} data-dashboard-card="">
    <header className="flex items-start justify-between gap-3">
      <h3 className="min-w-0 text-lg font-semibold text-foreground">
        {href ? <Link href={href} className="hover:text-primary">{row.display_name}</Link> : row.display_name}
      </h3>
      <span className="shrink-0 rounded-full border border-line px-2.5 py-1 text-[11px] font-bold tracking-wide text-foreground-secondary"
        data-mode="">{block?.mode ?? "-"}</span>
    </header>

    <div className="mt-4" data-status-block="">
      <p className="flex items-center gap-2 text-sm font-semibold text-foreground" data-status={state || ""}>
        <span aria-hidden className={`inline-block h-2.5 w-2.5 rounded-full ${state ? STATE_DOT[state] : "bg-foreground-muted"}`}/>
        {state ?? "-"}
      </p>
      <p className="mt-1 text-xs text-muted" data-period="">{periodLine(block)}</p>
      {/* Reserved on every card so a healthy card is exactly as tall as one with an issue. */}
      <div className="mt-1 min-h-4 text-xs text-danger">
        {state === "ISSUE" && block?.status_reason && <p data-reason="">{block.status_reason}</p>}
      </div>
    </div>

    <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-3">
      <Field label="초기 자본"><Money value={noBook ? null : account.initial_equity}/></Field>
      <Field label="현재 자산"><Money value={noBook ? null : account.current_equity}/></Field>
      <Field label="최근 세션 손익"><Money signed value={noBook || !settled ? null : account.today_pnl}/></Field>
      <Field label="공식 누적 손익"><Money signed value={net}/></Field>
      <Field label="공식 거래 수">{trades}</Field>
      <Field label="최근 결정">{block?.last_decision || "-"}</Field>
    </dl>

    <p className="mt-auto pt-5 text-xs text-muted" data-last-run="">마지막 실행 {kstStamp(block?.last_run_at)}</p>
  </article>;
}

export function DashboardStrategyCards({ bundles, hrefs = {} }: { bundles: StrategyBundle[]; hrefs?: Record<string, string> }) {
  if (!bundles.length) return <EmptyState title="운영 중인 전략이 없습니다." description="전략 레지스트리에 활성 전략이 없습니다."/>;
  return <section aria-label="전략 현황" className="grid gap-4 lg:grid-cols-2">
    {bundles.map(bundle => <DashboardStrategyCard key={bundle.row.strategy_id} bundle={bundle} href={hrefs[bundle.row.strategy_id]}/>)}
  </section>;
}
