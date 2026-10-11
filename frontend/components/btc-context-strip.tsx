"use client";

import { type ReactNode, useEffect, useState } from "react";
import {
  BTC_CONTEXT_BASE, BTC_CONTEXT_HOLD_MS, BTC_CONTEXT_POLL_MS, BTC_CONTEXT_TIMEOUT_MS,
  type BtcContextPayload, contextLines, fetchBtcContext,
} from "@/lib/btc-context";

/** Poll the strip's API. Only GETs, one in flight at a time, and a failure is never thrown: it
 *  shows up as the reading ageing out to dashes after `BTC_CONTEXT_HOLD_MS`. */
export function useBtcContext(base: string = BTC_CONTEXT_BASE): BtcContextPayload | null {
  const [last, setLast] = useState<{ payload: BtcContextPayload; at: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let stopped = false;
    let inFlight: AbortController | null = null;
    const poll = async () => {
      setNow(Date.now());
      if (inFlight || (typeof document !== "undefined" && document.hidden)) return;
      const controller = new AbortController();
      inFlight = controller;
      const timer = setTimeout(() => controller.abort(), BTC_CONTEXT_TIMEOUT_MS);
      try {
        const payload = await fetchBtcContext(base, controller.signal);
        if (!stopped) setLast({ payload, at: Date.now() });
      } catch {
        // Dashes, not an error banner: this strip must never cover the order screen.
      } finally {
        clearTimeout(timer);
        inFlight = null;
      }
    };
    void poll();
    const id = setInterval(() => { void poll(); }, BTC_CONTEXT_POLL_MS);
    return () => { stopped = true; clearInterval(id); inFlight?.abort(); };
  }, [base]);

  if (!last || now - last.at > BTC_CONTEXT_HOLD_MS) return null;
  return last.payload;
}

function Dot({ live }: { live: boolean }) {
  return (
    <span aria-hidden="true" data-live={live ? "1" : "0"}
      className={`text-[8px] leading-none ${live ? "text-success" : "text-muted"}`}>●</span>
  );
}

function Cell({ label, live, testId, children }: {
  label: string; live: boolean; testId: string; children: ReactNode;
}) {
  // Phone: one row per section, label left. Wide: four stacked cells side by side, each as wide as
  // its text, so a figure is never cut to an ellipsis; a column too narrow for four wraps instead.
  return (
    <div className="flex min-w-0 items-baseline gap-2 xl:block xl:shrink-0" data-testid={testId}
      data-live={live ? "1" : "0"}>
      <div className="flex w-[84px] shrink-0 items-center gap-1 text-[10px] font-medium tracking-wide text-muted xl:w-auto">
        <Dot live={live} />{label}
      </div>
      <div className="min-w-0 flex-1 space-y-0.5 text-[12px] font-semibold leading-tight tabular-nums text-foreground xl:mt-0.5">
        {children}
      </div>
    </div>
  );
}

/** Pure render, so tests and the polling wrapper share one markup. */
export function BtcContextStripView({ payload }: { payload: BtcContextPayload | null }) {
  const lines = contextLines(payload);
  return (
    <section data-testid="btc-context-strip" aria-label="BTC market context"
      className="grid grid-cols-1 gap-y-1 rounded-lg border border-line bg-surface px-3 py-2 xl:flex xl:flex-wrap xl:justify-between xl:gap-x-6 xl:gap-y-2">
      <Cell label="STRUCTURE" live={lines.live.structure} testId="btc-context-structure">
        <div className="truncate">{lines.structure}</div>
      </Cell>
      <Cell label="LIQUIDITY" live={lines.live.liquidity} testId="btc-context-liquidity">
        <div className="truncate">{lines.liquidity}</div>
      </Cell>
      <Cell label="FLOW" live={lines.live.flow} testId="btc-context-flow">
        <div className="truncate">{lines.flow}</div>
      </Cell>
      <Cell label="DERIVATIVES" live={lines.live.derivatives} testId="btc-context-derivatives">
        <div className="truncate">{lines.oi}</div>
        <div className="truncate text-[11px] font-medium">{lines.rates}</div>
      </Cell>
    </section>
  );
}

/** BTC only. Anything else renders nothing, so a HOT or held-symbol tab never shows BTC figures. */
export function BtcContextStrip({ symbol, base }: { symbol: string; base?: string }) {
  if (symbol !== "BTCUSDT") return null;
  return <BtcContextStripPolling base={base} />;
}

function BtcContextStripPolling({ base }: { base?: string }) {
  const payload = useBtcContext(base);
  return <BtcContextStripView payload={payload} />;
}
