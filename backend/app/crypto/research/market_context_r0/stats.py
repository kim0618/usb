"""Significance for autocorrelated episode series.

A plain shuffle of the condition label would be too easy to beat here: both the label and the
outcome carry runs, and a shuffle destroys the runs in the label while leaving them in the outcome.
A circular rotation keeps the run structure of the label intact and only breaks its alignment with
the outcome, which is the null the study actually needs.
"""
from __future__ import annotations

import numpy as np

SEED = 20261005
SHIFTS = 1000
BOOTSTRAP = 10_000


def _diff(y: np.ndarray, x: np.ndarray) -> float:
    a, b = y[x], y[~x]
    if a.size == 0 or b.size == 0:
        return float("nan")
    return float(a.mean() - b.mean())


def circular_shift_p(y: np.ndarray, x: np.ndarray, shifts: int = SHIFTS) -> dict:
    """Two sided p for the observed mean difference, under rotation of the indicator."""
    obs = _diff(y, x)
    n = len(y)
    if not np.isfinite(obs) or n < 4:
        return {"observed_diff": None, "p_value": None, "shifts": 0}
    rng = np.random.default_rng(SEED)
    offsets = rng.integers(1, n, size=min(shifts, max(1, n - 1)))
    null = np.empty(len(offsets))
    for k, off in enumerate(offsets):
        null[k] = _diff(y, np.roll(x, int(off)))
    ok = np.isfinite(null)
    p = float((np.abs(null[ok]) >= abs(obs) - 1e-12).mean()) if ok.any() else None
    return {"observed_diff": round(obs, 4), "p_value": None if p is None else round(p, 5),
            "shifts": int(ok.sum()),
            "null_abs_p99": None if not ok.any() else round(float(np.percentile(np.abs(null[ok]), 99)), 4)}


def bootstrap_ci(y: np.ndarray, x: np.ndarray, draws: int = BOOTSTRAP) -> dict:
    a, b = y[x], y[~x]
    if a.size < 2 or b.size < 2:
        return {"lo": None, "hi": None, "draws": 0}
    rng = np.random.default_rng(SEED)
    d = np.empty(draws)
    for i in range(draws):
        d[i] = rng.choice(a, a.size, replace=True).mean() - rng.choice(b, b.size, replace=True).mean()
    return {"lo": round(float(np.percentile(d, 2.5)), 4),
            "hi": round(float(np.percentile(d, 97.5)), 4), "draws": draws}


def holm(tests: list[dict], alpha: float = 0.05) -> list[dict]:
    """Holm-Bonferroni over the pre-registered grid. `tests` carry a `p_value`; order is restored."""
    have = [t for t in tests if t.get("p_value") is not None]
    have.sort(key=lambda t: t["p_value"])
    m = len(have)
    still = True
    for i, t in enumerate(have):
        thresh = alpha / (m - i)
        t["holm_threshold"] = round(thresh, 6)
        if still and t["p_value"] <= thresh:
            t["holm_significant"] = True
        else:
            still = False
            t["holm_significant"] = False
    for t in tests:
        if t.get("p_value") is None:
            t["holm_threshold"] = None
            t["holm_significant"] = False
    return tests
