"""D-AGG-2 scoring on row arrays: primary statistics, gate inputs and secondary slots.

Pure computation. The runner (``d2.py``) loads and verifies the D-AGG-1 artifacts and hands the
columns over; this module never opens a file, so the same code runs on synthetic tables in tests.

Order of work, which is also the firewall:

1. select the primary setup from A(q) alone (``setup.select``), before validity is looked at
2. fix the evaluable sessions (D0: fewer than 20 label-valid setup rows -> session dropped from
   every statistic, both groups)
3. compute every gate input, then the gate verdict
4. only then the secondary slots; the verdict is re-derived from the stored gate inputs after the
   secondaries and must be identical (``secondary_cannot_overturn``)

Conventions this module had to fix because D0 does not word them (all fixed before any real
number was computed, and none of them is a gate threshold):

* leave-out recomputation re-applies the 20-row session rule, because D0 says a thin session is
  dropped "from every statistic of this study"; the variant that keeps every primary session is
  reported next to it
* ties in the session or ticker excess are broken by position ascending (session index, then
  ticker column)
* secondary selections (Top 5%, Top 2%, B0, rv_20 deciles) are scored on the primary evaluable
  sessions without the 20-row rule (Top 2% has 6 names a session); a session where the secondary
  group has no valid row is dropped from that slot only, and counted
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

import numpy as np

from app.backtest.strategy_d_agg import gate as gate_mod, ratios, setup
from app.backtest.strategy_d_agg.config import THRESHOLD_EPS
from app.backtest.strategy_d_agg.models import EXCURSION_STATUS_ORDER, HardFail

SECONDARY_THRESHOLDS = {"5": (0.05, -0.05), "15": (0.15, -0.15)}
PERCENTILES = (10, 20, 30, 40, 50, 60, 70, 80, 90)


@dataclass
class Rows:
    """Row-aligned columns of one table. Optional columns may be None."""

    session: np.ndarray
    ticker_col: np.ndarray
    valid: np.ndarray
    status: np.ndarray
    up: np.ndarray
    down: np.ndarray
    mfe: np.ndarray
    mae: np.ndarray
    rv20: np.ndarray | None = None
    close_return: np.ndarray | None = None
    excess_return: np.ndarray | None = None

    def __len__(self) -> int:
        return int(self.session.shape[0])


@dataclass
class Queries(Rows):
    sample_rank: np.ndarray = field(default=None)
    analog: np.ndarray = field(default=None)
    signal_ok: np.ndarray = field(default=None)
    b0: np.ndarray | None = None


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _lifts_dict(l: ratios.Lifts) -> dict[str, float | None]:
    return {k: _finite(v) for k, v in l.as_dict().items()}


def stats_of(rows: Rows, member: np.ndarray, sessions: np.ndarray, *, up=None, down=None) -> ratios.SessionStats:
    return ratios.session_stats(sessions, rows.session, member, rows.valid,
                                rows.up if up is None else up, rows.down if down is None else down,
                                rows.mfe, rows.mae)


def _flags(rows: Rows, up: float, down: float) -> tuple[np.ndarray, np.ndarray]:
    with np.errstate(invalid="ignore"):
        return rows.valid & (rows.mfe >= up - THRESHOLD_EPS), rows.valid & (rows.mae <= down + THRESHOLD_EPS)


def secondary_lifts(group: ratios.SessionStats, comp: ratios.SessionStats) -> dict[str, Any]:
    keep = np.flatnonzero((group.n > 0) & (comp.n > 0))
    try:
        out = _lifts_dict(ratios.lifts(group.take(keep), comp.take(keep)))
    except HardFail as exc:                  # descriptive slot: an empty ratio is reported, not fatal
        out = {"TL": None, "DL": None, "NTL": None, "AG": None, "TEP": None, "undefined": str(exc)}
    out["sessions"] = int(keep.size)
    out["sessions_dropped_empty"] = int(group.n.size - keep.size)
    return out


def evaluable(setup_stats: ratios.SessionStats, universe_stats: ratios.SessionStats,
              minimum: int) -> np.ndarray:
    return np.flatnonzero((setup_stats.n >= minimum) & (universe_stats.n > 0))


def leave_tickers_tl(q: Queries, u: Rows, chosen: np.ndarray, sessions: np.ndarray,
                     drop_cols: np.ndarray, minimum: int) -> dict[str, float | int | None]:
    q_keep = chosen & ~np.isin(q.ticker_col, drop_cols)
    u_keep = ~np.isin(u.ticker_col, drop_cols)
    s = stats_of(q, q_keep, sessions)
    c = stats_of(u, u_keep, sessions)
    strict = evaluable(s, c, minimum)
    loose = np.flatnonzero((s.n > 0) & (c.n > 0))
    return {"tl": ratios.lifts(s.take(strict), c.take(strict)).tl,
            "tl_keep_all_sessions": ratios.lifts(s.take(loose), c.take(loose)).tl,
            "sessions": int(strict.size), "sessions_below_minimum_after_removal": int(sessions.size - strict.size)}


def primary(q: Queries, u: Rows, *, quantile: Fraction, minimum: int, draws_fn,
            partition_fn, spec: gate_mod.GateSpec, pit_violations: int) -> dict[str, Any]:
    chosen = setup.select(q.session, q.sample_rank, q.analog, q.signal_ok, quantile)
    all_sessions = np.unique(q.session)
    per_session_selected = np.bincount(np.searchsorted(all_sessions, q.session[chosen]),
                                       minlength=all_sessions.size)
    s_all = stats_of(q, chosen, all_sessions)
    u_all = stats_of(u, np.ones(len(u), dtype=bool), all_sessions)
    keep = evaluable(s_all, u_all, minimum)
    sessions = all_sessions[keep]
    s, c = s_all.take(keep), u_all.take(keep)
    main = ratios.lifts(s, c)

    draws = draws_fn(sessions.size)
    boot = ratios.bootstrap(s, c, draws)
    tl_ci = ratios.percentile_ci(boot.tl)
    partition = partition_fn(sessions.size)
    block = ratios.blocks(s, c, partition)

    # concentration
    e_t = ratios.session_excess(s, c)
    top_sessions = ratios.top_positions(e_t, 5)
    rest = np.setdiff1d(np.arange(sessions.size), top_sessions)
    leave_sessions_tl = ratios.lifts(s.take(rest), c.take(rest)).tl
    in_eval = chosen & q.valid & np.isin(q.session, sessions)
    p_up_u = dict(zip(sessions.tolist(), c.p_up.tolist()))
    contrib = q.up[in_eval].astype(np.float64) - np.array([p_up_u[x] for x in q.session[in_eval]])
    cols = q.ticker_col[in_eval]
    unique_cols, inverse = np.unique(cols, return_inverse=True)
    e_i = np.bincount(inverse, weights=contrib, minlength=unique_cols.size)
    top_cols = unique_cols[ratios.top_positions(e_i, 10)]
    leave_t = leave_tickers_tl(q, u, chosen, sessions, top_cols, minimum)
    up_by_ticker = np.bincount(inverse, weights=q.up[in_eval].astype(np.float64), minlength=unique_cols.size)
    total_up = float(up_by_ticker.sum())
    single_share = float(up_by_ticker.max() / total_up) if total_up > 0 else float("nan")

    selected_all = int(chosen.sum())
    setup_invalid = int((chosen & ~q.valid).sum())
    universe_invalid = int((~u.valid).sum())
    metrics = {
        "evaluable_sessions": int(sessions.size),
        "valid_setup_rows": int(s.n.sum()),
        "unique_setup_tickers": int(unique_cols.size),
        "setup_up_events": int(s.k_up.sum()),
        "pit_violations": int(pit_violations),
        "setup_invalid_share": setup_invalid / selected_all,
        "universe_invalid_share": universe_invalid / len(u),
        "TL": main.tl, "DL": main.dl, "NTL": main.ntl, "AG": main.ag, "TEP": main.tep,
        "single_ticker_share": single_share,
        "TL_ci_low": tl_ci[0], "TL_ci_high": tl_ci[1],
        "blocks_tl_above_one": int(sum(1 for b in block if b.tl > 1.0)),
        "leave_top5_sessions_TL": leave_sessions_tl,
        "leave_top10_tickers_TL": leave_t["tl"],
    }
    verdict = gate_mod.evaluate(metrics, spec)
    status_selected = {n: int((q.status[chosen] == i).sum()) for i, n in enumerate(EXCURSION_STATUS_ORDER)}
    status_universe = {n: int((u.status == i).sum()) for i, n in enumerate(EXCURSION_STATUS_ORDER)}
    return {
        "chosen": chosen, "sessions": sessions, "setup_stats": s, "universe_stats": c,
        "all_sessions": all_sessions, "boot": boot, "draws": draws, "partition": partition,
        "metrics": metrics, "gate": verdict,
        "detail": {
            "setup": {"sessions": int(all_sessions.size), "selected_before_validity": selected_all,
                      "selected_per_session_min": int(per_session_selected.min()),
                      "selected_per_session_max": int(per_session_selected.max()),
                      "valid_after_geometry": int((chosen & q.valid).sum()),
                      "invalid_after_geometry": setup_invalid,
                      "validity_ratio": (selected_all - setup_invalid) / selected_all,
                      "status": status_selected,
                      "sessions_dropped_thin": [int(x) for x in all_sessions[np.setdiff1d(np.arange(all_sessions.size), keep)]]},
            "universe": {"eligible_rows": len(u), "valid": int(u.valid.sum()), "invalid": universe_invalid,
                         "status": status_universe},
            "session_means": {"setup_p_up": float(s.p_up.mean()), "universe_p_up": float(c.p_up.mean()),
                              "setup_p_dn": float(s.p_dn.mean()), "universe_p_dn": float(c.p_dn.mean())},
            "bootstrap": {"TL": tl_ci, "DL": ratios.percentile_ci(boot.dl),
                          "NTL": ratios.percentile_ci(boot.ntl), "AG": ratios.percentile_ci(boot.ag),
                          "TEP": ratios.percentile_ci(boot.tep)},
            "blocks": [{"sessions": int(p.size), "first": int(sessions[p[0]]), "last": int(sessions[p[-1]]),
                        **_lifts_dict(b)} for p, b in zip(partition, block)],
            "concentration": {
                "top5_sessions": [int(x) for x in sessions[top_sessions]],
                "top5_session_excess": [float(x) for x in e_t[top_sessions]],
                "leave_top5_sessions_TL": leave_sessions_tl,
                "top10_ticker_cols": [int(x) for x in top_cols],
                "top10_ticker_excess": [float(x) for x in np.sort(e_i)[::-1][:10]],
                "leave_top10_tickers": leave_t,
                "single_ticker_share": single_share,
                "single_ticker_col": int(unique_cols[int(np.argmax(up_by_ticker))]) if total_up > 0 else None,
            },
        },
        "_e_t": e_t, "_e_i": e_i, "_unique_cols": unique_cols, "_in_eval": in_eval,
    }


def _overlap(rows: Rows, member: np.ndarray) -> dict[str, int]:
    m = member & rows.valid
    return {"up_only": int((m & rows.up & ~rows.down).sum()), "down_only": int((m & rows.down & ~rows.up).sum()),
            "both": int((m & rows.up & rows.down).sum()), "neither": int((m & ~rows.up & ~rows.down).sum())}


def _pooled(rows: Rows, member: np.ndarray) -> dict[str, float]:
    m = member & rows.valid
    return {"rows": int(m.sum()), "up10_share": float(rows.up[m].mean()), "dn10_share": float(rows.down[m].mean())}


def _describe_returns(values: np.ndarray) -> dict[str, float]:
    v = values[np.isfinite(values)]
    return {"mean": float(v.mean()), "median": float(np.median(v)), "share_positive": float((v > 0).mean()),
            "rows": int(v.size)}


def _decile_groups(q: Queries, sessions: np.ndarray, key: np.ndarray, groups: int) -> list[np.ndarray]:
    """Per session, signal-OK queries ordered by ``key`` descending (sample_rank ties) split in ``groups``."""
    masks = [np.zeros(len(q), dtype=bool) for _ in range(groups)]
    ok = np.flatnonzero(q.signal_ok & np.isin(q.session, sessions))
    order = ok[np.lexsort((q.sample_rank[ok], -key[ok], q.session[ok]))]
    s_sorted = q.session[order]
    _, starts = np.unique(s_sorted, return_index=True)
    ends = np.append(starts[1:], order.size)
    for a, b in zip(starts, ends):
        for g, part in enumerate(np.array_split(order[a:b], groups)):
            masks[g][part] = True
    return masks


def secondary(q: Queries, u: Rows, prim: Mapping[str, Any], *, minimum: int) -> dict[str, Any]:
    sessions = prim["sessions"]
    chosen = prim["chosen"]
    s, c = prim["setup_stats"], prim["universe_stats"]
    universe_all = np.ones(len(u), dtype=bool)
    in_sessions_u = np.isin(u.session, sessions)
    in_sessions_q = np.isin(q.session, sessions)
    out: dict[str, Any] = {"secondary_cannot_overturn": True}

    x1 = {}
    for name, (up_t, dn_t) in SECONDARY_THRESHOLDS.items():
        qu, qd = _flags(q, up_t, dn_t)
        uu, ud = _flags(u, up_t, dn_t)
        x1[f"pm{name}"] = secondary_lifts(stats_of(q, chosen, sessions, up=qu, down=qd),
                                          stats_of(u, universe_all, sessions, up=uu, down=ud))
    out["X-1"] = x1

    x2 = {}
    for label, value in (("top5", "0.05"), ("top2", "0.02")):
        pick = setup.select(q.session, q.sample_rank, q.analog, q.signal_ok, setup.exact_fraction(value))
        x2[label] = secondary_lifts(stats_of(q, pick, sessions), c)
    out["X-2"] = x2

    out["X-3"] = {"TEP": prim["metrics"]["TEP"], "TEP_ci": prim["detail"]["bootstrap"]["TEP"]}

    sample = stats_of(q, q.signal_ok, sessions)
    out["X-4"] = secondary_lifts(s, sample)

    if q.b0 is not None:
        b0_pick = setup.select(q.session, q.sample_rank, q.b0, q.signal_ok, Fraction(1, 10))
        b0_stats = stats_of(q, b0_pick, sessions)
        x5 = secondary_lifts(b0_stats, c)
        keep = np.flatnonzero((b0_stats.n > 0) & (s.n > 0))
        x5["setup_vs_b0_decile_TL"] = _finite(float(s.take(keep).p_up.sum() / b0_stats.take(keep).p_up.sum()))
        out["X-5"] = x5
    else:
        out["X-5"] = "NOT_COMPUTED"

    if q.rv20 is not None and u.rv20 is not None:
        rv_pick = setup.select(q.session, q.sample_rank, q.rv20, q.signal_ok, Fraction(1, 10))
        out["X-6"] = secondary_lifts(stats_of(q, rv_pick, sessions), c)
        out["X-7"] = vol_matched(q, u, chosen, sessions)
    else:
        out["X-6"] = out["X-7"] = "NOT_COMPUTED"

    if q.close_return is not None and u.close_return is not None:
        sel = chosen & q.valid & in_sessions_q
        uni = u.valid & in_sessions_u
        out["X-8"] = {"setup": {"close_return_5": _describe_returns(q.close_return[sel]),
                                "excess_return_5": _describe_returns(q.excess_return[sel])},
                      "universe": {"close_return_5": _describe_returns(u.close_return[uni]),
                                   "excess_return_5": _describe_returns(u.excess_return[uni])}}
    else:
        out["X-8"] = "NOT_COMPUTED"

    sel = chosen & q.valid & in_sessions_q
    uni = u.valid & in_sessions_u
    out["X-9"] = {group: {"mfe_5": dict(zip([f"p{p}" for p in PERCENTILES],
                                             np.percentile(rows.mfe[m], PERCENTILES).round(12).tolist())),
                          "mae_5": dict(zip([f"p{p}" for p in PERCENTILES],
                                             np.percentile(rows.mae[m], PERCENTILES).round(12).tolist()))}
                  for group, rows, m in (("setup", q, sel), ("universe", u, uni))}

    deciles = []
    for g, mask in enumerate(_decile_groups(q, sessions, q.analog, 10), start=1):
        st = stats_of(q, mask, sessions)
        keep = np.flatnonzero(st.n > 0)
        deciles.append({"decile_from_top": g, "p_up_session_mean": float(st.take(keep).p_up.mean()),
                        "p_dn_session_mean": float(st.take(keep).p_dn.mean()),
                        "median_mfe_session_mean": float(np.nanmean(st.m_up)),
                        "median_mae_session_mean": float(-np.nanmean(st.m_dn)),
                        **secondary_lifts(st, c)})
    out["X-10"] = {"note": "bottom deciles reported, never usable", "deciles": deciles}

    e_t, e_i = prim["_e_t"], prim["_e_i"]
    pos_t, pos_i = e_t[e_t > 0], e_i[e_i > 0]
    in_eval = prim["_in_eval"]
    rows_per_ticker = np.bincount(np.unique(q.ticker_col[in_eval], return_inverse=True)[1])
    out["X-11"] = {"top5_sessions_share_of_positive_excess": float(np.sort(pos_t)[::-1][:5].sum() / pos_t.sum()) if pos_t.size else None,
                   "top10_tickers_share_of_positive_excess": float(np.sort(pos_i)[::-1][:10].sum() / pos_i.sum()) if pos_i.size else None,
                   "unique_setup_tickers": int(prim["_unique_cols"].size),
                   "most_repeated_ticker_rows": int(rows_per_ticker.max()),
                   "sector_theme": "NOT_AVAILABLE (no point-in-time sector mapping in the store)"}
    out["X-12"] = prim["detail"]["blocks"]

    out["descriptive"] = {
        "pooled": {"setup": _pooled(q, chosen & in_sessions_q), "universe": _pooled(u, in_sessions_u)},
        "overlap": {"setup": _overlap(q, chosen & in_sessions_q), "universe": _overlap(u, in_sessions_u)},
        "pooled_TL": float(_pooled(q, chosen & in_sessions_q)["up10_share"] / _pooled(u, in_sessions_u)["up10_share"]),
        "note": "pooled values are descriptive; the gate reads the session-stratified TL only",
    }
    return out


def vol_matched(q: Queries, u: Rows, chosen: np.ndarray, sessions: np.ndarray) -> dict[str, Any]:
    """X-7: universe reweighted, per session, to the setup's rv_20 decile mix (edges from the universe)."""
    num_up = num_dn = den_up = den_dn = 0.0
    used = 0
    for t in sessions:
        um = (u.session == t) & u.valid
        qm = (q.session == t) & chosen & q.valid
        if not um.any() or not qm.any():
            continue
        edges = np.quantile(u.rv20[um], np.linspace(0, 1, 11)[1:-1])
        u_dec = np.searchsorted(edges, u.rv20[um], side="right")
        q_dec = np.searchsorted(edges, q.rv20[qm], side="right")
        w = np.bincount(q_dec, minlength=10) / q_dec.size
        cnt = np.bincount(u_dec, minlength=10)
        p_up_k = np.divide(np.bincount(u_dec, weights=u.up[um], minlength=10), cnt,
                           out=np.zeros(10), where=cnt > 0)
        p_dn_k = np.divide(np.bincount(u_dec, weights=u.down[um], minlength=10), cnt,
                           out=np.zeros(10), where=cnt > 0)
        num_up += q.up[qm].mean()
        num_dn += q.down[qm].mean()
        den_up += float(w @ p_up_k)
        den_dn += float(w @ p_dn_k)
        used += 1
    tl = num_up / den_up if den_up else float("nan")
    dl = num_dn / den_dn if den_dn else float("nan")
    return {"TL_vol_matched": _finite(tl), "DL_vol_matched": _finite(dl),
            "NTL_vol_matched": _finite(tl / dl) if dl else None, "sessions": used}
