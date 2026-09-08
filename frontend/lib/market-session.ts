export type MarketSession = "PREMARKET" | "REGULAR" | "POSTMARKET";

export interface MarketSessionItem {
  key: MarketSession;
  label: string;
  kstRange: string;
}

const NEW_YORK = "America/New_York";
const SEOUL = "Asia/Seoul";

function timeZoneOffsetMinutes(date: Date, timeZone: string): number {
  const name = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "longOffset" })
    .formatToParts(date).find(part => part.type === "timeZoneName")?.value;
  const match = name?.match(/^GMT([+-])(\d{2}):(\d{2})$/);
  if (!match) throw new Error(`Unable to resolve timezone offset for ${timeZone}`);
  const minutes = Number(match[2]) * 60 + Number(match[3]);
  return match[1] === "+" ? minutes : -minutes;
}

function newYorkTime(tradingDate: string, hour: number, minute: number): Date {
  const [year, month, day] = tradingDate.split("-").map(Number);
  const localAsUtc = Date.UTC(year, month - 1, day, hour, minute);
  const offset = timeZoneOffsetMinutes(new Date(Date.UTC(year, month - 1, day, 12)), NEW_YORK);
  return new Date(localAsUtc - offset * 60_000);
}

function kstTime(value: Date): string {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: SEOUL, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(value);
}

export function marketSessionSchedule(tradingDate: string, marketOpen?: string | null, marketClose?: string | null): MarketSessionItem[] {
  const open = marketOpen ? new Date(marketOpen) : newYorkTime(tradingDate, 9, 30);
  const close = marketClose ? new Date(marketClose) : newYorkTime(tradingDate, 16, 0);
  const range = (start: Date, end: Date) => `${kstTime(start)} ~ ${kstTime(end)}`;
  return [
    { key: "PREMARKET", label: "프리마켓", kstRange: range(newYorkTime(tradingDate, 4, 0), open) },
    { key: "REGULAR", label: "정규장", kstRange: range(open, close) },
    { key: "POSTMARKET", label: "애프터마켓", kstRange: range(close, newYorkTime(tradingDate, 20, 0)) },
  ];
}
