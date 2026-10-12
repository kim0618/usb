"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { compactUsd, MISSING, priceText } from "@/lib/btc-context";

/** [진입분석] next to [코인탐색]: a LOCAL PREVIEW of the Entry Advisor's verdict for the current
 *  second (CONTRACT_ENTRY_ADVISOR_R0 section 10).
 *
 *  It exists only in a build with `NEXT_PUBLIC_ENTRY_ANALYSIS_PREVIEW=1`; production builds do
 *  not set it and render nothing here. One GET per click, no polling, no order, no callback,
 *  nothing written. Until a family passes its research gate the verdict is 관망 with "현재 검증된
 *  진입 조건 없음", and the panel never prints a direction on its own. Internal family and
 *  question names never reach the screen: the panel shows figures (wall persistence, size,
 *  flow, open interest, gaps) and the words 관망 / LONG / SHORT only. */

export const ENTRY_ANALYSIS_PREVIEW = (process.env.NEXT_PUBLIC_ENTRY_ANALYSIS_PREVIEW ?? "").trim() === "1";

export type EntryAnalysis = {
  available: boolean;
  verdict: "NO_TRADE" | "LONG" | "SHORT";
  verdict_word: string;
  statement: string;
  entry_zone: [string, string] | null;
  invalidation: string | null;
  targets: string[];
  expected_r: string | null;
  basis: {
    mid: string | null;
    ask: { price: string; notional_usdt: string | null; age_ms: number | null; continuity: string | null; gap_bp: number | null }[];
    bid: { price: string; notional_usdt: string | null; age_ms: number | null; continuity: string | null; gap_bp: number | null }[];
    flow: { window: string | null; buy_pct: number | null; sell_pct: number | null; live: boolean | null };
    oi: { value_usdt: string | null; change_pct: string | null; window: string | null; direction: string | null };
  };
  analysis_request_id: string;
};

export async function fetchEntryAnalysis(base: string, signal?: AbortSignal): Promise<EntryAnalysis> {
  const response = await fetch(`${base}/entry-analysis`, { method: "GET", cache: "no-store", signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body = await response.json() as EntryAnalysis;
  if (!body || typeof body.verdict !== "string" || !body.basis) throw new Error("unexpected payload");
  return body;
}

function age(ms: number | null): string {
  if (ms == null) return MISSING;
  const s = Math.floor(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m`;
}

function WallRows({ rows, name }: { rows: EntryAnalysis["basis"]["ask"]; name: string }) {
  if (rows.length === 0) return <li className="text-muted">{name} {MISSING}</li>;
  return (
    <>
      {rows.map((w, i) => (
        <li key={`${name}${i}`} className="tabular-nums" data-testid="entry-analysis-wall">
          {name}{i + 1} {priceText(Number(w.price))} · {compactUsd(w.notional_usdt)} · {age(w.age_ms)} 유지
          {w.continuity === "CARRIED" ? " · 이어짐" : w.continuity === "NEW" ? " · 새로 관측" : ""}
          {w.gap_bp != null ? ` · 간격 ${(w.gap_bp / 100).toFixed(2)}%` : ""}
        </li>
      ))}
    </>
  );
}

export function EntryAnalysisPanel({ analysis, error, loading }: {
  analysis: EntryAnalysis | null; error: string | null; loading: boolean;
}) {
  const verdict = analysis?.verdict_word ?? MISSING;
  const flow = analysis?.basis.flow;
  const flowText = flow && flow.buy_pct != null && flow.sell_pct != null
    ? (flow.buy_pct > flow.sell_pct ? `매수우세 ${flow.buy_pct}%` : flow.sell_pct > flow.buy_pct ? `매도우세 ${flow.sell_pct}%` : `중립 ${flow.buy_pct}%`)
    : MISSING;
  const oi = analysis?.basis.oi;
  return (
    <div id="entry-analysis-panel" role="dialog" aria-label="진입분석 (검증 전 프리뷰)" data-testid="entry-analysis-panel"
      className="absolute right-0 top-full z-20 mt-1 w-[22rem] max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-surface p-3 text-[12px] shadow-lg">
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] font-medium text-muted">현재 판단</span>
        <span className="rounded bg-line px-1 text-[10px] text-muted">검증 전 프리뷰</span>
      </div>
      <p className="mt-0.5 text-lg font-bold text-foreground" data-testid="entry-analysis-verdict">
        {loading ? "…" : error ? MISSING : verdict}
      </p>
      <p className="text-[11px] text-muted" data-testid="entry-analysis-statement">
        {error ? "분석 서버 응답 없음" : analysis?.statement ?? ""}
      </p>
      {analysis && analysis.verdict !== "NO_TRADE" && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 tabular-nums" data-testid="entry-analysis-plan">
          <dt className="text-muted">진입 후보</dt>
          <dd>{analysis.entry_zone ? `${priceText(Number(analysis.entry_zone[0]))} ~ ${priceText(Number(analysis.entry_zone[1]))}` : MISSING}</dd>
          <dt className="text-muted">무효화</dt>
          <dd>{analysis.invalidation ? priceText(Number(analysis.invalidation)) : MISSING}</dd>
          <dt className="text-muted">목표</dt>
          <dd>{analysis.targets.length ? analysis.targets.map((t, i) => `${i + 1}차 ${priceText(Number(t))}`).join(" · ") : MISSING}</dd>
          <dt className="text-muted">예상 R:R</dt>
          <dd>{analysis.expected_r ? `1 : ${analysis.expected_r}` : MISSING}</dd>
        </dl>
      )}
      <details className="mt-2 border-t border-line pt-1">
        <summary className="cursor-pointer list-none text-[11px] text-muted [&::-webkit-details-marker]:hidden">근거 ▾</summary>
        <div className="mt-1 space-y-1 text-[11px] text-muted" data-testid="entry-analysis-basis">
          <p>현재 {analysis?.basis.mid ? priceText(Number(analysis.basis.mid)) : MISSING} · 체결흐름 {flowText}
            {" · OI "}{oi?.value_usdt ? compactUsd(oi.value_usdt) : MISSING}
            {oi?.change_pct != null ? ` (${oi.window} ${Number(oi.change_pct) > 0 ? "+" : ""}${oi.change_pct}%)` : ""}</p>
          <ul className="space-y-0.5">
            <WallRows rows={analysis?.basis.ask ?? []} name="상단벽" />
            <WallRows rows={analysis?.basis.bid ?? []} name="하단벽" />
          </ul>
          <p className="text-[10px]">벽 유지시간·크기·체결흐름·OI는 수집기 수치 그대로. 이 화면은 주문을 보내지 않습니다.</p>
        </div>
      </details>
    </div>
  );
}

/** The button and its panel. Renders nothing unless the preview flag is set at build time and a
 *  context API base is configured; BTC only. */
export function EntryAnalysisPreview({ base, symbol }: { base?: string; symbol: string }) {
  const [open, setOpen] = useState(false);
  const [analysis, setAnalysis] = useState<EntryAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  const enabled = ENTRY_ANALYSIS_PREVIEW && Boolean(base) && symbol === "BTCUSDT";

  const load = useCallback(async () => {
    if (!base) return;
    setLoading(true);
    setError(null);
    try {
      setAnalysis(await fetchEntryAnalysis(base));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [base]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    const onPointer = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  if (!enabled) return null;
  return (
    <div ref={root} className="relative mb-2 flex shrink-0 items-start" data-testid="entry-analysis">
      <button type="button" data-testid="entry-analysis-button" aria-expanded={open} aria-controls="entry-analysis-panel"
        title="현재 벽·체결흐름 기준의 진입 판단 프리뷰. 주문을 보내지 않습니다."
        onClick={() => { setOpen(value => !value); if (!open) void load(); }}
        className="rounded-md border border-line px-2.5 py-1 text-xs font-bold tracking-wide text-foreground transition-colors hover:bg-line">
        진입분석
      </button>
      {open && <EntryAnalysisPanel analysis={analysis} error={error} loading={loading} />}
    </div>
  );
}
