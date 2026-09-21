"""``D_AGG_RV20_DECILE_REWEIGHT_V1``: the volatility-matched control and the session Delta.

Per session D, on the D-AGG-1 universe rows (the as-of eligible population, invalid rows included):

    edges      numpy.quantile(population rv_20, [0.1 .. 0.9]) (linear), bucket = searchsorted(side='right')
               built on the whole population, setup members included, validity never consulted
    control    population rows whose ticker is not in that session's setup
    w_j(t)     valid setup trades in bucket j / valid setup trades
    E_C(t)     sum_j w_j(t) x mean net of valid control rows in bucket j
    E_S(t)     mean net of valid setup trades
    Delta(t)   E_S(t) - E_C(t)
    drop       < 20 valid setup trades, or a bucket with w_j > 0 and no valid control row

No random draw. Edges are fixed once per session; a leave-out removes rows afterwards and never
rebuilds edges.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_agg.models import HardFail

MIN_SETUP_TRADES = 20
BUCKETS = 10


def buckets(session_idx: np.ndarray, rv20: np.ndarray, sessions: np.ndarray) -> np.ndarray:
    """Decile bucket 0..9 of every population row, edges from its own session's population."""
    out = np.full(session_idx.shape, -1, dtype=np.int8)
    probs = np.linspace(0.1, 0.9, BUCKETS - 1)
    for t in sessions:
        m = session_idx == t
        values = rv20[m]
        if not np.isfinite(values).all():
            raise HardFail("R5", f"session {t}: a population row has no rv_20")
        edges = np.quantile(values, probs)
        out[m] = np.searchsorted(edges, values, side="right")
    return out


@dataclass(frozen=True)
class SessionDelta:
    sessions: np.ndarray       # retained sessions
    e_s: np.ndarray
    e_c: np.ndarray
    delta: np.ndarray
    n_setup: np.ndarray
    n_control: np.ndarray
    dropped_thin: tuple[int, ...]
    dropped_empty_bucket: tuple[int, ...]


def session_delta(session_idx: np.ndarray, bucket: np.ndarray, is_setup: np.ndarray,
                  valid: np.ndarray, net: np.ndarray, sessions: np.ndarray,
                  keep: np.ndarray | None = None) -> SessionDelta:
    """``keep`` optionally removes rows (leave-out) after edges were built."""
    keep = np.ones(session_idx.shape, dtype=bool) if keep is None else keep
    rows = {"s": [], "es": [], "ec": [], "ns": [], "nc": []}
    thin, empty = [], []
    order = np.argsort(session_idx, kind="stable")
    sorted_s = session_idx[order]
    for t in sessions:
        lo, hi = np.searchsorted(sorted_s, t, "left"), np.searchsorted(sorted_s, t, "right")
        idx = order[lo:hi]
        sv = idx[is_setup[idx] & valid[idx] & keep[idx]]
        cv = idx[~is_setup[idx] & valid[idx] & keep[idx]]
        if sv.size < MIN_SETUP_TRADES:
            thin.append(int(t))
            continue
        w = np.bincount(bucket[sv], minlength=BUCKETS) / sv.size
        cnt = np.bincount(bucket[cv], minlength=BUCKETS)
        if ((w > 0) & (cnt == 0)).any():
            empty.append(int(t))
            continue
        means = np.bincount(bucket[cv], weights=net[cv], minlength=BUCKETS) / np.maximum(cnt, 1)
        rows["s"].append(int(t))
        rows["es"].append(float(net[sv].mean()))
        rows["ec"].append(float(w @ means))
        rows["ns"].append(int(sv.size))
        rows["nc"].append(int(cv.size))
    es, ec = np.array(rows["es"]), np.array(rows["ec"])
    return SessionDelta(np.array(rows["s"], dtype=np.int64), es, ec, es - ec,
                        np.array(rows["ns"]), np.array(rows["nc"]), tuple(thin), tuple(empty))


def ticker_contributions(session_idx: np.ndarray, ticker_col: np.ndarray, is_setup: np.ndarray,
                         valid: np.ndarray, net: np.ndarray, sd: SessionDelta) -> tuple[np.ndarray, np.ndarray]:
    """c_i = sum over ticker i's valid setup trades of (net - E_C(t)) / n_S(t), retained sessions."""
    pos = {int(t): i for i, t in enumerate(sd.sessions)}
    use = np.flatnonzero(is_setup & valid & np.isin(session_idx, sd.sessions))
    k = np.array([pos[int(t)] for t in session_idx[use]], dtype=np.int64)
    contrib = (net[use] - sd.e_c[k]) / sd.n_setup[k]
    cols, inverse = np.unique(ticker_col[use], return_inverse=True)
    return cols, np.bincount(inverse, weights=contrib, minlength=cols.size)
