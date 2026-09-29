"""BTC-VOL-P0: can the large-move signal be turned into money without knowing direction?

Eight studies failed to find direction. BTC-P2 closed that line: 0 of 64 cells reached
STRONG_DIRECTION, and the large-move gate made direction *worse*, not better. What survived is
narrow but real: P1 forecasts whether a large move happens, with Brier skill +0.180 and AUC 0.727
on its strongest target.

This package is an audit, not a strategy. It reads what already exists to answer one question:
does a directionless structure exist that could pay for itself. No model is trained, no rule is
written, no order is placed, and no PnL is simulated.
"""
