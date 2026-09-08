import { EmptyState } from "@/components/ui";
import { formatKrw, formatSignedKrw, formatSignedUsd, formatUsd, usdToDisplayKrw } from "@/lib/format";
import type { DailyPerformance } from "@/types/api";

const pnlTone = (value: string) => {
  const amount = Number(value);
  return amount < 0 ? "text-danger" : amount > 0 ? "text-success" : "text-foreground-secondary";
};

const signedReturn = (value: string) => {
  const percentage = Number(value) * 100;
  return `${percentage >= 0 ? "+" : ""}${percentage.toFixed(2)}%`;
};

const SignedMoney = ({ value }: { value: string }) => <>{formatSignedUsd(value)}<span className="mt-0.5 block text-xs">{formatSignedKrw(usdToDisplayKrw(value))}</span></>;

export function PreviousSessionPerformance({ row }: { row?: DailyPerformance | null }) {
  if (!row) return <span className="text-foreground-secondary">-</span>;
  const tone = pnlTone(row.daily_pnl);
  return <><span className="block text-xs font-medium text-muted">{row.trading_date.slice(5).replace("-", "/")}</span><span className={`mt-1 block ${tone}`}>{formatSignedUsd(row.daily_pnl)}<span className="mt-0.5 block text-sm">{formatSignedKrw(usdToDisplayKrw(row.daily_pnl))}</span><span className={`mt-1 block text-xs font-semibold ${tone}`}>{signedReturn(row.daily_return)}</span></span></>;
}

export function DailyPerformanceTable({ rows, loading = false }: { rows?: DailyPerformance[] | null; loading?: boolean }) {
  if (!rows?.length) return <EmptyState title={loading ? "일별 손익 기록을 확인하고 있습니다." : "아직 일별 손익 기록이 없습니다."}/>;
  return <div className="table-wrap"><table><thead><tr><th>날짜</th><th>일 손익</th><th>수익률</th><th>누적 손익</th><th>누적 자산</th></tr></thead><tbody>{rows.map(day => <tr key={day.trading_date}><td className="font-medium text-foreground">{day.trading_date.slice(5).replace("-", "/")}</td><td className={pnlTone(day.daily_pnl)}><SignedMoney value={day.daily_pnl}/></td><td className={pnlTone(day.daily_return)}>{signedReturn(day.daily_return)}</td><td className={pnlTone(day.cumulative_pnl)}><SignedMoney value={day.cumulative_pnl}/></td><td className="text-foreground">{formatUsd(day.closing_equity)}<span className="mt-0.5 block text-xs text-muted">{formatKrw(usdToDisplayKrw(day.closing_equity))}</span></td></tr>)}</tbody></table></div>;
}
