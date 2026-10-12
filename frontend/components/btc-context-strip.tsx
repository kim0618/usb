"use client";

import {
  ageText, BTC_CONTEXT_BASE, type BtcContextPayload, compactUsd, contextLine, type ContextPosition,
  entryComparison, entryDistancePct, entryFor, MISSING, type PathRow, pctText, priceText,
  setWallSelection, useBtcContext, useWallSelection, wallPaths, type WallSelection,
} from "@/lib/btc-context";

/** The panel under the BTC chart (V3): where the next walls are, measured from where it matters.
 *
 *  Without a position it measures from the current price (CURRENT-MARKET). With one it measures
 *  from the entry (POSITION): every wall's leading figure is its distance from the entry in the
 *  position's favour, so the numbers hold still while the price moves; the distance from the
 *  current price is the tooltip and the detail. Above the price is the up path (ask walls), below
 *  is the down path (bid walls), nearest first, with the room between consecutive walls.
 *
 *  Nothing here is advice. There is no hold, exit, target or stop word, no colour that grades a
 *  wall, and no reading of a change: entry and current are two numbers side by side. */

function FlowBar({ buyPct }: { buyPct: number | null }) {
  // Buy share from the left in the buy colour, the rest in the sell colour. No reading is a
  // plain grey bar of the same size, so the line does not jump when a reading arrives.
  return (
    <span aria-hidden="true" data-testid="btc-context-flow-bar"
      className={`relative inline-block h-1.5 w-12 overflow-hidden rounded-full ${buyPct == null ? "bg-line" : "bg-danger opacity-80"}`}>
      {buyPct != null && (
        <span className="absolute inset-y-0 left-0 bg-success" style={{ width: `${buyPct}%` }} />
      )}
    </span>
  );
}

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

function PathColumn({ title, rows, mode, testId }: {
  title: string; rows: PathRow[]; mode: "MARKET" | "POSITION"; testId: string;
}) {
  const name = rows[0]?.side === "BID" || testId.endsWith("down") ? "매수벽" : "매도벽";
  return (
    <div data-testid={testId} className="min-w-0">
      <p className="text-[10px] font-medium text-muted">{title}</p>
      <ol className="mt-0.5 space-y-0.5">
        {rows.length === 0 && <li className="text-[11px] text-muted" data-testid={`${testId}-empty`}>{MISSING}</li>}
        {rows.map(row => (
          <li key={row.id} data-testid={`${testId}-row`} data-price={row.price}
            title={mode === "POSITION" ? `현재 대비 ${pctText(row.currentPct)}` : undefined}
            className="grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-baseline gap-x-1.5 text-[11px] tabular-nums">
            <span className="text-[10px] text-muted">{name}{row.order}</span>
            <span className="whitespace-nowrap font-semibold text-foreground" data-testid="path-price">{priceText(row.price)}</span>
            <span className="whitespace-nowrap text-right text-foreground" data-testid="path-primary">{pctText(row.primaryPct)}</span>
            <span className="whitespace-nowrap text-[10px] text-muted" data-testid="path-gap">간격 {pctText(row.gapPct).replace(/^[+-]/, "")}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function WallDetail({ rows, mode }: { rows: PathRow[]; mode: "MARKET" | "POSITION" }) {
  return (
    <>
      {rows.map(row => (
        <li key={row.id} data-testid="wall-detail-row" className="tabular-nums">
          {row.side === "ASK" ? "매도벽" : "매수벽"}{row.order} {priceText(row.price)}
          {mode === "POSITION" && <> · 현재 대비 {pctText(row.currentPct)}</>}
          {" · "}{ageText(row.ageMs)} 유지 · {compactUsd(row.notional)} · 크기 {row.sizeRank}위
        </li>
      ))}
    </>
  );
}

function stamp(ms: number | null): string {
  if (ms == null) return MISSING;
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Pure render, so tests and the polling wrapper share one markup. */
export function BtcContextStripView({ payload, position = null, mark = null, selection = "NEAR" }: {
  payload: BtcContextPayload | null; position?: ContextPosition | null; mark?: number | null;
  selection?: WallSelection;
}) {
  const line = contextLine(payload);
  const paths = wallPaths(payload, { mark, position, selection });
  const entry = entryFor(payload, position);
  const compare = entryComparison(payload, entry);
  const positionMode = paths.mode === "POSITION" && position != null;
  const flowColor = !line.live.flow ? "text-muted"
    : line.flow.side === "BUY" ? "text-success" : line.flow.side === "SELL" ? "text-danger" : "text-foreground";
  const derivColor = line.live.derivatives ? "text-foreground" : "text-muted";
  const ends = payload?.walls?.recent_ends ?? [];
  return (
    <section data-testid="btc-context-strip" data-mode={paths.mode}
      aria-label="BTC 호가벽·체결흐름·미결제약정·펀딩"
      className="rounded-lg border border-line bg-surface px-3 py-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] tabular-nums">
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
          <span data-testid="btc-context-current-vs-entry" className="text-foreground">
            <span className="text-muted">진입 대비 </span>{pctText(entryDistancePct(position, paths.current))}
          </span>
        )}
        <SelectionSwitch value={selection} />
      </div>

      {/* Side by side where there is room; stacked (up path first) on a phone, where two columns
          leave a price and its percentage too little width and they overprint. */}
      <div className="mt-1 grid gap-x-3 gap-y-1 sm:grid-cols-2">
        <PathColumn title={positionMode ? "위 · 다음 매도벽 (진입 대비 거리)" : "위 · 다음 매도벽 (현재 대비)"}
          rows={paths.up} mode={paths.mode} testId="btc-context-path-up" />
        <PathColumn title={positionMode ? "아래 · 다음 매수벽 (진입 대비 거리)" : "아래 · 다음 매수벽 (현재 대비)"}
          rows={paths.down} mode={paths.mode} testId="btc-context-path-down" />
      </div>

      <details className="group mt-1 border-t border-line pt-1">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-4 gap-y-1 text-[12px] font-semibold tabular-nums [&::-webkit-details-marker]:hidden">
          {positionMode ? (
            <>
              <span className="flex items-center gap-1.5" data-testid="btc-context-flow"
                data-live={line.live.flow ? "1" : "0"}>
                <span className="text-[10px] font-medium text-muted">체결흐름</span>
                <span className="text-foreground" data-testid="btc-context-flow-entry">
                  <span className="text-[10px] font-medium text-muted">진입 </span>{compare.flow.entry}
                </span>
                <span className="text-muted">→</span>
                <span className={flowColor} data-testid="btc-context-flow-current">
                  <span className="text-[10px] font-medium text-muted">현재 </span>{compare.flow.current}
                </span>
              </span>
              <span className={derivColor} data-testid="btc-context-oi" data-live={line.live.derivatives ? "1" : "0"}>
                OI {compare.oi.entry} → {compare.oi.current}
              </span>
              <span className={derivColor} data-testid="btc-context-funding" data-live={line.live.derivatives ? "1" : "0"}>
                펀딩 {compare.funding.entry} → {compare.funding.current}
              </span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-1.5" data-testid="btc-context-flow"
                data-live={line.live.flow ? "1" : "0"}>
                <span className="text-[10px] font-medium text-muted">체결흐름</span>
                <span className={flowColor}>{line.flow.text}</span>
                <FlowBar buyPct={line.live.flow ? line.flow.buyPct : null} />
              </span>
              <span className={derivColor} data-testid="btc-context-oi"
                data-live={line.live.derivatives ? "1" : "0"}>{line.oi}</span>
              <span className={derivColor} data-testid="btc-context-funding"
                data-live={line.live.derivatives ? "1" : "0"}>{line.funding}</span>
            </>
          )}
          <span aria-hidden="true" className="ml-auto text-[10px] text-muted transition-transform group-open:rotate-180">▾</span>
        </summary>
        <div data-testid="btc-context-detail" className="mt-1 space-y-1 text-[11px] tabular-nums text-muted">
          <div className="flex flex-wrap gap-x-4 gap-y-0.5">
            <span>{line.detail.oiValue}</span>
            <span>{line.detail.oiChange}</span>
            {positionMode
              ? <span>베이시스 {compare.basis.entry} → {compare.basis.current}</span>
              : <span>{line.detail.basis}</span>}
          </div>
          {positionMode && (
            <p data-testid="btc-context-entry-detail">
              진입 당시({stamp(compare.entryMs)}) 매도벽 {compare.walls.ask} · 매수벽 {compare.walls.bid}
              {!entry && " · 진입 기록 없음"}
            </p>
          )}
          <ul data-testid="btc-context-wall-detail" className="space-y-0.5">
            <WallDetail rows={paths.up} mode={paths.mode} />
            <WallDetail rows={paths.down} mode={paths.mode} />
          </ul>
          {ends.length > 0 && (
            <p data-testid="btc-context-recent-ends">
              최근 사라진 벽: {ends.slice(0, 3).map(end =>
                `${end.side === "ASK" ? "매도" : "매수"} ${priceText(Number(end.price))} ${compactUsd(end.notional_usdt)}${end.price_reached === false ? " 가격 미도달" : end.price_reached ? " 가격 도달" : ""}`).join(" · ")}
            </p>
          )}
          <p className="text-[10px]">벽은 수집기가 지금 보고 있는 50만 USDT 이상 호가(측면당 최대 6개 중 3개). 유지 시간과 크기는 수집기 수치 그대로.</p>
        </div>
      </details>
    </section>
  );
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
  return <BtcContextStripView payload={payload} position={position} mark={mark} selection={selection} />;
}
