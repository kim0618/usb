"""The vectorised prefilter must agree with the pure layer, minute by minute.

The engine only asks the pure layer about minutes this filter keeps, so a minute it drops is
a minute that can never produce a candidate. That makes this the one test standing between a
fast run and a quietly different study.
"""

from datetime import timedelta
import random

import pytest

from app.backtest.strategy_b.prefilter import cheap_gate_minutes
from app.strategy_b.config import DollarVolumePriceBasis, StrategyBConfig
from app.strategy_b.features import SessionTape, clock_return, rolling_dollar_volume
from app.strategy_b.models import Availability, MomentumBar, Session
from app.strategy_b.session import AggregationScope
from tests.strategy_b.fixtures import D, SYMBOL, boundaries, et, make_bar, make_tape

CONFIG = StrategyBConfig()
LEGS = ((1, CONFIG.scanner.return_1m_threshold), (3, CONFIG.scanner.return_3m_threshold),
        (5, CONFIG.scanner.return_5m_threshold))


def ticks(first=(9, 36), last=(15, 56)):
    """Availability moments across the regular session, the grid the engine walks."""
    start, end = et(*first), et(*last)
    count = int((end - start) / timedelta(minutes=1)) + 1
    return [start + timedelta(minutes=k) for k in range(count)]


def pure_cheap_verdict(tape: SessionTape, as_of) -> bool:
    """The gate with only the conditions the prefilter knows about (B-F0 4.1, cheap half)."""
    features = CONFIG.features
    leg = False
    for minutes, threshold in LEGS:
        measured = clock_return(tape, as_of, minutes, features.return_scope)
        leg |= measured.status is Availability.AVAILABLE and measured.value >= threshold
    dollar = rolling_dollar_volume(tape, as_of, features.rolling_dollar_volume_window_minutes,
                                   features.dollar_volume_scope, features.dollar_volume_basis)
    return leg and dollar.status is Availability.AVAILABLE and \
        dollar.value >= CONFIG.scanner.min_dollar_volume


def assert_agrees(bars) -> int:
    tape = make_tape(list(bars))
    grid = ticks()
    kept = set(cheap_gate_minutes(SYMBOL, tape.bars, grid, boundaries=boundaries(),
                                  scanner=CONFIG.scanner, features=CONFIG.features).minutes)
    expected = {tick for tick in grid if pure_cheap_verdict(tape, tick)}
    assert kept == expected
    return len(expected)


# ---- random tapes --------------------------------------------------------------------------

def random_bars(seed: int, *, density: float, spike: bool, premarket: bool):
    """A session that trades on a random subset of minutes, optionally with a real spike."""
    rng = random.Random(seed)
    bars, price = [], 10.0
    start = et(4, 0) if premarket else et(9, 30)
    minutes = int((et(15, 59) - start) / timedelta(minutes=1))
    for step in range(minutes):
        stamp = start + timedelta(minutes=step)
        if rng.random() > density:
            continue
        move = rng.gauss(0, 0.002)
        if spike and rng.random() < 0.02:
            move += rng.choice((0.03, -0.03))
        price = max(0.5, price * (1 + move))
        volume = rng.choice((0.0, 200.0, 5_000.0, 90_000.0))
        high = price * (1 + abs(rng.gauss(0, 0.001)))
        low = price * (1 - abs(rng.gauss(0, 0.001)))
        bars.append(MomentumBar(stamp, price, max(high, price), min(low, price), price, volume,
                                boundaries().classify(stamp), vwap=price, transactions=3.0))
    return bars


@pytest.mark.parametrize("seed", range(6))
def test_the_prefilter_matches_the_pure_layer_on_random_tapes(seed: int) -> None:
    density = (1.0, 0.6, 0.25, 0.08)[seed % 4]
    survivors = assert_agrees(random_bars(seed, density=density, spike=True,
                                          premarket=bool(seed % 2)))
    assert survivors >= 0


def test_edge_tapes_agree_too() -> None:
    assert assert_agrees(()) == 0
    assert assert_agrees([make_bar(9, 30, 10.0, 1_000_000.0)]) == 0  # one bar, no reference
    assert assert_agrees([make_bar(15, 58, 10.0, 1_000_000.0)]) == 0  # everything after the grid
    premarket_only = [make_bar(8, 0, 10.0, 900_000.0), make_bar(9, 0, 12.0, 900_000.0)]
    assert assert_agrees(premarket_only) == 0  # session-local dollar volume stays empty


def test_a_real_gate_minute_survives_the_filter() -> None:
    """A +3% minute on heavy volume must be kept, or the engine would never see it."""
    bars = [make_bar(9, 30 + k, 10.0, 60_000.0, open_=10.0, high=10.01, low=9.99) for k in range(6)]
    bars.append(make_bar(9, 36, 10.35, 120_000.0, open_=10.01, high=10.40, low=10.00))
    tape = make_tape(bars)
    result = cheap_gate_minutes(SYMBOL, tape.bars, ticks(), boundaries=boundaries(),
                                scanner=CONFIG.scanner, features=CONFIG.features)
    assert et(9, 37) in result.minutes
    assert pure_cheap_verdict(tape, et(9, 37))


def test_the_filter_refuses_a_configuration_it_does_not_implement() -> None:
    from dataclasses import replace
    tape = make_tape([make_bar(9, 30, 10.0, 1000.0)])
    for features in (replace(CONFIG.features, dollar_volume_basis=DollarVolumePriceBasis.SOURCE_VWAP),
                     replace(CONFIG.features, return_scope=AggregationScope.SESSION_LOCAL),
                     replace(CONFIG.features, dollar_volume_scope=AggregationScope.EXTENDED_DAY)):
        with pytest.raises(ValueError):
            cheap_gate_minutes(SYMBOL, tape.bars, ticks(), boundaries=boundaries(),
                               scanner=CONFIG.scanner, features=features)
    with pytest.raises(ValueError):
        cheap_gate_minutes(SYMBOL, tape.bars, [et(8, 0)], boundaries=boundaries(),
                           scanner=CONFIG.scanner, features=CONFIG.features)
