"""D6-C (FDN-V1) re-run with the input grid swapped to Binance (contract section 7). Rules, gates and
engine are the d6c package unchanged; only `D.load_inputs` and the engine's taker rate are replaced.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c1_binance_recheck.d6c_venue <arm>
    arm: bybit_full | bybit_cut | binance_cut_bybit_fee | binance_cut_binance_fee
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from ..d6 import data as D6D
from ..d6c import data as D6CD
from ..d6c import runner as R
from . import data as BD
from .recheck import BINANCE_TAKER, BYBIT_TAKER, CUT, OUTDIR, check_freeze, fee

KEYS = ("ts", "open", "high", "low", "close", "volume", "turnover", "mark_close", "mark_low", "mark_high",
        "index_close", "oi", "funding_last")


def cut(grid: dict) -> dict:
    keep = grid["ts"] < CUT
    out = {k: (v[keep] if isinstance(v, np.ndarray) and len(v) == len(keep) else v) for k, v in grid.items()}
    fk = out["funding_ts"] <= CUT
    out["funding_ts"], out["funding_rate"] = out["funding_ts"][fk], out["funding_rate"][fk]
    return out


def binance_grid() -> dict:
    gb, _ = BD.load()
    g = {k: gb[k] for k in KEYS}
    g["funding_ts"], g["funding_rate"] = gb["funding_ts"], gb["funding_rate"]
    g["oi_record_ts"] = gb["oi_record_ts"]
    g["next_funding_ts"] = D6D.next_funding_timestamps(g["ts"], g["funding_ts"])
    return g


def main(arm: str) -> dict:
    check_freeze()
    t0 = time.time()
    if arm.startswith("bybit"):
        grid = D6CD.load_inputs()
        grid = grid if arm == "bybit_full" else cut(grid)
    else:
        grid = cut(binance_grid())
    nan = {k: int(np.isnan(grid[k]).sum()) for k in ("mark_close", "mark_low", "index_close", "close")}
    R.D.load_inputs = lambda rebuild=False: grid
    with fee(BINANCE_TAKER if arm.endswith("binance_fee") else BYBIT_TAKER):
        p = R.run(write=False)
    (OUTDIR / f"d6c_{arm}_payload_v1.json").write_text(json.dumps(p, indent=1, default=str, sort_keys=True))
    keep = {"arm": arm, "runtime_sec": round(time.time() - t0, 1), "grid_rows": int(len(grid["ts"])),
            "grid_last_utc": str(np.datetime64(int(grid["ts"][-1]), "ms")), "nan_counts": nan,
            "engine_verification": {k: p["engine_verification"][k] for k in ("bars_checked", "eligible_checked")} |
            {"disagreements": len(p["engine_verification"]["disagreements"])},
            "main": p["main"], "bootstrap_ci": p["bootstrap_ci"], "verdict": p["verdict"],
            "gates": p["gates"], "years": p["years"], "sensitivity": p["sensitivity"], "nostop": p["nostop"],
            "trades": p["main"]["trades"]}
    (OUTDIR / f"d6c_{arm}_v1.json").write_text(json.dumps(keep, indent=1, default=str, sort_keys=True))
    return keep


if __name__ == "__main__":
    r = main(sys.argv[1])
    print(r["arm"], r["verdict"], r["main"]["average_trade_bp"], r["main"]["trades"], r["runtime_sec"])
