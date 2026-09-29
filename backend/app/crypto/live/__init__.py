"""Binance USDⓈ-M Futures LIVE account access for the manual terminal.

This package is the *only* place in the repository that holds Binance account credentials or
speaks to a Binance private endpoint. Three properties are structural rather than documented:

1. **Nothing in here touches the paper engine.** The package imports nothing from `..paper` at
   all, so a LIVE bug cannot move a paper balance. Checked by a test, not by reading.
2. **No strategy or research module may import it.** Enforced by a test
   (`tests/crypto/test_binance_live_safety.py`), not by convention.
3. **Sending an order needs two independent gates.** `BINANCE_LIVE_TRADING_ENABLED` must be true
   *and* the client must have been constructed with `trading_enabled=True`. Either one false
   refuses the call before a request is built.

The endpoint registry in `endpoints.py` carries the official documentation URL for every path
this package is allowed to call, and anything outside it is refused by construction.
"""
from __future__ import annotations

BINANCE_LIVE_VERSION = "binance-live-manual-v1"

__all__ = ["BINANCE_LIVE_VERSION"]
