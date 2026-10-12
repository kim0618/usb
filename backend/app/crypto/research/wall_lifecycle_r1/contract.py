"""Frozen figures of `wall-lifecycle.r1` and `entry-advisor.r0` (docs/crypto/wall_lifecycle_r1,
docs/crypto/entry_advisor_r0). Every number here is quoted from an existing frozen contract or
justified in the R1 document; none is tuned on results. `identity()` reports whether the two
documents still hash to what was recorded when the code was written."""
from __future__ import annotations

import hashlib
from pathlib import Path

VERSION = "wall-lifecycle.r1"
ADVISOR_VERSION = "entry-advisor.r0"

CONTRACT_R1 = "docs/crypto/wall_lifecycle_r1/CONTRACT_WALL_LIFECYCLE_R1.md"
CONTRACT_ADVISOR = "docs/crypto/entry_advisor_r0/CONTRACT_ENTRY_ADVISOR_R0.md"
CONTRACT_R1_SHA256 = "ccde6bde039f6efc5591a2482c18f5097dc7dbced1fcefc3242e8b4d30f6b06c"
CONTRACT_ADVISOR_SHA256 = "9335d796f6d410c96d3db9db7ebdd9bacfccca82057e1846ca525a10e84f1087"

# --------------------------------------------------------------------------- existing frozen
#: lm-wall.v2 open floors (wallrule.py R1, R2): what makes a V0 level row a level episode.
MIN_NOTIONAL_USDT = 250_000.0
MIN_MULTIPLE = 5.0
#: The screen's set (DISPLAY_MIN_NOTIONAL_USDT, DISPLAY_WALL_LIMIT).
DISPLAY_MIN_NOTIONAL_USDT = 500_000.0
DISPLAY_WALL_LIMIT = 6
#: wallrule bin: 5 USDT, anchored in absolute price.
BIN_WIDTH_USDT = 5.0
#: R0.1 APPROACH_BP: the collector's own near-wall bound.
APPROACH_BP = 5.0
#: V0 CANDIDATE_BAND: a level beyond 1 % of mid is out of coverage.
CANDIDATE_BAND = 0.01
#: Round-trip taker cost (D5_4 l.62; Q123; R0.1) and MC R0's meaningful move (2 x cost).
RT_COST_BP = 11.25
MEANINGFUL_BP = 22.5
#: Context cadence bound: a sample gap wider than this breaks a mid path (MC V1 PATH_GAP).
PATH_GAP_MS = 2_500
#: An outcome window with a gap wider than this is excluded.
WINDOW_GAP_MS = 60_000

# --------------------------------------------------------------------------- R1 pre-registered
#: Post-touch survival bins, seconds, [lo, hi).
SURVIVAL_BINS_S = ((0.0, 1.0), (1.0, 5.0), (5.0, 15.0), (15.0, 30.0), (30.0, float("inf")))
SURVIVAL_BIN_NAMES = ("0-1s", "1-5s", "5-15s", "15-30s", "30s+")
OUTCOME_MIN = (5, 15, 30, 60)
PRIMARY_H_MIN = 15
#: Cluster: adjacent display walls closer than this many ATR14(1m).
CLUSTER_ATR_FRACTION = 0.5
ATR_BARS = 14
#: Block bootstrap: 60 min wall-clock blocks, 2,000 resamples, seed.
BLOCK_MS = 3_600_000
BOOTSTRAP_N = 2_000
SEED = 20261012
PERMUTATIONS = 1_000

END_CONSUMED = "CONSUMED"
END_TOUCHED_PULLED = "TOUCHED_PULLED"
END_CANCEL_LIKE = "CANCEL_LIKE"
END_OUT_OF_COVERAGE = "OUT_OF_COVERAGE"
END_UNKNOWN = "UNKNOWN"
END_CLASSES = (END_CONSUMED, END_TOUCHED_PULLED, END_CANCEL_LIKE, END_OUT_OF_COVERAGE, END_UNKNOWN)

#: Sample stages (R0.1 section 8).
STAGES = ((20, "INSUFFICIENT"), (50, "EARLY"), (100, "REVIEW_ELIGIBLE"), (None, "STRONGER_EVIDENCE"))

# --------------------------------------------------------------------------- advisor
FAMILIES = ("F1", "F2", "F3", "F4", "F5", "F6")
FAMILY_NAMES = {"F1": "ASK_REJECTION", "F2": "ASK_BREAKOUT", "F3": "BID_REACTION",
                "F4": "BID_BREAKDOWN", "F5": "CLUSTER_REJECTION", "F6": "CLUSTER_BREAKOUT"}
REJECTION_FAMILIES = ("F1", "F3", "F5")
BREAKOUT_FAMILIES = ("F2", "F4", "F6")
TIMINGS = ("T0", "T1", "T2", "T3")
T2_AFTER_TOUCH_MS = 15_000
T3_AFTER_TOUCH_MS = 30_000
RANGE_WINDOW_MS = 60_000
HOLD_MAX_MIN = 60
FIXED_EXITS_MIN = (5, 15, 30, 60)
#: Binance funding settlement hours (UTC) and the top share that defines concentration.
FUNDING_HOURS_UTC = (0, 8, 16)
CONCENTRATION_TOP_SHARE = 0.10
CONCENTRATION_MAX = 0.50


def stage(n: int) -> str:
    for bound, name in STAGES:
        if bound is None or n < bound:
            return name
    return STAGES[-1][1]


def identity(repo_root: Path) -> dict:
    out = {}
    for name, rel, want in (("r1", CONTRACT_R1, CONTRACT_R1_SHA256),
                            ("advisor", CONTRACT_ADVISOR, CONTRACT_ADVISOR_SHA256)):
        path = repo_root / rel
        have = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        out[name] = {"path": rel, "recorded": want, "on_disk": have, "agrees": have == want}
    return out


__all__ = [name for name in dir() if name.isupper()] + ["stage", "identity"]
