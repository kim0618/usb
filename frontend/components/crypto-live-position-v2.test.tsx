import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  LiveAutoExit, LiveLeveragePanel, LiveOrderTicket, LivePositionCard, LivePositionPanel,
  LiveQuickSize,
} from "@/components/crypto-live-terminal";
import { ChartSection } from "@/components/crypto-terminal-layout";
import {
  liveOverlays, livePresetQty, liveSideAllowance, leverageRestriction,
} from "@/lib/crypto-live";
import type {
  LiveAccount, LiveExitGuard, LiveLeverageOptions, LivePositionCard as LiveCardData,
  LivePreview, LiveSideSizing, LiveSizing,
} from "@/lib/crypto-live";
import { positionOverlays } from "@/lib/crypto-paper";
import type { CryptoState } from "@/lib/crypto-paper";

/** LIVE Position & Leverage V2.
 *
 *  Four defects are pinned here, each with the real figures that exposed it:
 *
 *  1. the chart's blue 진입 line was the *paper* account's average entry on a LIVE screen;
 *  2. a held position blanked every quick size, because the opposite side's
 *     `REVERSE_NOT_ALLOWED` was being read as an answer about the side being pressed;
 *  3. 50x and 100x were offered as plain buttons on an account Binance refuses above 20x;
 *  4. the desktop panel had no "청산 시 예상 순손익" while the phone card did.
 *
 *  Every figure below comes from a fixture standing in for the server. Nothing in these tests
 *  computes a PnL, a margin or a size, because nothing in the components does.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

// Measured on the real accounts on 2026-10-02. The two entries are 24.25 USDT apart, which is
// exactly how small a wrong line can be and still be the wrong account's.
const LIVE_ENTRY = "83938.05";
const PAPER_ENTRY = "83962.30";

const account = (over: Partial<LiveAccount> = {}): LiveAccount => ({
  symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1, age_ms: 900, stale: false,
  source: "BINANCE_LIVE",
  balance: { asset: "USDT", wallet_balance: "340.29", available_balance: "0",
    margin_balance: "266.59", unrealized_pnl: "-73.70", max_withdraw: "0",
    initial_margin: "339.24", maint_margin: "27.13", account_wallet_usd: "340.0",
    account_available_usd: "0", account_margin_usd: "266.4", account_unrealized_usd: "-73.6",
    usd_valuation_ratio: "0.9994", update_time_ms: 1, balance_source: "binance" },
  position: { symbol: "BTCUSDT", side: "SHORT", position_side: "BOTH", qty: "0.080",
    signed_qty: "-0.080", entry_price: LIVE_ENTRY, break_even_price: LIVE_ENTRY,
    mark_price: "84811.20", unrealized_pnl: "-73.70", liquidation_price: "87840.33",
    isolated_margin: "0", notional: "-6784.89", initial_margin: "339.24",
    maint_margin: "27.13", adl: 1, update_time_ms: 1, is_flat: false },
  symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "20",
                   max_notional: "100000000", is_auto_add_margin: false },
  position_mode: { dual_side: false, mode: "ONE_WAY" },
  commission: { symbol: "BTCUSDT", maker: "0.0002", taker: "0.0005", source: "binance" },
  mark: { symbol: "BTCUSDT", mark_price: "84811.20", index_price: "84812.00",
          last_funding_rate: "0.0001", next_funding_time_ms: null },
  book: { best_bid: "84846.40", best_ask: "84846.50", bid_qty: "5", ask_qty: "5",
          spread: "0.10" },
  filters: null, krw: null,
  position_krw: { unrealized_pnl: "-98832.92", notional: "-9098546.26" },
  krw_per_usdt: "1341.00", krw_note: "",
  gates: { armed: true, env_flag: true, client_armed: true,
           env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
  stream: null, ...over,
});

const flat = (over: Partial<LiveAccount> = {}) => account({
  position: { symbol: "BTCUSDT", side: null, position_side: "BOTH", qty: "0", signed_qty: "0",
    entry_price: null, break_even_price: null, mark_price: "84811.20", unrealized_pnl: "0",
    liquidation_price: null, isolated_margin: "0", notional: "0", initial_margin: "0",
    maint_margin: "0", adl: 0, update_time_ms: null, is_flat: true },
  ...over,
});

const card = (over: Partial<LiveCardData> = {}): LiveCardData => ({
  open: true, available: true, symbol: "BTCUSDT", fetched_at_ms: 1, age_ms: 900,
  side: "SHORT", qty: "0.080", leverage: "20",
  entry_price: LIVE_ENTRY, break_even_price: LIVE_ENTRY, mark_price: "84859.31",
  liquidation_price: "87840.33", unrealized_pnl: "-73.70", notional: "-6788.74",
  exposure: "6788.74491304", exposure_basis: "BINANCE_POSITION_RISK_NOTIONAL_ABS",
  initial_margin: "339.43", maint_margin: "27.13",
  margin_basis: "BINANCE_POSITION_RISK_INITIAL_MARGIN",
  opened_at_ms: 1_790_854_724_746, opened_source: "BINANCE_USER_TRADES_WALKBACK",
  commission_paid: "3.35752200", realized_since_open: "0", funding_income: "0.46976187",
  close: { feasible: true, qty: "0.080",
           basis: "CLOSE_ENTIRE_POSITION_AT_MARKET_ON_THIS_BOOK_NOW",
           exit_fill_price: "84846.40", exit_fee: "3.39385600", gross_pnl: "-72.66800",
           fee_rate: "0.0005", reference_price: "84846.40", reject_code: null,
           reject_message: null },
  net_if_closed: "-78.94961613", net_basis: "CLOSE_ENTIRE_POSITION_AT_MARKET_ON_THIS_BOOK_NOW",
  net_complete: true,
  krw: { unrealized_pnl: "-98832.92", net_if_closed: "-105871.43",
         exposure: "9103706.69", initial_margin: "455175.63" },
  krw_per_usdt: "1341.00", ...over,
});

const preset = (label: string, qty: string, over: Record<string, unknown> = {}) => ({
  label, fraction: "0.25", qty, feasible: true, notional: "166.64",
  required_margin: "16.66", entry_fee: "0.083", required_total: "16.74",
  available_after: "357.44", entry_fill_price: "84846.40", exit_fill_price: "84846.30",
  reject_code: null, reject_message: null, ...over,
});

const refusedPresets = (code: string, message: string) =>
  ["25%", "HALF", "75%", "MAX"].map(label =>
    preset(label, "0", { feasible: false, reject_code: code, reject_message: message }));

const sideSizing = (over: Partial<LiveSideSizing> = {}): LiveSideSizing => ({
  side: "SHORT", leverage: "20", available_balance: "400", local_max_qty: null,
  max_qty: "0.095", max_feasible: true, reject_code: null, reject_message: null,
  max_definition: "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT_WITHIN_LOCAL_CEILING",
  instrument: { qty_step: "0.001", min_qty: "0.001", smallest_orderable_qty: "0.001",
                min_notional: "50", market_max_qty: "120" },
  presets: [preset("25%", "0.023"), preset("HALF", "0.047"), preset("75%", "0.071"),
            preset("MAX", "0.095")],
  ...over,
});

/** The exact server answer with a SHORT held: the held side sizes, the other side reverses. */
const holdingSizing = (over: Partial<LiveSizing> = {}): LiveSizing => ({
  source: "BINANCE_LIVE", available: true, symbol: "BTCUSDT", fetched_at_ms: 1, age_ms: 900,
  sides: {
    SHORT: sideSizing(),
    LONG: sideSizing({ side: "LONG", max_qty: "0", max_feasible: false,
                       reject_code: "REVERSE_NOT_ALLOWED",
                       reject_message: "SHORT 포지션 0.080이 열려 있습니다. 먼저 청산하세요.",
                       presets: refusedPresets("REVERSE_NOT_ALLOWED",
                         "SHORT 포지션 0.080이 열려 있습니다. 먼저 청산하세요.") }),
  },
  ...over,
});

const leverage = (over: Partial<LiveLeverageOptions> = {}): LiveLeverageOptions => ({
  symbol: "BTCUSDT", current: "20", margin_type: "CROSSED", max_leverage: 150,
  options: [1, 2, 3, 5, 10, 20, 50, 100],
  brackets: [{ bracket: 1, initialLeverage: 150, notionalCap: 300000, notionalFloor: 0,
               maintMarginRatio: 0.004, cum: 0 }],
  notional_coef: null, authority: "binance GET /fapi/v1/leverageBracket",
  margin_type_note: "", unavailable: {}, ...over,
});

const RESTRICTION = {
  above: 20, code: -4300, until_ms: 1_793_237_640_000,
  until_utc: "2026-10-29 01:34 UTC", observed_at_ms: 1,
  code_name: "ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE",
  message: "현재 계정에서 50x를 사용할 수 없습니다. 20x를 넘는 레버리지는 2026-10-29 01:34 UTC 이후에 열립니다.",
};

const preview = (over: Partial<LivePreview> = {}): LivePreview => ({
  source: "BINANCE_LIVE", symbol: "BTCUSDT", krw_per_usdt: "1341.00",
  mark_price: "84811.20", fetched_at_ms: 1,
  sides: {
    LONG: { side: "LONG", feasible: true, entry_fill_price: "84846.50",
            expected_entry_total_cost: "2.1100", breakeven_mark_price: "84900.00" },
    SHORT: { side: "SHORT", feasible: true, entry_fill_price: "84846.40",
             expected_entry_total_cost: "2.1200", breakeven_mark_price: "84780.00" },
  },
  ...over,
});


// ------------------------------------------------------------------ 1. the chart's entry line

describe("the LIVE chart's price lines", () => {
  it("draws the Binance entry price, not the paper account's average entry", () => {
    const lines = liveOverlays(account());
    const entry = lines.find(line => line.id === "entry");

    expect(entry?.price).toBe(Number(LIVE_ENTRY));
    expect(entry?.label).toBe("진입");
    expect(entry?.kind).toBe("ENTRY");
  });

  it("is a different line from the one the paper state would produce", () => {
    // Both accounts hold BTC within 25 USDT of each other, which is why the old line looked
    // plausible. This is the assertion that would have caught it.
    const paperState = {
      account: { position_side: "LONG", avg_entry: PAPER_ENTRY, liquidation_price: "0" },
      quote: { mark_price: "84820.00" },
    } as unknown as CryptoState;

    const paperEntry = positionOverlays(paperState).find(line => line.id === "entry")?.price;
    const liveEntry = liveOverlays(account()).find(line => line.id === "entry")?.price;

    expect(paperEntry).toBe(Number(PAPER_ENTRY));
    expect(liveEntry).toBe(Number(LIVE_ENTRY));
    expect(liveEntry).not.toBe(paperEntry);
  });

  it("takes the liquidation line from Binance and the mark from Binance too", () => {
    const lines = liveOverlays(account());
    expect(lines.find(line => line.id === "liquidation")?.price).toBe(87840.33);
    expect(lines.find(line => line.id === "mark")?.price).toBe(84811.20);
  });

  it("has no entry or liquidation line while flat, so the chart removes them", () => {
    const ids = liveOverlays(flat()).map(line => line.id);
    expect(ids).toEqual(["mark"]);
  });

  it("moves to the new entry on a re-entry instead of keeping the old cycle's", () => {
    const reopened = account({
      position: { ...account().position!, side: "LONG", signed_qty: "0.010", qty: "0.010",
                  entry_price: "84900.10" },
    });
    expect(liveOverlays(reopened).find(line => line.id === "entry")?.price).toBe(84900.10);
  });

  it("moves to Binance's weighted entry after a same-side add-on", () => {
    // 0.080 at 83938.05 plus 0.010 at 84800 is whatever Binance says it is; the point is that
    // the line follows `entryPrice` and this file never averages anything.
    const scaled = account({
      position: { ...account().position!, qty: "0.090", signed_qty: "-0.090",
                  entry_price: "84033.85" },
    });
    expect(liveOverlays(scaled).find(line => line.id === "entry")?.price).toBe(84033.85);
  });

  it("reports nothing at all for a missing account rather than a zero line", () => {
    expect(liveOverlays(null)).toEqual([]);
  });

  it("ignores a zero entry price, which is how Binance reports no position", () => {
    const zeroed = account({
      position: { ...account().position!, entry_price: "0", liquidation_price: "0" },
    });
    expect(zeroed.position!.is_flat).toBe(false);
    expect(liveOverlays(zeroed).map(line => line.id)).toEqual(["mark"]);
  });

  it("lets the chart panel be handed those lines instead of deriving its own", () => {
    const paperState = {
      account: { position_side: "LONG", avg_entry: PAPER_ENTRY, liquidation_price: "0" },
      quote: { mark_price: "84820.00", best_bid: "1", best_ask: "2", spread: "1", mid: "1" },
      state: {}, server_time_ms: 1,
    } as unknown as CryptoState;

    render(<ChartSection state={paperState} bars={[]} timeframe="1m" onTimeframe={vi.fn()}
      overlays={liveOverlays(account())} note="차트 캔들은 페이퍼 피드입니다." />);

    expect(screen.getByTestId("chart-source-note")).toHaveTextContent("페이퍼 피드");
  });
});


// ------------------------------------------------------------------ 2. same-side scale-in

describe("adding to a position already held", () => {
  it("allows the held side and blocks the other, flat allows both", () => {
    expect(liveSideAllowance(account().position)).toEqual({
      holding: "SHORT", allowed: ["SHORT"], blocked: "LONG" });
    expect(liveSideAllowance(flat().position)).toEqual({
      holding: null, allowed: ["LONG", "SHORT"], blocked: null });
  });

  it("sizes the held side instead of blanking the ladder on the other side's refusal", () => {
    // The defect: `REVERSE_NOT_ALLOWED` on LONG used to make every SHORT preset null.
    const bothSides = livePresetQty(holdingSizing(), "MAX");
    const heldSide = livePresetQty(holdingSizing(), "MAX", ["SHORT"]);

    expect(bothSides.qty).toBeNull();
    expect(bothSides.reason).toContain("먼저 청산");
    expect(heldSide.qty).toBe("0.095");
    expect(heldSide.reason).toBeNull();
  });

  it("keeps the both-sides minimum while flat, because one box feeds two buttons", () => {
    const asymmetric: LiveSizing = {
      source: "BINANCE_LIVE", available: true, sides: {
        LONG: sideSizing({ side: "LONG", presets: [preset("MAX", "0.010")] }),
        SHORT: sideSizing({ presets: [preset("MAX", "0.007")] }),
      },
    };
    expect(livePresetQty(asymmetric, "MAX").qty).toBe("0.007");
  });

  it("still refuses the held side when the margin is genuinely gone", () => {
    // The real account on 2026-10-02: SHORT 0.080 held with availableBalance 0.
    const maxedOut = holdingSizing({ sides: {
      ...holdingSizing().sides,
      SHORT: sideSizing({ max_qty: "0", max_feasible: false,
                          reject_code: "INSUFFICIENT_MARGIN",
                          reject_message: "필요 4.28 USDT가 주문가능 0 USDT를 넘습니다.",
                          presets: refusedPresets("INSUFFICIENT_MARGIN",
                            "필요 4.28 USDT가 주문가능 0 USDT를 넘습니다.") }),
    } });
    const answer = livePresetQty(maxedOut, "MAX", ["SHORT"]);

    expect(answer.qty).toBeNull();
    expect(answer.reason).toContain("주문가능");
  });

  it("leaves the quick-size buttons live for the held side", () => {
    render(<LiveQuickSize sizing={holdingSizing()} onPick={vi.fn()} sides={["SHORT"]} />);
    for (const label of ["25%", "HALF", "75%", "MAX"]) {
      expect(screen.getByTestId(`live-preset-${label}`)).toBeEnabled();
    }
    expect(screen.getByTestId("live-quick-size-addon-note")).toHaveTextContent("추가 진입");
  });

  it("says the sizes are add-ons rather than the position total", () => {
    render(<LiveQuickSize sizing={holdingSizing()} onPick={vi.fn()} sides={["SHORT"]} />);
    expect(screen.getByTestId("live-quick-size-addon-note"))
      .toHaveTextContent("기존 수량 합계가 아닙니다");
  });

  it("keeps the quantity input usable while a position is held", () => {
    render(<LiveOrderTicket account={account()} onOrder={vi.fn()} sizing={holdingSizing()} />);
    expect(screen.getByTestId("live-qty-input")).toBeEnabled();
    expect(screen.getByTestId("live-ticket-holding")).toHaveTextContent("0.080 BTC");
  });

  it("enables the held side's order button and disables the opposite one", () => {
    render(<LiveOrderTicket account={account()} onOrder={vi.fn()} sizing={holdingSizing()} />);
    expect(screen.getByTestId("live-short")).toBeEnabled();
    expect(screen.getByTestId("live-long")).toBeDisabled();
    expect(screen.getByTestId("live-opposite-blocked"))
      .toHaveTextContent("현재 포지션을 먼저 청산하세요");
  });

  it("enables both sides while flat", () => {
    render(<LiveOrderTicket account={flat()} onOrder={vi.fn()} sizing={holdingSizing()} />);
    expect(screen.getByTestId("live-long")).toBeEnabled();
    expect(screen.getByTestId("live-short")).toBeEnabled();
    expect(screen.queryByTestId("live-opposite-blocked")).toBeNull();
  });

  it("shows the entered quantity's own entry cost, for the openable side only", () => {
    render(<LiveOrderTicket account={account()} onOrder={vi.fn()} sizing={holdingSizing()}
      preview={preview()} />);

    expect(screen.getByTestId("live-preview-cost-SHORT")).toHaveTextContent("2.1200");
    expect(screen.queryByTestId("live-preview-cost-LONG")).toBeNull();
    expect(screen.getByTestId("live-preview")).toHaveTextContent("추가분 예상 진입 비용");
  });

  it("names the add-on in the confirmation rather than implying a fresh position", () => {
    render(<LiveOrderTicket account={account()} onOrder={vi.fn()} sizing={holdingSizing()} />);
    fireEvent.click(screen.getByTestId("live-short"));
    expect(screen.getByTestId("live-confirm")).toHaveTextContent("추가 진입");
    expect(screen.getByTestId("live-confirm")).toHaveTextContent("보유 0.080 BTC");
  });
});


// ------------------------------------------------------------------ 3. leverage

describe("the leverage ladder", () => {
  it("offers eight steps and no 기본 tag", () => {
    render(<LiveLeveragePanel account={flat()} options={leverage()} onSelect={vi.fn()} />);
    for (const value of [1, 2, 3, 5, 10, 20, 50, 100]) {
      expect(screen.getByTestId(`live-leverage-${value}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId("live-leverage-policy-tag")).toBeNull();
    expect(screen.queryByTestId("live-leverage-policy-note")).toBeNull();
    expect(screen.getByTestId("live-leverage-options")).not.toHaveTextContent("기본");
    expect(screen.getByTestId("live-leverage-panel")).not.toHaveTextContent("최대 150");
  });

  it("marks the current leverage by styling alone", () => {
    render(<LiveLeveragePanel account={flat()} options={leverage()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-leverage-20")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("live-leverage-10")).toHaveAttribute("aria-pressed", "false");
  });

  it("disables the steps Binance has refused for this account and says until when", () => {
    render(<LiveLeveragePanel account={flat()}
      options={leverage({ unavailable: { "50": RESTRICTION,
                                         "100": { ...RESTRICTION, message: "현재 계정에서 100x를 사용할 수 없습니다. 20x를 넘는 레버리지는 2026-10-29 01:34 UTC 이후에 열립니다." } } })}
      onSelect={vi.fn()} />);

    expect(screen.getByTestId("live-leverage-50")).toBeDisabled();
    expect(screen.getByTestId("live-leverage-100")).toBeDisabled();
    expect(screen.getByTestId("live-leverage-20")).toBeEnabled();
    expect(screen.getByTestId("live-leverage-restricted"))
      .toHaveTextContent("2026-10-29 01:34 UTC");
  });

  it("does not hardcode 100x as supported or as unsupported", () => {
    // Nothing is claimed until the server says something. A ladder with no `unavailable` map
    // offers every step; the restriction is data, not a constant in the component.
    render(<LiveLeveragePanel account={flat()} options={leverage()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("live-leverage-100")).toBeEnabled();
    expect(screen.queryByTestId("live-leverage-restricted")).toBeNull();
    expect(leverageRestriction(leverage(), 100)).toBeNull();
  });

  it("drops the steps the symbol's bracket table does not reach", () => {
    render(<LiveLeveragePanel account={flat()}
      options={leverage({ max_leverage: 20, options: [1, 2, 3, 5, 10, 20] })}
      onSelect={vi.fn()} />);
    expect(screen.queryByTestId("live-leverage-50")).toBeNull();
    expect(screen.queryByTestId("live-leverage-100")).toBeNull();
  });

  it("disables every step while a position is held, which is unchanged policy", () => {
    render(<LiveLeveragePanel account={account()} options={leverage()} onSelect={vi.fn()} />);
    for (const value of [1, 10, 20, 50, 100]) {
      expect(screen.getByTestId(`live-leverage-${value}`)).toBeDisabled();
    }
    expect(screen.getByTestId("live-leverage-locked")).toHaveTextContent("포지션 보유 중");
  });
});


// ------------------------------------------------------------------ 4. exposure and parity

describe("포지션 규모 and the two panels", () => {
  it("shows the exposure in KRW with USDT underneath, on the phone card", () => {
    render(<LivePositionCard card={card()} nowMs={1_790_854_800_000} onClose={vi.fn()} />);
    expect(screen.getByTestId("live-card-exposure-krw")).toHaveTextContent("9,103,707원");
    expect(screen.getByTestId("live-card-exposure")).toHaveTextContent("6,788.74 USDT");
  });

  it("shows the same exposure on the desktop panel", () => {
    render(<LivePositionPanel account={account()} card={card()} />);
    expect(screen.getByTestId("live-position-exposure-krw")).toHaveTextContent("9,103,707원");
    expect(screen.getByTestId("live-position-exposure")).toHaveTextContent("6,788.74 USDT");
  });

  it("shows the exposure as a positive figure for a SHORT", () => {
    // Binance's `notional` is -6788.74 here. A size is not a direction; the badge is.
    render(<LivePositionPanel account={account()} card={card()} />);
    expect(screen.getByTestId("live-position-exposure")).not.toHaveTextContent("-");
  });

  it("shows no exposure block while flat", () => {
    render(<LivePositionPanel account={flat()} card={{ open: false }} />);
    expect(screen.queryByTestId("live-position-exposure")).toBeNull();
  });

  it("keeps 증거금 and 포지션 규모 as separate figures on the desktop panel", () => {
    render(<LivePositionPanel account={account()} card={card()} />);
    const panel = screen.getByTestId("live-position-panel");
    expect(panel).toHaveTextContent("포지션 규모");
    expect(panel).toHaveTextContent("증거금");
    expect(panel).toHaveTextContent("339.43 USDT");   // initialMargin, 20x of the exposure
  });

  it("shows 청산 시 예상 순손익 on the desktop panel, which it used not to", () => {
    render(<LivePositionPanel account={account()} card={card()} />);
    expect(screen.getByTestId("live-position-net-krw")).toHaveTextContent("-105,871원");
    expect(screen.getByTestId("live-position-net")).toHaveTextContent("-78.9496 USDT");
  });

  it("prints the identical expected close net on desktop and phone", () => {
    // Parity by construction: both read `net_if_closed` off the same card. The assertion is
    // that neither panel adjusts it.
    const shared = card();
    const { unmount } = render(<LivePositionPanel account={account()} card={shared} />);
    const desktop = screen.getByTestId("live-position-net").textContent;
    const desktopKrw = screen.getByTestId("live-position-net-krw").textContent;
    unmount();

    render(<LivePositionCard card={shared} nowMs={1_790_854_800_000} onClose={vi.fn()} />);
    expect(screen.getByTestId("live-card-net").textContent).toBe(desktop);
    expect(screen.getByTestId("live-card-net-krw").textContent).toBe(desktopKrw);
  });

  it("withholds the net on both panels when the server could not price the close", () => {
    const noLiquidity = card({
      net_if_closed: null, net_complete: false,
      close: { feasible: false, qty: "0.080", basis: "x", reject_code: "NO_LIQUIDITY",
               reject_message: "호가가 수량을 소화하지 못합니다." },
    });
    const { unmount } = render(<LivePositionPanel account={account()} card={noLiquidity} />);
    expect(screen.getByTestId("live-position-net-unavailable"))
      .toHaveTextContent("호가가 수량을 소화하지 못합니다.");
    unmount();

    render(<LivePositionCard card={noLiquidity} nowMs={1} onClose={vi.fn()} />);
    expect(screen.getByTestId("live-card-net-unavailable"))
      .toHaveTextContent("호가가 수량을 소화하지 못합니다.");
  });

  it("falls back to the account snapshot when the card has not arrived yet", () => {
    render(<LivePositionPanel account={account()} card={null} />);
    expect(screen.getByTestId("live-position-side")).toHaveTextContent("SHORT");
    expect(screen.getByTestId("live-position-qty")).toHaveTextContent("0.080 BTC");
    expect(screen.getByTestId("live-position-net-unavailable")).toBeInTheDocument();
  });
});


// ------------------------------------------------------------------ 5. auto exit + scale-in

describe("자동청산 after a same-side add-on", () => {
  const guard = (over: Partial<LiveExitGuard> = {}): LiveExitGuard => ({
    state: "ARMED", enabled: true, symbol: "BTCUSDT", side: "SHORT",
    position_qty: "0.080", configured_qty: "0.080", scaled_in: false, opened_at_ms: 1,
    take_profit_krw: "50000", stop_loss_krw: "30000",
    current_net_usdt: "-78.94", current_net_krw: "-105871",
    created_at_ms: 1, updated_at_ms: 2, last_error: null, ...over,
  });

  it("stays on after a scale-in and says the size moved", () => {
    // Option A, and deliberately not B. The thresholds are amounts of money, the guard's
    // identity is the side and the opening fill (an add-on changes neither), and the CLOSE it
    // sends re-reads the real position with `reduceOnly`. Switching the guard off instead
    // would leave a position its owner believes is protected with no protection.
    render(<LiveAutoExit guard={guard({ position_qty: "0.090", scaled_in: true })}
      onSave={vi.fn()} onDisable={vi.fn()} />);

    expect(screen.getByTestId("live-auto-exit")).toHaveTextContent("ON");
    expect(screen.getByTestId("exit-scaled-in")).toHaveTextContent("0.080 → 0.090");
    expect(screen.getByTestId("exit-position-qty")).toHaveTextContent("0.090 BTC");
  });

  it("says nothing when the size has not moved", () => {
    render(<LiveAutoExit guard={guard()} onSave={vi.fn()} onDisable={vi.fn()} />);
    expect(screen.queryByTestId("exit-scaled-in")).toBeNull();
  });

  it("reads its current net from the same figure the panels show", () => {
    render(<LiveAutoExit guard={guard()} onSave={vi.fn()} onDisable={vi.fn()} />);
    expect(screen.getByTestId("exit-current-net")).toHaveTextContent("-105,871원");
  });
});
