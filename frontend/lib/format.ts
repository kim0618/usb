import { FX_CONFIG, krwToUsd, usdToKrw } from "@/lib/fx";

export const dash = (value: unknown) => value === null || value === undefined || value === "" ? "-" : String(value);
export const number = (value: number | null | undefined, digits = 2) => value === null || value === undefined ? "-" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
export const decimal = (value: string | null | undefined, suffix = "") => value == null ? "-" : `${formatDecimalString(value)}${suffix}`;
export const signedDecimal = (value: string | null | undefined, suffix = "") => value == null ? "-" : `${value.startsWith("-") || value.startsWith("+") ? "" : "+"}${formatDecimalString(value)}${suffix}`;
export function currency(value: string | null | undefined, code?: string | null): string {
  if (value == null) return "-";
  const amount = formatDecimalString(value); const normalized = code?.toUpperCase();
  if (normalized === "KRW") return `${amount}원`;
  if (normalized === "USD") return `$${amount}`;
  return normalized ? `${amount} ${normalized}` : amount;
}
export function formatDecimalString(value: string): string {
  const match = value.match(/^(-?)(\d+)(?:\.(\d+))?$/); if (!match) return value;
  const [, sign, whole, fraction] = match; return `${sign}${Number(whole).toLocaleString()}${fraction ? `.${fraction.slice(0, 4).replace(/0+$/, "")}` : ""}`.replace(/\.$/, "");
}
export const score = (value: number | null | undefined) => value == null ? "-" : value.toFixed(2);
export const etTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("ko-KR", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) + " ET" : "-";
export const kstTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) + " KST" : "-";
export const kstDate = (value: string | null | undefined) => value && !Number.isNaN(new Date(value).getTime()) ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit" }).format(new Date(value)).replace(/\.\s*/g, "/").replace(/\/$/, "") : "-";
export const tradingDate = (value: string | null | undefined) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "-";
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return "-";
  const formatted = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "UTC", month: "2-digit", day: "2-digit", weekday: "short",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => formatted.find(item => item.type === type)?.value;
  return `${part("month")}/${part("day")} (${part("weekday")})`;
};
export const multiple = (value: number | null | undefined) => value == null ? "-" : `${value.toFixed(2)}x`;
export const signedPercent = (value: number | null | undefined) => {
  if (value == null) return "-";
  const percentage = value * 100;
  return `${percentage > 0 ? "+" : ""}${percentage.toFixed(1)}%`;
};
export const compactUsd = (value: number | null | undefined) => value == null ? "-" : new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1,
}).format(value);
export const unconfirmedMarketCap = (value: number | null | undefined) => value == null ? "정보 없음" : `${new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value)} · 단위 확인 중`;

type MoneyInput = number | string | null | undefined;
const numericMoney = (value: MoneyInput) => value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
/** Strategy A's display rate. It is lib/fx's fixed rate, not a copy of it. */
export const KRW_DISPLAY_RATE = FX_CONFIG.usdKrw;
export const usdToDisplayKrw = (value: MoneyInput) => {
  const amount = numericMoney(value);
  return amount == null ? null : usdToKrw(amount);
};
export const formatUsd = (value: MoneyInput) => {
  const amount = numericMoney(value);
  return amount == null ? "-" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(amount);
};
export const formatKrw = (value: MoneyInput) => {
  const amount = numericMoney(value);
  return amount == null ? "-" : new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 }).format(Math.round(amount));
};
const signedMoney = (value: MoneyInput, formatter: (amount: number) => string) => {
  const amount = numericMoney(value);
  if (amount == null) return "-";
  return `${amount >= 0 ? "+" : ""}${formatter(amount)}`;
};
export const formatSignedUsd = (value: MoneyInput) => signedMoney(value, amount => formatUsd(amount));
export const formatSignedKrw = (value: MoneyInput) => signedMoney(value, amount => formatKrw(amount));

/** A KRW-stored amount on one line, USD first: "$7,428.92 (≈ ₩10,000,000)". For helper
 *  text; stacked values use components/money. The space after ≈ is non-breaking so a
 *  narrow card wraps before the parenthesis, never inside it. */
export const moneyText = (krw: number, signed = false): string => {
  const usd = krwToUsd(krw);
  return `${signed ? formatSignedUsd(usd) : formatUsd(usd)} (≈\u00a0${signed ? formatSignedKrw(krw) : formatKrw(krw)})`;
};
