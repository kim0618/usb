"""Manual Context & Exit R0.1 re-run on Binance inputs (contract section 7). The study code is the
manual_context_exit_r0 package unchanged; only its bar/derivative loaders and COST_BP are swapped.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.c1_binance_recheck.mce_venue <arm>
    arm: bybit_full | bybit_2021 | binance_2021

K1 (1m) Binance local archive starts 2021-02-01 while the Bybit D2 file starts 2020-03, so the
1h/4h/1d "all of K1" spans differ; bybit_2021 trims Bybit to the same start for the comparison.
C1J/backfill parts are skipped: they already ran on Binance-venue journal data in R0.1.
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile

import numpy as np

from ..manual_context_exit_r0 import bars as MB
from ..manual_context_exit_r0 import run as MR
from ..manual_context_exit_r0 import study as MY
from . import data as BD
from .recheck import CUT, OUTDIR, check_freeze

START_2021 = 1_612_137_600_000
AGG_DIR = MB.HIST / "aggtrades_daily"            # Binance USD-M aggTrades (Binance file naming, CHECKSUM files)
BINANCE_COST_BP = 11.25 - 2 * (5.5 - 5.0)         # R0.1 cost minus the taker difference on both legs


def binance_k1() -> MB.Series:
    g, _ = BD.load()
    return MB.Series("1m", MB.MINUTE_MS, g["ts"], g["open"], g["high"], g["low"], g["close"], g["volume"], g["turnover"])


def binance_15s() -> MB.Series:
    ts, px, qt = [], [], []
    for p in MB.k15_days():
        day = p.name[len("BTCUSDT"):len("BTCUSDT") + 10]
        z = zipfile.ZipFile(AGG_DIR / f"BTCUSDT-aggTrades-{day}.zip")
        text = z.read(z.namelist()[0]).decode()
        rows = text.splitlines()
        if rows and not rows[0][:1].isdigit():
            rows = rows[1:]
        for r in rows:
            c = r.split(",")
            ts.append(int(c[5])); px.append(float(c[1])); qt.append(float(c[2]))
    t = np.asarray(ts, dtype=np.int64); o = np.argsort(t, kind="stable")
    return MB.bars_from_trades(t[o], np.asarray(px)[o], np.asarray(qt)[o], MB.K15_FIRST_DAY_MS,
                               MB.K15_FIRST_DAY_MS + MB.K15_DAYS * MB.DAY_MS, 15_000)


def binance_derivatives() -> MB.Derivatives:
    g, _ = BD.load()
    ft, fr, _ = BD.funding()
    ot, ov = BD.open_interest()
    ok = np.isfinite(g["index_close"])
    return MB.Derivatives(ft, fr, g["ts"][ok], g["index_close"][ok], ot, ov)


def main(arm: str) -> dict:
    check_freeze()
    t0 = time.time()
    if arm == "binance_2021":
        k1 = binance_k1(); der = binance_derivatives()
        MB.load_15s = lambda include_rpi=True: binance_15s()
        MY.COST_BP = BINANCE_COST_BP
    else:
        k1 = MB.load_k1(); der = MB.load_derivatives()
        if arm == "bybit_2021":
            k1 = k1.slice(int(np.searchsorted(k1.ts, START_2021)), len(k1))
    tfs, raws = {}, {}
    for tf in MB.TIMEFRAMES:
        tfs[tf], raws[tf] = MR.tf_study(tf, k1, der)
    holm = MR.holm_block(raws)
    res = {"arm": arm, "cost_bp": MY.COST_BP, "timeframes": tfs, "holm": holm, "verdict": MR.verdict(tfs, holm),
           "k1_first_utc": str(np.datetime64(int(k1.ts[0]), "ms")), "runtime_sec": round(time.time() - t0, 1)}
    if arm == "binance_2021":
        res["funding_missing_after_utc"] = str(np.datetime64(int(der.funding_ts.max()), "ms"))
    (OUTDIR / f"mce_{arm}_v1.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=MR._json) + "\n")
    return res


if __name__ == "__main__":
    r = main(sys.argv[1])
    print(r["arm"], r["verdict"]["verdict_before_preview"], r["runtime_sec"])
