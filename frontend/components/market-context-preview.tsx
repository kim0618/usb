"use client";

import { useEffect, useState } from "react";
import { MarketContextPanel, PriceSection, StateChip } from "@/components/market-context-panel";
import {
  CASE_LABELS, MISSING, clockKst, fetchStructureReplay, fetchStructureStates,
} from "@/lib/market-context";
import type {
  StructureCase, StructureReplay, StructureStates,
} from "@/lib/market-context";

/** Isolated preview for Manual Market Context V1.
 *
 *  The point of this screen is the *placement* question, which a panel rendered on its own cannot
 *  answer: does the context panel fit where the manual terminal would put it without pushing the
 *  chart or the order ticket off the screen. So the frame below reproduces the terminal's own
 *  layout with the terminal's own classes - `max-w-[1600px]`, the
 *  `xl:grid-cols-[minmax(0,1fr)_360px]` split, and the chart's `h-[260px] sm:h-[300px]
 *  xl:h-[520px]` - with labelled boxes standing in for the chart and the ticket.
 *
 *  Stand-ins rather than the real components, because this step may not modify the deployed
 *  terminal and importing its trees here would pull the order path into this page's bundle. The
 *  boxes carry the real dimensions, which is what the 390 / 1440 / 1600 checks are about.
 *
 *  `minmax(0,1fr)` and `min-w-0` are not decoration. A grid item bottoms out at its min-content
 *  width, and a canvas that reports a rounded-up pixel is enough to push the whole page one pixel
 *  wide; that is how an earlier preview overflowed at 390px.
 */

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
const NOTIONAL_PRESETS = ["250000", "500000", "1000000"] as const;

/** The structure layer as it was published for one past minute, for the section 10 checks.
 *
 *  Near resistance, near support, a fresh breakout and a range do not happen on request, so this
 *  strip navigates to minutes the panel has already published instead of waiting for the market.
 *  It is labelled a replay in three places - the heading, the chip and the payload's own `mode` -
 *  because a historical structure panel photographed next to a live one is exactly the confusion
 *  this whole screen is built to avoid.
 */
function StructureReplayStrip() {
  const [states, setStates] = useState<StructureStates | null>(null);
  const [replay, setReplay] = useState<StructureReplay | null>(null);
  const [picked, setPicked] = useState<StructureCase | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchStructureStates(controller.signal)
      .then(setStates)
      .catch(caught => { if (caught?.name !== "AbortError") setError(String(caught)); });
    return () => controller.abort();
  }, []);

  const show = async (name: StructureCase, infoTs: number) => {
    setPicked(name);
    try {
      setReplay(await fetchStructureReplay(infoTs));
      setError(null);
    } catch (caught) {
      setError(String(caught));
    }
  };

  const cases = states?.cases;
  return (
    <section className="panel mt-4 min-w-0 overflow-hidden" data-testid="mc-replay-strip">
      <header className="border-b border-line px-3 py-2">
        <p className="text-[11px] font-bold tracking-wide text-foreground">
          PRICE 재생 <span className="font-normal text-muted">
            과거 확정 1분봉의 구조 레이어만 - 라이브 아님</span>
        </p>
        <p className="mt-1 text-[10px] text-muted">
          호가·체결·파생은 재생하지 않습니다. 이 뷰어에는 그 해상도의 과거가 없습니다.
        </p>
      </header>
      <div className="flex flex-wrap gap-1 px-3 py-2">
        {cases ? (Object.keys(CASE_LABELS) as StructureCase[]).map(name => {
          const entry = cases[name];
          const sample = entry?.first;
          return (
            <button key={name} type="button" className="btn-compact"
              data-testid={`mc-case-${name}`}
              disabled={!sample}
              aria-pressed={picked === name}
              onClick={() => sample && show(name, sample.info_ts)}>
              {CASE_LABELS[name]}
              <span className="ml-1 text-muted">{entry?.minutes ?? 0}분</span>
            </button>
          );
        }) : <span className="text-[11px] text-muted">{MISSING} 상태 색인을 읽는 중</span>}
      </div>
      {error ? <p className="px-3 pb-2 text-[10px] text-danger">{error}</p> : null}
      {replay ? (
        <div className="border-t border-line px-3 py-2" data-testid="mc-replay-body"
          data-case={picked}>
          <p className="mb-2 flex flex-wrap items-center gap-2 text-[10px] text-muted">
            <StateChip state="UNKNOWN" label="REPLAY" />
            {picked ? CASE_LABELS[picked] : ""} · {clockKst(replay.replay.info_ts)} ·
            {" "}재생 레이어 {replay.replay.layers_replayed.join(", ")} · 미재생{" "}
            {replay.replay.layers_not_replayed.join(", ")}
          </p>
          <PriceSection layer={replay.layers.PRICE} />
        </div>
      ) : null}
    </section>
  );
}

function Placeholder({ label, hint, className }: { label: string; hint: string;
  className?: string }) {
  return (
    <div className={`flex flex-col items-center justify-center rounded-xl border border-dashed
                     border-line bg-surface-alt text-center ${className ?? ""}`}>
      <span className="text-[11px] font-semibold text-foreground-secondary">{label}</span>
      <span className="mt-1 px-3 text-[10px] text-muted">{hint}</span>
    </div>
  );
}

export function MarketContextPreview() {
  const [symbol, setSymbol] = useState<string>(SYMBOLS[0]);
  const [notional, setNotional] = useState<string>("500000");
  const [below, setBelow] = useState(true);

  return (
    <main className="min-h-screen bg-background px-3 py-4 sm:px-4">
      <div className="mx-auto max-w-[1600px]">
        <header className="mb-3">
          <h1 className="text-sm font-bold tracking-wide text-foreground">
            Manual Market Context V1 - 격리 Preview
          </h1>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">
            읽기 전용입니다. 주문·AUTO·C1 계약을 건드리지 않고, 방향 판정과 score를 만들지
            않습니다. 자체 포트의 자체 백엔드만 읽으며 운영 터미널은 이 화면을 import하지
            않습니다.
          </p>
        </header>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="flex gap-1" role="group" aria-label="심볼">
            {SYMBOLS.map(item => (
              <button key={item} type="button" onClick={() => setSymbol(item)}
                aria-pressed={symbol === item}
                data-testid={`mc-symbol-${item}`}
                className={`btn-compact ${symbol === item
                  ? "border-primary text-foreground" : ""}`}>
                {item}
              </button>
            ))}
          </div>
          <div className="flex gap-1" role="group" aria-label="벽 표시 필터">
            {NOTIONAL_PRESETS.map(item => (
              <button key={item} type="button" onClick={() => setNotional(item)}
                aria-pressed={notional === item}
                className={`btn-compact ${notional === item
                  ? "border-primary text-foreground" : ""}`}>
                {Number(item) / 1000}k
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setBelow(value => !value)}
            className="btn-compact" data-testid="mc-placement-toggle">
            배치: {below ? "차트 아래" : "차트 옆"}
          </button>
        </div>

        {/* One flat grid rather than a left stack plus a right column, so the panel can be
            ordered independently of the ticket. On a phone the terminal's order is price, chart,
            order controls, then everything that documents rather than drives - and a context
            strip is the second kind. Measured: with the panel between the chart and the ticket,
            the ticket starts 1,736px down at 390px even with four layers collapsed. Below the
            ticket it starts where it always did and the panel costs the phone nothing until it is
            scrolled to. At xl the panel returns to the left column under the chart. */}
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]
                        xl:items-start">
          <Placeholder label="MARKET HEADER" hint="운영 터미널의 가격·잔고 밴드 자리"
            className="order-1 h-16 min-w-0 xl:col-start-1 xl:row-start-1" />
          <Placeholder label="CHART" hint="h-[260px] sm:h-[300px] xl:h-[520px]"
            className="order-2 h-[260px] min-w-0 sm:h-[300px] xl:col-start-1 xl:row-start-2
                       xl:h-[520px]" />
          <div className="order-3 min-w-0 xl:col-start-2 xl:row-start-1 xl:row-span-4">
            <Placeholder label="ORDER TICKET" hint="360px 고정 열 - 이 패널이 가리면 안 되는 영역"
              className="h-[420px]" />
          </div>
          <div className={`min-w-0 ${below
            ? "order-4 xl:col-start-1 xl:row-start-3"
            : "order-4 xl:col-start-1 xl:row-start-2 xl:ml-auto xl:w-[360px]"}`}>
            <MarketContextPanel symbol={symbol} minNotionalUsdt={notional}
              compact={below ? undefined : true} />
          </div>
          <Placeholder label="POSITION" hint="운영 터미널의 포지션 카드 자리"
            className="order-5 h-24 min-w-0 xl:col-start-1 xl:row-start-4" />
        </div>

        <StructureReplayStrip />

        <p className="mt-4 text-[10px] leading-relaxed text-muted">
          CHART·ORDER TICKET·MARKET HEADER·POSITION은 운영 터미널의 실제 치수를 가진 자리
          표시입니다. 이 단계는 운영 화면을 수정하지 않으므로 실제 컴포넌트를 import하지 않습니다.
          390px에서는 패널이 주문 티켓 아래에 오고, xl에서는 차트 아래(또는 차트 옆)로 돌아옵니다.
        </p>
      </div>
    </main>
  );
}
