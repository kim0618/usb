import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { HotCoinFinder, scanBlocker, useHotSlot } from "@/components/crypto-hot-coins";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { HOT_STORAGE_KEY, readStoredSlot, shouldReplaceSlot } from "@/lib/crypto-hot-coins";
import type { HotCoin, HotSlotView, HotState } from "@/lib/crypto-hot-coins";
import liveScan from "@/components/__fixtures__/hot-coins-live.json";
import paperTerminal from "@/components/__fixtures__/crypto-terminal-paper.json";

/** Dynamic HOT Trading V1 on the screen side.
 *
 *  The HOT slot is the server's. These tests hold the screen to that: the button follows the
 *  server's lock and nothing else, a failed or stale answer never costs the held HOT symbol, a
 *  reload comes back to it, the HOT tab is the only way to select it, and replacing it twenty
 *  times leaves no request, timer or listener behind for a symbol that left. */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
beforeEach(() => { try { window.localStorage.clear(); } catch { /* private window */ } });

vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: () => React.createElement("div", { "data-testid": "candle-chart-stub" }),
}));

const TOP = (liveScan as unknown as { top: HotCoin[] }).top;
let stamp = 1_000;

function slotOf(symbol: string, at = ++stamp): HotSlotView {
  const top = TOP.map((row, index) => ({ ...row, symbol: index === 0 ? symbol : row.symbol, scanned_at_ms: at }));
  return { symbol, selected_at_ms: at, scanned_at_ms: at, metrics: top[0], top };
}

function state(symbol: string | null, lock: Partial<HotState["lock"]> = {}, extra: Partial<HotState> = {}): HotState {
  return {
    main: "BTCUSDT", hot: symbol ? slotOf(symbol) : null,
    lock: { state: "ALLOWED", code: symbol ? "HOT_SYMBOL_FLAT" : "NO_ACTIVE_HOT_SYMBOL",
            symbol, message: "", ...lock },
    scanning: false, held: [], permitted: [], paper: { available: true, reason: null },
    server_time_ms: 0, ...extra,
  };
}

type Call = { url: string; method: string };
type Reply = HotState | { status: number; body: unknown } | Promise<HotState> | "hang";

/** A fake server for the two HOT routes. `get` answers polls; `scan` answers each POST in turn. */
function hotServer({ get, scan = [] }: { get: () => HotState; scan?: Reply[] }) {
  const calls: Call[] = [];
  let next = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method });
    if (url.includes("/api/crypto/hot/scan")) {
      const reply = scan[Math.min(next++, scan.length - 1)];
      if (reply === "hang") {
        return new Promise((_, reject) => init?.signal?.addEventListener("abort",
          () => reject(new DOMException("aborted", "AbortError"))));
      }
      return Promise.resolve(reply).then(value => {
        if (value && typeof value === "object" && "status" in value && "body" in value) {
          return { ok: false, status: value.status, json: async () => value.body };
        }
        return { ok: true, status: 200, json: async () => ({ ...value, status: "HOT_REPLACED", changed: true,
                                                             previous: null, paper_note: null, scan: null }) };
      });
    }
    if (url.includes("/api/crypto/hot")) return Promise.resolve({ ok: true, status: 200, json: async () => get() });
    return Promise.resolve({ ok: false, status: 503, json: async () => ({ error: { code: "OFFLINE", message: "x" } }) });
  }));
  return calls;
}

function Harness({ busy = false }: { busy?: boolean }) {
  const hot = useHotSlot(60_000);
  return (
    <div>
      <span data-testid="hot-symbol">{hot.symbol ?? "none"}</span>
      <HotCoinFinder hot={hot} busy={busy} />
    </div>
  );
}

const button = () => screen.getByTestId("hot-scan-button");

describe("HOT selection", () => {
  it("has no HOT until the first scan, and the button waits for the server", async () => {
    const calls = hotServer({ get: () => state(null) });
    render(<Harness />);
    expect(button()).toBeDisabled();                                  // not loaded yet
    await waitFor(() => expect(button()).not.toBeDisabled());
    expect(screen.getByTestId("hot-symbol").textContent).toBe("none");
    expect(calls.every(call => call.method === "GET")).toBe(true);   // no scan without a click
  });

  it("a scan puts #1 in the slot and shows no ranking", async () => {
    let current: string | null = null;
    hotServer({ get: () => state(current), scan: [state("LUMIAUSDT")] });
    render(<Harness />);
    await waitFor(() => expect(button()).not.toBeDisabled());
    current = "LUMIAUSDT";
    fireEvent.click(button());
    await waitFor(() => expect(screen.getByTestId("hot-symbol").textContent).toBe("LUMIAUSDT"));
    // Manual UX Final V1: rank 2 and below are the server's business; nothing lists them.
    expect(screen.queryAllByTestId(/^hot-row-/)).toHaveLength(0);
    expect(screen.queryByTestId("hot-list")).toBeNull();
    expect(screen.queryByTestId("hot-coins-panel")).toBeNull();
  });

  it("a scan hands its #1 to the caller so it becomes the selection", async () => {
    hotServer({ get: () => state(null), scan: [state("ARCUSDT")] });
    const picked: string[] = [];
    function Picking() {
      const hot = useHotSlot(60_000);
      return <HotCoinFinder hot={hot} onSelect={symbol => picked.push(symbol)} />;
    }
    render(<Picking />);
    await waitFor(() => expect(button()).not.toBeDisabled());
    fireEvent.click(button());
    await waitFor(() => expect(picked).toEqual(["ARCUSDT"]));
  });

  it("a double click is one scan, and the button is off while it runs", async () => {
    let release!: (value: HotState) => void;
    const calls = hotServer({ get: () => state(null),
                              scan: [new Promise<HotState>(resolve => { release = resolve; })] });
    render(<Harness />);
    await waitFor(() => expect(button()).not.toBeDisabled());
    fireEvent.click(button());
    fireEvent.click(button());
    expect(button()).toBeDisabled();
    expect(button().textContent).toContain("탐색 중");
    await act(async () => { release(state("LUMIAUSDT")); });
    expect(calls.filter(call => call.method === "POST")).toHaveLength(1);
  });

  it("a failed scan keeps the HOT symbol and says so", async () => {
    hotServer({ get: () => state("LUMIAUSDT"),
                scan: [{ status: 502, body: { error: { code: "HOT_SCAN_UPSTREAM", message: "Binance 시장 데이터를 읽지 못했습니다" } } }] });
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("hot-symbol").textContent).toBe("LUMIAUSDT"));
    fireEvent.click(button());
    await waitFor(() => expect(screen.getByTestId("hot-message").textContent).toContain("Binance 시장 데이터를 읽지 못했습니다"));
    expect(screen.getByTestId("hot-symbol").textContent).toBe("LUMIAUSDT");
  });

  it("a timed out scan keeps the HOT symbol", async () => {
    hotServer({ get: () => state("LUMIAUSDT"), scan: ["hang"] });
    const { result } = renderHook(() => useHotSlot(60_000, 30));
    await waitFor(() => expect(result.current.symbol).toBe("LUMIAUSDT"));
    await act(async () => { await result.current.scan(); });
    expect(result.current.error).toContain("시간이 초과");
    expect(result.current.symbol).toBe("LUMIAUSDT");
    expect(result.current.scanning).toBe(false);
  });

  it("an older answer never puts the previous HOT symbol back", async () => {
    const old = slotOf("MAGICUSDT", 10);
    const fresh = slotOf("KAIAUSDT", 20);
    expect(shouldReplaceSlot(fresh, old)).toBe(false);
    expect(shouldReplaceSlot(old, fresh)).toBe(true);
    expect(shouldReplaceSlot(fresh, null)).toBe(false);
    // A poll that left before the scan finished answers with the old slot after it.
    let poll = { ...state("MAGICUSDT"), hot: old };
    hotServer({ get: () => poll, scan: [{ ...state("KAIAUSDT"), hot: fresh }] });
    const { result } = renderHook(() => useHotSlot(20));
    await waitFor(() => expect(result.current.symbol).toBe("MAGICUSDT"));
    await act(async () => { await result.current.scan(); });
    expect(result.current.symbol).toBe("KAIAUSDT");
    poll = { ...state("MAGICUSDT"), hot: old };
    await new Promise(resolve => setTimeout(resolve, 80));            // several stale polls land
    expect(result.current.symbol).toBe("KAIAUSDT");
  });

  it("renders a Chinese-named HOT symbol as named", async () => {
    hotServer({ get: () => state("龙虾USDT") });
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("hot-symbol").textContent).toBe("龙虾USDT"));
  });
});

describe("persistence", () => {
  it("a reload paints the kept slot at once and the server's answer confirms it", async () => {
    hotServer({ get: () => state("LUMIAUSDT") });
    const first = render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("hot-symbol").textContent).toBe("LUMIAUSDT"));
    first.unmount();
    expect(readStoredSlot()?.symbol).toBe("LUMIAUSDT");

    let release!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise(resolve => { release = resolve; })));
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("hot-symbol").textContent).toBe("LUMIAUSDT"));
    expect(button()).toBeDisabled();                    // a kept copy is not the server's word
    await act(async () => { release({ ok: true, status: 200, json: async () => state("LUMIAUSDT") }); });
    await waitFor(() => expect(button()).not.toBeDisabled());
  });

  it("ignores a corrupt kept slot", () => {
    window.localStorage.setItem(HOT_STORAGE_KEY, "{not json");
    expect(readStoredSlot()).toBeNull();
    window.localStorage.setItem(HOT_STORAGE_KEY, JSON.stringify({ symbol: 1 }));
    expect(readStoredSlot()).toBeNull();
  });
});

describe("lock", () => {
  it.each([
    ["HOT_POSITION_OPEN", "현재 HOT 심볼에 포지션이 있어 탐색할 수 없습니다."],
    ["HOT_OPEN_ORDER", "현재 HOT 심볼에 미체결 주문이 있어 탐색할 수 없습니다."],
    ["HOT_ORDER_IN_FLIGHT", "현재 HOT 심볼 주문이 전송 중이라 탐색할 수 없습니다."],
    ["HOT_STATE_UNKNOWN", "현재 HOT 심볼 상태를 확인하지 못해 탐색을 막았습니다."],
  ])("the server's %s turns the button off", async (code, message) => {
    let blocked = true;
    hotServer({ get: () => state("LUMIAUSDT", blocked ? { state: "BLOCKED", code, message } : {}) });
    const { result } = renderHook(() => useHotSlot(20));
    await waitFor(() => expect(result.current.lock?.code).toBe(code));
    expect(scanBlocker(result.current)).toBe(message);
    blocked = false;                                    // the backend reports flat
    await waitFor(() => expect(scanBlocker(result.current)).toBeNull());
  });

  it("a scan refused by the server keeps the HOT symbol and adopts the server's lock", async () => {
    const refusal = { ...state("LUMIAUSDT", { state: "BLOCKED", code: "HOT_POSITION_OPEN",
                                               message: "현재 HOT 심볼에 포지션이 있어 탐색할 수 없습니다." }),
                      error: { code: "HOT_POSITION_OPEN", message: "현재 HOT 심볼에 포지션이 있어 탐색할 수 없습니다." } };
    hotServer({ get: () => state("LUMIAUSDT"), scan: [{ status: 409, body: refusal }] });
    const { result } = renderHook(() => useHotSlot(60_000));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => { await result.current.scan(); });
    expect(result.current.symbol).toBe("LUMIAUSDT");
    expect(result.current.lock?.code).toBe("HOT_POSITION_OPEN");
    expect(scanBlocker(result.current)).not.toBeNull();
  });

  it("an order in flight on screen turns the button off too", () => {
    expect(scanBlocker({ loaded: true, scanning: false, lock: { state: "ALLOWED", code: "", symbol: null, message: "" } }, true))
      .toContain("주문 처리 중");
  });
});

// --------------------------------------------------------------------------- in the terminal

type Paper = Record<string, Record<string, unknown>>;
const paper = paperTerminal as unknown as Paper;

/** The terminal's backend: paper fixtures for BTC, and the same fixtures relabelled for whatever
 *  symbol the HOT slot holds, so a request for the HOT symbol gets an answer that names it. */
function terminalServer() {
  const calls: Call[] = [];
  const hot = { current: null as string | null, next: null as string | null, held: [] as string[],
                position: null as string | null };
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const dead = { ok: false, status: 503, json: async () => ({ error: { code: "OFFLINE", message: "x" } }) };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method });
    if (url.includes("/api/crypto/hot/scan")) {
      if (hot.next) hot.current = hot.next;
      return ok({ ...state(hot.current, {}, { held: hot.held }), status: "HOT_REPLACED", changed: true,
                  previous: null, paper_note: null, scan: null });
    }
    if (url.includes("/api/crypto/hot")) return ok(state(hot.current, {}, { held: hot.held }));
    const symbol = new URL(url, "http://x").searchParams.get("symbol") ?? "BTCUSDT";
    const rows = paper.BTCUSDT;
    const relabel = (body: unknown) => (body && typeof body === "object" && "symbol" in (body as object)
      ? { ...(body as object), symbol } : body);
    if (url.includes("/api/crypto/chart-history")) return ok(relabel(rows.chart_history));
    if (url.includes("/api/crypto/chart")) return ok({ symbol, bars: (rows.chart_history as { bars: unknown[] }).bars });
    if (url.includes("/api/crypto/state")) {
      const body = relabel(rows.state) as Record<string, unknown>;
      if (symbol === hot.current) {
        const instrument = { ...(body.instrument as object), symbol, max_leverage: "20.00" };
        const account = hot.position
          ? { ...(body.account as object), position_side: hot.position, position_qty: "100" } : body.account;
        return ok({ ...body, instrument, account });
      }
      return ok(body);
    }
    if (url.includes("/api/crypto/sizing")) return ok(rows.sizing);
    if (url.includes("/api/crypto/performance")) return ok(rows.performance);
    if (url.includes("/api/crypto/ledger")) return ok(rows.ledger);
    if (url.includes("/api/crypto/trades")) return ok(rows.trades);
    if (url.includes("/api/crypto/pnl-breakdown")) return ok(rows.pnl_breakdown);
    return dead;
  }));
  return { calls, hot };
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

const ready = () => waitFor(() => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });
const hotTab = () => screen.getByTestId("symbol-tab-hot");
const scanTo = async (server: ReturnType<typeof terminalServer>, symbol: string) => {
  server.hot.next = symbol;
  await waitFor(() => expect(button()).not.toBeDisabled());
  fireEvent.click(button());
  await waitFor(() => expect(hotTab().getAttribute("data-symbol")).toBe(symbol));
};

describe.each([390, 1440, 1600])("in the terminal at %ipx", width => {
  it("BTC is fixed, ETH/SOL tabs are gone, and HOT makes the #1 the selected symbol", async () => {
    stubViewport(width);
    const server = terminalServer();
    render(<CryptoTerminal />);
    await ready();
    expect(screen.queryByTestId("symbol-tab-ETHUSDT")).toBeNull();
    expect(screen.queryByTestId("symbol-tab-SOLUSDT")).toBeNull();
    await waitFor(() => expect(hotTab().textContent).toContain("HOT 없음"));
    expect(hotTab()).toBeDisabled();

    await scanTo(server, "LUMIAUSDT");
    expect(hotTab().textContent).toContain("🔥");
    expect(hotTab().textContent).toContain("LUMIA");
    // Manual UX Final V1: the scan's #1 is the selection, with no second press.
    await waitFor(() => expect(hotTab().getAttribute("aria-selected")).toBe("true"));
    await waitFor(() => expect(server.calls.some(call => call.url.includes("/api/crypto/state?symbol=LUMIAUSDT"))).toBe(true));
    expect(window.localStorage.getItem("usb.crypto.symbol")).toBe("LUMIAUSDT");
    expect(server.calls.filter(call => call.method !== "GET").map(call => call.url))
      .toEqual([expect.stringContaining("/api/crypto/hot/scan")]);
  });
});

describe("selection and replacement", () => {
  it("every HOT press moves the selection to the new #1, from the HOT tab or from BTC", async () => {
    stubViewport(1440);
    const server = terminalServer();
    render(<CryptoTerminal />);
    await ready();
    await scanTo(server, "MAGICUSDT");
    fireEvent.click(hotTab());
    await waitFor(() => expect(hotTab().getAttribute("aria-selected")).toBe("true"));
    await scanTo(server, "KAIAUSDT");
    await waitFor(() => expect(hotTab().getAttribute("aria-selected")).toBe("true"));
    await waitFor(() => expect(server.calls.some(call => call.url.includes("/api/crypto/state?symbol=KAIAUSDT"))).toBe(true));

    fireEvent.click(screen.getByTestId("symbol-tab-BTCUSDT"));
    await waitFor(() => expect(screen.getByTestId("symbol-tab-BTCUSDT").getAttribute("aria-selected")).toBe("true"));
    await scanTo(server, "LUMIAUSDT");
    await waitFor(() => expect(hotTab().getAttribute("aria-selected")).toBe("true"));
    expect(screen.getByTestId("symbol-tab-BTCUSDT").getAttribute("aria-selected")).toBe("false");
  });

  it("a reload on the HOT tab comes back to it", async () => {
    stubViewport(1440);
    const server = terminalServer();
    server.hot.current = "LUMIAUSDT";
    window.localStorage.setItem("usb.crypto.symbol", "LUMIAUSDT");
    render(<CryptoTerminal />);
    await ready();
    await waitFor(() => expect(hotTab().getAttribute("aria-selected")).toBe("true"));
    expect(server.calls.some(call => call.url.includes("/api/crypto/state?symbol=BTCUSDT"))).toBe(false);
  });

  it("a remembered symbol that is no longer HOT falls back to BTC", async () => {
    stubViewport(1440);
    const server = terminalServer();
    server.hot.current = "KAIAUSDT";
    window.localStorage.setItem("usb.crypto.symbol", "MAGICUSDT");
    render(<CryptoTerminal />);
    await ready();
    await waitFor(() => expect(screen.getByTestId("symbol-tab-BTCUSDT").getAttribute("aria-selected")).toBe("true"));
  });

  it("a held non-HOT symbol keeps a tab so it can be closed", async () => {
    stubViewport(1440);
    const server = terminalServer();
    server.hot.current = "LUMIAUSDT";
    server.hot.held = ["ETHUSDT"];
    render(<CryptoTerminal />);
    await ready();
    await waitFor(() => expect(screen.getByTestId("symbol-tab-ETHUSDT")).toBeInTheDocument());
    server.hot.held = [];
    // Gone on the next poll (5 s), not before: the position closing is the backend's fact.
    await waitFor(() => expect(screen.queryByTestId("symbol-tab-ETHUSDT")).toBeNull(), { timeout: 7_000 });
  }, 12_000);

  it("twenty replacements leave no request, timer or listener for a symbol that left", async () => {
    stubViewport(1440);
    const server = terminalServer();
    const live = new Set<number>();
    const realSet = window.setTimeout.bind(window);
    const realClear = window.clearTimeout.bind(window);
    vi.spyOn(window, "setTimeout").mockImplementation(((fn: TimerHandler, ms?: number, ...rest: unknown[]) => {
      const id = realSet(((...args: unknown[]) => { live.delete(id); (fn as (...a: unknown[]) => void)(...args); }) as TimerHandler, ms, ...rest);
      live.add(id);
      return id;
    }) as typeof window.setTimeout);
    vi.spyOn(window, "clearTimeout").mockImplementation(((id?: number) => { if (id !== undefined) live.delete(id); realClear(id); }) as typeof window.clearTimeout);
    const added = vi.spyOn(document, "addEventListener");
    const removed = vi.spyOn(document, "removeEventListener");

    render(<CryptoTerminal />);
    await ready();
    const names = Array.from({ length: 21 }, (_, i) => `HOT${String(i).padStart(2, "0")}USDT`);
    await scanTo(server, names[0]);
    fireEvent.click(hotTab());
    let timersAt5 = 0;
    for (let i = 1; i < names.length; i += 1) {
      await scanTo(server, names[i]);
      await waitFor(() => expect(server.calls.some(call => call.url.includes(`/api/crypto/state?symbol=${names[i]}`))).toBe(true));
      if (i === 5) timersAt5 = live.size;
    }
    const mark = server.calls.length;
    await new Promise(resolve => setTimeout(resolve, 2_500));
    const after = server.calls.slice(mark);
    for (const old of names.slice(0, -1)) {
      expect(after.some(call => call.url.includes(`symbol=${old}`)), old).toBe(false);
    }
    expect(after.some(call => call.url.includes(`symbol=${names[names.length - 1]}`))).toBe(true);
    expect(live.size).toBeLessThanOrEqual(timersAt5 + 2);
    const net = (spy: typeof added, type: string) => spy.mock.calls.filter(([t]) => t === type).length;
    for (const type of ["keydown", "pointerdown"]) {
      expect(net(added, type) - net(removed, type)).toBeLessThanOrEqual(1);
    }
  }, 30_000);
});

describe("lock follows the position without waiting for the poll", () => {
  it("re-reads the HOT slot as soon as the screen sees the HOT position change", async () => {
    stubViewport(1440);
    const server = terminalServer();
    server.hot.current = "LUMIAUSDT";
    window.localStorage.setItem("usb.crypto.symbol", "LUMIAUSDT");
    render(<CryptoTerminal />);
    await ready();
    await waitFor(() => expect(hotTab().getAttribute("aria-selected")).toBe("true"));
    await new Promise(resolve => setTimeout(resolve, 300));
    const reads = () => server.calls.filter(call => call.method === "GET" && /\/api\/crypto\/hot(\?|$)/.test(call.url)).length;
    const before = reads();
    server.hot.position = "LONG";                    // the order filled; the state poll sees it
    await waitFor(() => expect(reads()).toBeGreaterThan(before), { timeout: 2_500 });   // poll is 5 s
  });
});

describe("leverage", () => {
  it("offers only what the HOT symbol's own margin table allows", async () => {
    stubViewport(1440);
    const server = terminalServer();
    server.hot.current = "LUMIAUSDT";
    window.localStorage.setItem("usb.crypto.symbol", "LUMIAUSDT");
    render(<CryptoTerminal />);
    await ready();
    await waitFor(() => expect(hotTab().getAttribute("aria-selected")).toBe("true"));
    const ticket = () => screen.getAllByTestId("order-ticket").find(element => element.offsetParent !== null)
      ?? screen.getAllByTestId("order-ticket")[0];
    await waitFor(() => expect(within(ticket()).getByTestId("paper-leverage-50")).toBeDisabled());
    expect(within(ticket()).getByTestId("paper-leverage-50").getAttribute("title")).toContain("최대 20x");
    expect(within(ticket()).getByTestId("paper-leverage-20")).not.toBeDisabled();
  });
});

describe("read-only where it must be", () => {
  it("the HOT code sends nothing but the two HOT routes, and no order", () => {
    for (const file of ["components/crypto-hot-coins.tsx", "lib/crypto-hot-coins.ts"]) {
      const source = readFileSync(join(__dirname, "..", file), "utf8");
      for (const needle of ["crypto-live", "liveApi", "cryptoApi", "/order", "/leverage", "storeSymbol"]) {
        expect(source, `${file} ${needle}`).not.toContain(needle);
      }
    }
    const lib = readFileSync(join(__dirname, "..", "lib/crypto-hot-coins.ts"), "utf8");
    expect(lib.match(/method: "POST"/g)).toHaveLength(1);
    expect(lib).toContain('"/api/crypto/hot/scan", { method: "POST"');
  });

  it("there is no ranking list and no second control to open one", async () => {
    hotServer({ get: () => state("LUMIAUSDT") });
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId("hot-symbol").textContent).toBe("LUMIAUSDT"));
    expect(screen.queryByTestId("hot-show-button")).toBeNull();
    expect(screen.queryByTestId("hot-list")).toBeNull();
    expect(within(screen.getByTestId("hot-finder")).getAllByRole("button")).toHaveLength(1);
  });
});

describe("hydration", () => {
  it("a page prerendered on BTC hydrates onto the remembered HOT tab with exactly one tab pressed", async () => {
    const { renderToString } = await import("react-dom/server");
    const { hydrateRoot } = await import("react-dom/client");
    const { SymbolTabs, useSelectedSymbol } = await import("@/components/crypto-symbol-tabs");
    function Strip() {
      const { symbol, select } = useSelectedSymbol(["BTCUSDT", "LUMIAUSDT"]);
      return <SymbolTabs value={symbol} onChange={select} permitted={["BTCUSDT"]} hotSlot={{ symbol: "LUMIAUSDT" }} />;
    }
    // The prerender has no storage: it is BTC.
    const html = renderToString(<Strip />);
    expect(html).toMatch(/aria-selected="true"[^>]*data-testid="symbol-tab-BTCUSDT"/);
    window.localStorage.setItem("usb.crypto.symbol", "LUMIAUSDT");
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);
    let root: ReturnType<typeof hydrateRoot> | undefined;
    await act(async () => { root = hydrateRoot(container, <Strip />); });
    const pressed = Array.from(container.querySelectorAll('[role="tab"][aria-selected="true"]'))
      .map(element => element.getAttribute("data-symbol"));
    expect(pressed).toEqual(["LUMIAUSDT"]);
    await act(async () => { root?.unmount(); });
    container.remove();
  });
});

describe("prices of a HOT symbol", () => {
  it("BTC, ETH and SOL print exactly as before; a sub-dollar HOT symbol no longer prints 0.0", async () => {
    const { price, chartPriceFormat } = await import("@/lib/crypto-paper");
    expect(price("85516.5")).toBe("85,516.5");
    expect(price("0.10", 2)).toBe("0.10");            // BTC's one-tick spread, unchanged
    expect(price("3120.01")).toBe("3,120.0");
    expect(price("165.43")).toBe("165.4");
    expect(price("0.0246690")).toBe("0.024669");
    expect(price("0.13045")).toBe("0.13045");
    expect(price("2.404")).toBe("2.4040");
    expect(price("0.0000010", 2)).toBe("0.0000010000"); // a HOT one-tick spread is not 0.00
    expect(price("0")).toBe("0.0");
    expect(price(null)).toBe("-");
    expect(chartPriceFormat("0.10")).toEqual({ precision: 1, minMove: 0.1 });
    expect(chartPriceFormat("0.0000010")).toEqual({ precision: 6, minMove: 0.000001 });
    expect(chartPriceFormat("0.000010")).toEqual({ precision: 5, minMove: 0.00001 });
    expect(chartPriceFormat(undefined)).toEqual({ precision: 1, minMove: 0.1 });
  });
});
