"""The context API: GET only, writes nothing, and never serves an old reading as a current one."""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.crypto.context_collector_v1 import api as API
from app.crypto.context_collector_v1 import contract as K

from tests.crypto.ctx_v1_fixtures import Script, context_collector


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(API.ROOT_ENV, raising=False)
    with TestClient(API.app) as session:
        yield session


@pytest.fixture
def live_root(tmp_path, monkeypatch):
    """A real collector's root, whose latest file is then re-dated to the test's own clock."""
    ctx, _ = context_collector(tmp_path)
    script = Script([ctx])
    script.start()
    script.run(70)
    script.second = 70
    # A wall the 60 s window has pushed more than its notional into, so the payload holds an
    # ABSORPTION_CANDIDATE whose fate under the floor can be checked.
    script.depth(70.5)
    script.trade(70.6, qty="45")
    script.sample(71)
    assert ctx.engine.last_latest["flow"]["absorption"]["state"] == "ABSORPTION_CANDIDATE"
    root = ctx.store.root
    ctx.store.close()
    monkeypatch.setenv(API.ROOT_ENV, str(root))
    return root


def redate(root, *, age_ms: int, **extra) -> dict:
    path = root / "state" / K.LATEST_FILENAME
    payload = json.loads(path.read_text())
    payload["written_ms"] = int(time.time() * 1000) - age_ms
    payload.update(extra)
    path.write_text(json.dumps(payload))
    return payload


def tree_digest(root) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        digest.update(f"{path.relative_to(root)}|{stat.st_size}|{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


# --------------------------------------------------------------------------- surface

def test_the_app_registers_get_routes_only():
    methods = {method for route in API.app.routes for method in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}
    paths = {route.path for route in API.app.routes}
    assert {"/health", "/snapshot", "/status"} <= paths
    assert not any(word in path for path in paths
                   for word in ("order", "account", "position", "balance", "arm", "auto"))


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("path", ["/health", "/snapshot", "/status"])
def test_every_mutating_method_is_refused(client, method, path):
    assert getattr(client, method)(path).status_code == 405


def test_serving_writes_nothing_to_the_root(client, live_root):
    redate(live_root, age_ms=100)
    before = tree_digest(live_root)
    for path in ("/health", "/snapshot", "/status", "/snapshot?symbol=ETHUSDT"):
        assert client.get(path).status_code == 200
    assert tree_digest(live_root) == before


# --------------------------------------------------------------------------- freshness floor

def test_a_fresh_reading_is_served_as_written(client, live_root):
    written = redate(live_root, age_ms=200)
    body = client.get("/snapshot").json()
    assert body["read"]["floored"] is False
    assert body["collector"]["state"] == written["collector"]["state"] == K.FEED_LIVE
    assert body["liquidity"]["state"] == "LIVE" and body["flow"]["state"] == "LIVE"


def test_an_old_reading_is_floored_to_stale_everywhere_with_its_values_kept(client, live_root):
    written = redate(live_root, age_ms=K.READ_STALE_MS + 1)
    body = client.get("/snapshot").json()
    assert body["read"]["floored"] is True
    assert body["read"]["floor_reasons"] == [API.FLOOR_AGE]
    assert body["collector"]["state"] == K.FEED_STALE
    assert body["liquidity"]["state"] == "STALE" and body["flow"]["state"] == "STALE"
    assert {side["wall_state"] for side in body["liquidity"]["sides"].values()} == {"STALE"}
    assert {w["state"] for w in body["flow"]["windows"].values()} == {"STALE"}
    assert body["flow"]["trade_stream"]["state"] == "STALE"
    assert body["flow"]["absorption"]["state"] == "NONE"
    assert body["flow"]["absorption"]["reason"] == API.FLOOR_AGE
    # The figures are still there, labelled: STALE means readable but old, not absent.
    assert (body["liquidity"]["sides"]["ASK"]["nearest_wall"]["price"]
            == written["liquidity"]["sides"]["ASK"]["nearest_wall"]["price"])
    served = {k: body[k] for k in ("collector", "liquidity", "flow")}

    def states(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "state" or key.endswith("_state"):
                    yield key, value
                yield from states(value)
        elif isinstance(node, list):
            for item in node:
                yield from states(item)

    found = list(states(served))
    assert found and not [pair for pair in found if pair[1] in ("LIVE", "PARTIAL")]


def test_an_ended_session_is_floored_even_when_its_file_is_new(client, live_root):
    redate(live_root, age_ms=10, session_ended=True)
    body = client.get("/snapshot").json()
    assert body["collector"]["state"] == K.FEED_STALE
    assert API.FLOOR_ENDED in body["read"]["floor_reasons"]


@pytest.mark.parametrize("content,reason", [(None, API.NO_FILE), (b"{not json", API.UNREADABLE),
                                            (b'{"schema": "other"}', API.UNREADABLE)])
def test_a_missing_or_unreadable_latest_file_is_unknown_not_an_error(client, tmp_path,
                                                                     monkeypatch, content,
                                                                     reason):
    (tmp_path / "state").mkdir()
    if content is not None:
        (tmp_path / "state" / K.LATEST_FILENAME).write_bytes(content)
    monkeypatch.setenv(API.ROOT_ENV, str(tmp_path))
    response = client.get("/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["collector"]["state"] == K.FEED_UNKNOWN
    assert body["collector"]["reasons"] == [reason]
    assert body["liquidity"]["state"] == "UNKNOWN" and body["flow"]["state"] == "UNKNOWN"


def test_an_unset_root_is_unknown(client):
    body = client.get("/snapshot").json()
    assert body["collector"]["state"] == K.FEED_UNKNOWN
    assert body["collector"]["reasons"] == [API.NOT_SET]
    assert client.get("/health").json()["state"] == K.FEED_UNKNOWN


def test_a_non_btc_symbol_is_unavailable_and_never_reads_the_btc_root(client, live_root,
                                                                      monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("the BTC root was read for a non-BTC symbol")

    monkeypatch.setattr(API, "read_latest", forbidden)
    for symbol in ("ETHUSDT", "SOLUSDT"):
        body = client.get(f"/snapshot?symbol={symbol}").json()
        assert body["liquidity"]["state"] == "UNAVAILABLE"
        assert body["flow"]["state"] == "UNAVAILABLE"
        assert body["liquidity"]["reasons"] == ["COLLECTOR_IS_BTC_ONLY"]


# --------------------------------------------------------------------------- health and status

def test_health_names_the_writer_and_whether_it_is_alive(client, live_root):
    redate(live_root, age_ms=100)
    body = client.get("/health").json()
    assert body["mode"] == API.MODE and body["symbol"] == "BTCUSDT"
    writer = body["writer"]
    assert writer["lock_version"] == "ms-v0-lock.v1-2"
    assert isinstance(writer["pid"], int) and writer["session_id"]
    assert writer["pid_alive"] is True        # this test process wrote it


def test_status_reports_the_session_the_policy_and_the_disk(client, live_root):
    body = client.get("/status").json()
    assert body["session"]["ended"] is False
    assert set(body["disk"]) == set(K.PERSISTED_V0_KINDS) | set(K.CONTEXT_KINDS)
    assert body["disk"]["context"]["compressed_files"] == 1
