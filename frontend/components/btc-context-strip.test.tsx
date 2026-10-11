import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { BtcContextStrip, BtcContextStripView } from "@/components/btc-context-strip";
import { marketContextSlot, renderBtcContext } from "@/components/crypto-terminal-context-slot";
import { contextLines, type BtcContextPayload } from "@/lib/btc-context";

/** BTC Manual Trading Market Context UI V1 inside the real terminal.
 *
 *  Payloads are the backend's own output, not written by hand: `btc-context-snapshot.json` is
 *  `app.crypto.btc_context_ui_v1.view.display` run over the production Context Collector's
 *  current-state file and live Market Context V1 structure/derivatives layers (2026-10-11), once
 *  as read one second after the file was written (`live`) and once an hour later (`stale`). The
 *  terminal's own answers are the recorded paper fixture the integration test uses.
 */
import snapshots from "@/components/__fixtures__/btc-context-snapshot.json";
import paperTerminal from "@/components/__fixtures__/crypto-terminal-paper.json";

const LIVE = snapshots.live as unknown as BtcContextPayload;
const STALE = snapshots.stale as unknown as BtcContextPayload;
const PHONE_WIDTH = 390;
const WIDE = [1600, 1440];

vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: ({ candles }: { candles: unknown[] }) =>
    React.createElement("div", { "data-testid": "candle-chart-stub", "data-count": candles.length }),
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => {
  try { window.localStorage.clear(); } catch { /* private window */ }
});

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

function hotState() {
  return {
    main: "BTCUSDT",
    hot: { symbol: "ETHUSDT", selected_at_ms: 1, scanned_at_ms: 1, metrics: {}, top: [] },
    lock: { state: "ALLOWED", code: "HOT_SYMBOL_FLAT", symbol: "ETHUSDT", message: "" },
    scanning: false, held: [], permitted: [], paper: { available: true, reason: null },
    server_time_ms: 0,
  };
}

/** One fake for the terminal (8100) and the strip's API. Every request is tallied by method at
 *  the receiving end, so "no non-GET" is read off what the server saw. */
function fakeBackends({ strip = "live" as "live" | "stale" | "down" } = {}) {
  const tally = { all: [] as { url: string; method: string; body?: string }[],
                  stripPolls: 0 };
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const dead = { ok: false, status: 503, json: async () => ({ error: { code: "OFFLINE" } }) };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    tally.all.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.endsWith("/snapshot")) {
      tally.stripPolls += 1;
      if (strip === "down") throw new TypeError("Failed to fetch");
      return ok(strip === "live" ? LIVE : STALE);
    }
    if (url.includes("/api/crypto/hot")) return ok(hotState());
    if (method !== "GET" && method !== "HEAD") return ok({ state: paper.BTCUSDT.state });
    const symbol = new URL(url, "http://x").searchParams.get("symbol") ?? "BTCUSDT";
    const rows = paper[symbol];
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

const slot = (symbol: string) => <BtcContextStrip key={symbol} symbol={symbol} />;

const terminalReady = () => waitFor(
  () => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });

const stripLoaded = () => waitFor(() => expect(
  screen.getByTestId("btc-context-structure").textContent).toContain("R 0.09%"));

// --------------------------------------------------------------------------- the strip itself

describe("BTC context strip", () => {
  it("renders the four sections from the backend's live payload", () => {
    render(<BtcContextStripView payload={LIVE} />);
    expect(screen.getByTestId("btc-context-structure")).toHaveTextContent("STRUCTURE");
    expect(screen.getByTestId("btc-context-structure")).toHaveTextContent("R 0.09% / S 0.16%");
    expect(screen.getByTestId("btc-context-liquidity")).toHaveTextContent("ASK STRONG / BID STRONG");
    expect(screen.getByTestId("btc-context-flow")).toHaveTextContent("BUY 28 / SELL 72");
    expect(screen.getByTestId("btc-context-derivatives")).toHaveTextContent("OI ↓ 7.71B");
    expect(screen.getByTestId("btc-context-derivatives"))
      .toHaveTextContent("FUND -0.0014% · BASIS -0.040%");
    for (const id of ["structure", "liquidity", "flow", "derivatives"]) {
      expect(screen.getByTestId(`btc-context-${id}`).getAttribute("data-live")).toBe("1");
    }
  });

  it("shows only dashes and grey dots for a stale reading", () => {
    render(<BtcContextStripView payload={STALE} />);
    const strip = screen.getByTestId("btc-context-strip");
    expect(strip).toHaveTextContent("R -- / S --");
    expect(strip).toHaveTextContent("ASK -- / BID --");
    expect(strip).toHaveTextContent("BUY -- / SELL --");
    expect(strip).toHaveTextContent("OI --");
    expect(strip).toHaveTextContent("FUND -- · BASIS --");
    for (const id of ["structure", "liquidity", "flow", "derivatives"]) {
      expect(screen.getByTestId(`btc-context-${id}`).getAttribute("data-live")).toBe("0");
    }
  });

  it("is all dashes with no payload at all", () => {
    expect(contextLines(null)).toMatchObject({
      structure: "R -- / S --", liquidity: "ASK -- / BID --", flow: "BUY -- / SELL --",
      oi: "OI --", rates: "FUND -- · BASIS --",
    });
  });

  it("never prints an internal state word or research label", () => {
    for (const payload of [LIVE, STALE, null]) {
      cleanup();
      render(<BtcContextStripView payload={payload} />);
      const text = screen.getByTestId("btc-context-strip").textContent ?? "";
      for (const word of ["STALE", "UNKNOWN", "PARTIAL", "UNAVAILABLE", "Q1", "Q2", "Q3",
                          "SHORT", "LONG", "CANDIDATE", "RESEARCH", "UNVALIDATED", "sample",
                          "confidence", "p-value", "contract", "version", "hash", "AUTO"]) {
        expect(text, word).not.toContain(word);
      }
    }
  });

  it("polls with GET and shows the answer", async () => {
    const tally = fakeBackends();
    render(<BtcContextStrip symbol="BTCUSDT" />);
    await stripLoaded();
    expect(tally.stripPolls).toBeGreaterThan(0);
    expect(tally.all.every(r => r.method === "GET")).toBe(true);
  });

  it("shows dashes and no error text when the API is unavailable", async () => {
    const tally = fakeBackends({ strip: "down" });
    render(<BtcContextStrip symbol="BTCUSDT" />);
    await waitFor(() => expect(tally.stripPolls).toBeGreaterThan(0));
    const strip = screen.getByTestId("btc-context-strip");
    expect(strip).toHaveTextContent("ASK -- / BID --");
    expect(strip.textContent).not.toMatch(/error|오류|실패|Failed/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("drops an answer that has aged past the hold window to dashes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let up = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (!up) throw new TypeError("Failed to fetch");
      return { ok: true, status: 200, json: async () => LIVE };
    }));
    render(<BtcContextStrip symbol="BTCUSDT" />);
    await stripLoaded();
    up = false;
    await vi.advanceTimersByTimeAsync(20_000);
    await waitFor(() => expect(screen.getByTestId("btc-context-structure"))
      .toHaveTextContent("R -- / S --"));
  });

  it("renders nothing and asks nothing for any symbol but BTC", async () => {
    const tally = fakeBackends();
    for (const symbol of ["ETHUSDT", "SOLUSDT", "DOGEUSDT"]) {
      const { container } = render(<BtcContextStrip symbol={symbol} />);
      expect(container.innerHTML).toBe("");
      expect(renderBtcContext(symbol)).toBeNull();
      cleanup();
    }
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(tally.stripPolls).toBe(0);
  });

  it("is not mounted at all by a build without a configured backend", () => {
    expect(process.env.NEXT_PUBLIC_BTC_CONTEXT_BASE ?? "").toBe("");
    expect(marketContextSlot()).toBeUndefined();
  });
});

// --------------------------------------------------------------------------- inside the terminal

describe("BTC context strip inside the terminal", () => {
  it.each(WIDE)("sits under the chart, left column, at %ipx", async width => {
    stubViewport(width);
    fakeBackends();
    render(<CryptoTerminal marketContext={slot} />);
    await terminalReady();
    await stripLoaded();
    const strip = screen.getByTestId("btc-context-strip");
    const chart = screen.getAllByTestId("candle-chart-stub")[0];
    expect(chart.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByTestId("btc-context-strip")).toHaveLength(1);
  });

  it.each(WIDE)("leaves the order ticket's container unchanged at %ipx", async width => {
    stubViewport(width);
    fakeBackends();
    const bare = render(<CryptoTerminal />);
    await terminalReady();
    const before = bare.container.querySelector("[data-testid='order-ticket']")?.parentElement?.className;
    cleanup();
    fakeBackends();
    const withIt = render(<CryptoTerminal marketContext={slot} />);
    await terminalReady();
    await stripLoaded();
    const after = withIt.container.querySelector("[data-testid='order-ticket']")?.parentElement?.className;
    expect(after).toBe(before);
  });

  it("puts the strip after the order ticket at 390px", async () => {
    stubViewport(PHONE_WIDTH);
    fakeBackends();
    render(<CryptoTerminal marketContext={slot} />);
    await terminalReady();
    await stripLoaded();
    const strip = screen.getByTestId("btc-context-strip");
    const ticket = screen.getAllByTestId("order-ticket")[0];
    expect(ticket.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByTestId("btc-context-strip")).toHaveLength(1);
  });

  it("is hidden on the HOT tab and comes back on BTC", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminal marketContext={slot} />);
    await terminalReady();
    await stripLoaded();
    await waitFor(() => expect(
      screen.getByTestId("symbol-tab-hot").getAttribute("data-symbol")).toBe("ETHUSDT"));
    await waitFor(() => expect(screen.getByTestId("symbol-tab-hot")).toBeEnabled());
    fireEvent.click(screen.getByTestId("symbol-tab-hot"));
    await waitFor(() => expect(
      screen.getByTestId("symbol-tab-hot").getAttribute("aria-selected")).toBe("true"));
    expect(screen.queryByTestId("btc-context-strip")).toBeNull();
    const pollsOnHot = tally.stripPolls;
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(tally.stripPolls).toBe(pollsOnHot);
    fireEvent.click(screen.getByTestId("symbol-tab-BTCUSDT"));
    await stripLoaded();
  });

  it("issues no non-GET while the terminal and the strip are on screen", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminal marketContext={slot} />);
    await terminalReady();
    await stripLoaded();
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(tally.all.filter(r => r.method !== "GET" && r.method !== "HEAD")).toEqual([]);
    expect(tally.all.filter(r => r.url.endsWith("/snapshot")).every(r => r.method === "GET"))
      .toBe(true);
  });

  it("sends the same order request with and without the strip", async () => {
    // The order callback is the terminal's; the strip must not change what LONG sends.
    const pressLong = async (withStrip: boolean) => {
      stubViewport(WIDE[0]);
      const tally = fakeBackends();
      render(<CryptoTerminal marketContext={withStrip ? slot : undefined} />);
      await terminalReady();
      if (withStrip) await stripLoaded();
      const longButton = screen.getAllByTestId("long-button")[0];
      expect(longButton).toBeEnabled();
      fireEvent.click(longButton);
      await waitFor(() => expect(tally.all.some(r => r.method === "POST")).toBe(true));
      const writes = tally.all.filter(r => r.method !== "GET" && r.method !== "HEAD")
        .map(r => ({ url: r.url, method: r.method, body: r.body }));
      cleanup();
      return writes;
    };
    const bare = await pressLong(false);
    const withIt = await pressLong(true);
    expect(bare.length).toBeGreaterThan(0);
    expect(withIt).toEqual(bare);
    expect(withIt.some(r => r.url.endsWith("/snapshot"))).toBe(false);
  });

  it("keeps the order ticket controls when the strip's API is down", async () => {
    stubViewport(WIDE[0]);
    fakeBackends({ strip: "down" });
    render(<CryptoTerminal marketContext={slot} />);
    await terminalReady();
    const ticket = screen.getAllByTestId("order-ticket")[0];
    expect(within(ticket).getAllByTestId("long-button")[0]).toBeEnabled();
    await waitFor(() => expect(screen.getByTestId("btc-context-strip"))
      .toHaveTextContent("BUY -- / SELL --"));
  });
});
