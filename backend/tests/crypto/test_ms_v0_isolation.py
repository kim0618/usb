"""Read-only and isolated, enforced as properties of the code rather than as a promise.

The collector runs beside a live trading service that holds Binance API keys. "It only reads" is
not worth anything as a comment, so it is checked four ways: the import graph cannot reach a
module that holds credentials, the sources contain no private endpoint or order vocabulary, the
URL guard refuses everything outside three public endpoints, and a subprocess import proves that
loading the package does not pull the trading code in.

The V0 scope bans are checked the same way. This step is a data foundation: no strategy, no
score, no LONG/SHORT decision. A file that started computing one would show up here.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.crypto.market_structure_v0 import contract as CONTRACT
from app.crypto.market_structure_v0 import safety as SAFETY

PACKAGE = Path(__file__).resolve().parents[2] / "app" / "crypto" / "market_structure_v0"
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


class _StripDocstrings(ast.NodeTransformer):
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


def code_text(exclude: set[str] | None = None) -> str:
    """Executable source only: docstrings and comments removed, other literals kept.

    A banned-vocabulary scan over raw text is self-defeating here, because these modules explain
    at length *why* they do not sign requests and *why* a wall candidate carries no spoofing
    verdict. Those sentences are the documentation working. `ast.unparse` of a docstring-stripped
    tree keeps every operational string (a URL, a deny-list fragment, a field name) and drops the
    prose, so the scan reads what the code does rather than what it says about itself.
    """
    chunks = []
    for path in sources():
        if exclude and path.name in exclude:
            continue
        tree = _StripDocstrings().visit(ast.parse(path.read_text(encoding="utf-8")))
        chunks.append(ast.unparse(ast.fix_missing_locations(tree)))
    return "\n".join(chunks)


def source_text() -> str:
    return code_text()


def test_the_package_has_the_files_it_is_supposed_to_have():
    assert {path.name for path in sources()} == {
        "__init__.py", "contract.py", "envelope.py", "safety.py", "book.py", "bands.py",
        "trades.py", "flow.py", "walls.py", "store.py", "collector.py"}


# --- isolation from the trading service --------------------------------------------------------

@pytest.mark.parametrize("banned", ["paper", "live", "terminal", "research"])
def test_no_module_imports_the_trading_or_research_packages(banned):
    for path in sources():
        for module in imported_modules(path):
            assert f"crypto.{banned}" not in module, f"{path.name} imports {module}"
            assert not module.startswith(f"{banned}."), f"{path.name} imports {module}"


def test_no_module_imports_anything_from_the_stock_side_of_the_repo():
    allowed_internal = {"", ".", "..", "app.crypto.market_structure_v0"}
    for path in sources():
        for module in imported_modules(path):
            if module.startswith("app.") and module not in allowed_internal:
                pytest.fail(f"{path.name} imports {module}")


def test_importing_the_package_does_not_load_the_trading_code():
    """An import-graph measurement, not a reading of the import lines."""
    program = (
        "import sys, json;"
        "import app.crypto.market_structure_v0.collector;"
        "print(json.dumps(sorted(name for name in sys.modules if name.startswith('app'))))")
    result = subprocess.run([sys.executable, "-c", program], cwd=BACKEND, capture_output=True,
                            text=True, check=True)
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    # Only the package itself and the two namespace packages above it. Nothing from paper, live,
    # terminal, research or the stock side is pulled in by importing the collector.
    outside = [name for name in loaded
               if not name.startswith("app.crypto.market_structure_v0")
               and name not in {"app", "app.crypto"}]
    assert outside == [], outside
    assert "app.crypto.market_structure_v0.store" in loaded


def environment_keys() -> set[str]:
    """Every literal key this package reads from the environment, read off the AST."""
    keys: set[str] = set()
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) in (
                    "os.environ", "environ"):
                if isinstance(node.slice, ast.Constant):
                    keys.add(str(node.slice.value))
            elif isinstance(node, ast.Call):
                target = ast.unparse(node.func)
                if target in ("os.environ.get", "environ.get", "os.getenv", "getenv"):
                    if node.args and isinstance(node.args[0], ast.Constant):
                        keys.add(str(node.args[0].value))
    return keys


def test_the_only_environment_variable_read_is_the_output_root():
    """A credential cannot be picked up from the environment if no other key is ever read."""
    assert environment_keys() == {"MS_V0_ROOT"}


def test_no_credential_type_or_secret_file_is_referenced():
    text = code_text()
    for secret in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "API_SECRET", "SECRET_KEY",
                   "LiveConfig", "load_credentials", "secrets.json", ".pem"):
        assert secret not in text, secret


# --- read only ---------------------------------------------------------------------------------

def test_only_three_public_endpoints_are_reachable():
    assert SAFETY.ALLOWED_URLS == {
        "wss://fstream.binance.com/public/ws/btcusdt@depth@100ms",
        "wss://fstream.binance.com/market/ws/btcusdt@aggTrade",
        "https://fapi.binance.com/fapi/v1/depth"}
    for url in SAFETY.ALLOWED_URLS:
        assert SAFETY.assert_public_url(url) == url


@pytest.mark.parametrize("url", [
    "https://fapi.binance.com/fapi/v1/order",
    "https://fapi.binance.com/fapi/v1/leverage",
    "https://fapi.binance.com/fapi/v3/account",
    "https://fapi.binance.com/fapi/v3/positionRisk",
    "https://fapi.binance.com/fapi/v1/listenKey",
    "https://fapi.binance.com/sapi/v1/capital/withdraw/apply",
    "https://api.binance.com/api/v3/order",
    "wss://fstream.binance.com/private/ws?listenKey=abc",
    "wss://fstream.binance.com/public/ws/ethusdt@depth@100ms",
])
def test_every_private_or_mutating_url_is_refused(url):
    with pytest.raises(SAFETY.NotPublicData):
        SAFETY.assert_public_url(url)


def test_the_sources_contain_no_order_or_account_endpoint_path():
    """`safety.py` is excluded because its deny list is *supposed* to name these paths."""
    text = code_text(exclude={"safety.py"})
    for path in ("/fapi/v1/order", "/fapi/v3/account", "/fapi/v1/leverage", "/fapi/v1/listenKey",
                 "/fapi/v3/positionRisk", "/sapi/", "/api/v3/", "positionSide", "marginType"):
        assert path not in text, path
    # And the deny list does name them, so the exclusion above is not a hole.
    assert {"order", "account", "leverage", "listenkey", "/sapi/", "/api/v3/"} <= set(
        SAFETY.DENIED_FRAGMENTS)


def test_the_sources_never_sign_a_request():
    text = source_text().lower()
    for term in ("hmac", "signature", "x-mbx-apikey", "signed"):
        assert term not in text, term


def test_the_only_http_verb_used_is_get():
    text = source_text()
    assert ".get(" in text
    for verb in (".post(", ".put(", ".delete(", ".patch("):
        assert verb not in text, verb


def test_the_safety_view_states_the_mode_for_the_session_record():
    view = SAFETY.safety_view()
    assert view["mode"] == "READ_ONLY_PUBLIC_MARKET_DATA"
    assert view["credentials_read"] is False and view["order_capability"] is False
    assert view["private_streams"] is False
    assert "separate process" in view["trading_service_coupling"]


# --- V0 scope ----------------------------------------------------------------------------------

def test_v0_computes_no_strategy_score_or_direction():
    """The step is a data foundation. A score or a LONG/SHORT decision belongs to a later step."""
    text = source_text()
    for banned in ("LONG", "SHORT", "signal_score", "long_score", "short_score", "entry_price",
                   "take_profit", "stop_loss", "position_size", "arm(", "auto_exit"):
        assert banned not in text, banned


def test_v0_names_no_spoofing_or_absorption_verdict():
    text = source_text().lower()
    for banned in ("spoof", "absorption", "absorb", "iceberg", "manipulat"):
        assert banned not in text, banned


# --- the contract --------------------------------------------------------------------------------

def test_the_frozen_contract_is_present_and_its_recorded_hash_still_agrees():
    identity = CONTRACT.contract_identity()
    assert identity["status"] == "PRESENT"
    assert identity["sha256_agrees"] is True, "the frozen contract changed without a new version"
    assert identity["contract_version"] == "btc-ms.v0.1"
    assert len(identity["contract_sha256"]) == 64


def test_the_contract_constants_match_the_frozen_document():
    # The document wraps at 96 columns, so a phrase can straddle a newline.
    text = " ".join(CONTRACT.contract_path().read_text(encoding="utf-8").split())
    assert "btcusdt@depth@100ms" in text and "/public/ws/" in text
    assert "btcusdt@aggTrade" in text and "/market/ws/" in text
    assert "limit=1000" in text
    assert "depth stale after 2 seconds" in text and "trades after 5 seconds" in text
    assert "64 MiB or 1 hour" in text
    assert "2048 depth frames, 8192 persistence records" in text
    assert "20,000 book levels" in text and "200,000" in text
    assert "NOT spot's lastUpdateId+1" in text
    assert CONTRACT.DEPTH_STALE_MS == 2_000 and CONTRACT.TRADE_STALE_MS == 5_000
    assert CONTRACT.ROTATE_BYTES == 64 << 20 and CONTRACT.ROTATE_SECONDS == 3_600
    assert CONTRACT.DEPTH_QUEUE_MAX == 2_048 and CONTRACT.PERSIST_QUEUE_MAX == 8_192
    assert CONTRACT.MAX_BOOK_LEVELS == 20_000 and CONTRACT.FLOW_MAX_RECORDS == 200_000
    assert [label for label, _ in CONTRACT.BANDS] == ["0.1", "0.25", "0.5", "1"]
    assert CONTRACT.FLOW_WINDOWS_S == (5, 15, 60)
    assert CONTRACT.WALL_MULTIPLE == 3 and CONTRACT.WALL_MIN_NEIGHBOURS == 3
    assert CONTRACT.WALL_NEIGHBOURS_PER_SIDE == 5
