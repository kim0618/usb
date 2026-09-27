"""The 14 frozen F0 features, computed from bars that start before 16:00 ET only.

``truncate`` is the structural cutoff: it keeps regular bars (09:30 <= m < 16:00) of D and of
earlier sessions and drops everything else before any arithmetic. ``minute_part`` then reads the
truncated tape only, and ``finalize`` combines it with the D-1 daily close and SPY's day return.
PIT-1 hands these functions deliberately poisoned tapes and requires bit-identical output.
"""

from collections.abc import Mapping

import numpy as np

from app.backtest.strategy_f0_regular_after.params import FEATURE_NAMES, Params
from app.backtest.strategy_f0_regular_after.tape import Tape, subset

NAN = float("nan")


def truncate(tape: Tape, d: int, p: Params) -> Tape:
    keep = (tape.day <= d) & (tape.minute >= p.reg_start) & (tape.minute < p.reg_end)
    cut = subset(tape, keep)
    order = np.lexsort((cut.minute, cut.day))
    return subset(cut, order)


def _prevailing(m: np.ndarray, c: np.ndarray, t: int, lookback: int) -> tuple[float, float]:
    """(close of the last bar with t-lookback <= m < t, its age in seconds at t) or NaN."""
    sel = np.flatnonzero((m >= t - lookback) & (m < t))
    if sel.size == 0:
        return NAN, NAN
    last = sel[-1]
    return float(c[last]), float(t * 60 - (int(m[last]) + 1) * 60)


def minute_part(tape: Tape, d: int, p: Params) -> dict[str, float]:
    """Everything the features need from minutes, as known at 15:59:59 ET of session index d."""
    cut = truncate(tape, d, p)
    cur = cut.day == d
    out: dict[str, float] = {"has_regular": float(cur.any())}
    m = cut.minute[cur].astype(np.int32)
    h, l, c = cut.h[cur], cut.l[cur], cut.c[cur]
    v, vw = cut.v[cur], cut.vw[cur]
    close_bar = np.flatnonzero(m == p.close_minute)
    out["c1559"] = float(c[close_bar[0]]) if close_bar.size else NAN
    out["high_reg"] = float(np.max(h)) if m.size else NAN
    out["low_reg"] = float(np.min(l)) if m.size else NAN
    vw_bad = bool(np.any((v > 0) & ~np.isfinite(vw)))
    out["vwap_unavailable"] = float(vw_bad)
    traded = v > 0
    dollars = np.where(traded, v * np.where(np.isfinite(vw), vw, 0.0), 0.0)
    out["dv_reg"] = NAN if vw_bad or not m.size else float(dollars.sum())
    out["volume_reg"] = float(v.sum()) if m.size else NAN
    for name, (lo, hi) in p.dv_windows.items():
        out[f"dv_{name}"] = NAN if vw_bad or not m.size else float(dollars[(m >= lo) & (m < hi)].sum())
    for name, t in p.anchors.items():
        price, age = _prevailing(m, c, t, p.prevailing_lookback)
        out[f"p_{name}"], out[f"age_{name}"] = price, age

    prior = cut.day < d
    if prior.any():
        pd_ = cut.day[prior].astype(np.int64)
        pv, pvw = cut.v[prior], cut.vw[prior]
        bad = (pv > 0) & ~np.isfinite(pvw)
        dv = np.bincount(pd_, weights=np.where(pv > 0, pv * np.where(np.isfinite(pvw), pvw, 0.0), 0.0),
                         minlength=d)
        nbad = np.bincount(pd_, weights=bad.astype(np.float64), minlength=d)
        present = np.bincount(pd_, minlength=d) > 0
        valid = present & (nbad == 0) & np.isfinite(dv) & (dv > 0)
        history = dv[np.flatnonzero(valid)][-p.rvol_window:]
    else:
        history = np.array([], dtype=np.float64)
    out["rvol_history_count"] = float(history.size)
    out["rvol_median"] = float(np.median(history)) if history.size >= p.rvol_minimum else NAN
    return out


def leaky_minute_part(tape: Tape, d: int, p: Params) -> dict[str, float]:
    """PIT-3 plant: the 16:05 open of D replaces the 15:59 close inside day_return. Test only."""
    out = minute_part(tape, d, p)
    bar = np.flatnonzero((tape.day == d) & (tape.minute == p.entry_minute))
    if bar.size:
        out["c1559"] = float(tape.o[bar[0]])
    return out


def _ratio(a: float, b: float) -> float:
    if not (np.isfinite(a) and np.isfinite(b)) or b == 0:
        return NAN
    return a / b - 1.0


def day_return(part: Mapping[str, float], prev_close: float) -> float:
    if not (np.isfinite(prev_close) and prev_close > 0):
        return NAN
    return _ratio(part["c1559"], prev_close)


def finalize(part: Mapping[str, float], prev_close: float, spy_day_return: float,
             p: Params) -> dict[str, float]:
    c = part["c1559"]
    hi, lo = part["high_reg"], part["low_reg"]
    dv = part["dv_reg"]
    dr = day_return(part, prev_close)
    f: dict[str, float] = {"day_return": dr, "regular_dollar_volume": dv}
    median = part["rvol_median"]
    f["regular_RVOL"] = dv / median if np.isfinite(dv) and np.isfinite(median) and median > 0 else NAN
    vol = part["volume_reg"]
    vwap = dv / vol if np.isfinite(dv) and np.isfinite(vol) and vol > 0 else NAN
    f["close_vs_VWAP"] = _ratio(c, vwap)
    f["position_in_day_range"] = ((c - lo) / (hi - lo) if np.isfinite(c) and np.isfinite(hi)
                                  and np.isfinite(lo) and hi > lo else NAN)
    f["distance_to_day_high"] = _ratio(c, hi)
    for name in p.anchors:
        f[name] = _ratio(c, part[f"p_{name}"])
    f["volume_1500_1600"] = part["dv_volume_1500_1600"]
    for name in ("last30m_volume_share", "last15m_volume_share"):
        num = part[f"dv_{name}"]
        f[name] = num / dv if np.isfinite(num) and np.isfinite(dv) and dv > 0 else NAN
    for alias, source in p.alias.items():
        f[alias] = f[source]
    f["relative_strength_vs_SPY"] = (dr - spy_day_return if np.isfinite(dr) and np.isfinite(spy_day_return)
                                     else NAN)
    return {name: f[name] for name in FEATURE_NAMES}


def masks(features: Mapping[str, float], p: Params) -> dict[str, bool]:
    """H membership on the unrounded features; a NaN input makes the row fail that H."""
    out = {}
    for key, terms in p.hypotheses.items():
        ok = True
        for name, op, value in terms:
            x = features[name]
            if not (np.isfinite(x) and x >= value):   # op is '>=' (checked in params)
                ok = False
                break
        out[key] = ok
    return out


def identical(a: Mapping[str, float], b: Mapping[str, float]) -> bool:
    """Bit-identical floats with NaN positions equal."""
    if a.keys() != b.keys():
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, float) and isinstance(y, float) and np.isnan(x) and np.isnan(y):
            continue
        if x != y:
            return False
    return True
