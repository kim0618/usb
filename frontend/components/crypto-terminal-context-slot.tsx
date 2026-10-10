"use client";

import dynamic from "next/dynamic";
import type { ReactNode } from "react";

/** Manual Market Context V1 as the deployed terminal's P2 slot.
 *
 *  Three properties this module exists to hold, none of which belong in the route file and none
 *  of which the preview needed:
 *
 *  1. **It cannot be in the route's critical path.** `market-context-panel` and its contract
 *     module are ~1,700 lines between them. Imported normally they would land in
 *     `app/crypto-paper/page.js`, which is the 149 KB chunk the price and the order ticket are
 *     already waiting on - so adding the panel would have made P0 slower, which is the one
 *     outcome this step is not allowed to produce. `ssr: false` puts them in their own chunk,
 *     requested after the terminal has mounted.
 *  2. **It cannot be reached by a build that has no backend.** With
 *     `NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL` unset the panel's default base is
 *     `http://127.0.0.1:8012`, which on an operator's machine is their own laptop: a 1 Hz
 *     request at nothing, forever, with a panel full of dashes to show for it. Unconfigured,
 *     this module hands back no slot at all and the screen is byte-for-byte the one that is
 *     deployed today.
 *  3. **It cannot carry the preview's sentinel.** The isolated preview patches `window.fetch` to
 *     refuse every non-GET, which is right for a QA route and would block the operator's own
 *     LONG, SHORT and CLOSE here. Nothing in this file touches `fetch`, and the panel it mounts
 *     only ever issues GETs of its own.
 */

/** Whether a backend was pointed at, as opposed to the panel module's development default of
 *  `http://127.0.0.1:8012` - which in a production build is the *operator's own laptop*, so a
 *  panel shipped without this check would poll nothing once a second and show dashes.
 *
 *  Read straight from `process.env` rather than imported from `@/lib/market-context`, which
 *  exports the same flag. The import is what matters: that module is the panel's 700-line
 *  contract, and naming it here put it in `app/crypto-paper/page.js` - the chunk the price and
 *  the order ticket are already waiting on. `NEXT_PUBLIC_*` is inlined at build time, so this
 *  constant costs the critical path nothing.
 */
const CONFIGURED = (process.env.NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL ?? "").trim().length > 0;

/** The panel, in its own chunk.
 *
 *  No `loading` placeholder on purpose. A P2 panel that reserves space before it has anything to
 *  say would push the layout around while the operator is reading the figures above it; the
 *  panel's own header appears when the chunk is in, and that is early enough for the last thing
 *  on the screen.
 */
const MarketContextPanel = dynamic(
  () => import("@/components/market-context-panel").then(m => m.MarketContextPanel),
  { ssr: false },
);

/** The slot `CryptoTerminal` takes, or `undefined` when no backend is configured.
 *
 *  Returned as a function of the symbol rather than as an element because that is the contract
 *  the terminal's slot already has: the terminal renders it once per layout tree with whichever
 *  symbol is selected, so the panel cannot end up showing one instrument's reading under
 *  another's heading.
 */
export function marketContextSlot(): ((symbol: string) => ReactNode) | undefined {
  if (!CONFIGURED) return undefined;
  return renderMarketContext;
}

/** `key` on the symbol so a tab change unmounts the panel rather than leaving a BTC reading under
 *  an ETH heading while the first poll of the new symbol is still in flight. The hook clears its
 *  payload on a key change as well; both, because this is the failure the multi-symbol terminal
 *  has already paid for once and it has no visual signature when it happens. */
function renderMarketContext(symbol: string): ReactNode {
  return <MarketContextPanel key={symbol} symbol={symbol} collapsible />;
}
