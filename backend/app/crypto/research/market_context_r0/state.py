"""Contract section 15's state label, for the preview only.

Two honesty notes live here because the label is the one thing on the screen that looks like a
recommendation and is not:

* The families of section 9 are measured **forwards**: their clauses look at `[s, s+60s]` and their
  outcomes start after that. A screen cannot do that, so the label reads the same clauses
  **backwards** over `[s-60s, s]`. It is the same vocabulary, not the same measurement.
* The study's verdict is `A_NO_CONTEXT_EDGE`, so the label is a description of the book and the
  tape, not a view on where price is going. `UNKNOWN` is a first class value and is produced
  whenever the book or the flow window is not COMPLETE, because an unobserved quantity and a
  neutral one are different things.
"""
from __future__ import annotations

from .families import WALL_PROX_BPS_HI, WALL_PROX_BPS_LO

LONG_LEANING = "LONG_LEANING"
SHORT_LEANING = "SHORT_LEANING"
CONFLICT = "CONFLICT"
NEUTRAL = "NEUTRAL"
UNKNOWN = "UNKNOWN"

BACKWARD_NOTE = ("the family clauses read over the trailing 60 s, because that is the only window "
                 "a screen has; the study measured them forwards")
NO_DIRECTION_NOTE = ("a description of book and tape under the frozen clauses, not a view on "
                     "direction: the study returned A_NO_CONTEXT_EDGE")


def _near(bins: dict) -> tuple[str, dict] | None:
    near = [(b, w) for b, w in bins.items()
            if WALL_PROX_BPS_LO <= abs(float(w["distance_bps"])) <= WALL_PROX_BPS_HI]
    if not near:
        return None
    return sorted(near, key=lambda bw: abs(float(bw[1]["distance_bps"])))[0]


def label(frame, i: int, lookback: int = 60) -> dict:
    """The state at sample `i`, from `[i - lookback, i]` only."""
    s = frame.samples[i]
    if not s.ok or not s.flow60_complete or s.flow60_imb is None:
        return {"state": UNKNOWN, "reason": "BOOK_OR_FLOW_NOT_COMPLETE", "fires": []}
    lo = max(0, i - lookback)
    fires: list[str] = []
    for side, bins, long_when_held in (("ASK", s.ask_bins, False), ("BID", s.bid_bins, True)):
        pick = _near(bins)
        if pick is None:
            continue
        bin_low, _ = pick
        present_now = True
        gap = any(bin_low not in (frame.samples[k].ask_bins if side == "ASK"
                                  else frame.samples[k].bid_bins) for k in range(lo, i))
        buy = s.flow60_imb > 0
        dom = buy if side == "ASK" else (not buy)
        if present_now and dom:
            fires.append("G4" if long_when_held else "G1")
        if present_now and gap:
            fires.append("G6" if long_when_held else "G3")
    longs = {"G4", "G6"} & set(fires)
    shorts = {"G1", "G3"} & set(fires)
    if longs and shorts:
        state = CONFLICT
    elif longs:
        state = LONG_LEANING
    elif shorts:
        state = SHORT_LEANING
    else:
        state = NEUTRAL
    return {"state": state, "reason": None, "fires": sorted(fires)}
