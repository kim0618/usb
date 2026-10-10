"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  COVERAGE_LABELS, LAYER_LABELS, MARKET_CONTEXT_POLL_MS, MISSING, MarketContextError,
  SIDE_LABELS, STATE_LABELS, VANISH_LABELS, WALL_STATE_LABELS,
  bps, btc, clockKst, duration, fetchPanel, num, pct, price, rangeLabel,
  reasonLabel, reasonText, retestLabel, signedPct, stateTone, trendLabel, usdt, wallStateTone,
} from "@/lib/market-context";
import type {
  AuxiliaryLayer, DerivativesLayer, FlowLayer, LayerName, LayerStateOrAbsent, LiquidityLayer,
  LiquiditySide, MarketContextPayload, PanelQuery, PriceLayer, Wall,
} from "@/lib/market-context";

/** Manual Market Context V1: one read-only context panel beside the manual order ticket.
 *
 *  It holds no order control, renders no direction and computes no score. What it does is put the
 *  five already-validated layers in front of the person who is deciding by hand, each one
 *  attributable to the contract that produced it, each one saying how fresh it is.
 *
 *  Two display rules shape every section below, and both exist because of a specific way a context
 *  strip misleads.
 *
 *  **A value is shown with its state, or it is not shown.** A dash and a reason is the correct
 *  rendering of something unobserved; a zero is not, because a depth band really can be empty and
 *  a flow window really can have no trades. And no layer borrows another's freshness: the header
 *  carries five words side by side rather than one, so a live book cannot make a stale structure
 *  look current.
 *
 *  **Nothing is combined.** There is no joint row, no agreement count and no overall verdict,
 *  because Market Context R0 measured the joint model and it came back `A_NO_CONTEXT_EDGE`. The
 *  backend publishes `rollup: {exists: false}` with that measurement, and this screen shows it
 *  rather than quietly filling the gap.
 */

// --------------------------------------------------------------------------- data

export function useMarketContext(query: PanelQuery) {
  const [payload, setPayload] = useState<MarketContextPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polls, setPolls] = useState(0);
  const latest = useRef(query);
  latest.current = query;
  // The symbol is part of the effect key: a poll in flight for BTC must not land as the ETH
  // answer. The multi-symbol terminal learned this the expensive way - a timer that was not keyed
  // to its symbol kept answering with the previous tab's figures and had no visual signature.
  const key = `${query.symbol}|${query.minNotionalUsdt ?? ""}`;

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setPayload(null);
    const poll = async () => {
      try {
        const next = await fetchPanel(latest.current, controller.signal);
        if (!alive) return;
        setPayload(next);
        setError(null);
        setPolls(count => count + 1);
      } catch (caught) {
        if (!alive || (caught as Error)?.name === "AbortError") return;
        // The last payload stays on screen *and* the error is shown. Every layer already carries
        // its own age, so the operator can see both that the poll is failing and how old what
        // they are looking at is - which is strictly more than either on its own.
        setError(caught instanceof MarketContextError ? caught.message : String(caught));
      }
      if (alive) timer = setTimeout(poll, MARKET_CONTEXT_POLL_MS);
    };
    void poll();
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [key]);

  return { payload, error, polls };
}

// --------------------------------------------------------------------------- primitives

/** A state, as its word plus a tone. The word is mandatory: section 4 of the contract forbids
 *  colour from carrying a state by itself, so this component never renders the class alone. */
export function StateChip({ state, label }: { state: LayerStateOrAbsent; label?: string }) {
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px]
                      font-semibold leading-none ${stateTone(state)}`}
      data-testid="mc-state-chip" data-state={state}>
      {label ?? STATE_LABELS[state]}
    </span>
  );
}

function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 py-0.5">
      <dt className="shrink-0 text-[11px] text-muted">{label}</dt>
      <dd className="min-w-0 text-right text-[11px] font-medium text-foreground">
        {children}
        {hint ? <span className="ml-1 font-normal text-muted">{hint}</span> : null}
      </dd>
    </div>
  );
}

/** A value the backend could not observe: the dash, and why. Never a zero. */
function Absent({ reason }: { reason: string | null | undefined }) {
  return (
    <span className="text-muted" data-testid="mc-absent">
      {MISSING}
      {reason ? <span className="ml-1 text-[10px]">{reasonLabel(reason)}</span> : null}
    </span>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <p className="mt-2 text-[10px] leading-relaxed text-muted">{children}</p>;
}

/** One layer. Collapsible, keeping its slot and its state word whether open or shut.
 *
 *  Collapsed by default on a phone for every layer but the first: at 390px the panel sits under
 *  the chart and above the order ticket, and five expanded layers would push the ticket off the
 *  first two screens. The header alone still carries the layer's state, so a collapsed section is
 *  never silent about whether its data is live.
 */
export function LayerSection({ name, state, reasons, defaultOpen, children, right }: {
  name: LayerName;
  state: LayerStateOrAbsent;
  reasons?: string[];
  defaultOpen?: boolean;
  children: ReactNode;
  right?: ReactNode;
}) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  const absent = state === "UNAVAILABLE";
  return (
    <section className="border-t border-line first:border-t-0" data-testid={`mc-layer-${name}`}
      data-state={state}>
      <h3>
        <button type="button" onClick={() => setOpen(value => !value)}
          aria-expanded={open}
          className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left
                     hover:bg-surface-hover">
          <span className="flex min-w-0 items-center gap-2">
            <span aria-hidden="true" className="w-3 shrink-0 text-[10px] text-muted">
              {open ? "−" : "+"}
            </span>
            <span className="truncate text-[11px] font-semibold tracking-wide text-foreground">
              {LAYER_LABELS[name]}
            </span>
            <StateChip state={state} />
          </span>
          <span className="shrink-0 text-[10px] text-muted">{right}</span>
        </button>
      </h3>
      {open ? (
        <div className="px-3 pb-3">
          {reasons && reasons.length > 0 ? (
            <p className="mb-2 text-[10px] text-muted" data-testid={`mc-reasons-${name}`}>
              {reasonText(reasons)}
            </p>
          ) : null}
          {absent ? null : children}
          {absent ? (
            <p className="text-[11px] text-muted" data-testid={`mc-unavailable-${name}`}>
              이 심볼에는 이 레이어가 없습니다. 빈 값이 아니라 부재입니다.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// --------------------------------------------------------------------------- PRICE

function LevelRow({ level }: { level: PriceLayer["levels"] extends null ? never
  : NonNullable<PriceLayer["levels"]>["resistance"] }) {
  const heading = level.side === "RESISTANCE" ? "저항 (가장 가까운)" : "지지 (가장 가까운)";
  if (!level.available) {
    return <Row label={heading}><Absent reason={level.unavailable_reason} /></Row>;
  }
  return (
    <>
      <Row label={heading}>
        {price(level.price)}
        <span className="ml-1 font-normal text-muted">{signedPct(level.distance_pct)}</span>
      </Row>
      <Row label="  수명주기 / 기원">
        <span className="text-[10px]">
          {level.lifecycle} · {level.origin_kind} · {level.confirm_tf}
          {level.sources ? ` · src ${level.sources}` : ""}
        </span>
      </Row>
      <Row label="  강도 / 터치 / id">
        <span className="text-[10px]">
          {level.strength ?? MISSING} / {level.touches ?? MISSING} / {level.level_id ?? MISSING}
        </span>
      </Row>
    </>
  );
}

export function PriceSection({ layer }: { layer: PriceLayer }) {
  const row = layer.row;
  const events = layer.events;
  const range = layer.range;
  const previous = layer.prev_session;
  const series = layer.series;
  if (!row || !layer.levels) {
    return <p className="text-[11px] text-muted">{MISSING} 가격구조를 읽지 못했습니다.</p>;
  }
  return (
    <dl data-testid="mc-price">
      <Row label="확정봉 종가" hint={`${clockKst(row.bar_close_ms)} · ${duration(row.bar_age_ms)} 전`}>
        {price(row.close)}
      </Row>
      <Row label="구조 (스윙 라벨)">
        <span className="text-[10px]">
          {trendLabel(row.trend_structure)} · {row.last_high_label ?? MISSING}/
          {row.last_low_label ?? MISSING}
        </span>
      </Row>
      <LevelRow level={layer.levels.resistance} />
      <LevelRow level={layer.levels.support} />
      <Row label="돌파 상태">
        {events && events.breakout_state !== "NONE" ? (
          <span className="text-[10px]">
            {events.breakout_state} · lvl {events.breakout_level_id ?? MISSING} ·{" "}
            {events.breakout_confirm_tf ?? MISSING} · {events.breakout_age_min ?? MISSING}분 전
          </span>
        ) : <Absent reason="NO_BREAKOUT_RECORDED" />}
      </Row>
      <Row label="재방문 (retest)">
        <span className="text-[10px]">
          {retestLabel(events?.retest_state)}
          {events?.retest_level_id != null ? ` · lvl ${events.retest_level_id}` : ""}
        </span>
      </Row>
      <Row label="실패 돌파">
        <span className="text-[10px]">{events?.failed_break_state ?? MISSING}</span>
      </Row>
      <Row label="직전 스윕">
        {events?.sweep_direction ? (
          <span className="text-[10px]">
            {events.sweep_direction} · {events.sweep_age_min ?? MISSING}분 전
          </span>
        ) : <Absent reason="NO_SWEEP_RECORDED" />}
      </Row>
      <Row label="레인지">
        {range && range.high != null && range.low != null ? (
          <span className="text-[10px]">
            {rangeLabel(range.state)} · {price(range.low)}~{price(range.high)}
            {range.position != null ? ` · 위치 ${pct(range.position * 100, 1)}` : ""}
          </span>
        ) : (
          <span className="text-[10px]">{rangeLabel(range?.state)}
            <span className="ml-1 text-muted">경계 {MISSING}</span>
          </span>
        )}
      </Row>
      <Row label="발행 레벨 수" hint={`ATR 5m ${num(row.atr_5m, 1)} · 1h ${num(row.atr_1h, 1)}`}>
        {row.active_level_count}
      </Row>

      <div className="mt-2 rounded border border-line bg-surface-alt px-2 py-1.5"
        data-testid="mc-prev-session">
        <p className="mb-1 text-[10px] font-semibold text-foreground-secondary">
          전일 세션 고저 <span className="font-normal text-muted">레벨 북 밖의 별도 context</span>
        </p>
        <dl>
          <Row label="전일 고가">
            {previous?.high != null
              ? <>{price(previous.high)}
                <span className="ml-1 font-normal text-muted">
                  {signedPct(previous.high_distance_pct)}</span></>
              : <Absent reason="NO_PREVIOUS_SESSION" />}
          </Row>
          <Row label="전일 저가">
            {previous?.low != null
              ? <>{price(previous.low)}
                <span className="ml-1 font-normal text-muted">
                  {signedPct(previous.low_distance_pct)}</span></>
              : <Absent reason="NO_PREVIOUS_SESSION" />}
          </Row>
        </dl>
        <Note>{previous?.note}</Note>
      </div>

      {series ? (
        <Note>
          R1 큐레이션 · theta 1h {series.theta_1h} / 5m {series.theta_5m} · {series.venue} ·
          시리즈 {num(series.series_rows, 0)}분 (앵커 고정, 전방 확장만) ·
          repaint {series.repaint_violations} / vanished {series.vanished_levels} ·
          재계산 {series.recompute_ms}ms
        </Note>
      ) : null}
      <Note>{layer.note}</Note>
    </dl>
  );
}

// --------------------------------------------------------------------------- LIQUIDITY

function WallRow({ wall }: { wall: Wall }) {
  return (
    <>
      <Row label="  가격 / 거리">
        {price(wall.price)}
        <span className="ml-1 font-normal text-muted">
          {bps(wall.distance_bps)} · {pct(wall.distance_pct, 4)}
        </span>
      </Row>
      <Row label="  명목 / 수량">
        {usdt(wall.notional_usdt)}
        <span className="ml-1 font-normal text-muted">{btc(wall.qty_btc)}</span>
      </Row>
      <Row label="  관측 지속">
        {duration(wall.persistence_ms)}
        <span className="ml-1 font-normal text-muted">
          {wall.persistence_source === "CARRIED" ? "이어받음" : "자체"} ·{" "}
          {wall.continuity_status === "CARRIED"
            ? `carry ${wall.carried_members ?? MISSING}개`
            : "신규"}
        </span>
      </Row>
      <Row label="  bin / coverage">
        <span className="text-[10px]">
          {price(wall.bin_low)}~{price(wall.bin_high)} · {wall.bin_members ?? MISSING}개 ·{" "}
          {wall.coverage}
        </span>
      </Row>
    </>
  );
}

function SideBlock({ side }: { side: LiquiditySide }) {
  return (
    <div data-testid={`mc-wall-${side.side}`} data-wall-state={side.wall_state}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold text-foreground-secondary">
          {SIDE_LABELS[side.side]}
        </span>
        <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px]
                          font-semibold leading-none ${wallStateTone(side.wall_state)}`}>
          {WALL_STATE_LABELS[side.wall_state]}
        </span>
      </div>
      <dl>
        {side.nearest_wall ? <WallRow wall={side.nearest_wall} /> : (
          <Row label="  가장 가까운 벽">
            <Absent reason={side.wall_state_reason} />
          </Row>
        )}
        <Row label="  후보 / 선정 / 표시">
          <span className="text-[10px]">
            {side.candidates_total ?? MISSING} / {side.walls_selected ?? MISSING} /{" "}
            {side.walls_shown ?? MISSING}
          </span>
        </Row>
        {side.depth.map(band => (
          <Row key={band.band_pct} label={`  깊이 ±${band.band_pct}%`}>
            {band.observed_notional == null ? <Absent reason="NOT_OBSERVED" /> : (
              <>
                <span className={band.is_lower_bound ? "text-warning" : undefined}>
                  {band.is_lower_bound ? "≥ " : ""}{usdt(band.observed_notional)}
                </span>
                <span className="ml-1 font-normal text-muted">
                  {COVERAGE_LABELS[band.coverage]}
                </span>
              </>
            )}
          </Row>
        ))}
      </dl>
    </div>
  );
}

export function LiquiditySection({ layer }: { layer: LiquidityLayer }) {
  const book = layer.book;
  const sides = layer.sides;
  if (!sides || !book) {
    return <p className="text-[11px] text-muted">{MISSING} 호가를 읽지 못했습니다.</p>;
  }
  const imbalance = layer.band_imbalance;
  return (
    <div data-testid="mc-liquidity">
      <dl>
        <Row label="mid / spread">
          {price(book.mid)}
          <span className="ml-1 font-normal text-muted">{bps(book.spread_bps)}</span>
        </Row>
        <Row label="관측 구간">
          {book.observed_low == null ? <Absent reason="NO_BOOK" /> : (
            <span className="text-[10px]">
              {price(book.observed_low)}~{price(book.observed_high)} ·{" "}
              {pct(book.observed_low_pct, 3)}~{pct(book.observed_high_pct, 3)}
            </span>
          )}
        </Row>
        <Row label="±0.1% 잔량 (bid/ask)">
          {imbalance ? (
            <>
              <span className={imbalance.is_lower_bound ? "text-warning" : undefined}>
                {imbalance.is_lower_bound ? "≥ " : ""}
                {usdt(imbalance.notional_bid)} / {usdt(imbalance.notional_ask)}
              </span>
              <span className="ml-1 font-normal text-muted">
                {COVERAGE_LABELS[imbalance.coverage]}
              </span>
            </>
          ) : <Absent reason="NOT_OBSERVED" />}
        </Row>
      </dl>
      <div className="mt-2 grid grid-cols-1 gap-2">
        <SideBlock side={sides.ASK} />
        <SideBlock side={sides.BID} />
      </div>
      <dl className="mt-2">
        <Row label="벽 규칙 / carry 규칙">
          <span className="text-[10px]">
            {layer.walls?.rule ?? MISSING} · {layer.walls?.continuity_rule ?? MISSING}
          </span>
        </Row>
        <Row label="표시 필터 (운영자)">
          {usdt(layer.walls?.filter_min_notional_usdt)}
        </Row>
        <Row label="저널 나이 / 세션">
          <span className="text-[10px]">
            {duration(layer.journal?.journal_age_ms)} ·{" "}
            {layer.journal?.session_ended ? "종료" : "진행"} ·{" "}
            {layer.journal?.book_state ?? MISSING}
          </span>
        </Row>
      </dl>
      {layer.presence_base_rate ? (
        <Note>
          벽 존재는 신호가 아닙니다: 10bp 이내 ASK {pct(layer.presence_base_rate.ask_pct, 1)} ·
          BID {pct(layer.presence_base_rate.bid_pct, 1)} 샘플에서 관측,
          있을 때 평균 {num(layer.presence_base_rate.mean_per_side_when_present, 1)}개/측.
        </Note>
      ) : null}
      <Note>{layer.observed_range_note}</Note>
    </div>
  );
}

// --------------------------------------------------------------------------- FLOW

export function FlowSection({ layer }: { layer: FlowLayer }) {
  const windows = layer.windows;
  if (!windows) {
    return <p className="text-[11px] text-muted">{MISSING} 체결을 읽지 못했습니다.</p>;
  }
  const vanished = layer.vanished;
  const absorption = layer.absorption;
  return (
    <div data-testid="mc-flow">
      <dl>
        {["5s", "15s", "60s"].map(label => {
          const window = windows[label];
          if (!window) return null;
          return (
            <div key={label} data-testid={`mc-flow-${label}`} data-state={window.state}>
              <Row label={`${label} 공격적 BUY/SELL`}>
                {window.buy_usdt == null ? <Absent reason={window.coverage_reason} /> : (
                  <>
                    {usdt(window.buy_usdt)} / {usdt(window.sell_usdt)}
                    <span className={`ml-1 font-normal ${window.state === "LIVE"
                      ? "text-muted" : "text-warning"}`}>
                      {STATE_LABELS[window.state]}
                    </span>
                  </>
                )}
              </Row>
              <Row label={`  ${label} 불균형 / 건수`}>
                <span className="text-[10px]">
                  {window.imbalance_usdt == null ? MISSING : num(window.imbalance_usdt, 4)} ·{" "}
                  {window.trades ?? MISSING}건 · {COVERAGE_LABELS[window.coverage]}
                </span>
              </Row>
            </div>
          );
        })}
        <Row label="체결 스트림">
          <span className="text-[10px]">
            {layer.trade_stream?.connected ? "연결" : "미연결"} ·{" "}
            {duration(layer.trade_stream?.age_ms)} ·{" "}
            {STATE_LABELS[layer.trade_stream?.state ?? "UNKNOWN"]}
          </span>
        </Row>
      </dl>
      <Note>
        나이는 수집기가 쓴 샘플 기준입니다(지금 기준이 아님). 저널 상태{" "}
        {STATE_LABELS[layer.trade_stream?.journal_state ?? "UNKNOWN"]}가 창 상태의 바닥입니다.
      </Note>

      <div className="mt-2 rounded border border-line bg-surface-alt px-2 py-1.5"
        data-testid="mc-vanished">
        <p className="mb-1 text-[10px] font-semibold text-foreground-secondary">
          사라진 벽 <span className="font-normal text-muted">CONSUMED로 단정하지 않습니다</span>
        </p>
        {vanished && vanished.recent.length > 0 ? (
          <dl>
            {vanished.recent.slice(0, 4).map((item, index) => (
              <Row key={`${item.bin_low}-${item.last_seen_ms}-${index}`}
                label={`  ${item.side} ${price(item.bin_low)}`}>
                <span className="text-[10px]" data-testid="mc-vanish-state"
                  data-vanish-state={item.state}>
                  {VANISH_LABELS[item.state]} · {usdt(item.notional_usdt)} ·{" "}
                  {reasonLabel(item.reason)}
                </span>
              </Row>
            ))}
          </dl>
        ) : (
          <p className="text-[11px] text-muted" data-testid="mc-vanished-none">
            {MISSING} 관측된 소멸 없음 (추적 중 {vanished?.tracked_bins ?? 0}개,
            경로 샘플 {vanished?.path_samples ?? 0})
          </p>
        )}
        {vanished ? (
          <Note>
            누적: 소비후보 {vanished.totals.CONSUMED_CANDIDATE ?? 0} ·
            취소로 보임 {vanished.totals.CANCEL_LIKE ?? 0} ·
            판정불가 {vanished.totals.UNKNOWN ?? 0}
            {layer.vanish_base_rate ? ` / 실측 기준선 ASK ${
              layer.vanish_base_rate.ask_touched_bin_pct}% · BID ${
              layer.vanish_base_rate.bid_touched_bin_pct}%` : ""}
          </Note>
        ) : null}
      </div>

      <div className="mt-2 rounded border border-line bg-surface-alt px-2 py-1.5"
        data-testid="mc-absorption" data-absorption-state={absorption?.state ?? "UNKNOWN"}>
        <p className="mb-1 text-[10px] font-semibold text-foreground-secondary">
          absorption <span className="font-normal text-muted">candidate만, 판정 아님</span>
        </p>
        {absorption?.state === "ABSORPTION_CANDIDATE" ? (
          <dl>
            <Row label="  상태">ABSORPTION_CANDIDATE</Row>
            <Row label="  방향 / 창">
              <span className="text-[10px]">{absorption.side} · {absorption.window}</span>
            </Row>
            <Row label="  공격적 체결 / 벽 명목">
              <span className="text-[10px]">
                {usdt(absorption.aggressive_usdt)} / {usdt(absorption.wall_notional_usdt)}
              </span>
            </Row>
            <Row label="  order identity 증명">아니오</Row>
          </dl>
        ) : (
          <p className="text-[11px] text-muted">
            {MISSING} 후보 없음{absorption?.reason ? ` · ${reasonLabel(absorption.reason)}` : ""}
          </p>
        )}
      </div>
      <Note>{layer.vanish_note}</Note>
      <Note>{layer.absorption_note}</Note>
    </div>
  );
}

// --------------------------------------------------------------------------- DERIVATIVES

export function DerivativesSection({ layer }: { layer: DerivativesLayer }) {
  const oi = layer.open_interest;
  const change = layer.open_interest_change;
  const funding = layer.funding;
  const basis = layer.basis;
  return (
    <dl data-testid="mc-derivatives">
      <Row label="OI (기초자산)" hint={oi?.age_ms != null ? `${duration(oi.age_ms)} 전` : undefined}>
        {oi?.base == null ? <Absent reason={oi?.unavailable_reason} /> : num(oi.base, 3)}
      </Row>
      {["5m", "15m", "1h", "4h"].map(label => {
        const window = change?.windows?.[label];
        return (
          <Row key={label} label={`  OI 변화 ${label}`}>
            {!window || !window.available
              ? <Absent reason={window?.unavailable_reason ?? change?.unavailable_reason} />
              : (
                <>
                  {num(window.base, 3)}
                  <span className="ml-1 font-normal text-muted">
                    {signedPct(window.base_pct, 4)}
                  </span>
                </>
              )}
          </Row>
        );
      })}
      <Row label="funding (직전)"
        hint={funding?.next_funding_time_ms
          ? `다음 ${clockKst(funding.next_funding_time_ms)}` : undefined}>
        {funding?.last_rate == null
          ? <Absent reason={funding?.unavailable_reason} />
          : <>{num(funding.last_rate, 8)}
            <span className="ml-1 font-normal text-muted">{pct(funding.last_rate_pct, 6)}</span>
          </>}
      </Row>
      <Row label="mark / index">
        {basis?.mark_price == null ? <Absent reason={basis?.unavailable_reason} /> : (
          <span className="text-[10px]">
            {price(basis.mark_price)} / {price(basis.index_price)}
          </span>
        )}
      </Row>
      <Row label="basis / premium">
        {basis?.basis_usdt == null ? <Absent reason={basis?.unavailable_reason} /> : (
          <>
            {num(basis.basis_usdt, 2)} USDT
            <span className="ml-1 font-normal text-muted">{pct(basis.premium_pct, 6)}</span>
          </>
        )}
      </Row>
      <Note>
        OI 변화는 거래소의 확정 {change?.period ?? "5m"} 버킷 차이입니다(이 프로세스의 폴링 차이가
        아님). {basis?.formula}
      </Note>
      <Note>{layer.note}</Note>
    </dl>
  );
}

// --------------------------------------------------------------------------- AUXILIARY

export function AuxiliarySection({ layer }: { layer: AuxiliaryLayer }) {
  if (!layer.rendered || !layer.c1 || !layer.directional) {
    return (
      <p className="text-[11px] text-muted" data-testid="mc-auxiliary-absent">
        {MISSING} 이 심볼에는 참고 연구 결과가 없습니다.
      </p>
    );
  }
  const c1 = layer.c1;
  const directional = layer.directional;
  const engine = (c1.engine_state ?? {}) as Record<string, unknown>;
  const signal = c1.latest_signal as Record<string, unknown> | null;
  return (
    <div data-testid="mc-auxiliary">
      <div className="rounded border border-line bg-surface-alt px-2 py-1.5" data-testid="mc-c1">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="text-[10px] font-semibold text-foreground-secondary">
            C1 / C1x <span className="font-normal text-muted">auxiliary</span>
          </span>
          <StateChip state={c1.state} />
        </div>
        <dl>
          <Row label="엔진 상태">
            <span className="text-[10px]">
              {engine.armed === true ? "armed" : engine.armed === false ? "disarmed" : MISSING} ·
              bars {String(engine.bars ?? MISSING)} · signals {String(engine.signals ?? MISSING)}
            </span>
          </Row>
          <Row label="최근 판단">
            {c1.last_decision_age_ms == null
              ? <Absent reason="C1_STATE_HAS_NO_LAST_DECISION" />
              : <span className="text-[10px]">{duration(c1.last_decision_age_ms)} 전</span>}
          </Row>
          <Row label="최근 시그널">
            {signal
              ? <span className="text-[10px]">{String(signal.signal_id ?? MISSING)} ·{" "}
                {String(signal.state ?? MISSING)}</span>
              : <Absent reason="NO_SIGNAL_RECORDED" />}
          </Row>
          <Row label="기록 수 (signal / c1x)">
            <span className="text-[10px]">{c1.signal_records} / {c1.c1x_records}</span>
          </Row>
        </dl>
        <Note>저장소를 바이트로만 읽고 C1 계약을 평가하지 않습니다. 보조 참고용입니다.</Note>
      </div>

      <div className="mt-2 rounded border border-line bg-surface-alt px-2 py-1.5"
        data-testid="mc-directional">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="text-[10px] font-semibold text-foreground-secondary">
            Directional Probability{" "}
            <span className="font-normal text-warning">weak_auxiliary</span>
          </span>
          <StateChip state={directional.state} />
        </div>
        <dl>
          <Row label="verdict">
            <span className="text-[10px]">{directional.verdict ?? MISSING}</span>
          </Row>
          <Row label="usable / weak horizons">
            <span className="text-[10px]">
              {(directional.usable_horizons ?? []).length === 0
                ? "없음" : (directional.usable_horizons ?? []).join(", ")}
              {" / "}
              {(directional.weak_horizons ?? []).join(", ") || "없음"}
            </span>
          </Row>
          <Row label="현재 시점 확률">계산하지 않음</Row>
        </dl>
        {directional.bounds.map(bound => (
          <Note key={bound.study}>{bound.study}: {bound.finding}</Note>
        ))}
        <Note>{directional.note}</Note>
      </div>
      <Note>{layer.note}</Note>
    </div>
  );
}

// --------------------------------------------------------------------------- the panel

const LAYER_SUMMARY: Record<LayerName, (payload: MarketContextPayload) => ReactNode> = {
  PRICE: payload => {
    const row = payload.layers.PRICE.row;
    return row ? `종가 ${price(row.close)}` : MISSING;
  },
  LIQUIDITY: payload => {
    const sides = payload.layers.LIQUIDITY.sides;
    return sides
      ? `${WALL_STATE_LABELS[sides.ASK.wall_state]} / ${WALL_STATE_LABELS[sides.BID.wall_state]}`
      : MISSING;
  },
  FLOW: payload => {
    const window = payload.layers.FLOW.windows?.["60s"];
    return window?.imbalance_usdt != null ? `60s 불균형 ${num(window.imbalance_usdt, 3)}` : MISSING;
  },
  DERIVATIVES: payload => {
    const funding = payload.layers.DERIVATIVES.funding;
    return funding?.last_rate != null ? `funding ${num(funding.last_rate, 6)}` : MISSING;
  },
  AUXILIARY: payload => (payload.layers.AUXILIARY.rendered ? "참고 2건" : "없음"),
};

/** Tailwind's `xl` breakpoint, which is where the terminal's own layout turns two-column.
 *
 *  Below it the panel sits between the chart and the order ticket in a single column, and that is
 *  the only place a context strip can actually get in the way: measured at 390px with every layer
 *  open, the panel is 3,178px tall and pushes the ticket 1,555px down the page. Compact is
 *  therefore the phone default, not an option a caller has to remember to pass.
 */
export function useIsNarrow() {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(max-width: 1279px)");
    const update = () => setNarrow(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return narrow;
}

/** The whole panel: a header carrying all five states, then the five layers in priority order.
 *
 *  The compact shape - every layer collapsed but the first - is the default on a narrow screen and
 *  can be forced by a caller that is putting the panel in a narrow column on a wide screen. The
 *  wide shape opens PRICE, LIQUIDITY and FLOW, which is what fits beside a 520px chart.
 */
/** The journal's own state, as a word beside the panel's footer.

 *  The tape is the one thing this process writes, and it is written by a single-writer lock that
 *  fails closed. So there are states - a second preview refused the lock, the lock file pulled
 *  out from under the holder - in which the screen is complete and correct while nothing is being
 *  recorded. A read-only panel that went blank over a write failure would be the wrong trade, and
 *  one that said nothing would quietly lose the material it exists to collect, so the failure is
 *  shown here and nowhere else. */
const JOURNAL_STATUS_LABELS: Record<string, string> = {
  OFF: "꺼짐", WAITING: "대기", WRITING: "기록 중", STALE: "지연",
  ERROR: "오류", REFUSED: "다른 writer 보유", AUTHORITY_LOST: "권한 상실",
};

export function JournalStatus({ journal }: { journal: MarketContextPayload["journal"] }) {
  const status = journal.status ?? (journal.enabled ? "WRITING" : "OFF");
  const healthy = status === "WRITING" || status === "WAITING" || status === "OFF";
  return (
    <span data-testid="mc-journal-status" data-status={status}>
      <span className="text-muted">forward journal </span>
      <span className={healthy ? "text-muted" : "font-semibold text-warning"}>
        {JOURNAL_STATUS_LABELS[status] ?? status}
      </span>
      {journal.enabled ? (
        <span className="text-muted"> · {journal.records_written}건</span>
      ) : null}
      {journal.writer && !journal.writer.writable && journal.enabled ? (
        <span className="text-muted"> · 기록만 중단, 화면은 계속</span>
      ) : null}
    </span>
  );
}

/** The three layers whose names go in the collapsed header.
 *
 *  Not all five, because the header is a line on a phone and because these are the three the
 *  contract orders first. The chips below it still carry every layer's state, so collapsing hides
 *  figures and never hides whether a layer is live. */
const HEADLINE_LAYERS: LayerName[] = ["PRICE", "LIQUIDITY", "FLOW"];

export function MarketContextPanel({ symbol, minNotionalUsdt, compact, collapsible }: {
  symbol: string;
  minNotionalUsdt?: string;
  /** Omit to let the viewport decide. `true` forces the collapsed shape on any width. */
  compact?: boolean;
  /** Offer a single open/shut control for the whole panel, shut by default on a narrow screen.
   *
   *  This is what the terminal integration uses. Per-layer collapse (`compact`) still leaves a
   *  header, a state strip, a rollup note, an open PRICE section and a footer on the page, which
   *  measured 470px at 390px wide - under the order ticket that is 470px of scrolling before the
   *  next thing. Shut, the whole panel is one header. */
  collapsible?: boolean;
}) {
  const query = useMemo(() => ({ symbol, minNotionalUsdt }), [symbol, minNotionalUsdt]);
  const { payload, error, polls } = useMarketContext(query);
  const narrow = useIsNarrow();
  const collapsed = compact ?? narrow;
  /** `null` until the operator decides, which is what lets the default follow the viewport
   *  without overriding a choice already made. */
  const [open, setOpen] = useState<boolean | null>(null);
  const shut = collapsible && !(open ?? !narrow);

  if (!payload) {
    return (
      <div className="panel p-3 text-[11px] text-muted" data-testid="market-context-panel"
        data-loading="true">
        {error ? `Market Context 응답 없음 · ${error}` : "Market Context 불러오는 중"}
      </div>
    );
  }
  const states = payload.layer_states;
  /** Which sections start open: the first openable layers in contract order.
   *
   *  "Openable" means the layer has something to open - a layer the backend reports
   *  `UNAVAILABLE` has a reason and no body. The rule is written over availability rather than
   *  over position because of what an ETH screen looks like otherwise: PRICE, LIQUIDITY, FLOW and
   *  AUXILIARY are all BTC-only there, so opening "the first three" opened three layers that each
   *  say only "not available for this symbol" and left DERIVATIVES - the one layer that *is* this
   *  symbol's own - shut. The panel then had nothing on it. Found by rendering ETH, not by
   *  reading this code.
   *
   *  Three on a wide screen, one on a narrow one: three is what fits beside a 520px chart.
   */
  const openable = payload.layer_order.filter(name => states[name] !== "UNAVAILABLE");
  const startsOpen = new Set(openable.slice(0, collapsed ? 1 : 3));
  const stateStrip = (
    <div className="mt-1.5 flex flex-wrap gap-1" data-testid="mc-state-strip">
      {payload.layer_order.map(name => (
        <span key={name} className="inline-flex items-center gap-1">
          <span className="text-[10px] text-muted">{name}</span>
          <StateChip state={states[name]} />
        </span>
      ))}
    </div>
  );
  const toggle = collapsible ? (
    <button type="button" className="btn-compact shrink-0"
      data-testid="mc-panel-toggle" aria-expanded={!shut}
      aria-controls="mc-panel-body"
      onClick={() => setOpen(current => !(current ?? !narrow))}>
      {shut ? "펼치기" : "접기"}
    </button>
  ) : null;

  if (shut) {
    return (
      <div className="panel min-w-0 overflow-hidden" data-testid="market-context-panel"
        data-symbol={payload.symbol} data-collapsed="true">
        <header className="px-3 py-2">
          <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
            <span className="text-[11px] font-bold tracking-wide text-foreground">
              MARKET CONTEXT
              <span className="ml-1 font-normal text-muted">{payload.symbol} · 읽기 전용</span>
            </span>
            {toggle}
          </div>
          <p className="mt-1 text-[10px] text-muted" data-testid="mc-collapsed-summary">
            {HEADLINE_LAYERS.join(" · ")}
          </p>
          {stateStrip}
          {error ? (
            <p className="mt-1 text-[10px] text-danger" data-testid="mc-poll-error">
              폴링 실패 · {error} (화면 값은 마지막 응답)
            </p>
          ) : null}
        </header>
      </div>
    );
  }
  return (
    <div className="panel min-w-0 overflow-hidden" data-testid="market-context-panel"
      data-symbol={payload.symbol} data-collapsed="false">
      <header className="border-b border-line px-3 py-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
          <span className="text-[11px] font-bold tracking-wide text-foreground">
            MARKET CONTEXT
            <span className="ml-1 font-normal text-muted">{payload.symbol} · 읽기 전용</span>
          </span>
          <span className="flex items-center gap-2 text-[10px] text-muted">
            {clockKst(payload.server_time_ms)} · {payload.panel_version}
            {toggle}
          </span>
        </div>
        {stateStrip}
        <p className="mt-1.5 text-[10px] leading-relaxed text-muted" data-testid="mc-no-rollup">
          레이어를 하나의 상태로 합치지 않습니다. 데이터가 모자라면 UNKNOWN이며 NEUTRAL 칸은
          없습니다.
        </p>
        {error ? (
          <p className="mt-1 text-[10px] text-danger" data-testid="mc-poll-error">
            폴링 실패 · {error} (화면 값은 마지막 응답)
          </p>
        ) : null}
      </header>
      <div id="mc-panel-body">

      <LayerSection name="PRICE" state={states.PRICE} reasons={payload.layers.PRICE.reasons}
        defaultOpen={startsOpen.has("PRICE")} right={LAYER_SUMMARY.PRICE(payload)}>
        <PriceSection layer={payload.layers.PRICE} />
      </LayerSection>
      <LayerSection name="LIQUIDITY" state={states.LIQUIDITY}
        reasons={payload.layers.LIQUIDITY.reasons} defaultOpen={startsOpen.has("LIQUIDITY")}
        right={LAYER_SUMMARY.LIQUIDITY(payload)}>
        <LiquiditySection layer={payload.layers.LIQUIDITY} />
      </LayerSection>
      <LayerSection name="FLOW" state={states.FLOW} reasons={payload.layers.FLOW.reasons}
        defaultOpen={startsOpen.has("FLOW")} right={LAYER_SUMMARY.FLOW(payload)}>
        <FlowSection layer={payload.layers.FLOW} />
      </LayerSection>
      <LayerSection name="DERIVATIVES" state={states.DERIVATIVES}
        reasons={payload.layers.DERIVATIVES.reasons} defaultOpen={startsOpen.has("DERIVATIVES")}
        right={LAYER_SUMMARY.DERIVATIVES(payload)}>
        <DerivativesSection layer={payload.layers.DERIVATIVES} />
      </LayerSection>
      <LayerSection name="AUXILIARY" state={states.AUXILIARY}
        reasons={payload.layers.AUXILIARY.reasons} defaultOpen={startsOpen.has("AUXILIARY")}
        right={LAYER_SUMMARY.AUXILIARY(payload)}>
        <AuxiliarySection layer={payload.layers.AUXILIARY} />
      </LayerSection>

      </div>

      <footer className="border-t border-line px-3 py-2 text-[10px] leading-relaxed text-muted"
        data-testid="mc-footer">
        <p>
          {payload.contract.contract_version} ·{" "}
          {payload.contract.agrees ? "계약 해시 일치" : "계약 해시 불일치"} ·{" "}
          polls {polls} · {payload.cost.elapsed_ms}ms
        </p>
        <p className="mt-0.5">
          <JournalStatus journal={payload.journal} />
          {payload.journal.enabled ? " · 주문과 연결되지 않음" : ""}
        </p>
        <p className="mt-0.5">{payload.notes.scope}</p>
      </footer>
    </div>
  );
}
