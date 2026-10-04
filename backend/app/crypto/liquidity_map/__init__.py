"""Liquidity Map V1 preview: a read-only *viewer* of the Market Structure V0 journal.

This package reads. It opens no socket, holds no credential, writes nothing into the journal and
imports nothing from `app.crypto.paper`, `app.crypto.live` or `app.crypto.terminal`. The collector
stays the only writer and the only thing that talks to Binance; if this viewer dies the collector
does not notice, and if the collector dies this viewer says so instead of showing the last values
it happened to have.

Why a separate package rather than a module inside `market_structure_v0`: that package's contract
is frozen and `tests/crypto/test_ms_v0_isolation.py` asserts its exact file list, so a viewer
added there would either break that assertion or require editing a frozen boundary. Importing the
contract's constants from here is the right direction of dependency - the viewer depends on the
contract, never the other way round.

What this step deliberately does not do: no LONG/SHORT verdict, no score, no composite number of
any kind, no order path. It shows the collector's own figures and its own coverage vocabulary, and
where a figure is not available it says which clause of the contract makes it unavailable.
"""
from __future__ import annotations

#: Display/API version of the preview. Independent of `btc-ms.v0.1`, which is the data contract.
PREVIEW_VERSION = "liquidity-map.v1-1-preview.1"

__all__ = ["PREVIEW_VERSION"]
