import React from "react";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./app-shell";
import { StrategyCards, TodaySummary } from "./dashboard-summary";
import { EquityCurve } from "./equity-curve";
import { StrategyBTradingOverview } from "./strategy-b-overview";
import { StrategyBPositions, StrategyBTrades } from "./strategy-b-portfolio";
import { StrategyBDailyPnl, StrategyBPerformance, StrategyBSetupPerformance } from "./strategy-b-performance";
import { StrategyCompareView } from "./strategy-compare";
import { dashboardSource } from "../lib/dashboard-source";
import { KRW_DISPLAY_RATE, formatKrw, usdToDisplayKrw } from "../lib/format";
import { FX_CONFIG, formatFxRate, krwToUsd, usdToKrw } from "../lib/fx";
import { strategyAccount } from "../lib/strategy-b";
import { mockPositions, mockSummary } from "../mocks/strategy-b";
import { mockPerformance, mockTrades } from "../mocks/strategy-b-trades";
import { mockComparison } from "../mocks/strategy-comparison";

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@/hooks/use-api", () => ({ useApi: () => ({ data: null, loading: false, error: null, refresh: () => undefined, setData: () => undefined }) }));

const source = (path: string) => readFileSync(path, "utf8");
const projectFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
  const path = join(directory, entry.name);
  if (entry.name === "node_modules" || entry.name.startsWith(".next")) return [];
  if (entry.isDirectory()) return projectFiles(path);
  return /\.(?:ts|tsx)$/.test(entry.name) && !entry.name.includes(".test.") ? [path] : [];
});
const sourceFiles = () => ["app", "components", "lib", "mocks", "types", "hooks"].flatMap(projectFiles);

/** The USD and KRW lines a Money element rendered inside `scope`. */
const moneyIn = (scope: HTMLElement) => ({
  usd: Array.from(scope.querySelectorAll("[data-money-usd]")).map(node => node.textContent?.replace(/\u00a0/g, " ")),
  krw: Array.from(scope.querySelectorAll("[data-money-krw]")).map(node => node.textContent?.replace(/\u00a0/g, " ")),
});

afterEach(cleanup);

describe("Fixed USD/KRW source", () => {
  it("reuses Strategy A's rate: the paper account's USD 7,428.92 standing for KRW 10,000,000", () => {
    expect(FX_CONFIG.mode).toBe("FIXED");
    expect(FX_CONFIG.usdKrw).toBe(10_000_000 / 7_428.92);
    expect(source("../backend/app/dev/bootstrap_paper_account.py")).toContain('PAPER_INITIAL_CASH = Decimal("7428.92")');
    expect(KRW_DISPLAY_RATE).toBe(FX_CONFIG.usdKrw);
    expect(formatKrw(usdToDisplayKrw("7428.92"))).toBe("₩10,000,000");
    expect(krwToUsd(10_000_000)).toBe(7_428.92);
  });

  it("defines the rate in lib/fx only; no screen carries its own", () => {
    const offenders = sourceFiles().filter(path => path !== join("lib", "fx.ts"))
      .filter(path => /7_?428\.92|usdKrw\s*:|\b1[_,]?3[45]0\b/.test(source(path)));
    expect(offenders).toEqual([]);
  });

  it("rounds USD to cents and KRW to won, symmetric around zero", () => {
    expect(krwToUsd(123_800)).toBe(91.97);
    expect(krwToUsd(-123_800)).toBe(-91.97);
    expect(usdToKrw(1)).toBe(1_346);
    expect(usdToKrw(-1)).toBe(-1_346);
    expect(Object.is(krwToUsd(-1), 0)).toBe(true);
  });
});

describe("Header FX label", () => {
  it("prints the same fixed rate the formatters convert with, labelled 고정", () => {
    const { container } = render(<AppShell><p>body</p></AppShell>);
    const label = container.querySelector<HTMLElement>("[data-fx-rate]")!;
    expect(label.textContent).toBe(`USD/KRW : ${formatFxRate()} · 고정`);
    expect(formatFxRate()).toBe("1,346.09");
    expect(Number(label.dataset.fxRate)).toBe(FX_CONFIG.usdKrw);
    expect(label.dataset.fxMode).toBe("FIXED");
    expect(Number(formatFxRate().replace(/,/g, ""))).toBeCloseTo(KRW_DISPLAY_RATE, 2);
  });

  it("never calls the fixed value a current or live rate", () => {
    const { container } = render(<AppShell><p>body</p></AppShell>);
    ["현재 환율", "실시간 환율", "실시간"].forEach(word => expect(container.querySelector("header")!.textContent).not.toContain(word));
    sourceFiles().forEach(path => ["현재 환율", "실시간 환율"].forEach(word => expect(source(path), path).not.toContain(word)));
  });
});

describe("Dashboard money", () => {
  it("shows both strategies' equity and today P&L as USD over KRW", async () => {
    const snapshot = await dashboardSource.snapshot();
    render(<StrategyCards snapshot={snapshot}/>);
    for (const [name, side] of [["A", snapshot.a], ["B", snapshot.b]] as const) {
      const card = screen.getByRole("region", { name: new RegExp(`STRATEGY ${name}`) });
      const equity = within(card).getByText("현재 자산").parentElement!;
      expect(moneyIn(equity)).toEqual({ usd: [`$${krwToUsd(side.current_equity_krw).toLocaleString("en-US", { minimumFractionDigits: 2 })}`], krw: [`≈ ${formatKrw(side.current_equity_krw)}`] });
      const pnl = within(card).getByText("오늘 손익").parentElement!;
      expect(moneyIn(pnl).usd).toHaveLength(1);
      expect(moneyIn(pnl).krw[0]).toMatch(/^≈ [+-]₩/);
      // Return and positions stay as they were.
      expect(moneyIn(within(card).getByText("누적 수익률").parentElement!).usd).toHaveLength(0);
      expect(moneyIn(within(card).getByText("보유 포지션").parentElement!).usd).toHaveLength(0);
    }
  });

  it("stacks today's P&L in the summary row for A and B", async () => {
    const snapshot = await dashboardSource.snapshot();
    const { container } = render(<TodaySummary snapshot={snapshot}/>);
    const row = container.querySelector<HTMLElement>('[data-today-row="오늘 손익"]')!;
    expect(moneyIn(row).usd).toEqual(["+$60.92", "+$136.69"]);
    expect(moneyIn(row).krw).toEqual(["≈ +₩82,000", "≈ +₩184,000"]);
  });
});

describe("Strategy B trading money", () => {
  it("puts USD over KRW on all five account cards", () => {
    const account = strategyAccount(mockSummary.current_equity_krw, mockPositions, mockTrades);
    render(<StrategyBTradingOverview summary={mockSummary} account={account}/>);
    const cards = screen.getByRole("region", { name: "전략 B 계좌 요약" }).children;
    expect(cards).toHaveLength(5);
    Array.from(cards).forEach(card => {
      const money = moneyIn(card as HTMLElement);
      expect(money.usd).toHaveLength(1);
      expect(money.krw).toHaveLength(1);
      expect(money.krw[0]).toMatch(/^≈ [+-]?₩/);
    });
    expect(screen.getByText("초기 자본 $7,428.92 (≈ ₩10,000,000)")).toBeInTheDocument();
  });

  it("keeps entry, current and stop USD only and stacks the position P&L", () => {
    render(<StrategyBPositions positions={mockPositions}/>);
    const row = screen.getByText("NVTS").closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    [2, 3, 7].forEach(index => {
      expect(cells[index].textContent).toMatch(/^\$\d/);
      expect(cells[index].textContent).not.toContain("₩");
    });
    expect(moneyIn(cells[5])).toEqual({ usd: ["+$67.75"], krw: ["+₩91,200"] });
  });

  it("keeps entry and exit USD only and stacks the closed trade P&L", () => {
    render(<StrategyBTrades trades={mockTrades}/>);
    const row = screen.getByText("RGTI").closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    [2, 3].forEach(index => expect(cells[index].textContent).not.toContain("₩"));
    expect(moneyIn(cells[4])).toEqual({ usd: ["-$30.68"], krw: ["-₩41,300"] });
    expect(screen.getByText("+$44.72 (≈ +₩60,200)")).toBeInTheDocument();
  });
});

describe("Strategy B performance money", () => {
  it("converts net P&L, average win and average loss only", () => {
    const { container } = render(<StrategyBPerformance performance={mockPerformance}/>);
    const cell = (label: string) => screen.getAllByText(new RegExp(`^${label}`))[0].closest<HTMLElement>(".bg-surface")!;
    expect(moneyIn(cell("순손익"))).toEqual({ usd: ["+$551.23"], krw: ["≈ +₩742,000"] });
    expect(moneyIn(cell("평균 이익"))).toEqual({ usd: ["$69.39"], krw: ["≈ ₩93,400"] });
    expect(moneyIn(cell("평균 손실"))).toEqual({ usd: ["-$36.70"], krw: ["≈ -₩49,400"] });
    expect(container.querySelectorAll("[data-money-usd]")).toHaveLength(3);
    ["총 수익률", "매매 횟수", "승률", "Profit Factor", "기대값", "최대 낙폭", "평균 R"].forEach(label => {
      expect(cell(label).textContent).not.toMatch(/[$₩]/);
    });
  });

  it("stacks the daily and setup P&L tables the same way", () => {
    const daily = render(<StrategyBDailyPnl curve={mockComparison.equity_curve} baseline={10_000_000}/>);
    const first = daily.container.querySelector<HTMLElement>('[data-b-daily-row="2026-09-01"]')!;
    expect(moneyIn(first)).toEqual({ usd: ["+$104.00", "$7,532.92"], krw: ["+₩140,000", "₩10,140,000"] });
    cleanup();
    const setups = render(<StrategyBSetupPerformance trades={mockTrades}/>);
    setups.container.querySelectorAll<HTMLElement>("[data-setup-row]").forEach(row => expect(moneyIn(row).usd).toHaveLength(1));
  });

  it("labels the equity axis in USD and shows USD over KRW in the tooltip", () => {
    const { container } = render(<EquityCurve curve={mockComparison.equity_curve} baseline={10_000_000} labelA="Strategy A" labelB="Strategy B" only="B"/>);
    const axis = Array.from(container.querySelectorAll(".flex.w-12 span")).map(node => node.textContent);
    expect(axis).toHaveLength(4);
    axis.forEach(label => expect(label).toMatch(/^\$\d+(\.\d)?K$/));
    fireEvent.mouseEnter(container.querySelector('[data-curve-hit="2026-09-29"]')!);
    expect(moneyIn(screen.getByRole("status"))).toEqual({ usd: ["$7,980.15"], krw: ["₩10,742,000"] });
  });
});

describe("Comparison money", () => {
  it("converts initial capital, equity and net P&L for both strategies with one rate", () => {
    render(<StrategyCompareView comparison={mockComparison}/>);
    const kpi = screen.getByRole("table", { name: /핵심 지표/ });
    const row = (label: string) => within(kpi).getByText(label).closest("tr")!;
    expect(moneyIn(row("초기 자본"))).toEqual({ usd: ["$7,428.92", "$7,428.92"], krw: ["₩10,000,000", "₩10,000,000"] });
    expect(moneyIn(row("현재 자산"))).toEqual({ usd: ["$7,711.22", "$7,980.15"], krw: ["₩10,380,000", "₩10,742,000"] });
    expect(moneyIn(row("순손익"))).toEqual({ usd: ["+$282.30", "+$551.23"], krw: ["+₩380,000", "+₩742,000"] });
    ["수익률", "매매 횟수", "승률", "Profit Factor", "기대값", "최대 낙폭", "평균 R"].forEach(label => expect(row(label).textContent).not.toMatch(/[$₩]/));
  });

  it("keeps same-symbol entry and exit prices USD only", () => {
    render(<StrategyCompareView comparison={mockComparison}/>);
    ["진입가", "청산가"].forEach(label => {
      const row = document.querySelector<HTMLElement>(`[data-symbol-fact="${label}"]`)!;
      expect(row.textContent).toContain("$");
      expect(row.textContent).not.toContain("₩");
    });
  });

  it("prints the same USD for the same KRW on every screen", async () => {
    const equity = mockSummary.current_equity_krw;
    const expected = "$7,980.15";
    expect(krwToUsd(equity)).toBe(7_980.15);

    const snapshot = await dashboardSource.snapshot();
    const dashboard = render(<StrategyCards snapshot={snapshot}/>);
    expect(within(screen.getByRole("region", { name: /STRATEGY B/ })).getByText(expected)).toBeInTheDocument();
    dashboard.unmount();

    const trading = render(<StrategyBTradingOverview summary={mockSummary} account={strategyAccount(equity, mockPositions, mockTrades)}/>);
    expect(screen.getByText(expected)).toBeInTheDocument();
    trading.unmount();

    render(<StrategyCompareView comparison={mockComparison}/>);
    const row = within(screen.getByRole("table", { name: /핵심 지표/ })).getByText("현재 자산").closest("tr")!;
    expect(within(row).getByText(expected)).toBeInTheDocument();
  });
});
