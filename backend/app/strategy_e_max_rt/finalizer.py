"""E-RT2: single-app-key full-universe 09:24-cutoff finalization over two independent Kiwoom lanes.

Measured (E-RT2 probes): the REST limit is per API ID (4 IDs x 5/s ran concurrently with no 429),
and two chart IDs return minute-stamped intraday data whose per-minute OHLCV is identical:
``usa06011`` (1-minute bars, 100 per page) and ``usa06010`` (ticks, 100 per page, aggregated here to
minutes). Quotes / rankings are snapshots and are never a finalization lane.

Schedule (ET), shared by the live dry run and the tests:

* 04:00 - 09:20:40  rolling cache: every symbol's latest minute page on the minute lane, one cycle
  about every 9 minutes; only complete minutes (bar start + 1 min <= fetch time) are kept;
* 09:20:40 - 09:25:00  the tick-lane group is refreshed last on the minute lane, in finalization order;
* 09:25:00 -> T1  finalization in parallel: the minute-lane group takes one minute page (enough for
  100 minutes), the tick-lane group takes tick pages until they reach the symbol's cached minute.
  Only minutes <= 09:24 are merged; a page fetched at or after 09:25:00 makes 09:24 complete.

Lane groups are deterministic and outcome-free: symbols ordered by D-1 dollar volume (known before
the session); the most liquid go to the minute lane (one call regardless of prints), the rest to the
tick lane. Every symbol ends with ``last_complete_minute``, ``data_source``, ``finalized_at`` and a
contiguity verdict; a symbol not finalized, not contiguous or finished after the deadline is stale.
Kiwoom calls stop at the deadline (09:29:45), before A's first call at the 09:30 open.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.timestamps import minute_timestamp

ET = ZoneInfo("America/New_York")
MINUTE_API, TICK_API = "usa06011", "usa06010"
PREMARKET_START = dtime(4, 0)
CUTOFF_LAST = dtime(9, 24)
REFRESH_B_AT = dtime(9, 20, 40)
FINALIZE_AT = dtime(9, 25, 0)
DEADLINE = dtime(9, 29, 45)
MAX_TICK_PAGES = 30


class ChartLaneClient(KiwoomMarketDataClient):
    """The market-data client with the read-only tick chart added; order paths stay unreachable."""

    ALLOWED_ENDPOINTS = KiwoomMarketDataClient.ALLOWED_ENDPOINTS | {(TICK_API, "/api/us/chart")}


def kiwoom_code(symbol: str, listing=None) -> str:
    """The Kiwoom code of a Massive ticker: the ticker itself when Kiwoom lists it (e.g. "BH.A"),
    otherwise base + lowercase class for share classes (Kiwoom lists "BRK.B" as "BRKb")."""
    if listing is not None and symbol in listing:
        return symbol
    base, dot, klass = symbol.partition(".")
    return f"{base}{klass.lower()}" if dot and len(klass) == 1 else symbol


def _minute_key(ts: datetime) -> int:
    return ts.hour * 60 + ts.minute


@dataclass
class SymbolCache:
    symbol: str
    exchange: str
    code: str = ""
    bars: dict[int, list[float]] = field(default_factory=dict)       # minute -> [o, h, l, c, v]
    complete_through: int | None = None                             # last complete minute known
    first_fetch: datetime | None = None
    calls: int = 0
    finalized_at: datetime | None = None
    data_source: str | None = None
    contiguous: bool | None = None
    final_pages: int = 0
    ticks_read: int = 0
    last_relevant_tick: str | None = None
    error: str | None = None

    def merge_minutes(self, rows: Sequence[Mapping[str, Any]], fetched_at: datetime, session: date,
                      cutoff: int | None = None) -> int | None:
        """Keep complete minutes of ``session`` (<= cutoff); return the oldest minute on the page."""
        oldest = None
        for row in rows:
            ts = minute_timestamp(row)
            if ts.date() != session:
                oldest = -1 if oldest is None else min(oldest, -1)
                continue
            m = _minute_key(ts)
            oldest = m if oldest is None else min(oldest, m)
            if ts + timedelta(minutes=1) > fetched_at or m < 4 * 60 or (cutoff is not None and m > cutoff):
                continue
            self.bars[m] = [float(row["open_pric"]), float(row["high_pric"]), float(row["low_pric"]),
                            float(row["cur_prc"]), float(row["trde_qty"])]
        last_complete = _minute_key(fetched_at) - 1
        if cutoff is not None:
            last_complete = min(last_complete, cutoff)
        return oldest if oldest is not None else last_complete

    def merge_ticks(self, ticks: Sequence[Mapping[str, Any]], from_minute: int, session: date, cutoff: int) -> None:
        agg: dict[int, list[float]] = {}
        for row in reversed(ticks):                                  # oldest first
            ts = minute_timestamp(row)
            if ts.date() != session:
                continue
            m = _minute_key(ts)
            if m < from_minute or m > cutoff:
                continue
            p, o, h, l = (float(row[k]) for k in ("cur_prc", "open_pric", "high_pric", "low_pric"))
            if m not in agg:
                agg[m] = [o, h, l, p, 0.0]
            a = agg[m]
            a[1], a[2], a[3] = max(a[1], h), min(a[2], l), p
            a[4] += float(row["trde_qty"])
        for m in range(from_minute, cutoff + 1):
            self.bars.pop(m, None)
        self.bars.update(agg)


@dataclass
class Lane:
    name: str
    api_id: str
    client: KiwoomMarketDataClient
    calls: int = 0
    errors: int = 0
    latencies: list[float] = field(default_factory=list)

    def page(self, symbol: str, exchange: str, continuation=None):
        body = {"stex_tp": exchange, "stk_cd": symbol, "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}
        start = time.monotonic()
        try:
            return self.client.request(self.api_id, "/api/us/chart", body, continuation=continuation)
        except Exception:
            self.errors += 1
            raise
        finally:
            self.calls += 1
            self.latencies.append(time.monotonic() - start)


def split_lanes(symbols_by_liquidity: Sequence[str], minute_share: int) -> tuple[list[str], list[str]]:
    """Most liquid ``minute_share`` symbols -> minute lane; the rest -> tick lane (deterministic)."""
    return list(symbols_by_liquidity[:minute_share]), list(symbols_by_liquidity[minute_share:])


def hash_lanes(symbols: Sequence[str]) -> tuple[list[str], list[str]]:
    """Outcome-free partition by a stable hash: even sha256 -> minute lane, odd -> tick lane;
    each shard ordered by the hash so the order is fixed and unrelated to any market value."""
    import hashlib
    keyed = sorted((hashlib.sha256(s.encode()).hexdigest(), s) for s in symbols)
    return ([s for h, s in keyed if int(h, 16) % 2 == 0], [s for h, s in keyed if int(h, 16) % 2 == 1])


def finalize_minute(lane: Lane, cache: SymbolCache, session: date, now: Callable[[], datetime]) -> None:
    cutoff = 9 * 60 + 24
    page = lane.page(cache.code or cache.symbol, cache.exchange)
    cache.calls += 1
    cache.final_pages += 1
    fetched = now()
    oldest = cache.merge_minutes(page.body.get("result_list", []), fetched, session, cutoff)
    need = cache.complete_through if cache.complete_through is not None else 4 * 60 - 1
    cache.contiguous = fetched.time() >= FINALIZE_AT and (oldest is None or oldest <= need + 1 or oldest == -1)
    cache.finalized_at, cache.data_source = fetched, "KIWOOM_" + MINUTE_API


def finalize_ticks(lane: Lane, cache: SymbolCache, session: date, now: Callable[[], datetime]) -> None:
    cutoff = 9 * 60 + 24
    need_from = (cache.complete_through + 1) if cache.complete_through is not None else 4 * 60
    ticks, continuation, reached = [], None, False
    for _ in range(MAX_TICK_PAGES):
        page = lane.page(cache.code or cache.symbol, cache.exchange, continuation)
        cache.calls += 1
        cache.final_pages += 1
        rows = page.body.get("result_list", [])
        ticks.extend(rows)
        if rows:
            last = minute_timestamp(rows[-1])
            if last.date() < session or _minute_key(last) < need_from:
                reached = True
        if reached or not page.continuation or not rows:
            reached = reached or not page.continuation
            break
        continuation = page
    fetched = now()
    cache.ticks_read = len(ticks)
    relevant = [r for r in ticks if minute_timestamp(r).date() == session and _minute_key(minute_timestamp(r)) <= cutoff]
    cache.last_relevant_tick = relevant[0]["cntr_tm"] if relevant else None
    cache.merge_ticks(ticks, need_from, session, cutoff)
    cache.contiguous = reached and fetched.time() >= FINALIZE_AT
    cache.finalized_at, cache.data_source = fetched, "KIWOOM_" + TICK_API


def refresh(lane: Lane, cache: SymbolCache, session: date, now: Callable[[], datetime],
            catch_up_pages: int = 6) -> None:
    """Latest minute page; on a symbol's first refresh (worker started late or restarted) it pages
    back until 04:00 of the session is covered, so a late start is caught up, not treated as empty."""
    page = lane.page(cache.code or cache.symbol, cache.exchange)
    cache.calls += 1
    fetched = now()
    oldest = cache.merge_minutes(page.body.get("result_list", []), fetched, session)
    if cache.complete_through is None:
        for _ in range(catch_up_pages):
            if oldest is None or oldest == -1 or oldest <= 4 * 60 or not page.continuation:
                break
            page = lane.page(cache.code or cache.symbol, cache.exchange, page)
            cache.calls += 1
            older = cache.merge_minutes(page.body.get("result_list", []), fetched, session)
            oldest = older if older is not None else oldest
        if not (oldest is None or oldest == -1 or oldest <= 4 * 60 or not page.continuation):
            cache.first_fetch = cache.first_fetch or fetched
            return                                          # 04:00 not reached: stays incomplete
    through = min(_minute_key(fetched) - 1, 9 * 60 + 24)
    need = cache.complete_through if cache.complete_through is not None else 4 * 60 - 1
    if cache.complete_through is None or oldest is None or oldest <= need + 1 or oldest == -1:
        cache.complete_through = through                          # contiguous extension
    cache.first_fetch = cache.first_fetch or fetched


def audit(caches: Mapping[str, SymbolCache], deadline: datetime) -> dict[str, Any]:
    stale = [s for s, c in caches.items()
             if c.finalized_at is None or not c.contiguous or c.finalized_at > deadline]
    return {"symbols": len(caches), "stale": len(stale), "stale_examples": sorted(stale)[:20],
            "evidence_after_cutoff": sum(1 for c in caches.values() if any(m > 9 * 60 + 24 for m in c.bars))}
