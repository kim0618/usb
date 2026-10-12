import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BtcContextStrip, BtcContextStripView } from "@/components/btc-context-strip";
import { OVERLAY_STYLE, wallShade } from "@/components/crypto-candle-chart";
import {
  contextChartLines, type BtcContextPayload, type ContextPosition, distanceOrder, flowPressure,
  type FlowBins, PRESSURE_MINUTES, recordFlowMinute, resetBtcContextStores, wallSummary,
} from "@/lib/btc-context";
import v3 from "@/components/__fixtures__/btc-context-snapshot-v3.json";

/** BTC Wall UI Compact V1: upper/lower wall labels, distance-ordered shades, the compact panel and
 *  the five-minute flow count. Payloads are the V3 fixture (the backend's own output). */
const LIVE = v3.live as unknown as BtcContextPayload;
const SHORT_P = v3.short as unknown as BtcContextPayload;
const STALE = v3.stale as unknown as BtcContextPayload;
const MID = Number(LIVE.walls!.mid);
const SHORT: ContextPosition = { sign: -1, entry: 83612.9, qty: "0.010" };

afterEach(() => { cleanup(); resetBtcContextStores(); });

const MIN = 60_000;
/** The payload as answered inside minute `minute`, flow `buy`/`sell`. */
function at(minute: number, buy = 50, sell = 50, base: BtcContextPayload = LIVE): BtcContextPayload {
  const copy = structuredClone(base);
  copy.server_time_ms = minute * MIN + 30_000;
  copy.flow = { ...copy.flow, live: true, buy_pct: buy, sell_pct: sell };
  return copy;
}
function binsOf(rows: [number, number, number][]): FlowBins {
  const bins: FlowBins = {};
  for (const [m, buy, sell] of rows) bins[m] = { buy, sell };
  return bins;
}
const NOW = 29_000_000;

describe("terminology", () => {
  it("prints no 매도벽/매수벽/매도N/매수N anywhere a person reads: chart tags, panel, detail", () => {
    const banned = /매도벽|매수벽|매도\d|매수\d/;
    for (const [payload, position] of [[LIVE, null], [SHORT_P, SHORT], [STALE, null], [null, null]] as const) {
      for (const selection of ["NEAR", "SIZE"] as const) {
        for (const structure of ["HIDDEN", "ANNOTATION"] as const) {
          const lines = contextChartLines(payload, { timeframe: "1m", selection, structure });
          expect(lines.map(l => `${l.tag} ${l.short} ${l.label}`).join(" ")).not.toMatch(banned);
          const { container } = render(<BtcContextStripView payload={payload} position={position} mark={MID}
            selection={selection} structure={structure}
            flowBins={binsOf([[NOW - 1, 80, 20], [NOW - 2, 20, 80]])} />);
          expect(container.textContent).not.toMatch(banned);
          cleanup();
        }
      }
    }
  });

  it("labels the chart 상1 상2 상3 / 하1 하2 하3 and the detail 상단벽N / 하단벽N", () => {
    const lines = contextChartLines(LIVE, { timeframe: "1m" });
    expect(lines.filter(l => l.kind === "ASK_WALL").map(l => l.tag)).toEqual(["상1", "상2", "상3"]);
    expect(lines.filter(l => l.kind === "BID_WALL").map(l => l.tag))
      .toEqual(LIVE.walls!.bid.slice(0, 3).map((_, i) => `하${i + 1}`));
    expect(lines.filter(l => l.kind === "ASK_WALL").map(l => l.short)).toEqual(["상단벽1", "상단벽2", "상단벽3"]);
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const rows = screen.getAllByTestId("wall-detail-row").map(r => r.textContent ?? "");
    expect(rows[0]).toMatch(/^상단벽1 /);
    expect(rows[2]).toMatch(/^상단벽3 /);
    expect(rows.some(r => /^하단벽1 /.test(r))).toBe(true);
  });
});

describe("shades follow the distance, not the size", () => {
  it("upper walls are red and lower walls teal, darkest at 1 and lightest at 3", () => {
    const alpha = (css: string) => Number(/,([\d.]+)\)$/.exec(css)![1]);
    const ask = [1, 2, 3].map(o => wallShade("ASK_WALL", o as 1 | 2 | 3)!);
    const bid = [1, 2, 3].map(o => wallShade("BID_WALL", o as 1 | 2 | 3)!);
    expect(alpha(ask[0].line)).toBeGreaterThan(alpha(ask[1].line));
    expect(alpha(ask[1].line)).toBeGreaterThan(alpha(ask[2].line));
    expect(alpha(bid[0].line)).toBeGreaterThan(alpha(bid[2].line));
    expect(ask[0].line).toContain("244,63,94");
    expect(bid[0].line).toContain("20,184,166");
    expect(wallShade("MARK", undefined)).toBeNull();
    expect(wallShade("ENTRY", 1)).toBeNull();
  });

  it("gives every line its collector rank as the step, in NEAR and in SIZE mode alike", () => {
    const near = contextChartLines(LIVE, { timeframe: "1m", selection: "NEAR" });
    expect(near.filter(l => l.kind === "ASK_WALL").map(l => l.order)).toEqual([1, 2, 3]);
    const size = contextChartLines(LIVE, { timeframe: "1m", selection: "SIZE" });
    const picked = LIVE.walls!.ask.filter(w => w.size_rank <= 3).sort((a, b) => a.rank - b.rank);
    const lines = size.filter(l => l.kind === "ASK_WALL");
    expect(lines.map(l => l.price)).toEqual(picked.map(w => Number(w.price)));
    expect(lines.map(l => l.order)).toEqual(picked.map(w => distanceOrder(w.rank)));
    // the nearer the line, the darker (order never rises with the price distance)
    expect(lines.map(l => l.order)).toEqual([...lines.map(l => l.order!)].sort((a, b) => a - b));
    // and the step is not the size rank: some chosen wall is not rank 1..3 by distance
    expect(picked.some(w => w.rank > 3) || picked.map(w => w.rank).join() !== picked.map(w => w.size_rank).sort().join())
      .toBe(true);
  });

  it("clamps the step at 3 and leaves resistance/support grey, dotted and off by default", () => {
    expect([1, 2, 3, 4, 6].map(distanceOrder)).toEqual([1, 2, 3, 3, 3]);
    expect(OVERLAY_STYLE.RESISTANCE.color).toBe(OVERLAY_STYLE.SUPPORT.color);
    expect(OVERLAY_STYLE.RESISTANCE.color).toContain("148,163,184");
    expect(OVERLAY_STYLE.RESISTANCE.lineStyle).toBe(1);
    expect(contextChartLines(LIVE, { timeframe: "1m" })
      .some(l => l.kind === "RESISTANCE" || l.kind === "SUPPORT")).toBe(false);
  });

  it("keeps the timeframe thinning: 3, 3, 2, 1, 0, 0", () => {
    const counts = ["15s", "1m", "10m", "1h", "4h", "1d"].map(tf =>
      contextChartLines(LIVE, { timeframe: tf }).filter(l => l.kind === "ASK_WALL").length);
    expect(counts).toEqual([3, 3, 2, 1, 0, 0]);
  });
});

describe("compact summary", () => {
  it("shows a count and the nearest distance per side, current-relative without a position", () => {
    const summary = wallSummary(LIVE, { mark: MID });
    expect(summary.basis).toBe("현재 대비");
    const nearestUp = (Number(LIVE.walls!.ask[0].price) - MID) / MID * 100;
    expect(summary.up.text).toBe(`${LIVE.walls!.ask.length}개 · 현재 대비 +${nearestUp.toFixed(2)}%`);
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-up")).toHaveTextContent("상단벽 · 가까운 순");
    expect(screen.getByTestId("btc-context-path-down")).toHaveTextContent("하단벽 · 가까운 순");
    expect(screen.getByTestId("btc-context-path-up-value")).toHaveTextContent(summary.up.text);
    expect(screen.getByTestId("btc-context-path-down-value")).toHaveTextContent("현재 대비 -");
  });

  it("says 진입 대비 with a position, measured from the entry", () => {
    const summary = wallSummary(SHORT_P, { mark: MID, position: SHORT });
    expect(summary.basis).toBe("진입 대비");
    const nearest = (Number(SHORT_P.walls!.ask[0].price) - SHORT.entry) / SHORT.entry * 100;
    expect(summary.up.nearestPct).toBeCloseTo(nearest, 10);
    render(<BtcContextStripView payload={SHORT_P} position={SHORT} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-up-value")).toHaveTextContent("진입 대비");
    expect(screen.getByTestId("btc-context-path-up-value").textContent).not.toContain("현재 대비");
  });

  it("keeps the nearest figure when the detail is sorted by size, and shows the mode in the header", () => {
    expect(wallSummary(LIVE, { mark: MID, selection: "SIZE" }).up.nearestPct)
      .toBe(wallSummary(LIVE, { mark: MID, selection: "NEAR" }).up.nearestPct);
    render(<BtcContextStripView payload={LIVE} mark={MID} selection="SIZE" />);
    expect(screen.getByTestId("btc-context-path-up")).toHaveTextContent("상단벽 · 큰 순");
    expect(screen.getByTestId("btc-context-path-down")).toHaveTextContent("하단벽 · 큰 순");
  });

  it("does not list prices, gaps, size or persistence outside the detail", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const detail = screen.getByTestId("btc-context-detail");
    const clone = screen.getByTestId("btc-context-strip").cloneNode(true) as HTMLElement;
    clone.querySelector("[data-testid='btc-context-detail']")?.remove();
    const outside = clone.textContent ?? "";
    for (const row of LIVE.walls!.ask.slice(0, 3)) {
      expect(outside).not.toContain(Number(row.price).toLocaleString("en-US", { minimumFractionDigits: 1 }));
    }
    for (const word of ["간격", "유지", "이어짐", "새로 관측", "배수", "최초", "베이시스", "저항", "지지",
      "Q1", "Q2", "Q3", "진입분석", "관망"]) {
      expect(outside, word).not.toContain(word);
    }
    expect(detail).toBeInTheDocument();
  });

  it("is closed by default and its rows carry price, gaps, size, persistence and NEW/CARRIED when opened", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const details = screen.getByTestId("btc-context-details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(screen.getByTestId("btc-context-details-toggle"));
    details.open = true;
    const rows = within(screen.getByTestId("btc-context-wall-detail")).getAllByTestId("wall-detail-row");
    const first = rows[0].textContent ?? "";
    expect(first).toMatch(/^상단벽1 [\d,]+\.\d · 현재 대비 \+\d\.\d\d% · 간격 \d\.\d\d% · [\d.]+[KMB] · .+ 유지/);
    expect(first).toMatch(/(이어짐|새로 관측)/);
    expect(first).toMatch(/최초 \d\d:\d\d:\d\d/);
    expect(rows).toHaveLength(Math.min(3, LIVE.walls!.ask.length) + Math.min(3, LIVE.walls!.bid.length));
  });

  it("adds the entry-relative figure to each detail row with a position", () => {
    render(<BtcContextStripView payload={SHORT_P} position={SHORT} mark={MID} />);
    expect(screen.getAllByTestId("wall-detail-row")[0].textContent).toMatch(/현재 대비 .* · 진입 대비 [+-]/);
  });
});

describe("no wall and stale", () => {
  it("says 상단벽 없음 / 하단벽 없음 only where the collector read an empty side", () => {
    const none = structuredClone(LIVE);
    none.walls!.ask = []; none.walls!.bid = [];
    none.walls!.empty = { ask: true, bid: true };
    render(<BtcContextStripView payload={none} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-up-value")).toHaveTextContent(/^상단벽 없음$/);
    expect(screen.getByTestId("btc-context-path-down-value")).toHaveTextContent(/^하단벽 없음$/);
  });

  it("keeps stale and missing as -- and distinct from none", () => {
    for (const payload of [STALE, null]) {
      cleanup();
      render(<BtcContextStripView payload={payload} flowBins={binsOf([[NOW - 1, 90, 10]])} />);
      expect(screen.getByTestId("btc-context-path-up-value")).toHaveTextContent(/^--$/);
      expect(screen.getByTestId("btc-context-path-down-value")).toHaveTextContent(/^--$/);
      expect(screen.getByTestId("btc-context-pressure-text")).toHaveTextContent(/^--$/);
      expect(screen.queryByTestId("btc-context-pressure-dots")).toBeNull();
    }
  });
});

describe("five minutes of flow", () => {
  const live = at(NOW);
  it("counts one vote per completed minute: more buying = buy, more selling = sell, equal = neutral", () => {
    const bins = binsOf([[NOW - 5, 60, 40], [NOW - 4, 55, 45], [NOW - 3, 30, 70], [NOW - 2, 51, 49], [NOW - 1, 80, 20]]);
    const p = flowPressure(bins, live);
    expect([p.buy, p.sell, p.even, p.known]).toEqual([4, 1, 0, 5]);
    expect(p.text).toBe("매수 4 : 1 매도");
    expect(p.minutes).toEqual(["BUY", "BUY", "SELL", "BUY", "BUY"]);
    expect(p.coverage).toBe("");
  });

  it("states neutral minutes, only on an exact tie, and uses no threshold", () => {
    const bins = binsOf([[NOW - 5, 50, 50], [NOW - 4, 51, 49], [NOW - 3, 49, 51], [NOW - 2, 60, 40], [NOW - 1, 20, 80]]);
    const p = flowPressure(bins, live);
    expect([p.buy, p.even, p.sell]).toEqual([2, 1, 2]);
    expect(p.text).toBe("매수 2 · 중립 1 · 매도 2");
    // 51:49 is a buy minute: no 55/60 band
    expect(p.minutes[1]).toBe("BUY");
  });

  it("does not fill a minute it did not see, and ignores the minute in progress and older ones", () => {
    const bins = binsOf([[NOW - 7, 10, 90], [NOW - 4, 70, 30], [NOW - 2, 20, 80], [NOW, 99, 1]]);
    const p = flowPressure(bins, live);
    expect(p.minutes).toEqual([null, "BUY", null, "SELL", null]);
    expect(p.text).toBe("매수 1 : 1 매도");
    expect(p.coverage).toBe("2/5분");
    expect(flowPressure({}, live).text).toBe("수집 중");
  });

  it("keeps the latest reading of each minute and nothing from a stale or missing flow", () => {
    let bins: FlowBins = {};
    bins = recordFlowMinute(bins, at(NOW - 1, 30, 70));
    bins = recordFlowMinute(bins, at(NOW - 1, 80, 20));
    expect(bins[NOW - 1]).toEqual({ buy: 80, sell: 20 });
    const before = bins;
    expect(recordFlowMinute(bins, STALE)).toBe(before);
    expect(recordFlowMinute(bins, null)).toBe(before);
    const dead = at(NOW, 60, 40);
    dead.flow.live = false;
    expect(recordFlowMinute(bins, dead)).toBe(before);
    expect(recordFlowMinute(bins, at(NOW - 1, 80, 20))).toBe(before);
    // old minutes are dropped
    const rolled = recordFlowMinute(bins, at(NOW + 20, 55, 45));
    expect(Object.keys(rolled).map(Number)).toEqual([NOW + 20]);
    expect(PRESSURE_MINUTES).toBe(5);
  });

  it("renders the count and the five marks, with no LONG/SHORT word and no signal colour", () => {
    const bins = binsOf([[NOW - 5, 60, 40], [NOW - 4, 55, 45], [NOW - 3, 30, 70], [NOW - 2, 51, 49], [NOW - 1, 80, 20]]);
    const { container } = render(<BtcContextStripView payload={live} mark={MID} flowBins={bins} />);
    expect(screen.getByTestId("btc-context-pressure")).toHaveTextContent("최근 5분");
    expect(screen.getByTestId("btc-context-pressure-text")).toHaveTextContent("매수 4 : 1 매도");
    expect(screen.getByTestId("btc-context-pressure-dots").textContent).toBe("●●○●●");
    expect(screen.getByTestId("btc-context-pressure-dots").getAttribute("aria-label"))
      .toBe("최근 5분 매수 매수 매도 매수 매수");
    expect(container.textContent).not.toMatch(/LONG|SHORT|롱|숏/);
    const dots = screen.getByTestId("btc-context-pressure-dots");
    expect(dots.outerHTML).not.toMatch(/text-(success|danger)/);
    cleanup();
    render(<BtcContextStripView payload={live} mark={MID}
      flowBins={binsOf([[NOW - 5, 50, 50], [NOW - 4, 60, 40], [NOW - 3, 60, 40], [NOW - 2, 40, 60], [NOW - 1, 40, 60]])} />);
    expect(screen.getByTestId("btc-context-pressure-text")).toHaveTextContent("매수 2 · 중립 1 · 매도 2");
  });

  it("shows how many minutes it has while it is collecting", () => {
    render(<BtcContextStripView payload={live} mark={MID} flowBins={binsOf([[NOW - 1, 80, 20], [NOW - 2, 20, 80]])} />);
    expect(screen.getByTestId("btc-context-pressure-text")).toHaveTextContent("매수 1 : 1 매도");
    expect(screen.getByTestId("btc-context-pressure-coverage")).toHaveTextContent("2/5분");
  });
});

describe("the poll loop keeps the minutes", () => {
  it("records each minute's last answer and counts it once the minute is over", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let minute = NOW - 2;
    let buy = 80;
    const methods: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: RequestInit) => {
      methods.push((init?.method ?? "GET").toUpperCase());
      return { ok: true, status: 200, json: async () => at(minute, buy, 100 - buy) };
    }));
    try {
      render(<BtcContextStrip symbol="BTCUSDT" base="/ctx" />);
      await waitFor(() => expect(screen.getByTestId("btc-context-pressure")).toHaveTextContent("수집 중"));
      minute = NOW - 1; buy = 20;
      await vi.advanceTimersByTimeAsync(6_000);
      await waitFor(() => expect(screen.getByTestId("btc-context-pressure-text")).toHaveTextContent("매수 1 : 0 매도"));
      minute = NOW; buy = 55;
      await vi.advanceTimersByTimeAsync(6_000);
      await waitFor(() => expect(screen.getByTestId("btc-context-pressure-text")).toHaveTextContent("매수 1 : 1 매도"));
      expect(screen.getByTestId("btc-context-pressure-coverage")).toHaveTextContent("2/5분");
      expect(methods.every(m => m === "GET")).toBe(true);
    } finally {
      vi.useRealTimers(); vi.unstubAllGlobals();
    }
  });
});
