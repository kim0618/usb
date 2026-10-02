"""Structural guarantees for the signal layer: no order path, no shared ledger, no heavy deps.

These are the assertions that make "C1 cannot trade" a property of the tree rather than a promise
in a docstring. They read the source, so a future edit that wires the signal engine to an order
router fails here before it can reach a screen.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.crypto.c1 import contract as K
from app.crypto.c1.store import DEFAULT_ROOT, C1Store

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
C1_PACKAGE = APP / "crypto" / "c1"
#: Modules the signal engine must not be able to reach: anything that can move money or state on
#: an account, on either venue.
FORBIDDEN = ("live", "orders", "arm", "leverage", "exit_guard", "sizing", "engine_orders")
#: The one module outside the package that may import it.
ALLOWED_IMPORTERS = {APP / "crypto" / "terminal" / "c1_routes.py"}


def _files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(("." * node.level) + (node.module or ""))
    return names


def test_the_signal_package_cannot_reach_an_order_path() -> None:
    offenders = []
    for path in _files(C1_PACKAGE):
        for name in _imports(path):
            parts = [part for part in name.replace("..", ".").split(".") if part]
            if any(part in FORBIDDEN for part in parts):
                offenders.append(f"{path.relative_to(BACKEND)} imports {name}")
    assert offenders == []


def test_the_signal_package_does_not_import_the_paper_engine_or_its_ledger() -> None:
    """The shadow ledger is not a paper account. Sharing the engine would be the first step to
    sharing a balance."""
    offenders = []
    for path in _files(C1_PACKAGE):
        for name in _imports(path):
            if "paper" in name.split("."):
                offenders.append(f"{path.relative_to(BACKEND)} imports {name}")
    assert offenders == []


def test_only_the_c1_routes_module_imports_the_package_from_outside_it() -> None:
    offenders = []
    for path in _files(APP):
        if C1_PACKAGE in path.parents or path in ALLOWED_IMPORTERS:
            continue
        for name in _imports(path):
            if "c1" in name.split(".") and ("crypto" in name or name.startswith("..")):
                offenders.append(f"{path.relative_to(BACKEND)} imports {name}")
    assert offenders == []


def test_the_package_stays_within_the_deployed_snapshot_dependencies() -> None:
    """`app/crypto` is deployed as a self-contained snapshot that carries only the standard
    library plus fastapi, httpx and websockets. numpy or pandas here would break the server
    build, which is why the feature arithmetic is transcribed by hand."""
    offenders = []
    for path in _files(C1_PACKAGE):
        for name in _imports(path):
            root = name.split(".")[0]
            if root in ("numpy", "pandas", "scipy", "sklearn", "pyarrow"):
                offenders.append(f"{path.relative_to(BACKEND)} imports {name}")
    assert offenders == []


def test_the_research_package_is_not_imported_at_runtime() -> None:
    """The server snapshot has no research data, so a runtime import of it would not resolve."""
    offenders = []
    for path in _files(C1_PACKAGE):
        for name in _imports(path):
            if "research" in name.split("."):
                offenders.append(f"{path.relative_to(BACKEND)} imports {name}")
    assert offenders == []


def test_the_shadow_ledger_has_its_own_directory(tmp_path) -> None:
    store = C1Store(tmp_path / "c1")
    assert "c1" in str(DEFAULT_ROOT)
    for path in (store.signals_path, store.shadow_path, store.attribution_path, store.state_path):
        assert "paper" not in path.parts
        assert "live" not in path.parts


def test_the_contract_declares_that_it_trades_nothing() -> None:
    assert K.PLACES_ORDERS is False
    assert K.MUTATES_ACCOUNT is False
