"""After-hours features of DAY T and premarket labels of DAY T+1, per symbol-day.

Two boundaries carry the point-in-time claim and both are slices, not runtime checks:

* ``source_block`` reads only bars of ET day T whose start is at or before 19:59. It never sees
  another day, so no bar of T+1 can reach a feature. The after-hours window starts at **16:01**:
  the 16:00 bar carries the closing-cross prints (the coverage audit measured AAPL's median
  16:00 volume at 672k shares against 5k at 16:01), so it is reported apart and never enters a
  feature. The reference price of every after-hours return is the official close of T from the
  frozen daily panel, supplied by the caller.
* ``target_block`` reads only bars of ET day T+1 whose start is in the premarket, from the
  declared entry minute onward. It is a label and never an input to a feature or a filter.

Premarket and after-hours bars exist only where a trade printed. A price "at" a clock time is
therefore the close of the last print at or before it; an after-hours window with no print
carries the previous price forward (its return is 0), and the 16:00 anchor is the official close.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

AFTER_FIRST, AFTER_LAST = 16 * 60 + 1, 19 * 60 + 59
REGULAR_FIRST, REGULAR_LAST = 9 * 60 + 30, 15 * 60 + 59
PRE_FIRST, PRE_LAST = 4 * 60, 9 * 60 + 29
#: Clock points inside the after-hours window: the last bar a price "at" that time may read.
AFTER_POINTS = {"p1700": 16 * 60 + 59, "p1800": 17 * 60 + 59, "p1930": 19 * 60 + 29,
                "p2000": AFTER_LAST}
LAST30_FIRST = 19 * 60 + 30

SOURCE_FIELDS = ("after_bars", "after_volume", "after_dollar_volume", "after_high", "after_low",
                 "p1700", "p1800", "p1930", "p2000", "last30_dollar", "last30_bars",
                 "bar_1600_dollar", "regular_vwap", "regular_bars")


def _dollar(volume: np.ndarray, vwap: np.ndarray, close: np.ndarray) -> np.ndarray:
    price = np.where(np.isfinite(vwap), vwap, close)
    return np.where(np.isfinite(volume), volume, 0.0) * np.where(np.isfinite(price), price, 0.0)


def source_block(minute: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 volume: np.ndarray, vwap: np.ndarray) -> dict[str, float]:
    """One symbol's DAY T, as known at 20:00 ET. Arrays hold that ET day only, sorted by minute."""
    dollar = _dollar(volume, vwap, close)
    after = (minute >= AFTER_FIRST) & (minute <= AFTER_LAST)
    out: dict[str, float] = {
        "after_bars": float(after.sum()),
        "after_volume": float(np.nansum(np.where(after, volume, 0.0))),
        "after_dollar_volume": float(dollar[after].sum()),
        "after_high": float(np.nanmax(high[after])) if after.any() else float("nan"),
        "after_low": float(np.nanmin(low[after])) if after.any() else float("nan"),
        "last30_dollar": float(dollar[after & (minute >= LAST30_FIRST)].sum()),
        "last30_bars": float((after & (minute >= LAST30_FIRST)).sum()),
        "bar_1600_dollar": float(dollar[minute == AFTER_FIRST - 1].sum()),
    }
    for name, last in AFTER_POINTS.items():
        sel = np.flatnonzero(after & (minute <= last))
        out[name] = float(close[sel[-1]]) if sel.size else float("nan")
    regular = (minute >= REGULAR_FIRST) & (minute <= REGULAR_LAST)
    weights = np.where(regular & np.isfinite(volume) & np.isfinite(vwap), volume, 0.0)
    total = float(weights.sum())
    out["regular_vwap"] = (float(np.nansum(np.where(weights > 0, vwap * weights, 0.0))) / total
                           if total > 0 else float("nan"))
    out["regular_bars"] = float(regular.sum())
    return out


def target_block(minute: np.ndarray, open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                 close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, *, entry_minute: int,
                 entry_window: int, exits: Mapping[str, int]) -> dict[str, float]:
    """DAY T+1 from the entry onward. ``exits`` maps a tag to the last bar start it may read.

    Entry is the open of the first print in ``[entry_minute, entry_minute + entry_window - 1]``.
    An exit is the close of the last print in ``[entry bar, exit last bar]``; the entry bar
    itself counts, so an entry with no later print exits at its own close. MFE and MAE read the
    same bars and nothing before the entry bar.
    """
    dollar = _dollar(volume, vwap, close)
    pre = (minute >= PRE_FIRST) & (minute <= PRE_LAST)
    window = np.flatnonzero(pre & (minute >= entry_minute) & (minute <= entry_minute + entry_window - 1))
    out: dict[str, float] = {
        "pre_dollar_before_entry": float(dollar[pre & (minute < entry_minute)].sum()),
        "pre_bars_before_entry": float((pre & (minute < entry_minute)).sum()),
        "prior30_bars": float((pre & (minute >= entry_minute - 30) & (minute < entry_minute)).sum()),
    }
    if window.size == 0:
        out.update({"entry_price": float("nan"), "entry_minute": float("nan"),
                    "entry_volume": float("nan"), "entry_dollar": float("nan")})
        for tag in exits:
            for key in ("exit", "high", "low", "bars"):
                out[f"{key}_{tag}"] = float("nan")
        return out
    first = int(window[0])
    out["entry_price"] = float(open_[first])
    out["entry_minute"] = float(minute[first])
    out["entry_volume"] = float(volume[first]) if np.isfinite(volume[first]) else 0.0
    out["entry_dollar"] = float(dollar[first])
    for tag, last in exits.items():
        held = np.flatnonzero(pre & (minute >= minute[first]) & (minute <= last))
        if held.size == 0:
            for key in ("exit", "high", "low", "bars"):
                out[f"{key}_{tag}"] = float("nan")
            continue
        out[f"exit_{tag}"] = float(close[held[-1]])
        out[f"high_{tag}"] = float(np.nanmax(high[held]))
        out[f"low_{tag}"] = float(np.nanmin(low[held]))
        out[f"bars_{tag}"] = float(held.size)
    return out


def prior_mean(values: Sequence[float], present: Sequence[bool], window: int,
               minimum: int) -> np.ndarray:
    """Mean over the previous ``window`` grid sessions, strictly before each position.

    ``present`` marks grid sessions for which the symbol has a tape at all; a session without a
    tape is missing, not zero, while a session with a tape and no after-hours print is a real 0.
    """
    values = np.asarray(values, dtype=float)
    present = np.asarray(present, dtype=bool)
    out = np.full(values.size, np.nan)
    for i in range(values.size):
        lo = max(0, i - window)
        block = values[lo:i][present[lo:i]]
        if block.size >= minimum:
            out[i] = float(block.mean())
    return out


def after_features(src: Mapping[str, np.ndarray], close_t: np.ndarray,
                   after_rvol_denominator: np.ndarray) -> dict[str, np.ndarray]:
    """The declared after-hours feature set, from source blocks and the official close of T."""
    c = close_t
    with np.errstate(invalid="ignore", divide="ignore"):
        p17 = np.where(np.isfinite(src["p1700"]), src["p1700"], c)
        p18 = np.where(np.isfinite(src["p1800"]), src["p1800"], c)
        p1930 = np.where(np.isfinite(src["p1930"]), src["p1930"], c)
        p20 = np.where(np.isfinite(src["p2000"]), src["p2000"], c)
        high = np.fmax(src["after_high"], c)
        low = np.fmin(src["after_low"], c)
        span = high - low
        out = {
            "after_return_1600_2000": p20 / c - 1.0,
            "after_return_1600_1700": p17 / c - 1.0,
            "after_return_1700_1800": p18 / p17 - 1.0,
            "after_return_1800_2000": p20 / p18 - 1.0,
            "after_volume": src["after_volume"].astype(float),
            "after_dollar_volume": src["after_dollar_volume"].astype(float),
            "after_rvol": np.where(after_rvol_denominator > 0,
                                   src["after_dollar_volume"] / after_rvol_denominator, np.nan),
            "position_in_after_range": np.where(span > 0, (p20 - low) / span, np.nan),
            "distance_to_after_high": p20 / high - 1.0,
            "last30m_after_return": p20 / p1930 - 1.0,
            "last30m_after_volume_share": np.where(src["after_dollar_volume"] > 0,
                                                   src["last30_dollar"] / src["after_dollar_volume"],
                                                   np.nan),
            "after_high_return": high / c - 1.0,
            "after_low_return": low / c - 1.0,
            "after_peak_retention": np.where(high > c, (p20 - c) / (high - c), np.nan),
            "after_last_price": p20,
            "after_bars": src["after_bars"].astype(float),
        }
    return out


def labels(tgt: Mapping[str, np.ndarray], after_last: np.ndarray, tag: str) -> dict[str, np.ndarray]:
    """Return, MFE and MAE from the entry to one declared exit, and the pre-entry gap."""
    entry = tgt["entry_price"]
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(entry) & (entry > 0)
        r = np.where(ok, tgt[f"exit_{tag}"] / entry - 1.0, np.nan)
        mfe = np.where(ok, tgt[f"high_{tag}"] / entry - 1.0, np.nan)
        mae = np.where(ok, tgt[f"low_{tag}"] / entry - 1.0, np.nan)
        gap = np.where(ok & (after_last > 0), entry / after_last - 1.0, np.nan)
    return {"R": r, "MFE": mfe, "MAE": mae, "after_close_to_entry": gap}
