import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  AuxiliarySection, DerivativesSection, FlowSection, LayerSection, LiquiditySection,
  MarketContextPanel, PriceSection, StateChip,
} from "@/components/market-context-panel";
import {
  MISSING, STATE_LABELS, WALL_STATE_LABELS, reasonLabel, signedPct, stateTone,
} from "@/lib/market-context";
import type {
  AuxiliaryLayer, Coverage, DepthBand, DerivativesLayer, FlowLayer, LayerStateOrAbsent,
  LiquidityLayer, LiquiditySide, MarketContextPayload, PriceLayer, Side, Wall, WallState,
} from "@/lib/market-context";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

/** 390px is the phone this terminal is actually used on; the layout assertions use it. */
const PHONE_WIDTH = 390;
const NOW = 1_791_000_000_000;

// --------------------------------------------------------------------------- fixtures

function priceLayer(overrides: Partial<PriceLayer> = {}): PriceLayer {
  const level = (side: "RESISTANCE" | "SUPPORT", available: boolean) => ({
    side, price: available ? (side === "RESISTANCE" ? 86_400.5 : 85_600.25) : null,
    distance_pct: available ? (side === "RESISTANCE" ? 0.4651 : -0.4651) : null,
    lifecycle: available ? "CONFIRMED" : null, origin_kind: available ? "SWING_HIGH" : null,
    strength: available ? 9 : null, touches: available ? 6 : null,
    confirm_tf: available ? "1h" : null, sources: available ? "1h,5m" : null,
    level_id: available ? 16 : null, available,
    unavailable_reason: available ? null : "NO_PUBLISHED_LEVEL_ON_THIS_SIDE",
  });
  return {
    state: "LIVE", reasons: [], symbol: "BTCUSDT",
    series: {
      anchor_ms: NOW - 30 * 86_400_000, series_first_ms: NOW - 30 * 86_400_000,
      series_last_open_ms: NOW - 60_000, series_rows: 43_200,
      publish_from_ms: NOW - 86_400_000, publish_to_ms: NOW, published_minutes: 1_441,
      computed_at_ms: NOW, recompute_ms: 712, fetch_ms: 90,
      anchor_rule: "FIXED_AT_PROCESS_START_EXTENDED_FORWARD_ONLY",
      theta_1h: 3, theta_5m: 6, repaint_violations: 0, vanished_levels: 0,
      venue: "binance_usdm",
    },
    row: {
      bar_open_ms: NOW - 60_000, bar_close_ms: NOW, bar_age_ms: 20_000, close: 86_000.5,
      trend_structure: "BULLISH", last_high_label: "HH", last_low_label: "HL",
      active_level_count: 6, published_prices: [85_600.25, 86_400.5],
      atr_5m: 120.5, atr_1h: 310.25, vwap_z: -1.36, vwap_position: "BELOW",
    },
    levels: { resistance: level("RESISTANCE", true), support: level("SUPPORT", true) },
    events: {
      breakout_state: "UP", breakout_level_id: 16, breakout_at_ms: NOW - 180_000,
      breakout_age_min: 3, breakout_confirm_tf: "5m", retest_state: "BROKEN",
      retest_direction: "UP", retest_level_id: 16, failed_break_state: "NONE",
      sweep_direction: "DOWN", sweep_at_ms: NOW - 3_600_000, sweep_age_min: 60,
      sweep_level_id: 12,
    },
    range: { state: "NO_RANGE", high: null, low: null, position: null },
    prev_session: {
      high: 86_770, low: 84_677.3, high_distance_pct: 0.894, low_distance_pct: -1.538,
      in_level_book: false,
      note: "전일 고저는 레벨 북 밖의 별도 context입니다. R2에서 27건 중 1건 증가였습니다.",
    },
    note: "레벨 북은 R1 큐레이션 그대로이고 시리즈 시작점은 고정합니다.",
    ...overrides,
  };
}

function wall(extra: Partial<Wall> = {}): Wall {
  return {
    price: "86100.0", qty_btc: "10.02", notional_usdt: "862122.0", distance_bps: "11.60",
    distance_pct: "0.1160", multiple: "14.6", coverage: "COMPLETE" as const,
    persistence_ms: 42_000, own_persistence_ms: 42_000, carried_persistence_ms: null,
    persistence_source: "OWN" as const, continuity_status: "NEW" as const,
    not_carried_reason: null, carried_members: 0, bin_low: "86100.0", bin_high: "86105.0",
    bin_members: 1, bin_candidate_notional_usdt: "862122.0",
    bin_candidate_notional_is_lower_bound: true, values_as_of: "CHECKPOINT_CURRENT",
    generation: 1, first_seen_ms: NOW - 42_000, ...extra,
  };
}

function side(name: Side, wallState: WallState): LiquiditySide {
  const depth = (band: string, lower: boolean): DepthBand => ({
    band_pct: band, coverage: (lower ? "PARTIAL" : "COMPLETE") as Coverage,
    notional: lower ? null : "21093311.82", qty: lower ? null : "248.609",
    observed_notional: "38029410.87", observed_qty: "448.086", is_lower_bound: lower,
    levels: 1_006, price_low: "85900", price_high: "86100", imbalance_usdt: "0.0669",
  });
  return {
    side: name, wall_state: wallState,
    wall_state_reason: wallState === "OK" ? null
      : wallState === "NONE" ? "NO_CANDIDATE_QUALIFIES_UNDER_LM_WALL_V2"
      : wallState === "STALE" ? "JOURNAL_OLDER_THAN_FRESHNESS_BOUND" : "WALL_SET_PARTIAL",
    nearest_wall: wallState === "OK" ? wall() : null,
    coverage: (wallState === "PARTIAL" ? "PARTIAL" : "COMPLETE") as Coverage,
    candidates_total: 93, walls_selected: wallState === "OK" ? 3 : 0,
    walls_shown: wallState === "OK" ? 1 : 0,
    walls: wallState === "OK" ? [wall()] : [],
    depth: [depth("0.1", false), depth("0.25", true), depth("0.5", true), depth("1", true)],
  };
}

function liquidityLayer(overrides: Partial<LiquidityLayer> = {}): LiquidityLayer {
  return {
    state: "LIVE", reasons: [], symbol: "BTCUSDT",
    sides: { ASK: side("ASK", "OK"), BID: side("BID", "NONE") },
    book: {
      mid: "86000.5", best_bid: "86000.4", best_ask: "86000.6", spread_bps: "0.02",
      observed_low: "85900.0", observed_high: "86100.0", observed_low_pct: "-0.1168",
      observed_high_pct: "0.1157", observed_symmetric_pct: "0.1157", snapshot_limit: 1_000,
    },
    band_imbalance: {
      band_pct: "0.1", coverage: "PARTIAL", notional_bid: "19949053.0293",
      notional_ask: "24973100.601", is_lower_bound: true,
    },
    coverage: {
      bands: null, complete_bands: ["0.1"], partial_bands: ["0.25", "0.5", "1"],
      lower_bound_marker: "≥", lower_bounds_identical: true,
    },
    walls: {
      coverage: "COMPLETE", verified_by: "COLLECTOR_STATE_CHECKPOINT", unverified_reason: null,
      source: "COLLECTOR_STATE_CHECKPOINT", candidate_count: 93, walls_selected: 3, carried: 1,
      rule: "lm-wall.v2", continuity_rule: "lm-continuity.v5",
      filter_min_notional_usdt: "500000",
    },
    journal: {
      root: "/tmp/journal", session_id: "abc", session_age_ms: 600_000, session_ended: false,
      sample_receive_ms: NOW, journal_age_ms: 400, feed_state: "LIVE", depth_age_ms: 61,
      book_state: "SYNCED", exchange: "binance_usdm", symbol: "BTCUSDT",
      book_generation: 1,
    },
    presence_base_rate: {
      ask_pct: 99, bid_pct: 98.5, mean_per_side_when_present: 8.8,
      window: "Market Context R0, 3.61h / 12,779 samples, walls within 10 bp",
    },
    observed_range_note: "관측 구간 밖은 얇다가 아니라 보지 못했다입니다. ±0.15%를 덮습니다.",
    presence_note: "벽의 존재 자체는 신호가 아닙니다. 99.0% / 98.5% bullish 표식 없음.",
    ...overrides,
  };
}

function flowLayer(overrides: Partial<FlowLayer> = {}): FlowLayer {
  const window = (label: string, state: "LIVE" | "STALE" | "PARTIAL" | "UNKNOWN") => ({
    window: label, state, coverage: (state === "PARTIAL" ? "PARTIAL" : "COMPLETE") as Coverage,
    coverage_reason: null, coverage_age_ms: 1_000, trades: 122,
    buy_btc: "1.107", sell_btc: "11.823", buy_usdt: "93966.2559",
    sell_usdt: "1003578.8028", net_btc: "-10.716", net_usdt: "-909612.5469",
    imbalance_usdt: "-0.8288", imbalance_btc: "-0.8288", is_lower_bound: state === "PARTIAL",
  });
  return {
    state: "LIVE", reasons: [], symbol: "BTCUSDT",
    windows: { "5s": window("5s", "LIVE"), "15s": window("15s", "LIVE"),
               "60s": window("60s", "LIVE") },
    aggressor_rule: "aggressor=SELL if buyer is maker (m=true), else BUY",
    clock: "LOCAL_MONOTONIC_RECEIPT",
    trade_stream: { connected: true, age_ms: 80, last_receive_ms: NOW, stale_ms: 5_000,
                    state: "LIVE", age_measured_from: "THE_SAMPLE_THE_COLLECTOR_WROTE",
                    journal_state: "LIVE" },
    vanished: { this_reading: [], recent: [], totals: {}, tracked_bins: 3, path_samples: 1 },
    absorption: { state: "NONE", reason: "AGGRESSIVE_FLOW_BELOW_WALL_NOTIONAL",
                  order_identity_proven: false, note: "absorption은 candidate만 표시합니다." },
    vanish_base_rate: { ask_touched_bin_pct: 3.8, bid_touched_bin_pct: 6.9,
                        window: "Market Context R0, 3.61h journal" },
    vanish_note: "사라진 벽을 CONSUMED로 단정하지 않습니다.",
    absorption_note: "absorption은 candidate만 표시하며 판정이 아닙니다.",
    imbalance_note: "불균형은 방향 판정이 아닙니다.",
    ...overrides,
  };
}

function derivativesLayer(overrides: Partial<DerivativesLayer> = {}): DerivativesLayer {
  const change = (base: string, pct: string) => ({
    available: true, unavailable_reason: null, base, base_pct: pct,
    value_usdt: "1000", from_bucket_ms: NOW - 300_000, to_bucket_ms: NOW,
  });
  return {
    state: "LIVE", reasons: [], symbol: "BTCUSDT", read_at_ms: NOW,
    open_interest: { base: "98614.693", venue_time_ms: NOW, age_ms: 3_282, unit: "BASE_ASSET",
                     available: true, unavailable_reason: null },
    open_interest_change: {
      available: true, unavailable_reason: null,
      windows: { "5m": change("-20.262", "-0.0205"), "15m": change("116.609", "0.1184"),
                 "1h": change("156.726", "0.1591"), "4h": change("1063.934", "1.0904") },
      period: "5m", buckets: 60, source: "CLOSED_VENUE_BUCKETS_NOT_LOCAL_POLLS",
    },
    funding: { last_rate: "0.00006641", last_rate_pct: "0.006641", interest_rate: "0.0001",
               next_funding_time_ms: NOW + 3_600_000, venue_time_ms: NOW, age_ms: 3_282,
               available: true, unavailable_reason: null },
    basis: { mark_price: "86415.94", index_price: "86445.24", estimated_settle_price: "86568.8",
             basis_usdt: "-29.30", premium_pct: "-0.033893",
             formula: "basis = mark - index; premium_pct = (mark - index) / index * 100",
             venue_time_ms: NOW, age_ms: 3_282, available: true, unavailable_reason: null },
    note: "파생 정보는 공개 엔드포인트에서 읽은 원값입니다.",
    ...overrides,
  };
}

function auxiliaryLayer(overrides: Partial<AuxiliaryLayer> = {}): AuxiliaryLayer {
  return {
    state: "LIVE", rendered: true, symbol: "BTCUSDT", reasons: [],
    c1: {
      weight: "auxiliary", state: "LIVE", reasons: [], root: "data/runtime/crypto/c1",
      root_exists: true, last_decision_age_ms: 42_000, stale_ms: 300_000,
      engine_state: { armed: true, bars: 47_520, signals: 3 }, engine_state_age_ms: 10_000,
      latest_signal: { signal_id: "c1-7", state: "TRIGGERED" }, latest_signal_age_ms: 60_000,
      signal_records: 3, latest_c1x: null, c1x_records: 0,
      read_mode: "BYTES_ONLY_NO_C1_MODULE_IMPORTED", contract_evaluated: false,
    },
    directional: {
      weight: "weak_auxiliary", state: "LIVE", reasons: [],
      artifact: "data/research/crypto/directional_probability_r0/results.json",
      verdict: "WEAK_DIRECTIONAL_MODEL", usable_horizons: [], weak_horizons: [30, 60, 120],
      horizons: null,
      bounds: [
        { study: "BTC-P1", record: "a", finding: "directional AUC was 0.502 to 0.533" },
        { study: "BTC-P2", record: "b", finding: "STRONG 0/64, P3 NOT_AUTHORIZED" },
      ],
      computed_for_now: false, inference_run: false,
      note: "현재 시점 확률을 계산하지 않습니다.",
    },
    note: "BTCUSDT 전용입니다.",
    ...overrides,
  };
}

function payload(overrides: Partial<MarketContextPayload> = {}): MarketContextPayload {
  const layers: MarketContextPayload["layers"] = {
    PRICE: priceLayer(), LIQUIDITY: liquidityLayer(), FLOW: flowLayer(),
    DERIVATIVES: derivativesLayer(), AUXILIARY: auxiliaryLayer(),
    ...(overrides.layers ?? {}),
  };
  return {
    panel_version: "mc-v1.0", mode: "READ_ONLY_CONTEXT_PANEL",
    contract: { contract_version: "mc-display.v1", contract_path: "docs/x.md",
                contract_sha256: "abc", contract_sha256_recorded: "abc", agrees: true },
    server_time_ms: NOW, symbol: layers.PRICE.symbol,
    supported_symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    symbol_support: {},
    layer_order: ["PRICE", "LIQUIDITY", "FLOW", "DERIVATIVES", "AUXILIARY"],
    layer_states: {
      PRICE: layers.PRICE.state, LIQUIDITY: layers.LIQUIDITY.state, FLOW: layers.FLOW.state,
      DERIVATIVES: layers.DERIVATIVES.state, AUXILIARY: layers.AUXILIARY.state,
    },
    rollup: { exists: false, reason: "NO_JOINT_READING_IS_PUBLISHED",
              measurement: "Market Context R0: A_NO_CONTEXT_EDGE.",
              note: "레이어 5개를 하나의 상태로 합치지 않습니다. NEUTRAL 칸은 없습니다." },
    freshness_bounds: { depth_stale_ms: 2_000, trade_stale_ms: 5_000, journal_stale_ms: 3_000,
                        structure_grace_ms: 90_000, derivatives_stale_ms: 60_000 },
    notes: { scope: "읽기 전용 컨텍스트 패널입니다." },
    missing_value: "-",
    panel: { started_ms: NOW, anchor_ms: NOW - 30 * 86_400_000, anchor_days: 30, polls: 5,
             structure_recomputes: 2 },
    journal: { enabled: true, root: "/tmp/tape", root_env: "MC_V1_JOURNAL_ROOT",
               records_written: 12, last_written_ms: NOW, last_path: "/tmp/tape/a.jsonl",
               last_error: null, skipped_as_duplicate: 40, record: "MC_V1_FORWARD_CONTEXT",
               schema_version: 1, cadence: "ONE_PER_CLOSED_MINUTE", linked_to_orders: false,
               read_back_by_the_panel: false, note: "주문과 연결되지 않습니다.",
               wrote_this_poll: true },
    cost: { elapsed_ms: 14, structure_recompute_ms: 712 },
    ...overrides,
    // After the spread, not before: `overrides.layers` is a *partial* map, and letting it through
    // the spread would replace the whole five-layer object with the one or two layers a test
    // happens to be about. The panel then reads `layers.DERIVATIVES.reasons` on undefined.
    layers,
  };
}

function stubFetch(body: unknown, { ok = true } = {}) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok, status: ok ? 200 : 503, json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// --------------------------------------------------------------------------- state words

describe("every state reaches the screen as a word", () => {
  const states: LayerStateOrAbsent[] = ["LIVE", "STALE", "PARTIAL", "UNKNOWN", "UNAVAILABLE"];

  it.each(states)("renders %s as text, not only as a colour", state => {
    render(<StateChip state={state} />);
    const chip = screen.getByTestId("mc-state-chip");
    expect(chip).toHaveTextContent(STATE_LABELS[state]);
    expect(chip.getAttribute("data-state")).toBe(state);
    // The tone is an addition. Removing the class would leave the word; removing the word would
    // leave a colour, which section 4 of the contract forbids.
    expect(chip.className).toContain(stateTone(state));
  });

  it("has no NEUTRAL state to render", () => {
    expect(Object.keys(STATE_LABELS)).not.toContain("NEUTRAL");
  });
});

// --------------------------------------------------------------------------- PRICE

describe("PRICE", () => {
  it("shows the nearest levels with the sign of their distance", () => {
    render(<PriceSection layer={priceLayer()} />);
    expect(screen.getByText(/저항 \(가장 가까운\)/)).toBeInTheDocument();
    expect(screen.getByText(signedPct(0.4651))).toBeInTheDocument();
    expect(screen.getByText(signedPct(-0.4651))).toBeInTheDocument();
  });

  it("names a side with no published level instead of showing a zero", () => {
    const layer = priceLayer();
    layer.levels!.resistance = { ...layer.levels!.resistance, available: false, price: null,
                                 distance_pct: null,
                                 unavailable_reason: "NO_PUBLISHED_LEVEL_ON_THIS_SIDE" };
    render(<PriceSection layer={layer} />);
    const absent = screen.getAllByTestId("mc-absent");
    expect(absent.length).toBeGreaterThan(0);
    expect(absent[0]).toHaveTextContent(MISSING);
    expect(screen.getByText(/이 방향에 발행된 레벨 없음/)).toBeInTheDocument();
  });

  it("shows the breakout and retest states by name", () => {
    render(<PriceSection layer={priceLayer()} />);
    expect(screen.getByText(/UP · lvl 16/)).toBeInTheDocument();
    expect(screen.getByText(/돌파 유지/)).toBeInTheDocument();
  });

  it("keeps the previous session high and low outside the level book", () => {
    render(<PriceSection layer={priceLayer()} />);
    const block = screen.getByTestId("mc-prev-session");
    expect(block).toHaveTextContent("레벨 북 밖의 별도 context");
    expect(within(block).getByText(/27건 중 1건/)).toBeInTheDocument();
  });

  it("names the range as absent rather than drawing empty bounds", () => {
    render(<PriceSection layer={priceLayer()} />);
    expect(screen.getByText(/레인지 없음/)).toBeInTheDocument();
  });

  it("draws the range with its bounds and the price's position inside it", () => {
    render(<PriceSection layer={priceLayer({
      range: { state: "IN_RANGE", high: 85_224, low: 82_832, position: 0.129 } })} />);
    expect(screen.getByText(/레인지 내부 · 82,832.0~85,224.0 · 위치 12.9%/))
      .toBeInTheDocument();
  });

  it("shows a failed break by name", () => {
    const layer = priceLayer();
    layer.events = { ...layer.events!, failed_break_state: "FAILED_UP" };
    render(<PriceSection layer={layer} />);
    expect(screen.getByText("FAILED_UP")).toBeInTheDocument();
  });

  it("names an absent breakout and an absent sweep rather than leaving them blank", () => {
    const layer = priceLayer();
    layer.events = { ...layer.events!, breakout_state: "NONE", breakout_level_id: null,
                     sweep_direction: null, sweep_age_min: null };
    render(<PriceSection layer={layer} />);
    expect(screen.getByText(/기록된 돌파 없음/)).toBeInTheDocument();
    expect(screen.getByText(/기록된 스윕 없음/)).toBeInTheDocument();
  });

  it("publishes the series basis so every figure is attributable", () => {
    render(<PriceSection layer={priceLayer()} />);
    expect(screen.getByText(/theta 1h 3 \/ 5m 6/)).toBeInTheDocument();
    expect(screen.getByText(/repaint 0 \/ vanished 0/)).toBeInTheDocument();
    expect(screen.getByText(/binance_usdm/)).toBeInTheDocument();
  });

  it("renders the trend as a structural label with its swing labels beside it", () => {
    render(<PriceSection layer={priceLayer()} />);
    expect(screen.getByText(/BULLISH \(고점·저점 상승\) · HH\/HL/)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- LIQUIDITY

describe("LIQUIDITY", () => {
  it("keeps the four ways there is no wall apart", () => {
    const states: WallState[] = ["OK", "NONE", "PARTIAL", "STALE", "UNKNOWN"];
    for (const state of states) {
      cleanup();
      render(<LiquiditySection layer={liquidityLayer({
        sides: { ASK: side("ASK", state), BID: side("BID", state) },
      })} />);
      const block = screen.getByTestId("mc-wall-ASK");
      expect(block.getAttribute("data-wall-state")).toBe(state);
      expect(block).toHaveTextContent(WALL_STATE_LABELS[state]);
    }
  });

  it("says why there is no wall rather than only showing a dash", () => {
    render(<LiquiditySection layer={liquidityLayer({
      sides: { ASK: side("ASK", "NONE"), BID: side("BID", "STALE") },
    })} />);
    expect(screen.getByText(/lm-wall.v2 기준 통과 후보 없음/)).toBeInTheDocument();
    expect(screen.getByText(/저널이 신선도 상한 초과/)).toBeInTheDocument();
  });

  it("marks a lower bound with the sign rather than printing it as a quantity", () => {
    render(<LiquiditySection layer={liquidityLayer()} />);
    const ask = screen.getByTestId("mc-wall-ASK");
    expect(within(ask).getAllByText(/^≥ /).length).toBeGreaterThan(0);
  });

  it("renders an unobserved band as a dash and never as zero", () => {
    const empty = side("ASK", "OK");
    empty.depth = empty.depth.map(band => ({ ...band, coverage: "UNKNOWN" as const,
                                             observed_notional: null, notional: null,
                                             is_lower_bound: false }));
    render(<LiquiditySection layer={liquidityLayer({
      sides: { ASK: empty, BID: side("BID", "OK") } })} />);
    const ask = screen.getByTestId("mc-wall-ASK");
    expect(within(ask).getAllByTestId("mc-absent").length).toBe(4);
    expect(ask.textContent).not.toMatch(/0 USDT/);
  });

  it("publishes the observed interval beside the wall figures", () => {
    render(<LiquiditySection layer={liquidityLayer()} />);
    expect(screen.getByText(/85,900.0~86,100.0/)).toBeInTheDocument();
  });

  it("carries the measured presence base rate so presence is not read as information", () => {
    render(<LiquiditySection layer={liquidityLayer()} />);
    expect(screen.getByText(/벽 존재는 신호가 아닙니다/)).toBeInTheDocument();
    expect(screen.getByText(/99.0%/)).toBeInTheDocument();
  });

  it("names the frozen rules it is showing", () => {
    render(<LiquiditySection layer={liquidityLayer()} />);
    expect(screen.getByText(/lm-wall.v2 · lm-continuity.v5/)).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- FLOW

describe("FLOW", () => {
  it("shows the three windows with their own states", () => {
    render(<FlowSection layer={flowLayer()} />);
    for (const label of ["5s", "15s", "60s"]) {
      expect(screen.getByTestId(`mc-flow-${label}`).getAttribute("data-state")).toBe("LIVE");
    }
  });

  it("says what the stream age is measured from", () => {
    render(<FlowSection layer={flowLayer()} />);
    expect(screen.getByText(/수집기가 쓴 샘플 기준/)).toBeInTheDocument();
  });

  it("marks every window stale when the journal is stale, whatever the stream age says", () => {
    const layer = flowLayer();
    layer.state = "STALE";
    layer.windows = Object.fromEntries(Object.entries(layer.windows!).map(([key, value]) =>
      [key, { ...value, state: "STALE" as const }]));
    layer.trade_stream = { ...layer.trade_stream!, state: "STALE", journal_state: "STALE",
                           age_ms: 80 };
    render(<FlowSection layer={layer} />);
    for (const label of ["5s", "15s", "60s"]) {
      expect(screen.getByTestId(`mc-flow-${label}`).getAttribute("data-state")).toBe("STALE");
    }
    expect(screen.getByText(/80ms/)).toBeInTheDocument();
  });

  it("never says CONSUMED about a vanished wall", () => {
    const layer = flowLayer();
    layer.vanished = {
      this_reading: [], totals: { CONSUMED_CANDIDATE: 1, CANCEL_LIKE: 9, UNKNOWN: 2 },
      tracked_bins: 3, path_samples: 1,
      recent: [
        { state: "CONSUMED_CANDIDATE", reason: "MID_PATH_REACHED_THE_BIN", side: "ASK",
          bin_low: "86100.0", bin_high: "86105.0", price: "86100.0",
          notional_usdt: "862122.0", last_seen_ms: NOW - 1_000, observed_at_ms: NOW,
          path_samples: 1, mid_path_touched_bin: true, order_identity_proven: false },
        { state: "CANCEL_LIKE", reason: "MID_PATH_NEVER_REACHED_THE_BIN", side: "BID",
          bin_low: "85900.0", bin_high: "85905.0", price: "85900.0",
          notional_usdt: "700000.0", last_seen_ms: NOW - 2_000, observed_at_ms: NOW,
          path_samples: 1, mid_path_touched_bin: false, order_identity_proven: false },
      ],
    };
    render(<FlowSection layer={layer} />);
    const states = screen.getAllByTestId("mc-vanish-state");
    expect(states[0]).toHaveTextContent("소비 후보");
    expect(states[1]).toHaveTextContent("취소로 보임");
    // Asserted on the state elements, not on the block's text: the heading says "CONSUMED로
    // 단정하지 않습니다", which is the sentence declaring the ban and therefore has to contain the
    // word. What must never appear is a *value* reading bare CONSUMED.
    for (const node of states) {
      expect(node.getAttribute("data-vanish-state")).toMatch(/^(CONSUMED_CANDIDATE|CANCEL_LIKE|UNKNOWN)$/);
      expect(node.textContent).not.toMatch(/\bCONSUMED\b/);
    }
    expect(screen.getByText(/실측 기준선 ASK 3.8% · BID 6.9%/)).toBeInTheDocument();
  });

  it("reads an empty vanish list as none observed rather than as silence", () => {
    render(<FlowSection layer={flowLayer()} />);
    expect(screen.getByTestId("mc-vanished-none")).toHaveTextContent("관측된 소멸 없음");
  });

  it("shows absorption as a candidate with order identity unproven", () => {
    const layer = flowLayer();
    layer.absorption = {
      state: "ABSORPTION_CANDIDATE",
      reason: "AGGRESSIVE_FLOW_AT_OR_ABOVE_WALL_NOTIONAL_AND_WALL_STILL_PRESENT",
      side: "ASK", window: "60s", aggressive_usdt: "1200000", wall_notional_usdt: "862122.0",
      wall_price: "86100.0", bin_low: "86100.0", bin_high: "86105.0",
      wall_persistence_ms: 42_000, order_identity_proven: false, note: "candidate만 표시합니다.",
    };
    render(<FlowSection layer={layer} />);
    const block = screen.getByTestId("mc-absorption");
    expect(block.getAttribute("data-absorption-state")).toBe("ABSORPTION_CANDIDATE");
    expect(block).toHaveTextContent("ABSORPTION_CANDIDATE");
    expect(block).toHaveTextContent("order identity 증명");
    expect(block).toHaveTextContent("아니오");
  });

  it("says why there is no absorption candidate", () => {
    render(<FlowSection layer={flowLayer()} />);
    expect(screen.getByTestId("mc-absorption"))
      .toHaveTextContent("공격적 체결이 벽 명목 미달");
  });
});

// --------------------------------------------------------------------------- DERIVATIVES

describe("DERIVATIVES", () => {
  it("shows open interest, its changes, funding and basis", () => {
    render(<DerivativesSection layer={derivativesLayer()} />);
    expect(screen.getByText("98,614.693")).toBeInTheDocument();
    expect(screen.getByText(signedPct("-0.0205", 4))).toBeInTheDocument();
    expect(screen.getByText("0.00006641")).toBeInTheDocument();
    expect(screen.getByText("-29.30 USDT")).toBeInTheDocument();
  });

  it("says the change is a difference of closed venue buckets", () => {
    render(<DerivativesSection layer={derivativesLayer()} />);
    expect(screen.getByText(/거래소의 확정 5m 버킷 차이/)).toBeInTheDocument();
    expect(screen.getByText(/basis = mark - index/)).toBeInTheDocument();
  });

  it("names a horizon it cannot compute rather than filling it in", () => {
    const layer = derivativesLayer();
    layer.open_interest_change!.windows["4h"] = {
      available: false, unavailable_reason: "NOT_ENOUGH_CLOSED_BUCKETS" };
    render(<DerivativesSection layer={layer} />);
    expect(screen.getByText(/확정 버킷 부족/)).toBeInTheDocument();
  });

  it("shows a failed read as a dash with its reason", () => {
    render(<DerivativesSection layer={derivativesLayer({
      open_interest: { base: null, venue_time_ms: null, age_ms: null, unit: "BASE_ASSET",
                       available: false, unavailable_reason: "FETCH_FAILED" } })} />);
    expect(screen.getAllByTestId("mc-absent")[0]).toHaveTextContent(MISSING);
  });
});

// --------------------------------------------------------------------------- AUXILIARY

describe("AUXILIARY", () => {
  it("labels C1 auxiliary and the directional study weak_auxiliary", () => {
    render(<AuxiliarySection layer={auxiliaryLayer()} />);
    expect(screen.getByTestId("mc-c1")).toHaveTextContent("auxiliary");
    expect(screen.getByTestId("mc-directional")).toHaveTextContent("weak_auxiliary");
  });

  it("says no probability is computed for now", () => {
    render(<AuxiliarySection layer={auxiliaryLayer()} />);
    const block = screen.getByTestId("mc-directional");
    expect(block).toHaveTextContent("현재 시점 확률");
    expect(block).toHaveTextContent("계산하지 않음");
    expect(block).toHaveTextContent("WEAK_DIRECTIONAL_MODEL");
    expect(block).toHaveTextContent("없음");
  });

  it("carries the later results that bound how the verdict may be read", () => {
    render(<AuxiliarySection layer={auxiliaryLayer()} />);
    expect(screen.getByText(/BTC-P1: directional AUC was 0.502 to 0.533/)).toBeInTheDocument();
    expect(screen.getByText(/BTC-P2: STRONG 0\/64, P3 NOT_AUTHORIZED/)).toBeInTheDocument();
  });

  it("says the C1 contract is not evaluated", () => {
    render(<AuxiliarySection layer={auxiliaryLayer()} />);
    expect(screen.getByText(/C1 계약을 평가하지 않습니다/)).toBeInTheDocument();
  });

  it("is absent rather than empty for a symbol the research never covered", () => {
    render(<AuxiliarySection layer={auxiliaryLayer({
      state: "UNAVAILABLE", rendered: false, symbol: "ETHUSDT", c1: null, directional: null })} />);
    expect(screen.getByTestId("mc-auxiliary-absent"))
      .toHaveTextContent("참고 연구 결과가 없습니다");
    expect(screen.queryByTestId("mc-c1")).toBeNull();
    expect(screen.queryByTestId("mc-directional")).toBeNull();
  });
});

// --------------------------------------------------------------------------- the whole panel

describe("the panel", () => {
  it("renders the five layers in priority order with a state word each", async () => {
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" />);
    await screen.findByTestId("market-context-panel");
    const order = ["PRICE", "LIQUIDITY", "FLOW", "DERIVATIVES", "AUXILIARY"];
    const rendered = Array.from(document.querySelectorAll("[data-testid^='mc-layer-']"))
      .map(node => node.getAttribute("data-testid")!.replace("mc-layer-", ""));
    expect(rendered).toEqual(order);
    const strip = screen.getByTestId("mc-state-strip");
    for (const name of order) expect(strip).toHaveTextContent(name);
    expect(within(strip).getAllByTestId("mc-state-chip")).toHaveLength(5);
  });

  it("says on screen that the layers are not rolled up", async () => {
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" />);
    const note = await screen.findByTestId("mc-no-rollup");
    expect(note).toHaveTextContent("하나의 상태로 합치지 않습니다");
    expect(note).toHaveTextContent("NEUTRAL 칸은");
    expect(screen.getByTestId("market-context-panel").textContent)
      .not.toMatch(/Market Context: NEUTRAL/);
  });

  it("keeps a stale layer from borrowing a live one's freshness", async () => {
    stubFetch(payload({
      layers: { LIQUIDITY: liquidityLayer({ state: "STALE" }),
                FLOW: flowLayer({ state: "STALE" }) } as never,
    }));
    render(<MarketContextPanel symbol="BTCUSDT" />);
    await screen.findByTestId("market-context-panel");
    expect(screen.getByTestId("mc-layer-PRICE").getAttribute("data-state")).toBe("LIVE");
    expect(screen.getByTestId("mc-layer-LIQUIDITY").getAttribute("data-state")).toBe("STALE");
    expect(screen.getByTestId("mc-layer-FLOW").getAttribute("data-state")).toBe("STALE");
  });

  it("keeps an unavailable layer in its slot and says it is absent, not empty", async () => {
    stubFetch(payload({
      symbol: "ETHUSDT",
      layers: {
        PRICE: priceLayer({ state: "UNAVAILABLE", symbol: "ETHUSDT", series: null, row: null,
                            levels: null, events: null, range: null, prev_session: null,
                            reasons: ["NOT_CALIBRATED_FOR_SYMBOL"] }),
        LIQUIDITY: liquidityLayer({ state: "UNAVAILABLE", sides: null, book: null,
                                    reasons: ["COLLECTOR_IS_BTC_ONLY"] }),
        FLOW: flowLayer({ state: "UNAVAILABLE", windows: null,
                          reasons: ["COLLECTOR_IS_BTC_ONLY"] }),
        AUXILIARY: auxiliaryLayer({ state: "UNAVAILABLE", rendered: false, c1: null,
                                    directional: null,
                                    reasons: ["RESEARCH_IS_BTC_ONLY"] }),
      } as never,
    }));
    render(<MarketContextPanel symbol="ETHUSDT" />);
    await screen.findByTestId("market-context-panel");
    for (const name of ["PRICE", "LIQUIDITY", "FLOW", "AUXILIARY"]) {
      expect(screen.getByTestId(`mc-layer-${name}`).getAttribute("data-state"))
        .toBe("UNAVAILABLE");
    }
    // DERIVATIVES is the one layer this symbol has, so it is the one that opens. The rule used
    // to be "the first three in contract order", which on an ETH screen opened three layers that
    // each say only "not available for this symbol" and left the available one shut - the panel
    // had nothing on it. So the absence sentence for a BTC-only layer is now one click away, and
    // what is unconditionally on screen for it is its slot and its UNAVAILABLE chip.
    expect(screen.getByTestId("mc-layer-DERIVATIVES").getAttribute("data-state")).toBe("LIVE");
    expect(screen.getByTestId("mc-derivatives")).toBeInTheDocument();
    expect(screen.queryByTestId("mc-unavailable-PRICE")).toBeNull();
    fireEvent.click(within(screen.getByTestId("mc-layer-PRICE")).getByRole("button"));
    expect(screen.getByTestId("mc-unavailable-PRICE"))
      .toHaveTextContent("빈 값이 아니라 부재입니다");
  });

  it("asks the backend about the symbol it was given", async () => {
    const fetchMock = stubFetch(payload());
    render(<MarketContextPanel symbol="SOLUSDT" minNotionalUsdt="250000" />);
    await screen.findByTestId("market-context-panel");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("symbol=SOLUSDT");
    expect(url).toContain("min_notional_usdt=250000");
  });

  it("keeps the last payload on screen when a poll fails, and says the poll failed", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => payload() })
      .mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(<MarketContextPanel symbol="BTCUSDT" />);
    await screen.findByTestId("market-context-panel");
    await vi.waitFor(() => expect(screen.getByTestId("mc-poll-error")).toBeInTheDocument(),
                     { timeout: 3_000 });
    expect(screen.getByTestId("mc-layer-PRICE")).toBeInTheDocument();
  }, 10_000);

  it("reports the forward journal without linking it to an order", async () => {
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" />);
    const footer = await screen.findByTestId("mc-footer");
    expect(footer).toHaveTextContent("forward journal 기록 중");
    expect(footer).toHaveTextContent("주문과 연결되지 않음");
  });

  it("collapses every layer but the first in the compact shape", async () => {
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" compact />);
    await screen.findByTestId("market-context-panel");
    expect(screen.getByTestId("mc-price")).toBeInTheDocument();
    expect(screen.queryByTestId("mc-liquidity")).toBeNull();
    expect(screen.queryByTestId("mc-flow")).toBeNull();
    // A collapsed section still says whether its data is live.
    expect(screen.getByTestId("mc-layer-LIQUIDITY")).toHaveTextContent(STATE_LABELS.LIVE);
  });

  it("collapses by itself on a narrow viewport, without the caller remembering to ask", async () => {
    // Measured at 390px with everything open: the panel is 3,178px tall and pushes the order
    // ticket 1,555px down the page. A default that depends on a prop is a default that will be
    // missed by the next caller.
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("max-width: 1279px"), media: query,
      addEventListener: () => {}, removeEventListener: () => {},
    }));
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" />);
    await screen.findByTestId("market-context-panel");
    expect(screen.getByTestId("mc-price")).toBeInTheDocument();
    expect(screen.queryByTestId("mc-liquidity")).toBeNull();
    expect(screen.queryByTestId("mc-flow")).toBeNull();
  });

  it("opens the first three layers on a wide viewport", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false, media: query,
      addEventListener: () => {}, removeEventListener: () => {},
    }));
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" />);
    await screen.findByTestId("market-context-panel");
    expect(screen.getByTestId("mc-price")).toBeInTheDocument();
    expect(screen.getByTestId("mc-liquidity")).toBeInTheDocument();
    expect(screen.getByTestId("mc-flow")).toBeInTheDocument();
  });

  it("opens and closes a layer on click", async () => {
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" compact />);
    await screen.findByTestId("market-context-panel");
    const header = within(screen.getByTestId("mc-layer-LIQUIDITY")).getByRole("button");
    expect(header.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("mc-liquidity")).toBeInTheDocument();
    fireEvent.click(header);
    expect(screen.queryByTestId("mc-liquidity")).toBeNull();
  });
});

// --------------------------------------------------------------------------- 390px

describe("390px", () => {
  it("renders the panel without a horizontal overflow at phone width", async () => {
    stubFetch(payload());
    const { container } = render(
      <div style={{ width: `${PHONE_WIDTH}px`, overflow: "hidden" }}>
        <MarketContextPanel symbol="BTCUSDT" compact />
      </div>);
    await screen.findByTestId("market-context-panel");
    const panel = screen.getByTestId("market-context-panel");
    // jsdom reports no layout, so the check is structural: the panel clips its own overflow and
    // every multi-column grid inside it is single-column. Pixel truth is measured with a real
    // browser in the preview run; this test is the regression guard for the classes.
    expect(panel.className).toContain("overflow-hidden");
    expect(panel.className).toContain("min-w-0");
    for (const grid of Array.from(container.querySelectorAll("[class*='grid']"))) {
      expect(grid.className).toContain("grid-cols-1");
    }
  });

  it("keeps the state strip wrapping rather than widening the panel", async () => {
    stubFetch(payload());
    render(<MarketContextPanel symbol="BTCUSDT" compact />);
    const strip = await screen.findByTestId("mc-state-strip");
    expect(strip.className).toContain("flex-wrap");
  });
});
