"""Unit tests for the C-4 contextual analog engine."""

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_c4_analog import audit, context, evaluate, features, labels4, neighbors
from app.backtest.strategy_c4_analog.rules import (DECLARED_RULES_CHECKSUM, RULES_PATH,
                                                   canonical_checksum, feature_names, load_rules)

PACKAGE = Path(__file__).resolve().parents[2] / "app/backtest/strategy_c4_analog"


def test_declaration_hashes_to_the_recorded_checksum():
    import json
    raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    assert canonical_checksum(raw) == DECLARED_RULES_CHECKSUM


def test_family_sizes_are_nested_and_declared():
    rules = load_rules()
    sizes = {family: len(feature_names(rules, family)) for family in ("F0", "F1", "F2", "F3")}
    assert sizes["F0"] < sizes["F1"] < sizes["F2"] < sizes["F3"]
    for smaller, larger in (("F0", "F1"), ("F1", "F2"), ("F2", "F3")):
        assert set(feature_names(rules, smaller)) <= set(feature_names(rules, larger))


def test_percentile_rank_is_zero_to_one_with_averaged_ties():
    values = np.array([[1.0, 2.0, 2.0, 4.0]])
    mask = np.ones((1, 4), dtype=bool)
    ranked = features.percentile_rank(values, mask)
    assert ranked[0, 0] == pytest.approx(0.0)
    assert ranked[0, 3] == pytest.approx(1.0)
    assert ranked[0, 1] == ranked[0, 2] == pytest.approx(1.5 / 3.0)


def test_percentile_rank_ignores_masked_and_missing_entries():
    values = np.array([[1.0, np.nan, 3.0, 5.0]])
    mask = np.array([[True, True, True, False]])
    ranked = features.percentile_rank(values, mask)
    assert np.isnan(ranked[0, 1]) and np.isnan(ranked[0, 3])
    assert ranked[0, 0] == pytest.approx(0.0) and ranked[0, 2] == pytest.approx(1.0)


def test_percentile_rank_of_one_session_cannot_see_another():
    values = np.array([[1.0, 2.0], [100.0, 200.0]])
    mask = np.ones((2, 2), dtype=bool)
    ranked = features.percentile_rank(values, mask)
    assert ranked[0].tolist() == ranked[1].tolist() == [0.0, 1.0]


def test_expanding_rank_series_reads_only_earlier_dates():
    values = np.array([0.0, 1.0, 2.0, 3.0, -5.0])
    ranked = features.expanding_rank_series(values, first=0)
    assert np.isnan(ranked[0])
    assert ranked[1] == pytest.approx(1.0)
    assert ranked[4] == pytest.approx(0.0)
    mutated = values.copy()
    mutated[4] = 99.0
    assert np.allclose(features.expanding_rank_series(mutated, first=0)[:4], ranked[:4],
                       equal_nan=True)


def test_expanding_rank_panel_pool_grows_only_with_past_rows():
    values = np.array([[1.0], [2.0], [0.0]])
    mask = np.ones((3, 1), dtype=bool)
    ranked = features.expanding_rank_panel(values, mask, first=0)
    assert np.isnan(ranked[0, 0])
    assert ranked[1, 0] == pytest.approx(1.0)
    assert ranked[2, 0] == pytest.approx(0.0)


def _index(dates, tickers, figis=None, dims=2):
    matrix = np.tile(np.arange(len(dates), dtype=np.float32)[:, None], (1, dims))
    figis = np.full(len(dates), -1, dtype=np.int32) if figis is None else np.asarray(figis)
    return neighbors.build_index(matrix, np.asarray(dates), np.asarray(tickers), figis)


def test_cut_excludes_every_analog_inside_the_embargo():
    index = _index([0, 5, 10, 20, 30], [1, 2, 3, 4, 5])
    cut = neighbors.cut_for(index, query_date_idx=30, embargo=20)
    assert cut == 3
    assert index.date_idx[:cut].max() <= 10


def test_search_respects_ticker_and_date_caps_and_drops_the_query_entity():
    dates = [0, 0, 0, 1, 1, 2]
    tickers = [7, 7, 8, 9, 10, 11]
    index = _index(dates, tickers, dims=1)
    query = np.zeros((1, 1), dtype=np.float32)
    picks = neighbors.search_date(index, query, [7], [-1], cut=index.size, top_k=3,
                                  ticker_cap=1, date_cap=1)[0]
    chosen_tickers = index.ticker_col[picks]
    chosen_dates = index.date_idx[picks]
    assert 7 not in chosen_tickers.tolist()
    assert len(set(chosen_tickers.tolist())) == len(picks)
    assert len(set(chosen_dates.tolist())) == len(picks)


def test_two_share_classes_of_one_issuer_cannot_both_be_analogs():
    index = _index([0, 1, 2], [5, 6, 7], figis=[42, 42, 43], dims=1)
    query = np.zeros((1, 1), dtype=np.float32)
    picks = neighbors.search_date(index, query, [99], [-1], cut=3, top_k=3, ticker_cap=1,
                                  date_cap=5, figi_cap=1)[0]
    codes = index.figi[picks]
    assert len(set(codes.tolist())) == len(picks)
    assert len(picks) == 2


def test_search_drops_a_different_ticker_sharing_the_query_figi():
    index = _index([0, 1], [5, 6], figis=[42, 43], dims=1)
    query = np.zeros((1, 1), dtype=np.float32)
    picks = neighbors.search_date(index, query, [99], [42], cut=2, top_k=2, ticker_cap=1, date_cap=5)[0]
    assert index.ticker_col[picks].tolist() == [6]


def test_search_returns_nearest_first():
    matrix = np.array([[0.9], [0.1], [0.5]], dtype=np.float32)
    index = neighbors.build_index(matrix, np.array([0, 1, 2]), np.array([1, 2, 3]),
                                  np.full(3, -1, dtype=np.int32))
    query = np.array([[0.0]], dtype=np.float32)
    picks = neighbors.search_date(index, query, [99], [-1], cut=3, top_k=3, ticker_cap=1, date_cap=5)[0]
    assert index.matrix[picks].ravel() == pytest.approx([0.1, 0.5, 0.9], abs=1e-6)


def test_window_any_covers_the_session_window_and_nothing_later():
    flags = np.array([[False, True, False, False, False]])
    assert context._window_any(flags, 2)[0].tolist() == [False, True, True, True, False]


def test_window_sum_counts_only_the_window():
    values = np.array([[1, 0, 2, 0, 0]])
    assert context._window_sum(values, 2)[0].tolist() == [1, 1, 3, 2, 2]


def test_calendar_window_any_uses_real_dates():
    sessions = [date(2025, 1, 1) + timedelta(days=30 * i) for i in range(4)]
    flags = np.array([[True, False, False, False]])
    got = context._calendar_window_any(flags, sessions, days=45)[0].tolist()
    assert got == [True, True, False, False]


def test_revenue_bucket_is_ordinal_and_bounded():
    yoy = np.array([[-0.5, 0.02, 0.07, 0.2, 0.3, 0.9, np.nan]])
    bucket = context._bucket_of(yoy)[0]
    assert bucket[:6].tolist() == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert np.isnan(bucket[6])


def test_cell_excess_subtracts_the_matched_cell_and_falls_back_on_thin_cells():
    class Labels:
        close_return = {5: np.array([[0.10, 0.20, 0.30, 0.90]])}
    values = {"price_bucket": np.array([[0.0, 0.0, 0.0, 1.0]]),
              "atr_bucket": np.zeros((1, 4)), "adv20_bucket": np.zeros((1, 4))}
    population = np.ones((1, 4), dtype=bool)
    out = labels4.build(Labels(), values, population, 5, min_members=3)
    assert out.excess[0, 0] == pytest.approx(0.10 - 0.20)
    assert out.cell_matched[0, :3].tolist() == [True, True, True]
    assert out.cell_matched[0, 3] is np.False_ or not out.cell_matched[0, 3]
    assert out.excess[0, 3] == pytest.approx(0.90 - 0.375)


def test_spearman_is_one_for_a_monotone_map_and_minus_one_for_a_reversed_one():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert evaluate.spearman(a, a ** 3) == pytest.approx(1.0)
    assert evaluate.spearman(a, -a) == pytest.approx(-1.0)


def test_daily_ic_skips_sessions_below_the_declared_minimum():
    forecast = np.arange(12, dtype=float)
    realized = np.arange(12, dtype=float)
    dates = np.array([1] * 10 + [2] * 2)
    series = evaluate.daily_ic(forecast, realized, dates, min_rows=8)
    assert series.dates.tolist() == [1]


def test_quintiles_are_balanced_and_ordered():
    values = np.arange(20, dtype=float)
    bucket = evaluate.quintile_of(values, np.zeros(20))
    assert np.bincount(bucket).tolist() == [4, 4, 4, 4, 4]
    assert bucket[0] == 0 and bucket[-1] == 4


def test_block_draws_are_deterministic_for_a_seed():
    first = evaluate.block_draws(50, block_length=10, replicates=100, seed=20260921)
    second = evaluate.block_draws(50, block_length=10, replicates=100, seed=20260921)
    assert np.array_equal(first, second)
    assert first.shape == (100, 50)
    assert first.min() >= 0 and first.max() < 50


def test_neighbor_audit_counts_every_declared_violation():
    log = audit.AuditLog()
    audit.check_neighbors(log, query_date=100, embargo=20, query_ticker=5, query_figi=7,
                          neighbor_dates=np.array([120, 90, 80, 80, 80, 80, 80, 80]),
                          neighbor_tickers=np.array([1, 2, 5, 3, 3, 4, 6, 8]),
                          neighbor_figis=np.array([-1, -1, 7, -1, -1, -1, -1, -1]),
                          ticker_cap=1, date_cap=5, max_horizon=10)
    assert log.get("E1_future_analog") == 1
    assert log.get("E2_embargo_violation") == 2
    assert log.get("E3_entity_cap_violation") >= 3
    assert log.get("E4_date_cap_violation") == 1
    assert log.get("E6_label_leakage") >= 1


def test_engineering_gate_requires_every_check():
    log = audit.AuditLog()
    log.note("E7_determinism", True)
    log.note("E8_candidate_match", True)
    assert audit.engineering_pass(log)
    log.add("E2_embargo_violation", 1)
    assert not audit.engineering_pass(log)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_feature_code_never_imports_a_label_module():
    names = _imports(PACKAGE / "features.py")
    assert not any("label" in name for name in names)


def test_the_package_never_imports_strategy_d():
    for path in PACKAGE.glob("*.py"):
        assert not any("strategy_d" in name for name in _imports(path)), path.name


def test_the_package_never_writes_to_a_frozen_store():
    for path in PACKAGE.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        for frozen in ("data/runtime/strategy_c/e0/raw", "data/runtime/strategy_eqm/v0/raw"):
            for writer in (".write_text(", ".write_bytes(", "savez"):
                assert f'"{frozen}"{writer}' not in body


def test_acceptance_zone_is_the_audited_utc_reading():
    assert load_rules().acceptance_zone == "UTC"


def test_declared_window_is_derived_from_the_declared_parts():
    rules = load_rules()
    assert rules.first_query_idx == (rules.library_start_idx + rules.min_library_dates
                                     + rules.embargo_sessions)


def test_obv_return_is_bounded_by_one():
    rng = np.random.default_rng(3)
    price = np.cumprod(1 + rng.normal(0, 0.02, size=(60, 4))) .reshape(60, 4)
    volume = rng.uniform(1e5, 1e6, size=(60, 4))
    step = np.sign(np.diff(price, axis=0, prepend=price[:1])) * volume
    obv = np.cumsum(step, axis=0)
    traded = np.cumsum(volume, axis=0)
    ratio = (obv[10:] - obv[:-10]) / (traded[10:] - traded[:-10])
    assert np.all(np.abs(ratio) <= 1.0 + 1e-9)


def test_acceptance_datetime_is_read_as_utc_by_the_frozen_contract():
    from app.backtest.strategy_c_e0.pit import parse_acceptance
    moment = parse_acceptance("2022-11-08T21:10:31Z", "UTC")
    assert moment.astimezone(timezone.utc) == datetime(2022, 11, 8, 21, 10, 31, tzinfo=timezone.utc)
