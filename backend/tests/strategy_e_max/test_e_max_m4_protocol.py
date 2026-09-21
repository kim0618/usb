"""E-MAX-M4 protocol and X2 exit tests (phase A). Synthetic sessions only."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_r3 import build as B
from app.backtest.strategy_e_r3 import run as R3
from app.market.calendar import MarketCalendar
from app.strategy_e.execution import EntryBar
from app.strategy_e.exits import (
    DUPLICATE_EXIT_BAR, EXIT_MODEL, INVALID_EXIT_CLOSE, MISSING_EXIT_BAR, VALID_EXIT,
)
from app.strategy_e_max import breadth, capacity, exit_x2, m0, m3, m4, ranking
from app.strategy_e_v1_1 import decision as D

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"
CAL = MarketCalendar("America/New_York")
SESSION = date(2026, 9, 15)


def _tape(symbol, volume_d, drift):
    """09:30 open 10.0; 09:34 close 10*(1+4*drift)."""
    days, day = [], SESSION
    for _ in range(8):
        day = CAL.previous_trading_day(day)
        days.append(day)
    cols = ([], [], [], [])
    for d, volume in [(d, 1_000.0) for d in sorted(days)] + [(SESSION, volume_d)]:
        pre = [4 * 60 + 5, 7 * 60, 8 * 60 + 30] + list(range(9 * 60, 9 * 60 + 25, 4))
        for k, minute in enumerate(pre + list(range(P.OPEN_MIN, P.OPEN_MIN + 5))):
            price = 10.2 + 0.01 * k if minute < P.OPEN_MIN else 10.0 * (1 + drift * (minute - P.OPEN_MIN))
            for bucket, value in zip(cols, (ordinal(d), minute, price, volume)):
                bucket.append(value)
    p = np.array(cols[2])
    return SymbolTape(symbol=symbol, et_day=np.array(cols[0], dtype=np.int64),
                      minute=np.array(cols[1], dtype=np.int64), open=p, high=p * 1.0001,
                      low=p * 0.9999, close=p, volume=np.array(cols[3]), vwap=p, sources={},
                      overlap_sessions=0)


def _bar(symbol, close, minute="09:44", session=SESSION):
    return EntryBar(symbol, session, minute, close, close, close, close, 1_000.0, close)


def _session(drifts):
    tapes = {s: _tape(s, 5_000.0 + 1_000 * i, dr) for i, (s, dr) in enumerate(drifts.items())}
    rows = {s: B.symbol_rows(t, {SESSION}) for s, t in tapes.items()}
    columns = {s: j for j, s in enumerate(sorted(tapes))}
    n = len(columns)
    daily = B.DailySession(np.ones(n, bool), np.ones(n, bool), np.full(n, 10.0), np.full(n, 5e7))
    frame = B.frame_for(SESSION, rows, daily, columns, spy_close_previous=499.0)
    sealed = D.seal(frame, source_digest="s")
    view = R3.session_frame(frame, rows)
    bars = {s: dict(rows[s][SESSION].bars) for s in frame.symbols}
    return sealed, view, bars


@pytest.fixture(scope="module")
def rules():
    return m4.load_rules()


def test_upstream_identity(rules) -> None:
    up = rules["upstream"]
    assert up["m3_rules_canonical_sha256"] == m3.RULES_CANONICAL_SHA256
    assert up["m3_winner"] == "B2" and up["m3_m4_authorization"] == "M4 AUTHORIZED"
    assert "R1 + max 3 + B2" in rules["variants"]["comparator"]["configuration"]
    assert rules["exposure"].startswith("B2 unchanged")
    assert breadth.THRESHOLD == 0.030741


def test_x1_is_exact_0934_and_equals_capacity_execute() -> None:
    sealed, view, bars = _session({"AAA": 0.002, "BBB": -0.002, "CCC": 0.001})
    selected = ("AAA", "BBB", "CCC")
    a = exit_x2.execute(sealed.signal, selected, bars, view.descriptors, CAL, capacity=3, continuation=False)
    b = capacity.execute(sealed.signal, selected, bars, view.descriptors, CAL, capacity=3)
    assert a["records"] == b["records"] and a["returns"] == b["returns"]
    assert not any(d["extended"] for d in a["exit_decisions"])


def test_continuation_condition_is_strict_raw_price(rules) -> None:
    sealed, view, bars = _session({"AAA": 0.002, "BBB": -0.002, "CCC": 0.0})
    out = exit_x2.execute(sealed.signal, ("AAA", "BBB", "CCC"), {**bars, "AAA": {**bars["AAA"], "09:44": [_bar("AAA", 10.3)]}},
                          view.descriptors, CAL, capacity=3, continuation=True)
    decisions = {d["symbol"]: d for d in out["exit_decisions"]}
    assert decisions["AAA"]["extended"]                    # 10.08 > 10.00
    assert not decisions["BBB"]["extended"]                # below entry
    assert not decisions["CCC"]["extended"]                # equal is not strictly greater
    assert rules["variants"]["continuation_condition"] == "close(09:34) > open(09:30), strict, raw prices"


def test_extended_exit_uses_exact_0944_close_and_others_are_unchanged() -> None:
    sealed, view, bars = _session({"AAA": 0.002, "BBB": -0.002})
    bars["AAA"]["09:44"] = [_bar("AAA", 10.3)]
    x1 = exit_x2.execute(sealed.signal, ("AAA", "BBB"), bars, view.descriptors, CAL, capacity=3, continuation=False)
    x2 = exit_x2.execute(sealed.signal, ("AAA", "BBB"), bars, view.descriptors, CAL, capacity=3, continuation=True)
    a1 = next(r for r in x1["records"] if r["symbol"] == "AAA")
    a2 = next(r for r in x2["records"] if r["symbol"] == "AAA")
    assert a2["exit_price"] == 10.3 and a1["exit_price"] == pytest.approx(10.08)
    assert a2["COST_10BP"] == Decimal("10.3") / Decimal("10.0") - 1 - Decimal("0.001")
    assert next(r for r in x1["records"] if r["symbol"] == "BBB") == next(r for r in x2["records"] if r["symbol"] == "BBB")


@pytest.mark.parametrize("bars44,reason", [
    ([], MISSING_EXIT_BAR),
    ([_bar("AAA", 10.3, minute="09:43"), _bar("AAA", 10.3, minute="09:45")], MISSING_EXIT_BAR),
    ([_bar("AAA", 10.3), _bar("AAA", 10.4)], DUPLICATE_EXIT_BAR),
    ([_bar("AAA", float("nan"))], INVALID_EXIT_CLOSE),
    ([_bar("AAA", 0.0)], INVALID_EXIT_CLOSE),
])
def test_missing_or_invalid_0944_is_unresolved_without_fallback(bars44, reason) -> None:
    sealed, view, bars = _session({"AAA": 0.002, "BBB": 0.003})
    bars["AAA"]["09:44"] = bars44
    bars["BBB"]["09:44"] = [_bar("BBB", 10.5)]
    out = exit_x2.execute(sealed.signal, ("AAA", "BBB"), bars, view.descriptors, CAL, capacity=3, continuation=True)
    a = next(r for r in out["records"] if r["symbol"] == "AAA")
    b = next(r for r in out["records"] if r["symbol"] == "BBB")
    assert a["exit_reason"] == reason and a["exit_price"] is None and not a["standard_pnl"]
    assert b["weight"] == "1/1", "the remaining executable position takes the whole weight (E-D5 1/n)"


def test_missing_0934_cannot_extend() -> None:
    sealed, view, bars = _session({"AAA": 0.002})
    bars["AAA"]["09:34"] = []
    bars["AAA"]["09:44"] = [_bar("AAA", 10.5)]
    out = exit_x2.execute(sealed.signal, ("AAA",), bars, view.descriptors, CAL, capacity=3, continuation=True)
    assert out["records"][0]["exit_reason"] == MISSING_EXIT_BAR and not out["exit_decisions"][0]["extended"]


def test_gates_comparator_and_m5(rules) -> None:
    assert rules["improvement"]["comparator"].startswith("X1")
    e = rules["eligibility"]
    assert (e["mdd_10bp_ge"], e["top1_share_10bp_le"], e["min_coverage"]) == (-0.35, 0.7338, 0.95)
    ok, no = {"pass": True}, {"pass": False}
    assert m4.decide(ok, ok, ok, rules) == {"winner": "X2", "label": "E-MAX-M4 PASS — X2 SELECTED", "m5": "M5 AUTHORIZED"}
    assert m4.decide(ok, no, ok, rules)["label"] == "E-MAX-M4 NO_USEFUL_ENHANCEMENT"
    assert m4.decide(ok, no, ok, rules)["m5"] == "M5 AUTHORIZED"
    assert m4.decide(no, no, no, rules)["m5"] == "M5 NOT AUTHORIZED"
    assert m4.decide(ok, no, ok, rules, stop_d=True)["m5"] == "M5 NOT AUTHORIZED"
    assert rules["m5_authorization"]["grid"].endswith("1.0, 1.5, 2.0")


def test_eligibility_identity(rules) -> None:
    good = {"integrity": True, "coverage": 0.96, "mean_10bp": 1e-4, "pf_10bp": 1.2,
            "mdd_10bp": -0.35, "top1_share_10bp": 0.7338}
    assert m4.eligible(good, rules)["pass"]
    assert not m4.eligible({**good, "coverage": 0.9499}, rules)["pass"]


def test_no_new_horizon(rules) -> None:
    forbidden = " ".join(rules["variants"]["forbidden"])
    for token in ("09:39", "09:49", "09:59", "trailing", "VWAP"):
        assert token in forbidden
    assert exit_x2.EXTENDED_BAR_START_ET == "09:44" and EXIT_MODEL == "FIXED_FIVE_MINUTE_CLOSE_PROXY_V1"


def test_cost_limitation_recorded(rules) -> None:
    assert rules["costs"]["primary"] == "COST_10BP"
    assert "time-independent" in rules["costs"]["limitation"]


def test_paired_bootstrap_deterministic() -> None:
    delta = np.random.default_rng(3).normal(0, 1e-3, 480)
    assert X.bootstrap_mean_ci(delta) == X.bootstrap_mean_ci(delta)


def test_checksum_file() -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m4_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == m4.RULES_CANONICAL_SHA256
    assert recorded["file_sha256"] == hashlib.sha256((DOCS / "strategy_e_max_m4_rules_v1.json").read_bytes()).hexdigest()


# -- committed M4 result (phase B) ---------------------------------------------------------------

RESULT = DOCS / "strategy_e_max_m4_result_v1.json"


@pytest.fixture(scope="module")
def result():
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_result_checksum_and_repeat(result) -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m4_result_v1.sha256").read_text("utf-8"))
    assert recorded["file_sha256"] == hashlib.sha256(RESULT.read_bytes()).hexdigest()
    check = recorded["repeat_check"]
    assert check["identical"] and check["artifacts_identical"]
    assert check["first_result_digest"] == check["this_result_digest"] == recorded["file_sha256"]


def test_x1_reproduced_m3_b2_and_invariants(result, rules) -> None:
    pre = result["prechecks"]
    assert pre["x1_reproduces_m3_b2"] == {"trades_csv": rules["upstream"]["m3_b2_trades_csv_sha256"],
                                          "daily_returns_csv": rules["upstream"]["m3_b2_daily_returns_csv_sha256"]}
    assert pre["invariants"]["pass"] and pre["in_process_deterministic"]
    assert result["identity"]["m4_rules"] == m4.RULES_CANONICAL_SHA256


def test_exit_funnel_is_consistent(result) -> None:
    f = result["exit_funnel"]
    assert f["x2_0934_exits"] + f["x2_0944_extended_exits"] + f["x2_0944_unresolved"] + f["x2_unresolved_at_0934"] \
        == f["x1_0934_exits"] + f["x1_unresolved"]
    assert f["extension_attempts"] == f["x2_0944_extended_exits"] + f["x2_0944_unresolved"]
    assert result["continuation_outcome"]["gross_at_0934"]["positive_rate"] == 1.0


def test_trade_selection_identical(result) -> None:
    f1 = result["variants"]["X1"]["evaluation"]["funnel"]
    f2 = result["variants"]["X2"]["evaluation"]["funnel"]
    for key in ("eligible_universe_rows", "H5_candidates", "selected_candidates", "valid_entries"):
        assert f1[key]["count"] == f2[key]["count"]


def test_verdict_and_m5_recompute(result, rules) -> None:
    def summary(name):
        ev = result["variants"][name]["evaluation"]
        s = ev["scenarios"]["COST_10BP"]
        return {"integrity": result["integrity"], "coverage": ev["funnel"]["standard_pnl_coverage"],
                "mean_10bp": s["all_session_mean"], "pf_10bp": s["profit_factor"],
                "mdd_10bp": s["maximum_drawdown"],
                "top1_share_10bp": ev["concentration"]["cost_10bp"]["top1"]["share_of_total"]}
    e2, e1 = m4.eligible(summary("X2"), rules), m4.eligible(summary("X1"), rules)
    s1 = result["variants"]["X1"]["evaluation"]["scenarios"]["COST_10BP"]
    s2 = result["variants"]["X2"]["evaluation"]["scenarios"]["COST_10BP"]
    i = m4.improved(s2["cumulative_return"], s1["cumulative_return"], result["paired_x2_minus_x1"], rules)
    assert (e2, e1, i) == (result["x2_eligibility"], result["x1_eligibility"], result["x2_improvement"])
    assert m4.decide(e2, i, e1, rules) == result["verdict"]
    assert (result["paired_x2_minus_x1"]["seed"], result["paired_x2_minus_x1"]["replicates"]) == (20260921, 10_000)


def test_upstream_artifacts_unchanged() -> None:
    import subprocess
    for path in ("docs/backtest/strategy_e_candidate/strategy_e_v1_1_replay_result.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_m0_rules_v1.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_m3_result_v1.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_m4_rules_v1.json"):
        frozen = subprocess.run(["git", "-C", str(ROOT), "show", f"362b978:{path}"],
                                capture_output=True, check=True).stdout
        assert (ROOT / path).read_bytes() == frozen
