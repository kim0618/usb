"""D-AGG-2 tests: selection, ratios, bootstrap, blocks, concentration, gate, parent binding.

Hand-checkable synthetic tables only, except the parent tamper test, which copies the real
D-AGG-1 run directory into a temp dir and never prints or aggregates its contents.
"""

import ast
from fractions import Fraction
from pathlib import Path
import shutil

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.backtest.strategy_d_agg import config, d2, gate, ratios, scoring, setup
from app.backtest.strategy_d_agg.models import HardFail

PACKAGE = Path(config.__file__).resolve().parent
SPEC = gate.spec_from_rules(config.load_rules().raw)


# -- selection -------------------------------------------------------------------------------------

def test_quota_is_exact_ceiling():
    q = Fraction(1, 10)
    assert [setup.quota(n, q) for n in (290, 291, 299, 300, 301)] == [29, 30, 30, 30, 31]
    assert setup.quota(300, setup.exact_fraction("0.05")) == 15
    assert setup.quota(300, setup.exact_fraction("0.02")) == 6


def test_selection_by_a_desc_with_sample_rank_ties():
    session = np.zeros(10, dtype=np.int64)
    rank = np.arange(10)
    analog = np.array([0.1, 0.5, 0.5, 0.2, 0.3, 0.0, -0.1, 0.4, 0.5, 0.05])
    ok = np.ones(10, dtype=bool)
    chosen = setup.select(session, rank, analog, ok, Fraction(1, 5))       # 2 of 10
    assert np.flatnonzero(chosen).tolist() == [1, 2]                        # 0.5 ties: rank 1, 2 before 8


def test_selection_ignores_not_ok_and_is_per_session():
    session = np.array([0, 0, 0, 1, 1, 1])
    analog = np.array([9.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    ok = np.array([False, True, True, True, True, True])
    chosen = setup.select(session, np.arange(6), analog, ok, Fraction(1, 3))
    assert np.flatnonzero(chosen).tolist() == [2, 5]


def test_selection_signature_and_imports_see_no_outcome():
    tree = ast.parse((PACKAGE / "setup.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and m.endswith(("excursions", "labels", "d1", "d2", "scoring", "evaluation_labels"))
                   for m in mods)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "select")
    assert [a.arg for a in fn.args.args] == ["session_idx", "sample_rank", "analog", "signal_ok", "quantile"]


# -- ratios ----------------------------------------------------------------------------------------

def stats(n, k_up, k_dn, m_up, m_dn):
    return ratios.SessionStats(np.arange(len(n)), np.array(n), np.array(k_up), np.array(k_dn),
                               np.array(m_up, dtype=float), np.array(m_dn, dtype=float))


def test_tl_dl_ntl_ag_exact():
    s = stats([4, 4], [2, 2], [1, 0], [0.06, 0.04], [0.03, 0.05])
    u = stats([8, 4], [2, 2], [2, 1], [0.05, 0.03], [0.04, 0.04])
    l = ratios.lifts(s, u)
    assert l.tl == pytest.approx((0.5 + 0.5) / (0.25 + 0.5))
    assert l.dl == pytest.approx((0.25 + 0.0) / (0.25 + 0.25))
    assert l.ntl == pytest.approx(l.tl / l.dl)
    assert l.ag == pytest.approx(((0.06 + 0.04) / (0.03 + 0.05)) / ((0.05 + 0.03) / (0.04 + 0.04)))
    assert l.tep == pytest.approx(np.mean([0.25, 0.0]))


def test_session_stats_counts_and_medians():
    session = np.array([5, 5, 5, 7, 7])
    valid = np.array([True, True, False, True, True])
    up = np.array([True, False, True, False, False])
    dn = np.array([False, True, False, False, True])
    mfe = np.array([0.2, 0.01, 0.5, 0.03, 0.05])
    mae = np.array([-0.01, -0.12, -0.5, -0.02, -0.11])
    st = ratios.session_stats(np.array([5, 7]), session, np.ones(5, bool), valid, up, dn, mfe, mae)
    assert st.n.tolist() == [2, 2] and st.k_up.tolist() == [1, 0] and st.k_dn.tolist() == [1, 1]
    assert st.m_up.tolist() == pytest.approx([0.105, 0.04]) and st.m_dn.tolist() == pytest.approx([0.065, 0.065])


def test_zero_tl_denominator_is_hard_fail():
    with pytest.raises(HardFail):
        ratios.lifts(stats([2], [1], [0], [0.1], [0.1]), stats([2], [0], [1], [0.1], [0.1]))


def test_bootstrap_is_deterministic_and_uses_one_draw_for_both_sums():
    params = d2.bootstrap_params(config.load_rules())
    draws = d2.draws_fn(params)(40)
    assert np.array_equal(draws, d2.draws_fn(params)(40))
    rng = np.random.default_rng(3)
    s = stats(rng.integers(20, 30, 40), rng.integers(0, 8, 40), rng.integers(0, 8, 40),
              rng.uniform(0.02, 0.06, 40), rng.uniform(0.02, 0.06, 40))
    u = stats(np.full(40, 2600), rng.integers(100, 400, 40), rng.integers(100, 400, 40),
              rng.uniform(0.02, 0.06, 40), rng.uniform(0.02, 0.06, 40))
    boot = ratios.bootstrap(s, u, draws)
    row = draws[17]
    assert boot.tl[17] == pytest.approx(s.p_up[row].sum() / u.p_up[row].sum())
    assert boot.ag[17] == pytest.approx((s.m_up[row].sum() / s.m_dn[row].sum())
                                        / (u.m_up[row].sum() / u.m_dn[row].sum()))
    assert draws.shape == (10000, 40)


def test_four_blocks_of_221():
    assert [p.size for p in d2.partition_fn(221)] == [56, 55, 55, 55]


def test_top_positions_tie_rule():
    assert ratios.top_positions(np.array([1.0, 3.0, 3.0, 2.0]), 2).tolist() == [1, 2]
    assert ratios.top_positions(np.array([5.0, 5.0, 5.0]), 2).tolist() == [0, 1]


# -- synthetic end to end --------------------------------------------------------------------------

def synthetic(sessions=12, per=40, width=60, seed=1, hot_ticker=None):
    rng = np.random.default_rng(seed)
    qs = np.repeat(np.arange(sessions), per)
    cols = np.concatenate([rng.choice(width, per, replace=False) for _ in range(sessions)])
    analog = rng.normal(size=qs.size)
    if hot_ticker is not None:
        analog[cols == hot_ticker] = 5.0                  # always selected when sampled
    up = rng.random(qs.size) < (0.1 + 0.2 * (analog > 1.2))
    if hot_ticker is not None:
        up = up | (cols == hot_ticker)
    down = rng.random(qs.size) < 0.08
    mfe = np.where(up, 0.12, 0.04) + rng.uniform(0, 0.01, qs.size)
    mae = np.where(down, -0.12, -0.03) - rng.uniform(0, 0.01, qs.size)
    q = scoring.Queries(session=qs, ticker_col=cols, valid=np.ones(qs.size, bool),
                        status=np.zeros(qs.size, np.int64), up=up, down=down, mfe=mfe, mae=mae,
                        sample_rank=np.tile(np.arange(per), sessions), analog=analog,
                        signal_ok=np.ones(qs.size, bool))
    us = np.repeat(np.arange(sessions), width)
    ucols = np.tile(np.arange(width), sessions)
    uup = rng.random(us.size) < 0.12
    udn = rng.random(us.size) < 0.08
    u = scoring.Rows(session=us, ticker_col=ucols, valid=np.ones(us.size, bool),
                     status=np.zeros(us.size, np.int64), up=uup, down=udn,
                     mfe=np.where(uup, 0.12, 0.04), mae=np.where(udn, -0.12, -0.03))
    return q, u


def run_primary(q, u, minimum=4):
    return scoring.primary(q, u, quantile=Fraction(1, 10), minimum=minimum,
                           draws_fn=lambda n: np.random.default_rng(0).integers(0, n, (200, n)),
                           partition_fn=d2.partition_fn, spec=SPEC, pit_violations=0)


def test_primary_runs_and_matches_hand_tl():
    q, u = synthetic()
    prim = run_primary(q, u)
    s, c = prim["setup_stats"], prim["universe_stats"]
    assert prim["metrics"]["TL"] == pytest.approx(s.p_up.sum() / c.p_up.sum())
    assert prim["detail"]["setup"]["selected_before_validity"] == 12 * 4


def test_invalid_rows_do_not_change_selection_and_count_in_h3():
    q, u = synthetic()
    base = run_primary(q, u)["chosen"]
    q.valid[np.flatnonzero(base)[:3]] = False
    prim = run_primary(q, u)
    assert np.array_equal(prim["chosen"], base)
    assert prim["detail"]["setup"]["invalid_after_geometry"] == 3
    assert prim["metrics"]["setup_invalid_share"] == pytest.approx(3 / base.sum())


def test_thin_session_is_dropped_from_both_groups():
    q, u = synthetic()
    first = np.flatnonzero(run_primary(q, u)["chosen"] & (q.session == 0))
    q.valid[first[:2]] = False                           # 2 of 4 left
    prim = run_primary(q, u, minimum=3)
    assert 0 not in prim["sessions"].tolist()
    assert prim["detail"]["setup"]["sessions_dropped_thin"] == [0]


def test_leave_top10_tickers_removes_rows_from_both_groups():
    q, u = synthetic(hot_ticker=7)
    prim = run_primary(q, u)
    assert 7 in prim["detail"]["concentration"]["top10_ticker_cols"]
    manual = scoring.leave_tickers_tl(q, u, prim["chosen"], prim["sessions"],
                                      np.array(prim["detail"]["concentration"]["top10_ticker_cols"]), 4)
    assert prim["metrics"]["leave_top10_tickers_TL"] == manual["tl"]


def test_leave_top5_sessions_drops_the_largest_excess():
    q, u = synthetic()
    prim = run_primary(q, u)
    e = ratios.session_excess(prim["setup_stats"], prim["universe_stats"])
    top = set(prim["sessions"][np.argsort(-e, kind="stable")[:5]].tolist())
    assert set(prim["detail"]["concentration"]["top5_sessions"]) == top


def test_single_ticker_share():
    q, u = synthetic(hot_ticker=7)
    prim = run_primary(q, u)
    sel = prim["_in_eval"]
    ups = q.up[sel]
    share = max((ups & (q.ticker_col[sel] == c)).sum() for c in np.unique(q.ticker_col[sel])) / ups.sum()
    assert prim["metrics"]["single_ticker_share"] == pytest.approx(share)


# -- gate ------------------------------------------------------------------------------------------

PASSING = {"evaluable_sessions": 221, "valid_setup_rows": 6600, "unique_setup_tickers": 900,
           "setup_up_events": 1000, "pit_violations": 0, "setup_invalid_share": 0.002,
           "universe_invalid_share": 0.002, "TL": 1.30, "NTL": 1.15, "AG": 1.02,
           "single_ticker_share": 0.01, "TL_ci_low": 1.05, "TL_ci_high": 1.6,
           "blocks_tl_above_one": 4, "leave_top5_sessions_TL": 1.2, "leave_top10_tickers_TL": 1.2}


def verdict(**change):
    return gate.evaluate({**PASSING, **change}, SPEC)["verdict"]


def test_gate_pass_and_threshold_boundaries():
    assert verdict() == "D_AGG_SCREEN_PASS"
    assert verdict(TL=1.25) == "D_AGG_SCREEN_PASS"                     # >= inclusive
    assert verdict(NTL=1.10, leave_top5_sessions_TL=1.10) == "D_AGG_SCREEN_PASS"
    assert verdict(TL_ci_low=1.00) == "D_AGG_SCREEN_BORDERLINE"          # > strict


def test_gate_borderline_exactly_one():
    assert verdict(TL=1.2) == "D_AGG_SCREEN_BORDERLINE"
    assert verdict(blocks_tl_above_one=2) == "D_AGG_SCREEN_BORDERLINE"
    assert verdict(NTL=1.0) == "D_AGG_SCREEN_BORDERLINE"


def test_gate_two_soft_misses_or_below_floor_fail():
    assert verdict(TL=1.2, blocks_tl_above_one=2) == "D_AGG_SCREEN_FAIL"
    assert verdict(TL=1.09) == "D_AGG_SCREEN_FAIL"
    assert verdict(leave_top10_tickers_TL=1.00) == "D_AGG_SCREEN_FAIL"   # floor is > 1.00
    assert verdict(TL_ci_low=0.95) == "D_AGG_SCREEN_FAIL"                 # floor is > 0.95
    assert verdict(blocks_tl_above_one=1) == "D_AGG_SCREEN_FAIL"


def test_gate_hard_fails():
    assert verdict(NTL=0.99) == "D_AGG_SCREEN_FAIL"
    assert verdict(AG=0.999) == "D_AGG_SCREEN_FAIL"
    assert verdict(single_ticker_share=0.051) == "D_AGG_SCREEN_FAIL"
    assert verdict(setup_up_events=249) == "D_AGG_SCREEN_FAIL"
    assert verdict(pit_violations=1) == "D_AGG_SCREEN_FAIL"


def test_gate_h3_missingness():
    assert verdict(setup_invalid_share=0.004, universe_invalid_share=0.002) == "D_AGG_SCREEN_PASS"
    assert verdict(setup_invalid_share=0.0041, universe_invalid_share=0.002) == "D_AGG_SCREEN_FAIL"
    assert verdict(setup_invalid_share=0.021, universe_invalid_share=0.02) == "D_AGG_SCREEN_FAIL"


def test_gate_inverse_effect():
    assert verdict(TL=0.8, TL_ci_low=0.7, TL_ci_high=0.95) == "INVERSE_EFFECT"
    assert verdict(TL=0.95, TL_ci_low=0.8, TL_ci_high=1.0) == "D_AGG_SCREEN_FAIL"


def test_secondary_cannot_overturn():
    base = gate.evaluate({**PASSING, "TL": 1.0}, SPEC)
    noisy = gate.evaluate({**PASSING, "TL": 1.0, "X-6": {"TL": 9.9}, "X-7": 5.0, "pooled_TL": 3.0}, SPEC)
    assert base == noisy and base["verdict"] == "D_AGG_SCREEN_FAIL"
    assert base["secondary_cannot_overturn"] is True


def test_gate_module_is_pure():
    tree = ast.parse((PACKAGE / "gate.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not ({"json", "pathlib", "pyarrow", "pyarrow.parquet", "numpy"} & mods)
    assert all(m == "app.backtest.strategy_d_agg.models" for m in mods if m and m.startswith("app"))


def test_gate_spec_matches_contract():
    assert (SPEC.t1, SPEC.t2, SPEC.t3, SPEC.t4, SPEC.t5, SPEC.t6) == (
        (1.25, 1.10), (1.00, 0.95), (3.0, 2.0), (1.10, 1.00), (1.10, 1.00), (1.10, 1.00))
    assert SPEC.h1 == (150.0, 5000.0, 500.0, 250.0) and SPEC.h7 == 0.05


# -- parent binding --------------------------------------------------------------------------------

D1_DIR = config.RUNS_DIR / d2.D1_RUN_ID


@pytest.mark.skipif(not D1_DIR.exists(), reason="D-AGG-1 run store not on this machine")
def test_parent_d1_binds_and_rejects_tampering(tmp_path):
    rules = config.load_rules()
    d2.load_parent_d1(rules)                                            # the real one binds
    copy = tmp_path / d2.D1_RUN_ID
    shutil.copytree(D1_DIR, copy)
    table = pq.read_table(copy / config.QUERY_TABLE)
    flags = table.column("up10").to_numpy(zero_copy_only=False).copy()
    flags[0] = not flags[0]
    tampered = table.set_column(table.column_names.index("up10"), "up10", pa.array(flags))
    pq.write_table(tampered.replace_schema_metadata(table.schema.metadata), copy / config.QUERY_TABLE)
    with pytest.raises(HardFail):
        d2.load_parent_d1(rules, runs_dir=tmp_path)


def test_secondary_slots_all_compute_on_synthetic():
    q, u = synthetic()
    rng = np.random.default_rng(9)
    q.rv20, u.rv20 = rng.uniform(0.01, 0.05, len(q)), rng.uniform(0.01, 0.05, len(u))
    q.close_return, u.close_return = rng.normal(0, 0.05, len(q)), rng.normal(0, 0.05, len(u))
    q.excess_return, u.excess_return = q.close_return - 0.001, u.close_return - 0.001
    q.b0 = rng.normal(size=len(q))
    prim = run_primary(q, u)
    sec = scoring.secondary(q, u, prim, minimum=4)
    assert [f"X-{i}" for i in range(1, 13)] == [k for k in sec if k.startswith("X-")]
    assert all(sec[k] != "NOT_COMPUTED" for k in sec if k.startswith("X-"))
    assert len(sec["X-10"]["deciles"]) == 10 and len(sec["X-12"]) == 4
    assert sec["secondary_cannot_overturn"] is True
    assert gate.evaluate(prim["metrics"], SPEC) == prim["gate"]
