"""D5.3 liquidation forward collector process (one asyncio loop, public endpoints only).

    LIQFWD_ROOT=/root/usb_runtime/crypto_liqfwd/data python -m app.crypto.liquidation_forward.collector

Tasks: Bybit WS, Binance WS, OKX WS (audit), OKX REST poller (primary), heartbeat/rotation.
Raw messages go to raw/, parsed rows (after dedupe) to norm/. Contract sections 5-8.
"""
from __future__ import annotations

import asyncio
import json
import os
import resource
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets

from . import COLLECTOR_VERSION, EXCHANGES, SCHEMA_VERSION
from . import parsers as P
from . import store as S

BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
BINANCE_WS = "wss://fstream.binance.com/market/stream?streams=btcusdt@forceOrder/btcusdt@markPrice@1s"
OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"
OKX_REST = "https://www.okx.com/api/v5/public/liquidation-orders"
OKX_INSTRUMENTS = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP"
UA = "Mozilla/5.0 (US-B research liquidation collector; public data only)"

HEARTBEAT_S = 60
HEALTH_WINDOW_MS = 90_000
OKX_POLL_S = 60
OKX_OVERLAP_MS = 10 * 60_000
OKX_MAX_PAGES = 30
DISK_MIN_FREE = int(os.environ.get("LIQFWD_DISK_MIN_FREE", 300 * 1024 * 1024))
PING_S = 20


def now_ms() -> int:
    return int(time.time() * 1000)


class Stats:
    def __init__(self):
        self.c = {e: {"raw_msgs": 0, "rows": 0, "duplicates": 0, "intra_same_key": 0, "invalid": 0, "null_side": 0,
                      "reconnects": 0, "ws_errors": 0, "http_errors": 0, "rate_limit_hits": 0, "health_ms": 0,
                      "connected": False, "last_msg_ms": 0}
                  for e in ("bybit", "binance", "okx_ws", "okx_rest")}


class Collector:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.manifest = S.Manifest(root)
        self.stats = Stats()
        self.writers: dict[tuple[str, str], S.SegmentWriter] = {}
        self.dedupe = {s: P.Deduper() for s in ("bybit_ws", "binance_ws", "okx_ws", "okx_rest")}
        self.ct_val: float | None = None
        self.stop = asyncio.Event()
        self.disk_guard = False
        self.dropped_while_guarded = 0
        self.okx_last_max_ts: int | None = None
        self.okx_last_success_ms: int | None = None
        self.session_start = now_ms()

    # ------------------------------------------------------------------ writing
    def _writer(self, kind: str, exchange: str) -> S.SegmentWriter:
        k = (kind, exchange)
        if k not in self.writers:
            self.writers[k] = S.SegmentWriter(self.root, kind, exchange, self.manifest)
        return self.writers[k]

    def emit(self, stream: str, exchange: str, raw_text: str, rows: list[dict], recv: int) -> None:
        if self.disk_guard:
            self.dropped_while_guarded += 1
            return
        kept = []
        if rows:
            d = self.dedupe[rows[0]["source"]]
            before_dup, before_intra = d.duplicates, d.intra_message_same_key
            kept = d.filter(rows)
        # OKX REST re-polls mostly return rows already stored: keep a REST page's raw text only when it
        # carries at least one new row (the page count is still in the counters). WS raw is always kept.
        if stream != "okx_rest" or kept:
            self._writer("raw", exchange).write({"recv_ms": recv, "stream": stream, "raw": raw_text}, recv)
        if not rows:
            return
        st = self.stats.c[stream]
        st["duplicates"] += d.duplicates - before_dup
        st["intra_same_key"] += d.intra_message_same_key - before_intra
        for r in kept:
            if r["liquidated_side"] is None:
                st["null_side"] += 1
            self._writer("norm", exchange).write(r, recv)
        st["rows"] += len(kept)

    def session_log(self, event: str, **kw) -> None:
        p = self.root / "runtime" / "sessions.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps({"event": event, "ms": now_ms(), "pid": os.getpid(), "collector_version": COLLECTOR_VERSION,
                                "schema_version": SCHEMA_VERSION, **kw}) + "\n")
            f.flush(); os.fsync(f.fileno())

    # ------------------------------------------------------------------ websocket loops
    async def ws_loop(self, stream: str, exchange: str, url: str, subs: list, ping_payload, parse) -> None:
        st = self.stats.c[stream]
        first = True
        while not self.stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2**22,
                                              user_agent_header=UA, open_timeout=20) as ws:
                    if not first:
                        st["reconnects"] += 1
                    first = False
                    st["connected"] = True
                    for s in subs:
                        await ws.send(json.dumps(s))
                    last_ping = time.time()
                    while not self.stop.is_set():
                        if ping_payload is not None and time.time() - last_ping > PING_S:
                            await ws.send(ping_payload)
                            last_ping = time.time()
                        try:
                            text = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        recv = now_ms()
                        st["raw_msgs"] += 1
                        st["last_msg_ms"] = recv
                        if text == "pong":
                            st["health_ms"] = recv
                            continue
                        try:
                            msg = json.loads(text)
                        except ValueError:
                            st["invalid"] += 1
                            continue
                        health, rows = parse(msg, recv)
                        if health:
                            st["health_ms"] = recv
                        if rows is None:
                            continue
                        # OKX WS pushes every SWAP instrument: keep raw only when BTC-USDT-SWAP is in it.
                        if rows or (stream != "okx_ws" and self._is_liq(stream, msg)):
                            self.emit(stream, exchange, text, rows, recv)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 (record, back off, reconnect)
                st["ws_errors"] += 1
                st["connected"] = False
                self.session_log("ws_error", stream=stream, error=repr(e)[:300])
                await asyncio.sleep(5)
        st["connected"] = False

    @staticmethod
    def _is_liq(stream: str, msg: dict) -> bool:
        if stream == "bybit":
            return str(msg.get("topic", "")).startswith("allLiquidation.")
        if stream == "binance":
            return (msg.get("data") or {}).get("e") == "forceOrder"
        return (msg.get("arg") or {}).get("channel") == "liquidation-orders" and "data" in msg

    def parse_bybit(self, msg, recv):
        if msg.get("op") in ("pong", "ping") or msg.get("ret_msg") == "pong":
            return True, None
        if msg.get("op") == "subscribe":
            return True, None
        return False, P.bybit(msg, recv)

    def parse_binance(self, msg, recv):
        data = msg.get("data") or {}
        if data.get("e") == "markPriceUpdate":
            return True, None
        return False, P.binance(msg, recv)

    def parse_okx_ws(self, msg, recv):
        if "event" in msg:
            return True, None
        return False, P.okx_ws(msg, self.ct_val, recv)

    # ------------------------------------------------------------------ OKX REST (primary)
    async def okx_ct_val(self, client: httpx.AsyncClient) -> None:
        try:
            r = await client.get(OKX_INSTRUMENTS)
            j = r.json()
            self.ct_val = float(j["data"][0]["ctVal"])
            self.session_log("okx_instrument", ctVal=self.ct_val, ctValCcy=j["data"][0].get("ctValCcy"),
                             ctType=j["data"][0].get("ctType"))
        except Exception as e:  # noqa: BLE001
            self.session_log("okx_instrument_error", error=repr(e)[:300])

    async def okx_poll_once(self, client: httpx.AsyncClient) -> int:
        st = self.stats.c["okx_rest"]
        new_rows, after, pages = 0, None, 0
        stop_at = (self.okx_last_max_ts - OKX_OVERLAP_MS) if self.okx_last_max_ts else 0
        max_seen = self.okx_last_max_ts or 0
        while pages < OKX_MAX_PAGES:
            params = {"instType": "SWAP", "uly": "BTC-USDT", "state": "filled", "limit": "100"}
            if after:
                params["after"] = str(after)
            for attempt in range(4):
                try:
                    r = await client.get(OKX_REST, params=params)
                except Exception as e:  # noqa: BLE001
                    st["http_errors"] += 1
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status_code == 429:
                    st["rate_limit_hits"] += 1
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    st["http_errors"] += 1
                    if attempt == 3:
                        raise RuntimeError(f"HTTP {r.status_code}")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} after retries")
            recv = now_ms()
            text = r.text
            payload = json.loads(text)
            if payload.get("code") not in ("0", 0):
                if str(payload.get("code")) == "50011":
                    st["rate_limit_hits"] += 1
                st["http_errors"] += 1
                raise RuntimeError(f"OKX code {payload.get('code')}")
            pages += 1
            rows = P.okx_rest(payload, self.ct_val, recv)
            st["raw_msgs"] += 1
            self.emit("okx_rest", "okx", text, rows, recv)
            if not rows:
                break
            new_rows += len(rows)
            tss = [r_["event_ts_ms"] for r_ in rows]
            max_seen = max(max_seen, max(tss))
            oldest = min(tss)
            if oldest < stop_at:
                break
            after = oldest
            await asyncio.sleep(0.3)
        self.okx_last_max_ts = max_seen or self.okx_last_max_ts
        return pages

    async def okx_rest_loop(self) -> None:
        st = self.stats.c["okx_rest"]
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20) as client:
            await self.okx_ct_val(client)
            while not self.stop.is_set():
                try:
                    pages = await self.okx_poll_once(client)
                    st["connected"] = True
                    st["health_ms"] = now_ms()
                    self.okx_last_success_ms = st["health_ms"]
                    st["last_pages"] = pages
                except Exception as e:  # noqa: BLE001
                    st["connected"] = False
                    self.session_log("okx_rest_error", error=repr(e)[:300])
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=OKX_POLL_S)
                except asyncio.TimeoutError:
                    pass

    # ------------------------------------------------------------------ heartbeat / rotation
    def up_map(self, t: int) -> dict[str, bool]:
        c = self.stats.c
        ok = lambda s: bool(c[s]["connected"]) and t - c[s]["health_ms"] <= HEALTH_WINDOW_MS  # noqa: E731
        return {"bybit": ok("bybit"), "binance": ok("binance"), "okx_rest": ok("okx_rest"), "okx_ws": ok("okx_ws"),
                "okx": ok("okx_rest")}

    async def heartbeat_loop(self) -> None:
        start_marked = (self.root / "START_MARKER.json").exists()
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                pass
            t = now_ms()
            free = S.free_bytes(self.root)
            guard = free < DISK_MIN_FREE
            if guard != self.disk_guard:
                self.session_log("disk_guard", on=guard, free_bytes=free)
            self.disk_guard = guard
            for w in self.writers.values():
                w.rotate_if_stale(t)
                w.sync()
            for d in self.dedupe.values():
                d.prune(t)
            up = self.up_map(t)
            if guard:
                up = {k: False for k in up}
            rec = {"ms": t, "session_start_ms": self.session_start, "pid": os.getpid(), "up": up, "disk_free": free, "disk_guard": guard, "dropped_while_guarded": self.dropped_while_guarded,
                   "rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   "okx_last_success_ms": self.okx_last_success_ms, "ct_val": self.ct_val,
                   "stats": {k: {kk: vv for kk, vv in v.items()} for k, v in self.stats.c.items()},
                   "collector_version": COLLECTOR_VERSION}
            day = datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            p = self.root / "runtime" / f"heartbeat-{day}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a") as f:
                f.write(json.dumps(rec) + "\n")
                f.flush(); os.fsync(f.fileno())
            if not start_marked and up["bybit"] and up["binance"] and up["okx"]:
                self.write_start_marker(t)
                start_marked = True

    def write_start_marker(self, t: int) -> None:
        import hashlib
        src = Path(__file__).parent
        h = hashlib.sha256()
        for f in sorted(src.glob("*.py")):
            h.update(f.name.encode()); h.update(f.read_bytes())
        utc = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        kst = datetime.fromtimestamp(t / 1000 + 9 * 3600, tz=timezone.utc)
        marker = {"name": "LIQUIDATION_FORWARD_V1_START", "ms": t, "utc": utc.isoformat(),
                  "kst": kst.strftime("%Y-%m-%d %H:%M:%S KST"), "collector_version": COLLECTOR_VERSION,
                  "schema_version": SCHEMA_VERSION, "exchanges": list(EXCHANGES),
                  "sources": {"bybit": "bybit_ws", "binance": "binance_ws", "okx": "okx_rest (primary), okx_ws (audit)"},
                  "source_sha256": h.hexdigest(), "rule": "no liquidation data before this timestamp enters the forward canon"}
        p = self.root / "START_MARKER.json"
        tmp = p.with_name(p.name + ".partial")
        tmp.write_text(json.dumps(marker, indent=1))
        os.replace(tmp, p)
        self.session_log("start_marker", **marker)

    # ------------------------------------------------------------------ main
    async def run(self) -> None:
        rec = S.recover_partials(self.root, self.manifest)
        cut = self.session_start - 48 * 3_600_000
        n = 0
        for r in S.iter_norm_rows(self.root, cut):
            if r.get("event_ts_ms", 0) >= cut and r.get("source") in self.dedupe:
                self.dedupe[r["source"]].load(r["dedupe_key"], r["event_ts_ms"])
                n += 1
                if r["source"] == "okx_rest":
                    self.okx_last_max_ts = max(self.okx_last_max_ts or 0, r["event_ts_ms"])
        self.session_log("start", recovered=rec, dedupe_keys_reloaded=n, disk_min_free=DISK_MIN_FREE)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        tasks = [
            asyncio.create_task(self.ws_loop("bybit", "bybit", BYBIT_WS, [{"op": "subscribe", "args": ["allLiquidation.BTCUSDT"]}],
                                             json.dumps({"op": "ping"}), self.parse_bybit)),
            asyncio.create_task(self.ws_loop("binance", "binance", BINANCE_WS, [], None, self.parse_binance)),
            asyncio.create_task(self.ws_loop("okx_ws", "okx", OKX_WS, [{"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]}],
                                             "ping", self.parse_okx_ws)),
            asyncio.create_task(self.okx_rest_loop()),
            asyncio.create_task(self.heartbeat_loop()),
        ]
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for w in self.writers.values():
            w.close()
        self.session_log("stop", reason="signal")


def acquire_lock(root: Path):
    """Single instance per data root: a second collector would interleave writes into the same segments."""
    import fcntl
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    fh = open(root / "runtime" / "collector.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another collector holds {root / 'runtime' / 'collector.lock'}")
    fh.write(str(os.getpid())); fh.flush()
    return fh


def main() -> None:
    root = Path(os.environ.get("LIQFWD_ROOT", "data/research/crypto/liquidation_forward"))
    lock = acquire_lock(root)  # noqa: F841 (held for the process lifetime)
    asyncio.run(Collector(root).run())


if __name__ == "__main__":
    main()
