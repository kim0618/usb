"""The preview API, driven through the HTTP surface a browser actually hits.

Includes the operational cases the step has to survive rather than only the happy path: no root,
an empty root, a stopped collector, a reconnect in the middle of a session, a session with no
trades, and a candidate set that could not be proven.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.crypto.liquidity_map import api as API
from app.crypto.liquidity_map import view as V
from app.crypto.liquidity_map.wallstate import WallFollower

from tests.crypto.liquidity_map_fixtures import Handwriter, written_journal


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Each test gets its own follower cache, so one test's session cannot leak into another."""
    API.state = API.PreviewState()
    monkeypatch.delenv(API.ROOT_ENV, raising=False)
    yield


@pytest.fixture
def client():
    with TestClient(API.app) as session:
        yield session


def point_at(monkeypatch, root):
    monkeypatch.setenv(API.ROOT_ENV, str(root))


# --- availability -----------------------------------------------------------------------------

def test_health_reports_the_root_without_needing_one(client):
    body = client.get("/api/liquidity-map/health").json()
    assert body["mode"] == "READ_ONLY_JOURNAL_VIEWER"
    assert body["root"] is None and body["root_exists"] is False


def test_an_unset_root_is_a_screen_state_not_an_error(client):
    response = client.get("/api/liquidity-map/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["quality"]["state"] == V.NO_DATA
    assert body["quality"]["reasons"] == ["MS_V0_ROOT_NOT_SET"]


def test_a_root_that_does_not_exist_says_so(client, monkeypatch, tmp_path):
    point_at(monkeypatch, tmp_path / "missing")
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["quality"]["reasons"] == ["ROOT_NOT_FOUND"]


def test_a_root_with_no_session_recorded_says_so(client, monkeypatch, tmp_path):
    point_at(monkeypatch, tmp_path)
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["quality"]["reasons"] == ["NO_SESSION_RECORDED"]


def test_a_session_with_no_derived_sample_yet_is_no_data(client, monkeypatch, tmp_path):
    Handwriter(tmp_path).session_start()
    point_at(monkeypatch, tmp_path)
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["quality"]["state"] == V.NO_DATA
    assert body["source"]["session_id"] is not None


# --- the real journal -------------------------------------------------------------------------

def test_a_running_collector_produces_a_live_snapshot(client, monkeypatch, tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=12, end_session=False, seal=False)
    point_at(monkeypatch, root)
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["quality"]["state"] == V.LIVE
    assert body["price"]["mid"] is not None
    assert body["sides"]["ASK"]["nearest_wall"] is not None
    assert body["walls"]["coverage"] == "COMPLETE"
    assert body["overlay"]["renderable"] is True
    # The state checkpoint answered the whole poll, so no stream was walked for it.
    cost = body["source"]["read_cost"]
    assert cost["state_usable"] is True and cost["state_bytes"] > 0
    assert cost["derived_bytes"] == 0 and cost["wall_tail_records"] == 0
    assert cost["journal_walked"] is False
    # The whole poll is the state file plus the one wall row the tail walk had to look at to
    # prove it was already behind the checkpoint. No stream was read for its own sake.
    assert cost["total_bytes"] == cost["wall_bytes"]
    assert cost["wall_bytes"] == cost["state_bytes"] + cost["wall_tail_bytes"]
    assert body["walls"]["source"] == "COLLECTOR_STATE_CHECKPOINT"


def test_a_stopped_collector_is_stale_and_keeps_showing_its_last_sample(client, monkeypatch,
                                                                       tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=3, end_session=True, seal=True)
    point_at(monkeypatch, root)
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["quality"]["state"] == V.STALE
    assert "SESSION_ENDED" in body["quality"]["reasons"]
    # The figures are still there, labelled stale, because "how old" is the operator's question.
    assert body["price"]["best_bid"] is not None
    assert body["overlay"]["renderable"] is False


def test_the_three_wider_bands_are_partial_on_a_real_limit_1000_snapshot(client, monkeypatch,
                                                                        tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=3, end_session=False, seal=False)
    point_at(monkeypatch, root)
    body = client.get("/api/liquidity-map/snapshot").json()
    for side in ("ASK", "BID"):
        coverage = {band["band_pct"]: band["coverage"] for band in body["sides"][side]["depth"]}
        assert coverage["0.1"] == "COMPLETE"
        assert coverage["0.25"] == coverage["0.5"] == coverage["1"] == "PARTIAL"
        lower = [band for band in body["sides"][side]["depth"] if band["is_lower_bound"]]
        assert len(lower) == 3
        assert all(band["qty"] is None and band["observed_qty"] is not None for band in lower)


def test_a_session_with_no_trades_reports_coverage_rather_than_zero_volume(client, monkeypatch,
                                                                          tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=3, trades=0, end_session=False, seal=False)
    point_at(monkeypatch, root)
    body = client.get("/api/liquidity-map/snapshot").json()
    window = body["flow"]["windows"]["60s"]
    # Warmup, so the window is PARTIAL: the totals are a lower bound, not a canonical zero.
    assert window["coverage"] == "PARTIAL"
    assert window["buy_btc"] is None and window["imbalance_btc"] is None
    assert body["flow"]["trade_stream"]["connected"] is True


def test_a_reconnect_ends_the_candidates_and_the_screen_shows_the_resync(client, monkeypatch,
                                                                        tmp_path):
    """A reconnect invalidates the book, closes every candidate as UNKNOWN and resnapshots."""
    from app.crypto.market_structure_v0.collector import Collector
    from app.crypto.market_structure_v0.envelope import Session
    from app.crypto.market_structure_v0.store import Store
    from tests.crypto.liquidity_map_fixtures import wall_snapshot
    import time

    root = tmp_path / "journal"
    session = Session()
    store = Store.open(root, session.session_id, started_ns=session.started_ns)
    collector = Collector(store=store, session=session)
    base_ms, base_ns = int(time.time() * 1000), time.monotonic_ns()
    collector.write_session_record(config={})
    collector.on_depth_connect("d1", receive_ms=base_ms, mono_ns=base_ns)
    collector.on_snapshot(wall_snapshot(), receive_ms=base_ms + 10, mono_ns=base_ns + 10**7,
                          request_ms=base_ms, request_mono_ns=base_ns)
    collector.sample(at_ns=base_ns + 5 * 10**8, at_ms=base_ms + 500)
    opened = collector.wall.counters()["active"]
    assert opened > 0
    # The socket drops. Frames went to nobody, so the book cannot be continued.
    collector.on_depth_disconnect("ConnectionClosed", receive_ms=base_ms + 600,
                                  mono_ns=base_ns + 6 * 10**8)
    collector.sample(at_ns=base_ns + 7 * 10**8, at_ms=base_ms + 700)
    store.tick(base_ns + 3 * 10**9, base_ms + 3_000)
    if store.lock is not None:
        store.lock.release()
        store.lock = None

    point_at(monkeypatch, root)
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["quality"]["state"] == V.SYNCING
    assert "BOOK_UNSYNCED" in body["quality"]["reasons"]
    assert body["walls"]["candidate_count"] == 0
    assert body["sides"]["ASK"]["nearest_wall"] is None
    assert body["overlay"]["renderable"] is False
    events = [item["event"] for item in body["telemetry"]]
    assert "disconnect" in events


def test_an_unproven_candidate_set_withholds_the_nearest_wall(client, monkeypatch, tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    for index in range(200):
        writer.wall(side="ASK", price=str(84_800 + index), event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index)
    writer.stats(active=200, receive_ms=1_900)
    point_at(monkeypatch, tmp_path)
    API.state.followers[str(tmp_path)] = WallFollower(scan_budget_bytes=4_000)
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["walls"]["coverage"] == "PARTIAL"
    assert body["walls"]["missing_count"] > 0
    assert body["sides"]["ASK"]["nearest_wall"] is None
    assert body["overlay"]["walls_suppressed_reason"]


# --- the filter -------------------------------------------------------------------------------

def test_the_filter_defaults_to_the_measured_threshold(client, monkeypatch, tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=3, end_session=False, seal=False)
    point_at(monkeypatch, root)
    body = client.get("/api/liquidity-map/snapshot").json()
    assert body["walls"]["filter"]["min_notional_usdt"] == "500000"
    assert body["walls"]["filter"]["is_display_filter_not_rule"] is True
    # And the rule it sits on top of is the frozen one, agreeing with its own hash.
    assert body["walls"]["rule"]["rule_version"] == "lm-wall.v2"
    assert body["walls"]["rule"]["identity"]["sha256_agrees"] is True
    assert body["walls"]["rule"]["is_frozen_not_tunable"] is True


def test_the_filter_can_be_moved_from_the_query(client, monkeypatch, tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=12, end_session=False, seal=False)
    point_at(monkeypatch, root)
    loose = client.get("/api/liquidity-map/snapshot?min_notional_usdt=0").json()
    tight = client.get("/api/liquidity-map/snapshot?min_notional_usdt=99999999").json()
    assert loose["sides"]["ASK"]["walls_shown"] >= 1
    assert tight["sides"]["ASK"]["walls_shown"] == 0
    assert tight["sides"]["ASK"]["nearest_wall"] is None
    # Narrowing the view must change neither how many candidates exist nor how many of them the
    # frozen rule called walls. A screen that shows nothing because the zoom is high must not
    # look like a market with no walls in it.
    assert tight["sides"]["ASK"]["candidates_total"] == loose["sides"]["ASK"]["candidates_total"]
    assert tight["sides"]["ASK"]["walls_selected"] == loose["sides"]["ASK"]["walls_selected"]


@pytest.mark.parametrize("query", ["min_notional_usdt=abc", "min_notional_usdt=-1",
                                   "min_notional_usdt=nan"])
def test_an_unreadable_filter_is_refused_rather_than_guessed(client, monkeypatch, tmp_path, query):
    point_at(monkeypatch, tmp_path)
    response = client.get(f"/api/liquidity-map/snapshot?{query}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILTER_INVALID"


@pytest.mark.parametrize("limit", [0, -1, API.MAX_WALL_LIMIT + 1])
def test_an_out_of_range_wall_limit_is_refused(client, monkeypatch, tmp_path, limit):
    point_at(monkeypatch, tmp_path)
    response = client.get(f"/api/liquidity-map/snapshot?wall_limit={limit}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WALL_LIMIT_INVALID"


def test_the_wall_limit_bounds_the_list_but_not_the_count(client, monkeypatch, tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    # Ten dollars apart, so each one is its own price bin: five dollars apart they would be
    # grouped, which is the rule working and would make this test about something else.
    for index in range(8):
        writer.wall(side="ASK", price=str(84_800 + index * 10), event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index)
    point_at(monkeypatch, tmp_path)
    body = client.get("/api/liquidity-map/snapshot?wall_limit=3").json()
    assert len(body["sides"]["ASK"]["walls"]) == 3
    assert body["sides"]["ASK"]["walls_shown"] == 8


# --- polling ----------------------------------------------------------------------------------

def test_a_second_poll_reads_only_what_was_appended(client, monkeypatch, tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.derived(receive_ms=2_000)
    for index in range(60):
        writer.wall(side="ASK", price=str(84_800 + index), event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index, open_file=True)
    point_at(monkeypatch, tmp_path)
    first = client.get("/api/liquidity-map/snapshot").json()
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=2_500,
                open_file=True)
    second = client.get("/api/liquidity-map/snapshot").json()
    assert second["walls"]["candidate_count"] == first["walls"]["candidate_count"] + 1
    assert second["walls"]["scanned_bytes"] < first["walls"]["scanned_bytes"]
    assert second["walls"]["verified_by"] == "FOLLOWED_FROM_VERIFIED_STATE"


# --- sessions route ---------------------------------------------------------------------------

def test_the_sessions_route_says_what_the_root_holds(client, monkeypatch, tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=3, end_session=False, seal=False)
    point_at(monkeypatch, root)
    body = client.get("/api/liquidity-map/sessions").json()
    assert body["kinds"]["derived"]["files"] == 1
    assert body["kinds"]["wall"]["bytes"] > 0
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["contract"]["contract_version"] == "btc-ms.v0.1"


def test_the_sessions_route_is_empty_rather_than_broken_without_a_root(client):
    body = client.get("/api/liquidity-map/sessions").json()
    assert body["sessions"] == [] and body["kinds"] == {}


# --- method surface ---------------------------------------------------------------------------

def test_every_route_is_read_only():
    for route in API.app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue
        assert methods <= {"GET", "HEAD"}, (route.path, methods)


def test_a_write_to_a_preview_route_is_rejected(client, monkeypatch, tmp_path):
    point_at(monkeypatch, tmp_path)
    assert client.post("/api/liquidity-map/snapshot").status_code == 405
