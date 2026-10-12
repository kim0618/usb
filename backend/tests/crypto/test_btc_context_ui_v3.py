"""BTC Manual Trading UI V3: the wall hierarchy and the entry context the screen is handed.

`collector_latest_20261012.json` is the production Context Collector's current-state file as read
on 2026-10-12 08:5x KST (six ASK walls, two BID walls published). The recorder store is written
here in the recorder's own row shapes (`manual_snapshot_v1.episodes.OrderEvent.row`,
`recorder._snapshot`), because no production store exists yet.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.btc_context_ui_v1 import api as A
from app.crypto.btc_context_ui_v1 import entry_context as EC
from app.crypto.btc_context_ui_v1 import view as V
from app.crypto.context_collector_v1 import api as CTX

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "btc_context_ui_v1"


@pytest.fixture()
def latest() -> dict:
    return json.loads((FIXTURES / "collector_latest_20261012.json").read_text())


def _judged(tmp_path: Path, latest: dict, age_ms: int = 1_000) -> dict:
    state = tmp_path / "ctx" / "state"
    state.mkdir(parents=True)
    (state / "context_latest.json").write_text(json.dumps(latest))
    return CTX.judged(tmp_path / "ctx", now_ms=latest["written_ms"] + age_ms)


# --------------------------------------------------------------------------- walls

def test_walls_are_the_collectors_published_set_nearest_first(tmp_path, latest):
    view = _judged(tmp_path, latest)
    out = V.walls_section(view["liquidity"], view["flow"])
    for key, side in (("ask", "ASK"), ("bid", "BID")):
        published = latest["liquidity"]["sides"][side]["walls"]
        assert [w["price"] for w in out[key]] == [V._price(w["price"]) for w in published]
        assert [w["rank"] for w in out[key]] == list(range(1, len(published) + 1))
        assert [w["age_ms"] for w in out[key]] == [w["persistence_ms"] for w in published]
    asks = [float(w["price"]) for w in out["ask"]]
    bids = [float(w["price"]) for w in out["bid"]]
    mid = float(out["mid"])
    assert asks == sorted(asks) and all(p > mid for p in asks)
    assert bids == sorted(bids, reverse=True) and all(p < mid for p in bids)


def test_size_rank_is_a_sort_of_the_published_notionals(tmp_path, latest):
    view = _judged(tmp_path, latest)
    out = V.walls_section(view["liquidity"], view["flow"])
    for key in ("ask", "bid"):
        by_size = sorted(out[key], key=lambda w: w["size_rank"])
        sizes = [float(w["notional_usdt"]) for w in by_size]
        assert sizes == sorted(sizes, reverse=True)
        assert sorted(w["size_rank"] for w in out[key]) == list(range(1, len(out[key]) + 1))


def test_no_walls_unless_the_side_reads_ok_or_the_layer_is_current(tmp_path, latest):
    view = _judged(tmp_path, latest)
    view["liquidity"]["sides"]["BID"]["wall_state"] = "PARTIAL"
    out = V.walls_section(view["liquidity"], view["flow"])
    assert out["bid"] == [] and len(out["ask"]) > 0
    stale = _judged(tmp_path / "old", latest, age_ms=3_600_000)
    out = V.walls_section(stale["liquidity"], stale["flow"])
    assert out["ask"] == [] and out["bid"] == [] and out["mid"] is None and not out["live"]


def test_recent_ends_are_the_collectors_classified_ends(tmp_path, latest):
    view = _judged(tmp_path, latest)
    out = V.walls_section(view["liquidity"], view["flow"])
    recent = latest["flow"]["vanished"]["recent"][:V.RECENT_ENDS_LIMIT]
    assert [e["price"] for e in out["recent_ends"]] == [V._price(r["price"]) for r in recent]
    assert [e["price_reached"] for e in out["recent_ends"]] == [r["mid_path_touched_bin"] for r in recent]


# --------------------------------------------------------------------------- entry store

T_OPEN = 1_791_760_000_000


def _event(eid: str, etype: str, direction: str, first: int, qty_after: str, vwap: str,
           episode: str) -> dict:
    return {"event_id": eid, "order_id": int(eid.split(":")[0]), "event_type": etype,
            "origin": "MANUAL_TERMINAL", "direction": direction, "episode_id": episode,
            "first_fill_ms": first, "last_fill_ms": first + 40, "qty": "0.010", "vwap": vwap,
            "fills": 1, "commission": "0.3", "realized_pnl": "0", "client_order_id": "usbm-open-1",
            "qty_before": "0", "qty_after": qty_after, "vwap_entry_before": None,
            "vwap_entry_after": vwap, "episode_realized_before": "0"}


def _snapshot(eid: str, *, collector_status: str = "OK") -> dict:
    return {"snapshot_id": f"ev:{eid}", "kind": "EVENT", "as_of_ms": T_OPEN + 40,
            "market": {"last_1m_close": 83600.0, "mark": 83610.0, "index": 83600.0,
                       "basis_bp": 1.1961, "funding_rate_last": 0.00001,
                       "oi_last_value_usdt": 7412345678.9, "oi_change_1h_pct": 0.42},
            "collector": {"row_ms": T_OPEN, "age_ms": 40,
                          "book": {"mid": "83612.85"},
                          "liquidity": {"ASK": {"wall_state": "OK", "price": "83650"},
                                        "BID": {"wall_state": "NONE", "price": None}},
                          "flow": {"60s": {"buy_usdt": "390000", "sell_usdt": "610000",
                                           "state": "LIVE"}}},
            "layer_status": {"market": {"status": "OK"},
                             "collector": {"status": collector_status}}}


def _store(root: Path, events: list[dict], snapshots: list[dict], closed: list[str] = ()) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (root / "snapshots.jsonl").write_text("".join(json.dumps(s) + "\n" for s in snapshots))
    (root / "episodes.jsonl").write_text("".join(json.dumps({"episode_id": c}) + "\n" for c in closed))
    return root


def test_entry_is_the_open_episodes_first_fill_snapshot(tmp_path):
    ep = "ep-900"
    root = _store(tmp_path / "s", [
        _event("900", "OPEN_SHORT", "SHORT", T_OPEN, "-0.010", "83612.9", ep),
        _event("901", "ADD", "SHORT", T_OPEN + 600_000, "-0.020", "83700", ep)],
        [_snapshot("900"), _snapshot("901")])
    out = V.entry_section(EC.open_entry(root))
    assert out["available"] and out["direction_sign"] == -1 and out["entry_price"] == "83612.9"
    assert out["entry_ms"] == T_OPEN and out["episode_id"] == ep
    # restated in the units the current sections use
    assert out["flow"] == {"window": "60s", "buy_pct": 39, "sell_pct": 61}
    assert out["funding_pct"] == "0.0010" and out["basis_pct"] == "0.012"
    assert out["oi_value_usdt"] == "7412345679" and out["oi_change_pct"] == "0.42"
    assert out["ask_wall_price"] == "83650.0" and out["bid_wall_price"] is None


def test_closed_or_flat_episode_has_no_entry(tmp_path):
    ev = _event("900", "OPEN_LONG", "LONG", T_OPEN, "0.010", "83000", "ep-900")
    closed = _store(tmp_path / "a", [ev], [_snapshot("900")], closed=["ep-900"])
    assert EC.open_entry(closed) == {"available": False, "reason": EC.NO_OPEN_EPISODE}
    flat = dict(_event("901", "CLOSE", "LONG", T_OPEN + 1, "0", "83100", "ep-900"))
    root = _store(tmp_path / "b", [ev, flat], [_snapshot("900")])
    assert EC.open_entry(root)["reason"] == EC.NO_OPEN_EPISODE


def test_entry_not_yet_snapshotted_and_stale_collector(tmp_path):
    ev = _event("900", "OPEN_LONG", "LONG", T_OPEN, "0.010", "83000", "ep-900")
    root = _store(tmp_path / "a", [ev], [])
    assert EC.open_entry(root)["reason"] == EC.NO_ENTRY_SNAPSHOT
    root = _store(tmp_path / "b", [ev], [_snapshot("900", collector_status="NOT_AVAILABLE")])
    out = V.entry_section(EC.open_entry(root))
    assert out["collector_recorded"] is False and out["market_recorded"] is True
    # the collector predates nothing it can show: flow and walls are absent, market stays
    assert out["flow"]["sell_pct"] is None and out["ask_wall_price"] is None
    assert out["funding_pct"] == "0.0010"


def test_entry_reader_is_cached_until_a_store_file_changes(tmp_path, monkeypatch):
    ev = _event("900", "OPEN_LONG", "LONG", T_OPEN, "0.010", "83000", "ep-900")
    root = _store(tmp_path / "s", [ev], [_snapshot("900")])
    calls = []
    real = EC.open_entry
    monkeypatch.setattr(EC, "open_entry", lambda r: calls.append(r) or real(r))
    reader = EC.EntryReader()
    first = reader.read(root)
    assert reader.read(root) is first and len(calls) == 1
    with open(root / "snapshots.jsonl", "a") as handle:
        handle.write(json.dumps({"snapshot_id": "hold:ep-900:1", "kind": "HOLD"}) + "\n")
    reader.read(root)
    assert len(calls) == 2
    assert reader.read(None) == {"available": False, "reason": EC.NOT_CONFIGURED}


def test_entry_reader_writes_nothing_to_the_store(tmp_path):
    ev = _event("900", "OPEN_LONG", "LONG", T_OPEN, "0.010", "83000", "ep-900")
    root = _store(tmp_path / "s", [ev], [_snapshot("900")])
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
    EC.EntryReader().read(root)
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
    assert before == after


def test_torn_last_line_is_skipped(tmp_path):
    ev = _event("900", "OPEN_LONG", "LONG", T_OPEN, "0.010", "83000", "ep-900")
    root = _store(tmp_path / "s", [ev], [_snapshot("900")])
    with open(root / "events.jsonl", "a") as handle:
        handle.write('{"event_id": "901", "episode')
    assert EC.open_entry(root)["available"] is True


# --------------------------------------------------------------------------- API

def test_snapshot_serves_walls_and_entry_and_entry_off_without_store(tmp_path, latest, monkeypatch):
    fresh = dict(latest, written_ms=int(time.time() * 1000))
    state = tmp_path / "ctx" / "state"
    state.mkdir(parents=True)
    (state / "context_latest.json").write_text(json.dumps(fresh))
    monkeypatch.setenv(CTX.ROOT_ENV, str(tmp_path / "ctx"))
    monkeypatch.delenv(EC.STORE_ENV, raising=False)
    monkeypatch.setattr(A.state, "entry", EC.EntryReader())

    class Down:
        def __call__(self, request, timeout=None):
            raise OSError("venue down")
    monkeypatch.setattr(A.state, "sources", A.Sources(opener=Down()))
    body = TestClient(A.app).get("/snapshot").json()
    assert len(body["walls"]["ask"]) == len(latest["liquidity"]["sides"]["ASK"]["walls"])
    assert body["entry"] == {"available": False}
    ev = _event("900", "OPEN_SHORT", "SHORT", T_OPEN, "-0.010", "83612.9", "ep-900")
    monkeypatch.setenv(EC.STORE_ENV, str(_store(tmp_path / "store", [ev], [_snapshot("900")])))
    body = TestClient(A.app).get("/snapshot").json()
    assert body["entry"]["available"] and body["entry"]["entry_price"] == "83612.9"


def test_a_broken_store_is_not_available_and_the_rest_is_served(tmp_path, latest, monkeypatch):
    monkeypatch.setenv(EC.STORE_ENV, str(tmp_path / "nowhere"))
    monkeypatch.setattr(A.state, "entry", EC.EntryReader())
    assert A.entry_reading() == {"available": False, "reason": EC.NO_STORE}


def test_entry_payload_carries_no_internal_vocabulary(tmp_path):
    from tests.crypto.test_btc_context_ui_v1 import BANNED, _words
    ev = _event("900", "OPEN_SHORT", "SHORT", T_OPEN, "-0.010", "83612.9", "ep-900")
    root = _store(tmp_path / "s", [ev], [_snapshot("900")])
    for entry in (EC.open_entry(root), {"available": False, "reason": EC.NO_STORE}):
        text = " ".join(_words(V.entry_section(entry))).upper()
        for word in BANNED:
            assert word not in text, word
