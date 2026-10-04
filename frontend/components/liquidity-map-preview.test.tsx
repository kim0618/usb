import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  ContinuityPanel, CoveragePanel, FlowPanel, LadderOverlay, PriceCard, QualityStrip,
  ResnapshotPanel, SidePanel, WallSetPanel,
} from "@/components/liquidity-map-preview";
import { axisPosition, btc, duration, lowerBound, pct, usdt } from "@/lib/liquidity-map";
import type {
  BandDepth, ContinuityView, Coverage, CoverageView, FlowWindow, LiquiditySnapshot,
  ResnapshotView, Side, SideView, WallRow, WallV2,
} from "@/lib/liquidity-map";

afterEach(cleanup);

/** 390px is the phone this terminal is actually used on; the layout tests assert against it. */
const PHONE_WIDTH = 390;

function band(pct_: string, coverage: Coverage): BandDepth {
  const canonical = coverage === "COMPLETE";
  const observed = coverage !== "UNKNOWN";
  return {
    band_pct: pct_, coverage,
    qty: canonical ? "248.609" : null, notional: canonical ? "21093311.82" : null,
    observed_qty: observed ? "448.086" : null,
    observed_notional: observed ? "38029410.87" : null,
    is_lower_bound: coverage === "PARTIAL", levels: observed ? 1006 : null,
    price_low: observed ? "84800.45" : null, price_high: observed ? "85012.45" : null,
    imbalance_btc: canonical ? "0.0675" : null, imbalance_usdt: canonical ? "0.0669" : null,
  };
}

function wall(side: Side, price: string, extra: Partial<WallV2> = {}): WallV2 {
  return {
    side, price, qty_btc: "10.02", notional_usdt: "849701.01",
    distance_bps: "4.77", multiple: "14.6", local_average: "0.68",
    neighbours: 10, first_seen_ms: 1_000, observed_persistence_ms: 42_000,
    own_persistence_ms: 42_000, carried_persistence_ms: null, persistence_source: "OWN",
    continuity_status: "NEW", not_carried_reason: "NO_EARLIER_OBSERVATION",
    continuity_first_seen_ms: null, carried_members: 0, continuity_refreshes: null,
    continuity_proof: null,
    generation: 1, coverage: "PARTIAL", values_as_of: "CHECKPOINT_CURRENT",
    bin_low: "84840", bin_high: "84845", bin_members: 1,
    bin_candidate_notional_usdt: "849701.01", bin_candidate_notional_is_lower_bound: true,
    persistence_is_sampled_span: true, order_identity_proven: false, ...extra,
  };
}

function candidate(side: Side, price: string, extra: Partial<WallRow> = {}): WallRow {
  return {
    side, price, qty_btc: "10.02", notional_usdt: "849701.01", distance: "40.5",
    distance_pct: "0.0477", distance_bps: "4.77", multiple: "14.6", local_average: "0.68",
    neighbours: 10, first_seen_ms: 1_000, observed_persistence_ms: 42_000,
    persistence_rule: "LATEST_SAMPLE_MS_MINUS_FIRST_SEEN_MS", row_persistence_ms: 0,
    row_samples: 1, coverage: "PARTIAL", generation: 1, continuity_status: "NEW",
    continuity_first_seen_ms: null, carried_persistence_ms: null, continuity_samples: 1,
    continuity_refreshes: 0, continuity_origin_generation: 1,
    persistence_is_sampled_span: true, order_identity_proven: false, ...extra,
  };
}

function continuityRule(): ContinuityView["rule"] {
  return {
    rule_version: "lm-continuity.v3",
    identity: { status: "PRESENT", rule_sha256: "d68a26ce", recorded_sha256: "d68a26ce",
                sha256_agrees: true },
    hard_carries_nothing: true,
    soft_requires_all_gates: ["VOLUNTARY", "CONTINUITY", "NEWER", "BOUNDED_WINDOW", "OVERLAP"],
    soft_window_max_ms: 300,
    wall_identity_rule: "AT_LEAST_ONE_CARRIED_MEMBER_IN_THE_SAME_V2_BIN",
    member_identity_rule: "SAME_SIDE_AND_EXACT_PRICE",
    changes_v2_thresholds: false, can_only_extend_a_span: true,
  };
}

function continuityView(overrides: Partial<ContinuityView> = {}): ContinuityView {
  return {
    available: true, unavailable_reason: null, rule: continuityRule(),
    note: "HARD는 이력을 종료합니다. SOFT는 증명된 후보만 이어 갑니다.",
    active_carried_in_view: 2, candidates_in_view: 272,
    proof: "SOFT_REFRESH_EXACT_PRICE_PRESENT_IN_INSTALLED_SNAPSHOT",
    window_max_ms: 300,
    gates_required: ["VOLUNTARY", "CONTINUITY", "NEWER", "BOUNDED_WINDOW", "OVERLAP"],
    active_carried: 2, soft_refreshes: 3, soft_window_exempt: 0, hard_transitions: 1,
    wall_carried_total: 57,
    wall_ended_total: 18, wall_unknown_total: 229, proofs_superseded: 0, pending_proof: null,
    last_transition: {
      refresh_type: "SOFT", continuity_reason: "SOFT_ALL_GATES_PASSED", cause: null,
      generation_from: 3, generation_to: 4, receive_ms: 1_000, candidates_before: 277,
      carried_entering: 240, carried_lost: 0, eligible: 263, wall_carried: 261,
      wall_ended: 14, wall_unknown: 2,
      carried_truncated: false,
      overlap_check: { passed: true, levels_compared: 1_912, levels_identical: 1_780 },
      gates: { VOLUNTARY: true, CONTINUITY: true, NEWER: true, BOUNDED_WINDOW: true,
               OVERLAP: true },
      window_ms: 130, refresh_trigger: "coverage_edge",
      basis: "REPLAYED_CHAIN_SAME_UPDATE_ID", replayed_frames: 2, chain_preserved: true,
      window_verdict: "WITHIN_MAX", window_exempt: false,
    },
    ...overrides,
  };
}

function sideView(side: Side, overrides: Partial<SideView> = {}): SideView {
  return {
    side, coverage: "COMPLETE", nearest_wall: wall(side, side === "ASK" ? "84841" : "84760"),
    nearest_unavailable_reason: null, candidates_total: 129, walls_selected: 14, walls_shown: 9,
    selection: { considered: 129, passed: 16, bins: 14, grouped_away: 2,
                 values_as_of: "CHECKPOINT_CURRENT",
                 rejected: { BELOW_MIN_NOTIONAL: 78, BELOW_MIN_MULTIPLE: 27,
                             INSIDE_MIN_DISTANCE: 6, BELOW_MIN_PERSISTENCE: 2,
                             FIELD_MISSING_OR_UNPARSEABLE: 0, NO_USABLE_MID: 0 } },
    walls: [wall(side, side === "ASK" ? "84841" : "84760")],
    depth: [band("0.1", "COMPLETE"), band("0.25", "PARTIAL"), band("0.5", "PARTIAL"),
            band("1", "PARTIAL")],
    ...overrides,
  };
}

function coverageView(overrides: Partial<CoverageView> = {}): CoverageView {
  return {
    observed_low_pct: "-0.1515", observed_high_pct: "0.1468", observed_symmetric_pct: "0.1468",
    known_low: "84672", known_high: "84924.9", snapshot_limit: 1000,
    bands: [
      { band_pct: "0.1", coverage: "COMPLETE", bid_coverage: "COMPLETE",
        ask_coverage: "COMPLETE", is_lower_bound: false, qty_bid: "248.609",
        qty_ask: "248.609", observed_qty_bid: "248.609", observed_qty_ask: "248.609",
        observed_notional_bid: "21093311.82", observed_notional_ask: "21093311.82" },
      ...["0.25", "0.5", "1"].map(label => ({
        band_pct: label, coverage: "PARTIAL" as Coverage, bid_coverage: "PARTIAL" as Coverage,
        ask_coverage: "PARTIAL" as Coverage, is_lower_bound: true, qty_bid: null, qty_ask: null,
        observed_qty_bid: "448.086", observed_qty_ask: "448.086",
        observed_notional_bid: "38029410.87", observed_notional_ask: "38029410.87" })),
    ],
    complete_bands: ["0.1"], partial_bands: ["0.25", "0.5", "1"],
    lower_bound_marker: "\u2265", lower_bounds_identical: true,
    unobserved_is_null_not_zero: true,
    observed_range_note: "이 구간 밖은 유동성이 0인 것이 아니라 보지 못한 것입니다",
    lower_bound_note: "PARTIAL 구간의 숫자는 관측된 하한값입니다",
    identical_bounds_note: "세 구간이 같은 레벨만 더하기 때문이며 값이 멈춘 것이 아닙니다",
    scope_note: "coverage는 스트림 관측 범위입니다",
    ...overrides,
  };
}

function resnapshotView(overrides: Partial<ResnapshotView> = {}): ResnapshotView {
  return {
    available: true, unavailable_reason: null, stale: false,
    policy: "FAULT_IMMEDIATE_PLUS_COVERAGE_EDGE_PLUS_HOURLY_SAFETY",
    fixed_interval_polling: false, protected_band_bps: "10", coverage_margin_bps: "4.44",
    coverage_trigger_bps: "1.0", coverage_cooldown_s: 300,
    coverage_cooldown_remaining_s: null, safety_refresh_s: 3600, snapshot_age_s: 1641.2,
    coverage_refreshes: 0, safety_refreshes: 0, refreshes_rejected: 0, resyncs: 1,
    generation: 1, pending_reason: null,
    install: "STAGED_BUFFER_AND_REPLAY_ATOMIC_SWAP", refreshes_applied: 0,
    refresh_failures_consecutive: 0, refresh_retry_backoff_s: 10, refresh_retry_in_s: null,
    refresh_deadline_ms: 1000, failed_refresh_consumes_cooldown: false,
    refresh_in_progress: null,
    cost_note: "a resnapshot increments the book generation",
    note: "재스냅샷은 고정 주기 폴링이 아닙니다. 재스냅샷은 generation을 올려 모든 wall 관측을 "
      + "UNKNOWN으로 끝내므로 빈도는 그 손실로 묶여 있습니다",
    ...overrides,
  };
}

function flowWindow(label: string, coverage: Coverage): FlowWindow {
  const canonical = coverage === "COMPLETE";
  const observed = coverage !== "UNKNOWN";
  return {
    window: label, coverage, coverage_reason: canonical ? null : "WARMUP",
    coverage_age_ms: 24_855, trades: observed ? 23 : null,
    buy_btc: canonical ? "2.487" : null, sell_btc: canonical ? "4.739" : null,
    buy_usdt: canonical ? "210897.6" : null, sell_usdt: canonical ? "401874.19" : null,
    net_btc: canonical ? "-2.252" : null, net_usdt: canonical ? "-190976.59" : null,
    observed_buy_btc: observed ? "2.563" : null, observed_sell_btc: observed ? "4.946" : null,
    observed_buy_usdt: observed ? "217342.53" : null,
    observed_sell_usdt: observed ? "419428.14" : null,
    is_lower_bound: coverage === "PARTIAL",
    imbalance_btc: canonical ? "-0.3116" : null, imbalance_usdt: canonical ? "-0.3116" : null,
  };
}

function snapshot(overrides: Partial<LiquiditySnapshot> = {}): LiquiditySnapshot {
  return {
    preview: { version: "liquidity-map.v1-preview.1", mode: "READ_ONLY_JOURNAL_VIEWER",
               scope: "표시 전용", sample_interval_s: 1 },
    source: { root: "/data/ms_v0", session_id: "5750e43b-5085", session_started_ms: 1,
              session_age_ms: 1_999,
              session_ended: false, collector_version: "btc-ms.collector.0.1",
              symbol: "BTCUSDT", exchange: "binance_usdm", seq: 14673, sample_index: 424,
              sample_receive_ms: 1_000, server_time_ms: 2_000, journal_age_ms: 1_216,
              wall_stream_lag_ms: 0, read_cost: { wall_bytes: 10471 },
              contract: { contract_version: "btc-ms.v0.1", sha256_agrees: true } },
    quality: { state: "LIVE", reasons: [], sample_is_current: true, book_state: "SYNCED",
               book_coverage: "COMPLETE",
               trade_state: "LIVE", depth_age_ms: 118, trade_age_ms: 546, journal_age_ms: 1_216,
               lag_ms: -17, lag_state: "COMPLETE", generation: 1, levels: 2001,
               last_invalidation: null, journal_stale_ms: 3_000, depth_stale_ms: 2_000,
               trade_stale_ms: 5_000 },
    price: { best_bid: "84800.4", best_ask: "84800.5", spread: "0.1", spread_bps: "0.01",
             mid: "84800.45", mid_rule: "BEST_BID_AND_BEST_ASK", mark: null,
             mark_unavailable_reason: "V0 계약에 mark price 출처가 없습니다",
             known_low: "84672", known_high: "84924.9", known_low_pct: "-0.1515",
             known_high_pct: "0.1468" },
    sides: { ASK: sideView("ASK"), BID: sideView("BID") },
    coverage: coverageView(),
    resnapshot: resnapshotView(),
    continuity: continuityView(),
    candidates: { ASK: [candidate("ASK", "84841")], BID: [candidate("BID", "84760")] },
    flow: { windows: { "5s": flowWindow("5s", "COMPLETE"), "15s": flowWindow("15s", "COMPLETE"),
                       "60s": flowWindow("60s", "PARTIAL") },
            aggressor_rule: "aggressor=SELL if buyer is maker (m=true), else BUY",
            clock: "LOCAL_MONOTONIC_RECEIPT",
            trade_stream: { connected: true, age_ms: 546, last_receive_ms: 1, stale_ms: 5_000 },
            imbalance_note: "방향 판정이 아닙니다" },
    walls: { coverage: "COMPLETE", verified_by: "COLLECTOR_STATE_CHECKPOINT",
             unverified_reason: null, source: "COLLECTOR_STATE_CHECKPOINT",
             values_as_of: "CHECKPOINT_CURRENT",
             candidate_count: 272, walls_selected: 28,
             rejected: { BELOW_MIN_NOTIONAL: 156, BELOW_MIN_MULTIPLE: 54,
                         INSIDE_MIN_DISTANCE: 12, BELOW_MIN_PERSISTENCE: 4,
                         FIELD_MISSING_OR_UNPARSEABLE: 0, NO_USABLE_MID: 0 },
             truncated: false, carried: 0, continuity_rule: continuityRule(),
             authority_active: 272, authority_age_ms: 5_216,
             reconstructed_at_authority: 272, missing_count: 0, scanned_bytes: 86_412,
             scanned_records: 272, tail_records: 0, tail_bytes: 0, tail_complete: true,
             transitions_applied: 0,
             state_file: { path: "/data/ms_v0/state/collector_state.json", present: true,
                           usable: true, unusable_reason: null,
                           state_version: "ms-v0-state.v1-1", written_ms: 1_000, age_ms: 420,
                           stale_ms: 3_000, bytes: 86_412, seq: 14_673, active_count: 272,
                           items: 272, truncated: false, is_authority: false,
                           authority: "JOURNAL" },
             state_file_note: "저널은 여전히 유일한 재생 권위입니다",
             filter: { min_notional_usdt: "500000", is_display_filter_not_rule: true,
                       applies_after: "lm-wall.v2" },
             rule: { rule_version: "lm-wall.v2",
                     identity: { rule_version: "lm-wall.v2", status: "PRESENT",
                                 rule_sha256: "deaa9db8", recorded_sha256: "deaa9db8",
                                 sha256_agrees: true },
                     is_frozen_not_tunable: true, changes_data_contract: false,
                     min_notional_usdt: "250000", min_multiple: "5", min_distance_bps: "1.0",
                     min_persistence_ms: 10_000, bin_width_usdt: "5", bin_width_ticks: 50,
                     bin_rule: "FLOOR_ABSOLUTE_PRICE_PER_SIDE",
                     bin_representative: "LARGEST_NOTIONAL_MEMBER",
                     evaluation_order: "FILTER_MEMBERS_THEN_GROUP",
                     v0_rule: { min_multiple: "3", min_neighbours: 3, neighbours_per_side: 5,
                                band_pct: "1", bin_rule: "EXACT_PRICE" } },
             rule_note: "동결된 lm-wall.v2 규칙입니다",
             v0_rule_note: "coverage는 PARTIAL로 기록됩니다",
             no_verdict_note: "spoofing 판정이 아닙니다" },
    overlay: { renderable: true, suppressed_reason: null, mid: "84800.45", axis_low: "84672",
               axis_high: "84924.9", axis_rule: "SNAPSHOT_KNOWN_INTERVAL",
               best_bid: "84800.4", best_ask: "84800.5",
               sell_wall: { price: "84841", qty_btc: "10.02", notional_usdt: "849701.01",
                            distance_bps: "4.77", bin_low: "84840", bin_high: "84845",
                            bin_members: 1, coverage: "PARTIAL" },
               buy_wall: { price: "84760", qty_btc: "11.008", notional_usdt: "933045.78",
                           distance_bps: "4.77", bin_low: "84760", bin_high: "84765",
                           bin_members: 1, coverage: "PARTIAL" },
               walls_suppressed_reason: null },
    telemetry: [{ seq: 14_670, receive_ms: 1, event: "resync", stream: "depth", reason: null }],
    ...overrides,
  };
}

// --------------------------------------------------------------------------- formatting

describe("liquidity map formatting", () => {
  it("renders a missing value as a dash and never as zero", () => {
    expect(btc(null)).toBe("-");
    expect(usdt(null)).toBe("-");
    expect(pct(null)).toBe("-");
    expect(duration(null)).toBe("-");
  });

  it("keeps a real zero as a zero", () => {
    expect(btc("0")).toBe("0.000 BTC");
    expect(usdt("0")).toBe("0 USDT");
  });

  it("attaches the inequality to a lower bound and never to a missing value", () => {
    expect(lowerBound("448.086 BTC", true)).toBe("\u2265 448.086 BTC");
    expect(lowerBound("448.086 BTC", false)).toBe("448.086 BTC");
    // A dash is not a bound on anything, so it must not acquire one.
    expect(lowerBound("-", true)).toBe("-");
  });

  it("refuses to place a price the axis does not cover", () => {
    expect(axisPosition("84800", "84672", "84924.9")).toBeCloseTo(49.37, 1);
    expect(axisPosition("84000", "84672", "84924.9")).toBeNull();
    expect(axisPosition("85500", "84672", "84924.9")).toBeNull();
    expect(axisPosition("84800", null, "84924.9")).toBeNull();
    expect(axisPosition("84800", "84924.9", "84924.9")).toBeNull();
  });
});

// --------------------------------------------------------------------------- quality

describe("quality strip", () => {
  it("shows LIVE with no reasons on a fresh sample", () => {
    render(<QualityStrip snapshot={snapshot()} error={null} polls={12} />);
    const strip = screen.getByTestId("lm-quality");
    expect(strip).toHaveTextContent("LIVE");
    expect(screen.queryByTestId("lm-quality-reasons")).not.toBeInTheDocument();
    expect(screen.getByTestId("lm-poll-count")).toHaveTextContent("1초 폴링 · 12회");
  });

  it("names every reason the screen is not LIVE", () => {
    const stale = snapshot();
    stale.quality = { ...stale.quality, state: "STALE",
                      reasons: ["JOURNAL_STALE", "SESSION_ENDED"] };
    render(<QualityStrip snapshot={stale} error={null} polls={1} />);
    const reasons = screen.getByTestId("lm-quality-reasons");
    expect(reasons).toHaveTextContent("저널이 갱신되지 않음");
    expect(reasons).toHaveTextContent("collector 세션 종료됨");
  });

  it("shows an untranslated reason code rather than swallowing it", () => {
    const odd = snapshot();
    odd.quality = { ...odd.quality, state: "STALE", reasons: ["SOMETHING_NEW"] };
    render(<QualityStrip snapshot={odd} error={null} polls={1} />);
    expect(screen.getByTestId("lm-quality-reasons")).toHaveTextContent("SOMETHING_NEW");
  });

  it("shows SYNCING separately from STALE", () => {
    const syncing = snapshot();
    syncing.quality = { ...syncing.quality, state: "SYNCING", reasons: ["BOOK_UNSYNCED"] };
    render(<QualityStrip snapshot={syncing} error={null} polls={1} />);
    expect(screen.getByTestId("lm-quality")).toHaveTextContent("SYNCING");
  });

  it("shows a poll failure beside the age of what is still on screen", () => {
    render(<QualityStrip snapshot={snapshot()} error="Preview API에 연결할 수 없습니다." polls={3} />);
    expect(screen.getByTestId("lm-error")).toHaveTextContent("연결할 수 없습니다");
    // The figures stay, labelled with their age, because "how old" is the operator's question.
    expect(screen.getByTestId("lm-quality")).toHaveTextContent("1.2s");
  });

  it("renders NO DATA without a snapshot instead of crashing", () => {
    render(<QualityStrip snapshot={null} error={null} polls={0} />);
    expect(screen.getByTestId("lm-quality")).toHaveTextContent("NO DATA");
  });
});

// --------------------------------------------------------------------------- price

describe("price card", () => {
  it("shows bid, ask, spread and mid", () => {
    render(<PriceCard snapshot={snapshot()} />);
    const card = screen.getByTestId("lm-price");
    expect(card).toHaveTextContent("84,800.4");
    expect(card).toHaveTextContent("84,800.5");
    expect(card).toHaveTextContent("0.01 bp");
    expect(card).toHaveTextContent("84,800.45");
  });

  it("shows mark as unavailable with the reason rather than substituting mid", () => {
    render(<PriceCard snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-mark")).toHaveTextContent("-");
    expect(screen.getByTestId("lm-mark-note")).toHaveTextContent("mark price");
  });

  it("shows the known interval as the reach of the data", () => {
    render(<PriceCard snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-price")).toHaveTextContent("-0.152% ~ 0.147%");
  });
});

// --------------------------------------------------------------------------- depth and walls

describe("side panels", () => {
  it("shows the canonical quantity for the COMPLETE band", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />);
    const row = screen.getByTestId("lm-depth-0.1");
    expect(row).toHaveTextContent("COMPLETE");
    expect(row).toHaveTextContent("248.609");
    expect(row).not.toHaveTextContent("\u2265");
  });

  it("labels the three wider bands as a lower bound and hides the canonical figure", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />);
    for (const label of ["0.25", "0.5", "1"]) {
      const row = screen.getByTestId(`lm-depth-${label}`);
      expect(row).toHaveTextContent("PARTIAL");
      // The marker travels with the value, so a number read out of context stays a lower bound.
      expect(row).toHaveTextContent("\u2265 448.086");
      expect(row).not.toHaveTextContent("248.609");
    }
  });

  it("shows a dash and not a zero for an unobserved band", () => {
    const unknown = sideView("BID", { depth: [band("0.1", "UNKNOWN")] });
    render(<SidePanel side="BID" view={unknown} wallCoverage="UNKNOWN" />);
    const row = screen.getByTestId("lm-depth-0.1");
    expect(row).toHaveTextContent("UNKNOWN");
    expect(row).toHaveTextContent("-");
    expect(row).not.toHaveTextContent("0.000");
  });

  it("shows the nearest wall with its distance, size and observed span", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />);
    const nearest = screen.getByTestId("lm-nearest-ASK");
    expect(nearest).toHaveTextContent("84,841.0");
    expect(nearest).toHaveTextContent("4.77 bp");
    expect(nearest).toHaveTextContent("10.020 BTC");
    expect(nearest).toHaveTextContent("849,701 USDT");
    expect(nearest).toHaveTextContent("42.0s");
    expect(nearest).toHaveTextContent("14.6x");
  });

  it("withholds the nearest wall when the candidate set is not proven", () => {
    const unproven = sideView("ASK", { nearest_wall: null, coverage: "PARTIAL",
      nearest_unavailable_reason: "wall 후보 집합을 완전하다고 증명하지 못했습니다" });
    render(<SidePanel side="ASK" view={unproven} wallCoverage="PARTIAL" />);
    expect(screen.getByTestId("lm-nearest-ASK-missing"))
      .toHaveTextContent("완전하다고 증명하지 못했습니다");
    // The observed candidates are still listed; only the claim "nearest" is withheld.
    expect(screen.getAllByTestId("lm-wall-row")).toHaveLength(1);
  });

  it("says there is no wall rather than showing an empty list", () => {
    const empty = sideView("BID", { nearest_wall: null, walls: [], candidates_total: 0,
      walls_selected: 0, walls_shown: 0, nearest_unavailable_reason: null });
    render(<SidePanel side="BID" view={empty} wallCoverage="COMPLETE" />);
    expect(screen.getByTestId("lm-wall-empty-BID")).toHaveTextContent("V0 후보 0개");
  });

  it("publishes all three counts, so a high zoom cannot read as an empty market", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />);
    expect(screen.getByTestId("lm-wall-count-ASK"))
      .toHaveTextContent("9 / 규칙 통과 14 / V0 후보 129");
  });

  it("says on what ground the rule refused the rest", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />);
    const note = screen.getByTestId("lm-reject-ASK");
    expect(note).toHaveTextContent("금액 미달 78");
    expect(note).toHaveTextContent("mid 1bp 안쪽 6");
    // A rule that refused nothing on a ground must not print a zero beside it.
    expect(note).not.toHaveTextContent("mid 없음");
  });

  it("marks a wall that is a group of adjacent levels", () => {
    const grouped = sideView("ASK", { walls: [wall("ASK", "84841", { bin_members: 4,
      bin_candidate_notional_usdt: "2100000" })] });
    render(<SidePanel side="ASK" view={grouped} wallCoverage="COMPLETE" />);
    expect(screen.getByTestId("lm-wall-bin")).toHaveTextContent("4레벨");
  });

  it("does not mark a wall that is a single level", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />);
    expect(screen.queryByTestId("lm-wall-bin")).not.toBeInTheDocument();
  });

  it("renders without a side payload at all", () => {
    render(<SidePanel side="ASK" view={undefined} wallCoverage="UNKNOWN" />);
    expect(screen.getByTestId("lm-side-ASK")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- flow

describe("flow panel", () => {
  it("shows all three windows with their own coverage", () => {
    render(<FlowPanel snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-flow-5s")).toHaveTextContent("COMPLETE");
    expect(screen.getByTestId("lm-flow-60s")).toHaveTextContent("PARTIAL");
  });

  it("shows aggressive buy and sell volume for a COMPLETE window", () => {
    render(<FlowPanel snapshot={snapshot()} />);
    const row = screen.getByTestId("lm-flow-5s");
    expect(row).toHaveTextContent("2.487 BTC");
    expect(row).toHaveTextContent("4.739 BTC");
    expect(row).toHaveTextContent("-0.3116");
  });

  it("shows the observed lower bound and no net or imbalance for a PARTIAL window", () => {
    render(<FlowPanel snapshot={snapshot()} />);
    const row = screen.getByTestId("lm-flow-60s");
    expect(row).toHaveTextContent("2.563 BTC");
    expect(row).not.toHaveTextContent("-2.252");
    expect(within(row).getAllByText("-").length).toBeGreaterThan(0);
    expect(screen.getByTestId("lm-flow-freshness")).toHaveTextContent("하한 관측값");
  });

  it("marks the trade stream stale when the backend says it has gone silent", () => {
    const silent = snapshot();
    silent.quality = { ...silent.quality, trade_state: "STALE" };
    silent.flow = { ...silent.flow,
      trade_stream: { connected: true, age_ms: 9_000, last_receive_ms: 1, stale_ms: 5_000 } };
    render(<FlowPanel snapshot={silent} />);
    expect(screen.getByTestId("lm-flow")).toHaveTextContent("STALE");
    expect(screen.getByTestId("lm-flow-freshness")).toHaveTextContent("9.0s");
  });

  it("marks the trade stream disconnected", () => {
    const down = snapshot();
    down.quality = { ...down.quality, trade_state: "STALE" };
    down.flow = { ...down.flow,
      trade_stream: { connected: false, age_ms: null, last_receive_ms: null, stale_ms: 5_000 } };
    render(<FlowPanel snapshot={down} />);
    expect(screen.getByTestId("lm-flow")).toHaveTextContent("DISCONNECTED");
  });

  it("shows a genuinely quiet market as zero volume with no imbalance", () => {
    const quiet = snapshot();
    const zero: FlowWindow = { ...flowWindow("5s", "COMPLETE"), trades: 0, buy_btc: "0",
      sell_btc: "0", net_btc: "0", imbalance_btc: null, imbalance_usdt: null };
    quiet.flow = { ...quiet.flow, windows: { "5s": zero, "15s": zero, "60s": zero } };
    render(<FlowPanel snapshot={quiet} />);
    const row = screen.getByTestId("lm-flow-5s");
    expect(row).toHaveTextContent("0.000 BTC");
    expect(within(row).getAllByText("-").length).toBe(1);
  });
});

// --------------------------------------------------------------------------- overlay

describe("chart overlay", () => {
  it("draws the current price and one wall line per side", () => {
    render(<LadderOverlay snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-overlay-mid-line")).toHaveTextContent("84,800.45");
    expect(screen.getByTestId("lm-overlay-sell-line")).toHaveTextContent("84,841.0");
    expect(screen.getByTestId("lm-overlay-buy-line")).toHaveTextContent("84,760.0");
    expect(screen.getByTestId("lm-overlay-complete-band")).toHaveTextContent("±0.1% COMPLETE");
  });

  it("places each line inside the axis", () => {
    render(<LadderOverlay snapshot={snapshot()} />);
    for (const id of ["lm-overlay-mid-line", "lm-overlay-sell-line", "lm-overlay-buy-line"]) {
      const top = Number.parseFloat(screen.getByTestId(id).style.top);
      expect(top).toBeGreaterThanOrEqual(0);
      expect(top).toBeLessThanOrEqual(100);
    }
    // A sell wall above mid sits higher on the axis than mid does.
    expect(Number.parseFloat(screen.getByTestId("lm-overlay-sell-line").style.top))
      .toBeLessThan(Number.parseFloat(screen.getByTestId("lm-overlay-mid-line").style.top));
  });

  it("draws nothing when the backend says the coordinates are not trustworthy", () => {
    const suppressed = snapshot();
    suppressed.overlay = { ...suppressed.overlay, renderable: false,
      suppressed_reason: "BOOK_UNSYNCED", sell_wall: null, buy_wall: null };
    render(<LadderOverlay snapshot={suppressed} />);
    expect(screen.getByTestId("lm-overlay-suppressed")).toHaveTextContent("책 재동기화 중");
    expect(screen.queryByTestId("lm-overlay-mid-line")).not.toBeInTheDocument();
    expect(screen.queryByTestId("lm-overlay-canvas")).not.toBeInTheDocument();
  });

  it("keeps the price line but drops the wall lines when the candidate set is unproven", () => {
    const partial = snapshot();
    partial.overlay = { ...partial.overlay, sell_wall: null, buy_wall: null,
      walls_suppressed_reason: "wall 후보 집합을 완전하다고 증명하지 못했습니다" };
    render(<LadderOverlay snapshot={partial} />);
    expect(screen.getByTestId("lm-overlay-mid-line")).toBeInTheDocument();
    expect(screen.queryByTestId("lm-overlay-sell-line")).not.toBeInTheDocument();
    expect(screen.getByTestId("lm-overlay-walls-suppressed"))
      .toHaveTextContent("증명하지 못했습니다");
  });

  it("drops a wall line the axis cannot place rather than clamping it to the edge", () => {
    const outside = snapshot();
    outside.overlay = { ...outside.overlay,
      sell_wall: { price: "99999", qty_btc: "1", notional_usdt: "1", distance_bps: "1",
                   bin_low: "99995", bin_high: "100000", bin_members: 1,
                   coverage: "PARTIAL" } };
    render(<LadderOverlay snapshot={outside} />);
    expect(screen.queryByTestId("lm-overlay-sell-line")).not.toBeInTheDocument();
    expect(screen.getByTestId("lm-overlay-buy-line")).toBeInTheDocument();
  });

  it("renders the suppressed state without a snapshot", () => {
    render(<LadderOverlay snapshot={null} />);
    expect(screen.getByTestId("lm-overlay-suppressed")).toHaveTextContent("데이터 없음");
  });
});

// --------------------------------------------------------------------------- wall set

describe("wall set panel", () => {
  it("shows the candidate count beside what the rule selected", () => {
    render(<WallSetPanel snapshot={snapshot()} query={{ minNotionalUsdt: "500000" }}
      onQuery={() => {}} />);
    const panel = screen.getByTestId("lm-wallset");
    expect(panel).toHaveTextContent("272");
    expect(panel).toHaveTextContent("28");
    expect(panel).toHaveTextContent("COMPLETE");
    expect(screen.queryByTestId("lm-wallset-partial")).not.toBeInTheDocument();
  });

  it("states every frozen threshold of the rule", () => {
    render(<WallSetPanel snapshot={snapshot()} query={{}} onQuery={() => {}} />);
    const rule = screen.getByTestId("lm-rule");
    expect(rule).toHaveTextContent("250,000 USDT");
    expect(rule).toHaveTextContent("5.0x");
    expect(rule).toHaveTextContent("1.00 bp");
    expect(rule).toHaveTextContent("10.0s");
    expect(rule).toHaveTextContent("5 USDT");
    expect(rule).toHaveTextContent("50틱");
  });

  it("says the rule is frozen and changes no data contract", () => {
    render(<WallSetPanel snapshot={snapshot()} query={{}} onQuery={() => {}} />);
    expect(screen.getByTestId("lm-rule-frozen")).toHaveTextContent("화면에서 바꿀 수 없습니다");
    expect(screen.getByTestId("lm-wallset")).toHaveTextContent("lm-wall.v2");
  });

  it("says loudly when the frozen document no longer matches its hash", () => {
    const drifted = snapshot();
    drifted.walls = { ...drifted.walls, rule: { ...drifted.walls.rule,
      identity: { ...drifted.walls.rule.identity, sha256_agrees: false } } };
    render(<WallSetPanel snapshot={drifted} query={{}} onQuery={() => {}} />);
    expect(screen.getByTestId("lm-rule-frozen")).toHaveTextContent("기록된 해시와 다릅니다");
  });

  it("says the set came from the collector checkpoint and what the poll cost", () => {
    render(<WallSetPanel snapshot={snapshot()} query={{}} onQuery={() => {}} />);
    const panel = screen.getByTestId("lm-wallset");
    expect(panel).toHaveTextContent("collector 상태 체크포인트");
    expect(panel).toHaveTextContent("현재 샘플 값");
    expect(screen.getByTestId("lm-state-file")).toHaveTextContent("권위는 JOURNAL");
    expect(screen.getByTestId("lm-state-file")).toHaveTextContent("상태 파일 사용");
  });

  it("says why the checkpoint was not used and that the fallback ran", () => {
    const fallback = snapshot();
    fallback.walls = { ...fallback.walls, source: "JOURNAL_RECONSTRUCTION",
      values_as_of: "JOURNAL_OPEN_ROW", verified_by: "STREAM_START",
      state_file: { ...fallback.walls.state_file!, usable: false,
                    unusable_reason: "STATE_FILE_STALE" } };
    render(<WallSetPanel snapshot={fallback} query={{}} onQuery={() => {}} />);
    const panel = screen.getByTestId("lm-wallset");
    expect(panel).toHaveTextContent("저널 역방향 복원");
    expect(panel).toHaveTextContent("후보 개시 시점 값");
    expect(screen.getByTestId("lm-state-file")).toHaveTextContent("상태 파일이 갱신되지 않음");
  });

  it("says when the collector cut its own active list", () => {
    const cut = snapshot();
    cut.walls = { ...cut.walls, truncated: true };
    render(<WallSetPanel snapshot={cut} query={{}} onQuery={() => {}} />);
    expect(screen.getByTestId("lm-wallset")).toHaveTextContent("일부 생략");
  });

  it("says when the tail after the checkpoint could not be finished", () => {
    const partial = snapshot();
    partial.walls = { ...partial.walls, tail_complete: false };
    render(<WallSetPanel snapshot={partial} query={{}} onQuery={() => {}} />);
    expect(screen.getByTestId("lm-tail-incomplete")).toBeInTheDocument();
  });

  it("warns loudly when the set could not be proven and says how many are missing", () => {
    const partial = snapshot();
    partial.walls = { ...partial.walls, coverage: "PARTIAL", verified_by: null,
      unverified_reason: "SCAN_BUDGET_EXHAUSTED", missing_count: 7,
      reconstructed_at_authority: 265 };
    render(<WallSetPanel snapshot={partial} query={{}} onQuery={() => {}} />);
    expect(screen.getByTestId("lm-wallset-partial")).toHaveTextContent("표시하지 않습니다");
    expect(screen.getByTestId("lm-wallset")).toHaveTextContent("7");
  });

  it("applies a typed threshold on the button", () => {
    const onQuery = vi.fn();
    render(<WallSetPanel snapshot={snapshot()} query={{ minNotionalUsdt: "500000" }}
      onQuery={onQuery} />);
    fireEvent.change(screen.getByTestId("lm-min-notional"), { target: { value: "750000" } });
    fireEvent.click(screen.getByTestId("lm-filter-apply"));
    expect(onQuery).toHaveBeenCalledWith({ minNotionalUsdt: "750000" });
  });

  it("applies a typed threshold on Enter", () => {
    const onQuery = vi.fn();
    render(<WallSetPanel snapshot={snapshot()} query={{ minNotionalUsdt: "500000" }}
      onQuery={onQuery} />);
    const input = screen.getByTestId("lm-min-notional");
    fireEvent.change(input, { target: { value: "300000" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onQuery).toHaveBeenCalledWith({ minNotionalUsdt: "300000" });
  });

  it("refuses anything but digits in the threshold", () => {
    render(<WallSetPanel snapshot={snapshot()} query={{ minNotionalUsdt: "500000" }}
      onQuery={() => {}} />);
    const input = screen.getByTestId("lm-min-notional") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "-5e9abc" } });
    expect(input.value).toBe("59");
  });

  it("marks the active preset and says the filter does not move the rule", () => {
    render(<WallSetPanel snapshot={snapshot()} query={{ minNotionalUsdt: "500000" }}
      onQuery={() => {}} />);
    expect(screen.getByRole("button", { name: "500,000", pressed: true })).toBeInTheDocument();
    expect(screen.getByTestId("lm-wallset")).toHaveTextContent("규칙은 바꾸지 않습니다");
  });

  it("keeps the default zoom at the measured 500,000 USDT", () => {
    render(<WallSetPanel snapshot={snapshot()} query={{}} onQuery={() => {}} />);
    expect((screen.getByTestId("lm-min-notional") as HTMLInputElement).value).toBe("500000");
  });
});

// --------------------------------------------------------------------------- phone layout

describe("390px layout", () => {
  function widths(container: HTMLElement) {
    // jsdom does not lay out, so the check is structural: every element wide enough to overflow a
    // phone must be inside a scroll container, and nothing may carry a fixed width past 390px.
    return Array.from(container.querySelectorAll<HTMLElement>("*")).filter(node => {
      const width = node.style.width || node.style.minWidth;
      if (!width.endsWith("px")) return false;
      return Number.parseFloat(width) > PHONE_WIDTH;
    });
  }

  it("puts every wide table in a horizontal scroll container", () => {
    const { container } = render(<>
      <SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />
      <CoveragePanel snapshot={snapshot()} />
      <FlowPanel snapshot={snapshot()} />
    </>);
    const tables = container.querySelectorAll("table");
    expect(tables.length).toBe(3);
    tables.forEach(table => {
      expect(table.parentElement).toHaveClass("overflow-x-auto");
    });
  });

  it("pins no element to a pixel width wider than the phone", () => {
    const { container } = render(<>
      <QualityStrip snapshot={snapshot()} error={null} polls={1} />
      <PriceCard snapshot={snapshot()} />
      <LadderOverlay snapshot={snapshot()} />
      <SidePanel side="BID" view={sideView("BID")} wallCoverage="COMPLETE" />
      <CoveragePanel snapshot={snapshot()} />
      <ResnapshotPanel snapshot={snapshot()} />
      <ContinuityPanel snapshot={snapshot()} />
      <FlowPanel snapshot={snapshot()} />
      <WallSetPanel snapshot={snapshot()} query={{}} onQuery={() => {}} />
    </>);
    expect(widths(container)).toEqual([]);
  });

  it("wraps the chip rows and the candidate rows instead of pushing them sideways", () => {
    render(<>
      <QualityStrip snapshot={snapshot()} error={null} polls={1} />
      <SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />
    </>);
    expect(screen.getByTestId("lm-quality").firstElementChild).toHaveClass("flex-wrap");
    expect(screen.getAllByTestId("lm-wall-row")[0]).toHaveClass("flex-wrap");
  });

  it("keeps long values truncated rather than widening their column", () => {
    const { container } = render(<PriceCard snapshot={snapshot()} />);
    expect(container.querySelectorAll(".truncate").length).toBeGreaterThan(0);
  });
});

describe("coverage panel", () => {
  it("states the observed reach as a headline figure", () => {
    render(<CoveragePanel snapshot={snapshot()} />);
    const panel = screen.getByTestId("lm-coverage");
    expect(panel).toHaveTextContent("±0.147%");
    expect(panel).toHaveTextContent("-0.152% ~ 0.147%");
    expect(panel).toHaveTextContent("limit=1000");
  });

  it("separates the band that can be complete from the three that cannot", () => {
    render(<CoveragePanel snapshot={snapshot()} />);
    const panel = screen.getByTestId("lm-coverage");
    expect(panel).toHaveTextContent("±0.1%");
    expect(panel).toHaveTextContent("±0.25% ±0.5% ±1%");
  });

  it("prints every partial value with its inequality and the complete one without", () => {
    render(<CoveragePanel snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-coverage-0.1")).not.toHaveTextContent("\u2265");
    for (const label of ["0.25", "0.5", "1"]) {
      expect(screen.getByTestId(`lm-coverage-${label}`))
        .toHaveTextContent("\u2265 38,029,411");
    }
  });

  it("explains why the three wider bands print the same lower bound", () => {
    render(<CoveragePanel snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-coverage-identical"))
      .toHaveTextContent("값이 멈춘 것이 아닙니다");
  });

  it("says nothing when the lower bounds actually differ", () => {
    const varied = snapshot();
    varied.coverage = coverageView({ lower_bounds_identical: false });
    render(<CoveragePanel snapshot={varied} />);
    expect(screen.queryByTestId("lm-coverage-identical")).not.toBeInTheDocument();
  });

  it("says the unobserved region is not zero liquidity", () => {
    render(<CoveragePanel snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-coverage-null-note"))
      .toHaveTextContent("유동성 0이 아니라 미관측");
  });

  it("shows a dash rather than a zero for an unobserved band", () => {
    const unknown = snapshot();
    unknown.coverage = coverageView({ bands: [{ band_pct: "0.1", coverage: "UNKNOWN",
      bid_coverage: "UNKNOWN", ask_coverage: "UNKNOWN", is_lower_bound: false, qty_bid: null,
      qty_ask: null, observed_qty_bid: null, observed_qty_ask: null,
      observed_notional_bid: null, observed_notional_ask: null }],
      complete_bands: [], partial_bands: [], observed_symmetric_pct: null });
    render(<CoveragePanel snapshot={unknown} />);
    const row = screen.getByTestId("lm-coverage-0.1");
    expect(row).toHaveTextContent("UNKNOWN");
    // Both value cells are a dash. An unobserved band is not a band holding nothing.
    const cells = Array.from(row.querySelectorAll("td")).slice(1);
    expect(cells.map(cell => cell.textContent)).toEqual(["-", "-"]);
  });

  it("renders without a snapshot", () => {
    render(<CoveragePanel snapshot={null} />);
    expect(screen.getByTestId("lm-coverage")).toBeInTheDocument();
  });
});

describe("resnapshot panel", () => {
  it("says there is no fixed interval polling and shows the remaining margin", () => {
    render(<ResnapshotPanel snapshot={snapshot()} />);
    const panel = screen.getByTestId("lm-resnapshot");
    expect(panel).toHaveTextContent("고정 폴링 없음");
    expect(panel).toHaveTextContent("4.44 bp");
    expect(panel).toHaveTextContent("1.00 bp");
  });

  it("states what a resnapshot costs rather than only when one happens", () => {
    render(<ResnapshotPanel snapshot={snapshot()} />);
    expect(screen.getByTestId("lm-resnapshot")).toHaveTextContent("UNKNOWN으로 끝");
    // The collector's own English sentence says the same thing and is in the payload; the
    // screen prints one of the two rather than both.
    expect(screen.getByTestId("lm-resnapshot")).not.toHaveTextContent("resnapshot increments");
  });

  it("warns when the margin has nearly run out", () => {
    const tight = snapshot();
    tight.resnapshot = resnapshotView({ coverage_margin_bps: "1.20" });
    render(<ResnapshotPanel snapshot={tight} />);
    expect(screen.getByTestId("lm-resnapshot-tight")).toHaveTextContent("UNKNOWN으로 끝납니다");
  });

  it("does not warn while there is room", () => {
    render(<ResnapshotPanel snapshot={snapshot()} />);
    expect(screen.queryByTestId("lm-resnapshot-tight")).not.toBeInTheDocument();
  });

  it("shows a pending request with its reason", () => {
    const pending = snapshot();
    pending.resnapshot = resnapshotView({ pending_reason: "coverage_edge" });
    render(<ResnapshotPanel snapshot={pending} />);
    expect(screen.getByTestId("lm-resnapshot-pending")).toHaveTextContent("coverage_edge");
  });

  it("shows the cooldown remaining after a coverage refresh", () => {
    const cooling = snapshot();
    cooling.resnapshot = resnapshotView({ coverage_refreshes: 2,
      coverage_cooldown_remaining_s: 120 });
    render(<ResnapshotPanel snapshot={cooling} />);
    expect(screen.getByTestId("lm-resnapshot")).toHaveTextContent("쿨다운 2m 0s 남음");
  });

  it("says the figures are from the last state file when it stopped moving", () => {
    const stale = snapshot();
    stale.resnapshot = resnapshotView({ stale: true });
    render(<ResnapshotPanel snapshot={stale} />);
    expect(screen.getByTestId("lm-resnapshot-stale")).toBeInTheDocument();
  });

  it("says why the policy is unavailable rather than inventing one", () => {
    const none = snapshot();
    none.resnapshot = { available: false, unavailable_reason: "STATE_FILE_ABSENT",
      note: "재스냅샷은 고정 주기 폴링이 아닙니다" };
    render(<ResnapshotPanel snapshot={none} />);
    expect(screen.getByTestId("lm-resnapshot-missing"))
      .toHaveTextContent("collector 상태 파일 없음");
  });
});

describe("value truncation", () => {
  it("never cuts the known interval, because half a range is not a reading", () => {
    render(<PriceCard snapshot={snapshot()} />);
    const card = screen.getByTestId("lm-price");
    const range = within(card).getByText("84,672.0 ~ 84,924.9");
    expect(range).toHaveClass("break-words");
    expect(range).not.toHaveClass("truncate");
  });

  it("truncates the single-number fields so one long value cannot widen its column", () => {
    render(<PriceCard snapshot={snapshot()} />);
    const card = screen.getByTestId("lm-price");
    expect(within(card).getByText("84,800.45")).toHaveClass("truncate");
  });
});

describe("a sample that is no longer current", () => {
  function notCurrent() {
    const stale = snapshot();
    stale.quality = { ...stale.quality, state: "STALE", sample_is_current: false,
      trade_state: "STALE", reasons: ["SESSION_ENDED", "SAMPLE_NOT_CURRENT"] };
    return stale;
  }

  it("labels the book and trade chips as a reading from the last sample", () => {
    render(<QualityStrip snapshot={notCurrent()} error={null} polls={1} />);
    const strip = screen.getByTestId("lm-quality");
    expect(strip).toHaveTextContent("책 SYNCED · 마지막 샘플");
    expect(strip).toHaveTextContent("거래 STALE · 마지막 샘플");
    expect(strip).not.toHaveTextContent("거래 LIVE");
  });

  it("does not let the flow panel re-derive a live badge from the stale sample", () => {
    render(<FlowPanel snapshot={notCurrent()} />);
    // The sample's own trade age is 546 ms, well inside the threshold; the badge must still not
    // say LIVE, because that age was measured when the sample was written.
    expect(screen.getByTestId("lm-flow")).toHaveTextContent("STALE");
    expect(screen.getByTestId("lm-flow")).not.toHaveTextContent("LIVE");
  });

  it("translates every code in a joined suppression reason", () => {
    const stale = notCurrent();
    stale.overlay = { ...stale.overlay, renderable: false,
      suppressed_reason: "SESSION_ENDED|JOURNAL_STALE", sell_wall: null, buy_wall: null };
    render(<LadderOverlay snapshot={stale} />);
    const note = screen.getByTestId("lm-overlay-suppressed");
    expect(note).toHaveTextContent("collector 세션 종료됨 · 저널이 갱신되지 않음");
    expect(note).not.toHaveTextContent("SESSION_ENDED");
  });

  it("still shows an untranslated code in a joined reason", () => {
    const stale = notCurrent();
    stale.overlay = { ...stale.overlay, renderable: false,
      suppressed_reason: "JOURNAL_STALE|BRAND_NEW_CODE", sell_wall: null, buy_wall: null };
    render(<LadderOverlay snapshot={stale} />);
    expect(screen.getByTestId("lm-overlay-suppressed")).toHaveTextContent("BRAND_NEW_CODE");
  });
});

describe("session age", () => {
  it("says the observed span cannot exceed the session age", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE"
      sessionAgeMs={45_000} />);
    expect(screen.getByTestId("lm-session-cap-ASK")).toHaveTextContent("45.0s");
  });

  it("says nothing about the session when there is no session", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE"
      sessionAgeMs={null} />);
    expect(screen.queryByTestId("lm-session-cap-ASK")).not.toBeInTheDocument();
  });
});


// --------------------------------------------------------------------------- continuity

describe("continuity panel", () => {
  it("draws the three counts together, never the carries alone", () => {
    render(<ContinuityPanel snapshot={snapshot()} />);
    const counts = screen.getByTestId("lm-continuity-counts");
    expect(counts).toHaveTextContent("이어받음");
    expect(counts).toHaveTextContent("261");
    expect(counts).toHaveTextContent("종료 (관측됨)");
    expect(counts).toHaveTextContent("14");
    expect(counts).toHaveTextContent("UNKNOWN (미관측)");
    expect(counts).toHaveTextContent("전환 전 후보");
    expect(counts).toHaveTextContent("277");
  });

  it("states the counts of the last transition so they can be added up on screen", () => {
    render(<ContinuityPanel snapshot={snapshot()} />);
    const last = snapshot().continuity.last_transition!;
    expect((last.wall_carried ?? 0) + (last.wall_ended ?? 0) + (last.wall_unknown ?? 0))
      .toBe(last.candidates_before);
  });

  it("draws the five gates as gates, in the frozen rule's order", () => {
    render(<ContinuityPanel snapshot={snapshot()} />);
    const gates = screen.getByTestId("lm-continuity-gates");
    const order = ["자발 갱신", "스트림 연속", "스냅샷 최신", "REST 창 상한", "구간 겹침"];
    order.forEach(label => expect(gates).toHaveTextContent(`${label} 통과`));
    // The state file is canonical JSON with sorted keys, so iterating the object would draw
    // these alphabetically and the screen would stop matching the document.
    const drawn = Array.from(gates.querySelectorAll("li")).map(node => node.textContent ?? "");
    expect(drawn.map(text => order.findIndex(label => text.includes(label))))
      .toEqual([0, 1, 2, 3, 4]);
  });

  it("names the gate that failed when a refresh was refused", () => {
    const refused = snapshot();
    refused.continuity.last_transition = {
      ...refused.continuity.last_transition!,
      refresh_type: "HARD", continuity_reason: "HARD_REST_WINDOW_EXCEEDS_SAMPLE_INTERVAL",
      wall_carried: 0, wall_ended: 0, wall_unknown: 277, eligible: 0, window_ms: 1_400,
      gates: { VOLUNTARY: true, CONTINUITY: true, NEWER: true, BOUNDED_WINDOW: false,
               OVERLAP: true },
      basis: "SNAPSHOT_INSTALLED_ON_LIVE_BOOK", replayed_frames: null, chain_preserved: false,
      window_verdict: "EXCEEDED_MAX", window_exempt: false,
    };
    render(<ContinuityPanel snapshot={refused} />);
    const panel = screen.getByTestId("lm-continuity");
    expect(panel).toHaveTextContent("HARD");
    expect(panel).toHaveTextContent("REST 창이 샘플 간격 초과");
    expect(screen.getByTestId("lm-continuity-gates")).toHaveTextContent("REST 창 상한 실패");
    expect(panel).toHaveTextContent("상한 초과");
    expect(screen.queryByTestId("lm-continuity-window-exempt")).toBeNull();
    expect(screen.getByTestId("lm-continuity-counts")).toHaveTextContent("277");
  });

  // v4. The exemption is the one place a gate reads "통과" on a window over its own ceiling, so
  // the panel has to say that out loud rather than leaving a green chip to imply 130 ms.
  it("says when a carry passed the window gate by the replayed-chain exemption", () => {
    const exempt = snapshot();
    exempt.continuity.soft_window_exempt = 1;
    exempt.continuity.last_transition = {
      ...exempt.continuity.last_transition!,
      window_ms: 485, window_verdict: "EXEMPT_REPLAYED_CHAIN", window_exempt: true,
    };
    render(<ContinuityPanel snapshot={exempt} />);
    const panel = screen.getByTestId("lm-continuity");
    expect(panel).toHaveTextContent("면제 (체인 재생)");
    const note = screen.getByTestId("lm-continuity-window-exempt");
    expect(note).toHaveTextContent("485");
    expect(note).toHaveTextContent("300");
    expect(note).toHaveTextContent("동일한 update id");
    expect(screen.getByTestId("lm-continuity-gates")).toHaveTextContent("REST 창 상한 통과");
    // And the count of carries resting on the exemption is on the panel, not only in the note.
    expect(panel).toHaveTextContent("면제 1건");
  });

  it("never claims an exemption on a window that was inside the ceiling", () => {
    render(<ContinuityPanel snapshot={snapshot()} />);
    expect(screen.queryByTestId("lm-continuity-window-exempt")).toBeNull();
    expect(screen.getByTestId("lm-continuity")).toHaveTextContent("상한 이내");
  });

  it("says in plain numbers how much history a hard transition destroyed", () => {
    const hard = snapshot();
    hard.continuity.last_transition = {
      ...hard.continuity.last_transition!, refresh_type: "HARD",
      continuity_reason: "HARD_CONTINUITY_INTERRUPTED", cause: "RECONNECT",
      carried_entering: 31, carried_lost: 31, wall_carried: 0, wall_ended: 0,
      wall_unknown: 277, gates: null,
      overlap_check: null, window_ms: null, refresh_trigger: null,
      basis: null, replayed_frames: null, chain_preserved: false,
      window_verdict: "NOT_MEASURED", window_exempt: false,
    };
    render(<ContinuityPanel snapshot={hard} />);
    const lost = screen.getByTestId("lm-continuity-lost");
    expect(lost).toHaveTextContent("31");
    expect(lost).toHaveTextContent("재접속");
  });

  it("says the overlap count is evidence and not a threshold", () => {
    render(<ContinuityPanel snapshot={snapshot()} />);
    const overlap = screen.getByTestId("lm-continuity-overlap");
    expect(overlap).toHaveTextContent("비교 레벨 1,912개");
    expect(overlap).toHaveTextContent("레벨 일치율은 게이트가 아닙니다");
  });

  it("warns when the frozen rule document no longer matches its hash", () => {
    const drifted = snapshot();
    drifted.continuity.rule = {
      ...drifted.continuity.rule,
      identity: { ...drifted.continuity.rule.identity, recorded_sha256: "other",
                  sha256_agrees: false },
    };
    render(<ContinuityPanel snapshot={drifted} />);
    expect(screen.getByTestId("lm-continuity-drift")).toHaveTextContent("신뢰할 수 없습니다");
  });

  it("says so when the collector publishes no ledger at all", () => {
    const old = snapshot();
    old.continuity = continuityView({
      available: false, unavailable_reason: "COLLECTOR_PUBLISHES_NO_LEDGER",
      last_transition: null, active_carried: null, soft_refreshes: null,
    });
    render(<ContinuityPanel snapshot={old} />);
    expect(screen.getByTestId("lm-continuity-missing"))
      .toHaveTextContent("collector가 연속성 원장을 내지 않음");
    expect(screen.queryByTestId("lm-continuity-counts")).toBeNull();
  });

  it("flags the two bookkeeping failures that would otherwise be invisible", () => {
    const edge = snapshot();
    edge.continuity = continuityView({
      proofs_superseded: 2,
      last_transition: { ...snapshot().continuity.last_transition!, carried_truncated: true },
    });
    render(<ContinuityPanel snapshot={edge} />);
    expect(screen.getByTestId("lm-continuity-superseded")).toHaveTextContent("2");
    expect(screen.getByTestId("lm-continuity-truncated")).toHaveTextContent("개수는 정확하고");
  });

  it("renders nothing but a dash when there is no snapshot yet", () => {
    render(<ContinuityPanel snapshot={null} />);
    expect(screen.getByTestId("lm-continuity-missing")).toBeInTheDocument();
  });
});

describe("a carried wall on the ladder", () => {
  function carriedSide(side: Side): SideView {
    const carried = wall(side, side === "ASK" ? "84841" : "84760", {
      continuity_status: "CARRIED", not_carried_reason: null,
      observed_persistence_ms: 480_000, own_persistence_ms: 3_000,
      carried_persistence_ms: 480_000, persistence_source: "CARRIED",
      continuity_first_seen_ms: 1_000, carried_members: 1, continuity_refreshes: 2,
      continuity_proof: "SOFT_REFRESH_EXACT_PRICE_PRESENT_IN_INSTALLED_SNAPSHOT",
    });
    return sideView(side, { nearest_wall: carried, walls: [carried] });
  }

  it("marks the row and puts both spans in its title", () => {
    render(<SidePanel side="ASK" view={carriedSide("ASK")} wallCoverage="COMPLETE" />);
    const badge = screen.getByTestId("lm-wall-carried");
    expect(badge).toHaveTextContent("이어받음");
    expect(badge.getAttribute("title")).toContain("SOFT 갱신 2회");
    expect(badge.getAttribute("title")).toContain("3.0s");
    expect(badge.getAttribute("title")).toContain("8m 0s");
    expect(badge.getAttribute("title")).toContain("주문 동일성 증명은 아닙니다");
  });

  it("says on the nearest card that the span was carried", () => {
    render(<SidePanel side="BID" view={carriedSide("BID")} wallCoverage="COMPLETE" />);
    const nearest = screen.getByTestId("lm-nearest-BID");
    expect(nearest).toHaveTextContent("8m 0s");
    expect(nearest).toHaveTextContent("SOFT 갱신 2회 이어받음");
    expect(nearest).toHaveTextContent("자체 3.0s");
  });

  it("marks nothing on a wall that was not carried", () => {
    render(<SidePanel side="ASK" view={sideView("ASK")} wallCoverage="COMPLETE" />);
    expect(screen.queryByTestId("lm-wall-carried")).toBeNull();
  });
});


// --------------------------------------------------------------------------- staged refresh

describe("the staged refresh on screen", () => {
  it("says how a voluntary refresh is installed and what a failure costs", () => {
    render(<ResnapshotPanel snapshot={snapshot()} />);
    const panel = screen.getByTestId("lm-resnapshot-install");
    expect(panel).toHaveTextContent("update id가 같아졌을 때만 교체");
    expect(panel).toHaveTextContent("쿨다운도 소모하지 않습니다");
    expect(panel).toHaveTextContent("10.0s");
  });

  it("shows a refresh that is mid-flight, with what it has buffered and replayed", () => {
    const staging = snapshot();
    staging.resnapshot.refresh_in_progress = {
      trigger: "coverage_edge", state: "REPLAYING", outcome: null, failure: null,
      snapshot_update_id: 11731036473224, round_trip_ms: 105, buffered_at_snapshot: 4,
      replayed_frames: 3, discarded_older_than_snapshot: 1,
      attachment: "FIRST_FRAME_STRADDLES_SNAPSHOT_ID", frames_after_snapshot: 0,
      elapsed_ms: 140,
    };
    render(<ResnapshotPanel snapshot={staging} />);
    const row = screen.getByTestId("lm-resnapshot-staging");
    expect(row).toHaveTextContent("버퍼 4");
    expect(row).toHaveTextContent("재생 3");
    expect(row).toHaveTextContent("첫 프레임이 스냅샷 id를 포함");
  });

  it("warns on consecutive failures and says the band is unprotected meanwhile", () => {
    const failing = snapshot();
    failing.resnapshot.refresh_failures_consecutive = 4;
    failing.resnapshot.refresh_retry_in_s = 6.5;
    render(<ResnapshotPanel snapshot={failing} />);
    const warning = screen.getByTestId("lm-resnapshot-failures");
    expect(warning).toHaveTextContent("연속 실패 4회");
    expect(warning).toHaveTextContent("6.5s");
    expect(warning).toHaveTextContent("보호되지 않습니다");
  });

  it("does not warn when nothing has failed", () => {
    render(<ResnapshotPanel snapshot={snapshot()} />);
    expect(screen.queryByTestId("lm-resnapshot-failures")).toBeNull();
    expect(screen.queryByTestId("lm-resnapshot-staging")).toBeNull();
  });

  it("states that the swap kept the update id chain", () => {
    render(<ContinuityPanel snapshot={snapshot()} />);
    const panel = screen.getByTestId("lm-continuity");
    expect(panel).toHaveTextContent("체인 재생");
    expect(panel).toHaveTextContent("프레임 2개 재생 · update id 불변");
  });

  it("says plainly when a generation came from installing a snapshot instead", () => {
    const installed = snapshot();
    installed.continuity.last_transition = {
      ...installed.continuity.last_transition!,
      basis: "SNAPSHOT_INSTALLED_ON_LIVE_BOOK", replayed_frames: null, chain_preserved: false,
    };
    render(<ContinuityPanel snapshot={installed} />);
    expect(screen.getByTestId("lm-continuity")).toHaveTextContent("스냅샷 설치");
  });
});
