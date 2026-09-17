"use client";

import { useMemo, useState } from "react";
import { Drawer, EmptyState, StatusBadge } from "@/components/ui";
import { compactUsd, etTime, formatUsd } from "@/lib/format";
import {
  CANDIDATE_STATE_META, EMPTY_SCANNER_FILTER, SCANNER_SORT_KEYS, SETUP_META,
  dropReasonLabel, lifecycleSteps, numericToneClass, plainPct, rvolText,
  scoreTone, setupLabel, signedPct, stateMeta, visibleCandidates,
  type ScannerFilter, type ScannerSortKey, type SortDirection,
} from "@/lib/strategy-b";
import type { CandidateState, MomentumCandidate, SetupType } from "@/types/strategy-b";

function StateChip({ state }: { state: CandidateState }) {
  const meta = stateMeta(state);
  return <span data-candidate-state={state} title={state} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-bold tone-${meta.tone}`}><span aria-hidden="true">{meta.icon}</span>{meta.label}</span>;
}

function SortableHeader({ label, sortKey, sort, onSort }: { label: string; sortKey: ScannerSortKey; sort: { key: ScannerSortKey; direction: SortDirection } | null; onSort: (key: ScannerSortKey) => void }) {
  const active = sort?.key === sortKey;
  const ariaSort = active ? (sort!.direction === "asc" ? "ascending" : "descending") : "none";
  return <th scope="col" aria-sort={ariaSort} className="text-right">
    <button type="button" onClick={() => onSort(sortKey)} className={`inline-flex w-full items-center justify-end gap-1 font-semibold transition-colors hover:text-primary ${active ? "text-primary" : ""}`}>
      {label}<span aria-hidden="true" className="text-[10px]">{active ? (sort!.direction === "asc" ? "▲" : "▼") : "↕"}</span>
      <span className="sr-only">{active ? (sort!.direction === "asc" ? "오름차순 정렬됨" : "내림차순 정렬됨") : "정렬"}</span>
    </button>
  </th>;
}

function Facts({ items }: { items: Array<[string, string]> }) {
  return <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">{items.map(([label, value]) => <div key={label} className="rounded-lg border border-line p-3"><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 text-sm font-semibold text-foreground">{value}</dd></div>)}</dl>;
}

/** The lifecycle the scanner actually recorded. A step without a timestamp stays pending. */
function Lifecycle({ candidate }: { candidate: MomentumCandidate }) {
  const steps = lifecycleSteps(candidate);
  return <ol className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">{steps.map(step => {
    const meta = CANDIDATE_STATE_META[step.state];
    return <li key={step.state} className={`rounded-lg border p-2.5 ${step.reached ? `tone-${meta.tone}` : "border-line-subtle bg-surface-alt text-muted"}`}>
      <p className="text-[11px] font-bold">{meta.label}</p>
      <p className="mt-1 text-[11px] tabular-nums opacity-80">{step.at ? etTime(step.at).replace(" ET", "") : "대기"}</p>
    </li>;
  })}</ol>;
}

function CandidateDetail({ candidate }: { candidate: MomentumCandidate }) {
  const meta = stateMeta(candidate.state);
  return <div className="space-y-6">
    <header>
      <h2 className="text-xl font-semibold text-foreground">{candidate.symbol}<span className="ml-2 text-base font-normal text-muted">{formatUsd(candidate.price_usd)}</span></h2>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <StateChip state={candidate.state}/>
        <span className="text-sm text-foreground-secondary">{setupLabel(candidate.setup)}</span>
        <StatusBadge value={String(candidate.score)} label={`Score ${candidate.score}`} tone={scoreTone(candidate.score)}/>
      </div>
    </header>
    <section>
      <h3 className="label mb-2">모멘텀</h3>
      <Facts items={[["1분", signedPct(candidate.change_1m_pct, 1)], ["3분", signedPct(candidate.change_3m_pct, 1)], ["5분", signedPct(candidate.change_5m_pct, 1)]]}/>
    </section>
    <section>
      <h3 className="label mb-2">유동성 / 위치</h3>
      <Facts items={[["RVOL", rvolText(candidate.rvol)], ["거래대금", compactUsd(candidate.dollar_volume_usd)], ["스프레드", plainPct(candidate.spread_pct)], ["VWAP 이격", signedPct(candidate.vwap_distance_pct, 1)], ["당일 고가 이격", signedPct(candidate.hod_distance_pct, 1)], ["Score", String(candidate.score)]]}/>
    </section>
    <section>
      <h3 className="label mb-2">상태 흐름</h3>
      <Lifecycle candidate={candidate}/>
      <p className="mt-2 text-xs text-muted">상태 <span className="font-medium text-foreground-secondary">{meta.label}</span> <span className="font-mono text-[10px]">{candidate.state}</span>{candidate.drop_reason && <> · 사유 {dropReasonLabel(candidate.drop_reason)} <span className="font-mono text-[10px]">{candidate.drop_reason}</span></>}</p>
    </section>
    <p className="text-xs text-muted">모의 데이터입니다. 실제 시세, 실제 주문, 실시간 갱신은 연결되어 있지 않습니다.</p>
  </div>;
}

const STATE_OPTIONS = Object.keys(CANDIDATE_STATE_META) as CandidateState[];
const SETUP_OPTIONS = Object.keys(SETUP_META) as SetupType[];

/** The realtime scanner. Filtering and sorting run on the loaded snapshot only;
 *  nothing here polls, streams, or recalculates a momentum leg of its own. */
export function StrategyBScanner({ candidates }: { candidates: MomentumCandidate[] }) {
  const [filter, setFilter] = useState<ScannerFilter>(EMPTY_SCANNER_FILTER);
  const [sort, setSort] = useState<{ key: ScannerSortKey; direction: SortDirection } | null>({ key: "score", direction: "desc" });
  const [selected, setSelected] = useState<string | null>(null);

  const rows = useMemo(() => visibleCandidates(candidates, filter, sort), [candidates, filter, sort]);
  const selectedCandidate = candidates.find(candidate => candidate.symbol === selected) ?? null;
  const toggleSort = (key: ScannerSortKey) =>
    setSort(current => current?.key === key ? { key, direction: current.direction === "desc" ? "asc" : "desc" } : { key, direction: "desc" });

  return <section aria-labelledby="scanner-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id="scanner-title" className="font-semibold">실시간 스캐너</h2>
      <p className="text-xs text-muted">{rows.length}종목 표시 · 전체 {candidates.length}종목</p>
    </div>
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor="scanner-search">종목 검색</label>
      <input id="scanner-search" className="input h-9 w-40 sm:w-52" placeholder="종목 검색" value={filter.query} onChange={event => setFilter(current => ({ ...current, query: event.target.value }))}/>
      <label className="sr-only" htmlFor="scanner-state">상태 필터</label>
      <select id="scanner-state" className="input h-9 w-36 sm:w-44" value={filter.state} onChange={event => setFilter(current => ({ ...current, state: event.target.value as ScannerFilter["state"] }))}>
        <option value="ALL">상태 전체</option>
        {STATE_OPTIONS.map(state => <option key={state} value={state}>{CANDIDATE_STATE_META[state].label} ({state})</option>)}
      </select>
      <label className="sr-only" htmlFor="scanner-setup">셋업 필터</label>
      <select id="scanner-setup" className="input h-9 w-36 sm:w-44" value={filter.setup} onChange={event => setFilter(current => ({ ...current, setup: event.target.value as ScannerFilter["setup"] }))}>
        <option value="ALL">셋업 전체</option>
        {SETUP_OPTIONS.map(setup => <option key={setup} value={setup}>{SETUP_META[setup].label}</option>)}
      </select>
      <div className="ml-auto flex flex-wrap items-center gap-1">
        <span className="text-[11px] text-muted">정렬</span>
        {SCANNER_SORT_KEYS.map(option => <button key={option.key} type="button" aria-pressed={sort?.key === option.key} onClick={() => toggleSort(option.key)} className={`btn-compact ${sort?.key === option.key ? "btn-compact-active" : ""}`}>{option.label}</button>)}
      </div>
    </div>
    {rows.length === 0
      ? <EmptyState title="조건에 맞는 후보가 없습니다." description="검색어와 상태 · 셋업 필터를 확인해 주세요."/>
      : <div className="table-wrap relative"><table className="analysis-table">
        <caption className="sr-only">실시간 모멘텀 후보 목록. 행을 선택하면 상세가 열립니다.</caption>
        <thead><tr>
          <th scope="col">종목</th>
          <th scope="col" className="text-right">현재가</th>
          <SortableHeader label="1분" sortKey="change_1m_pct" sort={sort} onSort={toggleSort}/>
          <SortableHeader label="3분" sortKey="change_3m_pct" sort={sort} onSort={toggleSort}/>
          <SortableHeader label="5분" sortKey="change_5m_pct" sort={sort} onSort={toggleSort}/>
          <SortableHeader label="RVOL" sortKey="rvol" sort={sort} onSort={toggleSort}/>
          <th scope="col" className="text-right">거래대금</th>
          <th scope="col" className="text-right">스프레드</th>
          <SortableHeader label="Score" sortKey="score" sort={sort} onSort={toggleSort}/>
          <th scope="col">상태</th>
          <th scope="col">셋업</th>
        </tr></thead>
        <tbody>{rows.map(row => <tr key={row.symbol} data-scanner-row={row.symbol}>
          <td><button type="button" onClick={() => setSelected(row.symbol)} className="font-bold text-foreground underline-offset-4 hover:underline focus:underline">{row.symbol}<span className="sr-only"> 상세 보기</span></button></td>
          <td className="text-right tabular-nums text-foreground">{formatUsd(row.price_usd)}</td>
          <td className={`text-right tabular-nums ${numericToneClass(row.change_1m_pct)}`}>{signedPct(row.change_1m_pct, 1)}</td>
          <td className={`text-right tabular-nums ${numericToneClass(row.change_3m_pct)}`}>{signedPct(row.change_3m_pct, 1)}</td>
          <td className={`text-right tabular-nums ${numericToneClass(row.change_5m_pct)}`}>{signedPct(row.change_5m_pct, 1)}</td>
          <td className="text-right tabular-nums">{rvolText(row.rvol)}</td>
          <td className="text-right tabular-nums">{compactUsd(row.dollar_volume_usd)}</td>
          <td className="text-right tabular-nums">{plainPct(row.spread_pct)}</td>
          <td className="text-right"><span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-bold tabular-nums tone-${scoreTone(row.score)}`}>{row.score}</span></td>
          <td><StateChip state={row.state}/></td>
          <td className="whitespace-nowrap" title={row.setup ?? undefined}>{setupLabel(row.setup)}</td>
        </tr>)}</tbody>
      </table></div>}
    <Drawer open={selectedCandidate !== null} title={`${selectedCandidate?.symbol ?? "후보"} · 실시간 모멘텀 상세`} onClose={() => setSelected(null)}>
      {selectedCandidate && <CandidateDetail candidate={selectedCandidate}/>}
    </Drawer>
  </section>;
}
