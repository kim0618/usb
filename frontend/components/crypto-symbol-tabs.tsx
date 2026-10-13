"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { cryptoApi, qty as qtyFmt, signedKrw, toneClass } from "@/lib/crypto-paper";
import { liveApi } from "@/lib/crypto-live";
import { visiblePoll } from "@/lib/visible-poll";
import type { LivePositionsSummary } from "@/lib/crypto-live";
import {
  CRYPTO_SYMBOLS, DEFAULT_SYMBOL, readStoredSymbol, storeSymbol, symbolLabel,
} from "@/lib/crypto-symbols";
import {
  DIRECTION_ARROW, DIRECTION_WORD, VOLATILITY_POLL_MS, VOLATILITY_REQUEST_BARS, hotSymbols,
  rangeLabel, symbolRange1h, withFreshness,
} from "@/lib/crypto-volatility";
import type { SymbolVolatility, VolatilityUnknownReason } from "@/lib/crypto-volatility";

/** The selected instrument, remembered.
 *
 *  The selection has to survive three things the operator will do: reload the page, switch
 *  between PAPER and LIVE, and come back tomorrow. PAPER/LIVE is free because both render from
 *  this one piece of page state; the other two need storage.
 *
 *  The remembered value is read through `useSyncExternalStore` with BTC as the server snapshot.
 *  The page is prerendered without storage, and reading storage in a `useState` initialiser made
 *  the first client render disagree with the prerendered HTML: React kept the prerendered
 *  `aria-selected="true"` on BTC while the screen acted on the remembered symbol, so two tabs
 *  read as pressed. With a server snapshot React hydrates on BTC and then re-renders on the
 *  remembered symbol, patching every attribute. Nothing subscribes to storage events: another
 *  browser tab changing its selection must never move this tab's trading symbol.
 *
 *  `permitted` is what may be selected once it is known; `null` means not yet, and the
 *  remembered symbol is then held provisionally. The effect below moves the selection to BTC as
 *  soon as the list says the remembered symbol is not on it.
 */
const noSubscription = () => () => {};

export function useSelectedSymbol(permitted?: readonly string[] | null) {
  const remembered = useSyncExternalStore(noSubscription, () => readStoredSymbol(null),
                                          () => DEFAULT_SYMBOL);
  const [chosen, setChosen] = useState<string | null>(null);
  const symbol = chosen ?? remembered;

  const select = useCallback((next: string) => {
    setChosen(next);
    storeSymbol(next);
  }, []);

  useEffect(() => {
    if (!permitted || permitted.length === 0) return;
    if (permitted.includes(symbol)) return;
    // The server narrowed its list (or this browser remembered a symbol from a build that
    // supported more, or a HOT symbol that has since been replaced). Move to its default
    // rather than leaving a tab selected that every request will refuse.
    const fallback = permitted.includes(DEFAULT_SYMBOL) ? DEFAULT_SYMBOL : permitted[0];
    setChosen(fallback);
    storeSymbol(fallback);
  }, [permitted, symbol]);

  return { symbol, select };
}

/** Polls every rendered symbol's last-hour range.
 *
 *  Three reads of the existing `chart-history` route on one timer, which is the lightest
 *  read-only path to a figure about an instrument the operator is *not* looking at: the chart
 *  only ever holds the selected symbol's candles, and on any timeframe but 1m it holds no
 *  minute bars at all, so deriving the badge from what is already drawn would make the selected
 *  tab's figure come from somewhere different than the other two. One source for all three
 *  instead. No collector, no new route, no account read.
 *
 *  The route already caches each (symbol, timeframe, limit) response for 15 s in the backend, so
 *  this timer does not translate into provider traffic at its own rate - at most one upstream
 *  kline page per symbol per 15 s, of 64 bars.
 *
 *  A failed read keeps the previous reading rather than blanking the tab: a single 502 from the
 *  proxy is not news about volatility. The reading cannot go stale silently, because every tick
 *  re-applies `withFreshness` to whatever it is holding, so a backend that stays down turns the
 *  badges into `--` within `VOLATILITY_STALE_MS` and leaves them there.
 */
export function useSymbolVolatility(symbols?: readonly string[] | null, enabled = true,
                                    pollMs = VOLATILITY_POLL_MS) {
  const [rows, setRows] = useState<Record<string, SymbolVolatility>>({});
  // The effect must not restart on every render just because the parent built a new array.
  const roster = ((symbols && symbols.length > 0 ? symbols : CRYPTO_SYMBOLS)).join(",");

  useEffect(() => {
    if (!enabled) { setRows({}); return; }
    const list = roster.split(",");
    const controller = new AbortController();
    let stopped = false;

    const readOne = async (symbol: string): Promise<SymbolVolatility | null> => {
      try {
        const body = await cryptoApi.chartHistory(symbol, "1m", VOLATILITY_REQUEST_BARS, null,
                                                  controller.signal);
        // A response that names another instrument is dropped rather than attributed to this
        // tab. The route echoes the symbol it resolved, and a figure under the wrong label is
        // the one failure on this screen that looks entirely correct.
        if (body.symbol && body.symbol !== symbol) return null;
        return symbolRange1h(symbol, body.bars);
      } catch {
        return null;
      }
    };

    const read = async () => {
      const readings = await Promise.all(list.map(async symbol =>
        [symbol, await readOne(symbol)] as const));
      if (stopped) return;
      const nowMs = Date.now();
      setRows(previous => {
        // Built from `list`, not from `previous`, so a narrowed roster drops the symbols it no
        // longer contains instead of leaving their last figure on a tab that is gone.
        const next: Record<string, SymbolVolatility> = {};
        for (const [symbol, reading] of readings) {
          const held = reading ?? previous[symbol];
          if (held) next[symbol] = withFreshness(held, nowMs);
        }
        return next;
      });
    };
    // Stops while the tab is hidden; the badges are re-read at once when it comes back.
    const stop = visiblePoll(read, pollMs);

    return () => {
      stopped = true;
      controller.abort();
      stop();
    };
  }, [enabled, roster, pollMs]);

  return { rows };
}

/** The tab strip. One row, one selected, nothing clever.
 *
 *  Rendered from `permitted` when the server has said what it permits, so a build that narrows
 *  its symbol list does not show a tab whose every request would be refused.
 *
 *  `volatility` adds the last hour's range under each symbol and HOT to the widest of them. It
 *  is a label on market data and nothing else: it never changes `value`, never calls `onChange`,
 *  and the strip renders exactly as it did before when the prop is absent. HOT moving to another
 *  tab leaves the selection, the chart and the order ticket where they were - switching is the
 *  operator pressing a tab, which is the only thing that has ever switched them.
 */
export function SymbolTabs({ value, onChange, permitted, busy, volatility, hotSlot }: {
  value: string;
  onChange: (symbol: string) => void;
  permitted?: readonly string[] | null;
  busy?: boolean;
  /** Per-symbol readings, keyed by symbol. Present but empty while the first reads are in
   *  flight, which renders `--` rather than an absent line - the row has to hold its height
   *  from the first paint or the whole screen moves 13 px when the figures land. */
  volatility?: Record<string, SymbolVolatility> | null;
  /** Dynamic HOT V1: the HOT slot after the fixed tabs. `symbol: null` renders "HOT 없음",
   *  disabled. Absent means the strip renders exactly as it did before. */
  hotSlot?: { symbol: string | null } | null;
}) {
  const symbols = permitted && permitted.length > 0 ? permitted : CRYPTO_SYMBOLS;
  const hotSymbol = hotSlot?.symbol ?? null;
  const onScreen = hotSymbol && !symbols.includes(hotSymbol) ? [...symbols, hotSymbol] : symbols;
  // Ranked over the symbols actually on screen, so a reading for an instrument this build does
  // not render can never hold the badge that no visible tab would then carry.
  const hot = hotSymbols(onScreen.map(symbol => volatility?.[symbol])
    .filter((row): row is SymbolVolatility => row != null));

  const tab = (symbol: string, slot: boolean) => {
    const selected = symbol === value;
    const reading = volatility?.[symbol] ?? null;
    const isHot = hot.has(symbol);
    return (
      <button key={slot ? "hot-slot" : symbol} type="button" role="tab" aria-selected={selected}
        // Disabled while an order or a leverage write is in flight. Changing the symbol
        // mid-write would leave the response landing on a screen that is no longer about
        // the instrument it was sent for.
        disabled={busy && !selected}
        data-testid={slot ? "symbol-tab-hot" : `symbol-tab-${symbol}`}
        data-symbol={symbol}
        title={volatility ? tabTitle(symbol, reading, isHot) : (slot ? `HOT ${symbol}` : undefined)}
        onClick={() => { if (!selected) onChange(symbol); }}
        // Same shape as the PAPER / BINANCE LIVE switch directly above, deliberately: the
        // two rows sit together and a tab that styled its selection differently would read
        // as a different kind of control.
        //
        // `bg-accent` / `text-accent-foreground` / `bg-surface-muted` were used here first
        // and none of them is a token in this design system, so the selected tab rendered
        // with no border, no fill and no change beyond inheriting the text colour - there
        // was no pressed state on screen at all. Every class below is in tailwind.config.
        className={`shrink-0 rounded-md border text-xs font-bold tracking-wide
          transition-colors ${volatility ? "px-2.5 py-1" : "px-3 py-1.5"} ${
          selected
            ? "border-primary bg-surface-alt text-foreground"
            : "border-line text-muted hover:text-foreground"
        } ${busy && !selected ? "cursor-not-allowed opacity-40" : ""} ${slot ? "max-w-[9.5rem]" : ""}`}>
        <span className="flex items-center justify-center gap-1">
          {slot && <span aria-hidden="true">🔥</span>}
          <span className={slot ? "min-w-0 truncate" : undefined}>{symbolLabel(symbol)}</span>
          {volatility && (
            // Always rendered, hidden when the symbol is not the widest, so the badge
            // appearing on another tab does not change any tab's width. The word is the
            // badge: colour alone would say nothing to a reader who cannot see it, and
            // nothing on this strip is distinguished by colour anywhere else.
            <span data-testid={`symbol-hot-${symbol}`} data-hot={isHot}
              aria-hidden={!isHot || undefined}
              className={`rounded-sm border px-1 text-[9px] leading-[1.45] tracking-wider
                tone-warning ${isHot ? "" : "invisible"}`}>HOT</span>
          )}
        </span>
        {volatility && (
          <span className="mt-0.5 flex items-baseline justify-center gap-0.5 text-[10px]
            font-semibold tabular-nums" data-testid={`symbol-range-${symbol}`}>
            {/* The "1H" label is the first thing to go on a phone: the tabs have to stay
                on one line at 390 px, and the window is stated in the tooltip and in the
                screen-reader text either way. */}
            <span className="hidden text-muted sm:inline">1H</span>
            <span>{rangeLabel(reading)}</span>
            {reading?.state === "COMPLETE" && (
              <>
                <span aria-hidden="true">{DIRECTION_ARROW[reading.direction]}</span>
                <span className="sr-only">{DIRECTION_WORD[reading.direction]}</span>
              </>
            )}
          </span>
        )}
      </button>
    );
  };

  return (
    <div className="mb-2 flex items-center gap-1 overflow-x-auto" role="tablist"
      aria-label="거래 심볼" data-testid="symbol-tabs">
      {symbols.map(symbol => tab(symbol, false))}
      {hotSlot !== undefined && (hotSymbol && !symbols.includes(hotSymbol) ? tab(hotSymbol, true) : !hotSymbol && (
        <button type="button" role="tab" aria-selected={false} disabled data-testid="symbol-tab-hot"
          title="코인탐색을 누르면 HOT 1위가 여기에 들어갑니다."
          className={`shrink-0 cursor-not-allowed rounded-md border border-dashed border-line text-xs
            font-bold tracking-wide text-muted opacity-70 ${volatility ? "px-2.5 py-1" : "px-3 py-1.5"}`}>
          <span className="flex items-center justify-center gap-1"><span aria-hidden="true">🔥</span>HOT 없음</span>
          {volatility && <span className="mt-0.5 block text-[10px]">--</span>}
        </button>
      ))}
    </div>
  );
}

/** The tooltip, which is where the window and the reason for a missing figure live.
 *
 *  `--` on a tab is honest but not informative, and the reason it is `--` is the thing a person
 *  debugging the screen needs. It is a `title` rather than on-screen text because the strip is a
 *  glance: three tabs explaining their data coverage would bury the prices under them. */
export function tabTitle(symbol: string, row: SymbolVolatility | null, isHot: boolean): string {
  if (row == null) return `${symbolLabel(symbol)} · 1시간 변동폭을 읽는 중입니다.`;
  if (row.state === "UNKNOWN") return `${symbolLabel(symbol)} · 1시간 변동폭 없음 (${
    UNKNOWN_NOTES[row.reason]})`;
  const window = `${kstMinute(row.windowStartMs)}~${kstMinute(row.windowEndMs + 60_000)}`;
  return `${symbolLabel(symbol)} · 최근 60분 변동폭 ${rangeLabel(row)} ${
    DIRECTION_WORD[row.direction]} · ${window} KST${isHot ? " · 표시 종목 중 최대" : ""}`;
}

const UNKNOWN_NOTES: Record<VolatilityUnknownReason, string> = {
  NO_CONFIRMED_BAR: "완결된 1분봉이 없습니다",
  INCOMPLETE_WINDOW: "최근 60분 중 빠진 1분봉이 있습니다",
  NO_REFERENCE_PRICE: "기준가를 읽을 수 없습니다",
  STALE: "최근 데이터가 아닙니다",
};

const kstMinute = (ms: number) => new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", hour12: false, hour: "2-digit", minute: "2-digit" }).format(new Date(ms));

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
    // A signed read: stops while the tab is hidden and reads at once when it comes back.
    const stop = visiblePoll(async () => {
      try {
        const next = await liveApi.positions();
        if (!cancelled) { setSummary(next); setError(null); }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      }
    }, pollMs);
    return () => { cancelled = true; stop(); };
  }, [enabled, pollMs]);

  return { summary, error };
}
