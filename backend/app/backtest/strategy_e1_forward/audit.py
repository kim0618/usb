"""The two forward point-in-time audits the protocol requires.

Both are poisoning tests, and both are run against the real seal builder rather than a copy of
it, so a look-ahead introduced later fails them.

**Feature cutoff.** Replace every minute bar from 09:25 ET onward with noise, rebuild the seal,
and require the decision digest to be identical. That covers the features and the H5 mask in one
check, because the digest is taken over both.

**Daily / reference cutoff.** Poison the daily panel and reference membership *after* D-1 and
require the same. A decision taken at 09:25 on session D may read D-1 and earlier; if poisoning
D or later moves it, the decision was reading the future.

A failure of either makes the forward test invalid, not merely suspect.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_e1_forward import seal as seal_mod

POISON_FROM_MINUTE = 9 * 60 + 25     # 09:25 ET


def feature_cutoff(session: date, build_features: Callable[[bool], tuple[Sequence[str], Mapping[str, np.ndarray]]],
                   *, rules_digest: str) -> dict[str, Any]:
    """``build_features(poisoned)`` must return the same rows whether or not the tape is poisoned."""
    clean_symbols, clean_features = build_features(False)
    dirty_symbols, dirty_features = build_features(True)
    clean = seal_mod.build(session, clean_symbols, clean_features, provenance=seal_mod.RECONSTRUCTED,
                           sources={}, rules_digest=rules_digest)
    result: dict[str, Any] = {"check": "feature_cutoff_0924", "session": session.isoformat(),
                              "clean_rows": len(clean_symbols), "poisoned_rows": len(dirty_symbols)}
    if sorted(map(str, clean_symbols)) != sorted(map(str, dirty_symbols)):
        result.update(verdict="FAIL", reason="the eligible universe changed under poisoning")
        return result
    dirty = seal_mod.build(session, dirty_symbols, dirty_features,
                           provenance=seal_mod.RECONSTRUCTED, sources={}, rules_digest=rules_digest)
    moved = [name for name in seal_mod.SEALED_FEATURES
             if not np.array_equal(clean_features[name], dirty_features[name], equal_nan=True)]
    result.update(clean_digest=clean.decision_digest, poisoned_digest=dirty.decision_digest,
                  moved_features=moved,
                  verdict="PASS" if clean.decision_digest == dirty.decision_digest else "FAIL")
    return result


def daily_cutoff(session: date, build_features: Callable[[bool], tuple[Sequence[str], Mapping[str, np.ndarray]]],
                 *, rules_digest: str) -> dict[str, Any]:
    """The same shape, with the daily and reference inputs poisoned after D-1 instead."""
    result = feature_cutoff(session, build_features, rules_digest=rules_digest)
    result["check"] = "daily_reference_cutoff_d_minus_1"
    return result


def poison_tape_after_cutoff(minute: np.ndarray, columns: Mapping[str, np.ndarray],
                             seed: int = 20260920) -> dict[str, np.ndarray]:
    """Noise for every bar at or after 09:25 ET, positive, finite and missing-preserving.

    The same shape of poison E0 and E1 use: large enough that any reader moves, but leaving a
    missing value missing so that eligibility, which only asks 'is there a bar', is unchanged.
    """
    rng = np.random.default_rng(seed)
    target = minute >= POISON_FROM_MINUTE
    out = {}
    for name, array in columns.items():
        copy = array.copy()
        values = copy[target]
        noise = rng.uniform(1.0, 1000.0, size=int(target.sum())) * 19.0
        copy[target] = np.where(np.isfinite(values), noise, values)
        out[name] = copy
    return out
