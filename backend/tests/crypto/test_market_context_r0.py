"""Market Context R0 unit tests.

Contract: docs/crypto/market_context_r0/CONTRACT_MC_R0.md
Research only: nothing here touches C1/C1x, AUTO, order paths, PAPER, LIVE or any production
module, and nothing here writes to the Market Structure V0 journal.

Organised as the contract is. The frozen figures come first, because a study whose constants have
drifted away from its own specification is not a study. Then PIT safety, then the episode rules,
then the families, then the statistics, then isolation.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.market_context_r0 import families, layers, outcomes, stats, structure
from app.crypto.research.market_context_r0.layers import (LEVEL_TOL_PCT, MEANINGFUL_BP,
                                                          RT_COST_BP, WALL_PROX_BPS_HI,
                                                          WALL_PROX_BPS_LO, MinuteFrame,
                                                          SecondFrame, SecondSample)
from app.crypto.research.market_context_r0.replay import MIN_EPISODES, contract_identity

PACKAGE = Path(__file__).resolve().parents[2] / "app" / "crypto" / "research" / "market_context_r0"
CONTRACT = (Path(__file__).resolve().parents[3]
            / "docs" / "crypto" / "market_context_r0" / "CONTRACT_MC_R0.md")
MIN = 60_000
DAY = 86_400_000


# --------------------------------------------------------------------------- frozen figures
def test_contract_is_frozen_and_agrees_with_its_sidecar():
    assert CONTRACT.exists()
    ident = contract_identity()
    assert ident["sha256"] == hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    assert ident["agrees"] is True, "the contract changed without its hash being re-recorded"


@pytest.mark.parametrize("text", [
    "11.25", "22.5", "0.25", "10.0", "1.0", "30", "theta_1h = 3.0", "theta_5m = 6.0",
])
def test_every_frozen_figure_appears_in_the_contract(text):
    assert text in CONTRACT.read_text(encoding="utf-8")


def test_code_constants_match_the_contract():
    assert RT_COST_BP == 11.25
    assert MEANINGFUL_BP == 2 * RT_COST_BP
    assert LEVEL_TOL_PCT == 0.25
    assert (WALL_PROX_BPS_LO, WALL_PROX_BPS_HI) == (1.0, 10.0)
    assert MIN_EPISODES == 30
    assert (structure.THETA_1H, structure.THETA_5M) == (3.0, 6.0)


# --------------------------------------------------------------------------- Layer C long
def test_trailing_imbalance_sums_the_sides_before_dividing():
    buy = np.array([1.0, 0.0, 0.0])
    sell = np.array([0.0, 1.0, 1.0])
    out = layers._trailing_imbalance(buy, sell, 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    # (1 - 2) / 3, not the mean of the three per minute ratios, which would be -1/3.
    assert out[2] == pytest.approx(-1 / 3)


def test_trailing_imbalance_is_nan_on_an_empty_window():
    out = layers._trailing_imbalance(np.zeros(3), np.zeros(3), 3)
    assert np.isnan(out[2])


def test_previous_session_levels_use_the_completed_day_only():
    from app.crypto.research.price_structure_r0.data import Bars, wilder_atr
    n = 3 * 1440
    ts = np.arange(0, n * MIN, MIN, dtype=np.int64)
    close = np.full(n, 100.0)
    high = close.copy()
    low = close.copy()
    high[5] = 150.0        # day 0
    low[7] = 50.0          # day 0
    high[1440 + 5] = 999.0  # day 1, must not leak into day 1's own context
    b = Bars("1m", MIN, ts, close, high, low, close, close, close, wilder_atr(high, low, close))
    out = structure.prev_session_levels(b)
    assert out[DAY]["prev_session_high"] == 150.0
    assert out[DAY]["prev_session_low"] == 50.0
    assert out[2 * DAY]["prev_session_high"] == 999.0
    assert 0 not in out, "the first day has no previous day"


# --------------------------------------------------------------------------- outcomes, PIT
def _minute_frame(close, high=None, low=None) -> MinuteFrame:
    close = np.asarray(close, dtype=float)
    high = close.copy() if high is None else np.asarray(high, dtype=float)
    low = close.copy() if low is None else np.asarray(low, dtype=float)
    ts = np.arange(0, len(close) * MIN, MIN, dtype=np.int64)
    return MinuteFrame(rows=[], ts=ts, high=high, low=low, close=close,
                       imb_1m=np.zeros(len(close)), imb_3m=np.zeros(len(close)),
                       index_of={int(t): i for i, t in enumerate(ts)}, dom_quantile=0.1)


def test_minute_outcome_reads_nothing_at_or_before_the_decision_bar():
    close = [100.0] * 10
    high = list(close)
    low = list(close)
    high[3] = 1_000.0  # the decision bar's own high
    low[3] = 1.0
    fr = _minute_frame(close, high, low)
    out = outcomes.minute_outcome(fr, 3, "LONG", None, 5, "RES")
    assert out["mfe_up_bp"] == 0.0 and out["mfe_dn_bp"] == 0.0


def test_minute_outcome_is_none_when_the_horizon_runs_past_the_series():
    fr = _minute_frame([100.0] * 10)
    assert outcomes.minute_outcome(fr, 8, "LONG", None, 5, "RES") is None


def test_first_meaningful_direction_is_ambiguous_when_one_bar_breaches_both():
    p0 = 100.0
    up = p0 * (1 + MEANINGFUL_BP / 1e4)
    dn = p0 * (1 - MEANINGFUL_BP / 1e4)
    assert outcomes._first_meaningful_bars(np.array([up]), np.array([dn]), p0) == "AMBIGUOUS"
    assert outcomes._first_meaningful_bars(np.array([up]), np.array([p0]), p0) == "UP"
    assert outcomes._first_meaningful_bars(np.array([p0]), np.array([dn]), p0) == "DOWN"
    assert outcomes._first_meaningful_bars(np.array([p0]), np.array([p0]), p0) == "NONE"


def test_short_lean_flips_the_sign_of_the_directional_move():
    fr = _minute_frame([100.0, 100.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    long_side = outcomes.minute_outcome(fr, 1, "LONG", None, 5, "RES")
    short_side = outcomes.minute_outcome(fr, 1, "SHORT", None, 5, "RES")
    assert long_side["net_move_bp"] == pytest.approx(-100.0 - RT_COST_BP, abs=0.2)
    assert short_side["net_move_bp"] == pytest.approx(100.0 - RT_COST_BP, abs=0.2)


def test_reachable_move_never_goes_below_zero():
    fr = _minute_frame([100.0] * 8)
    out = outcomes.minute_outcome(fr, 1, "LONG", None, 5, "RES")
    assert out["reachable_bp"] == 0.0


# --------------------------------------------------------------------------- second frame
def _second_frame(n_per_session=20, sessions=2, mid=100.0) -> SecondFrame:
    samples, ms, sess = [], [], []
    t = 0
    for s in range(sessions):
        t += 100_000
        for k in range(n_per_session):
            samples.append(SecondSample(ms=t + k * 1000, session8=f"s{s}", mid=mid, ok=True,
                                        flow60_imb=0.5, flow60_complete=True, band_bid=1.0,
                                        band_ask=1.0, ask_bins={}, bid_bins={}))
            ms.append(t + k * 1000)
            sess.append(s)
        t += n_per_session * 1000
    return SecondFrame(samples=samples, mids=np.full(len(ms), mid),
                       ms=np.asarray(ms, dtype=np.int64),
                       session_of=np.asarray(sess, dtype=np.int32))


def test_horizon_never_crosses_a_session_boundary():
    fr = _second_frame(n_per_session=20, sessions=2)
    assert fr.index_after(0, 5_000) == 5
    assert fr.index_after(18, 60_000) is None, "the next session is not a continuation"


def test_horizon_rejects_a_stale_sample():
    fr = _second_frame(n_per_session=5, sessions=1)
    assert fr.index_after(0, 60_000) is None


# --------------------------------------------------------------------------- episodes
def _row(ts, res_price=None, res_id=None, res_d=None, sup_price=None, sup_id=None, sup_d=None,
         imb3=0.5, retest="NONE", failed="NONE"):
    return {"ts": ts, "res_price": res_price, "res_id": res_id, "res_distance_pct": res_d,
            "sup_price": sup_price, "sup_id": sup_id, "sup_distance_pct": sup_d,
            "flow_imb_3m": imb3, "retest_state": retest, "failed_break_state": failed,
            "trend_structure": "NEUTRAL", "range_state": "NO_RANGE"}


def test_episode_silence_window_is_per_level_and_side():
    fr = _minute_frame([100.0] * 400)
    rows = [_row(i * MIN, res_price=100.2, res_id=7, res_d=0.2) for i in range(0, 200)]
    fr.rows = rows
    eps = families.minute_episodes(fr)
    # 200 consecutive qualifying minutes, a 60 minute silence rule: minutes 0, 60, 120, 180.
    assert [e.anchor_ts // MIN for e in eps] == [0, 60, 120, 180]
    assert {e.level_id for e in eps} == {7}


def test_a_different_level_gets_its_own_episode_series():
    fr = _minute_frame([100.0] * 10)
    fr.rows = [_row(0, res_price=100.2, res_id=7, res_d=0.2),
               _row(MIN, res_price=100.3, res_id=8, res_d=0.2)]
    eps = families.minute_episodes(fr)
    assert sorted(e.level_id for e in eps) == [7, 8]


def test_an_approach_outside_the_tolerance_is_not_an_episode():
    fr = _minute_frame([100.0] * 10)
    fr.rows = [_row(0, res_price=101.0, res_id=7, res_d=LEVEL_TOL_PCT + 0.01)]
    assert families.minute_episodes(fr) == []


def test_support_approaches_take_the_negative_side_of_the_tolerance():
    fr = _minute_frame([100.0] * 10)
    fr.rows = [_row(0, sup_price=99.8, sup_id=3, sup_d=-0.2),
               _row(MIN, sup_price=99.8, sup_id=4, sup_d=+0.2)]
    eps = families.minute_episodes(fr)
    assert [e.level_id for e in eps] == [3]


# --------------------------------------------------------------------------- families
def test_h1_needs_both_aggression_and_the_absence_of_progress():
    fr = _minute_frame([100.0] * 100)
    fr.rows = [_row(0, res_price=100.2, res_id=7, res_d=0.2, imb3=0.5)]
    eps = families.minute_episodes(fr)
    fired = families.minute_family(fr, eps, "H1", "SIGN")["episodes"]
    assert fired[0]["fires"] is True
    fr.rows = [_row(0, res_price=100.2, res_id=7, res_d=0.2, imb3=-0.5)]
    eps = families.minute_episodes(fr)
    assert families.minute_family(fr, eps, "H1", "SIGN")["episodes"][0]["fires"] is False


def test_dom_variant_is_strictly_narrower_than_sign():
    fr = _minute_frame([100.0] * 100)
    fr.rows = [_row(0, res_price=100.2, res_id=7, res_d=0.2, imb3=0.05)]
    fr.dom_quantile = 0.2
    eps = families.minute_episodes(fr)
    assert families.minute_family(fr, eps, "H1", "SIGN")["episodes"][0]["fires"] is True
    assert families.minute_family(fr, eps, "H1", "DOM")["episodes"][0]["fires"] is False


def test_h2_decides_one_minute_after_the_approach():
    spec = families.FAMILIES
    assert spec["H2"][3] == 1 and spec["H4"][3] == 1
    assert spec["H1"][3] == 0 and spec["H3"][3] == 0


def test_h5_needs_the_structures_own_failure_memory():
    fr = _minute_frame([100.0] * 100)
    fr.rows = [_row(0, res_price=100.2, res_id=7, res_d=0.2, retest="RETEST_TOUCHED")]
    eps = families.minute_episodes(fr)
    assert families.minute_family(fr, eps, "H5", "SIGN")["episodes"][0]["fires"] is True
    fr.rows = [_row(0, res_price=100.2, res_id=7, res_d=0.2, retest="NONE", failed="NONE")]
    eps = families.minute_episodes(fr)
    assert families.minute_family(fr, eps, "H5", "SIGN")["episodes"][0]["fires"] is False


def _wall(price, bin_low, distance_bps, notional="400000"):
    return {"price": str(price), "bin_low": str(bin_low),
            "bin_high": str(float(bin_low) + 5.0), "distance_bps": str(distance_bps),
            "notional_usdt": notional}


def _wall_frame(bins_by_index: dict[int, dict], n=200, mids=None) -> SecondFrame:
    samples, ms = [], []
    for i in range(n):
        ask = bins_by_index.get(i, {})
        samples.append(SecondSample(ms=i * 1000, session8="s0",
                                    mid=100.0 if mids is None else mids[i], ok=True,
                                    flow60_imb=0.5, flow60_complete=True, band_bid=1.0,
                                    band_ask=1.0, ask_bins=ask, bid_bins={}))
        ms.append(i * 1000)
    return SecondFrame(samples=samples,
                       mids=np.asarray([s.mid for s in samples], dtype=float),
                       ms=np.asarray(ms, dtype=np.int64),
                       session_of=np.zeros(n, dtype=np.int32))


def test_wall_episode_band_is_the_rules_r3_up_to_the_complete_boundary():
    inside = {i: {"100.05": _wall(100.05, "100.05", 5.0)} for i in range(200)}
    assert families.wall_episodes(_wall_frame(inside))
    too_close = {i: {"100.00": _wall(100.0, "100.00", 0.5)} for i in range(200)}
    assert families.wall_episodes(_wall_frame(too_close)) == []
    too_far = {i: {"101.00": _wall(101.0, "101.00", 11.0)} for i in range(200)}
    assert families.wall_episodes(_wall_frame(too_far)) == []


def test_wall_episodes_respect_the_five_minute_silence():
    bins = {i: {"100.05": _wall(100.05, "100.05", 5.0)} for i in range(900)}
    fr = _wall_frame(bins, n=900)
    eps = families.wall_episodes(fr)
    assert [e.anchor_ms // 1000 for e in eps] == [0, 300, 600]


def test_a_wall_that_vanishes_without_price_reaching_it_is_cancelled_not_consumed():
    w = _wall(100.05, "100.05", 5.0)
    bins = {i: ({"100.05": w} if i < 30 else {}) for i in range(200)}
    fr = _wall_frame(bins)          # mid stays at 100.0, never reaches bin_high 105.05
    eps = families.wall_episodes(fr)
    out = families.wall_family(fr, eps, "G2")
    assert out["episodes"] == []
    assert out["excluded"]["CANCELLED_NOT_CONSUMED"] == 1


def test_a_wall_that_vanishes_while_price_trades_through_it_is_a_consumption_candidate():
    w = _wall(100.05, "100.05", 5.0)
    bins = {i: ({"100.05": w} if i < 30 else {}) for i in range(200)}
    mids = [100.0 if i < 40 else 106.0 for i in range(200)]
    fr = _wall_frame(bins, mids=mids)
    eps = families.wall_episodes(fr)
    out = families.wall_family(fr, eps, "G2")
    assert out["episodes"] and out["episodes"][0]["fires"] is True
    assert out["episodes"][0]["traded_through"] is True


def test_held_and_replenished_are_different_states():
    w = _wall(100.05, "100.05", 5.0)
    held = {i: {"100.05": w} for i in range(200)}
    fr = _wall_frame(held)
    eps = families.wall_episodes(fr)
    assert families.wall_family(fr, eps, "G1")["episodes"][0]["fires"] is True
    assert families.wall_family(fr, eps, "G3")["episodes"][0]["fires"] is False
    gapped = {i: ({} if 10 <= i <= 20 else {"100.05": w}) for i in range(200)}
    fr2 = _wall_frame(gapped)
    eps2 = families.wall_episodes(fr2)
    g3 = families.wall_family(fr2, eps2, "G3")["episodes"][0]
    assert g3["fires"] is True and g3["gap_inside_w"] is True


def test_an_incomplete_flow_window_excludes_the_episode_rather_than_reading_zero():
    w = _wall(100.05, "100.05", 5.0)
    bins = {i: {"100.05": w} for i in range(200)}
    fr = _wall_frame(bins)
    for s in fr.samples:
        s.flow60_complete = False
    eps = families.wall_episodes(fr)
    out = families.wall_family(fr, eps, "G1")
    assert out["episodes"] == [] and out["excluded"]["FLOW_NOT_COMPLETE"] == 1


def test_every_family_in_the_table_has_a_lean_and_a_track():
    assert set(families.FAMILIES) == {"F1", "F2", "F3", "F4", "G1", "G2", "G3", "G4", "G5", "G6",
                                      "H1", "H2", "H3", "H4", "H5", "H6"}
    for fam, (track, side, lean, _) in families.FAMILIES.items():
        assert track in ("T1", "T2", "T3")
        assert lean in ("LONG", "SHORT")
        assert side in ("RES", "SUP", "ASK", "BID")


# --------------------------------------------------------------------------- statistics
def test_circular_shift_keeps_the_label_runs_and_finds_no_effect_in_noise():
    rng = np.random.default_rng(7)
    y = rng.normal(size=400)
    x = np.zeros(400, dtype=bool)
    x[100:150] = True
    out = stats.circular_shift_p(y, x)
    assert out["p_value"] > 0.05


def test_circular_shift_finds_a_planted_effect():
    y = np.zeros(400)
    x = np.zeros(400, dtype=bool)
    x[100:200] = True
    y[x] = 50.0
    out = stats.circular_shift_p(y, x)
    assert out["p_value"] == 0.0


def test_circular_shift_reports_nothing_when_one_group_is_empty():
    y = np.zeros(10)
    assert stats.circular_shift_p(y, np.zeros(10, dtype=bool))["p_value"] is None


def test_holm_is_a_step_down_and_stops_at_the_first_failure():
    grid = [{"p_value": 0.001}, {"p_value": 0.04}, {"p_value": 0.2}]
    stats.holm(grid)
    assert [g["holm_significant"] for g in grid] == [True, False, False]


def test_holm_marks_a_missing_p_value_as_not_significant():
    grid = [{"p_value": None}]
    stats.holm(grid)
    assert grid[0]["holm_significant"] is False


def test_bootstrap_ci_brackets_a_known_difference():
    y = np.concatenate([np.full(200, 10.0), np.zeros(200)])
    x = np.concatenate([np.ones(200, dtype=bool), np.zeros(200, dtype=bool)])
    ci = stats.bootstrap_ci(y, x, draws=500)
    assert ci["lo"] == pytest.approx(10.0) and ci["hi"] == pytest.approx(10.0)


# --------------------------------------------------------------------------- isolation
def _sources() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_package_cannot_reach_an_order_path_or_a_credential():
    banned = ("app.services", "app.strategy", "app.api", "app.crypto.trading",
              "app.crypto.binance_live", "app.crypto.paper")
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not any(m.startswith(b) for b in banned), f"{path.name} imports {m}"


def test_no_source_names_an_order_endpoint_or_a_private_api():
    vocab = ("/fapi/v1/order", "listenKey", "userDataStream", "/sapi/", "apiKey", "api_secret",
             "place_order", "cancel_order")
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for word in vocab:
            assert word not in text, f"{path.name} mentions {word}"


def test_every_write_target_is_the_studys_own_output_directory():
    """Every module that writes names one `OUT`, and every `OUT` is inside this study."""
    writers = 0
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        writes = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in ("write_text", "write_bytes")]
        if not writes:
            continue
        writers += 1
        out = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "OUT" for t in n.targets)]
        assert out, f"{path.name} writes but declares no OUT"
        assert "data/research/crypto/market_context_r0" in ast.unparse(out[0]), path.name
        for node in writes:
            assert "OUT" in ast.unparse(node) or "path.with_suffix" in ast.unparse(node), \
                f"{path.name}: {ast.unparse(node)[:80]}"
    assert writers >= 3


def test_the_journal_is_opened_read_only():
    text = (PACKAGE / "journal.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            mode = [k for k in node.keywords if k.arg == "mode"]
            args = node.args
            if len(args) > 1 and isinstance(args[1], ast.Constant):
                assert "r" in args[1].value and "w" not in args[1].value
            assert not mode or "w" not in mode[0].value.value
