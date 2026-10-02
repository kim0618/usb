"""The historical parity gate, as a test.

The full gate is `scripts/c1_parity.py`, which replays the whole 2021-03-04 .. 2026-09-23 sample
window - 2,921,760 bars - through the production engine and compares every bar against the frozen
research arrays. That takes minutes and needs the research data, so what runs here by default is
one 45-day slice of the same comparison, which is enough to catch a change in any of the three
conditions or in the 4 h arithmetic. Set `C1_PARITY_FULL=1` to widen it.

Skipped when the research data is absent, which is the case on the server snapshot.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
pytest.importorskip("numpy")
if not (REPO / "data/runtime/crypto/d5/grid_v1.npz").exists():
    pytest.skip("research grid not present", allow_module_level=True)

import numpy as np

from app.crypto.c1 import contract as K
from app.crypto.c1.engine import C1Engine
from app.crypto.c1.grid import Bar, build_grid
from app.crypto.c1.shadow import open_shadow, settle
from app.crypto.research import dataset, features as F, long_horizon as LH
from app.crypto.research import derivatives_flow as DF

DAY = 86_400_000
WARMUP_DAYS = 35
VOL_LABEL = {0: "HIGH", 1: "MID", 2: "LOW", -1: "UNKNOWN"}


@pytest.fixture(scope="module")
def research():
    g = dataset.load()
    ts = g["ts"]
    bounds = [LH.ms(LH.SAMPLE_START)] + [LH.ms(d) for d in LH.FOLD_STARTS] + [LH.ms(LH.END)]
    f1 = (ts >= bounds[0]) & (ts < bounds[1])
    reg = F.regime_labels(g, f1)
    x = DF.load_inputs(g)
    f5 = DF.features5(x)
    s1 = DF.to_1m(f5["spot_basis_bybit"], x["B"], ts)
    bucket = F.bucketize(s1, len(ts) // F.DAY_BARS)
    with np.errstate(invalid="ignore", divide="ignore"):
        oi_change = np.log(g["oi"] / F._lag(g["oi"], 60))
    mask = (bucket == 0) & (oi_change < 0) & (reg["vol"] == 0)
    targets = F.targets(g, K.OFFICIAL_HORIZON_MIN)
    net = DF.net_values(targets, "LONG", K.TAKER_RATE,
                        {"BASE": K.MICRO_BASE_FRAC, "STRESS": K.MICRO_STRESS_FRAC})
    spot_ts, spot_close = DF.binance_spot()
    oi_raw = dataset._read("open_interest_5m", ["open_interest"])
    return {"g": g, "ts": ts, "s1": s1, "bucket": bucket, "oi_change": oi_change,
            "vol": reg["vol"], "mask": mask, "targets": targets, "net": net,
            "funding": list(zip(g["funding_ts"].tolist(), g["funding_rate"].tolist())),
            "spot": list(zip(spot_ts.tolist(), spot_close.tolist())),
            "oi_rows": list(zip(oi_raw["timestamp_ms"].tolist(),
                                oi_raw["open_interest"].tolist())),
            "oi_raw_ts": oi_raw["timestamp_ms"]}


def _replay(research, start: str, end: str):
    g, ts = research["g"], research["ts"]
    start_ms = int(np.datetime64(start, "ms").astype(np.int64))
    end_ms = int(np.datetime64(end, "ms").astype(np.int64))
    load_from = start_ms - WARMUP_DAYS * DAY
    lo = int(np.searchsorted(ts, load_from, "left"))
    hi = min(int(np.searchsorted(ts, end_ms, "left")) + K.OFFICIAL_HORIZON_MIN + 2, len(ts))
    bars = [Bar(ts_ms=int(t), open=float(o), high=float(h), low=float(l), close=float(c))
            for t, o, h, l, c in zip(ts[lo:hi], g["open"][lo:hi], g["high"][lo:hi],
                                     g["low"][lo:hi], g["close"][lo:hi]) if np.isfinite(c)]
    s_lo = int(np.searchsorted(np.array([row[0] for row in research["spot"]]),
                               load_from - 600_000, "left"))
    spot = [row for row in research["spot"][s_lo:] if row[0] < end_ms]
    o_lo = int(np.searchsorted(research["oi_raw_ts"], load_from - 2 * 3_600_000, "left"))
    oi_rows = [row for row in research["oi_rows"][o_lo:] if row[0] < end_ms]
    grid = build_grid(bars, spot, oi_rows, start_ms=int(ts[lo]), end_ms=int(ts[hi - 1]))
    engine = C1Engine()
    return grid, engine, lo, grid.index_of(start_ms), grid.index_of(end_ms)


def _window() -> tuple[str, str]:
    if os.environ.get("C1_PARITY_FULL"):
        return "2021-03-04", "2026-09-23"
    # A window that contains events in quantity: 2022 Q2, inside fold F2.
    return "2022-05-01", "2022-06-15"


def test_every_bar_of_the_window_matches_the_frozen_research(research) -> None:
    grid, engine, lo, begin, stop = _replay(research, *_window())
    compared = events = 0
    worst_net = 0.0
    for index, evaluation, signal in engine.walk(grid, start_index=begin, stop_index=stop):
        ref = lo + index - grid.index_of(int(research["ts"][lo]))
        assert research["ts"][ref] == grid.ts(index)
        compared += 1
        features = evaluation.features
        assert bool(evaluation.on) == bool(research["mask"][ref]), f"mask differs at {ref}"
        assert features.s1_bucket == int(research["bucket"][ref]), f"bucket differs at {ref}"
        assert features.vol_regime == VOL_LABEL[int(research["vol"][ref])]
        reference_s1 = research["s1"][ref]
        if np.isfinite(reference_s1):
            assert features.s1 == reference_s1
        else:
            assert math.isnan(features.s1)
        reference_oi = research["oi_change"][ref]
        if np.isfinite(reference_oi):
            assert features.oi_change_1h == reference_oi
        else:
            assert math.isnan(features.oi_change_1h)
        if signal is not None:
            events += 1
            trade = open_shadow(signal, grid, research["funding"])
            settle(trade, signal, grid, research["funding"])
            assert trade.status == "SETTLED"
            assert trade.entry_price == float(research["g"]["open"][ref + 1])
            assert trade.exit_price == float(
                research["g"]["open"][ref + 1 + K.OFFICIAL_HORIZON_MIN])
            assert trade.gross_return == float(research["targets"]["long"][ref])
            worst_net = max(worst_net,
                            abs(trade.net_return - float(research["net"]["VIP0_BASE"][ref])))
            assert trade.mfe == pytest.approx(float(research["targets"]["mfe_long"][ref]), abs=1e-15)
            assert trade.mae == pytest.approx(float(research["targets"]["mae_long"][ref]), abs=1e-15)
    assert compared > 50_000
    assert events > 0
    # The cost is summed in a different order than the study's vectorised form; the residual is
    # float rounding, four-hundred-thousandths of a basis point, and cannot reorder anything.
    assert worst_net < 1e-15


def test_the_engine_produces_one_signal_per_contiguous_research_event(research) -> None:
    """The study's sample count and the engine's signal count measure different things, and this
    is the relationship between them: one signal per run of true bars."""
    grid, engine, lo, begin, stop = _replay(research, *_window())
    mask = research["mask"]
    offset = lo - grid.index_of(int(research["ts"][lo]))
    expected = 0
    previous = False
    for index in range(begin, stop):
        now = bool(mask[index + offset])
        if now and not previous:
            expected += 1
        previous = now
    signals = list(engine.advance(grid, start_index=begin, stop_index=stop))
    assert len(signals) == expected > 0
