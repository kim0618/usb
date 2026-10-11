"""Public Binance USD-M market data (S4), read by time range and never by "latest", so every value
can be taken as of a past instant. No key, GET only.

Rate safety comes first. On the production host this process shares its IP with the trading
terminal, whose Binance reads must never be crowded out:
* every series is read in chunks sized to its interval (one request per chunk) and cached on disk;
  a chunk that is still filling is cached too, and only its missing tail is asked for next time;
* a run may send at most `budget` requests, spaced by `spacing_s`; past that it raises
  `BudgetExhausted`, the caller defers, and the next run continues from the cache;
* HTTP 429 / 418 raise `VenueBackoff` with the venue's Retry-After, which the caller persists so no
  run asks again before it expires.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

BASE = "https://fapi.binance.com"
DAY_MS = 86_400_000
MINUTE_MS = 60_000
INTERVAL_MS = {"1m": 60_000, "1h": 3_600_000, "4h": 14_400_000, "1d": DAY_MS}
#: One request per chunk: chunk length per series, chosen so a chunk is <= 1 500 rows.
CHUNK_MS = {"1m": DAY_MS, "1h": 30 * DAY_MS, "4h": 120 * DAY_MS, "1d": 720 * DAY_MS,
            "funding": 180 * DAY_MS, "oi": DAY_MS}

Fetch = Callable[[str, dict], list]


class BudgetExhausted(RuntimeError):
    """This run's request budget is spent. Everything fetched so far is cached."""


class VenueBackoff(RuntimeError):
    def __init__(self, status: int, retry_after_s: int) -> None:
        super().__init__(f"HTTP {status}, retry after {retry_after_s}s")
        self.status = status
        self.retry_after_s = retry_after_s


def http_get(path: str, params: dict, timeout: float = 10.0) -> list:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "usb-manual-research/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (418, 429):
            retry = int(exc.headers.get("Retry-After") or (120 if exc.code == 429 else 600))
            raise VenueBackoff(exc.code, retry) from None
        raise


class Market:
    """Bars as tuples (open_ms, open, high, low, close, volume), sorted, closed bars only."""

    def __init__(self, cache_dir: Path | None, fetch: Fetch = http_get, now_ms: int | None = None,
                 symbol: str = "BTCUSDT", budget: int | None = None, spacing_s: float = 0.0) -> None:
        self.cache_dir = cache_dir
        self.fetch = fetch
        self.now_ms = now_ms
        self.symbol = symbol
        self.budget = budget
        self.spacing_s = spacing_s
        self.requests = 0
        self._mem: dict[tuple, list] = {}

    def _get(self, path: str, params: dict) -> list:
        if self.budget is not None and self.requests >= self.budget:
            raise BudgetExhausted(f"{self.requests} requests")
        if self.requests and self.spacing_s:
            time.sleep(self.spacing_s)
        self.requests += 1
        return self.fetch(path, params)

    # -- chunked, tail-extending cache ---------------------------------------------------------
    def _chunk(self, kind: str, key: str, chunk_start: int, chunk_len: int, step: int,
               getter: Callable[[int, int], list]) -> list:
        mk = (kind, key, chunk_start)
        if mk in self._mem:
            return self._mem[mk]
        chunk_end = chunk_start + chunk_len - 1
        now = self.now_ms if self.now_ms is not None else int(time.time() * 1000)
        path = (self.cache_dir / f"{kind}_{key}_{chunk_start}.json") if self.cache_dir else None
        cached: dict = {"rows": [], "complete": False}
        if path is not None and path.exists():
            try:
                cached = json.loads(path.read_text())
            except ValueError:
                cached = None
            if not isinstance(cached, dict) or not isinstance(cached.get("rows"), list):
                cached = {"rows": [], "complete": False}      # foreign or old format: refetch
        rows = cached["rows"]
        if not cached.get("complete"):
            # Only the part that is not cached yet, and never beyond what has closed.
            start = (rows[-1][0] + step) if rows else chunk_start
            end = min(chunk_end, now - 1)
            if start <= end:
                new = getter(start, end)
                seen = {r[0] for r in rows}
                rows = sorted(rows + [r for r in new if r[0] not in seen])
            complete = chunk_end + step + 2 * MINUTE_MS <= now
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"rows": rows, "complete": complete}))
                tmp.replace(path)
        self._mem[mk] = rows
        return rows

    def _range(self, kind: str, key: str, start_ms: int, end_ms: int, chunk_len: int, step: int,
               getter: Callable[[int, int], list]) -> list:
        out = []
        c = (start_ms // chunk_len) * chunk_len
        while c <= end_ms:
            out.extend(self._chunk(kind, key, c, chunk_len, step, getter))
            c += chunk_len
        return out

    def _paged(self, path: str, base: dict, start: int, end: int, step_ms: int, limit: int,
               ts_key=lambda r: int(r[0])) -> list:
        rows: list = []
        cursor = start
        while cursor <= end:
            want = max(1, min(limit, (end - cursor) // step_ms + 2))
            page = self._get(path, {**base, "startTime": cursor, "endTime": end, "limit": want})
            if not page:
                break
            rows.extend(page)
            last = ts_key(page[-1])
            if len(page) < want or last + step_ms > end:
                break
            cursor = last + step_ms
        return rows

    # -- series --------------------------------------------------------------------------------
    def klines(self, interval: str, start_ms: int, end_ms: int, kind: str = "klines") -> list[tuple]:
        """Closed bars whose open time is in [start, end] and whose close is <= now."""
        step = INTERVAL_MS[interval]
        path = {"klines": "/fapi/v1/klines", "mark": "/fapi/v1/markPriceKlines",
                "index": "/fapi/v1/indexPriceKlines"}[kind]
        sym = {"pair": self.symbol} if kind == "index" else {"symbol": self.symbol}

        def get(a: int, b: int) -> list:
            raw = self._paged(path, {**sym, "interval": interval}, a, b, step, 1500)
            return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                    for r in raw]
        rows = self._range(kind, interval, start_ms, end_ms, CHUNK_MS[interval], step, get)
        lim = self.now_ms if self.now_ms is not None else 1 << 62
        out = {}
        for r in rows:
            if start_ms <= r[0] <= end_ms and r[0] + step <= lim:
                out[r[0]] = tuple(r)
        return [out[k] for k in sorted(out)]

    def funding(self, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        def get(a: int, b: int) -> list:
            raw = self._paged("/fapi/v1/fundingRate", {"symbol": self.symbol}, a, b, 28_800_000, 1000,
                              ts_key=lambda r: int(r["fundingTime"]))
            return [[int(r["fundingTime"]), float(r["fundingRate"])] for r in raw]
        rows = self._range("funding", "8h", start_ms, end_ms, CHUNK_MS["funding"], 1, get)
        return sorted({(int(t), float(v)) for t, v in rows if start_ms <= t <= end_ms})

    def open_interest(self, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        """5m statistics. Binance serves only the last 30 days; an older chunk is left empty."""
        horizon = (self.now_ms or int(time.time() * 1000)) - 29 * DAY_MS

        def get(a: int, b: int) -> list:
            if b < horizon:
                return []
            raw = self._paged("/futures/data/openInterestHist",
                              {"symbol": self.symbol, "period": "5m"}, max(a, horizon), b, 300_000, 500,
                              ts_key=lambda r: int(r["timestamp"]))
            return [[int(r["timestamp"]), float(r["sumOpenInterestValue"])] for r in raw]
        rows = self._range("oi", "5m", start_ms, end_ms, CHUNK_MS["oi"], 300_000, get)
        return sorted({(int(t), float(v)) for t, v in rows if start_ms <= t <= end_ms})

    def agg_bars_15s(self, end_ms: int, n: int = 20) -> list[tuple]:
        """The n closed 15 s bars ending at or before end_ms, from aggTrades (the venue serves only
        the last two days). Not cached."""
        stop = (end_ms // 15_000) * 15_000
        start = stop - n * 15_000
        raw: list = []
        cursor = start
        while cursor < stop:
            page = self._get("/fapi/v1/aggTrades", {"symbol": self.symbol, "startTime": cursor,
                                                    "endTime": stop - 1, "limit": 1000})
            if not page:
                break
            raw.extend(page)
            last = int(page[-1]["T"])
            if len(page) < 1000:
                break
            cursor = last + 1
        bars: dict[int, list] = {}
        for t in raw:
            ts, p, q = int(t["T"]), float(t["p"]), float(t["q"])
            if not (start <= ts < stop):
                continue
            k = (ts // 15_000) * 15_000
            b = bars.get(k)
            if b is None:
                bars[k] = [k, p, p, p, p, q]
            else:
                b[2] = max(b[2], p); b[3] = min(b[3], p); b[4] = p; b[5] += q
        out, prev = [], None
        for k in range(start, stop, 15_000):
            b = bars.get(k)
            if b is None:
                if prev is None:
                    continue
                b = [k, prev, prev, prev, prev, 0.0]
            prev = b[4]
            out.append(tuple(b))
        return out
