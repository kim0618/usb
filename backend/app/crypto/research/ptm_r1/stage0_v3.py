"""PTM-R1 V3 Stage 0: 1D-only thesis census on TRAIN (contract V3), with V1/V2 state comparison.

Counts, durations and state sequences only. No price is parsed; the output is scanned for
performance keys (contract V3 list: return, PnL, gross, net, MFE, MAE, win rate, PF, profit, loss).

Run: python -m app.crypto.research.ptm_r1.stage0_v3  (from backend/)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from . import data, stage0_v2, v3
from .stage0 import FORBIDDEN, forbidden_keys
from .states import all_states, states_v2a, states_v2b

DOCS = data.ROOT / "docs/crypto/personal_trading_method_r1"
CONTRACTS = {k: DOCS / f"CONTRACT_PTM_R1_{k}.md" for k in ("V1", "V2", "V3")}
OUT = data.ROOT / "data/research/crypto/ptm_r1/stage0_v3.json"
FORBIDDEN_V3 = FORBIDDEN + ("net", "loss")


def shas() -> dict:
    return {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in CONTRACTS.items()}


def leak_check(vol: data.Volumes, days: int = 20, cut: int = 10 * 1440 + 737) -> dict:
    """Rewrite every minute after `cut` as all-buy: everything up to `cut` must be unchanged."""
    m = days * 1440
    base = data.Volumes(ts=vol.ts[:m], quote=vol.quote[:m], taker=vol.taker[:m], valid=vol.valid[:m])
    taker = base.taker.copy()
    taker[cut + 1:] = base.quote[cut + 1:]
    alt = data.Volumes(ts=base.ts, quote=base.quote, taker=taker, valid=base.valid)
    a, b = v3.states_v3(base), v3.states_v3(alt)
    res = {f"state_{k}_equal": bool(np.array_equal(a[k][:cut + 1], b[k][:cut + 1])) for k in a}
    res["setups_equal"] = ([s for s in v3.detect_setups(a) if s.t <= cut]
                           == [s for s in v3.detect_setups(b) if s.t <= cut])
    res["warnings_equal"] = all(
        np.array_equal(v3.warning_masks(a, d)[w][:cut + 1], v3.warning_masks(b, d)[w][:cut + 1])
        for d in (1, -1) for w in ("W1", "W2", "W3"))
    res["perturbation_visible_after_cut"] = any(
        not np.array_equal(a[k][cut + 1:], b[k][cut + 1:]) for k in a)
    res["pass"] = all(res.values())
    return res


def _pooled_v2(fam: dict, inv: str = "I4") -> dict:
    out = {}
    for h in ("12h", "24h", "48h"):
        s = [fam[d]["invalidation"][inv]["survival"][h] for d in ("LONG", "SHORT")]
        e = sum(x["eligible"] for x in s)
        out[h] = round(sum(x["thesis_alive"] for x in s) / e, 4) if e else None
    return out


def comparison(old: dict, new: dict) -> dict:
    rows = {}
    for name, c in old.items():
        rows[name] = {"D_flips_per_day": c["flips"]["D"]["changes_per_day"],
                      "thesis": "D=H4 regime (I4)",
                      "thesis_median_min": c["duration"]["REGIME"]["median_min"],
                      "families": {f: {"primary": c["families"][f]["primary_dedup_total"],
                                       "overlap_48h_N": c["families"][f]["overlap_adjusted_48h_N"],
                                       "survival": _pooled_v2(c["families"][f])}
                                   for f in ("F1", "F2", "F3")}}
    rows["V3"] = {"D_flips_per_day": new["flips"]["D"]["changes_per_day"],
                  "thesis": "completed 1D alone (I1)",
                  "thesis_median_min": new["thesis"]["duration"]["p50"],
                  "families": {f: {"primary": new["families"][f]["primary_dedup_total"],
                                   "overlap_48h_N": new["families"][f]["overlap_adjusted_48h_N"],
                                   "survival": {h: new["families"][f]["survival_pooled"][h]
                                                ["alive_share"] for h in ("12h", "24h", "48h")}}
                               for f in ("F1", "F2", "F3")}}
    return rows


def run(out: Path | None = OUT) -> dict:
    log = data.ReadLog()
    before = shas()
    par = data.parity(log)
    vol, cov = data.load_train(log)
    data_ok = par["pass"] and not cov["missing_minutes"] and not cov["invalid_minutes"]
    leak = leak_check(vol)
    c3 = v3.census(v3.states_v3(vol))
    old = {"V1": stage0_v2.census(all_states(vol)), "V2A": stage0_v2.census(states_v2a(vol)),
           "V2B": stage0_v2.census(states_v2b(vol))}
    judgement = v3.judge(c3, data_ok, leak["pass"])
    result = {"study": "PTM-R1", "stage": "0-V3", "split": "TRAIN", "contract_sha256": before,
              "parity": par, "train_coverage": cov, "files_read": log.entries,
              "leak_check": leak, "census_v3": c3, "comparison": comparison(old, c3),
              "judgement": judgement, "verdict": judgement["verdict"]}
    if shas() != before:
        raise RuntimeError("contract changed during the run")
    bad = forbidden_keys(result, tokens=FORBIDDEN_V3)
    if bad:
        raise RuntimeError(f"forbidden performance keys in output: {bad}")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    r = run(target)
    json.dump({"verdict": r["verdict"], "contract_sha256": r["contract_sha256"],
               "parity": r["parity"]["pass"], "leak": r["leak_check"]["pass"]}, sys.stdout, indent=1)
    print()
