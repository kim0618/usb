"""E2 parity: the operational C1x against the research function it was transcribed from.

`c1_exit.e2.trig_c1x` is called directly here, not re-implemented, so what is compared is the
frozen research rule against the live-path code. For every real C1 event in the window the two
must choose the same trigger bar, the same executable bar and the same censored verdict.

The operational side derives its buckets from its own grid rather than from the research arrays,
so this also re-confirms that the bucket series the diagnostic reads matches the one the study
used - the premium boundary is the whole content of the rule.

Skipped when the research data is absent, which is the case on the server snapshot.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
pytest.importorskip("numpy")
if not (REPO / "data/runtime/crypto/d5/grid_v1.npz").exists():
    pytest.skip("research grid not present", allow_module_level=True)

import numpy as np

from app.crypto.c1 import c1x as C
from app.crypto.c1 import contract as K
from app.crypto.c1.engine import C1Engine
from app.crypto.c1.grid import Bar, build_grid
from app.crypto.research import dataset, features as F
from app.crypto.research import derivatives_flow as DF
from app.crypto.research.c1_exit import e2 as E2

DAY = 86_400_000
WARMUP_DAYS = 35


def _window() -> tuple[str, str]:
    if os.environ.get("C1_PARITY_FULL"):
        return "2022-01-01", "2026-09-23"
    return "2022-05-01", "2022-06-15"


@pytest.fixture(scope="module")
def replay():
    """One shared replay: the operational grid, its C1 events, and the research bucket series."""
    g = dataset.load()
    ts = g["ts"]
    x = DF.load_inputs(g)
    f5 = DF.features5(x)
    s1 = DF.to_1m(f5["spot_basis_bybit"], x["B"], ts)
    research_bucket = F.bucketize(s1, len(ts) // F.DAY_BARS)

    start, end = _window()
    start_ms = int(np.datetime64(start, "ms").astype(np.int64))
    end_ms = int(np.datetime64(end, "ms").astype(np.int64))
    lo = int(np.searchsorted(ts, start_ms - WARMUP_DAYS * DAY, "left"))
    # Reach past the window by the diagnostic's own 8 h horizon plus the entry offset.
    hi = min(int(np.searchsorted(ts, end_ms, "left")) + K.C1X_MAX_HOLD_MIN + 2, len(ts))
    bars = [Bar(ts_ms=int(t), open=float(o), high=float(h), low=float(l), close=float(c))
            for t, o, h, l, c in zip(ts[lo:hi], g["open"][lo:hi], g["high"][lo:hi],
                                     g["low"][lo:hi], g["close"][lo:hi]) if np.isfinite(c)]
    spot_ts, spot_close = DF.binance_spot()
    s_lo = int(np.searchsorted(spot_ts, start_ms - WARMUP_DAYS * DAY - 600_000, "left"))
    s_hi = int(np.searchsorted(spot_ts, end_ms, "left"))
    oi = dataset._read("open_interest_5m", ["open_interest"])
    o_lo = int(np.searchsorted(oi["timestamp_ms"], start_ms - WARMUP_DAYS * DAY - 7_200_000, "left"))
    o_hi = int(np.searchsorted(oi["timestamp_ms"], end_ms, "left"))
    grid = build_grid(bars,
                      list(zip(spot_ts[s_lo:s_hi].tolist(), spot_close[s_lo:s_hi].tolist())),
                      list(zip(oi["timestamp_ms"][o_lo:o_hi].tolist(),
                               oi["open_interest"][o_lo:o_hi].tolist())),
                      start_ms=int(ts[lo]), end_ms=int(ts[hi - 1]))
    engine = C1Engine()
    signals = list(engine.advance(grid, start_index=grid.index_of(start_ms),
                                  stop_index=grid.index_of(end_ms)))
    funding = list(zip(g["funding_ts"].tolist(), g["funding_rate"].tolist()))
    return {"ts": ts, "grid": grid, "engine": engine, "signals": signals, "lo": lo,
            "research_bucket": research_bucket, "funding": funding, "g": g,
            "eval5": ((ts + 60_000) % (5 * 60_000)) == 0}


def _research_choice(replay, entry_index_grid: int):
    """What `e2.trig_c1x` decides for this entry, on the research arrays."""
    grid = replay["grid"]
    entry_ref = replay["lo"] + entry_index_grid - grid.index_of(int(replay["ts"][replay["lo"]]))
    assert replay["ts"][entry_ref] == grid.ts(entry_index_grid)
    stop = entry_ref + E2.MAX_HOLD + 1
    w = {"eval5": replay["eval5"][entry_ref:stop],
         "s1_bucket": replay["research_bucket"][entry_ref:stop]}
    exit_local, exit_ref, trigger_local = E2.trig_c1x(w, 0.0, 0.0)
    return exit_local, trigger_local, entry_ref


def test_the_window_produced_events_to_compare(replay) -> None:
    assert len(replay["signals"]) > 20


def test_every_event_picks_the_same_trigger_bar_as_the_research_rule(replay) -> None:
    grid, engine = replay["grid"], replay["engine"]
    compared = triggered = censored = 0
    for signal in replay["signals"]:
        entry_index = grid.index_of(signal.official_entry_at_ms)
        if entry_index + K.C1X_MAX_HOLD_MIN + 1 >= len(grid):
            continue                                   # the 8 h window runs past the replay
        exit_local, trigger_local, _ = _research_choice(replay, entry_index)
        event = C.evaluate(signal, grid, engine.cutoffs.get, replay["funding"])
        compared += 1
        if trigger_local is None:
            assert event.status == "EXPIRED_MAX_HOLD", signal.signal_id
            assert event.censored_by_max_hold is True
            assert event.triggered_at_ms is None
            censored += 1
            continue
        triggered += 1
        assert event.status == "TRIGGERED", signal.signal_id
        assert event.censored_by_max_hold is False
        # The research returns local offsets from the entry bar; the operational record carries
        # absolute instants, and the bar close is the decision instant.
        assert event.triggered_at_ms == grid.ts(entry_index + trigger_local) + K.MINUTE_MS
        assert event.executable_at_ms == grid.ts(entry_index + exit_local)
        assert event.hypothetical_exit_price == grid.opens[entry_index + exit_local]
        assert event.holding_minutes == exit_local
    assert compared > 20
    assert triggered > 0, "the window produced no triggers, so nothing was really compared"
    print(f"\nC1x E2 parity: {compared} events, {triggered} triggered, {censored} censored")


def test_the_trigger_rate_is_in_the_region_the_research_reported(replay) -> None:
    """E2 measured 83% triggered / 17% censored over 1,603 events. This window is far smaller, so
    this is a sanity bound rather than a reproduction of that figure."""
    grid, engine = replay["grid"], replay["engine"]
    outcomes = []
    for signal in replay["signals"]:
        entry_index = grid.index_of(signal.official_entry_at_ms)
        if entry_index + K.C1X_MAX_HOLD_MIN + 1 >= len(grid):
            continue
        outcomes.append(C.evaluate(signal, grid, engine.cutoffs.get, replay["funding"]).status)
    rate = sum(1 for status in outcomes if status == "TRIGGERED") / len(outcomes)
    assert 0.4 <= rate <= 1.0
    assert K.C1X_RESEARCH["trigger_rate"] == 0.83


def test_the_hypothetical_pricing_matches_the_research_exit_bar(replay) -> None:
    """E2 prices the candidate at `open[e + exit_local]`. A one-bar disagreement here would be
    invisible in the aggregate and wrong in every record."""
    grid, engine = replay["grid"], replay["engine"]
    checked = 0
    for signal in replay["signals"][:40]:
        entry_index = grid.index_of(signal.official_entry_at_ms)
        if entry_index + K.C1X_MAX_HOLD_MIN + 1 >= len(grid):
            continue
        exit_local, trigger_local, entry_ref = _research_choice(replay, entry_index)
        if trigger_local is None:
            continue
        event = C.evaluate(signal, grid, engine.cutoffs.get, replay["funding"])
        assert event.entry_price == float(replay["g"]["open"][entry_ref])
        assert event.hypothetical_exit_price == float(replay["g"]["open"][entry_ref + exit_local])
        ratio = event.hypothetical_exit_price / event.entry_price
        assert event.gross_if_exited == pytest.approx(ratio - 1, abs=1e-18)
        checked += 1
    assert checked > 0
