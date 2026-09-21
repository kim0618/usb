"""D-AGG-4 tests: bracket execution, control, noise reproduction, gate, PIT audit. Synthetic only."""

import ast
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_d_agg import backtest, config, control, d4, evaluation4 as ev, excursions, pit4
from app.backtest.strategy_d_agg.models import HardFail
from tests.strategy_d_agg.test_d_agg_d1 import D, GEOMETRY, T, drop_bar, hand_panel, set_cell
from tests.strategy_d_v2.fixtures import make_panel

PACKAGE = Path(config.__file__).resolve().parent


def trade(panel, row=D, **kw):
    ex = excursions.compute(panel, **GEOMETRY)
    s, j = np.array([row]), np.array([0])
    t = backtest.execute(s, j, ex.valid[s, j], backtest.adjusted(panel), **kw)
    return {"gross": t.gross[0], "reason": backtest.EXIT_REASONS[t.reason[0]], "k": int(t.exit_offset[0]),
            "entry": t.entry_price[0], "exit": t.exit_price[0], "valid": bool(ex.valid[row, 0])}


def with_open(panel, row, value):
    p = set_cell(panel, "open", row, value)
    p = set_cell(p, "high", row, max(p.high[row, 0], value))
    return set_cell(p, "low", row, min(p.low[row, 0], value))


# -- execution -------------------------------------------------------------------------------------

def test_entry_is_d_plus_1_open_and_d_is_ignored():
    base = trade(hand_panel(highs=(101, 102, 103, 102, 101), lows=(99, 98, 97, 98, 99)))
    assert base["entry"] == 100.0 and base["reason"] == "TIME"
    wild = set_cell(set_cell(hand_panel(highs=(101, 102, 103, 102, 101), lows=(99, 98, 97, 98, 99)),
                             "high", D, 500.0), "low", D, 1.0)
    assert trade(wild)["gross"] == base["gross"]


def test_tp_exact_boundary_under_float_error():
    t = trade(hand_panel(highs=(3.05, 3.1, 3.3, 3.2, 3.1), lows=(2.95,) * 5, entry=3.0))
    assert t["reason"] == "TP" and t["k"] == 3 and t["gross"] == 0.10


def test_tp_just_below_is_not_touched():
    t = trade(hand_panel(highs=(101, 105, 109.999, 104, 103), lows=(99, 98, 97, 98, 99)))
    assert t["reason"] == "TIME"


def test_sl_exact_boundary():
    t = trade(hand_panel(highs=(3.05,) * 5, lows=(2.95, 2.9, 2.7, 2.8, 2.9), entry=3.0))
    assert t["reason"] == "SL" and t["k"] == 3 and t["gross"] == -0.10


def test_same_bar_sl_first_and_tp_first_secondary():
    panel = hand_panel(highs=(101, 111, 104, 103, 102), lows=(99, 89, 95, 96, 97))
    assert trade(panel)["reason"] == "SAME_BAR_SL" and trade(panel)["gross"] == -0.10
    s2 = trade(panel, same_bar="TP_FIRST")
    assert s2["reason"] == "SAME_BAR_TP" and s2["gross"] == 0.10


def test_same_bar_on_entry_session():
    t = trade(hand_panel(highs=(112, 101, 101, 101, 101), lows=(88, 99, 99, 99, 99)))
    assert t["reason"] == "SAME_BAR_SL" and t["k"] == 1


def test_gap_above_tp_exits_at_open():
    panel = with_open(hand_panel(highs=(101, 102, 113, 102, 101), lows=(99, 98, 97, 98, 99)), D + 3, 112.0)
    t = trade(panel)
    assert t["reason"] == "GAP_TP" and t["k"] == 3 and t["gross"] == pytest.approx(0.12)


def test_gap_below_sl_exits_at_open():
    panel = with_open(hand_panel(highs=(101, 102, 103, 102, 101), lows=(99, 98, 84, 98, 99)), D + 3, 85.0)
    t = trade(panel)
    assert t["reason"] == "GAP_SL" and t["gross"] == pytest.approx(-0.15)


def test_entry_print_is_never_a_gap_exit():
    t = trade(hand_panel(highs=(101, 102, 103, 102, 101), lows=(99, 98, 97, 98, 99), entry=100.0))
    assert t["reason"] != "GAP_TP" and t["k"] == 5


def test_time_exit_at_d5_close():
    panel = set_cell(hand_panel(highs=(101, 102, 103, 104, 105), lows=(99, 98, 97, 98, 99)), "close", D + 5, 104.0)
    t = trade(panel)
    assert t["reason"] == "TIME" and t["k"] == 5 and t["gross"] == pytest.approx(0.04)


def test_d6_is_never_used():
    base = trade(hand_panel(highs=(101, 102, 103, 102, 101), lows=(99, 98, 97, 98, 99)))
    moved = trade(set_cell(hand_panel(highs=(101, 102, 103, 102, 101), lows=(99, 98, 97, 98, 99)), "high", D + 6, 900.0))
    assert moved["gross"] == base["gross"] and moved["reason"] == base["reason"]


def test_middle_missing_session_is_skipped():
    panel = drop_bar(hand_panel(highs=(101, 105, 112, 105, 103), lows=(99, 98, 97, 98, 99)), D + 3)
    t = trade(panel)
    assert t["valid"] and t["reason"] == "TIME" and t["k"] == 5


def test_missing_final_bar_and_entry_bar_are_no_trade():
    for row in (D + 5, D + 1):
        t = trade(drop_bar(hand_panel(), row))
        assert not t["valid"] and t["reason"] == "NO_TRADE" and np.isnan(t["gross"]) and t["k"] == 0


def test_ca_suspect_is_no_trade():
    jumped = hand_panel()
    for row in range(D + 2, T):
        for f in ("open", "high", "low", "close"):
            jumped = set_cell(jumped, f, row, getattr(jumped, f)[row, 0] * 4.0)
    assert trade(jumped)["reason"] == "NO_TRADE"


def test_cost_is_round_trip_subtraction():
    t = backtest.Trades(np.array([100.0]), np.array([110.0]), np.array([1]), np.array([1]), np.array([0.10]))
    assert t.net(backtest.PRIMARY_COST)[0] == pytest.approx(0.099)
    assert t.net(d4.SECONDARY_COST)[0] == pytest.approx(0.098)


def test_split_inside_trade_window_is_neutral():
    from tests.strategy_d_agg.test_d_agg_d1 import _split_from
    base = trade(hand_panel(highs=(101, 105, 112, 105, 103), lows=(99, 98, 97, 98, 99)))
    split = trade(_split_from(hand_panel(highs=(101, 105, 112, 105, 103), lows=(99, 98, 97, 98, 99)), D + 2, 1.0, 2.0))
    assert split["reason"] == base["reason"] and split["gross"] == pytest.approx(base["gross"], abs=1e-12)


# -- control -------------------------------------------------------------------------------------

def test_buckets_are_session_deciles_without_validity():
    s = np.repeat([0, 1], 20)
    rv = np.concatenate([np.arange(20.0), np.arange(20.0)[::-1] * 2])
    b = control.buckets(s, rv, np.array([0, 1]))
    assert b[:20].tolist() == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9]
    fn = next(n for n in ast.parse((PACKAGE / "control.py").read_text()).body
              if isinstance(n, ast.FunctionDef) and n.name == "buckets")
    assert [a.arg for a in fn.args.args] == ["session_idx", "rv20", "sessions"]   # no validity, no outcome


def test_session_delta_weights_exclusion_and_exact_value():
    # 22 setup rows in bucket 0 (net 0.02) and 22 in bucket 1 (net 0.04); control: bucket 0 net 0.01, bucket 1 net 0.00/0.02
    n = 22
    s = np.zeros(2 * n + 5, dtype=np.int64)
    bucket = np.array([0] * n + [1] * n + [0, 1, 1, 0, 1])
    is_setup = np.array([True] * (2 * n) + [False] * 5)
    valid = np.ones(s.size, dtype=bool)
    valid[-1] = False                                           # invalid control row is ignored
    net = np.array([0.02] * n + [0.04] * n + [0.01, 0.00, 0.02, 0.01, 9.99])
    sd = control.session_delta(s, bucket, is_setup, valid, net, np.array([0]))
    assert sd.e_s[0] == pytest.approx(0.03)
    assert sd.e_c[0] == pytest.approx(0.5 * 0.01 + 0.5 * 0.01)
    assert sd.delta[0] == pytest.approx(0.02)


def test_empty_bucket_and_thin_session_are_dropped():
    s = np.zeros(25, dtype=np.int64)
    bucket = np.array([3] * 20 + [0] * 5)
    is_setup = np.array([True] * 20 + [False] * 5)
    sd = control.session_delta(s, bucket, is_setup, np.ones(25, bool), np.zeros(25), np.array([0]))
    assert sd.dropped_empty_bucket == (0,) and sd.sessions.size == 0
    thin = control.session_delta(s, bucket, is_setup, np.array([True] * 19 + [False] + [True] * 5),
                                 np.zeros(25), np.array([0]))
    assert thin.dropped_thin == (0,)


def test_ticker_contributions_and_concentration():
    sd = control.SessionDelta(np.array([0]), np.array([0.0]), np.array([0.01]), np.array([0.0]),
                              np.array([2]), np.array([10]), (), ())
    cols, c = control.ticker_contributions(np.array([0, 0, 0]), np.array([5, 9, 5]), np.array([True, True, False]),
                                           np.ones(3, bool), np.array([0.05, -0.01, 0.3]), sd)
    assert cols.tolist() == [5, 9] and c.tolist() == pytest.approx([(0.05 - 0.01) / 2, (-0.01 - 0.01) / 2])


def test_top_positions_tie_rule():
    assert ev.top_positions(np.array([1.0, 3.0, 3.0, 2.0]), 2).tolist() == [1, 2]


# -- noise, bootstrap, blocks ------------------------------------------------------------------------

def test_noise_ignores_location_and_reproduction_checks():
    rng = np.random.default_rng(1)
    delta = rng.normal(0, 0.014, 221)
    draws = d4.noise_draws(221)
    a, b = ev.noise(delta, draws), ev.noise(delta + 0.05, draws)
    assert a["sd_delta_session"] == pytest.approx(b["sd_delta_session"]) and a["se_block"] == pytest.approx(b["se_block"])
    ok = ev.noise_reproduction({"sd_delta_session": 0.0141552, "se_block": 0.0008858, "se_iid": 0.0}, 221, 6619)
    assert ok["match"]
    bad = ev.noise_reproduction({"sd_delta_session": 0.014157, "se_block": 0.000886, "se_iid": 0.0}, 221, 6619)
    assert not bad["match"] and not bad["checks"]["sd_delta_session"]
    assert not ev.noise_reproduction({"sd_delta_session": 0.014155, "se_block": 0.000886, "se_iid": 0}, 221, 6618)["match"]


def test_primary_bootstrap_deterministic_and_seeded():
    a, b = d4.primary_draws(221), d4.primary_draws(221)
    assert np.array_equal(a, b) and a.shape == (10000, 221)
    assert not np.array_equal(a, d4.noise_draws(221))


def test_four_blocks():
    from app.backtest.strategy_d_analog import resample
    assert [p.size for p in resample.block_partition(221, 4)] == [56, 55, 55, 55]


# -- gate -----------------------------------------------------------------------------------------

SPEC = ev.spec_from_contract(d4.load_trading_contract()[0])
GOOD = {"pit_violations": 0, "retained_sessions": 221, "dropped_sessions": 0, "valid_setup_trades": 6619,
        "setup_no_trade_share": 0.0017, "control_no_trade_share": 0.002, "mean_setup_net": 0.004,
        "mean_delta": 0.003, "delta_ci_low": 0.001, "blocks_delta_positive": 3,
        "delta_without_top5_sessions": 0.002, "delta_without_top10_tickers": 0.002,
        "single_ticker_positive_share": 0.01}


def v(**change):
    return ev.evaluate({**GOOD, **change}, SPEC)["verdict"]


def test_spec_matches_contract():
    assert SPEC == {"P1": (150.0, 5.0, 5000.0, 0.02, 2.0), "P7": 0.05}


def test_gate_pass_fixture():
    assert v() == "D_AGG_BACKTEST_PASS"


@pytest.mark.parametrize("change", [
    {"mean_setup_net": 0.0}, {"mean_setup_net": -0.001},            # P2
    {"mean_delta": 0.0},                                            # P3
    {"delta_ci_low": 0.0}, {"delta_ci_low": -0.0001},               # P4
    {"blocks_delta_positive": 2},                                   # P5
    {"delta_without_top5_sessions": -0.0001}, {"delta_without_top10_tickers": 0.0},   # P6
    {"single_ticker_positive_share": 0.0501},                       # P7
    {"pit_violations": 1}, {"dropped_sessions": 6}, {"valid_setup_trades": 4999},
    {"setup_no_trade_share": 0.0041, "control_no_trade_share": 0.002},              # P1
])
def test_gate_fail_paths(change):
    assert v(**change) == "D_AGG_BACKTEST_FAIL"


def test_gate_boundaries_inclusive_where_declared():
    assert v(single_ticker_positive_share=0.05, dropped_sessions=5, retained_sessions=150,
             valid_setup_trades=5000, setup_no_trade_share=0.004, control_no_trade_share=0.002) == "D_AGG_BACKTEST_PASS"


def test_no_borderline_and_technical_inconclusive_only():
    assert "BORDERLINE" not in (PACKAGE / "evaluation4.py").read_text().split('"""', 2)[2]
    t = ev.evaluate(GOOD, SPEC, technical_failure="artifact mutation")
    assert t["verdict"] == "D_AGG_BACKTEST_INCONCLUSIVE_TECHNICAL"


def test_secondary_cannot_overturn():
    base = ev.evaluate({**GOOD, "delta_ci_low": -0.001}, SPEC)
    noisy = ev.evaluate({**GOOD, "delta_ci_low": -0.001, "S2_same_bar_tp_first": {"mean_delta": 9.0}}, SPEC)
    assert base == noisy and base["verdict"] == "D_AGG_BACKTEST_FAIL" and base["secondary_cannot_overturn"]


def test_evaluation_is_pure():
    tree = ast.parse((PACKAGE / "evaluation4.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not ({"json", "pathlib", "pyarrow", "pyarrow.parquet"} & mods)


def test_contract_checksum_is_read_from_sha_file():
    raw, checksum = d4.load_trading_contract()
    assert checksum == d4.declared_trading_checksum() and len(checksum) == 64


# -- PIT audit on a synthetic panel ---------------------------------------------------------------

@pytest.mark.parametrize("days,d", [(120, 70), (300, 260)])      # 260 > int8 range: regression
def test_pit_audit_synthetic_has_witnesses_and_no_findings(days, d):
    panel = make_panel(days)
    cols = np.arange(len(panel.tickers))
    ex = excursions.compute(panel, **GEOMETRY)
    rv = pit4.rv20_independent(panel, d, cols)
    probs = np.linspace(0.1, 0.9, 9)
    bucket = np.searchsorted(np.quantile(rv, probs), rv, side="right")
    out = pit4.audit(panel, d, cols, ex.valid[d, cols], rv, bucket)
    assert out["findings"] == []
    assert out["witness"]["TP_CONTROL"] > 0 and out["witness"]["SL_CONTROL"] > 0 and out["witness"]["ENTRY_CONTROL"] > 0
