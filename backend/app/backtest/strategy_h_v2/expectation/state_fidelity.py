"""H-V2-D4-H1: `CODE_OWNED_STATE_FIDELITY` - a claim may not assert a categorical state that
contradicts the code-owned state of the metric it is talking about.

Why this gate is not a corner of M8. M8 is "no claim cites a code-owned fact while stating a DIFFERENT
number", and its unit of comparison is a number. A `research_facts.fundamental_changes.<metric>.state`
code fact carries no number - its value is a `change_detection.ChangeState` member - so M8 has nothing
to compare it against, and the live validator has always agreed: `validate._COMPARABLE_UNITS` has no
`STATE_TOKEN` entry, so `check_code_fact_numerics` has never examined a state fact in any run. Before
D4-H the only place the state question was asked at all was the audit layer's
`numeric_roles.fact_is_restated`, through

    return str(value).upper() in text.upper() or not any(c.isdigit() for c in text)

which was over-strict on an unrelated digit and let an outright mis-restatement through when the
sentence had none. D4-H removed that line (R1) and moved the question here. M8 is numeric only and
this gate is categorical only; the STATE_TOKEN branch is not coming back.

## D4-H's V1 contract, and why it did not survive its own first measurement

V1 asked a single question - set containment per claim: is every state token the claim NAMES owned by
one of the STATE_TOKEN facts the claim CITES? It caught the regression it was built for, and on 38
eligible historical claims it returned 6 violations of which `H_V2_D4_H_PRE_TIER_B_INTEGRITY_
HARDENING_V1.md` §H found **0 to be true state defects**, from two mechanisms V1's contract had not
anticipated:

  S1  Naming a state token is not asserting it. `IMPROVING`, `ACCELERATING`, `STABLE`, `INCREASING`
      and `DECREASING` are ordinary English adjectives, and a lowercase rendering has to count as a
      restatement (a claim writing "revenue is accelerating" against a STABLE fact is exactly what
      this gate exists to catch). So V1 could not tell a restatement from a DENIAL: "not
      accelerating", "mixed rather than improving" and "steady rather than improving" were all read as
      positive assertions. 3 of the 8 flagged tokens.

  S2  A claim's citations are not the pipeline's ownership. V1 derived the owned set from
      `claim.evidence_ids`, so a claim restating `eps_diluted = DETERIORATING` correctly, while citing
      revenue's and free cash flow's state facts, was reported as introducing an unowned state. 5 of
      the 8. That made V1 a citation-completeness check wearing a fidelity check's name, and citation
      completeness already belongs to E2's sourcing layer.

## The V2 contract this file implements

Per ASSERTION, not per claim, and in three steps that are each answerable on their own:

    1  POLARITY     is this state token asserted, negated, or rejected/contrasted?
                    only ASSERTED is a claim about what the metric's state IS.
    2  BINDING      which code-owned metric is this assertion about?
                    read from the prose's own metric identifiers; no identifier -> NOT_EVALUATED.
    3  COMPARISON   ASSERTED state vs the candidate's authoritative state FOR THAT METRIC.
                    equal -> PASS, different -> FAIL.

Step 3's authority is `authoritative_state[candidate][metric]`, read from the candidate's own
`fundamental_changes` block - the same source `code_facts.build_code_fact_index` reads. Two things
follow, and both are deliberate:

  PROVENANCE AND AUTHORITY ARE DIFFERENT THINGS. `evidence_ids` are support references; the code-owned
  metric state is the categorical truth. This gate compares against the second and says nothing about
  the first. A claim that states its metric's state correctly passes whether or not it cited that
  metric's state fact, and a claim that cites badly is E2's finding, not this gate's.

  METRIC BINDING IS MANDATORY, NOT A CONVENIENCE. "the candidate owns this token somewhere" is NOT
  sufficient. With `revenue = IMPROVING` and `operating_income = DECELERATING`, the claim "Operating
  income is improving." FAILS, because binding resolves to `operating_income` and IMPROVING is not
  DECELERATING. Widening the authority to the candidate without binding would have turned S2's repair
  into a hole big enough to drive a fabricated state through.

When binding cannot be resolved from the prose's own metric identifiers, the outcome is
NOT_EVALUATED - never a forced match and never a silent PASS. "fundamentals continued improving on the
operating line" names no metric identifier ("the operating line" is prose, not `operating_income`), and
guessing would be the kind of inference this layer has no basis for.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping
import re

from app.backtest.strategy_h_v2.change_detection import ChangeState
from app.backtest.strategy_h_v2.expectation.code_facts import CodeFact
from app.backtest.strategy_h_v2.expectation.d4_2_contract import MechanicalGateStatus

GATE_ID = "CODE_OWNED_STATE_FIDELITY"

STATE_TOKEN_UNIT = "STATE_TOKEN"
"""`code_facts.build_code_fact_index`'s own unit string for a state fact. Matched on the unit rather
than on the path so a future state fact from another block is covered without editing this file."""

STATE_FACT_PATH_PREFIX = "research_facts.fundamental_changes."
STATE_FACT_PATH_SUFFIX = ".state"


# ---------------------------------------------------------------------------------------------
# The state vocabulary
# ---------------------------------------------------------------------------------------------

#: `ChangeState.UNKNOWN` is recognized as a FACT value (a metric whose state is genuinely UNKNOWN
#: still gets a code fact) but NOT as a token named in prose. "unknown" is the contract's own honesty
#: marker - `unknown_fields`, the UNKNOWN claim type, "the driver is unknown" - and reading it as a
#: restatement of a code-owned state would turn the discipline the contract asks for into a defect,
#: the same reason `audit_strategy_h_v2_d4_1.NON_PROSE_KEYS` skips `consensus_status`. It is also not
#: a directional claim about a metric, which is the only thing this gate is about.
NOT_NAMED_IN_PROSE: frozenset[str] = frozenset({ChangeState.UNKNOWN.value})

#: Read from `change_detection.ChangeState`, the authoritative code-owned enum, rather than listed
#: here. A state this gate would not recognize is therefore impossible to introduce by adding an enum
#: member and forgetting this file.
RECOGNIZED_STATES: frozenset[str] = frozenset(
    state.value for state in ChangeState if state.value not in NOT_NAMED_IN_PROSE
)

#: One pattern per recognized state. The parts of a two-part token may be joined by an underscore, a
#: space or a hyphen ("INFLECTION_NEGATIVE", "inflection negative", "loss-to-profit"); the separator
#: class is the ONLY latitude, and `\b` on both ends keeps "STABLE" out of "STABLENESS". A prose
#: paraphrase is NOT a restatement: "steady", "slowing", "picking up" are not matched, and the
#: existing contract is why - `prompt.D3_IMMUTABILITY` tells the model to "copy the BARE TOKEN exactly
#: as the D3 output states it", so a paraphrase is not a state restatement to hold to the enum.
_STATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (state, re.compile(r"\b" + r"[\s_-]+".join(re.escape(p) for p in state.split("_")) + r"\b",
                       re.IGNORECASE))
    for state in sorted(RECOGNIZED_STATES, key=len, reverse=True)
)


def named_states(text: str) -> set[str]:
    """Every recognized code-owned state token named in `text`, as its canonical enum value. Says
    nothing about polarity - `state_occurrences` is the one that reads how the token is used."""
    return {state for state, pattern in _STATE_PATTERNS if pattern.search(text or "")}


# ---------------------------------------------------------------------------------------------
# Step 1 - polarity (S1)
# ---------------------------------------------------------------------------------------------

class Polarity(StrEnum):
    ASSERTED = "ASSERTED"
    """The claim says the metric's state IS this. The only polarity ever compared."""
    NEGATED = "NEGATED"
    """The claim says the metric's state is NOT this ("not accelerating")."""
    REJECTED_OR_CONTRASTED = "REJECTED_OR_CONTRASTED"
    """The claim names this state to set something against it ("stable rather than accelerating")."""


#: What ends a polarity scope. A negation cannot reach past one of these to the next clause, which is
#: what keeps this from becoming a blanket "the sentence contains 'not', ignore every state in it"
#: exemption: in "Revenue is not stable and is accelerating", `and` resets the scope, so STABLE is
#: NEGATED and ACCELERATING is ASSERTED. `and`/`or` reset polarity but do NOT split a binding segment
#: (see `_binding_segment`) - coordination carries a subject forward while it does not carry a
#: negation forward.
_POLARITY_RESET = re.compile(
    r"[.;:,]|\b(?:while|whereas|although|though|alongside|but|so|because|since|however|yet"
    r"|and|or)\b", re.IGNORECASE)

#: A contrast construction: the state after it is what the claim is arguing AGAINST. Checked before
#: the negators because it is the more specific, multi-word reading of the same "this is not the
#: state" intent, and because "rather than" is how every instance in the D4-H corpus was written.
_CONTRAST = re.compile(r"\b(?:rather than|instead of|as opposed to|unlike|not\s+\w+\s+but)\b",
                       re.IGNORECASE)

#: Plain negation. Whole words only, so "no" cannot be found inside "not" and neither can be found
#: inside an ordinary word. Bounded by `_POLARITY_RESET`, so a negation in a neighbouring clause
#: never reaches this token.
_NEGATOR = re.compile(
    r"\b(?:not|no|never|neither|nor|without|nothing|absent|lacks|lacking|fails? to|failed to"
    r"|cannot|n't)\b", re.IGNORECASE)


def _polarity(text: str, start: int) -> Polarity:
    """How the state token beginning at `start` is being used, read from the text between it and the
    nearest preceding polarity reset."""
    resets = [m.end() for m in _POLARITY_RESET.finditer(text, 0, start)]
    window = text[(resets[-1] if resets else 0):start]
    if _CONTRAST.search(window):
        return Polarity.REJECTED_OR_CONTRASTED
    if _NEGATOR.search(window):
        return Polarity.NEGATED
    return Polarity.ASSERTED


# ---------------------------------------------------------------------------------------------
# Step 2 - metric binding (S2)
# ---------------------------------------------------------------------------------------------

#: How a code-owned metric identifier is written in prose. The KEYS are the authoritative metric
#: names - the keys of the candidate's own `fundamental_changes` block, produced by
#: `pipeline._candidate_result` from `change_detection.ChangeEvidence.metric` - and the values are
#: renderings of that same identifier, not descriptions of it.
#:
#: The line this table draws is deliberate and it is where D4-H §K said the S2 repair had to stop.
#: "EPS" IS the identifier `eps_diluted` written the way a filing writes it. "the operating line",
#: "top-line", "fundamentals", "ARR growth" are prose ABOUT a metric, and mapping them would be the
#: forced matching the H1 brief §8 forbids: "the operating line" could be operating income or
#: operating margin, and "fundamentals" is not a metric at all. An assertion whose segment names none
#: of these is NOT_EVALUATED, which is the honest answer and not a PASS.
METRIC_PROSE_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "revenues"),
    "operating_income": ("operating income",),
    "eps_diluted": ("eps", "diluted eps", "earnings per share"),
    "free_cash_flow": ("free cash flow", "fcf"),
    "operating_margin": ("operating margin",),
    "shares_outstanding": ("shares outstanding", "share count", "diluted shares"),
}

#: The metric IDENTIFIER itself is always an alias, derived from the key rather than listed, so a
#: metric added to `METRIC_PROSE_ALIASES` can never be unrecognized in the one form the H1 brief §8
#: ranks highest ("structured metric id > code fact linkage > existing deterministic mapping").
#:
#: This was missing from the first H1 implementation, and the omission is worth recording because of
#: how it was found rather than what it was. The prose aliases alone left `eps_diluted` unrecognizable
#: when a claim wrote the identifier verbatim - `\beps\b` cannot match inside "eps_diluted", because
#: the underscore is a word character - so five claims of the frozen corpus that state
#: "The code-owned fundamental change state for eps_diluted is DETERIORATING" came back
#: NO_METRIC_NAMED. They were found by auditing the NOT_EVALUATED list rather than the violations,
#: which is the half of a gate's output that is easy not to read: a gate that quietly declines to
#: evaluate the clearest cases it has is not passing them, and 0 violations over a denominator that
#: excludes them is a weaker result than it looks.
_METRIC_IDENTIFIER_ALIASES: dict[str, tuple[str, ...]] = {
    metric: tuple(dict.fromkeys(aliases + (metric.replace("_", " "),)))
    for metric, aliases in METRIC_PROSE_ALIASES.items()
}

#: Longest alias first, so "diluted eps" is read as `eps_diluted` rather than being satisfied by the
#: shorter "eps" inside it, and so "free cash flow" is never partially matched.
_METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (metric, re.compile(r"\b" + r"[\s_-]+".join(re.escape(p) for p in alias.split()) + r"\b",
                        re.IGNORECASE))
    for metric, alias in sorted(
        ((m, a) for m, aliases in _METRIC_IDENTIFIER_ALIASES.items() for a in aliases),
        key=lambda pair: len(pair[1]), reverse=True)
)

#: A clause boundary for BINDING. Narrower than `_POLARITY_RESET` on purpose: `and`/`or` are absent,
#: because "EPS is deteriorating and free cash flow is at a negative inflection" needs both halves
#: readable while "Revenue is not stable and is accelerating" needs the subject to carry across the
#: `and`. Coordination is handled by `_SUB_SPLIT` below, only when a segment turns out ambiguous.
_BINDING_BOUNDARY = re.compile(
    r"[.;:,]|\b(?:while|whereas|although|though|alongside|but|so|because|since|however)\b",
    re.IGNORECASE)
_SUB_SPLIT = re.compile(r"\b(?:and|or)\b", re.IGNORECASE)


def _segment(text: str, start: int, boundary: re.Pattern[str]) -> tuple[int, int]:
    before = [m.end() for m in boundary.finditer(text, 0, start)]
    after = boundary.search(text, start)
    return (before[-1] if before else 0), (after.start() if after else len(text))


def metrics_named(text: str) -> set[str]:
    """Every authoritative metric identifier written in `text`."""
    return {metric for metric, pattern in _METRIC_PATTERNS if pattern.search(text or "")}


class BindingStatus(StrEnum):
    BOUND = "BOUND"
    NO_METRIC_NAMED = "NO_METRIC_NAMED"
    """The assertion's clause names no code-owned metric identifier."""
    AMBIGUOUS = "AMBIGUOUS"
    """Its clause names more than one, and coordination does not separate them."""


def _bind_metric(text: str, start: int, end: int) -> tuple[str | None, BindingStatus, str]:
    """Which code-owned metric the state token at `[start, end)` is about.

    Two stages. The token's clause is read first, and if exactly one metric identifier appears in it
    the binding is that metric - whether the metric precedes the state ("EPS is deteriorating") or
    follows it ("DECELERATING operating income"), because a clause is about one metric either way.
    If the clause names two or more, coordination is the only thing allowed to separate them: the
    clause is sub-split on `and`/`or` and the piece holding the token is re-read. Still more than one,
    or none at all, and the answer is that there is no answer.
    """
    lo, hi = _segment(text, start, _BINDING_BOUNDARY)
    segment = text[lo:hi]
    found = metrics_named(segment)
    if len(found) == 1:
        return found.pop(), BindingStatus.BOUND, segment.strip()
    if len(found) > 1:
        sub_lo, sub_hi = _segment(text, start, _SUB_SPLIT)
        sub_lo, sub_hi = max(sub_lo, lo), min(sub_hi, hi)
        piece = text[sub_lo:sub_hi]
        sub_found = metrics_named(piece)
        if len(sub_found) == 1:
            return sub_found.pop(), BindingStatus.BOUND, piece.strip()
        return None, BindingStatus.AMBIGUOUS, segment.strip()
    return None, BindingStatus.NO_METRIC_NAMED, segment.strip()


# ---------------------------------------------------------------------------------------------
# Step 3 - comparison against the candidate's authoritative state
# ---------------------------------------------------------------------------------------------

class NotEvaluatedReason(StrEnum):
    POLARITY_NOT_ASSERTED = "POLARITY_NOT_ASSERTED"
    NO_METRIC_NAMED = "NO_METRIC_NAMED"
    AMBIGUOUS_METRIC = "AMBIGUOUS_METRIC"
    METRIC_NOT_CODE_OWNED = "METRIC_NOT_CODE_OWNED"
    AUTHORITY_UNKNOWN = "AUTHORITY_UNKNOWN"
    """The candidate's own state for that metric is UNKNOWN. UNKNOWN is the ABSENCE of a code-owned
    categorical truth, not a competing one, so there is nothing for an assertion to contradict. A
    directional claim over an UNKNOWN metric may still be a sourcing question, which is E2's."""


@dataclass(frozen=True)
class StateAssertion:
    path: str
    state: str
    polarity: Polarity
    span: tuple[int, int]
    metric: str | None
    binding: BindingStatus
    clause: str
    authoritative_state: str | None
    status: MechanicalGateStatus
    reason: NotEvaluatedReason | None

    @property
    def violated(self) -> bool:
        return self.status == MechanicalGateStatus.FAIL

    @property
    def evaluated(self) -> bool:
        return self.status in (MechanicalGateStatus.PASS, MechanicalGateStatus.FAIL)

    def to_dict(self) -> dict:
        return {"path": self.path, "state": self.state, "polarity": self.polarity.value,
                "span": list(self.span), "metric": self.metric, "binding": self.binding.value,
                "clause": self.clause[:200], "authoritative_state": self.authoritative_state,
                "status": self.status.value,
                "reason": self.reason.value if self.reason is not None else None}


def state_occurrences(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Every recognized state token occurrence in `text`, in document order, as `(state, span)`. Per
    OCCURRENCE rather than per distinct state, because the same token can be asserted in one clause
    and denied in another."""
    found = [(state, m.span())
             for state, pattern in _STATE_PATTERNS for m in pattern.finditer(text or "")]
    return sorted(found, key=lambda pair: pair[1])


def state_assertions(path: str, text: str,
                     authority: Mapping[str, str] | None) -> list[StateAssertion]:
    """Every state token occurrence in `text`, carried through polarity, binding and comparison.

    `authority` is `{metric: state}` for the candidate - its own `fundamental_changes` block. `None`
    or empty means no code-owned state namespace was available, and every assertion then comes back
    NOT_EVALUATED rather than being compared against nothing.
    """
    text = text or ""
    authority = dict(authority or {})
    assertions: list[StateAssertion] = []
    for state, (start, end) in state_occurrences(text):
        polarity = _polarity(text, start)
        metric, binding, clause = _bind_metric(text, start, end)

        status = MechanicalGateStatus.NOT_EVALUATED
        reason: NotEvaluatedReason | None = None
        authoritative: str | None = None

        if polarity is not Polarity.ASSERTED:
            reason = NotEvaluatedReason.POLARITY_NOT_ASSERTED
        elif binding is BindingStatus.NO_METRIC_NAMED:
            reason = NotEvaluatedReason.NO_METRIC_NAMED
        elif binding is BindingStatus.AMBIGUOUS:
            reason = NotEvaluatedReason.AMBIGUOUS_METRIC
        elif metric not in authority:
            reason = NotEvaluatedReason.METRIC_NOT_CODE_OWNED
        else:
            authoritative = str(authority[metric]).upper()
            if authoritative in NOT_NAMED_IN_PROSE:
                reason = NotEvaluatedReason.AUTHORITY_UNKNOWN
            elif authoritative == state:
                status = MechanicalGateStatus.PASS
            else:
                status = MechanicalGateStatus.FAIL

        assertions.append(StateAssertion(
            path=path, state=state, polarity=polarity, span=(start, end), metric=metric,
            binding=binding, clause=clause, authoritative_state=authoritative, status=status,
            reason=reason))
    return assertions


# ---------------------------------------------------------------------------------------------
# The candidate's authoritative state namespace, and the state-fact inventory
# ---------------------------------------------------------------------------------------------

def authoritative_states(fundamental_changes: Mapping[str, object] | None) -> dict[str, str]:
    """`{metric: state}` from a candidate's own `fundamental_changes` block - the same source
    `code_facts.build_code_fact_index` reads its STATE_TOKEN facts from, so the two cannot disagree
    about what the pipeline owns. The metric namespace is whatever that block's keys are; no metric
    list is restated here."""
    return {metric: str(block["state"]).upper()
            for metric, block in (fundamental_changes or {}).items()
            if isinstance(block, dict) and block.get("state") is not None}


def state_fact_inventory(candidate_id: str, facts: Iterable[CodeFact]) -> list[dict]:
    """H1 brief §2: every code-owned state fact as `(candidate_id, metric, state, fact_id)`, so the
    authority side of the comparison can be read without re-deriving it."""
    inventory = []
    for fact in facts:
        if fact.unit != STATE_TOKEN_UNIT:
            continue
        metric = fact.path
        if metric.startswith(STATE_FACT_PATH_PREFIX) and metric.endswith(STATE_FACT_PATH_SUFFIX):
            metric = metric[len(STATE_FACT_PATH_PREFIX):-len(STATE_FACT_PATH_SUFFIX)]
        inventory.append({"candidate_id": candidate_id, "metric": metric,
                          "state": str(fact.value).upper(), "fact_id": fact.evidence_id})
    return sorted(inventory, key=lambda row: row["metric"])


# ---------------------------------------------------------------------------------------------
# Claim-level finding and the gate roll-up
# ---------------------------------------------------------------------------------------------

def owned_states(facts: Iterable[CodeFact]) -> set[str]:
    """The states the STATE_TOKEN facts in `facts` hold. Retained because D4-H's claim-level
    ELIGIBILITY denominator is defined in terms of a claim citing at least one state fact, and that
    denominator has to stay computable exactly as D4-H defined it for D4-H's frozen prediction to be
    measurable against the same 38 claims. It is no longer the comparison authority - that is
    `authoritative_states`, per H1 §5/§6."""
    return {str(fact.value).upper() for fact in facts if fact.unit == STATE_TOKEN_UNIT}


@dataclass(frozen=True)
class StateFidelityFinding:
    path: str
    assertions: tuple[StateAssertion, ...]
    cites_state_fact: bool
    """D4-H's claim-level eligibility half: did this claim cite at least one STATE_TOKEN fact."""
    text: str

    @property
    def d4_h_eligible(self) -> bool:
        """D4-H V1's claim-level eligibility, unchanged: cites >= 1 STATE_TOKEN fact AND names >= 1
        recognized state token. Kept verbatim so the 38-claim denominator D4-H's frozen prediction
        was made about is still the denominator it is measured on."""
        return self.cites_state_fact and bool(self.assertions)

    @property
    def violations(self) -> tuple[StateAssertion, ...]:
        return tuple(a for a in self.assertions if a.violated)

    @property
    def violated(self) -> bool:
        return bool(self.violations)

    def to_dict(self) -> dict:
        return {"path": self.path, "cites_state_fact": self.cites_state_fact,
                "d4_h_eligible": self.d4_h_eligible,
                "assertions": [a.to_dict() for a in self.assertions],
                "text": self.text}


def claim_state_fidelity(path: str, text: str, facts: Iterable[CodeFact],
                         authority: Mapping[str, str] | None) -> StateFidelityFinding | None:
    """One claim's finding, or `None` when it names no recognized state token at all.

    Note what is NOT a precondition any more: citing a state fact. H1 §6 separates provenance from
    authority, so a claim asserting a metric's state is compared against that metric's code-owned
    state whether or not it cited it. `cites_state_fact` is still recorded, because D4-H's frozen
    denominator is defined in terms of it.
    """
    facts = list(facts)
    assertions = state_assertions(path, text, authority)
    if not assertions:
        return None
    return StateFidelityFinding(path=path, assertions=tuple(assertions),
                                cites_state_fact=bool(owned_states(facts)), text=text[:200])


def state_fidelity_status(findings: Iterable[StateFidelityFinding]) -> MechanicalGateStatus:
    """FAIL on any violated assertion, PASS only when at least one assertion was actually compared
    and none failed, and NOT_EVALUATED when nothing was comparable - the convention
    `d4_2_contract.build_gates` already uses, and the reason a zero here is never reported as a clean
    run (D4-H brief §11)."""
    assertions = [a for f in findings for a in f.assertions]
    if any(a.violated for a in assertions):
        return MechanicalGateStatus.FAIL
    if not any(a.evaluated for a in assertions):
        return MechanicalGateStatus.NOT_EVALUATED
    return MechanicalGateStatus.PASS


def state_fidelity_report(findings: Iterable[StateFidelityFinding]) -> dict:
    """The gate's own block. Two denominators, both stated, because they answer different questions:
    `assertions_evaluated` is H1's own unit of adjudication, and `d4_h_eligible_claims` is D4-H V1's
    claim-level denominator, kept so its frozen prediction (38 eligible, 0 violations) is measured on
    the same 38 claims it was made about rather than on a denominator this step redefined."""
    findings = list(findings)
    assertions = [a for f in findings for a in f.assertions]
    d4_h_eligible = [f for f in findings if f.d4_h_eligible]
    by_reason: dict[str, int] = {}
    for a in assertions:
        if a.reason is not None:
            by_reason[a.reason.value] = by_reason.get(a.reason.value, 0) + 1
    return {
        "gate": GATE_ID,
        "status": state_fidelity_status(findings).value,
        "claims_naming_a_state": len(findings),
        "assertions_total": len(assertions),
        "assertions_evaluated": sum(1 for a in assertions if a.evaluated),
        "assertions_passed": sum(1 for a in assertions
                                 if a.status == MechanicalGateStatus.PASS),
        "assertions_failed": sum(1 for a in assertions if a.violated),
        "not_evaluated_by_reason": dict(sorted(by_reason.items())),
        "polarity_counts": {
            p.value: sum(1 for a in assertions if a.polarity is p) for p in Polarity
        },
        "d4_h_eligible_claims": len(d4_h_eligible),
        "d4_h_violating_claims": sum(1 for f in d4_h_eligible if f.violated),
        "violating_claims": [f.to_dict() for f in findings if f.violated],
        "claims": [f.to_dict() for f in findings],
    }
