"""Build a preview fixture out of real C1 history.

    PYTHONPATH=backend .venv/bin/python scripts/c1_fixture.py <out.json> [n_events] [end_date]

Replays the frozen research grid through the production engine and writes the last `n_events`
signals, with their settled shadow trades, rebased so the newest one is still holding. The
rebasing is what lets a reviewer see an ACTIVE signal and a COMPLETED one on the same screen; the
figures inside each record are the real replayed ones and are not altered.

Needs the research data, so it runs on a development machine and not on the server. Its output is
a plain JSON file that `FixtureRuntime` reads.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from app.crypto.research import dataset, features as F
from app.crypto.research import derivatives_flow as DF
from app.crypto.c1 import contract as K
from app.crypto.c1.engine import C1Engine
from app.crypto.c1.fixture import FixtureRuntime
from app.crypto.c1.grid import Bar, build_grid
from app.crypto.c1 import c1x as c1x_rule
from app.crypto.c1.shadow import open_shadow, settle

DAY = 86_400_000


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/runtime/crypto/c1_preview/fixture.json")
    want = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    end = sys.argv[3] if len(sys.argv) > 3 else "2026-09-23"

    g = dataset.load()
    ts = g["ts"]
    end_ms = int(np.datetime64(end, "ms").astype(np.int64))
    start_ms = end_ms - 75 * DAY
    lo = int(np.searchsorted(ts, start_ms, "left"))
    hi = int(np.searchsorted(ts, end_ms, "left"))
    bars = [Bar(ts_ms=int(t), open=float(o), high=float(h), low=float(l), close=float(c))
            for t, o, h, l, c in zip(ts[lo:hi], g["open"][lo:hi], g["high"][lo:hi],
                                     g["low"][lo:hi], g["close"][lo:hi]) if np.isfinite(c)]
    spot_ts, spot_close = DF.binance_spot()
    s_lo = int(np.searchsorted(spot_ts, start_ms, "left"))
    s_hi = int(np.searchsorted(spot_ts, end_ms, "left"))
    oi = dataset._read("open_interest_5m", ["open_interest"])
    o_lo = int(np.searchsorted(oi["timestamp_ms"], start_ms - 2 * 3_600_000, "left"))
    o_hi = int(np.searchsorted(oi["timestamp_ms"], end_ms, "left"))
    grid = build_grid(bars, list(zip(spot_ts[s_lo:s_hi].tolist(), spot_close[s_lo:s_hi].tolist())),
                      list(zip(oi["timestamp_ms"][o_lo:o_hi].tolist(),
                               oi["open_interest"][o_lo:o_hi].tolist())),
                      start_ms=int(ts[lo]), end_ms=int(ts[hi - 1]))
    funding = list(zip(g["funding_ts"].tolist(), g["funding_rate"].tolist()))

    engine = C1Engine()
    collected: list[tuple] = []
    begin = grid.index_of(int(ts[lo]) + 35 * DAY)
    for _, _, signal in engine.walk(grid, start_index=begin, stop_index=len(grid)):
        if signal is None:
            continue
        trade = open_shadow(signal, grid, funding)
        settle(trade, signal, grid, funding)
        signal.state = "COMPLETED" if trade.status == "SETTLED" else "ACTIVE"
        diagnostic = c1x_rule.evaluate(signal, grid, engine.cutoffs.get, funding)
        c1x_rule.pair_with_e0(diagnostic, trade)
        collected.append((signal, trade, diagnostic))
    chosen = collected[-want:]
    if not chosen:
        raise SystemExit("the window produced no C1 events; widen it")

    # Slide the whole set forward so the newest signal is one hour old and therefore still
    # holding, which is the state the preview has to show. Only the clock moves.
    now = int(time.time() * 1000)
    now -= now % 60_000
    offset = (now - 60 * 60_000) - chosen[-1][0].triggered_at_ms
    signals, shadow, diagnostics = [], [], []
    for signal, trade, diagnostic in chosen:
        raw = signal.to_json()
        for key in ("triggered_at_ms", "signal_bar_ms", "official_entry_at_ms", "planned_exit_at_ms"):
            raw[key] += offset
        raw["signal_id"] = f"{K.STRATEGY}-{K.DIRECTION}-{raw['triggered_at_ms']}"
        srow = trade.to_json()
        srow["signal_id"] = raw["signal_id"]
        srow["entry_at_ms"] += offset
        if srow.get("exit_at_ms"):
            srow["exit_at_ms"] += offset
        if raw["planned_exit_at_ms"] > now:
            raw["state"] = "ACTIVE"
            srow["status"] = "ACTIVE"
            for key in ("exit_price", "gross_return", "cost", "net_return", "mfe", "mae"):
                srow[key] = None
            srow["observations"] = {k: v for k, v in (srow.get("observations") or {}).items()
                                    if int(k.split("m_")[0]) * 60_000 + srow["entry_at_ms"] <= now}
        drow = diagnostic.to_json()
        drow["signal_id"] = raw["signal_id"]
        drow["c1x_event_id"] = f"C1X-{raw['signal_id']}"
        for key in ("entry_at_ms", "triggered_at_ms", "observed_at_ms", "executable_at_ms"):
            if drow.get(key):
                drow[key] += offset
        # A diagnostic whose confirmation lands in the future has not happened yet on this
        # preview's clock, so it is shown as still pending rather than as a trigger.
        if drow.get("triggered_at_ms") and drow["triggered_at_ms"] > now:
            drow["status"] = "NOT_TRIGGERED"
            for key in ("triggered_at_ms", "executable_at_ms", "observed_price",
                        "hypothetical_exit_price", "holding_minutes", "gross_if_exited",
                        "cost_if_exited", "net_if_exited"):
                drow[key] = None
        if raw["planned_exit_at_ms"] > now:
            drow["e0_status"] = "PENDING"
            drow["e0_net"] = None
            drow["delta_net"] = None
        signals.append(raw)
        shadow.append(srow)
        diagnostics.append(drow)

    FixtureRuntime.write_fixture(
        out, {"built_at_ms": now, "source": "RESEARCH_REPLAY",
              "contract_sha256": K.CONTRACT_SHA256,
              "last_decided_at_ms": now, "signals": signals, "shadow": shadow,
              "c1x": diagnostics}, indent=1)
    print(f"{len(signals)} signals -> {out}")
    for row, drow in zip(signals, diagnostics):
        print(" ", row["signal_id"], row["state"], "| C1x", drow["status"],
              "" if drow.get("net_if_exited") is None else f"{drow['net_if_exited'] * 1e4:+.1f}bp")


if __name__ == "__main__":
    main()
