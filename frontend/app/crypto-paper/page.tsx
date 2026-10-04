"use client";

import { useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/ui";
import {
  LedgerTable, OrderTicket, PerformancePanel, RunFooter, SourceBanner, TradeHistory,
  useCryptoTerminal, useLivePnl,
} from "@/components/crypto-paper-terminal";
import {
  AutoNote, ChartSection, Disclosure, MarketHeader, MobilePositionCard, PositionStrip,
  useC1Signals,
} from "@/components/crypto-terminal-layout";
import {
  AccountSourceSwitch, LiveAccountCards, LiveActivateDialog, LiveAuthorityNote, LiveBlockedPanel,
  LiveAutoExit, LiveBookStrip, LiveLeverageNotes, LiveLeveragePanel, LiveMarketHeader, LiveOrderTicket,
  LivePerformanceSummary, LivePositionCard, LivePositionPanel, LiveTradeBar, useBinanceLive,
} from "@/components/crypto-live-terminal";
import { C1AutoControl } from "@/components/crypto-c1-auto-control";
import {
  OpenPositionsStrip, SymbolTabs, useOpenPositions, useSelectedSymbol,
} from "@/components/crypto-symbol-tabs";
import { DEFAULT_SYMBOL } from "@/lib/crypto-symbols";
import type { ChartTimeframe } from "@/lib/crypto-paper";
import { positionOpenedMs } from "@/lib/crypto-paper";
import { ARM_NOTE, LIVE_CHART_SOURCE_NOTE, LIVE_LOCK_NOTE, liveOverlays, liveTradeGate }
  from "@/lib/crypto-live";
import type { AccountSource } from "@/lib/crypto-live";

/** US-B CRYPTO manual futures terminal. Bybit PUBLIC market data, simulated fills, no account.
 *
 *  Ordered for a phone held in one hand: price and equity, then the chart, then the order
 *  controls, then the position. Everything that documents the run rather than driving a decision
 *  is behind a disclosure at the bottom. On a wide screen the same blocks become two columns so
 *  the chart and the order panel are side by side and both above the fold.
 */
/** Tailwind's `xl` breakpoint. The page renders the order ticket twice (phone and wide trees);
 *  only the one on screen may fetch its preview. */
function useIsWide() {
  const [wide, setWide] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(min-width: 1280px)");
    const update = () => setWide(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return wide;
}

export default function CryptoPaperPage() {
  /** PAPER unless the operator switches, and the switch is only offered when the backend says a
   *  Binance key is configured. The two accounts never render at the same time. */
  const [source, setSource] = useState<AccountSource>("PAPER");
  /** What the server permits, learned from `/status` and remembered across the PAPER/LIVE
   *  switch so the tabs do not flicker back to the client's own list. `null` until the first
   *  status answers, which is what `useSelectedSymbol` treats as "no opinion yet". */
  const [permitted, setPermitted] = useState<readonly string[] | null>(null);
  /** The one piece of state both trees read.
   *
   *  Held here rather than inside either tree, which is what makes the selection survive the
   *  PAPER/LIVE switch for free: switching `source` re-renders this component's children and
   *  leaves this state alone. Reload survival is `localStorage`, inside the hook.
   */
  const { symbol, select: selectSymbol } = useSelectedSymbol(permitted);
  const terminal = useCryptoTerminal(symbol);
  const [timeframe, setTimeframe] = useState<ChartTimeframe>("1m");
  /** C1 is a BTCUSDT research result and stays on BTCUSDT.
   *
   *  The engine is only polled on the default symbol, so an ETH or SOL screen draws no marker
   *  and runs no signal poll - not a hidden marker, no request at all. Presenting a BTC signal
   *  as another instrument's would be the research leaking into a screen it says nothing about.
   */
  const c1Enabled = symbol === DEFAULT_SYMBOL;
  const signals = useC1Signals(c1Enabled);
  const wide = useIsWide();
  const live = useLivePnl(terminal.state?.account?.position_side != null, symbol);
  const binance = useBinanceLive(source === "BINANCE_LIVE", symbol);
  /** Every symbol's open position, polled while LIVE is on screen. */
  const positions = useOpenPositions(source === "BINANCE_LIVE");
  /** Raised by the bar and by a CLOSE pressed after the window lapsed. One dialog for both, so
   *  the sentence the operator has to agree to is written once. */
  const [activating, setActivating] = useState(false);

  // The server's list, once it has one. Written from an effect rather than read inline so the
  // tabs keep the list across a switch back to PAPER, where no status poll runs.
  useEffect(() => {
    const served = binance.symbols;
    if (served && served.length > 0) setPermitted(served);
  }, [binance.symbols]);

  const sourceSwitch = (
    <AccountSourceSwitch value={source} onChange={setSource} available={binance.available} />
  );
  /** The tab strip, rendered identically above both trees so the selection reads the same
   *  whichever account is on screen. Disabled while a write is in flight. */
  const symbolTabs = (
    <SymbolTabs value={symbol} onChange={selectSymbol} permitted={permitted}
      busy={source === "BINANCE_LIVE" ? binance.busy : terminal.busy} />
  );

  if (source === "BINANCE_LIVE") {
    const account = binance.account;
    // One verdict for the whole screen: the bar states it, the buttons obey it. It is the
    // server's answer - the router's both-gates view plus the arm session - never this page's
    // memory of a click, so a lapsed TTL, a manual disarm, a restart, a refused key or a stale
    // snapshot all put the screen back to 거래불가 on the next poll without any special case.
    const gate = liveTradeGate(account, binance.arm, binance.error);
    const activate = () => setActivating(true);
    return (
      <div className="mx-auto max-w-[1600px]">
        {sourceSwitch}
        {/* Every symbol's position first, then the tabs, then the selected symbol's detail.
            The strip is above the tabs on purpose: what is open is the thing an operator needs
            before deciding which tab to be on. */}
        <OpenPositionsStrip summary={positions.summary} selected={symbol}
          onSelect={selectSymbol} error={positions.error} />
        {symbolTabs}
        <LiveTradeBar account={account} gate={gate} busy={binance.busy} error={binance.armError}
          onActivate={activate} onDisarm={binance.disarmLive} />
        <LiveActivateDialog open={activating} busy={binance.busy}
          ttlS={binance.arm?.ttl_s ?? null}
          onCancel={() => setActivating(false)}
          onConfirm={() => { void binance.armLive().finally(() => setActivating(false)); }} />
        {!account || !account.ready ? (
          <LiveBlockedPanel blockers={account?.blockers ?? binance.status?.blockers ?? []}
            error={binance.error} />
        ) : (
          <div className="space-y-3">
            <LiveMarketHeader account={account} />
            <LivePerformanceSummary performance={binance.performance} />
            {/* Phone: the held position comes before the chart with its own CLOSE, exactly
                where the paper screen puts it. The wide layout keeps the full panel below the
                chart, so nothing moves on a desktop. */}
            <div className="xl:hidden">
              <LivePositionCard card={binance.positionCard} nowMs={Date.now()}
                busy={binance.busy}
                onClose={() => (gate.tradable
                  ? binance.order({ side: account.position?.side || "LONG", intent: "CLOSE" })
                  : activate())} />
            </div>
            <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
              <div className="space-y-3">
                {/* The candles stay on the paper feed in V1: switching them to Binance is a
                    larger change than this step allows, and the LIVE numbers that matter -
                    mark, PnL, preview - are Binance's.
                    The *price lines* are a different matter and are Binance's here. `state` is
                    the paper terminal's and is passed for the candles alone; letting the panel
                    derive its overlays from it drew the paper account's entry on a LIVE chart,
                    which is exactly the number an operator reads their position against. */}
                {/* Binance's book first, then the chart it is not drawn from. The label sits
                    above the chart rather than under it so it is read before the price line. */}
                <LiveBookStrip account={account} />
                {terminal.state && (
                  <ChartSection state={terminal.state} bars={terminal.bars} timeframe={timeframe}
                    onTimeframe={setTimeframe} overlays={liveOverlays(account)}
                    note={LIVE_CHART_SOURCE_NOTE}
                    c1={c1Enabled ? signals.state : null}
                    c1Markers={c1Enabled ? signals.markers : []} />
                )}
                {/* Wide only: the phone already has the card above the chart, and showing
                    both would print the same position twice. Same split the paper screen uses
                    between MobilePositionCard and PositionStrip. */}
                <div className="hidden xl:block">
                  <LivePositionPanel account={account} card={binance.positionCard} />
                </div>
              </div>
              {/* Size, leverage and the two order buttons in one column, in the order they are
                  used, the way the paper ticket reads. */}
              <div className="space-y-3">
                <LiveLeveragePanel account={account} options={binance.leverage} compact
                  onSelect={binance.changeLeverage} busy={binance.busy}
                  error={binance.leverageError} />
                <LiveOrderTicket account={account} onOrder={binance.order} busy={binance.busy}
                  error={binance.actionError} preview={binance.preview}
                  onPreview={binance.requestPreview} gate={gate} onActivate={activate}
                  sizing={binance.sizing} />
                {account.position && !account.position.is_flat && (
                  // AUTO is out of scope for the multi-symbol step, so the guard still watches
                  // one instrument. On the others the server answers UNAVAILABLE with the
                  // symbol it does watch, and the panel states that instead of rendering a
                  // guard that would close a different position than the tab suggests.
                  binance.exitGuard && (binance.exitGuard as { available?: boolean }).available === false
                    ? (
                      <p className="rounded-lg border border-border bg-surface px-3 py-2 text-[11px] text-muted"
                        data-testid="auto-exit-unavailable" role="status">
                        {(binance.exitGuard as { unavailable_message?: string }).unavailable_message
                          ?? "자동청산은 이 심볼에 제공되지 않습니다."}
                      </p>
                    )
                    : (
                      <LiveAutoExit guard={binance.exitGuard} onSave={binance.saveExitGuard}
                        onDisable={binance.disableExitGuard} busy={binance.busy}
                        error={binance.exitGuardError} />
                    )
                )}
              </div>
            </div>

            {/* Everything that documents the screen rather than driving a decision, behind the
                same disclosures the paper screen uses. */}
            <div className="mt-4 sm:mt-6">
              <Disclosure title="계좌 상세" testId="disclosure-live-account">
                <LiveAccountCards account={account} />
                <div className="mt-3"><LiveAuthorityNote account={account} /></div>
              </Disclosure>
              <Disclosure title="레버리지·증거금 설명" testId="disclosure-live-leverage">
                <LiveLeverageNotes options={binance.leverage} />
              </Disclosure>
              <Disclosure title="실주문 안전장치" testId="disclosure-live-safety">
                <p className="text-[11px] text-muted">{LIVE_LOCK_NOTE}</p>
                <p className="mt-1 text-[11px] text-muted">{ARM_NOTE}</p>
              </Disclosure>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (!terminal.state) {
    return (
      <div className="mx-auto max-w-[1600px]">
        {sourceSwitch}
        {symbolTabs}
        {terminal.error
          ? <ErrorState message={terminal.error} retry={terminal.refresh} />
          : <LoadingState />}
      </div>
    );
  }

  const state = terminal.state;
  const openedMs = positionOpenedMs(terminal.events);

  return (
    <div className="mx-auto max-w-[1600px]">
      {sourceSwitch}
      {symbolTabs}
      {terminal.error && (
        <p className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning" role="status">
          {terminal.error}
        </p>
      )}

      <MarketHeader state={state} performance={terminal.performance}
        onAction={terminal.act} busy={terminal.busy} />
      {/* C1 AUTO is the default symbol's. Not rendered elsewhere, because the control would
          otherwise arm a BTC strategy from a screen titled ETH. */}
      {c1Enabled && state.c1_available !== false && (
        <C1AutoControl state={state} markers={signals.markers} busy={terminal.busy}
          onAction={terminal.act} />
      )}

      {/* Phone: the held position comes before the chart, with its own CLOSE. */}
      <div className="xl:hidden">
        <MobilePositionCard state={state} openedMs={openedMs} nowMs={state.server_time_ms}
          preview={terminal.breakdown?.position} live={live} onAction={terminal.act} busy={terminal.busy} />
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
        <div className="space-y-3">
          <ChartSection state={state} bars={terminal.bars} timeframe={timeframe}
            onTimeframe={setTimeframe}
            c1={c1Enabled ? signals.state : null}
            c1Markers={c1Enabled ? signals.markers : []} />
          {/* On a phone the order panel sits here, directly under the chart. The wide layout
              moves it to the right column, which is why it is rendered twice rather than
              repositioned with CSS order: two different trees, each simple. */}
          <div className="xl:hidden">
            <OrderTicket state={state} sizing={terminal.sizing} onAction={terminal.act}
              busy={terminal.busy} error={terminal.actionError} previewEnabled={!wide} />
          </div>
          {/* Wide layout keeps the full position panel under the chart; a phone has the card above. */}
          <div className="hidden xl:block">
            <PositionStrip state={state} openedMs={openedMs} nowMs={state.server_time_ms}
              preview={terminal.breakdown?.position} live={live} />
          </div>
        </div>

        <div className="hidden xl:block">
          <OrderTicket state={state} sizing={terminal.sizing} onAction={terminal.act}
            busy={terminal.busy} error={terminal.actionError} previewEnabled={wide} />
        </div>
      </div>

      <div className="mt-4 sm:mt-6">
        <Disclosure title="이 세션의 성과" testId="disclosure-performance">
          <PerformancePanel performance={terminal.performance} />
        </Disclosure>
        <Disclosure title="거래 기록" testId="disclosure-trades">
          <TradeHistory trades={terminal.trades} breakdowns={terminal.breakdown?.trades} />
        </Disclosure>
        <Disclosure title="주문·체결 원장" testId="disclosure-ledger">
          <LedgerTable events={terminal.events} />
        </Disclosure>
        <Disclosure title="런 설정 근거" testId="disclosure-run">
          <SourceBanner state={state} />
          <div className="mt-3"><RunFooter state={state} /></div>
          <div className="mt-3"><AutoNote reason={state.state.auto_unavailable_reason} /></div>
        </Disclosure>
      </div>
    </div>
  );
}
