"use client";

/** Pick a strategy, see its record. Nothing else.
 *
 *  The table is the one the trading screen already uses (``StrategyTrades``): session, symbol,
 *  state, entry, exit, pnl. Strategy H has no trades, so it shows its decisions in the same flat
 *  shape instead of an empty table. */

import { StrategyPositions, StrategyTrades } from "@/components/strategy-runtime";
import { EmptyState, LoadingState, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { STRATEGY_H, strategiesApi, type DailyView } from "@/lib/strategies";

export function StrategyPicker({ strategies, picked, onPick }: {
  strategies: DailyView["strategies"]; picked: string | null; onPick: (id: string | null) => void;
}) {
  return <nav aria-label="전략 선택" className="mb-4 flex flex-wrap gap-2" data-strategy-picker={picked || ""}>
    {strategies.map(s => {
      const active = picked === s.strategy_id;
      return <button key={s.strategy_id} type="button" aria-pressed={active}
        data-pick={s.strategy_id}
        onClick={() => onPick(active ? null : s.strategy_id)}
        className={`inline-flex h-9 items-center rounded-lg border px-3.5 text-sm font-semibold transition-colors focus-visible:ring-2 focus-visible:ring-primary ${active ? "border-primary bg-primary-soft text-primary" : "border-line bg-surface text-foreground-secondary hover:border-primary hover:text-primary"}`}>
        {s.display_name}
      </button>;
    })}
  </nav>;
}

export function StrategyRecord({ strategyId, label }: { strategyId: string; label: string }) {
  return strategyId === STRATEGY_H ? <HRecord label={label}/> : <TradeRecord strategyId={strategyId} label={label}/>;
}

function TradeRecord({ strategyId, label }: { strategyId: string; label: string }) {
  const state = useApi(() => Promise.all([
    strategiesApi.positions(strategyId), strategiesApi.trades(strategyId, 100),
  ]), 60_000);
  if (state.loading) return <LoadingState/>;
  if (!state.data) return <EmptyState title={`${label} 기록 조회 실패`} description={state.error || undefined}/>;
  const [positions, trades] = state.data;
  return <section aria-labelledby="record-title" className="mb-7 min-w-0" data-record={strategyId}>
    <h2 id="record-title" className="mb-3 font-semibold">{label} · 기록</h2>
    {positions.length > 0 && <div className="mb-5">
      <h3 className="mb-2 text-sm font-semibold">보유 중</h3>
      <div className="table-wrap relative"><StrategyPositions positions={positions}/></div>
    </div>}
    <h3 className="mb-2 text-sm font-semibold">거래 기록</h3>
    <div className="table-wrap relative"><StrategyTrades trades={trades}/></div>
  </section>;
}

/** H trades nothing, so its record is its decisions, in the same flat shape. */
function HRecord({ label }: { label: string }) {
  const state = useApi(() => strategiesApi.forward(), 60_000);
  if (state.loading) return <LoadingState/>;
  if (!state.data) return <EmptyState title={`${label} 기록 조회 실패`} description={state.error || undefined}/>;
  const rows = state.data.rows;
  return <section aria-labelledby="record-title" className="mb-7 min-w-0" data-record={STRATEGY_H}>
    <h2 id="record-title" className="mb-1 font-semibold">{label} · 기록</h2>
    <p className="mb-3 text-xs text-muted">거래 대신 종목별 결정을 관찰합니다. 포지션은 없습니다.</p>
    {rows.length === 0 ? <EmptyState title="관찰 중인 종목이 없습니다."/>
      : <div className="table-wrap relative"><table className="data-table">
          <thead><tr>
            <th scope="col">종목</th><th scope="col">결정</th>
            <th scope="col">현재가</th><th scope="col">TP1</th><th scope="col">Bear</th>
            <th scope="col">결정 세션</th>
          </tr></thead>
          <tbody>{rows.map(row => <tr key={row.ticker} data-h-record={row.ticker}>
            <td className="font-semibold">{row.ticker}</td>
            <td><StatusBadge value={row.decision || "-"}
              tone={row.decision === "WATCH" ? "warning" : row.decision === "REJECT" ? "danger" : "success"}/></td>
            <td className="tabular-nums">{row.current_price == null ? "-" : `$${row.current_price.toFixed(2)}`}</td>
            <td className="tabular-nums">{row.tp1 == null ? "-" : `$${row.tp1.toFixed(2)}`}</td>
            <td className="tabular-nums" title={row.bear_na_reason || undefined}>
              {row.bear == null ? "N/A" : `$${row.bear.toFixed(2)}`}</td>
            <td className="tabular-nums">{row.pre_launch_drift.decision_session}</td>
          </tr>)}</tbody>
        </table></div>}
  </section>;
}
