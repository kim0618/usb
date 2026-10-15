"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ErrorState, LoadingState } from "@/components/ui";
import { OrderTicket, useCryptoTerminal, useLivePnl } from "@/components/crypto-paper-terminal";
import {
  ChartSection, Disclosure, MarketHeader, MobilePositionCard, PositionStrip,
} from "@/components/crypto-terminal-layout";
import {
  AccountSourceSwitch, LiveAccountCards, LiveActivateDialog, LiveAuthorityNote, LiveBlockedPanel,
  LiveAutoExit, LiveBookStrip, LiveLeverageNotes, LiveLeveragePanel, LiveMarketHeader, LiveOrderTicket,
  LivePositionCard, LivePositionPanel, LiveTradeBar, useBinanceLive,
} from "@/components/crypto-live-terminal";
import { HotCoinFinder, useHotSlot } from "@/components/crypto-hot-coins";
import {
  OpenPositionsStrip, SymbolTabs, useOpenPositions, useSelectedSymbol, useSymbolVolatility,
} from "@/components/crypto-symbol-tabs";
import { DEFAULT_SYMBOL } from "@/lib/crypto-symbols";
import type { ChartTimeframe } from "@/lib/crypto-paper";
import { positionOpenedMs } from "@/lib/crypto-paper";
import type { ContextView } from "@/lib/btc-context";
import { ARM_NOTE, LIVE_CHART_SOURCE_NOTE, LIVE_LOCK_NOTE, liveOverlays, liveTradeGate }
  from "@/lib/crypto-live";
import type { AccountSource } from "@/lib/crypto-live";

/** US-B CRYPTO manual futures terminal. Bybit PUBLIC market data, simulated fills, no account.
 *
 *  Lifted out of `app/crypto-paper/page.tsx` unchanged so that a preview route can render the
 *  real screen instead of a copy of it. The route is now a two-line wrapper around this, and the
 *  only thing this component gained is `marketContext`: an optional slot, left empty by the
 *  deployed route, so the production tree renders exactly what it rendered before.
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

export function CryptoTerminal({ marketContext }: {
  /** Rendered under the chart on a wide screen and under the order ticket on a phone.
   *
   *  A function of the on-screen symbol rather than a ready-made node, because the one failure
   *  this terminal has already paid for is a figure read for one instrument appearing under
   *  another instrument's label. Taking the symbol from here means a caller cannot supply a BTC
   *  panel to an ETH tab, and `key` on the result means switching tabs remounts rather than
   *  carrying the previous symbol's reading across. */
  marketContext?: (symbol: string, view?: ContextView) => ReactNode;
  /** Accepted and ignored: the wall and level lines it used to draw on the BTC chart are gone
   *  from the chart. The Wall/Flow reading stays beside the chart in `marketContext`. */
  contextBase?: string;
} = {}) {
  /** PAPER unless the operator switches, and the switch is only offered when the backend says a
   *  Binance key is configured. The two accounts never render at the same time. */
  const [source, setSource] = useState<AccountSource>("PAPER");
  /** Dynamic HOT V1: BTC is fixed, the HOT slot holds whatever 코인탐색 last put there.
   *
   *  The slot is the server's, on both accounts, and survives a reload, a route change and a
   *  restart. It is not the selection: BTC can be on screen while the slot holds LUMIA, and
   *  pressing the HOT tab is what selects it. */
  /** Told which account is on screen, so the PAPER screen's lock poll makes no signed read. */
  const hot = useHotSlot(undefined, undefined, source);
  /** Every symbol's open position, polled while LIVE is on screen. */
  const positions = useOpenPositions(source === "BINANCE_LIVE");
  /** Symbols other than BTC and the HOT symbol that still hold a position: an ETH position from
   *  before the fixed tabs went, or a former HOT symbol a crash left open. They get a tab for as
   *  long as the position lasts, so nothing open is ever unreachable from this screen. */
  const liveHeld = (source === "BINANCE_LIVE" ? positions.summary?.positions ?? [] : [])
    .filter(row => row.available && !row.is_flat).map(row => row.symbol).join(",");
  const heldKey = [...hot.held, ...(liveHeld ? liveHeld.split(",") : [])]
    .filter((item, index, all) => item !== DEFAULT_SYMBOL && item !== hot.symbol && all.indexOf(item) === index)
    .join(",");
  /** The fixed tabs: BTC, then any held symbol. The HOT tab is rendered after them. */
  const fixedTabs = useMemo(() => [DEFAULT_SYMBOL, ...(heldKey ? heldKey.split(",") : [])], [heldKey]);
  /** What may be selected. `null` until the HOT slot has answered, which `useSelectedSymbol`
   *  treats as "no opinion yet" so a reload on the HOT tab stays on it. */
  const selectable = useMemo(() => (hot.loaded
    ? [...fixedTabs, ...(hot.symbol ? [hot.symbol] : [])] : null), [hot.loaded, hot.symbol, fixedTabs]);
  /** The one piece of state both trees read.
   *
   *  Held here rather than inside either tree, which is what makes the selection survive the
   *  PAPER/LIVE switch for free: switching `source` re-renders this component's children and
   *  leaves this state alone. Reload survival is `localStorage`, inside the hook.
   */
  const { symbol, select: selectSymbol } = useSelectedSymbol(selectable);
  /** On the HOT tab when the slot moves on: follow it to the new HOT symbol. Anywhere else the
   *  selection stays where the operator put it. */
  const previousHot = useRef<string | null>(hot.symbol);
  useEffect(() => {
    const before = previousHot.current;
    previousHot.current = hot.symbol;
    if (before && hot.symbol && before !== hot.symbol && symbol === before) selectSymbol(hot.symbol);
  }, [hot.symbol, symbol, selectSymbol]);
  /** The paper account's reads stop on the LIVE screen; its state and candles keep coming,
   *  because the LIVE chart is still drawn from the paper feed. */
  const terminal = useCryptoTerminal(symbol, { paper: source === "PAPER" });
  const [timeframe, setTimeframe] = useState<ChartTimeframe>("1m");
  const wide = useIsWide();
  const live = useLivePnl(source === "PAPER" && terminal.state?.account?.position_side != null, symbol);
  const binance = useBinanceLive(source === "BINANCE_LIVE", symbol);
  /** The position and price the context under the chart is measured from: the account on screen,
   *  read from the same objects its own chart lines are drawn from. Read only. */
  const contextView = useMemo<ContextView>(() => (source === "BINANCE_LIVE"
    ? liveContextView(binance.account) : paperContextView(terminal.state)),
  [source, binance.account, terminal.state]);
  /** Every symbol's last-hour range, on both accounts.
   *
   *  Not gated on `source`: the range is Bybit's market data and says nothing about an account,
   *  so PAPER and BINANCE LIVE show the same three figures. Switching accounts therefore does
   *  not restart the poll or blank the badges, for the same reason the selection survives it -
   *  this state lives above both trees.
   */
  const volatility = useSymbolVolatility(useMemo(
    () => [...fixedTabs, ...(hot.symbol ? [hot.symbol] : [])], [fixedTabs, hot.symbol]));
  /** A position opening or closing on screen is the moment the HOT lock can change, so the
   *  slot is re-read then instead of at its next poll. The lock is still the server's answer. */
  const positionSignature = source === "BINANCE_LIVE"
    ? `${symbol}:${binance.account?.position?.side ?? "FLAT"}:${binance.account?.position?.qty ?? "0"}`
    : `${symbol}:${terminal.state?.account?.position_side ?? "FLAT"}:${terminal.state?.account?.position_qty ?? "0"}`;
  const refreshHot = hot.refresh;
  useEffect(() => { void refreshHot(); }, [positionSignature, refreshHot]);
  /** Raised by the bar and by a CLOSE pressed after the window lapsed. One dialog for both, so
   *  the sentence the operator has to agree to is written once. */
  const [activating, setActivating] = useState(false);

  const sourceSwitch = (
    <AccountSourceSwitch value={source} onChange={setSource} available={binance.available} />
  );
  /** The tab strip, rendered identically above both trees so the selection reads the same
   *  whichever account is on screen. Disabled while a write is in flight. */
  const symbolTabs = (
    // [코인탐색] sits at the end of the tab row. It changes the HOT slot (on the server, behind
    // the HOT lock), never the selection: nothing in its panel can select a tab or reach an
    // order. `items-stretch` makes the finder as tall as the tab row, so its panel opens below
    // the tabs' range line instead of over it.
    <div className="flex items-stretch gap-2">
      <div className="min-w-0 flex-1">
        <SymbolTabs value={symbol} onChange={selectSymbol} permitted={fixedTabs}
          busy={source === "BINANCE_LIVE" ? binance.busy : terminal.busy}
          volatility={volatility.rows} hotSlot={{ symbol: hot.symbol }} />
      </div>
      <HotCoinFinder hot={hot} onSelect={selectSymbol}
        busy={source === "BINANCE_LIVE" ? binance.busy : terminal.busy} />
    </div>
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
            {/* Phone: the held position comes before the chart with its own CLOSE, exactly
                where the paper screen puts it. The wide layout keeps the full panel below the
                chart, so nothing moves on a desktop. */}
            <div className="xl:hidden">
              <LivePositionCard card={binance.positionCard} nowMs={Date.now()}
                busy={binance.busy} symbolConfig={account.symbol_config}
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
                    note={LIVE_CHART_SOURCE_NOTE} executions={binance.executions} />
                )}
                {/* Manual Market Context, read-only, directly under the chart.

                    In the left column on purpose: the right column is the order ticket's 360px and
                    nothing added here moves it. The slot is a function of the symbol rather than a
                    node, so a caller cannot hand this screen a panel built for a different
                    instrument than the tab it is rendered under - the symbol comes from here. */}
                {wide && marketContext ? marketContext(symbol, contextView) : null}
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

            {/* The same panel on a phone, below the order ticket rather than above it.

                Rendered in exactly one of the two places - gated on `wide`, never both - so one panel
                is mounted and one poll loop runs. Position measured rather than chosen: with the panel
                between the chart and the ticket at 390px, the ticket starts 1,736px down the page even
                with four layers collapsed. Below it, the ticket is where it always was and the context
                costs the phone nothing until it is scrolled to. */}
            {!wide && marketContext
              ? <div className="mt-3">{marketContext(symbol, contextView)}</div> : null}

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

      {/* Phone: the held position comes before the chart, with its own CLOSE. */}
      <div className="xl:hidden">
        <MobilePositionCard state={state} openedMs={openedMs} nowMs={state.server_time_ms}
          preview={terminal.breakdown?.position} live={live} onAction={terminal.act} busy={terminal.busy} />
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
        <div className="space-y-3">
          <ChartSection state={state} bars={terminal.bars} timeframe={timeframe}
            onTimeframe={setTimeframe} executions={terminal.executions} />
          {/* Manual Market Context, read-only, directly under the chart.

              In the left column on purpose: the right column is the order ticket's 360px and
              nothing added here moves it. The slot is a function of the symbol rather than a
              node, so a caller cannot hand this screen a panel built for a different
              instrument than the tab it is rendered under - the symbol comes from here. */}
          {wide && marketContext ? marketContext(symbol, contextView) : null}
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

      {/* The same panel on a phone, below the order ticket rather than above it.

          Rendered in exactly one of the two places - gated on `wide`, never both - so one panel
          is mounted and one poll loop runs. Position measured rather than chosen: with the panel
          between the chart and the ticket at 390px, the ticket starts 1,736px down the page even
          with four layers collapsed. Below it, the ticket is where it always was and the context
          costs the phone nothing until it is scrolled to. */}
      {!wide && marketContext ? <div className="mt-3">{marketContext(symbol, contextView)}</div> : null}

      {/* Trading terminal only. The session performance, trade history, order/fill ledger and
          run rationale panels are off this screen; the ledger, the trade list and the run config
          they were read from are kept on the server for later analysis. */}
    </div>
  );
}


function finite(value: string | null | undefined): number | null {
  if (value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function liveContextView(account: ReturnType<typeof useBinanceLive>["account"]): ContextView {
  const position = account?.position;
  const mark = finite(position?.mark_price) ?? finite(account?.mark?.mark_price);
  const entry = finite(position?.entry_price);
  if (!position || position.is_flat || entry == null || (position.side !== "LONG" && position.side !== "SHORT")) {
    return { position: null, mark };
  }
  return { position: { sign: position.side === "LONG" ? 1 : -1, entry, qty: position.qty }, mark };
}

function paperContextView(state: ReturnType<typeof useCryptoTerminal>["state"]): ContextView {
  const mark = finite(state?.quote?.mark_price);
  const account = state?.account;
  const entry = finite(account?.avg_entry);
  if (!account || account.position_side == null || entry == null) return { position: null, mark };
  return { position: { sign: account.position_side === "LONG" ? 1 : -1, entry, qty: account.position_qty }, mark };
}
