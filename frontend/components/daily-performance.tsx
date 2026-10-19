import { formatKrw, formatSignedKrw, formatSignedUsd, formatUsd, usdToDisplayKrw } from "@/lib/format";

/** The PnL tone, exported so every screen colours money the same way. */
export const pnlTone = (value: string) => {
  const amount = Number(value);
  return amount < 0 ? "text-danger" : amount > 0 ? "text-success" : "text-foreground-secondary";
};

const signedReturn = (value: string) => {
  const percentage = Number(value) * 100;
  return `${percentage >= 0 ? "+" : ""}${percentage.toFixed(2)}%`;
};

/** An account amount on a summary card: USD primary, KRW secondary, exactly as Strategy A's
 *  account cards render it. */
export function UsdCardValue({ value }: { value?: string | null }) {
  if (value == null) return <span className="text-foreground-secondary">-</span>;
  return <>{formatUsd(value)}<span className="mt-1 block text-sm font-medium text-muted">{formatKrw(usdToDisplayKrw(value))}</span></>;
}

/** The five summary-card icons. Defined once so Strategy A and Strategy E draw the same row. */
export const SummaryIcon = ({ type }: { type: "wallet" | "layers" | "cash" | "trend" | "pulse" }) => {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (type === "wallet") return <svg viewBox="0 0 24 24" {...common}><path d="M4 7.5h15a1 1 0 0 1 1 1v10H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2h12v4"/><path d="M16 12h4v3h-4a1.5 1.5 0 0 1 0-3Z"/></svg>;
  if (type === "layers") return <svg viewBox="0 0 24 24" {...common}><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></svg>;
  if (type === "cash") return <svg viewBox="0 0 24 24" {...common}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9a2 2 0 0 1-2 2v2a2 2 0 0 1 2 2M17 9a2 2 0 0 0 2 2v2a2 2 0 0 0-2 2"/><circle cx="12" cy="12" r="2.5"/></svg>;
  if (type === "trend") return <svg viewBox="0 0 24 24" {...common}><path d="M4 18V6M4 18h16M7 15l4-4 3 2 5-6"/><path d="M16 7h3v3"/></svg>;
  return <svg viewBox="0 0 24 24" {...common}><path d="M3 12h4l2-6 4 12 2-6h6"/><circle cx="12" cy="12" r="9" opacity=".35"/></svg>;
};

const SignedMoney = ({ value }: { value: string }) => <>{formatSignedUsd(value)}<span className="mt-0.5 block text-xs">{formatSignedKrw(usdToDisplayKrw(value))}</span></>;

/** A signed amount on a card: USD primary, KRW secondary, Strategy A's tone. A missing value is a
 *  dash, never a converted zero. */
export function SignedMoneyValue({ value, note }: { value?: string | null; note?: string }) {
  if (value == null) return <span className="text-foreground-secondary">-{note && <span className="mt-1 block text-xs font-medium text-muted">{note}</span>}</span>;
  return <span className={pnlTone(value)}><SignedMoney value={value}/></span>;
}

/** The cumulative-return card value. Both numbers come from the backend: the strategy's own
 *  cumulative PnL and the baseline equity that PnL was measured from. The percentage is their
 *  ratio, the same relation the API already uses between ``daily_pnl`` and ``opening_equity``;
 *  no separate cumulative definition is computed here. Tone classes are the shared PnL ones. */
export function CumulativePerformance({ pnl, baseline, note }: { pnl?: string | null; baseline?: string | null; note?: string }) {
  if (pnl == null) return <span className="text-foreground-secondary">-{note && <span className="mt-1 block text-xs font-medium text-muted">{note}</span>}</span>;
  const tone = pnlTone(pnl);
  const base = baseline == null ? Number.NaN : Number(baseline);
  const ratio = Number.isFinite(base) && base !== 0 ? String(Number(pnl) / base) : null;
  return <span className={tone}>{formatSignedUsd(pnl)}
    <span className="mt-0.5 block text-sm">{formatSignedKrw(usdToDisplayKrw(pnl))}</span>
    {ratio && <span className={`mt-1 block text-xs font-semibold ${tone}`}>{signedReturn(ratio)}</span>}
    {note && <span className="mt-1 block text-xs font-medium text-muted">{note}</span>}</span>;
}

/** One completed session's realized PnL, as the strategy's own book recorded it. */
export function SessionPerformance({ session, pnl }: { session?: string | null; pnl?: string | null }) {
  if (pnl == null) return <span className="text-foreground-secondary">-{session && <span className="mt-1 block text-xs font-medium text-muted">{session}</span>}</span>;
  const tone = pnlTone(pnl);
  return <>{session && <span className="block text-xs font-medium text-muted">{session.slice(5).replace("-", "/")}</span>}
    <span className={`mt-1 block ${tone}`}>{formatSignedUsd(pnl)}
      <span className="mt-0.5 block text-sm">{formatSignedKrw(usdToDisplayKrw(pnl))}</span></span></>;
}
