import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { BtcContextStrip, BtcContextStripView } from "@/components/btc-context-strip";
import { contextTags } from "@/components/crypto-candle-chart";
import {
  contextChartLines, type BtcContextPayload, type BtcWall, type ContextPosition, entryComparison,
  entryDistancePct, entryFor, pickWalls, resetBtcContextStores, setWallSelection, wallLinesFor,
  wallPaths, WALL_LINES_BY_TIMEFRAME,
} from "@/lib/btc-context";

/** BTC Manual Trading UI V3: position-aware context.
 *
 *  `btc-context-snapshot-v3.json` is `app.crypto.btc_context_ui_v1.view.display` run over the
 *  production Context Collector's current-state file of 2026-10-12 (six ask walls, two bid walls
 *  published) with a recorder store in the recorder's own row shapes holding one open SHORT
 *  (`short`) or LONG (`long`) entry at 83,612.9. A third bid wall, where a test needs one, is
 *  added in the test and says so.
 */
import v3 from "@/components/__fixtures__/btc-context-snapshot-v3.json";
import paperTerminal from "@/components/__fixtures__/crypto-terminal-paper.json";

const LIVE = v3.live as unknown as BtcContextPayload;
const SHORT_P = v3.short as unknown as BtcContextPayload;
const LONG_P = v3.long as unknown as BtcContextPayload;
const STALE = v3.stale as unknown as BtcContextPayload;
const MID = Number(LIVE.walls!.mid);
const SHORT: ContextPosition = { sign: -1, entry: 83612.9, qty: "0.010" };
const LONG: ContextPosition = { sign: 1, entry: 83612.9, qty: "0.010" };

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

/** The fixture with a third bid wall below the two published ones (test data, not collector). */
function withThirdBid(payload: BtcContextPayload): BtcContextPayload {
  const copy = structuredClone(payload);
  const last = copy.walls!.bid[copy.walls!.bid.length - 1];
  copy.walls!.bid.push({ ...last, rank: last.rank + 1, price: (Number(last.price) - 40).toFixed(1),
    notional_usdt: "999999", size_rank: 1 });
  copy.walls!.bid.forEach(w => { if (w.price !== copy.walls!.bid[2].price) w.size_rank += 1; });
  return copy;
}

const rowsOf = (testId: string) => screen.queryAllByTestId(`${testId}-row`);
const prices = (testId: string) => rowsOf(testId).map(r => Number(r.getAttribute("data-price")));

// --------------------------------------------------------------------------- pure functions

describe("wall paths", () => {
  it("renders 3 ask and 3 bid walls, nearest first on each path", () => {
    render(<BtcContextStripView payload={withThirdBid(LIVE)} mark={MID} />);
    const up = prices("btc-context-path-up"), down = prices("btc-context-path-down");
    expect(up).toHaveLength(3);
    expect(down).toHaveLength(3);
    expect(up).toEqual([...up].sort((a, b) => a - b));
    expect(down).toEqual([...down].sort((a, b) => b - a));
    expect(up.every(p => p > MID) && down.every(p => p < MID)).toBe(true);
    expect(up).toEqual(LIVE.walls!.ask.slice(0, 3).map(w => Number(w.price)));
  });

  it("shows only the walls the collector published, never a made-up one", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    expect(prices("btc-context-path-down")).toEqual(LIVE.walls!.bid.map(w => Number(w.price)));
    expect(LIVE.walls!.bid).toHaveLength(2);
  });

  it("measures from the current price without a position, gap from the previous step", () => {
    const paths = wallPaths(LIVE, { mark: MID });
    expect(paths.mode).toBe("MARKET");
    const first = paths.up[0], second = paths.up[1];
    expect(first.primaryPct).toBeCloseTo((first.price - MID) / MID * 100, 10);
    expect(first.entryPct).toBeNull();
    expect(first.gapPct).toBeCloseTo((first.price - MID) / MID * 100, 10);
    expect(second.gapPct).toBeCloseTo((second.price - first.price) / first.price * 100, 10);
    expect(paths.down[0].primaryPct).toBeLessThan(0);
  });

  it("measures from the entry in the price's direction for a SHORT: above the entry is +", () => {
    const paths = wallPaths(SHORT_P, { mark: MID, position: SHORT });
    expect(paths.mode).toBe("POSITION");
    for (const row of [...paths.up, ...paths.down]) {
      expect(row.primaryPct).toBeCloseTo((row.price - SHORT.entry) / SHORT.entry * 100, 10);
      expect(row.currentPct).toBeCloseTo((row.price - MID) / MID * 100, 10);
    }
    // the sign follows the price against the entry, not the path: a bid wall below the current
    // price but above the entry reads +
    for (const row of [...paths.up, ...paths.down]) {
      expect(Math.sign(row.primaryPct)).toBe(Math.sign(row.price - SHORT.entry));
    }
    // never turned towards the position's gain: a lower price is - for a SHORT too
    expect(entryDistancePct(SHORT, 82_980)).toBeLessThan(0);
    expect(entryDistancePct(SHORT, 84_000)).toBeGreaterThan(0);
    expect(entryDistancePct(SHORT, 83_894.1)).toBeCloseTo(0.3363, 3);
  });

  it("measures from the entry in the price's direction for a LONG: the same sign as a SHORT", () => {
    const paths = wallPaths(LONG_P, { mark: MID, position: LONG });
    for (const row of paths.up) {
      expect(row.primaryPct).toBeCloseTo((row.price - LONG.entry) / LONG.entry * 100, 10);
    }
    expect(entryDistancePct(LONG, 84_000)).toBeGreaterThan(0);
    expect(entryDistancePct(LONG, 83_000)).toBeLessThan(0);
    expect(entryDistancePct(LONG, 84_000)).toBe(entryDistancePct(SHORT, 84_000));
  });

  it("puts the count of published walls per side in the title, and names an empty side", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-up-count")).toHaveTextContent(String(LIVE.walls!.ask.length));
    expect(screen.getByTestId("btc-context-path-down-count")).toHaveTextContent(String(LIVE.walls!.bid.length));
    cleanup();
    const askOnly = structuredClone(LIVE);
    askOnly.walls!.bid = [];
    askOnly.walls!.empty = { ask: false, bid: true };
    render(<BtcContextStripView payload={askOnly} mark={MID} />);
    expect(screen.getAllByTestId("btc-context-path-up-row")).toHaveLength(3);
    expect(screen.getByTestId("btc-context-path-down-count")).toHaveTextContent("0");
    expect(screen.getByTestId("btc-context-path-down-empty")).toHaveTextContent("현재 적격 매수벽 없음");
    expect(screen.getByTestId("btc-context-path-down-empty").textContent).not.toMatch(/USDT|50만|500/);
    cleanup();
    const bidOnly = structuredClone(LIVE);
    bidOnly.walls!.ask = [];
    bidOnly.walls!.empty = { ask: true, bid: false };
    render(<BtcContextStripView payload={bidOnly} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-up-empty")).toHaveTextContent("현재 적격 매도벽 없음");
    expect(screen.getAllByTestId("btc-context-path-down-row")).toHaveLength(LIVE.walls!.bid.length);
    cleanup();
    // a side the collector could not read (not its NONE) and a stale reading stay "--"
    const unread = structuredClone(LIVE);
    unread.walls!.ask = [];
    unread.walls!.empty = { ask: false, bid: false };
    render(<BtcContextStripView payload={unread} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-up-empty")).toHaveTextContent("--");
    cleanup();
    render(<BtcContextStripView payload={STALE} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-up-empty")).toHaveTextContent("--");
    expect(screen.getByTestId("btc-context-path-up-count")).toHaveTextContent("0");
  });

  it("lists one or two walls as they are, three at most, nearest first", () => {
    const one = structuredClone(LIVE);
    one.walls!.ask = one.walls!.ask.slice(0, 1);
    render(<BtcContextStripView payload={one} mark={MID} />);
    expect(prices("btc-context-path-up")).toEqual([Number(LIVE.walls!.ask[0].price)]);
    cleanup();
    const two = structuredClone(LIVE);
    two.walls!.ask = two.walls!.ask.slice(0, 2);
    render(<BtcContextStripView payload={two} mark={MID} />);
    expect(prices("btc-context-path-up")).toEqual(LIVE.walls!.ask.slice(0, 2).map(w => Number(w.price)));
    cleanup();
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const up = prices("btc-context-path-up");
    expect(up).toHaveLength(3);
    expect(up).toEqual([...up].sort((a, b) => a - b));
    expect(screen.getByTestId("btc-context-path-up-count")).toHaveTextContent("6");
  });

  it("orders the largest three by size, then lists them in path order; differs from nearest", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} selection="SIZE" />);
    const bySize = prices("btc-context-path-up");
    const largest = [...LIVE.walls!.ask].sort((a, b) => a.size_rank - b.size_rank).slice(0, 3)
      .map(w => Number(w.price)).sort((a, b) => a - b);
    expect(bySize).toEqual(largest);
    cleanup();
    render(<BtcContextStripView payload={LIVE} mark={MID} selection="NEAR" />);
    expect(prices("btc-context-path-up")).toEqual(LIVE.walls!.ask.slice(0, 3).map(w => Number(w.price)));
  });

  it("measures from the walls' own book mid, the terminal's mark only without one", () => {
    // A PAPER mark is another venue's price: an ask wall must never read as below "current".
    expect(wallPaths(LIVE, { mark: MID + 100 }).current).toBe(MID);
    const paths = wallPaths(LIVE, { mark: Number(LIVE.walls!.ask[0].price) + 50 });
    expect(paths.up.every(r => r.currentPct > 0) && paths.down.every(r => r.currentPct < 0)).toBe(true);
    const noMid = structuredClone(LIVE);
    noMid.walls!.mid = null;
    expect(wallPaths(noMid, { mark: 83_000 }).current).toBe(83_000);
  });

  it("chooses the three largest when asked, still in path order", () => {
    const walls = LIVE.walls!.ask;
    const size = pickWalls(walls, "SIZE");
    expect(size.map(w => w.size_rank).sort()).toEqual([1, 2, 3]);
    expect(size.map(w => w.rank)).toEqual([...size.map(w => w.rank)].sort((a, b) => a - b));
    expect(pickWalls(walls, "NEAR").map(w => w.rank)).toEqual([1, 2, 3]);
  });
});

describe("entry against current", () => {
  it("states entry and current flow, OI and funding side by side", () => {
    render(<BtcContextStripView payload={SHORT_P} position={SHORT} mark={MID} />);
    expect(screen.getByTestId("btc-context-mode")).toHaveTextContent("진입가 기준");
    expect(screen.getByTestId("btc-context-flow-entry")).toHaveTextContent("진입 매도우세 61%");
    const current = SHORT_P.flow.sell_pct! > SHORT_P.flow.buy_pct! ? `매도우세 ${SHORT_P.flow.sell_pct}%`
      : `매수우세 ${SHORT_P.flow.buy_pct}%`;
    expect(screen.getByTestId("btc-context-flow-current")).toHaveTextContent(current);
    expect(screen.getByTestId("btc-context-oi")).toHaveTextContent("OI 7.41B → ");
    expect(screen.getByTestId("btc-context-funding")).toHaveTextContent("펀딩 +0.0010% → ");
    expect(screen.getByTestId("btc-context-entry")).toHaveTextContent("진입 83,612.9");
  });

  it("freezes the entry context for an episode whatever a later answer says", () => {
    const first = entryFor(SHORT_P, SHORT);
    const later = structuredClone(SHORT_P);
    if (later.entry?.available) later.entry.flow = { window: "60s", buy_pct: 90, sell_pct: 10 };
    expect(entryFor(later, SHORT)).toBe(first);
    expect(entryComparison(later, entryFor(later, SHORT)).flow.entry).toBe("매도우세 61%");
  });

  it("updates the current side on every answer", () => {
    const { rerender } = render(<BtcContextStripView payload={SHORT_P} position={SHORT} mark={MID} />);
    const next = structuredClone(SHORT_P);
    next.flow = { ...next.flow, live: true, buy_pct: 20, sell_pct: 80 };
    next.derivatives = { ...next.derivatives, oi_value_usdt: "7750000000", funding_pct: "0.0004" };
    rerender(<BtcContextStripView payload={next} position={SHORT} mark={MID} />);
    expect(screen.getByTestId("btc-context-flow-current")).toHaveTextContent("매도우세 80%");
    expect(screen.getByTestId("btc-context-oi")).toHaveTextContent("OI 7.41B → 7.75B");
    expect(screen.getByTestId("btc-context-funding")).toHaveTextContent("+0.0010% → +0.0004%");
    expect(screen.getByTestId("btc-context-flow-entry")).toHaveTextContent("매도우세 61%");
  });

  it("does not compare with an entry recorded for the other side", () => {
    expect(entryFor(SHORT_P, LONG)).toBeNull();
    render(<BtcContextStripView payload={SHORT_P} position={LONG} mark={MID} />);
    expect(screen.getByTestId("btc-context-flow-entry")).toHaveTextContent("진입 --");
    expect(screen.getByTestId("btc-context-entry-detail")).toHaveTextContent("진입 · 기록 없음");
  });

  it("shows ENTRY and CURRENT as two rows of the same figures, the entry row from the record only", () => {
    render(<BtcContextStripView payload={SHORT_P} position={SHORT} mark={MID} />);
    const entryRow = screen.getByTestId("btc-context-entry-detail");
    const currentRow = screen.getByTestId("btc-context-current-detail");
    expect(entryRow).toHaveTextContent("기록");
    expect(entryRow).not.toHaveTextContent("검증용");
    expect(entryRow).toHaveTextContent("체결흐름 매도우세 61%");
    expect(entryRow).toHaveTextContent("OI 7.41B");
    expect(entryRow).toHaveTextContent("매도벽 83,650.0");
    expect(entryRow).toHaveTextContent("매수벽 --");
    expect(currentRow).toHaveTextContent("현재");
    expect(currentRow).toHaveTextContent(`매도벽 ${SHORT_P.walls!.ask.slice(0, 3).map(w => Number(w.price).toLocaleString("en-US", { minimumFractionDigits: 1 })).join(" / ")}`);
  });

  it("never turns the first current reading into an entry: no record means every entry figure is --", () => {
    const noRecord = structuredClone(SHORT_P);
    noRecord.entry = { available: false };
    render(<BtcContextStripView payload={noRecord} position={SHORT} mark={MID} />);
    expect(screen.getByTestId("btc-context-flow-entry")).toHaveTextContent("진입 --");
    expect(screen.getByTestId("btc-context-flow-current")).not.toHaveTextContent("--");
    const entryRow = screen.getByTestId("btc-context-entry-detail");
    expect(entryRow).toHaveTextContent("진입 · 기록 없음");
    for (const figure of ["체결흐름 --", "OI --", "펀딩 --", "베이시스 --", "매도벽 --", "매수벽 --"]) {
      expect(entryRow).toHaveTextContent(figure);
    }
    expect(screen.getByTestId("btc-context-oi")).toHaveTextContent(/^OI -- → /);
    // a later answer with a record is still not adopted for this episode's first reading
    expect(entryFor(noRecord, SHORT)).toBeNull();
  });

  it("labels a preview store's entry as such, and a recorder's as a record", () => {
    const fixture = structuredClone(SHORT_P);
    if (fixture.entry?.available) fixture.entry = { ...fixture.entry, source: "FIXTURE", episode_id: "ep-fixture" };
    render(<BtcContextStripView payload={fixture} position={SHORT} mark={MID} />);
    expect(screen.getByTestId("btc-context-entry-detail")).toHaveTextContent("검증용 기록");
    cleanup();
    expect(SHORT_P.entry?.available && SHORT_P.entry.source).toBe("RECORDER");
    render(<BtcContextStripView payload={SHORT_P} position={SHORT} mark={MID} />);
    expect(screen.getByTestId("btc-context-entry-detail")).not.toHaveTextContent("검증용");
  });

  it("the header's 진입 대비 is the current price against the entry in the price's direction", () => {
    render(<BtcContextStripView payload={SHORT_P} position={SHORT} mark={MID} />);
    const expected = (MID - SHORT.entry) / SHORT.entry * 100;
    expect(screen.getByTestId("btc-context-current-vs-entry")).toHaveTextContent(
      `진입 대비 ${expected > 0 ? "+" : "-"}${Math.abs(expected).toFixed(2)}%`);
  });

  it("shows only the current flow without a position", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    expect(screen.getByTestId("btc-context-mode")).toHaveTextContent("현재가 기준");
    expect(screen.queryByTestId("btc-context-flow-entry")).toBeNull();
    expect(screen.queryByTestId("btc-context-entry")).toBeNull();
  });
});

describe("walls change", () => {
  it("replaces, drops and adds walls as the collector does, nothing carried over", () => {
    const { rerender } = render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const replaced = structuredClone(LIVE);
    replaced.walls!.ask[0] = { ...replaced.walls!.ask[0], price: "83761.5" };
    rerender(<BtcContextStripView payload={replaced} mark={MID} />);
    expect(prices("btc-context-path-up")[0]).toBe(83761.5);
    const gone = structuredClone(LIVE);
    gone.walls!.bid = gone.walls!.bid.slice(1).map((w, i) => ({ ...w, rank: i + 1 }));
    rerender(<BtcContextStripView payload={gone} mark={MID} />);
    expect(prices("btc-context-path-down")).toEqual([Number(LIVE.walls!.bid[1].price)]);
    rerender(<BtcContextStripView payload={withThirdBid(LIVE)} mark={MID} />);
    expect(prices("btc-context-path-down")).toHaveLength(3);
    const none = structuredClone(LIVE);
    none.walls!.bid = [];
    rerender(<BtcContextStripView payload={none} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-down-empty")).toHaveTextContent("--");
    none.walls!.empty = { ask: false, bid: true };
    rerender(<BtcContextStripView payload={structuredClone(none)} mark={MID} />);
    expect(screen.getByTestId("btc-context-path-down-empty")).toHaveTextContent("현재 적격 매수벽 없음");
  });

  it("shows age, size and the collector's recent ends in the detail", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const detail = screen.getByTestId("btc-context-wall-detail");
    expect(within(detail).getAllByTestId("wall-detail-row")[0].textContent).toMatch(/매도벽1 .* 유지 · .* · 크기 \d위/);
    if (LIVE.walls!.recent_ends.length > 0) {
      expect(screen.getByTestId("btc-context-recent-ends")).toHaveTextContent("최근 사라진 벽:");
    }
  });

  it("says nothing it cannot back: no advice word, no target name, no class name", () => {
    for (const [payload, position] of [[LIVE, null], [SHORT_P, SHORT], [LONG_P, LONG]] as const) {
      const { container } = render(<BtcContextStripView payload={payload} position={position} mark={MID} />);
      const text = container.textContent ?? "";
      for (const word of ["HOLD", "EXIT", "TAKE PROFIT", "STOP", "TP1", "TP2", "익절", "손절", "청산하",
        "보유 권장", "LONG", "SHORT", "CANCEL", "CONSUMED", "강화", "약화", "추천"]) {
        expect(text).not.toContain(word);
      }
      cleanup();
    }
  });

  it("is dashes for a stale reading or none", () => {
    render(<BtcContextStripView payload={STALE} />);
    expect(screen.getByTestId("btc-context-path-up-empty")).toBeInTheDocument();
    expect(screen.getByTestId("btc-context-current")).toHaveTextContent("현재 --");
    cleanup();
    render(<BtcContextStripView payload={null} position={SHORT} />);
    expect(screen.getByTestId("btc-context-flow-entry")).toHaveTextContent("진입 --");
  });

  it("shows no threshold figure anywhere on the panel", () => {
    for (const [payload, position] of [[LIVE, null], [SHORT_P, SHORT], [STALE, null]] as const) {
      const { container } = render(<BtcContextStripView payload={payload} position={position} mark={MID} />);
      expect(container.textContent).not.toMatch(/50만|500,000|500k|USDT 이상/);
      cleanup();
    }
  });
});

describe("resistance and support (V2 lines) in V3", () => {
  it("are hidden by default and a name-only reference annotation when asked", () => {
    const hidden = contextChartLines(LIVE, { timeframe: "1m" });
    expect(hidden.filter(l => l.kind === "RESISTANCE" || l.kind === "SUPPORT")).toHaveLength(0);
    const shown = contextChartLines(LIVE, { timeframe: "1m", structure: "ANNOTATION" });
    const levels = shown.filter(l => l.kind === "RESISTANCE" || l.kind === "SUPPORT");
    expect(levels.length).toBeGreaterThan(0);
    for (const level of levels) expect(level.tag).toMatch(/^(저항|지지)$/);
    expect(hidden.filter(l => l.kind === "ASK_WALL" || l.kind === "BID_WALL"))
      .toEqual(shown.filter(l => l.kind === "ASK_WALL" || l.kind === "BID_WALL"));
  });

  it("has a switch on the panel, 숨김 pressed by default, that drives the chart's lines", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const group = screen.getByTestId("btc-context-structure");
    expect(within(group).getByRole("button", { name: "숨김" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(group).getByRole("button", { name: "참고선" }));
    expect(contextChartLines(LIVE, { timeframe: "1m", structure: "ANNOTATION" })
      .some(l => l.kind === "RESISTANCE" || l.kind === "SUPPORT")).toBe(true);
  });

  it("lists size, persistence, NEW/CARRIED and the gap for each wall in the detail", () => {
    render(<BtcContextStripView payload={LIVE} mark={MID} />);
    const rows = within(screen.getByTestId("btc-context-wall-detail")).getAllByTestId("wall-detail-row");
    expect(rows[0].textContent).toMatch(/유지 · .*K · 크기 \d위 · (이어짐|새로 관측) · 간격 \d+\.\d+%/);
    expect(rows.map(r => r.textContent).join(" ")).not.toMatch(/강화|약화/);
  });
});

describe("chart lines by timeframe", () => {
  it.each(Object.entries(WALL_LINES_BY_TIMEFRAME))("draws %s with %i wall line(s) per side", (tf, n) => {
    const lines = contextChartLines(withThirdBid(LIVE), { timeframe: tf });
    expect(lines.filter(l => l.kind === "ASK_WALL")).toHaveLength(n);
    expect(lines.filter(l => l.kind === "BID_WALL")).toHaveLength(n);
    expect(lines.filter(l => l.kind === "RESISTANCE" || l.kind === "SUPPORT").length).toBeLessThanOrEqual(2);
  });

  it("names the walls in path order and follows the panel's selection", () => {
    const near = contextChartLines(LIVE, { timeframe: "1m" }).filter(l => l.kind === "ASK_WALL");
    expect(near.map(l => l.tag)).toEqual(["매도1", "매도2", "매도3"]);
    expect(near.map(l => l.price)).toEqual(LIVE.walls!.ask.slice(0, 3).map(w => Number(w.price)));
    const size = contextChartLines(LIVE, { timeframe: "1m", selection: "SIZE" })
      .filter(l => l.kind === "ASK_WALL").map(l => l.price);
    expect(size).toEqual(pickWalls(LIVE.walls!.ask, "SIZE").map(w => Number(w.price)));
    expect(wallLinesFor(undefined)).toBe(3);
  });

  it("gives an off-pane wall no tag and keeps a six-wall stack on the pane (zoom, pan)", () => {
    const lines = contextChartLines(withThirdBid(LIVE), { timeframe: "1m" });
    const pane = 300;
    const at = lines.map((l, i) => ({ id: l.id, kind: l.kind, tag: l.tag ?? "", y: 140 + i }));
    const tags = contextTags(at, pane);
    expect(tags.every(t => t.top >= 0 && t.top + 15 <= pane)).toBe(true);
    expect(tags.map(t => t.top)).toEqual([...tags.map(t => t.top)].sort((a, b) => a - b));
    const panned = contextTags(at.map(l => ({ ...l, y: l.kind.endsWith("WALL") ? -50 : l.y })), pane);
    expect(panned.some(t => t.kind.endsWith("WALL"))).toBe(false);
  });
});

// --------------------------------------------------------------------------- inside the terminal

type Paper = Record<string, Record<string, unknown>>;
const paper = paperTerminal as unknown as Paper;
const BASE = "/btc-context-api";

function hotState() {
  return {
    main: "BTCUSDT",
    hot: { symbol: "ETHUSDT", selected_at_ms: 1, scanned_at_ms: 1, metrics: {}, top: [] },
    lock: { state: "ALLOWED", code: "HOT_SYMBOL_FLAT", symbol: "ETHUSDT", message: "" },
    scanning: false, held: [], permitted: [], paper: { available: true, reason: null },
    server_time_ms: 0,
  };
}

/** A paper state holding a SHORT at 83,612.9 (test data on the recorded paper fixture). */
function shortState() {
  const state = structuredClone(paper.BTCUSDT.state) as Record<string, Record<string, unknown>>;
  Object.assign(state.account, { position_side: "SHORT", position_qty: "0.010",
    position_signed_qty: "-0.010", avg_entry: "83612.9", leverage: "10", entry_notional: "836.129" });
  return state;
}

function fakeBackends({ strip = () => SHORT_P as unknown, held = false } = {} as {
  strip?: () => unknown; held?: boolean;
}) {
  const tally = { all: [] as { url: string; method: string; body?: string }[], stripPolls: 0 };
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const dead = { ok: false, status: 503, json: async () => ({ error: { code: "OFFLINE" } }) };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    tally.all.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.endsWith("/snapshot")) {
      tally.stripPolls += 1;
      const body = strip();
      if (body === "down") throw new TypeError("Failed to fetch");
      return ok(body);
    }
    if (url.includes("/api/crypto/hot")) return ok(hotState());
    if (method !== "GET" && method !== "HEAD") return ok({ state: paper.BTCUSDT.state });
    const symbol = new URL(url, "http://x").searchParams.get("symbol") ?? "BTCUSDT";
    const rows = paper[symbol];
    if (!rows) return dead;
    if (url.includes("/api/crypto/chart-history")) return ok(rows.chart_history);
    if (url.includes("/api/crypto/chart")) return ok({ symbol, bars: (rows.chart_history as { bars: unknown[] }).bars });
    if (url.includes("/api/crypto/state")) return ok(held && symbol === "BTCUSDT" ? shortState() : rows.state);
    if (url.includes("/api/crypto/sizing")) return ok(rows.sizing);
    if (url.includes("/api/crypto/performance")) return ok(rows.performance);
    if (url.includes("/api/crypto/ledger")) return ok(rows.ledger);
    if (url.includes("/api/crypto/trades")) return ok(rows.trades);
    if (url.includes("/api/crypto/pnl-breakdown")) return ok(rows.pnl_breakdown);
    return dead;
  }));
  return tally;
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

const slot = (symbol: string, view?: { position: ContextPosition | null; mark: number | null }) =>
  <BtcContextStrip key={symbol} symbol={symbol} base={BASE} position={view?.position ?? null} mark={view?.mark ?? null} />;
const terminalReady = () => waitFor(
  () => expect(screen.getByTestId("market-header")).toBeInTheDocument(), { timeout: 4_000 });
const chartOverlays = () => screen.getAllByTestId("candle-chart-stub")
  .map(node => node.getAttribute("data-overlays") ?? "").join("|");
const wallLines = () => (chartOverlays().match(/ctx-(ask|bid)-wall-\d/g) ?? []).length;

describe("V3 inside the terminal", () => {
  it("switches to measuring from the entry when the account holds BTC", async () => {
    stubViewport(1440);
    fakeBackends({ held: true });
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await waitFor(() => expect(screen.getByTestId("btc-context-strip").getAttribute("data-mode")).toBe("POSITION"));
    expect(screen.getByTestId("btc-context-entry")).toHaveTextContent("진입 83,612.9");
    await waitFor(() => expect(screen.getByTestId("btc-context-flow-entry")).toHaveTextContent("매도우세 61%"));
  });

  it("measures from the price when the account is flat", async () => {
    stubViewport(1440);
    fakeBackends({ strip: () => LIVE });
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await waitFor(() => expect(rowsOf("btc-context-path-up")).toHaveLength(3));
    expect(screen.getByTestId("btc-context-strip").getAttribute("data-mode")).toBe("MARKET");
  });

  it("thins the wall lines when the timeframe changes and keeps them on 1m", async () => {
    stubViewport(1440);
    fakeBackends({ strip: () => withThirdBid(LIVE) });
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await waitFor(() => expect(wallLines()).toBe(6));
    for (const [tf, n] of [["1h", 2], ["4h", 0], ["15s", 6], ["1m", 6]] as const) {
      fireEvent.click(screen.getAllByRole("button", { name: tf })[0]);
      await waitFor(() => expect(wallLines()).toBe(n));
    }
  });

  it("moves the chart lines with the panel's selection", async () => {
    stubViewport(1440);
    fakeBackends({ strip: () => LIVE });
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await waitFor(() => expect(wallLines()).toBeGreaterThan(0));
    fireEvent.click(within(screen.getByTestId("btc-context-selection")).getByText("큰 순"));
    const size = pickWalls(LIVE.walls!.ask, "SIZE").map(w => w.price);
    await waitFor(() => expect(chartOverlays()).toContain(`ctx-ask-wall-1@${Number(size[0])}`));
    act(() => setWallSelection("NEAR"));
  });

  it("draws nothing and asks nothing on the HOT tab", async () => {
    stubViewport(1440);
    const tally = fakeBackends({ strip: () => LIVE });
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await waitFor(() => expect(wallLines()).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId("symbol-tab-hot")).toBeEnabled());
    fireEvent.click(screen.getByTestId("symbol-tab-hot"));
    await waitFor(() => expect(chartOverlays()).not.toContain("ctx-"));
    expect(screen.queryByTestId("btc-context-strip")).toBeNull();
    const polls = tally.stripPolls;
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(tally.stripPolls).toBe(polls);
  });

  it("shows dashes and no lines while the API is down, and recovers when it is back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubViewport(1440);
    let up = false;
    fakeBackends({ strip: () => (up ? LIVE : "down"), held: false });
    render(<CryptoTerminal marketContext={slot} contextBase={BASE} />);
    await terminalReady();
    await waitFor(() => expect(screen.getByTestId("btc-context-path-up-empty")).toBeInTheDocument());
    expect(chartOverlays()).not.toContain("ctx-");
    expect(within(screen.getAllByTestId("order-ticket")[0]).getAllByTestId("long-button")[0]).toBeEnabled();
    up = true;
    await vi.advanceTimersByTimeAsync(6_000);
    await waitFor(() => expect(rowsOf("btc-context-path-up")).toHaveLength(3));
    await waitFor(() => expect(wallLines()).toBeGreaterThan(0));
  });

  it("issues no non-GET and sends the same order with and without the V3 context", async () => {
    const pressLong = async (withContext: boolean) => {
      stubViewport(1440);
      const tally = fakeBackends({ strip: () => SHORT_P });
      render(withContext ? <CryptoTerminal marketContext={slot} contextBase={BASE} /> : <CryptoTerminal />);
      await terminalReady();
      if (withContext) {
        await waitFor(() => expect(rowsOf("btc-context-path-up").length).toBeGreaterThan(0));
        fireEvent.click(within(screen.getByTestId("btc-context-selection")).getByText("큰 순"));
        expect(tally.all.filter(r => r.method !== "GET" && r.method !== "HEAD")).toEqual([]);
      }
      fireEvent.click(screen.getAllByTestId("long-button")[0]);
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
  });
});
