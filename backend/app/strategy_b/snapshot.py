"""Assemble one ``FeatureSnapshot`` from the pure feature functions."""

from collections.abc import Sequence
from datetime import datetime

from app.strategy_b.config import StrategyBConfig
from app.strategy_b.errors import PointInTimeViolation
from app.strategy_b.features import (
    SessionTape, clock_return, cumulative_dollar_volume, high_low_of_day, last_price,
    missing_minute_ratio, percent_distance, rolling_dollar_volume, session_vwap, tape_density,
    volume_acceleration,
)
from app.strategy_b.halt_inference import infer_halt
from app.strategy_b.models import CorporateActionFlag, FeatureSnapshot
from app.strategy_b.rvol import VolumeProfile, time_of_day_rvol
from app.strategy_b.session import ET
from app.strategy_b.split_adjustment import SplitRecord


def compute_feature_snapshot(
    tape: SessionTape,
    as_of: datetime,
    config: StrategyBConfig,
    *,
    rvol_history: Sequence[VolumeProfile] = (),
    splits: Sequence[SplitRecord] = (),
    corporate_action_flags: frozenset[CorporateActionFlag] = frozenset(),
) -> FeatureSnapshot:
    """Everything B may know about ``tape.symbol`` at ``as_of``.

    ``corporate_action_flags`` comes from ``corporate_actions.derive_corporate_action_flags``
    for the tape's date. ``rvol_history`` and ``splits`` are PIT-checked inside ``rvol``.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if as_of.astimezone(ET).date() != tape.boundaries.session_date:
        raise PointInTimeViolation(
            f"as_of {as_of.isoformat()} is not on the tape's session date {tape.boundaries.session_date}")
    f = config.features
    price, price_bar = last_price(tape, as_of, f.return_scope)
    vwap = session_vwap(tape, as_of, f.vwap_scope)
    hod, lod = high_low_of_day(tape, as_of, f.hod_scope)
    missing_ratio = missing_minute_ratio(tape, as_of, f.density_scope)
    rvol = time_of_day_rvol(tape, as_of, rvol_history, splits=splits, config=config.rvol)
    return FeatureSnapshot(
        symbol=tape.symbol,
        as_of=as_of,
        session=tape.boundaries.classify(as_of),
        price=price,
        price_age_seconds=None if price_bar is None else (as_of - price_bar.available_at).total_seconds(),
        return_1m=clock_return(tape, as_of, 1, f.return_scope),
        return_3m=clock_return(tape, as_of, 3, f.return_scope),
        return_5m=clock_return(tape, as_of, 5, f.return_scope),
        session_vwap=vwap,
        vwap_distance_pct=percent_distance(price, vwap),
        hod=hod,
        lod=lod,
        hod_distance_pct=percent_distance(price, hod),
        rolling_dollar_volume=rolling_dollar_volume(
            tape, as_of, f.rolling_dollar_volume_window_minutes, f.dollar_volume_scope,
            f.dollar_volume_basis),
        cumulative_dollar_volume=cumulative_dollar_volume(
            tape, as_of, f.dollar_volume_scope, f.dollar_volume_basis),
        volume_acceleration=volume_acceleration(
            tape, as_of, f.volume_acceleration_window_minutes, f.dollar_volume_scope),
        rvol=rvol.measured(),
        rvol_status=rvol.status,
        missing_minute_ratio=missing_ratio,
        sparse_status=tape_density(missing_ratio, f),
        halt_inferred=infer_halt(tape, as_of, config.halt).status,
        split_adjusted=rvol.split_adjusted,
        corporate_action_flags=frozenset(corporate_action_flags),
    )
