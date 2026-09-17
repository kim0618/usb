import { Money } from "@/components/money";
import { EmptyState, InfoTooltip } from "@/components/ui";
import { formatUsd, etTime, moneyText } from "@/lib/format";
import {
  EXIT_REASON_LABELS, POSITION_STATE_LABELS, TRADE_RESULT_META,
  derivedR, derivedReturnPct, holdingTime, numericToneClass, setupLabel, signedPct, signedR,
} from "@/lib/strategy-b";
import type { StrategyPosition, StrategyTrade } from "@/types/strategy-b";

/** Open positions. Return and R are recomputed from the row's own entry, price and stop,
 *  so the table can never show a percentage that its prices do not support. */
export function StrategyBPositions({ positions }: { positions: StrategyPosition[] }) {
  return <section aria-labelledby="strategy-b-positions-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id="strategy-b-positions-title" className="font-semibold">보유 포지션</h2>
      <p className="text-xs text-muted">{positions.length}종목</p>
    </div>
    {positions.length === 0 ? <EmptyState title="보유 중인 포지션이 없습니다."/> : <div className="table-wrap relative"><table className="trading-positions-table analysis-table">
      <caption className="sr-only">전략 B가 보유 중인 포지션</caption>
      <thead><tr>
        <th scope="col">종목</th><th scope="col">셋업</th>
        <th scope="col" className="text-right">진입가</th><th scope="col" className="text-right">현재가</th>
        <th scope="col" className="text-right">수익률</th><th scope="col" className="text-right">손익</th>
        <th scope="col" className="text-right">R<InfoTooltip label="R" text="진입가와 손절가의 차이를 1R로 두고, 현재 수익이 그 몇 배인지 나타냅니다."/></th>
        <th scope="col" className="text-right">Stop</th><th scope="col" className="text-right">보유 시간</th><th scope="col">상태</th>
      </tr></thead>
      <tbody>{positions.map(position => {
        const returnPct = derivedReturnPct(position.entry_usd, position.current_usd);
        const rMultiple = derivedR(position.entry_usd, position.current_usd, position.stop_usd);
        return <tr key={position.symbol} data-position-row={position.symbol}>
          <td className="font-bold text-foreground">{position.symbol}</td>
          <td className="whitespace-nowrap" title={position.setup}>{setupLabel(position.setup)}</td>
          <td className="text-right tabular-nums">{formatUsd(position.entry_usd)}</td>
          <td className="text-right tabular-nums text-foreground">{formatUsd(position.current_usd)}</td>
          <td className={`text-right tabular-nums ${numericToneClass(returnPct)}`}>{signedPct(returnPct)}</td>
          <td className={`text-right tabular-nums ${numericToneClass(position.pnl_krw)}`}><Money krw={position.pnl_krw} signed size="cell"/></td>
          <td className={`text-right tabular-nums ${numericToneClass(rMultiple)}`}>{signedR(rMultiple)}</td>
          <td className="text-right tabular-nums">{formatUsd(position.stop_usd)}</td>
          <td className="text-right tabular-nums">{holdingTime(position.holding_minutes)}</td>
          <td><span className="inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-bold tone-info">{POSITION_STATE_LABELS[position.state]}</span></td>
        </tr>;
      })}</tbody>
    </table></div>}
  </section>;
}

/** Closed trades of the current session, newest exit last. Wins and losses are shown the
 *  same way; the screen never hides a losing row. */
export function StrategyBTrades({ trades }: { trades: StrategyTrade[] }) {
  const netPnl = trades.reduce((total, trade) => total + trade.pnl_krw, 0);
  const wins = trades.filter(trade => trade.result === "WIN").length;
  return <section aria-labelledby="strategy-b-trades-title" className="mb-7">
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id="strategy-b-trades-title" className="font-semibold">당일 종료 거래</h2>
      <p className="text-xs text-muted">{trades.length}건 · {wins}승 {trades.length - wins}패 · 실현 손익 <span className={`font-medium tabular-nums ${numericToneClass(netPnl)}`}>{moneyText(netPnl, true)}</span></p>
    </div>
    {trades.length === 0 ? <EmptyState title="당일 종료된 거래가 없습니다."/> : <div className="table-wrap relative"><table className="analysis-table">
      <caption className="sr-only">전략 B의 당일 종료 거래</caption>
      <thead><tr>
        <th scope="col">종목</th><th scope="col">셋업</th>
        <th scope="col" className="text-right">진입가</th><th scope="col" className="text-right">청산가</th>
        <th scope="col" className="text-right">손익</th><th scope="col" className="text-right">R</th>
        <th scope="col" className="text-right">보유</th><th scope="col">청산 사유</th><th scope="col">결과</th>
      </tr></thead>
      <tbody>{trades.map(trade => {
        const result = TRADE_RESULT_META[trade.result];
        return <tr key={trade.id} data-trade-row={trade.symbol}>
          <td className="font-bold text-foreground" title={etTime(trade.exited_at)}>{trade.symbol}</td>
          <td className="whitespace-nowrap" title={trade.setup}>{setupLabel(trade.setup)}</td>
          <td className="text-right tabular-nums">{formatUsd(trade.entry_usd)}</td>
          <td className="text-right tabular-nums">{formatUsd(trade.exit_usd)}</td>
          <td className={`text-right tabular-nums ${numericToneClass(trade.pnl_krw)}`}><Money krw={trade.pnl_krw} signed size="cell"/></td>
          <td className={`text-right tabular-nums ${numericToneClass(trade.r_multiple)}`}>{signedR(trade.r_multiple)}</td>
          <td className="text-right tabular-nums">{holdingTime(trade.holding_minutes)}</td>
          <td className="whitespace-nowrap" title={trade.exit_reason}>{EXIT_REASON_LABELS[trade.exit_reason]}</td>
          <td><span className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-bold tone-${result.tone}`}>{result.label}</span></td>
        </tr>;
      })}</tbody>
    </table></div>}
  </section>;
}
