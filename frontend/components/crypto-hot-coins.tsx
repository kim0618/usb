"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CryptoApiError } from "@/lib/crypto-paper";
import { visiblePoll } from "@/lib/visible-poll";
import {
  HOT_POLL_MS, HOT_SCAN_TIMEOUT_MS, HotRefused, fetchHotState, hotBps, hotLabel, hotPct, hotPrice,
  hotSignedPct, hotTime, hotVolume, postHotScan, readStoredSlot, shouldReplaceSlot, storeSlot,
} from "@/lib/crypto-hot-coins";
import type { HotCoin, HotSlotView, HotState } from "@/lib/crypto-hot-coins";

export type HotSlotController = ReturnType<typeof useHotSlot>;

/** The HOT slot as the server holds it, plus a guarded way to ask for a new one.
 *
 *  `loaded` is false until the server has answered once. Until then the tab may show the copy
 *  this browser kept, but the button stays off: whether a scan is allowed is the server's
 *  answer, and "no answer yet" is not "allowed".
 *
 *  The lock is re-read on a timer because the event that releases it - a HOT position closing -
 *  happens on the backend, and the button must come back on when the backend says flat, not when
 *  this page last saw a CLOSE succeed. The poll reads no market data and starts no scan.
 */
export function useHotSlot(pollMs = HOT_POLL_MS, timeoutMs = HOT_SCAN_TIMEOUT_MS,
                           source?: "PAPER" | "BINANCE_LIVE") {
  const [slot, setSlot] = useState<HotSlotView | null>(null);
  const [server, setServer] = useState<HotState | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const slotRef = useRef<HotSlotView | null>(null);
  const inFlight = useRef(false);
  const alive = useRef(true);

  const applySlot = useCallback((next: HotSlotView | null) => {
    if (!shouldReplaceSlot(slotRef.current, next)) return;
    slotRef.current = next;
    setSlot(next);
    storeSlot(next);
  }, []);

  const applyState = useCallback((body: HotState) => {
    setServer(body);
    applySlot(body.hot);
  }, [applySlot]);

  /** Ask the server now rather than at the next poll. Called when the screen sees a position
   *  change, so the button turns off as soon as a HOT position opens and back on as soon as the
   *  backend reports it flat - still the server's answer, just not five seconds late. */
  const refresh = useCallback(async () => {
    try {
      const body = await fetchHotState(undefined, source);
      if (alive.current) applyState(body);
    } catch {
      // The poll will try again.
    }
  }, [applyState, source]);

  useEffect(() => {
    alive.current = true;
    const cached = readStoredSlot();
    if (cached && slotRef.current == null) {
      slotRef.current = cached;
      setSlot(cached);
    }
    const controller = new AbortController();
    // Stops while the tab is hidden and reads at once when it comes back (`visiblePoll`). A
    // change of account on screen restarts it, which is the immediate read the switch needs.
    const stop = visiblePoll(async () => {
      try {
        const body = await fetchHotState(controller.signal, source);
        if (alive.current) applyState(body);
      } catch {
        // A failed poll keeps the last answer; the next poll tries again.
      }
    }, pollMs);
    return () => {
      alive.current = false;
      controller.abort();
      stop();
    };
  }, [applyState, pollMs, source]);

  const scan = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setScanning(true);
    setError(null);
    setNote(null);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const body = await postHotScan(controller.signal);
      if (!alive.current) return;
      applyState(body);
      setNote(body.paper_note);
    } catch (exception) {
      if (!alive.current) return;
      if (exception instanceof HotRefused && exception.state) applyState(exception.state);
      setError(exception instanceof CryptoApiError ? exception.message : "탐색에 실패했습니다.");
    } finally {
      window.clearTimeout(timer);
      inFlight.current = false;
      if (alive.current) setScanning(false);
    }
  }, [applyState, timeoutMs]);

  return {
    slot,
    symbol: slot?.symbol ?? null,
    lock: server?.lock ?? null,
    held: server?.held ?? [],
    paper: server?.paper ?? null,
    loaded: server != null,
    scanning: scanning || (server?.scanning ?? false),
    error,
    note,
    scan,
    refresh,
  };
}

/** Why the button may not be pressed right now, or null when it may. */
export function scanBlocker(hot: Pick<HotSlotController, "loaded" | "scanning" | "lock">,
                            busy = false): string | null {
  if (!hot.loaded) return "HOT 상태를 확인하는 중입니다.";
  if (hot.scanning) return "탐색 중입니다.";
  if (busy) return "주문 처리 중에는 탐색할 수 없습니다.";
  if (!hot.lock || hot.lock.state !== "ALLOWED") return hot.lock?.message ?? "HOT 상태를 알 수 없습니다.";
  return null;
}

/** [코인탐색] and its panel.
 *
 *  The panel floats over the page instead of taking a row, so opening it moves neither the tabs
 *  nor the order ticket. The TOP5 rows are text: the HOT tab carries #1, and choosing to trade it
 *  is pressing that tab, never a row here.
 */
export function HotCoinFinder({ hot, busy = false }: { hot: HotSlotController; busy?: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const blocker = scanBlocker(hot, busy);

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
    <div ref={root} className="relative mb-2 flex shrink-0 items-start gap-1" data-testid="hot-finder">
      <button type="button" data-testid="hot-scan-button" disabled={blocker !== null}
        aria-busy={hot.scanning} aria-expanded={open} aria-controls="hot-coins-panel"
        title={blocker ?? "Binance 선물 전체에서 HOT 1위를 찾아 HOT 탭에 둡니다."}
        onClick={() => { setOpen(true); void hot.scan(); }}
        className={`rounded-md border px-2.5 py-1 text-xs font-bold tracking-wide transition-colors ${
          hot.scanning ? "cursor-wait border-line text-muted"
            : blocker ? "cursor-not-allowed border-line text-muted opacity-60"
              : "border-warning text-warning hover:bg-warning-soft"}`}>
        {hot.scanning ? "탐색 중…" : "코인탐색"}
      </button>
      <button type="button" data-testid="hot-show-button" onClick={() => setOpen(value => !value)}
        aria-label="HOT 탐색 결과 보기" aria-expanded={open}
        className="rounded-md border border-line px-1.5 text-xs text-muted hover:text-foreground">
        ▾
      </button>
      {open && <HotCoinsPanel hot={hot} blocker={blocker} onClose={() => setOpen(false)} />}
    </div>
  );
}

export function HotCoinsPanel({ hot, blocker, onClose }: {
  hot: Pick<HotSlotController, "slot" | "scanning" | "error" | "note" | "lock" | "paper">;
  blocker: string | null;
  onClose: () => void;
}) {
  const slot = hot.slot;
  const status = hot.error
    ? { tone: "tone-danger", text: `${hot.error}${slot ? " · 현재 HOT을 유지합니다" : ""}` }
    : hot.scanning
      ? { tone: "tone-info", text: slot ? "탐색 중 · 아래는 현재 HOT 기준입니다" : "Binance 선물 전체를 탐색하는 중입니다" }
      : blocker && hot.lock?.state === "BLOCKED"
        ? { tone: "tone-warning", text: `${hot.lock.message} 포지션·미체결이 정리되면 다시 탐색할 수 있습니다.` }
        : { tone: "tone-neutral", text: "탐색 1위가 HOT 탭에 들어갑니다. BTC 포지션과는 무관합니다." };
  return (
    <section id="hot-coins-panel" role="dialog" aria-label="HOT 코인 탐색 결과" data-testid="hot-coins-panel"
      className="absolute right-0 top-full z-40 mt-1 w-[min(26rem,calc(100vw-2rem))] rounded-lg
        border border-line bg-surface p-3 shadow-panel">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-xs font-bold tracking-wider text-foreground">HOT COINS</h2>
          <p className="text-[11px] text-muted" data-testid="hot-scanned-at">
            {slot ? `${hotTime(slot.scanned_at_ms)} KST 탐색 · HOT ${hotLabel(slot.symbol)} (${hotTime(slot.selected_at_ms)} 선정)`
              : "아직 HOT이 없습니다"}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="닫기" data-testid="hot-close"
          className="rounded-md border border-line px-2 text-xs text-muted hover:text-foreground">✕</button>
      </header>
      <p aria-live="polite" data-testid="hot-status"
        className={`mt-2 rounded-md border px-2 py-1 text-[11px] ${status.tone}`}>{status.text}</p>
      {(hot.note || (hot.paper && !hot.paper.available)) && (
        <p className="mt-1 rounded-md border px-2 py-1 text-[11px] tone-warning" data-testid="hot-paper-note">
          {hot.note ?? hot.paper?.reason} · 차트·호가는 정상이며 PAPER 주문만 막힙니다.
        </p>
      )}
      {slot && (
        <ol className={`mt-2 space-y-1.5 ${hot.scanning ? "opacity-60" : ""}`} data-testid="hot-list">
          {slot.top.map(coin => <HotCoinRow key={coin.symbol} coin={coin} current={coin.symbol === slot.symbol} />)}
        </ol>
      )}
      <p className="mt-2 text-[10px] leading-snug text-muted">
        24h 거래대금 20M·1h 0.5M 이상, 스프레드 10bp·$1,000 왕복 20bp 이하 중 최근 1시간 변동폭 순. BTC 제외.
      </p>
    </section>
  );
}

function HotCoinRow({ coin, current }: { coin: HotCoin; current: boolean }) {
  const tone = coin.direction === "UP" ? "tone-text-success"
    : coin.direction === "DOWN" ? "tone-text-danger" : "text-muted";
  return (
    <li className={`rounded-md border px-2 py-1.5 ${current ? "border-warning" : "border-line"}`}
      data-testid={`hot-row-${coin.rank}`} data-symbol={coin.symbol}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-sm font-bold text-foreground">
          <span className="mr-1 text-muted tabular-nums">{coin.rank}.</span>
          {current && <span aria-label="HOT">🔥</span>}{hotLabel(coin.symbol)}
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
