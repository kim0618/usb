import { describe, expect, it } from "vitest";
import { compactUsd, currency, decimal, etTime, formatDecimalString, formatKrw, formatSignedKrw, formatSignedUsd, formatUsd, kstDate, kstTime, multiple, signedDecimal, signedPercent, tradingDate, unconfirmedMarketCap, usdToDisplayKrw } from "./format";

describe("financial display formatting", () => {
  it("preserves string input while formatting for display", () => { expect(formatDecimalString("10250.5500")).toBe("10,250.55"); expect(decimal("1.4238", "R")).toBe("1.4238R"); });
  it("does not display null literals", () => { expect(decimal(null)).toBe("-"); });
  it("formats account currency without applying FX", () => { expect(currency("1034500", "KRW")).toBe("1,034,500원"); expect(currency("748.32", "USD")).toBe("$748.32"); expect(currency("10", null)).toBe("10"); });
  it("renders explicit positive and negative signs", () => { expect(signedDecimal("34.50")).toBe("+34.5"); expect(signedDecimal("-12.25", "%")).toBe("-12.25%"); expect(signedDecimal(null)).toBe("-"); });
  it("formats USD and whole-won KRW values without performing FX conversion", () => {
    expect(formatUsd("748.32")).toBe("$748.32");
    expect(formatKrw("76204000.4")).toBe("₩76,204,000");
    expect(formatSignedKrw(43800)).toBe("+₩43,800");
    expect(formatSignedKrw(-43800)).toBe("-₩43,800");
    expect(formatSignedUsd(32)).toBe("+$32.00");
  });
  it("converts USD with the Paper Day-0 KRW display rate", () => {
    expect(formatKrw(usdToDisplayKrw("7428.92"))).toBe("₩10,000,000");
    expect(formatKrw(usdToDisplayKrw("0"))).toBe("₩0");
    expect(formatSignedKrw(usdToDisplayKrw("10"))).toMatch(/^\+₩/);
    expect(formatSignedKrw(usdToDisplayKrw("-10"))).toMatch(/^-₩/);
    expect(formatSignedKrw(usdToDisplayKrw("0"))).toBe("+₩0");
    expect(formatKrw(usdToDisplayKrw(null))).toBe("-");
  });
  it("formats Quant ratios without changing their raw semantics", () => {
    expect(multiple(1.48)).toBe("1.48x");
    expect(signedPercent(0.01)).toBe("+1.0%");
    expect(signedPercent(0)).toBe("0.0%");
    expect(signedPercent(-0.03)).toBe("-3.0%");
    expect(compactUsd(79_380_000)).toBe("$79.4M");
  });
  it("uses KST for user time and does not claim a market-cap currency", () => {
    const instant = "2026-09-03T21:43:00-04:00";
    expect(kstTime(instant)).toContain("9월 4일");
    expect(kstTime(instant)).toContain("KST");
    expect(etTime(instant)).toContain("9월 3일");
    expect(unconfirmedMarketCap(5_535_047_000)).toBe("5.5B · 단위 확인 중");
    expect(unconfirmedMarketCap(5_535_047_000)).not.toContain("$");
  });
  it("formats the authoritative XNYS trading date without converting it through KST", () => {
    expect(tradingDate("2026-09-04")).toBe("09/04 (금)");
    expect(tradingDate(null)).toBe("-");
    expect(tradingDate("invalid")).toBe("-");
  });
  it("renders the paper account instant as a KST calendar date", () => {
    expect(kstDate("2026-09-07T16:30:00Z")).toBe("09/08");
    expect(kstDate(null)).toBe("-");
    expect(kstDate("invalid")).toBe("-");
  });
});
