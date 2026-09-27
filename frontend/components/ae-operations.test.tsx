import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PerformanceTable, PortfolioPanel, ResearchHistory } from "./ae-operations";
import type { PerformanceBoard, PortfolioView, StrategyMetrics, StrategyRow } from "../lib/strategies";

afterEach(() => cleanup());

const metrics = (over: Partial<StrategyMetrics> = {}): StrategyMetrics => ({
  strategy_id: "X", trades: 0, unreconciled_trades: 0, operating_sessions: 0, gross_pnl: null, costs: null,
  net_pnl: null, return: null, win_rate: null, pf: null, expectancy: null, mdd: null, mdd_amount: null,
  avg_mfe: null, avg_mae: null, avg_holding_seconds: null, initial_equity: null, equity_change: null,
  ledger_vs_equity_gap: null, accounting_mix: [], na: { net_pnl: "NO_CLOSED_TRADE" }, ...over,
});

const rows: StrategyRow[] = [
  { strategy_id: "STRATEGY_A", display_name: "Strategy A", short_name: "A", variant_label: "기존 전략", version: "V0",
    enabled: true, mode: "SIMULATION_PAPER", market_data_source: "KIWOOM", research_lifecycle: "PASSED_TO_PAPER",
    lifecycle: "PAPER", runtime_status: null, paper_status: null, evidence_status: null, session: null },
  { strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", short_name: "E", variant_label: "E-MAX V1",
    version: "E-MAX V1", enabled: true, mode: "SIMULATION_PAPER", market_data_source: "KIWOOM",
    research_lifecycle: "PASSED_TO_PAPER", lifecycle: "PAPER", runtime_status: null, paper_status: null,
    evidence_status: null, session: null },
  ...(["B", "C", "D"] as const).map(letter => ({
    strategy_id: `STRATEGY_${letter}`, display_name: `Strategy ${letter}`, short_name: letter, version: "x",
    enabled: false, mode: "RESEARCH_CLOSED", market_data_source: "KIWOOM", research_lifecycle: "CLOSED",
    lifecycle: "RETIRED", closed_on: "2026-09-21", runtime_status: null, paper_status: null,
    evidence_status: null, session: null })),
];

const board = (started: boolean): PerformanceBoard => ({
  currency: "USD",
  paper_clock: { paper_evaluation_version: "AE_PAPER_V1", accounting_version: "V1",
                 official_paper_start: started ? "2026-09-29" : null, status: started ? "STARTED" : "NOT_STARTED",
                 gate_contract: "AE_PAPER_EVALUATION_GATE_V1" },
  strategies: {
    STRATEGY_A: { strategy_id: "STRATEGY_A", official: metrics(),
                  legacy: metrics({ trades: 3, net_pnl: "72.3555", accounting_mix: ["TOTAL_COST_DEDUCTED_V0"] }) },
    STRATEGY_E_MAX_V1: { strategy_id: "STRATEGY_E_MAX_V1", official: metrics(),
                         legacy: metrics({ trades: 3, net_pnl: "-123.1542", accounting_mix: ["TOTAL_COST_DEDUCTED_V0"] }) },
  },
  combined: metrics(), legacy_combined: metrics({ trades: 6, net_pnl: "-50.7987" }),
  gate: {}, books: { STRATEGY_E_MAX_V1: "paper_state_v1/OFFICIAL_KIWOOM_PAPER" }, excluded: {}, e_sessions: [],
});

describe("official and legacy performance", () => {
  it("shows the official book empty with its clock, and never a legacy figure in it", () => {
    render(<PerformanceTable board={board(false)} rows={rows} book="official"/>);
    const section = document.querySelector("[data-book=official]") as HTMLElement;
    expect(section.querySelector("[data-clock=NOT_STARTED]")).not.toBeNull();
    const trades = within(section).getByText("Trades").closest("tr")!;
    expect(within(trades).getAllByText("0")).toHaveLength(3);                     // A, E, Combined
    expect(section.textContent).not.toContain("72.36");
    expect(section.textContent).not.toContain("123.15");
  });

  it("keeps legacy V0 in its own table, labelled as excluded from official evaluation", () => {
    render(<PerformanceTable board={board(true)} rows={rows} book="legacy"/>);
    const section = document.querySelector("[data-book=legacy]") as HTMLElement;
    expect(within(section).getByRole("heading").textContent).toBe("Legacy Paper (V0) · 참고용");
    expect(section.textContent).toContain("공식 평가·게이트·합산에서 제외");
    expect(section.querySelector("[data-accounting-warning]")).not.toBeNull();
    const trades = within(section).getByText("Trades").closest("tr")!;
    expect(within(trades).getByText("6")).toBeInTheDocument();                    // legacy combined only
  });

  it("names the official start once the clock runs", () => {
    render(<PerformanceTable board={board(true)} rows={rows} book="official"/>);
    expect(screen.getByText(/ACCOUNTING_V1 · 2026-09-29부터/)).toBeInTheDocument();
    expect(document.querySelector("[data-clock]")).toBeNull();
  });
});

describe("portfolio book label", () => {
  it("says which book the simulation reads", () => {
    const view = { currency: "USD", book: "official", accounting_version: "V1",
      paper_clock: board(false).paper_clock,
      baseline: { mode: "SIMULATION_BASELINE_ONLY", risk_budget: { STRATEGY_A: "0.5", STRATEGY_E_MAX_V1: "0.5" },
        allocation_rule: "NOT_IN_USE", definition: "d", window: null, days: 0,
        symbol_overlap: { both: 0, either: 0, a_only: 0, e_only: 0, share_of_either: null, symbols: [] },
        same_session_symbol_collisions: [], sector_overlap: null, equity_curve: [], na: {} },
      combined: { net_pnl: null, mdd: null }, exposure: { per_strategy: {}, per_symbol: [], total_cost_basis: "0" },
      cash_usage: {} } as PortfolioView;
    render(<PortfolioPanel view={view} rows={rows}/>);
    expect(screen.getByRole("heading", { name: "A+E 포트폴리오 · Official (V1)" })).toBeInTheDocument();
  });
});

describe("research history", () => {
  it("lists A and E as active and B, C, D as retired", () => {
    render(<ResearchHistory rows={rows}/>);
    ["STRATEGY_A", "STRATEGY_E_MAX_V1"].forEach(id =>
      expect(document.querySelector(`[data-history=${id}]`)!.textContent).toContain("ACTIVE · PAPER"));
    ["STRATEGY_B", "STRATEGY_C", "STRATEGY_D"].forEach(id =>
      expect(document.querySelector(`[data-history=${id}]`)!.textContent).toContain("RETIRED"));
  });
});
