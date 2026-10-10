"use client";

import { useEffect, useRef, useState } from "react";
import { CryptoTerminal } from "@/components/crypto-terminal";
import { MarketContextPanel } from "@/components/market-context-panel";

/** Manual Market Context V1 inside the real terminal, on a route that does not deploy.
 *
 *  The step before this one answered the placement question against a frame of labelled boxes
 *  carrying the terminal's dimensions. Boxes were honest about what they were, and they were also
 *  the limit: a box cannot push the order ticket, cannot report a canvas that rounds its width up,
 *  and cannot show what happens when the symbol tabs change underneath the panel. So this screen
 *  renders `CryptoTerminal` - the same component `/crypto-paper` renders, from the same file -
 *  and hands it the context panel through the one slot that component gained.
 *
 *  What makes it a preview rather than a second production route is not the URL. It is three
 *  things, in increasing order of how much they would survive someone being careless:
 *
 *  1. It is reachable from nothing. No navigation entry, no link, no import from a deployed page.
 *  2. It is pointed at a terminal backend started without exchange credentials, in a scratch
 *     runtime root. With no key configured the account switch is never offered, so the BINANCE
 *     LIVE tree cannot be reached from here at all.
 *  3. Every non-GET request made by anything inside this page is refused before it leaves the
 *     browser, and counted. That is the sentinel below, and it is the one of the three that holds
 *     even if somebody wires this route to a credentialed backend by mistake.
 */

/** A request this page refused to send. */
interface Refusal { method: string; url: string; atMs: number }

interface Sentinel { gets: number; refusals: Refusal[] }

/** Refuse and count every non-GET request for as long as this page is mounted.
 *
 *  Why a sentinel rather than a promise in a report: the terminal rendered here is the real one,
 *  wired to the real order callbacks - that is the point, since callbacks that were cut out would
 *  no longer be the thing under test. So "no order was sent during QA" has to be a property of
 *  the page and not of how carefully the person clicking behaved. A click on an order button here
 *  produces the ticket's own error state and a line in the tally, which is also the most direct
 *  possible evidence that the button is still wired to what it was wired to.
 *
 *  `HEAD` is allowed with `GET`: it is the same read, with the body dropped.
 */
function installSentinel(onChange: (sentinel: Sentinel) => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const original = window.fetch;
  const tally: Sentinel = { gets: 0, refusals: [] };
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method
      ?? (typeof Request !== "undefined" && input instanceof Request ? input.method : "GET")
    ).toUpperCase();
    const url = typeof input === "string" ? input
      : input instanceof URL ? input.toString()
        : (input as Request).url;
    if (method !== "GET" && method !== "HEAD") {
      tally.refusals = [...tally.refusals,
        { method, url, atMs: Date.now() }].slice(-20);
      onChange({ ...tally });
      throw new Error(`PREVIEW_IS_READ_ONLY: ${method} ${url} 는 이 화면에서 차단됩니다.`);
    }
    tally.gets += 1;
    onChange({ ...tally });
    return original(input, init);
  };
  return () => { window.fetch = original; };
}

function Banner({ sentinel }: { sentinel: Sentinel }) {
  const blocked = sentinel.refusals.length;
  return (
    <section className="mb-3 rounded-xl border border-line bg-surface-alt px-3 py-2"
      data-testid="terminal-preview-banner">
      <p className="text-[11px] font-bold tracking-wide text-foreground">
        Manual Market Context V1 - 터미널 통합 격리 Preview
      </p>
      <p className="mt-1 text-[11px] leading-relaxed text-muted">
        아래는 운영과 같은 터미널 컴포넌트입니다. 이 라우트는 어디에서도 링크되지 않고, 자격증명이
        없는 백엔드를 봅니다. Market Context는 읽기 전용이며 방향·score를 만들지 않습니다.
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px]"
        data-testid="mutation-sentinel" data-blocked={blocked} data-gets={sentinel.gets}>
        <span className="text-muted">GET {sentinel.gets}</span>
        <span className={blocked > 0 ? "font-semibold text-danger" : "text-muted"}>
          차단된 non-GET {blocked}
        </span>
        {blocked > 0 ? (
          <span className="text-danger" data-testid="mutation-sentinel-last">
            최근 {sentinel.refusals[blocked - 1].method}{" "}
            {sentinel.refusals[blocked - 1].url}
          </span>
        ) : (
          <span className="text-muted">주문·CLOSE·레버리지·arm·AUTO 경로는 전송 전에 거부됩니다.</span>
        )}
      </p>
    </section>
  );
}

export function CryptoTerminalPreview() {
  const [sentinel, setSentinel] = useState<Sentinel>({ gets: 0, refusals: [] });
  /** The terminal is not mounted until the sentinel is in place.
   *
   *  Not a detail: child effects run before parent effects, so a terminal mounted alongside this
   *  one would have fired its first polls - and could have fired a write - through the unpatched
   *  `fetch`. Gating the mount on `armed` makes the ordering structural instead of hopeful.
   */
  const [armed, setArmed] = useState(false);
  const restore = useRef<(() => void) | null>(null);

  useEffect(() => {
    restore.current = installSentinel(setSentinel);
    setArmed(true);
    return () => { restore.current?.(); restore.current = null; setArmed(false); };
  }, []);

  return (
    <main className="min-h-screen bg-background px-3 py-4 sm:px-4">
      <div className="mx-auto max-w-[1600px]">
        <Banner sentinel={sentinel} />
        {armed ? (
          <CryptoTerminal marketContext={symbol => (
            /* `key` on the symbol, so a tab change unmounts the panel rather than letting a
               BTC reading sit under an ETH heading while the first poll is in flight. The hook
               clears its payload on a key change too; both, because this is the one failure
               class the multi-symbol terminal has already paid for once and it has no visual
               signature when it happens. */
            <MarketContextPanel key={symbol} symbol={symbol} collapsible />
          )} />
        ) : (
          <p className="panel p-3 text-[11px] text-muted" data-testid="terminal-preview-arming">
            읽기 전용 감시기를 설치하는 중입니다. 설치 전에는 터미널을 띄우지 않습니다.
          </p>
        )}
      </div>
    </main>
  );
}
