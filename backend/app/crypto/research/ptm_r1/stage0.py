"""PTM-R1 Stage 0: state and occurrence census on TRAIN (contract section 14).

Outputs counts and durations only. The output is checked for forbidden performance keys before it
is written.

Run: python -m app.crypto.research.ptm_r1.stage0  (from backend/)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from . import data
from .states import (FAMILIES, HORIZONS, INVALIDATIONS, MAX_HOLD_MIN, NONE, all_states,
                     big_direction, dedup_one_position, dedup_primary, detect_setups,
                     invalidation_masks, next_true, regime_ids)

CONTRACT = data.ROOT / "docs/crypto/personal_trading_method_r1/CONTRACT_PTM_R1_V1.md"
OUT = data.ROOT / "data/research/crypto/ptm_r1/stage0_v1.json"
FORBIDDEN = ("return", "pnl", "mfe", "mae", "profit", "win_rate", "winrate", "pf", "price",
             "net_bp", "gross")
DIR_NAME = {1: "LONG", -1: "SHORT"}


def contract_sha() -> str:
    return hashlib.sha256(CONTRACT.read_bytes()).hexdigest()


def viability(n: int) -> str:
    return "TOO_RARE" if n < 100 else "LIMITED" if n < 500 else "RESEARCHABLE"


def _q(values: list[int] | np.ndarray) -> dict | None:
    if len(values) == 0:
        return None
    a = np.asarray(values, dtype=float)
    return {"n": int(len(a)), "mean": round(float(a.mean()), 1),
            **{f"p{p}": round(float(np.percentile(a, p)), 1) for p in (10, 25, 50, 75, 90)}}


def _flips(s: np.ndarray, days: float) -> dict:
    ok = (s[1:] != NONE) & (s[:-1] != NONE)
    change = ok & (s[1:] != s[:-1])
    direct = ok & (s[1:] * s[:-1] == -1)
    return {"changes": int(change.sum()), "buy_sell_direct": int(direct.sum()),
            "changes_per_day": round(change.sum() / days, 2),
            "changes_per_week": round(change.sum() / days * 7, 2)}


def census(vol: data.Volumes) -> dict:
    st = all_states(vol)
    n = len(vol.ts)
    first = int(np.argmax(np.all([st[k] != NONE for k, _ in HORIZONS], axis=0)))
    ev = slice(first, n)                            # all four horizons defined
    ev_min = n - first
    days = ev_min / 1440

    freq = {}
    for name, minutes in HORIZONS:
        s = st[name][ev]
        freq[name] = {"window_minutes": minutes,
                      **{lab: round(float((s == v).mean() * 100), 3)
                         for lab, v in (("BUY_pct", 1), ("SELL_pct", -1), ("EVEN_pct", 0),
                                        ("NONE_pct", NONE))},
                      "EVEN_count": int((s == 0).sum()), "NONE_count": int((s == NONE).sum())}
    flips = {name: _flips(st[name][ev], days) for name, _ in HORIZONS}

    d, h4, h1, m = (st[k][ev] for k in ("D", "H4", "H1", "M"))
    nz = (d == 1) | (d == -1)
    align = {
        "D_H4_pct": round(float((nz & (d == h4)).mean() * 100), 3),
        "D_H4_H1_pct": round(float((nz & (d == h4) & (h4 == h1)).mean() * 100), 3),
        "D_H4_H1_M_pct": round(float((nz & (d == h4) & (h4 == h1) & (h1 == m)).mean() * 100), 3),
        "NO_TRADE_pct": round(float((~(nz & (d == h4))).mean() * 100), 3),
    }

    bigdir = big_direction(st)
    regime = regime_ids(bigdir)
    setups = detect_setups(st)
    keep_p = dedup_primary(setups, regime)
    keep_g = dedup_one_position(setups)

    masks = {dirn: invalidation_masks(st, dirn) for dirn in (1, -1)}
    nxt = {dirn: {k: next_true(v) for k, v in masks[dirn].items()} for dirn in (1, -1)}
    last = n - 1

    fam_out: dict = {}
    for fam in FAMILIES:
        fam_out[fam] = {}
        for dirn in (1, -1):
            idx = [i for i, s in enumerate(setups) if s.family == fam and s.direction == dirn]
            ts = [setups[i].t for i in idx]
            acc = [setups[i].t for i in idx if keep_p[i]]
            inval = {}
            for k in INVALIDATIONS:
                obs, within, censored, never = [], 0, 0, 0
                for t in acc:
                    j = int(nxt[dirn][k][t + 1]) if t + 1 <= last else n
                    if j <= last:
                        dt = j - t
                        obs.append(dt)
                        within += dt <= MAX_HOLD_MIN
                    elif t + MAX_HOLD_MIN > last:
                        censored += 1
                    else:
                        never += 1
                done = len(acc) - censored
                inval[k] = {"accepted_setups": len(acc), "train_tail_censored": censored,
                            "invalidated_within_48h": within,
                            "within_48h_share": round(within / done, 4) if done else None,
                            "not_by_48h": done - within,
                            "minutes_to_invalidation": _q(obs),
                            "minutes_to_invalidation_within_48h":
                                _q([x for x in obs if x <= MAX_HOLD_MIN])}
            fam_out[fam][DIR_NAME[dirn]] = {
                "raw": len(ts), "primary_dedup": len(acc),
                "one_position_48h": sum(1 for i in idx if keep_g[i]),
                "duplicate_share_primary": round(1 - len(acc) / len(ts), 4) if ts else None,
                "interval_minutes_raw": _q(np.diff(ts)) if len(ts) > 1 else None,
                "interval_minutes_primary": _q(np.diff(acc)) if len(acc) > 1 else None,
                "invalidation": inval,
            }
        both = sum(fam_out[fam][x]["primary_dedup"] for x in ("LONG", "SHORT"))
        fam_out[fam]["primary_dedup_total"] = both
        fam_out[fam]["viability"] = viability(both) if fam != "UNCLASSIFIED_EVEN" else "NOT_STUDIED"

    return {
        "evaluation": {"first_index_all_defined": first, "first_boundary_open_ms":
                       int(vol.ts[first]), "evaluated_minutes": ev_min,
                       "evaluated_days": round(days, 3)},
        "state_frequency": freq, "flips": flips, "alignment": align,
        "setups_total_raw": len(setups),
        "duplicate_share_one_position_48h": round(1 - sum(keep_g) / len(setups), 4)
        if setups else None,
        "regimes": {"count": int(regime[-1]),
                    "long_regimes": int(len({regime[i] for i in np.flatnonzero(bigdir == 1)})),
                    "short_regimes": int(len({regime[i] for i in np.flatnonzero(bigdir == -1)}))},
        "families": fam_out,
    }


def forbidden_keys(obj, path="", tokens=FORBIDDEN) -> list[str]:
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            low = str(k).lower()
            if any(tok == low or low.startswith(tok + "_") or low.endswith("_" + tok)
                   or f"_{tok}_" in low for tok in tokens):
                bad.append(f"{path}/{k}")
            bad += forbidden_keys(v, f"{path}/{k}", tokens)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += forbidden_keys(v, f"{path}[{i}]", tokens)
    return bad


def verdict(par: dict, cov: dict, fams: dict) -> str:
    if not par["pass"] or cov["missing_minutes"] or cov["invalid_minutes"]:
        return "C_DATA_CONTRACT_PROBLEM"
    labels = [fams[f]["viability"] for f in ("F1", "F2", "F3")]
    if all(lab == "TOO_RARE" for lab in labels):
        return "A_FAMILY_TOO_RARE"
    return "B_STAGE0_RESEARCHABLE"


def run(out: Path | None = OUT) -> dict:
    log = data.ReadLog()
    sha_before = contract_sha()
    par = data.parity(log)
    vol, cov = data.load_train(log)
    body = census(vol)
    result = {
        "study": "PTM-R1", "stage": 0, "split": "TRAIN",
        "contract": str(CONTRACT.relative_to(data.ROOT)), "contract_sha256": sha_before,
        "parity": par, "train_coverage": cov, "files_read": log.entries, **body,
    }
    result["verdict"] = verdict(par, cov, body["families"])
    if contract_sha() != sha_before:
        raise RuntimeError("contract changed during the run")
    bad = forbidden_keys(result)
    if bad:
        raise RuntimeError(f"forbidden performance keys in output: {bad}")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    r = run()
    json.dump({k: r[k] for k in ("verdict", "contract_sha256", "parity", "train_coverage")},
              sys.stdout, indent=1)
    print()
