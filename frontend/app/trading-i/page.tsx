"use client";

import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusBadge } from "@/components/ui";
import { TradingTabs } from "@/components/section-tabs";
import { useApi } from "@/hooks/use-api";
import { formatSignedUsd, formatUsd, kstTime } from "@/lib/format";
import { STATUS_LABEL, STATUS_TONE, strategyIApi, type IPaperView } from "@/lib/strategy-i";

const usd = (v: number | null | undefined) => v == null ? "-" : formatUsd(v.toFixed(2));
const signedUsd = (v: number | null | undefined) => v == null ? "-" : formatSignedUsd(v.toFixed(2));
const pct = (v: number | null | undefined, digits = 2) => v == null ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
const share = (v: number | null | undefined) => v == null ? "-" : `${(v * 100).toFixed(1)}%`;
const md = (session: string) => session.slice(5).replace("-", "/");

/** Strategy I (MOM12-2 V2.1) inside the Trading menu. Its own paper store only: no order is ever sent,
 *  the industry overlay is a separate virtual record, and before launch the screen shows the state
 *  and nothing else (no capital card, no position, no return). */
export default function TradingIPage() {
  const state = useApi(strategyIApi.paper, 60_000);
  const header = <PageHeader title="Strategy I · MOM12-2" description="월간 모멘텀 · 가상 계좌 · 실제 주문 없음"/>;
  if (state.loading) return <><TradingTabs/>{header}<LoadingState/></>;
  if (!state.data) return <><TradingTabs/>{header}<ErrorState message={state.error || "Strategy I 상태 조회 실패"} retry={state.refresh}/></>;
  const v = state.data;
  return <div className="trading-screen" data-i-status={v.status}>
    <TradingTabs/>
    {header}
    <StatusPanel view={v}/>
    {v.account ? <>
      <Account view={v}/>
      <Positions view={v}/>
      <Rebalances view={v}/>
      <Trades view={v}/>
      <Continuity view={v}/>
      <Overlay view={v}/>
    </> : <EmptyState size="medium" title={emptyTitle(v)} description="시작 전에는 자산, 보유 종목, 수익률을 표시하지 않습니다."/>}
  </div>;
}

/** Why there is no book yet. A completed run with zero events before the launch signal is the normal state. */
function emptyTitle(view: IPaperView) {
  if (view.status === "NOT_LAUNCHED") return "Paper가 아직 시작되지 않았습니다.";
  if (!view.last_run) return "아직 완료된 Paper 실행이 없습니다.";
  if (view.launch_signal && view.last_run.as_of < view.launch_signal) return `시작 신호일(${view.launch_signal}) 전입니다. 첫 리밸런싱 후 장부가 표시됩니다.`;
  return "첫 리밸런싱 전입니다. 실행 후 장부가 표시됩니다.";
}

function StatusPanel({ view }: { view: IPaperView }) {
  const d = view.flags?.data_delay;
  const failed = view.status === "ERROR" || view.status === "INPUT_BLOCKED";
  // a guard SKIP since the last completed run explains why the as-of has not moved (normal hold, not an error)
  const skip = view.guard?.last_skip;
  const heldSkip = skip?.at && (!view.last_run?.completed_at || Date.parse(skip.at) > Date.parse(view.last_run.completed_at)) ? skip : null;
  return <section aria-label="운영 상태" className="mb-7" data-i-state="">
    <div className="panel p-5">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4">
        <div><dt className="label">운영 상태</dt><dd className="mt-1"><StatusBadge value={view.status} label={STATUS_LABEL[view.status] ?? view.status} tone={STATUS_TONE[view.status] ?? "neutral"}/></dd></div>
        <div><dt className="label">시작 신호일</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{view.launch_signal ?? "-"}</dd></div>
        <div><dt className="label">마지막 평가일</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground" data-i-asof="">{view.last_run?.as_of ?? "-"}</dd></div>
        <div><dt className="label">다음 신호 예정</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{view.next_signal ?? "-"}</dd></div>
        <div><dt className="label">초기 자금</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{usd(10000)}</dd></div>
        <div><dt className="label">목표 / 실제 보유</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{view.target_holdings ?? 20} / {view.account?.holdings ?? "-"}</dd></div>
        <div><dt className="label">운영 세션</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{view.account?.operating_sessions ?? "-"}</dd></div>
        <div><dt className="label">마지막 실행 완료</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-foreground">{kstTime(view.last_run?.completed_at)}</dd></div>
      </dl>
      {(view.reason || d?.delayed || heldSkip || (view.flags?.refused_signals.length ?? 0) > 0) && <ul className="mt-4 space-y-1 text-sm" data-i-alerts="">
        {view.reason && <li className={failed ? "text-danger" : "text-foreground-secondary"}>{view.reason}</li>}
        {d?.delayed && <li className="text-warning">데이터 지연: 마지막 평가 {d.as_of}, 기대 {d.expected}</li>}
        {heldSkip && <li className="text-foreground-secondary" data-i-guard-skip="">Guard가 {kstTime(heldSkip.at)} 실행을 보류했습니다 (호스트 부하 보호, 오류 아님){heldSkip.reasons?.length ? `: ${heldSkip.reasons.join(", ")}` : ""}</li>}
        {(view.flags?.refused_signals.length ?? 0) > 0 && <li className="text-warning">입력 부족으로 건너뛴 신호: {view.flags?.refused_signals.join(", ")}</li>}
      </ul>}
    </div>
  </section>;
}

function Account({ view }: { view: IPaperView }) {
  const a = view.account!;
  return <section aria-label="Strategy I paper 계좌" className="mb-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-i-account="">
    <MetricCard accent="primary" label="현재 NAV" value={usd(a.nav)} detail={`초기 자금 ${usd(10000)} · ${a.as_of}`}/>
    <MetricCard accent="bluegreen" label="현금" value={usd(a.cash)} detail={`보유 평가 ${usd(a.positions_value)}`}/>
    <MetricCard accent="green" label="누적 손익" value={signedUsd(a.total_pnl)} detail={`수익률 ${pct(a.total_return)}`}/>
    <MetricCard accent="gold" label="최근 평가일 손익" value={signedUsd(a.day_pnl)} detail={`${a.day_session} · ${pct(a.day_return)}`}/>
  </section>;
}

function Positions({ view }: { view: IPaperView }) {
  const rows = view.positions ?? [];
  return <section aria-labelledby="i-positions-title" className="mb-7">
    <h2 id="i-positions-title" className="mb-3 font-semibold">보유 종목 <span className="text-sm font-normal text-foreground-secondary">{rows.length} / {view.target_holdings ?? 20}</span></h2>
    {!rows.length ? <EmptyState title="보유 종목이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>종목</th><th>수량</th><th>평균단가</th><th>종가</th><th>평가금액</th><th>비중</th></tr></thead>
      <tbody>{rows.map(p => <tr key={p.ticker}>
        <td className="font-bold text-foreground">{p.ticker}{p.stale_price ? " · 가격 중단" : ""}</td>
        <td>{p.shares}</td><td>{usd(p.avg_cost)}</td><td>{usd(p.last_close)}</td><td>{usd(p.value)}</td><td>{share(p.weight)}</td>
      </tr>)}</tbody></table></div>}
  </section>;
}

function Rebalances({ view }: { view: IPaperView }) {
  const rows = (view.rebalances ?? []).slice(0, 13);
  return <section aria-labelledby="i-reb-title" className="mb-7">
    <h2 id="i-reb-title" className="mb-3 font-semibold">월간 리밸런싱</h2>
    {!rows.length ? <EmptyState title="아직 리밸런싱이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>실행일</th><th>신호일</th><th>보유</th><th>거래</th><th>현금 비중</th><th>NAV</th><th>비용</th><th>회전율</th></tr></thead>
      <tbody>{rows.map(r => <tr key={r.session}>
        <td>{r.session}</td><td>{r.signal}</td><td>{r.holdings}</td><td>{r.trade_count}</td><td>{r.cash_pct.toFixed(2)}%</td>
        <td>{usd(r.nav_post)}</td><td>{usd(r.costs)}</td><td>{r.turnover_one_way == null ? "초기 구축" : `${(r.turnover_one_way * 100).toFixed(1)}%`}</td>
      </tr>)}</tbody></table></div>}
  </section>;
}

function Trades({ view }: { view: IPaperView }) {
  const rows = (view.trades ?? []).slice(0, 40);
  return <section aria-labelledby="i-trades-title" className="mb-7">
    <h2 id="i-trades-title" className="mb-3 font-semibold">최근 거래 <span className="text-sm font-normal text-foreground-secondary">가상 체결 · 실행일 종가</span></h2>
    {!rows.length ? <EmptyState title="아직 거래가 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>실행일</th><th>구분</th><th>종목</th><th>수량</th><th>금액</th><th>비용</th></tr></thead>
      <tbody>{rows.map((t, j) => <tr key={`${t.session}-${t.ticker}-${j}`}>
        <td>{md(t.session)}</td><td>{t.side === "SELL" ? (t.stale_last_close ? "매도 · 마지막 종가" : "매도") : t.side === "BUY_ALLOC" ? "추가 매수" : "매수"}</td>
        <td className="font-bold text-foreground">{t.ticker}</td><td>{t.shares}</td><td>{usd(t.notional)}</td><td>{usd(t.cost)}</td>
      </tr>)}</tbody></table></div>}
  </section>;
}

function Continuity({ view }: { view: IPaperView }) {
  const f = view.flags;
  const rows = [...(f?.continuity_unresolved ?? []), ...(f?.continuity_linked ?? []), ...(f?.price_stop_held ?? [])]
    .sort((a, b) => b.session.localeCompare(a.session)).slice(0, 12);
  const LABEL: Record<string, string> = { CONTINUITY_UNRESOLVED: "CIK 미해결 · V1 가격중단 매도", CONTINUITY_LINKED: "PIT 전환 연결", PRICE_STOP_HELD: "가격 중단 보유" };
  return <section aria-labelledby="i-cont-title" className="mb-7" data-i-continuity="">
    <h2 id="i-cont-title" className="mb-3 font-semibold">가격 중단 · 티커 전환</h2>
    {!rows.length ? <EmptyState title="가격 중단이나 미해결 전환이 없습니다."/> : <div className="table-wrap"><table>
      <thead><tr><th>세션</th><th>종목</th><th>상태</th><th>마지막 종가일</th><th>사유</th></tr></thead>
      <tbody>{rows.map((e, j) => <tr key={`${e.type}-${e.ticker}-${j}`}>
        <td>{md(e.session)}</td><td className="font-bold text-foreground">{e.ticker}{e.new ? ` → ${e.new}` : ""}</td>
        <td>{LABEL[e.type] ?? e.type}</td><td>{e.last_close_session ?? "-"}</td><td className="text-foreground-secondary">{e.reason ?? "-"}</td>
      </tr>)}</tbody></table></div>}
  </section>;
}

function Overlay({ view }: { view: IPaperView }) {
  const o = view.overlay?.latest;
  return <section aria-labelledby="i-overlay-title" data-i-overlay="">
    <h2 id="i-overlay-title" className="mb-3 font-semibold">산업 Overlay <span className="text-sm font-normal text-foreground-secondary">가상 관찰 · 장부와 무관</span></h2>
    <div className="panel p-5">
      {!o ? <p className="text-sm text-foreground-secondary">관찰 기록이 없습니다.</p>
        : o.status !== "EVALUATED" ? <p className="text-sm text-foreground-secondary">{o.signal} 신호: 평가 안 함 ({o.reason === "NO_FORWARD_PIT_SIC_SNAPSHOT" || o.reason === "NO_SIC_STORE" ? "전방 SIC 스냅샷 없음" : o.reason})</p>
        : <>
          <p className="mb-3 text-sm text-foreground-secondary">{o.signal} 신호 기준 · 25% 초과 산업 {o.over_cap?.length ? o.over_cap.join(", ") : "없음"}
            {o.virtual_forced_exits?.length ? ` · 가상 청산 ${o.virtual_forced_exits.join(", ")}` : ""}
            {o.virtual_blocked_entries?.length ? ` · 가상 진입 차단 ${o.virtual_blocked_entries.join(", ")}` : ""}</p>
          <div className="table-wrap"><table>
            <thead><tr><th>SIC2</th><th>비중</th></tr></thead>
            <tbody>{Object.entries(o.weights_pct ?? {}).sort((a, b) => b[1] - a[1]).map(([g, w]) => <tr key={g}><td>{g}</td><td>{w.toFixed(2)}%</td></tr>)}</tbody>
          </table></div></>}
      <p className="mt-3 text-xs text-muted">관찰 {view.overlay?.observations ?? 0}회 · 평가 {view.overlay?.evaluated_count ?? 0}회. 실제 보유·현금·주문에는 영향을 주지 않습니다.</p>
    </div>
  </section>;
}
