import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DashboardStrategyCard, kstStamp } from "./dashboard-strategy-card";
import { usdToKrw } from "../lib/fx";
import { formatKrw } from "../lib/format";
import type { StrategyBundle } from "./strategy-runtime";

afterEach(cleanup);

const block = { mode: "OFFICIAL PAPER", status: "RUNNING" as const, status_reason: null, operating_since: "2026-09-28",
  operating_day: 16, session_count: 10, session_kind: "공식", last_run_at: "2026-10-13T00:32:00+00:00", last_decision: "신호 없음" };
const base = (over: Record<string, unknown> = {}): StrategyBundle => ({
  row: { strategy_id: "STRATEGY_A", display_name: "Strategy A", version: "V0", enabled: true, mode: "SIMULATION_PAPER",
         market_data_source: "KIWOOM", runtime_status: null, paper_status: null, evidence_status: null, session: null },
  status: {} as never, equity: {} as never,
  account: { strategy_id: "STRATEGY_A", available: true, currency: "USD", initial_equity: "7428.92",
             current_equity: "7509.4", today_pnl: "-52.1100", total_pnl: null, open_positions: 0,
             closed_trades_today: null, source: "PAPER_DB" },
  card: { strategy_id: "STRATEGY_A", net_pnl: "80.4785", trades: 3, official: { trades: 3, net_pnl: "80.4785", accounting_versions: ["V1"] },
          dashboard: block, na: {} } as never,
  ...over,
} as StrategyBundle);

describe("dashboard strategy card", () => {
  it("shows USD with two decimals and whole-won KRW, signed amounts keeping their sign", () => {
    const { container } = render(<DashboardStrategyCard bundle={base()}/>);
    const text = container.textContent!;
    expect(text).toContain("$7,428.92");
    expect(text).toContain(formatKrw(usdToKrw(7428.92)));   // ₩10,000,000 from the single fixed rate
    expect(text).toContain("-$52.11");
    expect(text).toContain(`-${formatKrw(Math.abs(usdToKrw(-52.11)))}`);
    expect(text).toContain("+$80.48");
    expect(text).toContain("운영 16일째 · 공식 10세션");
    expect(text).toContain("마지막 실행 10/13 09:32 KST");
  });

  it("prints a dash, never a zero or a converted value, where the book holds nothing", () => {
    const b = base();
    (b.card as { forward?: unknown }).forward = {};
    (b.card as { dashboard: unknown }).dashboard = { ...block, mode: "SHADOW", session_kind: "관찰", operating_day: 11,
      session_count: 8, last_run_at: null, last_decision: "WATCH" };
    const { container } = render(<DashboardStrategyCard bundle={b}/>);
    const values = [...container.querySelectorAll("dd")].map(dd => dd.textContent);
    expect(values).toEqual(["-", "-", "-", "-", "-", "WATCH"]);
    expect(container.textContent).toContain("관찰 11일째 · 8세션");
    expect(container.textContent).toContain("마지막 실행 -");
    expect(container.textContent).not.toContain("₩0");
  });

  it("shows the reason only while the state is ISSUE", () => {
    const issue = base();
    (issue.card as { dashboard: unknown }).dashboard = { ...block, status: "ISSUE", status_reason: "데이터 오류" };
    const first = render(<DashboardStrategyCard bundle={issue}/>);
    expect(first.container.querySelector("[data-reason]")!.textContent).toBe("데이터 오류");
    cleanup();
    const ok = render(<DashboardStrategyCard bundle={base()}/>);
    expect(ok.container.querySelector("[data-reason]")).toBeNull();
  });

  it("formats KST stamps and tolerates garbage", () => {
    expect(kstStamp("2026-10-12T15:05:00+00:00")).toBe("10/13 00:05 KST");
    expect(kstStamp(null)).toBe("-");
    expect(kstStamp("nope")).toBe("-");
  });

  it("shows an official PnL of zero when the official book has no trade, and never the legacy figure", () => {
    const b = base();
    (b.card as never as { official: unknown }).official = { trades: 0, net_pnl: null, accounting_versions: [] };
    (b.card as never as { net_pnl: unknown; trades: number }).net_pnl = null;
    (b.card as never as { trades: number }).trades = 0;
    (b.card as never as { legacy: unknown }).legacy = { trades: 3, net_pnl: "72.3555", accounting_versions: ["V0"] };
    b.account = { ...b.account, total_pnl: "80.4785" };               // the account's all-time figure
    const { container } = render(<DashboardStrategyCard bundle={b}/>);
    const fields = Object.fromEntries([...container.querySelectorAll("dl > div")].map(el => [
      el.querySelector("dt")!.textContent, el.querySelector("dd")!.textContent]));
    expect(fields["공식 누적 손익"]).toBe("$0.00₩0");
    expect(fields["공식 거래 수"]).toBe("0");
    expect(container.textContent).not.toContain("80.48");
    expect(container.textContent).not.toContain("72.36");
  });

  it("keeps a dash where trades exist but their net is unknown, and for a strategy without a book", () => {
    const unknown = base();
    (unknown.card as never as { official: unknown }).official = { trades: 5, net_pnl: null, accounting_versions: [] };
    (unknown.card as never as { net_pnl: unknown }).net_pnl = null;
    const first = render(<DashboardStrategyCard bundle={unknown}/>);
    const netOf = (c: HTMLElement) => [...c.querySelectorAll("dl > div")]
      .find(el => el.querySelector("dt")!.textContent === "공식 누적 손익")!.querySelector("dd")!.textContent;
    expect(netOf(first.container)).toBe("-");
    cleanup();
    const shadow = base();
    (shadow.card as never as { forward: unknown }).forward = {};
    (shadow.card as never as { official: unknown }).official = { trades: 0, net_pnl: null, accounting_versions: [] };
    const second = render(<DashboardStrategyCard bundle={shadow}/>);
    expect(netOf(second.container)).toBe("-");                      // H has no capital book at all
  });

  it("shows H's paper account once launched: $10,000 with no trade is a real zero, not a dash", () => {
    const h = base({ account: { strategy_id: "STRATEGY_H_V2", available: true, currency: "USD", initial_equity: "10000",
      current_equity: "10000", today_pnl: "0", total_pnl: "0", open_positions: 0, closed_trades_today: 0, source: "H_PAPER_EVENTS" } });
    (h.card as never as Record<string, unknown>).forward = {};
    (h.card as never as Record<string, unknown>).initial_equity = "10000";
    (h.card as never as Record<string, unknown>).official = { trades: 0, net_pnl: "0", accounting_versions: [] };
    (h.card as never as Record<string, unknown>).dashboard = { ...block, mode: "PAPER + SHADOW", session_kind: "관찰", last_decision: "WATCH" };
    const { container } = render(<DashboardStrategyCard bundle={h}/>);
    const fields = Object.fromEntries([...container.querySelectorAll("dl > div")].map(el => [
      el.querySelector("dt")!.textContent, el.querySelector("dd")!.textContent]));
    expect(fields["초기 자본"]).toContain("$10,000.00");
    expect(fields["현재 자산"]).toContain("$10,000.00");
    expect(fields["공식 거래 수"]).toBe("0");
    expect(container.querySelector("[data-mode]")!.textContent).toBe("PAPER + SHADOW");
  });
});
