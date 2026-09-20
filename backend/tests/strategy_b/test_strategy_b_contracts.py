"""Type, config and isolation contracts of the Strategy B research layer."""

import ast
from dataclasses import fields, is_dataclass, replace
import json
from pathlib import Path
import sys

import pytest

from app.strategy_b import config as config_module
from app.strategy_b.config import (
    RESEARCH_DEFAULTS, STRATEGY_ID, DollarVolumePriceBasis, StrategyBConfig, SymbolListProvenance,
    TrailingModel,
)
from app.strategy_b.errors import InvalidConfig, SyntheticBarMisuse
from app.strategy_b.models import (
    TERMINAL_CANDIDATE_STATES, Availability, CandidateState, CorporateActionFlag, FeatureSnapshot,
    Measured, MomentumBar, Session, SetupType, require_actual_bars,
)
from app.strategy_b.scope import TickerMetadataAsOf, evaluate_research_scope
from app.strategy_b.spread import SpreadFeatures
from tests.strategy_b.fixtures import et


PACKAGE = Path(__file__).resolve().parents[2] / "app" / "strategy_b"


# ---- setups, states, sessions ----------------------------------------------------------

def test_v1_has_exactly_two_setups() -> None:
    assert [s.value for s in SetupType] == ["HOD_BREAKOUT", "FIRST_PULLBACK"]


def test_no_vwap_reclaim_setup_anywhere_in_the_package() -> None:
    forbidden = ("VWAP_RECLAIM", "VWAP 회복", "VWAP_RECOVERY")
    for path in PACKAGE.rglob("*"):
        if path.suffix in {".py", ".md"}:
            text = path.read_text(encoding="utf-8")
            assert not [word for word in forbidden if word in text], path.name
    with pytest.raises(ValueError):
        SetupType("VWAP_RECLAIM")


def test_candidate_fsm_states_are_b_owned() -> None:
    assert [s.value for s in CandidateState] == [
        "DETECTED", "QUALIFIED", "WATCHING", "SETUP_READY", "ENTRY_SIGNALLED", "ENTERED",
        "REJECTED", "EXPIRED", "CANCELLED"]
    assert TERMINAL_CANDIDATE_STATES == {CandidateState.ENTERED, CandidateState.REJECTED,
                                         CandidateState.EXPIRED, CandidateState.CANCELLED}
    from app.strategy.lifecycle import StrategyPhase
    assert not issubclass(CandidateState, StrategyPhase)


def test_session_and_corporate_action_vocabularies() -> None:
    assert [s.value for s in Session] == ["PREMARKET", "REGULAR", "AFTER", "OUTSIDE"]
    assert {f.value for f in CorporateActionFlag} == {
        "SPLIT_ON_DAY", "RECENT_SPLIT", "IPO_WARMUP", "DELISTING_WINDOW", "CA_SUSPECT", "SYMBOL_CHANGE"}


def test_feature_snapshot_carries_every_required_field() -> None:
    required = {
        "symbol", "as_of", "session", "price", "return_1m", "return_3m", "return_5m",
        "session_vwap", "vwap_distance_pct", "hod", "lod", "hod_distance_pct",
        "rolling_dollar_volume", "cumulative_dollar_volume", "volume_acceleration", "rvol",
        "rvol_status", "missing_minute_ratio", "sparse_status", "halt_inferred", "split_adjusted",
        "corporate_action_flags"}
    assert required <= {f.name for f in fields(FeatureSnapshot)}


def test_measured_value_exists_exactly_when_available() -> None:
    assert Measured.of(1.5).value == 1.5
    assert Measured.missing(Availability.NO_DATA).value is None
    with pytest.raises(ValueError):
        Measured(None, Availability.AVAILABLE)
    with pytest.raises(ValueError):
        Measured(1.0, Availability.NO_DATA)
    with pytest.raises(ValueError):
        Measured.of(float("nan"))


# ---- bar contract ----------------------------------------------------------------------

def test_bar_contract_accepts_source_vwap_transactions_and_fractional_volume() -> None:
    bar = MomentumBar(et(9, 30), 10, 10.2, 9.9, 10.1, 123.45, Session.REGULAR, vwap=10.05, transactions=7)
    assert bar.available_at == et(9, 31)
    assert bar.synthetic is False


@pytest.mark.parametrize("kwargs, message", [
    ({"timestamp": et(9, 30).replace(tzinfo=None)}, "timezone-aware"),
    ({"timestamp": et(9, 30, 5)}, "minute boundary"),
    ({"high": 9.95}, "bound"),
    ({"volume": -1.0}, "volume"),
    ({"vwap": 0.0}, "vwap"),
    ({"close": float("inf")}, "finite"),
])
def test_bar_contract_refuses_malformed_bars(kwargs: dict, message: str) -> None:
    base = dict(timestamp=et(9, 30), open=10.0, high=10.2, low=9.9, close=10.1, volume=1.0,
                session=Session.REGULAR)
    with pytest.raises(ValueError, match=message):
        MomentumBar(**{**base, **kwargs})


def test_synthetic_bar_is_flat_zero_volume_and_refused_by_actual_bar_consumers() -> None:
    synthetic = MomentumBar(et(9, 31), 10, 10, 10, 10, 0.0, Session.REGULAR, synthetic=True)
    with pytest.raises(ValueError):
        MomentumBar(et(9, 31), 10, 10, 10, 10, 5.0, Session.REGULAR, synthetic=True)
    with pytest.raises(ValueError):
        MomentumBar(et(9, 31), 10, 10.1, 10, 10, 0.0, Session.REGULAR, synthetic=True)
    actual = MomentumBar(et(9, 30), 10, 10.2, 9.9, 10.1, 1.0, Session.REGULAR)
    require_actual_bars([actual])
    with pytest.raises(SyntheticBarMisuse):
        require_actual_bars([actual, synthetic])


# ---- config ----------------------------------------------------------------------------

def test_config_has_every_required_section_and_field() -> None:
    plain = StrategyBConfig().to_dict()
    expected = {
        "scanner": {"return_1m_threshold", "return_3m_threshold", "return_5m_threshold",
                    "min_dollar_volume", "min_rvol", "max_spread_pct"},
        "candidate": {"score_threshold", "candidate_ttl_minutes", "setup_ttl_minutes", "signal_ttl_minutes",
                      "price_drift_tolerance_pct"},
        "hod_breakout": {"consolidation_min_bars", "consolidation_max_bars", "breakout_buffer_pct",
                         "max_pullback_pct"},
        "first_pullback": {"min_pullback_pct", "max_pullback_pct", "min_duration_bars",
                           "max_duration_bars"},
        "risk": {"risk_per_trade_pct", "max_position_pct", "max_open_positions",
                 "max_entries_per_symbol", "daily_loss_limit_r"},
        "exit": {"partial_take_profit_r", "partial_exit_fraction", "trailing_model", "time_stop_minutes"},
    }
    for section, names in expected.items():
        assert names <= set(plain[section]), section
    assert plain["strategy_id"] == STRATEGY_ID
    assert plain["defaults_status"] == RESEARCH_DEFAULTS


def test_config_round_trips_through_json_and_fingerprint_is_deterministic() -> None:
    config = StrategyBConfig()
    restored = StrategyBConfig.from_dict(json.loads(config.canonical_json()))
    assert restored == config
    assert restored.fingerprint() == config.fingerprint() == StrategyBConfig().fingerprint()
    assert len(config.fingerprint()) == 64


def test_fingerprint_changes_with_any_value() -> None:
    base = StrategyBConfig()
    changed = replace(base, scanner=replace(base.scanner, min_rvol=3.5))
    other_enum = replace(base, exit=replace(base.exit, trailing_model=TrailingModel.BREAKEVEN_AFTER_PARTIAL))
    basis = replace(base, features=replace(base.features, dollar_volume_basis=DollarVolumePriceBasis.SOURCE_VWAP))
    assert len({base.fingerprint(), changed.fingerprint(), other_enum.fingerprint(), basis.fingerprint()}) == 4


# ---- time units ------------------------------------------------------------------------

CLOCK_MINUTE_FIELDS = {
    ("candidate", "candidate_ttl_minutes"), ("candidate", "setup_ttl_minutes"),
    ("candidate", "signal_ttl_minutes"), ("exit", "time_stop_minutes"),
    ("exit", "eod_min_margin_minutes"),
    ("features", "rolling_dollar_volume_window_minutes"),
    ("features", "volume_acceleration_window_minutes"),
    ("halt", "min_gap_minutes"), ("halt", "max_gap_minutes"),
}
ACTUAL_BAR_FIELDS = {
    ("hod_breakout", "consolidation_min_bars"), ("hod_breakout", "consolidation_max_bars"),
    ("first_pullback", "min_duration_bars"), ("first_pullback", "max_duration_bars"),
}
RENAMED_CLOCK_FIELDS = {"candidate": ("candidate_ttl", "setup_ttl", "signal_ttl"), "exit": ("time_stop",)}


def _config_leaf_names() -> set[tuple[str, str]]:
    config = StrategyBConfig()
    return {(section.name, leaf.name) for section in fields(config)
            if is_dataclass(value := getattr(config, section.name)) for leaf in fields(value)}


def test_every_minutes_and_bars_field_has_a_declared_time_unit() -> None:
    leaves = _config_leaf_names()
    assert {leaf for leaf in leaves if leaf[1].endswith("_minutes")} == CLOCK_MINUTE_FIELDS
    assert {leaf for leaf in leaves if leaf[1].endswith("_bars")} == ACTUAL_BAR_FIELDS


def test_old_bar_named_clock_fields_are_rejected_and_new_names_accepted() -> None:
    plain = StrategyBConfig().to_dict()
    for section, stems in RENAMED_CLOCK_FIELDS.items():
        for stem in stems:
            payload = json.loads(json.dumps(plain))
            payload[section][f"{stem}_bars"] = payload[section].pop(f"{stem}_minutes")
            with pytest.raises(InvalidConfig, match=rf"unknown=\['{stem}_bars'\] missing=\['{stem}_minutes'\]"):
                StrategyBConfig.from_dict(payload)
    accepted = json.loads(json.dumps(plain))
    accepted["candidate"]["candidate_ttl_minutes"] = 45
    accepted["exit"]["time_stop_minutes"] = 20
    restored = StrategyBConfig.from_dict(accepted)
    assert (restored.candidate.candidate_ttl_minutes, restored.exit.time_stop_minutes) == (45, 20)


def test_old_clock_field_names_appear_nowhere_in_the_package_or_its_tests() -> None:
    old_names = [f"{stem}_bars" for stems in RENAMED_CLOCK_FIELDS.values() for stem in stems]
    offenders = [f"{path.name}:{name}"
                 for root in (PACKAGE, Path(__file__).resolve().parent)
                 for path in sorted(root.rglob("*")) if path.suffix in {".py", ".md"}
                 for name in old_names if name in path.read_text(encoding="utf-8")]
    assert offenders == []


def test_canonical_json_uses_the_new_names_and_fingerprint_tracks_them() -> None:
    base = StrategyBConfig()
    text = base.canonical_json()
    assert text == StrategyBConfig().canonical_json()
    assert all(f'"{name}":' in text for _, name in CLOCK_MINUTE_FIELDS | ACTUAL_BAR_FIELDS)
    variants = [
        replace(base, candidate=replace(base.candidate, candidate_ttl_minutes=31)),
        replace(base, candidate=replace(base.candidate, setup_ttl_minutes=11)),
        replace(base, candidate=replace(base.candidate, signal_ttl_minutes=3)),
        replace(base, exit=replace(base.exit, time_stop_minutes=31)),
        replace(base, first_pullback=replace(base.first_pullback, max_duration_bars=11)),
    ]
    prints = [base.fingerprint(), *(v.fingerprint() for v in variants)]
    assert len(set(prints)) == len(prints)
    assert [StrategyBConfig.from_dict(json.loads(v.canonical_json())).fingerprint() for v in variants] == prints[1:]


def test_test_symbol_list_is_marked_unverified_research_default() -> None:
    config = StrategyBConfig()
    assert config.scope.test_symbols_provenance is SymbolListProvenance.RESEARCH_DEFAULT_UNVERIFIED
    assert [m.value for m in SymbolListProvenance] == ["RESEARCH_DEFAULT_UNVERIFIED"]
    plain = config.to_dict()
    assert plain["scope"]["test_symbols_provenance"] == "RESEARCH_DEFAULT_UNVERIFIED"
    plain["scope"]["test_symbols_provenance"] = "VERIFIED"
    with pytest.raises(InvalidConfig):
        StrategyBConfig.from_dict(plain)


def test_from_dict_refuses_unknown_missing_and_mistyped_values() -> None:
    plain = StrategyBConfig().to_dict()
    with pytest.raises(InvalidConfig, match="unknown"):
        StrategyBConfig.from_dict({**plain, "extra": 1})
    missing = {k: v for k, v in plain.items() if k != "risk"}
    with pytest.raises(InvalidConfig, match="missing"):
        StrategyBConfig.from_dict(missing)
    for section, name, bad in [("risk", "max_open_positions", True), ("risk", "max_open_positions", 2.5),
                               ("scanner", "min_rvol", "3"), ("exit", "trailing_model", "VWAP"),
                               ("features", "return_scope", "WHOLE_WEEK")]:
        payload = json.loads(json.dumps(plain))
        payload[section][name] = bad
        with pytest.raises(InvalidConfig):
            StrategyBConfig.from_dict(payload)


def test_config_refuses_inconsistent_ranges() -> None:
    base = StrategyBConfig()
    with pytest.raises(InvalidConfig):
        replace(base.first_pullback, min_pullback_pct=9.0, max_pullback_pct=8.0)
    with pytest.raises(InvalidConfig):
        replace(base.exit, partial_exit_fraction=1.5)
    with pytest.raises(InvalidConfig):
        replace(base.rvol, lookback_sessions=4, min_partial_sessions=5)
    with pytest.raises(InvalidConfig):
        replace(base, strategy_id="REALTIME_MOMENTUM_V2")


# ---- isolation and absent features -----------------------------------------------------

def test_package_imports_only_itself_and_the_standard_library() -> None:
    """No Strategy A, backtest core, broker, database, websocket or any third-party import."""
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                offenders.append(f"{path.name}:relative import")
                continue
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            offenders += [f"{path.name}:{n}" for n in names
                          if not (n == "app.strategy_b" or n.startswith("app.strategy_b.")
                                  or n.split(".")[0] in sys.stdlib_module_names)]
    assert offenders == []


def test_scope_metadata_has_no_market_cap_and_the_filter_takes_no_same_day_argument() -> None:
    assert not [f.name for f in fields(TickerMetadataAsOf) if "cap" in f.name.lower()]
    import inspect
    assert list(inspect.signature(evaluate_research_scope).parameters) == ["inputs", "config"]


def test_spread_layer_does_not_estimate_a_spread() -> None:
    assert "estimated_spread_pct" not in {f.name for f in fields(SpreadFeatures)}
    assert "estimated_spread" not in Path(config_module.__file__).with_name("spread.py").read_text().replace(
        "no ``estimated_spread_pct``", "")
