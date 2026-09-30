"use client";

import { useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/ui";
import {
  LedgerTable, OrderTicket, PerformancePanel, RunFooter, SourceBanner, TradeHistory,
  useCryptoTerminal, useLivePnl,
} from "@/components/crypto-paper-terminal";
import {
  AutoNote, ChartSection, Disclosure, MarketHeader, MobilePositionCard, PositionStrip,
} from "@/components/crypto-terminal-layout";
import {
  AccountSourceSwitch, LiveAccountCards, LiveArmPanel, LiveBadge, LiveBlockedPanel,
  LiveBookStrip, LiveLeveragePanel, LiveMarketHeader, LiveOrderTicket, LivePositionPanel,
  useBinanceLive,
} from "@/components/crypto-live-terminal";
import type { ChartTimeframe } from "@/lib/crypto-paper";
import { positionOpenedMs } from "@/lib/crypto-paper";
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
  const terminal = useCryptoTerminal();
  const [timeframe, setTimeframe] = useState<ChartTimeframe>(1);
  const wide = useIsWide();
  const live = useLivePnl(terminal.state?.account?.position_side != null);
  /** PAPER unless the operator switches, and the switch is only offered when the backend says a
   *  Binance key is configured. The two accounts never render at the same time. */
  const [source, setSource] = useState<AccountSource>("PAPER");
  const binance = useBinanceLive(source === "BINANCE_LIVE");

  const sourceSwitch = (
    <AccountSourceSwitch value={source} onChange={setSource} available={binance.available} />
  );

  if (source === "BINANCE_LIVE") {
    const account = binance.account;
    return (
      <div className="mx-auto max-w-[1600px]">
        {sourceSwitch}
        <LiveBadge account={account} />
        {!account || !account.ready ? (
          <LiveBlockedPanel blockers={account?.blockers ?? binance.status?.blockers ?? []}
            error={binance.error} />
        ) : (
          <div className="space-y-3">
            <LiveMarketHeader account={account} />
            <LiveAccountCards account={account} />
            <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
              <div className="space-y-3">
                {/* The chart stays on the paper feed in V1: switching it to Binance candles is
                    a larger change than this step allows, and the LIVE numbers that matter -
                    mark, PnL, preview - are Binance's. The caption says so rather than letting
                    the price line imply it is Binance's. */}
                {/* Binance's book first, then the chart it is not drawn from. The label sits
                    above the chart rather than under it so it is read before the price line. */}
                <LiveBookStrip account={account} />
                {terminal.state && (
                  <ChartSection state={terminal.state} bars={terminal.bars} timeframe={timeframe}
                    onTimeframe={setTimeframe} />
                )}
                <LivePositionPanel account={account} />
                <LiveLeveragePanel account={account} options={binance.leverage}
                  onSelect={binance.changeLeverage} busy={binance.busy}
                  error={binance.leverageError} />
              </div>
              <div className="space-y-3">
                {/* Arming sits above the order buttons, because it is the thing that has to be
                    true before any of them do anything. */}
                <LiveArmPanel arm={binance.arm} busy={binance.busy} error={binance.armError}
                  onArm={confirmation => binance.armLive(confirmation)}
                  onDisarm={binance.disarmLive} />
                <LiveOrderTicket account={account} onOrder={binance.order} busy={binance.busy}
                  error={binance.actionError} preview={binance.preview}
                  onPreview={binance.requestPreview} />
              </div>
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
      {terminal.error && (
        <p className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning" role="status">
          {terminal.error}
        </p>
      )}

      <MarketHeader state={state} performance={terminal.performance}
        onAction={terminal.act} busy={terminal.busy} />

      {/* Phone: the held position comes before the chart, with its own CLOSE. */}
      <div className="xl:hidden">
        <MobilePositionCard state={state} openedMs={openedMs} nowMs={state.server_time_ms}
          preview={terminal.breakdown?.position} live={live} onAction={terminal.act} busy={terminal.busy} />
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
        <div className="space-y-3">
          <ChartSection state={state} bars={terminal.bars} timeframe={timeframe}
            onTimeframe={setTimeframe} />
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
