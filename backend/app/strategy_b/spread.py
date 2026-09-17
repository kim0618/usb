"""Inputs for a future synthetic spread model. No spread is estimated here.

Basic has no historical quotes, so a historical spread can only ever be modelled from trade
data. This module fixes what such a model may read, gathered PIT from the tape, and stops
there: ``SpreadFeatures`` deliberately has no ``estimated_spread_pct``. That model, its
calibration and its numbers are a separate research step.
"""

from dataclasses import dataclass
from datetime import datetime

from app.strategy_b.config import FeatureConfig, HaltInferenceConfig
from app.strategy_b.features import SessionTape, missing_minute_ratio
from app.strategy_b.halt_inference import infer_halt
from app.strategy_b.models import HaltStatus, Measured, Session


@dataclass(frozen=True, slots=True)
class SpreadFeatureInput:
    """What a spread model may see about the last actual minute available at ``as_of``."""

    as_of: datetime
    session: Session
    price: float
    minute_dollar_volume: float
    """``close × volume`` of this one bar in USD, always the close so it exists without a VWAP."""
    transactions: float | None
    high_low_range: float
    vwap_close_distance: float | None
    missing_minute_ratio: Measured
    halt_inferred: HaltStatus


@dataclass(frozen=True, slots=True)
class SpreadFeatures:
    """Scale-free transforms of ``SpreadFeatureInput``. Still not a spread estimate."""

    range_pct: float
    vwap_close_distance_pct: float | None
    dollar_volume_per_transaction: float | None
    missing_minute_ratio: Measured
    session: Session
    halt_inferred: HaltStatus


def spread_feature_input(tape: SessionTape, as_of: datetime, *, features: FeatureConfig,
                         halt: HaltInferenceConfig) -> SpreadFeatureInput | None:
    """None when no actual bar is available yet in the as-of session."""
    session = tape.boundaries.classify(as_of)
    start = tape.boundaries.session_start(session)
    now = tape.cut(as_of)
    if start is None or now == 0 or tape.bars[now - 1].timestamp < start:
        return None
    bar = tape.bars[now - 1]
    return SpreadFeatureInput(
        as_of=as_of,
        session=session,
        price=bar.close,
        minute_dollar_volume=bar.close * bar.volume,
        transactions=bar.transactions,
        high_low_range=bar.high - bar.low,
        vwap_close_distance=None if bar.vwap is None else bar.close - bar.vwap,
        missing_minute_ratio=missing_minute_ratio(tape, as_of, features.density_scope),
        halt_inferred=infer_halt(tape, as_of, halt).status,
    )


def spread_features(value: SpreadFeatureInput) -> SpreadFeatures:
    per_transaction = (value.minute_dollar_volume / value.transactions
                       if value.transactions else None)
    return SpreadFeatures(
        range_pct=value.high_low_range / value.price * 100,
        vwap_close_distance_pct=(None if value.vwap_close_distance is None
                                 else value.vwap_close_distance / value.price * 100),
        dollar_volume_per_transaction=per_transaction,
        missing_minute_ratio=value.missing_minute_ratio,
        session=value.session,
        halt_inferred=value.halt_inferred,
    )
