"""Entry execution variants, fill models and per-trade economics (contract sections 3 and 4). Pure numpy."""
from __future__ import annotations

import numpy as np

from ..ptm_r1 import data as PD
from ..ptm_r1 import r1_train as R

TICK = 0.1
WINDOW = 5
HORIZON = 240
TAKER = 0.0005
MAKER = 0.0002
STRESS_TAKER = 0.00055
STRESS_MAKER = 0.00022
HALF_SPREAD = R.BASE_RT / 2
STRESS_HALF_SPREAD = R.STRESS_RT / 2
FILL_MODELS = ("OPTIMISTIC", "BASE", "CONSERVATIVE")
VARIANTS = ("TAKER", "M1", "M2", "M3")
BUCKETS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.0001))


def limit_price(variant: str, ref: float, dirn: int) -> float:
    if variant in ("M1", "M3"):
        return ref - dirn * TICK
    if variant == "M2":
        return ref
    raise ValueError(variant)


def trigger(L: float, dirn: int, model: str) -> float:
    """Price the bar must reach for a fill at L (LONG: low <= trigger; SHORT: high >= trigger)."""
    if model == "OPTIMISTIC":
        return L
    if model == "BASE":
        return L - dirn * TICK
    if model == "CONSERVATIVE":
        return L * (1 - dirn * 1e-4)
    raise ValueError(model)


def maker_fill(px: PD.Prices, e: int, dirn: int, L: float, model: str, window: int = WINDOW) -> int | None:
    t = trigger(L, dirn, model)
    for k in range(e, e + window):
        if (dirn == 1 and px.low[k] <= t) or (dirn == -1 and px.high[k] >= t):
            return k
    return None


def trade(mk: R.Market, k_fill: int, fill_price: float, x: int, dirn: int, entry_fee: float, entry_spread: float,
          exit_fee: float = TAKER, exit_spread: float = HALF_SPREAD) -> dict:
    """Position from fill (minute k_fill, price fill_price) to a taker exit at open[x]."""
    px = mk.px
    gross = dirn * (px.open[x] / fill_price - 1.0)
    ratio = 1.0 + dirn * gross
    fee = entry_fee + exit_fee * ratio
    a, b = np.searchsorted(mk.fund_idx, [k_fill, x + 1])
    funding = float(np.sum(dirn * mk.fund_rate[a:b] * px.open[mk.fund_idx[a:b]] / fill_price))
    net = gross - fee - entry_spread - exit_spread - funding
    sl = slice(k_fill, x)
    fav = (px.high[sl] / fill_price - 1.0) if dirn == 1 else (1.0 - px.low[sl] / fill_price)
    adv = (px.low[sl] / fill_price - 1.0) if dirn == 1 else (1.0 - px.high[sl] / fill_price)
    mfe = max(0.0, float(fav.max()) if len(fav) else 0.0, gross)
    mae = max(0.0, float(-adv.min()) if len(adv) else 0.0, -gross)
    return {"gross": float(gross), "fee": float(fee), "funding": funding, "net": float(net), "mfe": mfe, "mae": mae,
            "fill_minute": int(k_fill), "fill_price": float(fill_price)}


def execute(mk: R.Market, e: int, dirn: int, variant: str, model: str, stress: bool = False) -> dict:
    """One signal. Returns {'filled': bool, 'maker': bool, ...trade fields}."""
    taker = STRESS_TAKER if stress else TAKER
    maker = STRESS_MAKER if stress else MAKER
    half = STRESS_HALF_SPREAD if stress else HALF_SPREAD
    x = e + HORIZON
    px = mk.px
    if variant == "TAKER":
        t = trade(mk, e, px.open[e], x, dirn, taker, half, taker, half)
        return {"filled": True, "maker": False, **t}
    L = limit_price(variant, float(px.open[e]), dirn)
    k = maker_fill(px, e, dirn, L, model)
    if k is not None:
        t = trade(mk, k, L, x, dirn, maker, 0.0, taker, half)
        return {"filled": True, "maker": True, **t}
    if variant == "M3":
        k = e + WINDOW
        t = trade(mk, k, px.open[k], x, dirn, taker, half, taker, half)
        return {"filled": True, "maker": False, **t}
    return {"filled": False, "maker": False, "gross": 0.0, "fee": 0.0, "funding": 0.0, "net": 0.0, "mfe": 0.0, "mae": 0.0,
            "fill_minute": None, "fill_price": None}


def bucket_of(conf: float) -> int | None:
    for i, (a, b) in enumerate(BUCKETS):
        if a <= conf < b:
            return i
    return None


def bucket_label(i: int) -> str:
    a, b = BUCKETS[i]
    return f"{a:.2f}-{min(b, 1.0):.2f}"
