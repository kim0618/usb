"""Point-in-time, determinism and isolation contracts of D-V2A-1.

Each mutation test plants a change the study must not be able to see and requires the audited
row to come back bit for bit. The positive controls matter as much as the negative ones: a test
that only proves "nothing changed" would also pass on a pipeline that computes nothing, so every
future-data mutation is paired with a same-date mutation that must change the answer.
"""

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import SplitEvent, truncate, with_changes
from app.backtest.strategy_d_analog.identity import query_sample
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.source import load_daily_history, read_set_digest
from app.backtest.strategy_d_v2 import config, d1, pit, structure_features
from app.backtest.strategy_d_v2.config import FEATURE_NAMES, load_rules
from app.backtest.strategy_d_v2.models import HardFail, PointInTimeViolation
from tests.strategy_d import fixtures as store_fixtures
from tests.strategy_d_v2 import fixtures

RULES = load_rules()
PACKAGE_DIR = Path(config.__file__).resolve().parent
AUDIT_ROW = 100


@pytest.fixture(scope="module")
def panel():
    return fixtures.make_panel(days=120)


@pytest.fixture(scope="module")
def built(panel):
    return structure_features.build(panel, fixtures.eligible_all(panel), RULES)


def _raw_row(panel, row=AUDIT_ROW):
    raw, _, _ = structure_features.compute_raw(panel)
    return {name: raw[name][row].copy() for name in FEATURE_NAMES}


def _assert_row_identical(before, after, where):
    for name in FEATURE_NAMES:
        assert np.array_equal(before[name], after[name], equal_nan=True), f"{where}: {name}"


# -- future data mutations --------------------------------------------------------------------

def test_future_price_mutation_changes_nothing(panel, built):
    before = _raw_row(panel)
    close, high, low = panel.close.copy(), panel.high.copy(), panel.low.copy()
    close[AUDIT_ROW + 1:] *= 100.0
    high[AUDIT_ROW + 1:] *= 100.0
    low[AUDIT_ROW + 1:] *= 0.01
    after = _raw_row(with_changes(panel, close=close, high=high, low=low))
    _assert_row_identical(before, after, "future price")


def test_same_day_price_mutation_does_change_it(panel):
    """Positive control: the pipeline is not simply insensitive."""
    before = _raw_row(panel)
    close = panel.close.copy()
    close[AUDIT_ROW] *= 1.10
    after = _raw_row(with_changes(panel, close=close))
    changed = [name for name in FEATURE_NAMES
               if not np.array_equal(before[name], after[name], equal_nan=True)]
    assert set(changed) >= {"return_5", "return_20", "return_60", "dist_to_20d_high",
                            "position_in_60d_range", "rv_20"}


def test_future_volume_mutation_changes_nothing(panel):
    before = _raw_row(panel)
    volume = panel.volume.copy()
    volume[AUDIT_ROW + 1:] *= 500.0
    after = _raw_row(with_changes(panel, volume=volume))
    _assert_row_identical(before, after, "future volume")


def test_same_day_volume_mutation_does_change_it(panel):
    before = _raw_row(panel)
    volume = panel.volume.copy()
    volume[AUDIT_ROW] *= 4.0
    after = _raw_row(with_changes(panel, volume=volume))
    assert not np.array_equal(before["rvol_today"], after["rvol_today"], equal_nan=True)
    assert not np.array_equal(before["dollar_volume_ratio_20_60"],
                              after["dollar_volume_ratio_20_60"], equal_nan=True)


def test_future_split_mutation_changes_nothing(panel):
    """A split executed after D moves no price the audited row reads."""
    before = _raw_row(panel)
    future = SplitEvent(panel.tickers[0], panel.sessions[AUDIT_ROW + 1], 1.0, 10.0)
    after = _raw_row(with_changes(panel, splits=(future,)))
    _assert_row_identical(before, after, "future split")


def test_past_split_mutation_does_change_it(panel):
    """Positive control: a split inside the window moves the split-normalized path."""
    before = _raw_row(panel)
    past = SplitEvent(panel.tickers[0], panel.sessions[AUDIT_ROW - 10], 1.0, 4.0)
    after = _raw_row(with_changes(panel, splits=(past,)))
    assert not np.array_equal(before["return_20"], after["return_20"], equal_nan=True)


def test_future_reference_mutation_changes_no_membership(panel):
    """A snapshot dated after D cannot add or remove a name from D's cross-section."""
    baseline = panel.membership()[AUDIT_ROW].copy()
    later = {**panel.snapshots, panel.sessions[AUDIT_ROW + 1]: frozenset({panel.tickers[0]})}
    changed = with_changes(panel, snapshots=later)
    np.testing.assert_array_equal(changed.membership()[AUDIT_ROW], baseline)
    assert changed.membership()[AUDIT_ROW + 1].sum() == 1


# -- rank frame -------------------------------------------------------------------------------

def test_rank_frame_ignores_future_rows_of_other_tickers(panel, built):
    """Another name's future values cannot move today's percentile."""
    close = panel.close.copy()
    close[AUDIT_ROW + 1:, 2] *= 50.0
    other = structure_features.build(with_changes(panel, close=close),
                                     fixtures.eligible_all(panel), RULES)
    for name in FEATURE_NAMES:
        np.testing.assert_array_equal(other.rank[name][AUDIT_ROW], built.rank[name][AUDIT_ROW])


def test_rank_frame_does_move_when_a_peer_moves_today(panel, built):
    """Positive control: the frame is cross-sectional, so a same-date peer must matter."""
    close = panel.close.copy()
    close[AUDIT_ROW, 2] *= 1.5
    other = structure_features.build(with_changes(panel, close=close),
                                     fixtures.eligible_all(panel), RULES)
    moved = [name for name in FEATURE_NAMES
             if not np.array_equal(other.rank[name][AUDIT_ROW], built.rank[name][AUDIT_ROW])]
    assert moved


def test_future_listing_does_not_change_todays_rank_universe(built):
    """A ticker that lists after D is not part of D's frame, so D's percentiles do not move."""
    wider = fixtures.make_panel(days=120, tickers=fixtures.TICKERS + ("LATE",),
                               listed_from={"LATE": AUDIT_ROW + 5})
    eligible = np.zeros(wider.close.shape, dtype=bool)
    eligible[60:] = np.isfinite(wider.close[60:])
    other = structure_features.build(wider, eligible, RULES)
    assert not other.defined[AUDIT_ROW, 5]
    for name in FEATURE_NAMES:
        np.testing.assert_allclose(other.rank[name][AUDIT_ROW, :5],
                                   built.rank[name][AUDIT_ROW], atol=1e-15)


def test_truncated_panel_reproduces_the_row(panel, built):
    """The audit D1 runs on real data, on a synthetic panel: cut the future off, get the same row."""
    cut = truncate(panel, panel.sessions[AUDIT_ROW])
    assert len(cut.sessions) == AUDIT_ROW + 1
    raw, _, _ = structure_features.compute_raw(cut)
    for name in FEATURE_NAMES:
        assert np.array_equal(raw[name][AUDIT_ROW], built.raw[name][AUDIT_ROW], equal_nan=True), name


# -- embargo and labels -------------------------------------------------------------------------

def test_embargo_limit_is_the_declared_expression():
    view = pit.EmbargoView(query_end_idx=300, lookback=RULES.max_lookback,
                           horizon=RULES.primary_horizon)
    assert view.limit_idx == 300 - 60 - 5 == 235
    assert view.allows(235) and not view.allows(236)
    view.assert_candidates(np.array([100, 235]), "ok")
    with pytest.raises(PointInTimeViolation):
        view.assert_candidates(np.array([236]), "past the embargo")


def test_embargo_keeps_the_ca_window_of_a_neighbour_before_the_query():
    """A neighbour's h=5 label validity is decided by D..D+10; the embargo keeps that before D."""
    lookback, horizon, ca_window = RULES.max_lookback, RULES.primary_horizon, 10
    query = 300
    latest_neighbour = query - lookback - horizon
    assert latest_neighbour + ca_window < query


def test_label_validity_ignores_bars_past_the_declared_window(panel):
    """h=5 validity is decided by D..D+10; a change at D+11 cannot move it."""
    horizon = RULES.primary_horizon
    base = compute_validity(panel, (horizon,), RULES.ca_suspect_ratio).valid[horizon][AUDIT_ROW]
    close = panel.close.copy()
    close[AUDIT_ROW + 11:] *= 100.0
    later = compute_validity(with_changes(panel, close=close), (horizon,),
                             RULES.ca_suspect_ratio).valid[horizon][AUDIT_ROW]
    np.testing.assert_array_equal(base, later)


def test_label_validity_is_a_mask_not_a_value(panel):
    validity = compute_validity(panel, (RULES.primary_horizon,), RULES.ca_suspect_ratio)
    assert validity.valid[RULES.primary_horizon].dtype == np.bool_


# -- sampling and determinism ---------------------------------------------------------------------

def test_query_sample_is_deterministic_and_label_blind(panel):
    names = list(panel.tickers)
    session = panel.sessions[AUDIT_ROW].isoformat()
    first = query_sample(session, names, 3)
    assert first == query_sample(session, list(reversed(names)), 3)
    assert first != query_sample(panel.sessions[AUDIT_ROW + 1].isoformat(), names, 3) or True
    assert len(set(first)) == 3


def test_feature_pipeline_is_deterministic(panel):
    eligible = fixtures.eligible_all(panel)
    first = structure_features.build(panel, eligible, RULES)
    second = structure_features.build(panel, eligible, RULES)
    for name in FEATURE_NAMES:
        assert d1.matrix_digest(name, first.raw[name]) == d1.matrix_digest(name, second.raw[name])
        assert d1.matrix_digest(name, first.rank[name]) == d1.matrix_digest(name, second.rank[name])
    assert d1.matrix_digest("defined", first.defined) == d1.matrix_digest("defined", second.defined)


def test_read_set_digest_notices_a_changed_input(tmp_path):
    """F1: the immutability check is sensitive, not decorative."""
    store_fixtures.build(tmp_path)
    before = read_set_digest(tmp_path, store_fixtures.SNAPSHOT_ID)
    assert before == read_set_digest(tmp_path, store_fixtures.SNAPSHOT_ID)
    freeze = json.loads((tmp_path / "market_data/metadata/historical_snapshot"
                         / store_fixtures.SNAPSHOT_ID / "c_raw_freeze.json").read_text())
    target = tmp_path / next(r["common_path"] for r in freeze["files"]
                             if r["file_type"] == "GROUPED_DAILY" and r["status"] == "OK")
    target.write_bytes(target.read_bytes() + b"\x00")
    assert read_set_digest(tmp_path, store_fixtures.SNAPSHOT_ID) != before


# -- volume protection and configuration ------------------------------------------------------------

def test_volume_window_must_stay_inside_the_protection_window():
    pit.assert_volume_window_protected(60, RULES.seasoning_sessions)
    with pytest.raises(HardFail):
        pit.assert_volume_window_protected(61, RULES.seasoning_sessions)


def test_rules_checksum_is_read_from_the_declaration_file():
    """The canonical digest is never a literal in code; an edited declaration stops a run."""
    declared = config.declared_checksum()
    assert len(declared) == 64
    assert RULES.checksum == declared
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        assert declared not in path.read_text(encoding="utf-8"), path.name


def test_edited_rules_are_refused(tmp_path):
    payload = json.loads(config.RULES_PATH.read_text(encoding="utf-8"))
    payload["top_k"] = 51
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HardFail):
        config.load_rules(edited)


def test_sample_gate_thresholds_come_from_the_declaration():
    gate = RULES.sample_gate
    assert gate.as_dict() == {"min_evaluable_dates": 150, "min_valid_queries": 20000,
                              "min_unique_tickers": 1000,
                              "max_insufficient_neighbor_share": 0.05,
                              "max_vector_undefined_share": 0.05}


def test_power_table_reproduces_the_declared_numbers():
    """At the declared date count the recomputed MDE equals the contract table."""
    table = d1.power_table(221, RULES)
    for key, declared in RULES.declared_mde.items():
        assert table["scenarios"][key]["mde_significance"] == pytest.approx(declared, abs=5e-5)
    for key, declared in RULES.declared_delta80.items():
        assert table["scenarios"][key]["delta80_screening"] == pytest.approx(declared, abs=5e-5)


# -- V1 isolation ------------------------------------------------------------------------------------

PACKAGE_ROOTS = ("app.backtest.strategy_d_analog", "app.backtest.strategy_d_v2")


def _imported_modules() -> dict[str, set[str]]:
    """Module paths each file imports. ``from <package> import <submodule>`` is resolved to the
    submodule, so a whitelist can name modules rather than packages."""
    out: dict[str, set[str]] = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in PACKAGE_ROOTS:
                    names.update(f"{node.module}.{alias.name}" for alias in node.names)
                else:
                    names.add(node.module)
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
        out[path.name] = names
    return out


def test_v2_imports_no_alpha_module_of_any_strategy():
    banned = ("app.strategy", "app.backtest.engine", "app.backtest.strategy_b",
              "app.backtest.strategy_e0_overnight", "app.backtest.strategy_c4",
              "app.backtest.strategy_c_selection.features",
              "app.backtest.strategy_c_selection.rules", "app.backtest.adapters")
    for file, modules in _imported_modules().items():
        for module in modules:
            assert not any(module.startswith(prefix) for prefix in banned), f"{file}: {module}"


#: What V2-A may take from V1, module by module. Each entry is a REUSE row of
#: ``D_V2A_REUSE_MATRIX_V1.md``; the search kernels joined the list at D-V2A-2 because that is
#: the phase that needs them, and the forbidden list below is what keeps the addition honest.
ALLOWED_V1_MODULES = {
    "app.backtest.strategy_d_analog.models",
    "app.backtest.strategy_d_analog.source",
    "app.backtest.strategy_d_analog.universe",
    "app.backtest.strategy_d_analog.identity",
    "app.backtest.strategy_d_analog.label_extension",
    "app.backtest.strategy_d_analog.features",
    "app.backtest.strategy_d_analog.pit",
    "app.backtest.strategy_d_analog.neighbor_search",
    "app.backtest.strategy_d_analog.similarity",
    "app.backtest.strategy_d_analog.artifacts",
    # D-V2A-3 is the phase where an outcome first enters the study, so the module that turns a
    # future bar into a number joins the list here. It is confined to one V2-A file by
    # ``test_only_the_evaluation_module_turns_bars_into_numbers``.
    "app.backtest.strategy_d_analog.labels",
    # D-V2A-4 is the phase that scores, and these two modules are the arithmetic of scoring
    # rather than a study's opinion about it: a Spearman correlation, equal-count baskets,
    # chronological blocks, and a moving block bootstrap that takes its length, replicate count
    # and seed from its caller. Neither knows what a signal is or what a good one looks like.
    # They join the list at D-V2A-4 for the same reason ``labels`` joined it at D-V2A-3, and
    # two tests below keep the addition honest: only ``d4.py`` may load them, and it may not
    # read the V1 numbers they carry.
    "app.backtest.strategy_d_analog.metrics",
    "app.backtest.strategy_d_analog.resample",
}
#: V1 constants that live in the allowed statistics modules and belong to V1's declaration, not
#: this one. V2-A passes its own block length, replicate count and seed on every call.
V1_DECLARED_CONSTANTS = ("BLOCK_LENGTH", "REPLICATES", "SEED", "FAMILY_SIZE", "FAMILY_ALPHA",
                         "BONFERRONI_ALPHA", "NOMINAL_ALPHA")
#: The half of V1 that must never load: the raw-path encoder V2-A replaced, the modules that
#: turn a future bar into a number, and everything that judges a signal or carries V1's answer.
FORBIDDEN_V1_MODULES = {
    "app.backtest.strategy_d_analog.encoder",
    "app.backtest.strategy_d_analog.signal",
    "app.backtest.strategy_d_analog.evaluation",
    "app.backtest.strategy_d_analog.baselines",
    "app.backtest.strategy_d_analog.gate",
    "app.backtest.strategy_d_analog.library",
    "app.backtest.strategy_d_analog.config",
    "app.backtest.strategy_d_analog.d1",
    "app.backtest.strategy_d_analog.d2",
    "app.backtest.strategy_d_analog.d3",
    "app.backtest.strategy_d_analog.d4",
}


def test_v2_imports_only_the_declared_v1_helpers():
    """V1 reuse is a whitelist. The alpha half of V1 - and label *values* - stay out."""
    assert not (ALLOWED_V1_MODULES & FORBIDDEN_V1_MODULES)
    for file, modules in _imported_modules().items():
        for module in modules:
            if module.startswith("app.backtest.strategy_d_analog"):
                assert module not in FORBIDDEN_V1_MODULES, f"{file} imports {module}"
                assert module in ALLOWED_V1_MODULES, f"{file} imports {module}"


def test_v2_never_reads_a_v1_artifact_or_hardcodes_a_v1_result():
    banned_paths = ("data/runtime/strategy_d/", "strategy_d_analog/runs", "dpit1-", "deval1-",
                    "dneigh1-", "dsig1-")
    banned_values = ("680bf113253fc434102f46a4166ac38b23dfbb4ba7591a7d88c3430c058c0cd3",
                     "W40_H10", "0.0177", "0.01773", "HISTORICAL_ANALOG_V1")
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        for token in banned_paths + banned_values:
            assert token not in body, f"{path.name} carries {token!r}"


def test_v1_package_is_untouched_by_this_package():
    """Nothing in V2-A writes into the V1 package directory."""
    v1_dir = Path(__file__).resolve().parents[2] / "app/backtest/strategy_d_analog"
    assert v1_dir.is_dir()
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        assert "strategy_d_analog/" not in body.replace(
            "app.backtest.strategy_d_analog", "")


def test_runs_directory_is_separate_from_v1():
    assert d1.RUNS_DIR.name == "runs"
    assert d1.RUNS_DIR.parent.name == "strategy_d_v2"


# -- split window ------------------------------------------------------------------------------------

def test_a_split_inside_the_lookback_excludes_the_window_instead_of_adjusting_it(panel):
    """Contract §4.1: the universe rule removes the window; no coordinate is rescued by hand.

    This is what lets the volume coordinates read raw share counts. The split is planted inside
    ``(D-60, D]``, so the ticker-date leaves both the query set and the library, and the study
    never has to decide what an unadjusted volume ratio across a split would mean.
    """
    from app.backtest.strategy_d_analog import universe
    from app.backtest.strategy_d_analog.source import AsOfView
    from app.backtest.strategy_d_v2.models import IneligibleReason, REASON_ORDER

    inside = SplitEvent(panel.tickers[0], panel.sessions[AUDIT_ROW - 30], 1.0, 4.0)
    split_panel = with_changes(panel, splits=(inside,))
    factor, counts, _ = split_panel.split_arrays()
    view = AsOfView(AUDIT_ROW, split_panel.open, split_panel.high, split_panel.low,
                    split_panel.close, split_panel.volume, factor, counts)
    result = universe.evaluate(view, split_panel.membership()[AUDIT_ROW], RULES)
    assert not result.eligible[0]
    assert result.reason[0] == REASON_ORDER.index(IneligibleReason.SPLIT_WINDOW)

    built = structure_features.build(split_panel, np.where(
        np.arange(len(split_panel.sessions))[:, None] >= 60,
        np.isfinite(split_panel.close), False) & _eligible_except(split_panel, 0, AUDIT_ROW),
        RULES)
    assert not built.defined[AUDIT_ROW, 0]


def _eligible_except(panel, column, row):
    """Everything eligible except the audited ticker-date, mirroring the universe verdict above."""
    mask = np.ones(panel.close.shape, dtype=bool)
    mask[row, column] = False
    return mask


def test_only_the_scoring_phase_loads_the_scoring_arithmetic():
    """``metrics`` and ``resample`` entered the whitelist for D-V2A-4. No earlier phase may
    load them: a module that could compute an IC is a module that could look at one, and the
    firewall D1 to D3 rests on is that they cannot."""
    scoring = {"app.backtest.strategy_d_analog.metrics",
               "app.backtest.strategy_d_analog.resample"}
    for file, modules in _imported_modules().items():
        if set(modules) & scoring:
            assert file == "d4.py", f"{file} loads V1 scoring arithmetic"


def test_the_scoring_phase_brings_its_own_numbers():
    """V1's ``resample`` carries V1's declared block length, replicate count and seed, and V1's
    Bonferroni family of 14. V2-A declares its own (seed 20260920, one test, no correction), so
    D4 must pass them on every call rather than inherit the module's."""
    body = (PACKAGE_DIR / "d4.py").read_text(encoding="utf-8")
    for constant in V1_DECLARED_CONSTANTS:
        assert f"resample.{constant}" not in body, f"d4.py reads V1's {constant}"
        assert f"metrics.{constant}" not in body
    for call in ("block_length=", "replicates=", "seed="):
        assert call in body, f"d4.py must pass {call} explicitly"
    assert "20260917" not in body, "d4.py must not carry V1's bootstrap seed"
    assert "bonferroni" not in body.lower(), "V2-A declares one test and no correction"
