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
"""

from __future__ import annotations

from dataclasses import dataclass
import re

CONSENSUS_LANGUAGE_CONTRACT_VERSION = "h_v2_d4_consensus_language_v2"

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
    r"\b(consensus|analysts?|sell[-\s]side|wall\s+street|the\s+street)\b[^.;]{0,40}?"
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
    r"|consensus(?:\s+(?:estimates?|expectations?|forecasts?|numbers?))?"
    r")\b",
    re.IGNORECASE,
)

# --- saying the evidence is not there ------------------------------------------------------------
#
# Every entry is about the AVAILABILITY of the evidence. Nothing here is about which way an
# expectation points, because a rule that accepted "does not expect" would have a hole the exact
# size of the thing it is guarding.

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


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def classify_sentence(sentence: str) -> ConsensusFinding:
    """One sentence's verdict. Scope is the sentence on purpose: an absence statement in one
    sentence must not license an assertion in the next."""
    lowered = sentence.lower()
    referent = _REFERENT.search(sentence)
    absence = _ABSENCE.search(sentence)

    triggers: list[str] = []
    if referent:
        verb = _EXPECTATION_VERB.search(sentence)
        if verb:
            triggers.append(f"{referent.group(0).strip()} ... {verb.group(0).strip()}")
    if _QUANTIFIED_NOUN.search(sentence):
        triggers.append("quantified consensus figure")
    if _COMPARISON.search(sentence):
        triggers.append("comparison against consensus")
    for phrase in V1_BANNED_SUBSTRINGS:
        if phrase in lowered:
            triggers.append(phrase)

    if absence and (referent or triggers):
        return ConsensusFinding(sentence, ConsensusVerdict.ABSENCE, absence.group(0).strip())
    if not triggers:
        return ConsensusFinding(sentence, ConsensusVerdict.NEUTRAL, "")
    return ConsensusFinding(sentence, ConsensusVerdict.ASSERTED, triggers[0])


def asserted_consensus_findings(text: str) -> list[ConsensusFinding]:
    """Every sentence in `text` that attributes an expectation to analysts or the market."""
    return [f for f in (classify_sentence(s) for s in split_sentences(text))
            if f.verdict == ConsensusVerdict.ASSERTED]


def asserts_consensus_expectation(text: str) -> bool:
    return bool(asserted_consensus_findings(text))
