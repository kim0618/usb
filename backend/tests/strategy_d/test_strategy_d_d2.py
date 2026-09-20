"""D2 test vectors V1~V25 and the D0 PIT mutation cases D2 is responsible for (T1~T15).

Expected values come from ``D_D2_TEST_VECTORS_V1.md``, which was written before this code existed
and computed them with numpy alone. They are transcribed, not derived from the implementation: a
test that agrees with the code it tests proves nothing.

Everything runs on synthetic data. No test reads the Drive workspace, the real freeze or the
network, and no test reads a forward return - the one label quantity that appears anywhere below
is the boolean validity mask, which is also all D2 itself is allowed to see.
"""

import ast
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_d_analog import (
    d2, encoder, label_extension, library, neighbor_search, pit, pit_audit, similarity, universe,
)
from app.backtest.strategy_d_analog.config import RULES_PATH, load_rules
from app.backtest.strategy_d_analog.identity import query_sample
from app.backtest.strategy_d_analog.models import (
    HardFail, IneligibleReason, PointInTimeViolation, QueryStatus, RulesChanged, TestId,
)
from app.backtest.strategy_d_analog.sampling import sample_date
from app.backtest.strategy_d_analog.source import load_daily_history
from tests.strategy_d import fixtures

PACKAGE = Path(__file__).resolve().parents[2] / "app/backtest/strategy_d_analog"
#: Tolerances of the test vector document: A is a dot product of bounded values, B goes through a
#: square root that turns a 1e-12 cancellation in the expansion into 1e-6 on the distance itself.
TOL_A = 1e-12
TOL_B = 1e-9
TOL_B_SAME = 1e-6


def linear_up(window: int = 20, base: float = 10.0, step: float = 1.0) -> np.ndarray:
    return (base + step * np.arange(window + 1, dtype=np.float64))[None, :]


def linear_down(window: int = 20) -> np.ndarray:
    return (30.0 - np.arange(window + 1, dtype=np.float64))[None, :]


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("d2workspace") / "1_US-B"
    fixtures.build(root)
    return root


def history_of(root, rules, **kwargs):
    return load_daily_history(root, fixtures.SNAPSHOT_ID, allowed_exchanges=rules.allowed_exchanges,
                              **kwargs)


# --- 1. Encoder (V1~V7) -------------------------------------------------------------------

def test_v1_linear_rise():
    prices = linear_up()
    encoded_a = encoder.encode_a(prices)
    z = encoded_a.vectors[0]
    assert encoded_a.defined[0]
    assert prices[0].std(ddof=0) == pytest.approx(6.0553007081949835, abs=TOL_A)
    assert z[0] == pytest.approx(-1.651445647689541, abs=TOL_A)
    assert z[20] == pytest.approx(1.651445647689541, abs=TOL_A)
    assert abs(z.sum()) <= 1e-9
    assert abs((z ** 2).sum() - 21) <= 1e-9 * 21
    c = encoder.encode_b(prices).vectors[0]
    assert c.shape == (20,)
    assert c[-1] == pytest.approx(1.0986122886681098, abs=TOL_B)
    assert c[0] == pytest.approx(0.09531017980432493, abs=TOL_B)


def test_v2_linear_fall():
    up, down = linear_up(), linear_down()
    z_up, z_down = encoder.encode_a(up).vectors[0], encoder.encode_a(down).vectors[0]
    assert np.abs(z_down + z_up).max() <= TOL_A
    rho = similarity.score_block(similarity.Metric.PEARSON, encoder.encode_a(up).vectors,
                                 encoder.encode_a(down).vectors)
    assert rho.metric_value[0, 0] == pytest.approx(-1.0, abs=TOL_A)
    c_up, c_down = encoder.encode_b(up).vectors, encoder.encode_b(down).vectors
    assert c_down[0][-1] == pytest.approx(-1.0986122886681098, abs=TOL_B)
    distance = similarity.score_block(similarity.Metric.EUCLIDEAN, c_up, c_down)
    assert distance.metric_value[0, 0] == pytest.approx(5.834719216810601, abs=TOL_B)


@pytest.mark.parametrize("value, length, exact_zero_std", [
    (10.0, 21, True), (10.07, 61, False), (7.77, 21, False)])
def test_v3_a_constant_window_has_no_direction_in_a_but_is_defined_in_b(value, length,
                                                                       exact_zero_std):
    prices = np.full((1, length), value)
    # The documented trap: the population std of 61 copies of 10.07 is 5.3e-15, not 0, so an
    # implementation that tested std == 0 would divide noise by noise and pass this window on.
    # Only a value that is exactly representable, like 10.0, gives a std that is exactly zero.
    assert bool(prices[0].std(ddof=0) == 0.0) is exact_zero_std
    assert np.ptp(prices[0]) == 0.0
    assert not encoder.encode_a(prices).defined[0]
    encoded_b = encoder.encode_b(prices)
    assert encoded_b.defined[0]
    assert np.array_equal(encoded_b.vectors[0], np.zeros(length - 1))


def test_v4_price_level_is_invisible_to_both_representations():
    prices, scaled = linear_up(), linear_up() * 100.0
    z, z_scaled = encoder.encode_a(prices).vectors, encoder.encode_a(scaled).vectors
    assert np.abs(z_scaled - z).max() <= TOL_A
    assert similarity.score_block(similarity.Metric.PEARSON, z, z_scaled
                                  ).metric_value[0, 0] == pytest.approx(1.0, abs=TOL_A)
    c, c_scaled = encoder.encode_b(prices).vectors, encoder.encode_b(scaled).vectors
    assert similarity.score_block(similarity.Metric.EUCLIDEAN, c, c_scaled
                                  ).metric_value[0, 0] <= TOL_A


def test_v4b_a_window_encodes_identically_as_query_and_as_library_row(dataset, rules):
    """The same (ticker, e) must give the same bits whichever side of the search it is on."""
    history = history_of(dataset, rules)
    index, window = 200, 20
    view = history.panel_view(index)
    column = np.array([history.tickers.index("Q00")])
    as_query = encoder.gather(view.price, np.array([index]), column, window, as_of_idx=index)
    as_library = encoder.gather(view.price, np.array([index]), column, window, as_of_idx=index)
    assert as_query.tobytes() == as_library.tobytes()
    assert encoder.encode_a(as_query).vectors.tobytes() == encoder.encode_a(as_library).vectors.tobytes()
    # And in a batch: a row's encoding must not depend on the rows it travels with.
    many = encoder.gather(view.price, np.full(5, index),
                          np.array([history.tickers.index(t) for t in fixtures.QUIET[:5]]),
                          window, as_of_idx=index)
    assert encoder.encode_a(many).vectors[0].tobytes() == encoder.encode_a(as_query).vectors[0].tobytes()


def test_v5_amplitude_is_invisible_to_a_and_visible_to_b():
    steep, shallow = linear_up(step=1.0), linear_up(step=0.1)
    rho = similarity.score_block(similarity.Metric.PEARSON, encoder.encode_a(steep).vectors,
                                 encoder.encode_a(shallow).vectors)
    assert rho.metric_value[0, 0] == pytest.approx(1.0, abs=TOL_A)
    c_shallow = encoder.encode_b(shallow).vectors
    assert c_shallow[0][-1] == pytest.approx(0.1823215567939546, abs=TOL_B)
    distance = similarity.score_block(similarity.Metric.EUCLIDEAN,
                                      encoder.encode_b(steep).vectors, c_shallow)
    assert distance.metric_value[0, 0] == pytest.approx(2.805256433161798, abs=TOL_B)


def test_v5b_a_shift_moves_b_but_not_a():
    prices = linear_up()
    shifted = 3.0 * prices + 7.0
    rho = similarity.score_block(similarity.Metric.PEARSON, encoder.encode_a(prices).vectors,
                                 encoder.encode_a(shifted).vectors)
    assert rho.metric_value[0, 0] == pytest.approx(1.0, abs=TOL_A)
    distance = similarity.score_block(similarity.Metric.EUCLIDEAN, encoder.encode_b(prices).vectors,
                                      encoder.encode_b(shifted).vectors)
    assert distance.metric_value[0, 0] > 1e-3


def test_v6_a_split_inside_the_window_excludes_it_and_leaves_the_path_flat(dataset, rules):
    """The raw -50% step is the split, not a move: on ``P = close / F`` the path is constant."""
    history = history_of(dataset, rules)
    split_idx = history.grid.index_of(
        [e.execution_date for e in history.panel.splits if e.ticker == "SPLITTER"][0])
    end = split_idx + 10
    result = universe.evaluate(history.panel_view(end), history.membership(end), rules)
    column = history.tickers.index("SPLITTER")
    assert result.reason[column] == universe.REASON_ORDER.index(IneligibleReason.SPLIT_WINDOW)

    raw = np.where(np.arange(61) < 10, 100.0, 50.0)[None, :]
    factor = np.where(np.arange(61) < 10, 1.0, 0.5)[None, :]
    normalized = raw / factor
    assert not encoder.encode_a(normalized).defined[0]
    assert np.array_equal(encoder.encode_b(normalized).vectors[0], np.zeros(60))


def test_v7_a_missing_bar_is_an_exclusion_and_never_a_repair(dataset, rules):
    history = history_of(dataset, rules)
    column = history.tickers.index("GAPPY")
    gap = int(np.argwhere(~np.isfinite(history.panel.close[:, column]))[0][0])
    spanning = universe.evaluate(history.panel_view(gap + 10), history.membership(gap + 10), rules)
    assert spanning.reason[column] == universe.REASON_ORDER.index(IneligibleReason.NO_HISTORY)
    broken = linear_up().copy()
    broken[0, 15] = np.nan
    with pytest.raises(HardFail) as caught:
        encoder.encode_a(broken)
    assert caught.value.code == "R5"
    with pytest.raises(HardFail):
        encoder.encode_b(broken)


# --- 2. Similarity (V8~V12) ---------------------------------------------------------------

def test_v8_a_vector_against_itself():
    z = encoder.encode_a(linear_up()).vectors
    scored = similarity.score_block(similarity.Metric.PEARSON, z, z)
    assert abs(scored.metric_value[0, 0] - 1.0) <= TOL_A
    assert scored.rank_score[0, 0] == scored.metric_value[0, 0]
    c = encoder.encode_b(linear_up()).vectors
    scored_b = similarity.score_block(similarity.Metric.EUCLIDEAN, c, c)
    assert 0.0 <= scored_b.metric_value[0, 0] <= TOL_B_SAME
    assert scored_b.rank_score[0, 0] == -scored_b.metric_value[0, 0]


def test_v9_opposite_vectors():
    z = encoder.encode_a(linear_up()).vectors
    assert similarity.score_block(similarity.Metric.PEARSON, z, -z
                                  ).metric_value[0, 0] == pytest.approx(-1.0, abs=TOL_A)
    c = encoder.encode_b(linear_up()).vectors
    assert np.linalg.norm(c[0]) == pytest.approx(3.3017118542166797, abs=TOL_B)
    assert similarity.score_block(similarity.Metric.EUCLIDEAN, c, -c
                                  ).metric_value[0, 0] == pytest.approx(6.603423708433359, abs=TOL_B)


def test_v10_b_measures_amplitude():
    c = encoder.encode_b(linear_up()).vectors
    norm = 3.3017118542166797
    assert similarity.score_block(similarity.Metric.EUCLIDEAN, c, 2.0 * c
                                  ).metric_value[0, 0] == pytest.approx(norm, abs=TOL_B)
    assert similarity.score_block(similarity.Metric.EUCLIDEAN, np.zeros_like(c), c
                                  ).metric_value[0, 0] == pytest.approx(norm, abs=TOL_B)


def test_v11_a_constant_vector_never_reaches_the_pearson_block():
    z = encoder.encode_a(linear_up()).vectors
    with pytest.raises(HardFail) as caught:
        similarity.score_block(similarity.Metric.PEARSON, np.zeros_like(z), z)
    assert caught.value.code == "R12"
    with pytest.raises(HardFail):
        similarity.score_block(similarity.Metric.PEARSON, z, np.full_like(z, 4.0))
    c = encoder.encode_b(linear_up()).vectors
    assert similarity.score_block(similarity.Metric.EUCLIDEAN, np.zeros_like(c), c).metric_value.size == 1


def test_v12_ties_break_by_end_index_then_ticker(dataset, rules):
    rows = [(100, "BBB"), (100, "AAA"), (95, "ZZZ")]
    lib = build_library(rows, vector=encoder.encode_a(linear_up()).vectors[0], shuffle=True)
    outcome = search_one(lib, encoder.encode_a(linear_up()).vectors[0], query_date=200, window=20,
                         horizon=5, query_ticker="QRY", top_k=3)
    assert [(int(e), t) for e, t in zip(lib["end_idx"][outcome.rows],
                                        [lib["names"][c] for c in lib["ticker_col"][outcome.rows]])] \
        == [(95, "ZZZ"), (100, "AAA"), (100, "BBB")]
    history = history_of(dataset, rules)
    assert list(history.panel.tickers) == sorted(history.panel.tickers)


# --- helpers for the neighbour vectors ----------------------------------------------------

def build_library(rows, *, vector=None, vectors=None, figis=None, shuffle=False, seed=0):
    """A library laid out the way ``library.build`` lays one out: ``(end_idx, ticker)`` ascending."""
    order = list(range(len(rows)))
    if shuffle:
        np.random.default_rng(seed).shuffle(order)
    names = tuple(sorted({ticker for _, ticker in rows}))
    column = {name: i for i, name in enumerate(names)}
    entries = []
    for position in order:
        end, ticker = rows[position]
        entries.append((end, column[ticker], position))
    entries.sort(key=lambda item: (item[0], item[1]))
    end_idx = np.array([e for e, _, _ in entries], dtype=np.int32)
    ticker_col = np.array([c for _, c, _ in entries], dtype=np.int32)
    source = [position for _, _, position in entries]
    if vectors is None:
        matrix = np.repeat(vector[None, :], len(rows), axis=0)
    else:
        matrix = np.ascontiguousarray(np.asarray(vectors, dtype=np.float64)[source])
    codes = np.full(len(rows), -1, dtype=np.int32)
    if figis is not None:
        table: dict[str, int] = {}
        for row, position in enumerate(source):
            value = figis[position]
            if value is not None:
                codes[row] = table.setdefault(value, len(table))
    return {"end_idx": end_idx, "ticker_col": ticker_col, "figi_code": codes,
            "vectors": np.ascontiguousarray(matrix), "names": names,
            "sq_norm": np.einsum("ij,ij->i", matrix, matrix)}


def search_one(lib, query_vector, *, query_date, window, horizon, query_ticker,
               query_figi_code=-1, metric=similarity.Metric.PEARSON, top_k=50, ticker_cap=1,
               date_cap=5, initial_m=neighbor_search.INITIAL_M, view=None):
    view = view or pit.EmbargoView(query_date, window, horizon)
    cut = neighbor_search.embargo_cut(lib["end_idx"], view)
    names = lib["names"]
    query_col = names.index(query_ticker) if query_ticker in names else -1
    figi_code = query_figi_code
    outcomes = neighbor_search.search_block(
        metric=metric, query_vectors=query_vector[None, :], library_vectors=lib["vectors"][:cut],
        library_sq_norm=lib["sq_norm"][:cut], ticker_col=lib["ticker_col"][:cut],
        end_idx=lib["end_idx"][:cut], figi_code=lib["figi_code"][:cut],
        query_ticker_col=np.array([query_col]), query_figi_code=np.array([figi_code]),
        view_of=lambda _: view, top_k=top_k, ticker_cap=ticker_cap, date_cap=date_cap,
        initial_m=initial_m)
    return outcomes[0]


def blended(alpha, window=20, seed=17):
    """A z vector a controlled distance from the V1 shape: bigger ``alpha``, lower correlation."""
    base = encoder.encode_a(linear_up(window)).vectors[0]
    noise = np.random.default_rng(seed).normal(size=base.size)
    mixed = base + alpha * noise
    return (mixed - mixed.mean()) / mixed.std(ddof=0)


def noisy_rows(count, *, end_start=100, end_step=5, per_date=8, seed=3, window=20):
    """A background library: many tickers over many end dates, none of them near the query."""
    rng = np.random.default_rng(seed)
    rows, vectors = [], []
    for index in range(count):
        end = end_start + end_step * (index // per_date)
        rows.append((end, f"N{index:04d}"))
        path = 50.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, window + 1)))
        vectors.append(encoder.encode_a(path[None, :]).vectors[0])
    return rows, vectors


# --- 3. Neighbour search (V13~V20) --------------------------------------------------------

def test_v13_the_query_ticker_is_never_its_own_neighbour():
    rows, vectors = noisy_rows(200)
    target = encoder.encode_a(linear_up()).vectors[0]
    near = encoder.encode_a(linear_up(step=0.9)).vectors[0]
    rows += [(200, "QQQX"), (205, "QQQX"), (210, "OTHR")]
    vectors += [target, target, near]
    lib = build_library(rows, vectors=vectors)
    outcome = search_one(lib, target, query_date=300, window=20, horizon=5, query_ticker="QQQX")
    chosen = [lib["names"][c] for c in lib["ticker_col"][outcome.rows]]
    assert "QQQX" not in chosen
    assert chosen[0] == "OTHR"
    cut = outcome.candidates_after_embargo
    assert outcome.candidates_after_same_symbol == cut - 2


def test_v14_same_figi_is_excluded_only_when_both_sides_have_one():
    rows, vectors = noisy_rows(200)
    target = encoder.encode_a(linear_up()).vectors[0]
    rows += [(200, "OLDT"), (205, "NEXT")]
    vectors += [target, encoder.encode_a(linear_up(step=0.9)).vectors[0]]
    lib = build_library(rows, vectors=vectors,
                        figis=[None] * 200 + ["BBG000X", "BBG000Y"])
    shared = int(lib["figi_code"][lib["ticker_col"] == lib["names"].index("OLDT")][0])
    excluded = search_one(lib, target, query_date=300, window=20, horizon=5, query_ticker="NEWT",
                          query_figi_code=shared)
    assert "OLDT" not in [lib["names"][c] for c in lib["ticker_col"][excluded.rows]]
    kept = search_one(lib, target, query_date=300, window=20, horizon=5, query_ticker="NEWT",
                      query_figi_code=-1)
    assert lib["names"][lib["ticker_col"][kept.rows[0]]] == "OLDT"


def test_v15_the_embargo_boundary_is_usable():
    """``d + h == D - W``: the neighbour's label ends on the query window's first session."""
    rows, vectors = noisy_rows(200, end_start=100, end_step=5)
    target = encoder.encode_a(linear_up()).vectors[0]
    rows.append((275, "EDGE"))
    vectors.append(target)
    lib = build_library(rows, vectors=vectors)
    outcome = search_one(lib, target, query_date=300, window=20, horizon=5, query_ticker="QRY")
    assert pit.EmbargoView(300, 20, 5).limit_idx == 275
    assert lib["names"][lib["ticker_col"][outcome.rows[0]]] == "EDGE"


def test_v16_one_session_past_the_boundary_is_unreachable():
    rows, vectors = noisy_rows(200, end_start=100, end_step=5)
    rows.append((275, "EDGE"))
    vectors.append(encoder.encode_a(linear_up()).vectors[0])
    lib = build_library(rows, vectors=vectors)
    outcome = search_one(lib, encoder.encode_a(linear_up()).vectors[0], query_date=299, window=20,
                         horizon=5, query_ticker="QRY")
    assert "EDGE" not in [lib["names"][c] for c in lib["ticker_col"][outcome.rows]]
    view = pit.EmbargoView(299, 20, 5)
    assert view.limit_idx == 274
    with pytest.raises(PointInTimeViolation):
        view.label_validity(np.ones(400, dtype=bool), np.array([275]))


def test_t6_the_exclusion_is_the_embargo_and_nothing_else():
    """Positive control (D0 PIT #6): loosen the bound and the same window is chosen at once."""
    rows, vectors = noisy_rows(200, end_start=100, end_step=5)
    rows.append((275, "EDGE"))
    vectors.append(encoder.encode_a(linear_up()).vectors[0])
    lib = build_library(rows, vectors=vectors)
    loose = search_one(lib, encoder.encode_a(linear_up()).vectors[0], query_date=299, window=20,
                       horizon=5, query_ticker="QRY", view=pit.EmbargoView(299, 0, 0))
    assert lib["names"][lib["ticker_col"][loose.rows[0]]] == "EDGE"


def test_v17_one_window_per_ticker():
    rows, vectors = noisy_rows(400)
    target = encoder.encode_a(linear_up()).vectors[0]
    for offset in range(10):
        rows.append((100 + 5 * offset, "CONC"))
        vectors.append(target)
    lib = build_library(rows, vectors=vectors)
    outcome = search_one(lib, target, query_date=600, window=20, horizon=5, query_ticker="QRY")
    chosen = [lib["names"][c] for c in lib["ticker_col"][outcome.rows]]
    assert chosen.count("CONC") == 1
    assert len(set(chosen)) == len(chosen) == 50


def test_v17b_a_ticker_blocked_by_a_full_date_can_still_place_a_later_window():
    """Pre-reducing to one window per ticker before ranking would lose X's 155 window."""
    target = encoder.encode_a(linear_up()).vectors[0]
    rows = [(150, f"A{i}") for i in range(1, 6)] + [(150, "X"), (155, "X")]
    vectors = [blended(0.01 * step) for step in range(1, 8)]
    background, background_vectors = noisy_rows(60, end_start=100, end_step=5, seed=9)
    lib = build_library(rows + background, vectors=vectors + background_vectors)
    # The fixture only works if the planted rows really are the seven best, in this order.
    planted = np.array([v @ target / target.size for v in vectors])
    assert list(planted) == sorted(planted, reverse=True)
    outcome = search_one(lib, target, query_date=600, window=20, horizon=5, query_ticker="QRY",
                         top_k=7)
    picked = [(int(e), lib["names"][c])
              for e, c in zip(lib["end_idx"][outcome.rows], lib["ticker_col"][outcome.rows])]
    assert picked[:5] == [(150, f"A{i}") for i in range(1, 6)]
    assert (155, "X") in picked and (150, "X") not in picked


def test_v18_five_windows_per_library_end_date():
    target = encoder.encode_a(linear_up()).vectors[0]
    rows = [(150, f"T{i:02d}") for i in range(10)]
    background, background_vectors = noisy_rows(100, end_start=100, end_step=5, seed=4)
    lib = build_library(rows + background, vectors=[target] * 10 + background_vectors)
    outcome = search_one(lib, target, query_date=600, window=20, horizon=5, query_ticker="QRY",
                         top_k=8)
    picked = [(int(e), lib["names"][c])
              for e, c in zip(lib["end_idx"][outcome.rows], lib["ticker_col"][outcome.rows])]
    assert [name for end, name in picked if end == 150] == [f"T{i:02d}" for i in range(5)]


def test_v19_too_few_candidates_is_a_counted_exclusion_not_a_failure():
    target = encoder.encode_a(linear_up()).vectors[0]
    thin, thin_vectors = noisy_rows(30, end_start=100, end_step=5, per_date=3, seed=5)
    lib = build_library(thin, vectors=thin_vectors)
    outcome = search_one(lib, target, query_date=600, window=20, horizon=5, query_ticker="QRY")
    assert outcome.status is QueryStatus.INSUFFICIENT_NEIGHBORS
    assert outcome.accepted_count == 30
    assert outcome.pool_final_m == outcome.candidates_after_same_symbol

    one_name = [(100 + 5 * (i // 3), "ONLY") for i in range(60)]
    lib_one = build_library(one_name, vector=target)
    single = search_one(lib_one, target, query_date=600, window=20, horizon=5, query_ticker="QRY")
    assert single.status is QueryStatus.INSUFFICIENT_NEIGHBORS
    assert single.accepted_count == 1


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_v20_the_answer_does_not_depend_on_input_order_or_pool_size(seed):
    rows, vectors = noisy_rows(600, end_start=100, end_step=5, per_date=10, seed=seed)
    target = encoder.encode_a(linear_up()).vectors[0]
    first = build_library(rows, vectors=vectors)
    shuffled = build_library(rows, vectors=vectors, shuffle=True, seed=seed)
    assert np.array_equal(first["end_idx"], shuffled["end_idx"])
    assert np.array_equal(first["vectors"], shuffled["vectors"])
    base = search_one(first, target, query_date=600, window=20, horizon=5, query_ticker="QRY")
    again = search_one(shuffled, target, query_date=600, window=20, horizon=5, query_ticker="QRY")
    small = search_one(first, target, query_date=600, window=20, horizon=5, query_ticker="QRY",
                       initial_m=50)
    whole = search_one(first, target, query_date=600, window=20, horizon=5, query_ticker="QRY",
                       initial_m=len(rows))
    for other in (again, small, whole):
        assert np.array_equal(base.rows, other.rows)
        assert base.metric_value.tobytes() == other.metric_value.tobytes()


@pytest.mark.parametrize("seed", range(6))
def test_the_optimised_search_equals_a_full_sort(seed):
    """§26: the Top-M expansion is an optimisation, so it must not be able to change an answer."""
    rng = np.random.default_rng(seed)
    rows, vectors = noisy_rows(500, end_start=100, end_step=5, per_date=7, seed=seed)
    lib = build_library(rows, vectors=vectors)
    scores = rng.normal(size=len(rows))
    if seed % 2:  # force heavy ties, where a partition boundary could cut a group in half
        scores = np.round(scores, 1)
    drop = rng.random(len(rows)) < 0.1
    optimised, _ = neighbor_search.select(scores, drop, lib["ticker_col"], lib["end_idx"],
                                          top_k=50, ticker_cap=1, date_cap=5)
    full = neighbor_search.select_full_sort(scores, drop, lib["ticker_col"], lib["end_idx"],
                                            top_k=50, ticker_cap=1, date_cap=5)
    assert optimised == full


# --- 4. Point in time (V21~V25, T1, T4, T9, T12, T15) --------------------------------------

def synthetic_panel(days: int = 40, names: tuple[str, ...] = ("AAA", "BBB", "CCC"), seed: int = 2):
    """A tiny raw panel for the label-validity contracts, built without touching the loader."""
    from app.backtest.strategy_c_selection.panel import Panel

    sessions = fixtures.sessions(days)
    rng = np.random.default_rng(seed)
    close = np.vstack([20.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, days))) for _ in names]).T
    panel = Panel(sessions, names, close * 0.999, close * 1.01, close * 0.99, close,
                  np.full((days, len(names)), 500_000.0), (), {sessions[0]: frozenset(names)})
    return panel


def drop_ticker(history, ticker: str):
    """Remove one name's column entirely, the way a later dataset drops a delisted symbol."""
    from app.backtest.strategy_c_selection.panel import Panel

    keep = [i for i, name in enumerate(history.panel.tickers) if name != ticker]
    panel = history.panel
    trimmed = Panel(panel.sessions, tuple(panel.tickers[i] for i in keep),
                    panel.open[:, keep], panel.high[:, keep], panel.low[:, keep],
                    panel.close[:, keep], panel.volume[:, keep],
                    tuple(e for e in panel.splits if e.ticker != ticker),
                    {d: frozenset(v - {ticker}) for d, v in panel.snapshots.items()})
    return pit_audit._replace_panel(history, trimmed)


def neighbors_at(history, rules, index, tests=None):
    horizons = tuple(sorted({h for _, h in rules.combinations}))
    validity = label_extension.compute_validity(history.panel, horizons, rules.ca_suspect_ratio)
    return pit_audit.neighbors_for_date(history, rules, validity.valid, index,
                                        tests or rules.tests)


def as_names(history, found):
    """Neighbour lists keyed by test and query, in ticker names so a column shift cannot hide."""
    return {key: ([int(e) for e in ends],
                  [history.panel.tickers[int(c)] for c in cols],
                  metric.tobytes())
            for key, (ends, cols, metric) in found.items()}


@pytest.fixture(scope="module")
def audit_index(rules):
    return rules.eval_range(290)[0] + 1


def test_v21_mutating_every_future_bar_changes_nothing(dataset, rules, audit_index):
    """D0 PIT #2: the query vectors, the candidate cut and the neighbour lists are bit identical."""
    history = history_of(dataset, rules)
    base = as_names(history, neighbors_at(history, rules, audit_index))
    assert base, "the fixture produced no neighbours to compare"
    mutated = pit_audit.mutate_future_bars(history_of(dataset, rules), audit_index)
    assert as_names(mutated, neighbors_at(mutated, rules, audit_index)) == base


def test_v22_a_split_executed_after_the_query_date_changes_nothing(dataset, rules, audit_index):
    history = history_of(dataset, rules)
    base = as_names(history, neighbors_at(history, rules, audit_index))
    planted = pit_audit.plant_future_splits(history_of(dataset, rules), audit_index)
    assert len(planted.panel.splits) > len(history.panel.splits)
    assert as_names(planted, neighbors_at(planted, rules, audit_index)) == base


def test_v23_a_name_that_resumes_after_the_query_date_is_still_judged_at_its_horizon(rules):
    """D0 PIT #16: validity asks for a bar on ``d+h`` and never for one anywhere later."""
    panel = synthetic_panel(days=60)
    column, horizon, end = 1, 10, 20
    quiet = panel.close.copy()
    quiet[end + horizon + 1: 45, column] = np.nan          # silent from d+h+1 until well past D
    with_resumption = quiet.copy()
    with_resumption[50:, column] = 33.0                     # and a bar planted after the query date
    ratio = rules.ca_suspect_ratio
    from app.backtest.strategy_c_selection.panel import with_changes

    before = label_extension.compute_validity(with_changes(panel, close=quiet, open=quiet * 0.999),
                                              (horizon,), ratio)
    after = label_extension.compute_validity(
        with_changes(panel, close=with_resumption, open=with_resumption * 0.999), (horizon,), ratio)
    assert before.valid[horizon][end, column]
    assert before.valid[horizon][end, column] == after.valid[horizon][end, column]

    gone = quiet.copy()
    gone[end + horizon, column] = np.nan                    # no bar on d+h itself
    resumed = gone.copy()
    resumed[50:, column] = 33.0
    missing = label_extension.compute_validity(
        with_changes(panel, close=resumed, open=resumed * 0.999), (horizon,), ratio)
    assert not missing.valid[horizon][end, column]
    assert missing.missing_horizon_bar[horizon][end, column]


def test_v24_a_raw_file_that_does_not_match_the_freeze_stops_the_run_before_any_artifact(
        tmp_path, rules):
    root = tmp_path / "corrupt" / "1_US-B"
    fixtures.build(root, corrupt_sha_on=fixtures.sessions(290)[150])
    runs = tmp_path / "runs"
    with pytest.raises(HardFail) as caught:
        d2.execute(root, fixtures.SNAPSHOT_ID, expected=None, parent_run="dpit1-test", rules=rules,
                   runs_dir=runs, log=lambda _: None, eval_date_limit=1)
    assert caught.value.code == "R2"
    assert not runs.exists()


def test_v25_an_edited_rule_file_cannot_produce_or_be_handed_a_d2_run(tmp_path):
    import json

    edited = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    edited["top_k"] = 51
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(RulesChanged):
        load_rules(path)
    parent = tmp_path / "dpit1-other"
    parent.mkdir()
    (parent / "COMPLETE.json").write_text("{}", encoding="utf-8")
    (parent / "run_identity.json").write_text(
        json.dumps({"phase": "D1", "rules_checksum": "0" * 64, "run_id": "dpit1-other", "data": {}}),
        encoding="utf-8")
    with pytest.raises(HardFail) as caught:
        d2.freeze_from_d1(parent)
    assert caught.value.code == "R1"


def test_t4_removing_a_name_leaves_the_others_identical_and_changes_the_neighbour_lists(
        dataset, rules, audit_index):
    """Negative and positive control in one: vectors must not move, neighbour lists must."""
    history = history_of(dataset, rules)
    named = as_names(history, neighbors_at(history, rules, audit_index))
    victim = next(name for _, names, _ in named.values() for name in names)
    trimmed = drop_ticker(history_of(dataset, rules), victim)

    window, index = 20, audit_index
    keep = [t for t in history.panel.tickers if t != victim]
    full_view, short_view = history.panel_view(index), trimmed.panel_view(index)
    full = encoder.gather(full_view.price, np.full(len(keep), index),
                          np.array([history.panel.tickers.index(t) for t in keep]), window)
    short = encoder.gather(short_view.price, np.full(len(keep), index),
                           np.array([trimmed.panel.tickers.index(t) for t in keep]), window)
    assert full.tobytes() == short.tobytes()
    assert as_names(trimmed, neighbors_at(trimmed, rules, audit_index)) != named


def test_t9_a_close_inside_the_window_moves_both_representations(dataset, rules, audit_index):
    history = history_of(dataset, rules)
    window, ticker = 20, "Q00"
    column = np.array([history.panel.tickers.index(ticker)])
    before = encoder.gather(history.panel_view(audit_index).price, np.array([audit_index]),
                            column, window)
    moved = pit_audit.scale_window_close(history_of(dataset, rules), audit_index - 5, ticker, 2.0)
    after = encoder.gather(moved.panel_view(audit_index).price, np.array([audit_index]),
                           column, window)
    assert not np.array_equal(encoder.encode_a(before).vectors, encoder.encode_a(after).vectors)
    assert not np.array_equal(encoder.encode_b(before).vectors, encoder.encode_b(after).vectors)


def test_t12_the_20_day_ca_extension_matches_c_up_to_ten_and_catches_a_jump_at_fifteen(rules):
    """D0 ``label_ca_suspect``: C stops at D+10, so a 10x move at D+15 passes as a valid 20D label."""
    from app.backtest.strategy_c_selection.labels import compute_labels
    from app.backtest.strategy_c_selection.panel import with_changes

    panel = synthetic_panel(days=60)
    close = panel.close.copy()
    origin, column = 20, 0
    close[origin + 15:, column] *= 10.0
    jumped = with_changes(panel, close=close, open=close * 0.999, high=close * 1.01, low=close * 0.99)
    ratio = rules.ca_suspect_ratio

    validity = label_extension.compute_validity(jumped, (1, 3, 5, 10, 20), ratio)
    reference = compute_labels(jumped, horizons=(1,), ca_ratio=ratio)
    for horizon in (1, 3, 5, 10):
        assert np.array_equal(validity.ca_suspect[horizon], reference.label_ca_suspect)
    assert np.array_equal(validity.no_entry_bar, reference.no_entry_bar)
    assert not validity.ca_suspect[10][origin, column]      # C cannot see a jump at D+15
    assert validity.ca_suspect[20][origin, column]          # the D extension does
    assert not validity.valid[20][origin, column]
    assert validity.valid[10][origin, column]


def test_t15_the_query_sample_does_not_move_when_every_label_is_destroyed(dataset, rules,
                                                                         audit_index):
    """D0 PIT #15: the sample is drawn from the universe rule and the date's hash, never a label."""
    history = history_of(dataset, rules)
    column_of = {ticker: i for i, ticker in enumerate(history.tickers)}
    eligible = universe.evaluate(history.panel_view(audit_index),
                                 history.membership(audit_index), rules)
    session = history.grid.session(audit_index)
    before = sample_date(audit_index, session, eligible.tickers(history.tickers), column_of,
                         rules.queries_per_date)
    horizons = tuple(sorted({h for _, h in rules.combinations}))
    validity = label_extension.compute_validity(history.panel, horizons, rules.ca_suspect_ratio)
    for mask in validity.valid.values():
        mask[:] = False
    after = sample_date(audit_index, session, eligible.tickers(history.tickers), column_of,
                        rules.queries_per_date)
    assert before.tickers == after.tickers
    assert before.tickers == query_sample(session.isoformat(), eligible.tickers(history.tickers),
                                          rules.queries_per_date)


# --- 5. The D2 run: artifacts, determinism, truncation, boundaries -------------------------

FORBIDDEN_COLUMNS = ("forward_return", "close_return", "excess_return", "mfe", "mae",
                     "future_close", "label_value", "return", "pnl", "win")
#: Names that could only appear in D2 if it had started to evaluate the analogues it finds.
FORBIDDEN_IDENTIFIERS = ("close_return", "excess_return", "forward_return", "compute_labels",
                         "spearman", "mfe", "mae", "quintile", "win_rate", "baseline_n1")


@pytest.fixture(scope="module")
def d2_run(dataset, rules, tmp_path_factory):
    expected = history_of(dataset, rules).freeze
    return d2.execute(dataset, fixtures.SNAPSHOT_ID, expected=expected, parent_run="dpit1-fixture",
                      rules=rules, runs_dir=tmp_path_factory.mktemp("d2runs"), log=lambda _: None,
                      eval_date_limit=3, audit_count=2)


def test_the_run_writes_every_declared_artifact_and_completes_last(d2_run, rules, dataset):
    from app.backtest.strategy_d_analog import artifacts

    run_dir = d2_run.run_dir
    assert not (run_dir / "COMPLETE.json").exists()          # nothing is complete until finish()
    d2.finish(d2_run, d2.run_context(dataset, fixtures.SNAPSHOT_ID))
    for name in ("run_identity.json", "library_manifest.json", "query_manifest.json",
                 "summary.json", "pit_runtime.json", "query_samples.parquet",
                 "query_results.parquet"):
        assert (run_dir / name).exists(), name
    for window in rules.pattern_windows:
        assert (run_dir / f"library_meta_W{window}.parquet").exists()
    for test in rules.tests:
        assert (run_dir / f"neighbors_{test.name}.parquet").exists()
    assert len(rules.tests) == 14
    complete = artifacts.load_complete(run_dir)
    assert complete["verdict"] == "PASS" and complete["run_id"] == d2_run.identity.run_id
    assert d2_run.identity.run_id.startswith("dneigh1-")
    manifest = d2_run.query_manifest
    assert manifest["hash_string"] == "Q|20260917|{D}|{ticker}"
    assert manifest["content_digest"] == d2_run.digests["query_samples"]
    assert manifest["rows"] == sum(manifest["sampled_by_date"].values())


def test_no_artifact_column_carries_an_outcome(d2_run):
    """Alpha blindness is checkable by schema: D2's tables have nowhere to put a label value."""
    import pyarrow.parquet as pq

    for path in sorted(d2_run.run_dir.glob("*.parquet")):
        for name in pq.read_schema(path).names:
            assert not any(bad in name.lower() for bad in FORBIDDEN_COLUMNS), (path.name, name)


def code_identifiers(path: Path) -> set[str]:
    """Every name and string literal the module actually executes; prose in docstrings is not code."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            found.add(node.attr.lower())
        elif isinstance(node, ast.arg):
            found.add(node.arg.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name.lower())
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg.lower())
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            found.add(node.value.lower())
    return found


#: The modules D2 runs on. D3 legitimately computes label values, so the scan below names the
#: D2 set explicitly rather than globbing a package that later phases also live in.
D2_MODULES = ("config.py", "models.py", "identity.py", "source.py", "universe.py",
              "label_extension.py", "sampling.py", "encoder.py", "library.py", "similarity.py",
              "neighbor_search.py", "pit.py", "artifacts.py", "d2.py")
#: What D2 may not even reach for: the modules that turn a future bar into a number.
LABEL_VALUE_MODULES = ("labels", "signal", "evaluation", "d3")


def test_the_d_package_cannot_evaluate_what_it_finds():
    """§31: no D2 module names, computes or writes an outcome, only the identity of a neighbour."""
    for name in D2_MODULES:
        path = PACKAGE / name
        assert path.exists(), name
        identifiers = code_identifiers(path)
        for identifier in FORBIDDEN_IDENTIFIERS:
            hits = [found for found in identifiers if identifier in found]
            assert not hits, (name, identifier, hits)


def test_no_d2_module_can_reach_the_label_value_path():
    """The structural half of the same guarantee: D2 cannot import what computes a return."""
    for name in D2_MODULES:
        for imported in imported_names(PACKAGE / name):
            tail = imported.rsplit(".", 1)[-1]
            assert tail not in LABEL_VALUE_MODULES, (name, imported)


def test_the_pit_runtime_record_shows_the_invariants_ran(d2_run):
    checks = d2_run.pit_runtime["invariant_checks"]
    for code in ("R1", "R2", "R3", "R4", "R6", "R7", "R9", "R10", "R11", "R12"):
        assert checks.get(code, 0) > 0, code
    assert d2_run.pit_runtime["hard_failures"] == 0
    assert d2_run.pit_runtime["violations"] == 0
    assert d2_run.summary["input_read_set"]["match"] is True
    assert d2_run.summary["input_read_set"]["pre"] == d2_run.summary["input_read_set"]["post"]


def test_two_identical_runs_agree_on_every_content_digest(dataset, rules, tmp_path):
    expected = history_of(dataset, rules).freeze
    first = d2.execute(dataset, fixtures.SNAPSHOT_ID, expected=expected, parent_run="dpit1-fixture",
                       rules=rules, runs_dir=tmp_path / "one", log=lambda _: None,
                       eval_date_limit=2, audit_count=1, capture_audit=False)
    second = d2.execute(dataset, fixtures.SNAPSHOT_ID, expected=expected, parent_run="dpit1-fixture",
                        rules=rules, runs_dir=tmp_path / "two", log=lambda _: None,
                        eval_date_limit=2, audit_count=1, capture_audit=False)
    assert first.digests == second.digests
    assert first.identity.digest == second.identity.digest
    assert first.summary["library"] == second.summary["library"]
    assert first.summary["status_by_test"] == second.summary["status_by_test"]


def test_t1_a_truncated_dataset_reproduces_the_neighbour_lists(d2_run):
    """D0 PIT #1 on the real pipeline: re-run the date on data that physically ends there."""
    audit = pit_audit.audit_run(d2_run.context.history, d2_run.context.rules,
                                d2_run.context.audit_dates, d2_run.context.capture)
    assert audit["passed"], audit["mismatched"]
    assert audit["compared_queries"] > 0
    assert audit["compared_neighbors"] > 0


def test_the_library_manifest_covers_every_test_of_its_window(d2_run, rules):
    manifest = d2_run.library_manifest
    for window in rules.pattern_windows:
        entry = manifest[f"W{window}"]
        horizons = sorted(h for w, h in rules.combinations if w == window)
        assert entry["window"] == window and entry["rows"] > 0
        assert sorted(int(h) for h in entry["valid_by_horizon"]) == horizons
        assert set(entry["rows_by_test"]) == {f"{rep}_H{h}" for h in horizons for rep in ("A", "B")}
        assert all(count <= entry["rows"] for count in entry["rows_by_test"].values())
        # The last library end a query can reach: eval_end - W - min(h) (D2 design §13).
        assert entry["last_end_idx"] <= d2_run.summary["evaluation"]["eval_range"][1] - window \
            - min(horizons)


def test_a_query_with_too_few_candidates_is_recorded_and_not_dropped(d2_run, rules):
    """The fixture universe is far smaller than K=50, so every query exercises the R16 path."""
    for test in rules.tests:
        statuses = d2_run.summary["status_by_test"][test.name]
        assert sum(statuses.values()) > 0
        assert statuses[QueryStatus.INSUFFICIENT_NEIGHBORS.value] > 0
        assert d2_run.summary["insufficient_by_test"][test.name]["queries"] > 0


def test_the_real_search_agrees_with_a_full_sort_on_the_fixture(d2_run, rules):
    """§26 on data that came through the whole pipeline, not only on hand-made score vectors."""
    history, index = d2_run.context.history, d2_run.context.audit_dates[0]
    validity = label_extension.compute_validity(
        history.panel, tuple(sorted({h for _, h in rules.combinations})), rules.ca_suspect_ratio)
    log = pit.InvariantLog()
    eligibility = d2.Eligibility(history, rules, log)
    figi = library.FigiCoder()
    checked = 0
    for test in rules.tests:
        lib = library.build(history, rules, window=test.window, horizons=(test.horizon,),
                            validity=validity.valid, eligibility=eligibility,
                            eval_end_idx=index + test.window + test.horizon, figi=figi)
        queries = d2.encode_queries(history, eligibility, rules, test.window, (index,), figi, log)
        entry = queries[index]
        encoded = entry.encoded[test.representation]
        compact = lib.compact_rows(test.representation, test.horizon)
        view = pit.EmbargoView(index, test.window, test.horizon)
        cut = neighbor_search.embargo_cut(lib.end_idx[compact], view)
        if cut == 0 or not encoded.defined.any():
            continue
        vectors = np.ascontiguousarray(lib.vectors[test.representation][compact][:cut])
        ends = np.ascontiguousarray(lib.end_idx[compact][:cut])
        cols = np.ascontiguousarray(lib.ticker_col[compact][:cut])
        codes = np.ascontiguousarray(lib.figi_code[compact][:cut])
        scores = similarity.score_block(similarity.METRIC_OF[test.representation],
                                        encoded.vectors[encoded.defined], vectors,
                                        np.einsum("ij,ij->i", vectors, vectors))
        for row, position in enumerate(np.nonzero(encoded.defined)[0]):
            drop = neighbor_search.drop_same_symbol(cols, codes,
                                                    int(entry.sample.ticker_col[position]),
                                                    int(entry.figi_code[position]))
            optimised, _ = neighbor_search.select(scores.rank_score[row], drop, cols, ends,
                                                  top_k=rules.top_k,
                                                  ticker_cap=rules.max_windows_per_ticker,
                                                  date_cap=rules.max_neighbors_per_end_date)
            full = neighbor_search.select_full_sort(scores.rank_score[row], drop, cols, ends,
                                                    top_k=rules.top_k,
                                                    ticker_cap=rules.max_windows_per_ticker,
                                                    date_cap=rules.max_neighbors_per_end_date)
            assert optimised == full
            checked += 1
    assert checked > 0


def test_a_library_with_a_repeated_key_is_a_hard_fail():
    with pytest.raises(HardFail) as caught:
        pit.assert_library_index(np.array([60, 60, 65], dtype=np.int32),
                                 np.array([1, 1, 0], dtype=np.int32), stride=5, minimum=60)
    assert caught.value.code == "R11"
    pit.assert_library_index(np.array([60, 60, 65], dtype=np.int32),
                             np.array([0, 1, 0], dtype=np.int32), stride=5, minimum=60)


# --- 6. Import boundaries (T13, T14) ------------------------------------------------------

def imported_names(path: Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
    return out


def test_t13_the_search_path_never_imports_the_label_path():
    """R13: sampling, encoding, similarity and the universe rule cannot see a label at all."""
    for name in ("encoder.py", "similarity.py", "universe.py", "sampling.py", "neighbor_search.py"):
        for imported in imported_names(PACKAGE / name):
            assert "label" not in imported and "signal" not in imported, (name, imported)


def test_t14_the_d2_modules_stay_inside_the_declared_import_boundary():
    from tests.strategy_d.test_strategy_d_d1 import FORBIDDEN_PREFIXES

    new_modules = ("encoder.py", "similarity.py", "library.py", "neighbor_search.py", "pit.py",
                   "sampling.py", "label_extension.py", "artifacts.py", "pit_audit.py", "d2.py")
    for name in new_modules:
        assert (PACKAGE / name).exists(), name
        for imported in imported_names(PACKAGE / name):
            assert not imported.startswith(FORBIDDEN_PREFIXES), (name, imported)


# --- 7. Artifact identity and input immutability (Pre-flight §9.2, §24) --------------------

def test_every_artifact_names_the_dataset_and_rules_it_came_from(d2_run, dataset):
    """Pre-flight §9.2: freeze mixing has to be detectable from any single file D3 picks up."""
    import json as json_module

    import pyarrow.parquet as pq

    from app.backtest.strategy_d_analog import artifacts

    run_dir = d2_run.run_dir
    if not (run_dir / "COMPLETE.json").exists():
        d2.finish(d2_run, d2.run_context(dataset, fixtures.SNAPSHOT_ID))
    expected = d2_run.summary["artifact_identity"]
    assert set(expected) == set(artifacts.IDENTITY_FIELDS)
    for path in sorted(run_dir.glob("*.parquet")):
        assert artifacts.read_identity(path) == expected, path.name
    for path in sorted(run_dir.glob("*.json")):
        payload = json_module.loads(path.read_text(encoding="utf-8"))
        for field in artifacts.IDENTITY_FIELDS:
            assert payload.get(field) == expected[field], (path.name, field)
    assert pq.read_schema(run_dir / "query_samples.parquet").metadata is not None


def test_a_table_cannot_be_written_without_an_identity(tmp_path):
    import pyarrow as pa

    from app.backtest.strategy_d_analog import artifacts

    target = tmp_path / "unused.parquet"
    with pytest.raises(HardFail) as caught:
        artifacts.write_table(target, pa.table({"a": [1]}), {"freeze_id": "X"})
    assert caught.value.code == "F1"
    assert not target.exists()
    with pytest.raises(HardFail):
        artifacts.write_json(tmp_path / "unused.json", {"a": 1}, {"freeze_id": "X"})


def test_the_run_identity_records_the_policy_and_the_label_contract(d2_run, rules):
    payload = d2_run.identity.payload
    assert payload["label_validity_contract"] == d2.LABEL_VALIDITY_CONTRACT
    policy = payload["library_policy"]
    assert policy["stride"] == rules.library_stride and policy["anchor"] == 0
    assert policy["ticker_cap"] == rules.max_windows_per_ticker == 1
    assert policy["date_cap"] == rules.max_neighbors_per_end_date == 5
    assert policy["embargo"] == "d + h <= D - W"
    assert payload["top_k"] == rules.top_k == 50
    assert len(payload["tests"]) == 14
    assert payload["implementation"]["query_chunk"] == neighbor_search.QUERY_CHUNK


def test_the_read_set_digest_moves_when_a_frozen_file_moves(tmp_path, rules):
    """§24: another writer may touch the workspace, but never D's own read set."""
    from app.backtest.strategy_d_analog.source import read_set_digest

    root = tmp_path / "immutable" / "1_US-B"
    fixtures.build(root)
    before = read_set_digest(root, fixtures.SNAPSHOT_ID)
    assert before == history_of(root, rules).freeze.d_read_digest
    assert before == read_set_digest(root, fixtures.SNAPSHOT_ID)

    (root / "market_data/raw/massive/minute").mkdir(parents=True)   # a writer outside the read set
    (root / "market_data/raw/massive/minute/new.json").write_text("{}", encoding="utf-8")
    assert read_set_digest(root, fixtures.SNAPSHOT_ID) == before

    victim = sorted((root / "market_data/raw/massive/grouped_daily").rglob("*.json.gz"))[0]
    victim.write_bytes(victim.read_bytes() + b"\x00")
    assert read_set_digest(root, fixtures.SNAPSHOT_ID) != before
    with pytest.raises(HardFail) as caught:
        history_of(root, rules)
    assert caught.value.code == "R2"


def test_r11_rejects_a_library_row_off_the_stride_or_before_the_seasoning(rules):
    good_end = np.array([60, 60, 65], dtype=np.int32)
    good_col = np.array([0, 1, 0], dtype=np.int32)
    pit.assert_library_index(good_end, good_col, stride=rules.library_stride,
                             minimum=rules.seasoning_sessions)
    for end in (np.array([60, 62], dtype=np.int32), np.array([55, 60], dtype=np.int32)):
        with pytest.raises(HardFail) as caught:
            pit.assert_library_index(end, np.array([0, 0], dtype=np.int32),
                                     stride=rules.library_stride,
                                     minimum=rules.seasoning_sessions)
        assert caught.value.code == "R11"
