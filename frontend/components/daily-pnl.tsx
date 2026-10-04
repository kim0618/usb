"use client";

/** The operating screen: how much each session made or lost, and what happened on the days it did.
 *
 *  Deliberately small. The comparison tables, the frozen gate and the portfolio simulation are
 *  analysis, and analysis happens elsewhere; this answers "오늘 얼마" and then gets out of the way.
 *  A row opens to that day's closed trades and nothing more.
 *
 *  Every figure is the backend's (GET /strategies/daily). A session a strategy did not report is
 *  "-", never a zero: "no row" and "flat" are different facts and must not look alike. */

import { useState } from "react";
import { pnlTone } from "@/components/daily-performance";
import { EmptyState } from "@/components/ui";
import { etTime, formatSignedUsd, formatUsd } from "@/lib/format";
import type { DailyRow, DailyView } from "@/lib/strategies";

function isFlat(value: string | null | undefined) {
  return value != null && Number(value) === 0;
}

/** A flat session is "$0.00", not "+$0.00": a plus sign on a zero reads as a gain that did not
 *  happen, and A sits flat for weeks at a time. */
function money(value: string | null | undefined) {
  if (value == null) return null;
  return isFlat(value) ? formatUsd(value) : formatSignedUsd(value);
}

/** One session. Click to see the trades that produced it. */
function Row({ row, columns, open, onToggle }: {
  row: DailyRow; columns: Array<{ id: string; label: string }>; open: boolean; onToggle: () => void;
}) {
  const traded = row.trades.length;
  return <>
    <tr data-daily-row={row.session} data-open={open ? "" : undefined}>
      <th scope="row" className="whitespace-nowrap font-medium">
        <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={`day-${row.session}`}
          className="underline-offset-4 hover:underline focus-visible:underline"
          disabled={!traded}>
          {row.session}
          {traded > 0 && <span className="ml-2 text-[10px] text-muted">거래 {traded}</span>}
        </button>
      </th>
      {columns.map(column => {
        const cell = row.strategies[column.id];
        const value = money(cell?.pnl);
        return <td key={column.id} className="text-right tabular-nums" data-cell={column.id}>
          {value == null ? <span className="text-muted" title="이 세션에 기록이 없습니다">-</span>
            : <span className={isFlat(cell?.pnl) ? "text-muted" : pnlTone(cell!.pnl!)}>{value}</span>}
        </td>;
      })}
      <td className="text-right font-semibold tabular-nums" data-cell="TOTAL">
        {row.total_pnl == null ? <span className="text-muted">-</span>
          : <span className={isFlat(row.total_pnl) ? "text-muted" : pnlTone(row.total_pnl)}>{money(row.total_pnl)}</span>}
      </td>
    </tr>
    {open && <tr id={`day-${row.session}`} data-daily-detail={row.session}>
      <td colSpan={columns.length + 2} className="bg-surface-alt p-4">
        <table className="analysis-table w-full text-xs">
          <caption className="sr-only">{row.session} 청산된 거래</caption>
          <thead><tr>
            <th scope="col">전략</th><th scope="col">종목</th>
            <th scope="col" className="text-right">수량</th>
            <th scope="col" className="text-right">진입</th><th scope="col" className="text-right">청산</th>
            <th scope="col" className="text-right">비용</th><th scope="col" className="text-right">손익</th>
            <th scope="col">사유</th><th scope="col">청산 시각</th>
          </tr></thead>
          <tbody>{row.trades.map((trade, index) => <tr key={`${trade.strategy_id}-${trade.symbol}-${index}`}>
            <td>{columns.find(c => c.id === trade.strategy_id)?.label || trade.strategy_id}</td>
            <td className="font-semibold">{trade.symbol}</td>
            <td className="text-right tabular-nums">{trade.qty ?? "-"}</td>
            <td className="text-right tabular-nums">{trade.entry_price ? formatUsd(trade.entry_price) : "-"}</td>
            <td className="text-right tabular-nums">{trade.exit_price ? formatUsd(trade.exit_price) : "-"}</td>
            <td className="text-right tabular-nums">{trade.costs ? formatUsd(trade.costs) : "-"}</td>
            <td className={`text-right tabular-nums ${trade.net_pnl ? pnlTone(trade.net_pnl) : ""}`}>
              {money(trade.net_pnl) ?? "-"}</td>
            <td>{trade.exit_reason || "-"}</td>
            <td className="whitespace-nowrap">{trade.exit_at ? etTime(trade.exit_at) : "-"}</td>
          </tr>)}</tbody>
        </table>
      </td>
    </tr>}
  </>;
}

export function DailyPnl({ view }: { view: DailyView }) {
  const [open, setOpen] = useState<string | null>(null);
  const columns = view.strategies.filter(s => s.has_daily_pnl)
    .map(s => ({ id: s.strategy_id, label: s.short_name || s.display_name }));
  const label = (id: string) =>
    view.strategies.find(s => s.strategy_id === id)?.short_name
    || view.strategies.find(s => s.strategy_id === id)?.display_name || id;
  const excluded = Object.entries(view.excluded_before_start || {});

  return <section aria-labelledby="daily-title" className="mb-7 min-w-0">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="daily-title" className="font-semibold">일별 손익</h2>
        <p className="text-xs text-muted">
          {view.paper_clock.official_paper_start
            ? `공식 Paper ${view.paper_clock.official_paper_start}부터 · ${view.currency}`
            : `공식 Paper 시작 전 · ${view.currency}`}
        </p>
      </div>
      {view.rows.length === 0
        ? <EmptyState title="기록된 세션이 없습니다." description="공식 Paper가 시작되면 세션마다 한 줄씩 쌓입니다."/>
        : <div className="table-wrap relative"><table className="analysis-table min-w-[560px]">
            <caption className="sr-only">세션별 전략 손익</caption>
            <thead><tr>
              <th scope="col">세션</th>
              {columns.map(c => <th key={c.id} scope="col" className="text-right" data-column={c.id}>{c.label}</th>)}
              <th scope="col" className="text-right">합계</th>
            </tr></thead>
            <tbody>{view.rows.map(row => <Row key={row.session} row={row} columns={columns}
              open={open === row.session} onToggle={() => setOpen(v => v === row.session ? null : row.session)}/>)}</tbody>
          </table></div>}
      <p className="mt-2 text-xs text-muted">{view.note}. 거래가 있던 날은 세션을 눌러 그날 내역을 봅니다.</p>
      {excluded.length > 0 && <p className="mt-1 text-xs text-muted" data-excluded-before-start="">
        {excluded.map(([id, info]) =>
          `${label(id)}는 공식 시작 전에 움직인 세션이 ${info.sessions}일 있습니다(마지막 ${info.last_session}).`).join(" ")}
        {" "}회계 방식이 달라 이 표에 섞지 않습니다.
      </p>}
  </section>;
}
