"""E-RT2.1: the MARKET_DATA_UNAVAILABLE contract for a canonical symbol the source cannot serve.

Measured on 2026-09-22: ``PS`` is in Kiwoom's canonical listing (``usa10099``) but both chart lanes
(``usa06011`` minute, ``usa06010`` tick) answer MARKET_DATA_UNAVAILABLE on ND, NY and NA, every time.
That is not "no premarket trading" and it is not an H5 answer, so it gets its own state.

Per symbol:

* ``FEATURE_COMPLETE``  - cutoff state built, premarket prints exist; H5 is evaluated normally;
* ``SPARSE_NO_PREMARKET`` - the source served the symbol and it simply did not print premarket. This
  is the frozen E1 semantics of "no premarket row": the symbol is not a decision-frame row, H5 is
  FALSE for it in the sense that it never becomes a candidate, and nothing is missing;
* ``MARKET_DATA_UNAVAILABLE`` - the source refused or could not serve the symbol. ``h5_status`` stays
  UNKNOWN: it is never silently turned into H5 = False, it can never be an executable candidate, and
  no other candidate is promoted in its place (NO BACKFILL).

The B2 denominator is untouched: it is whatever the frozen decision seal counts as eligible rows
(``SignalResult.eligible_count``), and no symbol is added to or removed from that count here. The
canonical universe list is also unchanged - an unavailable symbol stays in it and is reported.

A session is not blocked by unavailable symbols; each one fails closed on its own. The count and the
share are recorded in the session metadata, and this stage declares no tolerated-share threshold.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

FEATURE_COMPLETE = "FEATURE_COMPLETE"
SPARSE_NO_PREMARKET = "SPARSE_NO_PREMARKET"
MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
STALE = "STALE"
STATES = (FEATURE_COMPLETE, SPARSE_NO_PREMARKET, MARKET_DATA_UNAVAILABLE, STALE)

H5_TRUE, H5_FALSE, H5_UNKNOWN = "H5_TRUE", "H5_FALSE", "H5_UNKNOWN"
#: A source refusal seen on every lane for a symbol; a transport failure is not this.
SOURCE_REFUSAL_CODES = frozenset({"MARKET_DATA_UNAVAILABLE", "INVALID_SYMBOL"})


@dataclass(frozen=True)
class SymbolOutcome:
    """What one canonical symbol ended the 09:25 finalization with."""

    symbol: str
    finalized: bool
    contiguous: bool
    on_time: bool
    premarket_bars: int
    lane_errors: Mapping[str, str | None]      # lane -> error code (None when the lane succeeded)

    @property
    def refused_by_every_lane(self) -> bool:
        codes = list(self.lane_errors.values())
        return bool(codes) and all(code in SOURCE_REFUSAL_CODES for code in codes)


def classify(outcome: SymbolOutcome) -> str:
    """The symbol's state under the RT2.1 contract."""
    if outcome.refused_by_every_lane:
        return MARKET_DATA_UNAVAILABLE
    if not (outcome.finalized and outcome.contiguous and outcome.on_time
            and not any(outcome.lane_errors.values())):
        return STALE
    return FEATURE_COMPLETE if outcome.premarket_bars > 0 else SPARSE_NO_PREMARKET


def h5_status(state: str, h5_true: bool | None) -> str:
    """H5 for a symbol; an unavailable or stale symbol is UNKNOWN, never a silent False."""
    if state in (MARKET_DATA_UNAVAILABLE, STALE):
        return H5_UNKNOWN
    return H5_TRUE if h5_true else H5_FALSE


def executable(state: str) -> bool:
    """Only a symbol whose cutoff state is real can be selected; there is no replacement for one
    that is not (the frozen rule fills no gap: selection is the first three of the R1 order over the
    H5 candidates, and a symbol that never became a candidate is simply absent)."""
    return state in (FEATURE_COMPLETE, SPARSE_NO_PREMARKET)


def diagnostics(states: Mapping[str, str], h5_by_symbol: Mapping[str, str],
                eligible_rows: int, candidates: Sequence[str]) -> dict[str, Any]:
    """Counts recorded next to the decision. ``eligible_rows`` is the frozen B2 denominator, taken
    from the decision seal, not recomputed here."""
    counts = {state: sum(1 for value in states.values() if value == state) for state in STATES}
    unavailable = [s for s, value in states.items() if value == MARKET_DATA_UNAVAILABLE]
    total = len(states)
    return {
        "canonical_universe": total,
        "feature_complete": counts[FEATURE_COMPLETE],
        "sparse_no_premarket": counts[SPARSE_NO_PREMARKET],
        "market_data_unavailable": counts[MARKET_DATA_UNAVAILABLE],
        "stale": counts[STALE],
        "market_data_unavailable_symbols": sorted(unavailable),
        "market_data_unavailable_share": (len(unavailable) / total) if total else 0.0,
        "h5_true": sum(1 for v in h5_by_symbol.values() if v == H5_TRUE),
        "h5_false": sum(1 for v in h5_by_symbol.values() if v == H5_FALSE),
        "h5_unknown": sum(1 for v in h5_by_symbol.values() if v == H5_UNKNOWN),
        "breadth_denominator_eligible_rows": eligible_rows,
        "breadth_denominator_source": "frozen decision seal SignalResult.eligible_count; unchanged by "
                                      "this contract (no symbol added to or removed from it)",
        "h5_candidates": len(candidates),
        "no_backfill": "an unavailable or stale symbol is never replaced by the next candidate",
    }


def outcome_from_cache(cache: Any, *, deadline: Any, lane_errors: Mapping[str, str | None] | None = None,
                       cutoff_minute: int = 9 * 60 + 24) -> SymbolOutcome:
    """The RT2 finalizer's per-symbol cache seen through the RT2.1 contract.

    ``cache.error`` is the finalizer's last lane error. A refusal code means the source would not
    serve the symbol on that lane; the caller passes ``lane_errors`` when it tried more than one.
    """
    errors = dict(lane_errors) if lane_errors is not None else {cache.data_source or "lane": cache.error}
    minutes = sorted(m for m in cache.bars if m <= cutoff_minute)
    return SymbolOutcome(
        symbol=cache.symbol,
        finalized=cache.finalized_at is not None,
        contiguous=bool(cache.contiguous),
        on_time=cache.finalized_at is not None and cache.finalized_at <= deadline,
        premarket_bars=len(minutes),
        lane_errors=errors,
    )


def reclassify(status: str, error: str | None, *, lanes: int = 1) -> str:
    """Re-read an RT2 status row under this contract; RT2 had no unavailable state and said STALE."""
    code = (error or "").strip()
    if code in SOURCE_REFUSAL_CODES or code.startswith("MarketDataError"):
        return MARKET_DATA_UNAVAILABLE
    return status if status in STATES else STALE
