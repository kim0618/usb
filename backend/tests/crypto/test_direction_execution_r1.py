"""Direction Execution R1 tests (contract docs/crypto/direction_execution_r1/CONTRACT_DIRECTION_EXECUTION_R1_V1.md)."""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data as PD
from app.crypto.research.ptm_r1 import r1_train as R
from app.crypto.research.direction_execution_r1 import execution as X

N = 1000


def market(p=100.0, funding=()):
    o = np.full(N, p)
    px = PD.Prices(ts=PD.TRAIN_START_MS + np.arange(N, dtype=np.int64) * 60_000, open=o.copy(), high=o.copy(),
                   low=o.copy(), close=o.copy())
    return R.make_market(px, [(PD.TRAIN_START_MS + i * 60_000, r) for i, r in funding])


def test_taker_matches_r0_simulate():
    mk = market(funding=((150, 0.0003),))
    mk.px.open[100 + 240] = 101.0
    mk.px.high[200] = 102.0
    mk.px.low[120] = 99.0
    for d in (1, -1):
        x = X.execute(mk, 100, d, "TAKER", "BASE")
        s = R.simulate(mk, 100, d, 340, [])
        for k in ("gross", "net", "mfe", "mae"):
            assert x[k] == pytest.approx(s[k], abs=1e-15)


def test_limit_prices_and_triggers():
    assert X.limit_price("M1", 100.0, 1) == pytest.approx(99.9) and X.limit_price("M1", 100.0, -1) == pytest.approx(100.1)
    assert X.limit_price("M2", 100.0, 1) == 100.0
    assert X.trigger(99.9, 1, "OPTIMISTIC") == 99.9 and X.trigger(99.9, 1, "BASE") == pytest.approx(99.8)
    assert X.trigger(100.0, -1, "CONSERVATIVE") == pytest.approx(100.01)


def test_fill_models_ordering_and_window():
    mk = market(p=30000.0)                        # BTC-like price: 1 bp = 3 USDT = 30 ticks
    L = 29999.9
    mk.px.low[102] = L                            # touches the M1 LONG limit only
    assert X.maker_fill(mk.px, 100, 1, L, "OPTIMISTIC") == 102
    assert X.maker_fill(mk.px, 100, 1, L, "BASE") is None
    mk.px.low[103] = L - 0.1
    assert X.maker_fill(mk.px, 100, 1, L, "BASE") == 103
    assert X.maker_fill(mk.px, 100, 1, L, "CONSERVATIVE") is None
    mk.px.low[104] = L * (1 - 1e-4)
    assert X.maker_fill(mk.px, 100, 1, L, "CONSERVATIVE") == 104
    mk.px.low[102:105] = 30000.0
    mk.px.low[105] = 20000.0                      # outside the 5 minute window (100..104)
    assert X.maker_fill(mk.px, 100, 1, L, "OPTIMISTIC") is None
    mk.px.high[101] = 30000.1 + 0.1               # SHORT mirrored: limit 30000.1, BASE trigger 30000.2
    assert X.maker_fill(mk.px, 100, -1, 30000.1, "BASE") == 101


def test_unfilled_and_fallback_and_fees():
    mk = market()
    mk.px.open[340] = 102.0
    u = X.execute(mk, 100, 1, "M1", "BASE")
    assert not u["filled"] and u["net"] == 0.0
    f = X.execute(mk, 100, 1, "M3", "BASE")              # falls back to taker at open[105]
    assert f["filled"] and not f["maker"] and f["fill_minute"] == 105
    assert f["fee"] == pytest.approx(X.TAKER + X.TAKER * 1.02)
    mk.px.low[101] = 99.0
    m = X.execute(mk, 100, 1, "M1", "BASE")
    assert m["maker"] and m["fill_price"] == pytest.approx(99.9)
    assert m["gross"] == pytest.approx(102 / 99.9 - 1)
    assert m["fee"] == pytest.approx(X.MAKER + X.TAKER * (102 / 99.9))
    assert m["net"] == pytest.approx(m["gross"] - m["fee"] - X.HALF_SPREAD)


def test_funding_from_fill_minute():
    mk = market(funding=((102, 0.001), (200, 0.002)))
    mk.px.open[340] = 100.0
    mk.px.low[103] = 99.0
    m = X.execute(mk, 100, 1, "M1", "BASE")              # fills at 103, after the 102 settlement
    assert m["funding"] == pytest.approx(0.002 * 100 / 99.9)
    t = X.execute(mk, 100, -1, "TAKER", "BASE")
    assert t["funding"] == pytest.approx(-(0.001 + 0.002))


def test_buckets():
    assert X.bucket_of(0.5) == 0 and X.bucket_of(0.5999) == 1 and X.bucket_of(0.7) == 4 and X.bucket_of(1.0) == 4
    assert X.bucket_label(4) == "0.70-1.00"
