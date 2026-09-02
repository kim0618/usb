export const dash = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);
export const number = (value: number | null | undefined, digits = 2) => value === null || value === undefined ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
export const decimal = (value: string | null | undefined, suffix = "") => value == null ? "—" : `${formatDecimalString(value)}${suffix}`;
export function formatDecimalString(value: string): string {
  const match = value.match(/^(-?)(\d+)(?:\.(\d+))?$/); if (!match) return value;
  const [, sign, whole, fraction] = match; return `${sign}${Number(whole).toLocaleString()}${fraction ? `.${fraction.slice(0, 4).replace(/0+$/, "")}` : ""}`.replace(/\.$/, "");
}
export const score = (value: number | null | undefined) => value == null ? "—" : value.toFixed(2);
export const etTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("ko-KR", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) + " ET" : "—";
