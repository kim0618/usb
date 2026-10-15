"use client";

/** The LIVE half of the manual terminal: the same screen, showing a real Binance account.
 *
 *  Nothing here is a second product. The switch at the top changes which account the panels
 *  read; the layout, the language and the formatting are the paper screen's. What is different
 *  is what the screen is allowed to claim: every figure below is Binance's own, and the order
 *  buttons call the real order path, which the backend refuses while the trading flag is off.
 *
 *  The refusal is shown, not hidden. A disabled button that never calls anything would leave the
 *  most dangerous path in the system untested until the day it is armed.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { PnlBreakdown, PnlHeadline, PositionCardShell, PositionExposure, type BreakdownLine, type Fact } from "@/components/position-card";
import { MetricCard } from "@/components/ui";
import { CryptoApiError, costUsdt, holdingDuration, krw, num, price, qty as qtyFmt, signedKrw, signedUsdt,
  toneClass, usdt } from "@/lib/crypto-paper";
import type { OrderSide } from "@/lib/crypto-paper";
import {
  ACTIVATE_CONFIRM_NOTE, ARM_CONFIRMATION, ARM_NOTE, AccountSource, LIVE_AUTHORITY_NOTE,
  LIVE_LOCK_NOTE, LIVE_PRESET_LABELS,
  LiveAccount, LiveArmState, LiveBlocker, LiveExitGuard, LivePerformance, LiveLeverageOptions, LivePositionCard as LivePositionCardData,
  LivePreview, LiveSizing, LiveStatus, LiveTradeGate, MARGIN_MODE_LABELS,
  MARGIN_MODE_READONLY_NOTE, leverageRestriction, liveApi, liveBlockerLabel, livePresetQty,
  liveSideAllowance, liveTradeGate,
  liveLeverageLabel,
} from "@/lib/crypto-live";
import { DEFAULT_SYMBOL, baseAsset as baseAssetOf } from "@/lib/crypto-symbols";
import { visiblePoll } from "@/lib/visible-poll";
import { executionsFor } from "@/lib/crypto-executions";
import type { Execution } from "@/lib/crypto-executions";
import { OrderAmountSummary } from "@/components/crypto-order-amount";

/** The unit a quantity on screen is in: Binance's own `baseAsset` when the filters were read,
 *  and the symbol minus "USDT" when they were not.
 *
 *  One helper so a panel cannot print "BTC" because that is what it was written with. Every
 *  quantity label below goes through it, including the ones in confirmation sentences - a
 *  dialog that says "SHORT 1.5 BTC" over a SOL order is the one place a wrong unit becomes a
 *  wrong decision. */
export function liveUnit(account: LiveAccount | null | undefined,
                         fallback: string = DEFAULT_SYMBOL): string {
  return account?.base_asset || baseAssetOf(account?.symbol || fallback);
}

export const LIVE_POLL_MS = 2_000;
/** Older than this and the panel says so instead of presenting the figures as current. */
export const LIVE_STALE_MS = 15_000;
/** The ladder needs a depth read, so it runs slower than the account poll. */
export const SIZING_POLL_MS = 5_000;

export function AccountSourceSwitch({ value, onChange, available, busy }: {
  value: AccountSource; onChange: (next: AccountSource) => void; available: boolean; busy?: boolean;
}) {
  const options: { key: AccountSource; label: string; hint: string }[] = [
    { key: "PAPER", label: "PAPER", hint: "가상 계좌 · 기존 페이퍼 엔진" },
    { key: "BINANCE_LIVE", label: "BINANCE LIVE", hint: "Binance USDⓈ-M 실계좌" },
  ];
  return (
    <div className="mb-2 flex items-center gap-2" data-testid="account-source-switch">
      {options.map(option => {
        const active = value === option.key;
        const disabled = option.key === "BINANCE_LIVE" && !available;
        return (
          <button key={option.key} type="button" disabled={disabled || busy}
            aria-pressed={active} title={disabled ? "Binance API 키가 없어 선택할 수 없습니다." : option.hint}
            data-testid={`source-${option.key}`}
            onClick={() => onChange(option.key)}
            className={`rounded-md border px-3 py-1.5 text-xs font-bold tracking-wide transition-colors
              ${active ? (option.key === "BINANCE_LIVE" ? "border-danger bg-danger-soft text-danger" : "border-primary bg-surface-alt text-foreground")
                       : "border-line text-muted hover:text-foreground"}
              ${disabled ? "cursor-not-allowed opacity-40" : ""}`}>
            {option.label}
          </button>
        );
      })}
      {!available && (
        <span className="text-[11px] text-muted" data-testid="live-unavailable-note">
          Binance API 키가 설정되지 않아 PAPER만 사용할 수 있습니다.
        </span>
      )}
    </div>
  );
}

/** The whole of the LIVE screen's safety state, in one line the operator can read at a glance.
 *
 *  Two facts and one action. Is the screen still in touch with the account ("동기화 N초 전"),
 *  and may an order be sent right now ("거래가능"/"거래불가"). Everything else - which of the
 *  two server gates is shut, how long the window has left, how to close it early - is behind
 *  the badge, one click away, because it is what an operator reads *after* the answer rather
 *  than instead of it.
 *
 *  The panel this replaces named the environment variable, the session and the client flag on
 *  the default screen and left the reader to work out whether the buttons below would do
 *  anything. None of those gates moved: the server still holds all of them, this bar only
 *  reports their conclusion.
 */
export function LiveTradeBar({ account, gate, onActivate, onDisarm, busy, error }: {
  account: LiveAccount | null;
  gate: LiveTradeGate;
  onActivate: () => void;
  onDisarm: () => void;
  busy?: boolean;
  error?: string | null;
}) {
  const [detailOpen, setDetailOpen] = useState(false);
  const sync = !account ? "연결 대기"
    : !account.ready ? "연결 안 됨"
    : account.stale ? "응답 지연"
    : `동기화 ${Math.round(account.age_ms / 1000)}초 전`;
  // Only a session window has an end. An ENV-armed process has no countdown to show and no
  // deadline to promise, so the bar says nothing rather than implying one.
  const remaining = gate.armed && gate.armed_by === "SESSION" ? gate.remaining_s : null;

  return (
    <div className="mb-2" data-testid="live-trade-bar">
      <div className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2
        ${gate.tradable ? "border-success bg-success-soft" : "border-danger bg-danger-soft"}`}
        role="status">
        <span className="text-[11px] font-medium tabular-nums text-foreground-secondary"
          data-testid="live-sync">{sync}</span>
        <div className="flex items-center gap-2">
          {/* The badge is the control that opens the reasons. A state this consequential should
              be able to explain itself without the explanation being on screen all day. */}
          <button type="button" data-testid="live-trade-state" aria-expanded={detailOpen}
            onClick={() => setDetailOpen(open => !open)}
            className={`inline-flex whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-bold tracking-wide
              ${gate.tradable ? "tone-success" : "tone-danger"}`}>
            {gate.tradable ? "거래가능" : "거래불가"}
          </button>
          {!gate.armed && (
            <button type="button" data-testid="live-activate" disabled={busy || !gate.capability}
              onClick={onActivate}
              className="rounded-md bg-danger px-3 py-1.5 text-xs font-bold text-white disabled:opacity-40">
              거래 활성화
            </button>
          )}
        </div>
      </div>

      {detailOpen && (
        <div className="mt-1 rounded-lg border border-line bg-surface p-3" data-testid="live-trade-detail">
          <ul className="space-y-1">
            {gate.reasons.length ? gate.reasons.map((reason, index) => (
              <li key={`${reason.code}-${index}`} className="text-xs text-foreground-secondary">
                <span className="font-semibold text-foreground">{liveBlockerLabel(reason.code)}</span>
                {" · "}{reason.message}
              </li>
            )) : (
              <li className="text-xs text-foreground-secondary">
                차단 사유가 없습니다. 지금은 이 화면에서 실주문이 나갑니다.
              </li>
            )}
          </ul>
          {gate.armed && gate.armed_by === "ENV" && (
            <p className="mt-2 text-[11px] text-muted" data-testid="live-arm-by-env">
              환경변수로 열려 있는 프로세스입니다. 이 화면에서는 닫을 수 없습니다.
            </p>
          )}
          {/* The window's remaining time lives here rather than in the bar. BTCUSDT trades
              around the clock, so a countdown beside the verdict read as trading hours; it is
              a property of the arm session, which is what this panel is about. */}
          {remaining != null && (
            <p className="text-[11px] font-semibold tabular-nums text-foreground-secondary"
              data-testid="live-arm-remaining">
              {Math.floor(remaining / 60)}분 {remaining % 60}초 남음
            </p>
          )}
          {gate.armed && gate.armed_by === "SESSION" && (
            <button type="button" data-testid="live-disarm" disabled={busy} onClick={onDisarm}
              className="mt-2 rounded-md border border-line px-3 py-1.5 text-xs font-bold text-foreground disabled:opacity-40">
              거래 해제
            </button>
          )}
          <p className="mt-2 text-[10px] text-muted" data-testid="live-arm-note">{ARM_NOTE}</p>
        </div>
      )}

      {/* A refused activation is not detail. It is the answer to the click that was just made,
          so it stays on screen whether or not the reasons are open. */}
      {error && <p className="mt-1 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning"
        role="status" data-testid="live-arm-error">{error}</p>}
    </div>
  );
}

/** The one thing between a click and a real account.
 *
 *  It asks for a decision, not for a password. The typed phrase it replaces was protecting the
 *  *API* from an accidental call, which is the server's job and the server still does it: the
 *  request below still carries `ARM_CONFIRMATION` and is still refused without it. What the
 *  operator has to supply is intent, and a modal they must dismiss to get past supplies it.
 */
export function LiveActivateDialog({ open, onCancel, onConfirm, busy, ttlS }: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  busy?: boolean;
  ttlS?: number | null;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;
  const minutes = ttlS ? Math.round(ttlS / 60) : null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
      data-testid="live-activate-dialog" role="dialog" aria-modal="true"
      aria-labelledby="live-activate-title">
      <div className="w-full max-w-sm rounded-xl border border-danger bg-surface p-5 shadow-panel">
        <p id="live-activate-title" className="text-xs font-bold tracking-wide text-danger">
          BINANCE LIVE · 실계좌
        </p>
        <p className="mt-2 text-sm font-semibold text-foreground" data-testid="live-activate-note">
          {ACTIVATE_CONFIRM_NOTE}
        </p>
        <p className="mt-1 text-[11px] text-muted" data-testid="live-activate-ttl">
          {minutes
            ? `활성화 후 ${minutes}분이 지나거나 서버가 재시작하면 자동으로 거래불가로 돌아갑니다.`
            : "직접 해제할 때까지 거래가능 상태가 유지됩니다. 서버가 재시작하면 자동으로 거래불가로 돌아갑니다."}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" data-testid="live-activate-cancel" onClick={onCancel}
            className="btn-muted px-3 py-2 text-xs">취소</button>
          <button type="button" data-testid="live-activate-confirm" disabled={busy}
            onClick={onConfirm}
            className="rounded-md bg-danger px-3 py-2 text-xs font-bold text-white disabled:opacity-40">
            실거래 활성화
          </button>
        </div>
      </div>
    </div>
  );
}

export function LiveBlockedPanel({ blockers, error }: { blockers: LiveBlocker[]; error?: string | null }) {
  if (!blockers.length && !error) return null;
  return (
    <div className="panel border-danger p-4" data-testid="live-blocked">
      <p className="text-sm font-semibold text-danger">Binance 실계좌를 사용할 수 없습니다</p>
      <ul className="mt-2 space-y-1">
        {blockers.map(blocker => (
          <li key={blocker.code} className="text-xs text-foreground-secondary">
            <span className="font-semibold text-foreground">{liveBlockerLabel(blocker.code)}</span>
            {" · "}{blocker.message}
          </li>
        ))}
        {error && <li className="text-xs text-foreground-secondary">{error}</li>}
      </ul>
      <p className="mt-3 text-[11px] text-muted">
        주문 기능은 비활성화되어 있습니다. PAPER로 전환하면 기존 화면을 그대로 사용할 수 있습니다.
      </p>
    </div>
  );
}

export function LiveMarketHeader({ account }: { account: LiveAccount }) {
  const balance = account.balance;
  const krwFigures = account.krw;
  const mark = account.mark?.mark_price ?? null;
  return (
    <header className="mb-2 sm:mb-3" data-testid="live-market-header">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-bold tracking-wide text-foreground">{account.symbol}</span>
          <span className="text-[10px] font-medium text-muted">무기한 · Binance USDⓈ-M</span>
        </div>
        <span className="text-[10px] font-semibold text-muted" data-testid="live-margin-mode">
          {account.symbol_config
            ? `${MARGIN_MODE_LABELS[account.symbol_config.margin_type] || account.symbol_config.margin_type} · ${num(account.symbol_config.leverage)}x`
            : "설정 확인 불가"}
        </span>
      </div>
      <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3">
        <span className="text-[28px] font-bold leading-none tabular-nums text-foreground sm:text-4xl"
          data-testid="live-mark-price">{price(mark)}</span>
        <span className="text-[11px] text-muted">Mark · Binance</span>
      </div>
      <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 sm:mt-2.5 sm:grid-cols-4 sm:gap-y-1.5"
        data-testid="live-account-compact">
        <Figure label="지갑 잔고" value={krwFigures ? krw(krwFigures.wallet_balance) : usdt(balance?.wallet_balance)}
          sub={usdt(balance?.wallet_balance)} testId="live-wallet" />
        <Figure label="주문가능" value={krwFigures ? krw(krwFigures.available_balance) : usdt(balance?.available_balance)}
          sub={usdt(balance?.available_balance)} testId="live-available" />
        <Figure label="미실현" value={krwFigures ? signedKrw(krwFigures.unrealized_pnl) : signedUsdt(balance?.unrealized_pnl)}
          sub={signedUsdt(balance?.unrealized_pnl)} tone={toneClass(balance?.unrealized_pnl)} testId="live-unrealized" />
        <Figure label="마진 잔고" value={krwFigures ? krw(krwFigures.margin_balance) : usdt(balance?.margin_balance)}
          sub={usdt(balance?.margin_balance)} testId="live-margin-balance" />
      </dl>
    </header>
  );
}

/** Where the figures come from, and how the two dollar numbers reconcile.
 *
 *  True and worth being able to find, but not worth the two lines it used to take under the
 *  price on every render: the operator reads it once. It lives in the disclosure at the bottom
 *  of the screen with the rest of the documentation, the same place the paper screen keeps its
 *  run notes.
 */
export function LiveAuthorityNote({ account }: { account: LiveAccount }) {
  const balance = account.balance;
  return (
    <p className="text-[11px] text-muted" data-testid="live-authority-note">
      {LIVE_AUTHORITY_NOTE}
      {balance && (
        <span data-testid="live-usd-valuation">
          {" "}잔고는 Binance USDT 잔고({usdt(balance.wallet_balance, 6)})입니다.
          계정 USD 환산은 {usdt(balance.account_wallet_usd, 6).replace("USDT", "USD")}이며
          페그에 따라 조금씩 움직입니다.
        </span>
      )}
    </p>
  );
}

function Figure({ label, value, sub, tone, testId }: {
  label: string; value: string; sub: string; tone?: string; testId?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[10px] text-muted">{label}</dt>
      <dd className={`truncate text-[13px] font-semibold tabular-nums ${tone || "text-foreground"}`}
        data-testid={testId}>{value}</dd>
      <dd className="truncate text-[10px] tabular-nums text-muted">{sub}</dd>
    </div>
  );
}

/** Binance's own top of book, shown beside the chart.
 *
 *  The chart is still the paper screen's Bybit feed in V1, and the two exchanges do not print
 *  the same price to the tick. Leaving only the chart's Bybit bid/ask on a LIVE screen invites
 *  the operator to read it as the book their order would hit, so the real one is shown here and
 *  the chart is labelled for what it is.
 */
export function LiveBookStrip({ account }: { account: LiveAccount }) {
  const book = account.book;
  const spreadBps = book && Number(book.best_bid) > 0
    ? (Number(book.spread) / Number(book.best_bid)) * 10_000 : null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-md border border-line px-3 py-2"
      data-testid="live-book-strip">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs tabular-nums">
        <span className="text-[10px] font-bold tracking-wide text-danger">BINANCE 실호가</span>
        <span className="text-success" data-testid="live-best-bid">Bid {price(book?.best_bid)}</span>
        <span className="text-danger" data-testid="live-best-ask">Ask {price(book?.best_ask)}</span>
        <span className="text-muted">Spread {price(book?.spread, 2)}
          {spreadBps != null && ` · ${spreadBps.toFixed(2)} bp`}</span>
        <span className="text-muted">Funding {account.mark?.last_funding_rate
          ? `${(Number(account.mark.last_funding_rate) * 100).toFixed(4)}%` : "-"}</span>
      </div>
    </div>
  );
}

/** The held position on a wide screen, reading the same card the phone reads.
 *
 *  It takes `card` and not just `account` on purpose. "청산 시 예상 순손익" exists only on the
 *  card - it is `net_if_closed`, assembled on the server from the realised PnL inside the open
 *  cycle, the gross against the real opposite side of the book, the commission Binance has
 *  already charged, the close's own fee and the signed funding - and the phone has shown it
 *  since the card was added while this panel showed `명목` and `유지 마진` instead. Computing it
 *  here from fee, funding and price would be the one way to make the two screens disagree about
 *  the same position, so the panel is fed the figure rather than the ingredients.
 *
 *  Parity with the phone and with 자동청산 therefore holds by construction: one request,
 *  `GET /api/crypto/binance/position`, one `net_if_closed`, and the exit guard's
 *  `current_net_usdt` is that same field read by the guard's own tick.
 */
export function LivePositionPanel({ account, card }: {
  account: LiveAccount;
  /** The server's position card. Absent means the panel falls back to the account snapshot for
   *  the position figures and simply has no net estimate to show. */
  card?: LivePositionCardData | null;
}) {
  const position = account.position;
  if (!position || position.is_flat) return null;
  const open = card?.open ? card : null;
  const parts = livePositionParts({
    card: open, prefix: "live-position", symbolConfig: account.symbol_config,
    fallback: { entry: position.entry_price, mark: position.mark_price ?? account.mark?.mark_price ?? null,
                liquidation: position.liquidation_price, margin: position.initial_margin,
                maint: position.maint_margin, unrealized: position.unrealized_pnl,
                unrealizedKrw: account.position_krw?.unrealized_pnl ?? null },
  });
  return (
    <PositionCardShell source="LIVE" testId="live-position-panel" className="panel p-4 sm:p-5"
      side={position.side === "LONG" ? "LONG" : "SHORT"} sideTestId="live-position-side"
      leverage={account.symbol_config ? `${num(account.symbol_config.leverage)}x` : "-"}
      leverageTestId="live-position-leverage"
      qty={`${qtyFmt(position.qty)} ${liveUnit(account)}`} qtyTestId="live-position-qty"
      held={open ? holdingDuration(open.opened_at_ms ?? null, Date.now()) : null}
      top={open ? <LiveExposure card={open} testIdPrefix="live-position" /> : undefined}
      headline={<PnlHeadline pnl={parts.pnl} net={parts.net}/>}
      facts={parts.facts} breakdown={parts.breakdown} />
  );
}

type LivePartsInput = {
  card: LivePositionCardData | null; prefix: string;
  symbolConfig?: LiveAccount["symbol_config"] | null;
  /** Snapshot figures used only while the server's card has not arrived. */
  fallback?: { entry?: string | null; mark?: string | null; liquidation?: string | null; margin?: string | null;
               maint?: string | null; unrealized?: string | null; unrealizedKrw?: string | null };
};

/** Everything the LIVE card prints, formatted from the server's card and nothing else.
 *
 *  The phone card and the wide panel both call this, so the two screens cannot disagree about a
 *  position and the LIVE card reads in the same order as the paper one. No figure is derived: a
 *  line the server does not supply is a dash, and "청산 시 예상 순손익" exists only when the
 *  server priced the close. */
function livePositionParts({ card, prefix, symbolConfig, fallback }: LivePartsInput) {
  const f = fallback ?? {};
  const close = card?.close;
  const netShown = card?.net_complete === true && card.net_if_closed != null;
  const unrealized = card?.unrealized_pnl ?? f.unrealized ?? null;
  const unrealizedKrw = card?.krw?.unrealized_pnl ?? f.unrealizedKrw ?? null;
  const marginMode = symbolConfig
    ? (MARGIN_MODE_LABELS[symbolConfig.margin_type] || symbolConfig.margin_type) : "-";
  // Say which thing is missing: the card itself (not received yet), the server's own refusal, the
  // book, or the opening fill. Only the book case is "호가 확인 불가".
  const reason = !card ? "포지션 상세 조회 중"
    : card.available === false ? (card.reject_message || "포지션 상세를 불러오지 못했습니다")
    : close && close.feasible === false
      ? (close.reject_message || (close.reject_code === "NO_LIQUIDITY" ? "청산 가능한 호가 깊이 부족"
                                                                       : "실시간 호가 확인 불가"))
    : card.opened_at_ms == null ? "진입 시각을 확인할 수 없어 순손익을 계산하지 않았습니다"
    : "실시간 호가 확인 불가";
  const pnl = {
    krw: unrealizedKrw != null ? signedKrw(unrealizedKrw) : null,
    usdt: signedUsdt(unrealized), tone: unrealized, note: "Binance Mark 기준",
    krwTestId: `${prefix}${prefix === "live-card" ? "-unrealized-krw" : "-pnl-krw"}`,
    usdtTestId: `${prefix}${prefix === "live-card" ? "-unrealized" : "-pnl"}`,
  };
  const net = netShown
    ? { shown: true as const, krw: card!.krw?.net_if_closed != null ? signedKrw(card!.krw!.net_if_closed) : null,
        usdt: signedUsdt(card!.net_if_closed ?? null), tone: card!.net_if_closed,
        note: "수수료·펀딩·체결가 포함", krwTestId: `${prefix}-net-krw`, usdtTestId: `${prefix}-net` }
    : { shown: false as const, reason, testId: `${prefix}-net-unavailable` };
  const entry = card?.entry_price ?? f.entry ?? null;
  const mark = card?.mark_price ?? f.mark ?? null;
  const liquidation = card?.liquidation_price ?? f.liquidation ?? null;
  const facts: Fact[] = [
    ["진입가", price(entry), `${prefix}-entry`],
    ["Mark", price(mark), `${prefix}-mark`],
    ["청산가", Number(liquidation) > 0 ? price(liquidation) : "-"],
    // The margin Binance holds, off `positionRisk.initialMargin` - not `notional / leverage`,
    // which ignores the maintenance tier.
    ["사용 증거금", usdt(card?.initial_margin ?? f.margin ?? null, 2)],
    ["마진 모드", marginMode],
    ["유지 마진", usdt(card?.maint_margin ?? f.maint ?? null, 4)],
    ["손익분기", price(card?.break_even_price ?? null)],
  ];
  if (!card) return { pnl, net, facts, breakdown: undefined };

  const costOf = (value: string | null | undefined) => costUsdt(value, 4);
  const lines: BreakdownLine[] = [
    { label: "포지션 손익 (Mark 기준 미실현)", value: unrealizedKrw != null ? signedKrw(unrealizedKrw) : signedUsdt(unrealized),
      sub: unrealizedKrw != null ? signedUsdt(unrealized) : undefined, tone: unrealized },
    { label: "수수료 (확정)", value: costOf(card.commission_paid), tone: null },
  ];
  if (card.realized_since_open != null && Number(card.realized_since_open) !== 0) {
    lines.push({ label: "부분청산 실현손익", value: signedUsdt(card.realized_since_open), tone: card.realized_since_open });
  }
  lines.push({ label: "현재까지 Funding", value: signedUsdt(card.funding_income ?? null), tone: card.funding_income ?? null });
  lines.push({ label: "예상 청산 수수료", value: costOf(close?.exit_fee), tone: null });
  lines.push({ label: "예상 체결비용 (슬리피지·스프레드)", value: "-", tone: null,
    note: close?.exit_fill_price != null
      ? `Mark ${price(card.mark_price)} → 예상 체결 ${price(close.exit_fill_price)} · 순손익에 포함` : undefined });
  const breakdown = (
    <PnlBreakdown testId={`${prefix}-breakdown`} lines={lines}
      quoteAge={card.age_ms != null ? `Binance 기준 · ${Math.round(card.age_ms / 1000)}초 전` : null}
      unavailable={netShown ? null : reason}
      totals={[{ label: "청산 시 예상 순손익",
        value: card.krw?.net_if_closed != null ? signedKrw(card.krw.net_if_closed) : signedUsdt(card.net_if_closed ?? null),
        sub: card.krw?.net_if_closed != null ? signedUsdt(card.net_if_closed ?? null) : undefined,
        tone: card.net_if_closed }]} />
  );
  return { pnl, net, facts, breakdown };
}

/** How much market the position is actually holding, in the one place both panels read it.
 *
 *  "증거금" and "포지션 규모" are different numbers and the screen says which is which. The
 *  margin is the operator's own money Binance is holding (`positionRisk.initialMargin`, 339.24
 *  USDT on the real account at 20x); the exposure is the position's value at the mark
 *  (`abs(positionRisk.notional)`, 6,788.74 USDT on that same position). Twenty times apart, and
 *  only the second answers "how much is riding on this".
 *
 *  Both come from the server's card, so this component formats and never derives - including
 *  the sign, which is dropped on the server because a SHORT's notional is negative and a size
 *  is not a direction. KRW leads because that is the currency the operator thinks in; the USDT
 *  figure underneath is the accounting authority.
 */
export function LiveExposure({ card, testIdPrefix }: {
  card: LivePositionCardData; testIdPrefix: string;
}) {
  if (card.exposure == null) return null;
  const krwValue = card.krw?.exposure;
  return <PositionExposure krw={krwValue != null ? krw(krwValue) : null} usdt={usdt(card.exposure)}
    testIdPrefix={testIdPrefix} />;
}

/** The held LIVE position, in the shape the paper card settled on.
 *
 *  Same reading order as `MobilePositionCard`: what is held, the two money figures, entry and
 *  mark, CLOSE, and the rest folded away. The layout is deliberately the paper card's, because
 *  an operator switching accounts should not have to relearn where the close button is.
 *
 *  What is *not* shared is the arithmetic. Every figure here is a string this file formats and
 *  never derives: the position is Binance's `positionRisk`, the net is the server's own
 *  close-now estimate off the real book, and the hold duration is measured from the fill that
 *  `userTrades` says opened the position. When the server cannot establish one of them it says
 *  so, and the card shows "-" rather than a confident wrong number.
 */
export function LivePositionCard({ card, nowMs, onClose, busy, symbolConfig }: {
  card: LivePositionCardData | null;
  nowMs: number;
  onClose: () => void;
  busy?: boolean;
  /** Only for the 마진 모드 line: the account's per-symbol setting, which the card does not carry. */
  symbolConfig?: LiveAccount["symbol_config"] | null;
}) {
  if (!card?.open) return null;
  const parts = livePositionParts({ card, prefix: "live-card", symbolConfig });
  return (
    <PositionCardShell source="LIVE" testId="live-position-card" className="panel mb-2 p-3"
      side={card.side === "LONG" ? "LONG" : "SHORT"} sideTestId="live-card-side"
      leverage={`${num(card.leverage)?.toString() ?? "-"}x`} leverageTestId="live-card-leverage"
      qty={`${qtyFmt(card.qty)} ${baseAssetOf(card.symbol || DEFAULT_SYMBOL)}`} qtyTestId="live-card-qty"
      /* Absent rather than wrong: a position whose opening fill is off the fetched page has no
         honest duration, and `positionRisk.updateTime` is the last change, not the open. */
      held={holdingDuration(card.opened_at_ms ?? null, nowMs)} heldTestId="live-card-held"
      top={<LiveExposure card={card} testIdPrefix="live-card" />}
      headline={<PnlHeadline pnl={parts.pnl} net={parts.net}/>}
      facts={parts.facts} breakdown={parts.breakdown}
      fold={{ toggleTestId: "live-card-detail-toggle", detailTestId: "live-card-detail" }}
      footer={
        <button type="button" className="btn-muted h-11 w-full text-sm font-bold"
          data-testid="live-card-close" disabled={busy} onClick={onClose}>
          CLOSE · 전량 청산
        </button>} />
  );
}

export function LivePerformanceSummary({ performance }: {
  performance: LivePerformance | null;
}) {
  if (!performance?.has_trades) return null;
  const date = performance.first_trade_kst_date?.replaceAll("-", ".") || "-";
  return (
    <section className="panel px-4 py-3" data-testid="live-performance-summary">
      <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
        <div className="flex min-w-0 items-baseline justify-between gap-3 sm:block">
          <dt className="whitespace-nowrap text-muted">실주문 시작</dt>
          <dd className="whitespace-nowrap font-semibold tabular-nums text-foreground">
            {date} · {performance.running_day}일째
          </dd>
        </div>
        <div className="flex min-w-0 items-baseline justify-between gap-3 sm:block">
          <dt className="whitespace-nowrap text-muted">누적 수익</dt>
          <dd className={`whitespace-nowrap text-base font-bold tabular-nums ${toneClass(performance.cumulative_net_krw)}`}
            data-testid="live-performance-cumulative">
            {signedKrw(performance.cumulative_net_krw)}
          </dd>
        </div>
        <div className="flex min-w-0 items-baseline justify-between gap-3 sm:block">
          <dt className="whitespace-nowrap text-muted">오늘 수익</dt>
          <dd className={`whitespace-nowrap text-base font-bold tabular-nums ${toneClass(performance.today_net_krw)}`}
            data-testid="live-performance-today">
            {signedKrw(performance.today_net_krw)}
          </dd>
        </div>
      </dl>
    </section>
  );
}


export function LiveAccountCards({ account }: { account: LiveAccount }) {
  const balance = account.balance;
  const commission = account.commission;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="live-account-cards">
      {/* The headline is the USDT row, which is what the Binance app shows. The USD valuation
          sits underneath so the two numbers are reconciled on screen rather than looking like
          a discrepancy. */}
      <MetricCard label="지갑 잔고 (Binance)" accent="primary"
        value={<span data-testid="live-card-wallet">{usdt(balance?.wallet_balance, 6)}</span>}
        detail={account.krw ? `≈ ${krw(account.krw.wallet_balance)}` : undefined}
        meta="실계좌 USDT" />
      <MetricCard label="주문가능"
        value={<span data-testid="live-card-available">{usdt(balance?.available_balance, 6)}</span>}
        detail={account.krw ? `≈ ${krw(account.krw.available_balance)}` : undefined}
        meta={`개시 마진 ${usdt(balance?.initial_margin, 4)}`} />
      <MetricCard label="미실현 손익"
        value={<span className={toneClass(balance?.unrealized_pnl)}>{signedUsdt(balance?.unrealized_pnl)}</span>}
        detail={account.krw ? `≈ ${signedKrw(account.krw.unrealized_pnl)}` : undefined}
        meta="Binance Mark 기준" />
      <MetricCard label="수수료율 (내 계정)"
        value={<span data-testid="live-taker">{commission ? `${(Number(commission.taker) * 100).toFixed(4)}%` : "-"}</span>}
        detail={commission ? `Maker ${(Number(commission.maker) * 100).toFixed(4)}%` : undefined}
        meta="commissionRate" />
    </div>
  );
}

/** Leverage, margin mode and what they cost in margin.
 *
 *  Leverage is a margin setting, not an edge: 0.001 BTC is 0.001 BTC at 1x and at 50x, and what
 *  changes is how much of the wallet is locked to hold it and how close the liquidation sits.
 *  The panel is laid out to say exactly that - size is chosen in the order ticket, and this
 *  panel shows the consequence.
 *
 *  Nothing here is optimistic. A button press sends the change to Binance and then re-reads
 *  `symbolConfig`; the value displayed is always the one that came back.
 */
export function LiveLeveragePanel({ account, options, onSelect, busy, error, compact = false }: {
  account: LiveAccount;
  options: LiveLeverageOptions | null;
  onSelect: (leverage: number) => void;
  busy?: boolean;
  error?: string | null;
  /** Hides the two standing explanations. They are true and they are kept - the screen renders
   *  them in the disclosure at the bottom - but a prose paragraph under a row of buttons is
   *  what made this screen read as a developer console rather than a terminal. */
  compact?: boolean;
}) {
  const config = account.symbol_config;
  const current = config ? Number(config.leverage) : null;
  const position = account.position;
  const hasPosition = Boolean(position && !position.is_flat);

  // One sentence for the whole row: every restricted step shares the same cause (the account,
  // not the number), so the tightest one is quoted and the rest are struck through.
  const restrictions = (options?.options ?? [])
    .map(value => leverageRestriction(options, value))
    .filter((item): item is NonNullable<typeof item> => item != null);
  const restrictionNote = restrictions.length
    ? restrictions.reduce((left, right) => (left.above <= right.above ? left : right)).message
    : null;

  // Both figures are Binance's own, off `positionRisk`, not arithmetic done here. This file's
  // rule is that it formats numbers and never computes them, and margin is the last place to
  // break it: `notional / leverage` ignores the maintenance tier and would disagree with the
  // exchange exactly when the position is close to trouble. With no position open there is
  // nothing to report - the order ticket's preview carries the required margin for a size the
  // operator has actually chosen.

  return (
    <div className="panel p-4" data-testid="live-leverage-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-bold tracking-wide text-foreground">레버리지 · 마진</span>
      </div>

      {/* Eight steps, and the selected one is marked by its own styling and `aria-pressed`
          alone. The row used to carry a "기본" tag on 10x and a sentence under it naming the
          same number; both said the screen would not change anything by itself, which the
          screen demonstrates by not changing anything by itself. */}
      <div className="mt-3 flex flex-wrap gap-1.5" data-testid="live-leverage-options">
        {(options?.options ?? []).map(value => {
          const restriction = leverageRestriction(options, value);
          const locked = Boolean(restriction) || account.gates.symbol_orders_enabled === false;
          return (
            <button key={value} type="button" data-testid={`live-leverage-${value}`}
              aria-pressed={value === current} disabled={busy || hasPosition || locked}
              title={restriction ? restriction.message
                : account.gates.symbol_orders_enabled === false ? "HOT 심볼 LIVE 레버리지 변경은 꺼져 있습니다." : undefined}
              onClick={() => onSelect(value)}
              className={`rounded-md border px-2.5 py-1.5 text-xs font-bold tabular-nums transition-colors
                ${value === current ? "border-danger bg-danger-soft text-danger"
                                    : "border-line text-muted hover:text-foreground"}
                ${locked ? "line-through" : ""}
                ${busy || hasPosition || locked ? "cursor-not-allowed opacity-40" : ""}`}>
              {value}x
            </button>
          );
        })}
        {!options && <span className="text-[11px] text-muted">구간표를 읽는 중입니다.</span>}
      </div>

      {hasPosition && (
        <p className="mt-2 text-[11px] text-warning" data-testid="live-leverage-locked">
          포지션 보유 중에는 레버리지를 바꾸지 않습니다. 청산 후 변경하세요.
        </p>
      )}

      {/* A step Binance has refused for this account, named once rather than per button. The
          sentence is the server's, built from Binance's own refusal, and it carries the moment
          the restriction lifts - after which the server stops reporting it and the buttons come
          back without anything being deployed. */}
      {!hasPosition && restrictionNote && (
        <p className="mt-2 text-[11px] text-warning" data-testid="live-leverage-restricted">
          {restrictionNote}
        </p>
      )}

      {!compact && <div className="mt-2">
        <LiveLeverageNotes options={options} account={account} /></div>}
      {error && <p className="mt-2 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning"
        role="status" data-testid="live-leverage-error">{error}</p>}
    </div>
  );
}

export function LiveLeverageNotes({ options, account }: {
  options: LiveLeverageOptions | null;
  /** Supplies the example quantity and its unit. Optional so the panel still renders a correct
   *  sentence before the first account read; the example then falls back to the default
   *  symbol's minimum, which is what this note always used. */
  account?: LiveAccount | null;
}) {
  const unit = liveUnit(account, options?.symbol || DEFAULT_SYMBOL);
  // The example is this instrument's own minimum order, not the literal 0.001 the sentence was
  // written with. On SOLUSDT that literal would have been a quantity Binance refuses, used to
  // explain a rule - the sentence would be teaching the reader a wrong number.
  const example = String(account?.filters?.market_min_qty ?? account?.filters?.min_qty ?? "0.001");
  return (
    <>
      <p className="text-[11px] text-muted" data-testid="live-leverage-sizing-note">
        레버리지는 노출 배수가 아니라 증거금 설정입니다. {example} {unit}는 1x에서도 50x에서도
        {" "}{example} {unit}이고, 달라지는 것은 묶이는 증거금과 청산가입니다.
        주문 크기는 주문 패널에서 따로 고릅니다.
      </p>
      <p className="mt-1 text-[11px] text-muted" data-testid="live-margin-readonly-note">
        {options?.margin_type_note || MARGIN_MODE_READONLY_NOTE}
      </p>
    </>
  );
}

 /** The quick-size row, in the same place and shape as the paper ticket's.
 *
 *  Every quantity comes from `GET /api/crypto/binance/sizing`, which walks Binance's book for
 *  each candidate and applies the local ceiling, the exchange filters and the account's margin.
 *  Nothing on this side multiplies a balance by a leverage: the affordable size depends on how
 *  deep the order walks the book, and a formula here would overstate MAX in exactly the thin
 *  book where that is most expensive.
 *
 *  A press fills the quantity box and does nothing else. It sends no order, arms nothing and
 *  changes no leverage - the order buttons below still answer to the trade gate, which is why
 *  this row stays usable while the screen is 거래불가: choosing a size is not trading.
 *
 *  `sides` is which sides an OPEN may be sent on, and it is the whole of what a held position
 *  changes here. While a position is held only its own side can be added to, so only that
 *  side's ladder is read; the other side's `REVERSE_NOT_ALLOWED` is a true answer to a question
 *  nobody is asking and used to blank all four buttons. On the server the sizes for the held
 *  side are already add-on sizes: they are computed against `availableBalance`, which is what
 *  is left after the position's own margin.
 */
export function LiveQuickSize({ sizing, onPick, busy, stale, sides,
                                unit = baseAssetOf(DEFAULT_SYMBOL) }: {
  sizing: LiveSizing | null;
  onPick: (qty: string) => void;
  /** The coin the offered sizes are in. A prop because this panel prints quantities. */
  unit?: string;
  busy?: boolean;
  stale?: boolean;
  /** Defaults to both, the flat case. */
  sides?: OrderSide[];
}) {
  const open = sides ?? (["LONG", "SHORT"] as OrderSide[]);
  const adding = open.length === 1;
  const unavailable = stale
    ? "계좌 응답이 지연돼 수량을 계산하지 않습니다."
    : sizing == null ? "주문 가능 수량을 읽는 중입니다."
    : !sizing.available ? (sizing.reject_message || "주문 가능 수량을 계산하지 못했습니다.")
    : null;

  return (
    <div data-testid="live-quick-size">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-muted">{adding ? "빠른 수량 · 추가" : "빠른 수량"}</span>
        {unavailable && <span className="text-[10px] text-warning"
          data-testid="live-quick-size-unavailable">{unavailable}</span>}
      </div>
      <div className="mb-2 grid grid-cols-4 gap-1.5 sm:mb-3" role="group" aria-label="빠른 수량">
        {LIVE_PRESET_LABELS.map(label => {
          const { qty, reason } = livePresetQty(unavailable ? null : sizing, label, open);
          const strong = label === "MAX";
          return (
            <button key={label} type="button" data-testid={`live-preset-${label}`}
              disabled={busy || qty == null}
              title={qty == null ? (unavailable || reason || undefined) : `${qty} ${unit}`}
              onClick={() => qty && onPick(qty)}
              className={`btn-compact h-9 sm:h-10 ${
                strong ? "font-extrabold tracking-wide ring-1 ring-warning/60" : ""}`}>
              {label}
            </button>
          );
        })}
      </div>
      {adding && (
        <p className="mb-2 text-[10px] text-muted" data-testid="live-quick-size-addon-note">
          보유 포지션을 유지한 상태에서 남은 주문가능 잔고로 추가 진입할 수 있는 수량입니다.
          기존 수량 합계가 아닙니다.
        </p>
      )}
    </div>
  );
}

/** The order panel. It builds a real request and shows the real refusal.
 *
 *  `gate` is the same verdict the bar at the top shows, passed in rather than recomputed, so
 *  the buttons and the badge cannot disagree. Opening is offered only while it says tradable;
 *  closing is offered whenever Binance reports a position, because an operator who needs to
 *  reduce risk should never find that control greyed out - if the window has lapsed, the press
 *  opens the activation dialog instead of sending an order that would be refused.
 */
export function LiveOrderTicket({ account, onOrder, busy, error, preview, onPreview, gate,
  onActivate, sizing }: {
  account: LiveAccount;
  onOrder: (body: { side: string; intent: "OPEN" | "CLOSE"; qty?: string }) => void;
  busy?: boolean;
  error?: string | null;
  preview?: LivePreview | null;
  onPreview?: (qty: string) => void;
  /** Defaults to the account's own gate view, so a caller that has no arm poll still fails
   *  closed on what the server said rather than opening the buttons. */
  gate?: LiveTradeGate;
  onActivate?: () => void;
  /** Server-computed quick sizes. Absent means the row renders disabled with a reason. */
  sizing?: LiveSizing | null;
}) {
  const unit = liveUnit(account);
  /** The exchange minimum, not a round number. This ticket's default is what gets sent when
   *  somebody presses LONG without touching the size box, so it is set to the smallest order
   *  Binance will accept: a mis-click then costs the minimum rather than a multiple of it.
   *
   *  Read from this symbol's own filters rather than written as "0.001". That literal is
   *  BTCUSDT's minimum; on SOLUSDT the minimum is 0.1, so the box would have opened pre-filled
   *  with a hundredth of the smallest valid order - and the refusal would arrive from Binance
   *  rather than from the screen. */
  // The smallest size this symbol can actually be ordered at, which is not the same as its
  // minimum quantity: MIN_NOTIONAL points the other way. On SOLUSDT the minimum quantity is
  // 0.01, worth ~1.2 USDT, and Binance's minimum notional is 5 - so a box pre-filled with the
  // minimum quantity opens on a size the exchange refuses, and the refusal arrives from Binance
  // rather than from the screen. The server already computes the smallest size that clears both
  // floors; it is used when the ladder has been read and the quantity minimum is the fallback.
  const smallest = sizing?.sides?.LONG?.instrument?.smallest_orderable_qty
    ?? sizing?.sides?.SHORT?.instrument?.smallest_orderable_qty;
  const minimum = String(smallest ?? account.filters?.market_min_qty
                         ?? account.filters?.min_qty ?? "0.001");
  const [size, setSize] = useState(minimum);
  // The ladder arrives after the first render, so the box is corrected once it does - but only
  // while the operator has not typed, which is what `touched` records.
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current) setSize(minimum);
  }, [minimum]);
  const [pending, setPending] = useState<{ side: string; intent: "OPEN" | "CLOSE" } | null>(null);
  const tradable = (gate ?? liveTradeGate(account, null)).tradable;
  // The HOT switch is a server setting, not something arming fixes, so the ticket says so and
  // offers no activation for it.
  const symbolOff = account.gates.symbol_orders_enabled === false;
  const position = account.position;
  const hasPosition = Boolean(position && !position.is_flat);
  /** The router's own rule, read off Binance's reported position: a held side may be added to
   *  and the other side is refused. The backend refuses it again - `_open_plan` raises
   *  `REVERSE_NOT_ALLOWED` after re-reading `positionRisk` - so this disables a button rather
   *  than being the only thing standing between a click and a reverse. */
  const allowance = liveSideAllowance(position);
  /** The held side adds; the other side reduces the held position by the typed size, sent
   *  reduceOnly by the server. A reduce larger than the position would be a flip, which the LIVE
   *  route refuses, so the button says so instead of sending it. */
  const reducing = (side: OrderSide) => hasPosition && !allowance.allowed.includes(side);
  const heldQty = Number(position?.qty ?? 0);
  const reduceTooLarge = (side: OrderSide) => reducing(side) && !(Number(size) > 0 && Number(size) <= heldQty);

  useEffect(() => { onPreview?.(size); }, [size, onPreview]);

  const confirm = () => {
    if (!pending) return;
    onOrder({ ...pending, qty: pending.intent === "OPEN" ? size : undefined });
    setPending(null);
  };

  return (
    <div className="panel p-4" data-testid="live-order-ticket"
      data-preview-fetched-at={preview?.fetched_at_ms ?? undefined}>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold sm:text-base">수동 주문</h2>
        <span className="text-[11px] font-bold tracking-wide text-danger">실계좌</span>
      </div>
      {/* A held position is reported here, not hidden, because the size box below is now an
          add-on size and the operator needs the base it is being added to. */}
      {hasPosition && (
        <p className="mb-2 rounded-md bg-surface-alt px-2.5 py-1.5 text-[11px] text-foreground-secondary"
          data-testid="live-ticket-holding">
          보유 <span className="font-bold">{allowance.holding}</span>{" "}
          <span className="tabular-nums">{qtyFmt(position?.qty)} {unit}</span> · 같은 방향 추가 진입만 가능합니다.
        </p>
      )}
      <LiveQuickSize unit={unit} sizing={sizing ?? null} busy={busy} stale={account.stale}
        sides={allowance.allowed} onPick={next => { touched.current = true; setSize(next); }} />
      <label className="block text-[11px] text-muted" htmlFor="live-qty">
        {hasPosition ? `추가 수량 (${unit})` : `수량 (${unit})`}
      </label>
      {/* Never disabled because a position exists. A position means one side is unavailable,
          not that no size can be chosen. */}
      <input id="live-qty" data-testid="live-qty-input" value={size} inputMode="decimal"
        onChange={event => { touched.current = true; setSize(event.target.value); }}
        className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm tabular-nums" />
      {/* The pre-trade preview (fees, entry cost, break-even) is still requested on every size
          change and kept on the ticket's data attribute; the panel shows size and KRW notional only. */}
      <div className="mt-3">
        <OrderAmountSummary qty={size}
          price={account.position?.mark_price ?? account.mark?.mark_price ?? null}
          leverage={account.symbol_config?.leverage != null ? String(account.symbol_config.leverage) : null}
          takerRate={account.commission?.taker ?? null} krwPerUsdt={account.krw_per_usdt}
          unit={unit} testId="live-order-amount" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        {(["LONG", "SHORT"] as OrderSide[]).map(side => {
          const reduce = reducing(side);
          const tooLarge = reduceTooLarge(side);
          const long = side === "LONG";
          return (
            <button key={side} type="button" data-testid={`live-${side.toLowerCase()}`}
              disabled={busy || !tradable || tooLarge}
              title={tooLarge ? `감소 수량은 보유 ${qtyFmt(position?.qty)} ${unit} 이하여야 합니다.`
                : tradable ? undefined : "거래불가 상태입니다. 먼저 거래를 활성화하세요."}
              onClick={() => setPending({ side, intent: "OPEN" })}
              className={`rounded-md px-3 py-2 text-sm font-bold disabled:opacity-40
                ${long ? "bg-success-soft text-success" : "bg-danger-soft text-danger"}`}>
              {side}{reduce ? " · 감소" : hasPosition ? " 추가" : ""}
            </button>
          );
        })}
      </div>
      {allowance.blocked && (
        <p className="mt-1.5 text-[11px] text-muted" data-testid="live-opposite-blocked">
          {allowance.blocked}은 보유 {allowance.holding} 포지션을 입력 수량만큼 줄입니다(reduceOnly).
          보유량보다 큰 반대 주문(전환)은 LIVE에서 보내지 않습니다.
        </p>
      )}
      {/* CLOSE keeps working off Binance's reported position, and the backend still re-reads the
          real size and sends it reduceOnly. Nothing about that changes here. */}
      <button type="button" data-testid="live-close" disabled={busy || !hasPosition || symbolOff}
        onClick={() => (tradable ? setPending({ side: position?.side || "LONG", intent: "CLOSE" })
                                 : onActivate?.())}
        className="mt-2 w-full rounded-md border border-line px-3 py-2 text-sm font-bold text-foreground disabled:opacity-40">
        CLOSE {hasPosition ? `· ${qtyFmt(position?.qty)} ${unit}` : ""}
      </button>
      {symbolOff && (
        <p className="mt-2 text-[11px] text-muted" data-testid="live-hot-orders-off">
          HOT 심볼 LIVE 주문은 꺼져 있습니다. 조회만 가능합니다.
        </p>
      )}
      {!tradable && !symbolOff && (
        <p className="mt-2 text-[11px] text-muted" data-testid="live-order-locked">
          거래불가 상태입니다.{" "}
          {onActivate && (
            <button type="button" data-testid="live-order-activate" onClick={onActivate}
              className="font-semibold text-danger underline underline-offset-2">거래 활성화</button>
          )}{onActivate ? " 후 주문할 수 있습니다." : " 상단 배지를 눌러 사유를 확인하세요."}
        </p>
      )}
      {pending && (
        <div className="mt-3 rounded-md border border-danger bg-danger-soft p-3" data-testid="live-confirm">
          <p className="text-xs font-bold text-danger">BINANCE LIVE · 실계좌</p>
          <p className="mt-1 text-sm font-semibold text-foreground">
            {pending.intent === "CLOSE"
              ? `${pending.side} 전량 청산 (${qtyFmt(position?.qty)} ${unit})`
              : reducing(pending.side as OrderSide)
                ? `${allowance.holding} ${Number(size) >= heldQty ? "전량" : "부분"} 청산 ${size} ${unit} (보유 ${qtyFmt(position?.qty)} ${unit})`
              : hasPosition
                ? `${pending.side} 추가 진입 ${size} ${unit} (보유 ${qtyFmt(position?.qty)} ${unit})`
                : `${pending.side} ${size} ${unit}`}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            {(() => {
              const label = liveLeverageLabel(account.symbol_config?.leverage,
                pending.intent === "OPEN" ? sizing?.sides?.[pending.side as OrderSide] : null);
              return label ? <span data-testid="live-confirm-leverage">{label} · </span> : null;
            })()}
            {LIVE_LOCK_NOTE}
          </p>
          <div className="mt-2 flex gap-2">
            <button type="button" className="btn-muted px-3 py-1.5 text-xs" data-testid="live-cancel"
              onClick={() => setPending(null)}>취소</button>
            <button type="button" data-testid="live-submit" disabled={busy}
              className="rounded-md bg-danger px-3 py-1.5 text-xs font-bold text-white disabled:opacity-40"
              onClick={confirm}>실주문</button>
          </div>
        </div>
      )}
      {error && <p className="mt-3 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning"
        role="status" data-testid="live-order-error">{error}</p>}
    </div>
  );
}

export function LiveAutoExit({ guard, onSave, onDisable, busy, error }: {
  guard: LiveExitGuard | null;
  onSave: (takeProfit: string, stopLoss: string) => void;
  onDisable: () => void;
  busy?: boolean;
  error?: string | null;
}) {
  // The guard names the symbol it watches; the panel prints that symbol's unit rather than the
  // selected tab's, because the two can differ while AUTO stays single-symbol.
  const guardUnit = baseAssetOf((guard as { symbol?: string } | null)?.symbol || DEFAULT_SYMBOL);
  const [takeProfit, setTakeProfit] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [editing, setEditing] = useState(false);
  const active = Boolean(guard?.enabled);

  if (active && !editing) {
    return (
      <section className="panel p-4" data-testid="live-auto-exit">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">자동청산</h2>
          <span className="text-xs font-bold text-success">ON</span>
        </div>
        {/* A same-side add-on does not invalidate the guard: the thresholds are amounts of
            money, the identity it matches on is the side and the fill that opened the cycle
            (both unchanged by an add-on), and the CLOSE it eventually sends re-reads the real
            position and sends it `reduceOnly`, so it can only ever flatten what is actually
            there. What does change is how far price has to move to reach the same amount, and
            the operator typed those amounts against the smaller size, so the change is
            reported instead of the guard being silently switched off - switching it off would
            leave a position its owner believes is protected with no protection at all. */}
        {guard?.scaled_in && (
          <p className="mt-2 rounded-md bg-warning-soft px-3 py-2 text-[11px] text-warning"
            role="status" data-testid="exit-scaled-in">
            추가 진입으로 수량이 {guard.configured_qty} → {guard.position_qty} {guardUnit}로 늘었습니다.
            목표 금액은 설정 당시 수량 기준이니 확인하세요.
          </p>
        )}
        <dl className="mt-3 space-y-1 text-xs">
          <div className="flex justify-between gap-2"><dt className="text-muted">현재 순손익</dt>
            <dd className={toneClass(guard?.current_net_krw)}
              data-testid="exit-current-net">{signedKrw(guard?.current_net_krw)}</dd></div>
          <div className="flex justify-between gap-2"><dt className="text-muted">대상 수량</dt>
            <dd className="tabular-nums text-foreground-secondary"
              data-testid="exit-position-qty">{guard?.position_qty ?? "-"} {guardUnit}</dd></div>
          <div className="flex justify-between gap-2"><dt className="text-muted">익절</dt>
            <dd className="tabular-nums text-success">+{krw(guard?.take_profit_krw)}</dd></div>
          <div className="flex justify-between gap-2"><dt className="text-muted">손절</dt>
            <dd className="tabular-nums text-danger">-{krw(guard?.stop_loss_krw)}</dd></div>
        </dl>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button className="btn-muted h-9 text-xs" type="button"
            onClick={() => { setTakeProfit(guard?.take_profit_krw || ""); setStopLoss(guard?.stop_loss_krw || ""); setEditing(true); }}>
            설정 변경
          </button>
          <button className="btn-muted h-9 text-xs" type="button" disabled={busy}
            onClick={onDisable}>끄기</button>
        </div>
      </section>
    );
  }

  return (
    <section className="panel p-4" data-testid="live-auto-exit">
      <h2 className="text-sm font-semibold">자동청산</h2>
      <div className="mt-3 grid grid-cols-[auto_1fr_auto] items-center gap-2 text-xs">
        <label htmlFor="take-profit-krw">익절</label>
        <input id="take-profit-krw" inputMode="numeric" value={takeProfit}
          onChange={event => setTakeProfit(event.target.value.replace(/[^0-9]/g, ""))}
          className="min-w-0 rounded-md border border-line bg-surface px-3 py-2 text-right tabular-nums" />
        <span className="text-muted">원</span>
        <label htmlFor="stop-loss-krw">손절</label>
        <input id="stop-loss-krw" inputMode="numeric" value={stopLoss}
          onChange={event => setStopLoss(event.target.value.replace(/[^0-9]/g, ""))}
          className="min-w-0 rounded-md border border-line bg-surface px-3 py-2 text-right tabular-nums" />
        <span className="text-muted">원</span>
      </div>
      <button type="button" className="btn-muted mt-3 h-10 w-full text-sm font-bold"
        disabled={busy || !takeProfit || !stopLoss}
        onClick={() => { onSave(takeProfit, stopLoss); setEditing(false); }}>
        자동청산 켜기
      </button>
      {error && <p className="mt-2 text-xs text-warning" role="status">{error}</p>}
    </section>
  );
}


/** Status once, then the account on a timer. The status answer is what decides whether the
 *  switch may be used at all, so it is fetched even while the screen is on PAPER. */
/** The LIVE account for one symbol, polled.
 *
 *  `symbol` is the second argument and everything in here is about it. Two properties make the
 *  symbol safe rather than decorative, and both are needed:
 *
 *  1. **State is cleared the instant the symbol changes**, synchronously, before any fetch for
 *     the new symbol has returned. Without this the screen keeps rendering the previous
 *     symbol's balance, position, ladder and PnL under the new tab for one poll interval -
 *     every number wrong, every label right, which is the worst possible version of this bug.
 *
 *  2. **Every response is checked against the symbol that is current when it arrives**, and
 *     discarded if it does not match. Clearing alone is not enough: a request for BTCUSDT
 *     issued before the tab changed can resolve after it, and `setAccount` would then put BTC's
 *     position on the ETH screen. `AbortSignal` would not cover it either, because the stale
 *     response may already be in flight past the point abort takes effect.
 *
 *  The guard is on the symbol the response *claims*, not on which request was made, so a server
 *  that answered about the wrong instrument is also caught.
 */
export function useBinanceLive(enabled: boolean, symbol: string = DEFAULT_SYMBOL,
                               pollMs = LIVE_POLL_MS) {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [account, setAccount] = useState<LiveAccount | null>(null);
  const [preview, setPreview] = useState<LivePreview | null>(null);
  const [sizing, setSizing] = useState<LiveSizing | null>(null);
  const [positionCard, setPositionCard] = useState<LivePositionCardData | null>(null);
  const [exitGuard, setExitGuard] = useState<LiveExitGuard | null>(null);
  const [performance, setPerformance] = useState<LivePerformance | null>(null);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [exitGuardError, setExitGuardError] = useState<string | null>(null);
  const [arm, setArm] = useState<LiveArmState | null>(null);
  const [leverage, setLeverage] = useState<LiveLeverageOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [armError, setArmError] = useState<string | null>(null);
  const [leverageError, setLeverageError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const previewQty = useRef<string>("");
  /** The symbol every in-flight request must still be about when it resolves. Updated in the
   *  same layout pass that clears the state, so there is no window in which the ref and the
   *  rendered state disagree. */
  const current = useRef(symbol);

  /** Drop everything about the previous instrument, synchronously.
   *
   *  `useLayoutEffect` rather than `useEffect`: the cleared state must be what the browser
   *  paints, not what it paints one frame later. With `useEffect` the first paint after a tab
   *  click still carries the old symbol's figures.
   */
  useLayoutEffect(() => {
    current.current = symbol;
    setAccount(null);
    setPreview(null);
    setSizing(null);
    setPositionCard(null);
    setExitGuard(null);
    setPerformance(null);
    setExecutions([]);
    setLeverage(null);
    setError(null);
    setActionError(null);
    setLeverageError(null);
    setExitGuardError(null);
    previewQty.current = "";
    // `arm` and `status.available` are account-wide, not per symbol, so they are deliberately
    // kept: clearing them would flash "거래불가" on every tab change for no reason.
  }, [symbol]);

  /** True when `value` is still the symbol on screen.
   *
   *  A response with no symbol field at all passes: some routes legitimately answer without one
   *  (the arm state, an error body), and rejecting those would make the screen go blank. Only a
   *  response that *names a different* symbol is dropped.
   */
  const mine = useCallback((value?: string | null) =>
    value == null || value === "" || value === current.current, []);

  useEffect(() => {
    let cancelled = false;
    liveApi.status(symbol)
      .then(next => { if (!cancelled && mine(next.symbol)) setStatus(next); })
      .catch((exc: CryptoApiError) => { if (!cancelled) setError(exc.message); });
    return () => { cancelled = true; };
  }, [symbol, mine]);

  const refresh = useCallback(async () => {
    const asked = current.current;
    try {
      const next = await liveApi.account(asked);
      if (mine(next.symbol) && asked === current.current) {
        setAccount(next);
        setError(null);
      }
    } catch (exc) {
      if (asked === current.current) {
        setError(exc instanceof CryptoApiError ? exc.message : String(exc));
      }
    }
    // Polled with the account rather than on its own timer. The arm window is a countdown the
    // server owns, and a screen that showed "3분 남음" from a stale read would be claiming a
    // safety property it had not checked. Account-wide, so it is not symbol-guarded.
    try {
      setArm(await liveApi.armState());
    } catch {
      setArm(null);
    }
  }, [mine]);

  /** The ladder costs one depth read, so it runs on its own slower timer rather than with the
   *  2s account poll. It is a read: nothing on the trade path is touched. */
  const refreshSizing = useCallback(async () => {
    const asked = current.current;
    try {
      const next = await liveApi.sizing(asked);
      if (mine(next.symbol) && asked === current.current) setSizing(next);
    } catch {
      if (asked === current.current) setSizing(null);
    }
  }, [mine]);

  /** The card costs three extra reads, so it runs on the ladder's slower timer. The server
   *  short-circuits on a flat account, which is the common case. */
  const refreshPositionCard = useCallback(async () => {
    const asked = current.current;
    const still = () => asked === current.current;
    try {
      const next = await liveApi.positionCard(asked);
      if (mine(next.symbol) && still()) setPositionCard(next);
    } catch {
      if (still()) setPositionCard(null);
    }
    try {
      const guard = await liveApi.exitGuard(asked);
      if (still()) setExitGuard(guard);
    } catch { if (still()) setExitGuard(null); }
    try {
      const perf = await liveApi.performance(asked);
      if (mine((perf as { symbol?: string }).symbol) && still()) setPerformance(perf);
    } catch { if (still()) setPerformance(null); }
  }, [mine]);

  const refreshLeverage = useCallback(async () => {
    const asked = current.current;
    try {
      const next = await liveApi.leverageOptions(asked);
      if (mine(next.symbol) && asked === current.current) {
        setLeverage(next);
        setLeverageError(null);
      }
    } catch (exc) {
      if (asked === current.current) {
        setLeverageError(exc instanceof CryptoApiError ? exc.message : String(exc));
      }
    }
  }, [mine]);

  // Every LIVE read below is a signed Binance request, so none of them runs while the tab is
  // hidden; coming back reads at once (`visiblePoll`).
  useEffect(() => {
    if (!enabled) return;
    return visiblePoll(refresh, pollMs);
  }, [enabled, pollMs, refresh, symbol]);

  // The bracket table changes with the account's risk tier and with the symbol, not with the
  // tick, so it is read when LIVE is entered, when the symbol changes, and after a leverage
  // change rather than on the poll.
  useEffect(() => { if (enabled) void refreshLeverage(); }, [enabled, refreshLeverage, symbol]);

  useEffect(() => {
    if (!enabled) return;
    return visiblePoll(() => Promise.all([refreshSizing(), refreshPositionCard()]), SIZING_POLL_MS);
  }, [enabled, refreshSizing, refreshPositionCard, symbol]);

  /** This account's fills on this symbol, for the chart's markers. Read from the server's audit
   *  mirror (a local file), never from a signed Binance call, and only when something can have
   *  changed: entering LIVE, a symbol change, the position changing, an order. No poll. */
  const refreshExecutions = useCallback(async () => {
    const asked = current.current;
    try {
      const next = await liveApi.executions(asked);
      if (mine(next.symbol) && asked === current.current) {
        setExecutions(executionsFor(next.executions, { mode: "LIVE", symbol: asked, account: next.account }));
      }
    } catch { /* markers are display only; the position lines still come from Binance */ }
  }, [mine]);
  const positionKey = `${account?.symbol ?? ""}:${account?.position?.side ?? "FLAT"}:${account?.position?.qty ?? "0"}`;
  useEffect(() => { if (enabled) void refreshExecutions(); },
    [enabled, refreshExecutions, symbol, positionKey]);

  const requestPreview = useCallback((size: string) => {
    if (!enabled || !size) return;
    const asked = current.current;
    previewQty.current = size;
    liveApi.preview(asked, { qty: size })
      .then(next => {
        if (previewQty.current === size && mine(next.symbol) && asked === current.current) {
          setPreview(next);
        }
      })
      .catch(() => { if (asked === current.current) setPreview(null); });
  }, [enabled, mine]);

  const order = useCallback(async (body: { side: string; intent: "OPEN" | "CLOSE"; qty?: string }) => {
    setBusy(true);
    setActionError(null);
    try {
      await liveApi.order(current.current, body);
      await refresh();
      await refreshPositionCard();
      await refreshExecutions();
    } catch (exc) {
      // The expected V1 path: the backend refuses. The message is the operator's evidence that
      // the lock is real, so it is shown rather than swallowed.
      setActionError(exc instanceof CryptoApiError
        ? `${liveBlockerLabel(exc.code)} · ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshPositionCard, refreshExecutions]);

  /** The confirmation phrase is supplied here, not typed by the operator.
   *
   *  The server still requires it verbatim and still refuses without it - that check is the
   *  one that stops a stray POST from arming an account, and it has not moved. What it was
   *  never able to do is establish intent, which is now the dialog's job. */
  const armLive = useCallback(async (note?: string) => {
    setBusy(true);
    setArmError(null);
    try {
      setArm(await liveApi.arm({ confirmation: ARM_CONFIRMATION, note }));
    } catch (exc) {
      setArmError(exc instanceof CryptoApiError
        ? `${liveBlockerLabel(exc.code)} · ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
      await refresh();
    }
  }, [refresh]);

  const disarmLive = useCallback(async () => {
    setBusy(true);
    setArmError(null);
    try {
      setArm(await liveApi.disarm());
    } catch (exc) {
      setArmError(exc instanceof CryptoApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
      await refresh();
    }
  }, [refresh]);

  /** Sends the change and then re-reads. Nothing is applied to the screen optimistically: the
   *  leverage shown after this resolves is the one Binance reported back. */
  const changeLeverage = useCallback(async (value: number) => {
    setBusy(true);
    setLeverageError(null);
    try {
      await liveApi.setLeverage(current.current, value);
      previewQty.current = "";
      setPreview(null);
      await refresh();
      await refreshLeverage();
      await refreshSizing();
    } catch (exc) {
      setLeverageError(exc instanceof CryptoApiError
        ? `${liveBlockerLabel(exc.code)} · ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshLeverage, refreshSizing]);

  const saveExitGuard = useCallback(async (takeProfit: string, stopLoss: string) => {
    setBusy(true); setExitGuardError(null);
    try { setExitGuard(await liveApi.setExitGuard(current.current, takeProfit, stopLoss)); }
    catch (exc) { setExitGuardError(exc instanceof CryptoApiError ? exc.message : String(exc)); }
    finally { setBusy(false); }
  }, []);

  const disableExitGuard = useCallback(async () => {
    setBusy(true); setExitGuardError(null);
    try { setExitGuard(await liveApi.disableExitGuard()); }
    catch (exc) { setExitGuardError(exc instanceof CryptoApiError ? exc.message : String(exc)); }
    finally { setBusy(false); }
  }, []);

  return { symbol, status, account, preview, arm, leverage, sizing, positionCard, exitGuard, performance,
           executions,
           error, actionError, armError, leverageError, exitGuardError, busy, refresh, order,
           requestPreview, armLive, disarmLive, changeLeverage, refreshSizing, refreshPositionCard,
           saveExitGuard, disableExitGuard,
           available: Boolean(status?.available),
           /** What the server permits, never the client's own list. */
           symbols: status?.symbols ?? null,
           /** The unit a quantity is in: Binance's `baseAsset` when the filters were read. */
           baseAsset: account?.base_asset ?? status?.base_asset ?? baseAssetOf(symbol) };
}
