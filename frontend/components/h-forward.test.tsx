import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { HCohortTable, HEvaluationPanel, HForwardOutcomes, HIssuerDetail, HSummary } from "./h-forward";
import { ExperimentHistory, GatePanel, PerformanceTable, StrategySelector } from "./ae-operations";
import type {
  HCohortRow, HEvaluation, HForwardView, HIssuerView, HMaturity, HOutcome,
  PerformanceBoard, StrategyMetrics, StrategyRow,
} from "../lib/strategies";

afterEach(() => cleanup());

const BASELINE = "2026-10-02";

const pending = (horizon: number, maturity: string): HOutcome => ({
  ticker: "TG", horizon_sessions: horizon, baseline_session: BASELINE, benchmark: "SPY",
  maturity_session: maturity, state: "PENDING", sessions_observed: 0, sessions_required: horizon,
  reason: `session ${maturity} has not been stored`,
});

const matured = (horizon: number, maturity: string, over: Partial<HOutcome> = {}): HOutcome => ({
  ticker: "TG", horizon_sessions: horizon, baseline_session: BASELINE, benchmark: "SPY",
  maturity_session: maturity, state: "MATURED", sessions_observed: horizon, sessions_required: horizon,
  baseline_price: 10, maturity_price: 11, security_return: 0.1, benchmark_return: 0.02,
  excess_return: 0.08, mfe: 0.15, mae: -0.05, tp1_hit: false, tp2_hit: false, bear_breach: false,
  ...over,
});

const row = (over: Partial<HCohortRow> = {}): HCohortRow => ({
  strategy_id: "STRATEGY_H_V2", ticker: "TG", cik: "0000000001", security_id: "BBGTG",
  cohort_tag: "INITIAL_D7_COHORT",
  decision: "WATCH", previous_decision: null, decision_at_launch: "WATCH",
  decision_time: "2026-10-04T08:00:00+00:00", effective_session: BASELINE, thesis_version: "T1:TG",
  decision_changes: 0,
  position: null, open_positions: 0, position_reason: "WATCH는 포지션을 만들지 않는다 (D7 계약)",
  d4_expectation_gap: "NEUTRAL", d4_gap_confidence: "LOW",
  valuation_method: "EV/FCF", valuation_window: "RECENT_6M", valuation_confidence: "LOW",
  valuation_status: "VALUED",
  bear: 5.0, bear_na_reason: null, tp1: 9.0, tp2: 13.0,
  tp1_upside_at_decision: 0.275, tp2_upside_at_decision: 0.889, range_complete: true,
  key_binding_clause: "APPROVE_BLOCKED:expectation_gap_permits_approve",
  approve_blockers: ["expectation_gap_permits_approve"], reject_fired: [],
  watch_matched: ["thesis_needs_confirmation_from_the_next_print"],
  decision_close: 7.06, current_price: 7.03, current_price_session: BASELINE,
  tp1_distance: 0.28, tp2_distance: 0.85, bear_distance: -0.29,
  pre_launch_drift: { decision_session: "2026-09-16", decision_close: 7.06, baseline_session: BASELINE,
                      baseline_close: 7.03, return_since_decision: -0.004, is_forward_evidence: false,
                      note: "이 구간은 launch 시점에 이미 과거다" },
  forward: { "1D": pending(1, "2026-10-05"), "5D": pending(5, "2026-10-09"),
             "21D": pending(21, "2026-11-02"), "63D": pending(63, "2027-01-04") },
  checksums: { d5_row: "abc" },
  ...over,
});

const vrrm = row({
  ticker: "VRRM", bear: null, bear_na_reason: "NEGATIVE_IMPLIED_EQUITY", bear_distance: null,
  range_complete: false, valuation_confidence: "MEDIUM", valuation_method: "EV/EBIT",
  tp1: 4.42, tp2: 5.72, current_price: 2.81, tp1_distance: 0.572, tp2_distance: 1.035,
});

const maturity: HMaturity = {
  "1D": { matured: 0, pending: 8, incomplete: 0, no_baseline: 0, maturity_session: "2026-10-05" },
  "5D": { matured: 0, pending: 8, incomplete: 0, no_baseline: 0, maturity_session: "2026-10-09" },
  "21D": { matured: 0, pending: 8, incomplete: 0, no_baseline: 0, maturity_session: "2026-11-02" },
  "63D": { matured: 0, pending: 8, incomplete: 0, no_baseline: 0, maturity_session: "2027-01-04" },
};

const evaluation: HEvaluation = {
  strategy_id: "STRATEGY_H_V2", contract_id: "H_V2_D7_FORWARD_SHADOW_V1", state: "RUNNING",
  verdict: "INCONCLUSIVE", reasons: ["SAMPLE_NOT_REACHED"],
  sample_needed: { matured_21d_issuers: 8, matured_63d_issuers: 8 },
  sample_reached: { "21D": false, "63D": false }, maturity,
  under_ae_paper_gate: false, note: "H는 A/E paper gate 대상이 아니다",
};

const view = (over: Partial<HForwardView> = {}): HForwardView => ({
  strategy_id: "STRATEGY_H_V2",
  contract: { contract_id: "H_V2_D7_FORWARD_SHADOW_V1", contract_sha256: "33fe", step: "H-V2-D7",
              research_build: "CLOSED", forward_observation: "ACTIVE", d5_contract: "D5_D2R_V1",
              d6_contract: "h_v2_d6_integrated_decision_v1", decision_session: "2026-09-16",
              horizons: [1, 5, 21, 63], benchmark: "SPY", sizing_contract: "NOT_DEFINED",
              sample_needed: { matured_21d_issuers: 8 }, preregistered_forward_questions: ["q"] },
  launch: { status: "LAUNCHED", launched_at: "2026-10-04T08:00:00+00:00", baseline_session: BASELINE,
            decision_session: "2026-09-16", d5_contract: "D5_D2R_V1", issuers: 8,
            cohort_tags: ["INITIAL_D7_COHORT"] },
  decision_counts: { APPROVE: 0, WATCH: 6, REJECT: 2 },
  maturity, evaluation,
  position_rule: { watch_creates_position: false, reject_creates_position: false,
                   approve_creates_position: false, approve_creates_entry_candidate: true,
                   sizing_contract: "NOT_DEFINED", why: "A는 손절 기준, E는 당일 청산이 필요하다" },
  price_store: { source: "MASSIVE_GROUPED_DAILY", latest_session: BASELINE, sessions_observed: 1 },
  rows: [row(), vrrm, row({ ticker: "IDCC", decision: "REJECT", tp2: 280.0, tp2_distance: -0.166,
                            reject_fired: ["valuation_fully_prices_the_positive_case"],
                            key_binding_clause: "REJECT:valuation_fully_prices_the_positive_case" })],
  ledger_rows: 8, snapshot_rows: 8,
  ...over,
});

describe("H forward shadow summary", () => {
  it("shows zero positions and the watch/reject counts as states, not as an error", () => {
    render(<HSummary view={view()}/>);
    const card = (label: string) => screen.getByText(label).closest(".panel")!.textContent!;
    expect(card("H Paper Positions")).toContain("0");
    expect(card("H Watchlist")).toContain("6");
    expect(card("H Rejected")).toContain("2");
    expect(card("H Approved")).toContain("0");
  });

  it("names the required D5 contract so the screen cannot show the superseded valuation silently", () => {
    render(<HSummary view={view()}/>);
    expect(screen.getByText("D5 Contract").closest(".panel")!.textContent).toContain("D5_D2R_V1");
  });

  it("says why an APPROVE would still not become a position", () => {
    render(<HSummary view={view()}/>);
    const note = document.querySelector("[data-position-rule=NOT_DEFINED]")!;
    expect(note.textContent).toContain("WATCH·REJECT는 포지션을 만들지 않는다");
    expect(note.textContent).toContain("APPROVE가 0인 것은 오류가 아니다");
  });
});

describe("H cohort table", () => {
  it("renders a refused Bear as N/A with its reason, never as 0", () => {
    render(<HCohortTable rows={view().rows}/>);
    const line = document.querySelector("[data-h-row=VRRM]") as HTMLElement;
    const na = line.querySelector("[data-na='NEGATIVE_IMPLIED_EQUITY']")!;
    expect(na.textContent).toBe("N/A");
    expect(line.textContent).not.toContain("$0.00");
  });

  it("flags a range that lost a leg even when the valuation confidence rose", () => {
    render(<HCohortTable rows={view().rows}/>);
    const line = document.querySelector("[data-h-row=VRRM]") as HTMLElement;
    expect(line.textContent).toContain("MEDIUM");
    expect(line.textContent).toContain("range 불완전");
  });

  it("shows each issuer's decision and that it holds no position", () => {
    render(<HCohortTable rows={view().rows}/>);
    for (const [ticker, decision] of [["TG", "WATCH"], ["IDCC", "REJECT"]] as const) {
      const line = document.querySelector(`[data-h-row=${ticker}]`) as HTMLElement;
      expect(line.dataset.decision).toBe(decision);
      expect(line.textContent).toContain("포지션을 만들지 않는다");
    }
  });

  it("shows the binding clause that decided each issuer", () => {
    render(<HCohortTable rows={view().rows}/>);
    expect((document.querySelector("[data-h-row=IDCC]") as HTMLElement).textContent)
      .toContain("REJECT:valuation_fully_prices_the_positive_case");
    expect((document.querySelector("[data-h-row=TG]") as HTMLElement).textContent)
      .toContain("APPROVE_BLOCKED:expectation_gap_permits_approve");
  });

  it("says so when the cohort is empty instead of rendering an empty table", () => {
    render(<HCohortTable rows={[]}/>);
    expect(screen.getByText("H 코호트가 비어 있습니다.")).toBeTruthy();
  });
});

describe("H forward outcomes", () => {
  it("renders every unmatured horizon as PENDING with its maturity session", () => {
    render(<HForwardOutcomes rows={view().rows} horizons={[1, 5, 21, 63]}/>);
    const line = document.querySelector("[data-forward-row=TG]") as HTMLElement;
    const states = Array.from(line.querySelectorAll("[data-outcome-state]"))
      .map(cell => (cell as HTMLElement).dataset.outcomeState);
    expect(states).toEqual(["PENDING", "PENDING", "PENDING", "PENDING"]);
    expect(line.textContent).toContain("2026-11-02");
    expect(line.textContent).not.toContain("vs SPY");
  });

  it("shows excess return only once a horizon has matured", () => {
    const rows = [row({ forward: { "1D": matured(1, "2026-10-05"), "5D": pending(5, "2026-10-09"),
                                   "21D": pending(21, "2026-11-02"), "63D": pending(63, "2027-01-04") } })];
    render(<HForwardOutcomes rows={rows} horizons={[1, 5, 21, 63]}/>);
    const line = document.querySelector("[data-forward-row=TG]") as HTMLElement;
    expect(line.textContent).toContain("+8.0% vs SPY");
  });

  it("labels the decision-to-launch interval as not being a forward result", () => {
    render(<HForwardOutcomes rows={view().rows} horizons={[1]}/>);
    expect(screen.getByText(/결정 이후 가격변화 \(forward 아님\)/)).toBeTruthy();
  });
});

describe("H evaluation panel", () => {
  it("shows RUNNING with an INCONCLUSIVE verdict and no sample reached", () => {
    render(<HEvaluationPanel evaluation={evaluation}/>);
    expect(screen.getByText("RUNNING")).toBeTruthy();
    expect(screen.getByText("INCONCLUSIVE")).toBeTruthy();
    const line = document.querySelector("[data-horizon='21D']") as HTMLElement;
    expect(within(line).getByText("PENDING")).toBeTruthy();
  });
});

describe("H issuer detail", () => {
  const issuer: HIssuerView = {
    ...vrrm,
    thesis: { d3: { provenance: "VALID" }, d4: { expectation_gap: "UNKNOWN" },
              d5: { primary_method: "EV/EBIT" }, d6: { decision: "WATCH" } },
    decision_history: [{ record: "LAUNCH_STATE", decision_time: "2026-10-04T08:00:00+00:00",
                         previous_decision: null, decision: "WATCH", cause: "LAUNCH",
                         thesis_version: "T1:VRRM" }],
    launch_snapshot: {},
  };

  it("shows the refused Bear with its reason rather than a number", () => {
    render(<HIssuerDetail issuer={issuer}/>);
    const field = document.querySelector("[data-h-field='Bear']") as HTMLElement;
    expect(field.textContent).toContain("N/A");
    expect(field.textContent).toContain("NEGATIVE_IMPLIED_EQUITY");
  });

  it("separates the decision-session price from the forward baseline", () => {
    render(<HIssuerDetail issuer={issuer}/>);
    expect((document.querySelector("[data-h-field='결정일 종가']") as HTMLElement).textContent).toContain("$7.06");
    expect((document.querySelector("[data-h-field='Baseline 종가']") as HTMLElement).textContent).toContain("$7.03");
  });

  it("says the old thesis is kept and that a price move alone changes nothing", () => {
    render(<HIssuerDetail issuer={issuer}/>);
    expect(screen.getByText(/과거 thesis는 덮어쓰지 않는다/)).toBeTruthy();
    expect(screen.getByText(/가격이 움직였다는 이유만으로는 결정이 바뀌지 않는다/)).toBeTruthy();
  });

  it("lists the clauses that produced the decision", () => {
    render(<HIssuerDetail issuer={issuer}/>);
    const reasons = screen.getByText("결정 근거").nextElementSibling as HTMLElement;
    expect(within(reasons).getByText("APPROVE 차단")).toBeTruthy();
    expect(reasons.textContent).toContain("expectation_gap_permits_approve");
    expect(reasons.textContent).toContain("thesis_needs_confirmation_from_the_next_print");
  });
});

// -- the A/E/H board --------------------------------------------------------------------------------

const metrics = (over: Partial<StrategyMetrics> = {}): StrategyMetrics => ({
  strategy_id: "X", trades: 0, unreconciled_trades: 0, operating_sessions: 0, gross_pnl: null, costs: null,
  net_pnl: null, return: null, win_rate: null, pf: null, expectancy: null, mdd: null, mdd_amount: null,
  avg_mfe: null, avg_mae: null, avg_holding_seconds: null, initial_equity: null, equity_change: null,
  ledger_vs_equity_gap: null, accounting_mix: [], na: { net_pnl: "NO_CLOSED_TRADE" }, ...over,
});

const strategyRow = (id: string, short: string, mode: string): StrategyRow => ({
  strategy_id: id, display_name: `Strategy ${short}`, short_name: short, version: "v", enabled: true,
  mode, market_data_source: "KIWOOM", research_lifecycle: "PASSED_TO_PAPER", lifecycle: "PAPER",
  runtime_status: null, paper_status: null, evidence_status: null, session: null,
});

const rows: StrategyRow[] = [
  strategyRow("STRATEGY_A", "A", "SIMULATION_PAPER"),
  strategyRow("STRATEGY_E_MAX_V1", "E", "SIMULATION_PAPER"),
  strategyRow("STRATEGY_H_V2", "H", "FORWARD_SHADOW"),
];

const board: PerformanceBoard = {
  currency: "USD",
  paper_clock: { paper_evaluation_version: "AE_PAPER_V1", accounting_version: "V1",
                 official_paper_start: "2026-09-28", status: "STARTED",
                 gate_contract: "AE_PAPER_EVALUATION_GATE_V1" },
  strategies: {
    STRATEGY_A: { strategy_id: "STRATEGY_A", official: metrics({ trades: 3, net_pnl: "72.3555" }),
                  legacy: metrics() },
    STRATEGY_E_MAX_V1: { strategy_id: "STRATEGY_E_MAX_V1",
                         official: metrics({ trades: 12, net_pnl: "-212.39" }), legacy: metrics() },
    STRATEGY_H_V2: { strategy_id: "STRATEGY_H_V2", official: metrics({ operating_sessions: 1 }),
                     legacy: metrics() },
  },
  combined: metrics({ trades: 15, net_pnl: "-140.03" }), legacy_combined: metrics(),
  gate: {}, books: {}, excluded: {}, e_sessions: [],
  h_forward: view(),
  combined_definition: "Combined는 A와 E의 공식 장부 합계다. H는 자본 장부가 없어 합산 대상이 아니다",
};

describe("A/E/H performance board", () => {
  it("gives A, E and H a column each and keeps Combined to A+E", () => {
    render(<PerformanceTable board={board} rows={rows} book="official"/>);
    const section = document.querySelector("[data-book=official]") as HTMLElement;
    const columns = Array.from(section.querySelectorAll("th[data-column]"))
      .map(th => (th as HTMLElement).dataset.column);
    expect(columns).toEqual(["STRATEGY_A", "STRATEGY_E_MAX_V1", "STRATEGY_H_V2", "COMBINED"]);
    expect(section.textContent).toContain("H는 자본 장부가 없어 합산 대상이 아니다");
  });

  it("shows H's decision counts and horizon maturity on the board", () => {
    render(<PerformanceTable board={board} rows={rows} book="official"/>);
    const decisions = document.querySelector("[data-h-board=decisions]") as HTMLElement;
    expect(decisions.textContent).toContain("APPROVE 0");
    expect(decisions.textContent).toContain("WATCH 6");
    expect(decisions.textContent).toContain("REJECT 2");
    expect((document.querySelector("[data-h-board=maturity]") as HTMLElement).textContent)
      .toContain("21D 0/8");
  });

  it("narrows to one strategy and drops Combined when H alone is selected", () => {
    render(<PerformanceTable board={board} rows={rows} book="official" strategy="STRATEGY_H_V2"/>);
    const section = document.querySelector("[data-book=official]") as HTMLElement;
    const columns = Array.from(section.querySelectorAll("th[data-column]"))
      .map(th => (th as HTMLElement).dataset.column);
    expect(columns).toEqual(["STRATEGY_H_V2"]);
  });

  it("never shows a zero where H has no capital book", () => {
    render(<PerformanceTable board={board} rows={rows} book="official" strategy="STRATEGY_H_V2"/>);
    const section = document.querySelector("[data-book=official]") as HTMLElement;
    const na = Array.from(section.querySelectorAll("td[data-na]"))
      .filter(cell => (cell as HTMLElement).dataset.na === "NO_CLOSED_TRADE");
    expect(na.length).toBeGreaterThan(0);
    expect(na.every(cell => cell.textContent === "N/A")).toBe(true);
  });

  it("offers ALL, A, E and H in the selector", () => {
    render(<StrategySelector rows={rows} value="ALL" onChange={() => {}}/>);
    const options = Array.from(document.querySelectorAll("[data-strategy-option]"))
      .map(node => (node as HTMLElement).dataset.strategyOption);
    expect(options).toEqual(["ALL", "STRATEGY_A", "STRATEGY_E_MAX_V1", "STRATEGY_H_V2"]);
  });
});


// -- the comparison screen narrows to one strategy -------------------------------------------------

const gate = (id: string): import("../lib/strategies").GateResult => ({
  strategy_id: id, contract_id: "AE_PAPER_EVALUATION_GATE_V1", verdict: "INCONCLUSIVE",
  reasons: ["SAMPLE_NOT_REACHED"], failed: [], pending: ["net_pnl"],
  conditions: [{ condition: "net_pnl", value: null, threshold: "> 0", status: "PENDING", note: null }],
  accounting: [], error_rate: null, final: false,
});

describe("per-strategy narrowing", () => {
  const boardWithGate = { ...board, gate: { STRATEGY_A: gate("STRATEGY_A"), STRATEGY_E_MAX_V1: gate("STRATEGY_E_MAX_V1") } };

  it("shows both gates under ALL", () => {
    render(<GatePanel gate={boardWithGate.gate} rows={rows} board={boardWithGate}/>);
    expect(document.querySelectorAll("[data-gate]")).toHaveLength(2);
  });

  it("shows only the selected strategy's gate", () => {
    render(<GatePanel gate={boardWithGate.gate} rows={rows} board={boardWithGate} strategy="STRATEGY_A"/>);
    const shown = Array.from(document.querySelectorAll("[data-gate]")).map(n => (n as HTMLElement).dataset.gate);
    expect(shown).toEqual(["STRATEGY_A"]);
  });

  it("renders nothing rather than an empty gate card when H is selected", () => {
    const { container } = render(
      <GatePanel gate={boardWithGate.gate} rows={rows} board={boardWithGate} strategy="STRATEGY_H_V2"/>);
    expect(container.innerHTML).toBe("");
  });

  it("still says H is outside the gate when both are shown", () => {
    render(<GatePanel gate={boardWithGate.gate} rows={rows} board={boardWithGate}/>);
    const note = document.querySelector("[data-gate-excluded=STRATEGY_H_V2]")!;
    expect(note.textContent).toContain("H_V2_D7_FORWARD_SHADOW_V1");
  });
});

describe("A's exit-rule experiment is kept but not a tab", () => {
  it("names it as an experiment, says the A-E labels are not the strategies, and links to it", () => {
    render(<ExperimentHistory/>);
    const card = document.querySelector("[data-experiment=A_EXIT_VARIANTS]") as HTMLElement;
    expect(card.textContent).toContain("A 청산규칙 변형");
    expect(card.textContent).toContain("Strategy A·E와는 다른 것");
    expect(screen.getByRole("link", { name: "섀도 변형 화면 열기" })).toHaveAttribute("href", "/shadow");
  });
});
