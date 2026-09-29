"""What D6-C is not allowed to be.

The harness is the one place in D6 that sees both the decision engine and the paper account, so
the boundaries that matter here are different from D6-B's: the strategy package must stay clean,
the production account and run directory must stay untouched, and the banned data lines must stay
out. Determinism and the PIT regression are re-checked because the harness is what would break
them if anything did.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

D6_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto" / "research" / "d6"
D6C_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto" / "research" / "d6c"
CRYPTO_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto"


def sources(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def imported(path: Path) -> set[str]:
    """Module names, plus `module.name` for each `from x import y`.

    A relative `from ..d6 import decision` records its module as just `d6`, so the imported name
    has to be joined back on or the graph loses exactly the edge these tests are about.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes = set()
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc_nodes.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_nodes]


# --- the strategy package stays clean ---------------------------------------------------

def test_d6_still_does_not_import_the_paper_engine():
    """D6-B's isolation must survive D6-C existing."""
    for path in sources(D6_DIR):
        for module in imported(path):
            assert "crypto.paper" not in module and "crypto.terminal" not in module, \
                f"{path.name} imports {module}"
            assert "d6c" not in module, f"{path.name} imports the harness"


def test_the_paper_engine_does_not_import_the_harness():
    for path in list((CRYPTO_DIR / "paper").rglob("*.py")) + \
            list((CRYPTO_DIR / "terminal").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "research.d6" not in text, path


def test_the_harness_reuses_both_engines_rather_than_reimplementing_them():
    modules = set()
    for path in sources(D6C_DIR):
        modules |= imported(path)
    joined = " ".join(modules)
    assert "d6.decision" in joined or "..d6" in joined
    assert "paper.engine" in joined


# --- production safety ---------------------------------------------------------------------

def test_the_harness_never_names_a_production_run_directory():
    banned = ("data/runtime/crypto/paper", "usb_runtime", "/root/", "crypto-api",
              "localhost:8100", "traderj")
    for path in sources(D6C_DIR):
        for literal in literals(path):
            for name in banned:
                assert name not in literal, f"{path.name} has {literal!r}"


def test_the_harness_opens_no_ledger_file():
    banned = ("input.jsonl", "ledger.jsonl", "InputTape", "SegmentedTape", "recover(")
    for path in sources(D6C_DIR):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} touches {token}"


def test_the_harness_does_no_network_or_clock_work():
    banned = ("requests.", "urlopen", "websocket", "httpx", "random.random",
              "datetime.now(", "utcnow")
    for path in sources(D6C_DIR):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} uses {token}"


def test_no_banned_research_line_is_reachable():
    banned = ("liquidation_forward", "derivatives_flow", "expert_execution", "registry_d5_2")
    for path in sources(D6C_DIR):
        for module in imported(path):
            for name in banned:
                assert name not in module, f"{path.name} imports {module}"


def test_only_the_five_contracted_datasets_are_read():
    from app.crypto.research.d6.data import ROOT_DATASETS
    from app.crypto.research.d6c.data import MARK_FIELDS
    assert set(ROOT_DATASETS) == {"kline_1m", "mark_1m", "index_1m", "open_interest_5m", "funding"}
    assert set(MARK_FIELDS) == {"low", "high"}


def test_the_account_is_declared_isolated():
    doc = json.loads((Path(__file__).resolve().parents[3]
                      / "data/research/crypto/d6/d6c_contract_v1.json").read_text())
    isolation = doc["account"]["isolation"]
    assert "in-memory" in isolation
    assert "no production paper account" in isolation


# --- the preregistration forbids what it says it forbids ---------------------------------------

def test_the_prohibition_list_covers_the_prompt_bans():
    doc = json.loads((Path(__file__).resolve().parents[3]
                      / "data/research/crypto/d6/d6c_contract_v1.json").read_text())
    banned = set(doc["prohibitions"])
    for item in ("parameter tuning after results", "best fold selection", "best year selection",
                 "threshold movement", "feature change", "hard filter change",
                 "leverage change", "risk budget change", "fee change", "SHORT addition",
                 "maker rescue scenario", "deployment", "commit", "push"):
        assert item in banned, item


def test_results_are_not_allowed_to_move_a_gate():
    doc = json.loads((Path(__file__).resolve().parents[3]
                      / "data/research/crypto/d6/d6c_contract_v1.json").read_text())
    assert doc["gates"]["source"] == "D6-A contract sec13 verbatim"
    assert doc["gates"]["pass_requires"] == "G1..G10 all PASS"
    assert doc["on_fail"].startswith("FDN-V1 CLOSED")


# --- PIT regression --------------------------------------------------------------------------

def test_the_d6b_pit_suite_still_passes():
    """A cheap guard that the harness did not have to loosen anything upstream."""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/tests/crypto/test_d6_features.py",
         "-q", "-k", "pit"],
        cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-2000:]
    assert "passed" in result.stdout


def test_the_signal_pass_reads_no_future_bar():
    """Truncating the grid at a bar must not change that bar's eligibility."""
    import numpy as np
    from app.crypto.research.d6c import signals as S
    from app.crypto.research.d6c.contract import load
    from tests.crypto.test_d6c_i1 import _tiny_run_grid

    strategy = load().strategy
    grid = _tiny_run_grid()
    full = S.build(grid, strategy)
    cut = len(grid["ts"]) - 500
    short = {k: (v[:cut] if isinstance(v, np.ndarray) and len(v) == len(grid["ts"]) else v)
             for k, v in grid.items()}
    truncated = S.build(short, strategy)
    # The final UTC day is incomplete once truncated, so compare the bars before it.
    compare = cut - 1440
    np.testing.assert_array_equal(full.bar_eligible[:compare], truncated.bar_eligible[:compare])
    np.testing.assert_array_equal(full.long_score[:compare], truncated.long_score[:compare])
