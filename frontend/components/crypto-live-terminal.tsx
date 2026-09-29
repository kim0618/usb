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
import { useCallback, useEffect, useRef, useState } from "react";
import { MetricCard, StatusBadge } from "@/components/ui";
import { CryptoApiError, krw, num, price, qty as qtyFmt, signedKrw, signedUsdt, toneClass, usdt }
  from "@/lib/crypto-paper";
import type { OrderSide } from "@/lib/crypto-paper";
import {
  AccountSource, LIVE_AUTHORITY_NOTE, LIVE_LOCK_NOTE, LiveAccount, LiveBlocker, LivePreview,
  LiveStatus, MARGIN_MODE_LABELS, liveApi, liveBlockerLabel,
} from "@/lib/crypto-live";

export const LIVE_POLL_MS = 2_000;
/** Older than this and the panel says so instead of presenting the figures as current. */
export const LIVE_STALE_MS = 15_000;

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

export function LiveBadge({ account }: { account: LiveAccount | null }) {
  /** The badge reports the *account*, not the poll. An unreadable account showed "synced 0s ago"
   *  in the healthy colour because the snapshot object itself was fresh, which read as a working
   *  connection on a screen that had just refused to show a balance. Readiness comes first. */
  const connected = Boolean(account?.ready);
  const stale = account?.stale ?? false;
  const [tone, label] = !account ? ["REJECTED", "연결 대기"]
    : !connected ? ["REJECTED", "연결 안 됨"]
    : stale ? ["WARNING", "응답 지연"]
    : ["CONNECTED", `동기화 ${Math.round(account.age_ms / 1000)}초 전`];
  return (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger bg-danger-soft px-3 py-2"
      data-testid="live-badge" role="status">
      <div className="flex items-center gap-2">
        <span className="text-sm font-bold tracking-wide text-danger">BINANCE LIVE</span>
        <span className="text-xs font-semibold text-danger">실계좌</span>
      </div>
      <div className="flex items-center gap-2">
        <StatusBadge value={tone} label={label} />
        <span className="text-[11px] text-danger">{LIVE_LOCK_NOTE}</span>
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
      <p className="mt-1 text-[10px] text-muted" data-testid="live-authority-note">
        {LIVE_AUTHORITY_NOTE}
        {balance && (
          <span data-testid="live-usd-valuation">
            {" "}잔고는 Binance USDT 잔고({usdt(balance.wallet_balance, 6)})입니다.
            계정 USD 환산은 {usdt(balance.account_wallet_usd, 6).replace("USDT", "USD")}이며
            페그에 따라 조금씩 움직입니다.
          </span>
        )}
      </p>
    </header>
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
      <span className="text-[10px] text-muted" data-testid="live-chart-source-note">
        아래 차트는 Bybit 공개 시세입니다. 주문·손익·미리보기는 위 Binance 값 기준입니다.
      </span>
    </div>
  );
}

export function LivePositionPanel({ account }: { account: LiveAccount }) {
  const position = account.position;
  if (!position || position.is_flat) {
    return <div className="panel p-5 text-sm text-muted" data-testid="live-position-panel">
      Binance 실계좌에 보유 포지션이 없습니다.
    </div>;
  }
  const long = position.side === "LONG";
  const rows: [string, string][] = [
    ["수량", `${qtyFmt(position.qty)} BTC`],
    ["진입가", price(position.entry_price)],
    ["Mark", price(position.mark_price ?? account.mark?.mark_price ?? null)],
    ["청산가", price(position.liquidation_price)],
    ["명목", usdt(position.notional)],
    ["레버리지", account.symbol_config ? `${num(account.symbol_config.leverage)}x` : "-"],
    ["마진 모드", account.symbol_config
      ? (MARGIN_MODE_LABELS[account.symbol_config.margin_type] || account.symbol_config.margin_type) : "-"],
    ["유지 마진", usdt(position.maint_margin, 4)],
  ];
  return (
    <div className="panel p-5" data-testid="live-position-panel">
      <div className="mb-4 flex items-center justify-between">
        <span className={`rounded-md px-2 py-1 text-xs font-bold ${long ? "bg-success-soft text-success" : "bg-danger-soft text-danger"}`}
          data-testid="live-position-side">{long ? "LONG" : "SHORT"}</span>
        <span className={`text-lg font-bold tabular-nums ${toneClass(position.unrealized_pnl)}`}
          data-testid="live-position-pnl">
          {signedUsdt(position.unrealized_pnl)}
          {account.position_krw && <span className="ml-2 text-xs font-medium">
            {signedKrw(account.position_krw.unrealized_pnl)}</span>}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[10px] text-muted">{label}</dt>
            <dd className="text-sm font-semibold tabular-nums text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
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

/** The order panel. It builds a real request and shows the real refusal. */
export function LiveOrderTicket({ account, onOrder, busy, error, preview, onPreview }: {
  account: LiveAccount;
  onOrder: (body: { side: string; intent: "OPEN" | "CLOSE"; qty?: string }) => void;
  busy?: boolean;
  error?: string | null;
  preview?: LivePreview | null;
  onPreview?: (qty: string) => void;
}) {
  const [size, setSize] = useState("0.002");
  const [pending, setPending] = useState<{ side: string; intent: "OPEN" | "CLOSE" } | null>(null);
  const armed = account.gates.armed;
  const position = account.position;
  const hasPosition = Boolean(position && !position.is_flat);

  useEffect(() => { onPreview?.(size); }, [size, onPreview]);

  const confirm = () => {
    if (!pending) return;
    onOrder({ ...pending, qty: pending.intent === "OPEN" ? size : undefined });
    setPending(null);
  };

  return (
    <div className="panel p-4" data-testid="live-order-ticket">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs font-bold tracking-wide text-danger">BINANCE LIVE 주문</p>
        <StatusBadge value={armed ? "WARNING" : "REJECTED"}
          label={armed ? "실주문 활성" : "실주문 잠금"} />
      </div>
      <label className="block text-[11px] text-muted" htmlFor="live-qty">수량 (BTC)</label>
      <input id="live-qty" data-testid="live-qty-input" value={size} inputMode="decimal"
        onChange={event => setSize(event.target.value)}
        className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm tabular-nums" />
      {preview?.sides && (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-[11px]" data-testid="live-preview">
          {(["LONG", "SHORT"] as OrderSide[]).map(side => {
            const row = preview.sides[side];
            if (!row) return null;
            return (
              <div key={side} className="rounded-md border border-line p-2">
                <dt className="font-semibold text-foreground">{side}</dt>
                {row.feasible ? (
                  <dd className="mt-1 space-y-0.5 tabular-nums text-muted">
                    <p>예상 진입 {price(row.entry_fill_price)}</p>
                    <p>왕복 비용 {usdt(row.round_trip_cost, 4)}</p>
                    <p>손익분기 {price(row.breakeven_mark_price)}</p>
                  </dd>
                ) : (
                  <dd className="mt-1 text-danger">{row.reject_message || row.reject_code}</dd>
                )}
              </div>
            );
          })}
        </dl>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button type="button" data-testid="live-long" disabled={busy}
          onClick={() => setPending({ side: "LONG", intent: "OPEN" })}
          className="rounded-md bg-success-soft px-3 py-2 text-sm font-bold text-success disabled:opacity-40">LONG</button>
        <button type="button" data-testid="live-short" disabled={busy}
          onClick={() => setPending({ side: "SHORT", intent: "OPEN" })}
          className="rounded-md bg-danger-soft px-3 py-2 text-sm font-bold text-danger disabled:opacity-40">SHORT</button>
      </div>
      <button type="button" data-testid="live-close" disabled={busy || !hasPosition}
        onClick={() => setPending({ side: position?.side || "LONG", intent: "CLOSE" })}
        className="mt-2 w-full rounded-md border border-line px-3 py-2 text-sm font-bold text-foreground disabled:opacity-40">
        CLOSE {hasPosition ? `· ${qtyFmt(position?.qty)} BTC` : ""}
      </button>
      {pending && (
        <div className="mt-3 rounded-md border border-danger bg-danger-soft p-3" data-testid="live-confirm">
          <p className="text-xs font-bold text-danger">BINANCE LIVE · 실계좌</p>
          <p className="mt-1 text-sm font-semibold text-foreground">
            {pending.intent === "CLOSE"
              ? `${pending.side} 전량 청산 (${qtyFmt(position?.qty)} BTC)`
              : `${pending.side} ${size} BTC`}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            {account.symbol_config ? `${num(account.symbol_config.leverage)}x · ` : ""}
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

/** Status once, then the account on a timer. The status answer is what decides whether the
 *  switch may be used at all, so it is fetched even while the screen is on PAPER. */
export function useBinanceLive(enabled: boolean, pollMs = LIVE_POLL_MS) {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [account, setAccount] = useState<LiveAccount | null>(null);
  const [preview, setPreview] = useState<LivePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const previewQty = useRef<string>("");

  useEffect(() => {
    let cancelled = false;
    liveApi.status()
      .then(next => { if (!cancelled) setStatus(next); })
      .catch((exc: CryptoApiError) => { if (!cancelled) setError(exc.message); });
    return () => { cancelled = true; };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await liveApi.account();
      setAccount(next);
      setError(null);
    } catch (exc) {
      setError(exc instanceof CryptoApiError ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = setInterval(() => { void refresh(); }, pollMs);
    return () => clearInterval(timer);
  }, [enabled, pollMs, refresh]);

  const requestPreview = useCallback((size: string) => {
    if (!enabled || !size) return;
    previewQty.current = size;
    liveApi.preview({ qty: size })
      .then(next => { if (previewQty.current === size) setPreview(next); })
      .catch(() => setPreview(null));
  }, [enabled]);

  const order = useCallback(async (body: { side: string; intent: "OPEN" | "CLOSE"; qty?: string }) => {
    setBusy(true);
    setActionError(null);
    try {
      await liveApi.order(body);
      await refresh();
    } catch (exc) {
      // The expected V1 path: the backend refuses. The message is the operator's evidence that
      // the lock is real, so it is shown rather than swallowed.
      setActionError(exc instanceof CryptoApiError
        ? `${liveBlockerLabel(exc.code)} · ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return { status, account, preview, error, actionError, busy, refresh, order, requestPreview,
           available: Boolean(status?.available) };
}
