import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrategyComparison } from "./shadow-performance";
import { api } from "../lib/api";
import { formatR, periodRange, rTone, STRATEGY_DESCRIPTIONS, winRate } from "../lib/shadow-performance";
import type { ShadowVariant } from "../types/api";

const pageSource = readFileSync("app/shadow/page.tsx", "utf8");
const source = pageSource + readFileSync("components/shadow-performance.tsx", "utf8");
const strategy = (wins: number, losses: number): ShadowVariant => ({ variant: "C", control: true, candidate_paths: wins + losses, trades: wins + losses, no_trade: 0, wins, losses, net_pnl: "0", net_r: "0", avg_net_r: "0", total_cost: "0", ambiguity: 0, overnight: 0, pyramid: null, source: "SIMULATION" });

afterEach(cleanup);

describe("Stage 9.14.2 strategy performance period filter", () => {
  it("keeps the five verified strategy configuration labels", () => {
    expect(STRATEGY_DESCRIPTIONS).toEqual({ A: "당일 청산 · ATR 1.5", B: "Day2 허용 · ATR 1.0", C: "Day2 허용 · ATR 1.5", D: "Day2 허용 · ATR 2.0", E: "당일 청산 · 구조 손절" });
  });

  it("calculates display-only win rate and safely handles no completed trades", () => {
    expect(winRate(strategy(14, 5))).toBe("73.7%");
    expect(winRate(strategy(0, 0))).toBe("-");
  });

  it("rounds R to two decimals with explicit signs and neutral zero", () => {
    expect(formatR("15.2343")).toBe("+15.23R");
    expect(formatR("10.0634")).toBe("+10.06R");
    expect(formatR("-0.806")).toBe("-0.81R");
    expect(formatR("0")).toBe("0.00R");
    expect(formatR(null)).toBe("-");
    expect(rTone("1")).toBe("text-success");
    expect(rTone("-1")).toBe("text-danger");
    expect(rTone("0")).toBe("text-foreground");
  });

  it("builds inclusive ET calendar ranges", () => {
    const now = new Date("2026-09-04T02:30:00Z"); // September 3 in New York
    expect(periodRange("7d", now)).toEqual({ startDate: "2026-08-28", endDate: "2026-09-03" });
    expect(periodRange("30d", now)).toEqual({ startDate: "2026-08-05", endDate: "2026-09-03" });
    expect(periodRange("all", now)).toBeUndefined();
  });

  it("renders all periods, selected state, columns, and the C badge", () => {
    const variants = ["A", "B", "C", "D", "E"].map(variant => ({ ...strategy(1, 1), variant, control: variant === "C" }));
    let selected = "";
    render(<StrategyComparison variants={variants} period="30d" loading={false} onPeriodChange={value => { selected = value; }} />);
    expect(screen.getByRole("button", { name: "최근 30일" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "최근 7일" }));
    expect(selected).toBe("7d");
    expect(screen.getByRole("button", { name: "전체" })).toHaveAttribute("aria-pressed", "false");
    ["전략", "설정", "매매", "미진입", "승", "패", "승률", "누적 R", "평균 R", "익일 보유", "기준 전략"].forEach(label => expect(screen.getAllByText(label).length).toBeGreaterThan(0));
  });

  it("uses range params for recent periods and no params for all", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => ({ source: "SIMULATION", variants: [] }) } as Response);
    await api.shadow({ startDate: "2026-08-28", endDate: "2026-09-03" });
    await api.shadow();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/shadow/summary?start_date=2026-08-28&end_date=2026-09-03");
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/api\/v1\/shadow\/summary$/);
    fetchMock.mockRestore();
  });

  it("shows a period-specific compact empty state", () => {
    render(<StrategyComparison variants={[strategy(0, 0)]} period="7d" loading={false} onPeriodChange={() => undefined} />);
    expect(screen.getByText("최근 7일 전략 성과 데이터가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("keeps the default and excludes removed detail UI", () => {
    expect(pageSource).toContain('useState<PerformancePeriod>("30d")');
    ["Synthetic Replay", "전략별 매매 결과", "api.shadowTrades", "api.replay", "기간: 전체"].forEach(label => expect(source).not.toContain(label));
  });
});
