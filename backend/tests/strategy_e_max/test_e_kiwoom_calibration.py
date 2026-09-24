"""E-KIWOOM-CAL K0: the calibration measures, and the guards that keep it a measurement."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e1_premarket import premarket as P
from app.dev import run_e_kiwoom_calibration as K
from app.strategy_e_max_rt.rvol_store import RVOL_MINIMUM, RVOL_WINDOW

REPO = Path(__file__).resolve().parents[3]
D = date(2026, 9, 16)
SOURCE = inspect.getsource(K)


# -- identity ----------------------------------------------------------------------------------

def test_kiwoom_rolling_median_is_the_frozen_rule() -> None:
    """The calibration's own roller must agree with the frozen research implementation."""
    values = [100.0 * (n + 1) for n in range(30)]
    series = [(D - timedelta(days=30 - n), v) for n, v in enumerate(values)]
    mine = K.rolling_rvol(series)
    frozen = P._rolling_prior_median(values, RVOL_WINDOW, RVOL_MINIMUM)  # noqa: SLF001
    for i, (session, value) in enumerate(series):
        median = frozen[i]
        if np.isnan(median) or median <= 0:
            assert session not in mine
        else:
            assert mine[session][0] == pytest.approx(value / median, rel=0, abs=1e-12)
    assert all(depth <= RVOL_WINDOW for _, depth in mine.values())


def test_the_denominator_never_sees_its_own_session_or_a_later_one() -> None:
    series = [(D - timedelta(days=10 - n), 100.0) for n in range(6)] + [(D, 900.0)]
    out = K.rolling_rvol(series)
    assert out[D][0] == pytest.approx(9.0)          # 900 / median(prior 100s), the row itself excluded
    assert out[D][1] == 6                            # exactly the six priors, nothing after


def test_overlap_join_keeps_only_rows_both_sides_hold() -> None:
    """The join is (symbol, session) and both sides must produce a finite RVOL."""
    assert "kiwoom.get(symbol, {}).get(day)" in SOURCE
    assert "math.isfinite(float(massive_rvol))" in SOURCE
    assert "staged=1 AND complete=1" in SOURCE       # only staged Kiwoom rows enter


def test_sources_are_never_mixed() -> None:
    """No Massive numerator over a Kiwoom denominator, or the reverse, outside the labelled control."""
    assert "depth_matched_control" in SOURCE
    assert "a diagnostic control, not a rule change" in SOURCE
    # the two RVOL series are built by separate functions from separate stores
    assert "def kiwoom_rows(" in SOURCE and "def massive_frame(" in SOURCE


# -- the decision pieces stay the frozen ones ------------------------------------------------------

def test_only_the_rvol_column_is_swapped() -> None:
    body = inspect.getsource(K.structural)
    assert 'features = dict(base) | {"premarket_rvol": rvol}' in body
    for untouched in ("premarket_gap", "position_in_premarket_range", "return_0900_0925"):
        assert f'"{untouched}"' not in body.split("features = dict(base)")[1]


def test_h5_r1_top3_and_b2_come_from_the_frozen_modules() -> None:
    body = inspect.getsource(K.structural)
    assert "EV.mask(\"H5\", features)" in body                      # Research's own mask
    assert "ranking.order(v1.RANKING" in body                        # R1 as implemented
    assert "order[:v1.MAX_SELECTED]" in body                         # max 3, nothing refilled
    assert "breadth.multiplier(" in body                             # B2 as implemented
    assert "backfill" not in body.lower()


# -- candidates are not chosen by a return ----------------------------------------------------------

def test_threshold_candidates_use_the_distribution_only() -> None:
    body = SOURCE.split("threshold_candidates")[-1]
    for banned in ("pnl", "return_pct", "cagr", "profit", "equity"):
        assert banned not in body.lower()
    assert "np.quantile(kiwoom_rvol" in SOURCE                       # quantile match
    assert "no return was used, no grid was scanned" in SOURCE


def test_no_grid_search_anywhere() -> None:
    for banned in ("np.arange(2", "np.linspace(", "for threshold in ("):
        assert banned not in SOURCE


def test_no_performance_number_is_computed_in_k0() -> None:
    for banned in ("cagr", "sharpe", "max_drawdown", "profit_factor", "net_pnl"):
        assert banned not in SOURCE.lower()


# -- the contract and the artifacts ------------------------------------------------------------------

def test_the_protocol_was_frozen_and_still_hashes_to_its_recorded_digest() -> None:
    rules = REPO / "docs/backtest/strategy_e_max/e_kiwoom_rvol_calibration_k0_rules.json"
    recorded = (rules.with_suffix(".json.sha256")).read_text(encoding="utf-8").strip()
    canonical = json.dumps(json.loads(rules.read_text(encoding="utf-8")), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(canonical).hexdigest() == recorded
    body = json.loads(rules.read_text(encoding="utf-8"))
    limits = body["acceptance_limits_declared_before_results"]
    assert limits["top3_set_agreement_min"] == 0.8 and limits["rvol_spearman_min"] == 0.85
    assert body["paper_runtime"]["threshold_during_this_study"] == 3.0


def test_the_hybrid_is_never_called_a_kiwoom_native_backtest() -> None:
    rules = json.loads((REPO / "docs/backtest/strategy_e_max/e_kiwoom_rvol_calibration_k0_rules.json")
                       .read_text(encoding="utf-8"))
    assert rules["k1_authorization"]["hybrid_label"].startswith("a Massive feature frame")
    for path in ("docs/backtest/strategy_e_max/E_KIWOOM_RVOL_CALIBRATION_K0_RESULT.md",
                 "docs/backtest/strategy_e_max/E_KIWOOM_RVOL_CALIBRATION_K0_PROTOCOL.md"):
        text = (REPO / path).read_text(encoding="utf-8")
        assert "Kiwoom-native backtest" not in text or "never called a Kiwoom-native backtest" in text


def test_strategy_e_max_v1_artifacts_are_unchanged() -> None:
    rules = REPO / "docs/backtest/strategy_e_max/strategy_e_max_v1_rules.json"
    recorded = (REPO / "docs/backtest/strategy_e_max/strategy_e_max_v1_rules.sha256").read_text().strip()
    canonical = json.dumps(json.loads(rules.read_text(encoding="utf-8")), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(canonical).hexdigest() in recorded
    from app.strategy_e_max import v1
    assert v1.RULES_CANONICAL_SHA256 in recorded                     # the loader agrees with the file


def test_the_frozen_h5_threshold_is_still_three() -> None:
    from app.strategy_e_max_rt.paper import frozen_rvol_threshold
    assert frozen_rvol_threshold() == 3.0 == K.THRESHOLD
