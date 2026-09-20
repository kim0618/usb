"""D1 contracts on synthetic data: grid G1~G8, hard-fail codes, the universe rule and PIT.

Every test here runs on a fixture dataset written into ``tmp_path``. No test reads the real
freeze, the Drive workspace or the network, and no test looks at a forward label - D1 cannot see
a study result, by construction.
"""

import ast
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from app.backtest.strategy_d_analog import d1, universe
from app.backtest.strategy_d_analog.config import (
    DECLARED_RULES_CHECKSUM, RULES_PATH, canonical_checksum, load_rules,
)
from app.backtest.strategy_d_analog.identity import (
    code_digest, n1_order_key, query_order_key, query_sample, run_identity,
)
from app.backtest.strategy_d_analog.models import (
    HardFail, IneligibleReason, PointInTimeViolation, RulesChanged,
)
from app.backtest.strategy_d_analog.source import load_daily_history
from tests.strategy_d import fixtures

PACKAGE = Path(__file__).resolve().parents[2] / "app/backtest/strategy_d_analog"
FORBIDDEN_PREFIXES = (
    "app.strategy", "app.strategy_b", "app.services", "app.risk", "app.broker", "app.execution",
    "app.integrations.kiwoom", "app.backtest.portfolio", "app.backtest.replay",
    "app.backtest.baseline", "app.backtest.experiments", "app.backtest.strategy_b",
    "app.backtest.engine.adapter", "app.backtest.engine.driver", "app.backtest.engine.clock",
    "app.backtest.strategy_c_selection.features", "app.backtest.strategy_c_selection.rules",
    "app.backtest.strategy_c_selection.evaluate", "app.backtest.strategy_c_selection.run",
    "app.backtest.strategy_c_selection.pit_audit", "app.backtest.strategy_c_v2",
)
#: Modules that must not be in ``sys.modules`` after the D package is imported (Pre-flight §7.2).
FORBIDDEN_LOADED = ("app.strategy", "app.strategy_b", "app.services", "app.risk", "app.broker",
                    "app.backtest.engine.driver", "app.backtest.engine.identity")


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("workspace") / "1_US-B"
    fixtures.build(root)
    return root


def _imported_names(path: Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
    return out


def _history(root: Path, rules, **kwargs):
    return load_daily_history(root, fixtures.SNAPSHOT_ID, allowed_exchanges=rules.allowed_exchanges,
                              **kwargs)


# --- rules and identity -------------------------------------------------------------------

def test_declared_checksum_is_the_one_in_the_file(rules):
    assert rules.checksum == DECLARED_RULES_CHECKSUM
    assert len(rules.tests) == 14
    assert rules.eval_range(501) == (260, 480)


def test_an_edited_rule_file_is_refused(tmp_path, rules):
    edited = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    edited["top_k"] = 25
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(RulesChanged):
        load_rules(path)
    assert load_rules(path, require_declared=False).top_k == 25


def test_checksum_recipe_equals_the_c_implementation():
    """D reimplements the recipe to stay off C's module; the two must never disagree (D1 decision)."""
    from app.backtest.strategy_c_selection.rules import canonical_checksum as c_recipe

    payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    assert canonical_checksum(payload) == c_recipe(payload) == DECLARED_RULES_CHECKSUM


def test_query_hash_is_the_declared_string(rules):
    import hashlib

    assert query_order_key("2025-03-04", "AAPL") == hashlib.sha256(
        b"Q|20260917|2025-03-04|AAPL").hexdigest()
    assert n1_order_key(0, "2025-03-04", "AAPL", "2024-11-12", "MSFT") == hashlib.sha256(
        b"N1|20260917|0|2025-03-04|AAPL|2024-11-12|MSFT").hexdigest()


def test_query_sample_is_hash_ordered_and_stable(rules):
    names = [f"T{i:03d}" for i in range(500)]
    first = query_sample("2025-03-04", names, rules.queries_per_date)
    assert len(first) == rules.queries_per_date
    assert first == query_sample("2025-03-04", list(reversed(names)), rules.queries_per_date)
    assert first != query_sample("2025-03-05", names, rules.queries_per_date)
    assert list(first) == sorted(first, key=lambda t: query_order_key("2025-03-04", t))


def test_identity_excludes_the_clock_and_changes_with_the_dataset(dataset, rules):
    history = _history(dataset, rules)
    one = run_identity(phase="D1", rules_checksum=rules.checksum, freeze=history.freeze)
    two = run_identity(phase="D1", rules_checksum=rules.checksum, freeze=history.freeze)
    assert one.digest == two.digest and one.run_id.startswith("dpit1-")
    moved = run_identity(phase="D1", rules_checksum=rules.checksum,
                         freeze=type(history.freeze)(**{**history.freeze.as_dict(),
                                                        "grid_digest": "0" * 64}))
    assert moved.digest != one.digest
    assert "created_at" not in one.payload and "host" not in one.payload


def test_identity_refuses_a_float_payload(dataset, rules):
    history = _history(dataset, rules)
    with pytest.raises(HardFail):
        run_identity(phase="D1", rules_checksum=rules.checksum, freeze=history.freeze,
                     extra={"threshold": 3.0})


def test_code_digest_follows_the_package(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "a.py").write_text("x = 1\n", encoding="utf-8")
    before = code_digest(package)
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert code_digest(package) != before
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "a.py").write_text("noise\n", encoding="utf-8")
    assert code_digest(package) != before  # unchanged by the cache directory
    assert code_digest(package) == code_digest(package)


# --- import boundary ----------------------------------------------------------------------

def test_d_package_has_no_forbidden_direct_imports():
    for path in sorted(PACKAGE.glob("*.py")):
        for name in _imported_names(path):
            assert not name.startswith(FORBIDDEN_PREFIXES), (path.name, name)


def test_identity_module_imports_only_stdlib_and_d_models():
    for name in _imported_names(PACKAGE / "identity.py"):
        assert not name.startswith("app.") or name == "app.backtest.strategy_d_analog.models", name


def test_only_source_knows_where_bytes_live():
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in {"source.py", "config.py", "d1.py"}:
            continue
        for name in _imported_names(path):
            assert not name.startswith("app.backtest.historical_store"), (path.name, name)
    for name in ("encoder.py", "similarity.py", "universe.py"):
        path = PACKAGE / name
        if path.exists():
            assert not any(n.endswith(("labels", "signal")) for n in _imported_names(path)), name


def test_importing_the_package_does_not_load_a_trading_module():
    code = ("import importlib, pkgutil, sys;"
            "import app.backtest.strategy_d_analog as p;"
            "[importlib.import_module(m.name) for m in pkgutil.iter_modules(p.__path__, p.__name__ + '.')];"
            f"bad=[m for m in sys.modules if m.startswith({FORBIDDEN_LOADED!r})];"
            "print(sorted(bad))")
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=root, env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]", done.stdout


# --- grid and freeze contracts ------------------------------------------------------------

def test_grid_passes_and_is_bound_to_the_freeze(dataset, rules):
    history = _history(dataset, rules)
    grid = history.grid
    assert len(grid) == 290
    assert all(later > earlier for earlier, later in zip(grid.dates, grid.dates[1:]))
    assert history.freeze.session_count == len(grid)
    assert history.freeze.first_session == grid.session(0).isoformat()
    assert history.freeze.grid_digest == grid.digest
    assert history.freeze.daily_authority == "MASSIVE_GROUPED_DAILY"
    reloaded = _history(dataset, rules, expected=history.freeze)
    assert reloaded.freeze == history.freeze  # D2~D4 admission test on the same dataset


def test_a_different_dataset_is_refused_against_an_expected_identity(dataset, tmp_path, rules):
    other = tmp_path / "other" / "1_US-B"
    fixtures.build(other, day_count=200)
    expected = _history(dataset, rules).freeze
    with pytest.raises(HardFail) as caught:
        _history(other, rules, expected=expected)
    assert caught.value.code == "R2"


@pytest.mark.parametrize("kwargs, code", [
    ({"drop_session": 150}, "R3"),          # G3: an XNYS session missing inside the range
    ({"unusable_index": 150}, "R3"),        # G4: an unusable session inside the grid
    ({"break_session_label": None}, "R3"),  # G5: the file's own session label disagrees
    ({"corrupt_sha_on": None}, "R2"),       # G6: file bytes do not match the freeze row
])
def test_a_broken_grid_is_a_hard_fail(tmp_path, rules, kwargs, code):
    target = fixtures.sessions(290)[150]
    if "break_session_label" in kwargs:
        kwargs["break_session_label"] = target
    if "corrupt_sha_on" in kwargs:
        kwargs["corrupt_sha_on"] = target
    root = tmp_path / "broken" / "1_US-B"
    fixtures.build(root, **kwargs)
    with pytest.raises(HardFail) as caught:
        _history(root, rules)
    assert caught.value.code == code


def test_an_unusable_session_before_the_grid_is_normal(tmp_path, rules):
    root = tmp_path / "leading" / "1_US-B"
    fixtures.build(root, unusable_index=0)
    history = _history(root, rules)
    assert len(history.grid) == 289
    assert history.grid.session(0) == fixtures.sessions(290)[1]


def test_a_repeated_ticker_row_stops_the_run(tmp_path, rules):
    session = fixtures.sessions(290)[100]
    root = tmp_path / "dup" / "1_US-B"
    fixtures.build(root, duplicate_on=session)
    with pytest.raises(HardFail) as caught:
        _history(root, rules)
    assert caught.value.code == "F3"
    measured = _history(root, rules, duplicate_rows_allowed=True)
    assert measured.checks["grouped"]["duplicate_ticker_rows"] == 1
    assert measured.checks["grouped"]["duplicate_examples"][0][0] == session.isoformat()


def test_an_unfrozen_snapshot_is_refused(dataset, rules, tmp_path):
    root = tmp_path / "thawed" / "1_US-B"
    fixtures.build(root)
    path = (root / "market_data/metadata/historical_snapshot" / fixtures.SNAPSHOT_ID / "snapshot.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "BUILDING"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HardFail) as caught:
        _history(root, rules)
    assert caught.value.code == "R2"


# --- universe rule ------------------------------------------------------------------------

def test_every_exclusion_reason_has_a_witness(dataset, rules):
    history = _history(dataset, rules)
    index = 200
    result = universe.evaluate(history.panel_view(index), history.membership(index), rules)
    names = history.tickers
    reason = {t: result.reason[i] for i, t in enumerate(names)}
    order = {r: i for i, r in enumerate(universe.REASON_ORDER)}
    assert reason["LOWPRICE"] == order[IneligibleReason.LOW_PRICE]
    assert reason["THIN"] == order[IneligibleReason.LOW_ADV]
    assert reason["CAJUMP"] in (order[IneligibleReason.CA_SUSPECT], order[IneligibleReason.NO_HISTORY])
    assert result.count >= len(fixtures.QUIET) - 1
    assert sum(result.reason_counts().values()) == len(names)


def test_a_split_inside_the_window_excludes_the_ticker(dataset, rules):
    history = _history(dataset, rules)
    split_idx = history.grid.index_of(
        [e.execution_date for e in history.panel.splits if e.ticker == "SPLITTER"][0])
    order = {r: i for i, r in enumerate(universe.REASON_ORDER)}
    column = history.tickers.index("SPLITTER")
    inside = universe.evaluate(history.panel_view(split_idx + 5), history.membership(split_idx + 5), rules)
    assert inside.reason[column] == order[IneligibleReason.SPLIT_WINDOW]
    later = split_idx + rules.seasoning_sessions + 1
    after = universe.evaluate(history.panel_view(later), history.membership(later), rules)
    assert after.eligible[column]  # the window has moved past the execution date


def test_a_missing_bar_excludes_only_the_windows_that_span_it(dataset, rules):
    history = _history(dataset, rules)
    column = history.tickers.index("GAPPY")
    gap = int(np.argwhere(~np.isfinite(history.panel.close[:, column]))[0][0])
    order = {r: i for i, r in enumerate(universe.REASON_ORDER)}
    spanning = universe.evaluate(history.panel_view(gap + 10), history.membership(gap + 10), rules)
    assert spanning.reason[column] == order[IneligibleReason.NO_HISTORY]
    clear = gap + rules.seasoning_sessions + 1
    assert universe.evaluate(history.panel_view(clear), history.membership(clear), rules).eligible[column]


def test_a_name_outside_the_snapshot_is_not_a_member(dataset, rules):
    history = _history(dataset, rules)
    column = history.tickers.index("LATECO")
    early = history.grid.index_of(history.snapshot_dates[1]) - 1
    order = {r: i for i, r in enumerate(universe.REASON_ORDER)}
    result = universe.evaluate(history.panel_view(early), history.membership(early), rules)
    assert result.reason[column] == order[IneligibleReason.NOT_MEMBER]


def test_library_stride_is_anchored_at_grid_index_zero(rules):
    indices = universe.library_end_indices(rules, 0, 480)
    assert indices[0] == 60 and indices[-1] == 480
    assert all(i % rules.library_stride == 0 and i >= rules.seasoning_sessions for i in indices)


# --- point in time ------------------------------------------------------------------------

def test_the_as_of_view_cannot_reach_a_later_session(dataset, rules):
    history = _history(dataset, rules)
    view = history.panel_view(100)
    assert view.close.shape[0] == 101
    with pytest.raises(PointInTimeViolation):
        view.row(101)
    with pytest.raises(PointInTimeViolation):
        history.split_factor(history.tickers[0], 120, 100)
    with pytest.raises(HardFail):
        history.panel_view(len(history.grid))


def test_a_future_bar_cannot_change_an_earlier_mask(dataset, rules):
    """D0 PIT #2: mutating every row after D leaves the D-date universe bit identical."""
    history = _history(dataset, rules)
    index = 200
    before = universe.evaluate(history.panel_view(index), history.membership(index), rules)
    history.panel.close[index + 1:] *= 7.5
    history.panel.volume[index + 1:] *= 0.0
    after = universe.evaluate(history.panel_view(index), history.membership(index), rules)
    assert np.array_equal(before.eligible, after.eligible)
    assert np.array_equal(before.reason, after.reason)


def test_a_future_split_cannot_change_an_earlier_mask(dataset, rules, tmp_path):
    """D0 PIT #3: a split executed after D is invisible to the D-date universe."""
    from app.backtest.strategy_c_selection.panel import SplitEvent, with_changes

    history = _history(dataset, rules)
    index = 150
    before = universe.evaluate(history.panel_view(index), history.membership(index), rules)
    planted = with_changes(history.panel, splits=history.panel.splits + (
        SplitEvent("Q00", history.grid.session(index + 1), 1.0, 10.0),))
    history.panel = planted
    history._cache.clear()
    after = universe.evaluate(history.panel_view(index), history.membership(index), rules)
    assert np.array_equal(before.eligible, after.eligible)


def test_a_truncated_dataset_gives_the_same_mask(dataset, rules):
    """D0 PIT #1: re-running on data that ends at D reproduces the D-date mask bit for bit."""
    from app.backtest.strategy_c_selection.panel import truncate, with_changes

    history = _history(dataset, rules)
    index = 200
    full = universe.evaluate(history.panel_view(index), history.membership(index), rules)
    cut = truncate(history.panel, history.grid.session(index))
    history.panel = with_changes(cut)
    history._cache.clear()
    short = universe.evaluate(history.panel_view(index), history.membership(index), rules)
    assert np.array_equal(full.eligible, short.eligible)
    assert np.array_equal(full.reason, short.reason)


# --- the D1 run ---------------------------------------------------------------------------

def test_d1_runs_end_to_end_and_writes_a_complete_run(dataset, rules, tmp_path):
    result = d1.execute(dataset, fixtures.SNAPSHOT_ID, rules=rules, log=lambda _: None)
    assert result.verdict == "PASS"
    report = result.report
    assert report["grid"]["session_count"] == 290
    assert report["universe"]["eval_range"] == [260, 269]
    assert report["universe"]["eligible_ticker_dates"] > 0
    assert report["dataset"]["duplicate_ticker_rows"] == 0
    assert report["figi"]["null_share"] is not None
    assert report["library"]["stride_dates"] > 0
    assert report["cost_estimate"]["estimated_seconds_all_14_tests"] >= 0
    run_dir = d1.write_run(result, {"host": "test"}, runs_dir=tmp_path / "runs")
    assert (run_dir / "COMPLETE.json").exists()
    written = json.loads((run_dir / "run_identity.json").read_text(encoding="utf-8"))
    assert written["identity_digest"] == result.identity.digest
    again = d1.execute(dataset, fixtures.SNAPSHOT_ID, rules=rules, log=lambda _: None)
    assert again.identity.digest == result.identity.digest  # same input, same identity


def test_d1_reports_a_dataset_that_is_too_short(tmp_path, rules):
    root = tmp_path / "short" / "1_US-B"
    fixtures.build(root, day_count=200)
    with pytest.raises(HardFail) as caught:
        d1.execute(root, fixtures.SNAPSHOT_ID, rules=rules, log=lambda _: None)
    assert caught.value.code == "F4"


def test_a_snapshot_row_that_is_not_an_active_common_stock_never_enters_the_panel(dataset, rules):
    """The declaration asks for type=CS, active=true; a dead name or an ETF is not a member."""
    history = _history(dataset, rules)
    assert "DEADCO" not in history.tickers
    assert "SOMEETF" not in history.tickers
    for table in history.figi.values():
        assert "DEADCO" not in table and "SOMEETF" not in table
