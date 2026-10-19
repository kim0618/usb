"""H-N1 census guards: leakage, F-/invalid separation, frozen cutoff reuse, gate and verdict precedence."""

import math

import numpy as np
import pytest

from app.backtest.strategy_h_n1 import cutoff, events, outcomes
from app.backtest.strategy_h_n1.common import HN1Stop, split_of


def _ev(split="DEV", r=0.0, sig="2025-03-03", **kw):
    e = {"split": split, "exclusion": None, "fundamental_valid": True, "reaction": r,
         "signal_session": sig, "accession": "x", "f_plus": True, "f_minus": False}
    e.update(kw)
    return e


def test_pre_snapshot_signal_is_excluded_split():
    assert split_of("2024-10-24") == "PRE_SNAPSHOT"
    assert split_of("2024-10-25") == "DEV"
    assert split_of("2025-12-31") == "DEV"
    assert split_of("2026-01-02") == "HOLDOUT"


def test_quantile_refuses_holdout_rows():
    with pytest.raises(HN1Stop):
        cutoff.dev_quantiles([_ev(), _ev(split="HOLDOUT", sig="2026-02-02")])


def test_quantile_refuses_excluded_or_invalid_rows():
    with pytest.raises(HN1Stop):
        cutoff.dev_quantiles([_ev(), _ev(exclusion="REACTION_UNAVAILABLE")])
    with pytest.raises(HN1Stop):
        cutoff.dev_quantiles([_ev(), _ev(fundamental_valid=False)])


def test_population_is_dev_only():
    pop = cutoff.tercile_population([_ev(), _ev(split="HOLDOUT"), _ev(exclusion="PRICE_LT_5")])
    assert len(pop) == 1 and pop[0]["split"] == "DEV"


def test_bucket_ties_and_frozen_reuse():
    q33, q67 = cutoff.dev_quantiles([_ev(r=x) for x in (-0.1, 0.0, 0.1, 0.2)])
    assert cutoff.bucket(q33, q33, q67) == "WEAK"
    assert cutoff.bucket(q67, q33, q67) == "MIDDLE"
    # the same frozen numbers classify HOLDOUT; there is no holdout path that recomputes
    assert cutoff.bucket(-0.5, q33, q67) == "WEAK"


@pytest.mark.parametrize("status", ["XBRL_MISSING", "INVALID_FILING_MAPPING", "REVENUE_MISSING",
                                    "NET_INCOME_PRIOR_MISSING", "INVALID_PERIOD"])
def test_invalid_fundamentals_never_f_minus(status):
    row = events.classify_f({"fund_status": status})
    assert row["f_minus"] is None and row["f_plus"] is None and not row["fundamental_valid"]


def test_f_plus_and_f_minus():
    base = {"fund_status": "VALID", "rev_prior": 100.0, "rev_cur": 110.0, "ni_prior": -5.0, "ni_cur": -1.0}
    assert events.classify_f(dict(base))["f_plus"] is True
    assert events.classify_f(dict(base, rev_prior=0.0))["f_minus"] is True
    assert events.classify_f(dict(base, ni_cur=-6.0))["f_minus"] is True


def test_phase1_forbidden_columns_cover_forward_fields():
    for k in ("ret_5", "excess_obs", "exit_session", "fwd21", "net_10", "entry_open"):
        assert any(k.startswith(f) or f in k for f in events.FORBIDDEN_COLUMNS)


def _S(mean0, mean25=None, n=400):
    mean25 = mean0 - 0.0025 if mean25 is None else mean25
    d = {"n": n, "mean_0": mean0, "mean_10": mean0 - 0.001, "mean_25": mean25,
         "median_10": mean0 - 0.001, "ci_lo_10": mean0 - 0.002, "ci_hi_10": mean0,
         "conc10": {"net_total": 1.0, "top5_share_of_net": 0.1, "top1pct_removed_mean": 0.01}}
    return d


def _stats(fn):
    S = {}
    for coh in ("PRIMARY", "CONTRAST"):
        for sp in ("DEV", "HOLDOUT"):
            for h in (5, 21, 63):
                for v in ("obs", "cons"):
                    S[(coh, sp, h, v)] = fn(coh, sp, h, v)
    return S


def _gates(S, seasons_pos=True):
    SEAS = {(h, v): [{"eligible": True, "mean_10": 0.01 if seasons_pos else -0.01}]
            for h in (5, 21, 63) for v in ("obs", "cons")}
    return {(h, v): outcomes.gate_matrix(S, SEAS, h, v) for h in (5, 21, 63) for v in ("obs", "cons")}


def test_verdict_pass_and_5d_alone_cannot_pass():
    S = _stats(lambda c, sp, h, v: _S(0.02 if c == "PRIMARY" else 0.0))
    assert outcomes.verdict(S, _gates(S))[0] == "H_N1_PASS"
    S = _stats(lambda c, sp, h, v: _S(0.02 if (c == "PRIMARY" and h == 5) else -0.001))
    assert outcomes.verdict(S, _gates(S))[0] != "H_N1_PASS"


def test_verdict_precedence_insufficient_first():
    S = _stats(lambda c, sp, h, v: _S(0.02, n=50))
    assert outcomes.verdict(S, _gates(S))[0] == "INSUFFICIENT"


def test_verdict_fail_signal_before_cost_bound():
    S = _stats(lambda c, sp, h, v: _S(-0.01))
    assert outcomes.verdict(S, _gates(S))[0] == "FAIL_SIGNAL"
    S = _stats(lambda c, sp, h, v: _S(0.001, mean25=-0.0015))
    assert outcomes.verdict(S, _gates(S))[0] == "FAIL_COST_BOUND"


def test_verdict_fail_holdout_and_borderline():
    S = _stats(lambda c, sp, h, v: _S(0.02 if c == "PRIMARY" and sp == "DEV" else
                                      (-0.01 if c == "PRIMARY" else -0.02)))
    assert outcomes.verdict(S, _gates(S))[0] == "FAIL_HOLDOUT"
    S = _stats(lambda c, sp, h, v: _S(0.02 if c == "PRIMARY" else 0.0))
    assert outcomes.verdict(S, _gates(S, seasons_pos=False))[0] == "BORDERLINE"


def test_shorter_horizon_consistency_blocks_long_pass():
    S = _stats(lambda c, sp, h, v: _S((-0.0001 if h == 5 and sp == "HOLDOUT" else 0.02) if c == "PRIMARY" else -0.03))
    g = _gates(S)
    assert g[(21, "obs")]["G8"][0] is False and g[(63, "obs")]["G8"][0] is False
    assert g[(5, "obs")]["G8"][0] is None
    assert outcomes.verdict(S, g)[0] != "H_N1_PASS"


def test_delisting_disagreement_blocks_pass():
    S = _stats(lambda c, sp, h, v: _S((0.02 if v == "obs" else 0.0005) if c == "PRIMARY" else -0.03))
    g = _gates(S)
    assert outcomes.horizon_pass(g[(21, "obs")]) != outcomes.horizon_pass(g[(21, "cons")]) or \
        not outcomes.horizon_pass(g[(21, "cons")])
    assert outcomes.verdict(S, g)[0] != "H_N1_PASS"


def test_bootstrap_deterministic():
    v = np.arange(20, dtype=float)
    b = [f"w{i // 4}" for i in range(20)]
    assert outcomes.bootstrap_ci(v, b) == outcomes.bootstrap_ci(v, b)


def test_final_line_mapping():
    assert outcomes.final_line("FAIL_HOLDOUT") == "FAIL"
    assert outcomes.final_line("H_N1_PASS") == "PASS"


# ---------------------------------------------------------------- price path on a synthetic panel

from app.backtest.strategy_h_n1.common import Panel, TickerMaps


def _panel(rows, splits=None):
    """rows: ticker -> list of (open, close) or None per session; SPY added flat-ish."""
    sessions = [f"2025-01-{d:02d}" for d in range(2, 2 + len(next(iter(rows.values()))))]
    rows = dict(rows, SPY=[(100.0 + i, 101.0 + i) for i in range(len(sessions))])
    tick = {t: j for j, t in enumerate(sorted(rows))}
    o = np.full((len(sessions), len(tick)), np.nan)
    c, v = o.copy(), o.copy()
    for t, seq in rows.items():
        for i, x in enumerate(seq):
            if x is not None:
                o[i, tick[t]], c[i, tick[t]], v[i, tick[t]] = x[0], x[1], 1e6
    return Panel(sessions, tick, o, c, v, splits or {})


def test_entry_is_next_session_open_and_spy_window_aligned():
    p = _panel({"AAA": [(10, 10), (20, 21), (22, 23), (24, 25), (26, 27), (28, 29), (30, 31)]})
    ch = outcomes.Chain(p, TickerMaps({"1": ["AAA"]}, []))
    out = outcomes.event_outcomes({"cik": "1", "ticker": "AAA", "signal_idx": 0}, ch)
    o5 = out[5]
    assert o5["exit_session"] == p.sessions[5]          # entry = session 1, h counts entry as 1
    assert math.isclose(o5["stock_obs"], 29 / 20 - 1)   # entry OPEN of session after signal
    assert math.isclose(o5["spy"], (101 + 5) / (100 + 1) - 1)
    assert math.isclose(o5["excess_obs"], o5["stock_obs"] - o5["spy"])


def test_split_adjusts_exit_price():
    p = _panel({"AAA": [(10, 10), (20, 20), (10, 10), (10, 11), (11, 11), (11, 11), (11, 11)]},
               splits={"AAA": [("2025-01-04", 2.0)]})
    ch = outcomes.Chain(p, TickerMaps({"1": ["AAA"]}, []))
    out = outcomes.event_outcomes({"cik": "1", "ticker": "AAA", "signal_idx": 0}, ch)
    assert math.isclose(out[5]["stock_obs"], 11 * 2 / 20 - 1)


def test_true_delisting_observed_vs_conservative():
    p = _panel({"AAA": [(10, 10), (20, 20), (22, 22), None, None, None, None]})
    ch = outcomes.Chain(p, TickerMaps({"1": ["AAA"]}, []))
    o5 = outcomes.event_outcomes({"cik": "1", "ticker": "AAA", "signal_idx": 0}, ch)[5]
    assert o5["status"] == "TRUE_DELISTING"
    assert math.isclose(o5["stock_obs"], 22 / 20 - 1)
    assert math.isclose(o5["excess_cons"], -1.0 - o5["spy"])


def test_ticker_rename_by_cik_is_not_delisting():
    p = _panel({"AAA": [(10, 10), (20, 20), (22, 22), None, None, None, None],
                "BBB": [None, None, None, (23, 24), (24, 25), (25, 26), (26, 27)]})
    maps = TickerMaps({"1": ["AAA"]}, [("2025-03-01", {"1": ["BBB"]})])   # successor seen in a later snapshot
    o5 = outcomes.event_outcomes({"cik": "1", "ticker": "AAA", "signal_idx": 0}, outcomes.Chain(p, maps))[5]
    assert o5["status"] == "OK" and o5["ticker_changed"]
    assert math.isclose(o5["stock_obs"], 26 / 20 - 1)


def test_exit_gap_uses_next_close_and_matching_spy():
    p = _panel({"AAA": [(10, 10), (20, 20), (21, 21), (22, 22), (23, 23), None, (30, 30)]})
    o5 = outcomes.event_outcomes({"cik": "1", "ticker": "AAA", "signal_idx": 0},
                                 outcomes.Chain(p, TickerMaps({"1": ["AAA"]}, [])))[5]
    assert o5["status"] == "EXIT_GAP" and o5["exit_session"] == p.sessions[6]
    assert math.isclose(o5["spy"], (101 + 6) / (100 + 1) - 1)
