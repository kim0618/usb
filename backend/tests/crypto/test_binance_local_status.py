"""PAPER LOCAL BINANCE STATUS V1: `available` for the PAPER account switch, from local state only.

The full `/binance/status` reads the whole Binance account (five signed requests) to answer a
screen that uses one field of it. `/binance/local-status` must give that field the same value in
every case and must not send a request, construct a client, start the user data stream or build a
per-symbol LIVE runtime - a HOT tab on PAPER included. The full route stays exactly as it was.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.crypto import symbols as symbols_module
from app.crypto.symbols import SUPPORTED_SYMBOLS
from app.crypto.terminal import live_routes
from app.crypto.terminal.server import app
from tests.crypto.test_crypto_multisymbol import live_app  # noqa: F401

HOT = "IRYSUSDT"
KEY_ENV = ("BINANCE_API_KEY", "BINANCE_API_SECRET")


@pytest.fixture(autouse=True)
def clean_dynamic(monkeypatch):
    monkeypatch.setattr(symbols_module, "_dynamic", set())


def _unbuilt_runtime(monkeypatch) -> None:
    """A process in which nothing LIVE has been built yet."""
    runtime = live_routes.live_runtime
    for name, value in (("symbols", {}), ("adapter", None), ("config", None), ("client", None),
                        ("mirror", None), ("stream", None), ("error", None)):
        monkeypatch.setattr(runtime, name, value)

    def no_build(self):
        raise AssertionError("local-status must not build the shared LIVE runtime")
    monkeypatch.setattr(live_routes.LiveRuntime, "_build_shared", no_build)


def test_local_status_sends_nothing_to_the_exchange(live_app) -> None:  # noqa: F811
    client, fake, mirror = live_app
    calls, lines = len(fake.calls), mirror.path.read_text().count("\n") if mirror.path.exists() else 0
    for symbol in SUPPORTED_SYMBOLS:
        body = client.get("/api/crypto/binance/local-status", params={"symbol": symbol}).json()
        assert body == {"symbol": symbol, "symbols": list(SUPPORTED_SYMBOLS), "available": True}
    assert len(fake.calls) == calls, fake.calls[calls:]
    after = mirror.path.read_text().count("\n") if mirror.path.exists() else 0
    assert after == lines                                # no RECONCILE line either


def test_the_full_status_still_reads_the_account(live_app) -> None:  # noqa: F811
    client, fake, _ = live_app
    calls = len(fake.calls)
    full = client.get("/api/crypto/binance/status", params={"symbol": "BTCUSDT"}).json()
    local = client.get("/api/crypto/binance/local-status", params={"symbol": "BTCUSDT"}).json()
    assert full["available"] is True and local["available"] is True
    assert full["symbols"] == local["symbols"]
    assert {"blockers", "ready", "gates", "stream", "rest"} <= set(full)   # unchanged contract
    read = [path for _, path, _ in fake.calls[calls:]]
    assert any(path.endswith("/fapi/v3/account") for path in read), read


def test_a_hot_tab_on_paper_builds_no_live_runtime(live_app) -> None:  # noqa: F811
    client, fake, _ = live_app
    symbols_module.admit_dynamic(HOT)
    calls = len(fake.calls)
    built = set(live_routes.live_runtime.symbols)
    body = client.get("/api/crypto/binance/local-status", params={"symbol": HOT}).json()
    assert body["available"] is True and body["symbol"] == HOT and HOT in body["symbols"]
    assert set(live_routes.live_runtime.symbols) == built
    assert len(fake.calls) == calls


def test_no_key_means_not_available_on_both_routes(monkeypatch) -> None:
    for name in KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    build = live_routes.LiveRuntime._build_shared
    _unbuilt_runtime(monkeypatch)
    client = TestClient(app)
    local = client.get("/api/crypto/binance/local-status").json()
    assert local["available"] is False and local["symbol"] == "BTCUSDT"
    # The full route builds as it always has (and finds no key); only the guard is lifted.
    monkeypatch.setattr(live_routes.LiveRuntime, "_build_shared", build)
    full = client.get("/api/crypto/binance/status").json()
    assert full["available"] is False
    assert local["symbols"] == full["symbols"]


def test_a_key_in_the_environment_is_available_without_building_anything(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "test-api-key-0123456789")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-api-secret-abcdef")
    _unbuilt_runtime(monkeypatch)
    body = TestClient(app).get("/api/crypto/binance/local-status").json()
    assert body["available"] is True
    runtime = live_routes.live_runtime
    assert runtime.client is None and runtime.mirror is None and runtime.stream is None
    assert runtime.symbols == {}


def test_half_a_key_is_not_available(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "test-api-key-0123456789")
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    _unbuilt_runtime(monkeypatch)
    assert TestClient(app).get("/api/crypto/binance/local-status").json()["available"] is False


def test_a_symbol_outside_the_list_is_refused_like_the_full_route(live_app) -> None:  # noqa: F811
    client, _fake, _ = live_app
    local = client.get("/api/crypto/binance/local-status", params={"symbol": "NOPEUSDT"})
    full = client.get("/api/crypto/binance/status", params={"symbol": "NOPEUSDT"})
    assert local.status_code == full.status_code == 400
    assert local.json()["error"]["code"] == full.json()["error"]["code"]
