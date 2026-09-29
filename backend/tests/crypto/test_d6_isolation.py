"""Manual/auto isolation, determinism, and the bans D6-B has to keep.

Isolation is enforced here as an import-graph property rather than a convention. The AUTO
strategy code must not be able to see the manual account, ledger, position or reset state, and
the manual engine must not be able to see the research code. Both directions are checked, since
an accidental import either way would let the two share state later.

The no-PnL rule is checked the same way: the package is read as text and searched for the
vocabulary of performance measurement. D6-B is allowed to *receive* a supplied PnL figure for the
daily guard, and that one exception is named explicitly rather than waved through.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.d6 import decision as DEC
from app.crypto.research.d6.contract import load as load_contract
from app.crypto.research.d6.model import HISTORICAL

from tests.crypto.d6_fixtures import clean_market, clean_state, force_last_bar, synthetic_grid, window

D6_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto" / "research" / "d6"
CRYPTO_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto"


@pytest.fixture(scope="module")
def contract():
    return load_contract()


def d6_sources() -> list[Path]:
    return sorted(p for p in D6_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level)
    return names


# --- manual / auto isolation ------------------------------------------------------------------

def test_d6_never_imports_the_paper_account_or_ledger():
    banned = ("paper", "terminal")
    for path in d6_sources():
        for module in imported_modules(path):
            for part in banned:
                assert f"crypto.{part}" not in module and not module.startswith(f"{part}."), \
                    f"{path.name} imports {module}"


def test_d6_source_text_names_no_manual_state_module():
    banned = ("paper.account", "paper.ledger", "paper.state", "paper.engine",
              "paper.persistence", "terminal.session", "terminal.api")
    for path in d6_sources():
        text = path.read_text(encoding="utf-8")
        for name in banned:
            assert f"import {name}" not in text and f"from {name}" not in text, \
                f"{path.name} reaches {name}"


def test_the_paper_engine_does_not_import_the_d6_research_package():
    for path in list((CRYPTO_DIR / "paper").rglob("*.py")) + \
            list((CRYPTO_DIR / "terminal").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "research.d6" not in text and "d6." not in text, path


def string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_d6_only_reads_the_five_contracted_datasets():
    from app.crypto.research.d6 import data as D
    assert set(D.ROOT_DATASETS) == {"kline_1m", "mark_1m", "index_1m", "open_interest_5m",
                                    "funding"}


def test_d6_imports_no_banned_research_line():
    """D5.2's external venues, the liquidation collector and AOA are all out of scope for V1."""
    banned = ("liquidation_forward", "derivatives", "expert_execution", "derivatives_flow",
              "registry_d5_2", "long_horizon")
    for path in d6_sources():
        for module in imported_modules(path):
            for name in banned:
                assert name not in module, f"{path.name} imports {module}"


def test_no_banned_data_source_is_named_in_a_string_literal():
    """Docstrings may say what is excluded; a string the code could open may not name one."""
    banned = ("binance", "okx", "coin-m", "coin_m", "tardis", "d5_2", "liqfwd",
              "liquidation_forward", "expert_execution", "aoa_")
    for path in d6_sources():
        for literal in string_literals(path):
            low = literal.lower()
            if len(low) > 200:
                continue   # module docstrings explain the exclusions in prose
            for name in banned:
                assert name not in low, f"{path.name} has literal {literal!r}"


def test_strategy_state_is_a_plain_value_object():
    """The state the engine reads has to be handed in, not fetched, or isolation is theatre."""
    from app.crypto.research.d6.model import StrategyState
    state = StrategyState()
    assert state.position_open is False
    with pytest.raises(Exception):
        state.position_open = True   # frozen dataclass


# --- no PnL, no backtest ------------------------------------------------------------------------

def test_the_package_computes_no_performance_figure():
    """`day_realized_pnl_pct` is supplied by the caller for the H10 guard and never computed."""
    banned = ("profit_factor", "win_rate", "max_drawdown", "drawdown", "sharpe",
              "forward_return", "future_return", "backtest", "equity_curve", "trade_pnl")
    for path in d6_sources():
        text = path.read_text(encoding="utf-8").lower()
        for word in banned:
            assert word not in text, f"{path.name} mentions {word}"


def code_identifiers(path: Path) -> set[str]:
    """Every name the code actually uses, with comments, docstrings and prose left out.

    Prose is allowed to discuss PnL; executable code is not allowed to handle it beyond the one
    guard field. Separating the two by AST keeps this check precise instead of turning it into an
    ever-growing allowlist of docstring phrasings.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node) for parent in ast.walk(tree)
                  if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                         ast.AsyncFunctionDef))
                  for node in [ast.get_docstring(parent, clean=False)] if node}
    del docstrings   # ast.get_docstring returns text, not nodes; filtered below by Expr position
    doc_nodes = set()
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc_nodes.add(id(body[0].value))

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc_nodes:
            names.add(node.value)
    return names


def test_pnl_appears_in_code_only_as_the_supplied_guard_input():
    """The H10 guard reads one supplied field. No other executable name may mention PnL.

    Docstrings are exempt on purpose: the package has to be able to say that it computes no PnL.
    """
    allowed = {
        "day_realized_pnl_pct",                          # the one supplied guard field
        "UTC-day realized pnl <= -2.0% of capital",      # the contract's H10 threshold literal
        "FDN-V1 offline decision runner (no PnL)",       # CLI help, a statement of absence
    }
    for path in d6_sources():
        for name in code_identifiers(path):
            if "pnl" in name.lower():
                assert name in allowed, f"{path.name} uses {name!r} in code"


def test_the_engine_never_touches_the_clock_or_the_network():
    banned = ("time.time(", "datetime.now(", "datetime.utcnow(", "requests.", "urlopen",
              "websocket", "httpx", "random.")
    for path in d6_sources():
        if path.name == "runner.py":
            continue   # the CLI formats timestamps for display; the engine below it does not
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} uses {token}"


def test_the_runner_only_formats_time_and_never_reads_the_clock():
    text = (D6_DIR / "runner.py").read_text(encoding="utf-8")
    assert "datetime.now(" not in text and "time.time(" not in text
    assert "utcnow" not in text


def test_no_order_object_is_built():
    banned = ("place_order", "submit_order", "PaperEngine", "apply_market", "OrderRequest")
    for path in d6_sources():
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} builds or sends an order via {token}"


# --- determinism ---------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def repeatable_scene():
    grid = synthetic_grid(days=32, oi_slope=-1e-7, vol=9.0e-4)
    force_last_bar(grid, basis=-0.004, drop_1h=-0.02, oi_change_1h=-0.03)
    return grid


def test_the_same_input_produces_byte_identical_output(contract, repeatable_scene):
    win = window(repeatable_scene)
    market, state = clean_market(win), clean_state()
    first = DEC.decide(contract, win, market, state, mode=HISTORICAL).to_json()
    for _ in range(4):
        again = DEC.decide(contract, win, market, state, mode=HISTORICAL).to_json()
        assert again == first


def test_a_fresh_window_object_produces_the_same_bytes(contract, repeatable_scene):
    a = DEC.decide(contract, window(repeatable_scene), clean_market(window(repeatable_scene)),
                   clean_state(), mode=HISTORICAL).to_json()
    b = DEC.decide(contract, window(repeatable_scene), clean_market(window(repeatable_scene)),
                   clean_state(), mode=HISTORICAL).to_json()
    assert a == b


def test_output_is_stable_json(contract, repeatable_scene):
    win = window(repeatable_scene)
    payload = DEC.decide(contract, win, clean_market(win), clean_state(),
                         mode=HISTORICAL).to_json()
    reparsed = json.loads(payload)
    assert json.dumps(reparsed, sort_keys=True, separators=(",", ":")) == payload


def test_dict_key_order_does_not_drift(contract, repeatable_scene):
    win = window(repeatable_scene)
    keys = [list(DEC.decide(contract, win, clean_market(win), clean_state(),
                            mode=HISTORICAL).as_dict()) for _ in range(3)]
    assert keys[0] == keys[1] == keys[2]


# --- PIT at the decision level ------------------------------------------------------------------

def test_pit_appending_future_bars_does_not_change_a_decision(contract):
    """The whole decision, not just one feature: extend the grid and re-decide at the same bar."""
    grid = synthetic_grid(days=33, oi_slope=-1e-7, vol=9.0e-4)
    cut = 32 * 1440 - 1
    short_win = window(grid, end_index=cut)
    market, state = clean_market(short_win), clean_state()
    before = DEC.decide(contract, short_win, market, state, mode=HISTORICAL).to_json()

    tampered = {k: v.copy() for k, v in grid.items()}
    for key in ("close", "index_close", "oi"):
        tampered[key][cut + 1:] *= 3.0
    long_win = window(tampered, end_index=cut)
    after = DEC.decide(contract, long_win, market, state, mode=HISTORICAL).to_json()
    assert before == after


def test_pit_the_decision_window_cannot_contain_future_rows(contract):
    grid = synthetic_grid(days=32)
    win = window(grid, end_index=1000)
    assert len(win.ts_ms) == 1001
    assert win.decision_ts_ms == int(grid["ts"][1000])
    assert win.ts_ms.max() == win.decision_ts_ms


def test_pit_the_market_context_holds_no_future_price(contract):
    """`entry_reference_price` is the decision-time price; `open[t+1]` must not appear here."""
    from app.crypto.research.d6 import data as D
    grid = {"ts": np.arange(5, dtype=np.int64) * 60_000,
            "close": np.array([1.0, 2.0, 3.0, 4.0, 5.0]),
            "open": np.array([9.0, 9.1, 9.2, 9.3, 9.4]),
            "mark_close": np.array([1.0, 2.0, 3.0, 4.0, 5.0]),
            "oi_record_ts": np.full(5, -1, dtype=np.int64),
            "next_funding_ts": np.full(5, -1, dtype=np.int64)}
    market = D.market_at(grid, 2)
    assert market.entry_reference_price == 3.0        # close[t]
    assert market.entry_reference_price not in (9.2, 9.3)
