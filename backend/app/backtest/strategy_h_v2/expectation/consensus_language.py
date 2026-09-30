"""Defect 1's repair: telling an ASSERTED consensus expectation apart from an HONEST ABSENCE of one.

V1 asked one question - does the text contain any of eleven substrings - and one of those
substrings, "consensus expect", is a prefix of "consensus expectations", which is the phrase the
contract's own §18 remedy sentence uses. So the rule rejected its prescribed cure, and its error
message told the model to write the sentence that had just been rejected. D4.1 Tier A's two
candidates both ended on exactly that error after exhausting the repair loop, having fabricated
nothing. A contract that cannot be satisfied is not strict, it is broken.

What is prohibited has not changed and is not relaxed here. D4 has no analyst-consensus source for
any candidate - `earnings.status` is UNKNOWN for 2,010 of 2,010 - so any statement of what analysts
or the market expect is invention. What V2 fixes is that the OPPOSITE statement, that no such
evidence exists, was being punished as if it were the invention.

The discrimination is deterministic and sentence-scoped, never a substring scan and never a model
call:

  a sentence VIOLATES when it names a consensus referent (consensus / analysts / Wall Street /
  the Street / sell-side) AND attributes an expectation to it - as a verb ("analysts expect"), as a
  quantified noun ("consensus estimates of $5.00"), or as a comparison ("beat consensus") - AND
  does not, in that same sentence, say that the evidence for it is unavailable.

The availability vocabulary is deliberately narrow. A blanket "contains a negation" carve-out would
admit "Consensus does not expect growth", which is an assertion about consensus and exactly what
the rule exists to stop. Only statements about the EVIDENCE's availability qualify; statements
about the expectation's CONTENT never do, whichever way they point.

D4.3R's audit-alignment pass (§D) found and deliberately left open one coverage gap in this same,
unchanged rule: the referent list named "consensus", "analysts", "sell-side", "wall street" and "the
street", but not bare "market". "The market expects X." named no OTHER consensus referent and
carried no absence language, so it fell through to NEUTRAL - a real detection gap for a sentence the
rule's own stated principle ("analyst / consensus / market expectation" cannot be asserted without a
source) was always meant to cover. This is that closure, added as two narrowly SCOPED patterns rather
than by widening `_REFERENT` itself: `_MARKET_VERB` catches "market expects"/"market is expecting"
with the verb directly at "market"'s side, and `_MARKET_EXPECTATION_SUBJECT` catches "market
expectations/consensus/estimates/views" as a bare attributed subject even when no listed verb follows
("Market expectations imply..."). Both require direct adjacency rather than `_REFERENT`'s
whole-sentence, position-independent search, because "market" - unlike "consensus" or "analysts" - is
common enough in D4's own approved vocabulary (price reactions, milestones) that an unscoped pairing
produced a real false positive during this closure's own validation: a sentence about an "operational
milestone, not a financial target" that separately mentioned a "market-expectation gap" paired the
unrelated word "target" with "market" purely because both sat somewhere in one sentence. One absence
phrase ("insufficient evidence") is added for the same reason the market patterns are: once "market"
can trigger ASSERTED, an honest sentence like "There is insufficient evidence to determine what the
market expects" needs a matching absence phrase or it flips from NEUTRAL to wrongly ASSERTED instead
of ABSENCE - the exact D4.1 failure mode this rule exists to prevent. Ordinary uses of the word
"market" that attribute nothing ("the company serves the US market", "market share increased") are
untouched: they still produce no trigger and classify NEUTRAL, exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

CONSENSUS_LANGUAGE_CONTRACT_VERSION = "h_v2_d4_consensus_language_v2_2"

#: V1's eleven substrings, copied verbatim from commit 8a54895. V2 must still reject every one of
#: them in an asserting sentence; `test_consensus_language.py` proves it phrase by phrase, so the
#: repair cannot quietly become a relaxation.
V1_BANNED_SUBSTRINGS: tuple[str, ...] = (
    "analysts expect", "analysts estimate", "consensus expect", "consensus estimate",
    "wall street expect", "the street expect", "investors are pricing", "consensus assumes",
    "beat consensus", "missed consensus", "consensus forecast",
)

#: The mandated replacement (D4 brief §18). Present here so a test can assert the validator accepts
#: the sentence the prompt orders the model to write - the single check V1 would have failed.
PRESCRIBED_ABSENCE_SENTENCE = "Available evidence does not establish consensus expectations."

# --- who the expectation would be attributed to --------------------------------------------------

_REFERENT = re.compile(
    r"\b(consensus|analysts?|sell[-\s]side|wall\s+street|the\s+street|street\s+estimates?)\b",
    re.IGNORECASE,
)

# --- attributing an expectation to them ----------------------------------------------------------
#
# Verbs only in the first pattern: "consensus expectations" as a bare noun phrase attributes
# nothing, which is why "consensus expectations are unavailable" has to survive.

_EXPECTATION_VERB = re.compile(
    r"\b(expects?|expected|expecting|estimates?|estimated|estimating|forecasts?|forecasted|"
    r"forecasting|assumes?|assumed|anticipates?|anticipated|projects?|projected|models?|modell?ed|"
    r"predicts?|predicted|sees?|saw|calls?\s+for|looking\s+for|targets?|targeted)\b",
    re.IGNORECASE,
)

#: A number attributed to a consensus noun without any verb: "consensus estimates of $5.00",
#: "analyst expectations around 20% growth". The quantification is the attribution.
_QUANTIFIED_NOUN = re.compile(
    r"\b(consensus|analysts?|sell[-\s]side|wall\s+street|the\s+street|market)\b[^.;]{0,40}?"
    r"\b(expectations?|estimates?|forecasts?|projections?|numbers?|views?|targets?)\b"
    r"[^.;]{0,20}?\b(of|for|at|around|near|above|below|versus|vs\.?)\b[^.;]{0,20}?"
    r"[-+$]?\d",
    re.IGNORECASE,
)

#: Comparing a result to consensus is an assertion that a consensus figure existed.
_COMPARISON = re.compile(
    r"\b(beat|beats|beating|miss|missed|misses|missing|above|below|ahead\s+of|behind|"
    r"in\s+line\s+with|versus|vs\.?|against|relative\s+to|topped|exceeded|trailed)\b\s+"
    r"(?:the\s+|a\s+)?"
    r"(?:"
    r"(?:analysts?\'?|wall\s+street|the\s+street|street|sell[-\s]side)\s+"
    r"(?:consensus|estimates?|expectations?|forecasts?|numbers?)"
    r"|market\s+(?:consensus|estimates?|expectations?|forecasts?|numbers?)"
    r"|consensus(?:\s+(?:estimates?|expectations?|forecasts?|numbers?))?"
    r")\b",
    re.IGNORECASE,
)

#: "Market expectations imply...", "market's consensus suggests...": the referent + expectation-noun
#: PAIR is itself the attribution, independent of which verb (if any) follows - unlike
#: `_EXPECTATION_VERB`, which needs a listed verb, this catches "imply", "suggest" and any other verb
#: the model chooses once the noun phrase alone already names whose expectation is being stated.
#: Scoped to "market" only: "consensus"/"analysts" already get this coverage from `_QUANTIFIED_NOUN`
#: and `_EXPECTATION_VERB`, and widening this pattern to every referent is not this gap's scope.
#: Deliberately requires DIRECT adjacency ("market" then whitespace then the noun), not a gap-bridged
#: search: this rule's own error message names "the market" and, three words later inside a quoted
#: trigger, "Consensus" - a `[^.;]{0,15}` gap would bridge that punctuation boundary and make the
#: rule's own explanation of itself misclassify as ASSERTED.
_MARKET_EXPECTATION_SUBJECT = re.compile(
    r"\bmarket'?s?\s+(expectations?|consensus|estimates?|views?)\b",
    re.IGNORECASE,
)

#: "The market expects X.", "The market is expecting X.": unlike `_REFERENT`+`_EXPECTATION_VERB`'s
#: whole-sentence, unscoped search (fine for rare words like "consensus"/"analysts", where an
#: unrelated verb elsewhere in the same sentence is not a realistic collision), "market" is common
#: enough in D4's own approved vocabulary (price reactions, milestones, market caps) that the same
#: unscoped pairing produced a real false positive: "...not a financial target, and says little
#: about the size of the market-expectation gap" paired the unrelated "target" with "market" purely
#: because both appeared somewhere in one sentence. So "market" is deliberately NOT added to
#: `_REFERENT` - this pattern requires the verb directly at "market"'s side (at most one copula
#: between them), not merely present anywhere in the same sentence.
_MARKET_VERB = re.compile(
    r"\bmarket\b\s*(?:is\s+|are\s+|was\s+|were\s+)?" + _EXPECTATION_VERB.pattern,
    re.IGNORECASE,
)

#: D4-S. "Investors expect X." named no listed referent and carried no listed absence phrase, so it
#: fell through to NEUTRAL - the same coverage gap D4.3R2 closed for bare "market", in the same
#: shape, for the one remaining subject D4's brief names. Scoped exactly like `_MARKET_VERB` (the
#: verb directly at the subject's side, at most one copula between) rather than by adding
#: `investors` to `_REFERENT`, because an unscoped whole-sentence pairing is what produced R2's own
#: false positive. "Investors are pricing ..." was already a V1 banned substring; this covers the
#: expectation verbs that phrase does not.
_INVESTOR_VERB = re.compile(
    r"\binvestors?\b\s*(?:is\s+|are\s+|was\s+|were\s+)?" + _EXPECTATION_VERB.pattern,
    re.IGNORECASE,
)

# --- saying the evidence is not there ------------------------------------------------------------
#
# Every entry is about the AVAILABILITY of the evidence. Nothing here is about which way an
# expectation points, because a rule that accepted "does not expect" would have a hole the exact
# size of the thing it is guarding.
#
# "insufficient evidence" was added alongside the "market" referent above: without it, "There is
# insufficient evidence to determine what the market expects" would flip from NEUTRAL (pre-R2, no
# referent matched at all) to wrongly ASSERTED (post-R2, "market" + "expects" now trigger) instead of
# ABSENCE, which is exactly the D4.1 failure mode this rule exists to prevent.

_ABSENCE = re.compile(
    r"("
    r"\bnot\s+available\b|\bunavailable\b|\bno\s+[^.;]{0,40}\bavailable\b|"
    r"\bdoes\s+not\s+establish\b|\bdo\s+not\s+establish\b|\bnot\s+established\b|"
    r"\bcannot\s+be\s+established\b|\bcannot\s+establish\b|"
    r"\bdoes\s+not\s+support\b|\bdo\s+not\s+support\b|"
    r"\bnot\s+provided\b|\bnot\s+present\b|\bnot\s+disclosed\b|"
    r"\bis\s+absent\b|\bare\s+absent\b|\babsent\s+from\b|"
    r"\bnot\s+connected\b|\bno\s+provider\b|\bno\s+source\b|\bno\s+[^.;]{0,40}\bsource\s+set\b|"
    r"\bno\s+consensus\b|\bno\s+analysts?\b|\bno\s+sell[-\s]side\b|\bno\s+estimate\b|"
    r"\bnot\s+exist\b|\bdoes\s+not\s+exist\b|\bdo\s+not\s+exist\b|\bnone\s+(is|are)\s+available\b|"
    r"\bcannot\s+be\s+determined\b|\bcould\s+not\s+be\s+determined\b|"
    r"\bnot\s+in\s+the\s+(current\s+)?(evidence|source)\b|\bnot\s+part\s+of\s+the\s+evidence\b|"
    r"\bno\s+[^.;]{0,40}\bcoverage\b|\bnot\s+covered\b|\bnot\s+collected\b|\bnot\s+retrievable\b|"
    r"\binsufficient\s+evidence\b|"
    r"\bSOURCE_NOT_AVAILABLE\b|\black(s|ing)?\b|\bwithout\s+(any\s+)?(a\s+)?(consensus|analyst)\b"
    r")",
    re.IGNORECASE,
)

#: Punctuation followed by whitespace. Deliberately not a bare "." split: "$1.05 to $1.15 billion"
#: must stay one sentence, or a decimal point silently becomes a scope boundary.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


class ConsensusVerdict:
    ABSENCE = "ABSENCE"
    """States that consensus evidence is unavailable. Always allowed - this is what §18 asks for."""
    ASSERTED = "ASSERTED"
    """Attributes an expectation to analysts or the market. Always rejected."""
    NEUTRAL = "NEUTRAL"
    """Names no consensus referent, or names one without attributing an expectation to it."""


@dataclass(frozen=True)
class ConsensusFinding:
    sentence: str
    verdict: str
    trigger: str
    """What matched - the V1 substring, the verb, the quantified noun or the comparison."""


@dataclass(frozen=True)
class AttributionTrigger:
    """One attribution match, WITH the span it occupies in the sentence.

    D4-S extracted this. The span is what a structural polarity test needs and a label alone cannot
    give: deciding whether an attribution sits under a negated epistemic frame, or is that frame's
    subject, is a question about WHERE the attribution is, not only that one exists. The labels and
    their order are exactly `classify_sentence`'s former inline list, so the trigger a finding
    reports is unchanged.
    """

    label: str
    start: int
    end: int


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def attribution_triggers(sentence: str) -> list[AttributionTrigger]:
    """Every way `sentence` attributes an expectation to a consensus referent, in frozen order.

    This is the trigger half of `classify_sentence`, lifted out unchanged so that exactly one copy
    of these patterns exists. It deliberately does NOT consult `_ABSENCE`: a caller that wants the
    frozen ABSENCE/ASSERTED/NEUTRAL verdict calls `classify_sentence`, and a caller doing its own
    polarity analysis (`expectation_state`) needs the raw attributions without that short-circuit.
    """
    lowered = sentence.lower()
    triggers: list[AttributionTrigger] = []

    referent = _REFERENT.search(sentence)
    if referent:
        verb = _EXPECTATION_VERB.search(sentence)
        if verb:
            triggers.append(AttributionTrigger(
                f"{referent.group(0).strip()} ... {verb.group(0).strip()}",
                min(referent.start(), verb.start()), max(referent.end(), verb.end())))
    quantified = _QUANTIFIED_NOUN.search(sentence)
    if quantified:
        triggers.append(AttributionTrigger(
            "quantified consensus figure", quantified.start(), quantified.end()))
    comparison = _COMPARISON.search(sentence)
    if comparison:
        triggers.append(AttributionTrigger(
            "comparison against consensus", comparison.start(), comparison.end()))
    subject = _MARKET_EXPECTATION_SUBJECT.search(sentence)
    if subject:
        triggers.append(AttributionTrigger(
            "market expectation noun phrase", subject.start(), subject.end()))
    market_verb = _MARKET_VERB.search(sentence)
    if market_verb:
        triggers.append(AttributionTrigger(
            f"market ... {market_verb.group(1).strip()}", market_verb.start(), market_verb.end()))
    investor_verb = _INVESTOR_VERB.search(sentence)
    if investor_verb:
        triggers.append(AttributionTrigger(
            f"investors ... {investor_verb.group(1).strip()}",
            investor_verb.start(), investor_verb.end()))
    for phrase in V1_BANNED_SUBSTRINGS:
        index = lowered.find(phrase)
        if index >= 0:
            triggers.append(AttributionTrigger(phrase, *_whole_words(sentence, index,
                                                                    index + len(phrase))))
    return triggers


def _whole_words(sentence: str, start: int, end: int) -> tuple[int, int]:
    """Grow a span out to token boundaries.

    The V1 substrings are not word-anchored, and one of them - "consensus expect" - is a PREFIX of
    "consensus expectations", which is the very collision D4.1 was built to fix. A raw substring
    span therefore ends mid-token, and any caller reasoning about what sits BESIDE the attribution
    would read the tail of the word it matched ("ations") as a neighbouring content word. Snapping
    to token boundaries is not a leniency: the same characters still match, they are just measured
    to the end of the word they are part of.
    """
    while start > 0 and (sentence[start - 1].isalnum() or sentence[start - 1] == "'"):
        start -= 1
    while end < len(sentence) and (sentence[end].isalnum() or sentence[end] == "'"):
        end += 1
    return start, end


def classify_sentence(sentence: str) -> ConsensusFinding:
    """One sentence's verdict. Scope is the sentence on purpose: an absence statement in one
    sentence must not license an assertion in the next."""
    referent = _REFERENT.search(sentence)
    absence = _ABSENCE.search(sentence)
    triggers = attribution_triggers(sentence)

    if absence and (referent or triggers):
        return ConsensusFinding(sentence, ConsensusVerdict.ABSENCE, absence.group(0).strip())
    if not triggers:
        return ConsensusFinding(sentence, ConsensusVerdict.NEUTRAL, "")
    return ConsensusFinding(sentence, ConsensusVerdict.ASSERTED, triggers[0].label)


def asserted_consensus_findings(text: str) -> list[ConsensusFinding]:
    """Every sentence in `text` that attributes an expectation to analysts or the market."""
    return [f for f in (classify_sentence(s) for s in split_sentences(text))
            if f.verdict == ConsensusVerdict.ASSERTED]


def asserts_consensus_expectation(text: str) -> bool:
    return bool(asserted_consensus_findings(text))
