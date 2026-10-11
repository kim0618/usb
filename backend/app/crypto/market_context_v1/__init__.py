"""Manual Market Context V1: one read-only context panel beside the manual order ticket.

Frozen display contract: ``docs/crypto/market_context_v1/CONTRACT_MC_V1.md`` (``mc-display.v1``),
hashed beside it and frozen before this package was written.

The panel composes five layers that were each validated somewhere else, and composes them by
*reading* them: the R1 level book, the Liquidity Map viewer over the Market Structure V0 journal,
that viewer's flow windows, Binance's public derivatives endpoints, and two closed BTC studies.
Nothing here re-implements a rule, moves a threshold, or turns two layers into a third reading.

Why composition and not a model: Market Context R0 measured the joint model and it did not survive
(``A_NO_CONTEXT_EDGE``). So this step is deliberately the other thing - put the measured layers in
front of the person who is already deciding by hand, keep every figure attributable to the contract
that produced it, and keep the raw material so the questions R0 could not ask become askable.

Isolation, which is the property the tests check rather than the comment claim: this package is
served by its own ASGI app on its own port, is imported by nothing that deploys, imports nothing
from ``paper``, ``live``, ``terminal`` or the C1 runtime, and writes exactly one thing - the
append-only forward context journal.
"""
from __future__ import annotations

#: Version of the panel itself. Independent of every contract it reads.
PREVIEW_VERSION = "mc-v1.0"

__all__ = ["PREVIEW_VERSION"]
