"use client";

import Link from "next/link";
import { useState } from "react";
import { EmptyState, ErrorState, LoadingState, MetricCard, StatusBadge } from "@/components/ui";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { formatExecutionBroker, formatOrderRejectionReason, formatOrderSide, formatOrderStatus, strategyStatusDisplay, formatTradeStatus } from "@/lib/display";
import { currency, decimal, etTime, signedDecimal } from "@/lib/format";
import { composeTradingHistory, historySideLabel, tradingHistoryEventLabel, type TradingHistoryEventType, type TradingHistoryRow } from "@/lib/trading-history";
import type { TradingAccount, TradingPosition } from "@/types/api";

const accountValue = (account: TradingAccount | null, field: keyof TradingAccount) => currency(account?.[field] as string | null | undefined, account?.currency);
const positionValue = (position: TradingPosition, field: keyof TradingPosition) => currency(position[field] as string | null | undefined, position.currency);
const pnlTone = (value?: string | null) => {
  const numericValue = value == null ? Number.NaN : Number(value);
  return numericValue < 0 ? "text-danger" : numericValue > 0 ? "text-success" : "text-foreground-secondary";
};
const isOpenOrder = (status: string) => status === "PENDING" || status === "PARTIALLY_FILLED";
const historyFilters: Array<{ value: TradingHistoryEventType; label: string }> = [{ value: "TRADE", label: "청산" }, { value: "ORDER", label: "주문" }, { value: "FILL", label: "체결" }];
const historyEmptyLabel: Readonly<Record<TradingHistoryEventType, string>> = { ORDER: "주문 내역이 없습니다.", FILL: "체결 내역이 없습니다.", TRADE: "청산 내역이 없습니다." };
const historyRowTone = (row: TradingHistoryRow) => row.eventType === "TRADE" && row.rawSource.net_pnl.startsWith("-") ? "bg-danger-soft" : row.eventType === "ORDER" && row.status === "REJECTED" ? "bg-danger-soft" : row.eventType === "ORDER" && (row.status === "CANCELLED" || row.status === "PARTIALLY_FILLED") ? "bg-warning-soft" : "";
const SummaryIcon = ({ type }: { type: "wallet" | "layers" | "cash" | "trend" | "pulse" }) => {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (type === "wallet") return <svg viewBox="0 0 24 24" {...common}><path d="M4 7.5h15a1 1 0 0 1 1 1v10H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2h12v4"/><path d="M16 12h4v3h-4a1.5 1.5 0 0 1 0-3Z"/></svg>;
  if (type === "layers") return <svg viewBox="0 0 24 24" {...common}><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></svg>;
  if (type === "cash") return <svg viewBox="0 0 24 24" {...common}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9a2 2 0 0 1-2 2v2a2 2 0 0 1 2 2M17 9a2 2 0 0 0 2 2v2a2 2 0 0 0-2 2"/><circle cx="12" cy="12" r="2.5"/></svg>;
  if (type === "trend") return <svg viewBox="0 0 24 24" {...common}><path d="M4 18V6M4 18h16M7 15l4-4 3 2 5-6"/><path d="M16 7h3v3"/></svg>;
  return <svg viewBox="0 0 24 24" {...common}><path d="M3 12h4l2-6 4 12 2-6h6"/><circle cx="12" cy="12" r="9" opacity=".35"/></svg>;
};

export default function TradingPage() {
  const [historyFilter, setHistoryFilter] = useState<TradingHistoryEventType>("TRADE");
  const [expandedHistoryId, setExpandedHistoryId] = useState<string | null>(null);
  const overview = useApi(api.trading, 10000); const dashboard = useApi(api.dashboard, 20000); const research = useApi(api.research, 20000);
  const orders = useApi(api.orders, 10000); const fills = useApi(api.fills, 10000); const trades = useApi(api.trades, 10000);
  if (overview.loading) return <LoadingState/>;
  if (!overview.data) return <ErrorState message={overview.error || "매매 상태 조회 실패"} retry={overview.refresh}/>;

  const o = overview.data; const account = o.account; const openSymbols = new Set(o.open_positions.map(position => position.symbol));
  const researchIsToday = !!dashboard.data && research.data?.analysis.trading_date === dashboard.data.market.trading_date;
  const waiting = researchIsToday ? research.data!.candidates.filter(candidate => candidate.human_decision?.decision === "APPROVE" && !openSymbols.has(candidate.symbol)).slice(0, 2) : [];
  const waitingLoading = dashboard.loading || research.loading;
  const pendingOrders = orders.data?.filter(order => isOpenOrder(order.status)).length || 0;
  const history = composeTradingHistory(orders.data || [], fills.data || [], trades.data || []);
  const visibleHistory = history.filter(row => row.eventType === historyFilter);

  return <div className="trading-screen">
    <section aria-label="계좌 요약" className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <MetricCard accent="primary" icon={<SummaryIcon type="wallet"/>} label={`총 자산${account?.currency ? ` (${account.currency})` : ""}`} meta="계좌" detail="Broker account 기준" value={accountValue(account, "equity")}/>
      <MetricCard accent="indigo" icon={<SummaryIcon type="layers"/>} label="투자 중" meta="포지션" detail="보유 종목 기준" value={accountValue(account, "invested_notional")}/>
      <MetricCard accent="gold" icon={<SummaryIcon type="cash"/>} label="보유 현금" meta="주문 가능" detail="Broker 주문 가능 기준" value={accountValue(account, "cash")}/>
      <MetricCard accent="green" icon={<SummaryIcon type="trend"/>} label="평가 손익" meta="평가" detail="실시간 평가 기준" value={<span className={pnlTone(account?.unrealized_pnl)}>{account?.unrealized_pnl == null ? "-" : signedDecimal(account.unrealized_pnl, account.currency === "KRW" ? "원" : account.currency === "USD" ? " USD" : "")}</span>}/>
      <MetricCard accent="bluegreen" icon={<SummaryIcon type="pulse"/>} label="오늘 손익" meta="금일" detail="당일 체결 기준" value={<span className={pnlTone(account?.today_pnl)}>{account?.today_pnl == null ? "-" : signedDecimal(account.today_pnl, account.currency === "KRW" ? "원" : account.currency === "USD" ? " USD" : "")}</span>}/>
    </section>
    {o.availability === "NO_ACTIVE_SIM_BROKER" && <div className="mb-4 rounded-lg border border-line bg-surface-alt px-4 py-2.5 text-xs text-muted"><span className="font-medium text-foreground-secondary">활성 Simulation Broker 없음</span><span className="ml-2">계좌와 현재 포지션 truth는 연결 후 표시되며, 저장된 주문·체결·매매 기록은 계속 제공합니다.</span></div>}

    <section className="mb-7"><div className="mb-3"><h2 className="font-semibold">현재 보유 종목</h2></div>{o.open_positions.length ? <div className="table-wrap"><table className="trading-positions-table"><thead><tr><th>종목</th><th>투자금</th><th>보유 수량</th><th>평균단가</th><th>현재가</th><th>평가금액</th><th>손익</th><th>수익률</th><th>현재 Stop</th><th>전략 상태</th></tr></thead><tbody>{o.open_positions.map(position => { const status = position.phase ? strategyStatusDisplay(position.phase) : null; return <tr key={position.symbol}><td className="font-bold text-foreground">{position.symbol}</td><td>{positionValue(position, "invested_notional")}</td><td>{decimal(position.quantity)}</td><td>{positionValue(position, "average_price")}</td><td>{positionValue(position, "mark_price")}</td><td>{positionValue(position, "market_value")}</td><td className={pnlTone(position.unrealized_pnl)}>{signedDecimal(position.unrealized_pnl)}</td><td className={pnlTone(position.return_pct)}>{signedDecimal(position.return_pct, "%")}</td><td>{positionValue(position, "active_stop")}</td><td>{status ? <StatusBadge value={position.phase!} label={status.label} tone={status.tone}/> : "-"}</td></tr>; })}</tbody></table></div> : <EmptyState title="보유 중인 포지션이 없습니다." description={o.availability === "NO_ACTIVE_SIM_BROKER" ? "활성 브로커 연결 전에는 현재 포지션을 확인할 수 없습니다." : undefined}/>}</section>

    <div className="mb-7 grid items-stretch gap-5 xl:grid-cols-2">
      <section className="flex flex-col"><h2 className="mb-3 font-semibold">오늘 손익 상세</h2><div className="panel pnl-metric-grid grid flex-1 grid-cols-2 sm:grid-cols-4">{[["실현 손익", account?.realized_pnl], ["평가 손익", account?.unrealized_pnl], ["비용", null], ["순손익", account?.today_pnl]].map(([label, value]) => <div className={`pnl-metric ${label === "순손익" ? "pnl-metric-net" : ""}`} key={label}><p className="label">{label}</p><p className={`mt-2 text-lg font-semibold ${pnlTone(value)}`}>{signedDecimal(value)}</p></div>)}</div></section>
      <section className="flex flex-col"><div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">진입 대기</h2><Link href="/research" className="btn-action-secondary-compact">분석 보기</Link></div>{waiting.length ? <div className="grid flex-1 auto-rows-fr gap-3 sm:grid-cols-2">{waiting.map(candidate => <div className="panel flex items-center justify-between p-4" key={candidate.symbol}><div><p className="text-lg font-bold text-foreground">{candidate.symbol}</p></div><StatusBadge value="APPROVE" label="진입 대기"/></div>)}</div> : <EmptyState title={waitingLoading ? "진입 대기 종목을 확인하고 있습니다." : "승인 후 아직 진입하지 않은 종목이 없습니다."}/>}</section>
    </div>

    <section aria-labelledby="trading-history-title">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><h2 id="trading-history-title" className="font-semibold">매매 내역</h2>{pendingOrders > 0 && <span className="rounded-full border border-warning bg-warning-soft px-3 py-1 text-xs text-warning">미체결 주문 {pendingOrders}건</span>}</div><div className="flex flex-wrap gap-1" aria-label="매매 내역 필터">{historyFilters.map(filter => <button key={filter.value} type="button" aria-pressed={historyFilter === filter.value} onClick={() => { setHistoryFilter(filter.value); setExpandedHistoryId(null); }} className={`btn-compact ${historyFilter === filter.value ? "btn-compact-active" : ""}`}>{filter.label}</button>)}</div></div>
      {visibleHistory.length ? <div className="table-wrap"><table><thead><tr><th>시각</th><th>종목</th><th>구분</th><th>방향</th><th>수량</th><th>가격</th><th>상태 / 결과</th></tr></thead><tbody>{visibleHistory.map(row => <HistoryRows key={row.id} row={row} expanded={expandedHistoryId === row.id} onToggle={() => setExpandedHistoryId(current => current === row.id ? null : row.id)}/>)}</tbody></table></div> : <EmptyState title={historyEmptyLabel[historyFilter]}/>}
    </section>
  </div>;
}

function HistoryRows({ row, expanded, onToggle }: { row: TradingHistoryRow; expanded: boolean; onToggle: () => void }) {
  const resultTone = row.eventType === "TRADE" ? pnlTone(row.rawSource.net_pnl) : "";
  return <>
    <tr className={historyRowTone(row)}><td>{etTime(row.timestamp)}</td><td><button type="button" className="font-bold text-foreground underline-offset-4 hover:underline focus:underline" aria-expanded={expanded} aria-controls={`${row.id}-detail`} onClick={onToggle}>{row.symbol}<span className="sr-only"> 상세 {expanded ? "접기" : "보기"}</span></button></td><td><StatusBadge value={row.eventType} label={tradingHistoryEventLabel[row.eventType]}/></td><td>{historySideLabel(row)}</td><td>{decimal(row.quantity)}</td><td>{decimal(row.price)}</td><td className={resultTone}>{row.eventType === "ORDER" ? <div><StatusBadge value={row.status} label={formatOrderStatus(row.status)}/>{row.rawSource.rejection_reason && <p className="mt-1 text-xs text-muted">{formatOrderRejectionReason(row.rawSource.rejection_reason)}</p>}</div> : row.result}</td></tr>
    {expanded && <tr id={`${row.id}-detail`}><td colSpan={7} className="bg-surface-alt p-4"><HistoryDetail row={row}/></td></tr>}
  </>;
}

function HistoryDetail({ row }: { row: TradingHistoryRow }) {
  if (row.eventType === "ORDER") { const order = row.rawSource; return <DetailList items={[["Order ID", order.order_id], ["Provider", formatExecutionBroker(order.broker_type)], ["주문 수량", decimal(order.requested_quantity)], ["체결 수량", decimal(order.filled_quantity)], ["기준가", decimal(order.reference_price)], ["방향", formatOrderSide(order.side)], ["상태", formatOrderStatus(order.status)], ["거절 사유", formatOrderRejectionReason(order.rejection_reason)], ["주문 시각", etTime(order.submitted_at)], ["완료 시각", etTime(order.completed_at)]]}/>; }
  if (row.eventType === "FILL") { const fill = row.rawSource; return <DetailList items={[["Fill ID", fill.fill_id], ["Order ID", fill.order_id], ["체결가", decimal(fill.fill_price)], ["체결 수량", decimal(fill.quantity)], ["Spread", decimal(fill.spread_cost)], ["Slippage", decimal(fill.slippage_cost)], ["수수료", decimal(fill.commission)], ["FX 비용", decimal(fill.fx_cost)], ["총비용", decimal(fill.total_cost)], ["체결 시각", etTime(fill.filled_at)]]}/>; }
  const trade = row.rawSource; return <DetailList items={[["Trade ID", trade.id], ["진입가", decimal(trade.entry)], ["청산가", decimal(trade.exit)], ["Gross PnL", signedDecimal(trade.gross_pnl)], ["Net PnL", signedDecimal(trade.net_pnl)], ["Gross R", signedDecimal(trade.gross_r, "R")], ["Net R", signedDecimal(trade.net_r, "R")], ["비용", decimal(trade.total_cost)], ["보유 기간", `${trade.holding_duration}일`], ["청산 사유", trade.exit_reason || "-"], ["상태", formatTradeStatus(trade.status)]]}/>;
}

function DetailList({ items }: { items: Array<[string, string]> }) {
  return <dl className="grid gap-x-5 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">{items.map(([label, value]) => <div key={label}><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 break-all text-foreground">{value}</dd></div>)}</dl>;
}
