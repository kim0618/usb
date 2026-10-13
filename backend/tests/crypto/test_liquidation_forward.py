"""D5.3 liquidation forward collector: parsers, side semantics, dedupe, segment files, recovery,
gap detection, OKX REST paging/retry, and isolation. No network."""
from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import httpx
import pytest

from app.crypto.liquidation_forward import collector as C
from app.crypto.liquidation_forward import daily as D
from app.crypto.liquidation_forward import parsers as P
from app.crypto.liquidation_forward import store as S

T = 1_790_500_000_000
PKG = Path(__file__).resolve().parents[2] / "app" / "crypto" / "liquidation_forward"


def by(S_, p="85000", v="0.5", t=T):
    return {"topic": "allLiquidation.BTCUSDT", "type": "snapshot", "ts": t,
            "data": [{"T": t, "s": "BTCUSDT", "S": S_, "v": v, "p": p}]}


def bn(S_, X="FILLED", ap="85000", z="0.2", t=T):
    return {"stream": "btcusdt@forceOrder", "data": {"e": "forceOrder", "E": t + 5, "o": {
        "s": "BTCUSDT", "S": S_, "o": "LIMIT", "f": "IOC", "q": "0.2", "p": "84900", "ap": ap, "X": X, "l": "0.1", "z": z, "T": t}}}


def ok_details(pos, side, ts=T, px="85100", sz="3"):
    return {"bkLoss": "0", "bkPx": px, "ccy": "", "posSide": pos, "side": side, "sz": sz, "ts": str(ts)}


# ------------------------------------------------------------------ parse + side
def test_bybit_parse_and_side():
    r = P.bybit(by("Buy"), T + 100)[0]
    assert r["liquidated_side"] == "LONG" and r["side_rule"] == "bybit_S_position_side"
    assert r["price"] == 85000 and r["bankruptcy_price"] == 85000 and r["qty"] == 0.5 and r["notional_usd"] == 42500
    assert r["is_aggregated"] is False and r["source_event_id"] is None and r["mark_price"] is None
    assert P.bybit(by("Sell"), 0)[0]["liquidated_side"] == "SHORT"
    assert P.bybit({"topic": "allLiquidation.ETHUSDT", "data": []}, 0) == []


def test_binance_parse_side_aggregated_partial():
    r = P.binance(bn("SELL"), T)[0]
    assert r["liquidated_side"] == "LONG" and r["is_aggregated"] is True and r["is_partial"] is False
    assert r["price_kind"] == "avg_fill" and r["qty"] == 0.2
    r2 = P.binance(bn("BUY", X="PARTIALLY_FILLED", ap="0"), T)[0]
    assert r2["liquidated_side"] == "SHORT" and r2["is_partial"] is True and r2["price_kind"] == "order_price"
    assert P.binance({"data": {"e": "markPriceUpdate"}}, T) == []


def test_okx_parse_rest_ws_and_side_rules():
    payload = {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "details": [ok_details("short", "buy"), ok_details("net", "sell"),
                                                                              ok_details("net", "x"), ok_details("weird", "buy")]},
                                     {"instId": "BTC-USD-SWAP", "details": [ok_details("long", "sell")]}]}
    rows = P.okx_rest(payload, 0.01, T)
    assert [r["liquidated_side"] for r in rows] == ["SHORT", "LONG", None, None]
    assert [r["side_rule"] for r in rows] == ["okx_posSide", "okx_net_order_side", "unknown", "unknown"]
    assert rows[0]["qty"] == pytest.approx(0.03) and rows[0]["source"] == "okx_rest"
    assert P.okx_rest(payload, None, T)[0]["qty"] is None                     # ctVal unknown -> null, never guessed
    ws = P.okx_ws({"arg": {"channel": "liquidation-orders", "instType": "SWAP"}, "data": payload["data"]}, 0.01, T)
    assert len(ws) == 4 and ws[0]["source"] == "okx_ws" and ws[0]["dedupe_key"] == rows[0]["dedupe_key"]


def test_schema_fields_and_timestamps():
    r = P.bybit(by("Buy"), T + 250)[0]
    assert set(r) >= {"event_ts_ms", "receive_ts_ms", "exchange", "symbol", "instrument_type", "liquidated_side", "price",
                      "qty", "notional_usd", "source", "source_event_id", "is_aggregated", "is_partial", "raw_side",
                      "raw_symbol", "collector_version", "schema_version", "bankruptcy_price", "mark_price", "index_price"}
    assert r["event_ts_ms"] == T and r["receive_ts_ms"] == T + 250 and r["schema_version"] == "liq.v1"


# ------------------------------------------------------------------ dedupe
def test_dedupe_previous_message_only_and_reconnect_reload():
    d = P.Deduper()
    m = by("Buy")
    m["data"].append(dict(m["data"][0]))                                      # same key twice in ONE message
    assert len(d.filter(P.bybit(m, T))) == 2 and d.intra_message_same_key == 1
    assert d.filter(P.bybit(by("Buy"), T)) == [] and d.duplicates == 1        # replay after reconnect
    d2 = P.Deduper()
    d2.load(P.bybit(by("Buy"), 0)[0]["dedupe_key"], T)                        # keys reloaded on restart
    assert d2.filter(P.bybit(by("Buy"), T)) == []
    d2.prune(T + 49 * 3_600_000)
    assert d2.seen == {}


# ------------------------------------------------------------------ files
def test_segment_partition_seal_manifest_sha(tmp_path):
    man = S.Manifest(tmp_path)
    w = S.SegmentWriter(tmp_path, "norm", "bybit", man)
    t = 1_790_503_000_000                                                     # 2026-09-27 hour 09 UTC
    w.write({"receive_ts_ms": t, "x": 1}, t)
    w.write({"receive_ts_ms": t + 3_600_000, "x": 2}, t + 3_600_000)          # next hour -> previous sealed
    w.close()
    files = sorted((tmp_path / "norm" / "exchange=bybit").rglob("*.jsonl.gz"))
    assert [f.name for f in files] == ["bybit-2026-09-27T09.jsonl.gz", "bybit-2026-09-27T10.jsonl.gz"]
    assert files[0].parent.name == "date=2026-09-27"
    recs = [json.loads(l) for l in open(man.path)]
    assert len(recs) == 2 and recs[0]["rows"] == 1 and recs[0]["sha256"] == S.sha256_file(files[0])
    assert not list(tmp_path.rglob("*.partial"))


def test_partial_recovery_cuts_torn_line_and_never_overwrites(tmp_path):
    man = S.Manifest(tmp_path)
    d = tmp_path / "raw" / "exchange=okx" / "date=2026-09-27"
    d.mkdir(parents=True)
    (d / "okx-2026-09-27T10.jsonl.gz").write_bytes(gzip.compress(b'{"a":0}\n'))   # an already sealed file
    p = d / "okx-2026-09-27T10.jsonl.partial"
    p.write_bytes(b'{"recv_ms": 1}\n{"recv_ms": 2}\n{"recv_')                # crash mid-line
    (d / "okx-2026-09-27T09.jsonl.gz.partial").write_bytes(b"half")
    out = S.recover_partials(tmp_path, man)
    assert {"removed_half_gzip": str(d / "okx-2026-09-27T09.jsonl.gz.partial")} in out
    sealed = d / "okx-2026-09-27T10-r1.jsonl.gz"
    assert sealed.exists() and gzip.decompress(sealed.read_bytes()) == b'{"recv_ms": 1}\n{"recv_ms": 2}\n'
    assert gzip.decompress((d / "okx-2026-09-27T10.jsonl.gz").read_bytes()) == b'{"a":0}\n'
    rec = json.loads(open(man.path).read().splitlines()[-1])
    assert rec["recovered"] and rec["torn_bytes_cut"] == len(b'{"recv_')


# ------------------------------------------------------------------ gaps
def test_gap_detection_from_heartbeats():
    hb = lambda ms, up=True, sess=1: {"ms": ms, "session_start_ms": sess, "up": {"bybit": up, "okx": up}, "stats": {}}  # noqa: E731
    t0, t1 = T, T + 20 * 60_000
    hbs = [hb(T + i * 60_000) for i in range(6)] + [hb(T + 13 * 60_000, sess=2), hb(T + 14 * 60_000, sess=2),
                                                   hb(T + 15 * 60_000, False, 2), hb(T + 16 * 60_000, sess=2)]
    up = D.up_intervals(hbs, "bybit", t0, t1)
    assert up == [[T, T + 5 * 60_000], [T + 13 * 60_000, T + 14 * 60_000]]     # 8-min collector gap and 1 DOWN minute excluded
    assert D.complement(up, t0, t1)[0] == [T + 5 * 60_000, T + 13 * 60_000]


def test_counter_deltas_across_sessions():
    h = lambda ms, s, n: {"ms": ms, "session_start_ms": s, "up": {}, "stats": {"bybit": {"reconnects": n, "connected": True}}}  # noqa: E731
    tot = D.counter_deltas([h(T - 1, 1, 5), h(T + 1, 1, 7), h(T + 2, 2, 3)], T)
    assert tot["bybit"]["reconnects"] == 2 + 3


# ------------------------------------------------------------------ OKX REST paging / retry
def test_okx_rest_pagination_overlap_and_retry(tmp_path):
    calls = []
    pages = {None: [T + 300, T + 200], str(T + 200): [T + 100, T + 50], str(T + 50): []}
    state = {"fail": 1}

    def handler(req: httpx.Request):
        after = req.url.params.get("after")
        calls.append(after)
        if state["fail"]:
            state["fail"] -= 1
            return httpx.Response(429)
        dets = [ok_details("long", "sell", ts=t) for t in pages[after]]
        return httpx.Response(200, json={"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "details": dets}] if dets else []})

    async def go():
        col = C.Collector(tmp_path)
        col.ct_val = 0.01
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cl:
            n1 = await col.okx_poll_once(cl)
            rows1 = col.stats.c["okx_rest"]["rows"]
            n2 = await col.okx_poll_once(cl)                                   # same data again -> all duplicates
        for w in col.writers.values():
            w.close()
        return col, n1, rows1, n2
    col, n1, rows1, n2 = asyncio.run(go())
    assert col.stats.c["okx_rest"]["rate_limit_hits"] == 1 and calls[0] is None
    assert rows1 == 4 and col.stats.c["okx_rest"]["rows"] == 4 and col.stats.c["okx_rest"]["duplicates"] >= 2
    assert col.okx_last_max_ts == T + 300


# ------------------------------------------------------------------ isolation / safety
def test_no_private_endpoints_or_keys():
    text = "\n".join(p.read_text() for p in PKG.glob("*.py")).lower()
    for bad in ("api_key", "apikey", "secret", "signature", "/private", "/api/v5/trade", "/fapi/v1/order", "/v5/order",
                "x-mbx-apikey", "ok-access-key", "listenkey"):
        assert bad not in text, bad


def test_paper_and_terminal_do_not_import_collector():
    root = PKG.parent
    for p in list((root / "paper").rglob("*.py")) + list((root / "terminal").rglob("*.py")):
        assert "liquidation_forward" not in p.read_text(), p
    import ast
    for p in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for m in mods:
                assert not m.startswith(("app.crypto.paper", "app.crypto.terminal", "app.crypto.research")), (p, m)


def test_single_instance_lock(tmp_path):
    fh = C.acquire_lock(tmp_path)
    with pytest.raises(SystemExit):
        C.acquire_lock(tmp_path)
    fh.close()
    C.acquire_lock(tmp_path).close()


def test_restart_inside_heartbeat_gap_is_down():
    hb = lambda ms, sess: {"ms": ms, "session_start_ms": sess, "up": {"bybit": True}, "stats": {}}  # noqa: E731
    hbs = [hb(T, 1), hb(T + 60_000, 1), hb(T + 100_000, 2), hb(T + 160_000, 2)]
    assert D.up_intervals(hbs, "bybit", T, T + 160_000) == [[T, T + 60_000], [T + 100_000, T + 160_000]]


def test_okx_rest_raw_kept_only_for_pages_with_new_rows(tmp_path):
    col = C.Collector(tmp_path)
    rows = P.okx_rest({"data": [{"instId": "BTC-USDT-SWAP", "details": [ok_details("long", "sell")]}]}, 0.01, T)
    col.emit("okx_rest", "okx", "page1", rows, T)
    col.emit("okx_rest", "okx", "page2-duplicate", P.okx_rest({"data": [{"instId": "BTC-USDT-SWAP", "details": [ok_details("long", "sell")]}]}, 0.01, T), T)
    col.emit("okx_ws", "okx", "ws-msg", [], T)
    for w in col.writers.values():
        w.close()
    raw = [json.loads(l) for f in (tmp_path / "raw").rglob("*.gz") for l in gzip.open(f, "rt")]
    assert [r["raw"] for r in raw] == ["page1", "ws-msg"]


def test_heartbeat_file_compressed_after_finalize_and_still_read(tmp_path):
    rt = tmp_path / "runtime"
    rt.mkdir(parents=True)
    t0, _ = D.day_bounds("2026-09-20")
    (rt / "heartbeat-2026-09-20.jsonl").write_text("\n".join(json.dumps({"ms": t0 + i * 60_000, "session_start_ms": 1,
                                                                         "up": {"bybit": True}, "stats": {}}) for i in range(3)) + "\n")
    info = D.compress_heartbeat(tmp_path, "2026-09-20")
    assert info and (rt / "heartbeat-2026-09-20.jsonl.gz").exists() and not (rt / "heartbeat-2026-09-20.jsonl").exists()
    assert len(D.heartbeats(tmp_path, t0, t0 + 3_600_000)) == 3
