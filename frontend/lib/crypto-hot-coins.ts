import { CRYPTO_API_BASE, CryptoApiError } from "@/lib/crypto-paper";

/** Dynamic HOT Trading V1: BTC is the fixed main instrument, the HOT slot holds one more.
 *
 *  The slot is the server's. `GET /api/crypto/hot` says which symbol it holds, since when, and
 *  whether 코인탐색 may replace it right now; `POST /api/crypto/hot/scan` is the only thing that
 *  replaces it, and the server checks the lock on the current HOT symbol before the scan and
 *  again before the swap. The screen never decides the lock: a button that re-enabled because
 *  the page *thought* a position was closed is exactly what the backend check exists to prevent.
 *
 *  What the browser keeps is a copy of the last slot it saw, so a reload paints the HOT tab
 *  before the first answer arrives. The server's answer always replaces it.
 */
export type HotDirection = "UP" | "DOWN" | "FLAT";

export type HotCoin = {
  rank: number;
  symbol: string;
  last_price: number;
  one_hour_range_pct: number;
  one_hour_change_pct: number;
  direction: HotDirection;
  twenty_four_hour_range_pct: number;
  one_hour_quote_volume: number;
  twenty_four_hour_quote_volume: number;
  spread_bps: number;
  estimated_round_trip_slippage_bps_1000: number;
  scanned_at_ms: number;
};

export type HotSlotView = {
  symbol: string;
  selected_at_ms: number;
  scanned_at_ms: number;
  metrics: HotCoin;
  top: HotCoin[];
};

export type HotLock = {
  state: "ALLOWED" | "BLOCKED";
  code: string;
  symbol: string | null;
  message: string;
};

export type HotState = {
  main: string;
  hot: HotSlotView | null;
  lock: HotLock;
  scanning: boolean;
  held: string[];
  permitted: string[];
  paper: { available: boolean; reason: string | null } | null;
  server_time_ms: number;
};

export type HotScanAnswer = HotState & {
  status: "HOT_SELECTED" | "HOT_REPLACED" | "HOT_UNCHANGED";
  changed: boolean;
  previous: string | null;
  paper_note: string | null;
  scan: { scanned_at_ms: number; universe_size: number;
          api: { requests: number; weight: number; duration_ms: number } } | null;
};

export const HOT_POLL_MS = 5_000;
export const HOT_SCAN_TIMEOUT_MS = 30_000;
const STORAGE_KEY = "usb.crypto.hotSlot.v1";
export const HOT_STORAGE_KEY = STORAGE_KEY;

type ErrorBody = { error?: { code?: string; message?: string } } & Partial<HotState>;

/** A refusal that still carries the server's current view (409 on a blocked scan does). */
export class HotRefused extends CryptoApiError {
  constructor(status: number, code: string, message: string, public state: HotState | null) {
    super(status, code, message);
    this.name = "HotRefused";
  }
}

async function call<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${CRYPTO_API_BASE}${path}`, { ...init, cache: "no-store" });
  } catch (exception) {
    if (exception instanceof DOMException && exception.name === "AbortError") {
      throw new CryptoApiError(0, "REQUEST_ABORTED", "탐색 시간이 초과되었습니다.");
    }
    throw new CryptoApiError(0, "NETWORK_ERROR", "탐색 서버에 연결할 수 없습니다.");
  }
  const body = await response.json().catch(() => null) as ErrorBody | null;
  if (!response.ok || !body) {
    const state = body && isHotState(body) ? body as HotState : null;
    throw new HotRefused(response.status, body?.error?.code || "HTTP_ERROR",
      body?.error?.message || `HTTP ${response.status}`, state);
  }
  return body as T;
}

export const fetchHotState = (signal?: AbortSignal) =>
  call<HotState>("/api/crypto/hot", { method: "GET", signal });

export const postHotScan = (signal?: AbortSignal) =>
  call<HotScanAnswer>("/api/crypto/hot/scan", { method: "POST", signal });

export function isHotState(value: unknown): value is HotState {
  const body = value as HotState | null;
  return body != null && typeof body === "object" && typeof body.main === "string"
    && body.lock != null && typeof body.lock.state === "string"
    && (body.hot === null || isSlot(body.hot));
}

function isSlot(value: unknown): value is HotSlotView {
  const slot = value as HotSlotView | null;
  return slot != null && typeof slot.symbol === "string" && typeof slot.scanned_at_ms === "number"
    && typeof slot.selected_at_ms === "number" && Array.isArray(slot.top);
}

/** An answer only replaces the held slot when it is not older. A slow poll that was sent before
 *  a scan finished must not put the previous HOT symbol back on the tab. */
export function shouldReplaceSlot(held: HotSlotView | null, next: HotSlotView | null): boolean {
  if (next == null) return held == null;
  if (held == null) return true;
  return next.scanned_at_ms >= held.scanned_at_ms;
}

export function readStoredSlot(): HotSlotView | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    return isSlot(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function storeSlot(slot: HotSlotView | null): void {
  if (typeof window === "undefined") return;
  try {
    if (slot) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(slot));
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Not remembered across a reload; the server still has it.
  }
}

export const hotPct = (value: number) => `${value.toFixed(value >= 10 ? 1 : 2)}%`;
export const hotSignedPct = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
export const hotBps = (value: number) => `${value.toFixed(1)}bp`;
export const hotVolume = (value: number) => {
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`;
  return value.toFixed(0);
};
/** Up to six significant digits: HOT coins run from 0.0001 to five figures. */
export const hotPrice = (value: number) =>
  value >= 1000 ? value.toFixed(2) : Number(value.toPrecision(6)).toString();
export const hotTime = (ms: number) => new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", hour12: false, month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(ms));
/** The label a HOT symbol is shown under. Binance names some in Chinese; those stay as named. */
export const hotLabel = (symbol: string) => symbol.endsWith("USDT") ? symbol.slice(0, -4) : symbol;
