"""The panel reads, and that is checked as a property of the code rather than promised.

This viewer sits beside a live trading service that holds Binance API keys, beside a frozen
collector that must stay the only writer of the journal, and beside a C1 engine whose contract may
not move. So the bans of section 1 of `mc-display.v1` are enforced here the way the V0 collector and
the Liquidity Map viewer enforce theirs: the import graph cannot reach the order path, the sources
contain no private endpoint or order vocabulary, every file opened for writing is the forward
journal, and the scope bans show up as a vocabulary scan.

The vocabulary scan uses the same `ast.unparse` of a prose-stripped tree the other two use, and for
the same reason: these modules explain at length *why* they publish no direction and no score, and a
scan over raw text would fail on its own documentation. `*_NOTE` constants are stripped too, because
the operator-facing sentences that **declare** the bans are code literals here, and a companion test
proves that exclusion is not a hole.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "app" / "crypto" / "market_context_v1"
BACKEND = Path(__file__).resolve().parents[2]


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
    """Docstrings and `*_NOTE` string constants out of the tree before it is scanned."""

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
    chunks = []
    for path in sources():
        tree = _StripProse().visit(ast.parse(path.read_text(encoding="utf-8")))
        chunks.append(ast.unparse(ast.fix_missing_locations(tree)))
    return "\n".join(chunks)


def test_the_package_has_the_files_it_is_supposed_to_have():
    assert {path.name for path in sources()} == {
        "__init__.py", "__main__.py", "contract.py", "venue.py", "structure.py", "liquidity.py",
        "flow.py", "derivatives.py", "auxiliary.py", "journal.py", "assemble.py", "api.py"}


# --- isolation from the order path, AUTO and C1 ------------------------------------------------

@pytest.mark.parametrize("banned", ["paper", "live", "terminal", "strategy", "backtest"])
def test_no_module_imports_the_trading_packages(banned):
    for path in sources():
        for module in imported_modules(path):
            assert f"crypto.{banned}" not in module, f"{path.name} imports {module}"
            assert not module.startswith(f"{banned}."), f"{path.name} imports {module}"


def test_no_module_imports_any_c1_module():
    """Ban B5. The C1 store is read as bytes; importing the engine would make a contract change
    reachable from here, and constructing `C1Store` would create directories."""
    for path in sources():
        for module in imported_modules(path):
            assert "crypto.c1" not in module, f"{path.name} imports {module}"


def test_the_internal_dependencies_are_exactly_the_layers_the_contract_names():
    """A closed list, so a later edit cannot quietly widen what this panel is built on."""
    allowed = {
        "", ".", "..",
        "app.crypto.liquidity_map.api",
        "app.crypto.liquidity_map.view",
        "app.crypto.market_structure_v0.contract",
        "app.crypto.research.market_context_r0.structure",
        "app.crypto.research.price_structure_r0.data",
        "app.crypto.research.price_structure_r1.engine",
    }
    for path in sources():
        for module in imported_modules(path):
            if module.startswith("app.") and module not in allowed:
                pytest.fail(f"{path.name} imports {module}")


def test_nothing_the_panel_reads_imports_the_panel():
    """The layers are contracts. A contract must never depend on its viewer."""
    for package in ("liquidity_map", "market_structure_v0", "research/price_structure_r1",
                    "research/price_structure_r0", "research/market_context_r0", "c1"):
        base = BACKEND / "app" / "crypto" / package
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            assert "market_context_v1" not in path.read_text(encoding="utf-8"), path


def test_importing_the_panel_does_not_load_the_trading_code():
    program = (
        "import sys, json;"
        "import app.crypto.market_context_v1.api;"
        "print(json.dumps(sorted(name for name in sys.modules if name.startswith('app'))))")
    result = subprocess.run([sys.executable, "-c", program], cwd=BACKEND, capture_output=True,
                            text=True, check=True)
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    forbidden = [name for name in loaded
                 if any(part in name for part in ("crypto.paper", "crypto.live",
                                                  "crypto.terminal", "crypto.c1",
                                                  "app.strategy", "app.services"))]
    assert forbidden == [], forbidden


def test_no_credential_type_or_secret_file_is_referenced():
    text = code_text()
    for secret in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "API_SECRET", "SECRET_KEY",
                   "LiveConfig", "load_credentials", "secrets.json", ".pem", "hmac",
                   "X-MBX-APIKEY", "listenKey", "sign_request", "signed=", "recvWindow"):
        assert secret not in text, secret
    # `signature` is not on that list, and the reason is worth recording: the package does have a
    # `change_signature`, which is a journal cadence key and has nothing to do with request
    # signing. A ban on the bare word would have had to be deleted the first time it fired, and a
    # ban that gets deleted protects nothing. The tokens above are the ones only a signed request
    # needs.
    assert "hmac" not in text and "hashlib.new" not in text


def environment_keys() -> set[str]:
    """Every key this package reads from the environment, read off the AST.

    Module constants are resolved first, because a key read through an indirection would make this
    test pass by finding nothing - and the point of the test is that no *other* key can be read.
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


def test_the_environment_keys_are_a_closed_list_with_no_credential_in_it():
    assert environment_keys() == {"MS_V0_ROOT", "MC_V1_JOURNAL_ROOT", "MC_V1_RESEARCH_ROOT",
                                  "MC_V1_ANCHOR_DAYS", "MC_V1_PUBLISH_WINDOW_H",
                                  "CRYPTO_C1_ROOT"}


def test_the_environment_scan_actually_found_the_reads_it_is_asserting_about():
    from app.crypto.market_context_v1 import auxiliary as AUX
    from app.crypto.market_context_v1 import journal as JRN

    assert JRN.ROOT_ENV == "MC_V1_JOURNAL_ROOT"
    assert AUX.C1_ROOT_ENV == "CRYPTO_C1_ROOT"
    assert len(environment_keys()) == 6


# --- writing -----------------------------------------------------------------------------------

def test_the_only_module_that_opens_a_file_for_writing_is_the_forward_journal():
    """Section 1: the panel writes exactly one thing. Every other module reads."""
    writers: set[str] = set()
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("open", "io.open"):
                modes = [argument.value for argument in node.args[1:]
                         if isinstance(argument, ast.Constant)]
                modes += [keyword.value.value for keyword in node.keywords
                          if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)]
                assert modes, f"{path.name} calls open() without an explicit mode"
                for mode in modes:
                    if set(str(mode)) <= {"r", "b"}:
                        continue
                    assert str(mode) == "a", (path.name, mode)
                    writers.add(path.name)
    assert writers == {"journal.py"}, writers


def test_the_journal_only_appends_and_never_replaces_or_truncates():
    text = code_text()
    for mutator in ("os.replace", "os.remove", "os.unlink", "os.truncate", "shutil.",
                    "os.rename", "write_text", "write_bytes", "Store.open", "canonical_line"):
        assert mutator not in text, mutator


def directory_creations() -> list[tuple[str, str]]:
    """Every `.mkdir(...)` call in the package, off the AST, as (file, receiver).

    Counted as calls rather than as occurrences of the word. The earlier version of this test
    counted `"mkdir"` in the raw source of `journal.py`, which was a fine proxy right up until the
    writer lock gave that file three sentences explaining *why* there is only one directory
    creation - at which point the scan would have failed on its own documentation and the honest
    fix would have looked like weakening the test. The AST cannot be confused by prose and is
    strictly the stronger check: it also sees a `mkdir` reached through an alias.
    """
    found: list[tuple[str, str]] = []
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "mkdir":
                found.append((path.name, ast.unparse(node.func.value)))
            elif isinstance(node, ast.Call) and ast.unparse(node.func) in ("os.makedirs",
                                                                           "os.mkdir"):
                found.append((path.name, ast.unparse(node.args[0]) if node.args else "?"))
    return found


def test_no_module_writes_into_the_collector_journal_or_the_c1_store():
    """Both are owned by other processes. Only the panel's own root may be created."""
    from app.crypto.market_context_v1 import journal as JRN

    assert directory_creations() == [("journal.py", "self.root")]
    assert JRN.ROOT_ENV != "MS_V0_ROOT"


def test_the_only_file_the_low_level_write_calls_can_reach_is_the_writer_lock():
    """`open()` is not the only way to write a file, so the scan follows the other way too.

    The writer lock has to use the descriptor-level calls - `flock` needs a real fd, and the
    payload is written at offset 0 - so a scan that only looked at `open()` would have a hole in
    it the width of this feature. Every `os.open` in the package is therefore pinned to the lock
    file, and the modes are pinned too: `O_RDWR | O_CREAT` creates and rewrites in place, and
    `O_TRUNC` is absent because truncating a lock file is how a live holder's payload gets
    destroyed by a process that was about to be refused.
    """
    calls: list[tuple[str, str, str]] = []
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in (
                    "os.open", "os.write", "os.pwrite", "os.lseek", "os.truncate",
                    "os.ftruncate", "os.mknod", "os.symlink", "os.link"):
                calls.append((path.name, ast.unparse(node.func),
                              ast.unparse(node.args[0]) if node.args else "?"))
    assert {name for name, _, _ in calls} == {"journal.py"}, calls
    opens = [(func, target) for _, func, target in calls if func == "os.open"]
    assert opens == [("os.open", "self.path")], opens
    # The flags come off the AST, not out of the text. A raw-text ban on `O_TRUNC` and `O_APPEND`
    # was written here first and failed immediately - on this feature's own docstring, which uses
    # the word `O_APPEND` to explain why two appenders do not tear the file. Same lesson as the
    # `CONSUMED` scan two sections down: a ban belongs on the thing that executes, and a ban that
    # fires on documentation is one that gets deleted rather than obeyed.
    flags = [ast.unparse(node.args[1])
             for path in sources()
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and ast.unparse(node.func) == "os.open"
             and len(node.args) > 1]
    assert flags == ["os.O_RDWR | os.O_CREAT"], flags


def test_the_writer_lock_is_the_only_thing_outside_the_tape_that_the_journal_opens():
    """`self.path` is the lock, `day_file(...)` is the tape, and there is no third path."""
    from app.crypto.market_context_v1 import journal as JRN

    assert JRN.LOCK_FILENAME == ".writer.lock"
    assert JRN.lock_file(Path("/tmp/root")) == Path("/tmp/root/.writer.lock")
    assert JRN.day_file(Path("/tmp/root"), 0).parent == Path("/tmp/root")


# --- scope: no direction, no score, no order, no AUTO ------------------------------------------

def test_the_panel_computes_no_direction_and_no_score():
    text = code_text()
    for banned in ("LONG", "SHORT", "signal_score", "long_score", "short_score", "score",
                   "entry_price", "take_profit", "stop_loss", "position_size", "arm(",
                   "auto_exit", "place_order", "submit", "new_order", "set_leverage",
                   "AUTO_", "autoexit"):
        assert banned not in text, banned


def string_literals() -> list[str]:
    """Every string literal in the prose-stripped tree, minus the `__all__` export names.

    `__all__` holds *names* of things, so `"ABSORPTION_WINDOW"` appearing there says nothing about
    what the panel can render. Everything else is a candidate for the screen.
    """
    out: list[str] = []
    for path in sources():
        tree = _StripProse().visit(ast.parse(path.read_text(encoding="utf-8")))
        exports = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__all__"
                           for target in node.targets)]
        skip = {id(node) for export in exports for node in ast.walk(export)}
        for node in ast.walk(ast.fix_missing_locations(tree)):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
    return out


def test_the_panel_asserts_no_spoofing_or_manipulation_verdict():
    text = code_text().lower()
    for banned in ("spoof", "iceberg", "manipulat"):
        assert banned not in text, banned


def test_no_value_the_panel_can_publish_says_consumed_or_absorption_without_candidate():
    """The shape check that makes section 7 enforceable.

    Scanned over string *literals* rather than over the whole source, which is the difference
    between a test that means something and one that has to be weakened until it passes. A dict
    key spelled `absorption` is structure - it names where the row goes. A literal
    `"CONSUMED"` is a verdict, and that is the thing `btc-ms.v0.1` cannot support: it does not
    prove order identity, so a wall partly filled below the notional floor is indistinguishable
    from one that was cancelled.
    """
    for literal in string_literals():
        for word in ("CONSUMED", "ABSORPTION"):
            if word in literal:
                assert "CANDIDATE" in literal, (word, literal)


def test_the_two_values_that_do_exist_are_the_contracts_own():
    from app.crypto.market_context_v1 import contract as C

    assert C.VANISH_CONSUMED_CANDIDATE == "CONSUMED_CANDIDATE"
    assert C.ABSORPTION_CANDIDATE == "ABSORPTION_CANDIDATE"
    assert "CONSUMED" not in C.VANISH_STATES
    assert set(C.VANISH_STATES) == {"CONSUMED_CANDIDATE", "CANCEL_LIKE", "UNKNOWN"}


def test_the_stripped_notes_are_the_text_that_declares_the_bans():
    """The `_NOTE` exclusion is not a hole: these are the sentences stating what is not computed."""
    from app.crypto.market_context_v1 import contract as C

    assert "LONG/SHORT" in C.SCOPE_NOTE and "score" in C.SCOPE_NOTE
    assert "NEUTRAL" in C.NO_ROLLUP_NOTE and "A_NO_CONTEXT_EDGE" in C.NO_ROLLUP_NOTE
    assert "0" in C.MISSING_VALUE_NOTE and "-" in C.MISSING_VALUE_NOTE
    assert "99.0%" in C.WALL_PRESENCE_NOTE and "bullish" in C.WALL_PRESENCE_NOTE
    assert "CONSUMED" in C.VANISH_NOTE and "3.8%" in C.VANISH_NOTE
    assert "candidate" in C.ABSORPTION_NOTE and "order_identity_proven=false" in C.ABSORPTION_NOTE
    assert "basis = mark - index" in C.DERIVATIVES_NOTE
    assert "auxiliary" in C.AUXILIARY_NOTE and "weak_auxiliary" in C.AUXILIARY_NOTE
    assert "WEAK_DIRECTIONAL_MODEL" in C.DIRECTIONAL_NOTE and "0.502" in C.DIRECTIONAL_NOTE
    assert "27" in C.PREV_SESSION_NOTE and "PROVISIONAL" in C.PREV_SESSION_NOTE
    assert "repaint" in C.STRUCTURE_SERIES_NOTE
    assert "±0.15%" in C.OBSERVED_RANGE_NOTE
    assert "append-only" in C.JOURNAL_NOTE and "Q1~Q3" in C.JOURNAL_NOTE


def test_every_note_constant_reaches_the_payload():
    """A note stripped from the scan but never shown would be an exclusion for free."""
    from app.crypto.market_context_v1 import contract as C

    payload = repr(C.notes_payload())
    for _, note in C.NOTES:
        assert note in payload, note
    assert len(C.NOTES) == len({key for key, _ in C.NOTES})


def test_the_vocabulary_is_the_contracts_own_not_a_second_one():
    from app.crypto.market_context_v1 import contract as C
    from app.crypto.market_structure_v0.contract import COMPLETE, PARTIAL, UNKNOWN

    assert (C.COMPLETE, C.PARTIAL, C.UNKNOWN) == (COMPLETE, PARTIAL, UNKNOWN)
    assert C.LAYER_STATES == ("LIVE", "STALE", "PARTIAL", "UNKNOWN")
    assert "NEUTRAL" not in C.LAYER_STATES
    assert C.UNAVAILABLE not in C.LAYER_STATES
    assert C.MISSING == "-"


def test_the_thresholds_are_read_from_the_contracts_that_own_them():
    from app.crypto.liquidity_map import view as V
    from app.crypto.market_context_v1 import contract as C
    from app.crypto.market_structure_v0 import contract as MSC

    bounds = C.freshness_bounds()
    assert bounds["depth_stale_ms"] == MSC.DEPTH_STALE_MS
    assert bounds["trade_stale_ms"] == MSC.TRADE_STALE_MS
    assert bounds["journal_stale_ms"] == V.JOURNAL_STALE_MS


def test_the_frozen_thetas_are_the_research_modules_own():
    from app.crypto.market_context_v1 import contract as C
    from app.crypto.research.market_context_r0 import structure as R0S

    assert (C.THETA_1H, C.THETA_5M) == (R0S.THETA_1H, R0S.THETA_5M)


def test_the_display_contract_document_still_hashes_to_what_was_recorded():
    from app.crypto.market_context_v1 import contract as C

    identity = C.contract_identity()
    assert identity["contract_version"] == "mc-display.v1"
    assert identity["contract_sha256"] is not None
    assert identity["agrees"] is True, identity


# --- the venue surface -------------------------------------------------------------------------

def test_the_only_host_and_paths_are_public_market_data():
    from app.crypto.market_context_v1 import venue as VN

    assert VN.HOST == "https://fapi.binance.com"
    assert set(VN.ALLOWED_PATHS) == {"/fapi/v1/klines", "/fapi/v1/premiumIndex",
                                     "/fapi/v1/openInterest", "/futures/data/openInterestHist"}
    for path in VN.ALLOWED_PATHS:
        VN.guard(path)


@pytest.mark.parametrize("path", ["/fapi/v1/order", "/fapi/v1/account", "/fapi/v1/leverage",
                                  "/fapi/v1/listenKey", "/fapi/v3/positionRisk"])
def test_the_guard_refuses_every_private_path(path):
    from app.crypto.market_context_v1 import venue as VN

    with pytest.raises(VN.VenueError, match="PATH_NOT_ALLOWED"):
        VN.guard(path)


def test_only_the_venue_module_names_a_url():
    """One module reaches the network, so the surface is one file wide."""
    for path in sources():
        if path.name == "venue.py":
            continue
        tree = _StripProse().visit(ast.parse(path.read_text(encoding="utf-8")))
        text = ast.unparse(ast.fix_missing_locations(tree))
        for fragment in ("https://", "wss://", "binance.com", "fstream", "urllib.request",
                         "httpx", "aiohttp", "websockets"):
            assert fragment not in text, (path.name, fragment)


def test_the_api_exposes_no_route_that_is_not_a_get():
    from app.crypto.market_context_v1.api import app

    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue
        assert methods <= {"GET", "HEAD"}, (getattr(route, "path", route), methods)
