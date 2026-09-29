"""Tests for the BTC-VOL-P0 audit.

The audit builds no model and places no order, so these protect its arithmetic and its scope:
the break-even identities must be right, the read-only analyses must not reach into trading code,
and the path-versus-endpoint distinction that the whole verdict rests on must be preserved.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.btc_vol_p0 import activation_filter as AF
from app.crypto.research.btc_vol_p0 import move_economics as ME
from app.crypto.research.btc_vol_p0 import realized_vs_trailing as RVT
from app.crypto.research.btc_vol_p0 import signal_shape as SS

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS = REPO_ROOT / "data/research/crypto/btc_vol_p0"
MATRIX = RESULTS / "route_matrix_v1.json"


# --- break-even arithmetic ------------------------------------------------------------------

def test_straddle_cost_and_breakeven_are_inverses():
    for vol in (0.3, 0.8, 1.5):
        for years in (4 / 8766, 12 / 8766, 24 / 8766):
            cost = ME.straddle_cost_fraction(vol, years)
            assert ME.breakeven_implied_vol(cost, years) == pytest.approx(vol, rel=1e-12)


def test_straddle_cost_scales_with_the_square_root_of_time():
    # Four times the horizon should double the premium, not quadruple it.
    short = ME.straddle_cost_fraction(0.8, 4 / 8766)
    long = ME.straddle_cost_fraction(0.8, 16 / 8766)
    assert long == pytest.approx(2 * short, rel=1e-12)


def test_breakeven_implied_vol_matches_a_hand_computed_case():
    # A 2.05 percent expected endpoint move over four hours.
    years = 4 / 8766
    got = ME.breakeven_implied_vol(0.0205, years)
    expected = 0.0205 / (0.7978845608 * np.sqrt(years))
    assert got == pytest.approx(expected, rel=1e-12)
    assert 1.15 < got < 1.25          # about 120 percent annualised


def test_a_lognormal_sample_recovers_its_own_break_even():
    # If realised moves were lognormal with volatility v, the straddle's fair implied is v.
    rng = np.random.default_rng(20260928)
    years = 4 / 8766
    vol = 0.9
    returns = rng.normal(0.0, vol * np.sqrt(years), 400_000)
    implied = ME.breakeven_implied_vol(float(np.mean(np.abs(returns))), years)
    assert implied == pytest.approx(vol, rel=0.02)


def test_breakeven_accuracy_identity():
    # Capturing M and losing M, after cost C: break even at 0.5 + C / (2M).
    assert AF.breakeven_accuracy(100.0, 10.0) == pytest.approx(0.55)
    assert AF.breakeven_accuracy(1000.0, 10.0) == pytest.approx(0.505)
    # A bigger move lowers the bar, which is the whole point of a size filter.
    assert AF.breakeven_accuracy(200.0, 11.0) < AF.breakeven_accuracy(100.0, 11.0)


def test_breakeven_accuracy_of_a_free_trade_is_one_half():
    assert AF.breakeven_accuracy(100.0, 0.0) == pytest.approx(0.5)


def test_breakeven_accuracy_is_undefined_for_a_zero_move():
    assert np.isnan(AF.breakeven_accuracy(0.0, 11.0))


def test_annualising_a_realised_volatility_round_trips():
    # 274 bp realised over four hours is about 128 percent annualised.
    from app.crypto.research.btc_vol_p0 import straddle_gap as SG

    got = SG.annualise(274.0, 240)
    assert got == pytest.approx(0.0274 * np.sqrt(8766 / 4), rel=1e-12)
    assert 1.25 < got < 1.31


# --- episode counting -----------------------------------------------------------------------

def test_consecutive_hours_are_one_episode():
    # Counting each hour separately would turn one signal into ten.
    flags = np.array([False, True, True, True, False, True, False])
    ts = np.arange(len(flags), dtype=np.int64) * 3_600_000
    assert SS._episodes(flags, ts) == [(1, 3), (5, 5)]


def test_an_episode_running_to_the_end_is_closed():
    flags = np.array([False, True, True])
    ts = np.arange(3, dtype=np.int64) * 3_600_000
    assert SS._episodes(flags, ts) == [(1, 2)]


def test_no_flags_means_no_episodes():
    flags = np.zeros(5, dtype=bool)
    assert SS._episodes(flags, np.arange(5, dtype=np.int64)) == []


# --- forward windows never read the decision bar --------------------------------------------

def test_time_to_touch_starts_after_the_decision_bar():
    close = np.full(10, 100.0)
    high = close.copy()
    low = close.copy()
    high[0] = 500.0                     # a spike on the decision bar itself
    rows = np.array([0])
    got = SS._time_to_touch(4, 100, rows, high, low, close)
    assert np.isnan(got[0])


def test_time_to_touch_returns_the_first_minute():
    close = np.full(10, 100.0)
    high = close.copy()
    low = close.copy()
    high[3] = 101.5
    got = SS._time_to_touch(6, 100, np.array([0]), high, low, close)
    assert got[0] == 3


def test_forward_stats_measure_from_the_next_bar():
    close = np.full(10, 100.0)
    high = close.copy()
    low = close.copy()
    high[0] = 200.0                     # decision bar spike must not appear in the excursion
    stats = ME._forward_stats(np.array([0]), 5, high, low, close)
    assert stats["up"][0] == pytest.approx(0.0)
    assert stats["endpoint"][0] == pytest.approx(0.0)


def test_forward_realised_volatility_uses_only_future_returns():
    returns = np.zeros(100)
    returns[0] = 10.0                   # a huge return at the decision bar
    got = RVT._forward_realised_bp(np.array([0]), 10, returns)
    assert got[0] == pytest.approx(0.0)


# --- the distinction the verdict rests on ---------------------------------------------------

@pytest.mark.skipif(not (RESULTS / "move_economics_v1.json").exists(),
                    reason="economics not computed")
def test_touch_probability_exceeds_endpoint_probability_everywhere():
    # A path-touch forecast read as an option payoff probability is the main way this audit
    # could have gone wrong, so the gap is pinned.
    payload = json.loads((RESULTS / "move_economics_v1.json").read_text())
    for combo, entry in payload["combos"].items():
        for name, sub in entry["subsets"].items():
            if not sub.get("n"):
                continue
            straddle = sub["straddle_held_to_expiry"]
            assert straddle["share_touch_beyond_threshold"] > \
                straddle["share_endpoint_beyond_threshold"], (combo, name)


@pytest.mark.skipif(not (RESULTS / "move_economics_v1.json").exists(),
                    reason="economics not computed")
def test_the_excursion_is_never_smaller_than_the_endpoint():
    payload = json.loads((RESULTS / "move_economics_v1.json").read_text())
    for entry in payload["combos"].values():
        for sub in entry["subsets"].values():
            if not sub.get("n"):
                continue
            assert sub["closed_at_touch"]["mean_max_excursion"] >= \
                sub["straddle_held_to_expiry"]["mean_absolute_endpoint_move"]


@pytest.mark.skipif(not (RESULTS / "straddle_gap_v1.json").exists(), reason="gap not computed")
def test_the_generous_assumption_is_recorded():
    payload = json.loads((RESULTS / "straddle_gap_v1.json").read_text())
    assert "most generous" in payload["assumption"]
    assert "option bid-ask spread" in payload["excluded_costs"]
    assert payload["no_option_data_used"] is True


# --- scope --------------------------------------------------------------------------------

PACKAGE = Path(SS.__file__).parent


def _code_only(path: Path) -> str:
    import io
    import tokenize

    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return " ".join(kept)


def test_nothing_imports_the_trading_engine():
    banned = ("crypto.paper", "crypto.terminal", "submit_order", "place_order",
              "liquidation_forward", "manual_r1", "manual_r2", "btc_p1_forward")
    for path in sorted(PACKAGE.glob("*.py")):
        code = _code_only(path)
        for needle in banned:
            assert needle not in code, f"{path.name} references {needle}"


def test_every_analysis_declares_itself_read_only():
    for name in ("signal_shape_v1.json", "move_economics_v1.json",
                 "realized_vs_trailing_v1.json", "breakout_followthrough_v1.json",
                 "activation_filter_v1.json", "straddle_gap_v1.json"):
        path = RESULTS / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        assert payload["status"].startswith("READ_ONLY")
        assert payload.get("no_pnl_backtest", True) is True


@pytest.mark.skipif(not MATRIX.exists(), reason="matrix not written")
def test_the_matrix_records_that_nothing_was_traded_or_bought():
    payload = json.loads(MATRIX.read_text())
    assert payload["orders_placed"] == 0
    assert payload["pnl_backtest"] is False
    assert payload["data_purchased"] is False
    assert payload["deployed"] is False


@pytest.mark.skipif(not MATRIX.exists(), reason="matrix not written")
def test_no_route_was_declared_viable():
    payload = json.loads(MATRIX.read_text())
    assert payload["verdict"] == "ECONOMICALLY_UNPROMISING"
    assert payload["vol_p1"] == "NOT_AUTHORIZED"
    assert not any(route["verdict"] == "VIABLE" for route in payload["routes"])


@pytest.mark.skipif(not MATRIX.exists(), reason="matrix not written")
def test_the_korea_restriction_is_quoted_not_paraphrased():
    payload = json.loads(MATRIX.read_text())
    okx = payload["exchange_access"]["OKX"]
    assert okx["verified"] == "direct fetch"
    assert "derivatives-related" in okx["quote"]
    assert payload["exchange_access"]["Binance"]["korea"] == "UNKNOWN"


@pytest.mark.skipif(not MATRIX.exists(), reason="matrix not written")
def test_the_free_data_window_is_recorded_as_insufficient():
    payload = json.loads(MATRIX.read_text())
    free = payload["free_data"]
    assert free["verdict"] == "INSUFFICIENT"
    assert free["files"] == 147
    assert max(free["p1_high_state_episodes_in_window"].values()) < 10
