"""The manual arm session, and the leverage options route.

Two properties are being defended here and they are the reason the session exists at all:

1. **A restart is a disarm.** The session holds no file, so a fresh process cannot come back
   armed however the last one ended.
2. **Arming takes a deliberate act and expires.** No GET, no page load and no retried request
   can open the gate, and a window nobody closed closes itself.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.arm import (ARMED_BY_ENV, ARMED_BY_SESSION, CONFIRMATION, DISARM_BOOT,
                                 DISARM_EXPIRED, DISARM_MANUAL, ArmRefused, ArmSession)
from app.crypto.live.mirror import LiveEvent, LiveMirror
from app.crypto.live.orders import LIVE_TRADING_DISABLED, LiveOrderRouter
from app.crypto.live.account import AccountReader
from app.crypto.terminal import live_routes
from app.crypto.terminal.server import app
from tests.crypto.binance_fixtures import (FakeBinance, POSITION_RISK_FLAT, make_client,
                                           make_config)

D = Decimal


class _Flag:
    """Stands in for the REST client: the session only ever touches `trading_enabled`."""

    def __init__(self) -> None:
        self.trading_enabled = True  # deliberately wrong at construction, see the first test


# ------------------------------------------------------------------ the session on its own

def test_a_new_session_is_disarmed_and_says_so_on_the_client() -> None:
    """Construction is a disarm. The flag starts wrong on purpose: if `__post_init__` did not
    write through, a client left armed by an earlier construction would stay armed."""
    flag = _Flag()
    session = ArmSession(client=flag)
    assert session.armed is False
    assert flag.trading_enabled is False
    assert session.last_disarm_reason == DISARM_BOOT
    assert session.armed_by is None


def test_arming_requires_the_confirmation_phrase_verbatim() -> None:
    session = ArmSession(client=_Flag())
    for attempt in ("", "yes", "arm live trading", CONFIRMATION + " "):
        with pytest.raises(ArmRefused) as caught:
            session.arm(confirmation=attempt)
        assert caught.value.code == "CONFIRMATION_REQUIRED"
    assert session.armed is False
    assert session.arm_count == 0


def test_arming_opens_the_client_flag_and_reports_the_window() -> None:
    flag = _Flag()
    session = ArmSession(client=flag, ttl_s=900)
    state = session.arm(confirmation=CONFIRMATION, ttl_s=60, note="smoke")

    assert session.armed is True and flag.trading_enabled is True
    assert session.armed_by == ARMED_BY_SESSION
    assert state["remaining_s"] is not None and 0 < state["remaining_s"] <= 60
    assert state["operator_note"] == "smoke"
    assert session.arm_count == 1


def test_the_window_closes_on_its_own_and_the_client_flag_closes_with_it(monkeypatch) -> None:
    """Expiry is observed on read rather than by a timer, so what the gate sees and what the
    panel reports cannot drift apart."""
    flag = _Flag()
    session = ArmSession(client=flag, ttl_s=900)
    session.arm(confirmation=CONFIRMATION, ttl_s=1)
    assert session.armed is True

    now = session._now_ms()
    monkeypatch.setattr(ArmSession, "_now_ms", staticmethod(lambda: now + 2_000))

    assert session.armed is False
    assert flag.trading_enabled is False
    assert session.last_disarm_reason == DISARM_EXPIRED
    assert session.view()["remaining_s"] is None


def test_a_ttl_longer_than_the_session_maximum_is_refused() -> None:
    session = ArmSession(client=_Flag(), ttl_s=900)
    with pytest.raises(ArmRefused) as caught:
        session.arm(confirmation=CONFIRMATION, ttl_s=86_400)
    assert caught.value.code == "TTL_OUT_OF_RANGE"
    assert session.armed is False


def test_manual_disarm_closes_the_flag_immediately() -> None:
    flag = _Flag()
    session = ArmSession(client=flag)
    session.arm(confirmation=CONFIRMATION)
    session.disarm()
    assert session.armed is False and flag.trading_enabled is False
    assert session.last_disarm_reason == DISARM_MANUAL


def test_an_env_armed_process_is_armed_from_boot_and_needs_no_session() -> None:
    """`BINANCE_LIVE_CLIENT_ARMED` keeps its old meaning. The local validation path is not
    changed by any of this."""
    flag = _Flag()
    session = ArmSession(client=flag, env_armed=True)
    assert session.armed is True and flag.trading_enabled is True
    assert session.armed_by == ARMED_BY_ENV
    with pytest.raises(ArmRefused) as caught:
        session.arm(confirmation=CONFIRMATION)
    assert caught.value.code == "ALREADY_ARMED_BY_ENV"


def test_the_session_view_carries_no_credential_and_no_secret() -> None:
    session = ArmSession(client=_Flag())
    session.arm(confirmation=CONFIRMATION, note="tjd618")
    blob = repr(session.view())
    for secret in ("test-api-key-0123456789", "test-api-secret-abcdef"):
        assert secret not in blob


# ------------------------------------------------------------------ the gate below it

def _router(*, env_capability: bool) -> tuple[LiveOrderRouter, ArmSession, FakeBinance]:
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    client, fake = make_client(fake, trading_enabled=False)
    config = make_config(trading_enabled=env_capability, client_armed=False)
    reader = AccountReader(client, config)
    session = ArmSession(client=client)
    return LiveOrderRouter(reader=reader, client=client, config=config), session, fake


def test_arming_alone_cannot_trade_without_the_capability_flag() -> None:
    """The two gates are still two. A session on a deployment whose environment says no is
    armed and still refused, which is the property that makes the session safe to expose."""
    router, session, _ = _router(env_capability=False)
    router.arm = session
    session.arm(confirmation=CONFIRMATION)
    assert session.armed is True
    assert router.armed is False
    assert "BINANCE_LIVE_TRADING_ENABLED=false" in router._locked_message()


def test_capability_alone_cannot_trade_without_an_arm() -> None:
    router, _session, _ = _router(env_capability=True)
    assert router.armed is False
    assert "무장" in router._locked_message()


def test_both_together_open_the_gate_and_the_window_closing_shuts_it(monkeypatch) -> None:
    router, session, _ = _router(env_capability=True)
    router.arm = session
    session.arm(confirmation=CONFIRMATION, ttl_s=60)
    assert router.armed is True

    now = session._now_ms()
    monkeypatch.setattr(ArmSession, "_now_ms", staticmethod(lambda: now + 61_000))
    assert router.armed is False


# ------------------------------------------------------------------ the routes

@pytest.fixture
def live(monkeypatch, tmp_path: Path):
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    client, fake = make_client(fake, trading_enabled=False)
    config = make_config(trading_enabled=True, client_armed=False)
    mirror = LiveMirror(path=tmp_path / "live" / "events.jsonl",
                        account_fingerprint=config.fingerprint)
    adapter = BinanceLiveAdapter(config=config, client=client, mirror=mirror)
    session = ArmSession(client=client)
    monkeypatch.setattr(live_routes.live_runtime, "adapter", adapter)
    monkeypatch.setattr(live_routes.live_runtime, "config", config)
    monkeypatch.setattr(live_routes.live_runtime, "arm", session)
    monkeypatch.setattr(live_routes.live_runtime, "error", None)
    return TestClient(app), adapter, fake, mirror, session


def test_the_arm_route_reports_disarmed_before_anything_is_asked(live) -> None:
    client, *_ = live
    body = client.get("/api/crypto/binance/arm").json()
    assert body["armed"] is False and body["available"] is True
    assert body["confirmation_phrase"] == CONFIRMATION
    assert body["capability"] is True


def test_a_post_without_the_phrase_does_not_arm(live) -> None:
    client, adapter, _fake, mirror, session = live
    response = client.post("/api/crypto/binance/arm", json={"confirmation": "please"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert session.armed is False
    assert adapter.client.trading_enabled is False
    assert any(row["event_type"] == LiveEvent.ARM_REFUSED for row in mirror.recent(50))


def test_a_get_never_arms_however_often_it_is_repeated(live) -> None:
    client, adapter, *_ = live
    for _ in range(5):
        client.get("/api/crypto/binance/arm")
    assert adapter.client.trading_enabled is False


def test_arming_through_the_route_opens_the_gate_and_is_written_to_the_mirror(live) -> None:
    client, adapter, _fake, mirror, session = live
    body = client.post("/api/crypto/binance/arm",
                       json={"confirmation": CONFIRMATION, "ttl_s": 120}).json()

    assert body["armed"] is True and body["armed_by"] == ARMED_BY_SESSION
    assert body["gates"]["armed"] is True
    assert adapter.client.trading_enabled is True
    assert any(row["event_type"] == LiveEvent.ARM for row in mirror.recent(50))

    client.post("/api/crypto/binance/disarm")
    assert session.armed is False and adapter.client.trading_enabled is False
    assert any(row["event_type"] == LiveEvent.DISARM for row in mirror.recent(50))


def test_arming_is_refused_when_the_deployment_has_no_capability(monkeypatch, tmp_path: Path) -> None:
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    rest, fake = make_client(fake, trading_enabled=False)
    config = make_config(trading_enabled=False, client_armed=False)
    mirror = LiveMirror(path=tmp_path / "live" / "events.jsonl",
                        account_fingerprint=config.fingerprint)
    adapter = BinanceLiveAdapter(config=config, client=rest, mirror=mirror)
    monkeypatch.setattr(live_routes.live_runtime, "adapter", adapter)
    monkeypatch.setattr(live_routes.live_runtime, "config", config)
    monkeypatch.setattr(live_routes.live_runtime, "arm", ArmSession(client=rest))
    monkeypatch.setattr(live_routes.live_runtime, "error", None)

    response = TestClient(app).post("/api/crypto/binance/arm",
                                    json={"confirmation": CONFIRMATION})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == LIVE_TRADING_DISABLED
    assert rest.trading_enabled is False


def test_an_order_is_refused_before_arming_and_accepted_after(live) -> None:
    """The whole point, end to end: the same request, the same account, two answers separated
    by one deliberate act."""
    client, adapter, fake, _mirror, _session = live
    body = {"side": "LONG", "intent": "OPEN", "qty": "0.002"}

    refused = client.post("/api/crypto/binance/order", json=body)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == LIVE_TRADING_DISABLED
    assert fake.count("/fapi/v1/order") == 0

    client.post("/api/crypto/binance/arm", json={"confirmation": CONFIRMATION})
    accepted = client.post("/api/crypto/binance/order", json=body)
    assert accepted.status_code == 200
    assert fake.count("/fapi/v1/order") == 1


def test_the_status_route_shows_the_arm_state(live) -> None:
    client, *_ = live
    assert client.get("/api/crypto/binance/status").json()["arm"]["armed"] is False
    client.post("/api/crypto/binance/arm", json={"confirmation": CONFIRMATION})
    assert client.get("/api/crypto/binance/status").json()["arm"]["armed"] is True


# ------------------------------------------------------------------ leverage options

def test_the_leverage_options_come_from_the_accounts_own_brackets(live) -> None:
    client, *_ = live
    body = client.get("/api/crypto/binance/leverage").json()

    assert body["max_leverage"] == 150
    assert body["options"] == [1, 2, 3, 5, 10, 20, 50]
    assert body["authority"] == "binance GET /fapi/v1/leverageBracket"
    assert body["current"] == "10"  # SYMBOL_CONFIG fixture, Decimal -> string
    assert body["margin_type"] == "CROSSED"


def test_a_lower_bracket_ceiling_removes_the_steps_above_it(live, monkeypatch) -> None:
    """The ladder is presentation; the bracket table decides. A hardcoded list would go on
    offering 50x on an account whose tier no longer allows it."""
    client, _adapter, fake, *_ = live
    fake.routes[("GET", "/fapi/v1/leverageBracket")] = [
        {"symbol": "BTCUSDT", "brackets": [
            {"bracket": 1, "initialLeverage": 10, "notionalCap": 50000, "notionalFloor": 0,
             "maintMarginRatio": 0.01, "cum": 0.0}]}]
    body = client.get("/api/crypto/binance/leverage").json()

    assert body["max_leverage"] == 10
    assert 20 not in body["options"] and 50 not in body["options"]
    assert body["options"][-1] == 10


def test_the_current_leverage_is_always_offered_even_off_the_ladder(live, monkeypatch) -> None:
    """7x is not a step on the ladder. An account already on it must still see it selected
    rather than see a row of buttons none of which is the one it is on."""
    import dataclasses

    client, adapter, *_ = live
    snapshot = adapter.snapshot()
    snapshot.symbol_config = dataclasses.replace(snapshot.symbol_config, leverage=D(7))
    monkeypatch.setattr(adapter, "snapshot", lambda **_: snapshot)

    body = client.get("/api/crypto/binance/leverage").json()
    assert 7 in body["options"]
    assert body["current"] == "7"


def test_the_leverage_route_says_margin_mode_is_read_only(live) -> None:
    client, *_ = live
    body = client.get("/api/crypto/binance/leverage").json()
    assert "Binance" in body["margin_type_note"]


def test_reading_leverage_options_sends_no_trade_request(live) -> None:
    client, adapter, fake, *_ = live
    client.get("/api/crypto/binance/leverage")
    assert adapter.client.telemetry.trade_requests == 0
    assert fake.count("/fapi/v1/leverage") == 0
