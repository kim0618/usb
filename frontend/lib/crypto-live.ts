/** Binance LIVE (real account) client for the manual terminal.
 *
 *  Same screen, different account. This file talks to `/api/crypto/binance/*` and holds no
 *  credential of any kind: the key lives in the backend's environment and nothing derived from
 *  it beyond a fingerprint ever crosses the wire.
 *
 *  Every figure arrives as a decimal string and stays one until it is formatted, exactly as
 *  `crypto-paper.ts` does. Nothing here computes a balance, a PnL or a liquidation price - when
 *  the backend cannot supply one, the panel shows "-" rather than a number this file invented.
 */
import { CRYPTO_API_BASE, CryptoApiError, OrderSide } from "@/lib/crypto-paper";

export type AccountSource = "PAPER" | "BINANCE_LIVE";

export type LiveBlocker = { code: string; message: string };

export type LiveGates = {
  armed: boolean; env_flag: boolean; client_armed: boolean; env_flag_name: string;
};

/** Asset-denominated figures (`wallet_balance` and friends) come from Binance's `assets[USDT]`
 *  row. The `account_*_usd` figures are the same account valued in USD by Binance and drift with
 *  the peg; they are shown as a footnote, never as the balance. */
export type LiveBalance = {
  asset: string; wallet_balance: string; available_balance: string; margin_balance: string;
  unrealized_pnl: string; max_withdraw: string; initial_margin: string; maint_margin: string;
  account_wallet_usd: string; account_available_usd: string; account_margin_usd: string;
  account_unrealized_usd: string; usd_valuation_ratio: string | null;
  update_time_ms: number | null; balance_source: string;
};

export type LivePosition = {
  symbol: string; side: OrderSide | null; position_side: string; qty: string; signed_qty: string;
  entry_price: string | null; break_even_price: string | null; mark_price: string | null;
  unrealized_pnl: string; liquidation_price: string | null; isolated_margin: string | null;
  notional: string | null; initial_margin: string | null; maint_margin: string | null;
  adl: number | null; update_time_ms: number | null; is_flat: boolean;
};

export type LiveSymbolConfig = {
  symbol: string; margin_type: string; leverage: string; max_notional: string | null;
  is_auto_add_margin: boolean | null;
};

export type LiveAccount = {
  symbol: string; ready: boolean; blockers: LiveBlocker[]; fetched_at_ms: number; age_ms: number;
  stale: boolean; source: "BINANCE_LIVE";
  balance: LiveBalance | null; position: LivePosition | null;
  symbol_config: LiveSymbolConfig | null;
  position_mode: { dual_side: boolean; mode: "ONE_WAY" | "HEDGE" } | null;
  commission: { symbol: string; maker: string; taker: string; source: string } | null;
  mark: { symbol: string; mark_price: string; index_price: string | null;
          last_funding_rate: string | null; next_funding_time_ms: number | null } | null;
  book: { best_bid: string; best_ask: string; bid_qty: string; ask_qty: string; spread: string } | null;
  filters: Record<string, string | number | null> | null;
  krw: Record<string, string> | null;
  position_krw: Record<string, string> | null;
  krw_per_usdt: string | null;
  krw_note: string;
  gates: LiveGates;
  stream: Record<string, unknown> | null;
};

export type LiveStatus = {
  source: "BINANCE_LIVE"; available: boolean; ready: boolean; blockers: LiveBlocker[];
  gates: LiveGates; config: Record<string, unknown>; runtime_error: string | null;
  endpoints: { name: string; method: string; path: string; security: string; weight: string; doc: string }[];
  stream?: Record<string, unknown> | null;
  rest?: Record<string, unknown> | null;
  mirror?: Record<string, unknown> | null;
};

export type LivePreviewSide = {
  side: OrderSide; feasible: boolean; qty?: string; notional?: string; leverage?: string | null;
  required_margin?: string | null; entry_fill_price?: string; entry_fee?: string;
  exit_fill_price?: string; exit_fee?: string; round_trip_cost?: string;
  immediate_round_trip_net?: string; breakeven_exit_fill_price?: string;
  breakeven_mark_price?: string; breakeven_move?: string; breakeven_move_pct?: string;
  fee_rate?: string; fee_source?: string; fill_basis?: string;
  reject_stage?: string; reject_code?: string; reject_message?: string;
};

export type LivePreview = {
  source: "BINANCE_LIVE"; symbol: string; krw_per_usdt: string | null;
  mark_price: string | null; fetched_at_ms: number;
  sides: Partial<Record<OrderSide, LivePreviewSide>>;
};

export type LiveFill = {
  id: number; order_id: number; side: string; price: string; qty: string; quote_qty: string;
  realized_pnl: string; commission: string; commission_asset: string; maker: boolean;
  time_ms: number;
};

export type LiveFunding = {
  symbol: string; income_type: string; income: string; asset: string; time_ms: number;
};

export type LiveEventRow = { seq: number; ts_ms: number; event_type: string } & Record<string, unknown>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${CRYPTO_API_BASE}${path}`, {
      ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store",
    });
  } catch {
    throw new CryptoApiError(0, "NETWORK_ERROR", "Terminal API에 연결할 수 없습니다.");
  }
  const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
  if (!response.ok) {
    throw new CryptoApiError(response.status, body?.error?.code || "HTTP_ERROR",
      body?.error?.message || `HTTP ${response.status}`);
  }
  return body as T;
}

export const liveApi = {
  status: (signal?: AbortSignal) => request<LiveStatus>("/api/crypto/binance/status", { signal }),
  account: (signal?: AbortSignal) => request<LiveAccount>("/api/crypto/binance/account", { signal }),
  preview: (params: { side?: OrderSide; qty?: string; notional_usdt?: string }, signal?: AbortSignal) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== "") as [string, string][]);
    return request<LivePreview>(`/api/crypto/binance/preview?${query.toString()}`, { signal });
  },
  fills: (limit = 20) => request<{ total: number; fills: LiveFill[]; authority: string }>(
    `/api/crypto/binance/fills?limit=${limit}`),
  funding: (limit = 20) => request<{ total: number; funding: LiveFunding[]; authority: string }>(
    `/api/crypto/binance/funding?limit=${limit}`),
  events: (limit = 50) => request<{ total: number; events: LiveEventRow[]; role?: string }>(
    `/api/crypto/binance/events?limit=${limit}`),
  /** Sends the order the backend will refuse while the trading flag is off. Kept so the button
   *  exercises the real path rather than a disabled stub. */
  order: (body: { side: string; intent: "OPEN" | "CLOSE"; qty?: string; notional_usdt?: string }) =>
    request<{ plan: Record<string, unknown>; response: Record<string, unknown> }>(
      "/api/crypto/binance/order", { method: "POST", body: JSON.stringify(body) }),
};

/** Why LIVE is unavailable, in the operator's language. An unknown code falls through to itself
 *  rather than to a vague sentence, the same rule the paper screen follows. */
export const LIVE_BLOCKER_LABELS: Record<string, string> = {
  CREDENTIALS_MISSING: "API 키 미설정",
  HEDGE_MODE_UNSUPPORTED: "Hedge Mode 계정 · V1 미지원",
  SYMBOL_NOT_TRADING: "심볼 거래 중지",
  BINANCE_UNREACHABLE: "Binance 연결 실패",
  BINANCE_AUTH_FAILED: "인증 거부 · 키 권한/IP 확인",
  CLOCK_SKEW: "서버 시각 오차",
  RESPONSE_SHAPE_CHANGED: "응답 형식 변경 감지",
  LIVE_UNAVAILABLE: "LIVE 사용 불가",
  LIVE_TRADING_DISABLED: "실주문 잠금",
};

export const liveBlockerLabel = (code: string) => LIVE_BLOCKER_LABELS[code] || code;

export const MARGIN_MODE_LABELS: Record<string, string> = {
  CROSSED: "교차 (Cross)", CROSS: "교차 (Cross)", ISOLATED: "격리 (Isolated)",
};

/** The one sentence the LIVE screen must always be able to say about itself. */
export const LIVE_AUTHORITY_NOTE =
  "LIVE에서는 Binance 응답이 정본입니다. 잔고·포지션·청산가·수수료·펀딩 모두 Binance 값을 그대로 표시합니다.";

export const LIVE_LOCK_NOTE =
  "V1은 읽기 전용입니다. LONG·SHORT·CLOSE 버튼은 실제 주문 경로를 그대로 호출하지만 서버가 " +
  "BINANCE_LIVE_TRADING_ENABLED=false로 거부합니다.";
