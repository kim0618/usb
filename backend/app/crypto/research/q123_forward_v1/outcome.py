"""Contract section 6: forward outcomes at 5 / 15 / 30 / 60 minutes, R0's `second_outcome` unchanged.

The added fields are restatements of R0's numbers (raw signed move, gross directional move, the
lean's MFE, and the fee / slippage split of the same 11.25 bp), never a new measurement.
"""
from __future__ import annotations

from ..market_context_r0 import outcomes as R0O
from . import contract as K


def outcome(sf, lean: str, level_price, side: str, horizon_min: int, *,
            decision_index: int) -> dict:
    target = int(sf.ms[decision_index]) + horizon_min * 60_000
    if int(sf.ms[-1]) < target:
        return {"horizon_min": horizon_min, "status": "PENDING"}   # not yet knowable
    o = R0O.second_outcome(sf, decision_index, lean, level_price, horizon_min * 60, side)
    if o is None:
        return {"horizon_min": horizon_min, "status": "MISSING"}  # session end or > 5 s gap
    raw = (o["close_h"] - o["p0"]) / o["p0"] * 1e4
    directional = raw if lean == "LONG" else -raw
    mfe = o["mfe_up_bp"] if lean == "LONG" else o["mfe_dn_bp"]
    return {"horizon_min": horizon_min, "status": "MEASURED", "p0": o["p0"],
            "close_h": o["close_h"], "raw_move_bp": round(raw, 3),
            "directional_move_bp": round(directional, 3), "mfe_bp": round(mfe, 3),
            "mae_bp": o["mae_bp"], "mfe_up_bp": o["mfe_up_bp"], "mfe_dn_bp": o["mfe_dn_bp"],
            "high_excursion_bp": o["mfe_up_bp"], "low_excursion_bp": o["mfe_dn_bp"],
            "est_fee_bp": K.EST_FEE_BP, "est_slippage_bp": K.EST_SLIPPAGE_BP,
            "net_move_bp": o["net_move_bp"], "reachable_bp": o["reachable_bp"],
            "first_meaningful_direction": o["first_meaningful_direction"],
            "reversal": o["reversal"], "breakout_continuation": o["breakout_continuation"],
            "samples_in_horizon": o["samples_in_horizon"]}
