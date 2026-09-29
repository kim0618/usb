"""The 13 contracted hard filters.

A filter is not a score. Any FAIL blocks entry outright, however high the score is, and the
result of every filter is reported whether it blocked or not so a HOLD can always be explained.

Three statuses exist. PASS and FAIL mean what they say. NOT_EVALUATED is reserved for the two
filters the contract marks `realtime_only` when the engine runs over history, because D1
established there is no historical order book: those two are skipped by design and the skip is
recorded, never silently treated as a pass. A filter whose input is missing when it should be
present is a FAIL, not a skip.
"""
from __future__ import annotations

import numpy as np

from .contract import Contract
from .features import VOL_LOW
from .model import (BucketAssignment, FAIL, FeatureValues, FilterResult, HISTORICAL,
                    MarketContext, NOT_EVALUATED, PASS, StrategyState)

REALTIME_ONLY = ("H6", "H7")


def _result(contract: Contract, filter_id: str, status: str, reason: str,
            observed=None, threshold=None) -> FilterResult:
    spec = contract.hard_filter(filter_id)
    return FilterResult(filter_id=filter_id, name=spec["name"], status=status, reason=reason,
                        observed=observed, threshold=threshold if threshold is not None
                        else spec["threshold"])


def evaluate(contract: Contract, *, mode: str, features: FeatureValues,
             buckets: dict[str, BucketAssignment], vol_label: str | None,
             market: MarketContext, state: StrategyState,
             bar_close_ms: int, intended_qty: float | None = None) -> list[FilterResult]:
    """Every filter, in contract order. Returns results; the caller decides what to do."""
    out: list[FilterResult] = []
    b = contract.bound

    # H1 DATA_STALE -----------------------------------------------------------------------
    age = market.now_ms - bar_close_ms
    limit = b["data_stale"]["max_age_ms"]
    out.append(_result(contract, "H1", FAIL if age > limit else PASS,
                       "H1_DATA_STALE" if age > limit else "H1_OK",
                       observed={"bar_age_ms": age}))

    # H2 OI_STALE -------------------------------------------------------------------------
    limit = b["oi_stale"]["max_age_ms"]
    if market.oi_record_ts_ms is None:
        out.append(_result(contract, "H2", FAIL, "H2_OI_RECORD_TS_UNKNOWN", observed=None))
    else:
        oi_age = market.now_ms - market.oi_record_ts_ms
        out.append(_result(contract, "H2", FAIL if oi_age > limit else PASS,
                           "H2_OI_STALE" if oi_age > limit else "H2_OK",
                           observed={"oi_age_ms": oi_age}))

    # H3 INSUFFICIENT_HISTORY -------------------------------------------------------------
    min_fraction = b["insufficient_history"]["min_valid_fraction"]
    thin = {name: round(assignment.valid_fraction, 6)
            for name, assignment in buckets.items() if assignment.cutoffs is None}
    out.append(_result(contract, "H3", FAIL if thin else PASS,
                       "H3_INSUFFICIENT_HISTORY" if thin else "H3_OK",
                       observed={"features_without_cutoffs": thin,
                                 "min_valid_fraction": min_fraction}))

    # H4 FEATURE_NAN ----------------------------------------------------------------------
    nan_features = [name for name, value in features.as_dict().items()
                    if not np.isfinite(value)]
    out.append(_result(contract, "H4", FAIL if nan_features else PASS,
                       "H4_FEATURE_NAN" if nan_features else "H4_OK",
                       observed={"nan_features": nan_features}))

    # H5 VOL_LOW --------------------------------------------------------------------------
    if vol_label is None:
        out.append(_result(contract, "H5", FAIL, "H5_VOL_LABEL_UNKNOWN", observed=None))
    else:
        low = vol_label == b["vol_low"]["blocked_label"]
        out.append(_result(contract, "H5", FAIL if low else PASS,
                           "H5_VOL_LOW" if low else "H5_OK", observed={"label": vol_label}))

    # H6 SPREAD_WIDE (realtime only) ------------------------------------------------------
    if mode == HISTORICAL:
        out.append(_result(contract, "H6", NOT_EVALUATED, "H6_NO_HISTORICAL_ORDERBOOK"))
    elif market.bid is None or market.ask is None or market.bid <= 0 or market.ask <= 0:
        out.append(_result(contract, "H6", FAIL, "H6_QUOTE_UNKNOWN"))
    else:
        mid = (market.bid + market.ask) / 2
        spread_bp = (market.ask - market.bid) / mid * 10_000 if mid > 0 else float("inf")
        wide = spread_bp > b["spread_wide"]["max_spread_bp"]
        out.append(_result(contract, "H6", FAIL if wide else PASS,
                           "H6_SPREAD_WIDE" if wide else "H6_OK",
                           observed={"spread_bp": spread_bp}))

    # H7 DEPTH_SHORT (realtime only) ------------------------------------------------------
    if mode == HISTORICAL:
        out.append(_result(contract, "H7", NOT_EVALUATED, "H7_NO_HISTORICAL_ORDERBOOK"))
    elif market.safe_max_qty is None:
        out.append(_result(contract, "H7", FAIL, "H7_SAFE_MAX_UNAVAILABLE"))
    elif intended_qty is None:
        out.append(_result(contract, "H7", PASS, "H7_NO_QTY_REQUESTED"))
    else:
        short = intended_qty > market.safe_max_qty
        out.append(_result(contract, "H7", FAIL if short else PASS,
                           "H7_DEPTH_SHORT" if short else "H7_OK",
                           observed={"intended_qty": intended_qty,
                                     "safe_max_qty": market.safe_max_qty}))

    # H8 COOLDOWN -------------------------------------------------------------------------
    cooldown_ms = b["cooldown"]["cooldown_ms"]
    if state.last_exit_ts_ms is None:
        out.append(_result(contract, "H8", PASS, "H8_NO_PRIOR_EXIT"))
    else:
        since = market.now_ms - state.last_exit_ts_ms
        cooling = since < cooldown_ms
        out.append(_result(contract, "H8", FAIL if cooling else PASS,
                           "H8_COOLDOWN" if cooling else "H8_OK",
                           observed={"ms_since_exit": since}))

    # H9 POSITION_OPEN --------------------------------------------------------------------
    out.append(_result(contract, "H9", FAIL if state.position_open else PASS,
                       "H9_POSITION_OPEN" if state.position_open else "H9_FLAT",
                       observed={"position_open": state.position_open}))

    # H10 DAILY_LOSS_GUARD ----------------------------------------------------------------
    guard = b["daily_loss_guard"]["guard_pct"]
    breached = state.day_realized_pnl_pct <= -guard
    out.append(_result(contract, "H10", FAIL if breached else PASS,
                       "H10_DAILY_LOSS_GUARD" if breached else "H10_OK",
                       observed={"day_realized_pnl_pct": state.day_realized_pnl_pct,
                                 "day_utc": state.day_utc}))

    # H11 CONSECUTIVE_LOSS ----------------------------------------------------------------
    max_losses = b["consecutive_loss"]["max_consecutive"]
    block_ms = b["consecutive_loss"]["block_ms"]
    streak = state.consecutive_losses >= max_losses
    within_block = (state.last_loss_ts_ms is not None
                    and market.now_ms - state.last_loss_ts_ms < block_ms)
    blocked = streak and within_block
    out.append(_result(contract, "H11", FAIL if blocked else PASS,
                       "H11_CONSECUTIVE_LOSS" if blocked else "H11_OK",
                       observed={"consecutive_losses": state.consecutive_losses,
                                 "ms_since_last_loss": None if state.last_loss_ts_ms is None
                                 else market.now_ms - state.last_loss_ts_ms}))

    # H12 FUNDING_WINDOW ------------------------------------------------------------------
    lead_ms = b["funding_window"]["min_lead_ms"]
    if market.next_funding_ts_ms is None:
        out.append(_result(contract, "H12", FAIL, "H12_NEXT_FUNDING_UNKNOWN"))
    else:
        lead = market.next_funding_ts_ms - market.now_ms
        too_close = lead < lead_ms
        out.append(_result(contract, "H12", FAIL if too_close else PASS,
                           "H12_FUNDING_WINDOW" if too_close else "H12_OK",
                           observed={"ms_to_funding": lead}))

    # H13 LEDGER_DIVERGENCE / emergency stop ----------------------------------------------
    stopped = state.emergency_stop is not None
    out.append(_result(contract, "H13", FAIL if stopped else PASS,
                       "H13_EMERGENCY_STOP" if stopped else "H13_OK",
                       observed={"trigger": state.emergency_stop}))

    seen = [r.filter_id for r in out]
    if tuple(seen) != contract.hard_filter_ids:
        raise RuntimeError(f"filter coverage mismatch: evaluated {seen}, "
                           f"contract lists {list(contract.hard_filter_ids)}")
    return out


def blocking(results: list[FilterResult]) -> list[FilterResult]:
    return [r for r in results if r.blocking]
