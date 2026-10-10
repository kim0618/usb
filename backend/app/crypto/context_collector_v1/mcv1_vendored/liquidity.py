"""Layer LIQUIDITY: the resting book, read through the Liquidity Map viewer.

Parity with that viewer is structural rather than compared: this module calls
`liquidity_map.api.snapshot` - the very function the Liquidity Map preview serves - and then
projects fields out of the dict it returns. There is no second implementation of the wall rule, the
continuity rule, the coverage accounting or the depth bands, so there is nothing that can drift.
The frozen `lm-wall.v2` selection and `lm-continuity.v5` carry rule arrive already applied.

What this module adds is the discrimination the panel needs and a single `nearest_wall` field
cannot express: **why** there is no wall. `NONE` (a complete reading that found none), `PARTIAL`
(a bound, not a number), `STALE` (readable but old) and `UNKNOWN` (no reading at all) call for
opposite reactions from the person watching, and collapsing them into a dash would make the most
dangerous of the four look like the most innocent.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from . import contract as C

#: What the viewer calls its own feed states. Mapped, not re-derived.
LM_LIVE = "LIVE"
LM_STALE = "STALE"
LM_SYNCING = "SYNCING"
LM_NO_DATA = "NO_DATA"

#: Section 6. The viewer's default display filter, carried so the panel shows the same set the
#: Liquidity Map preview does. It is the operator's filter, not the frozen floor.
DEFAULT_MIN_NOTIONAL_USDT = "500000"
DEFAULT_WALL_LIMIT = 6






def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _pct_of(distance_bps: Any) -> str | None:
    """bps to percent, as a string, keeping the viewer's decimal discipline."""
    bps = _dec(distance_bps)
    return None if bps is None else str(bps / Decimal(100))


def unavailable_view(symbol: str, reason: str) -> dict[str, Any]:
    """What LIQUIDITY returns for an instrument the collector does not cover."""
    return {"state": C.UNAVAILABLE, "reasons": [reason], "symbol": symbol,
            "sides": None, "depth": None, "coverage": None, "book": None, "walls": None,
            "presence_base_rate": None,
            "observed_range_note": C.OBSERVED_RANGE_NOTE,
            "presence_note": C.WALL_PRESENCE_NOTE}


def _layer_state(snapshot: dict[str, Any]) -> tuple[str, list[str]]:
    """One of LIVE / STALE / PARTIAL / UNKNOWN for the whole layer, with its reasons.

    Driven by the **wall set's** coverage, not by the depth bands. Three of the four contract
    bands are permanently PARTIAL on a `limit=1000` snapshot - they are bounds by construction and
    each carries its own marker - so letting them set the layer state would pin it to PARTIAL
    forever and leave no state left to mean "the wall set could not be proven complete", which is
    the thing an operator has to notice.
    """
    quality = snapshot.get("quality") or {}
    feed = str(quality.get("state") or LM_NO_DATA)
    reasons = [str(reason) for reason in (quality.get("reasons") or [])]
    if feed == LM_NO_DATA or feed == LM_SYNCING:
        return C.UNKNOWN, reasons or [feed]
    if feed == LM_STALE:
        return C.STALE, reasons or [LM_STALE]
    walls = snapshot.get("walls") or {}
    if str(walls.get("coverage")) != C.COMPLETE:
        return C.PARTIAL, reasons + ["WALL_SET_COVERAGE_" + str(walls.get("coverage"))]
    return C.LIVE, reasons


def _wall_state(side: dict[str, Any], layer_state: str) -> tuple[str, str | None]:
    """One of OK / NONE / PARTIAL / STALE / UNKNOWN for one side, with its reason."""
    if layer_state == C.UNKNOWN:
        return C.UNKNOWN, side.get("nearest_unavailable_reason") or "NO_READING"
    if layer_state == C.STALE:
        return C.STALE, "JOURNAL_OLDER_THAN_FRESHNESS_BOUND"
    if side.get("nearest_wall") is not None:
        return C.WALL_OK, None
    coverage = str(side.get("coverage"))
    if coverage == C.COMPLETE:
        # A proven-complete set with nothing in it is the one case where "no wall" is a reading
        # rather than an absence of one. It is also the only case the panel may say it in.
        return C.WALL_NONE, "NO_CANDIDATE_QUALIFIES_UNDER_LM_WALL_V2"
    if coverage == C.PARTIAL:
        return C.PARTIAL, side.get("nearest_unavailable_reason") or "WALL_SET_PARTIAL"
    return C.UNKNOWN, side.get("nearest_unavailable_reason") or "WALL_SET_UNKNOWN"


def _wall(wall: dict[str, Any] | None) -> dict[str, Any] | None:
    """One wall, exactly the viewer's figures, with distance restated in percent as well as bps."""
    if wall is None:
        return None
    return {
        "price": wall.get("price"),
        "qty_btc": wall.get("qty_btc"),
        "notional_usdt": wall.get("notional_usdt"),
        "distance_bps": wall.get("distance_bps"),
        "distance_pct": _pct_of(wall.get("distance_bps")),
        "multiple": wall.get("multiple"),
        "coverage": wall.get("coverage"),
        "persistence_ms": wall.get("observed_persistence_ms"),
        "own_persistence_ms": wall.get("own_persistence_ms"),
        "carried_persistence_ms": wall.get("carried_persistence_ms"),
        "persistence_source": wall.get("persistence_source"),
        "continuity_status": wall.get("continuity_status"),
        "not_carried_reason": wall.get("not_carried_reason"),
        "carried_members": wall.get("carried_members"),
        "bin_low": wall.get("bin_low"),
        "bin_high": wall.get("bin_high"),
        "bin_members": wall.get("bin_members"),
        "bin_candidate_notional_usdt": wall.get("bin_candidate_notional_usdt"),
        "bin_candidate_notional_is_lower_bound":
            wall.get("bin_candidate_notional_is_lower_bound"),
        "values_as_of": wall.get("values_as_of"),
        "generation": wall.get("generation"),
        "first_seen_ms": wall.get("first_seen_ms"),
    }


def _side_view(snapshot: dict[str, Any], side_key: str, layer_state: str) -> dict[str, Any]:
    side = (snapshot.get("sides") or {}).get(side_key) or {}
    wall_state, reason = _wall_state(side, layer_state)
    depth = [{"band_pct": band.get("band_pct"), "coverage": band.get("coverage"),
              "notional": band.get("notional"), "qty": band.get("qty"),
              "observed_notional": band.get("observed_notional"),
              "observed_qty": band.get("observed_qty"),
              "is_lower_bound": band.get("is_lower_bound"),
              "levels": band.get("levels"),
              "price_low": band.get("price_low"), "price_high": band.get("price_high"),
              "imbalance_usdt": band.get("imbalance_usdt")}
             for band in (side.get("depth") or [])]
    return {
        "side": side_key,
        "wall_state": wall_state,
        "wall_state_reason": reason,
        "nearest_wall": _wall(side.get("nearest_wall")),
        "coverage": side.get("coverage"),
        "candidates_total": side.get("candidates_total"),
        "walls_selected": side.get("walls_selected"),
        "walls_shown": side.get("walls_shown"),
        "walls": [_wall(wall) for wall in (side.get("walls") or [])],
        "depth": depth,
    }


def liquidity_view(snapshot: dict[str, Any] | None, *, symbol: str,
                   reason: str | None = None) -> dict[str, Any]:
    """The LIQUIDITY layer payload for one poll."""
    if symbol != C.BTC:
        return unavailable_view(symbol, C.REASON_COLLECTOR_BTC_ONLY)
    if snapshot is None or "__error__" in snapshot:
        view = unavailable_view(symbol, reason or "VIEWER_UNREADABLE")
        view["state"] = C.UNKNOWN
        return view
    state, reasons = _layer_state(snapshot)
    ask = _side_view(snapshot, "ASK", state)
    bid = _side_view(snapshot, "BID", state)
    coverage = snapshot.get("coverage") or {}
    price = snapshot.get("price") or {}
    quality = snapshot.get("quality") or {}
    walls = snapshot.get("walls") or {}
    source = snapshot.get("source") or {}
    imbalance = None
    bands = coverage.get("bands") or []
    if bands:
        tightest = bands[0]
        imbalance = {"band_pct": tightest.get("band_pct"),
                     "coverage": tightest.get("coverage"),
                     "notional_bid": tightest.get("observed_notional_bid"),
                     "notional_ask": tightest.get("observed_notional_ask"),
                     "is_lower_bound": tightest.get("is_lower_bound")}
    return {
        "state": state,
        "reasons": reasons,
        "symbol": symbol,
        "sides": {"ASK": ask, "BID": bid},
        "book": {
            "mid": price.get("mid"),
            "best_bid": price.get("best_bid"),
            "best_ask": price.get("best_ask"),
            "spread_bps": price.get("spread_bps"),
            "observed_low": coverage.get("known_low"),
            "observed_high": coverage.get("known_high"),
            "observed_low_pct": coverage.get("observed_low_pct"),
            "observed_high_pct": coverage.get("observed_high_pct"),
            "observed_symmetric_pct": coverage.get("observed_symmetric_pct"),
            "snapshot_limit": coverage.get("snapshot_limit"),
        },
        "band_imbalance": imbalance,
        "coverage": {
            "bands": coverage.get("bands"),
            "complete_bands": coverage.get("complete_bands"),
            "partial_bands": coverage.get("partial_bands"),
            "lower_bound_marker": coverage.get("lower_bound_marker"),
            "lower_bounds_identical": coverage.get("lower_bounds_identical"),
        },
        "walls": {
            "coverage": walls.get("coverage"),
            "verified_by": walls.get("verified_by"),
            "unverified_reason": walls.get("unverified_reason"),
            "source": walls.get("source"),
            "candidate_count": walls.get("candidate_count"),
            "walls_selected": walls.get("walls_selected"),
            "carried": walls.get("carried"),
            "rule": (walls.get("rule") or {}).get("rule_version"),
            "continuity_rule": (walls.get("continuity_rule") or {}).get("rule_version"),
            "filter_min_notional_usdt": (walls.get("filter") or {}).get("min_notional_usdt"),
        },
        "journal": {
            "root": source.get("root"),
            "session_id": source.get("session_id"),
            "session_age_ms": source.get("session_age_ms"),
            "session_ended": source.get("session_ended"),
            "sample_receive_ms": source.get("sample_receive_ms"),
            "journal_age_ms": source.get("journal_age_ms"),
            "feed_state": quality.get("state"),
            "depth_age_ms": quality.get("depth_age_ms"),
            "book_state": quality.get("book_state"),
            "exchange": source.get("exchange"),
            "symbol": source.get("symbol"),
            # The resnapshot generation, which is what makes a wall observation comparable with
            # the previous reading's. Taken from the collector's own state file, because a
            # generation read off the wall rows is unavailable exactly when it matters most - a
            # resnapshot that emptied the set leaves no row to read it from.
            "book_generation": (snapshot.get("resnapshot") or {}).get("generation"),
        },
        "presence_base_rate": {
            "ask_pct": C.WALL_PRESENCE_BASE_RATE_ASK_PCT,
            "bid_pct": C.WALL_PRESENCE_BASE_RATE_BID_PCT,
            "mean_per_side_when_present": C.WALL_MEAN_PER_SIDE_WHEN_PRESENT,
            "window": "Market Context R0, 3.61h / 12,779 samples, walls within 10 bp",
        },
        "observed_range_note": C.OBSERVED_RANGE_NOTE,
        "presence_note": C.WALL_PRESENCE_NOTE,
    }


__all__ = ["liquidity_view", "unavailable_view", "DEFAULT_MIN_NOTIONAL_USDT",
           "DEFAULT_WALL_LIMIT"]
