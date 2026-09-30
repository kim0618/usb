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
  arm_session?: LiveArmState | null;
};

/** The manual arm window.
 *
 *  `env_flag` is the deployment's capability and `armed` is whether an order can be sent right
 *  now. The two are separate because they fail for different reasons and need different
 *  remedies: one is a server setting, the other is a click that expires. */
export type LiveArmState = {
  armed: boolean; armed_by: "ENV" | "SESSION" | null;
  armed_at_ms: number | null; expires_at_ms: number | null; remaining_s: number | null;
  ttl_s: number; arm_count: number; disarm_count: number;
  last_disarm_reason: string | null; last_disarm_ms: number | null;
  operator_note: string | null; confirmation_phrase: string; env_armed: boolean; role: string;
  available?: boolean; capability?: boolean; reason?: string;
  gates?: LiveGates;
};

export type LiveLeverageBracket = {
  bracket: number; initialLeverage: number; notionalCap: number; notionalFloor: number;
  maintMarginRatio: number; cum: number;
};

export type LiveLeverageOptions = {
  symbol: string; current: string | null; margin_type: string | null;
  max_leverage: number; options: number[]; brackets: LiveLeverageBracket[];
  notional_coef: number | null; authority: string; margin_type_note: string;
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
  /** Sends the order. The backend refuses it unless the deployment has the capability flag
   *  *and* the operator has armed a manual window; both refusals come back as real responses,
   *  so the button exercises the real path rather than a disabled stub. */
  order: (body: { side: string; intent: "OPEN" | "CLOSE"; qty?: string; notional_usdt?: string }) =>
    request<{ plan: Record<string, unknown>; response: Record<string, unknown> }>(
      "/api/crypto/binance/order", { method: "POST", body: JSON.stringify(body) }),
  armState: (signal?: AbortSignal) =>
    request<LiveArmState>("/api/crypto/binance/arm", { signal }),
  arm: (body: { confirmation: string; note?: string; ttl_s?: number }) =>
    request<LiveArmState>("/api/crypto/binance/arm",
      { method: "POST", body: JSON.stringify(body) }),
  disarm: () => request<LiveArmState>("/api/crypto/binance/disarm", { method: "POST" }),
  leverageOptions: (signal?: AbortSignal) =>
    request<LiveLeverageOptions>("/api/crypto/binance/leverage", { signal }),
  /** Asks Binance to change the leverage. The response carries what Binance reports afterwards;
   *  the caller shows that, never the requested value. */
  setLeverage: (leverage: number) =>
    request<{ requested: string; leverage: string | null; response: Record<string, unknown> }>(
      "/api/crypto/binance/leverage",
      { method: "POST", body: JSON.stringify({ leverage: String(leverage) }) }),
};

/** Whether an order may leave this screen right now, and if not, why not.
 *
 *  One function, because the badge at the top and the buttons in the ticket answering
 *  differently is the failure that matters: a screen that says "거래가능" over a button the
 *  server will refuse, or the reverse. Everything here is read from the server's own answers -
 *  `account.gates.armed` is the router's both-gates verdict and `arm.armed` is the session that
 *  expires on read - and the two are ANDed, so a snapshot that has drifted from the arm poll
 *  fails closed rather than open.
 *
 *  Connection counts as part of the gate. A stale or unreadable account is not a screen anybody
 *  should be sending a market order from, even while the window is legitimately armed.
 */
export type LiveTradeGate = {
  /** The one boolean the buttons read. */
  tradable: boolean;
  /** The account is readable and its snapshot is current. */
  connected: boolean;
  /** `BINANCE_LIVE_TRADING_ENABLED` on the server. Not something a click can change. */
  capability: boolean;
  /** The server says an order can be sent now. */
  armed: boolean;
  remaining_s: number | null;
  armed_by: "ENV" | "SESSION" | null;
  /** Empty when tradable. Shown only when the operator opens the status detail. */
  reasons: LiveBlocker[];
};

export function liveTradeGate(account: LiveAccount | null, arm: LiveArmState | null,
                              error?: string | null): LiveTradeGate {
  const reasons: LiveBlocker[] = [];
  const available = arm?.available !== false;
  // `env_flag` on the account snapshot is the same server setting as `capability` on the arm
  // state, and it is present on every poll. The arm *response* to a POST carries neither, so
  // reading `env_armed` before it would report a capable server as incapable for the one render
  // between arming and the re-read.
  const capability = Boolean(arm?.capability ?? account?.gates.env_flag ?? arm?.env_armed ?? false);
  // The account snapshot carries the router's verdict; the arm poll carries the session's. Both
  // have to say yes, so a disagreement between two reads a tick apart locks rather than opens.
  const armed = Boolean(account?.gates.armed) && (arm == null || Boolean(arm.armed));
  const connected = Boolean(account?.ready && !account.stale);

  if (!available) {
    reasons.push({ code: "LIVE_UNAVAILABLE", message: arm?.reason || "LIVE를 사용할 수 없습니다." });
  }
  if (!account) {
    reasons.push({ code: "NOT_CONNECTED", message: "계좌를 아직 읽지 못했습니다." });
  } else if (!account.ready) {
    if (account.blockers.length) reasons.push(...account.blockers);
    else reasons.push({ code: "NOT_CONNECTED", message: "계좌를 읽을 수 없습니다." });
  } else if (account.stale) {
    reasons.push({ code: "SNAPSHOT_STALE",
                   message: `계좌 응답이 ${Math.round(account.age_ms / 1000)}초째 갱신되지 않았습니다.` });
  }
  if (!capability) {
    reasons.push({ code: "LIVE_TRADING_DISABLED",
                   message: `이 서버는 ${account?.gates.env_flag_name || "BINANCE_LIVE_TRADING_ENABLED"}=false 입니다. 서버 설정을 바꿔야 열립니다.` });
  } else if (!armed) {
    reasons.push({ code: "NOT_ARMED", message: "거래 활성화 후 제한 시간 동안만 주문이 나갑니다." });
  }
  if (error) reasons.push({ code: "LIVE_ERROR", message: error });

  return {
    tradable: available && connected && capability && armed,
    connected, capability, armed,
    remaining_s: arm?.remaining_s ?? null,
    armed_by: arm?.armed_by ?? null,
    reasons,
  };
}

/** Typed verbatim to arm. Mirrors `live.arm.CONFIRMATION`; the server checks it again and is
 *  the authority, so a drift here fails closed with a visible refusal. */
export const ARM_CONFIRMATION = "ARM LIVE TRADING";

/** Why LIVE is unavailable, in the operator's language. An unknown code falls through to itself
 *  rather than to a vague sentence, the same rule the paper screen follows. */
export const LIVE_BLOCKER_LABELS: Record<string, string> = {
  CREDENTIALS_MISSING: "API 키 미설정",
  NOT_CONNECTED: "계좌 연결 안 됨",
  SNAPSHOT_STALE: "계좌 응답 지연",
  NOT_ARMED: "거래 비활성",
  LIVE_ERROR: "연결 오류",
  HEDGE_MODE_UNSUPPORTED: "Hedge Mode 계정 · V1 미지원",
  SYMBOL_NOT_TRADING: "심볼 거래 중지",
  BINANCE_UNREACHABLE: "Binance 연결 실패",
  BINANCE_AUTH_FAILED: "인증 거부 · 키 권한/IP 확인",
  CLOCK_SKEW: "서버 시각 오차",
  RESPONSE_SHAPE_CHANGED: "응답 형식 변경 감지",
  LIVE_UNAVAILABLE: "LIVE 사용 불가",
  LIVE_TRADING_DISABLED: "실거래 잠금",
};

export const liveBlockerLabel = (code: string) => LIVE_BLOCKER_LABELS[code] || code;

export const MARGIN_MODE_LABELS: Record<string, string> = {
  CROSSED: "교차 (Cross)", CROSS: "교차 (Cross)", ISOLATED: "격리 (Isolated)",
};

/** The one sentence the LIVE screen must always be able to say about itself. */
export const LIVE_AUTHORITY_NOTE =
  "LIVE에서는 Binance 응답이 정본입니다. 잔고·포지션·청산가·수수료·펀딩 모두 Binance 값을 그대로 표시합니다.";

export const LIVE_LOCK_NOTE =
  "실주문은 거래가능 상태인 동안에만 나갑니다. 서버 재시작·시간 만료·해제 중 하나라도 발생하면 다시 잠깁니다.";

/** Shown next to the arm control. States the three ways the window closes, because an operator
 *  who believes it stays open is the one who leaves an armed account unattended. */
export const ARM_NOTE =
  "거래 활성화는 이 서버 프로세스의 메모리에만 있습니다. 제한 시간이 지나거나, 해제하거나, 서버가 " +
  "재시작하면 자동으로 거래불가로 돌아갑니다. AUTO는 활성화 여부와 무관하게 실계좌 주문을 낼 수 없습니다.";

/** The one line the confirmation dialog has to say. Everything the operator needs in order to
 *  decide is in it: this is the real account, and orders from this screen reach it. */
export const ACTIVATE_CONFIRM_NOTE = "실제 Binance 계좌에서 주문이 실행됩니다.";

/** The operating default for BINANCE LIVE.
 *
 *  A policy, not an action. Nothing in this screen applies it: the panel reads whatever Binance
 *  reports and marks this value as the one operations settled on, and only a click sends the
 *  change. That separation is the whole point - a page that set the account to its own idea of
 *  a default on load would move an operator's margin and liquidation price without them asking,
 *  and a page load is not a decision.
 *
 *  Leverage is a margin setting. At 10x the same 0.001 BTC is still 0.001 BTC; what changes is
 *  how much of the wallet is held against it and how far the liquidation sits.
 */
export const LIVE_DEFAULT_LEVERAGE = 10;

export const LIVE_LEVERAGE_POLICY_NOTE =
  `운영 기본은 ${LIVE_DEFAULT_LEVERAGE}x입니다. 화면이 알아서 바꾸지 않으니 ` +
  `필요하면 ${LIVE_DEFAULT_LEVERAGE}x를 눌러 직접 변경하세요.`;

/** V1 does not change the margin mode; `POST /fapi/v1/marginType` is on the endpoint deny list. */
export const MARGIN_MODE_READONLY_NOTE =
  "마진 모드는 Binance에서 설정한 값을 표시만 합니다. 변경은 Binance 앱/웹에서 하세요.";
