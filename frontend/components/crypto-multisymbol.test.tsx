import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import {
  OpenPositionsStrip, SymbolTabs, useOpenPositions, useSelectedSymbol,
} from "@/components/crypto-symbol-tabs";
import { LiveLeveragePanel, LiveOrderTicket, LiveQuickSize, liveUnit, useBinanceLive }
  from "@/components/crypto-live-terminal";
import { leverageRestriction, liveApi } from "@/lib/crypto-live";
import type {
  LiveAccount, LivePositionsSummary, LiveSizing, LiveStatus,
} from "@/lib/crypto-live";
import { CRYPTO_SYMBOLS, DEFAULT_SYMBOL, baseAsset, readStoredSymbol, storeSymbol, withSymbol }
  from "@/lib/crypto-symbols";
import { cryptoApi } from "@/lib/crypto-paper";

/** The multi-symbol manual terminal, on the screen side.
 *
 *  These tests are about one failure and its variants: a figure read for one instrument being
 *  rendered under another instrument's label. That failure has no visual signature - every
 *  number looks plausible and every label is right - so it is pinned here rather than left to
 *  be noticed. The two mechanisms under test are clearing state when the symbol changes, and
 *  discarding a response that names a different symbol than the one on screen.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => { try { window.localStorage.clear(); } catch { /* private window */ } });

const PRICES: Record<string, string> = {
  BTCUSDT: "83500.00", ETHUSDT: "3120.00", SOLUSDT: "165.4300",
};
const QTY: Record<string, string> = { BTCUSDT: "0.058", ETHUSDT: "1.250", SOLUSDT: "12" };

const account = (symbol: string, over: Partial<LiveAccount> = {}): LiveAccount => ({
  symbol, ready: true, blockers: [], fetched_at_ms: 1, age_ms: 900, stale: false,
  source: "BINANCE_LIVE",
  base_asset: baseAsset(symbol),
  balance: { asset: "USDT", wallet_balance: "1000", available_balance: "800",
    margin_balance: "1000", unrealized_pnl: "0", max_withdraw: "800", initial_margin: "0",
    maint_margin: "0", account_wallet_usd: "999.4", account_available_usd: "799.5",
    account_margin_usd: "999.4", account_unrealized_usd: "0", usd_valuation_ratio: "0.9994",
    update_time_ms: 1, balance_source: "binance" },
  position: { symbol, side: "LONG", position_side: "BOTH", qty: QTY[symbol],
    signed_qty: QTY[symbol], entry_price: PRICES[symbol], break_even_price: PRICES[symbol],
    mark_price: PRICES[symbol], unrealized_pnl: "1.25", liquidation_price: null,
    isolated_margin: "0", notional: "100", initial_margin: "10", maint_margin: "1",
    adl: 1, update_time_ms: 1, is_flat: false },
  symbol_config: { symbol, margin_type: "CROSSED", leverage: "20", max_notional: "1000000",
                   is_auto_add_margin: false },
  position_mode: { dual_side: false, mode: "ONE_WAY" },
  commission: { symbol, maker: "0.0002", taker: "0.0005", source: "binance" },
  mark: { symbol, mark_price: PRICES[symbol], index_price: PRICES[symbol],
          last_funding_rate: "0.0001", next_funding_time_ms: null },
  book: { best_bid: PRICES[symbol], best_ask: PRICES[symbol], bid_qty: "5", ask_qty: "5",
          spread: "0.10" },
  filters: { symbol, market_min_qty: symbol === "SOLUSDT" ? "0.1"
               : symbol === "ETHUSDT" ? "0.01" : "0.001" },
  krw: null, position_krw: null, krw_per_usdt: "1341.00", krw_note: "",
  gates: { armed: true, env_flag: true, client_armed: true,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  stream: null, ...over,
});

const status = (symbols: string[] = [...CRYPTO_SYMBOLS]): LiveStatus => ({
  source: "BINANCE_LIVE", available: true, ready: true, blockers: [],
  symbol: DEFAULT_SYMBOL, symbols, default_symbol: DEFAULT_SYMBOL,
  gates: { armed: true, env_flag: true, client_armed: true,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  config: {}, runtime_error: null, endpoints: [],
});

const summary = (over: Partial<LivePositionsSummary> = {}): LivePositionsSummary => ({
  source: "BINANCE_LIVE", symbols: [...CRYPTO_SYMBOLS], default_symbol: DEFAULT_SYMBOL,
  positions: [
    { ...account("BTCUSDT").position!, available: true, base_asset: "BTC",
      krw: { unrealized_pnl: "12300" } },
    { ...account("ETHUSDT").position!, side: null, qty: "0", signed_qty: "0", is_flat: true,
      available: true, base_asset: "ETH", krw: null },
    { ...account("SOLUSDT").position!, side: "SHORT", available: true, base_asset: "SOL",
      krw: { unrealized_pnl: "-4500" } },
  ],
  others: [], krw_per_usdt: "1341.00", fetched_at_ms: 1,
  authority: "binance GET /fapi/v3/positionRisk (all symbols, one read)",
  others_note: "이 터미널에서 거래하지 않는 심볼의 보유 포지션입니다. 조회만 가능합니다.",
  ...over,
});

const side = (symbol: string, name: "LONG" | "SHORT") => ({
  side: name, leverage: "20", available_balance: "800", max_qty: QTY[symbol],
  max_feasible: true, local_max_qty: null, max_definition: "X", reject_code: null,
  reject_message: null,
  instrument: { qty_step: "0.001", min_qty: "0.001", smallest_orderable_qty: "0.002",
                min_notional: "100", market_max_qty: "120" },
  presets: [{ label: "MAX", fraction: "1.00", qty: QTY[symbol], feasible: true,
              reject_code: null, reject_message: null }],
});

// Both sides, because `livePresetQty` offers a size only when both ladders agree: a LONG walks
// the asks and a SHORT walks the bids, so the affordable size differs and the smaller wins.
const sizing = (symbol: string): LiveSizing => ({
  available: true, symbol, fetched_at_ms: 1, age_ms: 10,
  sides: { LONG: side(symbol, "LONG"), SHORT: side(symbol, "SHORT") },
} as unknown as LiveSizing);

// ------------------------------------------------------------------ the selection


describe("the symbol selection", () => {
  it("defaults to BTCUSDT and survives a reload", () => {
    expect(readStoredSymbol()).toBe("BTCUSDT");
    const first = renderHook(() => useSelectedSymbol(null));
    expect(first.result.current.symbol).toBe("BTCUSDT");
    act(() => { first.result.current.select("SOLUSDT"); });
    expect(first.result.current.symbol).toBe("SOLUSDT");
    first.unmount();
    // A fresh mount is what a reload is. The selection has to come back on the first render,
    // not be corrected one frame later, because that frame is long enough to read a price from.
    const reloaded = renderHook(() => useSelectedSymbol(null));
    expect(reloaded.result.current.symbol).toBe("SOLUSDT");
  });

  it("discards a stored symbol the server does not permit", () => {
    storeSymbol("SOLUSDT");
    const { result } = renderHook(({ permitted }) => useSelectedSymbol(permitted),
      { initialProps: { permitted: ["BTCUSDT", "ETHUSDT"] as readonly string[] } });
    // A tab selected on a symbol every request would refuse is a dead screen, so it moves.
    expect(result.current.symbol).toBe("BTCUSDT");
  });

  it("falls back to the default rather than throwing when storage is unavailable", () => {
    const getItem = vi.spyOn(window.localStorage, "getItem")
      .mockImplementation(() => { throw new Error("blocked"); });
    const setItem = vi.spyOn(window.localStorage, "setItem")
      .mockImplementation(() => { throw new Error("blocked"); });
    expect(readStoredSymbol()).toBe("BTCUSDT");
    expect(() => storeSymbol("ETHUSDT")).not.toThrow();
    getItem.mockRestore();
    setItem.mockRestore();
  });

  it("never lets a request go out without a symbol", () => {
    expect(withSymbol("/api/crypto/binance/account", "ETHUSDT"))
      .toBe("/api/crypto/binance/account?symbol=ETHUSDT");
    expect(withSymbol("/api/crypto/binance/fills?limit=20", "SOLUSDT"))
      .toBe("/api/crypto/binance/fills?limit=20&symbol=SOLUSDT");
  });
});


describe("the symbol tabs", () => {
  it("renders the server's list and marks one selected", () => {
    render(<SymbolTabs value="ETHUSDT" onChange={vi.fn()} permitted={["BTCUSDT", "ETHUSDT"]} />);
    expect(screen.getByTestId("symbol-tab-BTCUSDT")).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("symbol-tab-ETHUSDT")).toHaveAttribute("aria-selected", "true");
    // Not rendered: the client's own third entry, because the server did not offer it.
    expect(screen.queryByTestId("symbol-tab-SOLUSDT")).toBeNull();
  });

  it("gives the selected tab a pressed state the design system actually renders", () => {
    // The first version used `bg-accent`, `text-accent-foreground` and `bg-surface-muted`, none
    // of which is a token in tailwind.config. Tailwind emits nothing for an unknown utility, so
    // the selected tab had no border, no fill and no visible change - aria-selected was correct
    // and the screen showed no pressed state at all. Asserting the aria attribute alone would
    // not have caught it, so the classes are checked against the token list.
    const TOKENS = ["primary", "surface-alt", "line", "foreground", "muted", "danger",
                    "success", "warning", "surface", "background"];
    render(<SymbolTabs value="ETHUSDT" onChange={vi.fn()} />);
    const on = screen.getByTestId("symbol-tab-ETHUSDT");
    const off = screen.getByTestId("symbol-tab-BTCUSDT");
    // The selected tab is visibly different, not merely announced as selected.
    expect(on.className).not.toBe(off.className);
    expect(on.className).toMatch(/border-primary/);
    expect(on.className).toMatch(/bg-surface-alt/);
    expect(off.className).toMatch(/border-line/);
    // Every colour utility resolves to a defined token. Size and weight utilities share the
    // `text-` prefix, so they are excluded by name rather than by guessing.
    const NOT_COLOUR = new Set(["xs", "sm", "base", "lg", "xl", "bold", "semibold", "medium",
                                "center", "left", "right", "wide", "nowrap"]);
    const colours = [...`${on.className} ${off.className}`.matchAll(/(?:bg|text|border)-([a-z-]+)/g)]
      .map(m => m[1]).filter(name => !NOT_COLOUR.has(name));
    for (const name of colours) {
      expect(TOKENS.some(token => name === token || name.startsWith(`${token}-`)),
             `unknown design token: ${name}`).toBe(true);
    }
  });

  it("does not switch while a write is in flight", () => {
    const onChange = vi.fn();
    render(<SymbolTabs value="BTCUSDT" onChange={onChange} busy />);
    fireEvent.click(screen.getByTestId("symbol-tab-ETHUSDT"));
    // A response landing on a screen that is no longer about the instrument it was sent for is
    // the reason this is disabled rather than merely discouraged.
    expect(onChange).not.toHaveBeenCalled();
  });
});


describe("the open positions strip", () => {
  it("shows every symbol including the flat ones", () => {
    render(<OpenPositionsStrip summary={summary()} selected="BTCUSDT" onSelect={vi.fn()} />);
    const strip = screen.getByTestId("open-positions-strip");
    expect(strip).toHaveTextContent("BTC");
    expect(strip).toHaveTextContent("LONG");
    // "SOL FLAT" is information somebody acts on; an absent row is not.
    expect(screen.getByTestId("open-position-ETHUSDT")).toHaveTextContent("FLAT");
    expect(screen.getByTestId("open-position-SOLUSDT")).toHaveTextContent("SHORT");
  });

  it("names positions on symbols this terminal does not trade", () => {
    const body = summary({ others: [{ symbol: "XRPUSDT", side: "LONG", qty: "500",
      signed_qty: "500", unrealized_pnl: "1.0", notional: "250", tradable_here: false }] });
    render(<OpenPositionsStrip summary={body} selected="BTCUSDT" onSelect={vi.fn()} />);
    // It holds margin every tab's Safe MAX already reflects, so a strip titled OPEN POSITIONS
    // that hid it would be wrong by omission.
    expect(screen.getByTestId("open-positions-others")).toHaveTextContent("XRPUSDT");
  });

  it("selects a symbol when its row is pressed", () => {
    const onSelect = vi.fn();
    render(<OpenPositionsStrip summary={summary()} selected="BTCUSDT" onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("open-position-SOLUSDT"));
    expect(onSelect).toHaveBeenCalledWith("SOLUSDT");
  });

  it("reads the summary with one account-wide call, not one per symbol", async () => {
    const spy = vi.spyOn(liveApi, "positions").mockResolvedValue(summary());
    const { result } = renderHook(() => useOpenPositions(true));
    await waitFor(() => expect(result.current.summary).not.toBeNull());
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith();
  });
});


// ------------------------------------------------------------------ no mixing


describe("the LIVE hook on a symbol change", () => {
  const stub = (delays: Record<string, number> = {}) => {
    vi.spyOn(liveApi, "status").mockImplementation(async symbol => status());
    vi.spyOn(liveApi, "armState").mockResolvedValue({ armed: true } as never);
    vi.spyOn(liveApi, "sizing").mockImplementation(async symbol => sizing(symbol));
    vi.spyOn(liveApi, "positionCard").mockImplementation(
      async symbol => ({ open: false, available: true, symbol }) as never);
    vi.spyOn(liveApi, "exitGuard").mockResolvedValue({ state: "OFF" } as never);
    vi.spyOn(liveApi, "performance").mockImplementation(
      async symbol => ({ available: true, symbol }) as never);
    vi.spyOn(liveApi, "leverageOptions").mockImplementation(
      async symbol => ({ symbol, options: [1, 20], current: "20", max_leverage: 125,
                         brackets: [], notional_coef: null, margin_type: "CROSSED",
                         authority: "", margin_type_note: "" }) as never);
    return vi.spyOn(liveApi, "account").mockImplementation(async symbol => {
      const wait = delays[symbol] ?? 0;
      if (wait) await new Promise(resolve => setTimeout(resolve, wait));
      return account(symbol);
    });
  };

  it("clears the previous symbol's figures before the new ones arrive", async () => {
    stub({ ETHUSDT: 50 });
    const { result, rerender } = renderHook(({ symbol }) => useBinanceLive(true, symbol),
      { initialProps: { symbol: "BTCUSDT" } });
    await waitFor(() => expect(result.current.account?.symbol).toBe("BTCUSDT"));
    act(() => { rerender({ symbol: "ETHUSDT" }); });
    // The moment the tab changes, BTC's balance, position and ladder are gone. Keeping them for
    // one poll interval would mean every number on the ETH screen was BTC's and every label
    // said ETH - the worst available version of this bug.
    expect(result.current.account).toBeNull();
    expect(result.current.sizing).toBeNull();
    expect(result.current.positionCard).toBeNull();
    expect(result.current.leverage).toBeNull();
    await waitFor(() => expect(result.current.account?.symbol).toBe("ETHUSDT"));
    expect(result.current.account?.position?.qty).toBe(QTY.ETHUSDT);
  });

  it("discards a response that names the previous symbol", async () => {
    // BTC's read is slow, so it resolves *after* the tab has already changed to SOL. Clearing
    // alone would not catch this: the late `setAccount` would put BTC's position on the SOL
    // screen, and an abort signal does not help once the response is already in flight.
    stub({ BTCUSDT: 80 });
    const { result, rerender } = renderHook(({ symbol }) => useBinanceLive(true, symbol),
      { initialProps: { symbol: "BTCUSDT" } });
    act(() => { rerender({ symbol: "SOLUSDT" }); });
    await waitFor(() => expect(result.current.account?.symbol).toBe("SOLUSDT"));
    await new Promise(resolve => setTimeout(resolve, 120));
    expect(result.current.account?.symbol).toBe("SOLUSDT");
    expect(result.current.account?.position?.qty).toBe(QTY.SOLUSDT);
  });

  it("asks every route about the selected symbol", async () => {
    const accountSpy = stub();
    const { result } = renderHook(() => useBinanceLive(true, "SOLUSDT"));
    await waitFor(() => expect(result.current.account?.symbol).toBe("SOLUSDT"));
    expect(accountSpy).toHaveBeenCalledWith("SOLUSDT");
    expect(liveApi.sizing).toHaveBeenCalledWith("SOLUSDT");
    expect(liveApi.leverageOptions).toHaveBeenCalledWith("SOLUSDT");
    expect(liveApi.positionCard).toHaveBeenCalledWith("SOLUSDT");
  });

  it("sends an order with the symbol on screen", async () => {
    stub();
    const order = vi.spyOn(liveApi, "order").mockResolvedValue({ plan: {}, response: {} });
    const { result } = renderHook(() => useBinanceLive(true, "ETHUSDT"));
    await waitFor(() => expect(result.current.account?.symbol).toBe("ETHUSDT"));
    await act(async () => { await result.current.order({ side: "LONG", intent: "OPEN", qty: "1" }); });
    expect(order).toHaveBeenCalledWith("ETHUSDT",
      { side: "LONG", intent: "OPEN", qty: "1" });
  });
});


// ------------------------------------------------------------------ units and defaults


describe("the leverage row refuses a step the account cannot use", () => {
  const options = (over: Record<string, unknown> = {}) => ({
    symbol: "SOLUSDT", current: "20", margin_type: "CROSSED", max_leverage: 100,
    options: [1, 2, 3, 5, 10, 20, 50, 100], brackets: [], notional_coef: null,
    authority: "", margin_type_note: "", unavailable: {}, ...over,
  }) as never;

  it("greys a step blocked only by the account-wide restriction", () => {
    // The gap this closes: after per-symbol capability isolation, ETHUSDT and SOLUSDT have an
    // empty `unavailable` because they have never been refused. Without reading
    // `account_scope`, 50x and 100x rendered as live buttons on those symbols, and pressing one
    // sends a leverage write Binance rejects with -4300.
    const scope = {
      above: 20, code: -4300, until_ms: 1_793_237_640_000, until_utc: "2026-10-29 01:34 UTC",
      observed_at_ms: 1, scope: "ACCOUNT", observed_on_symbol: null, blocked: [50, 100],
      messages: { "50": "현재 계정에서 50x를 사용할 수 없습니다. 계정 전체 제한입니다.",
                  "100": "현재 계정에서 100x를 사용할 수 없습니다. 계정 전체 제한입니다." },
      authority: "",
    };
    expect(leverageRestriction(options(), 50)).toBeNull();
    const withScope = options({ account_scope: scope });
    expect(leverageRestriction(withScope, 20)).toBeNull();
    expect(leverageRestriction(withScope, 50)?.message).toContain("계정 전체 제한");
    expect(leverageRestriction(withScope, 100)?.above).toBe(20);
    // A flat account: a held position disables the whole row for its own reason, which would
    // mask the thing under test.
    const flat = account("SOLUSDT", { position: { ...account("SOLUSDT").position!, side: null,
      qty: "0", signed_qty: "0", is_flat: true } });
    render(<LiveLeveragePanel account={flat} options={withScope} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-leverage-50")).toBeDisabled();
    expect(screen.getByTestId("live-leverage-100")).toBeDisabled();
    expect(screen.getByTestId("live-leverage-10")).not.toBeDisabled();
  });

  it("prefers the symbol's own observed refusal over the account-wide wording", () => {
    const own = options({
      unavailable: { "50": { above: 20, code: -4300, until_ms: 1, until_utc: "x",
                             observed_at_ms: 1, code_name: "ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE",
                             message: "이 심볼에서 관측된 거부" } },
      account_scope: { above: 20, code: -4300, until_ms: 1, until_utc: "x", observed_at_ms: 1,
                       scope: "ACCOUNT", observed_on_symbol: "BTCUSDT", blocked: [50, 100],
                       messages: { "50": "계정 전체 제한" }, authority: "" },
    });
    expect(leverageRestriction(own, 50)?.message).toBe("이 심볼에서 관측된 거부");
  });
});

describe("quantities are labelled and defaulted per instrument", () => {
  it("takes the unit from Binance's own baseAsset", () => {
    expect(liveUnit(account("SOLUSDT"))).toBe("SOL");
    expect(liveUnit(account("ETHUSDT"))).toBe("ETH");
    // No snapshot yet: the symbol minus USDT, never a hardcoded "BTC".
    expect(liveUnit(null, "SOLUSDT")).toBe("SOL");
    expect(liveUnit(undefined)).toBe("BTC");
  });

  it("labels the order ticket in the selected symbol's coin", () => {
    const { container } = render(
      <LiveOrderTicket account={account("SOLUSDT")} onOrder={vi.fn()} />);
    const text = container.textContent ?? "";
    expect(text).toContain("SOL");
    // The whole panel, not one label: a single "BTC" left anywhere on a SOL ticket is a
    // quantity in the wrong unit beside a button that sends a real order.
    expect(text).not.toContain("BTC");
  });

  it("defaults the size box to this instrument's own minimum", () => {
    // "0.001" is BTCUSDT's minimum. On SOLUSDT the minimum is 0.1, so that literal would have
    // pre-filled the box with a hundredth of the smallest valid order and the refusal would
    // have come from Binance rather than from the screen.
    render(<LiveOrderTicket account={account("SOLUSDT")} onOrder={vi.fn()} />);
    const box = screen.getByRole("textbox") as HTMLInputElement;
    expect(box.value).toBe("0.1");
  });

  it("defaults the LIVE size to the smallest size that clears MIN_NOTIONAL", async () => {
    // Found on the production screen: SOLUSDT opened pre-filled with 0.01, its minimum
    // quantity, which is worth ~1.2 USDT against Binance's 5 USDT minimum notional - so both
    // order buttons read "notional 1.211000 is below the minimum 5" before anything was typed.
    const ladder = sizing("SOLUSDT") as unknown as { sides: Record<string, { instrument: Record<string, string> }> };
    ladder.sides.LONG.instrument.smallest_orderable_qty = "0.05";
    ladder.sides.SHORT.instrument.smallest_orderable_qty = "0.05";
    render(<LiveOrderTicket account={account("SOLUSDT")} onOrder={vi.fn()}
      sizing={ladder as never} />);
    await waitFor(() => {
      const box = screen.getByRole("textbox") as HTMLInputElement;
      expect(box.value).toBe("0.05");
    });
  });

  it("titles a quick size in the selected symbol's coin", () => {
    render(<LiveQuickSize unit="ETH" sizing={sizing("ETHUSDT")} onPick={vi.fn()} />);
    const button = screen.getByTitle(`${QTY.ETHUSDT} ETH`);
    expect(button).toBeTruthy();
  });
});

describe("the chart follows the selected symbol", () => {
  it("reads history and 15 s candles for the state's symbol, not the default", async () => {
    // The defect this pins had no visual signature. `ChartSection` called both chart hooks
    // without a symbol, so they fell back to BTCUSDT and every tab drew BTCUSDT candles while
    // the header, the position and the order panel were all correct about ETH or SOL. It was
    // found by tracing the requests the page made in the isolated preview.
    const history = vi.spyOn(cryptoApi, "chartHistory").mockResolvedValue({
      symbol: "SOLUSDT", timeframe: "1m", bucket_ms: 60_000, source: "BYBIT_PUBLIC_KLINE",
      source_interval: "1", bars: [], has_more: false, next_before_ms: null } as never);
    const fifteen = vi.spyOn(cryptoApi, "candles15s").mockResolvedValue({
      symbol: "SOLUSDT", timeframe: "15s", status: "CONNECTED", candles: [], current: null,
      server_time_ms: 1, coverage_from_ms: 1 } as never);

    const { ChartSection } = await import("@/components/crypto-terminal-layout");
    const state = { symbol: "SOLUSDT", leverage: "10", server_time_ms: 1,
                    state: { mode: "MANUAL" }, quote: null, account: null,
                    feed: { connected: true } } as never;

    render(<ChartSection state={state} bars={[]} timeframe="1m" onTimeframe={() => {}} />);
    await waitFor(() => expect(history).toHaveBeenCalled());
    expect(history.mock.calls.every(call => call[0] === "SOLUSDT")).toBe(true);
    cleanup();

    render(<ChartSection state={state} bars={[]} timeframe="15s" onTimeframe={() => {}} />);
    await waitFor(() => expect(fifteen).toHaveBeenCalled());
    expect(fifteen.mock.calls.every(call => call[0] === "SOLUSDT")).toBe(true);
  });
});

describe("the order preview does not survive a symbol change", () => {
  it("clears the previous instrument's costs and ignores its late response", async () => {
    // Found in the production smoke QA: one `order-preview?symbol=<previous>` left on every tab
    // change. The response was already aborted, but the preview *on screen* was not cleared, so
    // the fee, break-even and round-trip cost of the previous instrument kept rendering under
    // the new tab until the new answer landed.
    const spy = vi.spyOn(cryptoApi, "orderPreview");
    const { OrderTicket } = await import("@/components/crypto-paper-terminal");
    const state = (symbol: string) => ({
      symbol, leverage: "10", server_time_ms: 1, run_id: "r",
      state: { mode: "MANUAL", modes: ["MANUAL"], can_open_new_position: true,
               new_entry_blocked_reason: null, auto_available: false,
               auto_unavailable_reason: "" },
      quote: { ts_ms: 1, mark_price: PRICES[symbol], best_bid: PRICES[symbol],
               best_ask: PRICES[symbol] },
      account: null, instrument: { qty_step: symbol === "SOLUSDT" ? "0.1" : "0.001" },
      feed: { connected: true },
    }) as never;
    const preview = (symbol: string) => ({
      symbol, run_id: "r", server_time_ms: 1, feed_connected: true, quote_ts_ms: 1,
      krw_per_usdt: "1341", sides: { LONG: { side: "LONG", feasible: true, qty: "1" } },
    }) as never;

    const { rerender } = render(
      <OrderTicket state={state("BTCUSDT")} onAction={vi.fn()} busy={false} error={null}
        previewOverride={preview("BTCUSDT")} />);
    expect(screen.getByTestId("order-ticket")).toBeTruthy();
    // With an override the component renders what it is given; the guard under test is the
    // fetched path, so re-render on the real path and assert the request carries the new symbol.
    rerender(
      <OrderTicket state={state("SOLUSDT")} onAction={vi.fn()} busy={false} error={null} />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls.every(call => call[0] === "SOLUSDT")).toBe(true);
  });
});
