"""C1 Signal V1: the D5.2 `dislocation_event` as an operational signal and shadow ledger.

A strategy *observation* layer. It evaluates the frozen D5.2 C1 contract on live public data,
marks each event on the chart, and keeps its own 4 h shadow ledger so the contract accumulates a
forward record. It does not trade. Nothing in this package can place an order, size one, change
leverage or arm a session, on the paper account or the Binance account, and the research verdict
is why: C1-EVENT-240m-LONG is WEAK, D5.2's gate is CASE C, and no cell survived.
"""
from .contract import (
    CONTRACT_SHA256, DIRECTION, DIRECTION_CONTRACT, OFFICIAL_HORIZON_MIN, SCHEMA_VERSION,
    STRATEGY,
)

__all__ = ["CONTRACT_SHA256", "DIRECTION", "DIRECTION_CONTRACT", "OFFICIAL_HORIZON_MIN",
           "SCHEMA_VERSION", "STRATEGY"]
