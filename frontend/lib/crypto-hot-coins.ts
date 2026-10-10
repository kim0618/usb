import { CRYPTO_API_BASE, CryptoApiError } from "@/lib/crypto-paper";

/** HOT Coin Finder V0: read-only.
 *
 *  One GET, `/api/crypto/hot-coins`, and nothing else. The browser never calls Binance itself:
 *  the backend owns the request budget, the single-flight guard and the encoding of symbols such
 *  as 龙虾USDT. A HOT result is a list to look at. It is held in its own storage key and its own
 *  state, never in the selected trading symbol, so no result can move the chart, the order
 *  ticket or the tabs.
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

export type HotScanResponse = {
  status: "OK";
  scanned_at_ms: number;
  scanned_at: string;
  cached: boolean;
  coalesced: boolean;
  top: HotCoin[];
  universe_size: number;
  api: { requests: number; weight: number; duration_ms: number };
};

/** What the screen holds and restores after a reload. */
export type HotSnapshot = {
  scannedAtMs: number;
  top: HotCoin[];
  universeSize: number;
};

export const HOT_SCAN_TIMEOUT_MS = 30_000;
const STORAGE_KEY = "usb.crypto.hotCoins.v0";

export async function fetchHotCoins(signal?: AbortSignal): Promise<HotScanResponse> {
  let response: Response;
  try {
    response = await fetch(`${CRYPTO_API_BASE}/api/crypto/hot-coins`, {
      method: "GET", cache: "no-store", signal,
    });
  } catch (exception) {
    if (exception instanceof DOMException && exception.name === "AbortError") {
      throw new CryptoApiError(0, "REQUEST_ABORTED", "탐색 시간이 초과되었습니다.");
    }
    throw new CryptoApiError(0, "NETWORK_ERROR", "탐색 서버에 연결할 수 없습니다.");
  }
  const body = await response.json().catch(() => null) as
    (HotScanResponse & { error?: { code?: string; message?: string } }) | null;
  if (!response.ok || !body) {
    throw new CryptoApiError(response.status, body?.error?.code || "HTTP_ERROR",
      body?.error?.message || `HTTP ${response.status}`);
  }
  if (!isSnapshotRows(body.top) || typeof body.scanned_at_ms !== "number") {
    throw new CryptoApiError(response.status, "BAD_RESPONSE", "탐색 응답 형식이 올바르지 않습니다.");
  }
  return body;
}

export function toSnapshot(body: HotScanResponse): HotSnapshot {
  return { scannedAtMs: body.scanned_at_ms, top: body.top, universeSize: body.universe_size };
}

/** A response only replaces what is held if it is not older. A slow answer to an earlier
 *  request must never paint over a newer result. */
export function shouldReplace(held: HotSnapshot | null, next: HotSnapshot): boolean {
  return held == null || next.scannedAtMs >= held.scannedAtMs;
}

function isSnapshotRows(rows: unknown): rows is HotCoin[] {
  return Array.isArray(rows) && rows.length > 0 && rows.every(row =>
    row != null && typeof row === "object"
    && typeof (row as HotCoin).symbol === "string"
    && typeof (row as HotCoin).one_hour_range_pct === "number"
    && typeof (row as HotCoin).twenty_four_hour_range_pct === "number"
    && typeof (row as HotCoin).spread_bps === "number"
    && typeof (row as HotCoin).estimated_round_trip_slippage_bps_1000 === "number");
}

/** `localStorage` throws in a private window and under blocked site data; both read as "nothing
 *  held". A stored value that fails the shape check is ignored rather than half-rendered. */
export function readStoredHot(): HotSnapshot | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<HotSnapshot>;
    if (typeof parsed.scannedAtMs !== "number" || !isSnapshotRows(parsed.top)) return null;
    return { scannedAtMs: parsed.scannedAtMs, top: parsed.top,
      universeSize: typeof parsed.universeSize === "number" ? parsed.universeSize : 0 };
  } catch {
    return null;
  }
}

export function storeHot(snapshot: HotSnapshot): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // Not remembered across a reload; the result on screen is unaffected.
  }
}

export const HOT_STORAGE_KEY = STORAGE_KEY;

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
