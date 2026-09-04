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
  return `${amount > 0 ? "+" : ""}${formatter(amount)}`;
};
export const formatSignedUsd = (value: MoneyInput) => signedMoney(value, amount => formatUsd(amount));
export const formatSignedKrw = (value: MoneyInput) => signedMoney(value, amount => formatKrw(amount));
