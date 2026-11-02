import json
from pathlib import Path

import pytest

from app.crypto.tsmom.signal import H4_MS, weights

RESEARCH = Path(__file__).resolve().parents[3] / "data/research/crypto/btc_tsmom_v1"


def _bars(closes, start=1_600_000_000_000 - 1_600_000_000_000 % (24 * 3600 * 1000)):
    return [(start + i * H4_MS, c) for i, c in enumerate(closes)]


def test_rising_series_is_fully_long():
    bars = _bars([100 + i for i in range(6 * 45)])
    w = weights(bars)
    assert w[-1] is not None and w[-1].weight == 1.0 and w[-1].longs == 36


def test_falling_series_is_flat():
    bars = _bars([1000 - i for i in range(6 * 45)])
    assert weights(bars)[-1].weight == 0.0


def test_not_ready_before_40_days():
    bars = _bars([100 + i for i in range(6 * 30)])
    assert weights(bars)[-1] is None


@pytest.mark.skipif(not (RESEARCH / "klines_4h.json").exists(), reason="research archive not present")
def test_matches_research_v3_weights():
    """연구 스크립트의 weight(i)와 봉 단위로 같아야 한다(연구 weight(i)는 봉 i에 적용 = 여기서는 봉 i-1 마감 결정)."""
    K = json.load(open(RESEARCH / "klines_4h.json"))
    bars = [(k[0], float(k[4])) for k in K]
    mine = weights(bars)
    ns = {}
    src = (RESEARCH / "run_tsmom_v3.py").read_text(encoding="utf-8").split("out = {")[0]
    cwd = Path.cwd()
    import os
    os.chdir(RESEARCH)
    try:
        exec(src, ns)
    finally:
        os.chdir(cwd)
    checked = 0
    for i in range(1, len(bars)):
        r = ns["weight"](i)
        m = mine[i - 1]
        assert (r is None) == (m is None), i
        if r is not None:
            assert abs(r - m.weight) < 1e-12, i
            checked += 1
    assert checked > 15000
