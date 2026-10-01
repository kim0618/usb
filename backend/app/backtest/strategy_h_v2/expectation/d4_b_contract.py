"""H-V2-D4-B: the Tier B execution preregistration, frozen before the first live call.

What this file adds, and what it deliberately does not. Tier B's sample, its E1-E8 thresholds, its
verdict contract and its manual-audit subset were all frozen before D4-B existed and are used here BY
REFERENCE, never restated:

    sample + CIK exclusion      `d4_1_contract.TIER_B_SAMPLE` / `EXCLUDED_CIKS`  (frozen 2026-09-29)
    E1-E8 thresholds            `d4_1_contract.E1_..E8_` + `evaluate_d4_1_gates`
    verdict                     `d4_1_contract.d4_1_verdict` (PASS / PASS_WITH_LIMITATIONS / FAIL)
    budget accounting           `d4_2_contract.TIER_B_BUDGET` (the repaired call-topology reserve)
    manual-audit subset         `manual_audit_strategy_h_v2_d4_1` (all six issuers, seeded sampling)
    D3 leg contract             `d3_3_contract` + `run_strategy_h_v2_d3_3.research_one_v3`
    D4 leg contract             `run_strategy_h_v2_d4_2.analyze_one_v2`

The ONE thing that has no frozen home yet is the State Fidelity gate. `CODE_OWNED_STATE_FIDELITY` was
built in D4-H, repaired in D4-H1, and adjudicated offline in both - wired to no gate list, because
adding it to a frozen M-gate list after two graded runs would have changed what those runs measured.
Tier B is the first run where it is ACTIVE, so the D4-B brief §21 requires its gate id and its
aggregation rule to be frozen here, before any Tier B output exists. That is what this file is for.

The Tier B hard cap is also restated here, because Tier B is now funded by an independent
authorization rather than by what was left of D4.1's $30.00. The number is not new - it is
`d4_2_contract.TIER_B_HARD_BUDGET_USD`, the repaired worst case - and it is asserted against that
module rather than typed as a literal, so the two cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    TIER_B_SAMPLE,
    tier_b_checksum,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    MechanicalGateStatus,
    TIER_B_BUDGET,
    TIER_B_HARD_BUDGET_USD,
)
from app.backtest.strategy_h_v2.expectation.state_fidelity import GATE_ID as STATE_FIDELITY_GATE_ID

D4_B_CONTRACT_VERSION = "h_v2_d4_b_tier_b_disjoint_live_contract_v1"

D4_B_ROOT = Path("data/runtime/strategy_h_v2/d4_b")
ANALYSES_ROOT = D4_B_ROOT / "analyses"
D3_LEG_ATTEMPTS_ROOT = D4_B_ROOT / "d3_leg" / "attempts"

TIER_B_N = len(TIER_B_SAMPLE)
assert TIER_B_N == 6, "Tier B is the frozen six; a different length means the sample moved"

# ---------------------------------------------------------------------------------------------
# Budget - independent authorization, same repaired arithmetic (brief §8/§9)
# ---------------------------------------------------------------------------------------------

TIER_B_HARD_CAP_USD = round(TIER_B_HARD_BUDGET_USD, 2)
"""$45.60. A contractual worst-case CEILING, not an expected spend.

It is `d4_2_contract`'s own repaired figure - the per-call cap times the call topology, plus D3's
observed worst case, times six - bound to that module rather than retyped, so a literal here can never
disagree with the accounting it came from. D4.1's $3.60-per-candidate reserve is what this replaced: it
reserved one call's cap for a candidate that can make three, which is why it could not have stopped
the overrun it produced.

ROUNDED TO CENTS, and the reason is a defect this step found in its own dry run rather than a
preference. `TIER_B_BUDGET.candidate_worst_case_budget_usd` is 7.6, which has no exact binary
representation, so `6 * 7.6` evaluates to 45.599999999999994 - seven femtocents BELOW the $45.60 the
authorization names. `admits_next_candidate` compares `spent + 7.6 <= cap`, and before the SIXTH
candidate that reads `45.6 <= 45.599999999999994`, which is False. The frozen six would therefore have
stopped at five with `budget_exhausted: True` if every candidate had spent its full worst case - the
sample silently truncated by an IEEE-754 artifact, not by a budget decision.

It would not have bitten at observed costs (~$2.50/candidate leaves the sixth candidate $33 of room),
which is exactly why it is worth naming: a latent truncation that only appears when spending runs high
is a truncation that appears on the run you least want it on. Rounding to cents makes the ceiling the
decimal amount the authorization actually states. The assertions below pin both that it equals $45.60
exactly and that the correction is smaller than one cent, so this can never become a route to widening
a cap."""
assert TIER_B_HARD_CAP_USD == 45.60, "the ceiling is the authorized decimal amount"
assert abs(TIER_B_HARD_CAP_USD - TIER_B_HARD_BUDGET_USD) < 0.01, (
    "rounding to cents may correct a float artifact; it may never move the cap by a cent"
)
assert TIER_B_HARD_CAP_USD >= TIER_B_HARD_BUDGET_USD, "the correction is upward, by design"

#: The property the rounding exists to guarantee, asserted rather than trusted: at the frozen ceiling
#: the preflight admits all six candidates even if each spends its entire worst case, and refuses a
#: seventh. A cap that cannot admit its own frozen sample is not a cap, it is a truncation.
_spent = 0.0
for _i in range(TIER_B_N):
    assert TIER_B_BUDGET.admits_next_candidate(
        spent_so_far_usd=_spent, overall_hard_cap_usd=TIER_B_HARD_CAP_USD), (
        f"candidate {_i + 1} of {TIER_B_N} is not admitted at its own frozen worst case"
    )
    _spent += TIER_B_BUDGET.candidate_worst_case_budget_usd
assert not TIER_B_BUDGET.admits_next_candidate(
    spent_so_far_usd=_spent, overall_hard_cap_usd=TIER_B_HARD_CAP_USD), (
    "a seventh candidate must not be admitted; the ceiling has to bound something"
)
del _spent, _i

OBSERVED_PROJECTION_USD = 16.59
"""Non-gating diagnostic only (brief §8): D3.3's measured $1.4692885/candidate plus D4.3A's
$1.2957568, times six. Reported beside the actual spend so "how close to the projection" is answerable,
and it gates nothing - the ceiling does the gating."""


# ---------------------------------------------------------------------------------------------
# SF1 - the State Fidelity gate, frozen here before any Tier B output exists (brief §21)
# ---------------------------------------------------------------------------------------------

SF1_GATE_ID = "SF1"
SF1_GATE_NAME = "code-owned categorical state fidelity"
SF1_UNDERLYING_GATE = STATE_FIDELITY_GATE_ID
"""`CODE_OWNED_STATE_FIDELITY`. SF1 is the Tier B gate id; the gate's own name and semantics live in
`state_fidelity.py` and are not restated here."""

SF1_MAX_VIOLATIONS = 0
"""An ASSERTED categorical state that differs from `authoritative_state[candidate][metric]` is a
fabricated or altered code-owned fact. Zero, and for the same reason E3's and E5's thresholds are
zero: there is no version of this that is an operational inconvenience.

Not adjustable after a result. If Tier B produces a violation, it is reported as a violation."""

SF1_AGGREGATION = "ASSERTION"
"""The unit adjudicated, frozen before the result. D4-H1 settled this: an occurrence is carried
through polarity, then metric binding, then comparison, and only an ASSERTED occurrence bound to a
code-owned metric is compared. The run-level count is the number of FAILED assertions summed over
every candidate - not a per-candidate rate, and not a per-claim count, because one claim can assert
several metrics' states and each is its own comparison."""

SF1_ZERO_ELIGIBLE_IS = MechanicalGateStatus.NOT_EVALUATED
"""Brief §16/§26. If no assertion anywhere was bound and compared, SF1 is NOT_EVALUATED. A gate that
declined to evaluate everything and then reported zero violations would be reporting nothing, and
`state_fidelity.state_fidelity_status` already enforces this - restated here so the Tier B report's
reader does not have to go and check."""

SF1_NOT_A_VIOLATION = ("NEGATED", "REJECTED_OR_CONTRASTED")
"""Brief §16. Naming a state to deny or contrast it is not asserting it, so neither polarity can
produce an SF1 violation. They are still counted and reported, because a polarity classifier that
silently dropped them could not be audited."""

SF1_IS_A_CORE_GATE = True
"""SF1 FAILURE is a FAIL, never a reportable limitation - the same class as E3/E4/E5/E7. A state
mismatch means the model restated a code-owned categorical fact as something the pipeline does not
hold, which is fabrication of exactly the kind Tier B exists to detect.

Core-ness here means precisely that, and not more, which is a distinction this step's dry run forced
rather than one chosen for convenience. `d4_1_contract.d4_1_verdict` treats a core gate that is merely
NOT_EVALUATED as a FAIL, and for E3/E4/E5/E7 that is right: each is a count over every graded output,
so each always has a denominator in Tier B and a missing one would mean the audit did not run. SF1's
denominator is DATA-dependent - a run in which the model never restated a code-owned categorical state
has nothing for SF1 to compare - so inheriting that rule unchanged would have graded such a run FAIL
for the absence of a defect. `d4_b_verdict` therefore splits the two readings: a FAIL is fatal, and a
zero denominator caps the verdict at PASS_WITH_LIMITATIONS instead of producing one."""


# ---------------------------------------------------------------------------------------------
# M8 scope for Tier B, recorded explicitly (brief §18)
# ---------------------------------------------------------------------------------------------

M8_ATOMIC_NUMERIC_OWNERSHIP = "ACTIVE"
M8_R1_STATE_TOKEN_COVERAGE = "CLOSED"
M8_R2_WINDOW_ELLIPSIS_COVERAGE = "CLOSED"
M8_R3_COMPOUND_SET_VALUED = "DEFERRED"
"""Unchanged and not implemented in this run. R3 changes M8's unit of comparison rather than its
coverage, its measured exposure in the D4-H corpus is 0 occurrences, and implementing it mid-run would
change what E5 measures after the run started."""

M8_SCOPE_NOTE = (
    "E5/M8 examines ATOMIC claims only, with R1 and R2's corrected numeric-role coverage. A compound "
    "claim's numeric ownership is reported in `compound_claim_coverage_gap`, wired to no gate, "
    "pending the R3 decision. STATE_TOKEN facts are not M8's subject at all - they are SF1's."
)


# ---------------------------------------------------------------------------------------------
# Gate list and aggregation
# ---------------------------------------------------------------------------------------------

D4_B_GATES: tuple[str, ...] = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", SF1_GATE_ID)
"""E1-E8 exactly as `d4_1_contract.TIER_B_GATES` froze them, plus SF1. Order is frozen."""

D4_B_CORE_GATES: tuple[str, ...] = ("E3", "E4", "E5", "E7", SF1_GATE_ID)
"""`d4_1_contract.D4_1_CORE_GATES` plus SF1. A FAIL on any of these is a FAIL, never a limitation."""

MUST_ALWAYS_EVALUATE: tuple[str, ...] = ("E3", "E4", "E5", "E7")
"""Core gates whose denominator is every graded output, so NOT_EVALUATED means the audit did not run
rather than that there was nothing to check - and is therefore a FAIL. SF1 is deliberately absent:
see `SF1_IS_A_CORE_GATE`."""


class D4BVerdict(StrEnum):
    PASS = "PASS"
    PASS_WITH_LIMITATIONS = "PASS_WITH_LIMITATIONS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class SF1Result:
    eligible_assertions: int
    violations: int
    status: MechanicalGateStatus

    def to_dict(self) -> dict:
        return {"gate": SF1_GATE_ID, "name": SF1_GATE_NAME,
                "underlying_gate": SF1_UNDERLYING_GATE, "aggregation": SF1_AGGREGATION,
                "threshold": f"== {SF1_MAX_VIOLATIONS}",
                "observed": f"{self.violations} violating assertions of "
                            f"{self.eligible_assertions} evaluated",
                "eligible_assertions": self.eligible_assertions,
                "violations": self.violations, "status": self.status.value}


def sf1_result(*, evaluated_assertions: int, failed_assertions: int) -> SF1Result:
    """SF1's status from the two numbers `SF1_AGGREGATION` names. Zero evaluated is NOT_EVALUATED and
    is never a PASS."""
    if failed_assertions > SF1_MAX_VIOLATIONS:
        status = MechanicalGateStatus.FAIL
    elif evaluated_assertions == 0:
        status = SF1_ZERO_ELIGIBLE_IS
    else:
        status = MechanicalGateStatus.PASS
    return SF1Result(evaluated_assertions, failed_assertions, status)


def d4_b_verdict(statuses: dict[str, str]) -> D4BVerdict:
    """Tier B's verdict over E1-E8 + SF1, from `d4_1_contract.d4_1_verdict`'s rule with one split.

        any gate FAIL                         -> FAIL   (core or not; a defect is a defect)
        a MUST_ALWAYS_EVALUATE gate unscored   -> FAIL   (its denominator is every graded output, so
                                                          a missing score means the audit did not run)
        every gate PASS                        -> PASS
        otherwise (something NOT_EVALUATED)    -> PASS_WITH_LIMITATIONS

    A zero denominator can therefore cap the verdict but can never produce a PASS, which is §26, and
    it can never manufacture a FAIL either, which is what `SF1_IS_A_CORE_GATE` explains.
    """
    missing = [g for g in D4_B_GATES if g not in statuses]
    if missing:
        raise ValueError(f"no status recorded for {missing}; a gate is never a silent PASS")
    if any(statuses[g] == MechanicalGateStatus.FAIL.value for g in D4_B_GATES):
        return D4BVerdict.FAIL
    if any(statuses[g] != MechanicalGateStatus.PASS.value for g in MUST_ALWAYS_EVALUATE):
        return D4BVerdict.FAIL
    if all(statuses[g] == MechanicalGateStatus.PASS.value for g in D4_B_GATES):
        return D4BVerdict.PASS
    return D4BVerdict.PASS_WITH_LIMITATIONS


# ---------------------------------------------------------------------------------------------
# D5 authorization (brief §28)
# ---------------------------------------------------------------------------------------------

D5_READY = "READY FOR CONTRACT DESIGN"
D5_NOT_READY = "NOT READY"


def d5_authorization(verdict: D4BVerdict) -> str:
    """D5 becomes READY FOR CONTRACT DESIGN - not READY TO EXECUTE - on PASS or
    PASS_WITH_LIMITATIONS. Contract design is the next step either way; execution is a further user
    authorization that this function does not grant."""
    if verdict in (D4BVerdict.PASS, D4BVerdict.PASS_WITH_LIMITATIONS):
        return D5_READY
    return D5_NOT_READY


def sample_integrity() -> dict:
    """The §5 preflight, as data: the frozen six with their checksum recomputed."""
    return {
        "tier_b_checksum_recomputed": tier_b_checksum(),
        "sample": [{"ticker": e.ticker, "cik": e.cik, "depth": e.depth, "priority": e.priority}
                   for e in TIER_B_SAMPLE],
    }
