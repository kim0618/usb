import { CryptoTerminalPreview } from "@/components/crypto-terminal-preview";

/** Isolated terminal-integration preview for Manual Market Context V1.
 *
 *  Not in the navigation, not linked from any page, and imported by nothing that deploys. It
 *  renders the same `CryptoTerminal` the production route renders, which is the whole point: the
 *  question this step has to answer is whether the context panel fits beside the real chart and
 *  the real order ticket, and a copy of the terminal could not answer it.
 *
 *  `/crypto-paper` is unaffected by this file's existence - it passes no context slot and renders
 *  what it rendered before.
 */
export const metadata = { title: "Market Context V1 terminal integration preview" };

export default function CryptoTerminalPreviewPage() {
  return <CryptoTerminalPreview />;
}
