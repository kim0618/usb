import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import CryptoPaperPage from "@/app/crypto-paper/page";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { CryptoTerminalPreview } from "@/components/crypto-terminal-preview";
import { MarketContextPanel } from "@/components/market-context-panel";
import type { MarketContextPayload } from "@/lib/market-context";

/** Manual Market Context V1 inside the real terminal.
 *
 *  Three things are under test here and only one of them is the panel.
 *
 *  **The production route did not change.** The screen was lifted into `CryptoTerminal` so a
 *  preview could render the real thing; the route now passes no context slot, and the first
 *  describe below pins that the slot being absent is indistinguishable from the slot not
 *  existing. A refactor that quietly added a panel to `/crypto-paper` would fail there.
 *
 *  **A BTC reading never appears under another symbol.** This terminal has already paid for that
 *  failure once - a chart hook that was not passed its symbol drew BTC candles on every tab, and
 *  because the header, the position and the ticket were all correct there was no visual
 *  signature at all. So the switching tests assert on absence during the switch, not only on the
 *  state after it settles.
 *
 *  **Nothing this page renders can write.** The preview refuses every non-GET before it leaves
 *  the browser and counts it. That has to be checked by pressing a real order button, because a
 *  sentinel that was never fired is a sentinel that might be installed too late.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
beforeEach(() => { try { window.localStorage.clear(); } catch { /* private window */ } });

/** Both backends' real answers, captured from a live run of this very preview.
 *
 *  Hand-written payloads were tried first and were wrong in three places that all type-checked:
 *  `vanished.this_reading` where the layer publishes `vanished.recent` too, `ts_ms` where a chart
 *  bar carries `start_ms` and `confirmed`, and a `sides` block missing the depth bands. Every one
 *  of them rendered something plausible. So the fixtures are the recorded responses of the two
 *  processes this screen actually talks to - `app.crypto.terminal.server` on 8100 and
 *  `app.crypto.market_context_v1` on 8012 - and the shapes under test are the shapes that ship.
 *
 *  Captured 2026-10-05 against live Binance USDⓈ-M public data and a 2-minute Market Structure V0
 *  collector, so BTC carries five live layers, a wall on each side, a firing
 *  `ABSORPTION_CANDIDATE` and no published resistance - which is the `NO_LEVEL_ABOVE` case.
 */
import btcContext from "@/components/__fixtures__/market-context-btc.json";
import ethContext from "@/components/__fixtures__/market-context-eth.json";
import solContext from "@/components/__fixtures__/market-context-sol.json";
import paperTerminal from "@/components/__fixtures__/crypto-terminal-paper.json";

const PHONE_WIDTH = 390;

/** Widths the report measures. jsdom reports no layout, so these drive `matchMedia` only: the
 *  pixel truth is measured in a real browser and recorded in the preview report. */
const WIDE = [1600, 1440];

// The chart is a canvas library; these tests are about what surrounds it.
vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: ({ candles }: { candles: unknown[] }) =>
    React.createElement("div", { "data-testid": "candle-chart-stub",
                                 "data-count": candles.length }),
}));

function stubViewport(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const max = /max-width:\s*(\d+)px/.exec(query);
    const matches = min ? width >= Number(min[1]) : max ? width <= Number(max[1]) : false;
    return { matches, media: query, addEventListener: () => {}, removeEventListener: () => {},
             addListener: () => {}, removeListener: () => {}, onchange: null,
             dispatchEvent: () => false };
  });
}

type Paper = Record<string, Record<string, unknown>>;
const paper = paperTerminal as unknown as Paper;
const CONTEXT: Record<string, MarketContextPayload> = {
  BTCUSDT: btcContext as unknown as MarketContextPayload,
  ETHUSDT: ethContext as unknown as MarketContextPayload,
  SOLUSDT: solContext as unknown as MarketContextPayload,
};

/** BTC figures that appear nowhere in the ETH or SOL payloads, read off the fixtures themselves.
 *
 *  Read off rather than typed in, so the list cannot drift away from what the panel would print
 *  if a BTC reading leaked. A hard-coded number that the fixture no longer contains is a test
 *  that passes because it is looking for nothing. */
function btcOnlyFigures(): string[] {
  const btc = CONTEXT.BTCUSDT.layers;
  const liquidity = btc.LIQUIDITY as unknown as
    { sides: Record<string, { nearest_wall: { price: number; notional_usdt: string } }> };
  const flow = btc.FLOW as unknown as
    { windows: Record<string, { buy_usdt: string }>;
      absorption: { state: string } | null };
  const price = btc.PRICE as unknown as
    { levels: Record<string, { price: number } | null>;
      row: { trend_structure: string } };
  const figures = [
    String(liquidity.sides.ASK.nearest_wall.price),
    String(liquidity.sides.BID.nearest_wall.price),
    flow.windows["60s"].buy_usdt.split(".")[0],
    String(price.levels.support?.price ?? ""),
    flow.absorption?.state ?? "",
  ].filter(value => value.length > 3);
  return Array.from(new Set(figures));
}

/** One fake for both backends: the terminal's on 8100, the context panel's on 8012.
 *
 *  `writes` is incremented by the fake *server*, so "no write reached the backend" is read off
 *  the receiving end rather than off the sending end.
 */
function fakeBackends({ context = (symbol: string) => CONTEXT[symbol],
                        contextFails = false }: {
  context?: (symbol: string) => MarketContextPayload; contextFails?: boolean } = {}) {
  const tally = { writes: [] as { url: string; method: string }[], contextPolls: [] as string[] };
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const dead = { ok: false, status: 503, json: async () => ({
    error: { code: "OFFLINE", message: "not part of this test" } }) };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") {
      tally.writes.push({ url, method });
      return ok({ state: paper.BTCUSDT.state });
    }
    const symbol = new URL(url, "http://x").searchParams.get("symbol") ?? "BTCUSDT";
    const rows = paper[symbol];
    if (url.includes("/api/market-context/panel")) {
      if (contextFails) return dead;
      tally.contextPolls.push(symbol);
      return ok(context(symbol));
    }
    if (!rows) return dead;
    if (url.includes("/api/crypto/chart-history")) return ok(rows.chart_history);
    if (url.includes("/api/crypto/chart")) {
      return ok({ symbol, bars: (rows.chart_history as { bars: unknown[] }).bars });
    }
    if (url.includes("/api/crypto/state")) return ok(rows.state);
    if (url.includes("/api/crypto/sizing")) return ok(rows.sizing);
    if (url.includes("/api/crypto/performance")) return ok(rows.performance);
    if (url.includes("/api/crypto/ledger")) return ok(rows.ledger);
    if (url.includes("/api/crypto/trades")) return ok(rows.trades);
    if (url.includes("/api/crypto/pnl-breakdown")) return ok(rows.pnl_breakdown);
    return dead;
  }));
  return tally;
}

/** A context payload with one or more layers pushed into a degraded state, built by editing the
 *  captured one rather than by writing a new one. */
function degrade(symbol: string, edit: (payload: MarketContextPayload) => void) {
  const copy = JSON.parse(JSON.stringify(CONTEXT[symbol])) as MarketContextPayload;
  edit(copy);
  copy.layer_states = {
    PRICE: copy.layers.PRICE.state, LIQUIDITY: copy.layers.LIQUIDITY.state,
    FLOW: copy.layers.FLOW.state, DERIVATIVES: copy.layers.DERIVATIVES.state,
    AUXILIARY: copy.layers.AUXILIARY.state,
  };
  return copy;
}

function withPanel(extra: { minNotional?: string } = {}) {
  function ContextSlot(symbol: string) {
    return <MarketContextPanel key={symbol} symbol={symbol}
      minNotionalUsdt={extra.minNotional} collapsible />;
  }
  return ContextSlot;
}

const terminalReady = () => waitFor(
  () => expect(screen.getByTestId("market-header")).toBeInTheDocument(),
  { timeout: 4_000 });

/** Wait for a *loaded* panel.
 *
 *  `findByTestId("market-context-panel")` is not that: the loading placeholder carries the same
 *  testid on purpose, so the screen can say "불러오는 중" in the panel's own slot. Waiting on the
 *  testid alone therefore resolves before any payload has arrived, and an assertion about the
 *  footer then fails intermittently depending on how fast the stub resolved - which is how this
 *  helper came to exist. `data-symbol` is only set once a payload is on screen.
 */
const panelLoaded = async (symbol?: string) => {
  await waitFor(() => expect(
    screen.getByTestId("market-context-panel").hasAttribute("data-symbol")).toBe(true));
  const panel = screen.getByTestId("market-context-panel");
  if (symbol) expect(panel.getAttribute("data-symbol")).toBe(symbol);
  return panel;
};

// --------------------------------------------------------------------------- regression

describe("the deployed route is what it was", () => {
  it("renders no context panel on /crypto-paper", async () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    render(<CryptoPaperPage />);
    await terminalReady();
    expect(screen.queryByTestId("market-context-panel")).toBeNull();
  });

  it("makes no Market Context request at all from the deployed route", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoPaperPage />);
    await terminalReady();
    // Not "renders nothing" but "asks for nothing": a panel hidden with CSS would still poll a
    // second backend from the production screen, which is the thing this step must not do.
    expect(tally.contextPolls).toEqual([]);
  });

  it("keeps the blocks the terminal had, in the order it had them", async () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    render(<CryptoPaperPage />);
    await terminalReady();
    for (const id of ["source-PAPER", "symbol-tabs", "market-header", "candle-chart-stub",
                      "order-ticket", "disclosure-performance", "disclosure-trades",
                      "disclosure-ledger", "disclosure-run"]) {
      expect(screen.queryAllByTestId(id).length, id).toBeGreaterThan(0);
    }
  });

  it("renders the same tree with the slot absent as with the slot undefined", async () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    const route = render(<CryptoPaperPage />);
    await terminalReady();
    const fromRoute = route.container.innerHTML;
    cleanup();
    fakeBackends();
    const direct = render(<CryptoTerminal marketContext={undefined} />);
    await terminalReady();
    expect(direct.container.innerHTML).toBe(fromRoute);
  });
});

// --------------------------------------------------------------------------- placement

describe("placement inside the real layout", () => {
  it.each(WIDE)("puts the panel in the chart's column at %ipx, not the ticket's", async width => {
    stubViewport(width);
    fakeBackends();
    const { container } = render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    const panel = await panelLoaded();
    const column = container.querySelector(".grid > div.space-y-3");
    expect(column, "the chart's column").not.toBeNull();
    expect(column!.contains(panel)).toBe(true);
    // And it is after the chart rather than before it.
    const chart = screen.getAllByTestId("candle-chart-stub")[0];
    expect(chart.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it.each(WIDE)("leaves the order ticket where it was at %ipx", async width => {
    stubViewport(width);
    fakeBackends();
    const bare = render(<CryptoTerminal />);
    await terminalReady();
    const before = bare.container.querySelector("[data-testid='order-ticket']")
      ?.parentElement?.className;
    cleanup();
    fakeBackends();
    const withIt = render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await screen.findByTestId("market-context-panel");
    const after = withIt.container.querySelector("[data-testid='order-ticket']")
      ?.parentElement?.className;
    expect(after).toBe(before);
  });

  it("puts the panel after the order ticket at 390px", async () => {
    stubViewport(PHONE_WIDTH);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    const panel = await panelLoaded();
    const ticket = screen.getAllByTestId("order-ticket")[0];
    expect(ticket.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("mounts exactly one panel, so only one poll loop runs", async () => {
    stubViewport(PHONE_WIDTH);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    expect(screen.getAllByTestId("market-context-panel")).toHaveLength(1);
  });
});

// --------------------------------------------------------------------------- mobile

describe("390px", () => {
  it("opens collapsed, showing the three layer names and a 펼치기 control", async () => {
    stubViewport(PHONE_WIDTH);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    const panel = await panelLoaded();
    expect(panel.getAttribute("data-collapsed")).toBe("true");
    expect(screen.getByTestId("mc-collapsed-summary"))
      .toHaveTextContent("PRICE · LIQUIDITY · FLOW");
    expect(screen.getByTestId("mc-panel-toggle")).toHaveTextContent("펼치기");
    // Collapsed hides figures, never freshness: every layer's state word is still on screen.
    expect(within(screen.getByTestId("mc-state-strip"))
      .getAllByTestId("mc-state-chip")).toHaveLength(5);
    for (const id of ["mc-price", "mc-liquidity", "mc-flow"]) {
      expect(screen.queryByTestId(id), id).toBeNull();
    }
  });

  it("expands on the control and still keeps the ticket above it", async () => {
    stubViewport(PHONE_WIDTH);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    fireEvent.click(screen.getByTestId("mc-panel-toggle"));
    expect(await screen.findByTestId("mc-price")).toBeInTheDocument();
    expect(screen.getByTestId("mc-panel-toggle")).toHaveTextContent("접기");
    const panel = screen.getByTestId("market-context-panel");
    const ticket = screen.getAllByTestId("order-ticket")[0];
    expect(ticket.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("opens expanded on a wide screen", async () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    const panel = await panelLoaded();
    expect(panel.getAttribute("data-collapsed")).toBe("false");
    expect(screen.getByTestId("mc-price")).toBeInTheDocument();
  });

  it("clips its own overflow and stays single-column inside the terminal", async () => {
    stubViewport(PHONE_WIDTH);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    const panel = await panelLoaded();
    fireEvent.click(screen.getByTestId("mc-panel-toggle"));
    await screen.findByTestId("mc-price");
    expect(panel.className).toContain("overflow-hidden");
    expect(panel.className).toContain("min-w-0");
    for (const grid of Array.from(panel.querySelectorAll("[class*='grid']"))) {
      expect(grid.className).toContain("grid-cols-1");
    }
  });
});

// --------------------------------------------------------------------------- symbol isolation

describe("symbol isolation", () => {
  const switchTo = async (symbol: string) => {
    fireEvent.click(screen.getByTestId(`symbol-tab-${symbol}`));
    await waitFor(() => expect(
      screen.getByTestId(`symbol-tab-${symbol}`).getAttribute("aria-selected")).toBe("true"));
  };

  it("shows BTC's full context on BTC", async () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    const panel = await panelLoaded();
    expect(panel.getAttribute("data-symbol")).toBe("BTCUSDT");
    for (const id of ["mc-price", "mc-liquidity", "mc-flow"]) {
      expect(screen.getByTestId(id), id).toBeInTheDocument();
    }
    const chips = within(screen.getByTestId("mc-state-strip")).getAllByTestId("mc-state-chip");
    expect(chips.map(chip => chip.getAttribute("data-state")))
      .toEqual(["LIVE", "LIVE", "LIVE", "LIVE", "STALE"]);
  });

  it.each(["ETHUSDT", "SOLUSDT"])("marks the BTC-only layers unavailable on %s", async symbol => {
    stubViewport(WIDE[0]);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    await switchTo(symbol);
    await waitFor(() => expect(
      screen.getByTestId("market-context-panel").getAttribute("data-symbol")).toBe(symbol));
    const chips = within(screen.getByTestId("mc-state-strip")).getAllByTestId("mc-state-chip");
    expect(chips.map(chip => chip.getAttribute("data-state")))
      .toEqual(["UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE", "LIVE", "UNAVAILABLE"]);
    // DERIVATIVES is the one layer that is this symbol's own, so it is the one that renders.
    expect(screen.getByTestId("mc-derivatives")).toBeInTheDocument();
  });

  it.each(["ETHUSDT", "SOLUSDT"])("carries no BTC figure onto %s", async symbol => {
    stubViewport(WIDE[0]);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    await switchTo(symbol);
    await waitFor(() => expect(
      screen.getByTestId("market-context-panel").getAttribute("data-symbol")).toBe(symbol));
    const text = (screen.getByTestId("market-context-panel").textContent ?? "")
      .replace(/,/g, "");
    for (const btcOnly of btcOnlyFigures()) {
      expect(text, btcOnly).not.toContain(btcOnly);
    }
  });

  it("leaves nothing behind across BTC → ETH → SOL → BTC", async () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    for (const symbol of ["ETHUSDT", "SOLUSDT", "BTCUSDT"]) {
      await switchTo(symbol);
      await waitFor(() => expect(
        screen.getByTestId("market-context-panel").getAttribute("data-symbol")).toBe(symbol));
      const panel = screen.getByTestId("market-context-panel");
      expect(panel.getAttribute("data-symbol")).toBe(symbol);
      if (symbol !== "BTCUSDT") {
        expect(within(panel).queryByTestId("mc-liquidity")).toBeNull();
        expect(within(panel).queryByTestId("mc-flow")).toBeNull();
        expect(within(panel).queryByTestId("mc-auxiliary-c1")).toBeNull();
      }
    }
    expect(screen.getByTestId("mc-liquidity")).toBeInTheDocument();
    expect(screen.getByTestId("mc-flow")).toBeInTheDocument();
  });

  it("shows no stale symbol's reading during a fast switch", async () => {
    stubViewport(WIDE[0]);
    // Every Market Context answer is held until released, so the window between "the tab says
    // ETH" and "the ETH payload arrived" is open for as long as the assertion needs. That window
    // is where the previous symbol's figures would be visible, and it is invisible in a test
    // that only looks after things settle.
    const gate: Array<() => void> = [];
    fakeBackends();
    const served = window.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/api/market-context/panel")) {
        await new Promise<void>(resolve => gate.push(resolve));
      }
      return served(input, init);
    }));
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await waitFor(() => expect(gate.length).toBeGreaterThan(0));
    gate.shift()!();                                   // let BTC's first answer through
    await waitFor(() => expect(
      screen.getByTestId("market-context-panel").getAttribute("data-symbol")).toBe("BTCUSDT"));
    expect(screen.getByTestId("mc-liquidity")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("symbol-tab-ETHUSDT"));
    fireEvent.click(screen.getByTestId("symbol-tab-SOLUSDT"));
    fireEvent.click(screen.getByTestId("symbol-tab-BTCUSDT"));
    fireEvent.click(screen.getByTestId("symbol-tab-ETHUSDT"));
    // Nothing released yet: the panel must be showing no symbol's reading rather than the last
    // one it had.
    await waitFor(() => expect(
      screen.getByTestId("market-context-panel").getAttribute("data-loading")).toBe("true"));
    const panel = screen.getByTestId("market-context-panel");
    expect(panel.hasAttribute("data-symbol")).toBe(false);
    expect(screen.queryByTestId("mc-liquidity")).toBeNull();
    while (gate.length) gate.shift()!();
    await waitFor(() => expect(
      screen.getByTestId("market-context-panel").getAttribute("data-symbol")).toBe("ETHUSDT"));
  });
});

// --------------------------------------------------------------------------- degraded layers

describe("a layer that is not live says so in the terminal too", () => {
  const degraded = (symbol: string) => degrade(symbol, payload => {
    payload.layers.LIQUIDITY.state = "STALE";
    payload.layers.LIQUIDITY.reasons = ["JOURNAL_OLDER_THAN_STALE_BOUND"];
    payload.layers.FLOW.state = "STALE";
    payload.layers.FLOW.reasons = ["JOURNAL_OLDER_THAN_STALE_BOUND"];
  });

  it("renders a stale liquidity and flow layer as STALE, not as a zero", async () => {
    stubViewport(WIDE[0]);
    fakeBackends({ context: degraded });
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    const chips = within(screen.getByTestId("mc-state-strip")).getAllByTestId("mc-state-chip");
    expect(chips.map(chip => chip.getAttribute("data-state")))
      .toEqual(["LIVE", "STALE", "STALE", "LIVE", "STALE"]);
  });

  it("renders a book with no qualifying wall as NONE rather than as an empty row", async () => {
    stubViewport(WIDE[0]);
    const noWall = (symbol: string) => degrade(symbol, payload => {
      const sides = (payload.layers.LIQUIDITY as unknown as
        { sides: Record<string, Record<string, unknown>> }).sides;
      for (const side of ["ASK", "BID"]) {
        sides[side].wall_state = "NONE";
        sides[side].nearest_wall = null;
        sides[side].wall_state_reason = "NO_BIN_AT_OR_ABOVE_THE_NOTIONAL_FLOOR";
      }
    });
    fakeBackends({ context: noWall });
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    const liquidity = screen.getByTestId("mc-liquidity");
    const wallNotional = (CONTEXT.BTCUSDT.layers.LIQUIDITY as unknown as
      { sides: Record<string, { nearest_wall: { notional_usdt: string } }> })
      .sides.ASK.nearest_wall.notional_usdt.split(".")[0];
    expect((liquidity.textContent ?? "").replace(/,/g, "")).not.toContain(wallNotional);
    expect(liquidity.textContent).toContain("벽 없음");
  });

  it("keeps the terminal whole when the context backend is down", async () => {
    stubViewport(WIDE[0]);
    fakeBackends({ contextFails: true });
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    // The panel says it has no answer; the chart, the ticket and the header do not care. This
    // is the one place that must *not* wait for a loaded panel - there is never going to be one.
    const panel = await screen.findByTestId("market-context-panel");
    expect(panel.getAttribute("data-loading")).toBe("true");
    expect(panel.hasAttribute("data-symbol")).toBe(false);
    expect(panel).toHaveTextContent("Market Context 응답 없음");
    expect(screen.getAllByTestId("order-ticket").length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("candle-chart-stub").length).toBeGreaterThan(0);
  });
});

// --------------------------------------------------------------------------- journal failure

describe("a journal that cannot write costs the screen nothing", () => {
  const refused = (symbol: string) => degrade(symbol, payload => {
    payload.journal.status = "REFUSED";
    payload.journal.wrote_this_poll = false;
    payload.journal.writer!.writable = false;
    payload.journal.writer!.refusal = "REFUSED";
    payload.journal.writer!.blocked_writes = 42;
  });

  it("renders every layer while the forward journal is refused", async () => {
    stubViewport(WIDE[0]);
    fakeBackends({ context: refused });
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    for (const id of ["mc-price", "mc-liquidity", "mc-flow"]) {
      expect(screen.getByTestId(id), id).toBeInTheDocument();
    }
    const status = screen.getByTestId("mc-journal-status");
    expect(status.getAttribute("data-status")).toBe("REFUSED");
    expect(status).toHaveTextContent("다른 writer 보유");
    expect(status).toHaveTextContent("기록만 중단, 화면은 계속");
  });

  it.each(["AUTHORITY_LOST", "STALE", "ERROR"])("names %s rather than hiding it", async state => {
    stubViewport(WIDE[0]);
    fakeBackends({ context: symbol => degrade(symbol, payload => {
      payload.journal.status = state as "STALE";
      payload.journal.wrote_this_poll = false;
    }) });
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    expect(screen.getByTestId("mc-journal-status").getAttribute("data-status")).toBe(state);
  });

  it("does not stop the panel polling because the tape stopped", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends({ context: refused });
    render(<CryptoTerminal marketContext={withPanel()} />);
    await terminalReady();
    await panelLoaded();
    const first = tally.contextPolls.length;
    await waitFor(() => expect(tally.contextPolls.length).toBeGreaterThan(first), { timeout: 4_000 });
  });
});

// --------------------------------------------------------------------------- mutation

describe("nothing on the preview route can write", () => {
  it("counts GETs and refuses non-GETs", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminalPreview />);
    await terminalReady();
    await waitFor(() => expect(
      Number(screen.getByTestId("mutation-sentinel").getAttribute("data-gets")))
      .toBeGreaterThan(0));
    expect(screen.getByTestId("mutation-sentinel").getAttribute("data-blocked")).toBe("0");
    expect(tally.writes).toEqual([]);
  });

  it("refuses an order the operator actually presses, and the button is still wired", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminalPreview />);
    await terminalReady();
    const longButton = screen.getAllByTestId("long-button")[0];
    expect(longButton).toBeEnabled();
    fireEvent.click(longButton);
    // The sentinel fired, which can only happen if the click reached `cryptoApi.order` - so the
    // callback is intact - and the server never saw it.
    await waitFor(() => expect(
      screen.getByTestId("mutation-sentinel").getAttribute("data-blocked")).toBe("1"));
    expect(screen.getByTestId("mutation-sentinel-last")).toHaveTextContent("POST");
    expect(tally.writes).toEqual([]);
  });

  it("does not mount the terminal before the sentinel is installed", () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    // One synchronous render pass: effects have not run, so the sentinel is not yet in place and
    // the terminal must not be either. This is the ordering the whole proof rests on - child
    // effects run before parent effects, so a terminal mounted beside this component could have
    // fired a write through the unpatched `fetch`.
    const { container } = render(<CryptoTerminalPreview />);
    expect(container.querySelector("[data-testid='terminal-preview-banner']")).not.toBeNull();
  });

  it("puts the global fetch back when it unmounts", async () => {
    stubViewport(WIDE[0]);
    fakeBackends();
    const stubbed = window.fetch;
    const view = render(<CryptoTerminalPreview />);
    await terminalReady();
    expect(window.fetch).not.toBe(stubbed);
    view.unmount();
    expect(window.fetch).toBe(stubbed);
  });
});
