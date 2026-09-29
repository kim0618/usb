"""Spread / visible-depth cost scenarios measured from a real-time top-5 tape.

The historical D2 files carry no order book (D4 replay synthesises one), so BASE / STRESS costs
for D5 come from observed real-time books, never from invented numbers. The rule that turns the
tape into numbers is frozen in CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1 section 8.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def load_books(path: Path) -> dict[str, np.ndarray]:
    ts, bids, asks = [], [], []
    with open(path) as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("kind") != "MARKET":
                continue
            p = rec["payload"]
            b, a = p.get("bids") or [], p.get("asks") or []
            if len(b) < 1 or len(a) < 1:
                continue
            ts.append(p["ts_ms"])
            bids.append([(float(x), float(q)) for x, q in b[:5]] + [(np.nan, 0.0)] * (5 - len(b[:5])))
            asks.append([(float(x), float(q)) for x, q in a[:5]] + [(np.nan, 0.0)] * (5 - len(a[:5])))
    return {"ts": np.array(ts), "bids": np.array(bids), "asks": np.array(asks)}


def walk_cost(levels: np.ndarray, best: np.ndarray, mid: np.ndarray, qty: float) -> tuple[np.ndarray, np.ndarray]:
    """Extra cost beyond the best price, as a fraction of mid, for taking ``qty`` through the
    visible levels. Returns (cost, sufficient). Insufficient visible depth -> NaN cost."""
    px, q = levels[..., 0], levels[..., 1]
    cum = np.cumsum(q, axis=1)
    prev = np.concatenate([np.zeros((len(q), 1)), cum[:, :-1]], axis=1)
    take = np.clip(qty - prev, 0, q)
    sufficient = cum[:, -1] + 1e-12 >= qty
    with np.errstate(invalid="ignore"):
        vwap = np.nansum(take * px, axis=1) / qty
    cost = np.abs(vwap - best) / mid
    cost[~sufficient] = np.nan
    return cost, sufficient


def measure(path: Path, qtys=(0.01, 0.1, 1.0)) -> dict:
    bk = load_books(path)
    bid1, ask1 = bk["bids"][:, 0, 0], bk["asks"][:, 0, 0]
    ok = (ask1 > bid1)
    mid = (bid1 + ask1) / 2
    spread = (ask1 - bid1) / mid
    out = {
        "snapshots": int(len(bid1)), "crossed_or_locked": int((~ok).sum()),
        "start_ms": int(bk["ts"][0]), "end_ms": int(bk["ts"][-1]),
        "spread_frac": _q(spread[ok]),
        "depth5_bid_btc": _q(bk["bids"][ok, :, 1].sum(1)), "depth5_ask_btc": _q(bk["asks"][ok, :, 1].sum(1)),
        "impact": {},
    }
    for qty in qtys:
        buy, sb = walk_cost(bk["asks"][ok], ask1[ok], mid[ok], qty)
        sell, ss = walk_cost(bk["bids"][ok], bid1[ok], mid[ok], qty)
        out["impact"][str(qty)] = {
            "buy_insufficient_rate": float(1 - sb.mean()), "sell_insufficient_rate": float(1 - ss.mean()),
            "buy_frac": _q(buy[sb]), "sell_frac": _q(sell[ss]),
        }
    return out


def _q(x: np.ndarray) -> dict:
    x = x[np.isfinite(x)]
    return {"n": int(len(x)), "mean": float(x.mean()), "p50": float(np.quantile(x, .5)),
            "p90": float(np.quantile(x, .9)), "p95": float(np.quantile(x, .95)), "p99": float(np.quantile(x, .99)),
            "max": float(x.max())}
