"""F0 diagnostics. None of these is an input of ``verdict.verdict``; the runner computes them only
after verdict.json has been written.
"""

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from app.backtest.strategy_e0_overnight.stats import bucket_index
from app.backtest.strategy_f0_regular_after import stats as S
from app.backtest.strategy_f0_regular_after.params import FEATURE_NAMES, Params

ET = ZoneInfo("America/New_York")
SEC_ROOT = Path(__file__).resolve().parents[4] / "data/runtime/strategy_c/e0/raw"


def _brief(g, mfe, p: Params) -> dict:
    if g.size == 0:
        return {"N": 0}
    return {"N": int(g.size), "mean": float(g.mean()), "median": float(np.median(g)),
            "P(MFE>=0.03)": S.tail_rate(mfe, p), "P(r>=0.03)": float((g >= 0.03).mean()),
            "P(r<=-0.02)": S.downside_rate(g, p), "win_rate": float((g > 0).mean())}


def feature_buckets(features: Mapping[str, np.ndarray], g, mfe, p: Params) -> dict:
    out = {}
    for name in FEATURE_NAMES:
        if name in p.alias:
            out[name] = {"ALIAS_OF": p.alias[name], "reported_once": True}
            continue
        x = features[name]
        edges = S.bucket_edges(x, p)
        b = S.bucket_of(x, edges)
        rows = []
        for q in range(1, len(p.quantiles) + 2):
            sel = b == q
            r = _brief(g[sel], mfe[sel], p)
            r["mfe_mean"] = float(mfe[sel].mean()) if sel.any() else None
            rows.append({"Q": q, **r})
        means = [r.get("mean") for r in rows]
        tails = [r.get("P(MFE>=0.03)") for r in rows]

        def mono(v):
            if any(x is None for x in v):
                return "UNDEFINED"
            d = np.diff(v)
            return "INCREASING" if np.all(d > 0) else "DECREASING" if np.all(d < 0) else "NON_MONOTONE"
        out[name] = {"edges": edges, "missing": int((b == 0).sum()), "buckets": rows,
                     "mean_monotonicity": mono(means), "tail_monotonicity": mono(tails),
                     "Q5_minus_Q1_mean": (means[-1] - means[0]) if None not in (means[0], means[-1]) else None,
                     "Q5_minus_Q1_tail": (tails[-1] - tails[0]) if None not in (tails[0], tails[-1]) else None}
    return out


def matched_control(cols: Mapping[str, np.ndarray], mask: np.ndarray, day: np.ndarray, g, mfe,
                    p: Params) -> dict:
    """Same-session CEM (primary) and session-demeaned cross-session CEM (secondary)."""
    names = list(p.match_edges)
    parts, valid = [], np.ones(g.size, bool)
    for name in names:
        idx = bucket_index(cols[name], p.match_edges[name])
        valid &= idx >= 0
        parts.append(idx)
    cell = np.zeros(g.size, np.int64)
    for idx in parts:
        cell = cell * 16 + np.maximum(idx, 0)
    cell = np.where(valid, cell, -1)
    tail = (mfe >= p.primary_mfe).astype(float)
    out = {"note": "DIAGNOSTIC ONLY - does not change the F0 verdict", "variables": names}
    # same session
    key = day.astype(np.int64) * 100_000 + cell
    ctrl = ~mask & valid
    sums = {}
    for k, gv, tv in zip(key[ctrl], g[ctrl], tail[ctrl]):
        s = sums.setdefault(int(k), [0, 0.0, 0.0])
        s[0] += 1; s[1] += gv; s[2] += tv
    dr, dt, un = [], [], 0
    for i in np.flatnonzero(mask):
        s = sums.get(int(key[i])) if valid[i] else None
        if not s:
            un += 1
            continue
        dr.append(g[i] - s[1] / s[0]); dt.append(tail[i] - s[2] / s[0])
    n = int(mask.sum())
    out["same_session"] = {"H_rows": n, "matched": len(dr), "unmatched": un,
                           "match_rate": len(dr) / n if n else None,
                           "matched_mean_difference": float(np.mean(dr)) if dr else None,
                           "matched_tail_difference": float(np.mean(dt)) if dt else None}
    # session demeaned, cross-session within cell
    sess_mean = np.bincount(day, weights=g, minlength=day.max() + 1) / np.maximum(np.bincount(day, minlength=day.max() + 1), 1)
    sess_tail = np.bincount(day, weights=tail, minlength=day.max() + 1) / np.maximum(np.bincount(day, minlength=day.max() + 1), 1)
    gd, td = g - sess_mean[day], tail - sess_tail[day]
    cs = {}
    for k, gv, tv in zip(cell[ctrl], gd[ctrl], td[ctrl]):
        s = cs.setdefault(int(k), [0, 0.0, 0.0])
        s[0] += 1; s[1] += gv; s[2] += tv
    dr2, dt2, un2 = [], [], 0
    for i in np.flatnonzero(mask):
        s = cs.get(int(cell[i])) if valid[i] else None
        if not s:
            un2 += 1
            continue
        dr2.append(gd[i] - s[1] / s[0]); dt2.append(td[i] - s[2] / s[0])
    out["session_demeaned"] = {"matched": len(dr2), "unmatched": un2,
                               "matched_mean_difference": float(np.mean(dr2)) if dr2 else None,
                               "matched_tail_difference": float(np.mean(dt2)) if dt2 else None}
    return out


def sec_events(symbols: np.ndarray, day: np.ndarray, sessions: list[str], prev_sessions: list[date],
               cik_maps: list[dict], root: Path = SEC_ROOT) -> np.ndarray:
    """EVENT / NON_EVENT / UNKNOWN_EVENT_STATUS per row (8-K item 2.02, [D-1 16:00, D 17:00) ET)."""
    import json
    from app.backtest.strategy_c_e0.sec_store import read_gz_json, rows_of
    manifest = json.loads((root / "store_manifest.json").read_text())
    per = {r["cik"]: r for r in manifest["per_cik"]}
    cache: dict[str, list[datetime]] = {}

    def accepted(cik: str) -> list[datetime] | None:
        entry = per.get(cik)
        if not entry or not entry.get("covered"):
            return None
        if cik not in cache:
            times = []
            for page in entry["pages"]:
                for r in rows_of(read_gz_json(root / "submissions" / f"CIK{cik}" / f"{page}.gz")):
                    if r.get("form") == "8-K" and "2.02" in str(r.get("items") or "") and r.get("acceptanceDateTime"):
                        times.append(datetime.fromisoformat(r["acceptanceDateTime"].replace("Z", "+00:00")))
            cache[cik] = times
        return cache[cik]

    fetched = date.fromisoformat(manifest["created_at"][:10])
    out = np.empty(symbols.size, dtype=object)
    for i, (sym, d) in enumerate(zip(symbols, day)):
        session = date.fromisoformat(sessions[d])
        cik = cik_maps[d].get(sym)
        times = accepted(str(cik).zfill(10)) if cik else None
        entry = per.get(str(cik).zfill(10)) if cik else None
        if times is None or session > fetched or session.isoformat() < entry.get("required_from", "9999"):
            out[i] = "UNKNOWN_EVENT_STATUS"
            continue
        lo = datetime.combine(prev_sessions[d], time(16, 0), tzinfo=ET)
        hi = datetime.combine(session, time(17, 0), tzinfo=ET)
        out[i] = "EVENT" if any(lo <= t < hi for t in times) else "NON_EVENT"
    return out
