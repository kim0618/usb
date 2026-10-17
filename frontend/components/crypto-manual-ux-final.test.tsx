import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { OrderTicket } from "@/components/crypto-paper-terminal";
import { LiveOrderTicket } from "@/components/crypto-live-terminal";
import { executionMarkers, executionsFor, orderAmounts } from "@/lib/crypto-executions";
import type { Execution } from "@/lib/crypto-executions";
import type { CryptoSizing, CryptoState } from "@/lib/crypto-paper";
import type { LiveAccount, LiveSizing } from "@/lib/crypto-live";
import { ARM_CONFIRMATION } from "@/lib/crypto-live";
import paperTerminal from "@/components/__fixtures__/crypto-terminal-paper.json";

/** Manual UX Final V1 on the screen side: PAPER and LIVE share a layout and a symbol, never an
 *  account; every quick size, amount and marker comes from the account on screen. */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: ({ markers = [] }: { markers?: { id: string; text: string }[] }) =>
    React.createElement("div", { "data-testid": "candle-chart-stub",
                                 "data-markers": markers.map(m => m.id).join("|") }),
}));

type Paper = Record<string, Record<string, unknown>>;
const paper = paperTerminal as unknown as Paper;
const paperState = (over: Partial<CryptoState> = {}) =>
  ({ ...structuredClone(paper.BTCUSDT.state as CryptoState), ...over }) as CryptoState;

// ---------------------------------------------------------------- PAPER: 10,000 USDT account
function paperSizing(): CryptoSizing {
  const body = structuredClone(paper.BTCUSDT.sizing) as unknown as CryptoSizing & Record<string, unknown>;
  body.available_balance = "10000";
  const ladder = { "25%": "0.290", HALF: "0.580", "75%": "0.870", MAX: "1.160" } as Record<string, string>;
  for (const side of ["LONG", "SHORT"] as const) {
    const sideBody = body.sides![side]! as unknown as { presets: { label: string; qty: string; feasible: boolean }[] };
    for (const row of sideBody.presets) { row.qty = ladder[row.label]; row.feasible = true; }
  }
  return body;
}

// ---------------------------------------------------------------- LIVE: 500 USDT available
const LIVE_LADDER = { "25%": "0.001", HALF: "0.002", "75%": "0.004", MAX: "0.005" } as Record<string, string>;
function liveSizing(): LiveSizing {
  const side = (s: "LONG" | "SHORT") => ({
    side: s, available: true, max_qty: LIVE_LADDER.MAX,
    instrument: { smallest_orderable_qty: "0.001" },
    presets: Object.entries(LIVE_LADDER).map(([label, qty]) => ({ label, qty, feasible: true })),
  });
  return { source: "BINANCE_LIVE", available: true, symbol: "BTCUSDT",
           sides: { LONG: side("LONG"), SHORT: side("SHORT") } } as unknown as LiveSizing;
}

function liveAccount({ held = null as null | { side: "LONG" | "SHORT"; qty: string; entry: string },
                       armed = false } = {}): LiveAccount {
  return {
    symbol: "BTCUSDT", ready: true, blockers: [], fetched_at_ms: 1, age_ms: 900, stale: false,
    source: "BINANCE_LIVE",
    balance: { asset: "USDT", wallet_balance: "500", available_balance: "500", margin_balance: "500",
      unrealized_pnl: "0", max_withdraw: "500", initial_margin: "0", maint_margin: "0",
      account_wallet_usd: "500", account_available_usd: "500", account_margin_usd: "500",
      account_unrealized_usd: "0", usd_valuation_ratio: "1", update_time_ms: 1, balance_source: "binance" },
    position: held
      ? { symbol: "BTCUSDT", side: held.side, position_side: "BOTH", qty: held.qty,
          signed_qty: held.side === "LONG" ? held.qty : `-${held.qty}`, entry_price: held.entry,
          break_even_price: held.entry, mark_price: "100000", unrealized_pnl: "0",
          liquidation_price: null, isolated_margin: null, notional: "1000", initial_margin: "50",
          maint_margin: "4", adl: 0, update_time_ms: 1, is_flat: false }
      : { symbol: "BTCUSDT", side: null, position_side: "BOTH", qty: "0", signed_qty: "0",
          entry_price: null, break_even_price: null, mark_price: "100000", unrealized_pnl: "0",
          liquidation_price: null, isolated_margin: null, notional: "0", initial_margin: "0",
          maint_margin: "0", adl: 0, update_time_ms: null, is_flat: true },
    symbol_config: { symbol: "BTCUSDT", margin_type: "CROSSED", leverage: "20", max_notional: null,
                     is_auto_add_margin: false },
    position_mode: { dual_side: false, mode: "ONE_WAY" },
    commission: { symbol: "BTCUSDT", maker: "0.0002", taker: "0.0005", source: "binance" },
    mark: { symbol: "BTCUSDT", mark_price: "100000", index_price: "100000",
            last_funding_rate: "0.0001", next_funding_time_ms: null },
    book: { best_bid: "99999.9", best_ask: "100000.1", bid_qty: "1", ask_qty: "1", spread: "0.2" },
    filters: { min_qty: "0.001", market_min_qty: "0.001" },
    krw: null, position_krw: null, krw_per_usdt: "1420", krw_note: "",
    gates: { armed, env_flag: true, client_armed: armed, env_flag_name: "BINANCE_LIVE_TRADING_ENABLED" },
    stream: null,
  } as unknown as LiveAccount;
}

// ================================================================ executions -> markers

const exec = (seq: number, over: Partial<Execution> = {}): Execution => ({
  seq, ts_ms: 1_000_000 + seq * 60_000, mode: "PAPER", symbol: "BTCUSDT", account: "run-1",
  side: "LONG", event_type: "ADD_ENTRY", price: "84000", qty: "0.01", leverage: "20", ...over,
});

describe("execution markers", () => {
  it("one marker per fill: initial, two adds and a partial close", () => {
    const rows = [exec(1, { event_type: "INITIAL_ENTRY", price: "84000", qty: "0.01" }),
                  exec(2, { price: "83500", qty: "0.02" }), exec(3, { price: "82900", qty: "0.01" }),
                  exec(4, { event_type: "PARTIAL_CLOSE", price: "85100", qty: "0.015" })];
    const candles = Array.from({ length: 10 }, (_, i) => 1_000 + i * 60);
    const markers = executionMarkers(rows, candles);
    expect(markers).toHaveLength(4);
    expect(markers.map(m => m.text)).toEqual([
      "#1 LONG 0.01 @ 84000", "#2 LONG 0.02 @ 83500",
      "#3 LONG 0.01 @ 82900", "LONG 부분청산 0.015 @ 85100"]);
    expect(markers.slice(0, 3).every(m => m.shape === "arrowUp" && m.position === "belowBar")).toBe(true);
    expect(markers[3].position).toBe("aboveBar");
    // Every marker sits on a candle that exists, at or before the fill.
    for (const [index, marker] of markers.entries()) {
      expect(candles).toContain(marker.time);
      expect(marker.time).toBeLessThanOrEqual(Math.floor(rows[index].ts_ms / 1000));
    }
  });

  it("keeps another mode, symbol or account off the chart and drops duplicates", () => {
    const rows = [exec(1), exec(1), exec(2, { mode: "LIVE" }), exec(3, { symbol: "ARCUSDT" }),
                  exec(4, { account: "other-run" })];
    expect(executionsFor(rows, { mode: "PAPER", symbol: "BTCUSDT", account: "run-1" }).map(r => r.seq))
      .toEqual([1]);
    expect(executionsFor(rows, { mode: "LIVE", symbol: "BTCUSDT" }).map(r => r.seq)).toEqual([2]);
    expect(executionsFor(rows, { mode: "PAPER", symbol: "ARCUSDT" }).map(r => r.seq)).toEqual([3]);
  });

  it("leaves a fill older than the first candle off rather than misplacing it", () => {
    expect(executionMarkers([exec(1, { ts_ms: 0 })], [1_000, 1_060])).toEqual([]);
  });
});

// ================================================================ amounts

describe("order amounts", () => {
  it("notional, margin and fee in USDT and KRW at the account's own leverage and fee", () => {
    const a = orderAmounts({ qty: "0.010", price: "100000", leverage: "20", takerRate: "0.0005",
                             krwPerUsdt: "1420" })!;
    expect(a.notional_usdt).toBeCloseTo(1000);
    expect(a.notional_krw).toBeCloseTo(1_420_000);
    expect(a.margin_usdt).toBeCloseTo(50);
    expect(a.margin_krw).toBeCloseTo(71_000);
    expect(a.fee_usdt).toBeCloseTo(0.5);
    expect(a.fee_krw).toBeCloseTo(710);
    expect(orderAmounts({ qty: "0", price: "1", leverage: "1", takerRate: "0", krwPerUsdt: null })).toBeNull();
  });

  it("the notional is qty x price; leverage moves only the margin", () => {
    const at = (leverage: string | null) => orderAmounts({ qty: "0.010", price: "100000", leverage,
                                                           takerRate: "0.0005", krwPerUsdt: "1420" })!;
    expect(at("5").notional_krw).toBe(at("100").notional_krw);
    expect(at("5").margin_usdt).toBeCloseTo(at("100").margin_usdt! * 20);
    expect(at(null).notional_krw).toBeCloseTo(1_420_000);
    expect(at(null).margin_usdt).toBeNull();
  });
});

// ================================================================ PAPER ticket

const paperTicket = (state: CryptoState, sizing: CryptoSizing | null = paperSizing()) =>
  render(<OrderTicket state={state} sizing={sizing} onAction={vi.fn()} busy={false} error={null}
    previewOverride={null} />);
const text = (id: string) => screen.getByTestId(id).textContent ?? "";

const won = (value: number) => `₩${Math.round(value).toLocaleString("ko-KR")}`;

describe("PAPER ticket", () => {
  it("offers 5/10/20/50/100x on PAPER's own ceiling even where the instrument stops at 20x", () => {
    const state = paperState({ paper_max_leverage: "100",
      instrument: { ...paperState().instrument!, max_leverage: "20.00" } });
    paperTicket(state);
    for (const value of ["5", "10", "20", "50", "100"]) {
      expect(screen.getByTestId(`paper-leverage-${value}`)).toBeEnabled();
    }
  });

  it("shows only leverage, quick sizes, the selected quantity, its KRW size and the buttons", () => {
    paperTicket(paperState());
    const ticket = screen.getByTestId("order-ticket");
    expect(screen.queryByTestId("order-preview")).toBeNull();
    expect(screen.queryByTestId("order-arming")).toBeNull();
    for (const word of ["주문 미리보기", "예상 진입 수수료", "예상 청산 수수료", "왕복 총비용", "손익분기",
                        "필요 증거금", "USDT 약"]) {
      expect(ticket).not.toHaveTextContent(word);
    }
    expect(screen.getByTestId("paper-order-amount-qty")).toBeInTheDocument();
    expect(screen.getByTestId("paper-order-amount-notional").textContent).toMatch(/^₩[\d,]+$/);
  });

  it("PAPER MAX, HALF, 25%, 75% and a typed size: KRW notional = qty x mark x the run's rate", () => {
    const state = paperState({ leverage: "10" });
    paperTicket(state);
    const mark = Number(state.quote!.mark_price);
    const fx = Number(state.fx.krw_per_usdt);
    for (const [label, qty] of [["MAX", "1.160"], ["HALF", "0.580"], ["25%", "0.290"], ["75%", "0.870"]]) {
      fireEvent.click(screen.getByTestId(`preset-${label}`));
      expect(text("paper-order-amount-qty")).toBe(`${Number(qty)} BTC`);
      expect(text("paper-order-amount-notional")).toBe(won(Number(qty) * mark * fx));
      // 내가 거는 금액 = the notional over the PAPER leverage (10x here).
      expect(text("paper-order-amount-margin")).toBe(won(Number(qty) * mark * fx / 10));
    }
    fireEvent.click(screen.getByTestId("preset-clear"));
    fireEvent.change(screen.getByLabelText("주문 크기"), { target: { value: "0.123" } });
    expect(text("paper-order-amount-notional")).toBe(won(0.123 * mark * fx));
    expect(text("paper-order-amount-margin")).toBe(won(0.123 * mark * fx / 10));
  });

  it("a leverage change moves 내가 거는 금액 and leaves the notional of the same quantity alone", () => {
    const base = paperState({ leverage: "10" });
    const view = paperTicket(base);
    fireEvent.change(screen.getByLabelText("주문 크기"), { target: { value: "0.100" } });
    const notional = 0.1 * Number(base.quote!.mark_price) * Number(base.fx.krw_per_usdt);
    const at10 = text("paper-order-amount-notional");
    expect(text("paper-order-amount-margin")).toBe(won(notional / 10));
    for (const leverage of ["5", "20", "50", "100"]) {
      view.rerender(<OrderTicket state={paperState({ leverage })} sizing={paperSizing()}
        onAction={vi.fn()} busy={false} error={null} previewOverride={null} />);
      expect(text("paper-order-amount-notional")).toBe(at10);
      expect(text("paper-order-amount-margin")).toBe(won(notional / Number(leverage)));
    }
  });

  it("a price change or a symbol change re-reads the KRW notional", () => {
    const base = paperState();
    const view = paperTicket(base);
    fireEvent.change(screen.getByLabelText("주문 크기"), { target: { value: "0.100" } });
    const fx = Number(base.fx.krw_per_usdt);
    const moved = paperState({ quote: { ...base.quote!, mark_price: "90000" } });
    view.rerender(<OrderTicket state={moved} sizing={paperSizing()} onAction={vi.fn()} busy={false}
      error={null} previewOverride={null} />);
    expect(text("paper-order-amount-notional")).toBe(won(0.1 * 90000 * fx));
    const eth = paperState({ symbol: "ETHUSDT", quote: { ...base.quote!, mark_price: "3000" },
      instrument: { ...base.instrument!, symbol: "ETHUSDT", qty_step: "0.01" } });
    view.rerender(<OrderTicket key="ETHUSDT" state={eth} sizing={paperSizing()} onAction={vi.fn()}
      busy={false} error={null} previewOverride={null} />);
    fireEvent.change(screen.getByLabelText("주문 크기"), { target: { value: "0.50" } });
    expect(text("paper-order-amount-qty")).toBe("0.5 ETH");
    expect(text("paper-order-amount-notional")).toBe(won(0.5 * 3000 * fx));
    expect(text("paper-order-amount-margin")).toBe(won(0.5 * 3000 * fx / Number(eth.leverage)));
  });

  it("a held LONG leaves LONG (add), SHORT (reduce) and every quick size usable", () => {
    const state = paperState();
    state.account = { ...state.account!, position_side: "LONG", position_qty: "0.050",
                      avg_entry: "84000" };
    paperTicket(state);
    expect(screen.getByTestId("long-button")).toBeEnabled();
    expect(screen.getByTestId("long-button")).toHaveTextContent("추가");
    expect(screen.getByTestId("short-button")).toBeEnabled();
    expect(screen.getByTestId("short-button")).toHaveTextContent("감소");
    for (const label of ["25%", "HALF", "75%", "MAX"]) {
      expect(screen.getByTestId(`preset-${label}`)).toBeEnabled();
    }
    expect(screen.getByLabelText("주문 크기")).toBeEnabled();
  });
});

// ================================================================ LIVE ticket

describe("LIVE ticket", () => {
  it("LIVE MAX (and every quick size): KRW notional from Binance's mark and the display rate", () => {
    render(<LiveOrderTicket account={liveAccount()} onOrder={vi.fn()} sizing={liveSizing()} />);
    for (const label of ["MAX", "HALF", "25%", "75%"]) {
      const qty = LIVE_LADDER[label];
      fireEvent.click(screen.getByTestId(`live-preset-${label}`));
      expect(screen.getByTestId("live-qty-input")).toHaveValue(qty);
      expect(text("live-order-amount-notional")).toBe(won(Number(qty) * 100_000 * 1420));
      // Binance's own leverage for the symbol (20x), not PAPER's.
      expect(text("live-order-amount-margin")).toBe(won(Number(qty) * 100_000 * 1420 / 20));
    }
    const ticket = screen.getByTestId("live-order-ticket");
    for (const word of ["예상 진입", "손익분기", "필요 증거금", "수수료"]) expect(ticket).not.toHaveTextContent(word);
  });

  it("LIVE leverage does not move the notional; the mark does", () => {
    const view = render(<LiveOrderTicket account={liveAccount()} onOrder={vi.fn()} sizing={liveSizing()} />);
    fireEvent.click(screen.getByTestId("live-preset-MAX"));
    const before = text("live-order-amount-notional");
    const fifty = liveAccount();
    fifty.symbol_config = { ...fifty.symbol_config!, leverage: "50" } as never;
    view.rerender(<LiveOrderTicket account={fifty} onOrder={vi.fn()} sizing={liveSizing()} />);
    expect(text("live-order-amount-notional")).toBe(before);
    expect(text("live-order-amount-margin")).toBe(won(0.005 * 100_000 * 1420 / 50));
    const moved = liveAccount();
    moved.mark = { ...moved.mark!, mark_price: "90000" } as never;
    moved.position = { ...moved.position!, mark_price: "90000" } as never;
    view.rerender(<LiveOrderTicket account={moved} onOrder={vi.fn()} sizing={liveSizing()} />);
    expect(text("live-order-amount-notional")).toBe(won(0.005 * 90_000 * 1420));
  });

  it("a held LONG keeps adding and lets SHORT reduce, with the presets still live", () => {
    render(<LiveOrderTicket account={liveAccount({ held: { side: "LONG", qty: "0.010", entry: "84000" } })}
      onOrder={vi.fn()} sizing={liveSizing()} />);
    for (const label of ["25%", "HALF", "75%", "MAX"]) {
      expect(screen.getByTestId(`live-preset-${label}`)).toBeEnabled();
    }
    expect(screen.getByTestId("live-qty-input")).toBeEnabled();
    expect(screen.getByTestId("live-long")).toHaveTextContent("LONG 추가");
    expect(screen.getByTestId("live-short")).toHaveTextContent("SHORT · 감소");
  });
});

// ================================================================ the whole page: one symbol, two accounts

function backends() {
  const calls: { url: string; method: string }[] = [];
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const dead = { ok: false, status: 503, json: async () => ({ error: { code: "OFFLINE", message: "x" } }) };
  const hot = { current: null as string | null };
  const hotState = () => ({
    main: "BTCUSDT", hot: hot.current ? { symbol: hot.current, selected_at_ms: 1, scanned_at_ms: Date.now(),
                                          metrics: {}, top: [] } : null,
    lock: { state: "ALLOWED", code: "HOT_SYMBOL_FLAT", symbol: hot.current, message: "" },
    scanning: false, held: [], permitted: [], paper: { available: true, reason: null }, server_time_ms: 0,
  });
  const sequence = ["ARCUSDT", "SOLUSDT"];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method });
    const symbol = new URL(url, "http://x").searchParams.get("symbol") ?? "BTCUSDT";
    if (url.includes("/api/crypto/hot/scan")) {
      hot.current = sequence.shift() ?? hot.current;
      return ok({ ...hotState(), status: "HOT_REPLACED", changed: true, previous: null, paper_note: null, scan: null });
    }
    if (url.includes("/api/crypto/hot")) return ok(hotState());
    if (url.includes("/binance/status")) {
      return ok({ source: "BINANCE_LIVE", available: true, ready: true, blockers: [],
                  gates: liveAccount().gates, config: {}, runtime_error: null, endpoints: [] });
    }
    if (url.includes("/binance/account")) return ok({ ...liveAccount(), symbol });
    if (url.includes("/binance/arm")) {
      return ok({ armed: false, armed_by: null, armed_at_ms: null, expires_at_ms: null, remaining_s: null,
                  ttl_s: null, arm_count: 0, disarm_count: 0, last_disarm_reason: "BOOT", last_disarm_ms: null,
                  operator_note: null, confirmation_phrase: ARM_CONFIRMATION, env_armed: false,
                  role: "MANUAL_ONLY", available: true, capability: true });
    }
    if (url.includes("/binance/sizing")) return ok({ ...liveSizing(), symbol });
    if (url.includes("/binance/executions")) {
      return ok({ mode: "LIVE", symbol, account: "acct", executions: [
        { seq: 1, ts_ms: 1_791_170_000_000, mode: "LIVE", symbol, account: "acct", side: "LONG", event_type: "INITIAL_ENTRY",
          price: "84000", qty: "0.010", leverage: "20" }] });
    }
    if (url.includes("/binance/")) return dead;
    const rows = paper.BTCUSDT;
    const relabel = (body: unknown) => (body && typeof body === "object" ? { ...(body as object), symbol } : body);
    if (url.includes("/api/crypto/executions")) {
      return ok({ mode: "PAPER", symbol, account: "run", executions: [
        { seq: 1, ts_ms: 1_791_170_000_000, mode: "PAPER", symbol, account: "run", side: "SHORT", event_type: "INITIAL_ENTRY",
          price: "85000", qty: "0.020", leverage: "10" }] });
    }
    if (url.includes("/api/crypto/chart-history")) return ok(relabel(rows.chart_history));
    if (url.includes("/api/crypto/chart")) return ok({ symbol, bars: [] });
    if (url.includes("/api/crypto/state")) return ok(relabel(rows.state));
    if (url.includes("/api/crypto/sizing")) return ok(relabel(paperSizing()));
    if (url.includes("/api/crypto/performance")) return ok(rows.performance);
    if (url.includes("/api/crypto/ledger")) return ok(rows.ledger);
    if (url.includes("/api/crypto/pnl-breakdown")) return ok(rows.pnl_breakdown);
    return dead;
  }));
  return { calls, hot };
}

function stubViewport(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const matches = min ? width >= Number(min[1]) : false;
    return { matches, media: query, addEventListener: () => {}, removeEventListener: () => {},
             addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false };
  });
}

const selectedSymbol = () => screen.getAllByRole("tab").find(tab => tab.getAttribute("aria-selected") === "true")
  ?.getAttribute("data-symbol") ?? screen.getAllByRole("tab").find(tab => tab.getAttribute("aria-selected") === "true")
  ?.getAttribute("data-testid");

describe("PAPER <-> LIVE on one page", () => {
  it("ten round trips: each mode reads only its own account, markers and sizes; no write", async () => {
    stubViewport(1440);
    const { calls } = backends();
    render(<CryptoTerminal />);
    await waitFor(() => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });
    await waitFor(() => expect(screen.getByTestId("source-BINANCE_LIVE")).toBeEnabled());
    for (let round = 0; round < 10; round++) {
      // PAPER
      await waitFor(() => expect(screen.getAllByTestId("order-ticket").length).toBeGreaterThan(0));
      const paperTicketNode = screen.getAllByTestId("order-ticket").at(-1)!;
      await waitFor(() => expect(within(paperTicketNode).getByTestId("preset-MAX")).toBeEnabled());
      fireEvent.click(within(paperTicketNode).getByTestId("preset-MAX"));
      expect(within(paperTicketNode).getByTestId("paper-order-amount-qty")).toHaveTextContent("1.16 BTC");
      // PAPER margin is at the PAPER run's leverage (fixture 10x), never Binance's 20x.
      const paperState0 = paper.BTCUSDT.state as CryptoState;
      expect(within(paperTicketNode).getByTestId("paper-order-amount-margin")).toHaveTextContent(
        won(1.16 * Number(paperState0.quote!.mark_price) * Number(paperState0.fx.krw_per_usdt) / Number(paperState0.leverage)));
      await waitFor(() => expect(screen.getAllByTestId("candle-chart-stub")[0].getAttribute("data-markers"))
        .toBe("PAPER:run:1"));
      expect(screen.queryByTestId("live-order-ticket")).toBeNull();

      const beforeLive = calls.length;
      fireEvent.click(screen.getByTestId("source-BINANCE_LIVE"));
      await waitFor(() => expect(screen.getByTestId("live-order-ticket")).toBeInTheDocument());
      await waitFor(() => expect(screen.getByTestId("live-preset-MAX")).toBeEnabled());
      fireEvent.click(screen.getByTestId("live-preset-MAX"));
      expect(screen.getByTestId("live-qty-input")).toHaveValue("0.005");
      expect(screen.getByTestId("live-order-amount-notional")).toHaveTextContent(won(0.005 * 100_000 * 1420));
      expect(screen.getByTestId("live-order-amount-margin")).toHaveTextContent(won(0.005 * 100_000 * 1420 / 20));
      await waitFor(() => expect(screen.getAllByTestId("candle-chart-stub")[0].getAttribute("data-markers"))
        .toBe("LIVE:acct:1"));
      expect(screen.queryByTestId("order-ticket")).toBeNull();
      // On the LIVE screen nothing asks the PAPER account for sizing or fills.
      const liveCalls = calls.slice(beforeLive).map(call => call.url);
      expect(liveCalls.some(url => /\/api\/crypto\/(sizing|executions|ledger|performance)\?/.test(url))).toBe(false);

      const beforePaper = calls.length;
      fireEvent.click(screen.getByTestId("source-PAPER"));
      await waitFor(() => expect(screen.getAllByTestId("order-ticket").length).toBeGreaterThan(0));
      await new Promise(resolve => setTimeout(resolve, 20));
      // And on the PAPER screen nothing reads the Binance account's sizing or fills.
      const paperCalls = calls.slice(beforePaper).map(call => call.url);
      expect(paperCalls.some(url => /\/binance\/(sizing|executions|account)/.test(url))).toBe(false);
    }
    expect(calls.filter(call => call.method !== "GET" && call.method !== "HEAD")).toEqual([]);
  }, 60_000);

  it("HOT is one global symbol: rank #1 becomes the selection on PAPER and on LIVE", async () => {
    stubViewport(1440);
    const { calls } = backends();
    render(<CryptoTerminal />);
    await waitFor(() => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });
    await waitFor(() => expect(screen.getByTestId("hot-scan-button")).toBeEnabled());
    // PAPER: HOT -> ARCUSDT (#1). Rank 2 and 3 are never listed.
    fireEvent.click(screen.getByTestId("hot-scan-button"));
    await waitFor(() => expect(screen.getByTestId("symbol-tab-hot").getAttribute("aria-selected")).toBe("true"));
    expect(screen.getByTestId("symbol-tab-hot").getAttribute("data-symbol")).toBe("ARCUSDT");
    expect(screen.queryByTestId("hot-list")).toBeNull();
    await waitFor(() => expect(calls.some(call => call.url.includes("/api/crypto/state?symbol=ARCUSDT"))).toBe(true));
    // LIVE sees the same selection without doing anything.
    await waitFor(() => expect(screen.getByTestId("source-BINANCE_LIVE")).toBeEnabled());
    fireEvent.click(screen.getByTestId("source-BINANCE_LIVE"));
    await waitFor(() => expect(calls.some(call => call.url.includes("/binance/account?symbol=ARCUSDT"))).toBe(true));
    expect(screen.getByTestId("symbol-tab-hot").getAttribute("aria-selected")).toBe("true");
    // LIVE: HOT -> SOLUSDT; back on PAPER it is SOLUSDT too.
    await waitFor(() => expect(screen.getByTestId("hot-scan-button")).toBeEnabled());
    fireEvent.click(screen.getByTestId("hot-scan-button"));
    await waitFor(() => expect(screen.getByTestId("symbol-tab-hot").getAttribute("data-symbol")).toBe("SOLUSDT"));
    await waitFor(() => expect(screen.getByTestId("symbol-tab-hot").getAttribute("aria-selected")).toBe("true"));
    fireEvent.click(screen.getByTestId("source-PAPER"));
    await waitFor(() => expect(calls.some(call => call.url.includes("/api/crypto/state?symbol=SOLUSDT"))).toBe(true));
    expect(screen.getByTestId("symbol-tab-hot").getAttribute("aria-selected")).toBe("true");
    expect(selectedSymbol()).toBeTruthy();
    // Switching the symbol is a view change: the only write was the two scans.
    expect(calls.filter(call => call.method !== "GET").map(call => call.url))
      .toEqual([expect.stringContaining("/hot/scan"), expect.stringContaining("/hot/scan")]);
  }, 30_000);

  it("the analysis panels are off the trading screen", async () => {
    stubViewport(1440);
    backends();
    render(<CryptoTerminal />);
    await waitFor(() => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });
    for (const id of ["disclosure-performance", "disclosure-trades", "disclosure-ledger", "disclosure-run"]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
  });
});
