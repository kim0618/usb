"""Held out swing threshold calibration (contract section 16).

Warmup window only. The replay window is never touched here and no metric of section 13 is read.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import data
from .swings import THETA_LADDER, ZigZag

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/price_structure_r0"
TARGET_PER_DAY = {"1h": 2.0, "5m": 8.0}


def density(bars, tf: str, theta: float, end_ms: int) -> tuple[int, float]:
    zz = ZigZag(tf, theta, label_frame=(tf == "1h"))
    n = 0
    for i in range(len(bars)):
        if bars.close_ms(i) > end_ms:
            break
        n += len(zz.on_bar(bars, i))
    days = (end_ms - int(bars.ts[0])) / 86_400_000
    return n, n / days


def run() -> dict:
    bars = data.load()
    end = data.REPLAY_START_MS
    table = {}
    chosen = {}
    for tf in ("1h", "5m"):
        rows = []
        for th in THETA_LADDER:
            n, per_day = density(bars[tf], tf, th, end)
            rows.append({"theta": th, "confirmations": n, "per_day": round(per_day, 3)})
        table[tf] = rows
        ok = [r for r in rows if r["per_day"] <= TARGET_PER_DAY[tf]]
        pick = ok[0] if ok else rows[-1]
        chosen[tf] = {"theta": pick["theta"], "per_day": pick["per_day"],
                      "met_target": bool(ok), "target_per_day": TARGET_PER_DAY[tf]}
    out = {"window": {"start_ms": int(bars["1h"].ts[0]), "end_ms": end,
                      "days": round((end - int(bars["1h"].ts[0])) / 86_400_000, 3)},
           "ladder": list(THETA_LADDER), "target_per_day": TARGET_PER_DAY,
           "table": table, "chosen": chosen}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "calibration_v1.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


if __name__ == "__main__":
    r = run()
    for tf, rows in r["table"].items():
        print(tf, "target<=", r["target_per_day"][tf])
        for row in rows:
            print("   theta=%.1f  n=%4d  per_day=%.2f" % (row["theta"], row["confirmations"], row["per_day"]))
        print("   chosen:", r["chosen"][tf])
