"""Flow horizons: exact trailing 10 m / 1 h / 4 h / 1 d aggressive buy and sell notional.

The venue stub is built from synthetic *trades*, so every expectation is computed from the raw
trades by brute force and never from the klines the code under test reads.
"""
from __future__ import annotations

import json
import random
import time
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from app.crypto.btc_context_ui_v1 import api as A
from app.crypto.btc_context_ui_v1 import flow_horizon as FH
from app.crypto.market_context_v1 import venue as VN

MIN = 60_000
T0 = 1_791_700_000_000 - 1_791_700_000_000 % MIN       # a minute boundary


def make_trades(first_minute: int, minutes: int, seed: int = 7, skip: set[int] | None = None):
    """[(time_ms, 'BUY'|'SELL', notional Decimal)] with 2-9 trades a minute, ms resolution."""
    rnd = random.Random(seed)
    trades = []
    for m in range(first_minute, first_minute + minutes):
        if skip and m in skip:
            continue
        for _ in range(rnd.randint(2, 9)):
            trades.append((m * MIN + rnd.randrange(MIN), rnd.choice(("BUY", "SELL")),
                           Decimal(rnd.randrange(1_000, 900_000)) / 100))
    return trades


class Venue:
    """Binance klines from raw trades, the way the venue aggregates them (by trade time)."""

    def __init__(self, trades):
        self.trades = trades
        self.calls: list[tuple[int, int]] = []
        self.fail = False

    def rows(self, lo: int, hi: int):
        by_min: dict[int, list] = {}
        for t, side, n in self.trades:
            by_min.setdefault(t - t % MIN, []).append((side, n))
        out = []
        for m in sorted(by_min):
            if lo <= m <= hi:
                q = sum((n for _, n in by_min[m]), Decimal(0))
                b = sum((n for s, n in by_min[m] if s == "BUY"), Decimal(0))
                out.append([m, "0", "0", "0", "0", "0", m + MIN - 1, str(q), len(by_min[m]), "0", str(b), "0"])
        return out

    def __call__(self, request, timeout: float = 0):
        if self.fail:
            raise OSError("venue down")
        q = {k: v[0] for k, v in parse_qs(urlparse(request.full_url).query).items()}
        lo, hi = int(q["startTime"]), int(q["endTime"])
        self.calls.append((lo, hi))
        body = json.dumps(self.rows(lo, hi)[: int(q["limit"])]).encode()

        class R:
            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return R()


def brute(trades, end_ms: int, minutes: int):
    lo = end_ms - minutes * MIN
    buy = sum((n for t, s, n in trades if lo <= t < end_ms and s == "BUY"), Decimal(0))
    sell = sum((n for t, s, n in trades if lo <= t < end_ms and s == "SELL"), Decimal(0))
    return buy, sell


NOW = T0 + 1500 * MIN + 7_000           # 7 s into a minute; minute-in-progress opens at T0+1500m
END = T0 + 1500 * MIN


@pytest.fixture()
def trades():
    return make_trades(T0 // MIN, 1600)


def test_every_horizon_equals_the_sum_of_raw_trades(tmp_path, trades):
    venue = Venue(trades)
    out = FH.run_once(tmp_path, now_ms=NOW, opener=venue)
    assert out["result"] == "UPDATED" and out["end_ms"] == END
    snap = json.loads((tmp_path / FH.SNAPSHOT_NAME).read_text())
    for label, minutes in FH.HORIZONS:
        h = snap["horizons"][label]
        buy, sell = brute(trades, END, minutes)
        assert Decimal(h["buy_notional"]) == buy.quantize(Decimal("0.01"))
        assert Decimal(h["sell_notional"]) == sell.quantize(Decimal("0.01"))
        assert h["buy_pct"] + h["sell_pct"] == 100
        assert h["lean"] == ("BUY" if buy > sell else "SELL")
        assert (h["coverage_seconds"], h["expected_seconds"], h["coverage_ratio"]) == (
            minutes * 60, minutes * 60, 1.0)
        assert h["quality"] == "COMPLETE"


def test_rolling_snapshots_would_double_count_and_the_minute_sum_does_not(trades):
    """60 rolling 60 s windows sampled each second sum ~60x the real notional; whole minutes do not."""
    lo, hi = END - 10 * MIN, END
    rolling = Decimal(0)
    for sample in range(lo // 1000, hi // 1000):                 # one sample a second
        at = sample * 1000
        rolling += sum((n for t, s, n in trades if at - 60_000 < t <= at), Decimal(0))
    real = sum((n for t, s, n in trades if lo <= t < hi), Decimal(0))
    assert rolling > real * 50
    rows = {int(r[0]): (int(r[0]), r[7], r[10]) for r in Venue(trades).rows(lo, hi - 1)}
    view = FH.horizon_view(rows, hi, 10)
    assert Decimal(view["buy_notional"]) + Decimal(view["sell_notional"]) == real.quantize(Decimal("0.01"))


def test_a_missing_minute_withholds_only_the_horizons_it_falls_in(tmp_path):
    gap = END - 30 * MIN                       # inside 1h, 4h and 1d but not 10m
    venue = Venue(make_trades(T0 // MIN, 1600, skip={gap // MIN}))
    FH.run_once(tmp_path, now_ms=NOW, opener=venue)
    h = json.loads((tmp_path / FH.SNAPSHOT_NAME).read_text())["horizons"]
    assert h["10m"]["quality"] == "COMPLETE" and h["10m"]["buy_pct"] is not None
    for label, minutes in (("1h", 60), ("4h", 240), ("1d", 1440)):
        assert h[label]["quality"] == "PARTIAL"
        assert h[label]["buy_pct"] is None and h[label]["lean"] is None
        assert h[label]["coverage_seconds"] == (minutes - 1) * 60
        assert h[label]["coverage_ratio"] == round((minutes - 1) / minutes, 4)


def rows_of(pairs):
    return {T0 + i * MIN: (T0 + i * MIN, str(q), str(b)) for i, (q, b) in enumerate(pairs)}


def test_buy_sell_even_and_zero():
    end = T0 + 10 * MIN
    buy = FH.horizon_view(rows_of([(100, 70)] * 10), end, 10)
    assert (buy["lean"], buy["buy_pct"], buy["sell_pct"]) == ("BUY", 70, 30)
    sell = FH.horizon_view(rows_of([(100, 30)] * 10), end, 10)
    assert (sell["lean"], sell["buy_pct"], sell["sell_pct"]) == ("SELL", 30, 70)
    even = FH.horizon_view(rows_of([(100, 50)] * 10), end, 10)
    assert (even["lean"], even["buy_pct"]) == ("EVEN", 50)
    # 50.4 % rounds to 50 but is still a buy lean: the lean comes from the exact notionals
    close = FH.horizon_view(rows_of([(1000, 504)] * 10), end, 10)
    assert (close["buy_pct"], close["sell_pct"], close["lean"]) == (50, 50, "BUY")
    zero = FH.horizon_view(rows_of([(0, 0)] * 10), end, 10)
    assert zero["quality"] == "UNKNOWN" and zero["lean"] is None and zero["buy_pct"] is None


def test_an_unreadable_minute_counts_as_missing():
    end = T0 + 10 * MIN
    rows = rows_of([(100, 60)] * 10)
    rows[T0 + 3 * MIN] = (T0 + 3 * MIN, "nan", "5")
    rows[T0 + 4 * MIN] = (T0 + 4 * MIN, "10", "20")        # taker buy larger than the total
    assert FH.horizon_view(rows, end, 10)["quality"] == "PARTIAL"


def test_second_run_fetches_only_the_minutes_that_closed(tmp_path, trades):
    venue = Venue(trades)
    FH.run_once(tmp_path, now_ms=NOW, opener=venue)
    first = list(venue.calls)
    assert first and first[0][0] == END - 1440 * MIN
    venue.calls.clear()
    again = FH.run_once(tmp_path, now_ms=NOW + 20_000, opener=venue)
    assert again["result"] == "UP_TO_DATE" and venue.calls == []
    nxt = FH.run_once(tmp_path, now_ms=NOW + MIN, opener=venue)
    assert nxt["fetched"] == 1 and venue.calls == [(END, END + MIN - 1)]
    snap = json.loads((tmp_path / FH.SNAPSHOT_NAME).read_text())
    assert snap["end_ms"] == END + MIN
    buy, sell = brute(trades, END + MIN, 1440)
    assert Decimal(snap["horizons"]["1d"]["buy_notional"]) == buy.quantize(Decimal("0.01"))
    # the series file keeps exactly one day of minutes, not the journal and not more
    series = json.loads((tmp_path / FH.SERIES_NAME).read_text())["rows"]
    assert len(series) == 1440


def test_venue_failure_leaves_the_snapshot_to_age(tmp_path, trades):
    venue = Venue(trades)
    FH.run_once(tmp_path, now_ms=NOW, opener=venue)
    before = (tmp_path / FH.SNAPSHOT_NAME).read_text()
    venue.fail = True
    out = FH.run_once(tmp_path, now_ms=NOW + 5 * MIN, opener=venue)
    assert out["result"] == "VENUE_ERROR"
    assert (tmp_path / FH.SNAPSHOT_NAME).read_text() == before


def test_stale_and_missing_snapshot_publish_no_figures(tmp_path, trades):
    FH.run_once(tmp_path, now_ms=NOW, opener=Venue(trades))
    snap = FH.read_snapshot(tmp_path / FH.SNAPSHOT_NAME)
    live = FH.horizons_section(snap, END + 30_000)
    assert live["live"] is True and live["horizons"]["10m"]["buy_pct"] is not None
    stale = FH.horizons_section(snap, END + 10 * MIN)
    assert stale["live"] is False
    assert all(v["buy_pct"] is None and v["lean"] is None for v in stale["horizons"].values())
    missing = FH.horizons_section(FH.read_snapshot(tmp_path / "nope.json"), END)
    assert missing["live"] is False and set(missing["horizons"]) == {"10m", "1h", "4h", "1d"}


def test_snapshot_route_adds_flow_horizons_and_keeps_every_existing_field(tmp_path, trades, monkeypatch):
    from fastapi.testclient import TestClient
    FH.run_once(tmp_path, now_ms=int(time.time() * 1000), opener=Venue(make_trades(
        (int(time.time() * 1000) // MIN) - 1600, 1601)))
    monkeypatch.setenv(A.FLOW_HORIZONS_ENV, str(tmp_path / FH.SNAPSHOT_NAME))
    body = TestClient(A.app).get("/snapshot").json()
    for key in ("symbol", "server_time_ms", "structure", "liquidity", "flow", "derivatives", "walls",
                "entry"):
        assert key in body
    assert set(body["flow_horizons"]) == {"live", "end_ms", "horizons"}
    assert body["flow_horizons"]["live"] is True
    assert set(body["flow_horizons"]["horizons"]) == {"10m", "1h", "4h", "1d"}
    monkeypatch.delenv(A.FLOW_HORIZONS_ENV)
    off = TestClient(A.app).get("/snapshot").json()
    assert off["flow_horizons"]["live"] is False


def test_only_the_existing_public_klines_path_is_used():
    assert "/fapi/v1/klines" in VN.ALLOWED_PATHS
    with pytest.raises(VN.VenueError):
        VN.guard("/fapi/v1/account")
