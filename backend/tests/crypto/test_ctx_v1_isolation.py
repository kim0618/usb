"""The Production Context Collector cannot reach an order, an account or a trading service.

Proved three ways, none of which trusts a docstring:

1. **Static.** Every import statement in the package, relative ones resolved, is checked against
   a banned prefix list.
2. **Dynamic.** A fresh interpreter imports the collector and the API and reports every module
   that ended up loaded - the transitive graph, not only the direct imports.
3. **Reach.** The executable code contains no URL, no credential vocabulary and no environment
   read other than its own root; the only network reach is the V0 runner's, which passes every URL
   through `assert_public_url`.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import app.crypto.context_collector_v1 as PKG

PACKAGE_DIR = Path(PKG.__file__).resolve().parent
BACKEND = PACKAGE_DIR.parents[2]
PACKAGE = "app.crypto.context_collector_v1"

#: Nothing that trades, holds an account, runs a strategy or serves the terminal.
BANNED_PREFIXES = (
    "app.crypto.paper", "app.crypto.live", "app.crypto.terminal", "app.crypto.c1",
    "app.crypto.research", "app.crypto.derivatives", "app.api", "app.services", "app.strategy",
    "app.strategy_a_mover_live", "app.backtest", "app.main", "app.broker", "app.kiwoom",
    "app.crypto.liquidity_map.api",
)
#: What the package is allowed to read from inside `app`.
ALLOWED_APP_PREFIXES = ("app.crypto.market_structure_v0", "app.crypto.liquidity_map",
                        PACKAGE)


def modules() -> list[Path]:
    return sorted(PACKAGE_DIR.rglob("*.py"))


def dotted(path: Path) -> str:
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    package = dotted(path) if path.name == "__init__.py" else dotted(path).rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def executable_constants(path: Path) -> list[str]:
    """String constants that are not docstrings."""
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


# --------------------------------------------------------------------------- static

@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_no_module_imports_anything_that_can_trade(path):
    for name in imports_of(path):
        assert not name.startswith(BANNED_PREFIXES), f"{path.name} imports {name}"
        if name.startswith("app."):
            assert name.startswith(ALLOWED_APP_PREFIXES), f"{path.name} imports {name}"


# --------------------------------------------------------------------------- dynamic

def loaded_modules(*imports: str) -> set[str]:
    code = ("import json, sys\n" + "".join(f"import {name}\n" for name in imports)
            + "print(json.dumps(sorted(sys.modules)))")
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, check=True,
                            capture_output=True, text=True, timeout=60)
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_the_collector_process_loads_no_trading_module_and_no_web_framework():
    loaded = loaded_modules(f"{PACKAGE}.collector")
    assert not [m for m in loaded if m.startswith(BANNED_PREFIXES)]
    # The collector serves nothing: neither the viewer's app nor any web framework is loaded.
    assert "fastapi" not in loaded and "starlette" not in loaded
    assert not [m for m in loaded if m.startswith("app.") and not m.startswith(
        ALLOWED_APP_PREFIXES + ("app.crypto",)) and m not in ("app",)]


def test_the_api_process_loads_no_trading_module_and_opens_no_venue_client():
    loaded = loaded_modules(f"{PACKAGE}.api")
    assert not [m for m in loaded if m.startswith(BANNED_PREFIXES)]
    assert "websockets" not in loaded
    assert not [m for m in loaded if m.startswith(f"{PACKAGE}.collector")]


# --------------------------------------------------------------------------- reach

def test_the_only_environment_variables_read_are_the_root_and_the_cache():
    keys = set()
    for path in modules():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = ast.unparse(node.func)
                if target in ("os.environ.get", "os.getenv") and node.args:
                    keys.add(ast.unparse(node.args[0]))
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
                keys.add(ast.unparse(node.slice))
    assert keys == {"ROOT_ENV", "STATE_CACHE_ENV"}
    from app.crypto.context_collector_v1 import api, collector, contract
    assert api.ROOT_ENV == collector.ROOT_ENV == "CTX_V1_ROOT"
    assert contract.STATE_CACHE_ENV == "CTX_V1_STATE_CACHE"


#: Module path segments that name a way to change an account or place an order.
MUTATION_SEGMENT = re.compile(
    r"(^|_)(order|orders|account|accounts|leverage|arm|armed|auto|position|positions|"
    r"live|paper|terminal|broker|execution|trade_executor)(_|$)")


def test_no_loaded_module_path_names_an_order_account_leverage_arm_or_auto_path():
    """The transitive graph of both processes, by module name segment."""
    for entry in (f"{PACKAGE}.collector", f"{PACKAGE}.api"):
        loaded = loaded_modules(entry)
        offenders = [m for m in loaded if m.startswith("app.")
                     and any(MUTATION_SEGMENT.search(part) for part in m.split("."))]
        assert offenders == [], (entry, offenders)


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_no_url_and_no_credential_vocabulary_in_executable_code(path):
    for value in executable_constants(path):
        lowered = value.lower()
        assert "http://" not in lowered and "https://" not in lowered and "wss://" not in lowered
        for word in ("apikey", "api_key", "secret", "signature", "x-mbx", "listenkey",
                     "/fapi/v1/order", "/fapi/v2/account"):
            assert word not in lowered, f"{path.name}: {value!r}"


def own_modules() -> list[Path]:
    return [p for p in modules() if "mcv1_vendored" not in p.parts]


@pytest.mark.parametrize("path", own_modules(), ids=lambda p: p.name)
def test_no_direction_or_score_vocabulary_in_the_collectors_own_code(path):
    """Whole words only, split on anything that is not a letter.

    A substring scan is wrong in both directions: it flags "no longer" for LONG and `buy_usdt`
    for BUY, and BUY/SELL are the V0 aggressor sides, which the FLOW layer exists to publish. What
    is banned is a verdict word. The vendored panel code carries its own scan in its own package.
    """
    banned = {"LONG", "SHORT", "BULLISH", "BEARISH", "NEUTRAL", "SCORE", "SIGNAL", "ENTRY",
              "PROBABILITY", "RECOMMEND", "RECOMMENDATION"}
    for value in executable_constants(path):
        tokens = {token.upper() for token in re.findall(r"[A-Za-z]+", value)}
        assert not tokens & banned, f"{path.name}: {value!r}"
    names = {node.id for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Name)}
    assert not {n for n in names if any(w.lower() in n.lower() for w in ("score", "signal"))}


def test_the_runner_reaches_the_network_only_through_the_v0_public_allow_list():
    from app.crypto.market_structure_v0 import safety
    from app.crypto.context_collector_v1.collector import ContextRunner
    from app.crypto.market_structure_v0.collector import Runner
    overridden = {name for name in vars(ContextRunner) if not name.startswith("__")}
    # The readers, the snapshot fetch and the socket factory are the parent's, unchanged.
    assert not overridden & {"_depth_reader", "_trade_reader", "_fetch_snapshot",
                             "_request_snapshot", "_book_worker"}
    assert safety.ALLOWED_URLS == frozenset({
        "wss://fstream.binance.com/public/ws/btcusdt@depth@100ms",
        "wss://fstream.binance.com/market/ws/btcusdt@aggTrade",
        "https://fapi.binance.com/fapi/v1/depth"})
    assert issubclass(ContextRunner, Runner)
