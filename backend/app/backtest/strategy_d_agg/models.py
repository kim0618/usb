"""Statuses and failure codes of D-AGG-1.

``HardFail`` is V1's, re-exported, so one audit reads every D phase the same way. What is new is
the vocabulary of a five-session excursion window: why a ``(session, ticker)`` has, or has no,
usable MFE/MAE. The codes partition every row (first match in ``EXCURSION_STATUS_ORDER`` wins).
"""

from app.backtest.strategy_d_analog.models import FreezeIdentity, HardFail  # noqa: F401

STRATEGY_ID = "MARKET_STRUCTURE_ANALOG_TAIL_V1"

#: Row status of one excursion window. ``VALID`` and ``VALID_GAP`` are the two valid states;
#: every other code is invalid. Integer codes are the artifact encoding and never change.
EXCURSION_STATUS_ORDER = (
    "VALID",                # D+1..D+5 all carry a bar, not CA suspect
    "VALID_GAP",            # valid under the frozen V2-A contract, but a middle session D+2..D+4 has no bar
    "BOUNDARY",             # session D+5 lies past the end of the frozen grid
    "NO_ENTRY_BAR",         # no bar (or a non-positive open) on D+1
    "MISSING_HORIZON_BAR",  # D+1 bar present, no bar on session D+5 itself (halt or delisting)
    "LABEL_CA_SUSPECT",     # V2-A label CA rule (ratio 3.0 over D..D+10) flags the window
)
VALID_CODES = (0, 1)
STATUS_CODE = {name: code for code, name in enumerate(EXCURSION_STATUS_ORDER)}

#: Descriptive sub-classification of MISSING_HORIZON_BAR. It reads bars after D+5 and therefore
#: never enters validity; it only tells a reader whether the gap looked like a halt or an exit.
MISSING_SUBCLASS_ORDER = ("NOT_MISSING", "RESUMES_LATER", "NO_LATER_BAR")
