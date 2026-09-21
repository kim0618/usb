"""E-MAX-M2 protocol and capacity-execution tests (phase A). Synthetic sessions only."""

from __future__ import annotations

from datetime import date
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6 import replay as E6
from app.backtest.strategy_e_r3 import build as B
from app.backtest.strategy_e_r3 import run as R3
from app.market.calendar import MarketCalendar
from app.strategy_e import execution, exits
from app.strategy_e_max import capacity, m0, m1, m2, ranking
from app.strategy_e_v1_1 import decision as D

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"
CAL = MarketCalendar("America/New_York")
SESSION = date(2026, 9, 15)


def _tape(symbol, *, volume_d, d_open=True, exit_bar=True):
    days, day = [], SESSION
    for _ in range(8):
        day = CAL.previous_trading_day(day)
        days.append(day)
    cols = ([], [], [], [])
    for d, volume in [(d, 1_000.0) for d in sorted(days)] + [(SESSION, volume_d)]:
        pre = [4 * 60 + 5, 7 * 60, 8 * 60 + 30] + list(range(9 * 60, 9 * 60 + 25, 4))
        regular = [] if (d == SESSION and not d_open) else list(range(P.OPEN_MIN, P.OPEN_MIN + 4))
        if regular and (d != SESSION or exit_bar):
            regular.append(P.OPEN_MIN + 4)
        for k, minute in enumerate(pre + regular):
            price = 10.2 + 0.01 * k if minute < P.OPEN_MIN else 10.0 * (1 + 0.002 * (minute - P.OPEN_MIN))
            for bucket, value in zip(cols, (ordinal(d), minute, price, volume)):
                bucket.append(value)
    p = np.array(cols[2])
    return SymbolTape(symbol=symbol, et_day=np.array(cols[0], dtype=np.int64),
                      minute=np.array(cols[1], dtype=np.int64), open=p, high=p * 1.0001,
                      low=p * 0.9999, close=p, volume=np.array(cols[3]), vwap=p, sources={},
                      overlap_sessions=0)


# six candidates; R1 (rvol) order F, E, D, C, B, A; C lacks its 09:30 bar
SPEC = {"A": 5_000, "B": 6_000, "C": 7_000, "D": 8_000, "E": 9_000, "F": 10_000}


def _session(missing=("C",)):
    tapes = {s: _tape(s, volume_d=v, d_open=s not in missing) for s, v in SPEC.items()}
    rows = {s: B.symbol_rows(t, {SESSION}) for s, t in tapes.items()}
    columns = {s: j for j, s in enumerate(sorted(tapes))}
    n = len(columns)
    daily = B.DailySession(np.ones(n, bool), np.ones(n, bool), np.full(n, 10.0), np.full(n, 5e7))
    frame = B.frame_for(SESSION, rows, daily, columns, spy_close_previous=499.0)
    sealed = D.seal(frame, source_digest="s")
    view = R3.session_frame(frame, rows)
    feats = {s: {k: float(frame.features[k][frame.symbols.index(s)]) for k in
                 ("premarket_rvol", "return_0900_0925", "position_in_premarket_range", "premarket_gap")}
             for s in frame.symbols}
    bars = {s: rows[s][SESSION].bars for s in frame.symbols}
    return sealed, view, feats, bars


@pytest.fixture(scope="module")
def rules():
    return m2.load_rules()


def test_m0_and_m1_identity(rules) -> None:
    assert rules["upstream"]["m0_rules_canonical_sha256"] == m0.RULES_CANONICAL_SHA256
    assert rules["upstream"]["m1_rules_canonical_sha256"] == m1.RULES_CANONICAL_SHA256
    assert rules["upstream"]["m1_winner"] == "R1"


def test_capacities_are_exactly_three_and_five(rules) -> None:
    assert rules["variants"]["comparator"]["max_selected"] == 3
    assert rules["variants"]["candidate"]["max_selected"] == 5
    assert rules["variants"]["comparator"]["ordering"] == rules["variants"]["candidate"]["ordering"] == "R1"


@pytest.mark.parametrize("value", [4, 6])
def test_other_capacities_are_refused(tmp_path, value, monkeypatch) -> None:
    payload = json.loads(m2.RULES_PATH.read_text(encoding="utf-8"))
    payload["variants"]["candidate"]["max_selected"] = value
    path = tmp_path / "m2.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(m2, "RULES_CANONICAL_SHA256", m0.canonical_sha256(payload))
    with pytest.raises(m2.M2ContractError):
        m2.load_rules(path)


def test_r1_ordering_unchanged() -> None:
    _, _, feats, _ = _session()
    assert ranking.order("R1", list(feats), feats) == ("F", "E", "D", "C", "B", "A")


def test_capacity_three_reproduces_the_frozen_replay_record_for_record() -> None:
    sealed, view, feats, bars = _session()
    selected, _ = ranking.select("R1", sealed.signal.candidate_symbols, feats, 3)
    from dataclasses import replace
    subset = replace(sealed.signal, candidate_symbols=tuple(sorted(selected)), candidate_count=3)
    frozen = E6.replay_session(subset, view, {s: bars[s] for s in selected}, CAL)
    ours = capacity.execute(sealed.signal, selected, bars, view.descriptors, CAL, capacity=3)
    assert ours["returns"] == frozen["returns"] and ours["exposure"] == frozen["exposure"]
    assert ours["records"] == frozen["records"]
    assert ours["digests"]["execution"] != "" and frozen["digests"]["execution"]


def test_max5_equal_weight_and_no_sixth_backfill() -> None:
    sealed, view, feats, bars = _session(missing=("C",))
    selected, rest = ranking.select("R1", sealed.signal.candidate_symbols, feats, 5)
    assert selected == ("F", "E", "D", "C", "B") and rest == ("A",)
    out = capacity.execute(sealed.signal, selected, bars, view.descriptors, CAL, capacity=5)
    by = {r["symbol"]: r for r in out["records"]}
    assert "A" not in by, "candidate #6 is never executed"
    assert by["C"]["entry_status"] == execution.MISSING_ENTRY_BAR and by["C"]["weight"] == "0/1"
    assert {by[s]["weight"] for s in ("F", "E", "D", "B")} == {"1/4"}
    assert Fraction(out["exposure"]) == 1
    expected = sum(Fraction(1, 4) * Fraction(str(by[s]["COST_10BP"])) for s in ("F", "E", "D", "B"))
    assert Fraction(str(out["returns"]["COST_10BP"])) == expected


def test_all_five_executable_get_one_fifth() -> None:
    sealed, view, feats, bars = _session(missing=())
    selected, _ = ranking.select("R1", sealed.signal.candidate_symbols, feats, 5)
    out = capacity.execute(sealed.signal, selected, bars, view.descriptors, CAL, capacity=5)
    assert {r["weight"] for r in out["records"]} == {"1/5"} and out["exposure"] == "1"


def test_entry_is_the_same_whichever_chunk_a_symbol_lands_in() -> None:
    sealed, view, feats, bars = _session(missing=())
    five = capacity.execute(sealed.signal, ("A", "B", "C", "D", "E"), bars, view.descriptors, CAL, capacity=5)
    three = capacity.execute(sealed.signal, ("C", "D", "E"), bars, view.descriptors, CAL, capacity=3)
    for symbol in ("D", "E"):
        a = next(r for r in five["records"] if r["symbol"] == symbol)
        b = next(r for r in three["records"] if r["symbol"] == symbol)
        assert (a["entry_price"], a["exit_price"], a["COST_10BP"]) == (b["entry_price"], b["exit_price"], b["COST_10BP"])


def test_capacity_refuses_oversized_or_foreign_selection() -> None:
    sealed, view, feats, bars = _session(missing=())
    with pytest.raises(capacity.CapacityError):
        capacity.execute(sealed.signal, ("A", "B", "C", "D"), bars, view.descriptors, CAL, capacity=3)
    with pytest.raises(capacity.CapacityError):
        capacity.execute(sealed.signal, ("A", "ZZZ"), bars, view.descriptors, CAL, capacity=5)


def test_entry_exit_cost_exposure_identity(rules) -> None:
    ex = rules["execution"]
    assert ex["primary_cost"] == "COST_10BP" and ex["exposure_multiplier"] == 1.0
    assert tuple(ex["costs"]) == E6.SCENARIOS == capacity.SCENARIOS
    assert execution.ENTRY_BAR_START_ET == "09:30" and exits.EXIT_BAR_START_ET == "09:34"
    assert "NOT COMMON-RISK COMPLIANT" in rules["risk_status"]["c2"]


def test_gates_and_comparator(rules) -> None:
    assert rules["improvement"]["comparator"].startswith("C1")
    e = rules["eligibility"]
    assert (e["mdd_10bp_ge"], e["top1_share_10bp_le"], e["min_coverage"]) == (-0.35, 0.7338, 0.95)
    good = {"integrity": True, "coverage": 0.96, "mean_10bp": 1e-4, "pf_10bp": 1.2,
            "mdd_10bp": -0.2, "top1_share_10bp": 0.2}
    assert m2.eligible(good, rules)["pass"]
    assert not m2.eligible({**good, "top1_share_10bp": 0.74}, rules)["pass"]
    assert not m2.eligible({**good, "mdd_10bp": -0.36}, rules)["pass"]
    ok = m2.improved(0.4, 0.3, {"mean": 1e-5, "p_mean_le_zero": 0.05}, rules)
    assert m2.decide(m2.eligible(good, rules), ok, rules)["label"] == "E-MAX-M2 PASS — MAX5 SELECTED"
    bad = m2.improved(0.4, 0.3, {"mean": 1e-5, "p_mean_le_zero": 0.2}, rules)
    assert m2.decide(m2.eligible(good, rules), bad, rules)["label"] == "E-MAX-M2 NO_USEFUL_ENHANCEMENT"


def test_paired_bootstrap_deterministic() -> None:
    delta = np.random.default_rng(1).normal(0, 1e-3, 480)
    assert X.bootstrap_mean_ci(delta) == X.bootstrap_mean_ci(delta)


def test_checksum_file() -> None:
    recorded = json.loads((DOCS / "strategy_e_max_m2_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == m2.RULES_CANONICAL_SHA256
    assert recorded["file_sha256"] == hashlib.sha256((DOCS / "strategy_e_max_m2_rules_v1.json").read_bytes()).hexdigest()
