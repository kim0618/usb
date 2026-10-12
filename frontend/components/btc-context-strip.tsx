"use client";

import {
  ageText, BTC_CONTEXT_BASE, type BtcContextPayload, compactUsd, contextLine, type ContextPosition,
  entryComparison, entryDistancePct, entryFor, flowHorizonRows, type FlowHorizonRow, MISSING,
  type PathRow, pctText, priceText, setStructureDisplay, setWallSelection, type StructureDisplay,
  useBtcContext, useStructureDisplay, useWallSelection, wallPaths, wallSummary, type WallSelection,
  type WallSideSummary,
} from "@/lib/btc-context";

/** The panel under the BTC chart (V3): where the next walls are, measured from where it matters.
 *
 *  Without a position it measures from the current price (CURRENT-MARKET). With one it measures
 *  from the entry (POSITION): every wall's leading figure is its distance from the entry in the
 *  price's own direction (+ above the entry, - below, LONG and SHORT alike), so the numbers hold
 *  still while the price moves; the distance from the current price is the tooltip and the
 *  detail. Above the price is the up path (ask walls), below is the down path (bid walls),
 *  nearest first, with the room between consecutive walls and how many walls the collector
 *  published on that side.
 *
 *  A side with no row says why in the collector's own terms: "none" only when the collector read
 *  the side and found no qualifying wall; "--" when there is no current reading at all.
 *
 *  Nothing here is advice. There is no hold, exit, target or stop word, no colour that grades a
 *  wall, no sign turned towards the position's gain, and no reading of a change: entry and
 *  current are two rows of figures side by side. An entry row exists only when the recorder wrote
 *  one; nothing stands in for it. */

const SELECTIONS: { value: WallSelection; text: string }[] = [
  { value: "NEAR", text: "가까운 순" }, { value: "SIZE", text: "큰 순" },
];

function SelectionSwitch({ value }: { value: WallSelection }) {
  return (
    <span className="ml-auto inline-flex overflow-hidden rounded border border-line text-[10px]"
      role="group" aria-label="벽 선택 기준" data-testid="btc-context-selection">
      {SELECTIONS.map(option => (
        <button key={option.value} type="button" aria-pressed={value === option.value}
          onClick={() => setWallSelection(option.value)}
          className={`px-1.5 py-0.5 ${value === option.value ? "bg-line text-foreground" : "text-muted"}`}>
          {option.text}
        </button>
      ))}
    </span>
  );
}

const STRUCTURE_OPTIONS: { value: StructureDisplay; text: string }[] = [
  { value: "ANNOTATION", text: "참고선" }, { value: "HIDDEN", text: "숨김" },
];

/** The V2 resistance/support lines: a faint reference annotation or hidden. A preview choice. */
function StructureSwitch({ value }: { value: StructureDisplay }) {
  return (
    <span className="inline-flex items-center gap-1" data-testid="btc-context-structure">
      <span>저항·지지</span>
      <span className="inline-flex overflow-hidden rounded border border-line" role="group" aria-label="저항·지지 참고선">
        {STRUCTURE_OPTIONS.map(option => (
          <button key={option.value} type="button" aria-pressed={value === option.value}
            onClick={() => setStructureDisplay(option.value)}
            className={`px-1.5 py-0 ${value === option.value ? "bg-line text-foreground" : "text-muted"}`}>
            {option.text}
          </button>
        ))}
      </span>
    </span>
  );
}

function SideCell({ side, testId }: { side: WallSideSummary; testId: string }) {
  return (
    <div data-testid={testId} className="min-w-0">
      <p className="text-[10px] font-medium text-muted">{side.name} · {side.order}</p>
      <p className="text-[12px] font-semibold tabular-nums text-foreground" data-testid={`${testId}-value`}>
        {side.text}
      </p>
    </div>
  );
}

/** Sell and buy colours: the same hues as the chart's upper (red) and lower (teal) walls. */
const SELL_COLOR = "rgba(244,63,94,0.85)";
const BUY_COLOR = "rgba(20,184,166,0.85)";
const EVEN_COLOR = "rgba(148,163,184,0.6)";

/** One horizon as a two-sided bar. The centre line is 50:50; each half is 0..100 % of its own
 *  side, sell growing left in red and buy growing right in teal, so the shape (which half is
 *  longer) is the direction before any number is read. No reading is two empty halves. */
function HorizonBar({ row }: { row: FlowHorizonRow }) {
  const has = row.lean != null && row.buyPct != null && row.sellPct != null;
  const even = row.lean === "EVEN";
  const word = row.lean === "BUY" ? "매수 우세" : row.lean === "SELL" ? "매도 우세" : row.lean === "EVEN" ? "중립" : "자료 없음";
  return (
    <li data-testid={`flow-horizon-${row.key}`} data-lean={row.lean ?? ""} data-buy={row.buyPct ?? ""}
      data-sell={row.sellPct ?? ""}
      aria-label={`${row.label} 체결 흐름 ${has ? `${word} ${row.lean === "SELL" ? row.sellPct : row.buyPct}%` : word}`}
      className="grid grid-cols-[3.25rem_minmax(0,1fr)_4.5rem] items-center gap-x-2 text-[11px] tabular-nums">
      <span className="text-muted">{row.label}</span>
      <span aria-hidden="true" className="relative flex h-2.5 items-center">
        <span className="flex h-full w-1/2 justify-end overflow-hidden rounded-l-sm bg-line/40">
          {has && <span data-testid="flow-bar-sell" className="h-full"
            style={{ width: `${row.sellPct}%`, background: even ? EVEN_COLOR : SELL_COLOR }} />}
        </span>
        <span className="h-4 w-px bg-muted/70" />
        <span className="flex h-full w-1/2 justify-start overflow-hidden rounded-r-sm bg-line/40">
          {has && <span data-testid="flow-bar-buy" className="h-full"
            style={{ width: `${row.buyPct}%`, background: even ? EVEN_COLOR : BUY_COLOR }} />}
        </span>
      </span>
      <span data-testid={`flow-horizon-${row.key}-text`}
        className={`text-right text-[11px] ${has ? "text-foreground" : "text-muted"}`}>{row.text}</span>
    </li>
  );
}

function stamp(ms: number | null): string {
  if (ms == null) return MISSING;
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function clock(ms: number | null): string {
  if (ms == null) return MISSING;
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function wallList(rows: PathRow[]): string {
  return rows.length === 0 ? MISSING : rows.map(row => priceText(row.price)).join(" / ");
}

/** The detail rows of one side, "상단벽1 83,730.2 ...". Everything the compact panel leaves out. */
function WallDetail({ rows, mode }: { rows: PathRow[]; mode: "MARKET" | "POSITION" }) {
  return (
    <>
      {rows.map(row => (
        <li key={row.id} data-testid="wall-detail-row" data-side={row.side} data-price={row.price}
          className="tabular-nums">
          <span className="font-semibold text-foreground">{row.side === "ASK" ? "상단벽" : "하단벽"}{row.order}</span>
          {" "}{priceText(row.price)}
          {" · 현재 대비 "}{pctText(row.currentPct)}
          {mode === "POSITION" && <>{" · 진입 대비 "}{pctText(row.entryPct)}</>}
          {" · 간격 "}{pctText(row.gapPct).replace(/^[+-]/, "")}
          {" · "}{compactUsd(row.notional)}
          {" · "}{ageText(row.ageMs)} 유지
          {row.continuity === "CARRIED" ? " · 이어짐" : row.continuity === "NEW" ? " · 새로 관측" : ""}
          {row.multiple != null && <>{" · 배수 "}{row.multiple}</>}
          {row.firstSeenMs != null && <>{" · 최초 "}{clock(row.firstSeenMs)}</>}
          {" · 크기 "}{row.sizeRank}위
        </li>
      ))}
    </>
  );
}

/** Pure render, so tests and the polling wrapper share one markup.
 *
 *  The default area is the trailing aggressive flow only: four two-sided bars (10분, 1시간, 4시간,
 *  1일), independent of the chart's timeframe. Everything else (wall prices, gaps, size,
 *  persistence, OI, funding, basis, the 60 s flow, entry against current, the sort switch when it
 *  lives here) is behind "상세". `sortPlacement` HEADER keeps the 가까운 순 / 큰 순 switch beside
 *  the title (as it was); DETAIL moves it into the detail. */
export function BtcContextStripView({ payload, position = null, mark = null, selection = "NEAR",
  structure = "HIDDEN", sortPlacement = "HEADER" }: {
  payload: BtcContextPayload | null; position?: ContextPosition | null; mark?: number | null;
  selection?: WallSelection; structure?: StructureDisplay; sortPlacement?: "HEADER" | "DETAIL";
}) {
  const line = contextLine(payload);
  const paths = wallPaths(payload, { mark, position, selection });
  const summary = wallSummary(payload, { mark, position, selection });
  const rows = flowHorizonRows(payload);
  const entry = entryFor(payload, position);
  const compare = entryComparison(payload, entry);
  const positionMode = paths.mode === "POSITION" && position != null;
  const derivColor = line.live.derivatives ? "text-foreground" : "text-muted";
  const ends = payload?.walls?.recent_ends ?? [];
  const entryLabel = entry
    ? `진입 ${stamp(compare.entryMs)} · ${compare.source === "FIXTURE" ? "검증용 기록" : "기록"}`
    : "진입 · 기록 없음";
  const coverage = rows.map(r => `${r.label} ${r.coverage == null ? MISSING : `${r.coverage}%`}`).join(" · ");
  return (
    <section data-testid="btc-context-strip" data-mode={paths.mode} data-sort-placement={sortPlacement}
      aria-label="BTC 체결 흐름: 10분 1시간 4시간 1일"
      className="rounded-lg border border-line bg-surface px-3 py-1.5">
      <div className="flex items-center gap-2 text-[11px]">
        <span className="font-semibold text-foreground" data-testid="flow-horizon-title">체결 흐름</span>
        <span className="text-[10px] text-muted">매도</span>
        <span aria-hidden="true" className="text-[10px] text-muted">|</span>
        <span className="text-[10px] text-muted">매수</span>
        {sortPlacement === "HEADER" && <SelectionSwitch value={selection} />}
      </div>
      <ul data-testid="flow-horizons" className="mt-1 space-y-1">
        {rows.map(row => <HorizonBar key={row.key} row={row} />)}
      </ul>

      <details className="group mt-1.5 border-t border-line pt-1" data-testid="btc-context-details">
        <summary className="flex cursor-pointer list-none items-center gap-1 text-[11px] text-muted [&::-webkit-details-marker]:hidden"
          data-testid="btc-context-details-toggle">
          <span>상세</span>
          <span aria-hidden="true" className="text-[10px] transition-transform group-open:rotate-180">▾</span>
        </summary>
        <div data-testid="btc-context-detail" className="mt-1 space-y-1.5 text-[11px] tabular-nums text-muted">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
            <span className="rounded bg-line px-1 text-[10px] text-foreground" data-testid="btc-context-mode">
              {positionMode ? "진입가 기준" : "현재가 기준"}
            </span>
            {positionMode && position && (
              <span data-testid="btc-context-entry">
                <span className="text-muted">진입 </span>
                <span className="font-semibold text-foreground">{priceText(position.entry)}</span>
              </span>
            )}
            <span data-testid="btc-context-current" title="벽과 같은 Binance 호가창의 중간가">
              <span className="text-muted">현재 </span>
              <span className="font-semibold text-foreground">{paths.current != null ? priceText(paths.current) : MISSING}</span>
            </span>
            {positionMode && position && paths.current != null && (
              <span data-testid="btc-context-current-vs-entry" className="text-foreground"
                title="현재가가 진입가보다 위면 +, 아래면 -">
                <span className="text-muted">진입 대비 </span>{pctText(entryDistancePct(position, paths.current))}
              </span>
            )}
            {sortPlacement === "DETAIL" && <SelectionSwitch value={selection} />}
          </div>
          <div className="grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-2" data-testid="btc-context-walls">
            <SideCell side={summary.up} testId="btc-context-path-up" />
            <SideCell side={summary.down} testId="btc-context-path-down" />
          </div>
          <ul data-testid="btc-context-wall-detail" className="space-y-0.5">
            <WallDetail rows={paths.up} mode={paths.mode} />
            <WallDetail rows={paths.down} mode={paths.mode} />
          </ul>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-[12px] font-semibold">
            <span className={derivColor} data-testid="btc-context-oi"
              data-live={line.live.derivatives ? "1" : "0"}>{line.oi}</span>
            <span className={derivColor} data-testid="btc-context-funding"
              data-live={line.live.derivatives ? "1" : "0"}>{line.funding}</span>
          </div>
          {positionMode ? (
            /* ENTRY against CURRENT, the same figures in the same order. The entry row is the
               recorder's record of the fill or "기록 없음"; it is never filled from the current
               reading. */
            <div data-testid="btc-context-compare" className="space-y-0.5">
              <p data-testid="btc-context-entry-detail" className="flex flex-wrap gap-x-2">
                <span className="rounded bg-line px-1 text-[10px] text-foreground">{entryLabel}</span>
                <span>체결흐름 {compare.flow.entry}</span>
                <span>OI {compare.oi.entry}</span>
                <span>펀딩 {compare.funding.entry}</span>
                <span>베이시스 {compare.basis.entry}</span>
                <span>상단벽 {compare.walls.ask}</span>
                <span>하단벽 {compare.walls.bid}</span>
              </p>
              <p data-testid="btc-context-current-detail" className="flex flex-wrap gap-x-2">
                <span className="rounded bg-line px-1 text-[10px] text-foreground">현재</span>
                <span>체결흐름 {compare.flow.current}</span>
                <span>{line.detail.oiValue}</span>
                <span>펀딩 {compare.funding.current}</span>
                <span>베이시스 {compare.basis.current}</span>
                <span>상단벽 {wallList(paths.up)}</span>
                <span>하단벽 {wallList(paths.down)}</span>
              </p>
              <p className="flex flex-wrap gap-x-4"><span>{line.detail.oiChange}</span></p>
            </div>
          ) : (
            <div className="flex flex-wrap gap-x-4 gap-y-0.5" data-testid="btc-context-figures">
              <span data-testid="btc-context-flow60" data-live={line.live.flow ? "1" : "0"}>
                60초 체결 {line.flow.text}
              </span>
              <span>{line.detail.oiValue}</span>
              <span>{line.detail.oiChange}</span>
              <span>{line.detail.basis}</span>
            </div>
          )}
          {ends.length > 0 && (
            <p data-testid="btc-context-recent-ends">
              최근 사라진 벽: {ends.slice(0, 3).map(end =>
                `${end.side === "ASK" ? "상단벽" : "하단벽"} ${priceText(Number(end.price))} ${compactUsd(end.notional_usdt)}${end.price_reached === false ? " 가격 미도달" : end.price_reached ? " 가격 도달" : ""}`).join(" · ")}
            </p>
          )}
          <p data-testid="flow-horizon-coverage" className="text-[10px]">
            체결 흐름은 직전 N분의 거래소 1분 집계 합(현재 시각 기준 trailing). 집계 범위 {coverage}
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-[10px]">
            <StructureSwitch value={structure} />
            <span>벽은 수집기가 지금 공개한 호가벽 중 측면당 3개. 색은 가까운 순서이고 크기가 아닙니다.</span>
          </div>
        </div>
      </details>
    </section>
  );
}

/** Preview switch for the review: `?sort=detail` moves the 가까운 순 / 큰 순 switch into the
 *  detail. Nothing else reads the URL; the default is the switch beside the title. */
function sortPlacementFromLocation(): "HEADER" | "DETAIL" {
  try {
    return new URLSearchParams(window.location.search).get("sort") === "detail" ? "DETAIL" : "HEADER";
  } catch {
    return "HEADER";
  }
}

/** BTC only. Anything else renders nothing, so a HOT or held-symbol tab never shows BTC figures. */
export function BtcContextStrip({ symbol, base, position = null, mark = null }: {
  symbol: string; base?: string; position?: ContextPosition | null; mark?: number | null;
}) {
  if (symbol !== "BTCUSDT") return null;
  return <BtcContextStripPolling base={base} position={position} mark={mark} />;
}

function BtcContextStripPolling({ base = BTC_CONTEXT_BASE, position, mark }: {
  base?: string; position: ContextPosition | null; mark: number | null;
}) {
  const payload = useBtcContext(base);
  const selection = useWallSelection();
  const structure = useStructureDisplay();
  return <BtcContextStripView payload={payload} position={position} mark={mark} selection={selection}
    structure={structure} sortPlacement={sortPlacementFromLocation()} />;
}
