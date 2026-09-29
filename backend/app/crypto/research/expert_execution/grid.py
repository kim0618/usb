"""E2 minute grid from the E1 XBTUSD quote/trade parquet (contract section 3).

Row k is the boundary b_k (minute END, UTC ns). mid_close = M(b_k) = mid of the last valid quote with
ts < b_k, blank if that quote is older than 60 s. mid_hi / mid_lo = extremes of valid quote mids inside
[b_k - 1m, b_k), falling back to mid_close when the minute has no quote.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.expert_execution.grid --workers 4
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import market as M

MIN_NS = 60 * 10**9
DAY_MIN = 1440
STALE_NS = 60 * 10**9
GRID = Path("data/research/expert_execution/e2/minute_grid.parquet")


def day_minutes(d: str, quotes: pd.DataFrame | None, trades: pd.DataFrame | None) -> pd.DataFrame:
    """Per-minute aggregates for one UTC day. Minute i covers [day + i m, day + (i+1) m)."""
    day0 = int(pd.Timestamp(d, tz="UTC").value)
    out = {"b": day0 + (np.arange(DAY_MIN) + 1) * MIN_NS,
           "last_ts": np.full(DAY_MIN, -1, np.int64), "last_mid": np.full(DAY_MIN, np.nan),
           "last_spread": np.full(DAY_MIN, np.nan), "hi": np.full(DAY_MIN, np.nan), "lo": np.full(DAY_MIN, np.nan),
           "n_quotes": np.zeros(DAY_MIN, np.int64), "n_trades": np.zeros(DAY_MIN, np.int64),
           "trade_qty": np.zeros(DAY_MIN, np.int64)}
    if quotes is not None and len(quotes):
        q = quotes[(quotes.bid > 0) & (quotes.ask > 0) & (quotes.bid < quotes.ask)]
        ts = q.ts.to_numpy()
        mid = ((q.bid + q.ask) / 2).to_numpy()
        spr = (q.ask - q.bid).to_numpy()
        mi = ((ts - day0) // MIN_NS).astype(np.int64)
        ok = (mi >= 0) & (mi < DAY_MIN)
        ts, mid, spr, mi = ts[ok], mid[ok], spr[ok], mi[ok]
        if len(mi):
            last = np.r_[mi[1:] != mi[:-1], True]          # ts sorted -> last row of each minute
            out["last_ts"][mi[last]] = ts[last]
            out["last_mid"][mi[last]] = mid[last]
            out["last_spread"][mi[last]] = spr[last]
            g = pd.DataFrame({"mi": mi, "mid": mid}).groupby("mi").mid
            hi, lo = g.max(), g.min()
            out["hi"][hi.index.to_numpy()] = hi.to_numpy()
            out["lo"][lo.index.to_numpy()] = lo.to_numpy()
            out["n_quotes"] = np.bincount(mi, minlength=DAY_MIN)
    if trades is not None and len(trades):
        mi = ((trades.ts.to_numpy() - day0) // MIN_NS).astype(np.int64)
        ok = (mi >= 0) & (mi < DAY_MIN)
        out["n_trades"] = np.bincount(mi[ok], minlength=DAY_MIN)
        out["trade_qty"] = np.bincount(mi[ok], weights=trades["size"].to_numpy()[ok], minlength=DAY_MIN).astype(np.int64)
    return pd.DataFrame(out)


def _one(d: str) -> pd.DataFrame:
    return day_minutes(d, _load("quote", d), _load("trade", d))


def _load(kind: str, d: str) -> pd.DataFrame | None:
    p = M.MARKET / kind / f"{d}.parquet"
    if not p.exists():
        return None
    cols = ["ts", "bid", "ask"] if kind == "quote" else ["ts", "size"]
    return pd.read_parquet(p, columns=cols)


def finalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Carry the last valid quote forward across empty minutes and apply the 60 s staleness rule."""
    raw = raw.sort_values("b").reset_index(drop=True)
    has = raw.last_ts.to_numpy() >= 0
    idx = np.where(has, np.arange(len(raw)), -1)
    idx = np.maximum.accumulate(idx)
    j = np.clip(idx, 0, None)
    last_ts = np.where(idx >= 0, raw.last_ts.to_numpy()[j], -1)
    mid = np.where(idx >= 0, raw.last_mid.to_numpy()[j], np.nan)
    spread = np.where(idx >= 0, raw.last_spread.to_numpy()[j], np.nan)
    age = raw.b.to_numpy() - last_ts
    fresh = (idx >= 0) & (age <= STALE_NS)
    mid = np.where(fresh, mid, np.nan)
    # hi/lo are the mids seen inside the minute; a minute without quotes falls back to mid_close
    hi = np.where(np.isnan(raw.hi), mid, raw.hi)
    lo = np.where(np.isnan(raw.lo), mid, raw.lo)
    return pd.DataFrame({"b": raw.b.to_numpy(), "mid_close": mid, "mid_hi": hi, "mid_lo": lo,
                         "spread_close": np.where(fresh, spread, np.nan), "quote_age_s": np.where(idx >= 0, age / 1e9, np.nan),
                         "n_quotes": raw.n_quotes.to_numpy(), "n_trades": raw.n_trades.to_numpy(),
                         "trade_qty": raw.trade_qty.to_numpy()})


def build(workers: int = 4) -> dict:
    t0 = time.time()
    ds = M.days()
    with ProcessPoolExecutor(workers) as pool:
        parts = list(pool.map(_one, ds, chunksize=8))
    g = finalize(pd.concat(parts, ignore_index=True))
    GRID.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(GRID, index=False, compression="zstd")
    return {"minutes": len(g), "mid_missing": int(g.mid_close.isna().sum()), "seconds": round(time.time() - t0, 1),
            "bytes": GRID.stat().st_size}


def load_grid() -> pd.DataFrame:
    return pd.read_parquet(GRID)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    print(build(ap.parse_args().workers))
