"""PTM-R1 V3: 1D-only thesis, entry families, warnings (contract V3). No prices.

States are V2-A's arrays (completed UTC day / 4 h / 1 h blocks, rolling 10 m). What V3 changes is
the meaning: D alone is the thesis; H4, H1 and M are entry conditions and warnings.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import Volumes
from .stage0 import _flips, _q
from .states import MAX_HOLD_MIN, NONE, next_true, run_lengths, states_v2a

FAMILIES = ("F1", "F2", "F3", "UNCLASSIFIED_EVEN")
STUDIED = ("F1", "F2", "F3")
ORDER = ("M", "H1", "H4", "D")
SURVIVAL_H = (4, 8, 12, 24, 48)
DIR_NAME = {1: "LONG", -1: "SHORT"}


def states_v3(vol: Volumes) -> dict[str, np.ndarray]:
    return states_v2a(vol)


@dataclass(frozen=True)
class SetupV3:
    t: int
    direction: int
    family: str
    tag: str


def _prefix(mask: np.ndarray) -> np.ndarray:
    return np.concatenate(([0], np.cumsum(mask)))


def detect_setups(st: dict[str, np.ndarray]) -> list[SetupV3]:
    m, h1, h4, d = st["M"], st["H1"], st["H4"], st["D"]
    defined = (m != NONE) & (h1 != NONE) & (h4 != NONE) & (d != NONE)
    if not defined.any():
        return []
    first = int(np.argmax(defined))
    out: list[SetupV3] = []
    for dirn in (1, -1):
        full = (m == dirn) & (h1 == dirn) & (h4 == dirn) & (d == dirn)
        d_off = _prefix(d != dirn)
        h4_off, h4_opp = _prefix(h4 != dirn), _prefix(h4 == -dirn)
        h1_off, h1_opp = _prefix(h1 != dirn), _prefix(h1 == -dirn)
        m_opp = _prefix(m == -dirn)
        onset = np.flatnonzero(full & ~np.concatenate(([False], full[:-1])))
        full_idx = np.flatnonzero(full)
        for t in onset:
            if t < first:
                continue
            k = np.searchsorted(full_idx, t) - 1
            g0 = max(first, int(full_idx[k]) + 1) if k >= 0 else first
            g1 = int(t)

            def any_(p):
                return p[g1] - p[g0] > 0

            if g1 <= g0 or any_(d_off):
                fam, tag = "F1", "THESIS_START"
            elif any_(h4_off):
                fam, tag = ("F3", "H4_REALIGN") if any_(h4_opp) else ("UNCLASSIFIED_EVEN", "H4_EVEN")
            elif any_(h1_off):
                fam, tag = ("F2", "H1_REALIGN") if any_(h1_opp) else ("UNCLASSIFIED_EVEN", "H1_EVEN")
            else:
                fam, tag = ("F1", "M_REALIGN") if any_(m_opp) else ("UNCLASSIFIED_EVEN", "M_EVEN")
            out.append(SetupV3(t=int(t), direction=dirn, family=fam, tag=tag))
    out.sort(key=lambda s: (s.t, s.direction))
    return out


def thesis_episode_ids(d: np.ndarray) -> np.ndarray:
    change = np.concatenate(([True], d[1:] != d[:-1]))
    return np.cumsum(change)


def episodes(d: np.ndarray) -> list[tuple[int, int, int]]:
    """(start, end_exclusive, dir) of non-zero D runs, runs touching either series end dropped."""
    edges = np.flatnonzero(d[1:] != d[:-1]) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(d)]))
    return [(int(s), int(e), int(d[s])) for s, e in zip(starts, ends)
            if d[s] in (1, -1) and s > 0 and e < len(d)]


def dedup_primary(setups: list[SetupV3], episode: np.ndarray) -> list[bool]:
    last: dict[tuple[str, int], int] = {}
    keep = []
    for s in setups:
        key = (s.family, s.direction)
        prev = last.get(key)
        dup = prev is not None and episode[prev] == episode[s.t] and s.t - prev < MAX_HOLD_MIN
        keep.append(not dup)
        if not dup:
            last[key] = s.t
    return keep


def dedup_48h(setups: list[SetupV3], per_family: bool) -> list[bool]:
    last: dict[str, int] = {}
    keep = []
    for s in setups:
        key = s.family if per_family else "*"
        prev = last.get(key)
        ok = prev is None or s.t - prev >= MAX_HOLD_MIN
        keep.append(ok)
        if ok:
            last[key] = s.t
    return keep


def warning_masks(st: dict[str, np.ndarray], dirn: int) -> dict[str, np.ndarray]:
    m, h1, h4 = st["M"], st["H1"], st["H4"]
    return {"W1": h4 == -dirn, "W2": (h1 == -dirn) & (h4 == -dirn),
            "W3": (m == -dirn) & (h1 == -dirn) & (h4 == -dirn)}


def survival(acc: list[int], js: list[int], last: int) -> dict:
    out = {}
    for h in SURVIVAL_H:
        hm = h * 60
        elig = [(t, j) for t, j in zip(acc, js) if j <= last or t + hm <= last]
        alive = sum(1 for t, j in elig if j - t > hm)
        out[f"{h}h"] = {"eligible": len(elig), "thesis_alive": alive,
                        "alive_share": round(alive / len(elig), 4) if elig else None}
    return out


def _onsets(mask: np.ndarray) -> np.ndarray:
    return mask & ~np.concatenate(([False], mask[:-1]))


def census(st: dict[str, np.ndarray]) -> dict:
    n = len(st["D"])
    last = n - 1
    d = st["D"]
    first = int(np.argmax(np.all([st[k] != NONE for k in ORDER], axis=0)))
    ev = slice(first, n)
    days = (n - first) / 1440

    freq = {k: {lab: round(float((st[k][ev] == v).mean() * 100), 3)
                for lab, v in (("BUY_pct", 1), ("SELL_pct", -1), ("EVEN_pct", 0),
                               ("NONE_pct", NONE))} for k in ORDER}
    flips = {k: _flips(st[k][ev], days) for k in ORDER}
    eps = episodes(d[ev])
    ep_len = np.array([e - s for s, e, _ in eps], dtype=np.int64)
    thesis = {"episodes_complete": len(eps), "duration": _q(ep_len),
              "duration_days_hist": {str(k): int(v) for k, v in
                                     zip(*np.unique(ep_len // 1440, return_counts=True))},
              "long_episodes": sum(1 for *_, x in eps if x == 1),
              "short_episodes": sum(1 for *_, x in eps if x == -1),
              "neutral_days": int(((d[ev] == 0)).sum() // 1440),
              "flips_per_day": flips["D"]["changes_per_day"]}

    # warnings over all thesis minutes
    dz = d[ev]
    thz = (dz == 1) | (dz == -1)
    warn = {}
    for w in ("W1", "W2", "W3"):
        mask = np.zeros(len(dz), dtype=bool)
        for dirn in (1, -1):
            mask |= (dz == dirn) & warning_masks({k: st[k][ev] for k in ORDER}, dirn)[w]
        runs = run_lengths(mask.astype(np.int8), values=(1,))
        warn[w] = {"share_of_thesis_minutes_pct": round(float(mask.sum() / thz.sum() * 100), 3),
                   "runs": int(_onsets(mask).sum()), "run_minutes": _q(runs),
                   "runs_per_thesis_day": round(float(_onsets(mask).sum() / (thz.sum() / 1440)), 3)}

    # per-episode lower-horizon changes and H4 excursions
    sub = {k: st[k][ev] for k in ORDER}
    ch = {k: _prefix(np.concatenate(([False], sub[k][1:] != sub[k][:-1]))) for k in ("H4", "H1", "M")}
    per_ep = {k: [] for k in ("H4", "H1", "M")}
    exc_total = exc_ret = exc_open = exc_other = ep_with_exc = 0
    for s, e, dirn in eps:
        for k in per_ep:
            per_ep[k].append(int(ch[k][e] - ch[k][s + 1]))
        h4 = sub["H4"][s:e]
        opp = h4 == -dirn
        ep_with_exc += bool(opp.any())
        starts = np.flatnonzero(_onsets(opp))
        exc_total += len(starts)
        for a in starts:
            rest = np.flatnonzero(~opp[a:])
            if len(rest) == 0:
                exc_open += 1                      # still against when the thesis ends
            elif h4[a + int(rest[0])] == dirn:
                exc_ret += 1
            else:
                exc_other += 1                     # left through EVEN/NONE
    total_days = ep_len.sum() / 1440 if len(ep_len) else 0
    within = {k: {"per_episode": _q(v), "per_thesis_day": round(sum(v) / total_days, 3)
                  if total_days else None} for k, v in per_ep.items()}
    excursion = {"episodes": len(eps), "episodes_with_H4_against": ep_with_exc,
                 "H4_excursions_against": exc_total, "returned_to_thesis_dir": exc_ret,
                 "still_against_at_episode_end": exc_open, "left_via_even_or_none": exc_other,
                 "returned_share": round(exc_ret / exc_total, 4) if exc_total else None}

    # setups
    episode = thesis_episode_ids(d)
    setups = detect_setups(st)
    keep_p = dedup_primary(setups, episode)
    keep_f = dedup_48h(setups, per_family=True)
    keep_a = dedup_48h(setups, per_family=False)
    nxt_i1 = {dirn: next_true(d == -dirn) for dirn in (1, -1)}
    nxt_neu = {dirn: next_true((d == 0) | (d == NONE)) for dirn in (1, -1)}
    nxt_w1 = {dirn: next_true(st["H4"] == -dirn) for dirn in (1, -1)}
    w1_on = {dirn: _prefix(_onsets(st["H4"] == -dirn)) for dirn in (1, -1)}

    fams: dict = {}
    for fam in FAMILIES:
        idx = [i for i, s in enumerate(setups) if s.family == fam]
        f: dict = {"overlap_adjusted_48h_N": sum(1 for i in idx if keep_f[i]),
                   "all_family_one_position_48h_N": sum(1 for i in idx if keep_a[i])}
        pooled_acc, pooled_js = [], []
        seq = {"eligible": 0, "W1_before_I1": 0, "I1_after_W1_within_48h": 0,
               "I1_within_48h": 0, "I1_while_W1_active": 0, "W1_runs_before_end": [],
               "neutral_before_I1_within_48h": 0}
        for dirn in (1, -1):
            mine = [i for i in idx if setups[i].direction == dirn]
            acc = [setups[i].t for i in mine if keep_p[i]]
            js = [int(nxt_i1[dirn][t + 1]) if t + 1 <= last else n for t in acc]
            obs = [j - t for t, j in zip(acc, js) if j <= last]
            el48 = [(t, j) for t, j in zip(acc, js) if j <= last or t + MAX_HOLD_MIN <= last]
            within48 = sum(1 for t, j in el48 if j - t <= MAX_HOLD_MIN)
            for t, j in zip(acc, js):
                if t + MAX_HOLD_MIN > last:
                    continue
                end = min(j, t + MAX_HOLD_MIN)
                seq["eligible"] += 1
                fw = int(nxt_w1[dirn][t + 1])
                i1_in = j <= t + MAX_HOLD_MIN
                seq["I1_within_48h"] += i1_in
                if fw < end:
                    seq["W1_before_I1"] += 1
                    seq["I1_after_W1_within_48h"] += i1_in
                if i1_in and st["H4"][j] == -dirn:
                    seq["I1_while_W1_active"] += 1
                seq["W1_runs_before_end"].append(int(w1_on[dirn][end + 1] - w1_on[dirn][t + 1]))
                nn = int(nxt_neu[dirn][t + 1])
                seq["neutral_before_I1_within_48h"] += nn < j and nn <= t + MAX_HOLD_MIN
            tags: dict[str, int] = {}
            for i in mine:
                tags[setups[i].tag] = tags.get(setups[i].tag, 0) + 1
            f[DIR_NAME[dirn]] = {
                "raw": len(mine), "primary_dedup": len(acc), "raw_by_tag": tags,
                "interval_minutes_primary": _q(np.diff(acc)) if len(acc) > 1 else None,
                "I1_within_48h": within48,
                "I1_within_48h_share": round(within48 / len(el48), 4) if el48 else None,
                "minutes_to_I1": _q(obs), "survival": survival(acc, js, last)}
            pooled_acc += acc
            pooled_js += js
        e = seq["eligible"]
        runs = seq.pop("W1_runs_before_end")
        seq.update({
            "W1_before_I1_share": round(seq["W1_before_I1"] / e, 4) if e else None,
            "I1_after_W1_share": round(seq["I1_after_W1_within_48h"] / seq["W1_before_I1"], 4)
            if seq["W1_before_I1"] else None,
            "I1_while_W1_active_share": round(seq["I1_while_W1_active"] / seq["I1_within_48h"], 4)
            if seq["I1_within_48h"] else None,
            "W1_runs_before_I1_or_48h": _q(runs)})
        f["warning_sequence"] = seq
        f["survival_pooled"] = survival(pooled_acc, pooled_js, last)
        el = [(t, j) for t, j in zip(pooled_acc, pooled_js) if j <= last or t + MAX_HOLD_MIN <= last]
        f["I1_within_48h_share_pooled"] = (round(sum(1 for t, j in el if j - t <= MAX_HOLD_MIN)
                                                 / len(el), 4) if el else None)
        f["primary_dedup_total"] = f["LONG"]["primary_dedup"] + f["SHORT"]["primary_dedup"]
        f["long_share_primary"] = (round(f["LONG"]["primary_dedup"] / f["primary_dedup_total"], 4)
                                   if f["primary_dedup_total"] else None)
        fams[fam] = f

    return {"evaluation": {"first_index_all_defined": first, "evaluated_days": round(days, 3)},
            "state_frequency": freq, "flips": flips, "thesis": thesis, "warnings": warn,
            "within_thesis_changes": within, "H4_excursions": excursion,
            "setups_total_raw": len(setups), "families": fams}


def judge(c: dict, data_ok: bool, leak_ok: bool) -> dict:
    med = c["thesis"]["duration"]["p50"] if c["thesis"]["duration"] else 0
    per = {}
    for fam in STUDIED:
        f = c["families"][fam]
        s = f["survival_pooled"]
        sh = {h: s[h]["alive_share"] for h in ("12h", "24h", "48h")}
        q = all(v is not None for v in sh.values()) and sh["12h"] >= 0.40 and sh["24h"] >= 0.20 \
            and sh["48h"] >= 0.05
        per[fam] = {"survival_12h": sh["12h"], "survival_24h": sh["24h"], "survival_48h": sh["48h"],
                    "QUALIFIES": q, "overlap_adjusted_48h_N": f["overlap_adjusted_48h_N"],
                    "SAMPLE": f["overlap_adjusted_48h_N"] >= 100,
                    "I1_within_48h_share": f["I1_within_48h_share_pooled"]}
    too_slow = med > 96 * 60 and all((p["I1_within_48h_share"] or 0) < 0.25 for p in per.values())
    if not data_ok or not leak_ok:
        v = "E_CONTRACT_OR_DATA_PROBLEM"
    elif too_slow:
        v = "B_V3_THESIS_TOO_SLOW"
    elif med < 24 * 60 or not any(p["QUALIFIES"] for p in per.values()):
        v = "A_V3_THESIS_TOO_FAST"
    elif not any(p["QUALIFIES"] and p["SAMPLE"] for p in per.values()):
        v = "C_V3_FAMILY_TOO_RARE"
    else:
        v = "D_V3_RESEARCHABLE"
    return {"thesis_median_min": med, "TOO_SLOW": too_slow, "families": per, "verdict": v}
