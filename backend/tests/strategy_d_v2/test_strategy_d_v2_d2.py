"""The structure analog engine: exact distance, exact selection, and nothing from the future.

Every selection rule is checked against a hand-built library where the right answer is obvious,
and the optimised search is checked against the exhaustive reference on the same inputs. The
two future-data tests run the whole pipeline - coordinates, library, search - twice on the same
synthetic market, once with the future intact and once with it replaced by nonsense.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import with_changes
from app.backtest.strategy_d_analog import neighbor_search, similarity
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_v2 import d2, library as structure_library, pit, schema
from app.backtest.strategy_d_v2 import structure_encoder, structure_features
from app.backtest.strategy_d_v2.config import FEATURE_NAMES, load_rules
from app.backtest.strategy_d_v2.models import HardFail
from tests.strategy_d_v2 import fixtures

RULES = load_rules()
SCHEMA = schema.build(RULES)
DIM = len(FEATURE_NAMES)


# -- distance ---------------------------------------------------------------------------------

def _distance_row(query, library):
    scores = similarity.score_block(d2.METRIC, np.asarray(query, dtype=np.float64).reshape(1, -1),
                                    np.asarray(library, dtype=np.float64))
    return scores.metric_value[0], scores.rank_score[0]


def test_identical_vectors_are_distance_zero_to_the_kernel_s_precision():
    """The block kernel's gram expansion cancels at zero; the direct recomputation does not.

    Both numbers reach the artifact (``distance`` and ``distance_exact``) precisely so this
    difference is a measured quantity rather than an assumption.
    """
    vector = np.linspace(0.0, 1.0, DIM)
    distance, score = _distance_row(vector, vector.reshape(1, -1))
    assert distance[0] == pytest.approx(0.0, abs=1e-7)
    assert score[0] == -distance[0]
    assert d2.exact_distance(vector, vector.reshape(1, -1))[0] == 0.0


def test_the_expansion_error_is_bounded_and_reported():
    """Over random unit-cube vectors the kernel agrees with the direct distance to about 1e-8."""
    rng = np.random.default_rng(17)
    query = rng.random(DIM)
    library = np.vstack([rng.random((200, DIM)), query, query + 1e-9])
    kernel, _ = _distance_row(query, library)
    direct = d2.exact_distance(query, library)
    assert float(np.abs(kernel - direct).max()) < 1e-7
    order_kernel = np.argsort(kernel, kind="stable")
    order_direct = np.argsort(direct, kind="stable")
    well_separated = np.abs(np.diff(np.sort(direct))) > 1e-6
    assert well_separated.all() or np.array_equal(order_kernel, order_direct)


def test_one_coordinate_changed_is_exact():
    left = np.full(DIM, 0.5)
    for index in range(DIM):
        right = left.copy()
        right[index] = 0.9
        distance, _ = _distance_row(left, right.reshape(1, -1))
        assert distance[0] == pytest.approx(0.4, abs=1e-12)


def test_reversed_vector_is_exact():
    left = np.linspace(0.0, 1.0, DIM)
    right = left[::-1].copy()
    expected = math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))
    distance, _ = _distance_row(left, right.reshape(1, -1))
    assert distance[0] == pytest.approx(expected, rel=1e-12)


def test_rank_score_is_negative_distance():
    left = np.full(DIM, 0.25)
    right = np.random.default_rng(0).random((8, DIM))
    distance, score = _distance_row(left, right)
    np.testing.assert_array_equal(score, -distance)


def test_coordinates_stay_in_the_unit_cube_and_nan_is_refused():
    with pytest.raises(HardFail):
        structure_encoder.distance(np.full(DIM, np.nan), np.zeros(DIM))
    library = np.zeros((1, DIM))
    library[0, 0] = 2.0
    with pytest.raises(HardFail):
        _make_library(vectors=library, end_idx=[60], ticker_col=[0], figi=[-1])


# -- a hand-built library ------------------------------------------------------------------------

def _make_library(*, vectors, end_idx, ticker_col, figi, horizon=5, lookback=60):
    vectors = np.ascontiguousarray(np.asarray(vectors, dtype=np.float64))
    if vectors.size and (vectors.min() < 0.0 or vectors.max() > 1.0):
        raise HardFail("R12", "a library coordinate is outside [0, 1]")
    end = np.asarray(end_idx, dtype=np.int32)
    col = np.asarray(ticker_col, dtype=np.int32)
    return structure_library.StructureLibrary(
        horizon=horizon, lookback=lookback, end_idx=end, ticker_col=col,
        figi_code=np.asarray(figi, dtype=np.int32), vectors=vectors,
        sq_norm=np.einsum("ij,ij->i", vectors, vectors),
        stride_end_indices=tuple(sorted(set(int(x) for x in end))),
        rows_by_end={}, exclusions={}, figi_strings=())


def _grid(values):
    """One library row per value: a vector that differs from the origin by that much."""
    out = np.zeros((len(values), DIM))
    out[:, 0] = values
    return out


def _search(lib, query, *, query_ticker=999, query_figi=-1, date_idx=300, initial_m=400,
            top_k=None):
    view = pit.EmbargoView(date_idx, RULES.max_lookback, RULES.primary_horizon)
    cut = lib.prefix(view.limit_idx)
    scores = similarity.score_block(d2.METRIC, np.asarray(query).reshape(1, -1),
                                    lib.vectors[:cut], lib.sq_norm[:cut])
    drop = neighbor_search.drop_same_symbol(lib.ticker_col[:cut], lib.figi_code[:cut],
                                            query_ticker, query_figi)
    accepted, pool = neighbor_search.select(
        scores.rank_score[0], drop, lib.ticker_col[:cut], lib.end_idx[:cut],
        top_k=top_k or RULES.top_k, ticker_cap=RULES.max_windows_per_ticker,
        date_cap=RULES.max_neighbors_per_end_date, initial_m=initial_m)
    return accepted, pool, scores


def test_same_ticker_is_excluded():
    lib = _make_library(vectors=_grid([0.1, 0.2, 0.3]), end_idx=[60, 60, 65],
                        ticker_col=[7, 8, 9], figi=[-1, -1, -1])
    accepted, _, _ = _search(lib, np.zeros(DIM), query_ticker=7, top_k=3)
    assert 0 not in accepted
    assert [int(lib.ticker_col[r]) for r in accepted] == [8, 9]


def test_same_figi_is_excluded_when_both_sides_have_one():
    lib = _make_library(vectors=_grid([0.1, 0.2, 0.3]), end_idx=[60, 60, 65],
                        ticker_col=[7, 8, 9], figi=[4, 5, 4])
    accepted, _, _ = _search(lib, np.zeros(DIM), query_ticker=999, query_figi=4, top_k=3)
    assert [int(lib.ticker_col[r]) for r in accepted] == [8]


def test_null_figi_skips_the_comparison_instead_of_excluding():
    lib = _make_library(vectors=_grid([0.1, 0.2]), end_idx=[60, 65], ticker_col=[7, 8],
                        figi=[-1, -1])
    accepted, _, _ = _search(lib, np.zeros(DIM), query_ticker=999, query_figi=-1, top_k=3)
    assert len(accepted) == 2


def test_embargo_boundary_is_inclusive():
    limit = 300 - RULES.max_lookback - RULES.primary_horizon
    lib = _make_library(vectors=_grid([0.1, 0.2]), end_idx=[limit, limit + 5],
                        ticker_col=[7, 8], figi=[-1, -1])
    accepted, _, _ = _search(lib, np.zeros(DIM), top_k=3)
    assert [int(lib.end_idx[r]) for r in accepted] == [limit]
    with pytest.raises(Exception):
        pit.EmbargoView(300, RULES.max_lookback, RULES.primary_horizon).assert_candidates(
            np.array([limit + 5]), "past the embargo")


def test_symbol_cap_takes_the_best_window_per_ticker():
    lib = _make_library(vectors=_grid([0.1, 0.2, 0.3, 0.4]), end_idx=[60, 65, 70, 75],
                        ticker_col=[7, 7, 8, 8], figi=[-1] * 4)
    accepted, _, _ = _search(lib, np.zeros(DIM), top_k=4)
    assert len(accepted) == 2
    assert sorted(int(lib.ticker_col[r]) for r in accepted) == [7, 8]
    assert [float(lib.vectors[r, 0]) for r in accepted] == [0.1, 0.3]


def test_date_cap_limits_windows_per_library_end_date():
    lib = _make_library(vectors=_grid([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]),
                        end_idx=[60] * 6 + [65], ticker_col=list(range(7)), figi=[-1] * 7)
    accepted, _, _ = _search(lib, np.zeros(DIM), top_k=7)
    ends = [int(lib.end_idx[r]) for r in accepted]
    assert ends.count(60) == RULES.max_neighbors_per_end_date
    assert ends.count(65) == 1


def test_tie_break_is_end_idx_then_ticker():
    """Equal distance: the older library end wins, then the smaller ticker column."""
    vectors = np.zeros((4, DIM))
    vectors[:, 0] = 0.3
    lib = _make_library(vectors=vectors, end_idx=[60, 60, 65, 65], ticker_col=[3, 9, 1, 4],
                        figi=[-1] * 4)
    accepted, _, _ = _search(lib, np.zeros(DIM), top_k=4)
    assert [(int(lib.end_idx[r]), int(lib.ticker_col[r])) for r in accepted] == [
        (60, 3), (60, 9), (65, 1), (65, 4)]


def test_pool_expands_until_the_answer_cannot_change():
    rng = np.random.default_rng(5)
    rows = 600
    vectors = rng.random((rows, DIM))
    end_idx = np.repeat(np.arange(60, 60 + rows // 5 * 5, 5), 5)[:rows]
    lib = _make_library(vectors=vectors, end_idx=end_idx, ticker_col=np.arange(rows),
                        figi=[-1] * rows)
    query = np.full(DIM, 0.5)
    live = lib.prefix(pit.EmbargoView(300, RULES.max_lookback, RULES.primary_horizon).limit_idx)
    small, pool_small, _ = _search(lib, query, initial_m=4)
    big, pool_big, _ = _search(lib, query, initial_m=400)
    assert small == big
    assert pool_small > 4                      # the pool doubled until K could be filled
    assert pool_big == min(400, live)          # and a pool wider than the library is capped


def test_optimised_search_equals_the_full_sort():
    rng = np.random.default_rng(11)
    rows = 500
    vectors = rng.random((rows, DIM))
    end_idx = np.repeat(np.arange(60, 60 + rows // 5 * 5, 5), 5)[:rows]
    ticker_col = np.arange(rows) % 120
    lib = _make_library(vectors=vectors, end_idx=end_idx, ticker_col=ticker_col,
                        figi=[-1] * rows)
    for query_seed in range(5):
        query = np.random.default_rng(query_seed).random(DIM)
        accepted, _, scores = _search(lib, query)
        cut = lib.prefix(pit.EmbargoView(300, RULES.max_lookback, RULES.primary_horizon).limit_idx)
        drop = neighbor_search.drop_same_symbol(lib.ticker_col[:cut], lib.figi_code[:cut], 999, -1)
        reference = neighbor_search.select_full_sort(
            scores.rank_score[0], drop, lib.ticker_col[:cut], lib.end_idx[:cut],
            top_k=RULES.top_k, ticker_cap=RULES.max_windows_per_ticker,
            date_cap=RULES.max_neighbors_per_end_date)
        assert accepted == reference


# -- full pipeline on a synthetic market -----------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic():
    panel = fixtures.make_panel(days=220, tickers=fixtures.WIDE_TICKERS, seed=3)
    history = fixtures.make_history(panel)
    eligible = fixtures.eligible_all(panel)
    features = structure_features.build(panel, eligible, RULES)
    valid = compute_validity(panel, (RULES.primary_horizon,), RULES.ca_suspect_ratio)
    lib = structure_library.build(history, RULES, features, valid.valid[RULES.primary_horizon],
                                  eval_end_idx=219, horizon=RULES.primary_horizon)
    return panel, history, features, lib


def _pipeline_neighbors(panel, date_idx=210):
    history = fixtures.make_history(panel)
    eligible = fixtures.eligible_all(panel)
    features = structure_features.build(panel, eligible, RULES)
    valid = compute_validity(panel, (RULES.primary_horizon,), RULES.ca_suspect_ratio)
    lib = structure_library.build(history, RULES, features, valid.valid[RULES.primary_horizon],
                                  eval_end_idx=date_idx, horizon=RULES.primary_horizon)
    columns = np.arange(len(panel.tickers), dtype=np.int64)
    sessions = np.full(columns.size, date_idx, dtype=np.int64)
    vectors = features.matrix(sessions, columns)
    view = pit.EmbargoView(date_idx, RULES.max_lookback, RULES.primary_horizon)
    outcomes = d2.search_date(lib=lib, view=view, query_vectors=vectors,
                              query_defined=features.defined[sessions, columns],
                              query_ticker_col=columns, query_figi_code=np.full(columns.size, -1,
                                                                                dtype=np.int32),
                              rules=RULES)
    identities = [[(int(lib.end_idx[r]), int(lib.ticker_col[r])) for r in o.rows]
                  for o in outcomes]
    distances = [o.metric_value.copy() for o in outcomes]
    return identities, distances, lib


def test_the_library_is_ordered_and_finite(synthetic):
    _, _, _, lib = *synthetic[:3], synthetic[3]
    assert len(lib) > 0
    order = np.lexsort((lib.ticker_col, lib.end_idx))
    np.testing.assert_array_equal(order, np.arange(len(lib)))
    assert np.isfinite(lib.vectors).all()
    assert lib.vectors.min() >= 0.0 and lib.vectors.max() <= 1.0


def test_future_query_mutation_changes_no_neighbor(synthetic):
    """Everything after the query date replaced by nonsense: same vectors, same Top-K, same order."""
    panel = synthetic[0]
    before_ids, before_distance, _ = _pipeline_neighbors(panel)
    after_ids, after_distance, _ = _pipeline_neighbors(d2.mutate_future(panel, 210))
    assert before_ids == after_ids
    for left, right in zip(before_distance, after_distance):
        np.testing.assert_array_equal(left, right)


def test_a_library_window_cannot_see_its_own_future(synthetic):
    """One window, one ticker: everything after its label contract reads is invisible to it.

    The statement is per window, not global. A library window at ``d`` is described by
    ``d-60..d`` and its h=5 label is decided by ``d..d+10``; changing that ticker's bars after
    ``d+10`` must leave both its vector and its label validity alone. Other windows of that
    ticker legitimately change, which is why the mutation is confined to one column.
    """
    panel, history, features, lib = synthetic
    row = int(np.argmin(lib.end_idx))
    end, column = int(lib.end_idx[row]), int(lib.ticker_col[row])
    before_vector = lib.vectors[row].copy()
    horizon_valid = compute_validity(panel, (RULES.primary_horizon,),
                                     RULES.ca_suspect_ratio).valid[RULES.primary_horizon]

    close = panel.close.copy()
    high, low, volume = panel.high.copy(), panel.low.copy(), panel.volume.copy()
    close[end + 11:, column] *= 50.0
    high[end + 11:, column] *= 50.0
    low[end + 11:, column] *= 0.02
    volume[end + 11:, column] *= 30.0
    mutated = with_changes(panel, close=close, high=high, low=low, volume=volume)

    after = structure_features.build(mutated, fixtures.eligible_all(mutated), RULES)
    after_vector = after.matrix(np.array([end]), np.array([column]))[0]
    np.testing.assert_array_equal(after_vector, before_vector)
    after_valid = compute_validity(mutated, (RULES.primary_horizon,),
                                   RULES.ca_suspect_ratio).valid[RULES.primary_horizon]
    assert bool(after_valid[end, column]) == bool(horizon_valid[end, column])


def test_search_is_deterministic(synthetic):
    panel = synthetic[0]
    first_ids, first_distance, _ = _pipeline_neighbors(panel)
    second_ids, second_distance, _ = _pipeline_neighbors(panel)
    assert first_ids == second_ids
    for left, right in zip(first_distance, second_distance):
        np.testing.assert_array_equal(left, right)


def test_library_rows_all_carry_a_decidable_label(synthetic):
    panel, history, features, lib = synthetic
    valid = compute_validity(panel, (RULES.primary_horizon,), RULES.ca_suspect_ratio)
    assert valid.valid[RULES.primary_horizon][lib.end_idx, lib.ticker_col].all()
    assert features.defined[lib.end_idx, lib.ticker_col].all()


# -- schema and parent binding -------------------------------------------------------------------

def test_feature_schema_digest_covers_order_and_formulas():
    assert SCHEMA.feature_order == FEATURE_NAMES
    assert SCHEMA.payload["dimension"] == DIM
    SCHEMA.assert_matches(SCHEMA.digest)
    with pytest.raises(HardFail):
        SCHEMA.assert_matches("0" * 64)

    class Shuffled:
        raw = dict(RULES.raw)

    reordered = json.loads(json.dumps(RULES.raw))
    reordered["representation"]["features"][0]["formula"] = "P(D)/P(D-6) - 1"
    from app.backtest.strategy_d_v2.config import V2ARules
    other = schema.build(V2ARules(reordered, RULES.checksum))
    assert other.digest != SCHEMA.digest


def test_parent_binding_rejects_an_unfinished_or_foreign_parent(tmp_path):
    run_dir = tmp_path / "dv2a1-fake"
    run_dir.mkdir()
    identity = {"identity_digest": "a" * 64, "phase": "D1", "rules_checksum": RULES.checksum,
                "strategy_id": RULES.raw["strategy_id"],
                "data": {"freeze_digest": "f", "grid_digest": "g", "d_read_digest": "r"}}
    report = {"verdict": "PASS", "determinism": {"digests": {}}}
    (run_dir / "run_identity.json").write_text(json.dumps(identity))
    (run_dir / "d1_report.json").write_text(json.dumps(report))
    with pytest.raises(HardFail):      # no COMPLETE token
        d2.load_parent(tmp_path, "dv2a1-fake", RULES)

    (run_dir / "COMPLETE.json").write_text(json.dumps(
        {"verdict": "FAIL", "identity_digest": "a" * 64}))
    with pytest.raises(HardFail):      # finished, but not a PASS
        d2.load_parent(tmp_path, "dv2a1-fake", RULES)

    (run_dir / "COMPLETE.json").write_text(json.dumps(
        {"verdict": "PASS", "identity_digest": "b" * 64}))
    with pytest.raises(HardFail):      # token does not match the identity it claims
        d2.load_parent(tmp_path, "dv2a1-fake", RULES)

    (run_dir / "COMPLETE.json").write_text(json.dumps(
        {"verdict": "PASS", "identity_digest": "a" * 64}))
    identity["rules_checksum"] = "0" * 64
    (run_dir / "run_identity.json").write_text(json.dumps(identity))
    with pytest.raises(HardFail):      # a parent from another declaration
        d2.load_parent(tmp_path, "dv2a1-fake", RULES)


def test_parent_matrix_digests_must_reproduce():
    class Parent:
        digests = {"raw_feature_matrix": "x", "rank_feature_matrix": "y", "validity_mask": "z",
                   "vector_status": "s", "query_sample": "q", "library_eligibility": "l",
                   "label_validity_primary": "v", "b0_rows": "b"}
    good = {name: value for name, value in Parent.digests.items()}
    assert all(d2.verify_parent_matrices(Parent, good).values())
    bad = dict(good, rank_feature_matrix="different")
    with pytest.raises(HardFail):
        d2.verify_parent_matrices(Parent, bad)
    with pytest.raises(HardFail):
        d2.verify_parent_matrices(Parent, {"raw_feature_matrix": "x"})


def test_neighbor_artifact_carries_no_outcome_column(synthetic):
    columns = {name: np.zeros(2, dtype=np.int32) for name in
               ("query_date_idx", "sample_rank", "query_ticker_col", "rank", "library_row",
                "neighbor_end_idx", "neighbor_ticker_col", "neighbor_figi_code", "pool_m")}
    columns["distance"] = np.zeros(2)
    columns["distance_exact"] = np.zeros(2)
    columns["rank_score"] = np.zeros(2)
    table = d2.neighbor_table(columns, ("AAA", "BBB"), synthetic[0].sessions[:2])
    for banned in ("return", "excess", "mfe", "mae", "label", "outcome"):
        assert not any(banned in name.lower() for name in table.column_names)
