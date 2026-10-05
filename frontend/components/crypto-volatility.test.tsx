import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { SymbolTabs, tabTitle, useSelectedSymbol, useSymbolVolatility }
  from "@/components/crypto-symbol-tabs";
import {
  VOLATILITY_BUCKET_MS, VOLATILITY_POLL_MS, VOLATILITY_REQUEST_BARS, VOLATILITY_STALE_MS,
  VOLATILITY_WINDOW_BARS, hotSymbols, rangeLabel, symbolRange1h, withFreshness,
} from "@/lib/crypto-volatility";
import type { SymbolVolatility } from "@/lib/crypto-volatility";
import { cryptoApi } from "@/lib/crypto-paper";
import type { ChartBar } from "@/lib/crypto-paper";
import { CRYPTO_SYMBOLS, readStoredSymbol } from "@/lib/crypto-symbols";
import fixture from "@/components/__fixtures__/crypto-1h-range.json";

/** The 1H volatility badge on the tab strip.
 *
 *  Two things are under test and they fail differently. The arithmetic fails by producing a
 *  plausible number from the wrong minutes - a window stretched across a provider gap, an
 *  in-progress bar counted, an hourly return printed where a range was promised - and none of
 *  those look wrong on screen, so they are pinned against a reading recomputed outside this
 *  codebase. The screen fails by *acting*: a badge that moved the selection, the order target or
 *  the chart would be a trading change dressed as a label, so the selection is asserted to be
 *  deaf to HOT in every form the strip can be driven.
 */

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
beforeEach(() => { try { window.localStorage.clear(); } catch { /* private window */ } });

// --------------------------------------------------------------------------- synthetic bars

/** `count` confirmed one-minute bars ending at the last minute that has closed.
 *
 *  Dated off the clock rather than off a constant, because the hook puts every reading through
 *  `withFreshness`: a window from a fixed 2023 timestamp is correctly refused as stale, and a
 *  test built on one would be asserting against `--` without saying so. Under fake timers this
 *  follows the faked clock, which is what lets the staleness test below set the time first. */
function bars(count: number, over: {
  endMs?: number; open?: number; high?: number; low?: number; close?: number;
} = {}): ChartBar[] {
  const endMs = over.endMs
    ?? (Math.floor(Date.now() / VOLATILITY_BUCKET_MS) - 1) * VOLATILITY_BUCKET_MS;
  const rows: ChartBar[] = [];
  for (let index = count - 1; index >= 0; index -= 1) {
    rows.push({
      start_ms: endMs - index * VOLATILITY_BUCKET_MS,
      open: String(over.open ?? 100), high: String(over.high ?? 100),
      low: String(over.low ?? 100), close: String(over.close ?? 100),
      volume: "1", confirmed: true,
    });
  }
  return rows;
}

/** A complete 60-bar window whose range is exactly `rangePct`, around a reference of 100.
 *
 *  The range is centred on the reference, so it is the same figure whichever way the hour
 *  closed - the point of the feature is that direction and size are separate, and a helper whose
 *  range changed with its direction would hide exactly that. */
function windowOf(rangePct: number, direction: "UP" | "DOWN" | "FLAT" = "FLAT"): ChartBar[] {
  const reference = 100;
  const half = (rangePct / 100) * reference / 2;
  const rows = bars(VOLATILITY_WINDOW_BARS, { open: reference, high: reference + half,
                                              low: reference - half, close: reference });
  rows[rows.length - 1].close = String(direction === "UP" ? reference + half / 2
    : direction === "DOWN" ? reference - half / 2 : reference);
  return rows;
}

// --------------------------------------------------------------------------- the definition

describe("1H range, against an independently computed reading", () => {
  /** The fixture is a real Bybit capture plus the figures `scripts/check_1h_range.py` derived
   *  from it in Decimal arithmetic, sharing no code with the module under test. */
  const captured = fixture as unknown as {
    captured_at_ms: number;
    expected_hot: string[];
    symbols: Record<string, { bars: ChartBar[]; expected: Record<string, string | number> }>;
  };

  it("covers all three instruments", () => {
    expect(Object.keys(captured.symbols).sort()).toEqual([...CRYPTO_SYMBOLS].sort());
  });

  for (const symbol of Object.keys(captured.symbols)) {
    it(`reproduces ${symbol} to the reference's precision`, () => {
      const { bars: rows, expected } = captured.symbols[symbol];
      const row = symbolRange1h(symbol, rows);
      expect(row.state).toBe("COMPLETE");
      if (row.state !== "COMPLETE") return;
      expect(row.windowStartMs).toBe(expected.window_start_ms);
      expect(row.windowEndMs).toBe(expected.window_end_ms);
      expect(row.referencePrice).toBe(Number(expected.reference));
      expect(row.high).toBe(Number(expected.high));
      expect(row.low).toBe(Number(expected.low));
      expect(row.close).toBe(Number(expected.close));
      expect(row.direction).toBe(expected.direction);
      // The reference is exact; a double agrees with it to well past the two decimals shown.
      expect(row.rangePct).toBeCloseTo(Number(expected.range_pct), 10);
      expect(rangeLabel(row)).toBe(`${expected.range_pct_2dp}%`);
    });
  }

  it("covers exactly 60 one-minute buckets", () => {
    const { bars: rows, expected } = captured.symbols.BTCUSDT;
    expect(Number(expected.window_end_ms) - Number(expected.window_start_ms))
      .toBe((VOLATILITY_WINDOW_BARS - 1) * VOLATILITY_BUCKET_MS);
    // And the window ends on the newest *confirmed* bar, not the newest bar in the response.
    const newest = Math.max(...rows.map(row => row.start_ms));
    const newestConfirmed = Math.max(...rows.filter(row => row.confirmed).map(row => row.start_ms));
    expect(newest).toBeGreaterThan(newestConfirmed);
    expect(expected.window_end_ms).toBe(newestConfirmed);
  });

  it("names the same HOT symbol as the reference", () => {
    const rows = Object.entries(captured.symbols).map(([symbol, entry]) =>
      symbolRange1h(symbol, entry.bars));
    expect([...hotSymbols(rows)].sort()).toEqual([...captured.expected_hot].sort());
  });
});

describe("range, not return", () => {
  it("reports the travel of an hour that ended where it started", () => {
    // Open 100, close 100, but it touched 102 and 98. An hourly return would call this 0.00%.
    const rows = bars(VOLATILITY_WINDOW_BARS, { open: 100, high: 102, low: 98, close: 100 });
    const row = symbolRange1h("BTCUSDT", rows);
    expect(row.state).toBe("COMPLETE");
    if (row.state !== "COMPLETE") return;
    expect(row.rangePct).toBeCloseTo(4, 10);
    expect(row.direction).toBe("FLAT");
  });

  it("divides by the window's first open, not by the last close", () => {
    const rows = bars(VOLATILITY_WINDOW_BARS, { open: 200, high: 210, low: 200, close: 210 });
    const row = symbolRange1h("BTCUSDT", rows);
    if (row.state !== "COMPLETE") throw new Error("expected COMPLETE");
    expect(row.referencePrice).toBe(200);
    expect(row.rangePct).toBeCloseTo(5, 10);      // 10/200, not 10/210
  });

  it("signs the direction off the last confirmed close against that same open", () => {
    const up = symbolRange1h("BTCUSDT", windowOf(1, "UP"));
    const down = symbolRange1h("BTCUSDT", windowOf(1, "DOWN"));
    const flat = symbolRange1h("BTCUSDT", windowOf(1, "FLAT"));
    expect([up, down, flat].map(row => row.state === "COMPLETE" && row.direction))
      .toEqual(["UP", "DOWN", "FLAT"]);
  });

  it("calls a one-tick move a move, with no flat band invented around zero", () => {
    const rows = bars(VOLATILITY_WINDOW_BARS, { open: 100, high: 101, low: 100, close: 100 });
    rows[rows.length - 1].close = "100.00000001";
    const row = symbolRange1h("BTCUSDT", rows);
    expect(row.state === "COMPLETE" && row.direction).toBe("UP");
  });
});

// --------------------------------------------------------------------------- missing data

describe("data that cannot answer the question", () => {
  it("refuses a window of fewer than 60 confirmed bars", () => {
    for (const count of [0, 1, 59]) {
      const row = symbolRange1h("BTCUSDT", bars(count));
      expect(row.state).toBe("UNKNOWN");
      if (row.state !== "UNKNOWN") continue;
      expect(row.reason).toBe(count === 0 ? "NO_CONFIRMED_BAR" : "INCOMPLETE_WINDOW");
      expect(row.confirmedBars).toBe(count);
    }
    expect(symbolRange1h("BTCUSDT", bars(60)).state).toBe("COMPLETE");
  });

  it("will not stretch the window over a gap to find 60 rows", () => {
    // 61 bars with the 30th missing: 60 rows are present, but they span 61 minutes. Counting
    // rows would call that an hour and print a plausible, wrong figure.
    const rows = bars(61);
    rows.splice(30, 1);
    expect(rows.filter(row => row.confirmed).length).toBe(VOLATILITY_WINDOW_BARS);
    const row = symbolRange1h("BTCUSDT", rows);
    expect(row.state).toBe("UNKNOWN");
    expect(row.state === "UNKNOWN" && row.reason).toBe("INCOMPLETE_WINDOW");
  });

  it("ignores the minute in progress, including its high and low", () => {
    const rows = bars(VOLATILITY_WINDOW_BARS, { open: 100, high: 101, low: 100, close: 100 });
    const endMs = rows[rows.length - 1].start_ms + VOLATILITY_BUCKET_MS;
    rows.push({ start_ms: endMs, open: "100", high: "500", low: "1", close: "400",
                volume: "1", confirmed: false });
    const row = symbolRange1h("BTCUSDT", rows);
    if (row.state !== "COMPLETE") throw new Error("expected COMPLETE");
    expect(row.high).toBe(101);
    expect(row.low).toBe(100);
    expect(row.windowEndMs).toBe(endMs - VOLATILITY_BUCKET_MS);
  });

  it("refuses a reference price it cannot divide by", () => {
    for (const open of ["0", "-1", "", "abc"]) {
      const rows = bars(VOLATILITY_WINDOW_BARS);
      rows[0].open = open;
      const row = symbolRange1h("BTCUSDT", rows);
      expect(row.state).toBe("UNKNOWN");
      if (row.state !== "UNKNOWN") continue;
      // A non-numeric open drops the whole bar, which leaves the window short; a zero or
      // negative one keeps it and fails on the division. Either way: no figure.
      expect(["NO_REFERENCE_PRICE", "INCOMPLETE_WINDOW"]).toContain(row.reason);
    }
  });

  it("deduplicates a repeated bucket and drops one that is not on a minute boundary", () => {
    const rows = bars(VOLATILITY_WINDOW_BARS);
    rows.push({ ...rows[rows.length - 1], high: "999" });                  // same bucket again
    rows.push({ ...rows[rows.length - 1], start_ms: rows[0].start_ms + 30_000 });  // half-minute
    const row = symbolRange1h("BTCUSDT", rows);
    if (row.state !== "COMPLETE") throw new Error("expected COMPLETE");
    expect(row.high).toBe(999);      // the later row for the same bucket wins
    expect(row.windowEndMs % VOLATILITY_BUCKET_MS).toBe(0);
  });

  it("prints -- for every state that is not COMPLETE", () => {
    expect(rangeLabel(null)).toBe("--");
    expect(rangeLabel(undefined)).toBe("--");
    expect(rangeLabel(symbolRange1h("BTCUSDT", bars(10)))).toBe("--");
  });
});

describe("freshness", () => {
  const complete = () => {
    const row = symbolRange1h("BTCUSDT", bars(VOLATILITY_WINDOW_BARS, { high: 101 }));
    if (row.state !== "COMPLETE") throw new Error("expected COMPLETE");
    return row;
  };

  it("keeps a reading whose window closed within the bound", () => {
    const row = complete();
    const closedAt = row.windowEndMs + VOLATILITY_BUCKET_MS;
    expect(withFreshness(row, closedAt).state).toBe("COMPLETE");
    expect(withFreshness(row, closedAt + VOLATILITY_STALE_MS).state).toBe("COMPLETE");
  });

  it("stops presenting one that fell behind it", () => {
    const row = complete();
    const stale = withFreshness(row, row.windowEndMs + VOLATILITY_BUCKET_MS + VOLATILITY_STALE_MS + 1);
    expect(stale.state).toBe("UNKNOWN");
    expect(stale.state === "UNKNOWN" && stale.reason).toBe("STALE");
    expect(rangeLabel(stale)).toBe("--");
  });

  it("leaves an already-UNKNOWN reading alone", () => {
    const row = symbolRange1h("BTCUSDT", bars(3));
    expect(withFreshness(row, Date.now())).toBe(row);
  });
});

// --------------------------------------------------------------------------- HOT

describe("HOT", () => {
  const set = (...pcts: [string, number][]) =>
    pcts.map(([symbol, pct]) => symbolRange1h(symbol, windowOf(pct)));

  it("badges BTC when BTC > ETH > SOL", () => {
    expect([...hotSymbols(set(["BTCUSDT", 1.83], ["ETHUSDT", 1.14], ["SOLUSDT", 0.72]))])
      .toEqual(["BTCUSDT"]);
  });

  it("badges SOL when SOL > ETH > BTC", () => {
    expect([...hotSymbols(set(["BTCUSDT", 0.72], ["ETHUSDT", 1.14], ["SOLUSDT", 1.83]))])
      .toEqual(["SOLUSDT"]);
  });

  it("badges both on a tie rather than picking by symbol order", () => {
    const tied = hotSymbols(set(["BTCUSDT", 1.14], ["ETHUSDT", 1.14], ["SOLUSDT", 0.72]));
    expect([...tied].sort()).toEqual(["BTCUSDT", "ETHUSDT"]);
    // And the reverse order gives the same answer, which is what "no fixed priority" means.
    const reversed = hotSymbols(set(["SOLUSDT", 0.72], ["ETHUSDT", 1.14], ["BTCUSDT", 1.14]));
    expect([...reversed].sort()).toEqual(["BTCUSDT", "ETHUSDT"]);
  });

  it("ties at the precision it shows, so two tabs reading the same figure agree", () => {
    // 0.7249% against 0.7241%: not equal, but both print 0.72%. Deciding on the raw double
    // would put HOT on one of two tabs showing the identical number, with the reason - the
    // fourth decimal - nowhere on screen.
    const shared = [
      symbolRange1h("BTCUSDT", bars(60, { open: 100, high: 100.7249, low: 100 })),
      symbolRange1h("ETHUSDT", bars(60, { open: 100, high: 100.7241, low: 100 })),
    ];
    expect(shared.map(rangeLabel)).toEqual(["0.72%", "0.72%"]);
    expect([...hotSymbols(shared)].sort()).toEqual(["BTCUSDT", "ETHUSDT"]);
    // A difference the screen does show is still a difference.
    const apart = [
      symbolRange1h("BTCUSDT", bars(60, { open: 100, high: 100.7251, low: 100 })),
      symbolRange1h("ETHUSDT", bars(60, { open: 100, high: 100.7249, low: 100 })),
    ];
    expect(apart.map(rangeLabel)).toEqual(["0.73%", "0.72%"]);
    expect([...hotSymbols(apart)]).toEqual(["BTCUSDT"]);
  });

  it("always contains the true maximum, because rounding cannot reorder", () => {
    const rows = [
      symbolRange1h("BTCUSDT", bars(60, { open: 100, high: 100.7250001, low: 100 })),
      symbolRange1h("ETHUSDT", bars(60, { open: 100, high: 100.725, low: 100 })),
    ];
    const widest = rows.reduce((best, row) =>
      row.state === "COMPLETE" && best.state === "COMPLETE" && row.rangePct > best.rangePct
        ? row : best);
    expect(hotSymbols(rows).has(widest.symbol)).toBe(true);
  });

  it("leaves an UNKNOWN symbol out of the ranking entirely", () => {
    const rows = [
      symbolRange1h("BTCUSDT", windowOf(0.4)),
      symbolRange1h("ETHUSDT", bars(10)),              // UNKNOWN
      symbolRange1h("SOLUSDT", windowOf(0.9)),
    ];
    expect([...hotSymbols(rows)]).toEqual(["SOLUSDT"]);
    // Not ranked at zero either: an UNKNOWN symbol never wins and never loses a comparison.
    expect(hotSymbols([symbolRange1h("ETHUSDT", bars(10))]).size).toBe(0);
  });

  it("badges the only measurable symbol when two are UNKNOWN", () => {
    const rows = [
      symbolRange1h("BTCUSDT", bars(0)),
      symbolRange1h("ETHUSDT", bars(12)),
      symbolRange1h("SOLUSDT", windowOf(0.31)),
    ];
    expect([...hotSymbols(rows)]).toEqual(["SOLUSDT"]);
  });

  it("badges nothing when no symbol has a figure", () => {
    expect(hotSymbols([]).size).toBe(0);
    expect(hotSymbols([symbolRange1h("BTCUSDT", bars(2)),
                       symbolRange1h("ETHUSDT", bars(0))]).size).toBe(0);
  });

  it("ignores a zero range only insofar as it is the smallest, not as missing", () => {
    const rows = [symbolRange1h("BTCUSDT", windowOf(0)), symbolRange1h("ETHUSDT", windowOf(0))];
    expect(rows.map(rangeLabel)).toEqual(["0.00%", "0.00%"]);
    expect([...hotSymbols(rows)].sort()).toEqual(["BTCUSDT", "ETHUSDT"]);
  });
});

// --------------------------------------------------------------------------- the strip

const rows = (spec: Record<string, number | null>) => {
  const out: Record<string, SymbolVolatility> = {};
  for (const [symbol, pct] of Object.entries(spec)) {
    out[symbol] = pct == null ? symbolRange1h(symbol, bars(5))
      : symbolRange1h(symbol, windowOf(pct, "UP"));
  }
  return out;
};

describe("the tab strip with a reading", () => {
  const WIDEST = { BTCUSDT: 0.72, ETHUSDT: 1.14, SOLUSDT: 1.83 };

  it("prints each symbol's figure and its direction", () => {
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()} volatility={rows(WIDEST)} />);
    expect(screen.getByTestId("symbol-range-BTCUSDT").textContent).toContain("0.72%");
    expect(screen.getByTestId("symbol-range-ETHUSDT").textContent).toContain("1.14%");
    expect(screen.getByTestId("symbol-range-SOLUSDT").textContent).toContain("1.83%");
    expect(screen.getByTestId("symbol-range-BTCUSDT").textContent).toContain("↑");
  });

  it("says HOT in words on the widest tab, and only there", () => {
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()} volatility={rows(WIDEST)} />);
    expect(screen.getByTestId("symbol-hot-SOLUSDT")).toHaveAttribute("data-hot", "true");
    expect(screen.getByTestId("symbol-hot-SOLUSDT").textContent).toBe("HOT");
    expect(screen.getByTestId("symbol-tab-SOLUSDT").textContent).toContain("HOT");
    for (const symbol of ["BTCUSDT", "ETHUSDT"]) {
      expect(screen.getByTestId(`symbol-hot-${symbol}`)).toHaveAttribute("data-hot", "false");
      expect(screen.getByTestId(`symbol-hot-${symbol}`)).toHaveClass("invisible");
    }
  });

  it("reserves the badge's width on every tab so it cannot push the layout", () => {
    const { rerender } = render(
      <SymbolTabs value="BTCUSDT" onChange={vi.fn()} volatility={rows(WIDEST)} />);
    const slots = () => CRYPTO_SYMBOLS.map(symbol =>
      screen.getByTestId(`symbol-hot-${symbol}`).textContent);
    expect(slots()).toEqual(["HOT", "HOT", "HOT"]);   // present on all three, shown on one
    rerender(<SymbolTabs value="BTCUSDT" onChange={vi.fn()}
      volatility={rows({ BTCUSDT: 2.5, ETHUSDT: 1.14, SOLUSDT: 0.3 })} />);
    expect(slots()).toEqual(["HOT", "HOT", "HOT"]);
    expect(screen.getByTestId("symbol-hot-BTCUSDT")).toHaveAttribute("data-hot", "true");
  });

  it("hides the HOT slot from a screen reader unless it is the real badge", () => {
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()} volatility={rows(WIDEST)} />);
    expect(screen.getByTestId("symbol-hot-BTCUSDT")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId("symbol-hot-SOLUSDT")).not.toHaveAttribute("aria-hidden");
  });

  it("gives the direction a word, not only an arrow", () => {
    const { container } = render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()}
      volatility={rows({ BTCUSDT: 1, ETHUSDT: 1, SOLUSDT: 1 })} />);
    expect(container.querySelectorAll(".sr-only").length).toBe(3);
    expect(screen.getByTestId("symbol-range-BTCUSDT").textContent).toContain("상승");
  });

  it("prints -- and says why for a symbol with no figure", () => {
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()}
      volatility={rows({ BTCUSDT: 0.5, ETHUSDT: null, SOLUSDT: null })} />);
    expect(screen.getByTestId("symbol-range-ETHUSDT").textContent).toContain("--");
    expect(screen.getByTestId("symbol-range-SOLUSDT").textContent).toContain("--");
    // The only measurable symbol still carries HOT: UNKNOWN is not a competitor.
    expect(screen.getByTestId("symbol-hot-BTCUSDT")).toHaveAttribute("data-hot", "true");
    expect(screen.getByTestId("symbol-tab-ETHUSDT").getAttribute("title"))
      .toContain("빠진 1분봉");
  });

  it("prints -- before the first read has answered, without moving the row", () => {
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()} volatility={{}} />);
    for (const symbol of CRYPTO_SYMBOLS) {
      expect(screen.getByTestId(`symbol-range-${symbol}`).textContent).toContain("--");
      expect(screen.getByTestId(`symbol-hot-${symbol}`)).toHaveAttribute("data-hot", "false");
      expect(screen.getByTestId(`symbol-tab-${symbol}`).getAttribute("title")).toContain("읽는 중");
    }
  });

  it("ranks only the symbols it renders", () => {
    // SOL is the widest, but this build does not permit it. The badge goes to the widest tab on
    // screen rather than disappearing onto an instrument nobody can select.
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()} permitted={["BTCUSDT", "ETHUSDT"]}
      volatility={rows(WIDEST)} />);
    expect(screen.queryByTestId("symbol-tab-SOLUSDT")).toBeNull();
    expect(screen.getByTestId("symbol-hot-ETHUSDT")).toHaveAttribute("data-hot", "true");
  });

  it("states the window and the ranking in the tooltip", () => {
    const row = symbolRange1h("SOLUSDT", windowOf(1.83, "DOWN"));
    expect(tabTitle("SOLUSDT", row, true)).toContain("최근 60분 변동폭 1.83% 하락");
    expect(tabTitle("SOLUSDT", row, true)).toContain("3종목 중 최대");
    expect(tabTitle("SOLUSDT", row, false)).not.toContain("최대");
    expect(tabTitle("SOLUSDT", null, false)).toContain("읽는 중");
  });
});

describe("the tab strip without a reading", () => {
  it("renders exactly as it did before when no volatility is passed", () => {
    render(<SymbolTabs value="ETHUSDT" onChange={vi.fn()} />);
    expect(screen.getByTestId("symbol-tab-BTCUSDT").textContent).toBe("BTC");
    expect(screen.getByTestId("symbol-tab-ETHUSDT")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("symbol-range-BTCUSDT")).toBeNull();
    expect(screen.queryByTestId("symbol-hot-BTCUSDT")).toBeNull();
    expect(screen.getByTestId("symbol-tab-BTCUSDT")).not.toHaveAttribute("title");
  });

  it("keeps the pressed state and the busy lock the strip already had", () => {
    const onChange = vi.fn();
    render(<SymbolTabs value="BTCUSDT" onChange={onChange} busy volatility={rows(
      { BTCUSDT: 0.1, ETHUSDT: 0.2, SOLUSDT: 0.3 })} />);
    const selected = screen.getByTestId("symbol-tab-BTCUSDT");
    const other = screen.getByTestId("symbol-tab-ETHUSDT");
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(selected.className).toContain("border-primary");
    expect(other.className).toContain("border-line");
    expect(other).toBeDisabled();
    fireEvent.click(other);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("switches on a press, which is still the only thing that switches", () => {
    const onChange = vi.fn();
    render(<SymbolTabs value="BTCUSDT" onChange={onChange} volatility={rows(
      { BTCUSDT: 0.1, ETHUSDT: 0.2, SOLUSDT: 3.0 })} />);
    // The HOT tab is SOL and the selected one is BTC. Pressing the selected tab is not a switch.
    fireEvent.click(screen.getByTestId("symbol-tab-BTCUSDT"));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("symbol-tab-SOLUSDT"));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("SOLUSDT");
  });
});

// --------------------------------------------------------------------------- the selection

/** The page's wiring in miniature: the strip, the remembered selection, and something that
 *  trades. `orders` records the symbol an order would have been sent for. */
function Terminal({ volatility, orders }: {
  volatility: Record<string, SymbolVolatility>; orders: string[];
}) {
  const { symbol, select } = useSelectedSymbol(null);
  return (
    <>
      <SymbolTabs value={symbol} onChange={select} volatility={volatility} />
      <button type="button" data-testid="order" onClick={() => orders.push(symbol)}>주문</button>
      <span data-testid="order-target">{symbol}</span>
    </>
  );
}

describe("HOT never moves the selection", () => {
  it("leaves the selected tab, and the order target, where they were", () => {
    const orders: string[] = [];
    const { rerender } = render(
      <Terminal orders={orders} volatility={rows({ BTCUSDT: 1.9, ETHUSDT: 0.3, SOLUSDT: 0.2 })} />);
    expect(screen.getByTestId("symbol-tab-BTCUSDT")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("symbol-hot-BTCUSDT")).toHaveAttribute("data-hot", "true");

    // SOL becomes the widest. Nothing about the selection may notice.
    rerender(<Terminal orders={orders}
      volatility={rows({ BTCUSDT: 0.2, ETHUSDT: 0.3, SOLUSDT: 1.9 })} />);
    expect(screen.getByTestId("symbol-hot-SOLUSDT")).toHaveAttribute("data-hot", "true");
    expect(screen.getByTestId("symbol-tab-BTCUSDT")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("symbol-tab-SOLUSDT")).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("order-target").textContent).toBe("BTCUSDT");
    fireEvent.click(screen.getByTestId("order"));
    expect(orders).toEqual(["BTCUSDT"]);
    expect(readStoredSymbol()).toBe("BTCUSDT");
  });

  it("follows the operator through BTC to ETH to SOL and no further", () => {
    const orders: string[] = [];
    render(<Terminal orders={orders} volatility={rows({ BTCUSDT: 2.2, ETHUSDT: 0.1, SOLUSDT: 0.1 })} />);
    for (const symbol of ["ETHUSDT", "SOLUSDT"]) {
      fireEvent.click(screen.getByTestId(`symbol-tab-${symbol}`));
      expect(screen.getByTestId("order-target").textContent).toBe(symbol);
      expect(readStoredSymbol()).toBe(symbol);
      // BTC still holds HOT throughout; it never pulled the selection back.
      expect(screen.getByTestId("symbol-hot-BTCUSDT")).toHaveAttribute("data-hot", "true");
    }
    fireEvent.click(screen.getByTestId("order"));
    expect(orders).toEqual(["SOLUSDT"]);
  });

  it("remembers the selection across a reload while HOT sits elsewhere", () => {
    const orders: string[] = [];
    const hotOnSol = rows({ BTCUSDT: 0.1, ETHUSDT: 0.1, SOLUSDT: 2.4 });
    const first = render(<Terminal orders={orders} volatility={hotOnSol} />);
    fireEvent.click(screen.getByTestId("symbol-tab-ETHUSDT"));
    expect(readStoredSymbol()).toBe("ETHUSDT");
    first.unmount();

    render(<Terminal orders={orders} volatility={hotOnSol} />);   // the reload
    expect(screen.getByTestId("symbol-tab-ETHUSDT")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("order-target").textContent).toBe("ETHUSDT");
    expect(screen.getByTestId("symbol-hot-SOLUSDT")).toHaveAttribute("data-hot", "true");
  });

  it("survives the PAPER/LIVE switch, which re-renders the strip around the same state", () => {
    // The page holds the selection above both trees, so a source switch is a re-render of this
    // subtree with the same props. Simulated here by remounting the strip inside one owner.
    function Page({ volatility }: { volatility: Record<string, SymbolVolatility> }) {
      const { symbol, select } = useSelectedSymbol(null);
      const [source, setSource] = React.useState("PAPER");
      return (
        <>
          <button type="button" data-testid="switch"
            onClick={() => setSource(s => s === "PAPER" ? "BINANCE_LIVE" : "PAPER")}>{source}</button>
          {source === "PAPER"
            ? <SymbolTabs value={symbol} onChange={select} volatility={volatility} />
            : <div><SymbolTabs value={symbol} onChange={select} volatility={volatility} /></div>}
          <span data-testid="order-target">{symbol}</span>
        </>
      );
    }
    const volatility = rows({ BTCUSDT: 0.2, ETHUSDT: 2.1, SOLUSDT: 0.3 });
    render(<Page volatility={volatility} />);
    fireEvent.click(screen.getByTestId("symbol-tab-SOLUSDT"));
    expect(screen.getByTestId("order-target").textContent).toBe("SOLUSDT");
    fireEvent.click(screen.getByTestId("switch"));
    expect(screen.getByTestId("switch").textContent).toBe("BINANCE_LIVE");
    // Same selection, same figures - the reading is market data and knows nothing about accounts.
    expect(screen.getByTestId("order-target").textContent).toBe("SOLUSDT");
    expect(screen.getByTestId("symbol-tab-SOLUSDT")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("symbol-range-ETHUSDT").textContent).toContain("2.10%");
    expect(screen.getByTestId("symbol-hot-ETHUSDT")).toHaveAttribute("data-hot", "true");
  });
});

// --------------------------------------------------------------------------- 390 px

describe("390px layout", () => {
  const PHONE_WIDTH = 390;

  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: PHONE_WIDTH });
  });

  it("pins no element to a pixel width wider than the phone", () => {
    // jsdom does not lay out, so the check is structural: nothing may carry a fixed width, and
    // the strip itself has to be a scroll container.
    const { container } = render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()}
      volatility={rows({ BTCUSDT: 12.34, ETHUSDT: 1.14, SOLUSDT: 123.45 })} />);
    const pinned = Array.from(container.querySelectorAll<HTMLElement>("*")).filter(node => {
      const width = node.style.width || node.style.minWidth;
      return width.endsWith("px") && Number.parseFloat(width) > PHONE_WIDTH;
    });
    expect(pinned).toEqual([]);
    expect(screen.getByTestId("symbol-tabs")).toHaveClass("overflow-x-auto");
  });

  it("keeps all three tabs on the one row, with no wrapping", () => {
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()}
      volatility={rows({ BTCUSDT: 0.72, ETHUSDT: 1.14, SOLUSDT: 1.83 })} />);
    const strip = screen.getByTestId("symbol-tabs");
    expect(strip.children.length).toBe(3);
    expect(strip.className).not.toContain("flex-wrap");
    CRYPTO_SYMBOLS.forEach(symbol =>
      expect(screen.getByTestId(`symbol-tab-${symbol}`).className).toContain("shrink-0"));
  });

  it("drops the 1H label on a phone and keeps it on a wide screen", () => {
    render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()}
      volatility={rows({ BTCUSDT: 0.72, ETHUSDT: 1.14, SOLUSDT: 1.83 })} />);
    const label = screen.getByTestId("symbol-range-BTCUSDT").firstElementChild as HTMLElement;
    expect(label.textContent).toBe("1H");
    expect(label.className).toContain("hidden");
    expect(label.className).toContain("sm:inline");
  });

  it("does not grow the tab's padding to fit the second line", () => {
    const { rerender } = render(<SymbolTabs value="BTCUSDT" onChange={vi.fn()} />);
    expect(screen.getByTestId("symbol-tab-BTCUSDT").className).toContain("py-1.5");
    rerender(<SymbolTabs value="BTCUSDT" onChange={vi.fn()} volatility={rows({ BTCUSDT: 1 })} />);
    expect(screen.getByTestId("symbol-tab-BTCUSDT").className).toContain("py-1");
    expect(screen.getByTestId("symbol-tab-BTCUSDT").className).not.toContain("py-1.5");
  });
});

// --------------------------------------------------------------------------- the poll

describe("useSymbolVolatility", () => {
  const body = (symbol: string, rowsIn: ChartBar[]) => ({
    symbol, timeframe: "1m" as const, bucket_ms: VOLATILITY_BUCKET_MS,
    source: "BYBIT_PUBLIC_KLINE" as const, source_interval: "1",
    bars: rowsIn, has_more: false, next_before_ms: null,
  });

  const spans: Record<string, number> = { BTCUSDT: 0.72, ETHUSDT: 1.14, SOLUSDT: 1.83 };
  const serve = () => vi.spyOn(cryptoApi, "chartHistory").mockImplementation(
    async (symbol: string) => body(symbol, windowOf(spans[symbol] ?? 1, "UP")));

  it("reads 1m history once per symbol, for the bars the window needs", async () => {
    const read = serve();
    const { result } = renderHook(() => useSymbolVolatility(CRYPTO_SYMBOLS));
    await waitFor(() => expect(Object.keys(result.current.rows).length).toBe(3));
    expect(read).toHaveBeenCalledTimes(3);
    for (const symbol of CRYPTO_SYMBOLS) {
      expect(read).toHaveBeenCalledWith(symbol, "1m", VOLATILITY_REQUEST_BARS, null,
                                        expect.anything());
    }
    expect(rangeLabel(result.current.rows.SOLUSDT)).toBe("1.83%");
    expect(hotSymbols(Object.values(result.current.rows))).toEqual(new Set(["SOLUSDT"]));
  });

  it("adds three reads per tick and no more, however long it runs", async () => {
    vi.useFakeTimers();
    const read = serve();
    renderHook(() => useSymbolVolatility(CRYPTO_SYMBOLS));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(read).toHaveBeenCalledTimes(3);
    for (let tick = 1; tick <= 6; tick += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(VOLATILITY_POLL_MS); });
      expect(read).toHaveBeenCalledTimes(3 * (tick + 1));
    }
    // Sixty seconds of the strip is eighteen reads of 64 bars. Nothing else was touched.
    expect(read.mock.calls.every(call => call[1] === "1m")).toBe(true);
  });

  it("stops when the strip goes away", async () => {
    vi.useFakeTimers();
    const read = serve();
    const { unmount } = renderHook(() => useSymbolVolatility(CRYPTO_SYMBOLS));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(VOLATILITY_POLL_MS * 5); });
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("reads nothing at all when disabled", async () => {
    const read = serve();
    renderHook(() => useSymbolVolatility(CRYPTO_SYMBOLS, false));
    await act(async () => { await Promise.resolve(); });
    expect(read).not.toHaveBeenCalled();
  });

  it("discards a response that names a different instrument", async () => {
    // The route answers about BTC whatever it was asked. Only BTC's tab may show a figure.
    vi.spyOn(cryptoApi, "chartHistory").mockImplementation(
      async () => body("BTCUSDT", windowOf(1.5, "UP")));
    const { result } = renderHook(() => useSymbolVolatility(CRYPTO_SYMBOLS));
    await waitFor(() => expect(result.current.rows.BTCUSDT).toBeDefined());
    expect(Object.keys(result.current.rows)).toEqual(["BTCUSDT"]);
  });

  it("keeps the last figure through a failed read, then lets it go stale", async () => {
    vi.useFakeTimers();
    const windowEnd = 1_700_000_000_000 - 1_700_000_000_000 % VOLATILITY_BUCKET_MS;
    vi.setSystemTime(windowEnd + VOLATILITY_BUCKET_MS);
    const read = vi.spyOn(cryptoApi, "chartHistory").mockImplementation(
      async (symbol: string) => body(symbol, windowOf(0.9, "UP")));
    const { result } = renderHook(() => useSymbolVolatility(["BTCUSDT"]));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(rangeLabel(result.current.rows.BTCUSDT)).toBe("0.90%");

    read.mockRejectedValue(new Error("502 Bad Gateway"));
    await act(async () => { await vi.advanceTimersByTimeAsync(VOLATILITY_POLL_MS); });
    // One failed poll is not news about volatility; the figure stands.
    expect(rangeLabel(result.current.rows.BTCUSDT)).toBe("0.90%");

    await act(async () => { await vi.advanceTimersByTimeAsync(VOLATILITY_STALE_MS); });
    // A backend that stays down cannot leave a figure on screen claiming to be the last hour.
    expect(rangeLabel(result.current.rows.BTCUSDT)).toBe("--");
    expect(result.current.rows.BTCUSDT.state === "UNKNOWN"
      && result.current.rows.BTCUSDT.reason).toBe("STALE");
  });

  it("drops a symbol the server stops permitting", async () => {
    serve();
    const { result, rerender } = renderHook(
      ({ list }: { list: readonly string[] }) => useSymbolVolatility(list),
      { initialProps: { list: CRYPTO_SYMBOLS as readonly string[] } });
    await waitFor(() => expect(Object.keys(result.current.rows).length).toBe(3));
    rerender({ list: ["BTCUSDT", "ETHUSDT"] });
    await waitFor(() => expect(Object.keys(result.current.rows).sort())
      .toEqual(["BTCUSDT", "ETHUSDT"]));
  });

  it("falls back to the client's own list when the server has not answered yet", async () => {
    const read = serve();
    renderHook(() => useSymbolVolatility(null));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(3));
    expect(read.mock.calls.map(call => call[0]).sort()).toEqual([...CRYPTO_SYMBOLS].sort());
  });
});
