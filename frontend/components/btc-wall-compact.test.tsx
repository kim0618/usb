import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BtcContextStrip, BtcContextStripView } from "@/components/btc-context-strip";
import { OVERLAY_STYLE, wallShade } from "@/components/crypto-candle-chart";
import {
  contextChartLines, type BtcContextPayload, type BtcFlowHorizon, type ContextPosition, distanceOrder,
  flowHorizonRows, type FlowHorizonKey, resetBtcContextStores, wallSummary,
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

const H = (buy: number | null, lean: BtcFlowHorizon["lean"], minutes: number, coverage = 1): BtcFlowHorizon => ({
  minutes, buy_pct: buy, sell_pct: buy == null ? null : 100 - buy, lean, quality: buy == null ? "PARTIAL" : "COMPLETE",
  coverage_ratio: coverage, coverage_seconds: minutes * 60 * coverage, expected_seconds: minutes * 60,
});
/** The payload with the backend's four horizons. `spec` is the buy share per horizon, null = withheld. */
function withHorizons(base: BtcContextPayload, spec: Record<FlowHorizonKey, number | null>, live = true): BtcContextPayload {
  const copy = structuredClone(base);
  const minutes = { "10m": 10, "1h": 60, "4h": 240, "1d": 1440 } as const;
  const horizons = {} as Record<FlowHorizonKey, BtcFlowHorizon>;
  for (const key of Object.keys(minutes) as FlowHorizonKey[]) {
    const buy = spec[key];
    horizons[key] = H(buy, buy == null ? null : buy > 50 ? "BUY" : buy < 50 ? "SELL" : "EVEN", minutes[key],
      buy == null ? 0.97 : 1);
  }
  copy.flow_horizons = { live, end_ms: 1_791_781_800_000, horizons };
  return copy;
}
const MIXED = withHorizons(LIVE, { "10m": 70, "1h": 61, "4h": 42, "1d": 52 });

describe("terminology", () => {
  it("prints no 매도벽/매수벽/매도N/매수N anywhere a person reads: chart tags, panel, detail", () => {
    const banned = /매도벽|매수벽|매도\d|매수\d/;
    for (const [payload, position] of [[LIVE, null], [SHORT_P, SHORT], [STALE, null], [null, null]] as const) {
      for (const selection of ["NEAR", "SIZE"] as const) {
        for (const structure of ["HIDDEN", "ANNOTATION"] as const) {
          const lines = contextChartLines(payload, { timeframe: "1m", selection, structure });
          expect(lines.map(l => `${l.tag} ${l.short} ${l.label}`).join(" ")).not.toMatch(banned);
          const { container } = render(<BtcContextStripView payload={payload} position={position} mark={MID}
            selection={selection} structure={structure} />);
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
      render(<BtcContextStripView payload={payload} />);
      expect(screen.getByTestId("btc-context-path-up-value")).toHaveTextContent(/^--$/);
      expect(screen.getByTestId("btc-context-path-down-value")).toHaveTextContent(/^--$/);
      for (const key of ["10m", "1h", "4h", "1d"]) {
        expect(screen.getByTestId(`flow-horizon-${key}-text`)).toHaveTextContent(/^--$/);
      }
    }
  });
});

describe("flow horizons", () => {
  it("draws four rows, 10분 1시간 4시간 1일, each with the larger side's share only", () => {
    render(<BtcContextStripView payload={MIXED} mark={MID} />);
    const rows = screen.getAllByTestId(/^flow-horizon-(10m|1h|4h|1d)$/);
    expect(rows.map(r => r.getAttribute("data-testid"))).toEqual(
      ["flow-horizon-10m", "flow-horizon-1h", "flow-horizon-4h", "flow-horizon-1d"]);
    expect(rows.map(r => r.textContent)).toEqual([
      expect.stringMatching(/^10분.*매수 70%$/), expect.stringMatching(/^1시간.*매수 61%$/),
      expect.stringMatching(/^4시간.*매도 58%$/), expect.stringMatching(/^1일.*매수 52%$/)]);
    expect(rows.map(r => r.getAttribute("data-lean"))).toEqual(["BUY", "BUY", "SELL", "BUY"]);
    expect(screen.getByTestId("flow-horizon-title")).toHaveTextContent("체결 흐름");
  });

  it("makes the direction a shape: the longer half is the larger side, centre is 50:50", () => {
    render(<BtcContextStripView payload={MIXED} mark={MID} />);
    const width = (key: string, side: "sell" | "buy") =>
      Number((within(screen.getByTestId(`flow-horizon-${key}`)).getByTestId(`flow-bar-${side}`) as HTMLElement)
        .style.width.replace("%", ""));
    expect([width("10m", "sell"), width("10m", "buy")]).toEqual([30, 70]);
    expect([width("4h", "sell"), width("4h", "buy")]).toEqual([58, 42]);
    expect(width("10m", "buy")).toBeGreaterThan(width("10m", "sell"));
    expect(width("4h", "sell")).toBeGreaterThan(width("4h", "buy"));
    const sell = within(screen.getByTestId("flow-horizon-10m")).getByTestId("flow-bar-sell") as HTMLElement;
    const buy = within(screen.getByTestId("flow-horizon-10m")).getByTestId("flow-bar-buy") as HTMLElement;
    expect(sell.style.background).toContain("244, 63, 94");  // the upper-wall red
    expect(buy.style.background).toContain("20, 184, 166");  // the lower-wall teal
  });

  it("says 중립 for an exact tie, in grey, and states no strength word", () => {
    const tie = withHorizons(LIVE, { "10m": 50, "1h": 50, "4h": 50, "1d": 50 });
    const { container } = render(<BtcContextStripView payload={tie} mark={MID} />);
    expect(screen.getByTestId("flow-horizon-10m-text")).toHaveTextContent(/^중립$/);
    const sell = within(screen.getByTestId("flow-horizon-10m")).getByTestId("flow-bar-sell") as HTMLElement;
    expect(sell.style.background).toContain("148, 163, 184");
    expect(container.textContent).not.toMatch(/강한|약한|강세|약세|LONG|SHORT|롱|숏/);
  });

  it("is all lean-buy or all lean-sell without changing the layout", () => {
    for (const [spec, word] of [[{ "10m": 64, "1h": 58, "4h": 55, "1d": 53 }, "매수"],
      [{ "10m": 36, "1h": 41, "4h": 45, "1d": 47 }, "매도"]] as const) {
      cleanup();
      render(<BtcContextStripView payload={withHorizons(LIVE, { ...spec })} mark={MID} />);
      for (const key of ["10m", "1h", "4h", "1d"]) {
        expect(screen.getByTestId(`flow-horizon-${key}-text`).textContent).toMatch(new RegExp(`^${word} \\d+%$`));
      }
    }
  });

  it("reads straight from the answer: one payload fills all four, nothing is accumulated", () => {
    expect(flowHorizonRows(MIXED).map(r => r.text)).toEqual(["매수 70%", "매수 61%", "매도 58%", "매수 52%"]);
    const { rerender } = render(<BtcContextStripView payload={MIXED} mark={MID} />);
    rerender(<BtcContextStripView payload={withHorizons(LIVE, { "10m": 20, "1h": 20, "4h": 20, "1d": 20 })} mark={MID} />);
    // the new answer replaces the old one outright
    expect(screen.getByTestId("flow-horizon-1d-text")).toHaveTextContent("매도 80%");
  });

  it("loads on the first poll and keeps no minute history in the browser", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => MIXED })));
    try {
      render(<BtcContextStrip symbol="BTCUSDT" base="/ctx" />);
      await waitFor(() => expect(screen.getByTestId("flow-horizon-1d-text")).toHaveTextContent("매수 52%"));
      expect((fetch as unknown as { mock: { calls: unknown[] } }).mock.calls).toHaveLength(1);
      for (const key of ["10m", "1h", "4h", "1d"]) {
        expect(screen.getByTestId(`flow-horizon-${key}-text`).textContent).not.toContain("수집");
      }
      expect(screen.queryByText(/최근 5분/)).toBeNull();
      const lib = await import("@/lib/btc-context");
      expect("useBtcFlowBins" in lib).toBe(false);
      expect("recordFlowMinute" in lib).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows -- for one withheld horizon only, and for all four when stale, missing or an older answer", () => {
    const partial = withHorizons(LIVE, { "10m": 70, "1h": 61, "4h": null, "1d": 52 });
    render(<BtcContextStripView payload={partial} mark={MID} />);
    expect(screen.getByTestId("flow-horizon-4h-text")).toHaveTextContent(/^--$/);
    expect(screen.getByTestId("flow-horizon-10m-text")).toHaveTextContent("매수 70%");
    expect(screen.getByTestId("flow-horizon-4h").querySelector("[data-testid^=flow-bar]")).toBeNull();
    for (const payload of [withHorizons(LIVE, { "10m": 70, "1h": 61, "4h": 42, "1d": 52 }, false), null, LIVE]) {
      cleanup();
      render(<BtcContextStripView payload={payload} mark={MID} />);
      for (const key of ["10m", "1h", "4h", "1d"]) {
        expect(screen.getByTestId(`flow-horizon-${key}-text`)).toHaveTextContent(/^--$/);
      }
    }
  });

  it("keeps the default area to the flow rows: no wall counts, no OI, no funding, no 5-minute line", () => {
    render(<BtcContextStripView payload={MIXED} mark={MID} />);
    const strip = screen.getByTestId("btc-context-strip").cloneNode(true) as HTMLElement;
    strip.querySelector("[data-testid='btc-context-detail']")?.remove();
    const outside = strip.textContent ?? "";
    for (const word of ["개 ·", "OI", "펀딩", "최근 5분", "상단벽", "하단벽", "현재 대비", "간격", "베이시스", "저항", "지지"]) {
      expect(outside, word).not.toContain(word);
    }
    expect(outside).toContain("10분");
    const detail = screen.getByTestId("btc-context-detail");
    for (const word of ["OI", "펀딩", "상단벽1", "하단벽1", "60초 체결", "가까운 순"]) {
      expect(detail.textContent, word).toContain(word);
    }
    expect(detail.textContent).toContain("집계 범위 10분 100%");
  });

  it("places the 가까운 순 / 큰 순 switch beside the title (A) or inside the detail (B)", () => {
    const a = render(<BtcContextStripView payload={MIXED} mark={MID} />);
    const inDetail = (c: HTMLElement) => c.querySelector("[data-testid='btc-context-detail'] [data-testid='btc-context-selection']");
    expect(screen.getByTestId("btc-context-selection")).toBeInTheDocument();
    expect(inDetail(a.container)).toBeNull();
    cleanup();
    const b = render(<BtcContextStripView payload={MIXED} mark={MID} sortPlacement="DETAIL" />);
    expect(inDetail(b.container)).not.toBeNull();
    expect(screen.getAllByTestId("btc-context-selection")).toHaveLength(1);
  });

  it("shows the wall rows only in the detail, and the chart lines are unchanged by the horizons", () => {
    expect(contextChartLines(MIXED, { timeframe: "1m" })).toEqual(contextChartLines(LIVE, { timeframe: "1m" }));
  });
});
