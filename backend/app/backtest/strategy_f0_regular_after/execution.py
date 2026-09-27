"""Entry, exit and path of one symbol-session from its after-close bars (m >= 16:00 ET).

* Entry: the bar whose start minute equals the declared entry minute; its OPEN. None -> NO_TRADE.
  A later bar is never used.
* Exit for target minute T: the bar with the largest start minute in [T - lookback, T - 1];
  its CLOSE. None -> UNRESOLVED_EXIT. No older bar, no bar at or after T.
* Path: high/low over bars from the entry minute through the exit bar, inclusive; nothing after.
"""

from dataclasses import dataclass

import numpy as np

NAN = float("nan")


@dataclass(frozen=True)
class Outcome:
    status: str          # NO_TRADE / UNRESOLVED_EXIT / RESOLVED
    entry: float = NAN
    exit: float = NAN
    exit_minute: int = -1
    exit_age_seconds: float = NAN
    gross: float = NAN
    mfe: float = NAN
    mae: float = NAN


def resolve(minute: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
            entry_minute: int, target: int, lookback: int) -> Outcome:
    entry_bar = np.flatnonzero(minute == entry_minute)
    if entry_bar.size == 0:
        return Outcome("NO_TRADE")
    entry = float(o[entry_bar[0]])
    window = np.flatnonzero((minute >= target - lookback) & (minute <= target - 1)
                            & (minute >= entry_minute))
    if window.size == 0:
        return Outcome("UNRESOLVED_EXIT", entry=entry)
    last = window[np.argmax(minute[window])]
    m_exit = int(minute[last])
    exit_price = float(c[last])
    path = (minute >= entry_minute) & (minute <= m_exit)
    return Outcome("RESOLVED", entry=entry, exit=exit_price, exit_minute=m_exit,
                   exit_age_seconds=float(target * 60 - (m_exit + 1) * 60),
                   gross=exit_price / entry - 1.0,
                   mfe=float(np.max(h[path])) / entry - 1.0,
                   mae=float(np.min(l[path])) / entry - 1.0)
