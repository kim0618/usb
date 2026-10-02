"""The C1 HTTP surface: off by default, read-only when on, and one signal series for both screens.

The app under test is the deployed one (`terminal.server:app`), so these assertions are about what
the running terminal actually exposes.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.crypto.c1 import contract as K
from app.crypto.c1.fixture import FixtureRuntime
from app.crypto.c1.models import C1xEvent, ShadowTrade, Signal
from app.crypto.terminal import c1_routes
from app.crypto.terminal.server import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _signal(triggered_at_ms: int, state: str = "ACTIVE") -> Signal:
    return Signal(signal_id=f"C1-LONG-{triggered_at_ms}", triggered_at_ms=triggered_at_ms,
                  signal_bar_ms=triggered_at_ms - 60_000, signal_price=90_000.0,
                  official_entry_at_ms=triggered_at_ms,
                  planned_exit_at_ms=triggered_at_ms + 240 * 60_000, state=state)


def _fixture_runtime(tmp_path, rows) -> FixtureRuntime:
    body = {"signals": [signal.to_json() for signal, _ in rows],
            "shadow": [trade.to_json() for _, trade in rows]}
    path = tmp_path / "fixture.json"
    FixtureRuntime.write_fixture(path, body)
    runtime = FixtureRuntime(tmp_path / "state", path)
    runtime.bootstrap()
    return runtime


@pytest.fixture
def live(tmp_path, monkeypatch) -> FixtureRuntime:
    settled = _signal(1_700_000_000_000, "COMPLETED")
    settled_trade = ShadowTrade(signal_id=settled.signal_id, entry_at_ms=settled.official_entry_at_ms,
                                entry_price=90_000.0, exit_at_ms=settled.planned_exit_at_ms,
                                exit_price=91_000.0, gross_return=0.0111, cost=0.0011,
                                net_return=0.0100, status="SETTLED",
                                observations={"30m_net": 0.001, "480m_net": 0.02})
    active = _signal(1_700_100_000_000, "ACTIVE")
    active_trade = ShadowTrade(signal_id=active.signal_id, entry_at_ms=active.official_entry_at_ms,
                               entry_price=90_500.0, status="ACTIVE")
    runtime = _fixture_runtime(tmp_path, [(settled, settled_trade), (active, active_trade)])
    monkeypatch.setattr(c1_routes, "c1_runtime", runtime)
    monkeypatch.setenv(c1_routes.ENABLE_ENV, "on")
    return runtime


# ----------------------------------------------------------------- default off
def test_the_layer_is_off_unless_the_environment_turns_it_on(client, monkeypatch) -> None:
    """Importing the routes into the deployed app must not change a running terminal."""
    monkeypatch.delenv(c1_routes.ENABLE_ENV, raising=False)
    monkeypatch.setattr(c1_routes, "c1_runtime", None)
    assert c1_routes.enabled() is False
    body = client.get("/api/crypto/c1/state").json()
    assert body["enabled"] is False
    assert body["ready"] is False and body["active"] == []
    assert body["contract"]["sha256"] == K.CONTRACT_SHA256
    assert client.get("/api/crypto/c1/ledger").status_code == 503
    assert client.get("/api/crypto/c1/markers").json() == {"enabled": False, "markers": []}


def test_the_existing_terminal_routes_are_untouched(client) -> None:
    paths = {route.path for route in app.routes}
    for path in ("/api/crypto/state", "/api/crypto/chart", "/api/crypto/chart-history",
                 "/api/crypto/sizing", "/api/crypto/performance", "/api/crypto/candles-15s"):
        assert path in paths
    assert client.get("/health").status_code == 200


# ----------------------------------------------------------------- on
def test_state_reports_the_contract_and_that_it_places_no_orders(client, live) -> None:
    body = client.get("/api/crypto/c1/state").json()
    assert body["enabled"] is True
    assert body["direction_contract"] == "LONG_ONLY" and body["direction"] == "LONG"
    assert body["places_orders"] is False and body["mutates_account"] is False
    assert body["contract"]["research_verdict"] == "WEAK_W1_NOT_SURVIVE"
    assert body["contract"]["research_oos"]["gate"] == "CASE_C_NO_SURVIVE"
    assert body["contract"]["official_horizon_min"] == 240
    assert body["mode"] == "FIXTURE"


def test_active_lists_only_what_is_still_holding(client, live) -> None:
    body = client.get("/api/crypto/c1/state").json()
    assert [row["signal"]["state"] for row in body["active"]] == ["ACTIVE"]
    assert body["signals_total"] == 2
    assert body["active"][0]["signal"]["display_seq"] == 2


def test_markers_are_one_series_with_no_timeframe_parameter(client, live) -> None:
    """A timeframe renders the signal series; it never recomputes it."""
    markers = client.get("/api/crypto/c1/markers").json()["markers"]
    assert len(markers) == 2
    assert {marker["direction"] for marker in markers} == {"LONG"}
    assert markers[0]["triggered_at_ms"] > markers[1]["triggered_at_ms"]
    assert all("timeframe" not in marker for marker in markers)
    assert len({marker["signal_id"] for marker in markers}) == 2
    assert [marker["display_seq"] for marker in markers] == [2, 1]


def test_c1x_uses_its_parent_c1_display_sequence_and_explicit_link(live) -> None:
    latest = max(live.signals.values(), key=lambda row: row.triggered_at_ms)
    live.c1x[latest.signal_id] = C1xEvent(signal_id=latest.signal_id, status="TRIGGERED",
                                          triggered_at_ms=latest.triggered_at_ms + 60_000)
    marker = live.markers()[0]
    assert marker["display_seq"] == 2
    assert marker["c1x"]["display_seq"] == 2
    assert marker["c1x"]["parent_signal_id"] == latest.signal_id
    assert marker["c1x"]["c1x_event_id"] == f"C1X-{latest.signal_id}"


def test_display_sequence_is_stable_after_reload_and_independent_of_file_order(tmp_path) -> None:
    older, newer = _signal(1_700_000_000_000), _signal(1_700_100_000_000)
    rows = [(newer, ShadowTrade(signal_id=newer.signal_id, status="ACTIVE")),
            (older, ShadowTrade(signal_id=older.signal_id, status="ACTIVE"))]
    first = _fixture_runtime(tmp_path / "first", rows)
    second = _fixture_runtime(tmp_path / "second", list(reversed(rows)))
    expected = {older.signal_id: 1, newer.signal_id: 2}
    assert first.display_sequences() == expected
    assert second.display_sequences() == expected


def test_markers_can_be_windowed_for_lazy_history(client, live) -> None:
    """The chart loads older candles in pages and asks for the markers of that page."""
    markers = client.get("/api/crypto/c1/markers",
                         params={"from_ms": 1_700_050_000_000}).json()["markers"]
    assert [marker["triggered_at_ms"] for marker in markers] == [1_700_100_000_000]


def test_the_ledger_is_its_own_and_summarises_only_the_official_horizon(client, live) -> None:
    body = client.get("/api/crypto/c1/ledger").json()
    assert body["ledger"] == "C1_SHADOW"
    assert set(body["isolated_from"]) == {"MANUAL_PAPER", "MANUAL_LIVE", "AUTO"}
    assert body["summary"]["horizon_min"] == 240
    assert body["summary"]["settled"] == 1
    assert body["summary"]["net_mean_bp"] == pytest.approx(100.0)
    rows = {row["signal_id"]: row for row in body["trades"]}
    assert "480m_net" in rows["C1-LONG-1700000000000"]["observations"]


# ----------------------------------------------------------------- attribution
def test_attribution_is_declared_and_never_inferred(client, live) -> None:
    before = client.get("/api/crypto/c1/attribution").json()
    assert before["source"] == "USER_DECLARED" and before["inferred"] == 0
    assert before["attributions"] == []
    assert before["attributable_signal_ids"] == ["C1-LONG-1700100000000"]

    response = client.post("/api/crypto/c1/attribute",
                           json={"signal_id": "C1-LONG-1700100000000", "account": "BINANCE_LIVE",
                                 "trade_ref": "orderId:123", "note": "manual"})
    assert response.status_code == 200
    record = response.json()["attribution"]
    assert record["source"] == "USER_DECLARED" and record["trade_ref"] == "orderId:123"
    after = client.get("/api/crypto/c1/attribution").json()
    assert len(after["attributions"]) == 1


def test_attribution_refuses_an_unknown_signal_or_a_blank_reference(client, live) -> None:
    missing = client.post("/api/crypto/c1/attribute",
                          json={"signal_id": "C1-LONG-1", "account": "PAPER", "trade_ref": "x"})
    assert missing.status_code == 404
    blank = client.post("/api/crypto/c1/attribute",
                        json={"signal_id": "C1-LONG-1700100000000", "account": "PAPER",
                              "trade_ref": "  "})
    assert blank.status_code == 400
    wrong = client.post("/api/crypto/c1/attribute",
                        json={"signal_id": "C1-LONG-1700100000000", "account": "COINBASE",
                              "trade_ref": "x"})
    assert wrong.status_code == 400


def test_only_the_attribution_route_accepts_a_write(client, live) -> None:
    """Everything else about C1 is a GET, and the one POST writes to C1's own file."""
    c1_posts = [route.path for route in app.routes
                if route.path.startswith("/api/crypto/c1") and "POST" in getattr(route, "methods", ())]
    assert c1_posts == ["/api/crypto/c1/attribute"]


def test_a_signal_with_no_entry_price_yet_still_serialises(tmp_path, monkeypatch) -> None:
    """A signal that fired on the newest closed bar has no entry price until the next bar opens.
    Left as a float NaN that field serialises to a bare `NaN` token, which the browser's
    JSON.parse rejects - and the whole marker response, so every signal on the chart, goes with
    it."""
    from app.crypto.c1.models import ShadowTrade as Trade

    fresh = _signal(1_700_200_000_000, "TRIGGERED")
    waiting = Trade(signal_id=fresh.signal_id, entry_at_ms=fresh.official_entry_at_ms,
                    entry_price=float("nan"), status="AWAITING_ENTRY")
    runtime = _fixture_runtime(tmp_path, [(fresh, waiting)])
    monkeypatch.setattr(c1_routes, "c1_runtime", runtime)
    monkeypatch.setenv(c1_routes.ENABLE_ENV, "on")

    response = TestClient(app).get("/api/crypto/c1/markers")
    assert "NaN" not in response.text
    json.loads(response.text)                      # what the browser does
    assert response.json()["markers"][0]["entry_price"] is None


# ------------------------------------------------------------------ C1x diagnostic
def _c1x(signal_id: str, **overrides):
    from app.crypto.c1.models import C1xEvent

    body = {"signal_id": signal_id, "status": "TRIGGERED", "confirmation_count": 2,
            "entry_at_ms": 1_700_000_000_000, "entry_price": 90_000.0,
            "triggered_at_ms": 1_700_004_500_000, "executable_at_ms": 1_700_004_560_000,
            "observed_price": 90_500.0, "hypothetical_exit_price": 90_700.0,
            "holding_minutes": 75, "gross_if_exited": 0.0078, "cost_if_exited": 0.0011,
            "net_if_exited": 0.0067}
    body.update(overrides)
    return C1xEvent(**body)


def _with_c1x(tmp_path, monkeypatch, events):
    settled = _signal(1_700_000_000_000, "COMPLETED")
    trade = ShadowTrade(signal_id=settled.signal_id, entry_at_ms=settled.official_entry_at_ms,
                        entry_price=90_000.0, exit_at_ms=settled.planned_exit_at_ms,
                        exit_price=91_000.0, net_return=0.0100, gross_return=0.0111,
                        cost=0.0011, status="SETTLED")
    runtime = _fixture_runtime(tmp_path, [(settled, trade)])
    runtime.c1x = {event.signal_id: event for event in events}
    monkeypatch.setattr(c1_routes, "c1_runtime", runtime)
    monkeypatch.setenv(c1_routes.ENABLE_ENV, "on")
    return runtime, settled


def test_the_diagnostic_route_says_it_is_not_an_exit(tmp_path, monkeypatch) -> None:
    runtime, settled = _with_c1x(tmp_path, monkeypatch, [_c1x("C1-LONG-1700000000000")])
    body = TestClient(app).get("/api/crypto/c1/c1x").json()
    assert body["id"] == "C1x"
    assert body["is_exit"] is False
    assert body["meaning"] == "PREMIUM_NORMALIZATION_DIAGNOSTIC"
    assert body["total"] == 1
    assert body["events"][0]["is_exit"] is False
    assert body["events"][0]["c1x_event_id"] == "C1X-C1-LONG-1700000000000"


def test_the_diagnostic_summary_refuses_to_conclude_on_a_small_sample(tmp_path, monkeypatch) -> None:
    """E2 needed 1,603 events for an interval that still crossed zero. A handful of forward pairs
    cannot say anything, and the flag is what stops a screen implying otherwise."""
    _with_c1x(tmp_path, monkeypatch, [_c1x("C1-LONG-1700000000000")])
    summary = TestClient(app).get("/api/crypto/c1/c1x").json()["summary"]
    assert summary["forward_sample_sufficient"] is False
    assert summary["c1x_triggered"] == 1
    assert summary["research"]["status"] == "INCONCLUSIVE"
    assert summary["research"]["winner_preservation"] == 0.488
    assert summary["benchmark"] == "E0" and summary["benchmark_horizon_min"] == 240


def test_the_diagnostic_rides_on_the_marker_row_without_becoming_a_signal(tmp_path, monkeypatch) -> None:
    _with_c1x(tmp_path, monkeypatch, [_c1x("C1-LONG-1700000000000")])
    markers = TestClient(app).get("/api/crypto/c1/markers").json()["markers"]
    row = next(item for item in markers if item["signal_id"] == "C1-LONG-1700000000000")
    assert row["direction"] == "LONG"                     # the signal is still the only direction
    assert row["c1x"]["status"] == "TRIGGERED"
    assert row["c1x"]["is_exit"] is False
    # One row per signal: the diagnostic never appears as a second entry in the series.
    assert len(markers) == len({item["signal_id"] for item in markers})


def test_no_route_can_act_on_a_diagnostic(tmp_path, monkeypatch) -> None:
    """The whole point: C1x is observed, never executed. Only one POST exists in this surface and
    it writes an attribution line."""
    _with_c1x(tmp_path, monkeypatch, [_c1x("C1-LONG-1700000000000")])
    writes = [route.path for route in app.routes
              if route.path.startswith("/api/crypto/c1")
              and {"POST", "PUT", "DELETE", "PATCH"} & set(getattr(route, "methods", ()))]
    assert writes == ["/api/crypto/c1/attribute"]
    text = TestClient(app).get("/api/crypto/c1/c1x").text.upper()
    for word in ("CLOSE", "SELL", "REDUCEONLY", "STOP_LOSS"):
        assert word not in text
