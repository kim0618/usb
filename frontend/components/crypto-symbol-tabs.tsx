"use client";

import { useCallback, useEffect, useState } from "react";
import { qty as qtyFmt, signedKrw, toneClass } from "@/lib/crypto-paper";
import { liveApi } from "@/lib/crypto-live";
import type { LivePositionsSummary } from "@/lib/crypto-live";
import {
  CRYPTO_SYMBOLS, DEFAULT_SYMBOL, readStoredSymbol, storeSymbol, symbolLabel,
} from "@/lib/crypto-symbols";

/** The selected instrument, remembered.
 *
 *  The selection has to survive three things the operator will do: reload the page, switch
 *  between PAPER and LIVE, and come back tomorrow. PAPER/LIVE is free because both render from
 *  this one piece of page state; the other two need storage, which is why the initial value is
 *  read from `localStorage` rather than defaulted to BTC and corrected afterwards.
 *
 *  `permitted` is the server's list once `/status` has answered. Until then it is the client's
 *  own, which is only used to render the tabs - a symbol the server refuses can never be
 *  selected, because the effect below moves the selection off it.
 */
export function useSelectedSymbol(permitted?: readonly string[] | null) {
  // Read inside the initialiser so the first render is already on the remembered symbol. A
  // `useEffect` correction would paint BTC for one frame on every load, and on a slow first
  // paint that frame is long enough to start reading.
  const [symbol, setSymbol] = useState<string>(() => readStoredSymbol());

  const select = useCallback((next: string) => {
    setSymbol(next);
    storeSymbol(next);
  }, []);

  useEffect(() => {
    if (!permitted || permitted.length === 0) return;
    if (permitted.includes(symbol)) return;
    // The server narrowed its list (or this browser remembered a symbol from a build that
    // supported more). Move to its default rather than leaving a tab selected that every
    // request will refuse.
    const fallback = permitted.includes(DEFAULT_SYMBOL) ? DEFAULT_SYMBOL : permitted[0];
    setSymbol(fallback);
    storeSymbol(fallback);
  }, [permitted, symbol]);

  return { symbol, select };
}

/** The tab strip. One row, one selected, nothing clever.
 *
 *  Rendered from `permitted` when the server has said what it permits, so a build that narrows
 *  its symbol list does not show a tab whose every request would be refused.
 */
export function SymbolTabs({ value, onChange, permitted, busy }: {
  value: string;
  onChange: (symbol: string) => void;
  permitted?: readonly string[] | null;
  busy?: boolean;
}) {
  const symbols = permitted && permitted.length > 0 ? permitted : CRYPTO_SYMBOLS;
  return (
    <div className="mb-2 flex items-center gap-1 overflow-x-auto" role="tablist"
      aria-label="거래 심볼" data-testid="symbol-tabs">
      {symbols.map(symbol => {
        const selected = symbol === value;
        return (
          <button key={symbol} type="button" role="tab" aria-selected={selected}
            // Disabled while an order or a leverage write is in flight. Changing the symbol
            // mid-write would leave the response landing on a screen that is no longer about
            // the instrument it was sent for.
            disabled={busy && !selected}
            data-testid={`symbol-tab-${symbol}`}
            onClick={() => { if (!selected) onChange(symbol); }}
            className={`shrink-0 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
              selected
                ? "bg-accent text-accent-foreground"
                : "bg-surface-muted text-foreground-secondary hover:text-foreground disabled:opacity-40"
            }`}>
            {symbolLabel(symbol)}
          </button>
        );
      })}
    </div>
  );
}

/** Every open position the account holds, above the tabs.
 *
 *  Always on screen, including the flat symbols, because "SOL FLAT" is information an operator
 *  acts on and an absent row is not. One server read covers all three, so the strip cannot
 *  disagree with the detail panel about a position that exists.
 *
 *  Clicking a row selects that symbol: on a phone the strip is the fastest way to get from
 *  "something is open on ETH" to the panel that can close it.
 */
export function OpenPositionsStrip({ summary, selected, onSelect, error }: {
  summary: LivePositionsSummary | null;
  selected: string;
  onSelect: (symbol: string) => void;
  error?: string | null;
}) {
  const rows = summary?.positions ?? [];
  return (
    <section className="mb-2 rounded-lg border border-border bg-surface px-3 py-2"
      data-testid="open-positions-strip" aria-label="보유 포지션 요약">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[11px] font-semibold tracking-wide text-muted">OPEN POSITIONS</h2>
        {summary == null && !error && (
          <span className="text-[11px] text-muted" data-testid="open-positions-loading">읽는 중</span>
        )}
      </div>
      {error && (
        <p className="mt-1 text-[11px] text-warning" role="status"
          data-testid="open-positions-error">{error}</p>
      )}
      <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
        {rows.map(row => {
          const flat = row.is_flat || row.side == null;
          const pnl = row.krw?.unrealized_pnl ?? null;
          return (
            <li key={row.symbol}>
              <button type="button" onClick={() => onSelect(row.symbol)}
                aria-current={row.symbol === selected}
                data-testid={`open-position-${row.symbol}`}
                className={`flex items-baseline gap-1.5 rounded px-1 text-xs ${
                  row.symbol === selected ? "font-semibold text-foreground" : "text-foreground-secondary"
                }`}>
                <span>{symbolLabel(row.symbol)}</span>
                {!row.available ? (
                  <span className="text-warning" title={row.reject_message}>읽기 실패</span>
                ) : flat ? (
                  <span className="text-muted">FLAT</span>
                ) : (
                  <>
                    <span className={row.side === "LONG" ? "text-success" : "text-danger"}>
                      {row.side}
                    </span>
                    <span className="tabular-nums">{qtyFmt(row.qty)}</span>
                    {pnl != null && (
                      <span className={`tabular-nums ${toneClass(pnl)}`}>
                        {signedKrw(pnl)}
                      </span>
                    )}
                  </>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {/* Positions on symbols this terminal does not trade. They hold margin that the Safe MAX
          on every tab already reflects, so a strip titled OPEN POSITIONS that hid them would be
          wrong by omission. Shown without controls: there is nothing here that can close them. */}
      {summary?.others?.length ? (
        <p className="mt-1 text-[11px] text-muted" data-testid="open-positions-others">
          {summary.others_note}{" "}
          {summary.others.map(row => (
            <span key={row.symbol} className="mr-2 tabular-nums">
              {row.symbol} {row.side} {qtyFmt(row.qty)}
            </span>
          ))}
        </p>
      ) : null}
    </section>
  );
}

/** Polls the all-symbol summary while LIVE is on screen.
 *
 *  Its own slow timer: the strip is a glance, not a tick, and one `positionRisk` read every few
 *  seconds is enough. It is a read and touches nothing on the order path.
 */
export const POSITIONS_POLL_MS = 5_000;

export function useOpenPositions(enabled: boolean, pollMs = POSITIONS_POLL_MS) {
  const [summary, setSummary] = useState<LivePositionsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setSummary(null);
      setError(null);
      return;
    }
    let cancelled = false;
    const read = async () => {
      try {
        const next = await liveApi.positions();
        if (!cancelled) { setSummary(next); setError(null); }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      }
    };
    void read();
    const timer = setInterval(() => { void read(); }, pollMs);
    return () => { cancelled = true; clearInterval(timer); };
  }, [enabled, pollMs]);

  return { summary, error };
}
