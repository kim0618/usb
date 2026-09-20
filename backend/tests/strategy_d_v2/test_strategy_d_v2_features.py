"""The ten coordinates, the rank frame and B0: formulas pinned against plain-Python references.

Nothing here compares the implementation to itself. Each coordinate is recomputed with an
explicit loop over the declared window, so a refactor that changes a window edge, a denominator
or a degrees-of-freedom setting fails the test instead of silently redefining the study.
"""

import math

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import with_changes
from app.backtest.strategy_d_v2 import b0_composite, structure_encoder, structure_features
from app.backtest.strategy_d_v2.config import FEATURE_NAMES, load_rules
from app.backtest.strategy_d_v2.models import HardFail, VECTOR_STATUS_ORDER, VectorStatus
from tests.strategy_d_v2 import fixtures

RULES = load_rules()
TOLERANCE = 1e-12


@pytest.fixture(scope="module")
def panel():
    return fixtures.make_panel(days=120)


@pytest.fixture(scope="module")
def built(panel):
    return structure_features.build(panel, fixtures.eligible_all(panel), RULES)


def _adjusted(panel, column):
    factor, _, _ = panel.split_arrays()
    return {"price": (panel.close / factor)[:, column],
            "high": (panel.high / factor)[:, column],
            "low": (panel.low / factor)[:, column],
            "volume": panel.volume[:, column],
            "dollar": (panel.close * panel.volume)[:, column]}


def _reference_true_range(series, t):
    if t == 0:
        return float("nan")
    previous = series["price"][t - 1]
    return max(series["high"][t] - series["low"][t],
               abs(series["high"][t] - previous), abs(series["low"][t] - previous))


def reference(series, name, t):
    """The declared formula, written out. Deliberately slow and deliberately literal."""
    price, high, low = series["price"], series["high"], series["low"]
    if name == "return_5":
        return price[t] / price[t - 5] - 1.0
    if name == "return_20":
        return price[t] / price[t - 20] - 1.0
    if name == "return_60":
        return price[t] / price[t - 60] - 1.0
    if name == "dist_to_20d_high":
        return price[t] / max(high[t - 19:t + 1]) - 1.0
    if name == "position_in_60d_range":
        top, bottom = max(high[t - 59:t + 1]), min(low[t - 59:t + 1])
        return (price[t] - bottom) / (top - bottom)
    if name == "rv_20":
        steps = [math.log(price[s] / price[s - 1]) for s in range(t - 19, t + 1)]
        mean = sum(steps) / len(steps)
        return math.sqrt(sum((x - mean) ** 2 for x in steps) / (len(steps) - 1))
    if name == "atr_ratio_20_60":
        short = sum(_reference_true_range(series, s) for s in range(t - 19, t + 1)) / 20
        long = sum(_reference_true_range(series, s) for s in range(t - 59, t + 1)) / 60
        return short / long
    if name == "tr_today_ratio":
        short = sum(_reference_true_range(series, s) for s in range(t - 19, t + 1)) / 20
        return _reference_true_range(series, t) / short
    if name == "rvol_today":
        base = sum(series["volume"][t - 20:t]) / 20
        return series["volume"][t] / base
    if name == "dollar_volume_ratio_20_60":
        short = sum(series["dollar"][t - 19:t + 1]) / 20
        long = sum(series["dollar"][t - 59:t + 1]) / 60
        return short / long
    raise AssertionError(name)


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_formula_matches_reference(panel, built, name):
    """Each coordinate equals the declared formula recomputed by hand, on every ticker."""
    for column in range(len(panel.tickers)):
        series = _adjusted(panel, column)
        for t in (60, 77, 95, 119):
            expected = reference(series, name, t)
            found = built.raw[name][t, column]
            assert found == pytest.approx(expected, rel=1e-12, abs=1e-12), f"{name} t={t}"


def test_declared_formula_strings_cover_every_coordinate():
    """The declaration names the same ten coordinates, in the same order, with the same lookback."""
    assert tuple(f.name for f in RULES.features) == FEATURE_NAMES
    assert {f.name: f.lookback for f in RULES.features} == dict(structure_features.FEATURE_LOOKBACK)
    assert RULES.max_lookback == 60


def test_history_boundary_is_the_declared_lookback(panel, built):
    """No vector exists before session 60, and every session from 60 on has one."""
    assert not built.defined[:60].any()
    assert built.defined[60:].all()
    early = built.status[59]
    assert set(np.unique(early)) <= {VECTOR_STATUS_ORDER.index(VectorStatus.NOT_ELIGIBLE),
                                     VECTOR_STATUS_ORDER.index(VectorStatus.INSUFFICIENT_HISTORY)}


def test_return_60_reads_exactly_sixty_sessions_back(panel, built):
    """Changing session D-60 moves ``return_60``; changing D-61 does not."""
    for offset, should_change in ((60, True), (61, False)):
        moved = panel.close.copy()
        moved[100 - offset, 0] *= 1.5
        other = with_changes(panel, close=moved)
        raw, _, _ = structure_features.compute_raw(other)
        changed = bool(raw["return_60"][100, 0] != built.raw["return_60"][100, 0])
        assert changed == should_change, f"offset {offset}"


def test_volume_windows_stay_inside_the_split_protection(panel, built):
    """``rvol_today`` reads D-20..D-1 and the dollar ratio D-59..D: both inside (D-60, D]."""
    for offset, name in ((20, "rvol_today"), (21, "rvol_today"),
                         (59, "dollar_volume_ratio_20_60"), (60, "dollar_volume_ratio_20_60")):
        moved = panel.volume.copy()
        moved[100 - offset, 0] *= 3.0
        other = with_changes(panel, volume=moved)
        raw, _, _ = structure_features.compute_raw(other)
        changed = bool(raw[name][100, 0] != built.raw[name][100, 0])
        expected = offset <= (20 if name == "rvol_today" else 59)
        assert changed == expected, f"{name} at offset {offset}"


def test_todays_bar_is_included_where_declared(panel, built):
    """``tr_today_ratio`` and ``rvol_today`` use session D itself; both move when D moves."""
    moved_volume = panel.volume.copy()
    moved_volume[100, 0] *= 2.0
    raw, _, _ = structure_features.compute_raw(with_changes(panel, volume=moved_volume))
    assert raw["rvol_today"][100, 0] != built.raw["rvol_today"][100, 0]
    moved_high = panel.high.copy()
    moved_high[100, 0] *= 1.05
    raw, _, _ = structure_features.compute_raw(with_changes(panel, high=moved_high))
    assert raw["tr_today_ratio"][100, 0] != built.raw["tr_today_ratio"][100, 0]


def test_incomplete_current_bar_has_no_vector(panel):
    """A session whose bar is not final cannot produce a signal input (available_at contract)."""
    for field in ("open", "high", "low", "close", "volume"):
        values = getattr(panel, field).copy()
        values[100, 0] = np.nan
        other = with_changes(panel, **{field: values})
        built = structure_features.build(other, fixtures.eligible_all(other), RULES)
        assert not built.defined[100, 0], field
        status = VECTOR_STATUS_ORDER[built.status[100, 0]]
        assert status in (VectorStatus.INCOMPLETE_BAR, VectorStatus.NOT_ELIGIBLE), field


def test_flat_series_is_a_zero_denominator_not_a_number():
    """A 60-session flat range has no position coordinate; the window is dropped, not imputed."""
    panel = fixtures.make_panel(days=120)
    flat_close = panel.close.copy()
    flat_high = panel.high.copy()
    flat_low = panel.low.copy()
    flat_close[:, 0] = 100.0
    flat_high[:, 0] = 100.0
    flat_low[:, 0] = 100.0
    other = with_changes(panel, close=flat_close, high=flat_high, low=flat_low)
    built = structure_features.build(other, fixtures.eligible_all(other), RULES)
    assert built.raw["return_20"][100, 0] == 0.0
    assert built.raw["dist_to_20d_high"][100, 0] == 0.0
    assert math.isnan(built.raw["position_in_60d_range"][100, 0])
    assert not built.defined[100, 0]
    assert VECTOR_STATUS_ORDER[built.status[100, 0]] is VectorStatus.ZERO_DENOMINATOR
    counts = built.zero_denominator_counts()["position_in_60d_range"]
    assert counts["eligible"] > 0 and counts["panel"] >= counts["eligible"]


def test_rank_is_the_declared_percentile_formula(panel, built):
    """``(average rank - 1)/(n - 1)`` over the date's frame, for every coordinate."""
    row = 100
    frame = np.nonzero(built.defined[row])[0]
    assert frame.size == len(panel.tickers)
    for name in FEATURE_NAMES:
        values = built.raw[name][row, frame]
        order = np.argsort(values, kind="stable")
        expected = np.empty(frame.size)
        expected[order] = np.arange(1, frame.size + 1)
        expected = (expected - 1.0) / (frame.size - 1)
        np.testing.assert_allclose(built.rank[name][row, frame], expected, atol=TOLERANCE)
        assert built.rank[name][row, frame].min() == 0.0
        assert built.rank[name][row, frame].max() == 1.0


def test_rank_ties_take_the_average_rank(panel):
    """Two identical coordinate values get the same percentile; ticker order never breaks a tie."""
    close = panel.close.copy()
    close[:, 1] = close[:, 0]           # BBB's path becomes AAA's
    high, low, open_ = panel.high.copy(), panel.low.copy(), panel.open.copy()
    volume = panel.volume.copy()
    for array in (high, low, open_, volume):
        array[:, 1] = array[:, 0]
    other = with_changes(panel, close=close, high=high, low=low, open=open_, volume=volume)
    built = structure_features.build(other, fixtures.eligible_all(other), RULES)
    for name in FEATURE_NAMES:
        assert built.raw[name][100, 0] == built.raw[name][100, 1], name
        assert built.rank[name][100, 0] == built.rank[name][100, 1], name


def test_rank_ignores_names_without_a_vector(panel):
    """A NaN coordinate is not ranked, and does not consume a rank slot of the names that are."""
    close = panel.close.copy()
    close[100, 4] = np.nan
    other = with_changes(panel, close=close)
    built = structure_features.build(other, fixtures.eligible_all(other), RULES)
    assert not built.defined[100, 4]
    frame = np.nonzero(built.defined[100])[0]
    assert frame.size == len(panel.tickers) - 1
    for name in FEATURE_NAMES:
        assert math.isnan(built.rank[name][100, 4])
        assert built.rank[name][100, frame].max() == 1.0


def test_rank_invariance_to_a_market_wide_constant(panel, built):
    """The reason relative strength is not a coordinate: rank(x - c) == rank(x) on every date.

    This is the contract that stops a duplicated coordinate from entering the vector, so it is a
    test rather than a comment.
    """
    rng = np.random.default_rng(3)
    for row in (60, 80, 100, 119):
        frame = np.nonzero(built.defined[row])[0]
        market = float(rng.normal(0.0, 0.05))
        for name in ("return_5", "return_20", "return_60"):
            shifted = built.raw[name][row].copy()
            shifted[frame] -= market
            values = built.raw[name][row, frame]
            order_plain = np.argsort(values, kind="stable")
            order_shift = np.argsort(shifted[frame], kind="stable")
            np.testing.assert_array_equal(order_plain, order_shift)
            expected = np.empty(frame.size)
            expected[order_plain] = np.arange(1, frame.size + 1)
            expected = (expected - 1.0) / (frame.size - 1)
            np.testing.assert_allclose(built.rank[name][row, frame], expected, atol=TOLERANCE)


def test_structure_vector_is_all_or_nothing(panel, built):
    """Ten coordinates or none; a partial vector is never imputed."""
    session = np.array([100, 100], dtype=np.int64)
    ticker = np.array([0, 1], dtype=np.int64)
    vectors = structure_encoder.encode(built, session, ticker)
    assert vectors.vectors.shape == (2, 10)
    assert vectors.defined.all()
    assert ((vectors.vectors >= 0.0) & (vectors.vectors <= 1.0)).all()

    close = panel.close.copy()
    close[100, 1] = np.nan
    other = with_changes(panel, close=close)
    partial = structure_features.build(other, fixtures.eligible_all(other), RULES)
    vectors = structure_encoder.encode(partial, session, ticker)
    assert not vectors.defined[1]
    assert np.isnan(vectors.vectors[1]).all()


def test_distance_is_plain_equal_weight_euclidean():
    left = np.array([0.0, 0.25, 0.5, 0.75, 1.0, 0.0, 0.25, 0.5, 0.75, 1.0])
    right = np.zeros(10)
    expected = math.sqrt(sum(x * x for x in left))
    assert structure_encoder.distance(left, right) == pytest.approx(expected, rel=1e-15)
    assert structure_encoder.distance(left, left) == 0.0
    with pytest.raises(HardFail):
        structure_encoder.distance(left[:9], right[:9])
    with pytest.raises(HardFail):
        structure_encoder.distance(np.full(10, np.nan), right)


def test_b0_uses_declared_signs_and_equal_weights(built):
    """B0 is the declared composite; STRONG/WEAK metadata never reaches a weight."""
    session = np.array([100, 100, 100], dtype=np.int64)
    ticker = np.array([0, 1, 2], dtype=np.int64)
    matrix = built.matrix(session, ticker)
    composite = b0_composite.build(matrix, RULES)
    signs = RULES.b0_sign_vector
    expected = np.array([sum(s * row[i] for i, s in enumerate(signs)) / 10.0 for row in matrix])
    np.testing.assert_allclose(composite.values, expected, atol=1e-15)
    assert composite.signs == signs
    assert signs == (-1, -1, 1, 1, 1, -1, -1, -1, -1, -1)

    strong = b0_composite.strong_only(matrix, RULES)
    assert len(RULES.b0_strong_names) == 6
    assert strong.values.shape == composite.values.shape
    assert strong.names == RULES.b0_strong_names


def test_b0_rejects_a_non_declared_sign_vector(built):
    matrix = built.matrix(np.array([100]), np.array([0]))
    with pytest.raises(HardFail):
        b0_composite.composite(matrix, (0, 1, 1, 1, 1, 1, 1, 1, 1, 1))
    with pytest.raises(HardFail):
        b0_composite.composite(matrix, (1, 1, 1))


def test_b0_cannot_see_a_label():
    """B0's only input is a rank matrix: there is no argument through which a label could arrive."""
    import ast
    import inspect
    assert list(inspect.signature(b0_composite.build).parameters) == ["rank_matrix", "rules"]
    assert list(inspect.signature(b0_composite.composite).parameters) == \
        ["rank_matrix", "signs", "names"]
    tree = ast.parse(inspect.getsource(b0_composite))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any("label" in (name or "") for name in imported)
    assert not any("strategy_d_analog" in (name or "") for name in imported)
