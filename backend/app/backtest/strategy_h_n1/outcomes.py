"""PHASE 3: forward outcomes, statistics, the 8-gate matrix and the verdict.

``compute`` refuses to run unless the contract checksum and the frozen cutoff digest verify.
"""

from __future__ import annotations

import math

import numpy as np

from app.backtest.strategy_h_n1.common import BENCH, HN1Stop, Panel, TickerMaps

PRIMARY_H = (5, 21, 63)
ALL_H = (1, 5, 21, 63)
COSTS = (0, 10, 25)
SEED, RESAMPLES = 20261019, 10_000


# ---------------------------------------------------------------- prices through the CIK chain

class Chain:
    def __init__(self, panel: Panel, maps: TickerMaps):
        self.p, self.m = panel, maps
        pres = np.isfinite(panel.close)
        self.pres = pres
        last = np.full(pres.shape[1], -1)
        any_ = pres.any(axis=0)
        last[any_] = pres.shape[0] - 1 - np.argmax(pres[::-1, any_], axis=0)
        self.last = last

    def _first_present(self, j: int, lo: int) -> int | None:
        col = self.pres[lo:, j]
        k = int(np.argmax(col)) if col.any() else -1
        return lo + k if k >= 0 else None

    def path(self, cik: str, tk: str, e: int, x: int) -> dict:
        """Price outcome from the entry open at e to the close of session x."""
        p = self.p
        j0 = p.tickers.get(tk)
        if j0 is None or not self.pres[e, j0]:
            cands = [t for t in self.m.any_snapshot(cik) if t in p.tickers and self.pres[e, p.tickers[t]]]
            if not cands:
                raise HN1Stop(f"entry row vanished for {cik} {tk} at {p.sessions[e]}")
            tk = cands[0]
        entry_open = float(p.open[e, p.tickers[tk]])
        used, cur, pos, factor = [tk], tk, e, 1.0
        while True:
            j = p.tickers[cur]
            L = int(self.last[j])
            if x <= L:
                u = self._first_present(j, x)
                factor *= p.split_factor(cur, pos, u)
                return {"status": "EXIT_GAP" if u > x else "OK", "exit_idx": u, "tickers": used,
                        "stock": float(p.close[u, j]) * factor / entry_open - 1.0}
            factor *= p.split_factor(cur, pos, L)
            best = None
            for t in self.m.any_snapshot(cik):
                if t in used or t not in p.tickers or L + 1 >= len(p.sessions):
                    continue
                f = self._first_present(p.tickers[t], L + 1)
                if f is not None and (best is None or f < best[1]):
                    best = (t, f)
            if best is None:
                obs = float(p.close[L, j]) * factor / entry_open - 1.0
                return {"status": "TRUE_DELISTING", "exit_idx": x, "last_idx": L, "tickers": used,
                        "stock": obs}
            # successor: carry the basis across the rename gap; splits of the successor in (L, first]
            cur, pos = best
            factor *= p.split_factor(cur, L, pos)
            used.append(cur)


def event_outcomes(ev: dict, chain: Chain) -> dict:
    p = chain.p
    e = ev["signal_idx"] + 1
    spy = p.tickers[BENCH]
    spy_open = float(p.open[e, spy])
    out = {}
    for h in ALL_H:
        x = e + h - 1
        if x >= len(p.sessions):
            continue
        r = chain.path(ev["cik"], ev["ticker"], e, x)
        u = r["exit_idx"]
        spy_ret = float(p.close[u, spy]) / spy_open - 1.0
        stock_obs = r["stock"]
        stock_cons = -1.0 if r["status"] == "TRUE_DELISTING" else stock_obs
        out[h] = {"status": r["status"], "ticker_changed": len(r["tickers"]) > 1,
                  "exit_session": p.sessions[u], "stock_obs": stock_obs, "spy": spy_ret,
                  "excess_obs": stock_obs - spy_ret, "excess_cons": stock_cons - spy_ret}
    # 63D overlap with the next 10-Q of the same CIK (diagnostic only)
    nx = ev.get("next_10q_filing_date")
    if 63 in out:
        x = e + 62
        s2 = p.first_after(nx) if nx else None
        ov = s2 is not None and s2 <= x
        out["overlap63"] = ov
        if ov and s2 >= e:
            r = chain.path(ev["cik"], ev["ticker"], e, s2)
            u = r["exit_idx"]
            out["censored63_excess"] = r["stock"] - (float(p.close[u, spy]) / spy_open - 1.0)
        else:
            out["censored63_excess"] = out[63]["excess_obs"]
    return out


# ---------------------------------------------------------------- statistics

def bootstrap_ci(values: np.ndarray, blocks: list[str], rng_seed: int = SEED, n: int = RESAMPLES):
    if values.size == 0:
        return (math.nan, math.nan)
    keys = sorted(set(blocks))
    k = {b: i for i, b in enumerate(keys)}
    bi = np.array([k[b] for b in blocks])
    sums = np.bincount(bi, weights=values, minlength=len(keys))
    cnts = np.bincount(bi, minlength=len(keys)).astype(float)
    rng = np.random.default_rng(rng_seed)
    draws = rng.integers(0, len(keys), size=(n, len(keys)))
    means = sums[draws].sum(axis=1) / cnts[draws].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def concentration(net10: np.ndarray) -> dict:
    n = net10.size
    if n == 0:
        return {"n": 0}
    s = np.sort(net10)[::-1]
    total = float(net10.sum())
    pos = float(net10[net10 > 0].sum())
    k1 = math.ceil(0.01 * n)
    k10 = math.ceil(0.10 * n)
    share = lambda m: float(s[:m].sum()) / total if total > 0 else math.nan
    pshare = lambda m: float(s[:m][s[:m] > 0].sum()) / pos if pos > 0 else math.nan
    return {"n": n, "net_total": total, "positive_sum": pos,
            "top1_share_of_net": share(1), "top5_share_of_net": share(5), "top10pct_share_of_net": share(k10),
            "top1_share_of_positive": pshare(1), "top5_share_of_positive": pshare(5),
            "top10pct_share_of_positive": pshare(k10),
            "top1pct_removed_n": k1, "top1pct_removed_mean": float(s[k1:].mean()) if n > k1 else math.nan}


def summarize(rows: list[dict], h: int, variant: str) -> dict:
    ex = np.array([r["out"][h][f"excess_{variant}"] for r in rows], dtype=float)
    blocks = [r["iso_week"] for r in rows]
    d = {"n": int(ex.size)}
    for c in COSTS:
        net = ex - c / 1e4
        d[f"mean_{c}"] = float(net.mean()) if ex.size else math.nan
        d[f"median_{c}"] = float(np.median(net)) if ex.size else math.nan
        d[f"win_{c}"] = float((net > 0).mean()) if ex.size else math.nan
    lo, hi = bootstrap_ci(ex, blocks)
    for c in COSTS:
        d[f"ci_lo_{c}"], d[f"ci_hi_{c}"] = lo - c / 1e4, hi - c / 1e4
    d["n_blocks"] = len(set(blocks))
    d["conc10"] = concentration(ex - 10 / 1e4)
    d["n_true_delisting"] = sum(1 for r in rows if r["out"][h]["status"] == "TRUE_DELISTING")
    d["n_exit_gap"] = sum(1 for r in rows if r["out"][h]["status"] == "EXIT_GAP")
    d["n_ticker_changed"] = sum(1 for r in rows if r["out"][h]["ticker_changed"])
    if ex.size:
        i, k = int(np.argmax(ex)), int(np.argmin(ex))
        d["largest_winner"] = {"ticker": rows[i]["ticker"], "signal": rows[i]["signal_session"],
                               "excess": float(ex[i])}
        d["largest_loser"] = {"ticker": rows[k]["ticker"], "signal": rows[k]["signal_session"],
                              "excess": float(ex[k])}
    return d


def seasons(rows: list[dict], h: int, variant: str) -> list[dict]:
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["season"], []).append(r["out"][h][f"excess_{variant}"] - 10 / 1e4)
    return [{"season": s, "n": len(v), "mean_10": float(np.mean(v)), "eligible": len(v) >= 30}
            for s, v in sorted(by.items())]


# ---------------------------------------------------------------- gates and verdict

def gate_matrix(S: dict, SEAS: dict, h: int, variant: str) -> dict:
    """S[(cohort, split, h, variant)] -> summary. Returns gate -> (bool|None, evidence)."""
    P = lambda sp, v=variant: S[("PRIMARY", sp, h, v)]
    C = lambda sp, v=variant: S[("CONTRAST", sp, h, v)]
    g = {}
    g["G1"] = (P("DEV")["mean_25"] > 0 and P("HOLDOUT")["mean_25"] > 0,
               f"mean25 dev {P('DEV')['mean_25']:+.4%} hold {P('HOLDOUT')['mean_25']:+.4%}")
    g["G2"] = (P("DEV")["median_10"] > 0 and P("HOLDOUT")["median_10"] > 0,
               f"median10 dev {P('DEV')['median_10']:+.4%} hold {P('HOLDOUT')['median_10']:+.4%}")
    g["G3"] = (P("DEV")["ci_lo_10"] > 0, f"dev CI10 [{P('DEV')['ci_lo_10']:+.4%}, {P('DEV')['ci_hi_10']:+.4%}]")
    cd, ch = P("DEV", "cons")["mean_10"], P("HOLDOUT", "cons")["mean_10"]
    g["G4"] = (cd > 0 and ch > 0, f"conservative mean10 dev {cd:+.4%} hold {ch:+.4%}")
    ok5, ev5 = True, []
    for sp in ("DEV", "HOLDOUT"):
        c = P(sp)["conc10"]
        t5 = c.get("top5_share_of_net", math.nan)
        rm = c.get("top1pct_removed_mean", math.nan)
        this = c.get("net_total", -1) > 0 and t5 < 0.5 and rm > 0
        ok5 &= bool(this)
        ev5.append(f"{sp.lower()} net {c.get('net_total', math.nan):+.3f} top5 {t5:.1%} top1%rm {rm:+.4%}")
    g["G5"] = (ok5, "; ".join(ev5))
    el = [s for s in SEAS[(h, variant)] if s["eligible"]]
    pos = sum(1 for s in el if s["mean_10"] > 0)
    g["G6"] = (len(el) > 0 and pos / len(el) >= 0.6, f"{pos}/{len(el)} eligible seasons positive")
    g["G7"] = (P("DEV")["mean_0"] > C("DEV")["mean_0"] and P("HOLDOUT")["mean_0"] > C("HOLDOUT")["mean_0"],
               f"dev P {P('DEV')['mean_0']:+.4%} vs C {C('DEV')['mean_0']:+.4%}; "
               f"hold P {P('HOLDOUT')['mean_0']:+.4%} vs C {C('HOLDOUT')['mean_0']:+.4%}")
    g["G8"] = shorter_consistency(S, h, variant)
    nd, nh = P("DEV")["n"], P("HOLDOUT")["n"]
    g["SAMPLE"] = (nd >= 300 and nh >= 300, f"N dev {nd} hold {nh}")
    return g


def shorter_consistency(S: dict, h: int, variant: str):
    need = {5: (), 21: (5,), 63: (5, 21)}[h]
    if not need:
        return (None, "N/A (5D)")
    vals = {(k, sp): S[("PRIMARY", sp, k, variant)]["mean_0"] for k in need for sp in ("DEV", "HOLDOUT")}
    ok = all(v >= 0 for v in vals.values())
    return (ok, ", ".join(f"{k}D {sp.lower()} {v:+.4%}" for (k, sp), v in vals.items()))


def horizon_pass(g: dict) -> bool:
    return all(v is not False for k, (v, _) in g.items())


def verdict(S: dict, gates: dict) -> tuple[str, dict]:
    """gates[(h, variant)] -> gate dict. Precedence follows the contract's verdict_order."""
    P = lambda sp, h, v="obs": S[("PRIMARY", sp, h, v)]
    why = {}
    if all(min(P("DEV", h)["n"], P("HOLDOUT", h)["n"]) < 100 for h in (21, 63)):
        return "INSUFFICIENT", why
    if all(P("DEV", h)["mean_0"] <= 0 for h in PRIMARY_H):
        return "FAIL_SIGNAL", why
    if any(P("DEV", h)["mean_0"] > 0 for h in PRIMARY_H) and not any(P("DEV", h)["mean_25"] > 0 for h in PRIMARY_H):
        return "FAIL_COST_BOUND", why
    for h in (21, 63):
        po, pc = horizon_pass(gates[(h, "obs")]), horizon_pass(gates[(h, "cons")])
        why[h] = {"obs_pass": po, "cons_pass": pc}
        if po and pc:
            return "H_N1_PASS", why
    for h in PRIMARY_H:
        g = gates[(h, "obs")]
        dev_ok = (P("DEV", h)["mean_25"] > 0 and P("DEV", h)["median_10"] > 0 and P("DEV", h)["ci_lo_10"] > 0
                  and P("DEV", h, "cons")["mean_10"] > 0 and _dev_conc(P("DEV", h))
                  and P("DEV", h)["mean_0"] > S[("CONTRAST", "DEV", h, "obs")]["mean_0"])
        if dev_ok and P("HOLDOUT", h)["mean_25"] <= 0:
            return "FAIL_HOLDOUT", why
    for h in (21, 63):
        if P("DEV", h)["mean_25"] > 0 and P("HOLDOUT", h)["mean_25"] > 0:
            return "BORDERLINE", why
    return "FAIL", why


def _dev_conc(d: dict) -> bool:
    c = d["conc10"]
    return c.get("net_total", -1) > 0 and c["top5_share_of_net"] < 0.5 and c["top1pct_removed_mean"] > 0


def final_line(v: str) -> str:
    return {"H_N1_PASS": "PASS", "BORDERLINE": "BORDERLINE", "INSUFFICIENT": "INSUFFICIENT"}.get(v, "FAIL")
