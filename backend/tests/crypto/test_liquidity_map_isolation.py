"""The preview reads, and that is checked as a property of the code rather than promised.

This viewer sits beside a live trading service that holds Binance API keys, and beside a frozen
collector that must keep being the only writer of the journal. So four things are enforced here:
the import graph cannot reach the order path or a credential, the sources contain no venue URL or
order vocabulary, nothing in the package writes to the journal, and the V1 scope bans (no
direction, no score) show up as a vocabulary check the way they do for V0.

The vocabulary scan uses the same `ast.unparse` of a docstring-stripped tree that
`test_ms_v0_isolation.py` uses, and for the same reason: these modules explain at length *why*
they compute no direction and no score, and a scan over raw text would fail on its own
documentation.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "app" / "crypto" / "liquidity_map"
BACKEND = Path(__file__).resolve().parents[2]
COLLECTOR = Path(__file__).resolve().parents[2] / "app" / "crypto" / "market_structure_v0"


def sources() -> list[Path]:
    return sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class _StripProse(ast.NodeTransformer):
    """Removes docstrings and `*_NOTE` string constants from the tree before it is scanned.

    Docstrings go for the reason `test_ms_v0_isolation.py` gives: these modules explain at length
    why they compute no direction and no score, and a raw scan would fail on that explanation.
    `*_NOTE` constants go for the same reason one level down - the operator-facing sentences that
    *declare* the bans are code literals here, not docstrings. `view.py` keeps every such
    sentence in a `_NOTE` constant precisely so this strip is a single mechanical rule, and
    `test_the_stripped_notes_are_the_text_that_declares_the_bans` checks the exclusion is not a
    hole.
    """

    def visit_Assign(self, node):
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if len(targets) == 1 and targets[0].endswith("_NOTE"):
            return ast.Assign(targets=node.targets, value=ast.Constant(value=""))
        return node

    def _strip(self, node):
        self.generic_visit(node)
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            node.body = node.body[1:] or [ast.Pass()]
        return node

    visit_Module = _strip
    visit_ClassDef = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip


def code_text() -> str:
    """Executable source only. Docstrings and `*_NOTE` prose out, every other literal kept."""
    chunks = []
    for path in sources():
        tree = _StripProse().visit(ast.parse(path.read_text(encoding="utf-8")))
        chunks.append(ast.unparse(ast.fix_missing_locations(tree)))
    return "\n".join(chunks)


def test_the_package_has_the_files_it_is_supposed_to_have():
    assert {path.name for path in sources()} == {
        "__init__.py", "__main__.py", "journal.py", "checkpoint.py", "wallrule.py",
        "continuity.py", "wallstate.py", "view.py", "api.py"}


# --- isolation from the trading service --------------------------------------------------------

@pytest.mark.parametrize("banned", ["paper", "live", "terminal", "research", "strategy"])
def test_no_module_imports_the_trading_or_research_packages(banned):
    for path in sources():
        for module in imported_modules(path):
            assert f"crypto.{banned}" not in module, f"{path.name} imports {module}"
            assert not module.startswith(f"{banned}."), f"{path.name} imports {module}"


def test_the_only_internal_dependency_is_the_frozen_collector_package():
    """The viewer depends on the contract. The contract must never depend on the viewer."""
    allowed = {"", ".", "..", "app.crypto.market_structure_v0"}
    for path in sources():
        for module in imported_modules(path):
            if module.startswith("app.") and module not in allowed:
                pytest.fail(f"{path.name} imports {module}")


def test_the_collector_package_does_not_import_the_viewer():
    for path in sorted(COLLECTOR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "liquidity_map" not in text, path.name


def test_importing_the_preview_does_not_load_the_trading_code():
    program = (
        "import sys, json;"
        "import app.crypto.liquidity_map.api;"
        "print(json.dumps(sorted(name for name in sys.modules if name.startswith('app'))))")
    result = subprocess.run([sys.executable, "-c", program], cwd=BACKEND, capture_output=True,
                            text=True, check=True)
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    outside = [name for name in loaded
               if not name.startswith("app.crypto.liquidity_map")
               and not name.startswith("app.crypto.market_structure_v0")
               and name not in {"app", "app.crypto"}]
    assert outside == [], outside


def test_no_credential_type_or_secret_file_is_referenced():
    text = code_text()
    for secret in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "API_SECRET", "SECRET_KEY",
                   "LiveConfig", "load_credentials", "secrets.json", ".pem", "hmac",
                   "signature", "X-MBX-APIKEY"):
        assert secret not in text, secret


def environment_keys() -> set[str]:
    """Every key this package reads from the environment, read off the AST.

    A key can be written as a literal or through a module constant (`os.environ.get(ROOT_ENV)`),
    so module-level string assignments are resolved first. Without that, an indirection would
    make this test pass by finding nothing - which is the failure mode that matters, because the
    point of the test is that no *other* key can be read.
    """
    keys: set[str] = set()
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {target.id: node.value.value
                     for node in tree.body if isinstance(node, ast.Assign)
                     for target in node.targets
                     if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant)
                     and isinstance(node.value.value, str)}

        def resolve(argument):
            if isinstance(argument, ast.Constant):
                return str(argument.value)
            if isinstance(argument, ast.Name) and argument.id in constants:
                return constants[argument.id]
            return f"UNRESOLVED:{ast.unparse(argument)}"

        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) in ("os.environ",
                                                                               "environ"):
                keys.add(resolve(node.slice))
            elif isinstance(node, ast.Call) and ast.unparse(node.func) in (
                    "os.environ.get", "environ.get", "os.getenv", "getenv"):
                if node.args:
                    keys.add(resolve(node.args[0]))
    return keys


def test_the_only_environment_variable_read_is_the_journal_root():
    """A credential cannot be picked up from the environment if no other key is ever read."""
    assert environment_keys() == {"MS_V0_ROOT"}


def test_the_environment_scan_actually_found_the_read_it_is_asserting_about():
    """Guards the test above against passing because it resolved nothing."""
    from app.crypto.liquidity_map.api import ROOT_ENV
    assert ROOT_ENV == "MS_V0_ROOT"
    assert environment_keys(), "the AST scan found no environment read at all"


# --- no venue, no writing ----------------------------------------------------------------------

def test_the_preview_reaches_no_venue_at_all():
    """The collector is the only thing that talks to Binance. This package has no URL."""
    text = code_text()
    for fragment in ("binance.com", "fstream", "fapi", "wss://", "https://", "websockets",
                     "httpx", "aiohttp", "requests."):
        assert fragment not in text, fragment


def test_the_preview_never_opens_a_file_for_writing():
    """A viewer that can write to the journal can corrupt the dataset it is showing."""
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("open", "io.open"):
                modes = [argument for argument in node.args[1:]
                         if isinstance(argument, ast.Constant)]
                keywords = [keyword.value for keyword in node.keywords
                            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)]
                for mode in modes + keywords:
                    assert set(str(mode.value)) <= {"r", "b"}, (path.name, mode.value)
                assert modes or keywords, f"{path.name} calls open() without an explicit mode"


def test_the_preview_calls_nothing_that_mutates_the_filesystem():
    text = code_text()
    for mutator in ("os.replace", "os.remove", "os.unlink", "os.truncate", "shutil.",
                    "mkdir", "write_text", "write_bytes", "os.rename", "touch(",
                    "Store.open", "canonical_line"):
        assert mutator not in text, mutator


# --- V1 scope ----------------------------------------------------------------------------------

def test_the_preview_computes_no_direction_and_no_score():
    text = code_text()
    for banned in ("LONG", "SHORT", "signal_score", "long_score", "short_score", "score",
                   "entry_price", "take_profit", "stop_loss", "position_size", "arm(",
                   "auto_exit", "place_order", "submit"):
        assert banned not in text, banned


def test_the_preview_names_no_spoofing_or_absorption_verdict():
    text = code_text().lower()
    for banned in ("spoof", "absorption", "absorb", "iceberg", "manipulat"):
        assert banned not in text, banned


def test_the_stripped_notes_are_the_text_that_declares_the_bans():
    """The `_NOTE` exclusion is not a hole: these are the sentences stating what is not computed."""
    from app.crypto.liquidity_map import view as V

    assert "score" in V.SCOPE_NOTE and "방향 판정" in V.SCOPE_NOTE
    assert "spoofing" in V.NO_VERDICT_NOTE and "iceberg" in V.NO_VERDICT_NOTE
    assert "mark price" in V.MARK_UNAVAILABLE_NOTE
    assert "방향 판정" in V.IMBALANCE_NOTE
    assert "PARTIAL" in V.WALL_RULE_NOTE
    assert "coverage" in V.COVERAGE_SCOPE_NOTE
    assert "null" in V.OBSERVED_RANGE_NOTE and "0" in V.OBSERVED_RANGE_NOTE
    assert "PARTIAL" in V.LOWER_BOUND_NOTE and "\u2265" in V.LOWER_BOUND_NOTE
    assert "±0.15%" in V.IDENTICAL_BOUNDS_NOTE
    assert "lm-wall.v2" in V.WALL_RULE_V2_NOTE
    assert "generation" in V.RESNAPSHOT_NOTE and "UNKNOWN" in V.RESNAPSHOT_NOTE
    assert "캐시" in V.STATE_FILE_NOTE
    # The continuity note is the sentence that states what a carry does *not* claim, which is
    # exactly the kind of text the scan has to be told about rather than allowed to delete.
    assert "HARD" in V.CONTINUITY_NOTE and "SOFT" in V.CONTINUITY_NOTE
    assert "order_identity_proven" in V.CONTINUITY_NOTE and "false" in V.CONTINUITY_NOTE
    assert "lm-wall.v2" in V.CONTINUITY_NOTE


def test_every_note_constant_reaches_the_payload():
    """A note that is stripped from the scan but never shown would be an exclusion for free."""
    from app.crypto.liquidity_map import view as V

    payload = repr(V.empty_view(root="", now_ms=0, reason="TEST"))
    for note in (V.SCOPE_NOTE, V.COVERAGE_SCOPE_NOTE, V.MARK_UNAVAILABLE_NOTE,
                 V.NO_VERDICT_NOTE, V.WALL_RULE_NOTE, V.IMBALANCE_NOTE,
                 V.OBSERVED_RANGE_NOTE, V.LOWER_BOUND_NOTE, V.IDENTICAL_BOUNDS_NOTE,
                 V.WALL_RULE_V2_NOTE, V.RESNAPSHOT_NOTE, V.STATE_FILE_NOTE,
                 V.CONTINUITY_NOTE):
        assert note in payload, note


def test_the_preview_reuses_the_contract_s_coverage_vocabulary_rather_than_inventing_one():
    from app.crypto.liquidity_map import view as V
    from app.crypto.market_structure_v0.contract import COMPLETE, PARTIAL, UNKNOWN

    assert (COMPLETE, PARTIAL, UNKNOWN) == ("COMPLETE", "PARTIAL", "UNKNOWN")
    # The preview's own vocabulary is about the feed, not about coverage, so the two cannot be
    # confused on screen.
    assert {V.LIVE, V.STALE, V.SYNCING, V.NO_DATA} == {"LIVE", "STALE", "SYNCING", "NO_DATA"}
    assert not {V.LIVE, V.STALE, V.SYNCING, V.NO_DATA} & {COMPLETE, PARTIAL, UNKNOWN}


def test_the_thresholds_are_read_from_the_contract_not_restated():
    from app.crypto.liquidity_map import view as V
    from app.crypto.market_structure_v0 import contract as C

    assert V.quality_view(derived=None, journal_age_ms=None, session_ended=False,
                          trade_stream=None)["depth_stale_ms"] == C.DEPTH_STALE_MS
    assert V.quality_view(derived=None, journal_age_ms=None, session_ended=False,
                          trade_stream=None)["trade_stale_ms"] == C.TRADE_STALE_MS
