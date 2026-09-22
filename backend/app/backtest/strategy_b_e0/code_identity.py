"""The code an authoritative B-E0 run actually executes, as one digest over the working tree.

The repository carries uncommitted work from several sessions, so ``HEAD`` does not say which
bytes ran. This module answers that from the bytes themselves: a fresh interpreter imports the
run's entry points, every ``app`` module that ends up loaded is a file the run can execute, and
the digest is sha256 over the sorted (path, file sha256) list of those files, plus
``app/strategy_b/config.py``'s defaults through the same file hash.

A fresh interpreter matters. In a long-lived process (pytest, a notebook) ``sys.modules`` holds
whatever else happened to be imported, and the list would depend on history rather than code.

The file list is recorded beside the digest, so a mismatch names the files that moved.
"""

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from app.backtest.engine.identity import code_digest

VERSION = "b-e0-code-identity-v1"

#: Every module an authoritative run imports, including those it imports lazily inside functions
#: (the CLI's inputs(), the differential's A broker), so the closure is the run's, not the parser's.
ENTRY_MODULES = (
    "app.dev.run_strategy_b_e0",
    "app.backtest.strategy_b_e0.run",
    "app.backtest.strategy_b_e0.freeze",
    "app.backtest.strategy_b_e0.execution_differential",
    "app.backtest.strategy_b_e0.session_source",
    "app.backtest.strategy_b_e0.dataset_facts",
    "app.backtest.strategy_b_e0.session_cache",
    "app.backtest.strategy_b_e0.market_inputs",
    "app.backtest.strategy_b_e0.mirror",
    "app.backtest.strategy_b_e0.code_identity",
    "app.broker.sim",
    "app.broker.accounting",
    "app.execution.config",
    "app.execution.costs",
    "app.execution.domain",
    "app.market.domain",
    "app.market.calendar",
    "app.dev.fetch_strategy_c_selection_raw",
    "app.strategy_b.config",
)

_PROBE = """
import importlib, json, sys
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
files = sorted({m.__file__ for n, m in list(sys.modules.items())
                if (n == "app" or n.startswith("app.")) and getattr(m, "__file__", None)})
print(json.dumps(files))
"""


def closure(backend_root: Path, modules: Sequence[str] = ENTRY_MODULES) -> tuple[Path, ...]:
    """The app source files a fresh interpreter loads for ``modules``, sorted."""
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(list(modules))], cwd=backend_root,
        env={"PYTHONPATH": str(backend_root), "PATH": "/usr/bin:/bin",
             "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=300, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"code identity probe failed: {completed.stderr[-2000:]}")
    root = backend_root.resolve()
    files = [Path(item).resolve() for item in json.loads(completed.stdout.strip().splitlines()[-1])]
    outside = [str(f) for f in files if root not in f.parents]
    if outside:
        raise RuntimeError(f"app modules loaded from outside {root}: {outside[:5]}")
    return tuple(sorted(files))


def identity(backend_root: Path) -> dict[str, object]:
    files = closure(backend_root)
    root = backend_root.resolve()
    return {
        "version": VERSION,
        "code_digest": code_digest(files, root=root),
        "file_count": len(files),
        "files": {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
                  for f in files},
    }


def changed_files(expected: Mapping[str, str], actual: Mapping[str, str]) -> list[str]:
    return sorted(name for name in set(expected) | set(actual)
                  if expected.get(name) != actual.get(name))
