"use client";

import { CumulativePerformance, SessionPerformance, SummaryIcon, UsdCardValue } from "@/components/daily-performance";
import { ErrorState, LoadingState, MetricCard, PageHeader } from "@/components/ui";
import { TradingTabs } from "@/components/section-tabs";
import {
  BootstrapProgress, EquitySparkline, EvidenceBooks, StrategyEStatus,
  StrategyPositions, StrategyTrades, UniversePanel,
} from "@/components/strategy-runtime";
import { useApi } from "@/hooks/use-api";
import { evidenceLabel, strategiesApi, STRATEGY_E,
  type StrategyAccount, type StrategyPosition, type StrategyStatus } from "@/lib/strategies";

async function load() {
  const [rows, status, account, positions, trades, equity] = await Promise.all([
    strategiesApi.list(), strategiesApi.status(STRATEGY_E), strategiesApi.account(STRATEGY_E),
    strategiesApi.positions(STRATEGY_E), strategiesApi.trades(STRATEGY_E), strategiesApi.equity(STRATEGY_E),
  ]);
  const row = rows.find(item => item.strategy_id === STRATEGY_E) || status;
  return { row, status, account, positions, trades, equity };
}

/** Strategy E-MAX V1: the frozen rule's realtime state, its evidence grade and its own book. */
export default function StrategyEPage() {
  const state = useApi(load, 60_000);
  const header = <PageHeader eyebrow="전략" title="Strategy E-MAX V1"
    description="09:25 결정 · 09:30 진입 · 09:34 청산. 시장 데이터는 Kiwoom, 주문은 시뮬레이션 전용입니다."/>;

  if (state.loading) return <><TradingTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><TradingTabs/>{header}<ErrorState message={state.error || "Strategy E 상태 조회 실패"} retry={state.refresh}/></>;

  const { status, account, positions, trades, equity } = state.data;
  return <div className="trading-screen">
    <TradingTabs/>
    {header}
    <AccountSummary account={account} positions={positions} status={status}/>
    <StrategyEStatus status={status}/>
    <BootstrapProgress state={status.detail?.bootstrap}/>
    <UniversePanel universe={status.detail?.universe}/>
    <EvidenceBooks equity={equity}/>
    <section aria-labelledby="e-equity-title" className="panel mb-7 p-5">
      <h2 id="e-equity-title" className="mb-3 font-semibold">자산 추이 · {evidenceLabel(equity.current_book)}</h2>
      <EquitySparkline equity={equity} height={96}/>
    </section>
    <section aria-labelledby="e-positions-title" className="mb-7">
      <h2 id="e-positions-title" className="mb-3 font-semibold">보유 포지션</h2>
      <StrategyPositions positions={positions}/>
    </section>
    <section aria-labelledby="e-trades-title" className="mb-7">
      <h2 id="e-trades-title" className="mb-3 font-semibold">거래 기록</h2>
      <StrategyTrades trades={trades}/>
    </section>
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
  const evidence = evidenceLabel(account.evidence_status).replace(" PAPER", "");   // the card meta is narrow
  return <section aria-label="계좌 요약" className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
    <MetricCard accent="primary" icon={<SummaryIcon type="wallet"/>} label="총 자산 (USD)" meta="가상계좌"
      value={<UsdCardValue value={account.current_equity}/>}/>
    <MetricCard accent="indigo" icon={<SummaryIcon type="layers"/>} label="투자 중" meta="포지션"
      value={<UsdCardValue value={invested}/>}/>
    <MetricCard accent="gold" icon={<SummaryIcon type="cash"/>} label="보유 현금" meta="주문 가능"
      value={<span className="text-foreground-secondary">-</span>}
      detail="E 런타임은 현금 잔고를 기록하지 않습니다"/>
    <MetricCard accent="green" icon={<SummaryIcon type="trend"/>} label="누적 수익" meta={evidence}
      value={<CumulativePerformance pnl={settled ? account.total_pnl : null} baseline={account.initial_equity}
        note={settled ? undefined : account.empty_reason || "기록된 세션 없음"}/>}
      detail="PROVISIONAL과 OFFICIAL 누적은 합산하지 않습니다"/>
    <MetricCard accent="bluegreen" icon={<SummaryIcon type="pulse"/>} label="직전 거래일 손익" meta="완료 세션"
      value={<SessionPerformance session={status.session} pnl={settled ? account.today_pnl : null}/>}/>
  </section>;
}
