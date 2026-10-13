"""PTM-R1 V2 Stage 0: V1 vs V2-A vs V2-B state census on TRAIN (contract V2 section 5).

Counts and durations only; no price is parsed and the output is scanned for performance keys.

Run: python -m app.crypto.research.ptm_r1.stage0_v2  (from backend/)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from . import data
from .stage0 import _flips, _q, forbidden_keys
from .states import (FAMILIES, INVALIDATIONS, MAX_HOLD_MIN, NONE, all_states, big_direction,
                     dedup_family_48h, dedup_one_position, dedup_primary, detect_setups,
                     invalidation_masks, next_true, regime_ids, run_lengths, states_v2a,
                     states_v2b)

DOCS = data.ROOT / "docs/crypto/personal_trading_method_r1"
CONTRACT_V1 = DOCS / "CONTRACT_PTM_R1_V1.md"
CONTRACT_V2 = DOCS / "CONTRACT_PTM_R1_V2.md"
OUT = data.ROOT / "data/research/crypto/ptm_r1/stage0_v2.json"
SURVIVAL_H = (4, 8, 12, 24, 48)
STUDIED = ("F1", "F2", "F3")
ORDER = ("M", "H1", "H4", "D")
DIR_NAME = {1: "LONG", -1: "SHORT"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dur(runs: np.ndarray) -> dict | None:
    if len(runs) == 0:
        return None
    return {"runs": int(len(runs)), "median_min": float(np.median(runs)),
            "mean_min": round(float(runs.mean()), 1),
            "p90_min": float(np.percentile(runs, 90))}


def census(st: dict[str, np.ndarray]) -> dict:
    n = len(st["D"])
    first = int(np.argmax(np.all([st[k] != NONE for k in ORDER], axis=0)))
    ev = slice(first, n)
    days = (n - first) / 1440

    freq, flips, dur = {}, {}, {}
    for k in ORDER:
        s = st[k][ev]
        freq[k] = {lab: round(float((s == v).mean() * 100), 3)
                   for lab, v in (("BUY_pct", 1), ("SELL_pct", -1), ("EVEN_pct", 0),
                                  ("NONE_pct", NONE))}
        flips[k] = _flips(s, days)
        dur[k] = _dur(run_lengths(s))

    d, h4, h1 = st["D"][ev], st["H4"][ev], st["H1"][ev]
    nz = (d == 1) | (d == -1)
    align = {"D_H4_pct": round(float((nz & (d == h4)).mean() * 100), 3),
             "D_H4_H1_pct": round(float((nz & (d == h4) & (h4 == h1)).mean() * 100), 3)}

    bigdir = big_direction(st)
    regime = regime_ids(bigdir)
    dur["REGIME"] = _dur(run_lengths(bigdir[ev]))

    setups = detect_setups(st)
    keep_p = dedup_primary(setups, regime)
    keep_f = dedup_family_48h(setups)
    keep_g = dedup_one_position(setups)
    nxt = {dirn: {k: next_true(v) for k, v in invalidation_masks(st, dirn).items()}
           for dirn in (1, -1)}
    last = n - 1

    fams: dict = {}
    for fam in FAMILIES:
        idx = [i for i, s in enumerate(setups) if s.family == fam]
        f = {"overlap_adjusted_48h_N": sum(1 for i in idx if keep_f[i]),
             "all_family_one_position_48h_N": sum(1 for i in idx if keep_g[i])}
        for dirn in (1, -1):
            mine = [i for i in idx if setups[i].direction == dirn]
            acc = [setups[i].t for i in mine if keep_p[i]]
            inval = {}
            for k in INVALIDATIONS:
                js = [int(nxt[dirn][k][t + 1]) if t + 1 <= last else n for t in acc]
                obs = [j - t for t, j in zip(acc, js) if j <= last]
                within = sum(1 for x in obs if x <= MAX_HOLD_MIN)
                surv = {}
                for h in SURVIVAL_H:
                    hm = h * 60
                    elig = [(t, j) for t, j in zip(acc, js) if j <= last or t + hm <= last]
                    alive = sum(1 for t, j in elig if j - t > hm)
                    surv[f"{h}h"] = {"eligible": len(elig), "thesis_alive": alive,
                                     "alive_share": round(alive / len(elig), 4) if elig else None}
                eligible48 = sum(1 for t, j in zip(acc, js) if j <= last or t + MAX_HOLD_MIN <= last)
                inval[k] = {"invalidated_within_48h": within,
                            "within_48h_share": round(within / eligible48, 4) if eligible48 else None,
                            "minutes_to_invalidation": _q(obs), "survival": surv}
            ts = [setups[i].t for i in mine]
            f[DIR_NAME[dirn]] = {"raw": len(ts), "primary_dedup": len(acc),
                                 "interval_minutes_primary": _q(np.diff(acc)) if len(acc) > 1
                                 else None, "invalidation": inval}
        f["primary_dedup_total"] = f["LONG"]["primary_dedup"] + f["SHORT"]["primary_dedup"]
        fams[fam] = f

    return {"evaluation": {"first_index_all_defined": first, "evaluated_days": round(days, 3)},
            "state_frequency": freq, "flips": flips, "duration": dur, "alignment": align,
            "regimes": int(len(np.unique(regime[ev]))), "setups_total_raw": len(setups),
            "families": fams}


def hold_length(fam: dict) -> tuple[float | None, int]:
    """I4 survival share at 12 h and LONG+SHORT setups alive at 24 h."""
    s12 = [fam[d]["invalidation"]["I4"]["survival"]["12h"] for d in ("LONG", "SHORT")]
    s24 = [fam[d]["invalidation"]["I4"]["survival"]["24h"] for d in ("LONG", "SHORT")]
    elig = sum(x["eligible"] for x in s12)
    share12 = sum(x["thesis_alive"] for x in s12) / elig if elig else None
    return share12, sum(x["thesis_alive"] for x in s24)


def judge(v1: dict, v2: dict, data_ok: bool, leak_ok: bool) -> dict:
    d_v1, d_v2 = v1["flips"]["D"]["changes_per_day"], v2["flips"]["D"]["changes_per_day"]
    r_v1 = v1["duration"]["REGIME"]["median_min"]
    r_v2 = (v2["duration"]["REGIME"] or {}).get("median_min", 0)
    slower = d_v2 <= d_v1 / 3 and r_v2 >= 3 * r_v1
    per_fam = {}
    for fam in STUDIED:
        share12, alive24 = hold_length(v2["families"][fam])
        hl = share12 is not None and share12 >= 0.20 and alive24 >= 100
        n48 = v2["families"][fam]["overlap_adjusted_48h_N"]
        per_fam[fam] = {"I4_alive_share_12h": None if share12 is None else round(share12, 4),
                        "I4_alive_24h_count": alive24, "HOLD_LENGTH": hl,
                        "overlap_adjusted_48h_N": n48, "SAMPLE": n48 >= 100}
    if not data_ok or not leak_ok:
        verdict = "D_CONTRACT_OR_DATA_PROBLEM"
    elif not slower or not any(p["HOLD_LENGTH"] for p in per_fam.values()):
        verdict = "A_V2_THESIS_TOO_FAST"
    elif not any(p["HOLD_LENGTH"] and p["SAMPLE"] for p in per_fam.values()):
        verdict = "C_FAMILY_TOO_RARE"
    else:
        verdict = "B_V2_RESEARCHABLE"
    return {"SLOWER": slower,
            "D_flips_per_day": {"V1": d_v1, "variant": d_v2, "limit": round(d_v1 / 3, 3)},
            "regime_median_min": {"V1": r_v1, "variant": r_v2, "need": 3 * r_v1},
            "families": per_fam, "verdict": verdict}


def leak_check(vol: data.Volumes, days: int = 20, cut: int = 10 * 1440 + 737) -> dict:
    """Rewrite every minute after `cut` as all-buy; states and setups up to `cut` must not move."""
    m = days * 1440
    base = data.Volumes(ts=vol.ts[:m], quote=vol.quote[:m], taker=vol.taker[:m], valid=vol.valid[:m])
    taker = base.taker.copy()
    taker[cut + 1:] = base.quote[cut + 1:]
    alt = data.Volumes(ts=base.ts, quote=base.quote, taker=taker, valid=base.valid)
    out = {}
    for name, fn in (("V1", all_states), ("V2A", states_v2a), ("V2B", states_v2b)):
        a, b = fn(base), fn(alt)
        same = all(np.array_equal(a[k][:cut + 1], b[k][:cut + 1]) for k in a)
        sa = [x for x in detect_setups(a) if x.t <= cut]
        sb = [x for x in detect_setups(b) if x.t <= cut]
        changed_after = any(not np.array_equal(a[k][cut + 1:], b[k][cut + 1:]) for k in a)
        out[name] = {"states_equal_up_to_cut": same, "setups_equal_up_to_cut": sa == sb,
                     "perturbation_visible_after_cut": changed_after}
    out["pass"] = all(v["states_equal_up_to_cut"] and v["setups_equal_up_to_cut"]
                      and v["perturbation_visible_after_cut"] for v in out.values())
    return out


def run(out: Path | None = OUT) -> dict:
    log = data.ReadLog()
    shas = {"v1": sha(CONTRACT_V1), "v2": sha(CONTRACT_V2)}
    par = data.parity(log)
    vol, cov = data.load_train(log)
    data_ok = par["pass"] and not cov["missing_minutes"] and not cov["invalid_minutes"]
    leak = leak_check(vol)
    leak_ok = leak["pass"]
    res = {"V1": census(all_states(vol)), "V2A": census(states_v2a(vol)),
           "V2B": census(states_v2b(vol))}
    verdicts = {v: judge(res["V1"], res[v], data_ok, leak_ok) for v in ("V2A", "V2B")}
    overall = (verdicts["V2A"]["verdict"] if verdicts["V2A"]["verdict"].startswith("B")
               else verdicts["V2B"]["verdict"] if verdicts["V2B"]["verdict"].startswith("B")
               else verdicts["V2A"]["verdict"])
    result = {"study": "PTM-R1", "stage": "0-V2", "split": "TRAIN",
              "contract_sha256": shas, "parity": par, "train_coverage": cov,
              "files_read": log.entries, "leak_check": leak, "census": res, "judgement": verdicts,
              "verdict": overall}
    if {"v1": sha(CONTRACT_V1), "v2": sha(CONTRACT_V2)} != shas:
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
    json.dump({"verdict": r["verdict"], "contract_sha256": r["contract_sha256"],
               "parity": r["parity"]["pass"]}, sys.stdout, indent=1)
    print()
