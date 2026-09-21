"""Strategy E trading boundary.

E-D1 intentionally exports signal identity only. Entry, exit, sizing, risk and PnL do not belong
to this package yet.
"""

from app.strategy_e.signal import (
    SignalContractError,
    SignalFrame,
    SignalResult,
    evaluate_h5_signal,
)

__all__ = ["SignalContractError", "SignalFrame", "SignalResult", "evaluate_h5_signal"]
