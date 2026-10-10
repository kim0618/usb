import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { HotCoinFinder, useHotCoins } from "@/components/crypto-hot-coins";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { HOT_STORAGE_KEY, readStoredHot, shouldReplace } from "@/lib/crypto-hot-coins";
import type { HotScanResponse } from "@/lib/crypto-hot-coins";
import liveScan from "@/components/__fixtures__/hot-coins-live.json";
import paperTerminal from "@/components/__fixtures__/crypto-terminal-paper.json";

/** HOT Coin Finder V0 on the screen side: on demand only, a failed scan never costs the held
 *  result, an older answer never replaces a newer one, the result survives a reload, and nothing
 *  about it can select a symbol or send anything but a GET. */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
beforeEach(() => { try { window.localStorage.clear(); } catch { /* private window */ } });

vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: () => React.createElement("div", { "data-testid": "candle-chart-stub" }),
}));

const LIVE = liveScan as unknown as HotScanResponse;

function scanAt(ms: number, symbols?: string[]): HotScanResponse {
  const top = LIVE.top.map((row, index) => ({
    ...row, scanned_at_ms: ms, symbol: symbols?.[index] ?? row.symbol }));
  return { ...LIVE, scanned_at_ms: ms, top };
}

type Call = { url: string; method: string };

function hotBackend(responses: Array<HotScanResponse | "fail" | "hang" | Promise<HotScanResponse>>) {
  const calls: Call[] = [];
  let index = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: (init?.method ?? "GET").toUpperCase() });
    const next = responses[Math.min(index++, responses.length - 1)];
    if (next === "fail") {
      return Promise.resolve({ ok: false, status: 502, json: async () => ({
        error: { code: "HOT_SCAN_UPSTREAM", message: "Binance 시장 데이터를 읽지 못했습니다" } }) });
    }
    if (next === "hang") {
      return new Promise((_, reject) => init?.signal?.addEventListener("abort",
        () => reject(new DOMException("aborted", "AbortError"))));
    }
    return Promise.resolve(next).then(body => ({ ok: true, status: 200, json: async () => body }));
  }));
  return calls;
}

const rows = () => screen.getAllByTestId(/^hot-row-/).map(row => row.getAttribute("data-symbol"));
const click = () => fireEvent.click(screen.getByTestId("hot-scan-button"));

describe("scan", () => {
  it("shows the server's TOP5 in rank order with every field", async () => {
    const calls = hotBackend([LIVE]);
    render(<HotCoinFinder />);
    click();
    await waitFor(() => expect(rows()).toEqual(LIVE.top.map(row => row.symbol)));
    const first = screen.getByTestId("hot-row-1");
    expect(first.textContent).toContain("BAT");
    for (const label of ["24H 범위", "1H 거래대금", "24H 거래대금", "스프레드", "$1,000 왕복"]) {
      expect(first.textContent).toContain(label);
    }
    expect(first.textContent).toContain("15.0%");  // 1H range 14.99 rounded
    expect(calls).toEqual([{ url: expect.stringContaining("/api/crypto/hot-coins"), method: "GET" }]);
    expect(screen.getByTestId("hot-status").textContent).toContain("조회 전용");
  });

  it("renders a Chinese-named symbol as named", async () => {
    hotBackend([scanAt(LIVE.scanned_at_ms, ["龙虾USDT"])]);
    render(<HotCoinFinder />);
    click();
    await waitFor(() => expect(screen.getByTestId("hot-row-1").textContent).toContain("龙虾"));
    expect(screen.getByTestId("hot-row-1").getAttribute("data-symbol")).toBe("龙虾USDT");
  });

  it("does not scan until the button is pressed, and never on a timer", async () => {
    const calls = hotBackend([LIVE]);
    render(<HotCoinFinder />);
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(calls).toHaveLength(0);
    const source = readFileSync(join(__dirname, "crypto-hot-coins.tsx"), "utf8");
    expect(source).not.toMatch(/setInterval/);
    expect(source.match(/setTimeout\(/g)).toHaveLength(1);  // the abort timer only
  });
});

describe("concurrency", () => {
  it("a double click costs one request and the button is disabled while scanning", async () => {
    let release!: (body: HotScanResponse) => void;
    const calls = hotBackend([new Promise<HotScanResponse>(resolve => { release = resolve; })]);
    render(<HotCoinFinder />);
    click();
    click();
    fireEvent.click(screen.getByTestId("hot-scan-button"));
    expect(screen.getByTestId("hot-scan-button")).toBeDisabled();
    expect(screen.getByTestId("hot-scan-button").textContent).toContain("탐색 중");
    await act(async () => { release(LIVE); });
    await waitFor(() => expect(screen.getByTestId("hot-scan-button")).not.toBeDisabled());
    expect(calls).toHaveLength(1);
  });

  it("the hook ignores a second scan() while one is in flight", async () => {
    let release!: (body: HotScanResponse) => void;
    const calls = hotBackend([new Promise<HotScanResponse>(resolve => { release = resolve; })]);
    const { result } = renderHook(() => useHotCoins());
    await act(async () => { void result.current.scan(); void result.current.scan(); });
    await act(async () => { release(LIVE); });
    await waitFor(() => expect(result.current.held?.top).toHaveLength(5));
    expect(calls).toHaveLength(1);
  });

  it("an older answer never replaces a newer held result", async () => {
    window.localStorage.setItem(HOT_STORAGE_KEY, JSON.stringify(
      { scannedAtMs: 2_000_000, top: scanAt(2_000_000, ["NEWUSDT"]).top, universeSize: 524 }));
    hotBackend([scanAt(1_000_000, ["OLDUSDT"])]);
    const { result } = renderHook(() => useHotCoins());
    await waitFor(() => expect(result.current.held?.scannedAtMs).toBe(2_000_000));
    await act(async () => { await result.current.scan(); });
    expect(result.current.held?.scannedAtMs).toBe(2_000_000);
    expect(result.current.held?.top[0].symbol).toBe("NEWUSDT");
    expect(shouldReplace(result.current.held, { scannedAtMs: 1, top: [], universeSize: 0 })).toBe(false);
  });
});

describe("failure keeps the previous result", () => {
  it("on a server error", async () => {
    hotBackend([LIVE, "fail"]);
    render(<HotCoinFinder />);
    click();
    await waitFor(() => expect(rows()).toHaveLength(5));
    click();
    await waitFor(() => expect(screen.getByTestId("hot-status").textContent)
      .toContain("이전 결과를 유지합니다"));
    expect(rows()).toEqual(LIVE.top.map(row => row.symbol));
    expect(readStoredHot()?.scannedAtMs).toBe(LIVE.scanned_at_ms);
  });

  it("on a timeout", async () => {
    hotBackend([LIVE, "hang"]);
    const { result } = renderHook(() => useHotCoins(30));
    await act(async () => { await result.current.scan(); });
    await act(async () => { await result.current.scan(); });
    expect(result.current.error).toContain("시간이 초과");
    expect(result.current.held?.scannedAtMs).toBe(LIVE.scanned_at_ms);
    expect(result.current.scanning).toBe(false);
  });

  it("on a network outage with nothing held yet", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("down"))));
    render(<HotCoinFinder />);
    click();
    await waitFor(() => expect(screen.getByTestId("hot-status").textContent)
      .toContain("연결할 수 없습니다"));
    expect(screen.queryByTestId("hot-list")).toBeNull();
  });
});

describe("persistence", () => {
  it("restores the last result and its scan time after a reload", async () => {
    hotBackend([LIVE]);
    const first = render(<HotCoinFinder />);
    click();
    await waitFor(() => expect(rows()).toHaveLength(5));
    first.unmount();

    const calls = hotBackend([LIVE]);
    render(<HotCoinFinder />);
    fireEvent.click(await screen.findByTestId("hot-show-button"));
    expect(rows()).toEqual(LIVE.top.map(row => row.symbol));
    expect(screen.getByTestId("hot-scanned-at").textContent).toContain("탐색");
    expect(calls).toHaveLength(0);  // viewing the held result does not scan
  });

  it("ignores a corrupt stored value", () => {
    window.localStorage.setItem(HOT_STORAGE_KEY, "{not json");
    expect(readStoredHot()).toBeNull();
    window.localStorage.setItem(HOT_STORAGE_KEY, JSON.stringify({ scannedAtMs: 1, top: [{ symbol: 1 }] }));
    expect(readStoredHot()).toBeNull();
  });
});

// --------------------------------------------------------------------------- in the terminal

type Paper = Record<string, Record<string, unknown>>;
const paper = paperTerminal as unknown as Paper;

function terminalBackend(hot: HotScanResponse) {
  const calls: Call[] = [];
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const dead = { ok: false, status: 503, json: async () => ({ error: { code: "OFFLINE", message: "x" } }) };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: (init?.method ?? "GET").toUpperCase() });
    if (url.includes("/api/crypto/hot-coins")) return ok(hot);
    const symbol = new URL(url, "http://x").searchParams.get("symbol") ?? "BTCUSDT";
    const rows = paper[symbol];
    if (!rows) return dead;
    if (url.includes("/api/crypto/chart-history")) return ok(rows.chart_history);
    if (url.includes("/api/crypto/chart")) return ok({ symbol, bars: (rows.chart_history as { bars: unknown[] }).bars });
    if (url.includes("/api/crypto/state")) return ok(rows.state);
    if (url.includes("/api/crypto/sizing")) return ok(rows.sizing);
    if (url.includes("/api/crypto/performance")) return ok(rows.performance);
    if (url.includes("/api/crypto/ledger")) return ok(rows.ledger);
    if (url.includes("/api/crypto/trades")) return ok(rows.trades);
    if (url.includes("/api/crypto/pnl-breakdown")) return ok(rows.pnl_breakdown);
    return dead;
  }));
  return calls;
}

function stubViewport(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const max = /max-width:\s*(\d+)px/.exec(query);
    const matches = min ? width >= Number(min[1]) : max ? width <= Number(max[1]) : false;
    return { matches, media: query, addEventListener: () => {}, removeEventListener: () => {},
             addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false };
  });
}

describe.each([390, 1440, 1600])("inside the terminal at %ipx", width => {
  it("scans without touching the selected symbol, the chart or any write", async () => {
    stubViewport(width);
    const calls = terminalBackend(scanAt(LIVE.scanned_at_ms, ["ETHUSDT", "SOLUSDT", "MAGICUSDT", "USUSDT", "BATUSDT"]));
    render(<CryptoTerminal />);
    await waitFor(() => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });
    const tabs = screen.getByTestId("symbol-tabs");
    const finder = screen.getByTestId("hot-finder");
    // the button sits beside the tabs, not inside the tab list
    expect(within(tabs).queryByTestId("hot-scan-button")).toBeNull();
    expect(screen.getByTestId("hot-scan-button").textContent).toBe("코인탐색");
    const before = calls.length;

    fireEvent.click(screen.getByTestId("hot-scan-button"));
    await waitFor(() => expect(within(finder).getAllByTestId(/^hot-row-/)).toHaveLength(5));
    // ETH and SOL are HOT here, and still not selected: a result is not a selection
    fireEvent.click(screen.getByTestId("hot-row-1"));
    expect(screen.getByTestId("symbol-tab-BTCUSDT").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("symbol-tab-ETHUSDT").getAttribute("aria-selected")).toBe("false");
    expect(window.localStorage.getItem("usb.crypto.symbol")).not.toBe("ETHUSDT");

    const after = calls.slice(before);
    const hotCalls = after.filter(call => call.url.includes("/api/crypto/hot-coins"));
    expect(hotCalls).toEqual([{ url: expect.stringContaining("/api/crypto/hot-coins"), method: "GET" }]);
    expect(calls.filter(call => call.method !== "GET")).toEqual([]);
    expect(after.filter(call => /symbol=(ETH|SOL|MAGIC|US|BAT)USDT/.test(call.url))).toEqual([]);
    // rows are text, not controls
    expect(within(screen.getByTestId("hot-list")).queryAllByRole("button")).toHaveLength(0);
  });
});

describe("read-only by construction", () => {
  it("imports nothing that can reach an account, an order or the selected symbol", () => {
    for (const file of ["components/crypto-hot-coins.tsx", "lib/crypto-hot-coins.ts"]) {
      const source = readFileSync(join(__dirname, "..", file), "utf8");
      const imports = [...source.matchAll(/from\s+"([^"]+)"/g)].map(match => match[1]);
      expect(imports.filter(name => name.startsWith("@/")).sort(), file).toEqual(
        file.endsWith(".tsx")
          ? ["@/lib/crypto-hot-coins", "@/lib/crypto-hot-coins", "@/lib/crypto-paper"]
          : ["@/lib/crypto-paper"]);
      for (const needle of ["crypto-live", "liveApi", "cryptoApi", "storeSymbol", "useSelectedSymbol",
                            "method: \"POST\"", "method: \"PUT\"", "method: \"DELETE\""]) {
        expect(source, `${file} ${needle}`).not.toContain(needle);
      }
    }
  });
});
