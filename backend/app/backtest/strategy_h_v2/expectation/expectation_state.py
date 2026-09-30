"""D4-S. The market/consensus expectation KNOWLEDGE STATE, owned by code, read by everyone.

Why this module exists. Before D4-S, "does this pipeline know what the market expects?" was answered
in three places at once - the code-owned evidence bundle's per-block `status`, two statuses the model
copied into its own output, and a sentence-level free-text classifier - and the free-text layer was
the one that decided. Two consecutive live runs (D4.3A, Final Tier A V3) had their first-attempt
validity set by that classifier rejecting a sentence which *denied* having consensus knowledge:

    "...the evidence gives no basis for saying market expectations lag the evidenced progress..."
    "...none of these shows whether the market's expectation lags or leads the evidenced progress."

Neither fabricates a consensus. Both were routed to ASSERTED because they pair `market` with an
expectation noun and carry a negation the `_ABSENCE` list does not happen to name. The defect is
structural, not lexical: a prose scan was being asked to decide a question that code already knows
the answer to, and every repair attempt was a guess at which phrasing the list would tolerate.

D4-S's rule. The AVAILABILITY of expectation knowledge is decided HERE, from the code-owned bundle,
and nowhere else. `ExpectationKnowledgeStateV1` is that answer. The prose classifier is kept - it is
not relaxed and not deleted - but demoted from truth decider to LEAKAGE GUARD: given that code has
already established no consensus source exists, its only remaining job is to catch an AFFIRMATIVE
attribution that slipped into the text anyway.

How absence language is admitted WITHOUT a phrase list. Enumerating "gives no basis for saying",
"none of these shows whether", "cannot establish", ... is what D4.1, D4.3R and D4.3R2 each tried,
and each closure opened a new false-positive surface. Instead this module tests CLAIM STRUCTURE: an
expectation attribution is non-affirmative when it sits under a NEGATED EPISTEMIC PREDICATE - a verb
or noun of knowing/showing/establishing that is itself negated or hedged, with nothing but function
words between the negator and the epistemic head. That single structural fact covers every sentence
above, and, critically, does NOT cover the one carve-out that would gut the rule:

    "Consensus does not expect growth."        -> still ASSERTED

because `expect` is not an epistemic predicate. The negation there attacks the expectation's CONTENT,
not the pipeline's knowledge of it, which is exactly the distinction `consensus_language`'s own
docstring warns a blanket negation carve-out would lose. A sentence can be as negative as it likes
about what analysts think and still be a fabrication; it is only safe when what is being denied is
that the evidence establishes anything.

What D4-S deliberately does NOT touch: the Expectation Gap enum, rule C1 (a POSITIVE gap needs
non-price expectation evidence), rule C4 (the consensus-absence confidence ceiling), the confidence
enum, priced-in semantics, or `consensus_language.classify_sentence` itself, which is byte-identical
to its R2 form so every phrase-by-phrase test frozen against it still holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from app.backtest.strategy_h_v2.expectation.consensus_language import (
    AttributionTrigger,
    ConsensusFinding,
    ConsensusVerdict,
    asserted_consensus_findings,
    attribution_triggers,
    classify_sentence,
    split_sentences,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability

EXPECTATION_STATE_CONTRACT_VERSION = "h_v2_d4_s_expectation_state_v1"


class ExpectationKnowledgeStatus(StrEnum):
    """How much of the market-expectation side this pipeline can actually see.

    Named after KNOWLEDGE, not after a gap: this enum says whether an expectation may be stated at
    all, and never which way one points. `ExpectationGapState` is untouched by D4-S.
    """

    UNKNOWN = "UNKNOWN"
    """No expectation source is readable. Every candidate in the current data layer."""
    PARTIAL = "PARTIAL"
    """Exactly one of the two expectation blocks is readable."""
    ESTABLISHED = "ESTABLISHED"
    """Both expectation blocks are readable."""


class ExpectationBasis(StrEnum):
    """Which code-owned block, if any, licenses a market-expectation statement."""

    CONSENSUS = "CONSENSUS"
    ESTIMATE_REVISIONS = "ESTIMATE_REVISIONS"


#: A block licenses an expectation claim only in these two states. `NOT_FOUND_FOR_CANDIDATE` does
#: NOT license one: "we can read this source type and it is empty for this issuer" is still an
#: absence, and treating it as a basis is how an absence becomes evidence.
_INFORMATIVE = (EvidenceAvailability.AVAILABLE, EvidenceAvailability.PARTIAL)


@dataclass(frozen=True)
class ExpectationKnowledgeStateV1:
    """The single authoritative answer to "may a market expectation be stated, and on what basis?".

    Built by `derive_expectation_knowledge_state` from the code-owned bundle. The model never
    supplies it, never edits it, and cannot contradict it without the validator saying so - the same
    ownership rule `applied_contract_rules` and `confidence_ceiling` already follow.
    """

    status: ExpectationKnowledgeStatus
    basis: tuple[str, ...]
    consensus_status: str
    estimate_revisions_status: str
    market_expectation_claim_allowed: bool
    source_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    contract_version: str = EXPECTATION_STATE_CONTRACT_VERSION

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "basis": list(self.basis),
            "consensus_status": self.consensus_status,
            "estimate_revisions_status": self.estimate_revisions_status,
            "market_expectation_claim_allowed": self.market_expectation_claim_allowed,
            "source_ids": list(self.source_ids),
            "evidence_ids": list(self.evidence_ids),
            "limitations": list(self.limitations),
            "contract_version": self.contract_version,
        }


def derive_expectation_knowledge_state(
    bundle: ExpectationEvidenceBundleV1,
) -> ExpectationKnowledgeStateV1:
    """The authoritative state, computed from the bundle alone.

    `market_expectation_claim_allowed` is the field every other layer keys off: it is True only when
    at least one expectation block is readable, so in the current data layer (`consensus` and
    `estimate_revisions` are `SOURCE_NOT_AVAILABLE` for all 2,010 candidates) it is False for every
    issuer. That is not a conservative default - it is the measured fact, restated where code can
    enforce it instead of asking a prose scan to infer it.
    """
    blocks = (
        (ExpectationBasis.CONSENSUS, bundle.consensus),
        (ExpectationBasis.ESTIMATE_REVISIONS, bundle.estimate_revisions),
    )
    basis: list[str] = []
    source_ids: list[str] = []
    evidence_ids: list[str] = []
    limitations: list[str] = []
    for name, block in blocks:
        if block.status in _INFORMATIVE:
            basis.append(name.value)
            for excerpt in block.excerpts:
                source_ids.append(excerpt.source_id)
                evidence_ids.append(excerpt.evidence_id)
        else:
            limitations.append(
                f"{name.value} expectation evidence is {block.status.value}: no market-expectation "
                f"statement may rest on it"
            )

    if len(basis) == 2:
        status = ExpectationKnowledgeStatus.ESTABLISHED
    elif basis:
        status = ExpectationKnowledgeStatus.PARTIAL
    else:
        status = ExpectationKnowledgeStatus.UNKNOWN

    return ExpectationKnowledgeStateV1(
        status=status,
        basis=tuple(basis),
        consensus_status=bundle.consensus.status.value,
        estimate_revisions_status=bundle.estimate_revisions.status.value,
        market_expectation_claim_allowed=bool(basis),
        source_ids=tuple(dict.fromkeys(source_ids)),
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        limitations=tuple(limitations),
    )


# -------------------------------------------------------------------------------------------------
# The leakage guard: affirmative attribution versus a denial of knowledge
# -------------------------------------------------------------------------------------------------
#
# Everything below runs ONLY on sentences `consensus_language` has already routed to ASSERTED, and
# can only ever REMOVE a finding. It cannot introduce a rejection the frozen classifier did not
# already make, which is what "guard, not truth decider" has to mean in code rather than in prose.

#: The authoritative statuses themselves, quoted verbatim in prose. `SOURCE_NOT_AVAILABLE` names its
#: own absence, but its underscores defeat `\bnot\b` and `\bavailable\b`, so a token-level rule
#: never sees the negation inside it. Generated FROM the enums rather than written out, so a member
#: added or renamed later cannot leave this rule reading a stale spelling - and it is not a phrase
#: list by any reading: these are the structured state's own values, and §5's whole point is that
#: restating the authoritative status is always allowed however it is worded.
_STATUS_TOKENS: tuple[str, ...] = tuple(sorted(
    {member.value for member in EvidenceAvailability if member not in _INFORMATIVE}
    | {ExpectationKnowledgeStatus.UNKNOWN.value},
    key=len, reverse=True,
))
_STATUS_TOKEN_PATTERN = "|".join(re.escape(token) for token in _STATUS_TOKENS)

#: Predicates of knowing, showing and establishing - the frame an honest absence statement puts the
#: expectation INSIDE ("the evidence does not establish whether the market expects ..."). Chosen so
#: that no member is also an `_EXPECTATION_VERB`: if `expect`, `estimate` or `forecast` appeared
#: here, "consensus does not expect growth" would become admissible and the rule would be hollow.
_EPISTEMIC_HEAD = re.compile(
    r"\b(establish(?:es|ed|ing|ment)?|show(?:s|ed|n|ing)?|say(?:s|ing)?|said|"
    r"state(?:s|d|ment|ments)?|determin(?:e|es|ed|ing|ation)|indicat(?:e|es|ed|ing|ion)|"
    r"demonstrat(?:e|es|ed|ing|ion)?|prove(?:s|d|n)?|reveal(?:s|ed|ing)?|tell(?:s|ing)?|told|"
    r"know(?:s|n|ing|ledge)?|basis|evidence|evidenced|clear|unclear|conclud(?:e|es|ed|ing)|"
    r"conclusion|support(?:s|ed|ing)?|separat(?:e|es|ed|ing)|disentangl(?:e|es|ed|ing)|"
    r"assess(?:es|ed|ing|ment)?|verif(?:y|ies|ied|ying)|confirm(?:s|ed|ing|ation)?|"
    r"distinguish(?:es|ed|ing)?|observ(?:e|es|ed|ing|ation)|identif(?:y|ies|ied|ying)|"
    r"ascertain(?:s|ed|ing)?|corroborat(?:e|es|ed|ing)|substantiat(?:e|es|ed|ing)|"
    r"quantif(?:y|ies|ied|ying)|measur(?:e|es|ed|ing|ement)|read(?:s|ing)?|infer(?:s|red|ring)?|"
    r"judg(?:e|es|ed|ing|ement|ment)|"
    # Words that ARE a negated epistemic predicate in a single token, so they need no separate
    # negator beside them. Each also appears in `_NEGATOR`, which is what makes the overlapping
    # match in `_governing_frames` fire.
    r"silent|unclear|unknown|unavailable|absent|"
    r"available|attribut(?:e|es|ed|ing|ion))\b"
    # The authoritative statuses, quoted verbatim. Outside the `\b`-delimited group because an
    # underscore is a word character: `\bSOURCE_NOT_AVAILABLE\b` would need boundaries the token's
    # own underscores do not provide.
    r"|" + _STATUS_TOKEN_PATTERN,
    re.IGNORECASE,
)

#: What negates or hedges that predicate. Generic English negation and epistemic hedging only -
#: nothing here names consensus, the market, or any expectation vocabulary, so this list does not
#: grow when a model phrases an absence a new way.
_NEGATOR = re.compile(
    r"(\bno\b|\bnot\b|n't\b|\bnone\b|\bneither\b|\bnor\b|\bnever\b|\bwithout\b|\bcannot\b|"
    r"\bcan\s*not\b|\bunable\b|\binsufficient\b|\bunclear\b|\bunknown\b|\bunavailable\b|"
    r"\black(?:s|ing|ed)?\b|\babsent\b|\bfail(?:s|ed|ing)?\b|\bimpossible\b|\bwhether\b|"
    r"\bcould\s+not\b|\bdoes\s+not\b|\bdo\s+not\b|\bis\s+not\b|\bare\s+not\b|\btoo\s+thin\b|"
    r"\bsilent\b|\bnothing\b|" + _STATUS_TOKEN_PATTERN + r")",
    re.IGNORECASE,
)

#: Words allowed to sit between the negator and the epistemic head without breaking the link. This
#: is what makes the test structural rather than a distance heuristic: "gives no basis for saying"
#: and "cannot be established" link, while "Without consensus data, it is clear ..." does not,
#: because `consensus` and `data` are content words that break the negator's scope.
_FUNCTION_WORDS = frozenset("""
a an the be is are was were been being am to for of that this these those it its their there here
any some such by from in on at as with within into over under about across alone itself themselves
yet still clearly readily reliably independently directly necessarily actually simply merely
really even also only just how why what which who whom whose whether if then than so and or but
something anything nothing someone anyone capable able possible position positioned
does do did done can could would will shall may might must has have had having more most less least
much many one either both each other others further much own way ways enough
""".split())

#: Clause boundaries INSIDE a sentence. A sentence-scoped carve-out would let one honest clause
#: license a fabrication in the next ("It is unclear whether the market expects X, but consensus
#: expects 22%"), so the affirmative test is re-run per clause. `consensus_language`'s own sentence
#: splitter is left untouched; this is an additional, narrower scope used only by the guard.
_CLAUSE_SPLIT = re.compile(
    r",\s+(?:but|however|yet|while|whereas|although|though|and|so|therefore|thus|because|since)\s+"
    r"|\s+(?:but|however|yet)\s+",
    re.IGNORECASE,
)


def _governing_frames(clause: str) -> list[tuple[int, int]]:
    """Spans of every NEGATED EPISTEMIC FRAME in `clause`, as (start, end).

    A frame is a negator/hedge and an epistemic head that it governs - governs meaning the two are
    adjacent up to function words, in either order, so "does not establish" and "cannot be
    established" both link while a negator separated from the head by content words does not. An
    overlapping match ("unclear", "silent") is its own frame: those words ARE a negated epistemic
    predicate in one token.
    """
    negators = list(_NEGATOR.finditer(clause))
    heads = list(_EPISTEMIC_HEAD.finditer(clause))
    if not negators or not heads:
        return []
    frames: list[tuple[int, int]] = []
    for negator in negators:
        for head in heads:
            first, second = sorted((negator, head), key=lambda m: m.start())
            if first.end() > second.start():
                frames.append((first.start(), max(first.end(), second.end())))
                continue
            words = re.findall(r"[A-Za-z']+", clause[first.end():second.start()])
            if all(word.lower() in _FUNCTION_WORDS for word in words):
                frames.append((first.start(), second.end()))

        # An indefinite pronoun in SUBJECT position negates the clause's whole predicate, and that
        # predicate can sit past a modifier of the subject: "Nothing IN THE POOL is capable of
        # telling us where the market's expectation sits." A clause has one predicate, so an
        # epistemic head anywhere after such a subject IS that predicate. Restricted to the four
        # words that can actually BE a subject - notably not `without`, which is a preposition:
        # "Without consensus data, it is clear the market expects 20%" must keep asserting.
        if (negator.group(0).lower() in _INDEFINITE_SUBJECTS
                and _only_function_words(clause[:negator.start()])):
            for head in heads:
                if head.start() >= negator.end():
                    frames.append((negator.start(), head.end()))
                    break
    return frames


#: Nouns that NAME the thing whose availability is being stated, and so belong to the subject noun
#: phrase of an availability sentence rather than sitting between a subject and its predicate:
#: "Consensus and estimate REVISIONS are SOURCE_NOT_AVAILABLE". Used only to widen the SUBJECT
#: position's adjacency test, so it can never cause a rejection - only admit a denial that a strict
#: function-word gap would have read as an affirmation.
_AVAILABILITY_SUBJECT_NOUNS = frozenset("""
revision revisions expectation expectations estimate estimates forecast forecasts projection
projections consensus coverage source sources provider providers feed feeds data dataset evidence
status statuses input inputs information history figures numbers view views signal signals
""".split())


#: Negators that can head a clause as an indefinite-pronoun subject. `no` is included because
#: "No evidence in the pool shows ..." has exactly the same shape as "Nothing in the pool shows ...".
_INDEFINITE_SUBJECTS = frozenset({"nothing", "none", "neither", "no"})


def _only_function_words(text: str, *, extra: frozenset[str] = frozenset()) -> bool:
    words = re.findall(r"[A-Za-z']+", text)
    return all(word.lower() in _FUNCTION_WORDS or word.lower() in extra for word in words)


#: A wh-complementizer, which turns the attribution into an embedded question rather than a
#: statement: "what the market expects ... cannot be observed" asserts nothing about what the market
#: expects. Needed as its own position because the embedded question's own modifiers ("about Cloud
#: growth, backlog conversion, capex") sit between the attribution and the predicate, so the SUBJECT
#: position's function-word adjacency cannot reach across them.
_WH_COMPLEMENTIZER = re.compile(r"\b(what|whether|how|why|which|if|the\s+extent\s+to\s+which)\b",
                                re.IGNORECASE)


def _is_denied_attribution(clause: str, trigger: AttributionTrigger) -> bool:
    """Is this one attribution being denied, rather than affirmed?

    Four structural positions count, and nothing else. Each is about WHAT the negation attacks - the
    pipeline's knowledge, or the expectation's content - because that is the only distinction that
    matters and the one a phrase list cannot make.

      COMPLEMENT - the frame comes first and the attribution is what it denies knowing.
          "none of these shows whether the market's expectation lags ..."
      SUBJECT    - the attribution comes first and the frame is predicated of it, separated only by
                   function words.
          "the market's expectation cannot be established from this evidence"
      WH-EMBEDDED - the attribution sits inside an embedded question that a frame is predicated of.
          "what the market expects about Cloud growth ... cannot be observed directly"
      EXISTENCE  - a negator governs the attribution's own referent phrase, denying that the source
                   is there at all. This is the structured state restated in prose, which §5 says
                   must always be allowed however it is worded.
          "No analyst-consensus or estimate-revision source is connected"

    Everything else affirms. In particular a frame that merely TRAILS the attribution across content
    words does not rescue it: "The market expects 20% despite management being silent" still asserts,
    because the negation is about management, not about what the evidence establishes.

    One limitation, stated rather than hidden. The EXISTENCE position admits "No analysts expect
    growth", which is a statement about content and arguably should not pass. It is not a D4-S
    regression - `consensus_language._ABSENCE` already carries `no analysts?` and `no consensus`
    verbatim, so the frozen classifier routes that sentence to ABSENCE today and D4-S neither widens
    nor narrows it. Closing it means deciding whether "no X expects Y" is ever legitimate output,
    which is a contract question for a later step, not a detector tweak for this one.
    """
    frames = _governing_frames(clause)
    negators = list(_NEGATOR.finditer(clause))

    for negator in negators:
        if negator.end() <= trigger.start and _only_function_words(
                clause[negator.end():trigger.start]):
            return True

    for start, end in frames:
        if start < trigger.start:
            return True
        if trigger.end <= start and _only_function_words(
                clause[trigger.end:start], extra=_AVAILABILITY_SUBJECT_NOUNS):
            return True

    if frames:
        for wh in _WH_COMPLEMENTIZER.finditer(clause):
            if wh.end() <= trigger.start and _only_function_words(
                    clause[wh.end():trigger.start]):
                return True
    return False


def is_affirmative_expectation_sentence(sentence: str) -> bool:
    """Does `sentence` AFFIRM a market/consensus expectation, rather than deny knowing one?

    Clause-scoped on purpose. A sentence-scoped test would let one honest clause license a
    fabrication beside it - "It is unclear whether the market expects X, but consensus expects 22%"
    has to reject on its second clause.
    """
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c.strip()] or [sentence]
    for clause in clauses:
        for trigger in attribution_triggers(clause):
            if not _is_denied_attribution(clause, trigger):
                return True
    return False


def affirmative_expectation_findings(
    text: str, *, state: ExpectationKnowledgeStateV1,
) -> list[ConsensusFinding]:
    """The LEAKAGE GUARD. Every sentence in `text` that AFFIRMS an expectation the state forbids.

    Two structural filters, in order, and this is the whole of D4-S's prose layer:

      1. `state.market_expectation_claim_allowed` - if code says an expectation source IS readable,
         stating an expectation is legitimate and this guard returns nothing at all. The prose layer
         no longer holds an opinion on availability; that question moved to the structured state.
      2. `is_affirmative_expectation_sentence` - polarity and claim structure, per clause.

    Note what is NOT consulted: `_ABSENCE`. The frozen classifier's absence PHRASE LIST decides
    nothing here. That list is why two live runs rejected honest sentences (a negation it did not
    happen to carry) and why one fabrication slipped past as ABSENCE ("Without consensus data, it is
    clear the market expects 20%" contains "without ... consensus"). Both failures are the same
    mistake - deciding polarity by phrase membership - and D4-S replaces the mechanism rather than
    lengthening the list. `classify_sentence` keeps that list and its verdicts, byte-identical, for
    every caller and test frozen against it.
    """
    if state.market_expectation_claim_allowed:
        return []
    findings: list[ConsensusFinding] = []
    for sentence in split_sentences(text):
        triggers = attribution_triggers(sentence)
        if not triggers or not is_affirmative_expectation_sentence(sentence):
            continue
        findings.append(
            ConsensusFinding(sentence, ConsensusVerdict.ASSERTED, triggers[0].label))
    return findings


def is_quantified_expectation_sentence(sentence: str) -> bool:
    """Does the sentence attach a FIGURE to a consensus or market expectation?

    Asked over the sentence's whole trigger list rather than off the reported label: a sentence can
    match the quantified pattern while an earlier trigger in the frozen order (referent + verb) is
    what gets reported, and "consensus estimates of $5.00" is precisely such a sentence.
    """
    return any(trigger.label == "quantified consensus figure"
               for trigger in attribution_triggers(sentence))


def affirmative_expectation_sentences(
    text: str, *, state: ExpectationKnowledgeStateV1,
) -> list[str]:
    return [f.sentence for f in affirmative_expectation_findings(text, state=state)]


def suppressed_absence_findings(
    text: str, *, state: ExpectationKnowledgeStateV1,
) -> list[ConsensusFinding]:
    """Findings the frozen classifier raised and D4-S dropped as honest absence language.

    Reported rather than discarded: this is the measurable difference D4-S makes, and a reviewer has
    to be able to read exactly which sentences stopped being rejected without re-running a live call.
    """
    if state.market_expectation_claim_allowed:
        return list(asserted_consensus_findings(text))
    kept = {f.sentence for f in affirmative_expectation_findings(text, state=state)}
    return [f for f in asserted_consensus_findings(text) if f.sentence not in kept]


__all__ = [
    "EXPECTATION_STATE_CONTRACT_VERSION",
    "ExpectationBasis",
    "ExpectationKnowledgeStateV1",
    "ExpectationKnowledgeStatus",
    "affirmative_expectation_findings",
    "affirmative_expectation_sentences",
    "derive_expectation_knowledge_state",
    "is_affirmative_expectation_sentence",
    "split_sentences",
    "suppressed_absence_findings",
]
