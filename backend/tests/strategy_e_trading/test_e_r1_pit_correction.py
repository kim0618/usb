"""E-R1 Trading V1.1 PIT universe correction tests.

Synthetic fixtures only. No historical tape is read, and no trade, portfolio or performance
statistic is computed; the one synthetic return below exists to show the unchanged E-D4 layer is
still the one applied.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from app.backtest.strategy_e0_overnight import dataset as e0_dataset
from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e1_premarket.config import load_rules as load_e1_rules
from app.market.calendar import MarketCalendar
from app.strategy_e import costs, execution, exits, risk, signal
from app.strategy_e.costs import CostScenario, apply_cost
from app.strategy_e.execution import (
    MISSING_ENTRY_BAR, NOT_SELECTED_CAPACITY, EntryBar,
)
from app.strategy_e.exits import resolve_exit_batch
from app.strategy_e.risk import NOT_EXECUTABLE, NOT_SELECTED, SIZED, build_sizing_records
from app.strategy_e_v1_1 import context as C
from app.strategy_e_v1_1 import decision as D
from app.strategy_e_v1_1 import universe as U


ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_candidate"
CAL = MarketCalendar("America/New_York")
SESSION = date(2026, 9, 22)
PRIOR = 8


def _prior_sessions(count: int = PRIOR) -> list[date]:
    days, day = [], SESSION
    for _ in range(count):
        day = CAL.previous_trading_day(day)
        days.append(day)
    return sorted(days)


def _minutes(*, open_bar: bool, until: int = 9 * 60 + 35) -> list[int]:
    premarket = [4 * 60 + 5, 7 * 60, 8 * 60 + 30] + list(range(9 * 60, 9 * 60 + 25, 4))
    regular = list(range(P.OPEN_MIN, until + 1)) if open_bar else []
    return premarket + regular


def _tape(symbol: str, *, d_open_bar: bool = True, prior_open_bars: tuple[bool, ...] | None = None,
          d_volume: float = 5_000.0, post_cutoff_scale: float = 1.0,
          later_day: bool = False) -> SymbolTape:
    """Prior sessions at 1,000 shares a bar; D at ``d_volume``; prices rise through the premarket."""
    prior_open_bars = prior_open_bars or (True,) * PRIOR
    days, minutes, prices, volumes = [], [], [], []
    plan = [(day, flag, 1_000.0) for day, flag in zip(_prior_sessions(), prior_open_bars)]
    plan.append((SESSION, d_open_bar, d_volume))
    if later_day:
        plan.append((CAL.next_trading_day(SESSION), True, 99_999.0))
    for day, open_bar, volume in plan:
        for k, minute in enumerate(_minutes(open_bar=open_bar)):
            price = 10.2 + 0.01 * k
            if day == SESSION and minute > P.DECISION_LAST_BAR:
                price *= post_cutoff_scale
            days.append(ordinal(day))
            minutes.append(minute)
            prices.append(price)
            volumes.append(volume)
    p = np.array(prices)
    return SymbolTape(symbol=symbol, et_day=np.array(days, dtype=np.int64),
                      minute=np.array(minutes, dtype=np.int64), open=p, high=p * 1.0001,
                      low=p * 0.9999, close=p, volume=np.array(volumes), vwap=p,
                      sources={}, overlap_sessions=0)


def _context(close: float = 10.0) -> U.DailyContext:
    return U.DailyContext(eligible=True, close_price=close, previous_day_dollar_volume=5e7)


def _frame(tapes: dict[str, SymbolTape]):
    rows = {s: U.premarket_row(t, SESSION) for s, t in tapes.items()}
    return U.build_frame(SESSION, rows, {s: _context() for s in tapes},
                         spy_premarket_last=500.0, spy_close_previous=499.0)


def _bar(symbol: str, minute: str, price: float) -> EntryBar:
    return EntryBar(symbol, SESSION, minute, price, price, price, price, 1_000.0, price)


# -- 1. V1 future dependency, reproduced ------------------------------------------------------

def test_v1_session_rows_drop_a_symbol_whose_only_difference_is_the_0930_bar() -> None:
    a = _tape("A", d_open_bar=True)
    b = _tape("B", d_open_bar=False)
    rows_a, rows_b = P.session_rows(a), P.session_rows(b)
    key = ordinal(SESSION)
    assert key in rows_a
    assert key not in rows_b, "V1 defect provenance: B vanished before H5 because 09:30 was missing"
    assert rows_a[key].premarket == U.premarket_row(b, SESSION).premarket


def _fake_panel(t_len: int = 28, n: int = 4):
    sessions, day = [], date(2026, 6, 1)
    for _ in range(t_len):
        sessions.append(day)
        day = CAL.next_trading_day(day)
    rng = np.random.default_rng(3)
    close = rng.uniform(20.0, 30.0, (t_len, n))
    volume = np.full((t_len, n), 1e6)
    open_ = close.copy()
    open_[21, 1] = np.nan              # symbol 1: grouped-daily open of D missing for D-1 = row 20
    close[22, 2] = 4.0                 # symbol 2: fails the $5 floor on row 22
    factor = np.ones((t_len, n))
    factor[22:, 3] = 2.0               # symbol 3: split executing into row 22
    membership = np.ones((t_len, n), dtype=bool)
    panel = SimpleNamespace(
        sessions=tuple(sessions), shape=(t_len, n), close=close, volume=volume, open=open_,
        high=close * 1.01, low=close * 0.99, tickers=tuple(f"S{j}" for j in range(n)),
        split_arrays=lambda: (factor, None, None), membership=lambda: membership)
    rules = SimpleNamespace(warmup=20, first_index=20, last_index=t_len - 2,
                            min_close=5.0, min_dollar_volume=5e6, min_present_sessions=15)
    return panel, rules, factor, membership


def test_v1_e0_row_for_d_minus_1_requires_session_d_daily_open() -> None:
    panel, rules, _, _ = _fake_panel()
    rows = e0_dataset.build(SimpleNamespace(panel=panel), np.full(panel.shape[0], 400.0), rules)
    kept = set(zip(rows.session_idx.tolist(), rows.ticker_idx.tolist()))
    assert (20, 1) not in kept and rows.counters["no_next_open"] == 1
    assert (20, 0) in kept and (21, 1) in kept


# -- 2/3. V1.1 universe knows nothing after 09:24 --------------------------------------------

def test_v1_1_universe_is_independent_of_0930_bar_existence() -> None:
    frame = _frame({"A": _tape("A", d_open_bar=True), "B": _tape("B", d_open_bar=False)})
    assert frame.symbols == ("A", "B")
    for name in SEALED_FEATURES:
        assert frame.features[name][0] == frame.features[name][1] or (
            np.isnan(frame.features[name][0]) and np.isnan(frame.features[name][1]))


def test_v1_1_daily_eligibility_equals_e0_without_the_next_open_test() -> None:
    panel, rules, factor, membership = _fake_panel()
    rows = e0_dataset.build(SimpleNamespace(panel=panel), np.full(panel.shape[0], 400.0), rules)
    e0_kept = set(zip(rows.session_idx.tolist(), rows.ticker_idx.tolist()))
    sessions = list(panel.sessions)
    for t in range(rules.first_index, rules.last_index + 1):
        mask = U.daily_eligibility(
            sessions[t + 1], sessions[:t + 1], close=panel.close[:t + 1],
            volume=panel.volume[:t + 1], factor=factor[:t + 1], membership=membership[t],
            split_in_window=factor[t + 1] != factor[t], calendar=CAL)
        next_open = panel.open[t + 1]
        for j in range(panel.shape[1]):
            v1 = (t, j) in e0_kept
            if np.isfinite(next_open[j]) and next_open[j] > 0:
                assert bool(mask[j]) == v1, (t, j)
            else:
                assert bool(mask[j]) and not v1, "V1.1 admits the row V1 dropped for open(D)"
    assert not U.daily_eligibility(
        sessions[22], sessions[:22], close=panel.close[:22], volume=panel.volume[:22],
        factor=factor[:22], membership=membership[21], split_in_window=factor[22] != factor[21],
        calendar=CAL)[3], "the split executing on D still excludes the symbol"


def test_same_0925_inputs_give_the_same_universe_whatever_happens_later() -> None:
    base = U.premarket_row(_tape("A"), SESSION)
    variants = [_tape("A", d_open_bar=False), _tape("A", post_cutoff_scale=37.0),
                _tape("A", later_day=True)]
    for tape in variants:
        row = U.premarket_row(tape, SESSION)
        assert row.premarket == base.premarket and row.derived == base.derived
        assert row.pm_rvol == base.pm_rvol


def test_v1_1_rows_are_bit_identical_to_every_row_v1_produced() -> None:
    flags = (True, False, True, True, False, True, True, True)
    for d_open in (True, False):
        tape = _tape("A", d_open_bar=d_open, prior_open_bars=flags)
        v1 = P.session_rows(tape)
        for key, row in v1.items():
            day = date.fromordinal(key + date(1970, 1, 1).toordinal())
            ours = U.premarket_row(tape, day)
            assert ours.premarket == row.premarket
            assert (ours.pm_rvol == row.pm_rvol) or (np.isnan(ours.pm_rvol) and np.isnan(row.pm_rvol))
    assert U.premarket_row(_tape("A", prior_open_bars=flags), SESSION).history_sessions == 6


# -- 4/5. H5 identity -------------------------------------------------------------------------

def test_h5_statement_is_unchanged_across_e1_e_d0_and_v1_1() -> None:
    e1 = load_e1_rules().hypotheses["H5"]["rule"]
    e_d0 = signal._load_frozen_rules()["alpha"]["statement"]
    v11 = D.load_rules()["alpha"]["statement"]
    assert e1 == e_d0 == v11
    assert D.load_rules()["alpha"]["changed"] is False


@pytest.mark.parametrize("name,value,expected", [
    ("premarket_gap", 0.0, False), ("premarket_rvol", 3.0, True), ("premarket_rvol", 2.999, False),
    ("position_in_premarket_range", 0.8, True), ("position_in_premarket_range", 0.7999, False),
    ("return_0900_0925", 0.0, False), ("premarket_rvol", float("nan"), False)])
def test_h5_thresholds_and_inclusivity_are_unchanged(name, value, expected) -> None:
    frame = _frame({"A": _tape("A")})
    features = {k: v.copy() for k, v in frame.features.items()}
    assert D.seal(frame, source_digest="s").candidates == ("A",)
    features[name][0] = value
    sealed = D.seal(replace(frame, features=features), source_digest="s")
    assert (sealed.candidates == ("A",)) is expected


def test_h5_candidates_do_not_depend_on_0930_entry_availability() -> None:
    with_bar = D.seal(_frame({"A": _tape("A"), "B": _tape("B")}), source_digest="s")
    without = D.seal(_frame({"A": _tape("A", d_open_bar=False),
                             "B": _tape("B", d_open_bar=False)}), source_digest="s")
    assert with_bar.candidates == without.candidates == ("A", "B")
    assert with_bar.seal_digest == without.seal_digest


# -- 6/7. selection before entry validation, no backfill --------------------------------------

def _four_candidate_seal():
    return D.seal(_frame({s: _tape(s) for s in ("D", "C", "B", "A")}), source_digest="s")


def test_max3_selection_is_sealed_before_any_entry_bar_is_seen() -> None:
    sealed = _four_candidate_seal()
    assert sealed.candidates == ("A", "B", "C", "D")
    assert sealed.selected == ("A", "B", "C") and sealed.not_selected == ("D",)


def test_a_selected_symbol_missing_its_entry_does_not_promote_candidate_four() -> None:
    sealed = _four_candidate_seal()
    bars = [_bar("A", "09:30", 10.0), _bar("C", "09:30", 11.0), _bar("D", "09:30", 12.0)]
    batch = D.execute(sealed, bars, calendar=CAL)
    by = {r.symbol: r for r in batch.records}
    assert by["B"].selected and not by["B"].execution_eligible
    assert by["B"].skip_reason == MISSING_ENTRY_BAR
    assert not by["D"].selected and by["D"].skip_reason == NOT_SELECTED_CAPACITY
    assert sorted(s for s, r in by.items() if r.execution_eligible) == ["A", "C"]


def test_a_seal_edited_after_0925_is_refused() -> None:
    sealed = _four_candidate_seal()
    for edited in (replace(sealed, selected=("A", "B", "D"), not_selected=("C",)),
                   replace(sealed, candidates=("A", "B", "C")),
                   replace(sealed, seal_digest="0" * 64)):
        with pytest.raises(D.DecisionContractError):
            D.execute(edited, [], calendar=CAL)


# -- 8-11. entry, exit, cost and sizing are the frozen layers ---------------------------------

def _executed():
    sealed = _four_candidate_seal()
    bars = [_bar("A", "09:30", 10.0), _bar("C", "09:30", 20.0)]
    batch = D.execute(sealed, bars, calendar=CAL)
    exit_bars = {"A": (_bar("A", "09:34", 10.1),), "C": (_bar("C", "09:34", 19.8),),
                 "B": (_bar("B", "09:34", 5.0),)}
    return batch, resolve_exit_batch(batch, exit_bars, calendar=CAL)


def test_entry_contract_unchanged() -> None:
    batch, _ = _executed()
    a = next(r for r in batch.records if r.symbol == "A")
    assert a.entry_model == "FIRST_REGULAR_MINUTE_OPEN_PROXY_V1"
    assert a.entry_timestamp_et == "09:30" and a.entry_price == 10.0
    assert a.execution_rules_digest == execution.EXECUTION_RULES_CANONICAL_SHA256
    assert D.load_rules()["entry"]["model"] == execution.ENTRY_MODEL


def test_exit_contract_unchanged() -> None:
    _, exit_batch = _executed()
    by = {r.symbol: r for r in exit_batch.records}
    assert by["A"].exit_price == 10.1 and by["A"].exit_timestamp_et == "09:34"
    assert by["B"].exit_price is None, "B never entered, so its 09:34 bar is never read"
    assert D.load_rules()["exit"]["model"] == exits.EXIT_MODEL
    assert D.load_rules()["exit"]["fallback"] == "NONE"


def test_cost_contract_unchanged() -> None:
    _, exit_batch = _executed()
    a = next(r for r in exit_batch.records if r.symbol == "A")
    grid = {s.value: apply_cost(a, exit_batch.exit_digest, s).round_trip_cost_bp for s in CostScenario}
    assert grid == {"COST_05BP": 5, "COST_10BP": 10, "COST_15BP": 15, "COST_20BP": 20}
    record = apply_cost(a, exit_batch.exit_digest, CostScenario.COST_10BP)
    assert record.net_return == record.gross_return - Decimal("0.001")
    rules = D.load_rules()["costs"]
    assert rules["primary"] == "COST_10BP"
    assert rules["scenarios_bp"] == {"GROSS_0BP": 0, "COST_05BP": 5, "COST_10BP": 10,
                                     "COST_15BP": 15, "COST_20BP": 20}


def test_sizing_contract_unchanged() -> None:
    _, exit_batch = _executed()
    sizing = build_sizing_records(exit_batch)
    by = {r.symbol: r for r in sizing.records}
    assert by["A"].risk_status == by["C"].risk_status == SIZED
    assert by["A"].normalized_weight == by["C"].normalized_weight == Fraction(1, 2)
    assert by["B"].risk_status == NOT_EXECUTABLE and by["B"].normalized_weight == 0
    assert by["D"].risk_status == NOT_SELECTED
    assert D.load_rules()["sizing"]["model"] == risk.SIZING_MODEL


# -- 12. nothing after 09:24 in the decision digest -------------------------------------------

def test_no_post_0925_value_reaches_the_decision_digest() -> None:
    clean = D.seal(_frame({"A": _tape("A"), "B": _tape("B")}), source_digest="s")
    poisoned = D.seal(_frame({"A": _tape("A", post_cutoff_scale=91.0, later_day=True),
                              "B": _tape("B", post_cutoff_scale=0.01)}), source_digest="s")
    assert clean.signal.decision_digest == poisoned.signal.decision_digest
    assert clean.seal_digest == poisoned.seal_digest
    frame = _frame({"A": _tape("A")})
    assert set(frame.features) == set(SEALED_FEATURES)
    assert not any(name.startswith(("R_", "MFE_", "MAE_", "open_")) for name in frame.features)


# -- 13/14. forward context --------------------------------------------------------------------

def _daily_sessions(count: int = 21) -> tuple[date, ...]:
    days, day = [], SESSION
    for _ in range(count):
        day = CAL.previous_trading_day(day)
        days.append(day)
    return tuple(sorted(days))


def _full_context(**changes) -> C.ForwardFeatureContext:
    base = C.ForwardFeatureContext(
        session=SESSION, provenance=C.LIVE, daily_sessions=_daily_sessions(),
        daily_missing_sessions=(), reference_as_of=date(2026, 7, 1),
        splits_published_at=datetime(2026, 9, 22, 8, 0, tzinfo=C.ET),
        splits_execution_through=SESSION, spy_close_previous=500.0,
        spy_minute=C.MinuteCoverage(True), symbols={"A": C.MinuteCoverage(True)},
        sealed_at=datetime(2026, 9, 22, 9, 25, 5, tzinfo=C.ET))
    return replace(base, **changes)


def test_forward_daily_context_cutoff_is_d_minus_1() -> None:
    C.load_contract()
    C.require_complete(_full_context(), calendar=CAL)
    future = [
        _full_context(daily_sessions=_daily_sessions() + (SESSION,)),
        _full_context(reference_as_of=SESSION),
        _full_context(sealed_at=datetime(2026, 9, 22, 9, 31, tzinfo=C.ET)),
        _full_context(splits_published_at=datetime(2026, 9, 22, 9, 40, tzinfo=C.ET)),
    ]
    for context in future:
        with pytest.raises(C.FutureContextViolation):
            C.require_complete(context, calendar=CAL)
    sessions = _daily_sessions()
    arrays = dict(close=np.full((22, 1), 10.0), volume=np.full((22, 1), 1e6),
                  factor=np.ones((22, 1)), membership=np.ones(1, bool),
                  split_in_window=np.zeros(1, bool))
    with pytest.raises(U.UniverseContractError):
        U.daily_eligibility(SESSION, sessions + (SESSION,), calendar=CAL, **arrays)
    short = {k: (v[:21] if k in ("close", "volume", "factor") else v) for k, v in arrays.items()}
    with pytest.raises(U.UniverseContractError):
        U.daily_eligibility(SESSION, sessions[:-1] + (date(2026, 9, 18),), calendar=CAL, **short)
    assert U.daily_eligibility(SESSION, sessions, calendar=CAL, **short)[0]


@pytest.mark.parametrize("changes", [
    {"daily_sessions": _daily_sessions()[:-1]},
    {"daily_sessions": _daily_sessions(20)},
    {"daily_missing_sessions": (date(2026, 9, 1),)},
    {"reference_as_of": None},
    {"splits_published_at": None},
    {"splits_execution_through": date(2026, 9, 16)},
    {"spy_close_previous": None},
    {"spy_minute": C.MinuteCoverage(False)},
    {"symbols": {}},
    {"symbols": {"A": C.MinuteCoverage(True), "B": C.MinuteCoverage(False)}},
    {"symbols": {"A": C.MinuteCoverage(True, (date(2026, 9, 3),))}},
])
def test_incomplete_context_fails_closed_instead_of_returning_h5_false(changes) -> None:
    with pytest.raises(C.FeatureContextIncomplete) as refused:
        C.require_complete(_full_context(**changes), calendar=CAL)
    assert refused.value.status == C.FEATURE_CONTEXT_INCOMPLETE == "FEATURE_CONTEXT_INCOMPLETE"
    assert refused.value.missing


def test_spy_context_needs_no_0930_bar() -> None:
    spy = U.premarket_row(_tape("SPY", d_open_bar=False), SESSION)
    assert spy is not None and np.isfinite(spy.premarket["pm_last_price"])


# -- 15. frozen V1 artifacts untouched ----------------------------------------------------------

FROZEN_V1 = (
    "E_D0_TRADING_PREREGISTRATION_V1.md", "strategy_e_trading_rules_v1.json",
    "strategy_e_trading_rules_v1.sha256", "E_D1_SIGNAL_CONTRACT_V1.md",
    "E_D2_ENTRY_EXECUTION_CONTRACT_V1.md", "strategy_e_execution_rules_v1.json",
    "E_D3_EXIT_CONTRACT_V1.md", "strategy_e_exit_rules_v1.json",
    "E_D3_HORIZON_SEMANTICS_RESOLUTION_V1.md", "strategy_e_horizon_semantics_v1.json",
    "E_D4_COST_SLIPPAGE_CONTRACT_V1.md", "strategy_e_cost_rules_v1.json",
    "E_D5_RISK_SIZING_CONTRACT_V1.md", "strategy_e_risk_rules_v1.json",
    "E_D6_BACKTEST_PROTOCOL_V1.md", "strategy_e_backtest_rules_v1.json",
    "E_D6_DEVELOPMENT_BACKTEST_RESULT_V1.md", "strategy_e_d6_result_v1.json",
    "strategy_e_d6_result_v1.sha256", "strategy_e_d6_development_tape_v1.json",
    "e1_premarket_rules_v1.json", "e0_overnight_rules_v1.json", "E1_H5_FORWARD_PROTOCOL.md",
)
E_D6_RESULT_COMMIT = "0fb1edcf75756332c72c31e0c1e6f571b23736aa"


@pytest.mark.parametrize("name", FROZEN_V1)
def test_frozen_v1_artifacts_are_byte_identical_to_the_e_d6_commit(name) -> None:
    frozen = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{E_D6_RESULT_COMMIT}:docs/backtest/strategy_e_candidate/{name}"],
        capture_output=True, check=True).stdout
    assert (DOCS / name).read_bytes() == frozen


def test_frozen_loaders_and_e_d6_identity_still_hold() -> None:
    signal._load_frozen_rules(); execution._load_rules(); exits._load_rules()
    costs._load_rules(); risk._load_rules()
    body = (DOCS / "strategy_e_d6_result_v1.json").read_bytes()
    assert hashlib.sha256(body).hexdigest() == D.load_rules()["upstream"]["e_d6_result_file_sha256"]
    assert json.loads(body)["primary_gate"]["verdict"] == "E-D6 INCONCLUSIVE — STATISTICAL"
    from app.backtest.strategy_e_d6.run import code_digest
    assert code_digest() == json.loads(body)["identity"]["code"], \
        "app.strategy_e must not gain modules; V1.1 lives in app.strategy_e_v1_1"


def test_v1_1_rules_and_context_contract_checksums() -> None:
    for name, digest in (("strategy_e_trading_v1_1_rules", D.RULES_CANONICAL_SHA256),
                         ("strategy_e_forward_feature_context_v1", C.CONTRACT_CANONICAL_SHA256)):
        recorded = json.loads((DOCS / f"{name}.sha256").read_text(encoding="utf-8"))
        assert recorded["canonical_sha256"] == digest
        assert recorded["file_sha256"] == hashlib.sha256((DOCS / f"{name}.json").read_bytes()).hexdigest()
    status = D.load_rules()["version_authority"]["trading_v1_1_status"]
    assert status == {"preregistered": True, "development_validated": False,
                      "forward_approved": False, "live_approved": False}
