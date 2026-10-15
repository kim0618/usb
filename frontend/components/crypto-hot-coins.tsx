"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CryptoApiError } from "@/lib/crypto-paper";
import { visiblePoll } from "@/lib/visible-poll";
import {
  HOT_POLL_MS, HOT_SCAN_TIMEOUT_MS, HotRefused, fetchHotState, postHotScan, readStoredSlot,
  shouldReplaceSlot, storeSlot,
} from "@/lib/crypto-hot-coins";
import type { HotSlotView, HotState } from "@/lib/crypto-hot-coins";

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

  /** Scan and return the symbol the server put in the slot (its #1), or null when the scan was
   *  refused or failed. The caller makes that symbol the global selection. */
  const scan = useCallback(async (): Promise<string | null> => {
    if (inFlight.current) return null;
    inFlight.current = true;
    setScanning(true);
    setError(null);
    setNote(null);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const body = await postHotScan(controller.signal);
      if (!alive.current) return null;
      applyState(body);
      setNote(body.paper_note);
      return body.hot?.symbol ?? null;
    } catch (exception) {
      if (!alive.current) return null;
      if (exception instanceof HotRefused && exception.state) applyState(exception.state);
      setError(exception instanceof CryptoApiError ? exception.message : "탐색에 실패했습니다.");
      return null;
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

/** [HOT]: one press, one result.
 *
 *  The scan runs on the server, which ranks the market and keeps its #1 in the HOT slot. That
 *  slot is one for the whole terminal, so `onSelect` makes the same symbol the selection on the
 *  PAPER and the LIVE screen alike. No ranking is shown: rank 2 and below are the server's
 *  business. Selecting a symbol changes what is on screen and where the next order goes; it
 *  closes, converts or moves no position on any symbol.
 */
export function HotCoinFinder({ hot, busy = false, onSelect }: {
  hot: HotSlotController;
  busy?: boolean;
  /** Called with the scan's #1 once the server has put it in the slot. */
  onSelect?: (symbol: string) => void;
}) {
  const blocker = scanBlocker(hot, busy);
  const message = hot.error ?? (hot.note || (hot.paper && !hot.paper.available ? hot.paper.reason : null));
  return (
    <div className="relative mb-2 flex shrink-0 flex-col items-end gap-0.5" data-testid="hot-finder">
      <button type="button" data-testid="hot-scan-button" disabled={blocker !== null}
        aria-busy={hot.scanning}
        title={blocker ?? "Binance 선물 전체에서 HOT 1위를 찾아 지금 심볼로 바꿉니다."}
        onClick={() => { void hot.scan().then(symbol => { if (symbol) onSelect?.(symbol); }); }}
        className={`rounded-md border px-2.5 py-1 text-xs font-bold tracking-wide transition-colors ${
          hot.scanning ? "cursor-wait border-line text-muted"
            : blocker ? "cursor-not-allowed border-line text-muted opacity-60"
              : "border-warning text-warning hover:bg-warning-soft"}`}>
        {hot.scanning ? "탐색 중…" : "HOT"}
      </button>
      {message && (
        <p className={`max-w-[14rem] text-right text-[10px] leading-snug ${hot.error ? "text-danger" : "text-warning"}`}
          data-testid="hot-message" role="status">{message}</p>
      )}
    </div>
  );
}
