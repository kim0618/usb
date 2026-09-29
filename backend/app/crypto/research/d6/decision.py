"""The FDN-V1 decision: features, buckets, filters, score, and what comes out of them.

The output is an explanation, not just a verdict. Every filter result, every category level and
every reason code is carried, so a HOLD can be read back later without rerunning anything.

The engine emits `ENTRY_INTENT_LONG` at most. It builds no order and calls no broker.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import features as F
from . import filters as FILTERS
from . import score as SCORE
from . import sizing as SIZING
from .contract import Contract
from .model import (BarWindow, BucketAssignment, CategoryScore, ENTRY_INTENT_LONG, FAIL,
                    FeatureValues, FilterResult, HISTORICAL, HOLD, LONG, MODES, MarketContext,
                    NO_ENTRY_INTENT, NOT_EVALUATED, SizingResult, StrategyState)

SCHEMA_VERSION = "D6B_DECISION_V1"


@dataclass(frozen=True)
class Decision:
    timestamp_ms: int
    decision_bar_ts_ms: int
    strategy_id: str
    strategy_version: str
    strategy_name: str
    contract_hash: str
    mode: str
    decision: str
    entry_intent: str
    entry_allowed: bool
    long_score: int
    short_score: int | None
    short_state: str
    categories: list[CategoryScore]
    filters: list[FilterResult]
    filter_pass: bool
    reason_codes: list[str]
    features: FeatureValues
    buckets: dict[str, BucketAssignment]
    volatility_label: str | None
    market_context: dict[str, Any]
    required_inputs_available: dict[str, bool]
    data_age: dict[str, int | None]
    sizing: SizingResult | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "timestamp_ms": self.timestamp_ms,
            "decision_bar_ts_ms": self.decision_bar_ts_ms,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_name": self.strategy_name,
            "contract_hash": self.contract_hash,
            "mode": self.mode,
            "decision": self.decision,
            "entry_intent": self.entry_intent,
            "entry_allowed": self.entry_allowed,
            "long_score": self.long_score,
            "short_score": self.short_score,
            "short_state": self.short_state,
            "category_scores": [c.as_dict() for c in self.categories],
            "hard_filters": [f.as_dict() for f in self.filters],
            "filter_pass": self.filter_pass,
            "reason_codes": list(self.reason_codes),
            "features": self.features.as_dict(),
            "buckets": {k: {"bucket": v.label, "valid_fraction": v.valid_fraction}
                        for k, v in self.buckets.items()},
            "volatility_label": self.volatility_label,
            "market_context": self.market_context,
            "required_inputs_available": self.required_inputs_available,
            "data_age": self.data_age,
            "sizing": None if self.sizing is None else self.sizing.as_dict(),
            "notes": self.notes,
        }

    def to_json(self) -> str:
        """Stable bytes: sorted keys, no whitespace drift, no set iteration."""
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"),
                          allow_nan=True)


def build_buckets(contract: Contract, window: BarWindow,
                  series: dict[str, np.ndarray]) -> dict[str, BucketAssignment]:
    """Cutoffs from the previous 30 complete UTC days, exactly as D5 forms them."""
    window_days = contract.bucket_window_days
    window_bars = window_days * F.DAY_BARS
    min_fraction = contract.bound["insufficient_history"]["min_valid_fraction"]
    history_slice = F.previous_days_slice(window.ts_ms, window.decision_ts_ms, window_days)
    out: dict[str, BucketAssignment] = {}
    for name in contract.bucketed_features:
        values = series[name]
        cutoffs, valid_fraction = F.bucket_cutoffs(
            values[history_slice], contract.bucket_quantiles, min_fraction, window_bars)
        out[name] = F.assign_bucket(float(values[-1]), cutoffs, valid_fraction)
    return out


def decide(contract: Contract, window: BarWindow, market: MarketContext,
           state: StrategyState, *, mode: str = HISTORICAL) -> Decision:
    """One deterministic decision for bar t. No clock, no network, no account access."""
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")

    series = F.compute_series(window)
    values = FeatureValues(f_basis=float(series["f_basis"][-1]),
                           f_oi1h=float(series["f_oi1h"][-1]),
                           f_drop1h=float(series["f_drop1h"][-1]),
                           f_rv24h=float(series["f_rv24h"][-1]))
    buckets = build_buckets(contract, window, series)
    vol_label = F.volatility_label(values.f_rv24h, contract.vol_low_below,
                                   contract.vol_high_above)

    categories = SCORE.long_categories(contract, values, buckets)
    long_points = SCORE.long_score(categories)
    short_points = SCORE.short_score(contract)

    sizing_result = SIZING.size(contract, equity=state.equity,
                                entry_price=market.entry_reference_price,
                                rv24h=values.f_rv24h, safe_max_qty=market.safe_max_qty)
    intended_qty = sizing_result.final_qty if sizing_result.feasible else None

    filter_results = FILTERS.evaluate(
        contract, mode=mode, features=values, buckets=buckets, vol_label=vol_label,
        market=market, state=state, bar_close_ms=window.bar_close_ms,
        intended_qty=intended_qty)

    reason_codes: list[str] = [r.reason for r in filter_results if r.status in (FAIL, NOT_EVALUATED)]
    filter_pass = not any(r.blocking for r in filter_results)

    if long_points < contract.long_entry_min_score:
        reason_codes.append("SCORE_BELOW_THRESHOLD")
    reason_codes.extend(SCORE.mandatory_failures(contract, categories))
    if not SCORE.separation_ok(contract, long_points, short_points):
        reason_codes.append("SCORE_SEPARATION_BELOW_MIN")
    if not contract.short_enabled:
        reason_codes.append("SHORT_DISABLED_FOR_V1")

    blocking_reasons = [code for code in reason_codes if code != "SHORT_DISABLED_FOR_V1"
                        and not code.startswith(("H6_NO_HISTORICAL", "H7_NO_HISTORICAL"))]
    entry_allowed = filter_pass and not blocking_reasons
    decision = LONG if entry_allowed else HOLD
    entry_intent = ENTRY_INTENT_LONG if entry_allowed else NO_ENTRY_INTENT

    if entry_allowed and not sizing_result.feasible:
        # A size that cannot be placed is not an entry, whatever the score said.
        reason_codes.append(f"SIZING_{sizing_result.reason}")
        entry_allowed = False
        decision = HOLD
        entry_intent = NO_ENTRY_INTENT

    return Decision(
        timestamp_ms=market.now_ms,
        decision_bar_ts_ms=window.decision_ts_ms,
        strategy_id=contract.strategy_id,
        strategy_version=contract.strategy_version,
        strategy_name=contract.strategy_name,
        contract_hash=contract.sha256,
        mode=mode,
        decision=decision,
        entry_intent=entry_intent,
        entry_allowed=entry_allowed,
        long_score=long_points,
        short_score=short_points,
        short_state="SHORT_DISABLED_FOR_V1",
        categories=categories,
        filters=filter_results,
        filter_pass=filter_pass,
        reason_codes=reason_codes,
        features=values,
        buckets=buckets,
        volatility_label=vol_label,
        market_context={
            "bar_close_ms": window.bar_close_ms,
            "entry_reference_price": market.entry_reference_price,
            "mark": market.mark,
            "bid": market.bid,
            "ask": market.ask,
            "next_funding_ts_ms": market.next_funding_ts_ms,
            "safe_max_qty": market.safe_max_qty,
            "equity": state.equity,
            "position_open": state.position_open,
        },
        required_inputs_available={
            "features": not values.any_nan(),
            "buckets": all(b.cutoffs is not None for b in buckets.values()),
            "oi_record_ts": market.oi_record_ts_ms is not None,
            "next_funding_ts": market.next_funding_ts_ms is not None,
            "entry_reference_price": market.entry_reference_price is not None,
            "equity": state.equity is not None,
            "quote": market.bid is not None and market.ask is not None,
            "safe_max_qty": market.safe_max_qty is not None,
        },
        data_age={
            "bar_age_ms": market.now_ms - window.bar_close_ms,
            "oi_age_ms": None if market.oi_record_ts_ms is None
            else market.now_ms - market.oi_record_ts_ms,
            "ms_to_funding": None if market.next_funding_ts_ms is None
            else market.next_funding_ts_ms - market.now_ms,
        },
        sizing=sizing_result,
    )
