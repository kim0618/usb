"""H-V2-D4.2: the Tier A re-run preregistration, frozen before the re-run exists.

D4.1 Tier A returned MECHANICAL NOT READY on the frozen contract's enforcement layer. D4.2 repairs
that layer and re-runs the SAME shakedown on the SAME three issuers. The sample is not refreshed
and not enlarged, and the reason is worth stating because refreshing it would look like rigour:
Tier A tests wiring, not generalization. Changing its issuers would change what the two runs can be
compared on, and comparability is the entire value of re-running a shakedown that has already
failed once.

What this tier can conclude is bounded the same way D4.1's was. Its issuers are three of D3.3's
twelve, whose outputs were read closely while D4 was being designed, so nothing here is evidence
about interpretation quality. The verdict below is MECHANICAL READY or MECHANICAL NOT READY, and
neither is a statement about whether an expectation gap means anything.

Everything frozen here was decided before the re-run: the gates, the budget, the stop rule and the
sample. The E1-E8 quality gates are NOT restated - they live in `d4_1_contract.py` unchanged, and
Tier B adjudicates on them exactly as it would have.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.backtest.strategy_h_v2.expectation.contract_v2 import (
    CandidateBudgetContract,
    D4_CONTRACT_VERSION,
    MAX_CALLS_PER_CANDIDATE,
)
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    D3_WORST_CASE_CANDIDATE_USD,
    D4_1_HARD_BUDGET_USD,
    TIER_A_CHECKSUM,
    TIER_A_N,
    TIER_A_TICKERS,
    TIER_B_SAMPLE,
    tier_a_checksum,
)

D4_2_CONTRACT_VERSION = "h_v2_d4_2_contract_repair_v1"

#: Unchanged from D4.1, by reference rather than by copy, so a "same sample" claim cannot drift
#: into a re-drawn one. Brief §24: the three Tier A issuers are reused deliberately.
TIER_A_TICKERS_UNCHANGED: tuple[str, ...] = TIER_A_TICKERS
assert tier_a_checksum(TIER_A_TICKERS_UNCHANGED) == TIER_A_CHECKSUM, (
    "D4.2 Tier A must run the frozen D4.1 sample; a checksum mismatch means the sample moved"
)

#: Where D3.3's stored attempts live. Defined here rather than imported from
#: `app.dev.run_strategy_h_v2_d3_3`, because that module imports an untracked file and so cannot be
#: imported from a clean checkout - which is the very property gate M12 measures.
D3_3_ATTEMPTS_ROOT = Path("data/runtime/strategy_h_v2/d3_3/attempts")
D4_2_ROOT = Path("data/runtime/strategy_h_v2/d4_2")


# ---------------------------------------------------------------------------------------------
# Budget - frozen before any result is seen (brief §12)
# ---------------------------------------------------------------------------------------------
#
# The overall hard cap is unchanged at $30.00. What changes is what a candidate is allowed to
# reserve against it: the per-call cap times the calls a candidate can make, instead of the
# per-call cap alone. D4.1's two candidates cost $2.43 and $2.11 against a $2.00 reserve, so the
# old number did not bound anything - it just happened to be the same figure as the CLI flag.
#
# The $30.00 cap is NOT re-spent. D4.1 already drew $4.54 from it against the same authorization;
# `D4_1_ALREADY_SPENT_USD` below is subtracted before D4.2's own ceiling is computed, so the two
# runs share one cap rather than each getting a fresh one.

PER_CALL_MAX_BUDGET_USD = 2.00
"""What the CLI's `--max-budget-usd` is handed. A cap on ONE call, which is what it always was."""

D4_1_ALREADY_SPENT_USD = 4.541394
"""Measured, not estimated: the sum of D4.1 Tier A's two candidate costs."""

OVERALL_HARD_CAP_USD = D4_1_HARD_BUDGET_USD
D4_2_REMAINING_CAP_USD = OVERALL_HARD_CAP_USD - D4_1_ALREADY_SPENT_USD

TIER_A_BUDGET = CandidateBudgetContract(
    per_call_max_budget_usd=PER_CALL_MAX_BUDGET_USD,
    max_calls_per_candidate=MAX_CALLS_PER_CANDIDATE,
    preceding_stage_worst_case_usd=0.0,
)
"""Tier A's D3 leg was bought by D3.3 and is not re-charged here, so the preceding stage is $0."""

TIER_B_BUDGET = CandidateBudgetContract(
    per_call_max_budget_usd=PER_CALL_MAX_BUDGET_USD,
    max_calls_per_candidate=MAX_CALLS_PER_CANDIDATE,
    preceding_stage_worst_case_usd=D3_WORST_CASE_CANDIDATE_USD,
)
"""Tier B chains a D3 run first, at D3's own observed worst case, before the D4 call topology."""

TIER_A_HARD_BUDGET_USD = TIER_A_N * TIER_A_BUDGET.candidate_worst_case_budget_usd
TIER_B_HARD_BUDGET_USD = len(TIER_B_SAMPLE) * TIER_B_BUDGET.candidate_worst_case_budget_usd

#: The arithmetic that made D4.1's budget contract wrong, written down so it cannot come back.
assert TIER_A_BUDGET.candidate_worst_case_budget_usd == PER_CALL_MAX_BUDGET_USD * 3 == 6.00
assert TIER_B_BUDGET.candidate_worst_case_budget_usd == 6.00 + D3_WORST_CASE_CANDIDATE_USD

#: Stated rather than asserted: under the repaired accounting the frozen two-tier sample no longer
#: fits the $30.00 cap, and pretending otherwise is what the V1 assert did. 3 x $6.00 = $18.00 for
#: Tier A and 6 x $7.60 = $45.60 for Tier B, against $25.46 remaining. Tier A fits; Tier B does not
#: and cannot be authorized on this cap without the user deciding how - a smaller Tier B sample, a
#: raised cap, or a lower per-call cap. D4.2 does not make that choice, and it does not run Tier B.
TIER_A_FITS_REMAINING_CAP = TIER_A_HARD_BUDGET_USD <= D4_2_REMAINING_CAP_USD
TIER_B_FITS_REMAINING_CAP = (
    TIER_A_HARD_BUDGET_USD + TIER_B_HARD_BUDGET_USD) <= D4_2_REMAINING_CAP_USD
assert TIER_A_FITS_REMAINING_CAP, "Tier A must fit the remaining cap or it cannot be re-run"
assert not TIER_B_FITS_REMAINING_CAP, (
    "recorded so the doc's claim is checked by code: Tier B does NOT fit the remaining cap under "
    "the repaired worst-case accounting, and needs a user decision before it can be authorized"
)


# ---------------------------------------------------------------------------------------------
# M1-M12 mechanical gates (brief §26)
# ---------------------------------------------------------------------------------------------

class MechanicalGateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class MechanicalGate:
    gate: str
    name: str
    requirement: str
    observed: str
    status: MechanicalGateStatus

    def to_dict(self) -> dict:
        return {"gate": self.gate, "name": self.name, "requirement": self.requirement,
                "observed": self.observed, "status": self.status.value}


#: Frozen list, in order. A gate that is not evaluated is NOT_EVALUATED and is never a silent PASS
#: - the convention D3.1's R10, D3.3's L3 and D4.1's E6/E8 all used for an unperformed check.
M_GATES: tuple[tuple[str, str, str], ...] = (
    ("M1", "canonical model valid",
     "every live call reports canonicalModel == claude-opus-5-5 and model_mismatch is False"),
    ("M2", "schema parse valid",
     "every final response is one parseable JSON object matching the V2 output schema"),
    ("M3", "consensus absence accepted",
     "no final output is rejected for stating that consensus evidence is unavailable"),
    ("M4", "fabricated consensus rejected or absent",
     "no final output attributes an expectation to analysts or the market"),
    ("M5", "direction enum contract respected",
     "every management_signal_changes[].direction is one of the seven frozen members, and the "
     "schema shown to the model listed them"),
    ("M6", "C1 respected",
     "no POSITIVE/WIDE_POSITIVE final output rests on price history alone"),
    ("M7", "C4 respected",
     "no final expectation_gap_confidence exceeds the frozen MEDIUM ceiling"),
    ("M8", "numeric ownership respected",
     "no claim cites a code-owned fact while stating a different number"),
    ("M9", "forbidden D5/D6 fields absent",
     "no prohibited decision, valuation or price-level field at any depth"),
    ("M10", "raw telemetry complete",
     "every attempt stores its full untruncated initial and repair responses, costs and statuses"),
    ("M11", "candidate budget contract respected",
     "every candidate's total cost is within its worst-case reserve, the preflight reserved the "
     "full call topology, and the run total is within the remaining cap"),
    ("M12", "clean-checkout reproducibility proven",
     "the D4 package, evidence build, schema generation, validator and Tier A runner import from "
     "a tree containing only committed files"),
)

M_GATE_IDS: tuple[str, ...] = tuple(g[0] for g in M_GATES)

#: A failure of any of these is always MECHANICAL NOT READY and never a reportable limitation.
#: M3/M5 are the two D4.1 defects; M4/M9 are fabrication and leakage; M12 is reproducibility.
M_CORE_GATES: tuple[str, ...] = ("M3", "M4", "M5", "M9", "M12")


def build_gates(observations: dict[str, tuple[bool | None, str]]) -> list[MechanicalGate]:
    """`{gate_id: (passed_or_None, observed_text)}` -> the frozen gate list, in frozen order.

    `None` means the check was not performed, which is NOT_EVALUATED. A gate missing from
    `observations` entirely is also NOT_EVALUATED - there is no default PASS.
    """
    gates: list[MechanicalGate] = []
    for gate_id, name, requirement in M_GATES:
        passed, observed = observations.get(gate_id, (None, "not performed"))
        if passed is None:
            status = MechanicalGateStatus.NOT_EVALUATED
        else:
            status = MechanicalGateStatus.PASS if passed else MechanicalGateStatus.FAIL
        gates.append(MechanicalGate(gate_id, name, requirement, observed, status))
    return gates


class MechanicalVerdict(StrEnum):
    MECHANICAL_READY = "MECHANICAL READY"
    MECHANICAL_NOT_READY = "MECHANICAL NOT READY"


def tier_a_verdict(gates: list[MechanicalGate]) -> MechanicalVerdict:
    """READY only when every one of M1-M12 passed. Not a quality judgement in either direction:
    READY says the contract can be executed, and says nothing about what it produced."""
    if all(g.status == MechanicalGateStatus.PASS for g in gates) and len(gates) == len(M_GATES):
        return MechanicalVerdict.MECHANICAL_READY
    return MechanicalVerdict.MECHANICAL_NOT_READY


def tier_b_authorized(tier_a: MechanicalVerdict) -> bool:
    """Brief §28. Tier B runs only behind a READY Tier A, and D4.2 adds a second condition it
    cannot satisfy on its own: Tier B must also fit the remaining cap, which it does not."""
    return tier_a == MechanicalVerdict.MECHANICAL_READY and TIER_B_FITS_REMAINING_CAP
