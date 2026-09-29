"""Point-in-time alignment of ledger events to external market bars (for E1; unused in E0).

A bar stamped by its OPEN time t covers [t, t+bar). An event at time e may only see bars that
have fully CLOSED at or before e, i.e. open_time + bar <= e. Forward returns start at the first
bar whose open_time >= e. Both rules are enforced here so no later caller can peek into the bar
that contains the event.
"""
from __future__ import annotations

import numpy as np


def last_closed_bar_index(bar_open_ns: np.ndarray, bar_ns: int, event_ns: np.ndarray) -> np.ndarray:
    """Index of the last bar with open + bar_ns <= event, or -1. bar_open_ns must be sorted."""
    closes = bar_open_ns + bar_ns
    return np.searchsorted(closes, event_ns, side="right") - 1


def first_forward_bar_index(bar_open_ns: np.ndarray, event_ns: np.ndarray) -> np.ndarray:
    """Index of the first bar opening at or after the event (len(bars) if none)."""
    return np.searchsorted(bar_open_ns, event_ns, side="left")
