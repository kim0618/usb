"""Every constant of `q123-forward.v1`, each one quoted from a frozen parent. Nothing is tuned here."""
from __future__ import annotations

from ..market_context_r0 import families as R0F
from ..market_context_r0 import layers as R0L
from ..market_context_r0.structure import THETA_1H, THETA_5M

#: Contract section 1: question -> R0 T1 family. Q3 is split exactly where R0 split it.
QUESTIONS: dict[str, str] = {"Q1": "F1", "Q2": "F2", "Q3A": "F3", "Q3B": "F4"}
#: R0 FAMILIES gives (track, side, lean, W); re-exported so nothing restates a lean.
QUESTION_SIDE = {q: R0F.FAMILIES[f][1] for q, f in QUESTIONS.items()}
QUESTION_LEAN = {q: R0F.FAMILIES[f][2] for q, f in QUESTIONS.items()}
VARIANT = "SIGN"  # R0 section 17.11

#: Contract section 3 (Market Context V1 section 5 anchoring rule applied to the production start).
SERIES_START_MS = 1_788_998_400_000   # 2026-09-10T00:00:00Z
PUBLISH_FROM_MS = 1_791_590_400_000   # 2026-10-10T00:00:00Z
THETAS = (THETA_1H, THETA_5M)

#: Contract section 6. R0 section 10 pre-registers 5 / 15 / 60 for T1; 30 is diagnostic only.
HORIZONS_MIN: tuple[int, ...] = (5, 15, 30, 60)
TEST_GRID_HORIZONS_MIN: tuple[int, ...] = (5, 15, 60)
#: D5_4 cost contract line 62: 11.25 bp round trip = fee 11.0 + spread / funding 0.25.
EST_FEE_BP = 11.0
EST_SLIPPAGE_BP = 0.25
RT_COST_BP = R0L.RT_COST_BP
assert abs(EST_FEE_BP + EST_SLIPPAGE_BP - RT_COST_BP) < 1e-9, "cost split must equal R0's RT"

#: Contract section 5: R0 section 17.4 reasons, plus one from CONTRACT_CTX_V1_1 section 2.
R0_REASONS = ("OUTSIDE_JOURNAL", "NO_SAMPLE_AT_MINUTE", "ANCHOR_NOT_OK", "NO_WALL_IN_BAND",
              "NO_DECISION_SAMPLE", "DECISION_NOT_OK", "FLOW_NOT_COMPLETE")
GENERATION_CHANGED = "GENERATION_CHANGED_IN_W"

#: Contract section 8.
STAGES = ((100, "STRONGER_EVIDENCE_AVAILABLE"), (50, "ELIGIBLE_FOR_SIGNAL_CONTRACT_REVIEW"),
          (20, "EARLY_SIGNAL_ONLY"), (0, "INSUFFICIENT"))
STAGE_HORIZON_MIN = 60

CANDIDATE_LABEL = {"Q1": "SHORT CANDIDATE (RESEARCH / UNVALIDATED)",
                   "Q2": "LONG CONTINUATION CANDIDATE (RESEARCH / UNVALIDATED)",
                   "Q3A": "LONG REVERSAL CANDIDATE (RESEARCH / UNVALIDATED)",
                   "Q3B": "SHORT CONTINUATION CANDIDATE (RESEARCH / UNVALIDATED)"}


def stage(n: int) -> str:
    for floor, name in STAGES:
        if n >= floor:
            return name
    return "INSUFFICIENT"
