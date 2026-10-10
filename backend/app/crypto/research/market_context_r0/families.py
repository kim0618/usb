"""Episodes and the pre-registered condition families of contract section 9.

The shape of every family is the same: an episode is a *position* (price is near a structural level,
or near a wall), the family is a *context* laid over that position, and the comparison is always
against the other episodes of the same position. That is what keeps the approach itself from being
mistaken for the finding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .layers import (LEVEL_TOL_PCT, RT_COST_BP, WALL_PROX_BPS_HI, WALL_PROX_BPS_LO,
                     MinuteFrame, SecondFrame)

MINUTE_MS = 60_000
T3_GAP_MS = 60 * MINUTE_MS
T2_GAP_MS = 300_000
WALL_W_MS = 60_000
T3_HORIZONS = (5, 15, 60)
T2_HORIZONS = (60, 300, 900)

#: id -> (track, side, lean, condition window in the track's own unit)
FAMILIES = {
    "F1": ("T1", "RES", "SHORT", 60), "F2": ("T1", "RES", "LONG", 60),
    "F3": ("T1", "SUP", "LONG", 60), "F4": ("T1", "SUP", "SHORT", 60),
    "G1": ("T2", "ASK", "SHORT", 60), "G2": ("T2", "ASK", "LONG", 60),
    "G3": ("T2", "ASK", "SHORT", 60), "G4": ("T2", "BID", "LONG", 60),
    "G5": ("T2", "BID", "SHORT", 60), "G6": ("T2", "BID", "LONG", 60),
    "H1": ("T3", "RES", "SHORT", 0), "H2": ("T3", "RES", "LONG", 1),
    "H3": ("T3", "SUP", "LONG", 0), "H4": ("T3", "SUP", "SHORT", 1),
    "H5": ("T3", "RES", "SHORT", 0), "H6": ("T3", "SUP", "LONG", 0),
}


# ----------------------------------------------------------------------------- T3 episodes
@dataclass
class MinuteEpisode:
    side: str
    level_id: int
    level_price: float
    anchor_ts: int
    anchor_index: int
    row: dict = field(repr=False, default_factory=dict)


def minute_episodes(fr: MinuteFrame) -> list[MinuteEpisode]:
    last: dict[tuple[int, str], int] = {}
    out: list[MinuteEpisode] = []
    for r in fr.rows:
        for side, dkey, pkey, ikey in (("RES", "res_distance_pct", "res_price", "res_id"),
                                       ("SUP", "sup_distance_pct", "sup_price", "sup_id")):
            d, p, lid = r[dkey], r[pkey], r[ikey]
            if p is None or d is None or lid is None:
                continue
            near = (0.0 <= d <= LEVEL_TOL_PCT) if side == "RES" else (-LEVEL_TOL_PCT <= d <= 0.0)
            if not near:
                continue
            key = (lid, side)
            if key in last and r["ts"] - last[key] < T3_GAP_MS:
                continue
            last[key] = r["ts"]
            out.append(MinuteEpisode(side=side, level_id=lid, level_price=p, anchor_ts=r["ts"],
                                     anchor_index=fr.row_index(r["ts"]), row=r))
    out.sort(key=lambda e: (e.anchor_ts, e.side, e.level_id))
    return out


def _buy_dominant(value: float | None, variant: str, q: float, want: str) -> bool | None:
    if value is None or not np.isfinite(value):
        return None
    if variant == "SIGN":
        return value > 0 if want == "BUY" else value < 0
    return value >= q if want == "BUY" else value <= -q


def minute_family(fr: MinuteFrame, eps: list[MinuteEpisode], fam: str, variant: str) -> dict:
    """Decision index, firing flag and level reference for every episode of the family's side."""
    _, side, lean, w_min = FAMILIES[fam]
    rows = []
    for e in eps:
        if e.side != side:
            continue
        i_dec = e.anchor_index + w_min
        if i_dec >= len(fr.close):
            continue
        dom = _buy_dominant(e.row["flow_imb_3m"], variant, fr.dom_quantile,
                            "BUY" if side == "RES" else "SELL")
        if dom is None:
            continue
        gate_up = e.level_price * (1 + RT_COST_BP / 1e4)
        gate_dn = e.level_price * (1 - RT_COST_BP / 1e4)
        cl_dec = float(fr.close[i_dec])
        cleared = cl_dec > gate_up if side == "RES" else cl_dec < gate_dn
        failure_memory = (e.row["retest_state"] in ("RETEST_TOUCHED", "RETEST_FAILED")
                          or (e.row["failed_break_state"] not in (None, "NONE")))
        if fam in ("H1", "H3"):
            fires = bool(dom and not cleared)
        elif fam in ("H2", "H4"):
            fires = bool(dom and cleared)
        elif fam in ("H5", "H6"):
            fires = bool(dom and not cleared and failure_memory)
        else:
            raise KeyError(fam)
        rows.append({"anchor_ts": e.anchor_ts, "level_id": e.level_id,
                     "level_price": e.level_price, "i_dec": i_dec, "fires": fires,
                     "flow_imb_3m": e.row["flow_imb_3m"], "cleared": bool(cleared),
                     "failure_memory": bool(failure_memory),
                     "trend_structure": e.row["trend_structure"],
                     "range_state": e.row["range_state"]})
    return {"family": fam, "variant": variant, "side": side, "lean": lean, "w": w_min,
            "episodes": rows}


# ----------------------------------------------------------------------------- T2 episodes
@dataclass
class WallEpisode:
    side: str
    bin_low: str
    bin_high: float
    wall_price: float
    anchor_ms: int
    anchor_index: int
    distance_bps: float


def wall_episodes(fr: SecondFrame) -> list[WallEpisode]:
    last: dict[tuple[str, str], int] = {}
    out: list[WallEpisode] = []
    for i, s in enumerate(fr.samples):
        if not s.ok:
            continue
        for side, bins in (("ASK", s.ask_bins), ("BID", s.bid_bins)):
            for bin_low, w in bins.items():
                d = abs(float(w["distance_bps"]))
                if not (WALL_PROX_BPS_LO <= d <= WALL_PROX_BPS_HI):
                    continue
                key = (side, bin_low)
                if key in last and s.ms - last[key] < T2_GAP_MS:
                    continue
                last[key] = s.ms
                out.append(WallEpisode(side=side, bin_low=bin_low,
                                       bin_high=float(w["bin_high"]),
                                       wall_price=float(w["price"]), anchor_ms=s.ms,
                                       anchor_index=i, distance_bps=d))
    out.sort(key=lambda e: (e.anchor_ms, e.side, e.bin_low))
    return out


def _bin_present(fr: SecondFrame, i: int, side: str, bin_low: str) -> bool:
    s = fr.samples[i]
    return bin_low in (s.ask_bins if side == "ASK" else s.bid_bins)


def wall_family(fr: SecondFrame, eps: list[WallEpisode], fam: str) -> dict:
    _, side, lean, _ = FAMILIES[fam]
    rows = []
    excluded = {"NO_DECISION_SAMPLE": 0, "DECISION_NOT_OK": 0, "FLOW_NOT_COMPLETE": 0,
                "CANCELLED_NOT_CONSUMED": 0}
    for e in eps:
        if e.side != side:
            continue
        j = fr.index_after(e.anchor_index, WALL_W_MS)
        if j is None:
            excluded["NO_DECISION_SAMPLE"] += 1
            continue
        sj = fr.samples[j]
        if not sj.ok:
            excluded["DECISION_NOT_OK"] += 1
            continue
        if not sj.flow60_complete:
            excluded["FLOW_NOT_COMPLETE"] += 1
            continue
        present_at_j = _bin_present(fr, j, side, e.bin_low)
        gap_inside = any(not _bin_present(fr, k, side, e.bin_low)
                         for k in range(e.anchor_index + 1, j))
        path = fr.mids[e.anchor_index + 1:j + 1]
        path = path[np.isfinite(path)]
        traded_through = bool(path.size and (float(path.max()) >= e.bin_high if side == "ASK"
                                             else float(path.min()) <= float(e.bin_low)))
        dom_buy = _buy_dominant(sj.flow60_imb, "SIGN", 0.0, "BUY")
        dom_sell = _buy_dominant(sj.flow60_imb, "SIGN", 0.0, "SELL")
        if fam == "G1":
            fires = bool(present_at_j and dom_buy)
        elif fam == "G2":
            if not present_at_j and not traded_through:
                excluded["CANCELLED_NOT_CONSUMED"] += 1
                continue
            fires = bool((not present_at_j) and traded_through and dom_buy)
        elif fam == "G3":
            fires = bool(gap_inside and present_at_j)
        elif fam == "G4":
            fires = bool(present_at_j and dom_sell)
        elif fam == "G5":
            if not present_at_j and not traded_through:
                excluded["CANCELLED_NOT_CONSUMED"] += 1
                continue
            fires = bool((not present_at_j) and traded_through and dom_sell)
        elif fam == "G6":
            fires = bool(gap_inside and present_at_j)
        else:
            raise KeyError(fam)
        rows.append({"anchor_ms": e.anchor_ms, "bin_low": e.bin_low, "bin_high": e.bin_high,
                     "level_price": e.wall_price, "distance_bps": e.distance_bps,
                     "i_dec": j, "fires": fires, "flow60_imb": sj.flow60_imb,
                     "wall_present_at_w": bool(present_at_j), "gap_inside_w": bool(gap_inside),
                     "traded_through": traded_through})
    return {"family": fam, "variant": "SIGN", "side": side, "lean": lean, "w": 60,
            "episodes": rows, "excluded": excluded}


# ----------------------------------------------------------------------------- T1 episodes
def joint_family(mf: MinuteFrame, sf: SecondFrame, eps: list[MinuteEpisode], fam: str,
                 variant: str) -> dict:
    """T1: a structural approach that also has a wall inside 10 bp on the matching side.

    The structural level supplies the position, the wall supplies the Layer B condition, and the
    60s flow window at the decision second supplies Layer C. The journal is 3.61 hours long, so the
    count this returns is the measurement that matters most here.
    """
    _, side, lean, _ = FAMILIES[fam]
    wall_side = "ASK" if side == "RES" else "BID"
    rows = []
    reasons = {"OUTSIDE_JOURNAL": 0, "NO_SAMPLE_AT_MINUTE": 0, "ANCHOR_NOT_OK": 0,
               "NO_WALL_IN_BAND": 0, "NO_DECISION_SAMPLE": 0, "DECISION_NOT_OK": 0,
               "FLOW_NOT_COMPLETE": 0}
    for e in eps:
        if e.side != side:
            continue
        info_ms = e.anchor_ts + MINUTE_MS
        if not (sf.ms[0] <= info_ms <= sf.ms[-1]):
            reasons["OUTSIDE_JOURNAL"] += 1
            continue
        i = int(np.searchsorted(sf.ms, info_ms, side="left"))
        if i >= len(sf.ms) or sf.ms[i] - info_ms > 5_000:
            reasons["NO_SAMPLE_AT_MINUTE"] += 1
            continue
        s = sf.samples[i]
        if not s.ok:
            reasons["ANCHOR_NOT_OK"] += 1
            continue
        bins = s.ask_bins if wall_side == "ASK" else s.bid_bins
        near = [(b, w) for b, w in bins.items()
                if WALL_PROX_BPS_LO <= abs(float(w["distance_bps"])) <= WALL_PROX_BPS_HI]
        if not near:
            reasons["NO_WALL_IN_BAND"] += 1
            continue
        bin_low, wall = sorted(near, key=lambda bw: abs(float(bw[1]["distance_bps"])))[0]
        j = sf.index_after(i, WALL_W_MS)
        if j is None:
            reasons["NO_DECISION_SAMPLE"] += 1
            continue
        sj = sf.samples[j]
        if not sj.ok:
            reasons["DECISION_NOT_OK"] += 1
            continue
        if not sj.flow60_complete:
            reasons["FLOW_NOT_COMPLETE"] += 1
            continue
        bin_high = float(wall["bin_high"])
        present_at_j = _bin_present(sf, j, wall_side, bin_low)
        path = sf.mids[i + 1:j + 1]
        path = path[np.isfinite(path)]
        traded_through = bool(path.size and (float(path.max()) >= bin_high if wall_side == "ASK"
                                             else float(path.min()) <= float(bin_low)))
        want = "BUY" if side == "RES" else "SELL"
        dom = _buy_dominant(sj.flow60_imb, variant if variant == "SIGN" else "SIGN", 0.0, want)
        gate = e.level_price * (1 + RT_COST_BP / 1e4) if side == "RES" \
            else e.level_price * (1 - RT_COST_BP / 1e4)
        mid_j = float(sf.mids[j])
        cleared = mid_j > gate if side == "RES" else mid_j < gate
        thinner_ahead = None
        if s.band_ask is not None and s.band_bid is not None:
            thinner_ahead = (s.band_ask < s.band_bid) if side == "RES" else (s.band_bid < s.band_ask)
        if fam in ("F1", "F3"):
            fires = bool(present_at_j and dom and not cleared)
        else:
            fires = bool((not present_at_j) and traded_through and dom and bool(thinner_ahead))
        rows.append({"anchor_ts": e.anchor_ts, "level_id": e.level_id,
                     "level_price": e.level_price, "wall_bin_low": bin_low,
                     "wall_distance_bps": abs(float(wall["distance_bps"])),
                     "i_dec": j, "fires": fires, "flow60_imb": sj.flow60_imb,
                     "wall_present_at_w": bool(present_at_j), "traded_through": traded_through,
                     "thinner_ahead": thinner_ahead, "cleared": bool(cleared)})
    return {"family": fam, "variant": variant, "side": side, "lean": lean, "w": 60,
            "episodes": rows, "excluded": reasons}
