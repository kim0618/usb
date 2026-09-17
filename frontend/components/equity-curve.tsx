"use client";

import { useId, useState } from "react";
import { Money } from "@/components/money";
import { moneyText } from "@/lib/format";
import { numericToneClass, signedPct } from "@/lib/strategy-b";
import {
  CHART_HEIGHT, CHART_WIDTH, axisTicks, compactEquityUsd, equityRange, linePath, scaleY, seriesPoints,
} from "@/lib/strategy-compare";
import type { EquityPoint } from "@/types/strategy-b";

const dateLabel = (date: string) => date.slice(5).replace("-", "/");

/** Two equity curves on one frame, drawn with inline SVG. The project has no chart
 *  dependency and this stage adds none. Strokes use non-scaling-stroke because the SVG is
 *  stretched to its container, and every label is HTML so it never stretches with it. */
/** `height` only changes the drawn frame; the geometry, labels and tooltip are identical,
 *  so the dashboard can show a shorter version of the very same chart. */
export function EquityCurve({ curve, baseline, labelA, labelB, height = 260, only }: { curve: EquityPoint[]; baseline: number; labelA: string; labelB: string; height?: number; only?: "A" | "B" }) {
  const [active, setActive] = useState<number | null>(null);
  const titleId = useId();
  if (curve.length === 0) return null;

  const showA = only !== "B";
  const showB = only !== "A";
  const range = equityRange(curve, baseline, [...(showA ? ["a_equity_krw" as const] : []), ...(showB ? ["b_equity_krw" as const] : [])]);
  const pointsA = seriesPoints(curve, "a_equity_krw", range);
  const pointsB = seriesPoints(curve, "b_equity_krw", range);
  const ticks = axisTicks(range);
  const baselineY = scaleY(baseline, range);
  const point = active == null ? null : curve[active];
  const activeLeft = active == null ? 0 : (active / Math.max(1, curve.length - 1)) * 100;

  return <div className="panel p-4">
    <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
      {showA && <span className="flex items-center gap-2 text-xs font-semibold text-foreground-secondary"><span aria-hidden="true" className="inline-block h-0.5 w-5 rounded" style={{ background: "var(--series-a)" }}/>{labelA}</span>}
      {showB && <span className="flex items-center gap-2 text-xs font-semibold text-foreground-secondary"><span aria-hidden="true" className="inline-block h-0.5 w-5 rounded" style={{ background: "var(--series-b)" }}/>{labelB}</span>}
      <span className="flex items-center gap-2 text-xs text-muted"><span aria-hidden="true" className="inline-block h-px w-5 border-t border-dashed border-line"/>초기 자본 {moneyText(baseline)}</span>
    </div>
    <div className="flex gap-2">
      <div className="flex w-12 shrink-0 flex-col justify-between py-1 text-right text-[10px] tabular-nums text-muted" style={{ height }}>
        {ticks.map(tick => <span key={tick}>{compactEquityUsd(tick)}</span>)}
      </div>
      <div className="relative min-w-0 flex-1" style={{ height }}>
        <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} preserveAspectRatio="none" className="h-full w-full" role="img" aria-labelledby={titleId}>
          <title id={titleId}>{only === "B" ? labelB : only === "A" ? labelA : `${labelA}와 ${labelB}`}의 기간 자산 곡선</title>
          {ticks.map(tick => <line key={tick} x1="0" x2={CHART_WIDTH} y1={scaleY(tick, range)} y2={scaleY(tick, range)} stroke="var(--border-subtle)" strokeWidth="1" vectorEffect="non-scaling-stroke"/>)}
          <line x1="0" x2={CHART_WIDTH} y1={baselineY} y2={baselineY} stroke="var(--border)" strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke"/>
          {active != null && <line x1={pointsA[active].x} x2={pointsA[active].x} y1="0" y2={CHART_HEIGHT} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke"/>}
          {showA && <path d={linePath(pointsA)} fill="none" stroke="var(--series-a)" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>}
          {showB && <path d={linePath(pointsB)} fill="none" stroke="var(--series-b)" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>}
          {active != null && <>
            {showA && <circle cx={pointsA[active].x} cy={pointsA[active].y} r="3" fill="var(--series-a)" vectorEffect="non-scaling-stroke"/>}
            {showB && <circle cx={pointsB[active].x} cy={pointsB[active].y} r="3" fill="var(--series-b)" vectorEffect="non-scaling-stroke"/>}
          </>}
        </svg>
        <div className="absolute inset-0 flex" onMouseLeave={() => setActive(null)}>
          {curve.map((entry, index) => <div key={entry.date} className="h-full flex-1" data-curve-hit={entry.date} onMouseEnter={() => setActive(index)}/>)}
        </div>
        {/* Centred on the hovered day but clamped to the plot in px, so a narrow phone plot can
            never push the tooltip past the page edge. */}
        {point && <div className="pointer-events-none absolute top-2 z-10 w-44 max-w-full rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-panel"
          style={{ left: `clamp(0px, calc(${activeLeft}% - 5.5rem), calc(100% - min(11rem, 100%)))` }} role="status">
          <p className="font-semibold text-foreground">{dateLabel(point.date)}</p>
          <dl className="mt-1.5 space-y-1">
            {showA && <>
              <div className="flex items-center justify-between gap-2"><dt className="text-muted">{labelA}</dt><dd className="text-right tabular-nums text-foreground"><Money krw={point.a_equity_krw} size="cell"/></dd></div>
              <div className="flex items-center justify-between gap-2"><dt className="text-muted">수익률</dt><dd className={`tabular-nums ${numericToneClass(point.a_equity_krw / baseline - 1)}`}>{signedPct((point.a_equity_krw / baseline - 1) * 100)}</dd></div>
            </>}
            {showB && <>
              <div className={`flex items-center justify-between gap-2 ${showA ? "border-t border-line-subtle pt-1" : ""}`}><dt className="text-muted">{labelB}</dt><dd className="text-right tabular-nums text-foreground"><Money krw={point.b_equity_krw} size="cell"/></dd></div>
              <div className="flex items-center justify-between gap-2"><dt className="text-muted">수익률</dt><dd className={`tabular-nums ${numericToneClass(point.b_equity_krw / baseline - 1)}`}>{signedPct((point.b_equity_krw / baseline - 1) * 100)}</dd></div>
            </>}
          </dl>
        </div>}
      </div>
    </div>
    <div className="relative ml-12 mt-2 h-4 text-[10px] tabular-nums text-muted">
      {curve.map((entry, index) => {
        const last = index === curve.length - 1;
        if (!(index === 0 || last || index % 4 === 0) || (!last && index > curve.length - 6)) return null;
        const position = last ? { right: 0 } : index === 0 ? { left: 0 } : { left: `${(index / (curve.length - 1)) * 100}%` };
        return <span key={entry.date} className={`absolute whitespace-nowrap ${index === 0 || last ? "" : "-translate-x-1/2"}`} style={position}>{dateLabel(entry.date)}</span>;
      })}
    </div>
  </div>;
}
