"""Expired chart pages are dropped instead of waiting for 48 newer keys to push them out.

Dropping them must not change a single response: an expired page was never served again anyway.
"""
from __future__ import annotations

import pytest

from app.crypto.hot_coins.market import BinanceChartHistory
from app.crypto.terminal.chart_history import CACHE_TTL_S, BybitChartHistory

MINUTE = 60_000


def _bybit_page(path, params):
    end = params.get("end", 1_800_000_000_000)
    end -= end % MINUTE
    rows = [[str(end - i * MINUTE), "1", "2", "0.5", "1.5", "10"] for i in range(params["limit"])]
    return {"retCode": 0, "result": {"list": rows}}


def _binance_page(path, params):
    end = params.get("endTime", 1_800_000_000_000)
    end -= end % MINUTE
    return [[end - i * MINUTE, "1", "2", "0.5", "1.5", "10"] for i in range(params["limit"])][::-1]


@pytest.mark.parametrize("factory,page,symbol", [
    (BybitChartHistory, _bybit_page, "BTCUSDT"),
    (BinanceChartHistory, _binance_page, "MAGICUSDT"),
])
def test_expired_pages_are_dropped_and_fresh_ones_still_served(factory, page, symbol) -> None:
    now = [1000.0]
    calls = []

    def request(path, params):
        calls.append(params)
        return page(path, params)

    history = factory(request, clock=lambda: now[0])
    first = history.get("1m", 500, symbol=symbol)
    history.get("1h", 200, symbol=symbol)
    assert len(history._cache) == 2

    # Inside the TTL: served from the cache, same object, no request.
    now[0] += CACHE_TTL_S / 2
    before = len(calls)
    assert history.get("1m", 500, symbol=symbol) is first
    assert len(calls) == before

    # Past the TTL: any access drops both expired pages; only the page asked for comes back.
    now[0] += CACHE_TTL_S
    again = history.get("1m", 500, symbol=symbol)
    assert again == first
    assert list(history._cache) == [(symbol, "1m", None, 500)]
