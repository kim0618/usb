"use client";

/** The held-position card, one structure for PAPER and LIVE.
 *
 *  Order: what is held (side, leverage, size, hold time, source badge) -> the two money figures
 *  (position PnL and what closing now would leave) -> the position facts -> the PnL breakdown.
 *  KRW leads and USDT sits under it; gains are green and losses red.
 *
 *  This file only lays out strings. Every figure is formatted by the caller from the backend's
 *  own fields (the paper engine's preview, or Binance's positionRisk and the server's close
 *  estimate); nothing here sums, converts or derives one. A figure that cannot be established is
 *  a dash with a short reason in muted text, never a large warning in the number's own size. */

import { useState, type ReactNode } from "react";
import { toneClass } from "@/lib/crypto-paper";

export type PositionSource = "PAPER" | "LIVE";
/** [label, value] or [label, value, testId]. */
export type Fact = readonly [string, string] | readonly [string, string, string];

export type PnlFigure = {
  /** Already formatted, e.g. "-172,461원"; null when only USDT exists. */
  krw: string | null;
  usdt: string;
  /** The raw signed value the colour follows. */
  tone: string | null | undefined;
  /** "+0.41%" style prefix shown before the USDT figure. */
  pct?: string | null;
  note?: string;
  krwTestId?: string;
  usdtTestId?: string;
};

export type NetFigure =
  | { shown: true; krw: string | null; usdt: string | null; tone: string | null | undefined; note?: string;
      krwTestId?: string; usdtTestId?: string }
  | { shown: false; reason: string; testId?: string };

/** Position PnL on the left, expected net on close on the right. */
export function PnlHeadline({ pnl, net, source, testId }: {
  pnl: PnlFigure; net: NetFigure; source?: { text: string; testId?: string }; testId?: string;
}) {
  return (
    <div className="mb-3 grid grid-cols-2 gap-2" data-testid={testId}>
      <div className="rounded-lg bg-surface-alt px-3 py-2">
        <p className="text-[11px] text-muted">현재 포지션 손익</p>
        {pnl.krw != null ? <>
          <p className={`whitespace-nowrap text-lg font-bold tabular-nums leading-tight sm:text-xl ${toneClass(pnl.tone)}`}
            data-testid={pnl.krwTestId}>{pnl.krw}</p>
          <p className={`whitespace-nowrap text-[11px] tabular-nums ${toneClass(pnl.tone)}`}>
            {pnl.pct ? `${pnl.pct} ` : ""}
            <span className="text-muted" data-testid={pnl.usdtTestId}>{pnl.usdt}</span>
          </p>
        </> : <p className={`whitespace-nowrap text-lg font-bold tabular-nums leading-tight sm:text-xl ${toneClass(pnl.tone)}`}
          data-testid={pnl.usdtTestId}>{pnl.usdt}</p>}
        {pnl.note && <p className="text-[11px] text-muted">{pnl.note}</p>}
      </div>
      <div className="rounded-lg bg-surface-alt px-3 py-2">
        <p className="text-[11px] text-muted">청산 시 예상 순손익</p>
        {net.shown ? <>
          {net.krw != null ? <>
            <p className={`whitespace-nowrap text-lg font-bold tabular-nums leading-tight sm:text-xl ${toneClass(net.tone)}`}
              data-testid={net.krwTestId}>{net.krw}</p>
            {net.usdt != null && <p className={`whitespace-nowrap text-[11px] tabular-nums leading-tight ${toneClass(net.tone)}`}
              data-testid={net.usdtTestId}>{net.usdt}</p>}
          </> : <p className={`whitespace-nowrap text-lg font-bold tabular-nums leading-tight sm:text-xl ${toneClass(net.tone)}`}
            data-testid={net.usdtTestId}>{net.usdt ?? "-"}</p>}
          {net.note && <p className="text-[11px] text-muted">{net.note}</p>}
        </> : <>
          <p className="text-lg font-bold leading-tight text-foreground-secondary sm:text-xl">-</p>
          <p className="text-[11px] text-muted" data-testid={net.testId}>{net.reason}</p>
        </>}
      </div>
      {source && <p className="col-span-2 text-right text-[10px] text-muted" data-testid={source.testId}>{source.text}</p>}
    </div>
  );
}

export type BreakdownLine = {
  label: string; value: string; sub?: string; tone: string | null | undefined; note?: string;
};
export type BreakdownTotal = {
  label: ReactNode; value: string; sub?: string; tone: string | null | undefined; testId?: string; minor?: boolean;
};

/** 손익 분해: price PnL, then each cost between it and what would be kept. */
export function PnlBreakdown({ lines, totals, quoteAge, quoteAgeTestId, unavailable, unavailableTestId, footnote, testId }: {
  lines: BreakdownLine[]; totals: BreakdownTotal[]; quoteAge?: string | null; quoteAgeTestId?: string;
  /** Short reason the expected net cannot be given; shown beside a dash total. */
  unavailable?: string | null; unavailableTestId?: string; footnote?: string; testId?: string;
}) {
  return (
    <div className="mt-3 border-t border-line pt-2" data-testid={testId}>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <p className="text-[11px] font-semibold text-foreground-secondary">손익 분해</p>
        {quoteAge != null && <p className="text-[10px] text-muted" data-testid={quoteAgeTestId}>{quoteAge}</p>}
      </div>
      <dl className="space-y-1 text-[11px]">
        {lines.map(line => (
          <div key={line.label}>
            <div className="flex justify-between gap-3">
              <dt className="text-muted">{line.label}</dt>
              <dd className={`shrink-0 whitespace-nowrap text-right tabular-nums ${line.tone == null ? "text-foreground-secondary" : toneClass(line.tone)}`}>
                {line.value}{line.sub && <span className="ml-1.5 text-[10px] text-muted">{line.sub}</span>}
              </dd>
            </div>
            {line.note && <p className="text-right text-[10px] text-muted">{line.note}</p>}
          </div>
        ))}
      </dl>
      <div className="mt-2 space-y-1 border-t border-line pt-2 text-xs">
        {unavailable != null ? (
          <>
            <div className="flex justify-between gap-3 font-semibold">
              <span>청산 시 예상 순손익</span>
              <span className="text-foreground-secondary">-</span>
            </div>
            <p className="text-right text-[10px] text-muted" data-testid={unavailableTestId}>{unavailable}</p>
          </>
        ) : totals.map(total => (
          <div key={String(total.label)} className={`flex justify-between gap-3 ${total.minor ? "text-[11px]" : "font-semibold"}`}>
            <span className={total.minor ? "text-muted" : undefined}>{total.label}</span>
            <span className={`shrink-0 whitespace-nowrap text-right tabular-nums ${total.minor ? "font-medium" : ""} ${toneClass(total.tone)}`}
              data-testid={total.testId}>
              {total.value}{total.sub && <span className="ml-1.5 text-[10px] font-normal text-muted">{total.sub}</span>}
            </span>
          </div>
        ))}
        {footnote && unavailable == null && <p className="text-[10px] text-muted">{footnote}</p>}
      </div>
    </div>
  );
}

/** The card itself. `fold` is the phone form: only the first `quickCount` facts and the CLOSE
 *  footer are visible, and the remaining facts plus the breakdown sit under a toggle. On a wide
 *  screen everything is open. The sections and their order are the same either way. */
export function PositionCardShell({
  source, testId, side, sideTestId, leverage, leverageTestId, qty, qtyTestId, held, heldTestId,
  top, headline, facts, quickCount = 2, breakdown, footer, fold, className,
}: {
  source: PositionSource; testId: string;
  side: string; sideTestId?: string; leverage: string; leverageTestId?: string;
  qty: string; qtyTestId?: string; held: string | null; heldTestId?: string;
  top?: ReactNode; headline: ReactNode; facts: Fact[]; quickCount?: number; breakdown?: ReactNode;
  footer?: ReactNode; fold?: { toggleTestId: string; detailTestId: string }; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const long = side === "LONG";
  const factList = (items: Fact[], cols: string) => (
    <dl className={`grid gap-x-3 gap-y-1 text-[11px] ${cols}`}>
      {items.map(([label, value, id]) => (
        <div key={label} className="flex justify-between gap-2">
          <dt className="text-muted">{label}</dt>
          <dd className="tabular-nums text-foreground-secondary" data-testid={id}>{value}</dd>
        </div>
      ))}
    </dl>
  );
  return (
    <section className={className ?? "panel p-3 sm:p-4"} data-testid={testId} data-source={source} aria-label="현재 포지션">
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
        <span className={`rounded px-2 py-0.5 font-bold ${long ? "bg-success-soft text-success" : "bg-danger-soft text-danger"}`}
          data-testid={sideTestId}>{side}</span>
        <span className="font-semibold tabular-nums text-foreground-secondary" data-testid={leverageTestId}>{leverage}</span>
        <span className="tabular-nums text-foreground-secondary">
          <span data-testid={qtyTestId}>{qty}</span>
          <span className="text-muted" data-testid={heldTestId}>{` · ${held ? `${held} 보유` : "보유 시간 -"}`}</span>
        </span>
        <span className={`ml-auto rounded-full border px-2 py-0.5 text-[10px] font-bold tracking-wide ${
          source === "LIVE" ? "border-danger text-danger" : "border-line text-foreground-secondary"}`}
          data-testid="position-source">{source}</span>
      </div>
      {top}
      {headline}
      {fold ? (
        <>
          {factList(facts.slice(0, quickCount), "mb-2 grid-cols-2")}
          {footer}
          <button type="button" aria-expanded={open} data-testid={fold.toggleTestId}
            className="mt-1.5 flex w-full items-center justify-between py-1 text-[11px] font-medium text-foreground-secondary"
            onClick={() => setOpen(v => !v)}>
            손익/비용 상세 <span aria-hidden="true">{open ? "▲" : "▼"}</span>
          </button>
          {open && <div data-testid={fold.detailTestId}>
            {factList(facts.slice(quickCount), "grid-cols-2")}
            {breakdown}
          </div>}
        </>
      ) : (
        <>
          {factList(facts, "grid-cols-2 sm:grid-cols-3")}
          {breakdown}
          {footer}
        </>
      )}
    </section>
  );
}
