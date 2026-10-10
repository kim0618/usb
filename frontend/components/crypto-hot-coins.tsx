"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CryptoApiError } from "@/lib/crypto-paper";
import {
  HOT_SCAN_TIMEOUT_MS, fetchHotCoins, hotBps, hotLabel, hotPct, hotPrice, hotSignedPct, hotTime,
  hotVolume, readStoredHot, shouldReplace, storeHot, toSnapshot,
} from "@/lib/crypto-hot-coins";
import type { HotCoin, HotSnapshot } from "@/lib/crypto-hot-coins";

/** The last HOT result, a scan on demand, and nothing on a timer.
 *
 *  `scan()` is the only thing that reaches the network, and only from a click. A second click
 *  while one scan is running is ignored here (and coalesced again by the server). A failed scan
 *  - timeout, network, Binance down - leaves the held result exactly as it was and says so. An
 *  answer older than what is held is dropped.
 *
 *  The held result is restored from storage after mount rather than in the initialiser: the page
 *  is prerendered without storage, and React does not repaint attributes it believes it already
 *  rendered, which is how the symbol tabs ended up showing BTC pressed while acting on SOL.
 */
export function useHotCoins(timeoutMs = HOT_SCAN_TIMEOUT_MS) {
  const [held, setHeld] = useState<HotSnapshot | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const heldRef = useRef<HotSnapshot | null>(null);
  const inFlight = useRef(false);
  const generation = useRef(0);

  useEffect(() => {
    const stored = readStoredHot();
    if (stored && shouldReplace(heldRef.current, stored)) {
      heldRef.current = stored;
      setHeld(stored);
    }
    return () => { generation.current += 1; };
  }, []);

  const scan = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    const mine = ++generation.current;
    setScanning(true);
    setError(null);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const body = await fetchHotCoins(controller.signal);
      if (mine !== generation.current) return;
      const next = toSnapshot(body);
      if (shouldReplace(heldRef.current, next)) {
        heldRef.current = next;
        setHeld(next);
        storeHot(next);
      }
    } catch (exception) {
      if (mine !== generation.current) return;
      setError(exception instanceof CryptoApiError ? exception.message : "탐색에 실패했습니다.");
    } finally {
      window.clearTimeout(timer);
      if (mine === generation.current) {
        inFlight.current = false;
        setScanning(false);
      }
    }
  }, [timeoutMs]);

  return { held, scanning, error, scan };
}

/** [코인탐색] and its result panel.
 *
 *  The panel floats over the page instead of taking a row, so opening it moves neither the
 *  symbol tabs nor the order ticket. The rows are text, not buttons: in V0 a HOT coin is
 *  something to look at, and there is deliberately no click path from a row to the selected
 *  trading symbol, the chart or an order.
 */
export function HotCoinFinder() {
  const { held, scanning, error, scan } = useHotCoins();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

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

  return (
    <div ref={root} className="relative mb-2 flex shrink-0 items-start gap-1"
      data-testid="hot-finder">
      <button type="button" data-testid="hot-scan-button" disabled={scanning}
        aria-busy={scanning} aria-expanded={open} aria-controls="hot-coins-panel"
        onClick={() => { setOpen(true); void scan(); }}
        className={`rounded-md border px-2.5 py-1 text-xs font-bold tracking-wide transition-colors
          ${scanning ? "cursor-wait border-line text-muted" : "border-warning text-warning hover:bg-warning-soft"}`}>
        {scanning ? "탐색 중…" : "코인탐색"}
      </button>
      {held && !open && (
        <button type="button" data-testid="hot-show-button" onClick={() => setOpen(true)}
          aria-label="마지막 탐색 결과 보기" title={`마지막 탐색 ${hotTime(held.scannedAtMs)} KST`}
          className="rounded-md border border-line px-1.5 text-xs text-muted hover:text-foreground">
          ▾
        </button>
      )}
      {open && (
        <HotCoinsPanel held={held} scanning={scanning} error={error}
          onClose={() => setOpen(false)} />
      )}
    </div>
  );
}

export function HotCoinsPanel({ held, scanning, error, onClose }: {
  held: HotSnapshot | null;
  scanning: boolean;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <section id="hot-coins-panel" role="dialog" aria-label="HOT 코인 탐색 결과"
      data-testid="hot-coins-panel"
      className="absolute right-0 top-full z-40 mt-1 w-[min(26rem,calc(100vw-2rem))] rounded-lg
        border border-line bg-surface p-3 shadow-panel">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-xs font-bold tracking-wider text-foreground">HOT COINS</h2>
          <p className="text-[11px] text-muted" data-testid="hot-scanned-at">
            {held ? `${hotTime(held.scannedAtMs)} KST 탐색` : "아직 탐색 결과가 없습니다"}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="닫기" data-testid="hot-close"
          className="rounded-md border border-line px-2 text-xs text-muted hover:text-foreground">✕</button>
      </header>
      <p aria-live="polite" data-testid="hot-status"
        className={`mt-2 rounded-md border px-2 py-1 text-[11px] ${
          error ? "tone-danger" : scanning ? "tone-info" : "tone-neutral"}`}>
        {error
          ? `${error}${held ? " · 이전 결과를 유지합니다" : ""}`
          : scanning
            ? held ? "탐색 중 · 아래는 이전 결과입니다" : "Binance 선물 전체를 탐색하는 중입니다"
            : "조회 전용 · 거래 심볼과 주문은 바뀌지 않습니다"}
      </p>
      {held && (
        <ol className={`mt-2 space-y-1.5 ${scanning ? "opacity-60" : ""}`} data-testid="hot-list">
          {held.top.map(coin => <HotCoinRow key={coin.symbol} coin={coin} />)}
        </ol>
      )}
      <p className="mt-2 text-[10px] leading-snug text-muted">
        24h 거래대금 20M·1h 0.5M 이상, 스프레드 10bp·$1,000 왕복 20bp 이하 중 최근 1시간 변동폭 순. BTC 제외.
      </p>
    </section>
  );
}

function HotCoinRow({ coin }: { coin: HotCoin }) {
  const tone = coin.direction === "UP" ? "tone-text-success"
    : coin.direction === "DOWN" ? "tone-text-danger" : "text-muted";
  return (
    <li className="rounded-md border border-line px-2 py-1.5" data-testid={`hot-row-${coin.rank}`}
      data-symbol={coin.symbol}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-sm font-bold text-foreground">
          <span className="mr-1 text-muted tabular-nums">{coin.rank}.</span>
          {hotLabel(coin.symbol)}
          <span className="ml-1 text-[10px] font-normal text-muted">{hotPrice(coin.last_price)}</span>
        </span>
        {/* The range is a size with no sign, so it stays neutral; only the change is coloured. */}
        <span className="shrink-0 text-xs font-bold tabular-nums text-foreground">
          1H {hotPct(coin.one_hour_range_pct)}
          <span className={`ml-1 font-semibold ${tone}`}>
            <span aria-hidden="true">{coin.direction === "UP" ? "▲" : coin.direction === "DOWN" ? "▼" : "·"}</span>
            {hotSignedPct(coin.one_hour_change_pct)}
          </span>
        </span>
      </div>
      <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] tabular-nums sm:grid-cols-3">
        <Metric label="24H 범위" value={hotPct(coin.twenty_four_hour_range_pct)} />
        <Metric label="1H 거래대금" value={hotVolume(coin.one_hour_quote_volume)} />
        <Metric label="24H 거래대금" value={hotVolume(coin.twenty_four_hour_quote_volume)} />
        <Metric label="스프레드" value={hotBps(coin.spread_bps)} />
        <Metric label="$1,000 왕복" value={hotBps(coin.estimated_round_trip_slippage_bps_1000)} />
      </dl>
    </li>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 justify-between gap-1">
      <dt className="truncate text-muted">{label}</dt>
      <dd className="font-semibold text-foreground-secondary">{value}</dd>
    </div>
  );
}
