"use client";

import Link from "next/link";
import { useState } from "react";
import { Drawer } from "@/components/ui";
import { entryDisplay, entryReasonLabel, executionFacts, premarketFacts, rowHeadline, type EntryDisplay, type EntryFact } from "@/lib/entry-board";
import { formatStrategyPhase } from "@/lib/display";
import { tradingDate } from "@/lib/format";
import type { EntryBoard, EntryBoardCandidate, EntryCapacity, TradingPosition } from "@/types/api";

/** Empty states of the board. Previous runs' candidates are never a fallback. */
function emptyCopy(board: EntryBoard): { title: string; description?: string } {
  const run = board.analysis_session_date ? tradingDate(board.analysis_session_date) : null;
  if (board.status === "NO_SCANNER_RUN") return { title: "오늘 스캐너 실행 결과가 없습니다." };
  if (board.status === "SCANNER_RUN_OUTDATED") return { title: "오늘 스캐너 실행 결과가 없습니다.", description: `마지막 스캐너 실행은 ${run} 기준이며, 그 진입 세션(${tradingDate(board.entry_session_date)})은 이미 지났습니다.` };
  if (board.status === "NO_ACTIVE_ANALYSIS") return { title: "현재 ScannerRun의 GPT 분석이 아직 없습니다.", description: `오늘 진입 후보가 아직 준비되지 않았습니다. 현재 ScannerRun(${run})의 GPT 분석과 채택이 완료되면 이곳에 진입 후보가 표시됩니다.` };
  return { title: "현재 분석에서 채택된 진입 후보가 없습니다." };
}

function StatusChip({ display }: { display: EntryDisplay }) {
  return <span data-entry-group={display.group} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-bold tone-${display.tone}`}><span aria-hidden="true">{display.icon}</span>{display.label}</span>;
}

const visibleFacts = (facts: EntryFact[]) => facts.filter((fact): fact is [string, string] => fact[1] !== null);

function RowTooltip({ id, row, display, board, position }: { id: string; row: EntryBoardCandidate; display: EntryDisplay; board: EntryBoard; position: TradingPosition | null }) {
  const facts = [...visibleFacts(premarketFacts(row, row.premarket ? board.thresholds : null)), ...visibleFacts(executionFacts(row, position))];
  return <div id={id} role="tooltip" className="pointer-events-none absolute left-16 top-full z-30 mt-1 hidden w-72 rounded-lg border border-line bg-surface px-3 py-2 text-left text-xs leading-5 text-foreground-secondary shadow-panel md:group-hover:block md:group-focus-within:block">
    <p className="font-bold text-foreground">{row.symbol}</p>
    <p>상태: {display.label}</p>
    <p>단계: {row.state ? formatStrategyPhase(row.state.phase) : "평가 기록 없음"}</p>
    {facts.length > 0 && <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 border-t border-line-subtle pt-1.5">{facts.map(([label, value]) => <div key={label} className="contents"><dt className="text-muted">{label}</dt><dd className="text-right text-foreground">{value}</dd></div>)}</dl>}
    {display.reasonCode && <p className="mt-1.5 border-t border-line-subtle pt-1.5">최종 사유: {entryReasonLabel(display.reasonCode)} <span className="font-mono text-[10px] text-muted">{display.reasonCode}</span></p>}
  </div>;
}

function FactGrid({ facts }: { facts: EntryFact[] }) {
  return <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">{facts.map(([label, value]) => <div key={label} className="rounded-lg border border-line p-3"><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 text-sm font-semibold text-foreground">{value ?? "—"}</dd></div>)}</dl>;
}

function CandidateDetail({ row, display, board, position }: { row: EntryBoardCandidate; display: EntryDisplay; board: EntryBoard; position: TradingPosition | null }) {
  const premarket = row.premarket;
  return <div className="space-y-6">
    <header><p className="text-sm font-bold text-primary">순위 {row.rank == null ? "—" : `#${row.rank}`}</p><h2 className="mt-1 text-xl font-semibold text-foreground">{row.symbol}{row.exchange && <span className="text-base font-normal text-muted"> · {row.exchange}</span>}</h2><div className="mt-2 flex flex-wrap items-center gap-2"><StatusChip display={display}/><span className="text-sm text-foreground-secondary">{display.detail}</span></div></header>
    <section><h3 className="label mb-2">현재 상태</h3><FactGrid facts={[["상태", display.label], ["단계", row.state ? formatStrategyPhase(row.state.phase) : "평가 기록 없음"], ["사유", display.reasonCode ? `${entryReasonLabel(display.reasonCode)} (${display.reasonCode})` : null]]}/></section>
    <section><h3 className="label mb-2">프리마켓 게이트 (V1 · 실제 판정)</h3><FactGrid facts={[...premarketFacts(row, board.thresholds), ["프리마켓 봉 수", premarket?.premarket_bars_count == null ? null : `${premarket.premarket_bars_count}개`], ["데이터 이상 항목", premarket?.invalid_field ?? null]]}/>{!premarket && <p className="mt-2 text-xs text-muted">아직 프리마켓 평가 기록이 없습니다.</p>}</section>
    <section><h3 className="label mb-2">진입 신호 / 체결</h3><FactGrid facts={executionFacts(row, position)}/></section>
    <p className="text-xs text-muted">Backend에 저장된 전략 상태와 프리마켓 진단값만 표시합니다. 기록되지 않은 값은 —로 표시하며 화면에서 다시 계산하지 않습니다.</p>
  </div>;
}

/** Fixed full-width entry status board. Order is the backend's (GPT rank); only the state changes. */
export function EntryStatusBoard({ board, loading, error, capacity, positions }: { board: EntryBoard | null; loading: boolean; error: string | null; capacity: EntryCapacity | null; positions: TradingPosition[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  // The capacity is the backend's for one entry session; it only applies to that session's board.
  const capacityReason = capacity && board && capacity.entry_session_date === board.entry_session_date ? capacity.blocked_reason : null;
  const positionFor = (symbol: string) => positions.find(position => position.symbol === symbol) ?? null;
  const selectedRow = board?.candidates.find(row => row.symbol === selected) ?? null;
  const ready = board?.status === "READY" && board.candidates.length > 0;
  return <section aria-labelledby="entry-board-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><h2 id="entry-board-title" className="font-semibold">진입 평가</h2>{board?.analysis_session_date && <span className="text-xs text-muted">{tradingDate(board.analysis_session_date)} 분석 기준{board.analysis_id != null && <> · Analysis #{board.analysis_id}</>}{board.entry_session_date && <> · 진입 세션 {tradingDate(board.entry_session_date)}</>}</span>}{capacity && <span className="text-xs text-muted">신규 진입 {capacity.new_entries_used}/{capacity.max_new_entries} · 보유 {capacity.open_positions_used}/{capacity.max_open_positions}</span>}</div><Link href="/research" className="btn-action-secondary-compact">분석 보기</Link></div>
    <div className="panel" data-entry-board>
      {ready ? <ol aria-label="진입 후보" className="divide-y divide-line-subtle">{board.candidates.map(row => {
        const display = entryDisplay(row, capacityReason); const position = positionFor(row.symbol); const headline = rowHeadline(row, position); const tooltipId = `entry-tip-${row.symbol}`;
        return <li key={row.symbol} className="group relative" data-entry-row={row.symbol}>
          <button type="button" aria-describedby={tooltipId} aria-label={`${row.symbol} ${display.label} ${display.detail} 상세 보기`} onClick={() => setSelected(row.symbol)} className="grid w-full grid-cols-[2.5rem_minmax(4.5rem,1fr)_auto] items-center gap-x-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-alt focus-visible:bg-surface-alt sm:grid-cols-[2.5rem_6rem_7.5rem_minmax(0,1fr)_auto]">
            <span className="text-sm font-bold text-primary">{row.rank == null ? "—" : `#${row.rank}`}</span>
            <span className="truncate text-sm font-bold text-foreground">{row.symbol}</span>
            <span><StatusChip display={display}/></span>
            <span className="hidden truncate text-sm text-foreground-secondary sm:block">{display.detail}</span>
            <span className="hidden whitespace-nowrap text-right text-xs text-muted sm:block">{headline ?? ""}</span>
          </button>
          <RowTooltip id={tooltipId} row={row} display={display} board={board} position={position}/>
        </li>;
      })}</ol> : <div className="px-6 py-8 text-center">{loading && !board ? <p className="text-sm font-medium text-foreground-secondary">진입 후보를 확인하고 있습니다.</p> : error && !board ? <><p className="text-sm font-medium text-foreground-secondary">진입 후보를 불러오지 못했습니다.</p><p className="mt-2 text-xs text-muted">{error}</p></> : board && <><p className="text-sm font-medium text-foreground-secondary">{emptyCopy(board).title}</p>{emptyCopy(board).description && <p className="mt-2 text-xs text-muted">{emptyCopy(board).description}</p>}</>}</div>}
    </div>
    <Drawer open={selectedRow !== null} title={`${selectedRow?.symbol ?? "진입 후보"} · 진입 평가 상세`} onClose={() => setSelected(null)}>{selectedRow && board && <CandidateDetail row={selectedRow} display={entryDisplay(selectedRow, capacityReason)} board={board} position={positionFor(selectedRow.symbol)}/>}</Drawer>
  </section>;
}
