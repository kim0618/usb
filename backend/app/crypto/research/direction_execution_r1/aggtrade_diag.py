"""aggTrades maker-fill diagnostic (contract section 5). Synthetic quotes at every UTC hour, both directions.
No classifier is applied: this 14-day window lies in the HOLDOUT period.

Run (venv): cd backend && ../.venv/bin/python -m app.crypto.research.direction_execution_r1.aggtrade_diag
"""
from __future__ import annotations

import glob
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..ptm_r1 import data as PD
from . import execution as X

SRC = PD.ROOT / "data/runtime/crypto/BTCUSDT/historical/aggtrades_daily"
OUT = PD.ROOT / "data/research/crypto/direction_execution_r1/aggtrade_diag_v1.json"
ORDER_QTY = 0.1
H_MS = 3_600_000
W_MS = X.WINDOW * 60_000


def load() -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    files = sorted(glob.glob(str(SRC / "BTCUSDT-aggTrades-2026-09-*.zip")))
    log, parts = [], []
    for f in files:
        log.append({"file": Path(f).name, "sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()})
        parts.append(pd.read_csv(f, usecols=["price", "quantity", "transact_time"],
                                 dtype={"price": float, "quantity": float, "transact_time": np.int64}))
    df = pd.concat(parts, ignore_index=True)
    return df["transact_time"].to_numpy(), df["price"].to_numpy(), df["quantity"].to_numpy(), log


def last_price_before(t, p, ts):
    i = np.searchsorted(t, ts, side="left") - 1
    return p[i] if i >= 0 else np.nan


def run(out: Path | None = OUT) -> dict:
    t, p, q, log = load()
    t0 = (int(t[0]) // H_MS + 1) * H_MS
    t_end = int(t[-1])
    rows = []
    H = t0
    while H + 60_000 + 4 * H_MS < t_end:
        start = H + 60_000
        i0 = np.searchsorted(t, start, side="left")
        ref = p[i0]
        i1 = np.searchsorted(t, start + W_MS, side="left")
        wp, wq, wt = p[i0:i1], q[i0:i1], t[i0:i1]
        for variant in ("M1", "M2"):
            for d in (1, -1):
                L = X.limit_price(variant, float(ref), d)
                s = d * (wp - L)                         # <= 0 means at or through the limit
                touch = s <= 1e-9
                thr_tick = s <= -X.TICK + 1e-9
                thr_bp = d * (wp - L * (1 - d * 1e-4)) <= 1e-9
                vol_at = float(wq[np.abs(wp - L) < 1e-9].sum())
                vol_through = float(wq[s < -1e-9].sum())
                rec = {"variant": variant, "dir": d, "touch": bool(touch.any()), "through_tick": bool(thr_tick.any()),
                       "through_1bp": bool(thr_bp.any()), "vol_at_L": vol_at, "vol_through": vol_through,
                       "move_4h_from_ref_bp": float(d * (last_price_before(t, p, start + 4 * H_MS) / ref - 1) * 1e4)}
                for name, mask in (("touch", touch), ("through_tick", thr_tick), ("through_1bp", thr_bp)):
                    if mask.any():
                        tf = int(wt[np.argmax(mask)])
                        rec[f"{name}_adverse_5m_bp"] = float(d * (last_price_before(t, p, tf + 300_000) / L - 1) * 1e4)
                        rec[f"{name}_move_4h_from_fill_bp"] = float(d * (last_price_before(t, p, start + 4 * H_MS) / L - 1) * 1e4)
                rows.append(rec)
        H += H_MS
    rep = {"window": "Binance aggTrades BTCUSDT 2026-09-09 .. 2026-09-22 (HOLDOUT period; no classifier applied)",
           "files": log, "trades": int(len(t)), "hours": len(rows) // 4, "order_qty_btc": ORDER_QTY, "by_variant": {}}
    for variant in ("M1", "M2"):
        R_ = [r for r in rows if r["variant"] == variant]
        n = len(R_)
        res = {"quotes": n}
        for name in ("touch", "through_tick", "through_1bp"):
            f = [r for r in R_ if r[name]]
            nf = [r for r in R_ if not r[name]]
            res[name] = {
                "fill_rate": len(f) / n,
                "adverse_5m_bp_mean": float(np.mean([r[f"{name}_adverse_5m_bp"] for r in f])) if f else None,
                "move_4h_from_fill_bp_mean": float(np.mean([r[f"{name}_move_4h_from_fill_bp"] for r in f])) if f else None,
                "move_4h_from_ref_filled_bp_mean": float(np.mean([r["move_4h_from_ref_bp"] for r in f])) if f else None,
                "move_4h_from_ref_unfilled_bp_mean": float(np.mean([r["move_4h_from_ref_bp"] for r in nf])) if nf else None}
        touch_only = [r for r in R_ if r["touch"] and not r["through_tick"]]
        res["touch_only_share_of_touches"] = len(touch_only) / max(1, sum(r["touch"] for r in R_))
        res["touch_only_vol_at_L_btc"] = {"median": float(np.median([r["vol_at_L"] for r in touch_only])) if touch_only else None,
                                          "p25": float(np.percentile([r["vol_at_L"] for r in touch_only], 25)) if touch_only else None}
        thr = [r for r in R_ if r["through_tick"]]
        res["base_fills_with_through_volume_below_order_qty"] = (sum(r["vol_through"] < ORDER_QTY for r in thr) / len(thr)) if thr else None
        rep["by_variant"][variant] = res
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True))
    return rep


if __name__ == "__main__":
    print(json.dumps(run()["by_variant"], indent=1))
