"""Manual UX Final V1: PAPER/LIVE isolation, re-entry, reduce/flip, leverage, arm, executions.

No network, no Binance key: the PAPER app is fed by hand like `test_paper_sizing_api`, and the
LIVE execution classifier is exercised on mirror lines written here.
"""
from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.live.arm import CONFIRMATION, ArmSession
from app.crypto.paper.config import PAPER_LEVERAGE_CEILING_FLOOR
from app.crypto.paper.engine import OrderRejected, PaperEngine
from app.crypto.paper.instrument import RiskTierTable
from app.crypto.terminal import api as terminal_api
from app.crypto.terminal import server as terminal_server
from app.crypto.terminal.executions import live_executions, live_executions_from_file
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD, make_config, quote
from tests.crypto.test_paper_terminal_api import snapshot_message, ticker_snapshot

D = Decimal


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    config_path = tmp_path / "run_config.json"
    # 10,000,000 KRW at 1,000 KRW/USDT: a 10,000 USDT PAPER account.
    config_path.write_text(json.dumps({
        "run_id": "ux-final", "starting_capital_krw": "10000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-23T00:00:00Z", "fee_version": "test-v1",
        "fee_taker_rate": "0.0006", "fee_maker_rate": "0.0002", "fee_source": "test",
        "fee_effective_date": "2026-01-01", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk)}))
    monkeypatch.setenv(terminal_api.CONFIG_ENV, str(config_path))
    monkeypatch.setenv("CRYPTO_PAPER_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "seed_klines", lambda self, **kw: None)
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(terminal_api.Runtime, "start", lambda self: None)
    with TestClient(terminal_server.app) as test_client:
        terminal_api.runtime.feed.handle(snapshot_message(), 1_001)
        terminal_api.runtime.feed.handle(ticker_snapshot(), 1_001)
        terminal_api.runtime.session.start(1_000)
        terminal_api.runtime.session.observe(terminal_api.runtime.feed.quote(), force=True)
        yield test_client


def order(client: TestClient, side: str, qty: str, intent: str = "OPEN") -> dict:
    response = client.post("/api/crypto/order", json={"side": side, "intent": intent, "qty": qty})
    assert response.status_code == 200, response.json()
    return response.json()


def executions(client: TestClient) -> list[dict]:
    body = client.get("/api/crypto/executions").json()
    assert body["mode"] == "PAPER" and body["account"] == "ux-final"
    return body["executions"]


def account(client: TestClient) -> dict:
    return client.get("/api/crypto/state").json()["account"]


# ------------------------------------------------------------------ PAPER re-entry and markers

def test_paper_initial_then_two_adds_are_three_entry_executions_with_the_right_average(client) -> None:
    order(client, "LONG", "0.010")
    order(client, "LONG", "0.020")
    order(client, "LONG", "0.010")
    rows = executions(client)
    assert [row["event_type"] for row in rows] == ["INITIAL_ENTRY", "ADD_ENTRY", "ADD_ENTRY"]
    assert [row["qty"] for row in rows] == ["0.010", "0.020", "0.010"]
    assert [row["seq"] for row in rows] == [1, 2, 3]
    assert all(row["side"] == "LONG" and row["mode"] == "PAPER" for row in rows)
    held = account(client)
    assert held["position_side"] == "LONG" and D(held["position_qty"]) == D("0.040")
    # Every fill walked the same 100000.1 ask, so the weighted average is that price.
    assert D(held["avg_entry"]) == D("100000.1")


def test_paper_holding_a_position_does_not_block_a_new_entry_or_the_presets(client) -> None:
    order(client, "LONG", "0.010")
    state = client.get("/api/crypto/state").json()
    assert state["state"]["can_open_new_position"] is True
    sizing = client.get("/api/crypto/sizing").json()
    presets = {row["label"]: row for row in sizing["sides"]["LONG"]["presets"]}
    assert all(presets[label]["feasible"] for label in ("25%", "HALF", "75%", "MAX"))


def test_paper_opposite_order_below_the_held_size_is_a_partial_close(client) -> None:
    order(client, "LONG", "0.030")
    order(client, "SHORT", "0.010")
    held = account(client)
    assert held["position_side"] == "LONG" and D(held["position_qty"]) == D("0.020")
    assert D(held["avg_entry"]) == D("100000.1")  # a reduce never moves the average
    assert [row["event_type"] for row in executions(client)] == ["INITIAL_ENTRY", "PARTIAL_CLOSE"]


def test_paper_opposite_order_equal_to_the_held_size_is_a_full_close(client) -> None:
    order(client, "LONG", "0.030")
    order(client, "SHORT", "0.030")
    assert account(client)["position_side"] is None
    assert [row["event_type"] for row in executions(client)] == ["INITIAL_ENTRY", "FULL_CLOSE"]


def test_paper_opposite_order_past_the_held_size_closes_then_opens_the_remainder(client) -> None:
    order(client, "LONG", "0.030")
    order(client, "SHORT", "0.050")
    held = account(client)
    assert held["position_side"] == "SHORT" and D(held["position_qty"]) == D("0.020")
    rows = executions(client)
    assert [(row["event_type"], row["side"], row["qty"]) for row in rows] == [
        ("INITIAL_ENTRY", "LONG", "0.030"), ("FULL_CLOSE", "LONG", "0.030"),
        ("POSITION_FLIP", "SHORT", "0.020")]


def test_paper_flip_that_cannot_open_the_remainder_sends_nothing(client) -> None:
    order(client, "LONG", "0.010")
    before = len(executions(client))
    # 200 BTC is past the market maximum (150): the rehearsal refuses before either leg is sent.
    response = client.post("/api/crypto/order", json={"side": "SHORT", "intent": "OPEN", "qty": "200.010"})
    assert response.status_code == 409
    assert len(executions(client)) == before
    assert account(client)["position_side"] == "LONG"


def test_paper_full_close_drops_the_average_but_keeps_the_history(client) -> None:
    order(client, "LONG", "0.010")
    order(client, "LONG", "0.010", )
    order(client, "LONG", "0.020", intent="CLOSE")
    held = account(client)
    assert held["position_side"] is None and held["avg_entry"] is None
    assert [row["event_type"] for row in executions(client)] == ["INITIAL_ENTRY", "ADD_ENTRY", "FULL_CLOSE"]


def test_paper_executions_are_stable_across_reads(client) -> None:
    order(client, "LONG", "0.010")
    order(client, "SHORT", "0.010")
    first, second = executions(client), executions(client)
    assert first == second and len({row["seq"] for row in first}) == len(first)


# ------------------------------------------------------------------ PAPER leverage

@pytest.mark.parametrize("leverage", ["5", "10", "20", "50", "100"])
def test_paper_leverage_presets_are_all_accepted_and_margined_as_selected(client, leverage) -> None:
    response = client.post("/api/crypto/leverage", json={"leverage": leverage})
    assert response.status_code == 200, response.json()
    state = response.json()["state"]
    assert D(state["leverage"]) == D(leverage)
    order(client, "LONG", "0.050")
    held = account(client)
    # 0.05 BTC at 100000.1 is 5000.005 USDT of notional; the margin posted is that over the
    # leverage actually selected, not over any other ceiling.
    assert D(held["used_margin"]) == (D("0.050") * D("100000.1") / D(leverage))


def test_paper_ceiling_is_the_runs_own_and_never_below_100x() -> None:
    config = make_config()
    hot_like = dataclasses.replace(config.instrument, max_leverage=D("20"))
    capped = dataclasses.replace(config, instrument=hot_like)
    assert capped.max_leverage == PAPER_LEVERAGE_CEILING_FLOOR == D("100")
    capped.validate_leverage(D("100"))
    with pytest.raises(ValueError):
        capped.validate_leverage(D("101"))
    # A measured ceiling above the floor still applies as measured.
    assert config.max_leverage == max(config.instrument.max_leverage, D("100"))


def test_paper_engine_accepts_100x_on_a_table_whose_tiers_stop_at_20x() -> None:
    config = make_config()
    hot_like = dataclasses.replace(config, instrument=dataclasses.replace(config.instrument,
                                                                          max_leverage=D("20")))
    payload = json.loads(json.dumps(RISK_LIMIT_PAYLOAD))
    for row in payload["result"]["list"]:
        row["maxLeverage"] = "20.00"
    tiers = RiskTierTable.from_payload(payload, source="test", source_sha256="x")
    engine = PaperEngine(hot_like, tiers)
    engine.start(1_000)
    engine.apply_market(quote(1_000))
    engine.set_leverage(ts_ms=1_001, leverage=D("100"))
    engine.submit_order(ts_ms=1_002, side="LONG", qty=D("0.050"), intent="OPEN",
                        request_id="r1", reason="TEST")
    assert engine.account.position.leverage == D("100")
    assert engine.account.used_margin == D("0.050") * D("100000.1") / D("100")
    with pytest.raises(ValueError):
        hot_like.validate_leverage(D("101"))


def test_paper_state_reports_its_own_ceiling(client) -> None:
    state = client.get("/api/crypto/state").json()
    assert D(state["paper_max_leverage"]) >= D("100")


def test_paper_sizing_is_priced_off_the_paper_account_alone(client) -> None:
    # 10,000 USDT of PAPER equity at 10x: MAX is bounded by that account, nothing else.
    sizing = client.get("/api/crypto/sizing").json()
    assert D(sizing["available_balance"]) == D("10000")
    max_qty = D(sizing["sides"]["LONG"]["max_qty"])
    assert D("0.9") < max_qty < D("1.0")  # 10,000 x 10 / 100,000 less fees, on the 0.001 grid
    half = next(row for row in sizing["sides"]["LONG"]["presets"] if row["label"] == "HALF")
    assert D(half["qty"]) == (max_qty * D("0.5")).quantize(D("0.001"), rounding="ROUND_DOWN")


# ------------------------------------------------------------------ LIVE arm

def test_live_arm_has_no_deadline_and_survives_well_past_15_minutes(monkeypatch) -> None:
    class Client:
        trading_enabled = False
    clock = {"now": 1_000_000}
    monkeypatch.setattr(ArmSession, "_now_ms", staticmethod(lambda: clock["now"]))
    client = Client()
    session = ArmSession(client=client)
    session.arm(confirmation=CONFIRMATION)
    clock["now"] += 16 * 60 * 1000
    assert session.armed and client.trading_enabled is True
    clock["now"] += 24 * 3600 * 1000
    view = session.view()
    assert view["armed"] is True and view["expires_at_ms"] is None and view["remaining_s"] is None
    session.disarm()
    assert session.armed is False and client.trading_enabled is False


def test_live_restart_is_a_disarm() -> None:
    class Client:
        trading_enabled = True
    client = Client()
    # A new process builds a new session: it starts disarmed and writes that through.
    session = ArmSession(client=client)
    assert session.armed is False and client.trading_enabled is False
    assert session.view()["last_disarm_reason"] == "BOOT"


def test_live_an_explicit_window_still_expires(monkeypatch) -> None:
    class Client:
        trading_enabled = False
    clock = {"now": 1_000_000}
    monkeypatch.setattr(ArmSession, "_now_ms", staticmethod(lambda: clock["now"]))
    session = ArmSession(client=Client())
    session.arm(confirmation=CONFIRMATION, ttl_s=60)
    clock["now"] += 61_000
    assert session.armed is False and session.last_disarm_reason == "EXPIRED"


# ------------------------------------------------------------------ LIVE executions

def _intent(cid: str, *, symbol: str = "BTCUSDT", intent: str = "OPEN", side: str = "LONG",
            held: str = "0", leverage: str | None = "20", account: str = "acct") -> dict:
    return {"event_type": "LIVE_ORDER_INTENT", "stage": "SUBMIT", "client_order_id": cid,
            "symbol": symbol, "intent": intent, "side": side, "position_qty_at_plan": held,
            "leverage": leverage, "account": account, "ts_ms": 1}


def _result(cid: str, qty: str, price: str, *, at: int, account: str = "acct") -> dict:
    return {"event_type": "LIVE_ORDER_RESULT", "client_order_id": cid, "account": account,
            "response": {"executedQty": qty, "avgPrice": price, "updateTime": at}, "ts_ms": at}


def test_live_executions_classify_entries_adds_and_closes_from_the_plan() -> None:
    lines = [
        _intent("a"), _result("a", "0.010", "84000", at=1_000),
        _intent("b", held="0.010"), _result("b", "0.020", "83500", at=2_000),
        _intent("c", held="0.030"), _result("c", "0.010", "82900", at=3_000),
        _intent("d", intent="CLOSE", held="0.040"), _result("d", "0.015", "85100", at=4_000),
        _intent("e", intent="CLOSE", held="0.025"), _result("e", "0.025", "85200", at=5_000),
    ]
    rows = live_executions(lines, symbol="BTCUSDT", account="acct")
    assert [(row["seq"], row["event_type"], row["qty"], row["price"]) for row in rows] == [
        (1, "INITIAL_ENTRY", "0.010", "84000"), (2, "ADD_ENTRY", "0.020", "83500"),
        (3, "ADD_ENTRY", "0.010", "82900"), (4, "PARTIAL_CLOSE", "0.015", "85100"),
        (5, "FULL_CLOSE", "0.025", "85200")]
    assert all(row["mode"] == "LIVE" and row["leverage"] == "20" for row in rows)


def test_live_executions_keep_other_symbols_accounts_and_unfilled_orders_out() -> None:
    lines = [
        _intent("btc"), _result("btc", "0.010", "84000", at=1_000),
        _intent("arc", symbol="ARCUSDT"), _result("arc", "100", "0.05", at=1_100),
        _intent("other", account="someone-else"), _result("other", "0.010", "84000", at=1_200,
                                                          account="someone-else"),
        _intent("refused"),
        _intent("zero"), _result("zero", "0", "0", at=1_300),
    ]
    rows = live_executions(lines, symbol="BTCUSDT", account="acct")
    assert [row["client_order_id"] for row in rows] == ["btc"]


def test_live_executions_read_from_the_mirror_file(tmp_path) -> None:
    path = tmp_path / "mirror.jsonl"
    noise = {"event_type": "LIVE_SNAPSHOT", "account": "acct", "ts_ms": 1}
    with path.open("w") as handle:
        for line in (noise, _intent("a"), noise, _result("a", "0.010", "84000", at=1_000)):
            handle.write(json.dumps(line) + "\n")
    rows = live_executions_from_file(path, symbol="BTCUSDT", account="acct")
    assert [row["event_type"] for row in rows] == ["INITIAL_ENTRY"]
    assert live_executions_from_file(tmp_path / "missing.jsonl", symbol="BTCUSDT", account="acct") == []


def test_paper_order_engine_still_refuses_a_one_order_reverse() -> None:
    # The flip lives in the order route as two commands; the engine's own policy is unchanged.
    config = make_config()
    engine = PaperEngine(config, RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="t",
                                                            source_sha256="x"))
    engine.start(1_000)
    engine.apply_market(quote(1_000))
    engine.submit_order(ts_ms=1_001, side="LONG", qty=D("0.010"), intent="OPEN",
                        request_id="r1", reason="TEST")
    with pytest.raises(OrderRejected):
        engine._execute(ts_ms=1_002, side="SHORT", qty=D("0.020"), intent="OPEN",
                        request_id="r2", reason="TEST", quote=engine.quote, confirmed=False)
