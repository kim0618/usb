import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { C1SignalStrip, ChartSection, useC1Signals } from "@/components/crypto-terminal-layout";
import {
  BUCKET_MS, activeChip, bucketOf, c1Api, c1xDetail, c1xNote, groupDetail, markerDetail,
  researchNote, toChartMarkers, withDisplaySequences,
} from "@/lib/crypto-c1";
import type { C1Marker, C1State } from "@/lib/crypto-c1";
import { CHART_TIMEFRAMES, cryptoApi } from "@/lib/crypto-paper";
import type { ChartTimeframe, CryptoState } from "@/lib/crypto-paper";

// The chart-section cases mount the real charting library, which asks jsdom for a 2D canvas
// context on construction and again on teardown. jsdom has none, and without a stub the
// teardown path logs a "Not implemented" error for every canvas it releases - noise that would
// sit next to a passing run and read like a failure.
beforeAll(() => {
  const context = {
    canvas: null, clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() {}, fill() {}, save() {}, restore() {}, scale() {}, translate() {}, closePath() {},
    arc() {}, setTransform() {}, measureText: () => ({ width: 0 }), fillText() {},
    createLinearGradient: () => ({ addColorStop() {} }), getImageData: () => ({ data: [] }),
    putImageData() {}, drawImage() {}, rect() {}, clip() {}, setLineDash() {},
  };
  HTMLCanvasElement.prototype.getContext =
    (() => context) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  // The library also watches the device pixel ratio through matchMedia, which jsdom omits.
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const T0 = 1_735_689_600_000;          // 2025-01-01T00:00:00Z, a clean bucket boundary

const marker = (overrides: Partial<C1Marker> = {}): C1Marker => ({
  signal_id: `C1-LONG-${overrides.triggered_at_ms ?? T0 + 60_000}`,
  display_seq: 1,
  strategy: "C1", direction: "LONG",
  triggered_at_ms: T0 + 60_000, signal_bar_ms: T0, signal_price: 90_000,
  planned_exit_at_ms: T0 + 241 * 60_000, horizon_min: 240, state: "ACTIVE", status: "ACTIVE",
  net_return: null, entry_price: null, exit_price: null, ...overrides,
});

const state = (overrides: Partial<C1State> = {}): C1State => ({
  enabled: true, ready: true, active: [], signals_total: 0,
  direction: "LONG", direction_contract: "LONG_ONLY", places_orders: false,
  contract: {
    document: "docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md", sha256: "c49cd0e5",
    research_cell: "C1-EVENT-240m-LONG", official_horizon_min: 240,
    research_oos: { verdict: "WEAK", gate: "CASE_C_NO_SURVIVE", net_vip0_base_bp: 19.278, n_eff: 77, days: 179 },
  },
  server_time_ms: T0 + 120 * 60_000, ...overrides,
});

describe("C1 markers on the chart", () => {
  it("recovers stable numbering from an old response without display_seq", () => {
    const older = marker({ signal_id: "older", triggered_at_ms: T0 + 60_000,
                           display_seq: undefined });
    const newer = marker({ signal_id: "newer", triggered_at_ms: T0 + 120_000,
                           signal_bar_ms: T0 + 60_000, display_seq: undefined,
                           c1x: c1x({ signal_id: "newer", parent_signal_id: undefined,
                                     display_seq: undefined }) });
    const restored = withDisplaySequences([newer, older]);
    expect(restored.map(row => row.display_seq)).toEqual([2, 1]);
    expect(restored[0].c1x?.display_seq).toBe(2);
    expect(restored[0].c1x?.parent_signal_id).toBe("newer");
    expect(toChartMarkers([older, newer], "1m").map(row => row.text))
      .toEqual(["C1 #1", "C1 #2", "C1x #2"]);
  });
  it("draws a LONG arrow below the candle and never a SHORT", () => {
    const [drawn] = toChartMarkers([marker()], "1m");
    expect(drawn.shape).toBe("arrowUp");
    expect(drawn.position).toBe("belowBar");
    expect(drawn.text).toBe("C1 #1");
    // The contract is LONG-only; nothing in the mapper can produce a downward arrow.
    expect(toChartMarkers([marker({ direction: "LONG" })], "1m")
      .every(item => item.shape === "arrowUp")).toBe(true);
  });

  it("anchors a marker to the signal bar, not to the decision instant one minute later", () => {
    // The decision is taken at the close of the signal bar, so using the trigger time would push
    // the arrow onto the following candle on a 1m chart.
    expect(bucketOf(marker(), "1m")).toBe(T0);
    expect(toChartMarkers([marker()], "1m")[0].time).toBe(T0 / 1000);
  });

  it("places the same event on every minute-or-higher timeframe without recomputing it", () => {
    const rows = [marker()];
    for (const timeframe of CHART_TIMEFRAMES.filter(value => value !== "15s")) {
      const drawn = toChartMarkers(rows, timeframe as ChartTimeframe);
      expect(drawn).toHaveLength(1);
      expect(drawn[0].signalIds).toEqual(rows.map(row => row.signal_id));
      const span = BUCKET_MS[timeframe as ChartTimeframe];
      expect(drawn[0].time * 1000).toBe(Math.floor(T0 / span) * span);
    }
  });

  it("hides both C1 and C1x markers on 15s without changing their pairing", () => {
    const row = withC1x();
    expect(toChartMarkers([row], "15s")).toEqual([]);
    expect(row.display_seq).toBe(1);
    expect(row.c1x?.display_seq).toBe(1);
    expect(row.c1x?.parent_signal_id).toBe(row.signal_id);
  });

  it("covers all six timeframes", () => {
    expect(Object.keys(BUCKET_MS).sort()).toEqual([...CHART_TIMEFRAMES].sort());
  });

  it("does not duplicate a marker when the same signal arrives twice", () => {
    // A 15 s poll overlapping a lazy-history page delivers the same event again.
    const same = [marker(), marker(), marker()];
    expect(toChartMarkers(same, "1m")).toHaveLength(1);
  });

  it("aggregates several events inside one high-timeframe candle", () => {
    const rows = [
      marker({ triggered_at_ms: T0 + 60_000, signal_bar_ms: T0 }),
      marker({ triggered_at_ms: T0 + 3_660_000, signal_bar_ms: T0 + 3_600_000,
               display_seq: 2 }),
    ];
    expect(toChartMarkers(rows, "1h")).toHaveLength(2);
    const folded = toChartMarkers(rows, "4h");
    expect(folded).toHaveLength(1);
    expect(folded[0].text).toBe("C1 x2");
    expect(folded[0].count).toBe(2);
    expect(folded[0].signalIds).toHaveLength(2);
  });

  it("keeps the display number on neighbouring and separated candles", () => {
    // The daily chart at 390 px puts adjacent days a few pixels apart, and two full labels there
    // overlap into something unreadable.
    const rows = [
      marker({ triggered_at_ms: T0 + 60_000, signal_bar_ms: T0 }),
      marker({ triggered_at_ms: T0 + 86_460_000, signal_bar_ms: T0 + 86_400_000,
               display_seq: 2 }),
    ];
    expect(toChartMarkers(rows, "1d").map(item => item.text)).toEqual(["C1 #1", "C1 #2"]);
    // Far apart on the same timeframe, the full label is kept.
    const spread = [
      marker({ triggered_at_ms: T0 + 60_000, signal_bar_ms: T0 }),
      marker({ triggered_at_ms: T0 + 10 * 86_400_000, signal_bar_ms: T0 + 10 * 86_400_000,
               display_seq: 2 }),
    ];
    expect(toChartMarkers(spread, "1d").map(item => item.text)).toEqual(["C1 #1", "C1 #2"]);
    // A group keeps its count either way, because the count is the information.
    const cluster = [
      marker({ triggered_at_ms: T0 + 60_000, signal_bar_ms: T0 }),
      marker({ triggered_at_ms: T0 + 120_000, signal_bar_ms: T0 + 60_000 }),
      marker({ triggered_at_ms: T0 + 86_460_000, signal_bar_ms: T0 + 86_400_000 }),
    ];
    expect(toChartMarkers(cluster, "1d").map(item => item.text)).toEqual(["C1 x2", "C1 #1"]);
  });

  it("returns markers in ascending time, as the chart library requires", () => {
    const rows = [
      marker({ triggered_at_ms: T0 + 600_000, signal_bar_ms: T0 + 540_000 }),
      marker({ triggered_at_ms: T0 + 60_000, signal_bar_ms: T0 }),
    ];
    const times = toChartMarkers(rows, "1m").map(item => item.time);
    expect(times).toEqual([...times].sort((a, b) => a - b));
  });

  it("keeps markers for a lazily loaded older page", () => {
    const older = marker({ triggered_at_ms: T0 - 86_400_000, signal_bar_ms: T0 - 86_460_000 });
    const rows = [marker(), older];
    expect(toChartMarkers(rows, "1m")).toHaveLength(2);
    // ...and can be limited to what is actually in view.
    expect(toChartMarkers(rows, "1m", T0 - 60_000)).toHaveLength(1);
  });
});

describe("C1 marker detail", () => {
  it("shows when the 4 h benchmark falls, while the signal is still running", () => {
    // A benchmark instant, not a planned exit: nothing closes at that time.
    const lines = markerDetail(marker());
    expect(lines[0]).toBe("C1 #1 · LONG");
    expect(lines[2]).toMatch(/^4H 기준 \d{2}:\d{2}$/);
  });

  it("shows the 4 h benchmark result once it is settled", () => {
    const lines = markerDetail(marker({ net_return: 0.0182, status: "SETTLED", state: "COMPLETED" }));
    expect(lines[2]).toBe("4H 기준 +1.82%");
  });

  it("summarises a group as one line", () => {
    const detail = groupDetail([marker(), marker({ signal_id: "C1-LONG-2", display_seq: 2 })]);
    expect(detail).toContain("C1 #1 · LONG");
    expect(detail).toContain("C1 #2 · LONG");
  });
});

describe("C1 pairing at 390 px", () => {
  it("keeps numbered C1/C1x detail available on mobile", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    const paired = c1x({ signal_id: "C1-LONG-3", display_seq: 3,
                         parent_signal_id: "C1-LONG-3" });
    const row = marker({ signal_id: "C1-LONG-3", display_seq: 3, c1x: paired });
    render(<C1SignalStrip state={state()} selected={[row]} selectedKind="C1x" />);
    expect(screen.getByTestId("c1-marker-detail")).toHaveTextContent(
      "C1x #3 · Premium Normalization");
  });
});

describe("C1 signal strip", () => {
  it("is one muted line when there is no signal, not an empty card", () => {
    render(<C1SignalStrip state={state()} selected={[]} />);
    expect(screen.getByTestId("c1-active-chip").textContent).toBe("C1 · 신호 없음");
    expect(screen.queryByTestId("c1-active-detail")).toBeNull();
    // Nothing to qualify while nothing is firing, so the verdict line stays out of the way.
    expect(screen.queryByTestId("c1-research-note")).toBeNull();
  });

  it("names the direction and when the 4 h benchmark falls", () => {
    const active = state({ active: [{ signal: marker() as never, shadow: null, c1x: null }], signals_total: 1 });
    render(<C1SignalStrip state={active} selected={[]} />);
    expect(screen.getByTestId("c1-active-chip").textContent).toContain("최근 C1 #1");
    const detail = screen.getByTestId("c1-active-detail").textContent ?? "";
    expect(detail).toContain("4H 기준");
    expect(detail).toContain("추적 중 1건");
    expect(detail).not.toContain("종료");
  });

  it("renders nothing at all when the backend says the layer is off", () => {
    const { container } = render(<C1SignalStrip state={state({ enabled: false })} selected={[]} />);
    expect(container.firstChild).toBeNull();
    expect(activeChip(state({ enabled: false }))).toBeNull();
  });

  it("says the research verdict rather than implying the signal is validated", () => {
    const active = state({ active: [{ signal: marker() as never, shadow: null, c1x: null }], signals_total: 1 });
    render(<C1SignalStrip state={active} selected={[]} />);
    const note = screen.getByTestId("c1-research-note").textContent ?? "";
    expect(note).toContain("WEAK");
    expect(note).toContain("CASE_C_NO_SURVIVE");
    expect(note).toContain("검증 통과 아님");
    expect(researchNote(state({ contract: { document: "d", sha256: "s" } }))).toBeNull();
  });

  it("marks the preview fixture so it cannot be read as a live signal", () => {
    render(<C1SignalStrip state={state({ mode: "FIXTURE" })} selected={[]} />);
    expect(screen.getByTestId("c1-fixture-badge")).toBeTruthy();
  });
});

describe("C1 polling", () => {
  it("fetches state and markers, and stays quiet when the layer is off", async () => {
    const stateSpy = vi.spyOn(c1Api, "state").mockResolvedValue(state({ enabled: false }));
    const markerSpy = vi.spyOn(c1Api, "markers")
      .mockResolvedValue({ enabled: false, markers: [] });
    const { result } = renderHook(() => useC1Signals(true));
    await waitFor(() => expect(result.current.state?.enabled).toBe(false));
    expect(stateSpy).toHaveBeenCalled();
    expect(markerSpy).not.toHaveBeenCalled();
  });

  it("surfaces the markers the backend produced", async () => {
    vi.spyOn(c1Api, "state").mockResolvedValue(state());
    vi.spyOn(c1Api, "markers").mockResolvedValue({ enabled: true, markers: [marker()] });
    const { result } = renderHook(() => useC1Signals(true));
    await waitFor(() => expect(result.current.markers).toHaveLength(1));
  });

  it("does not break the terminal when the signal layer is unreachable", async () => {
    vi.spyOn(c1Api, "state").mockRejectedValue(new Error("down"));
    const { result } = renderHook(() => useC1Signals(true));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.state).toBeNull();
    expect(result.current.markers).toEqual([]);
  });
});

describe("the chart section with signals", () => {
  const terminalState = { quote: null, account: null } as unknown as CryptoState;

  it("passes the folded markers to the chart and keeps the existing overlays", async () => {
    vi.spyOn(cryptoApi, "chartHistory").mockResolvedValue({
      timeframe: "1m", bucket_ms: 60_000, source: "BYBIT_PUBLIC_KLINE", source_interval: "1",
      bars: [{ start_ms: T0, open: "1", high: "2", low: "0.5", close: "1", volume: "3", confirmed: true }],
      has_more: false, next_before_ms: null,
    });
    render(<ChartSection state={terminalState} bars={[]} timeframe="1m" onTimeframe={() => {}}
      c1={state()} c1Markers={[marker()]} />);
    expect(screen.getByTestId("c1-signal-strip")).toBeTruthy();
    // The chart itself is a dynamic import, so wait for it and then read what it was handed.
    const chart = await screen.findByTestId("candle-chart");
    expect(chart.getAttribute("data-markers")).toBe("1");
    expect(chart.getAttribute("data-marker-text")).toBe("C1 #1");
  });

  it("keeps the signal status but passes zero C1/C1x markers to the 15s chart", async () => {
    vi.spyOn(cryptoApi, "candles15s").mockResolvedValue({
      candles: [], current: null, status: null,
    } as never);
    const row = withC1x();
    const active = state({ active: [{ signal: row as never, shadow: null, c1x: row.c1x! }],
      signals_total: 1 });
    render(<ChartSection state={terminalState} bars={[]} timeframe="15s" onTimeframe={() => {}}
      c1={active} c1Markers={[row]} />);
    expect(screen.getByTestId("c1-active-chip")).toHaveTextContent("최근 C1 #1");
    expect(screen.getByTestId("c1-active-detail")).toHaveTextContent("추적 중 1건");
    const chart = await screen.findByTestId("candle-chart");
    expect(chart).toHaveAttribute("data-markers", "0");
    expect(chart).toHaveAttribute("data-marker-text", "");
  });

  it("restores the same paired markers after switching 1m → 15s → 1m", async () => {
    vi.spyOn(cryptoApi, "chartHistory").mockResolvedValue({
      timeframe: "1m", bucket_ms: 60_000, source: "BYBIT_PUBLIC_KLINE", source_interval: "1",
      bars: [], has_more: false, next_before_ms: null,
    });
    vi.spyOn(cryptoApi, "candles15s").mockResolvedValue({
      candles: [], current: null, status: null,
    } as never);
    const row = withC1x();
    const props = { state: terminalState, bars: [], onTimeframe: () => {},
      c1: state(), c1Markers: [row] };
    const view = render(<ChartSection {...props} timeframe="1m" />);
    expect(await screen.findByTestId("candle-chart")).toHaveAttribute("data-markers", "2");
    view.rerender(<ChartSection {...props} timeframe="15s" />);
    expect(await screen.findByTestId("candle-chart")).toHaveAttribute("data-markers", "0");
    view.rerender(<ChartSection {...props} timeframe="1m" />);
    const restored = await screen.findByTestId("candle-chart");
    expect(restored).toHaveAttribute("data-markers", "2");
    expect(restored).toHaveAttribute("data-marker-text", "C1 #1|C1x #1");
  });

  it("shows no signal strip on a screen that was not given the layer", async () => {
    vi.spyOn(cryptoApi, "chartHistory").mockResolvedValue({
      timeframe: "1m", bucket_ms: 60_000, source: "BYBIT_PUBLIC_KLINE", source_interval: "1",
      bars: [], has_more: false, next_before_ms: null,
    });
    render(<ChartSection state={terminalState} bars={[]} timeframe="1m" onTimeframe={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("candle-chart")).toBeTruthy());
    expect(screen.queryByTestId("c1-signal-strip")).toBeNull();
  });
});

describe("C1 polling cost", () => {
  it("re-fetches the series only when a new event opens", async () => {
    vi.spyOn(c1Api, "state").mockResolvedValue(state({ signals_total: 3 }));
    const markerSpy = vi.spyOn(c1Api, "markers")
      .mockResolvedValue({ enabled: true, markers: [marker()] });
    const { result } = renderHook(() => useC1Signals(true));
    await waitFor(() => expect(result.current.markers).toHaveLength(1));
    // Several more state polls at the same count must not re-request the whole series.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 60)); });
    expect(markerSpy).toHaveBeenCalledTimes(1);
  });

  it("asks for more history than the deepest chart can show", async () => {
    vi.spyOn(c1Api, "state").mockResolvedValue(state({ signals_total: 1 }));
    const markerSpy = vi.spyOn(c1Api, "markers")
      .mockResolvedValue({ enabled: true, markers: [] });
    renderHook(() => useC1Signals(true));
    await waitFor(() => expect(markerSpy).toHaveBeenCalled());
    // 2,000 daily candles is the deepest history the chart loads; the series must outreach it.
    expect(markerSpy.mock.calls[0][2]).toBeGreaterThanOrEqual(2000);
  });
});

// ---------------------------------------------------------------- C1x diagnostic
const c1x = (overrides: Partial<NonNullable<C1Marker["c1x"]>> = {}) => ({
  c1x_event_id: "C1X-C1-LONG-1735689660000",
  signal_id: "C1-LONG-1735689660000",
  parent_signal_id: "C1-LONG-1735689660000",
  display_seq: 1,
  status: "TRIGGERED" as const,
  confirmation_count: 2,
  triggered_at_ms: T0 + 75 * 60_000,
  executable_at_ms: T0 + 75 * 60_000,
  observed_price: 90_500,
  premium_value: -0.0002,
  premium_bucket: 2,
  premium_boundary: -0.0004,
  hypothetical_exit_price: 90_700,
  holding_minutes: 75,
  net_if_exited: 0.0082,
  e0_net: null,
  e0_status: "PENDING" as const,
  delta_net: null,
  censored_by_max_hold: false,
  is_exit: false as const,
  meaning: "PREMIUM_NORMALIZATION_DIAGNOSTIC",
  ...overrides,
});

const withC1x = (overrides = {}) => marker({ c1x: c1x(overrides) } as Partial<C1Marker>);

describe("C1x markers", () => {
  it("draws the diagnostic as a square above the candle, never as an arrow", () => {
    const drawn = toChartMarkers([withC1x()], "1m");
    const diagnostic = drawn.find(item => item.kind === "C1x")!;
    expect(diagnostic.shape).toBe("square");
    expect(diagnostic.position).toBe("aboveBar");
    expect(diagnostic.text).toBe("C1x #1");
    // An arrow would read as a direction, and C1x is not one.
    expect(diagnostic.shape).not.toBe("arrowDown");
  });

  it("keeps the entry mark and the diagnostic distinguishable on the same chart", () => {
    const drawn = toChartMarkers([withC1x()], "1m");
    expect(drawn).toHaveLength(2);
    const entry = drawn.find(item => item.kind === "C1")!;
    const diagnostic = drawn.find(item => item.kind === "C1x")!;
    expect(entry.position).not.toBe(diagnostic.position);
    expect(entry.shape).not.toBe(diagnostic.shape);
    expect(entry.color).not.toBe(diagnostic.color);
    expect(entry.id).not.toBe(diagnostic.id);
  });

  it("places the diagnostic on the candle it was confirmed on", () => {
    const drawn = toChartMarkers([withC1x()], "1m");
    const diagnostic = drawn.find(item => item.kind === "C1x")!;
    // The trigger instant is a bar close, so the candle is the one that opened a minute earlier.
    expect(diagnostic.time).toBe((T0 + 74 * 60_000) / 1000);
  });

  it("draws nothing for a diagnostic that has not triggered", () => {
    for (const status of ["NOT_TRIGGERED", "CONFIRM_1", "EXPIRED_MAX_HOLD"] as const) {
      const drawn = toChartMarkers([withC1x({ status, triggered_at_ms: null })], "1m");
      expect(drawn.filter(item => item.kind === "C1x")).toHaveLength(0);
      expect(drawn.filter(item => item.kind === "C1")).toHaveLength(1);
    }
  });

  it("never folds an entry mark and a diagnostic into one count", () => {
    // Both land on the same daily candle; they must stay two marks saying different things.
    const drawn = toChartMarkers([withC1x()], "1d");
    expect(drawn.map(item => item.kind).sort()).toEqual(["C1", "C1x"]);
    expect(drawn.every(item => item.count === 1)).toBe(true);
  });

  it("aggregates several diagnostics in one candle separately from the entries", () => {
    const rows = [
      withC1x(),
      marker({
        signal_id: "C1-LONG-B", triggered_at_ms: T0 + 3_660_000, signal_bar_ms: T0 + 3_600_000,
        display_seq: 2,
        c1x: c1x({ signal_id: "C1-LONG-B", parent_signal_id: "C1-LONG-B",
                   display_seq: 2, c1x_event_id: "C1X-C1-LONG-B",
                   triggered_at_ms: T0 + 80 * 60_000 }),
      } as Partial<C1Marker>),
    ];
    const drawn = toChartMarkers(rows, "4h");
    expect(drawn.find(item => item.kind === "C1")!.text).toBe("C1 x2");
    expect(drawn.find(item => item.kind === "C1x")!.text).toBe("C1x x2");
    expect(groupDetail(rows, "C1x")).toContain("C1x #1 · Premium Normalization");
    expect(groupDetail(rows, "C1x")).toContain("C1x #2 · Premium Normalization");
  });

  it("maps the same diagnostic onto every minute-or-higher timeframe without recomputing it", () => {
    for (const timeframe of CHART_TIMEFRAMES.filter(value => value !== "15s")) {
      const drawn = toChartMarkers([withC1x()], timeframe as ChartTimeframe);
      const diagnostic = drawn.find(item => item.kind === "C1x")!;
      expect(diagnostic.signalIds).toEqual(["C1-LONG-1735689660000"]);
      const span = BUCKET_MS[timeframe as ChartTimeframe];
      expect(diagnostic.time * 1000).toBe(Math.floor((T0 + 74 * 60_000) / span) * span);
    }
  });
});

describe("C1x detail", () => {
  it("names itself Premium Normalization and shows the elapsed time", () => {
    const lines = c1xDetail(withC1x());
    expect(lines[0]).toBe("C1x #1 · Premium Normalization");
    expect(lines.some(line => line.startsWith("C1       "))).toBe(true);
    expect(lines.some(line => line.startsWith("C1x      "))).toBe(true);
    expect(lines.some(line => line.includes("경과") && line.includes("75분"))).toBe(true);
    expect(lines.some(line => line.includes("+0.82%"))).toBe(true);
  });

  it("says the benchmark is still being tallied until 4 h finishes", () => {
    expect(c1xDetail(withC1x()).join(" ")).toContain("4H 기준   집계 중");
    expect(c1xDetail(withC1x()).join(" ")).not.toContain("차이");
  });

  it("shows the benchmark and the difference once 4 h has settled", () => {
    const lines = c1xDetail(withC1x({ e0_status: "SETTLED", e0_net: 0.0047, delta_net: 0.0035 }));
    expect(lines).toContain("C1x 가정  +0.82%");
    expect(lines).toContain("4H 기준   +0.47%");
    expect(lines).toContain("차이      +0.35%");
  });

  it("is empty for a diagnostic that has not triggered", () => {
    expect(c1xDetail(withC1x({ status: "CONFIRM_1", triggered_at_ms: null }))).toEqual([]);
    expect(c1xDetail(marker())).toEqual([]);
  });
});

describe("wording", () => {
  const corpus = () => [
    ...markerDetail(marker()),
    ...markerDetail(marker({ net_return: 0.0182 })),
    ...c1xDetail(withC1x({ e0_status: "SETTLED", e0_net: 0.0047, delta_net: 0.0035 })),
    groupDetail([withC1x()], "C1x"),
    groupDetail([marker()], "C1"),
    activeChip(state({ active: [{ signal: withC1x() as never, shadow: null, c1x: null }] }))?.text ?? "",
    activeChip(state({ active: [{ signal: withC1x() as never, shadow: null, c1x: null }] }))?.detail ?? "",
    c1xNote({ id: "C1x", meaning: "", is_exit: false, benchmark: "E0", benchmark_horizon_min: 240,
              c1_signals: 3, c1x_triggered: 2, c1x_censored: 0, c1x_pending: 1,
              paired_with_e0: 1, forward_sample_sufficient: false,
              research: { status: "INCONCLUSIVE", winner_preservation: 0.488 } }) ?? "",
  ].join(" | ");

  it("never calls the 4 h horizon an exit", () => {
    // It is a benchmark. Nothing in this layer closes a position at that instant, so "종료" or
    // "청산 예정" would describe something that does not happen.
    expect(corpus()).not.toContain("4H 종료");
    expect(corpus()).not.toContain("종료 예정");
    expect(corpus()).not.toContain("청산");
    expect(corpus()).toContain("4H 기준");
  });

  it("never uses exit, sell or close for the diagnostic", () => {
    const text = corpus().toUpperCase();
    for (const word of ["EXIT", "SELL", "CLOSE", "STOP LOSS"]) {
      expect(text).not.toContain(word);
    }
  });

  it("reports the forward sample as insufficient rather than quoting a comparison", () => {
    const note = c1xNote({
      id: "C1x", meaning: "", is_exit: false, benchmark: "E0", benchmark_horizon_min: 240,
      c1_signals: 6, c1x_triggered: 4, c1x_censored: 1, c1x_pending: 1, paired_with_e0: 3,
      paired_delta_mean_bp: 999, forward_sample_sufficient: false,
      research: { status: "INCONCLUSIVE", winner_preservation: 0.488 },
    })!;
    expect(note).toContain("전방 표본 부족");
    expect(note).not.toContain("999");
    expect(note).toContain("48.8%");
  });
});

describe("the strip with a diagnostic", () => {
  it("shows the diagnostic tally when a signal is active", () => {
    const active = state({
      active: [{ signal: withC1x() as never, shadow: null, c1x: c1x() as never }],
      signals_total: 1,
      c1x: { id: "C1x", meaning: "", is_exit: false, benchmark: "E0", benchmark_horizon_min: 240,
             c1_signals: 1, c1x_triggered: 1, c1x_censored: 0, c1x_pending: 0,
             paired_with_e0: 0, forward_sample_sufficient: false,
             research: { status: "INCONCLUSIVE", winner_preservation: 0.488 } },
    });
    render(<C1SignalStrip state={active} selected={[]} />);
    const note = screen.getByTestId("c1x-note").textContent ?? "";
    expect(note).toContain("C1x 진단 1건 / C1 1건");
    expect(note).toContain("전방 표본 부족");
    expect(screen.getByTestId("c1-active-detail").textContent).toContain("4H 기준");
  });

  it("stays a single quiet line when nothing is active", () => {
    render(<C1SignalStrip state={state()} selected={[]} />);
    expect(screen.queryByTestId("c1x-note")).toBeNull();
  });
});
