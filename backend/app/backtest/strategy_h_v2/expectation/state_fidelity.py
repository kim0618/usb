"""H-V2-D4-H: `CODE_OWNED_STATE_FIDELITY` - a claim may not rename a code-owned categorical state.

Why this is a separate gate rather than a corner of M8. M8 is "no claim cites a code-owned fact
while stating a DIFFERENT number", and its unit of comparison is a number. A
`research_facts.fundamental_changes.<metric>.state` code fact carries no number at all - its value
is a `change_detection.ChangeState` member - so M8 has nothing to compare it against, and the live
validator has always agreed: `validate._COMPARABLE_UNITS` has no `STATE_TOKEN` entry, so
`check_code_fact_numerics` has never examined a state fact in any run.

Only the AUDIT layer's `numeric_roles.fact_is_restated` ever looked at one, through

    return str(value).upper() in text.upper() or not any(c.isdigit() for c in text)

and that line is wrong in both directions at once, which is what `H_V2_D4_S_M8_COMPOUND_COVERAGE_
AUDIT_V1.md` measured:

  OVER-STRICT (D4-H's R1). A correct qualitative statement about the state fails the moment its
  sentence contains an unrelated digit - "252-session", "D3" - because the escape hatch is a raw
  character scan that never consults `numeric_roles.classify_roles`. Two of the six compound audit
  findings are exactly this and nothing else.

  UNDER-STRICT, and worse. "Revenue is ACCELERATING." against a fact whose value is STABLE PASSES,
  because the sentence has no digit. The one thing a state-ownership check exists to catch is the
  one thing that line lets through, and whether it catches it depends on the presence of an
  unrelated number.

So D4-H removes the state question from the numeric matcher entirely (M8 keeps its exact meaning,
applied only to facts that have a number) and answers it here, on its own terms.

WHAT THIS GATE DECIDES, precisely. Set containment, per claim:

    eligible    the claim cites >= 1 STATE_TOKEN code fact
                AND names >= 1 recognized state token in its text
    PASS        every state token the claim names is owned by one of the STATE_TOKEN facts it cites
    FAIL        the claim names a state that none of its cited state facts holds
    NOT_EVAL    not eligible - zero eligible claims is NOT_EVALUATED, never a silent PASS

The unit of comparison is the CLAIM together with its full cited state set, not one (claim, fact)
pair at a time. That choice is what keeps this gate out of M8's deferred R3 problem instead of
importing it. A compound claim citing four metrics' states and naming three of them is restating
three facts faithfully and saying nothing about the fourth; judging it once per cited fact would
report the silent fact as a mismatch against tokens that were never about it, which is precisely
the per-fact/per-claim mismatch `H_V2_D4_S_M8_COMPOUND_COVERAGE_AUDIT_V1.md`'s mechanism C names.
Containment asks the question that has an answer without per-metric attribution: did the model
introduce a state that the pipeline does not own?

WHAT THIS GATE THEREFORE DOES NOT DECIDE, stated here rather than left to be discovered. It does
not bind a named state to a particular metric. A claim citing `{STABLE, DECELERATING}` that swaps
which metric is which passes containment. Binding a token to a metric needs per-metric attribution
over prose, which is the same unit-of-comparison change M8's R3 is deferred for, and it is
deliberately not attempted here. Containment is a real invariant - a fabricated or upgraded state
cannot pass it - and it is the whole of what this gate claims.

No synonyms. The existing D4 contract already tells the model to "copy the BARE TOKEN exactly as
the D3 output states it" (`prompt.D3_IMMUTABILITY`), and no contract anywhere in H-V2 licenses a
paraphrase of a state. Case is ignored and the underscore in a two-part token may be written as a
space or a hyphen, because those are renderings of the same token rather than different words; a
prose paraphrase ("picking up", "slowing") is NOT a restatement and makes the claim ineligible
rather than passing or failing it. The mapping is frozen here, before any stored output was read
through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import re

from app.backtest.strategy_h_v2.change_detection import ChangeState
from app.backtest.strategy_h_v2.expectation.code_facts import CodeFact
from app.backtest.strategy_h_v2.expectation.d4_2_contract import MechanicalGateStatus

GATE_ID = "CODE_OWNED_STATE_FIDELITY"

STATE_TOKEN_UNIT = "STATE_TOKEN"
"""`code_facts.build_code_fact_index`'s own unit string for a state fact. Matched on the unit rather
than on the path so a future state fact from another block is covered without editing this file."""

#: `ChangeState.UNKNOWN` is recognized as a FACT value (a metric whose state is genuinely UNKNOWN
#: still gets a code fact) but NOT as a token named in prose. "unknown" is the contract's own
#: honesty marker - `unknown_fields`, the UNKNOWN claim type, "the driver is unknown" - and reading
#: it as a restatement of a code-owned state would turn the discipline the contract asks for into a
#: defect, the same reason `audit_strategy_h_v2_d4_1.NON_PROSE_KEYS` skips `consensus_status`. It is
#: also not a directional claim about a metric, which is the only thing this gate is about.
NOT_NAMED_IN_PROSE: frozenset[str] = frozenset({ChangeState.UNKNOWN.value})

#: Read from `change_detection.ChangeState`, the authoritative code-owned enum, rather than listed
#: here. A state this gate would not recognize is therefore impossible to introduce by adding an
#: enum member and forgetting this file.
RECOGNIZED_STATES: frozenset[str] = frozenset(
    state.value for state in ChangeState if state.value not in NOT_NAMED_IN_PROSE
)

#: One pattern per recognized state. The parts of a two-part token may be joined by an underscore,
#: a space or a hyphen ("INFLECTION_NEGATIVE", "inflection negative", "loss-to-profit"); the
#: separator class is the ONLY latitude, and `\b` on both ends keeps "STABLE" out of "STABLENESS".
#: Longest first so `_named` reports "LOSS_TO_PROFIT" rather than being satisfied by a shorter
#: member that happens to be a substring of another (none is today - checked by test).
_STATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (state, re.compile(r"\b" + r"[\s_-]+".join(re.escape(p) for p in state.split("_")) + r"\b",
                       re.IGNORECASE))
    for state in sorted(RECOGNIZED_STATES, key=len, reverse=True)
)


def named_states(text: str) -> set[str]:
    """Every recognized code-owned state token named in `text`, as its canonical enum value."""
    return {state for state, pattern in _STATE_PATTERNS if pattern.search(text or "")}


def owned_states(facts: Iterable[CodeFact]) -> set[str]:
    """The states the STATE_TOKEN facts in `facts` actually hold. Non-state facts contribute
    nothing - a return or a close has no state to own."""
    return {str(fact.value).upper() for fact in facts if fact.unit == STATE_TOKEN_UNIT}


@dataclass(frozen=True)
class StateFidelityFinding:
    path: str
    named: tuple[str, ...]
    owned: tuple[str, ...]
    unowned: tuple[str, ...]
    """The named states no cited state fact holds. Empty on an eligible claim that passes."""
    text: str

    @property
    def violated(self) -> bool:
        return bool(self.unowned)

    def to_dict(self) -> dict:
        return {"path": self.path, "named": list(self.named), "owned": list(self.owned),
                "unowned": list(self.unowned), "text": self.text}


def claim_state_fidelity(path: str, text: str, facts: Iterable[CodeFact]
                         ) -> StateFidelityFinding | None:
    """One claim's finding, or `None` when the claim is not eligible.

    Ineligible for one of two structural reasons, never because of how the sentence is worded: the
    claim cites no STATE_TOKEN fact (nothing code-owned to be faithful to), or it names no
    recognized state (`fact = DECELERATING, claim does not restate the state` -> NOT_EVALUATED).
    """
    owned = owned_states(facts)
    if not owned:
        return None
    named = named_states(text)
    if not named:
        return None
    return StateFidelityFinding(
        path=path, named=tuple(sorted(named)), owned=tuple(sorted(owned)),
        unowned=tuple(sorted(named - owned)), text=text[:200],
    )


def state_fidelity_status(findings: Iterable[StateFidelityFinding]) -> MechanicalGateStatus:
    """FAIL on any violation, PASS only on a non-empty eligible set with none, and NOT_EVALUATED
    when nothing was eligible - the convention `d4_2_contract.build_gates` already uses, and the
    reason a zero here is never reported as a clean run (D4-H brief §11)."""
    findings = list(findings)
    if any(f.violated for f in findings):
        return MechanicalGateStatus.FAIL
    if not findings:
        return MechanicalGateStatus.NOT_EVALUATED
    return MechanicalGateStatus.PASS


def state_fidelity_report(findings: Iterable[StateFidelityFinding]) -> dict:
    """The gate's own block, shaped so a reader sees the denominator beside the numerator."""
    findings = list(findings)
    violations = [f for f in findings if f.violated]
    return {
        "gate": GATE_ID,
        "status": state_fidelity_status(findings).value,
        "eligible": len(findings),
        "violations": len(violations),
        "eligible_claims": [f.to_dict() for f in findings],
        "violating_claims": [f.to_dict() for f in violations],
    }
