"use client";

import { CumulativePerformance, SessionPerformance, SummaryIcon, UsdCardValue } from "@/components/daily-performance";
import { ErrorState, LoadingState, MetricCard, PageHeader } from "@/components/ui";
import { TradingTabs } from "@/components/section-tabs";
import { DailyPnl, Holdings, TodayDecision } from "@/components/strategy-e-operations";
import { useApi } from "@/hooks/use-api";
import { OFFICIAL, strategiesApi, STRATEGY_E,
  type StrategyAccount, type StrategyPosition, type StrategyStatus } from "@/lib/strategies";

async function load() {
  const [rows, status, account, positions, trades, daily, cards] = await Promise.all([
    strategiesApi.list(), strategiesApi.status(STRATEGY_E), strategiesApi.account(STRATEGY_E),
    strategiesApi.positions(STRATEGY_E), strategiesApi.trades(STRATEGY_E), strategiesApi.daily(60),
    strategiesApi.cards(),
  ]);
  const row = rows.find(item => item.strategy_id === STRATEGY_E) || status;
  const card = cards.find(item => item.strategy_id === STRATEGY_E);
  return { row, status, account, positions, trades, daily, decidedAt: card?.last_signal_at ?? null };
}

/** Strategy E (E-MAX V1): the same operating layout as Strategy A. KPI, holdings, today's decision,
 *  daily PnL. Research and runtime internals live on the API and the detail screens. */
export default function StrategyEPage() {
  const state = useApi(load, 60_000);
  // The name is the registry's (display_name), so this screen never says "B" or any label the
  // backend did not send. Until the registry answers, the header waits with a neutral title.
  const row = state.data?.row;
  const header = <PageHeader title={row ? row.display_name : "전략 상태"} description="09:25 결정 · 09:30 진입 · 09:34 청산"/>;

  if (state.loading) return <><TradingTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><TradingTabs/>{header}<ErrorState message={state.error || "Strategy E 상태 조회 실패"} retry={state.refresh}/></>;

  const { status, account, positions, trades, daily, decidedAt } = state.data;
  const official = account.evidence_status === OFFICIAL;
  return <div className="trading-screen">
    <TradingTabs/>
    {header}
    <p className="-mt-4 mb-5 text-xs text-muted" data-ledger={account.evidence_status || ""}
      title="PROVISIONAL과 OFFICIAL 누적은 합산하지 않습니다">
      {official ? "Official Paper 기준" : "Provisional Paper 기준 (공식 평가 전)"}</p>
    <AccountSummary account={account} positions={positions} status={status}/>
    <Holdings positions={positions}/>
    <TodayDecision status={status} account={account} decidedAt={decidedAt}/>
    <DailyPnl daily={daily} trades={trades}/>
  </div>;
}


/** The same five summary cards Strategy A's screen carries, in the same order, filled from Strategy
 *  E's own book. The active evidence book alone answers here: a provisional figure is never added to
 *  an official one, and a book with no session shows "-" rather than a manufactured zero. */
function AccountSummary({ account, positions, status }: {
  account: StrategyAccount; positions: StrategyPosition[]; status: StrategyStatus;
}) {
  const book = account.books?.[account.evidence_status || ""];
  const settled = (book?.sessions ?? 0) > 0;
  const invested = positions.length
    ? String(positions.reduce((total, position) => total + Number(position.cost_basis ?? 0), 0))
    : account.open_positions === 0 ? "0" : null;
  return <section aria-label="계좌 요약" className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
    <MetricCard accent="primary" icon={<SummaryIcon type="wallet"/>} label="총 자산 (USD)" meta="가상계좌"
      value={<UsdCardValue value={account.current_equity}/>}/>
    <MetricCard accent="indigo" icon={<SummaryIcon type="layers"/>} label="투자 중" meta="포지션"
      value={<UsdCardValue value={invested}/>}/>
    <MetricCard accent="gold" icon={<SummaryIcon type="cash"/>} label="보유 현금" meta="주문 가능"
      value={<span className="text-foreground-secondary">-</span>}
      detail="기록 없음"/>
    <MetricCard accent="green" icon={<SummaryIcon type="trend"/>} label="누적 수익" meta="전략 성과"
      value={<CumulativePerformance pnl={settled ? account.total_pnl : null} baseline={account.initial_equity}
        note={settled ? undefined : account.empty_reason || "기록된 세션 없음"}/>}
      />
    <MetricCard accent="bluegreen" icon={<SummaryIcon type="pulse"/>} label="직전 거래일 손익" meta="완료 세션"
      value={<SessionPerformance session={status.session} pnl={settled ? account.today_pnl : null}/>}/>
  </section>;
}
