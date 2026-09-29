"""The decision pass over the whole grid, vectorised but not re-implemented.

`d6.decision.decide` answers one bar at a time and rebuilds a 46,000 row window to do it. Over
2.9 million bars that is not affordable, so this module computes the same quantities column-wise
using **the D6-B feature functions themselves** and the score levels read from the contract.

Nothing here is allowed to be a second opinion. `verify_against_engine` re-runs the real
`decide()` on every bar this pass thinks is a signal, plus a fixed sample of bars it thinks are
not, and raises if the two ever disagree. The engine remains the authority; this module is only
a fast index into it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..d6 import decision as DEC
from ..d6 import features as F
from ..d6 import score as SCORE
from ..d6.contract import Contract as StrategyContract
from ..d6.model import (FAIL, FeatureValues, HISTORICAL, LONG, MarketContext, NOT_EVALUATED,
                        StrategyState)

DAY_BARS = F.DAY_BARS
MINUTE_MS = 60_000

#: Reason codes that do not block an entry: the two realtime-only filters skipped over history,
#: the standing SHORT notice, and the sizing deferral that I1 moves to execution time.
NON_BLOCKING = frozenset({"SHORT_DISABLED_FOR_V1",
                          "H6_NO_HISTORICAL_ORDERBOOK", "H7_NO_HISTORICAL_ORDERBOOK"})
DEFERRED_TO_EXECUTION = frozenset({"SIZING_ENTRY_PRICE_UNKNOWN"})


def signal_is_long(decision: DEC.Decision) -> bool:
    """Read a LONG signal out of an engine decision taken without an entry price.

    I1 moves sizing to execution, so a decision built with `entry_reference_price=None` reports
    HOLD for the sizing reason alone. That single deferral is not a rejection of the signal; any
    other blocking reason is.
    """
    if decision.decision == LONG:
        return True
    if not decision.filter_pass:
        return False
    return set(decision.reason_codes) <= (NON_BLOCKING | DEFERRED_TO_EXECUTION)


def oi_change(oi: np.ndarray, window_bars: int) -> np.ndarray:
    """`ln(OI[t] / OI[t-window])` using the D6-B primitives.

    At the contracted 60 bars this is `d6.features.f_oi1h` exactly, which
    `test_d6c_signals.py` asserts. The parameter exists only for the G8 sensitivity arms.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        ln_oi = np.log(np.asarray(oi, float))
    return F._clean(ln_oi - F._lag(ln_oi, window_bars))


def bucket_series(values: np.ndarray, ts: np.ndarray, contract: StrategyContract) -> np.ndarray:
    """B1..B5 per bar, 0 where the 30 day window was too sparse to form cutoffs.

    Cutoffs come from `d6.features.bucket_cutoffs`, the same function the engine calls, once per
    UTC day. The assignment is `searchsorted(..., side="right")`, which is what
    `d6.features.assign_bucket` does for a single value; the vectorised form is asserted equal to
    the scalar one in the tests.
    """
    window_days = contract.bucket_window_days
    window_bars = window_days * DAY_BARS
    min_fraction = contract.bound["insufficient_history"]["min_valid_fraction"]
    quantiles = contract.bucket_quantiles

    out = np.zeros(len(values), dtype=np.int8)
    first_day = int(ts[0] // (DAY_BARS * MINUTE_MS))
    last_day = int(ts[-1] // (DAY_BARS * MINUTE_MS))
    for day in range(first_day + window_days, last_day + 1):
        start_ms = (day - window_days) * DAY_BARS * MINUTE_MS
        end_ms = day * DAY_BARS * MINUTE_MS
        next_ms = (day + 1) * DAY_BARS * MINUTE_MS
        lo = int(np.searchsorted(ts, start_ms, side="left"))
        hi = int(np.searchsorted(ts, end_ms, side="left"))
        seg_hi = int(np.searchsorted(ts, next_ms, side="left"))
        cutoffs, _ = F.bucket_cutoffs(values[lo:hi], quantiles, min_fraction, window_bars)
        if cutoffs is None:
            continue
        seg = values[hi:seg_hi]
        assigned = np.searchsorted(cutoffs, seg, side="right") + 1
        assigned[~np.isfinite(seg)] = 0
        out[hi:seg_hi] = assigned
    return out


def _component_points(component: dict, bucket: np.ndarray, value: np.ndarray,
                      vol_label: np.ndarray) -> np.ndarray:
    """One category's points, driven by the contract's own level table.

    The precedence mirrors `d6.score`: a named bucket level wins; failing that, the `negative`
    level applies where the contract grants one (only `positioning` has it); failing that,
    `other`. A bar with no bucket falls straight past the first test, which is why the sign-based
    award is assigned before the bucket awards overwrite it.
    """
    levels = component["levels"]
    if component["input"] == "f_rv24h":
        points = np.zeros(len(value), dtype=np.int16)
        for label, code in (("LOW", 0), ("MID", 1), ("HIGH", 2)):
            points[vol_label == code] = int(levels[label])
        return points

    points = np.full(len(value), int(levels["other"]), dtype=np.int16)
    if "negative" in levels:
        points[np.isfinite(value) & (value < 0)] = int(levels["negative"])
    for label, award in levels.items():
        if label.startswith("B") and label[1:].isdigit():
            points[bucket == int(label[1:])] = int(award)
    return points


@dataclass
class SignalPass:
    """Per-bar decision inputs and the bar-level part of the entry test."""

    ts: np.ndarray
    features: dict[str, np.ndarray]
    buckets: dict[str, np.ndarray]
    vol_label: np.ndarray            # 0 LOW, 1 MID, 2 HIGH, -1 unknown
    category_points: dict[str, np.ndarray]
    long_score: np.ndarray
    stop_distance: np.ndarray
    bar_eligible: np.ndarray         # every bar-level gate passed and the score cleared
    block_reason: np.ndarray         # first bar-level reason that blocked, "" when eligible


def build(grid: dict[str, np.ndarray], contract: StrategyContract, *,
          score_threshold: int | None = None, stop_k: float | None = None,
          oi_window_min: int = 60) -> SignalPass:
    """Everything a decision needs that depends only on the bar."""
    ts = grid["ts"]
    threshold = contract.long_entry_min_score if score_threshold is None else score_threshold
    multiplier = contract.stop["sigma_multiplier"] if stop_k is None else stop_k

    features = {
        "f_basis": F.f_basis(grid["close"], grid["index_close"]),
        "f_oi1h": oi_change(grid["oi"], oi_window_min),
        "f_drop1h": F.f_drop1h(grid["close"]),
        "f_rv24h": F.f_rv24h(grid["close"]),
    }
    buckets = {name: bucket_series(features[name], ts, contract)
               for name in contract.bucketed_features}

    rv = features["f_rv24h"]
    vol_label = np.full(len(ts), -1, dtype=np.int8)
    finite = np.isfinite(rv)
    vol_label[finite] = 1
    vol_label[finite & (rv > contract.vol_high_above)] = 2
    vol_label[finite & (rv < contract.vol_low_below)] = 0

    category_points: dict[str, np.ndarray] = {}
    for component in contract.score_components:
        source = component["input"]
        category_points[component["name"]] = _component_points(
            component, buckets.get(source, np.zeros(len(ts), dtype=np.int8)),
            features[source], vol_label)
    long_score = sum(category_points.values()).astype(np.int16)

    stop = contract.stop
    with np.errstate(invalid="ignore"):
        raw = multiplier * rv * np.sqrt(stop["horizon_bars"])
        stop_distance = np.clip(raw, stop["dist_min"], stop["dist_max"])
    stop_distance[~np.isfinite(rv)] = np.nan

    # Bar-level hard filters. The state-dependent ones (H8..H11, H13) belong to the walk.
    reason = np.full(len(ts), "", dtype=object)

    def block(mask: np.ndarray, code: str) -> None:
        fresh = mask & (reason == "")
        reason[fresh] = code

    oi_age = (ts + MINUTE_MS) - grid["oi_record_ts"]
    block(grid["oi_record_ts"] < 0, "H2_OI_RECORD_TS_UNKNOWN")
    block(oi_age > contract.bound["oi_stale"]["max_age_ms"], "H2_OI_STALE")
    block(np.any([buckets[name] == 0 for name in buckets], axis=0), "H3_INSUFFICIENT_HISTORY")
    block(np.any([~np.isfinite(features[name]) for name in features], axis=0), "H4_FEATURE_NAN")
    block(vol_label < 0, "H5_VOL_LABEL_UNKNOWN")
    block(vol_label == 0, "H5_VOL_LOW")
    lead = grid["next_funding_ts"] - (ts + MINUTE_MS)
    block(grid["next_funding_ts"] < 0, "H12_NEXT_FUNDING_UNKNOWN")
    block(lead < contract.bound["funding_window"]["min_lead_ms"], "H12_FUNDING_WINDOW")

    # H1 is not tested here: in replay the decision instant *is* the bar close, so the bar age is
    # zero by construction. `verify_against_engine` re-runs the real filter set and would catch it
    # if that ever stopped being true.
    block(long_score < threshold, "SCORE_BELOW_THRESHOLD")
    for rule in contract.mandatory_minimums:
        block(category_points[rule["component"]] < int(rule["min"]),
              f"{rule['id']}_{rule['component'].upper()}_BELOW_MIN")

    return SignalPass(ts=ts, features=features, buckets=buckets, vol_label=vol_label,
                      category_points=category_points, long_score=long_score,
                      stop_distance=stop_distance, bar_eligible=(reason == ""),
                      block_reason=reason)


def verify_against_engine(grid: dict[str, np.ndarray], contract: StrategyContract,
                          signals: SignalPass, indices: np.ndarray, *,
                          lookback_bars: int) -> list[str]:
    """Re-run the real engine at the given bars and report any disagreement.

    The engine is called the way I1 prescribes, with no entry price, so a LONG signal shows up as
    `signal_is_long` rather than as `decision == LONG`.
    """
    problems: list[str] = []
    for index in indices:
        index = int(index)
        lo = max(0, index - lookback_bars + 1)
        window = DEC.BarWindow(ts_ms=grid["ts"][lo:index + 1], close=grid["close"][lo:index + 1],
                               index_close=grid["index_close"][lo:index + 1],
                               oi=grid["oi"][lo:index + 1])
        oi_ts = int(grid["oi_record_ts"][index])
        funding_ts = int(grid["next_funding_ts"][index])
        market = MarketContext(now_ms=window.bar_close_ms,
                               oi_record_ts_ms=None if oi_ts < 0 else oi_ts,
                               next_funding_ts_ms=None if funding_ts < 0 else funding_ts,
                               entry_reference_price=None)
        decision = DEC.decide(contract, window, market, StrategyState(equity=10_000.0),
                              mode=HISTORICAL)
        expected = bool(signals.bar_eligible[index])
        actual = signal_is_long(decision)
        if expected != actual:
            problems.append(f"bar {index} ts={int(grid['ts'][index])}: "
                            f"vectorised={expected} engine={actual} "
                            f"reasons={decision.reason_codes}")
        if int(decision.long_score) != int(signals.long_score[index]):
            problems.append(f"bar {index}: score vectorised={int(signals.long_score[index])} "
                            f"engine={decision.long_score}")
    return problems
