"""Forward outcomes. One fixed return is not an outcome, so six things are measured instead.

Every function here takes a decision index and reads strictly later data. The only subtlety is
`first_meaningful_direction` on 1m bars: when a single bar's high clears the up threshold and its
low clears the down threshold, the order inside the bar is unknown, and the honest value is
`AMBIGUOUS` rather than a guess dressed up as a measurement.
"""
from __future__ import annotations

import numpy as np

from .layers import MEANINGFUL_BP, RT_COST_BP, MinuteFrame, SecondFrame

AMBIGUOUS = "AMBIGUOUS"


def _first_meaningful_bars(high: np.ndarray, low: np.ndarray, p0: float) -> str:
    up, dn = p0 * (1 + MEANINGFUL_BP / 1e4), p0 * (1 - MEANINGFUL_BP / 1e4)
    for h, l in zip(high, low):
        hit_up, hit_dn = h >= up, l <= dn
        if hit_up and hit_dn:
            return AMBIGUOUS
        if hit_up:
            return "UP"
        if hit_dn:
            return "DOWN"
    return "NONE"


def _first_meaningful_path(path: np.ndarray, p0: float) -> str:
    up, dn = p0 * (1 + MEANINGFUL_BP / 1e4), p0 * (1 - MEANINGFUL_BP / 1e4)
    for x in path:
        if not np.isfinite(x):
            continue
        if x >= up:
            return "UP"
        if x <= dn:
            return "DOWN"
    return "NONE"


def minute_outcome(fr: MinuteFrame, i_dec: int, lean: str, level_price: float | None,
                   horizon_min: int, level_side: str) -> dict | None:
    end = i_dec + horizon_min
    if end >= len(fr.close):
        return None
    p0 = float(fr.close[i_dec])
    hi = fr.high[i_dec + 1:end + 1]
    lo = fr.low[i_dec + 1:end + 1]
    mfe_up = (float(hi.max()) - p0) / p0 * 1e4
    mfe_dn = (p0 - float(lo.min())) / p0 * 1e4
    close_h = float(fr.close[end])
    signed = (close_h - p0) / p0 * 1e4
    dir_move = signed if lean == "LONG" else -signed
    dir_mfe = mfe_up if lean == "LONG" else mfe_dn
    out = {"p0": round(p0, 2), "mfe_up_bp": round(mfe_up, 3), "mfe_dn_bp": round(mfe_dn, 3),
           "mae_bp": round(mfe_dn if lean == "LONG" else mfe_up, 3),
           "net_move_bp": round(dir_move - RT_COST_BP, 3),
           "reachable_bp": round(max(0.0, dir_mfe - RT_COST_BP), 3),
           "first_meaningful_direction": _first_meaningful_bars(hi, lo, p0),
           "close_h": round(close_h, 2), "reversal": None, "breakout_continuation": None}
    if level_price:
        up_gate = level_price * (1 + RT_COST_BP / 1e4)
        dn_gate = level_price * (1 - RT_COST_BP / 1e4)
        if level_side == "RES":
            out["reversal"] = bool(close_h < dn_gate)
            out["breakout_continuation"] = bool(close_h > up_gate)
        else:
            out["reversal"] = bool(close_h > up_gate)
            out["breakout_continuation"] = bool(close_h < dn_gate)
    return out


def second_outcome(fr: SecondFrame, i_dec: int, lean: str, level_price: float | None,
                   horizon_s: int, level_side: str) -> dict | None:
    j = fr.index_after(i_dec, horizon_s * 1000)
    if j is None:
        return None
    p0 = float(fr.mids[i_dec])
    if not np.isfinite(p0):
        return None
    path = fr.mids[i_dec + 1:j + 1]
    finite = path[np.isfinite(path)]
    if finite.size == 0:
        return None
    mfe_up = (float(finite.max()) - p0) / p0 * 1e4
    mfe_dn = (p0 - float(finite.min())) / p0 * 1e4
    close_h = float(fr.mids[j])
    if not np.isfinite(close_h):
        return None
    signed = (close_h - p0) / p0 * 1e4
    dir_move = signed if lean == "LONG" else -signed
    dir_mfe = mfe_up if lean == "LONG" else mfe_dn
    out = {"p0": round(p0, 2), "mfe_up_bp": round(mfe_up, 3), "mfe_dn_bp": round(mfe_dn, 3),
           "mae_bp": round(mfe_dn if lean == "LONG" else mfe_up, 3),
           "net_move_bp": round(dir_move - RT_COST_BP, 3),
           "reachable_bp": round(max(0.0, dir_mfe - RT_COST_BP), 3),
           "first_meaningful_direction": _first_meaningful_path(path, p0),
           "close_h": round(close_h, 2), "samples_in_horizon": int(finite.size),
           "reversal": None, "breakout_continuation": None}
    if level_price:
        up_gate = level_price * (1 + RT_COST_BP / 1e4)
        dn_gate = level_price * (1 - RT_COST_BP / 1e4)
        if level_side == "RES":
            out["reversal"] = bool(close_h < dn_gate)
            out["breakout_continuation"] = bool(close_h > up_gate)
        else:
            out["reversal"] = bool(close_h > up_gate)
            out["breakout_continuation"] = bool(close_h < dn_gate)
    return out
