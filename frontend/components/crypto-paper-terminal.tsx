"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { MetricCard, StatusBadge } from "@/components/ui";
import {
  CHART_HEIGHT, CHART_WIDTH, ChartBar, CryptoApiError, CryptoSizing, CryptoState, EVENT_LABELS,
  LedgerEvent, MODE_LABELS, OrderPreview, OrderPreviewParams, OrderPreviewSide, OrderSide,
  PRESET_LABELS, Performance, PnlBreakdown, PresetLabel, SizePreset, TradeBreakdown, TradeRow,
  FEED_STATUS_LABELS, LivePnl, chartGeometry, costKrw, costUsdt, tickAgeSeconds, tickFreshness, clockKst, cryptoApi, duration, feedAgeSeconds, isHighRisk,
  canEnter, describeReset, feedStatus, krw, num, percent, price, qty, rateBps, rejectLabel, signedKrw,
  signedUsdt, toneClass, usdt,
} from "@/lib/crypto-paper";

const LEVERAGE_PRESETS = ["1", "3", "5", "10", "20", "50"];
const STALE_SECONDS = 5;

/** The mark price and nothing else. Liquidation and valuation are mark based, so showing the
 *  last trade as the headline would put a different number in front of the operator than the
 *  one the account actually uses. */
export function PriceHeadline({ state }: { state: CryptoState }) {
  const quote = state.quote;
  const age = feedAgeSeconds(state);
  const stale = age == null || age > STALE_SECONDS;
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="text-xs text-muted">BTCUSDT 무기한 · Mark</p>
        <p className="text-3xl font-bold tabular-nums text-foreground" data-testid="mark-price">
          {price(quote?.mark_price)}
        </p>
        <p className="mt-1 text-xs text-muted">
          Last {price(quote?.last_price)} · Index {price(quote?.index_price)}
        </p>
      </div>
      <div className="text-right">
        <StatusBadge value={stale ? "STALE" : "LIVE"}
          label={stale ? "시세 지연" : `실시간 · ${age?.toFixed(1)}초 전`} />
        <p className="mt-1 text-xs text-muted" data-testid="feed-telemetry">
          재연결 {state.feed.reconnects} · 북 갭 {state.feed.book_gaps} · 재구독 {state.feed.book_resyncs}
        </p>
      </div>
    </div>
  );
}

export function PriceChart({ bars }: { bars: ChartBar[] }) {
  const geometry = chartGeometry(bars);
  if (!geometry) return <p className="py-10 text-center text-sm text-muted">차트 데이터를 받는 중입니다.</p>;
  const stroke = geometry.rising ? "var(--success)" : "var(--danger)";
  return (
    <figure className="mt-4">
      <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} className="h-[200px] w-full"
        preserveAspectRatio="none" role="img" aria-label="BTCUSDT 1분봉 종가 추이">
        <path d={geometry.path} fill="none" stroke={stroke} strokeWidth="2"
          vectorEffect="non-scaling-stroke" />
        <line x1="0" x2={CHART_WIDTH} y1={geometry.lastY} y2={geometry.lastY} stroke={stroke}
          strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" opacity="0.5" />
      </svg>
      <figcaption className="mt-2 flex justify-between text-xs text-muted">
        <span>1분봉 {bars.length}개</span>
        <span className="tabular-nums">저 {price(String(geometry.low))} · 고 {price(String(geometry.high))}</span>
      </figcaption>
    </figure>
  );
}

export function BookPanel({ state }: { state: CryptoState }) {
  const quote = state.quote;
  const spreadBps = quote?.spread && quote.mid ? (Number(quote.spread) / Number(quote.mid)) * 10_000 : null;
  return (
    <div className="grid grid-cols-3 gap-3" data-testid="book-panel">
      <div className="panel p-4">
        <p className="text-xs text-muted">Best Bid</p>
        <p className="text-lg font-semibold tabular-nums text-success" data-testid="best-bid">{price(quote?.best_bid)}</p>
        <p className="text-[11px] text-muted">상위 5호가 {qty(quote?.bid_depth_top5)} BTC</p>
      </div>
      <div className="panel p-4">
        <p className="text-xs text-muted">Best Ask</p>
        <p className="text-lg font-semibold tabular-nums text-danger" data-testid="best-ask">{price(quote?.best_ask)}</p>
        <p className="text-[11px] text-muted">상위 5호가 {qty(quote?.ask_depth_top5)} BTC</p>
      </div>
      <div className="panel p-4">
        <p className="text-xs text-muted">Spread</p>
        <p className="text-lg font-semibold tabular-nums text-foreground" data-testid="spread">{price(quote?.spread, 2)}</p>
        <p className="text-[11px] text-muted">{spreadBps == null ? "-" : `${spreadBps.toFixed(2)} bp`}</p>
      </div>
    </div>
  );
}

export function AccountPanel({ state }: { state: CryptoState }) {
  const account = state.account;
  const krwFigures = state.krw;
  return (
    <div data-testid="account-panel">
      {/* The shell header carries the equity screens' own USD/KRW. Saying which rate converted
          these figures keeps two different fixed rates from being read as one. */}
      <p className="mb-2 text-[11px] text-muted" data-testid="fx-note">
        KRW 환산은 이 런에 고정된 1 USDT = {Number(state.fx.krw_per_usdt).toLocaleString("ko-KR")}원 기준입니다.
        상단 헤더의 USD/KRW는 주식 화면 환율이며 여기에 쓰이지 않습니다.
      </p>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="가상 자산 (Equity)" accent="primary"
        value={<span data-testid="equity">{usdt(account?.equity)}</span>}
        detail={krwFigures ? `≈ ${krw(krwFigures.equity)}` : undefined}
        meta={`시작 ${krw(state.starting_capital_krw)}`} />
      <MetricCard label="주문가능 (Available)"
        value={<span data-testid="available">{usdt(account?.available_balance)}</span>}
        detail={krwFigures ? `≈ ${krw(krwFigures.available_balance)}` : undefined}
        meta={`사용 마진 ${usdt(account?.used_margin)}`} />
      <MetricCard label="미실현 손익"
        value={<span className={toneClass(account?.unrealized_pnl)} data-testid="unrealized">
          {signedUsdt(account?.unrealized_pnl)}</span>}
        detail={krwFigures ? `≈ ${signedKrw(krwFigures.unrealized_pnl)}` : undefined}
        meta="Mark 기준" />
      <MetricCard label="실현 손익"
        value={<span className={toneClass(account?.realized_pnl)} data-testid="realized">
          {signedUsdt(account?.realized_pnl)}</span>}
        detail={krwFigures ? `≈ ${signedKrw(krwFigures.realized_pnl)}` : undefined}
        meta={`수수료 ${usdt(account?.cumulative_fees, 4)} · 펀딩 ${signedUsdt(account?.cumulative_funding_paid)}`} />
    </div>
    </div>
  );
}

export function PositionPanel({ state }: { state: CryptoState }) {
  const account = state.account;
  if (!account || account.position_side === null) {
    return <div className="panel p-5 text-sm text-muted" data-testid="position-panel">
      보유 포지션이 없습니다. LONG 또는 SHORT로 진입할 수 있습니다.
    </div>;
  }
  const long = account.position_side === "LONG";
  const rows: [string, string][] = [
    ["수량", `${qty(account.position_qty)} BTC`],
    ["진입가", price(account.avg_entry)],
    ["Mark", price(state.quote?.mark_price)],
    ["명목 (Mark)", usdt(account.mark_notional)],
    ["레버리지", `${num(account.leverage)?.toString() ?? "-"}x`],
    ["사용 마진", usdt(account.used_margin)],
    ["유지 마진", usdt(account.maintenance_margin, 4)],
    ["마진 비율", account.margin_ratio == null ? "-" : `${Number(account.margin_ratio).toFixed(2)}x`],
  ];
  return (
    <div className="panel p-5" data-testid="position-panel">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`rounded-md px-2 py-1 text-xs font-bold ${long ? "bg-success-soft text-success" : "bg-danger-soft text-danger"}`}
            data-testid="position-side">{long ? "LONG" : "SHORT"}</span>
          <span className="text-xs text-muted">리스크 티어 {account.risk_tier ?? "-"}</span>
        </div>
        <div className="text-right">
          <p className="text-xs text-muted">청산가</p>
          <p className="text-lg font-bold tabular-nums text-warning" data-testid="liquidation-price">
            {price(account.liquidation_price)}
          </p>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="tabular-nums text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** What the selected preset would actually do, per side, straight from the engine's probe.
 *
 *  Both sides are shown rather than only the one about to be clicked. They are not mirror
 *  images: a LONG walks the ask and a SHORT walks the bid, so the same preset is a different
 *  quantity and a different liquidation price on each side. Showing one and labelling it "the"
 *  size would be a small lie that only surfaces on a lopsided book. */
export function PresetReadout({ sizing, preset, leverage }: {
  sizing?: CryptoSizing | null;
  preset: PresetLabel;
  leverage: string;
}) {
  if (!sizing) {
    return <p className="mb-3 rounded-lg border border-line px-3 py-2 text-[11px] text-muted"
      data-testid="preset-readout">주문 가능 수량을 계산하는 중입니다.</p>;
  }
  const sides: OrderSide[] = ["LONG", "SHORT"];
  const rowFor = (side: OrderSide) =>
    sizing.sides?.[side]?.presets.find(item => item.label === preset);

  const cells: [string, (row?: SizePreset) => string][] = [
    ["수량 (BTC)", row => qty(row?.qty)],
    ["명목 (USDT)", row => usdt(row?.notional)],
    ["필요 마진", row => usdt(row?.required_total, 4)],
    // Entry and exit are both shown because the size is only offered when both can happen.
    // Neither is the mark: a market order fills on the book, not at the valuation price.
    ["예상 진입가", row => price(row?.fill_price)],
    ["즉시 청산가", row => price(row?.exit_fill_price)],
    ["강제 청산가", row => price(row?.liquidation_price)],
  ];

  return (
    <div className="mb-3 rounded-lg border border-line bg-surface-alt p-3" data-testid="preset-readout">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-foreground">
          {preset} · {num(leverage)?.toString() ?? "-"}x
        </span>
      </div>
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-muted">
            <th className="w-[36%] py-0.5 text-left font-medium"> </th>
            {sides.map(side => (
              <th key={side} className="py-0.5 text-right font-semibold">{side}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cells.map(([label, read]) => (
            <tr key={label}>
              <td className="py-0.5 text-muted">{label}</td>
              {sides.map(side => {
                const row = rowFor(side);
                return (
                  <td key={side} className="py-0.5 text-right tabular-nums text-foreground-secondary"
                    data-testid={`preset-${side}-${label}`}>
                    {row?.feasible ? read(row) : "-"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {sides.map(side => {
        const row = rowFor(side);
        if (row == null || row.feasible) return null;
        return (
          <p key={side} className="mt-1.5 text-[11px] text-warning"
            data-testid={`preset-reject-${side}`}>
            {side} 불가 · {rejectLabel(row.reject_code, row.reject_message)}
          </p>
        );
      })}
      {/* Where the numbers came from. A preview is priced on one book snapshot and the book
          moves; the order is re-checked against a fresh one when it is actually sent. */}
      <p className="mt-2 text-[10px] leading-relaxed text-muted" data-testid="preset-basis">
        현재 호가 기준 · Bid {price(sizing.sides?.LONG?.best_bid)} / Ask {price(sizing.sides?.LONG?.best_ask)}
        {" · "}주문 시점 호가로 다시 확인합니다
      </p>
    </div>
  );
}

const signedPct = (value: string | null | undefined) => {
  const text = percent(value, 2);
  return value != null && Number(value) > 0 ? `+${text}` : text;
};

/** What entering this size and closing it at once would cost, per side, priced by the engine on
 *  clones before anything is sent. The compact rows are what a phone shows by default; the rest
 *  opens under [상세]. Fees arrive as positive amounts and are shown as deductions; slippage and
 *  net arrive as signed effects. Nothing is computed here. */
export function OrderPreviewPanel({ preview, state, highRisk, leverage, armed, detailOpen, onToggle, children }: {
  preview: OrderPreview | null; state: CryptoState; highRisk: boolean; leverage: string; armed: string;
  detailOpen: boolean; onToggle: () => void; children?: React.ReactNode;
}) {
  const sides: OrderSide[] = ["LONG", "SHORT"];
  const feed = feedStatus(state);
  const status = feed !== "LIVE" ? feed : preview ? tickFreshness(preview) : "LIVE";
  const tickAge = preview ? tickAgeSeconds(preview) : null;
  const age = tickAge == null ? null : Math.round(tickAge);
  const compact: [string, (row: OrderPreviewSide) => string, (row: OrderPreviewSide) => string | null][] = [
    ["예상 진입 수수료", row => costKrw(row.krw?.entry_fee), () => null],
    ["예상 청산 수수료", row => costKrw(row.krw?.exit_fee), () => null],
    ["왕복 총비용", row => costKrw(row.krw?.round_trip_cost), () => null],
    ["손익분기", row => signedPct(row.breakeven_move_pct), () => null],
  ];
  const detail: [string, (row: OrderPreviewSide) => string, (row: OrderPreviewSide) => string | null][] = [
    ["예상 진입 체결가", row => price(row.entry_fill_price), () => null],
    ["예상 청산 체결가", row => price(row.exit_fill_price), () => null],
    ["진입 체결비용", row => signedKrw(row.krw?.entry_slippage_pnl), row => row.entry_slippage_pnl ?? null],
    ["청산 체결비용", row => signedKrw(row.krw?.exit_slippage_pnl), row => row.exit_slippage_pnl ?? null],
    ["손익분기 가격 (Mark)", row => price(row.breakeven_mark_price), () => null],
    ["즉시 청산 시 예상 순손익", row => signedKrw(row.krw?.immediate_round_trip_net), row => row.immediate_round_trip_net ?? null],
    ["진입 수수료 (USDT)", row => costUsdt(row.entry_fee), () => null],
    ["청산 수수료 (USDT)", row => costUsdt(row.exit_fee), () => null],
    ["명목", row => usdt(row.notional, 0), () => null],
    ["필요 마진", row => usdt(row.required_margin), () => null],
  ];
  // Fixed layout: the wide layout's order column is only 360 px, and an auto table lets the
  // longest value push the whole page sideways.
  const table = (rows: typeof compact, testPrefix: string, header = false, wrap = false) => (
    <table className="w-full table-fixed text-[11px]">
      <colgroup><col className="w-[36%]" /><col className="w-[32%]" /><col className="w-[32%]" /></colgroup>
      {header && (
        <thead><tr className="text-muted">
          <th className="py-0.5 text-left font-medium"> </th>
          {sides.map(side => <th key={side} className="py-0.5 text-right font-semibold">{side}</th>)}
        </tr></thead>
      )}
      <tbody>
        {rows.map(([label, read, tone]) => (
          <tr key={label}>
            <td className="py-0.5 pr-2 leading-tight text-muted">{label}</td>
            {sides.map(side => {
              const row = preview?.sides?.[side];
              const shown = row?.feasible ? read(row) : "-";
              const toneValue = row?.feasible ? tone(row) : null;
              return (
                <td key={side} className={`${wrap ? "break-words" : "whitespace-nowrap"} py-0.5 text-right tabular-nums ${
                  toneValue == null ? "text-foreground-secondary" : toneClass(toneValue)}`}
                  data-testid={`${testPrefix}-${side}-${label}`}>{shown}</td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <div className="mb-3 rounded-lg border border-line bg-surface-alt p-3" data-testid="order-preview">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-foreground">
          주문 미리보기 <span className="font-normal text-muted">· {num(leverage)?.toString() ?? "-"}x · {armed}</span>
        </span>
        <span className="flex items-center gap-1.5">
          {highRisk && <span className="rounded bg-danger-soft px-1.5 py-0.5 text-[10px] font-bold text-danger"
            data-testid="high-risk-badge">HIGH RISK</span>}
          <span className="text-[10px] text-muted" data-testid="order-preview-age">
            {age == null ? "" : `${age}초 전`}
          </span>
        </span>
      </div>
      {highRisk && (
        <p className="mb-1.5 text-[11px] text-danger">
          최대 수량을 {num(leverage)?.toString()}배로 잡으면 청산가가 현재가 바로 옆에 붙습니다.
          작은 역방향 변동에도 포지션이 사라질 수 있습니다.
        </p>
      )}
      {status !== "LIVE" ? (
        <p className="rounded bg-warning-soft px-2 py-1 text-[11px] text-warning" data-testid="order-preview-unavailable">
          미리보기 불가: {FEED_STATUS_LABELS[status]} ({status === "STALE" ? "STALE market data" : "DISCONNECTED"})
        </p>
      ) : preview == null ? (
        <p className="text-[11px] text-muted" data-testid="order-preview-pending">비용을 계산하는 중입니다.</p>
      ) : (
        <>
          {table(compact, "preview", true)}
          {sides.map(side => {
            const row = preview.sides?.[side];
            if (row == null || row.feasible) return null;
            return (
              <p key={side} className="mt-1 text-[11px] text-warning" data-testid={`order-preview-reject-${side}`}>
                {side} 미리보기 불가 · {row.reject_code === "NO_LIQUIDITY" ? "호가 깊이 부족" : rejectLabel(row.reject_code, row.reject_message)}
                {row.safe_max_qty != null && ` · 현재 안전 최대 ${qty(row.safe_max_qty)} BTC`}
              </p>
            );
          })}
          <p className="mt-1 text-[10px] text-muted">
            왕복 총비용은 이 수량의 명목 기준, 진입 직후 전량 청산을 가정합니다.
          </p>
        </>
      )}
      <button type="button" className="mt-1.5 text-[11px] font-medium text-primary hover:underline"
        aria-expanded={detailOpen} data-testid="order-preview-detail-toggle" onClick={onToggle}>
        {detailOpen ? "상세 접기" : "상세"}
      </button>
      {detailOpen && (
        <div className="mt-2 border-t border-line pt-2" data-testid="order-preview-detail">
          {status === "LIVE" && preview != null && table(detail, "preview-detail", false, true)}
          {children}
        </div>
      )}
    </div>
  );
}

export function OrderTicket({ state, sizing, onAction, busy, error, wide = false,
  previewEnabled = true, previewOverride }: {
  state: CryptoState;
  sizing?: CryptoSizing | null;
  onAction: (run: () => Promise<unknown>) => void;
  busy: boolean;
  error: string | null;
  /** Wide layout: the preview detail starts open. */
  wide?: boolean;
  /** False for the twin that is hidden at the current breakpoint, so only one fetches. */
  previewEnabled?: boolean;
  /** Tests inject a preview instead of fetching one. */
  previewOverride?: OrderPreview | null;
}) {
  const [size, setSize] = useState("0.001");
  const [sizeMode, setSizeMode] = useState<"QTY" | "NOTIONAL">("QTY");
  const [preset, setPreset] = useState<PresetLabel | null>(null);
  const [confirmEmergency, setConfirmEmergency] = useState(false);
  const [emergencyOpen, setEmergencyOpen] = useState(false);
  const account = state.account;
  const flat = !account || account.position_side === null;
  const noQuote = state.quote === null;
  // One authority for "may this screen be traded on" (lib/crypto-paper feedStatus). A quote that
  // stopped moving is not a quote: the engine fills at the last tick it was handed, so an entry
  // against a frozen book is priced at a market that has moved on. The engine cannot see that by
  // itself, because the socket is still open. Closing stays available in every state on purpose:
  // reducing risk on a bad screen is the safer of the two mistakes.
  const status = feedStatus(state);
  const age = feedAgeSeconds(state);
  const blocked = !state.state.can_open_new_position || !canEnter(status)
    || Boolean(state.c1_auto?.enabled);

  const sideSizing = (side: OrderSide) => sizing?.sides?.[side];
  const presetFor = (side: OrderSide) =>
    preset == null ? undefined : sideSizing(side)?.presets.find(row => row.label === preset);

  /** A preset is held as a label, not as a number of coins, and is resolved against the side
   *  actually being ordered. LONG walks the ask and SHORT walks the bid, so one preset is two
   *  different quantities whenever the book is lopsided, and sending one side's figure to the
   *  other is how a MAX order turns into a rejection. */
  const body = (side: OrderSide) => {
    const chosen = presetFor(side);
    if (chosen) return { qty: chosen.qty };
    return sizeMode === "QTY" ? { qty: size } : { notional_usdt: size };
  };

  const presetUsable = (side: OrderSide) => presetFor(side)?.feasible !== false;
  const highRisk = isHighRisk(state.leverage, preset);

  // Pre-trade preview: re-priced when the leverage, the preset, the typed size, the size mode or
  // the quote changes. One request for both sides; the previous one is aborted, so a slow answer
  // can never overwrite a newer one. The hidden twin (phone vs wide layout) does not fetch.
  const [detailOpen, setDetailOpen] = useState(wide);
  const [fetched, setFetched] = useState<OrderPreview | null>(null);
  const previewParams: OrderPreviewParams | null = !flat ? null : preset
    ? { long_qty: presetFor("LONG")?.feasible ? presetFor("LONG")!.qty : undefined,
        short_qty: presetFor("SHORT")?.feasible ? presetFor("SHORT")!.qty : undefined }
    : sizeMode === "QTY" ? { long_qty: size, short_qty: size } : { notional_usdt: size };
  const previewKey = previewParams == null ? "" : `${JSON.stringify(previewParams)}|${state.leverage}`;
  const quoteTs = state.quote?.ts_ms ?? null;
  const paramsRef = useRef(previewParams);
  paramsRef.current = previewParams;
  useEffect(() => {
    if (previewOverride !== undefined || !previewEnabled || previewKey === "") {
      if (previewKey === "") setFetched(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      cryptoApi.orderPreview(paramsRef.current ?? {}, controller.signal)
        .then(body => { if (!controller.signal.aborted) setFetched(body); })
        .catch(() => { /* keep the last answer; the freshness check below retires it */ });
    }, 120);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [previewKey, quoteTs, previewEnabled, previewOverride]);
  const orderPreview = previewOverride !== undefined ? previewOverride : fetched;
  const armedLabel = preset ? preset : `직접 ${size || "-"} ${sizeMode === "QTY" ? "BTC" : "USDT"}`;

  return (
    <div className="panel p-3 sm:p-5" data-testid="order-ticket">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold sm:text-base">수동 주문</h2>
        <StatusBadge value={state.state.mode} label={`${MODE_LABELS[state.state.mode]} 모드`} />
      </div>

      <label className="mb-1 block text-xs text-muted" htmlFor="crypto-leverage">레버리지</label>
      <div className="mb-2 grid grid-cols-6 gap-1 sm:mb-3" id="crypto-leverage">
        {LEVERAGE_PRESETS.map(value => (
          <button key={value} type="button" disabled={busy || !flat}
            aria-pressed={state.leverage === value}
            className={`btn-compact h-9 px-0 sm:h-10 ${state.leverage === value ? "btn-compact-active" : ""}`}
            onClick={() => onAction(() => cryptoApi.leverage(value))}>{value}x</button>
        ))}
      </div>
      {!flat && <p className="mb-2 text-[11px] text-muted">보유 중 레버리지 변경 불가</p>}

      {/* Quick sizes come from the engine (GET /api/crypto/sizing), which prices each preset by
          submitting it to a throwaway copy of the account. Nothing here multiplies a balance by a
          leverage: the affordable size depends on how deep the order walks the book, and a
          formula on this side would overstate MAX in exactly the thin book where that costs the
          most. Selecting a preset overrides the typed size until it is cleared. */}
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-muted">빠른 수량</span>
        {preset && <button type="button" className="text-[11px] text-muted underline"
          data-testid="preset-clear" onClick={() => setPreset(null)}>직접 입력으로</button>}
      </div>
      <div className="mb-2 grid grid-cols-4 gap-1.5 sm:mb-3" role="group" aria-label="빠른 수량">
        {PRESET_LABELS.map(label => {
          const row = sizing?.sides?.LONG?.presets.find(item => item.label === label);
          const unavailable = sizing != null && row?.feasible === false;
          const active = preset === label;
          const strong = label === "MAX";
          return (
            <button key={label} type="button" aria-pressed={active}
              data-testid={`preset-${label}`}
              disabled={busy || unavailable || sizing == null}
              title={unavailable ? rejectLabel(row?.reject_code, row?.reject_message) : undefined}
              className={`btn-compact h-9 sm:h-10 ${active ? "btn-compact-active" : ""} ${
                strong ? "font-extrabold tracking-wide ring-1 ring-warning/60" : ""} ${
                strong && active ? "!bg-warning-soft !text-warning ring-2 ring-warning" : ""}`}
              onClick={() => setPreset(label)}>
              {label}
            </button>
          );
        })}
      </div>

      {!preset && <>
        <div className="mb-2 flex gap-1.5">
          {(["QTY", "NOTIONAL"] as const).map(option => (
            <button key={option} type="button" aria-pressed={sizeMode === option}
              className={`btn-compact ${sizeMode === option ? "btn-compact-active" : ""}`}
              onClick={() => setSizeMode(option)}>{option === "QTY" ? "수량 (BTC)" : "명목 (USDT)"}</button>
          ))}
        </div>
        <label className="sr-only" htmlFor="crypto-size">주문 크기</label>
        <input id="crypto-size" className="input mb-4 tabular-nums" value={size} inputMode="decimal"
          onChange={event => setSize(event.target.value)}
          placeholder={sizeMode === "QTY" ? "0.001" : "100"} />
      </>}

      {flat && (
        <OrderPreviewPanel preview={orderPreview} state={state} highRisk={highRisk}
          leverage={state.leverage} armed={armedLabel} detailOpen={detailOpen}
          onToggle={() => setDetailOpen(open => !open)}>
          {preset && <PresetReadout sizing={sizing} preset={preset} leverage={state.leverage} />}
        </OrderPreviewPanel>
      )}

      {/* The leverage and the size actually armed, always on screen: the two numbers that decide
          what a single click is about to do. */}
      <p className="mb-3 rounded-lg bg-surface-alt px-3 py-2 text-[11px] text-foreground-secondary"
        data-testid="order-arming">
        <span className="font-semibold">{num(state.leverage)?.toString() ?? "-"}x</span>
        {" · "}
        {preset
          ? <>빠른 수량 <span className="font-semibold">{preset}</span></>
          : <>직접 입력 <span className="font-semibold">{size || "-"}</span> {sizeMode === "QTY" ? "BTC" : "USDT"}</>}
      </p>

      {status !== "LIVE" && <p className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-[11px] text-warning"
        role="status" data-testid="stale-entry-block">
        {status === "DISCONNECTED"
          ? "시세 연결이 끊겨 신규 진입을 차단했습니다."
          : `시세가 ${age == null ? "도착하지" : `${age.toFixed(1)}초 동안 갱신되지`} 않아 신규 진입을 차단했습니다.`}
        {" "}청산은 계속 가능합니다.
      </p>}

      {/* One click sends the market order. There is no second confirmation on purpose: the size,
          the margin and the liquidation price are already on screen above, and a confirm step
          would only add latency to a decision the operator has already priced. EMERGENCY CLOSE
          keeps its tick box, because that one is irreversible in a way an entry is not. */}
      <div className="mb-3 grid grid-cols-2 gap-2">
        <button type="button" className="btn-success-soft h-14 text-base font-bold sm:h-12"
          disabled={busy || blocked || noQuote || !presetUsable("LONG")}
          data-testid="long-button"
          onClick={() => onAction(() => cryptoApi.order({ side: "LONG", intent: "OPEN", ...body("LONG") }))}>
          LONG{presetFor("LONG")?.feasible && <span className="ml-1.5 text-[11px] font-normal tabular-nums opacity-80">
            {qty(presetFor("LONG")!.qty)}</span>}
        </button>
        <button type="button" className="btn-danger-soft h-14 text-base font-bold sm:h-12"
          disabled={busy || blocked || noQuote || !presetUsable("SHORT")}
          data-testid="short-button"
          onClick={() => onAction(() => cryptoApi.order({ side: "SHORT", intent: "OPEN", ...body("SHORT") }))}>
          SHORT{presetFor("SHORT")?.feasible && <span className="ml-1.5 text-[11px] font-normal tabular-nums opacity-80">
            {qty(presetFor("SHORT")!.qty)}</span>}
        </button>
      </div>
      <button type="button" className="btn-muted mb-3 h-12 w-full text-sm font-bold"
        disabled={busy || flat || noQuote} data-testid="close-button"
        onClick={() => onAction(() => cryptoApi.order({
          side: account!.position_side!, intent: "CLOSE", qty: account!.position_qty }))}>
        CLOSE · 전량 청산
      </button>

      {/* EMERGENCY folds to one line while flat - there is nothing to close and this is when the
          operator is placing orders - and stands open whenever a position is held or the mode
          is already EMERGENCY. Folded is not removed: one tap opens it. */}
      {flat && state.state.mode !== "EMERGENCY" && !emergencyOpen ? (
        <button type="button" className="mb-1 flex w-full items-center justify-between rounded-lg border border-danger/40 px-3 py-1.5 text-[11px] font-semibold text-danger"
          data-testid="emergency-toggle" aria-expanded={false} onClick={() => setEmergencyOpen(true)}>
          비상 종료 <span aria-hidden="true">▸</span>
        </button>
      ) : (
      <div className="rounded-lg border border-danger/40 bg-danger-soft/40 p-3">
        <label className="flex items-center gap-2 text-xs text-foreground-secondary">
          <input type="checkbox" checked={confirmEmergency} data-testid="emergency-confirm"
            onChange={event => setConfirmEmergency(event.target.checked)} />
          비상 종료를 확인합니다
        </label>
        <button type="button" className="btn-danger mt-2 w-full" data-testid="emergency-button"
          disabled={busy || !confirmEmergency}
          onClick={() => onAction(() => cryptoApi.mode("EMERGENCY_ON", true))}>
          EMERGENCY CLOSE
        </button>
        <p className="mt-2 text-[11px] text-muted">
          신규 진입을 먼저 차단한 뒤 보유 포지션을 즉시 종료합니다. 해제는 수동입니다.
        </p>
      </div>
      )}

      {state.state.mode === "EMERGENCY" && (
        <button type="button" className="btn-muted mt-3 w-full" disabled={busy}
          data-testid="emergency-release"
          onClick={() => onAction(() => cryptoApi.mode("EMERGENCY_RELEASE"))}>
          비상 해제 · 수동 모드로
        </button>
      )}

      {/* AUTO is a state the engine defines but refuses to enter, because no strategy exists yet.
          It stays as one disabled line rather than a panel: a large control for something that
          cannot be switched on only competes with the controls that can. The full explanation
          moved to the run detail section. */}
      <p className="mt-3 flex items-center justify-between text-[11px] text-muted"
        data-testid="auto-note">
        <span>AUTO</span>
        <span className="font-medium" title={state.state.auto_unavailable_reason}>준비중</span>
      </p>

      {error && <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger"
        role="alert" data-testid="order-error">{error}</p>}
    </div>
  );
}

/** What produced the figures on screen: a live public feed, or a recorded tape.
 *  A replay's fills come from a synthesised book, so the two must never look alike. */
export function SourceBanner({ state }: { state: CryptoState }) {
  const restored = state.recovery?.restored === true;
  const repaired = state.recovery?.torn_writes_repaired ?? [];
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2" data-testid="source-banner">
      <StatusBadge value="LIVE_PAPER" label="실시간 페이퍼" />
      {restored && (
        <span className="rounded-md border border-warning bg-warning-soft px-2 py-1 text-xs font-semibold text-warning"
          data-testid="restored-badge">
          복구된 세션 · 원장 {state.recovery?.ledger_events}건 / 입력 {state.recovery?.tape_records}건 이어받음
        </span>
      )}
      {repaired.length > 0 && (
        <span className="rounded-md border border-danger bg-danger-soft px-2 py-1 text-xs font-semibold text-danger"
          data-testid="torn-badge">
          잘린 기록 {repaired.length}건 복구
        </span>
      )}
      <span className="text-[11px] text-muted">
        체결은 Bybit 공개 호가를 소모합니다. 과거 구간 재생은 별도 화면이며 합성 호가를 씁니다.
      </span>
    </div>
  );
}

export function PerformancePanel({ performance }: { performance: Performance | null }) {
  if (!performance) return <p className="panel p-5 text-sm text-muted">성과를 계산하는 중입니다.</p>;
  if (performance.trades === 0) {
    return <div className="panel p-5 text-sm text-muted" data-testid="performance-panel">
      아직 완결된 거래가 없습니다. 포지션을 종료하면 이 세션의 성과가 집계됩니다.
    </div>;
  }
  const cells: [string, string, string?][] = [
    ["거래", `${performance.trades}건`, `승 ${performance.wins} · 패 ${performance.losses}`],
    ["승률", percent(performance.win_rate), `기대값 ${signedUsdt(performance.expectancy)}`],
    // Named in full because the header now shows the other one. "순손익" alone was the
    // ambiguity that made a reset look like it had not worked.
    ["전체 누적 손익", signedUsdt(performance.net_pnl), `≈ ${signedKrw(performance.krw?.net_pnl)}`],
    ["총손익", signedUsdt(performance.gross_pnl), `수수료 ${usdt(performance.fees, 4)} · 펀딩 ${signedUsdt(performance.funding)}`],
    ["평균 이익", signedUsdt(performance.avg_win), `평균 손실 ${signedUsdt(performance.avg_loss)}`],
    ["Profit Factor", performance.profit_factor == null ? "-" : Number(performance.profit_factor).toFixed(2),
     `최장 연패 ${performance.longest_losing_streak}회`],
    ["MDD", usdt(performance.max_drawdown, 4),
     performance.max_drawdown_fraction == null ? undefined : percent(performance.max_drawdown_fraction, 2)],
    ["보유시간 중앙값", duration(performance.hold_ms_median),
     `누적 ${duration(performance.hold_ms_total)}`],
    ["MAE / MFE", `${signedUsdt(performance.max_adverse_excursion)} / ${signedUsdt(performance.max_favourable_excursion)}`,
     `강제청산 ${performance.liquidations}회`],
  ];
  const segment = performance.current_segment;
  return (
    <div className="panel p-5" data-testid="performance-panel">
      {segment && (segment.reset_count > 0) && (
        <div className="mb-4 rounded-lg border border-line bg-surface-alt p-3"
          data-testid="current-segment-panel">
          <p className="mb-2 text-xs font-semibold text-foreground">
            현재 구간 <span className="font-normal text-muted">
              · 초기화 {segment.reset_count}회차 이후</span>
          </p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 xl:grid-cols-6">
            {([
              ["거래", `${segment.trades}건`],
              ["승률", percent(segment.win_rate)],
              ["순손익", signedUsdt(segment.current_segment_net_pnl)],
              ["수수료", usdt(segment.current_segment_fees, 4)],
              ["펀딩", signedUsdt(segment.current_segment_funding)],
              ["MDD", usdt(segment.max_drawdown, 4)],
            ] as [string, string][]).map(([label, value]) => (
              <div key={label}>
                <dt className="text-[11px] text-muted">{label}</dt>
                <dd className={`text-xs tabular-nums ${label === "순손익"
                  ? toneClass(segment.current_segment_net_pnl) : "text-foreground-secondary"}`}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-[11px] text-muted">
            아래는 초기화 이전을 포함한 전체 누적입니다. 초기화는 기록을 지우지 않습니다.
          </p>
        </div>
      )}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 xl:grid-cols-5">
        {cells.map(([label, value, meta]) => (
          <div key={label}>
            <dt className="text-xs text-muted">{label}</dt>
            <dd className={`tabular-nums ${toneClass(
              label === "전체 누적 손익" ? performance.net_pnl : label === "총손익" ? performance.gross_pnl : null)}`}>
              {value}
              {meta && <span className="mt-0.5 block text-[11px] font-normal text-muted">{meta}</span>}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-[11px] text-muted" data-testid="performance-source">
        모든 수치는 원장에서 접어 낸 값입니다. 엔진이 들고 있던 누계와 대조한 결과:{" "}
        {Object.entries(performance.reconciliation)
          .filter(([key]) => key.includes("match"))
          .every(([, value]) => value === true) ? "일치" : "불일치"}
      </p>
    </div>
  );
}

const TRADE_COLUMNS = ["#", "방향", "수량", "진입", "청산", "보유", "총손익", "순손익", "MAE/MFE", "출처"];

/** Where a closed trade's result went, one line per cause. The slippage line is already inside
 *  the gross figure (fills are priced at the book, contract C1), so it is shown for reading and
 *  is not part of the subtraction; the note on the row says so. */
export function TradeCostDetail({ breakdown }: { breakdown: TradeBreakdown }) {
  const won = breakdown.krw ?? {};
  // Won first (converted by the backend at the run's rate), USDT beside it.
  const both = (krwText: string, usdtText: string) => won.net_realized_pnl != null ? `${krwText} · ${usdtText}` : usdtText;
  const rows: [string, string, string | null][] = [
    ["Gross Realized PnL (체결가 기준)", both(signedKrw(won.gross_realized_pnl), signedUsdt(breakdown.gross_realized_pnl)), breakdown.gross_realized_pnl],
    ["Entry Fee (진입 수수료)", both(`-${krw(won.entry_fee)}`, `-${usdt(breakdown.entry_fee, 4)}`), null],
    ["Exit Fee (청산 수수료)", both(`-${krw(won.exit_fee)}`, `-${usdt(breakdown.exit_fee, 4)}`), null],
    ["Funding", both(signedKrw(won.funding_pnl), signedUsdt(breakdown.funding_pnl)), breakdown.funding_pnl],
  ];
  return (
    <div className="grid gap-x-6 gap-y-1 text-[11px] sm:grid-cols-2" data-testid={`trade-detail-${breakdown.index}`}>
      <dl className="space-y-1">
        {rows.map(([label, value, tone]) => (
          <div key={label} className="flex justify-between gap-3">
            <dt className="text-muted">{label}</dt>
            <dd className={`tabular-nums ${tone == null ? "text-foreground-secondary" : toneClass(tone)}`}>{value}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-3 border-t border-line pt-1 font-semibold">
          <dt>Net Realized PnL (순손익)</dt>
          <dd className={`tabular-nums ${toneClass(breakdown.net_realized_pnl)}`}
            data-testid={`trade-detail-net-${breakdown.index}`}>{both(signedKrw(won.net_realized_pnl), signedUsdt(breakdown.net_realized_pnl))}</dd>
        </div>
      </dl>
      <dl className="space-y-1">
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Slippage·스프레드 (Mark 대비, Gross에 포함됨)</dt>
          <dd className={`tabular-nums ${toneClass(breakdown.slippage_pnl)}`}>{both(signedKrw(won.slippage_pnl), signedUsdt(breakdown.slippage_pnl))}</dd>
        </div>
        <p className="text-muted">
          진입 {signedUsdt(breakdown.entry_slippage)} ·
          청산 {signedUsdt(breakdown.exit_slippage)} (비용 +). 체결가에 이미 반영되어 Gross에 포함된 금액이며
          순손익에서 다시 빼지 않습니다.
        </p>
      </dl>
    </div>
  );
}

export function TradeHistory({ trades, breakdowns }: { trades: TradeRow[]; breakdowns?: TradeBreakdown[] | null }) {
  const [open, setOpen] = useState<number | null>(null);
  const byIndex = new Map((breakdowns ?? []).map(row => [row.index, row]));
  if (trades.length === 0) {
    return <p className="panel p-5 text-sm text-muted" data-testid="trade-history">완결된 거래가 없습니다.</p>;
  }
  return (
    <div className="table-wrap" data-testid="trade-history">
      <table className="w-full text-sm">
        <thead><tr className="border-b border-line text-left text-xs text-muted">
          {TRADE_COLUMNS.map(column => <th key={column} className="px-3 py-2 font-medium">{column}</th>)}
        </tr></thead>
        <tbody>
          {trades.map(trade => {
            const detail = byIndex.get(trade.index);
            const expanded = open === trade.index && detail != null;
            return (
            <Fragment key={trade.index}>
            {/* No wrapping in a data row: the table scrolls sideways inside its own wrapper, so a
                narrow phone gets one line per trade instead of a row three lines tall. */}
            <tr className="whitespace-nowrap border-b border-line/60 last:border-0">
              <td className="whitespace-nowrap px-3 py-2 tabular-nums text-muted">
                {trade.index}
                {/* In the first column so it is on screen on a phone without scrolling the table. */}
                {detail != null && (
                  <button type="button" className="ml-2 whitespace-nowrap text-[11px] font-medium text-primary hover:underline"
                    aria-expanded={expanded} data-testid={`trade-detail-toggle-${trade.index}`}
                    onClick={() => setOpen(expanded ? null : trade.index)}>
                    {expanded ? "접기" : "비용 상세"}
                  </button>
                )}
              </td>
              <td className="px-3 py-2">
                <span className={`rounded px-1.5 py-0.5 text-xs font-bold ${
                  trade.side === "LONG" ? "bg-success-soft text-success" : "bg-danger-soft text-danger"}`}>
                  {trade.side}
                </span>
                <span className="ml-1.5 text-[11px] text-muted">{trade.leverage}x</span>
              </td>
              <td className="px-3 py-2 tabular-nums">{qty(trade.qty)}</td>
              <td className="px-3 py-2 tabular-nums">{price(trade.entry_price)}</td>
              <td className="px-3 py-2 tabular-nums">{price(trade.exit_price)}</td>
              <td className="px-3 py-2 tabular-nums text-muted">{duration(trade.hold_ms)}</td>
              <td className={`px-3 py-2 tabular-nums ${toneClass(trade.gross_pnl)}`}>
                {signedUsdt(trade.gross_pnl)}
              </td>
              <td className={`px-3 py-2 tabular-nums font-medium ${toneClass(trade.net_pnl)}`}>
                {signedUsdt(trade.net_pnl)}
              </td>
              <td className="px-3 py-2 text-[11px] tabular-nums text-muted">
                {signedUsdt(trade.max_adverse_excursion, 2)} / {signedUsdt(trade.max_favourable_excursion, 2)}
              </td>
              <td className="px-3 py-2 text-[11px]">
                {trade.liquidated
                  ? <span className="font-semibold text-danger">강제청산</span>
                  : <span className="text-muted">{trade.origin}</span>}
              </td>
            </tr>
            {expanded && (
              <tr className="border-b border-line/60 bg-surface-alt">
                <td colSpan={TRADE_COLUMNS.length} className="px-3 py-3">
                  {/* Pinned to the visible left edge and capped in width, so on a phone the labels
                      and their values stay side by side instead of spreading across the table. */}
                  <div className="sticky left-3 w-[min(44rem,calc(100vw-4rem))]">
                    <TradeCostDetail breakdown={detail} />
                  </div>
                </td>
              </tr>
            )}
            </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const LEDGER_COLUMNS = ["시각", "이벤트", "내용"];

export function LedgerTable({ events }: { events: LedgerEvent[] }) {
  if (events.length === 0) return <p className="panel p-5 text-sm text-muted">원장이 비어 있습니다.</p>;
  return (
    <div className="table-wrap" data-testid="ledger-table">
      <table className="w-full text-sm">
        <thead><tr className="border-b border-line text-left text-xs text-muted">
          {LEDGER_COLUMNS.map(column => <th key={column} className="px-4 py-2 font-medium">{column}</th>)}
        </tr></thead>
        <tbody>
          {events.map(event => (
            <tr key={event.seq} className="border-b border-line/60 last:border-0">
              <td className="whitespace-nowrap px-4 py-2 tabular-nums text-muted">{clockKst(event.ts_ms)}</td>
              <td className="whitespace-nowrap px-4 py-2 font-medium">
                {EVENT_LABELS[event.event_type] || event.event_type}
              </td>
              <td className="px-4 py-2 text-xs text-foreground-secondary">{describe(event)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One line per event, in the terms the operator acted in. */
export function describe(event: LedgerEvent): string {
  const value = (key: string) => event[key] as string | undefined;
  switch (event.event_type) {
    case "ACCOUNT_RESET":
      // The preserved figures are named here because the whole point of the line is that the
      // record survived it.
      return `${describeReset(event)} · 거래·수수료 기록 보존 (실현손익 누계 ${signedUsdt(value("preserved_realized_pnl"))})`;
    case "FILL":
      return `${value("side")} ${qty(value("fill_qty"))} @ ${price(value("fill_price"))} · ${value("levels_consumed")}호가 소모`;
    case "POSITION_OPEN":
    case "POSITION_INCREASE":
      return `수량 ${qty(value("signed_qty"))} · 평단 ${price(value("avg_entry"))} · 청산가 ${price(value("liquidation_price"))}`;
    case "POSITION_REDUCE":
    case "POSITION_CLOSE":
      return `${qty(value("closed_qty"))} 청산 · 총손익 ${signedUsdt(value("gross_pnl"))} · 순손익 ${signedUsdt(value("net_pnl"))}`;
    case "FEE":
      return `${usdt(value("amount"), 6)} · ${value("liquidity")} ${rateBps(value("fee_rate"))} · ${value("cost_class")}`;
    case "FUNDING":
      return `${value("direction")} ${signedUsdt(value("amount_paid"), 6)} · 요율 ${rateBps(value("funding_rate"))}`;
    case "LIQUIDATION":
      return `Mark ${price(value("mark_price"))} · 계산 청산가 ${price(value("computed_liquidation_price"))}`;
    case "ORDER_REJECTED":
      return `${value("code")} · ${value("message")}`;
    case "MODE_CHANGE":
      return `${value("previous_mode")} → ${value("mode")}`;
    case "LEVERAGE_CHANGE":
      return `${value("previous_leverage")}x → ${value("leverage")}x`;
    case "ORDER_SUBMITTED":
      return `${value("side")} ${value("intent")} ${qty(value("qty"))}`;
    case "RUN_START":
      return `시작 자본 ${usdt(value("starting_capital_usdt"))} · 리스크 티어 ${event.risk_tier_count}개`;
    default:
      return "";
  }
}

export function RunFooter({ state }: { state: CryptoState }) {
  return (
    <dl className="grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4" data-testid="run-footer">
      <div className="panel p-4">
        <dt className="text-muted">수수료 가정</dt>
        <dd className="mt-1 text-foreground-secondary">
          Taker {rateBps(state.fees.taker_rate)} · Maker {rateBps(state.fees.maker_rate)}
          <span className="mt-1 block text-[11px] text-muted">{state.fees.version} · {state.fees.effective_date} · {state.fees.basis}</span>
        </dd>
      </div>
      <div className="panel p-4">
        <dt className="text-muted">환율 고정</dt>
        <dd className="mt-1 text-foreground-secondary">
          1 USDT = {Number(state.fx.krw_per_usdt).toLocaleString("ko-KR")}원
          <span className="mt-1 block text-[11px] text-muted">{state.fx.source} · {state.fx.asof_utc}</span>
        </dd>
      </div>
      <div className="panel p-4">
        <dt className="text-muted">슬리피지 모델</dt>
        <dd className="mt-1 text-foreground-secondary">
          {state.slippage.model}{state.slippage.model === "FIXED_BPS" ? ` ${state.slippage.bps} bp` : ""}
          <span className="mt-1 block text-[11px] text-muted">호가 소모는 별도로 항상 적용</span>
        </dd>
      </div>
      <div className="panel p-4">
        <dt className="text-muted">런</dt>
        <dd className="mt-1 text-foreground-secondary">
          {state.run_id}
          <span className="mt-1 block text-[11px] text-muted">
            엔진 {state.engine_version} · 원장 {state.ledger_event_count}건 · 입력 {state.input_record_count}건 · 청산 {state.liquidation_count}회
          </span>
        </dd>
      </div>
      {/* Archive and restore path. Operational rather than decision-making, so it lives here in
          the collapsed detail and never on the trading screen. */}
      {state.storage && (
        <div className="panel p-4" data-testid="storage-footer">
          <dt className="text-muted">테이프 보관</dt>
          <dd className="mt-1 text-foreground-secondary">
            세그먼트 {state.storage.segments}개 · {(state.storage.total_bytes / 1024 / 1024).toFixed(1)} MB
            <span className="mt-1 block text-[11px] text-muted">
              활성 {(state.storage.active_bytes / 1024).toFixed(0)} KB · 원장 {(state.storage.ledger_bytes / 1024).toFixed(0)} KB
              {state.storage.compressed_segments > 0 && ` · 압축 ${state.storage.compressed_segments}개`}
            </span>
          </dd>
        </div>
      )}
      {state.recovery?.source && (
        <div className="panel p-4" data-testid="recovery-footer">
          <dt className="text-muted">마지막 복구</dt>
          <dd className="mt-1 text-foreground-secondary">
            {state.recovery.source === "CHECKPOINT"
              ? `체크포인트 (세그먼트 ${state.recovery.checkpoint_segment})`
              : "전체 재생"}
            <span className="mt-1 block text-[11px] text-muted">
              입력 {state.recovery.tape_records}건 · 원장 꼬리 보정 {state.recovery.ledger_tail_rewritten_bytes}바이트
            </span>
          </dd>
        </div>
      )}
    </dl>
  );
}

/** Visual refresh for the open position's headline PnL. The 1 s account poll stays as it is;
 *  this reads a small route that prices the position on the newest feed tick from a clone, so
 *  nothing is recorded and the ledger keeps its own rhythm. */
export const LIVE_POLL_MS = 333;
export const LIVE_POLL_HIDDEN_MS = 2000;

export function useLivePnl(enabled: boolean, intervalMs = LIVE_POLL_MS) {
  const [live, setLive] = useState<LivePnl | null>(null);
  useEffect(() => {
    if (!enabled) { setLive(null); return; }
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    // Sequential, never overlapping: the next request starts only after the previous one ended,
    // so a slow network lowers the rate instead of stacking requests. A hidden tab backs off.
    const tick = async () => {
      if (stopped) return;
      if (typeof document !== "undefined" && document.hidden) {
        timer = window.setTimeout(tick, LIVE_POLL_HIDDEN_MS);
        return;
      }
      const started = Date.now();
      controller = new AbortController();
      try {
        const body = await cryptoApi.live(controller.signal);
        if (!stopped) setLive(body);
      } catch { /* the 1 s account poll remains the fallback */ }
      if (!stopped) timer = window.setTimeout(tick, Math.max(0, intervalMs - (Date.now() - started)));
    };
    void tick();
    return () => { stopped = true; controller?.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [enabled, intervalMs]);
  return live;
}

export function useCryptoTerminal(pollMs = 1000) {
  const [state, setState] = useState<CryptoState | null>(null);
  const [bars, setBars] = useState<ChartBar[]>([]);
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [sizing, setSizing] = useState<CryptoSizing | null>(null);
  const [breakdown, setBreakdown] = useState<PnlBreakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [next, ledger, summary, history, sizes] = await Promise.all([
        cryptoApi.state(), cryptoApi.ledger(60), cryptoApi.performance(), cryptoApi.trades(50),
        // Sizing moves with the mark and with the balance, so it is refreshed on the same tick
        // as the account rather than only when a preset is pressed. A stale MAX is a rejected
        // order. The engine prices all four presets for both sides in a couple of milliseconds.
        cryptoApi.sizing(),
      ]);
      setState(next);
      setEvents(ledger.events);
      setPerformance(summary);
      setTrades(history.trades);
      setSizing(sizes);
      setError(null);
    } catch (exception) {
      setError(exception instanceof CryptoApiError ? exception.message : "상태 조회 실패");
    }
    // Fetched on its own so a server that predates the route (404) or a failed preview never
    // takes the rest of the terminal down with it. Missing breakdown just hides the panel.
    try { setBreakdown(await cryptoApi.pnlBreakdown()); } catch { setBreakdown(null); }
  }, []);

  const refreshChart = useCallback(async () => {
    try { setBars((await cryptoApi.chart()).bars); } catch { /* the chart is not the authority */ }
  }, []);

  const act = useCallback((run: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    void run()
      .then(() => refresh())
      .catch(exception => setActionError(
        exception instanceof CryptoApiError ? `${exception.code} · ${exception.message}` : "요청 실패"))
      .finally(() => setBusy(false));
  }, [refresh]);

  useEffect(() => {
    void refresh();
    void refreshChart();
    const stateTimer = window.setInterval(refresh, pollMs);
    const chartTimer = window.setInterval(refreshChart, 15_000);
    return () => { window.clearInterval(stateTimer); window.clearInterval(chartTimer); };
  }, [refresh, refreshChart, pollMs]);

  return { state, bars, events, performance, trades, sizing, breakdown, error, actionError, busy, act, refresh };
}
