import { MarketContextPreview } from "@/components/market-context-preview";

/** Isolated preview route for Manual Market Context V1.
 *
 *  Not in the navigation and not imported by any other page, so it adds nothing to the bundle of
 *  the trading screens and cannot be reached by accident from them. It talks to its own backend on
 *  its own port; with that process stopped this page renders the panel's own "응답 없음" state and
 *  the rest of the dashboard is unaffected.
 */
export const metadata = { title: "Manual Market Context V1 preview" };

export default function MarketContextPreviewPage() {
  return <MarketContextPreview />;
}
