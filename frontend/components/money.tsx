import { formatKrw, formatSignedKrw, formatSignedUsd, formatUsd } from "@/lib/format";
import { krwToUsd } from "@/lib/fx";

/** Where the amount sits. `card` matches Strategy A's account cards, `figure` is a smaller
 *  KPI value, `cell` is a table cell that must not grow the row much. */
type MoneySize = "card" | "figure" | "cell";

const SECONDARY_CLASS: Readonly<Record<MoneySize, string>> = {
  card: "mt-1 block text-sm font-medium text-muted",
  figure: "mt-0.5 block text-xs font-medium text-muted",
  cell: "block text-[11px] font-normal leading-4 text-muted",
};

/** An account, cash or P&L amount: USD primary, KRW secondary. Share prices are never
 *  passed here; they stay USD only.
 *
 *  The Strategy B and comparison mocks hold KRW, so KRW is the stored value and USD is
 *  derived through lib/fx. The KRW line prints that stored value itself, never a round trip
 *  through the rounded USD, so the same KRW amount reads the same on every screen. */
export function Money({ krw, signed = false, size = "figure" }: { krw: number; signed?: boolean; size?: MoneySize }) {
  const usd = krwToUsd(krw);
  const krwText = signed ? formatSignedKrw(krw) : formatKrw(krw);
  return <>
    <span className="block" data-money-usd="">{signed ? formatSignedUsd(usd) : formatUsd(usd)}</span>
    <span className={SECONDARY_CLASS[size]} data-money-krw="">{size === "cell" ? krwText : `≈\u00a0${krwText}`}</span>
  </>;
}
