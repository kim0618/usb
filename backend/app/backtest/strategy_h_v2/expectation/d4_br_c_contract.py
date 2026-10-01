"""H-V2-D4-BR-C: the disjoint convergence confirmation, frozen before its first live call.

This file is the execution half of `d4_br_contract`. That module preregistered the sample and the
success rule and deliberately carries no runner and no budget - a test asserts it contains neither
`call_opus` nor `HARD_CAP`, because the step that wrote it was forbidden to spend. The money, the
roots and the verdict layering live here instead, so the preregistration stays a preregistration.

Nothing here is a new threshold. Everything that decides the outcome is imported:

    sample + checksum   `d4_br_contract.D4_BR_CONFIRMATION_SAMPLE` / `confirmation_checksum`
    E1                  `d4_1_contract.E1_MIN_SCHEMA_VALID_RATE` via `e1_confirmation_passes`
    E2-E8               `d4_1_contract.evaluate_d4_1_gates`, tier B's frozen set
    SF1                 `d4_b_contract.sf1_result` + `SF1_*`, frozen before Tier B ran
    verdict             `d4_b_contract.d4_b_verdict`
    D5 authorization    `d4_b_contract.d5_authorization`
    budget arithmetic   `d4_2_contract.TIER_B_BUDGET`, the repaired call-topology reserve

The one thing this file adds to the frozen aggregation is a DOWNGRADE-ONLY limitation reading, and
it is written down before the run for the reason every other threshold here was: a repair-dependence
rule invented after seeing the repair counts would be a rule chosen to fit them.
"""

from __future__ import annotations

from pathlib import Path

from app.backtest.strategy_h_v2.expectation.contract_v2 import MAX_REPAIR_ATTEMPTS
from app.backtest.strategy_h_v2.expectation.d4_2_contract import TIER_B_BUDGET
from app.backtest.strategy_h_v2.expectation.d4_b_contract import D4BVerdict
from app.backtest.strategy_h_v2.expectation.d4_br_contract import (
    D4_BR_CONFIRMATION_SAMPLE,
    D4_BR_CONFIRMATION_CHECKSUM,
    D4_BR_N,
    confirmation_checksum,
)

D4_BR_C_CONTRACT_VERSION = "h_v2_d4_br_c_disjoint_convergence_confirmation_v1"

D4_BR_C_ROOT = Path("data/runtime/strategy_h_v2/d4_br_c")
ANALYSES_ROOT = D4_BR_C_ROOT / "analyses"
D3_LEG_ATTEMPTS_ROOT = D4_BR_C_ROOT / "d3_leg" / "attempts"

D4_BR_C_RUN_ID_PREFIX = "D4_BR_C"
"""A separate root and a separate run-id prefix, so a confirmation record can never be read as a
Tier B record. Tier B's own store under `d4_b/` is not written to by this step."""

D4_BR_C_N = D4_BR_N
assert D4_BR_C_N == len(D4_BR_CONFIRMATION_SAMPLE) == 4, (
    "the confirmation is the preregistered four; a different length means the sample moved"
)

# ---------------------------------------------------------------------------------------------
# Budget - the authorized $30.40 ceiling
# ---------------------------------------------------------------------------------------------

D4_BR_C_HARD_CAP_USD = round(D4_BR_C_N * TIER_B_BUDGET.candidate_worst_case_budget_usd, 2)
"""$30.40. A worst-case CEILING to stop a mid-run budget refusal, not an expected spend.

Derived from `TIER_B_BUDGET.candidate_worst_case_budget_usd` ($7.60 - the per-call cap times the D4
call topology, plus D3's observed worst case) times four, bound to that module rather than typed, so
a literal here can never disagree with the accounting it came from.

Rounded to cents for the reason D4-B found the hard way: 7.6 has no exact binary representation, so
an unrounded `n * 7.6` can land femtocents below the decimal amount the authorization names and
silently truncate the sample before its last candidate. At n=4 the product happens to be exact, which
is luck rather than a property - the rounding is kept so the ceiling is the stated decimal either
way, and the assertions below pin that it can only ever correct an artifact, never widen a cap."""
assert D4_BR_C_HARD_CAP_USD == 30.40, "the ceiling is the authorized decimal amount"
assert abs(
    D4_BR_C_HARD_CAP_USD - D4_BR_C_N * TIER_B_BUDGET.candidate_worst_case_budget_usd) < 0.01, (
    "rounding to cents may correct a float artifact; it may never move the cap by a cent"
)

#: Asserted rather than trusted: at the authorized ceiling the preflight admits all four candidates
#: even if each spends its entire worst case, and refuses a fifth. A cap that cannot admit its own
#: frozen sample is a truncation, not a cap.
_spent = 0.0
for _i in range(D4_BR_C_N):
    assert TIER_B_BUDGET.admits_next_candidate(
        spent_so_far_usd=_spent, overall_hard_cap_usd=D4_BR_C_HARD_CAP_USD), (
        f"candidate {_i + 1} of {D4_BR_C_N} is not admitted at its own frozen worst case"
    )
    _spent += TIER_B_BUDGET.candidate_worst_case_budget_usd
assert not TIER_B_BUDGET.admits_next_candidate(
    spent_so_far_usd=_spent, overall_hard_cap_usd=D4_BR_C_HARD_CAP_USD), (
    "a fifth candidate must not be admitted; the ceiling has to bound something"
)
del _spent, _i

D4_BR_C_NO_AUTOMATIC_EXPANSION = True
"""The ceiling is not raised by this step, by the runner, or by a candidate that wants another
round. A run that reaches it stops and reports `budget_exhausted`."""

OBSERVED_PROJECTION_USD = round(
    D4_BR_C_N * (1.4692885 + 1.2957568), 2)
"""$11.06, non-gating diagnostic only: D3.3's measured $1.4692885 per candidate plus D4.3A's
$1.2957568, times four - the same two measurements Tier B projected from. Reported beside the actual
spend so "how close to the projection" is answerable. The ceiling does the gating."""


# ---------------------------------------------------------------------------------------------
# Verdict - the frozen aggregation, with a downgrade-only limitation reading
# ---------------------------------------------------------------------------------------------

REPAIR_DEPENDENCE_FULL_BUDGET_ROUNDS = MAX_REPAIR_ATTEMPTS
"""2. A candidate that used every repair round available to it converged on its last chance."""

REPAIR_DEPENDENCE_HIGH_AT = 3
"""Most of four. Strictly more than half, so a 2-of-4 split is not called "most".

Frozen here, before the run, and it is the §15 condition written as a number: a 4/4 that mostly took
the full repair budget is a 4/4 whose margin is one round wide, and reporting that as an unqualified
PASS would overstate what the confirmation measured."""


def repair_dependence_is_high(full_budget_candidates: int) -> bool:
    return full_budget_candidates >= REPAIR_DEPENDENCE_HIGH_AT


def d4_br_c_verdict(
    statuses: dict[str, str],
    *,
    repair_dependence_high: bool = False,
    structural_instability_found: bool = False,
) -> D4BVerdict:
    """`d4_b_contract.d4_b_verdict` over E1-E8 + SF1, then a downgrade-only limitation reading.

    The frozen function decides FAIL and decides what passes; this wrapper can only move a PASS to
    PASS_WITH_LIMITATIONS. It cannot turn a FAIL into a pass, it cannot turn a
    PASS_WITH_LIMITATIONS into a PASS, and it has no path that makes a result better than the gates
    found it - which is the property that makes adding it safe rather than a new threshold.

    `structural_instability_found` is a judgement the write-up supplies and the gates cannot compute;
    it defaults to False so a silent caller gets the frozen aggregation unchanged.
    """
    from app.backtest.strategy_h_v2.expectation.d4_b_contract import d4_b_verdict

    verdict = d4_b_verdict(statuses)
    if verdict is D4BVerdict.PASS and (repair_dependence_high or structural_instability_found):
        return D4BVerdict.PASS_WITH_LIMITATIONS
    return verdict


def sample_integrity() -> dict:
    """The preflight, as data: the preregistered four with their checksum recomputed."""
    return {
        "checksum_frozen": D4_BR_CONFIRMATION_CHECKSUM,
        "checksum_recomputed": confirmation_checksum(),
        "checksum_matches": confirmation_checksum() == D4_BR_CONFIRMATION_CHECKSUM,
        "sample": [{"ticker": e.ticker, "cik": e.cik, "depth": e.depth, "priority": e.priority}
                   for e in D4_BR_CONFIRMATION_SAMPLE],
    }
