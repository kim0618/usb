"""BTC Manual Trading Market Context UI V1: a few figures beside the BTC order ticket, read only.

The manual terminal shows four short lines under the BTC chart - STRUCTURE, LIQUIDITY, FLOW and
DERIVATIVES - and this package is the GET-only API that feeds them. It computes nothing new. Every
figure is read from something that already runs or already exists:

* LIQUIDITY and FLOW from the production Context Collector's current-state file, through the
  collector API's own `judged` read (so its staleness floor applies unchanged);
* STRUCTURE from the R1 level book on the anchored kline series (Market Context V1 `structure`);
* DERIVATIVES from Binance's public open interest, premium index and funding endpoints (Market
  Context V1 `derivatives`).

What the screen does not get is decided here, in `view`, rather than left to the frontend: the
payload carries no research label, no detector output, no sample count, no confidence, no contract,
version or hash, and no internal state word. A section is either live, or its values are absent.

Isolation: its own process and port, every route a GET, no import of any order, account, paper,
live, terminal, C1 or Q1/Q2/Q3 module, and no file written anywhere.
"""
from __future__ import annotations

__all__: list[str] = []
