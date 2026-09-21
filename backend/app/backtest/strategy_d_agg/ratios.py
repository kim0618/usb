"""Session-stratified tail ratios of D0 ``metrics``, their bootstrap, blocks and leave-outs.

    TL  = sum_t p_up_S(t) / sum_t p_up_U(t)
    DL  = sum_t p_dn_S(t) / sum_t p_dn_U(t)
    NTL = TL / DL
    AG  = R_S / R_U,   R_G = sum_t m_up_G(t) / sum_t m_dn_G(t)
          m_up_G(t) = median MFE_5 of G's valid rows on t, m_dn_G(t) = median(-MAE_5)
    TEP = mean_t (p_up_S(t) - p_up_U(t))       (descriptive)

Every ratio is a ratio of session sums; no per-session ratio and no per-row MFE/|MAE| is formed.
In a bootstrap replicate the numerator and denominator sums are taken over the same resampled
sessions (one draw matrix for the whole study). Nothing here knows a threshold.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_agg.models import HardFail


@dataclass(frozen=True)
class SessionStats:
    """Per-session counts and medians of one group over a fixed ordered list of sessions."""

    sessions: np.ndarray
    n: np.ndarray
    k_up: np.ndarray
    k_dn: np.ndarray
    m_up: np.ndarray
    m_dn: np.ndarray

    @property
    def p_up(self) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return self.k_up / self.n

    @property
    def p_dn(self) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return self.k_dn / self.n

    def take(self, positions: np.ndarray) -> "SessionStats":
        return SessionStats(self.sessions[positions], self.n[positions], self.k_up[positions],
                            self.k_dn[positions], self.m_up[positions], self.m_dn[positions])


def session_stats(sessions: np.ndarray, session_idx: np.ndarray, member: np.ndarray,
                  valid: np.ndarray, up: np.ndarray, down: np.ndarray, mfe: np.ndarray,
                  mae: np.ndarray) -> SessionStats:
    """Stats of the rows with ``member & valid``, grouped onto ``sessions`` (sorted, unique)."""
    use = member & valid
    s = session_idx[use]
    pos = np.searchsorted(sessions, s)
    if pos.size and ((pos >= sessions.size) | (sessions[np.minimum(pos, sessions.size - 1)] != s)).any():
        keep = (pos < sessions.size) & (sessions[np.minimum(pos, sessions.size - 1)] == s)
        pos, use_idx = pos[keep], np.flatnonzero(use)[keep]
    else:
        use_idx = np.flatnonzero(use)
    width = sessions.size
    n = np.bincount(pos, minlength=width).astype(np.int64)
    k_up = np.bincount(pos, weights=up[use_idx], minlength=width).astype(np.int64)
    k_dn = np.bincount(pos, weights=down[use_idx], minlength=width).astype(np.int64)
    m_up = np.full(width, np.nan)
    m_dn = np.full(width, np.nan)
    order = np.argsort(pos, kind="stable")
    sorted_pos = pos[order]
    starts = np.searchsorted(sorted_pos, np.arange(width), side="left")
    ends = np.searchsorted(sorted_pos, np.arange(width), side="right")
    rows = use_idx[order]
    for i in range(width):
        if ends[i] > starts[i]:
            block = rows[starts[i]:ends[i]]
            m_up[i] = float(np.median(mfe[block]))
            m_dn[i] = float(np.median(-mae[block]))
    return SessionStats(sessions, n, k_up, k_dn, m_up, m_dn)


def _ratio(num: float, den: float, what: str, *, allow_zero: bool) -> float:
    if den == 0:
        if allow_zero:
            return float("inf") if num > 0 else float("nan")
        raise HardFail("R5", f"{what}: zero denominator")
    return float(num / den)


@dataclass(frozen=True)
class Lifts:
    tl: float
    dl: float
    ntl: float
    ag: float
    tep: float

    def as_dict(self) -> dict[str, float]:
        return {"TL": self.tl, "DL": self.dl, "NTL": self.ntl, "AG": self.ag, "TEP": self.tep}


def lifts(s: SessionStats, u: SessionStats) -> Lifts:
    if not np.array_equal(s.sessions, u.sessions):
        raise HardFail("R5", "setup and comparator sessions differ")
    if (s.n <= 0).any() or (u.n <= 0).any():
        raise HardFail("R5", "a session in the ratio has no valid rows in one group")
    tl = _ratio(s.p_up.sum(), u.p_up.sum(), "TL", allow_zero=False)
    dl = _ratio(s.p_dn.sum(), u.p_dn.sum(), "DL", allow_zero=False)
    ntl = _ratio(tl, dl, "NTL", allow_zero=True)
    r_s = _ratio(s.m_up.sum(), s.m_dn.sum(), "R_S", allow_zero=True)
    r_u = _ratio(u.m_up.sum(), u.m_dn.sum(), "R_U", allow_zero=False)
    ag = _ratio(r_s, r_u, "AG", allow_zero=False)
    return Lifts(tl, dl, ntl, ag, float(np.mean(s.p_up - u.p_up)))


@dataclass(frozen=True)
class Boot:
    tl: np.ndarray
    dl: np.ndarray
    ntl: np.ndarray
    ag: np.ndarray
    tep: np.ndarray


def bootstrap(s: SessionStats, u: SessionStats, draws: np.ndarray) -> Boot:
    """All replicate statistics on one draw matrix ``(R, T)`` of session positions."""
    if draws.shape[1] != s.sessions.size:
        raise HardFail("R5", f"draws cover {draws.shape[1]} sessions, stats {s.sessions.size}")
    sup, uup = s.p_up[draws].sum(axis=1), u.p_up[draws].sum(axis=1)
    sdn, udn = s.p_dn[draws].sum(axis=1), u.p_dn[draws].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        tl, dl = sup / uup, sdn / udn
        ntl = tl / dl
        r_s = s.m_up[draws].sum(axis=1) / s.m_dn[draws].sum(axis=1)
        r_u = u.m_up[draws].sum(axis=1) / u.m_dn[draws].sum(axis=1)
        ag = r_s / r_u
    tep = (s.p_up - u.p_up)[draws].mean(axis=1)
    return Boot(tl, dl, ntl, ag, tep)


def percentile_ci(values: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    alpha = 1.0 - level
    low, high = np.quantile(values, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(low), float(high)


def blocks(s: SessionStats, u: SessionStats, partition: Sequence[np.ndarray]) -> list[Lifts]:
    return [lifts(s.take(part), u.take(part)) for part in partition]


def session_excess(s: SessionStats, u: SessionStats) -> np.ndarray:
    """``e_t = k_up_S(t) - n_S(t) * p_up_U(t)``."""
    return s.k_up - s.n * u.p_up


def top_positions(values: np.ndarray, count: int) -> np.ndarray:
    """Positions of the ``count`` largest values; ties broken by position ascending (declared)."""
    order = np.lexsort((np.arange(values.size), -values))
    return np.sort(order[:count])
