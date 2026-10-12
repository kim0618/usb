"""BTC Manual Trading Market Context UI V1: the GET-only strip API and its display reduction.

Fixtures are recorded, not written: `collector_latest.json` is the production Context Collector's
current-state file (2026-10-11), and the structure and derivatives layers are what Market Context V1
produced from live public Binance data in the same minute.
"""
from __future__ import annotations

import ast
import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.btc_context_ui_v1 import api as A
from app.crypto.btc_context_ui_v1 import view as V
from app.crypto.context_collector_v1 import api as CTX

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "btc_context_ui_v1"
PACKAGE = Path(A.__file__).resolve().parent


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture()
def latest() -> dict:
    return _load("collector_latest.json")


@pytest.fixture()
def structure() -> dict:
    return _load("structure_layer.json")["layer"]


@pytest.fixture()
def derivatives() -> dict:
    return _load("derivatives_layer.json")["layer"]


def _root(tmp_path: Path, payload: dict | None) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    if payload is not None:
        (state / "context_latest.json").write_text(json.dumps(payload))
    return tmp_path


# --------------------------------------------------------------------------- display reduction

def test_live_reading_reduces_to_display_figures(tmp_path, latest, structure, derivatives):
    root = _root(tmp_path, latest)
    view = CTX.judged(root, now_ms=latest["written_ms"] + 1_000)
    out = V.display(collector_view=view, structure_layer=structure,
                    derivatives_layer=derivatives, now_ms=latest["written_ms"] + 1_000)
    # R1 book: resistance +0.0871 %, support -0.155 % from the last closed minute.
    assert out["structure"] == {"live": True, "resistance_pct": "0.09", "support_pct": "0.16",
                                "resistance_price": "83113.9", "support_price": "82912.9"}
    # The collector's own nearest wall per side, as it published it.
    assert out["liquidity"] == {"live": True, "ask": "STRONG", "bid": "STRONG",
                                "ask_wall_price": "83050.4", "bid_wall_price": "82996.5"}
    # 563,945.6221 bought vs 1,446,520.2292 sold in the collector's 60 s window.
    assert out["flow"] == {"live": True, "window": "60s", "buy_pct": 28, "sell_pct": 72}
    d = out["derivatives"]
    assert d["live"] is True and d["oi_window"] == "1h"
    # -0.0003 % over 1 h: the sign is the direction, the display rounding never prints "-0.00".
    assert d["oi_direction"] == "DOWN" and d["oi_change_pct"] == "0.00"
    assert d["oi_value_usdt"].isdigit()
    assert d["funding_pct"] == "-0.0014"
    assert d["basis_pct"] == "-0.040"


def test_wall_price_is_the_collectors_nearest_wall_and_needs_ok(latest):
    layer = copy.deepcopy(latest["liquidity"])
    layer["sides"]["ASK"]["nearest_wall"] = {"price": "83184"}
    layer["sides"]["BID"]["nearest_wall"] = None
    out = V.liquidity_section(layer)
    assert out["ask_wall_price"] == "83184.0" and out["bid_wall_price"] is None
    # A wall the collector still lists but no longer judges OK (floored STALE) is not placed.
    layer["sides"]["ASK"]["wall_state"] = "STALE"
    assert V.liquidity_section(layer)["ask_wall_price"] is None
    out = V.liquidity_section(copy.deepcopy(latest["liquidity"]) | {"state": "STALE"})
    assert out["ask_wall_price"] is None and out["bid_wall_price"] is None


def test_flow_share_is_buy_over_sum_and_adds_to_100(latest):
    window = latest["flow"]["windows"]["60s"]
    buy, sell = float(window["buy_usdt"]), float(window["sell_usdt"])
    out = V.flow_section(latest["flow"])
    assert out["buy_pct"] == round(buy / (buy + sell) * 100)
    assert out["buy_pct"] + out["sell_pct"] == 100


def test_wall_words_are_the_collector_states_only(latest):
    layer = copy.deepcopy(latest["liquidity"])
    layer["sides"]["ASK"]["wall_state"] = "NONE"
    layer["sides"]["BID"]["wall_state"] = "PARTIAL"
    # Only an OK side places a wall: NONE has no wall, and any other state has no value at all.
    assert V.liquidity_section(layer) == {"live": True, "ask": "NORMAL", "bid": None,
                                          "ask_wall_price": None, "bid_wall_price": None}


def test_old_collector_file_is_dashes_not_numbers(tmp_path, latest):
    """The collector API floors a file older than its read bound to STALE; nothing is shown."""
    root = _root(tmp_path, latest)
    view = CTX.judged(root, now_ms=latest["written_ms"] + 60 * 60_000)
    assert view["read"]["floored"] is True
    out = V.display(collector_view=view, structure_layer=None, derivatives_layer=None,
                    now_ms=latest["written_ms"] + 60 * 60_000)
    assert out["liquidity"] == {"live": False, "ask": None, "bid": None,
                                 "ask_wall_price": None, "bid_wall_price": None}
    assert out["flow"]["buy_pct"] is None and out["flow"]["sell_pct"] is None
    assert out["flow"]["live"] is False


@pytest.mark.parametrize("payload", [None, "garbage"])
def test_missing_or_unreadable_collector_file_is_unknown_and_dashes(tmp_path, payload):
    root = tmp_path
    (root / "state").mkdir()
    if payload is not None:
        (root / "state" / "context_latest.json").write_text(payload)
    view = CTX.judged(root, now_ms=int(time.time() * 1000))
    out = V.display(collector_view=view, structure_layer=None, derivatives_layer=None, now_ms=0)
    for name in ("structure", "liquidity", "flow", "derivatives"):
        section = out[name]
        assert section["live"] is False
        assert all(value is None for key, value in section.items()
                   if key not in ("live", "window", "oi_window"))


def test_stale_and_unknown_structure_and_derivatives_publish_nothing(structure, derivatives):
    for state in ("STALE", "UNKNOWN", "UNAVAILABLE"):
        s = dict(structure, state=state)
        d = dict(derivatives, state=state)
        assert V.structure_section(s) == {"live": False, "resistance_pct": None,
                                          "support_pct": None, "resistance_price": None,
                                          "support_price": None}
        assert all(value is None for key, value in V.derivatives_section(d).items()
                   if key not in ("live", "oi_window"))


def test_partial_shows_values_without_live(derivatives):
    out = V.derivatives_section(dict(derivatives, state="PARTIAL"))
    assert out["live"] is False and out["funding_pct"] == "-0.0014"


def test_no_level_on_one_side_is_a_dash(structure):
    layer = copy.deepcopy(structure)
    layer["levels"]["resistance"].update(price=None, distance_pct=None, available=False)
    assert V.structure_section(layer)["resistance_pct"] is None
    assert V.structure_section(layer)["resistance_price"] is None
    assert V.structure_section(layer)["support_pct"] == "0.16"
    assert V.structure_section(layer)["support_price"] == "82912.9"


def test_zero_flow_is_dashes_not_fifty_fifty(latest):
    layer = copy.deepcopy(latest["flow"])
    layer["windows"]["60s"].update(buy_usdt="0", sell_usdt="0")
    assert V.flow_section(layer)["buy_pct"] is None


# --------------------------------------------------------------------------- nothing internal leaks

BANNED = ("Q1", "Q2", "Q3", "SHORT", "LONG", "CANDIDATE", "RESEARCH", "UNVALIDATED", "SAMPLE",
          "P_VALUE", "PVALUE", "CONFIDENCE", "SIGNAL", "CONTRACT", "VERSION", "HASH", "SHA",
          "ABSORPTION", "CANCEL", "CONSUMED", "STALE", "UNKNOWN", "PARTIAL", "UNAVAILABLE",
          "REASON", "AUTO")


def _words(node) -> list[str]:
    if isinstance(node, dict):
        return [w for k, v in node.items() for w in [str(k), *_words(v)]]
    if isinstance(node, list):
        return [w for item in node for w in _words(item)]
    return [str(node)]


@pytest.mark.parametrize("age_ms", [1_000, 60 * 60_000])
def test_payload_carries_no_research_or_internal_vocabulary(tmp_path, latest, structure,
                                                            derivatives, age_ms):
    root = _root(tmp_path, latest)
    view = CTX.judged(root, now_ms=latest["written_ms"] + age_ms)
    out = V.display(collector_view=view, structure_layer=structure,
                    derivatives_layer=derivatives, now_ms=0)
    text = " ".join(_words(out)).upper()
    for word in BANNED:
        assert word not in text, word


def test_payload_keys_are_exactly_the_display_fields(tmp_path, latest, structure, derivatives):
    root = _root(tmp_path, latest)
    view = CTX.judged(root, now_ms=latest["written_ms"] + 1_000)
    out = V.display(collector_view=view, structure_layer=structure,
                    derivatives_layer=derivatives, now_ms=0)
    assert set(out) == {"symbol", "server_time_ms", "structure", "liquidity", "flow",
                        "derivatives", "walls", "entry"}
    assert set(out["walls"]) == {"live", "mid", "ask", "bid", "recent_ends"}
    assert out["entry"] == {"available": False}
    assert set(out["structure"]) == {"live", "resistance_pct", "support_pct", "resistance_price",
                                     "support_price"}
    assert set(out["liquidity"]) == {"live", "ask", "bid", "ask_wall_price", "bid_wall_price"}
    assert set(out["flow"]) == {"live", "window", "buy_pct", "sell_pct"}
    assert set(out["derivatives"]) == {"live", "oi_direction", "oi_change_pct", "oi_window",
                                       "oi_value_usdt", "funding_pct", "basis_pct"}


# --------------------------------------------------------------------------- API

class _Failing:
    """A venue that refuses everything: no structure, no derivatives, and no exception."""

    calls = 0

    def __call__(self, request, timeout=None):
        _Failing.calls += 1
        raise OSError("venue down")


@pytest.fixture()
def client(tmp_path, latest, monkeypatch):
    fresh = dict(latest, written_ms=int(time.time() * 1000))
    root = _root(tmp_path, fresh)
    monkeypatch.setenv(CTX.ROOT_ENV, str(root))
    monkeypatch.setattr(A.state, "sources", A.Sources(opener=_Failing()))
    return TestClient(A.app)


def test_every_route_is_get_only():
    methods = {m for route in A.app.routes if getattr(route, "path", "") in ("/health", "/snapshot")
               for m in route.methods}
    assert methods == {"GET", "HEAD"} or methods == {"GET"}
    paths = {getattr(route, "path", "") for route in A.app.routes}
    assert paths == {"/health", "/snapshot"}
    assert not any(p.startswith(("/order", "/account", "/leverage", "/auto")) for p in paths)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_non_get_is_refused(client, method):
    for path in ("/snapshot", "/health"):
        assert getattr(client, method)(path).status_code == 405


def test_snapshot_with_live_collector_and_venue_down(client):
    response = client.get("/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["liquidity"]["live"] is True and body["flow"]["buy_pct"] == 28
    assert body["structure"] == {"live": False, "resistance_pct": None, "support_pct": None,
                                 "resistance_price": None, "support_price": None}
    assert body["derivatives"]["live"] is False


def test_snapshot_without_collector_root_is_all_dashes(monkeypatch):
    monkeypatch.delenv(CTX.ROOT_ENV, raising=False)
    monkeypatch.setattr(A.state, "sources", A.Sources(opener=_Failing()))
    body = TestClient(A.app).get("/snapshot").json()
    assert body["liquidity"] == {"live": False, "ask": None, "bid": None,
                                 "ask_wall_price": None, "bid_wall_price": None}
    assert body["flow"]["buy_pct"] is None
    assert TestClient(A.app).get("/health").json()["collector_root_set"] is False


def test_refresh_failure_keeps_worker_alive_and_never_raises():
    sources = A.Sources(opener=_Failing())
    sources.refresh(int(time.time() * 1000))
    assert not hasattr(sources, "series")          # STRUCTURE is the worker's file, not this process
    assert A.structure_layer(None, int(time.time() * 1000))["state"] == "UNKNOWN"


def test_kick_never_blocks_and_never_runs_twice():
    import threading
    sources = A.Sources(opener=_Failing())
    gate = threading.Event()
    started = []

    def slow(now_ms):
        started.append(now_ms)
        gate.wait(5)

    sources.refresh = slow                                  # type: ignore[method-assign]
    t0 = time.monotonic()
    assert sources.kick(int(time.time() * 1000)) is True
    assert sources.kick(int(time.time() * 1000)) is False   # one already running
    assert time.monotonic() - t0 < 0.5
    gate.set()


def test_derivatives_are_not_reread_inside_the_floor():
    sources = A.Sources(opener=_Failing())
    now = int(time.time() * 1000)
    sources.refresh(now)
    first = sources.derivatives
    assert first is not None
    assert not sources.derivatives_due(now + A.DERIVATIVES_MIN_INTERVAL_MS - 1)
    assert sources.derivatives_due(now + A.DERIVATIVES_MIN_INTERVAL_MS)


# --------------------------------------------------------------------------- isolation

FORBIDDEN_IMPORTS = ("paper", "live", "terminal", "c1", "q123", "order", "account", "binance_live",
                     "hot_coins", "exit_guard", "auto", "research.q123_forward_v1")


def test_package_imports_nothing_that_trades():
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                assert not any(p in FORBIDDEN_IMPORTS for p in parts), (path.name, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    assert not any(p in FORBIDDEN_IMPORTS for p in parts), (path.name, alias.name)


#: The modules the API process runs. The structure worker (`structure_state`, `structure_worker`)
#: is a separate oneshot that writes its own state directory; its writes are tested on their own.
API_MODULES = ("__init__.py", "__main__.py", "api.py", "view.py", "entry_context.py")


def test_package_writes_nothing():
    for path in (PACKAGE / name for name in API_MODULES):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in ("write_text", "write_bytes", "mkdir", "unlink", "rename",
                                    "replace", "rmtree", "remove"), (path.name, name)
                if name == "open":
                    modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                    assert not any(set(str(m)) & set("wax+") for m in modes), path.name
