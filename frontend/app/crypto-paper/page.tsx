"use client";

import { CryptoTerminal } from "@/components/crypto-terminal";
import { marketContextSlot } from "@/components/crypto-terminal-context-slot";

/** The deployed manual futures terminal.
 *
 *  The screen itself is `CryptoTerminal`. It was lifted out of this file so that the Market
 *  Context integration preview could render the real
 *  terminal rather than a frame that imitates one - the earlier isolated preview used labelled
 *  boxes with the right dimensions, and boxes cannot answer whether the panel fits beside the
 *  actual chart, the actual ticket and the actual symbol tabs.
 *
 *  The context slot is Manual Market Context V1, read-only and P2: it loads in its own chunk
 *  after the terminal has mounted, and `marketContextSlot` returns nothing at all unless a
 *  backend was configured at build time - so a build without one renders exactly what this
 *  route rendered before the slot existed.
 */
export default function CryptoPaperPage() {
  return <CryptoTerminal marketContext={marketContextSlot()} />;
}
