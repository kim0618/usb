"use client";

import dynamic from "next/dynamic";
import type { ReactNode } from "react";
import type { ContextView } from "@/lib/btc-context";

/** The deployed terminal's context slot: BTC Manual Trading Market Context UI V2.
 *
 *  One compact read-only line under the BTC chart (trade flow, open interest direction, funding),
 *  fed by the GET-only `btc_context_ui_v1` API. Resistance, support and walls are not here: they are
 *  price lines on the chart itself, drawn by the terminal from `contextBase()`.
 *  This replaces Manual Market Context V1 in this slot: that panel puts research states on the
 *  screen, which the order screen must not show. It still exists for its own preview route and is
 *  not imported from here.
 *
 *  Three properties this module holds:
 *
 *  1. **Not in the route's critical path.** The strip loads in its own chunk (`ssr: false`), after
 *     the price and the order ticket have mounted.
 *  2. **Nothing at all without a backend.** With `NEXT_PUBLIC_BTC_CONTEXT_BASE` unset this returns
 *     no slot, and the screen is exactly the one without it - no request, no empty box.
 *  3. **BTC only.** The slot is a function of the selected symbol and returns nothing for any tab
 *     other than BTCUSDT, so the HOT tab and any held-symbol tab never show it.
 *
 *  Nothing in this file or the strip touches `fetch` globally or issues anything but GET.
 */

/** Read straight from `process.env` so this file does not pull the strip's module into the
 *  route chunk. An unset `NEXT_PUBLIC_*` is not inlined by the build; it reads as undefined in the
 *  browser, which is what makes an unconfigured build inert. */
const CONFIGURED = (process.env.NEXT_PUBLIC_BTC_CONTEXT_BASE ?? "").trim().length > 0;

const BtcContextStrip = dynamic(
  () => import("@/components/btc-context-strip").then(m => m.BtcContextStrip),
  { ssr: false },
);

/** The context API base for the lines on the BTC chart, or `undefined` when none is configured. */
export function contextBase(): string | undefined {
  if (!CONFIGURED) return undefined;
  return (process.env.NEXT_PUBLIC_BTC_CONTEXT_BASE ?? "").trim().replace(/\/$/, "");
}

/** The slot `CryptoTerminal` takes, or `undefined` when no backend is configured. */
export function marketContextSlot(): ((symbol: string, view?: ContextView) => ReactNode) | undefined {
  if (!CONFIGURED) return undefined;
  return renderBtcContext;
}

/** `null` for every symbol but BTC, before the chunk is even requested. `view` is the account's
 *  position and mark, which switch the panel between measuring from the price and from the entry. */
export function renderBtcContext(symbol: string, view?: ContextView): ReactNode {
  if (symbol !== "BTCUSDT") return null;
  return <BtcContextStrip key={symbol} symbol={symbol} position={view?.position ?? null}
    mark={view?.mark ?? null} />;
}
