import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { BtcContextStrip, BtcContextStripView } from "@/components/btc-context-strip";
import { contextBase, marketContextSlot, renderBtcContext } from "@/components/crypto-terminal-context-slot";
import { contextLabelsShown, contextTags, TAG_HEIGHT_PX } from "@/components/crypto-candle-chart";
import {
  contextChartLines, contextLine, flowReading, resetBtcContextStores, type BtcContextPayload,
} from "@/lib/btc-context";
import { offscreenOverlays } from "@/lib/crypto-paper";

/** BTC Manual Trading Market Context UI V2 inside the real terminal.
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

// The chart stub publishes the price lines it was handed, which is the whole contract between the
// terminal and the chart for this feature: what is drawn is decided before the canvas.
vi.mock("@/components/crypto-candle-chart", async importOriginal => {
  const actual = await importOriginal<typeof import("@/components/crypto-candle-chart")>();
  return {
    ...actual,
    CandleChart: ({ candles, overlays }: { candles: unknown[]; overlays: { id: string; price: number; label: string; tag?: string }[] }) =>
      React.createElement("div", {
        "data-testid": "candle-chart-stub", "data-count": candles.length,
        "data-overlays": overlays.map(o => `${o.id}@${o.price}=${o.tag ?? o.label}`).join("|"),
      }),
  };
});

afterEach(() => {
  cleanup(); resetBtcContextStores();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});
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

const BASE = "/btc-context-api";
const slot = (symbol: string) => <BtcContextStrip key={symbol} symbol={symbol} base={BASE} />;

const terminalReady = () => waitFor(
  () => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });

const stripLoaded = () => waitFor(() => expect(
  screen.getByTestId("btc-context-flow60").textContent).toContain("매도우세 72%"));

const chartOverlays = () => screen.getAllByTestId("candle-chart-stub")
  .map(node => node.getAttribute("data-overlays") ?? "").join("|");

const linesDrawn = () => waitFor(() => expect(chartOverlays()).toContain("ctx-ask-wall"));

// --------------------------------------------------------------------------- chart lines

describe("BTC context lines on the chart", () => {
  it("draws resistance, support and both walls at the backend's prices", () => {
    // The axis label carries the price (no title); the chart's tag carries the name.
    // V3: resistance and support are hidden by default; as an annotation the tag is the name only.
    expect(contextChartLines(LIVE).map(l => l.id)).toEqual(["ctx-ask-wall", "ctx-bid-wall"]);
    expect(contextChartLines(LIVE, { structure: "ANNOTATION" })).toEqual([
      { id: "ctx-resistance", price: 83113.9, kind: "RESISTANCE", short: "저항", label: "", tag: "저항" },
      { id: "ctx-support", price: 82912.9, kind: "SUPPORT", short: "지지", label: "", tag: "지지" },
      { id: "ctx-ask-wall", price: 83050.4, kind: "ASK_WALL", short: "상단벽1", label: "", tag: "상1", order: 1 },
      { id: "ctx-bid-wall", price: 82996.5, kind: "BID_WALL", short: "하단벽1", label: "", tag: "하1", order: 1 },
    ]);
  });

  it("draws nothing for a stale reading, no reading, or another symbol", () => {
    expect(contextChartLines(STALE)).toEqual([]);
    expect(contextChartLines(null)).toEqual([]);
    expect(contextChartLines({ ...LIVE, symbol: "ETHUSDT" })).toEqual([]);
  });

  it("leaves out exactly the side the backend did not publish", () => {
    const noWall = { ...LIVE, liquidity: { ...LIVE.liquidity, ask_wall_price: null } };
    expect(contextChartLines(noWall, { structure: "ANNOTATION" }).map(l => l.id)).toEqual(["ctx-resistance", "ctx-support", "ctx-bid-wall"]);
    const noLevel = { ...LIVE, structure: { ...LIVE.structure, support_price: null, support_pct: null } };
    expect(contextChartLines(noLevel, { structure: "ANNOTATION" }).map(l => l.id)).toEqual(["ctx-resistance", "ctx-ask-wall", "ctx-bid-wall"]);
  });

  it("is at most one line per kind", () => {
    const kinds = contextChartLines(LIVE).map(l => l.kind);
    expect(new Set(kinds).size).toBe(kinds.length);
    expect(kinds.length).toBeLessThanOrEqual(4);
  });

  it("never adds a context line to the note above the chart, which would move the ticket", () => {
    const lines = contextChartLines(LIVE);
    expect(offscreenOverlays(lines, { from: 83020, to: 83040 })).toEqual([]);
    // The account's own lines are listed as before.
    const entry = { id: "entry", price: 90000, label: "진입", kind: "ENTRY" as const };
    expect(offscreenOverlays([...lines, entry], { from: 83020, to: 83040 }).map(l => l.id)).toEqual(["entry"]);
  });

  it("tags each line on the pane at its height, and an off-pane level on the edge it is past", () => {
    const tag = (id: string, kind: "RESISTANCE" | "SUPPORT" | "ASK_WALL" | "BID_WALL", y: number | null, text: string) =>
      ({ id, kind, y, tag: text });
    const tags = contextTags([
      tag("r", "RESISTANCE", -40, "저항 83,113.9 +0.09%"),
      tag("ask", "ASK_WALL", 100, "매도벽 83,050.4"),
      tag("bid", "BID_WALL", 900, "매수벽 82,996.5"),      // a wall off the pane: no tag
      tag("s", "SUPPORT", 450, "지지 82,912.9 -0.16%"),
    ], 300);
    expect(tags.map(t => [t.id, t.text, t.top, t.off])).toEqual([
      ["r", "저항 83,113.9 +0.09% ↑", 0, "UP"],
      ["ask", "매도벽 83,050.4", 100 - TAG_HEIGHT_PX - 1, null],
      ["s", "지지 82,912.9 -0.16% ↓", 300 - TAG_HEIGHT_PX, "DOWN"],
    ]);
    expect(contextTags([], 300)).toEqual([]);
    expect(contextTags([tag("r", "RESISTANCE", 10, "저항")], 0)).toEqual([]);
  });

  it("pushes tags apart without reordering them, and keeps the stack on the pane", () => {
    const lines = [
      { id: "a", kind: "RESISTANCE" as const, y: 100, tag: "a" },
      { id: "b", kind: "ASK_WALL" as const, y: 102, tag: "b" },
      { id: "c", kind: "BID_WALL" as const, y: 104, tag: "c" },
    ];
    const tops = contextTags(lines, 300).map(t => [t.id, t.top]);
    const first = 100 - TAG_HEIGHT_PX - 1;
    expect(tops).toEqual([["a", first], ["b", first + TAG_HEIGHT_PX], ["c", first + TAG_HEIGHT_PX * 2]]);
    const low = contextTags(lines.map(l => ({ ...l, y: l.y + 190 })), 300);
    expect(low.map(t => t.id)).toEqual(["a", "b", "c"]);
    expect(Math.max(...low.map(t => t.top)) + TAG_HEIGHT_PX).toBeLessThanOrEqual(300);
    for (let i = 1; i < low.length; i++) expect(low[i].top - low[i - 1].top).toBeGreaterThanOrEqual(TAG_HEIGHT_PX);
  });

  it("drops a context label that would overprint the price or a higher label, keeps the rest", () => {
    const shown = contextLabelsShown(100, [
      { id: "entry", kind: "ENTRY", y: 140 },
      { id: "r", kind: "RESISTANCE", y: 108 },      // 8 px under the price: label dropped
      { id: "s", kind: "SUPPORT", y: 200 },
      { id: "ask", kind: "ASK_WALL", y: 150 },      // 10 px under the entry label: dropped
      { id: "bid", kind: "BID_WALL", y: 210 },      // 10 px under support, which outranks it
      { id: "off", kind: "RESISTANCE", y: null },   // off the pane: nothing to place
    ]);
    expect([...shown].sort()).toEqual(["s"]);
    expect(contextLabelsShown(null, [{ id: "r", kind: "RESISTANCE", y: 10 },
      { id: "s", kind: "SUPPORT", y: 300 }]).size).toBe(2);
  });
});

// --------------------------------------------------------------------------- the line under the chart

describe("BTC context line", () => {
  it("states the larger side of the flow and its share", () => {
    expect(flowReading(LIVE)).toEqual({ text: "매도우세 72%", buyPct: 28, side: "SELL" });
    const flow = (buy: number) => ({ ...LIVE, flow: { ...LIVE.flow, buy_pct: buy, sell_pct: 100 - buy } });
    expect(flowReading(flow(64)).text).toBe("매수우세 64%");
    expect(flowReading(flow(51)).text).toBe("매수우세 51%");
    // Only an exact even split has no larger side; no band around 50 is invented here.
    expect(flowReading(flow(50)).text).toBe("중립 50%");
    expect(flowReading(STALE).text).toBe("--");
    expect(flowReading(null).text).toBe("--");
  });

  it("shows OI direction and funding, and keeps the other figures for the detail", () => {
    render(<BtcContextStripView payload={LIVE} />);
    expect(screen.getByTestId("btc-context-oi")).toHaveTextContent(/^OI ↓$/);
    expect(screen.getByTestId("btc-context-funding")).toHaveTextContent("펀딩 -0.0014%");
    const detail = screen.getByTestId("btc-context-detail");
    expect(detail).toHaveTextContent("60초 체결 매도우세 72%");
    expect(detail).toHaveTextContent("OI 7.71B");
    expect(detail).toHaveTextContent("1시간 0.00%");
    expect(detail).toHaveTextContent("베이시스 -0.040%");
    // The detail is closed until asked for, and its toggle is the only thing shown of it.
    expect(screen.getByTestId("btc-context-strip").querySelector("details")?.open).toBe(false);
    const summary = screen.getByTestId("btc-context-details-toggle");
    expect(summary.textContent).toBe("상세▾");
    for (const word of ["7.71B", "베이시스", "83,", "유지"]) {
      expect(screen.getByTestId("btc-context-strip").querySelector("details")?.open).toBe(false);
      expect(summary.textContent).not.toContain(word);
    }
  });

  it("is dashes and grey with a stale reading or none", () => {
    for (const payload of [STALE, null]) {
      cleanup();
      render(<BtcContextStripView payload={payload} />);
      for (const key of ["10m", "1h", "4h", "1d"]) {
        expect(screen.getByTestId(`flow-horizon-${key}-text`)).toHaveTextContent(/^--$/);
      }
      expect(screen.getByTestId("btc-context-oi")).toHaveTextContent("OI --");
      expect(screen.getByTestId("btc-context-funding")).toHaveTextContent("펀딩 --");
      for (const id of ["oi", "funding"]) {
        expect(screen.getByTestId(`btc-context-${id}`).getAttribute("data-live")).toBe("0");
      }
      expect(screen.getByTestId("btc-context-path-up-value")).toHaveTextContent(/^--$/);
      expect(screen.getByTestId("btc-context-path-down-value")).toHaveTextContent(/^--$/);
    }
    expect(contextLine(null).detail).toEqual({ oiValue: "OI --", oiChange: "1시간 --", basis: "베이시스 --" });
  });

  it("never prints an internal state word, an English section name or a research label", () => {
    for (const payload of [LIVE, STALE, null]) {
      cleanup();
      render(<BtcContextStripView payload={payload} />);
      const text = screen.getByTestId("btc-context-strip").textContent ?? "";
      const lines = contextChartLines(payload).map(l => l.tag).join(" ");
      for (const word of ["STALE", "UNKNOWN", "PARTIAL", "UNAVAILABLE", "Q1", "Q2", "Q3",
                          "SHORT", "LONG", "CANDIDATE", "RESEARCH", "research", "UNVALIDATED",
                          "sample", "confidence", "p-value", "contract", "version", "hash", "AUTO",
                          "STRUCTURE", "LIQUIDITY", "DERIVATIVES", "STRONG", "NORMAL", "BUY", "SELL"]) {
        expect(text, word).not.toContain(word);
        expect(lines, word).not.toContain(word);
      }
    }
  });

  it("polls with GET and shows the answer", async () => {
    const tally = fakeBackends();
    render(<BtcContextStrip symbol="BTCUSDT" base={BASE} />);
    await stripLoaded();
    expect(tally.stripPolls).toBeGreaterThan(0);
    expect(tally.all.every(r => r.method === "GET")).toBe(true);
    expect(tally.all.every(r => r.url === `${BASE}/snapshot`)).toBe(true);
  });

  it("shows dashes and no error text when the API is unavailable", async () => {
    const tally = fakeBackends({ strip: "down" });
    render(<BtcContextStrip symbol="BTCUSDT" base={BASE} />);
    await waitFor(() => expect(tally.stripPolls).toBeGreaterThan(0));
    const strip = screen.getByTestId("btc-context-strip");
    expect(screen.getByTestId("flow-horizon-10m-text")).toHaveTextContent(/^--$/);
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
    render(<BtcContextStrip symbol="BTCUSDT" base={BASE} />);
    await stripLoaded();
    up = false;
    await vi.advanceTimersByTimeAsync(20_000);
    await waitFor(() => expect(screen.getByTestId("flow-horizon-10m-text")).toHaveTextContent(/^--$/));
  });

  it("renders nothing and asks nothing for any symbol but BTC", async () => {
    const tally = fakeBackends();
    for (const symbol of ["ETHUSDT", "SOLUSDT", "DOGEUSDT"]) {
      const { container } = render(<BtcContextStrip symbol={symbol} base={BASE} />);
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
    expect(contextBase()).toBeUndefined();
  });
});

// --------------------------------------------------------------------------- inside the terminal

describe("BTC context inside the terminal", () => {
  it.each(WIDE)("draws the lines on the BTC chart and the line under it at %ipx", async width => {
    stubViewport(width);
    fakeBackends();
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await stripLoaded();
    await linesDrawn();
    const drawn = chartOverlays();
    // resistance and support are hidden by default (V3): only the walls are drawn
    expect(drawn).not.toContain("ctx-resistance");
    expect(drawn).not.toContain("ctx-support");
    expect(drawn).toContain("ctx-ask-wall@83050.4=상1");
    expect(drawn).toContain("ctx-bid-wall@82996.5=하1");
    // The account's own lines are still there, first.
    expect(drawn.split("|")[0]).toMatch(/^mark@/);
    const strip = screen.getByTestId("btc-context-strip");
    const chart = screen.getAllByTestId("candle-chart-stub")[0];
    expect(chart.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByTestId("btc-context-strip")).toHaveLength(1);
  });

  it("runs one poll loop for the chart lines and the line under the chart together", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await stripLoaded();
    const before = tally.stripPolls;
    await vi.advanceTimersByTimeAsync(20_000);
    // Four poll intervals: one loop asks four times (give or take the edge), two would ask eight.
    expect(tally.stripPolls - before).toBeGreaterThanOrEqual(3);
    expect(tally.stripPolls - before).toBeLessThanOrEqual(5);
  });

  it.each(WIDE)("leaves the order ticket's container unchanged at %ipx", async width => {
    stubViewport(width);
    fakeBackends();
    const bare = render(<CryptoTerminal />);
    await terminalReady();
    const before = bare.container.querySelector("[data-testid='order-ticket']")?.parentElement?.className;
    cleanup();
    fakeBackends();
    const withIt = render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await stripLoaded();
    const after = withIt.container.querySelector("[data-testid='order-ticket']")?.parentElement?.className;
    expect(after).toBe(before);
  });

  it("puts the line after the order ticket at 390px and still draws on the chart", async () => {
    stubViewport(PHONE_WIDTH);
    fakeBackends();
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await stripLoaded();
    await linesDrawn();
    const strip = screen.getByTestId("btc-context-strip");
    const ticket = screen.getAllByTestId("order-ticket")[0];
    expect(ticket.compareDocumentPosition(strip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByTestId("btc-context-strip")).toHaveLength(1);
  });

  it("draws nothing and asks nothing on the HOT tab, and comes back on BTC", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await stripLoaded();
    await linesDrawn();
    await waitFor(() => expect(
      screen.getByTestId("symbol-tab-hot").getAttribute("data-symbol")).toBe("ETHUSDT"));
    await waitFor(() => expect(screen.getByTestId("symbol-tab-hot")).toBeEnabled());
    fireEvent.click(screen.getByTestId("symbol-tab-hot"));
    await waitFor(() => expect(
      screen.getByTestId("symbol-tab-hot").getAttribute("aria-selected")).toBe("true"));
    expect(screen.queryByTestId("btc-context-strip")).toBeNull();
    await waitFor(() => expect(chartOverlays()).not.toContain("ctx-"));
    const pollsOnHot = tally.stripPolls;
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(tally.stripPolls).toBe(pollsOnHot);
    fireEvent.click(screen.getByTestId("symbol-tab-BTCUSDT"));
    await stripLoaded();
    await linesDrawn();
  });

  it("draws no context line on the BTC chart without a configured backend", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminal />);
    await terminalReady();
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(chartOverlays()).not.toContain("ctx-");
    expect(tally.stripPolls).toBe(0);
  });

  it("issues no non-GET while the terminal and the context are on screen", async () => {
    stubViewport(WIDE[0]);
    const tally = fakeBackends();
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await stripLoaded();
    await linesDrawn();
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(tally.all.filter(r => r.method !== "GET" && r.method !== "HEAD")).toEqual([]);
    expect(tally.all.filter(r => r.url.endsWith("/snapshot")).every(r => r.method === "GET"))
      .toBe(true);
  });

  it("sends the same order request with and without the context", async () => {
    // The order callback is the terminal's; the context must not change what LONG sends.
    const pressLong = async (withContext: boolean) => {
      stubViewport(WIDE[0]);
      const tally = fakeBackends();
      render(withContext ? <CryptoTerminal marketContext={slot} contextBase={BASE} /> : <CryptoTerminal />);
      await terminalReady();
      if (withContext) { await stripLoaded(); await linesDrawn(); }
      const longButton = screen.getAllByTestId("long-button")[0];
      expect(longButton).toBeEnabled();
      fireEvent.click(longButton);
      await waitFor(() => expect(tally.all.some(r => r.method === "POST")).toBe(true));
      const writes = tally.all.filter(r => r.method !== "GET" && r.method !== "HEAD")
        .map(r => ({ url: r.url, method: r.method, body: r.body }));
      cleanup();
      resetBtcContextStores();
      return writes;
    };
    const bare = await pressLong(false);
    const withIt = await pressLong(true);
    expect(bare.length).toBeGreaterThan(0);
    expect(withIt).toEqual(bare);
    expect(withIt.some(r => r.url.endsWith("/snapshot"))).toBe(false);
  });

  it("keeps the order ticket controls when the context API is down", async () => {
    stubViewport(WIDE[0]);
    fakeBackends({ strip: "down" });
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    const ticket = screen.getAllByTestId("order-ticket")[0];
    expect(within(ticket).getAllByTestId("long-button")[0]).toBeEnabled();
    await waitFor(() => expect(screen.getByTestId("flow-horizon-10m-text")).toHaveTextContent(/^--$/));
    expect(chartOverlays()).not.toContain("ctx-");
  });
});
