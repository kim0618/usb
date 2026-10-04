import { LiquidityMapPreview } from "@/components/liquidity-map-preview";

/** Isolated preview route for Liquidity Map V1.
 *
 *  Not in the navigation and not imported by any other page, so it adds nothing to the bundle of
 *  the trading screens and cannot be reached by accident from them. It talks to its own backend
 *  on its own port; with that process stopped this page renders NO_DATA and the rest of the
 *  dashboard is unaffected.
 */
export const metadata = { title: "Liquidity Map V1 preview" };

export default function LiquidityPreviewPage() {
  return <LiquidityMapPreview />;
}
