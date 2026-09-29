"""Structural safety for the Binance LIVE path. Referenced by name in `app/crypto/live/__init__`.

These are the checks that must not depend on anyone remembering a rule:

* only the LIVE package and its one route module may import the LIVE package,
* no strategy, research or backtest module may reach it at all (AUTO is not allowed near a real
  account while the strategies are still under research),
* withdrawal, transfer, Spot and Margin endpoints are unreachable by construction,
* an unarmed client cannot put a TRADE request on the wire,
* no credential reaches a response, a log line, an exception or the audit file,
* the frontend bundle never carries a key.

They read the repository rather than the behaviour, which is the point: a future edit that
breaks one of these fails here even if every functional test still passes.
"""
from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.live import endpoints
from app.crypto.live.account import AccountReader
from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.credentials import Credentials
from app.crypto.live.mirror import LiveMirror
from app.crypto.live.orders import LiveOrderRouter, OrderRefused
from app.crypto.live.rest import BinanceFuturesClient, TradingDisabled
from tests.crypto.binance_fixtures import FakeBinance, POSITION_RISK_FLAT, make_client, make_config

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
LIVE_PACKAGE = APP / "crypto" / "live"
#: The only module outside the package that may import it: the terminal's LIVE routes.
ALLOWED_IMPORTERS = {APP / "crypto" / "terminal" / "live_routes.py"}
#: Packages that must not be able to reach a real account, directly or indirectly.
FORBIDDEN_TREES = ("strategy", "backtest", "services", "crypto/research", "crypto/paper",
                   "crypto/liquidation_forward", "crypto/derivatives", "dev")

KEY = "test-api-key-0123456789"
SECRET = "test-api-secret-abcdef"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Relative imports inside app/crypto: `from ..live.rest import ...`
            names.append(("." * node.level) + module)
    return names


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def test_only_the_live_routes_module_imports_the_live_package_from_outside_it() -> None:
    offenders = []
    for path in _python_files(APP):
        if LIVE_PACKAGE in path.parents or path in ALLOWED_IMPORTERS:
            continue
        for name in _imports(path):
            if "live" in name.split(".") and ("crypto" in name or name.startswith("..")):
                if "live" in name.replace("..", "").split("."):
                    offenders.append((path.relative_to(BACKEND).as_posix(), name))
    assert offenders == [], f"unexpected importers of the LIVE package: {offenders}"


def test_no_strategy_research_or_backtest_module_mentions_the_live_package() -> None:
    offenders = []
    for tree in FORBIDDEN_TREES:
        root = APP / tree
        if not root.exists():
            continue
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            if "crypto.live" in text or "BinanceLiveAdapter" in text or "from ..live" in text:
                offenders.append(path.relative_to(BACKEND).as_posix())
    assert offenders == [], f"AUTO-side modules referencing the LIVE path: {offenders}"


def test_the_live_package_does_not_import_the_paper_engine() -> None:
    """A LIVE bug must not be able to move a paper balance. The isolation is one-directional
    and enforced here rather than by convention."""
    offenders = []
    for path in _python_files(LIVE_PACKAGE):
        for name in _imports(path):
            if "paper" in name.replace("..", "").split("."):
                offenders.append((path.name, name))
    assert offenders == [], f"LIVE modules importing the paper engine: {offenders}"


def test_no_registry_row_can_withdraw_transfer_or_trade_spot_or_margin() -> None:
    for endpoint in endpoints.ENDPOINTS.values():
        lowered = endpoint.path.lower()
        assert not any(fragment in lowered for fragment in endpoints.DENIED_FRAGMENTS)
        assert lowered.startswith("/fapi/")
        assert "/api/v3/" not in lowered and "/sapi/" not in lowered


def test_the_guard_refuses_a_denied_path_even_if_it_were_added_to_the_registry() -> None:
    endpoints.ENDPOINTS["sneaky"] = endpoints.Endpoint(
        name="sneaky", method="POST", path="/fapi/v1/marginType", security=endpoints.TRADE,
        weight="1", doc="https://developers.binance.com/x")
    try:
        with pytest.raises(endpoints.EndpointForbidden):
            endpoints.resolve("sneaky")
    finally:
        del endpoints.ENDPOINTS["sneaky"]


def test_an_unarmed_client_puts_no_trade_request_on_the_wire() -> None:
    client, fake = make_client(trading_enabled=False)
    for name, params in (("new_order", {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
                                        "quantity": "0.001"}),
                         ("set_leverage", {"symbol": "BTCUSDT", "leverage": 5})):
        with pytest.raises(TradingDisabled):
            client.call(name, params)
    assert fake.calls == []
    # `trade_requests` counts what was *sent*, so the safety report's "0 trading requests"
    # means exactly that; the refusal happens before the counter.
    assert client.telemetry.trade_requests == 0 and client.telemetry.requests == 0


def test_a_read_only_session_touches_no_trading_endpoint_at_all() -> None:
    client, fake = make_client()
    reader = AccountReader(client, make_config())
    reader.snapshot()
    reader.recent_fills(10)
    reader.funding(10)
    trade_paths = {endpoint.path for endpoint in endpoints.ENDPOINTS.values()
                   if endpoint.security == endpoints.TRADE}
    called = {path for _, path, _ in fake.calls}
    assert called.isdisjoint(trade_paths)
    assert client.telemetry.trade_requests == 0


def test_nothing_the_api_would_serialise_carries_a_key_or_a_secret(tmp_path: Path) -> None:
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    client = BinanceFuturesClient(credentials=Credentials(api_key=KEY, api_secret=SECRET),
                                  base_url="https://fapi.binance.com", recv_window_ms=5_000,
                                  transport=fake.transport())
    config = make_config()
    ledger = LiveMirror(path=tmp_path / "live" / "events.jsonl", account_fingerprint=config.fingerprint)
    adapter = BinanceLiveAdapter(config=config, client=client, mirror=ledger)
    with pytest.raises(OrderRefused):
        adapter.place_order("LONG", "OPEN", "0.002")
    serialised = json.dumps(adapter.view(), default=str)
    assert KEY not in serialised and SECRET not in serialised
    assert KEY not in ledger.path.read_text() and SECRET not in ledger.path.read_text()


def test_an_error_message_from_binance_cannot_smuggle_the_key_out() -> None:
    fake = FakeBinance()
    fake.fail("GET", "/fapi/v3/account", 400, -1022, f"bad signature for key {KEY}")
    client = BinanceFuturesClient(credentials=Credentials(api_key=KEY, api_secret=SECRET),
                                  base_url="https://fapi.binance.com", recv_window_ms=5_000,
                                  transport=fake.transport())
    reader = AccountReader(client, make_config())
    snapshot = reader.snapshot()
    assert KEY not in json.dumps(snapshot.view(), default=str)
    assert KEY not in (client.telemetry.last_error or "")


def test_the_frontend_never_holds_a_binance_credential() -> None:
    frontend = BACKEND.parent / "frontend"
    if not frontend.exists():  # backend-only checkout
        pytest.skip("no frontend tree")
    offenders = []
    for pattern in ("*.ts", "*.tsx"):
        for path in frontend.rglob(pattern):
            if "node_modules" in path.parts or ".next" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "BINANCE_API_SECRET" in text or "BINANCE_API_KEY" in text:
                offenders.append(path.name)
    assert offenders == [], f"frontend files naming a Binance credential: {offenders}"


def test_the_live_mirror_cannot_be_pointed_at_the_paper_run(tmp_path: Path) -> None:
    from app.crypto.live.mirror import MirrorPathRefused

    with pytest.raises(MirrorPathRefused):
        LiveMirror(path=tmp_path / "data" / "runtime" / "crypto" / "paper" / "run" / "x.jsonl")
