import type { ShadowVariant } from "@/types/api";

export type PerformancePeriod = "7d" | "30d" | "all";

export const PERFORMANCE_PERIODS: ReadonlyArray<{ value: PerformancePeriod; label: string }> = [
  { value: "7d", label: "최근 7일" },
  { value: "30d", label: "최근 30일" },
  { value: "all", label: "전체" },
];

export function periodRange(period: PerformancePeriod, now = new Date()): { startDate: string; endDate: string } | undefined {
  if (period === "all") return undefined;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value ?? "";
  const endDate = `${value("year")}-${value("month")}-${value("day")}`;
  const start = new Date(`${endDate}T12:00:00Z`);
  start.setUTCDate(start.getUTCDate() - (period === "7d" ? 6 : 29));
  return { startDate: start.toISOString().slice(0, 10), endDate };
}

export const STRATEGY_DESCRIPTIONS: Readonly<Record<string, string>> = {
  A: "당일 청산 · ATR 1.5", B: "Day2 허용 · ATR 1.0", C: "Day2 허용 · ATR 1.5",
  D: "Day2 허용 · ATR 2.0", E: "당일 청산 · 구조 손절",
};

export function winRate({ wins, losses }: Pick<ShadowVariant, "wins" | "losses">): string {
  const completed = wins + losses;
  return completed === 0 ? "-" : `${((wins / completed) * 100).toFixed(1)}%`;
}

export function formatR(value: string | null | undefined): string {
  if (value == null) return "-";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return value;
  if (amount === 0) return "0.00R";
  return `${amount > 0 ? "+" : ""}${amount.toFixed(2)}R`;
}

export function rTone(value: string | null | undefined): string {
  const amount = value == null ? Number.NaN : Number(value);
  if (amount > 0) return "text-success";
  if (amount < 0) return "text-danger";
  return "text-foreground";
}
