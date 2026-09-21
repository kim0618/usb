"""C-ATTACK-V0 contracts: frozen declaration, exit ordering, gap-through, ambiguity, delisting,
costs, the sleeve portfolio, the M_ONLY source check and the gate decision.

Every price path below is hand-written so a silent change to the exit semantics fails a test.
"""

import json

import numpy as np
import pandas as pd
import pytest

from app.backtest.strategy_c_attack import cohort, metrics
from app.backtest.strategy_c_attack.rules import (DECLARED_RULES_CHECKSUM, DeclarationChanged, RULES_PATH,
                                                  load_rules)
from app.backtest.strategy_c_attack.run import _decision
from app.backtest.strategy_c_attack.simulate import (AMBIGUOUS, NEITHER, SL_FIRST, TP_FIRST, Prices, simulate,
                                                     simulate_one)
from app.backtest.strategy_c_selection.rules import canonical_checksum

NAN = np.nan


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def test_declaration_is_frozen(rules, tmp_path):
    assert rules.checksum == DECLARED_RULES_CHECKSUM
    raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    raw["exits"]["primary_pair"] = "D"
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DeclarationChanged):
        load_rules(edited)
    assert canonical_checksum(raw) != DECLARED_RULES_CHECKSUM


def test_declared_values(rules):
    assert rules.primary_pair.name == "C" and (rules.primary_pair.tp, rules.primary_pair.sl) == (0.15, 0.07)
    assert [p.name for p in rules.pairs] == ["A", "B", "C", "D", "E"]
    assert rules.horizon == 10 and rules.secondary_horizons == (3, 5)
    assert rules.min_controls == 5
    assert sum(rules.published_status_counts.values()) == 6680
    cost = rules.cost
    rt = cost.round_trip(np.array([0, 3]), np.array([0, 2]), np.array([True, False]))
    assert rt == pytest.approx([0.0010 + 0.0060 + 0.0010 + 0.0020, 0.0010 + 0.0015])


def one_path(rows, horizon=10, tp=0.15, sl=0.07, conservative=True, pad_before=1, pad_after=3, haircut=0.30):
    """rows = [(o, h, l, c) or None] starting at D+1. Returns (vector Trades, scalar tuple)."""
    body = [(NAN,) * 4 if r is None else r for r in rows]
    body += [(NAN,) * 4] * max(0, horizon + pad_after - len(body))
    full = [(10.0, 10.0, 10.0, 10.0)] * pad_before + body
    arr = np.array(full, dtype=float)
    prices = Prices.build(arr[:, [0]], arr[:, [1]], arr[:, [2]], arr[:, [3]])
    vec = simulate(prices, np.array([pad_before - 1]), np.array([0]), tp, sl, horizon,
                   conservative=conservative, haircut=haircut)
    ref = simulate_one(arr[pad_before:, 0], arr[pad_before:, 1], arr[pad_before:, 2], arr[pad_before:, 3],
                       tp, sl, horizon, conservative=conservative, haircut=haircut)
    assert vec.exit_price[0] == pytest.approx(ref[0]) and vec.state[0] == ref[1]
    assert vec.exit_idx[0] == pad_before + ref[2]
    return vec, ref


def flat(n, px=100.0):
    return [(px, px + 1, px - 1, px)] * n


def test_tp_intraday_fills_at_tp():
    vec, _ = one_path([(100, 101, 99, 100), (100, 116, 99, 110)] + flat(8))
    assert vec.state[0] == TP_FIRST and vec.exit_price[0] == pytest.approx(115.0) and not vec.gap[0]


def test_sl_intraday_fills_at_sl():
    vec, _ = one_path([(100, 101, 99, 100), (100, 101, 92, 95)] + flat(8))
    assert vec.state[0] == SL_FIRST and vec.exit_price[0] == pytest.approx(93.0) and vec.stop_exit[0]


def test_gap_through_sl_exits_at_open_below_stop():
    vec, _ = one_path([(100, 101, 99, 100), (87, 90, 85, 88)] + flat(8))
    assert vec.state[0] == SL_FIRST and vec.gap[0] and vec.exit_price[0] == pytest.approx(87.0)
    assert vec.gross[0] == pytest.approx(-0.13)


def test_gap_up_through_tp_exits_at_better_open():
    vec, _ = one_path([(100, 101, 99, 100), (125, 130, 120, 128)] + flat(8))
    assert vec.state[0] == TP_FIRST and vec.gap[0] and vec.exit_price[0] == pytest.approx(125.0)


def test_entry_bar_has_no_gap_rule_but_intraday_applies():
    vec, _ = one_path([(100, 120, 99, 110)] + flat(9))
    assert vec.state[0] == TP_FIRST and vec.exit_idx[0] == 1 and vec.exit_price[0] == pytest.approx(115.0)


def test_same_bar_is_ambiguous_conservative_sl_optimistic_tp():
    path = [(100, 101, 99, 100), (100, 120, 90, 100)] + flat(8)
    cons, _ = one_path(path)
    opt, _ = one_path(path, conservative=False)
    assert cons.state[0] == AMBIGUOUS and cons.exit_price[0] == pytest.approx(93.0) and cons.stop_exit[0]
    assert opt.state[0] == AMBIGUOUS and opt.exit_price[0] == pytest.approx(115.0) and not opt.stop_exit[0]


def test_neither_exits_at_close_of_d10():
    vec, _ = one_path(flat(9) + [(100, 101, 99, 104.0)])
    assert vec.state[0] == NEITHER and vec.exit_price[0] == pytest.approx(104.0) and vec.exit_idx[0] == 10


def test_missing_bars_skip_then_late_exit_at_next_open():
    path = flat(3) + [None] * 7 + [None, (98, 99, 97, 98)]
    vec, _ = one_path(path)
    assert vec.state[0] == NEITHER and vec.late_exit[0] and vec.exit_price[0] == pytest.approx(98.0)
    assert vec.missing_sessions[0] == 7


def test_late_exit_open_below_stop_is_a_gap_stop():
    vec, _ = one_path(flat(9) + [None, (80, 82, 79, 81)])
    assert vec.state[0] == SL_FIRST and vec.gap[0] and vec.exit_price[0] == pytest.approx(80.0)


def test_delisted_uses_last_close_with_haircut():
    vec, _ = one_path(flat(4) + [(100, 101, 99, 96.0)], pad_after=0)
    assert vec.delisted[0] and vec.state[0] == NEITHER
    assert vec.exit_price[0] == pytest.approx(96.0 * 0.7) and vec.exit_idx[0] == 10


def test_nothing_before_entry_is_read():
    """The bar of D and earlier cannot change a trade: the vectorised path equals the scalar
    reference, which only ever receives the slice from D+1 on."""
    path = [(100, 101, 99, 100), (100, 116, 99, 110)] + flat(8)
    a, _ = one_path(path, pad_before=1)
    b, _ = one_path(path, pad_before=5)
    assert a.exit_price[0] == b.exit_price[0] and a.state[0] == b.state[0]


def test_vectorised_matches_scalar_on_random_paths():
    rng = np.random.default_rng(7)
    t, n = 40, 300
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.05, size=(t, n)), axis=0))
    o = c * np.exp(rng.normal(0, 0.03, size=(t, n)))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.04, size=(t, n))))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.04, size=(t, n))))
    holes = rng.random((t, n)) < 0.05
    holes[:3] = False
    for arr in (o, h, lo, c):
        arr[holes] = np.nan
    dead = rng.choice(n, 20, replace=False)
    for arr in (o, h, lo, c):
        arr[25:, dead] = np.nan
    prices = Prices.build(o, h, lo, c)
    ii = rng.integers(0, 20, size=n)
    jj = np.arange(n)
    ok = prices.has_bar[ii + 1, jj]
    ii, jj = ii[ok], jj[ok]
    for conservative in (True, False):
        vec = simulate(prices, ii, jj, 0.15, 0.07, 10, conservative=conservative)
        for k, (i, j) in enumerate(zip(ii, jj)):
            ref = simulate_one(o[i + 1:, j], h[i + 1:, j], lo[i + 1:, j], c[i + 1:, j], 0.15, 0.07, 10,
                               conservative=conservative)
            assert vec.exit_price[k] == pytest.approx(ref[0]) and vec.state[k] == ref[1]
            assert vec.exit_idx[k] == i + 1 + ref[2]


def trade_frame(nets, dates=None, exits=None, tickers=None):
    n = len(nets)
    return pd.DataFrame({"date_idx": dates or [0] * n, "exit_idx": exits or list(range(1, n + 1)),
                         "ticker": tickers or [f"T{i}" for i in range(n)], "net": nets, "gross": nets,
                         "state": [TP_FIRST if x > 0 else SL_FIRST for x in nets],
                         "gap": [False] * n, "delisted": [False] * n, "stop_exit": [x <= 0 for x in nets]})


def test_profit_factor_streaks_and_contribution():
    frame = trade_frame([0.1, -0.05, -0.05, 0.2, -0.05])
    assert metrics.profit_factor(frame["net"]) == pytest.approx(0.3 / 0.15)
    assert metrics.streaks(frame["net"]) == (2, 1)
    assert metrics.top_contribution(frame["net"], 1) == pytest.approx(0.2 / 0.15)
    assert metrics.top_contribution(pd.Series([-0.1, 0.05]), 1) is None


def test_sleeve_equity_is_bounded_and_non_compounding():
    frame = trade_frame([0.10, -0.10, 0.10, 0.10], dates=[0, 0, 1, 1], exits=[3, 3, 4, 5])
    equity = metrics.sleeve_equity(frame, 10)
    # date 0: two trades share 0.1 -> each weight 0.05; net 0 on idx 3; date 1: +0.005 twice.
    assert equity.loc[3] == pytest.approx(1.0) and equity.loc[5] == pytest.approx(1.01)
    dd = metrics.max_drawdown(pd.Series([1.0, 1.2, 0.9, 1.3]))
    assert dd == pytest.approx(0.25)


def test_loss_tail_counts_loss_beyond_stop():
    frame = trade_frame([-0.13, -0.07, 0.15])
    frame["gap"] = [True, False, False]
    tail = metrics.loss_tail(frame, 0.07)
    assert tail["gap_through_sl_count"] == 1
    assert tail["loss_beyond_nominal_sl_count"] == 1
    assert tail["loss_beyond_nominal_sl_mean"] == pytest.approx(-0.06)


def test_m_only_source_blocks_on_missing_or_wrong_counts(rules, tmp_path):
    base = pd.DataFrame({"signal_date": ["2026-01-02", "2026-01-02"], "ticker": ["A", "B"]})
    missing = cohort.verify(tmp_path / "nope.parquet", base, rules.published_status_counts)
    assert not missing.accepted and cohort.BLOCKED in missing.reason
    table = base.assign(status=["M_ONLY", "EM"])
    path = tmp_path / "status.parquet"
    table.to_parquet(path, index=False)
    wrong = cohort.verify(path, base, rules.published_status_counts)
    assert not wrong.accepted and "status counts" in wrong.reason
    published = {"M_ONLY": 1, "EM": 1}
    ok = cohort.verify(path, base, published)
    assert ok.accepted and ok.sha256
    other = pd.DataFrame({"signal_date": ["2026-01-02", "2026-01-05"], "ticker": ["A", "B"]})
    assert not cohort.verify(path, other, published).accepted


def conds(value=True, **override):
    base = {k: value for k in ("1_h1_net_expectancy_point", "2_h2_excess_point", "3_h2_excess_ci95_low",
                               "4_h3_profit_factor", "5_h4_sleeve_mdd", "8_time_stability", "9_concentration")}
    base.update(override)
    return {"conditions": base}


def test_decision_rules(rules):
    assert _decision(conds(), conds(), True, True, 0.1, rules)[0] == "PASS"
    assert _decision(conds(False), conds(False), True, True, 0.5, rules)[0] == "FAIL"
    # poor returns are never INCONCLUSIVE unless minute ordering could flip every condition
    assert _decision(conds(False), conds(), True, True, 0.10, rules)[0] == "FAIL"
    assert _decision(conds(False), conds(), True, True, 0.30, rules)[0] == "INCONCLUSIVE"
    assert _decision(conds(), conds(), False, True, 0.1, rules)[0] == "INCONCLUSIVE"
    assert _decision(conds(), conds(), True, False, 0.1, rules)[0] == "INCONCLUSIVE"


def test_outcomes_end_to_end_on_synthetic_panel(rules):
    """The whole return path (5 pairs x 2 orderings, bases, blocks, sensitivities, secondary,
    horizons, gate) runs on a synthetic panel and every trade is priced from its own path."""
    from app.backtest.strategy_c_attack.run import outcomes

    rng = np.random.default_rng(11)
    t, n = 80, 120
    c = 20 * np.exp(np.cumsum(rng.normal(0, 0.04, size=(t, n)), axis=0))
    o = c * np.exp(rng.normal(0, 0.02, size=(t, n)))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.03, size=(t, n))))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.03, size=(t, n))))
    prices = Prices.build(o, h, lo, c)
    primary = list(range(20, t - 10))
    ii, jj = np.meshgrid(primary, np.arange(n), indexing="ij")
    rows = pd.DataFrame({"date_idx": ii.ravel(), "ticker_idx": jj.ravel()})
    rows["ticker"] = "T" + rows["ticker_idx"].astype(str)
    rows["candidate"] = rng.random(len(rows)) < 0.08
    rows["price_bucket"] = rng.integers(0, 4, len(rows))
    rows["adv20_bucket"] = rng.integers(0, 3, len(rows))
    rows["cell"] = rows["date_idx"] * 10 + rows["price_bucket"] % 2
    rows["group"] = np.where(rows["candidate"], "candidate", "control")
    rows["status"] = np.where(rows["candidate"], np.where(rng.random(len(rows)) < 0.6, "M_ONLY", "EM"), "CONTROL")
    rows["no_entry_bar"] = False
    rows["label_ca_suspect"] = rng.random(len(rows)) < 0.005
    rows["trade_valid"] = ~rows["label_ca_suspect"]
    trade_rows = rows[rows["trade_valid"] & ((rows["group"] == "control") | (rows["status"] == "M_ONLY"))]
    result, trades = outcomes(prices, rows, trade_rows, rules, primary, t, True, True, log=lambda m: None)
    assert result["gate"]["decision"] in {"PASS", "FAIL", "INCONCLUSIVE"}
    assert set(result["pairs"]) == {f"{p}:{o}" for p in "ABCDE" for o in ("conservative", "optimistic")}
    primary_cons = result["pairs"]["C:conservative"]
    assert primary_cons["summary"]["n"] == int((trade_rows["group"] == "candidate").sum())
    assert "ci9500" in primary_cons["h2"] and len(primary_cons["time_blocks"]) == 4
    assert set(result["sensitivity"]) >= {"gross", "cost_2x", "delist_haircut_0", "delist_haircut_100",
                                          "ca_suspect_included_worst"}
    assert set(result["secondary_all_c_m0"]["C"]["by_status_expectancy"]) == {"M_ONLY", "EM"}
    # rates sum to one; optimistic never worse than conservative for the same trade
    s = primary_cons["summary"]
    assert s["tp_first_rate"] + s["sl_first_rate"] + s["neither_rate"] + s["ambiguous_rate"] == pytest.approx(1.0)
    cons = trades[(trades["pair"] == "C") & (trades["ordering"] == "conservative")].set_index(["date_idx", "ticker"])
    opt = trades[(trades["pair"] == "C") & (trades["ordering"] == "optimistic")].set_index(["date_idx", "ticker"])
    assert (opt["gross"] >= cons.loc[opt.index, "gross"] - 1e-12).all()
    # spot-check one trade against the scalar reference
    row = cons.reset_index().iloc[0]
    ref = simulate_one(o[row.date_idx + 1:, row.ticker_idx], h[row.date_idx + 1:, row.ticker_idx],
                       lo[row.date_idx + 1:, row.ticker_idx], c[row.date_idx + 1:, row.ticker_idx], 0.15, 0.07, 10)
    assert row.exit_price == pytest.approx(ref[0])


def test_source_a_requires_matching_c_e0_summary(tmp_path):
    base = pd.DataFrame({"signal_date": ["2026-01-02", "2026-01-02"], "ticker": ["A", "B"]})
    published = {"M_ONLY": 1, "EM": 1}
    path = tmp_path / "candidate_status.parquet"
    base.assign(status=["M_ONLY", "EM"]).to_parquet(path, index=False)
    assert "summary.json missing" in cohort.verify(path, base, published, "A", "ce01-x").reason
    (tmp_path / "summary.json").write_text(json.dumps({"run_id": "ce01-other", "status_counts": published}))
    assert "run_id" in cohort.verify(path, base, published, "A", "ce01-x").reason
    (tmp_path / "summary.json").write_text(json.dumps({"run_id": "ce01-x", "status_counts": {"M_ONLY": 2}}))
    assert "status_counts" in cohort.verify(path, base, published, "A", "ce01-x").reason
    (tmp_path / "summary.json").write_text(json.dumps({"run_id": "ce01-x", "status_counts": published}))
    assert cohort.verify(path, base, published, "A", "ce01-x").accepted
