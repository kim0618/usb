/** Multi-entry chart labels V1: one numbered marker per real fill, the account's own average.
 *
 *  The markers are the fills the backend classified (`/executions`), labelled `#n SIDE qty @
 *  price` with `n` the entry's number within its position. The average entry line is the
 *  account's figure - the PAPER engine's `avg_entry`, Binance's `entryPrice` on LIVE - and is
 *  never recomputed from the marker prices.
 */
import { describe, expect, it } from "vitest";

import { executionLabel, executionMarkers, executionsFor } from "@/lib/crypto-executions";
import type { Execution } from "@/lib/crypto-executions";
import { positionOverlays } from "@/lib/crypto-paper";
import type { CryptoState } from "@/lib/crypto-paper";
import { liveOverlays } from "@/lib/crypto-live";
import type { LiveAccount } from "@/lib/crypto-live";

const MIN = 60_000;
const T0 = 1_792_000_000_000;
/** One-minute candles covering the fills below, in seconds. */
const candles = Array.from({ length: 30 }, (_, i) => T0 / 1000 + i * 60);

let nextSeq = 0;
const fill = (over: Partial<Execution>): Execution => ({
  seq: ++nextSeq, ts_ms: T0 + nextSeq * MIN, mode: "PAPER", symbol: "AGTUSDT", account: "run-1",
  side: "LONG", event_type: "ADD_ENTRY", price: "0.03061", qty: "36014", leverage: "20",
  entry_no: null, ...over,
});

function longSeries() {
  nextSeq = 0;
  return [
    fill({ event_type: "INITIAL_ENTRY", entry_no: 1, qty: "36014", price: "0.03061" }),
    fill({ entry_no: 2, qty: "20000", price: "0.03080" }),
    fill({ entry_no: 3, qty: "15000", price: "0.03040" }),
  ];
}

const texts = (rows: Execution[]) => executionMarkers(rows, candles).map(m => m.text);

describe("numbered entry markers", () => {
  it("1. one LONG entry is #1", () => {
    expect(texts(longSeries().slice(0, 1))).toEqual(["#1 LONG 36,014 @ 0.03061"]);
  });

  it("2. an add keeps #1 and adds #2", () => {
    expect(texts(longSeries().slice(0, 2))).toEqual(["#1 LONG 36,014 @ 0.03061", "#2 LONG 20,000 @ 0.0308"]);
  });

  it("3. a third entry gives #1, #2, #3", () => {
    expect(texts(longSeries())).toEqual([
      "#1 LONG 36,014 @ 0.03061", "#2 LONG 20,000 @ 0.0308", "#3 LONG 15,000 @ 0.0304"]);
  });

  it("4. three entries inside one minute stay three markers on one bar, in fill order", () => {
    const rows = longSeries().map((row, i) => ({ ...row, ts_ms: T0 + 5 * MIN + i * 7_000 }));
    const markers = executionMarkers(rows, candles);
    expect(markers).toHaveLength(3);
    expect(new Set(markers.map(m => m.time)).size).toBe(1);
    expect(new Set(markers.map(m => m.id)).size).toBe(3);
    expect(markers.map(m => m.text.slice(0, 2))).toEqual(["#1", "#2", "#3"]);
  });

  it("10. SHORT is numbered the same way and drawn above the bar", () => {
    const rows = longSeries().map(row => ({ ...row, side: "SHORT" as const }));
    const markers = executionMarkers(rows, candles);
    expect(markers.map(m => m.text)).toEqual([
      "#1 SHORT 36,014 @ 0.03061", "#2 SHORT 20,000 @ 0.0308", "#3 SHORT 15,000 @ 0.0304"]);
    expect(markers.every(m => m.position === "aboveBar" && m.shape === "arrowDown")).toBe(true);
  });

  it("a re-entry after a full close starts again at #1; a flip's opening leg is #1", () => {
    nextSeq = 0;
    const rows = [
      fill({ event_type: "INITIAL_ENTRY", entry_no: 1, qty: "100" }),
      fill({ entry_no: 2, qty: "50" }),
      fill({ event_type: "FULL_CLOSE", qty: "150", price: "0.031" }),
      fill({ event_type: "INITIAL_ENTRY", entry_no: 1, qty: "70" }),
      fill({ event_type: "FULL_CLOSE", qty: "70", price: "0.0305" }),
      fill({ event_type: "POSITION_FLIP", side: "SHORT", entry_no: 1, qty: "30" }),
    ];
    expect(texts(rows)).toEqual([
      "#1 LONG 100 @ 0.03061", "#2 LONG 50 @ 0.03061", "LONG 청산 150 @ 0.031",
      "#1 LONG 70 @ 0.03061", "LONG 청산 70 @ 0.0305", "#1 SHORT 30 @ 0.03061"]);
  });

  it("an add with no number says 추가 instead of inventing one; an older server falls back to seq", () => {
    expect(executionLabel(fill({ entry_no: null, qty: "5", price: "1.50" }))).toBe("LONG 추가 5 @ 1.5");
    const legacy = fill({ qty: "5", price: "1.5" });
    delete legacy.entry_no;
    expect(executionLabel(legacy)).toBe(`#${legacy.seq} LONG 5 @ 1.5`);
  });
});

describe("closes", () => {
  it("6. HALF CLOSE keeps every entry marker and adds a partial-close marker", () => {
    const rows = [...longSeries(), fill({ event_type: "PARTIAL_CLOSE", qty: "35507", price: "0.03120" })];
    const markers = executionMarkers(rows, candles);
    expect(markers.map(m => m.text)).toEqual([
      "#1 LONG 36,014 @ 0.03061", "#2 LONG 20,000 @ 0.0308", "#3 LONG 15,000 @ 0.0304",
      "LONG 부분청산 35,507 @ 0.0312"]);
    expect(markers[3].shape).toBe("circle");
  });

  it("FULL CLOSE adds a close marker and keeps the entries", () => {
    const rows = [...longSeries(), fill({ event_type: "FULL_CLOSE", qty: "71014", price: "0.031" })];
    expect(texts(rows)).toHaveLength(4);
    expect(texts(rows)[3]).toBe("LONG 청산 71,014 @ 0.031");
  });
});

describe("the chart's set of markers", () => {
  it("7. a reload or refetch delivering the same fills again draws each once", () => {
    const rows = longSeries();
    const kept = executionsFor([...rows, ...rows, rows[1]], { mode: "PAPER", symbol: "AGTUSDT", account: "run-1" });
    expect(kept.map(r => r.seq)).toEqual([1, 2, 3]);
    expect(executionMarkers(kept, candles)).toHaveLength(3);
  });

  it("8. PAPER and LIVE fills never share a chart, and the same seq on both is two fills", () => {
    const paper = longSeries();
    const live = longSeries().map(row => ({ ...row, mode: "LIVE" as const, account: "acct-fp" }));
    const all = [...paper, ...live];
    const onPaper = executionsFor(all, { mode: "PAPER", symbol: "AGTUSDT", account: "run-1" });
    const onLive = executionsFor(all, { mode: "LIVE", symbol: "AGTUSDT", account: "acct-fp" });
    expect(onPaper.every(r => r.mode === "PAPER") && onPaper.length === 3).toBe(true);
    expect(onLive.every(r => r.mode === "LIVE") && onLive.length === 3).toBe(true);
    const ids = [...executionMarkers(onPaper, candles), ...executionMarkers(onLive, candles)].map(m => m.id);
    expect(new Set(ids).size).toBe(6);
  });

  it("9. symbol A's fills never reach symbol B's chart", () => {
    const a = longSeries();
    const b = longSeries().map(row => ({ ...row, symbol: "BTCUSDT", seq: row.seq + 10 }));
    expect(executionsFor([...a, ...b], { mode: "PAPER", symbol: "BTCUSDT", account: "run-1" })
      .every(r => r.symbol === "BTCUSDT")).toBe(true);
    expect(executionsFor([...a, ...b], { mode: "PAPER", symbol: "AGTUSDT", account: "run-1" })
      .map(r => r.seq)).toEqual([1, 2, 3]);
  });
});

// ------------------------------------------------------------------ the average entry line

const paperState = (avg: string | null, side: "LONG" | "SHORT" | null, qty = "0") => ({
  quote: { mark_price: "0.0306" },
  account: { position_side: side, avg_entry: avg, position_qty: qty, liquidation_price: null },
}) as unknown as Pick<CryptoState, "account" | "quote">;

const liveAccount = (entry: string | null, flat: boolean) => ({
  mark: { mark_price: "0.0306" },
  position: { symbol: "AGTUSDT", side: flat ? null : "LONG", qty: flat ? "0" : "71014",
              is_flat: flat, entry_price: entry, mark_price: "0.0306", liquidation_price: null },
}) as unknown as LiveAccount;

const entryLine = (overlays: { id: string; price: number }[]) => overlays.find(o => o.id === "entry")?.price ?? null;

describe("5. the average entry line is the account's own figure", () => {
  it("PAPER moves to the engine's avg_entry after each add, whatever the markers say", () => {
    // The engine's averages include fees and slippage the marker prices do not, so they differ
    // from a plain mean of the markers - which is exactly why the markers must not be used.
    const engine = ["0.03061", "0.0306781", "0.0306102"];
    const rows = longSeries();
    for (const [index, avg] of engine.entries()) {
      const shown = entryLine(positionOverlays(paperState(avg, "LONG")));
      expect(shown).toBe(Number(avg));
      const markers = rows.slice(0, index + 1);
      const naive = markers.reduce((s, r) => s + Number(r.qty) * Number(r.price), 0)
        / markers.reduce((s, r) => s + Number(r.qty), 0);
      if (index > 0) expect(shown).not.toBeCloseTo(naive, 9);
    }
  });

  it("PAPER keeps the average through a partial close and drops the line when flat", () => {
    expect(entryLine(positionOverlays(paperState("0.0306102", "LONG", "35507")))).toBe(0.0306102);
    expect(entryLine(positionOverlays(paperState(null, null)))).toBeNull();
  });

  it("LIVE draws Binance's entryPrice, and nothing once Binance reports flat", () => {
    expect(entryLine(liveOverlays(liveAccount("0.03061", false)))).toBe(0.03061);
    expect(entryLine(liveOverlays(liveAccount("0.0306455", false)))).toBe(0.0306455);
    expect(entryLine(liveOverlays(liveAccount("0.0306455", true)))).toBeNull();
  });
});
