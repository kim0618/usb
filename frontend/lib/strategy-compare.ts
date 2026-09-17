import { moneyText } from "@/lib/format";
import { krwToUsd } from "@/lib/fx";
import { plainPct, signedPct, signedR } from "@/lib/strategy-b";
import type { ComparisonSide, EquityPoint, StrategyComparison } from "@/types/strategy-b";

/** Chart viewBox. The SVG is stretched to its container, so strokes are drawn with
 *  vector-effect="non-scaling-stroke" and every label lives outside the SVG. */
export const CHART_WIDTH = 1000;
export const CHART_HEIGHT = 320;
const PADDING_Y = 12;

export interface ChartPoint { x: number; y: number }

/** The value window the curve is drawn in, padded so the line never touches the frame.
 *  `keys` narrows it to the series actually drawn, so a single-strategy chart is not
 *  squashed by the other strategy's range. */
export function equityRange(curve: readonly EquityPoint[], baseline: number, keys: ReadonlyArray<"a_equity_krw" | "b_equity_krw"> = ["a_equity_krw", "b_equity_krw"]): { min: number; max: number } {
  const values = curve.flatMap(point => keys.map(key => point[key])).concat(baseline);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const margin = (max - min) * 0.12 || Math.max(1, max * 0.01);
  return { min: min - margin, max: max + margin };
}

const scaleX = (index: number, count: number) => count <= 1 ? CHART_WIDTH / 2 : (index / (count - 1)) * CHART_WIDTH;

export function scaleY(value: number, range: { min: number; max: number }): number {
  const span = range.max - range.min || 1;
  const ratio = (value - range.min) / span;
  return CHART_HEIGHT - PADDING_Y - ratio * (CHART_HEIGHT - PADDING_Y * 2);
}

export function seriesPoints(curve: readonly EquityPoint[], key: "a_equity_krw" | "b_equity_krw", range: { min: number; max: number }): ChartPoint[] {
  return curve.map((point, index) => ({ x: scaleX(index, curve.length), y: scaleY(point[key], range) }));
}

export const linePath = (points: readonly ChartPoint[]): string =>
  points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");

/** Four evenly spaced value ticks, top to bottom. */
export function axisTicks(range: { min: number; max: number }, count = 4): number[] {
  return Array.from({ length: count }, (_, index) => range.max - ((range.max - range.min) * index) / (count - 1));
}

/** Axis label for a KRW-stored equity tick, in USD like every other account figure:
 *  "$7.9K". Converted through lib/fx, the same rate the tooltip and the cards use. */
export const compactEquityUsd = (krw: number): string => new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1,
}).format(krwToUsd(krw));

export interface DailyRow {
  date: string;
  a_return_pct: number;
  b_return_pct: number;
  a_pnl_krw: number;
  b_pnl_krw: number;
}

/** Daily returns are derived from the curve, never stated on their own, so the daily
 *  table and the equity chart always tell the same story. Day one is measured against
 *  the initial capital. */
export function dailyRows(curve: readonly EquityPoint[], initialA: number, initialB: number): DailyRow[] {
  return curve.map((point, index) => {
    const previousA = index === 0 ? initialA : curve[index - 1].a_equity_krw;
    const previousB = index === 0 ? initialB : curve[index - 1].b_equity_krw;
    return {
      date: point.date,
      a_return_pct: (point.a_equity_krw / previousA - 1) * 100,
      b_return_pct: (point.b_equity_krw / previousB - 1) * 100,
      a_pnl_krw: point.a_equity_krw - previousA,
      b_pnl_krw: point.b_equity_krw - previousB,
    };
  });
}

/** Worst peak-to-trough of a series, in percent. Returns 0 for a series that only rises. */
export function maxDrawdownPct(values: readonly number[]): number {
  let peak = -Infinity;
  let worst = 0;
  for (const value of values) {
    peak = Math.max(peak, value);
    worst = Math.min(worst, value / peak - 1);
  }
  return worst * 100;
}

export interface ComparisonMetricRow {
  label: string;
  a: string;
  b: string;
  hint?: string;
  /** Set on account amounts. The view stacks USD over KRW from these stored KRW values;
   *  `a` and `b` then hold the same pair on one line. */
  money?: { a: number; b: number; signed: boolean };
}

/** The KPI table, formatted. Deliberately free of any winner, ranking, or recommendation:
 *  both columns are stated the same way and the reader decides. */
export function comparisonMetrics(comparison: StrategyComparison): ComparisonMetricRow[] {
  const both = (pick: (side: ComparisonSide) => string) => ({ a: pick(comparison.a), b: pick(comparison.b) });
  const money = (pick: (side: ComparisonSide) => number, signed = false) => ({
    ...both(side => moneyText(pick(side), signed)),
    money: { a: pick(comparison.a), b: pick(comparison.b), signed },
  });
  return [
    { label: "초기 자본", ...money(side => side.initial_capital_krw) },
    { label: "현재 자산", ...money(side => side.current_equity_krw) },
    { label: "수익률", ...both(side => signedPct(side.return_pct)) },
    { label: "순손익", ...money(side => side.net_pnl_krw, true) },
    { label: "매매 횟수", ...both(side => `${side.trades}회`) },
    { label: "승률", ...both(side => plainPct(side.win_rate_pct, 1)) },
    { label: "Profit Factor", hint: "총이익을 총손실로 나눈 값입니다.", ...both(side => side.profit_factor.toFixed(2)) },
    { label: "기대값", hint: "매매 1회당 기대 수익을 리스크 단위(R)로 나타냅니다.", ...both(side => signedR(side.expectancy_r)) },
    { label: "최대 낙폭", hint: "기간 중 고점 대비 최대 하락폭입니다.", ...both(side => plainPct(side.max_drawdown_pct, 1)) },
    { label: "평균 R", hint: "종료된 매매 1회당 평균 R 성과입니다.", ...both(side => signedR(side.average_r)) },
  ];
}
