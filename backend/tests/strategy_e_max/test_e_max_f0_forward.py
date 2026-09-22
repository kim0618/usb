"""E-MAX-F0 forward shadow contract tests. Synthetic sessions only; no market data is read."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e1_forward import checkpoint as E1C, layout
from app.backtest.strategy_e1_forward.layout import ForwardViolation
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.market.calendar import MarketCalendar
from app.strategy_e.execution import EntryBar
from app.strategy_e_max import m0, v1
from app.strategy_e_max_forward import readiness as RD, rules as F, shadow as S
from app.strategy_e_v1_1 import context, decision
from app.strategy_e_v1_1.universe import DecisionFrame

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"
SESSION = date(2026, 9, 17)
CAL = MarketCalendar("America/New_York")


@pytest.fixture(scope="module")
def rules():
    return F.load_rules()


def _frame(n_h5=4, n_other=1, rvols=None, session=SESSION):
    symbols = [f"S{i:02d}" for i in range(n_h5 + n_other)]
    rvols = rvols or [3.0 + i for i in range(n_h5)]
    cols = {name: [] for name in SEALED_FEATURES}
    for i, s in enumerate(symbols):
        h5 = i < n_h5
        values = {"premarket_gap": 0.03 if h5 else -0.01, "premarket_rvol": rvols[i] if h5 else 1.0,
                  "position_in_premarket_range": 0.9, "return_0900_0925": 0.01,
                  "return_last30m": 0.01, "relative_strength_vs_spy": 0.02,
                  "premarket_dollar_volume": 1e6, "previous_day_dollar_volume": 5e7,
                  "close_price": 20.0, "spy_premarket_return": 0.001, "pm_bars": 50.0}
        for name in SEALED_FEATURES:
            cols[name].append(values[name])
    return DecisionFrame(session, tuple(symbols), {k: np.array(v, dtype=float) for k, v in cols.items()},
                         {"rows": len(symbols)})


def _sealed(frame):
    return decision.seal(frame, source_digest="synthetic")


def _bar(symbol, start, open_, close, session=SESSION, volume=1000.0):
    return EntryBar(symbol=symbol, session_date=session, bar_start_et=start, open=open_, high=max(open_, close),
                    low=min(open_, close), close=close, volume=volume)


def _decided(frame=None, mode=F.RECONSTRUCTED):
    frame = frame or _frame()
    sealed = _sealed(frame)
    when = datetime(2026, 9, 17, 20, 0, tzinfo=context.ET)
    return frame, sealed, S.shadow_decision(sealed, frame, forward_mode=mode, decided_at=when)


def _bars(symbols, *, missing_exit=()):
    out = {}
    for i, s in enumerate(symbols):
        out[s] = {"09:30": [_bar(s, "09:30", 10.0, 10.0)],
                  "09:34": [] if s in missing_exit else [_bar(s, "09:34", 10.0, 10.0 + 0.1 * (i + 1))]}
    return out


# -- identity / provenance ----------------------------------------------------------------------------

def test_e_max_v1_checksum_identity(rules) -> None:
    assert rules["upstream"]["e_max_v1_rules_canonical_sha256"] == v1.RULES_CANONICAL_SHA256
    recorded = json.loads((DOCS / "strategy_e_max_forward_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == F.RULES_CANONICAL_SHA256 == m0.canonical_sha256(dict(rules))
    assert recorded["file_sha256"] == hashlib.sha256((DOCS / "strategy_e_max_forward_rules_v1.json").read_bytes()).hexdigest()


def test_m6_result_and_code_identity(rules) -> None:
    closure = F.provenance_closure(rules)
    assert closure["checks"]["pass"], closure


def test_forward_code_outside_m6_digest_packages() -> None:
    assert not (ROOT / "backend/app/strategy_e_max/forward.py").exists()
    assert Path(F.__file__).parent.name == "strategy_e_max_forward"


# -- boundary ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("day", [date(2026, 9, 16), date(2026, 9, 15), date(2024, 11, 1)])
def test_forward_boundary_refuses_development(day, tmp_path) -> None:
    frame = _frame(session=day)
    sealed = _sealed(frame)
    with pytest.raises(ForwardViolation):
        S.shadow_decision(sealed, frame, forward_mode=F.RECONSTRUCTED, decided_at=datetime(2026, 9, 22, tzinfo=context.ET))
    with pytest.raises(ForwardViolation):
        S.append(tmp_path / "r.jsonl", [{"strategy": F.E_MAX, "forward_mode": F.RECONSTRUCTED,
                                         "session_date": day.isoformat()}])
    with pytest.raises(ForwardViolation):
        RD.assess(day, RD.Inventory(), today_et=date(2026, 9, 22), calendar=CAL)


def test_development_is_not_mutated() -> None:
    import subprocess
    for path in ("docs/backtest/strategy_e_max/strategy_e_max_v1_rules.json",
                 "docs/backtest/strategy_e_max/strategy_e_max_v1_result.json",
                 "docs/backtest/strategy_e_candidate/strategy_e_forward_feature_context_v1.json"):
        frozen = subprocess.run(["git", "-C", str(ROOT), "show", f"0a439a3:{path}"], capture_output=True, check=True).stdout
        assert (ROOT / path).read_bytes() == frozen


# -- seal ------------------------------------------------------------------------------------------

def test_decision_cutoff_and_live_seal_before_0930(rules) -> None:
    assert rules["decision_seal"]["cutoff_et"] == "09:25"
    frame = _frame()
    sealed = _sealed(frame)
    S.shadow_decision(sealed, frame, forward_mode=F.LIVE, decided_at=datetime(2026, 9, 17, 9, 25, tzinfo=context.ET))
    with pytest.raises(context.FutureContextViolation):
        S.shadow_decision(sealed, frame, forward_mode=F.LIVE, decided_at=datetime(2026, 9, 17, 9, 30, tzinfo=context.ET))


def test_r1_max3_and_e_base_selection() -> None:
    frame = _frame(n_h5=5, rvols=[3.5, 9.0, 4.0, 9.0, 6.0])
    _, sealed, record = _decided(frame)
    assert record[F.E_MAX]["order"] == ["S01", "S03", "S04", "S02", "S00"]      # rvol desc, tie symbol asc
    assert record[F.E_MAX]["selected"] == ["S01", "S03", "S04"]
    assert record[F.E_BASE]["selected"] == list(sealed.selected) == ["S00", "S01", "S02"]


@pytest.mark.parametrize("n_h5,n_other,high,final", [(4, 96, True, "3/1"), (3, 97, False, "2/1"), (4, 1, False, "2/1")])
def test_b2_state_and_exposure(n_h5, n_other, high, final) -> None:
    _, _, record = _decided(_frame(n_h5=n_h5, n_other=n_other))
    assert record[F.E_MAX]["breadth"]["high_breadth"] is high
    assert record[F.E_MAX]["final_exposure"] == final


def test_immutable_seal(tmp_path) -> None:
    _, _, record = _decided()
    path = tmp_path / "seal.json"
    S.write_seal(path, record)
    assert S.read_seal(path) == json.loads(path.read_text())
    with pytest.raises(ForwardViolation):
        S.write_seal(path, record)
    tampered = dict(record, **{F.E_MAX: dict(record[F.E_MAX], selected=["S00"])})
    path.write_text(json.dumps(tampered))
    with pytest.raises(ForwardViolation):
        S.read_seal(path)


def test_live_and_reconstructed_are_distinct() -> None:
    frame = _frame()
    sealed = _sealed(frame)
    a = S.shadow_decision(sealed, frame, forward_mode=F.LIVE, decided_at=datetime(2026, 9, 17, 9, 25, tzinfo=context.ET))
    b = S.shadow_decision(sealed, frame, forward_mode=F.RECONSTRUCTED, decided_at=datetime(2026, 9, 17, 9, 25, tzinfo=context.ET))
    assert (a["evidence_class"], b["evidence_class"]) == ("PRIMARY", "SECONDARY")
    assert a["decision_digest"] != b["decision_digest"]


# -- shadow outcome ----------------------------------------------------------------------------------

def test_exact_entry_exit_no_backfill_and_exposure() -> None:
    frame, sealed, record = _decided(_frame(n_h5=4, n_other=96))            # high breadth
    bars = _bars(["S00", "S01", "S02", "S03"], missing_exit={"S03"})
    out = S.shadow_outcome(record, sealed, frame, bars, CAL)
    emax, base = out[F.E_MAX], out[F.E_BASE]
    assert Fraction(emax["exposure"]) == 3 and Fraction(base["exposure"]) == 1
    emax_rows = {r["symbol"]: r for r in emax["records"]}
    assert record[F.E_MAX]["selected"] == ["S03", "S02", "S01"]
    assert emax_rows["S03"]["standard_pnl"] is False                      # UNRESOLVED_EXIT, no fallback
    assert "S00" not in emax_rows                                           # candidate #4 never backfills
    assert {r["weight"] for r in emax["records"] if r["standard_pnl"]} == {"3/2"}


def test_missing_entry_bar_is_entry_invalid() -> None:
    frame, sealed, record = _decided()
    bars = _bars(["S00", "S01", "S02", "S03"])
    bars["S03"]["09:30"] = []
    out = S.shadow_outcome(record, sealed, frame, bars, CAL)
    row = {r["symbol"]: r for r in out[F.E_MAX]["records"]}["S03"]
    assert row["entry_status"] != "EXECUTED_PROXY" and row["standard_pnl"] is False


def test_cost_schema_and_notional_scaling() -> None:
    frame, sealed, record = _decided()
    bars = _bars(["S00", "S01", "S02", "S03"])
    out = S.shadow_outcome(record, sealed, frame, bars, CAL)
    sessions = S.session_rows(record, out)
    for row in sessions:
        assert all(c in row for c in F.COST_SCENARIOS)
    emax = next(r for r in sessions if r["strategy"] == F.E_MAX)
    drag = Decimal(emax["GROSS_0BP"]) - Decimal(emax["COST_10BP"])
    assert abs(drag - Decimal(2) * Decimal("0.001")) < Decimal("1e-18")
    rows = S.registry_rows(record, out, bars)
    assert {k for k in ("net_05bp", "net_10bp", "net_15bp", "net_20bp", "gross_return")} <= set(rows[0])


def test_e_base_e_max_separation_and_registry_fields(rules) -> None:
    frame, sealed, record = _decided()
    bars = _bars(["S00", "S01", "S02", "S03"])
    out = S.shadow_outcome(record, sealed, frame, bars, CAL)
    rows = S.registry_rows(record, out, bars)
    assert {r["strategy"] for r in rows} == {F.E_BASE, F.E_MAX}
    assert set(rules["registry"]["position_fields"]) <= set(rows[0])
    by = {(r["strategy"], r["symbol"]): r for r in rows}
    assert by[(F.E_BASE, "S00")]["selected"] and not by[(F.E_MAX, "S00")]["selected"]
    assert by[(F.E_MAX, "S03")]["final_weight"] == "2/3" and by[(F.E_MAX, "S03")]["base_weight"] == "1/3"
    assert all(r["bid_0930"] is None and r["friction_status"] == "QUOTES_NOT_AVAILABLE" for r in rows)


def test_outcome_refuses_a_foreign_seal() -> None:
    frame, sealed, record = _decided()
    other = _sealed(_frame(n_h5=3))
    with pytest.raises(ForwardViolation):
        S.shadow_outcome(record, other, frame, _bars(["S00"]), CAL)


# -- registry / checkpoint ---------------------------------------------------------------------------

def test_append_only_registry_and_checkpoint(tmp_path, rules) -> None:
    frame, sealed, record = _decided()
    out = S.shadow_outcome(record, sealed, frame, _bars(["S00", "S01", "S02", "S03"]), CAL)
    path = tmp_path / "sessions.jsonl"
    S.append(path, S.session_rows(record, out))
    with pytest.raises(ForwardViolation):
        S.append(path, S.session_rows(record, out))
    assert S.checkpoint_n(path) == 4 and S.checkpoint_n(path, forward_mode=F.LIVE) == 0
    status = S.checkpoint_status(path, tmp_path / "cp")
    assert status["checkpoints"] == [250, 500, 1000, 2000] and status["n_reconstructed"] == 4
    assert tuple(rules["checkpoints"]["values"]) == E1C.CHECKPOINTS
    assert "H5 observation rows" in rules["checkpoints"]["n_definition"]
    assert rules["checkpoints"]["promotion_gate"].startswith("NOT DEFINED")


# -- readiness ---------------------------------------------------------------------------------------

def _inventory(universe=("AAA", "BBB")):
    inv = RD.Inventory()
    day = SESSION
    for _ in range(25):
        day = CAL.previous_trading_day(day)
        inv.daily_sessions.add(day)
    inv.reference_dates = {date(2026, 7, 1)}
    inv.splits_asof = {SESSION}
    inv.minute_ranges = {s: [(date(2026, 5, 18), SESSION)] for s in (*universe, "SPY")}
    inv.eligible_universe[SESSION] = tuple(universe)
    return inv


def test_readiness_ready_and_each_reason() -> None:
    assert RD.assess(SESSION, _inventory(), today_et=date(2026, 9, 22), calendar=CAL)["state"] == "READY"
    inv = _inventory()
    inv.splits_asof = set()
    inv.minute_ranges["BBB"] = [(date(2026, 5, 18), date(2026, 9, 16))]
    row = RD.assess(SESSION, inv, today_et=date(2026, 9, 17), calendar=CAL)
    assert row["state"] == "NOT_READY" and row["status"] == context.FEATURE_CONTEXT_INCOMPLETE
    assert set(row["reasons"]) == {"SESSION_NOT_CLOSED", "MISSING_SPLITS_ASOF", "MISSING_MINUTE"}
    empty = RD.assess(date(2026, 9, 21), RD.Inventory(), today_et=date(2026, 9, 22), calendar=CAL)
    assert set(empty["reasons"]) == {"MISSING_DAILY_CONTEXT", "MISSING_REFERENCE", "MISSING_SPLITS_ASOF",
                                     "MISSING_MINUTE", "MISSING_RVOL_HISTORY", "MISSING_SPY_CONTEXT"}


def test_incomplete_context_is_fail_closed_not_h5_false() -> None:
    inv = _inventory()
    del inv.minute_ranges["AAA"]
    row = RD.assess(SESSION, inv, today_et=date(2026, 9, 22), calendar=CAL)
    assert row["state"] == "NOT_READY" and "MISSING_RVOL_HISTORY" in row["reasons"]
    ctx = context.ForwardFeatureContext(SESSION, context.RECONSTRUCTED, (), (), None, None, None, None, None)
    with pytest.raises(context.FeatureContextIncomplete):
        context.require_complete(ctx, calendar=CAL)


def test_reserved_symbol_directory() -> None:
    assert RD.forward_minute_dir("CON") == "_CON" and RD.forward_minute_dir("AAPL") == "AAPL"


# -- no retuning path --------------------------------------------------------------------------------

def test_no_forward_retuning_path(rules) -> None:
    forbidden = " ".join(rules["forbidden"]["rule_changes"])
    for item in ("new ranking", "new max positions", "new breadth threshold", "new exit", "2.5x", "ABSI", "cost model"):
        assert item in forbidden
    assert "E-MAX V2" in rules["forbidden"]["in_response_to_forward_results"]
    assert rules["status_labels"] == {"DEVELOPMENT": "FROZEN", "FORWARD_SHADOW": "OBSERVATION",
                                      "PAPER": "NOT APPROVED", "LIVE": "NOT APPROVED"}
    assert v1.FINAL_EXPOSURE == {"normal": 2, "high_breadth": 3}


def test_friction_never_from_ohlc(rules) -> None:
    assert "never recorded as a spread" in rules["execution_friction"]["forbidden"]
    assert "NOT AVAILABLE" in rules["execution_friction"]["availability_2026_09_22"]["massive_stocks_basic"]
