"""D3.2 Validation Contract V2 - offline, zero-cost re-audit primitives.

This is a validation-layer repair, not a change to the D3 Research Schema or the D3.1 research
hypothesis. Nothing here touches `schema.py`'s `Claim`, `FutureBusinessItem`, the stage floor table,
or the prompt - those are frozen (`H_V2_D3_1_BATCH2_VALIDATION_V1.md` already decided not to tune the
Future Business ontology to reduce repairs, for reasons that still hold; see
`H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` §H). This module only changes how an *existing* output
is measured against its own cited evidence, so that D3.1's actual failures - citation precision, a
GAAP-vocabulary false positive, a numeric-audit blind spot below magnitude 10 - can be told apart
from genuine research-content defects, of which D3.1's manual audit found zero in 50 claims.

Every function here is pure and read-only: no model call, no network access, no mutation of any D2.1
or D3 artifact. `H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` §L records that a V1 gate percentage and
a V2 metric computed by this module are not comparable, because what "supported" and "violation" mean
changes here on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re

# ---------------------------------------------------------------------------------------------
# Numeric V2 (brief §10-§13)
# ---------------------------------------------------------------------------------------------
#
# V1's `extract_numbers` (audit_strategy_h_v2_d3_1.py) drops every value below 10 and every bare
# 4-digit year, then checks the survivors against the union of every shown chunk plus the FACTS
# block. Measured on Batch 2: 901 of 2,261 material-claim numbers (39.8%) never reached that check
# at all, and the union-of-everything support scope cannot tell "this number is in the chunk you
# cited" from "some number in the package rounds to this at some scale" - which is exactly how 7 of
# 50 R10-audited claims cited a neighbouring chunk without R4 or R5 noticing.
#
# V2 fixes both: no magnitude floor, and support is scoped to the claim's own cited chunk by
# default (brief §12 - "candidate 전체 검색으로 통과 금지").


class NumericUnit(StrEnum):
    USD = "USD"
    PERCENT = "PERCENT"
    BASIS_POINTS = "BASIS_POINTS"
    MULTIPLE = "MULTIPLE"
    SHARES = "SHARES"
    COUNT = "COUNT"
    """A bare number with no recognizable unit - still checked, just without a unit dimension."""
    UNKNOWN = "UNKNOWN"
    """Context genuinely does not disambiguate the unit (brief §11). Never silently dropped."""


#: Ordinary filing dates ("August 18, 2026", "June 30, 2026") are not the fabrication risk this
#: module is about, and treating their day/year fragments as freestanding numbers manufactures
#: false "unsupported" findings. Consumed and discarded before the numeric scan runs.
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
DATE_RE = re.compile(rf"\b(?:{_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b")
#: A bare 4-digit year on its own (no month), e.g. "in fiscal 2026" - still not a fabrication-risk
#: number, but *is* left in place as a COUNT-typed token at zero weight rather than deleted, so a
#: claim that mismatches every OTHER number is not silently missing a token to report.
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
#: Period/filing-type labels whose digit is an identifier, not a quantity: "Q2" is not the number
#: 2, "10-K" is not the number 10. Removing the magnitude-10 floor (this module's whole point) means
#: these would otherwise surface as spurious "unsupported" COUNT tokens on nearly every claim that
#: mentions a quarter or cites a filing type - caught by the first version of this parser reporting
#: "Q2 2026 revenue was $5.7 billion" as UNSUPPORTED because of the bare "2" in "Q2".
LABEL_RE = re.compile(
    r"\bQ[1-4]\b|\bH[1-2]\b|\bFY\s?\d{2,4}\b|\b(?:10-K|10-Q|8-K|6-K|20-F|S-1)\b|"
    r"\bEX-\d+(?:\.\d+)?\b|\bCHUNK:\d+\b", re.IGNORECASE)

#: Strict thousands-grouping (each comma-group after the first is exactly 3 digits), not the
#: permissive `[\d,]*` this module started with. That permissive form let a trailing, dangling
#: comma become part of the match - "in Q2 2026, and" captured "2026," as its own token, which then
#: failed `YEAR_RE.fullmatch` (the comma is not part of the year pattern) and fell through to being
#: audited as an ordinary COUNT value nothing in the evidence pool was ever going to satisfy, since
#: a bare year is only ever present as part of a DATE the pool already excludes. It also caused a
#: footnote superscript with no separating space, "loss)1,2" (footnotes 1 and 2), to be read as the
#: single value "12" - both found while auditing this batch's own real evidence text.
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
#: D2.1 chunk text keeps a filing table's own layout, so a number and its unit are often in
#: separate pipe-delimited cells - "Segment Operating Profit Margin | 79 | % |" - not adjacent
#: characters. A plain `\s?` between them (this module's first version) missed nearly every such
#: table cell, including one in the batch's own evidence: AMT's cited chunk states "79 | %" for the
#: exact "79%" its claim reports, and the first version wrongly reported it NOT_FOUND. Bounded to 8
#: characters of whitespace/pipe/parens so it cannot bridge into an unrelated column.
_GAP = r"[\s|()]{0,8}"
UNIT_PATTERNS: tuple[tuple[NumericUnit, re.Pattern[str]], ...] = (
    (NumericUnit.PERCENT, re.compile(rf"({_NUM}){_GAP}(?:%|percent(?:age points?)?|\bpp\b)")),
    #: "basis-point" (hyphenated singular) and a hard line-wrap inside "basis\npoints" both occur
    #: verbatim in this batch's own evidence (MOV) - `[\s-]+` covers either without requiring the
    #: literal single space a plain "basis points?" would.
    (NumericUnit.BASIS_POINTS,
     re.compile(rf"({_NUM}){_GAP}(?:bps|bp\b|basis[\s-]+points?)", re.IGNORECASE)),
    (NumericUnit.MULTIPLE, re.compile(rf"({_NUM}){_GAP}x\b(?!\w)")),
    (NumericUnit.SHARES, re.compile(rf"({_NUM}){_GAP}(?:million|billion|thousand)?{_GAP}shares\b",
                                    re.IGNORECASE)),
    (NumericUnit.USD, re.compile(rf"\${_GAP}({_NUM}){_GAP}(?:million|billion|thousand)?",
                                 re.IGNORECASE)),
    (NumericUnit.USD, re.compile(rf"({_NUM}){_GAP}(?:million|billion|thousand)\s+dollars\b",
                                 re.IGNORECASE)),
)

#: Scale multipliers a filing's own unit word can carry, applied on top of the bare digit value.
_WORD_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
#: A number with no explicit scale word may still be reported in thousands in a table ("116,047")
#: while a claim states "$116.047 million" - both correct, so support-matching tries every scale.
_SCALES = (1.0, 1e3, 1e6, 1e9, 1e-3, 1e-6, 1e-9)


@dataclass(frozen=True)
class NumericToken:
    raw: str
    value: float
    """Bare digit value before any unit-word scale is applied."""
    unit: NumericUnit
    decimals: int
    span: tuple[int, int]

    def scaled_value(self, text_window: str) -> float:
        """`value`, multiplied by "million"/"billion"/"thousand" if that word sits right after it."""
        tail = text_window[self.span[1]:self.span[1] + 12].lower()
        for word, scale in _WORD_SCALE.items():
            if tail.lstrip().startswith(word):
                return self.value * scale
        return self.value


def parse_numeric_tokens(text: str) -> list[NumericToken]:
    """Every number in `text` with a unit, at whatever magnitude it was written.

    No floor: "3.3%" and "90 bps" are tokens here, where V1's `extract_numbers` silently passed
    over them. Dates are consumed first so a day-of-month or a year is never reported as a bare
    COUNT. A number already claimed by a more specific pattern (PERCENT, USD, ...) is not
    re-reported as COUNT - each character position yields at most one token.
    """
    claimed: set[tuple[int, int]] = set()
    tokens: list[NumericToken] = []
    for start, end in (m.span() for m in DATE_RE.finditer(text)):
        claimed.add((start, end))
    for start, end in (m.span() for m in LABEL_RE.finditer(text)):
        claimed.add((start, end))

    def _overlaps_date(pos: int) -> bool:
        return any(s <= pos < e for s, e in claimed)

    for unit, pattern in UNIT_PATTERNS:
        for m in pattern.finditer(text):
            if _overlaps_date(m.start()):
                continue
            num_span = m.span(1)
            if any(num_span[0] < e and num_span[1] > s for s, e in claimed):
                continue
            cleaned = m.group(1).replace(",", "")
            try:
                value = float(cleaned)
            except ValueError:
                continue
            decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
            tokens.append(NumericToken(m.group(1), value, unit, decimals, num_span))
            claimed.add(num_span)

    for m in re.finditer(_NUM, text):
        span = m.span()
        if _overlaps_date(span[0]) or any(span[0] < e and span[1] > s for s, e in claimed):
            continue
        cleaned = m.group(0).replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if YEAR_RE.fullmatch(m.group(0)) and "." not in cleaned:
            tokens.append(NumericToken(m.group(0), value, NumericUnit.UNKNOWN, 0, span))
            continue
        decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
        tokens.append(NumericToken(m.group(0), value, NumericUnit.COUNT, decimals, span))
        claimed.add(span)

    return sorted(tokens, key=lambda t: t.span[0])


class SupportScope(StrEnum):
    CITED_CHUNK = "CITED_CHUNK"
    """The claim's own `evidence_id` - the only scope that counts as full support (brief §12)."""
    ADJACENT_CHUNK = "ADJACENT_CHUNK"
    """Same `source_id`, `chunk_index` within 1 of the cited chunk. Reported separately, never
    folded into "supported" - a claim that needed this is a citation-precision defect, not a
    fabrication, and the two must stay distinguishable (brief §8, §12)."""
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class NumericSupportResult:
    token: NumericToken
    scope: SupportScope
    matched_value: float | None = None


def _values_in(text: str) -> dict[NumericUnit, list[float]]:
    """Raw, as-written values (no unit-word scaling applied) - the unit word is not needed to
    resolve a match, because the scale trial in `_supported_by` already searches every plausible
    magnitude. Pre-scaling here as well would double-apply the same "million"/"thousand" factor
    when both the claim and the chunk spell out a unit word, and was the actual cause of this
    module's own `$5.7 billion` self-test failing against a chunk that also says `$5.7 billion`."""
    by_unit: dict[NumericUnit, list[float]] = {}
    for tok in parse_numeric_tokens(text):
        by_unit.setdefault(tok.unit, []).append(tok.value)
    return by_unit


def _supported_by(token: NumericToken, chunk_text: str) -> float | None:
    pool = _values_in(chunk_text)
    if token.unit in (NumericUnit.PERCENT, NumericUnit.BASIS_POINTS, NumericUnit.MULTIPLE):
        # Precise reported ratios - no unit-scale ambiguity, only rounding tolerance. A filing
        # table often states a ratio as a bare number under a "%"-labelled column without
        # repeating the sign in every cell ("Efficiency ratio ... | 46.57 | 48.19 |") - SRCE's own
        # cited chunk does exactly this for a value its claim states correctly. That fallback is
        # restricted to values precise to >=2 decimals: a round "3.3" or "19" collides too easily
        # with an unrelated bare count (a share count, a headcount) to treat a match as support -
        # brief §10's "unit mismatches" is exactly the failure this restriction keeps distinct.
        candidates = list(pool.get(token.unit, []))
        if token.decimals >= 2:
            candidates += pool.get(NumericUnit.COUNT, [])
        tolerance = max(0.5 * 10 ** (-token.decimals), abs(token.value) * 1e-6)
        for candidate in candidates:
            if abs(candidate - token.value) <= tolerance:
                return candidate
        return None
    if token.unit == NumericUnit.UNKNOWN:
        return None
    # USD / SHARES / COUNT: a filing may report the same fact in a different scale or unit than
    # the claim uses ("$1,430.4 million" claimed against a table's "1,430,379" in thousands), so
    # every scale is tried against the raw written values on both sides, tolerance scaled with
    # it - the same two-sided trial `audit_strategy_h_v2_d3_1.py`'s V1 matcher already used, kept
    # here rather than reinvented so the AAON-style false-fabrication class it fixed stays fixed.
    # The fallback is symmetric: USD/SHARES accept a bare COUNT as a match, and a bare COUNT
    # accepts a USD/SHARES match too - a range like "$5.0-9.0M" gives its first bound a "$" and its
    # second bound none (an entirely ordinary way to write it), so the claim's own second number
    # parses as COUNT while the evidence for the very same figure, in AIP's own cited chunk, is
    # written "$9.0" and parses as USD. One-directional fallback missed this in the batch's own
    # data; a currency figure not spelled out identically both times is the common case, not the
    # exception, for exactly the reason this asymmetry was found.
    candidates = list(pool.get(token.unit, []))
    if token.unit in (NumericUnit.USD, NumericUnit.SHARES):
        candidates += pool.get(NumericUnit.COUNT, [])
    elif token.unit == NumericUnit.COUNT:
        candidates += pool.get(NumericUnit.USD, []) + pool.get(NumericUnit.SHARES, [])
    base_tolerance = max(0.5 * 10 ** (-token.decimals), abs(token.value) * 1e-9)
    for scale in _SCALES:
        # `mult` is the SAME factor applied to both the target and its tolerance - tolerance must
        # shrink exactly as much as the target does when `mult < 1`, or it does not, and the two
        # get compared on inconsistent bases. The first version of this trial reused `tolerance *
        # max(1.0, scale)` for the `value / scale` branch too, so a small division target (e.g.
        # 1.1 / 1000 = 0.0011) kept the large multiplied-up tolerance (0.05 * 1000 = 50) instead of
        # the tiny one that division actually implies - a claim's raw digits then matched almost
        # any nearby evidence number within +-50 of zero. Caught only once magnitude-10 values
        # were auditable at all: LNG's own "$1.1 billion" claim matched an unrelated "$3.1 billion"
        # figure in its own cited chunk under the old formula.
        for mult in (scale, 1.0 / scale):
            target = token.value * mult
            tolerance = base_tolerance * mult
            for candidate in candidates:
                if abs(candidate - target) <= tolerance:
                    return candidate
    return None


def numeric_support(
    token: NumericToken, claim_text: str, *, cited_chunk_text: str,
    adjacent_chunk_texts: tuple[str, ...] = (),
) -> NumericSupportResult:
    """Where `token` (from `claim_text`) is actually supported, cited-chunk-first.

    `claim_text` is accepted for symmetry with the call sites (and so a future unit that does need
    surrounding-word context still has it) even though the current match is on `token.value` alone.

    Only `SupportScope.CITED_CHUNK` counts as the claim doing what it says. `ADJACENT_CHUNK` is
    reported so a citation-precision defect (brief §8's "adjacent chunk" problem) is visible
    without being laundered into a pass - it means the fact is real but the pointer is wrong.
    """
    del claim_text
    hit = _supported_by(token, cited_chunk_text)
    if hit is not None:
        return NumericSupportResult(token, SupportScope.CITED_CHUNK, hit)
    for adj in adjacent_chunk_texts:
        hit = _supported_by(token, adj)
        if hit is not None:
            return NumericSupportResult(token, SupportScope.ADJACENT_CHUNK, hit)
    return NumericSupportResult(token, SupportScope.NOT_FOUND)


# ---------------------------------------------------------------------------------------------
# Citation Integrity V2 (brief §5, §9)
# ---------------------------------------------------------------------------------------------


class ProvenanceStatus(StrEnum):
    OK = "OK"
    MISSING_SOURCE_ID = "MISSING_SOURCE_ID"
    MISSING_EVIDENCE_ID = "MISSING_EVIDENCE_ID"
    SOURCE_UNKNOWN_TO_CANDIDATE = "SOURCE_UNKNOWN_TO_CANDIDATE"
    EVIDENCE_UNKNOWN_TO_CANDIDATE = "EVIDENCE_UNKNOWN_TO_CANDIDATE"
    EVIDENCE_SOURCE_MISMATCH = "EVIDENCE_SOURCE_MISMATCH"
    """`evidence_id` does not start with `f"{source_id}:"` - the chunk belongs to a different
    source than the one the claim names, even if both resolve inside this candidate."""
    EVIDENCE_WRONG_CANDIDATE = "EVIDENCE_WRONG_CANDIDATE"
    """Resolves in the run's evidence pool but not in *this* candidate's own package - brief §9's
    cross-company rejection case."""


def structural_provenance(
    *, source_id: str | None, evidence_id: str | None,
    candidate_source_ids: frozenset[str], candidate_evidence_ids: frozenset[str],
) -> ProvenanceStatus:
    """R4A. Unchanged in substance from the check D3.1 §2.1 already added to `validate.py` - this
    is the audit-side restatement of it, kept independent so the audit does not just confirm the
    validator that produced the output (brief's own stated principle for why this audit exists).
    """
    if not source_id:
        return ProvenanceStatus.MISSING_SOURCE_ID
    if not evidence_id:
        return ProvenanceStatus.MISSING_EVIDENCE_ID
    if source_id not in candidate_source_ids:
        return ProvenanceStatus.SOURCE_UNKNOWN_TO_CANDIDATE
    if evidence_id not in candidate_evidence_ids:
        return ProvenanceStatus.EVIDENCE_WRONG_CANDIDATE
    if not evidence_id.startswith(f"{source_id}:"):
        return ProvenanceStatus.EVIDENCE_SOURCE_MISMATCH
    return ProvenanceStatus.OK


class PrecisionStatus(StrEnum):
    PRECISE = "PRECISE"
    """Every numeric token in the claim resolves inside its own cited chunk."""
    ADJACENT_RECOVERABLE = "ADJACENT_RECOVERABLE"
    """At least one token only resolves in a same-source neighbouring chunk - brief §8's defect
    class, found 5 times in D3.1's R10 sample."""
    UNSUPPORTED = "UNSUPPORTED"
    """At least one token resolves nowhere nearby - the stronger claim, not observed in D3.1's
    50-claim manual sample but not assumed away either."""
    NO_NUMERIC_CONTENT = "NO_NUMERIC_CONTENT"
    """Nothing to check this way; content accuracy still needs the R10 manual read."""


@dataclass(frozen=True)
class CitationPrecisionResult:
    status: PrecisionStatus
    token_results: tuple[NumericSupportResult, ...]

    @property
    def unsupported(self) -> tuple[NumericSupportResult, ...]:
        return tuple(r for r in self.token_results if r.scope == SupportScope.NOT_FOUND)

    @property
    def adjacent(self) -> tuple[NumericSupportResult, ...]:
        return tuple(r for r in self.token_results if r.scope == SupportScope.ADJACENT_CHUNK)


def citation_precision(
    claim_text: str, *, cited_chunk_text: str | None, adjacent_chunk_texts: tuple[str, ...] = (),
) -> CitationPrecisionResult:
    """R4B. Whether the numbers *in this specific claim* are where the claim says they are.

    `cited_chunk_text=None` (the `evidence_id` did not even resolve) is reported as every token
    unsupported, rather than raising - structural failure is `structural_provenance`'s job, this
    function only measures precision given a resolvable pointer.
    """
    tokens = [t for t in parse_numeric_tokens(claim_text) if t.unit != NumericUnit.UNKNOWN]
    if not tokens:
        return CitationPrecisionResult(PrecisionStatus.NO_NUMERIC_CONTENT, ())
    results = tuple(
        numeric_support(t, claim_text, cited_chunk_text=cited_chunk_text or "",
                        adjacent_chunk_texts=adjacent_chunk_texts)
        for t in tokens
    )
    if any(r.scope == SupportScope.NOT_FOUND for r in results):
        status = PrecisionStatus.UNSUPPORTED
    elif any(r.scope == SupportScope.ADJACENT_CHUNK for r in results):
        status = PrecisionStatus.ADJACENT_RECOVERABLE
    else:
        status = PrecisionStatus.PRECISE
    return CitationPrecisionResult(status, results)


# ---------------------------------------------------------------------------------------------
# Compound Claim detection (brief §6) - reporting only, no schema change
# ---------------------------------------------------------------------------------------------
#
# Splitting a compound claim into atomic claims is a prompt/schema change (D3.3 territory: it needs
# a live model to actually produce atomic claims). This offline module can only flag, on the
# existing D2.1-batch outputs, where a single claim already reads as more than one proposition
# sharing one `evidence_id` - brief §6's "one evidence_id for two unsupported propositions" case -
# so D3.3 knows how often it would matter.

#: Splitting on every sentence boundary was tried first and rejected: most Batch-2 claims are one
#: fact followed by a period and a sentence of reasoning about it (`Claim` schema's own
#: FACT/INTERPRETATION/INFERENCE structure encourages exactly that), and it flagged 864 of 1040
#: material claims (83%) as "compound" - almost the whole batch, and useless as a signal. Restricted
#: to an intra-sentence coordinating "and"/"but", which is what the brief's own example
#: ("Revenue increased and a new customer was added") actually looks like.
_CLAUSE_SPLIT = re.compile(r"\s+(?:and|but)\s+")
_HEDGE_LEAD = re.compile(r"^(which|so|this|that|it|then|therefore)\b", re.IGNORECASE)
#: A finite-verb signal anywhere in the segment - not anchored to its start, since the subject of
#: the second half of "revenue increased and a new customer was added" is a multi-word lowercase
#: noun phrase, not a capitalized name a start-anchored pattern could key off.
_FINITE_VERB = re.compile(
    r"\b(?:was|were|is|are|grew|fell|rose|closed|added|declared|reported|expanded|contracted|"
    r"increased|decreased|announced|completed|entered|signed|hired|launched|acquired|repaid|"
    r"issued|approved|opened)\b", re.IGNORECASE)


def compound_claim_segments(text: str) -> tuple[str, ...]:
    """Candidate independent propositions joined by "and"/"but" within one claim, sharing one
    `evidence_id` (brief §6). Deliberately conservative and heuristic, not a semantic judgment
    (brief §7): a segment counts only if it is long enough to be its own statement, is not a
    subordinate "which"/"so" clause explaining the first part, and carries either a number of its
    own or a distinguishable lead verb - so "plan, buy and measure digital advertising" (one verb
    phrase's own object list) does not count, but "revenue increased and a new customer was added"
    does. This undercounts real compound claims that share no number and no clear verb signal on
    purpose - a coarse detector that also flags list-like enumerations is not a useful one.
    """
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(text) if p.strip()]
    if len(parts) < 2:
        return ()
    scored = [p for p in parts if len(p) >= 15 and not _HEDGE_LEAD.match(p)
             and (re.search(r"\d", p) or _FINITE_VERB.search(p))]
    return tuple(scored) if len(scored) >= 2 else ()


def is_compound_claim(text: str) -> bool:
    return len(compound_claim_segments(text)) >= 2


# ---------------------------------------------------------------------------------------------
# Investment Language V2 (brief §14-§16)
# ---------------------------------------------------------------------------------------------
#
# D3.1 §I.2 found `\bfair value\b` flagging ordinary GAAP measurement vocabulary in 3 of 24
# candidates (AIP, LNG, MIDD - 181/215/156 occurrences of the phrase in their own filings) and the
# repair then reworded a correct accounting statement away from its source's own wording. The other
# terms in `BANNED_INVESTMENT_LANGUAGE_PATTERNS` (schema.py) are already whole-phrase analyst/
# strategy jargon ("price target", "strong buy", "entry zone", ...) that does not occur in ordinary
# filing prose, and D3.1's own pilot already fixed the bare "approve"/"reject"/"buy"/"sell"
# substring problem (schema.py's comment on that list documents it) - so V2 does not touch those,
# it only splits "fair value" into a lexical hit plus a context classifier, and does the same for
# "approve"/"reject" defensively so a future banned-language edit cannot reintroduce that mistake.


class LanguageVerdict(StrEnum):
    SAFE = "SAFE"
    """Matched a lexical trigger but the surrounding context is ordinary filing vocabulary."""
    VIOLATION = "VIOLATION"
    UNCLASSIFIED = "UNCLASSIFIED"
    """Matched with no GAAP-safe marker and no explicit opinion marker either. Reported, not
    silently passed - a term this ambiguous belongs in a manual sample, not a default verdict."""


@dataclass(frozen=True)
class LanguageMatch:
    term: str
    span: tuple[int, int]
    context: str
    verdict: LanguageVerdict


#: "fair value" is GAAP's own name for a measurement basis. It is safe whenever it is attached to
#: the thing being measured under GAAP, and a violation only when it names the company's own stock
#: or shares as the object of an opinion, in the pattern real sell-side language actually uses.
_FV_SAFE_OBJECT = re.compile(
    r"\b(?:of|for)\b[^.]{0,30}\b(?:derivative|instrument|asset|liabilit|plan asset|investment|"
    r"note receivable|contingent consideration|goodwill|inventory|debt|warrant|option|hedg|"
    r"reporting unit|business combination)", re.IGNORECASE)
_FV_SAFE_FRAME = re.compile(
    r"\b(?:measured|recorded|carried|remeasur\w*|accounted for)\b[^.]{0,20}\bat\b|"
    r"\bfair value hierarchy\b|\bchanges? in (?:the )?fair value\b|\bcarrying (?:value|amount)\b",
    re.IGNORECASE)
_FV_OPINION = re.compile(
    r"\b(?:stock|share price|shares|company|business)(?:'s)?\s+fair value\s+(?:is|of)\s*\$|"
    r"\bwe believe (?:the )?fair value\b|\bour fair value estimate\b", re.IGNORECASE)

_APPROVE_SAFE = re.compile(
    r"\bboard(?:\s+of\s+directors)?\s+approved\b|\bshareholders?\s+(?:approved|rejected)\b|"
    r"\bapproved\s+(?:a|the)\s+(?:share\s+repurchase|budget|merger|plan|proposal|agreement)\b",
    re.IGNORECASE)
_APPROVE_OPINION = re.compile(
    r"\bwe\s+(?:approve|recommend)\s+(?:this|the)\s+(?:stock|investment|company)\b|"
    r"\breject\s+(?:this|the)\s+company\s+as\s+an\s+investment\b", re.IGNORECASE)

#: Retired by D4-E7R. The same phrases are now in `_JARGON_TERMS` below, where clause-level negation
#: reaches them - which is what "This is not a price target" needed. They were never wrong about
#: which phrases are jargon; they were wrong that a phrase cannot be mentioned in order to deny it.


# ---------------------------------------------------------------------------------------------
# E7R - decision-leakage semantics (D4-E7R brief §3-§6)
# ---------------------------------------------------------------------------------------------
#
# What E7 prohibits is the PRODUCTION of an investment action or verdict: "buy the stock", "we
# recommend selling", "APPROVE", "my price target is $50". What it does not prohibit is the word.
# D4-BR-C failed on "there was neither a run-up nor a sell-off before the event", because the audit
# compiled `\bsell\b` as a whole word and a hyphen is a word boundary; it had already spent a repair
# round on "Nothing here is a valuation, a price target or a decision", because the unambiguous list
# matched a sentence that DENIES producing one. Two instances of one defect: a lexical hit read as an
# assertion.
#
# The repair inverts the direction. Instead of matching a bare token and then looking for an excuse,
# each family matches the SHAPE of a decision - an imperative, a recommendation frame, a verdict
# token, a price directive - and market or business vocabulary never has that shape. "sell-off",
# "buyback", "selling pressure", "the board approved the transaction" and "customer purchase" are not
# excused by a rule; they are not matched in the first place.
#
# Negation is read PER CLAUSE, which is the convention D4-BR §G already froze: a disclaimer behind a
# semicolon does not reach the clause above it. That is what makes "Do not sell; buy instead" a BUY
# violation while "This is not a recommendation to buy" is nothing at all - and it is why a blanket
# negation exemption is not what this implements.

_CLAUSE_BOUNDARY = re.compile(r"(?:[.!?;:]|\n)+")

_NEGATION_CUE = re.compile(
    r"\b(?:not|n't|no|nor|neither|never|nothing|none|without|cannot|"
    r"excludes?|exclusive of|absent|rather than|instead of)\b", re.IGNORECASE)
"""A denial cue. It only counts when it sits BEFORE the matched term in the SAME clause, so a
trailing disclaimer cannot retroactively excuse an assertion made earlier in the sentence."""

#: What the action has to be done to, when a family needs an object. Deliberately the security and
#: not the company: "customers buy replacement parts" and "the company sells HVAC equipment" are
#: ordinary business prose and are the D3-pilot false positives `schema.py` already documents.
_SECURITY_WORD = re.compile(
    r"\b(?:stock|shares?|equity|share price|position|holding|security|securities|ticker)\b",
    re.IGNORECASE)

#: An action verb at the START of a clause is an instruction. `(?!-)` is the whole point of this
#: module's existence: it stops "sell-off", "sell-side", "buy-side" and "buy-back" from being read as
#: the verb they are not. Gerunds and plurals are excluded by the word boundary itself - "selling",
#: "sells" and "sold" never match `\bsell\b` - and are reached through the recommendation frame
#: instead, where an actual recommender is named.
_ACTION_IMPERATIVE = re.compile(
    r"^\s*(?:please\s+)?(buy|sell|short|accumulate|trim|exit|avoid)\b(?!-)", re.IGNORECASE)

#: A named party being told to transact. "you should buy", "investors should sell the position".
_ACTION_PRESCRIPTION = re.compile(
    r"\b(?:you|we|i|investors?|readers?|clients?|one)\s+(?:should|ought\s+to|must|need\s+to)\s+"
    r"(?:buy|sell|short|accumulate|trim|exit|avoid|hold)\b(?!-)", re.IGNORECASE)

#: A recommendation whose object is a transaction. The verb is matched with `\w*` so "selling" and
#: "buying" are reached here, where the frame already establishes that somebody is recommending.
_ACTION_RECOMMENDATION = re.compile(
    r"\b(?:i|we|you|investors?|one)\s+(?:would\s+|strongly\s+|therefore\s+)?"
    r"(?:recommend|advise|urge|suggest)\w*\s+(?:that\s+\w+\s+|\w+\s+)?"
    r"(?:buy|sell|short|exit|accumulate|trim|avoid)\w*", re.IGNORECASE)

#: Analyst rating vocabulary. A rating is a verdict however it is phrased.
_ACTION_RATING = re.compile(
    r"\b(?:strong\s+(?:buy|sell)|(?:buy|sell)\s+(?:rating|recommendation)|"
    r"(?:rate|rated|rating\s+of)\s+(?:it\s+)?(?:a\s+)?(?:buy|sell))\b", re.IGNORECASE)

#: The D6 decision enum leaking into a D4 output. Case-SENSITIVE and uppercase on purpose: the
#: lowercase verbs are ordinary corporate prose ("the board approved", "shareholders rejected",
#: "we watch the metric") and are handled by `_APPROVE_SAFE`/`_APPROVE_OPINION`. An uppercase
#: APPROVE / WATCH / REJECT is the token, not the word.
_DECISION_TOKEN = re.compile(r"\b(?:APPROVE|APPROVED|WATCH|REJECT|REJECTED)\b")

#: A price directive. The price has to follow the term immediately, so "the segment's exit from
#: Europe cost $40 million" is not an entry/exit level.
_PRICE_DIRECTIVE = re.compile(
    r"\b(?:entry|exit|stop|target)\s+(?:is|at|was|of|near|around|price\s+(?:is|of))\s*\$|"
    r"\bstop[\s-]?loss\b|\btake[\s-]?profit\b", re.IGNORECASE)

#: A bare declarative valuation of the security. "Fair value is $60." is an opinion; "The fair value
#: was $5.0 million as of December 31" is an accounting disclosure, and only the present-tense
#: clause-initial form is matched so filing prose stays out. Checked AFTER the GAAP safe markers,
#: which keep precedence.
_VALUE_DECLARATION = re.compile(
    r"^\s*(?:the\s+|our\s+|my\s+)?(?:fair|intrinsic)\s+value\s+(?:is|of)\s*\$", re.IGNORECASE)

#: "intrinsic value" is also GAAP's term for share-based compensation. Safe when it is attached to
#: an award, a violation when it values the security.
_IV_SAFE = re.compile(
    r"\b(?:option|award|rsu|sar|warrant|exercis\w+|vest\w+|grant\w+|unvested|outstanding)\b",
    re.IGNORECASE)

#: Price-level opinions about the security. "expensive to manufacture" has no security in its clause.
_PRICE_OPINION_TERM = re.compile(r"\b(?:cheap|expensive|attractive)\b", re.IGNORECASE)

#: Whole-phrase jargon that has no non-decision reading at all. Unlike the pre-E7R list, these are
#: still subject to clause-level negation, which is what "This is not a price target" needed.
_JARGON_TERMS = (
    r"\bprice target\b", r"\btarget price\b", r"\bprice objective\b", r"\bfair value target\b",
    r"\bentry zone\b", r"\battractive entry\b", r"\bposition siz\w+\b", r"\bportfolio weight\b",
    r"\bundervalued\b", r"\bovervalued\b", r"\bwe recommend\b", r"\bconviction score\b",
    r"\bexpectation gap (?:is )?positive\b", r"\bexpectation gap (?:is )?negative\b",
)
_JARGON_RE = re.compile("|".join(_JARGON_TERMS), re.IGNORECASE)


def _clauses(text: str) -> list[tuple[int, str]]:
    """`(offset, clause)` for each clause. Commas are NOT boundaries: "Nothing here is a valuation,
    a price target or a decision" is one denial, and splitting it would strand the cue."""
    spans: list[tuple[int, str]] = []
    start = 0
    for match in _CLAUSE_BOUNDARY.finditer(text):
        spans.append((start, text[start:match.start()]))
        start = match.end()
    spans.append((start, text[start:]))
    return [(offset, clause) for offset, clause in spans if clause.strip()]


def _is_negated(clause: str, term_start: int) -> bool:
    """A cue before the term, in this clause. Nothing after the term can excuse it."""
    return any(m.start() < term_start for m in _NEGATION_CUE.finditer(clause))


def _denied_in_clause(text: str, term_start: int) -> bool:
    """Whether the clause containing `term_start` denies it, for the two families that predate E7R.

    Expressed over absolute offsets because `classify_investment_language` scans the whole text for
    those two rather than clause by clause, and rewriting their window logic would change verdicts
    D3.2 §G measured on real filings.
    """
    for offset, clause in _clauses(text):
        if offset <= term_start < offset + len(clause):
            return _is_negated(clause, term_start - offset)
    return False


def _decision_matches(text: str) -> list[LanguageMatch]:
    """Every decision-shaped assertion in `text`, clause by clause.

    A family that matches is a VIOLATION unless the clause denies it. Nothing here returns SAFE or
    UNCLASSIFIED: a shape either is an instruction or was never matched, which is the difference
    between this and a vocabulary list.
    """
    found: list[LanguageMatch] = []

    def _add(clause: str, offset: int, match: re.Match, family: str) -> None:
        if _is_negated(clause, match.start()):
            return
        found.append(LanguageMatch(
            family, (offset + match.start(), offset + match.end()),
            clause.strip()[:200], LanguageVerdict.VIOLATION))

    for offset, clause in _clauses(text):
        for family, pattern in (
            ("investment action", _ACTION_IMPERATIVE),
            ("investment prescription", _ACTION_PRESCRIPTION),
            ("investment recommendation", _ACTION_RECOMMENDATION),
            ("analyst rating", _ACTION_RATING),
            ("decision token", _DECISION_TOKEN),
            ("price directive", _PRICE_DIRECTIVE),
            ("analyst jargon", _JARGON_RE),
        ):
            match = pattern.search(clause)
            if match:
                _add(clause, offset, match, family)

        value = _VALUE_DECLARATION.search(clause)
        if value and not (_FV_SAFE_OBJECT.search(clause) or _FV_SAFE_FRAME.search(clause)
                          or _IV_SAFE.search(clause)):
            _add(clause, offset, value, "valuation of the security")

        for match in _PRICE_OPINION_TERM.finditer(clause):
            if _SECURITY_WORD.search(clause):
                _add(clause, offset, match, "price-level opinion")
                break

        for match in re.finditer(r"\bintrinsic value\b", clause, re.IGNORECASE):
            if not _IV_SAFE.search(clause) and _SECURITY_WORD.search(clause):
                _add(clause, offset, match, "valuation of the security")
                break

    return found


def decision_leakage_findings(text: str) -> tuple[LanguageMatch, ...]:
    """E7's authority: every investment-decision assertion in `text`, and nothing else.

    `audit_strategy_h_v2_d4_1` calls this instead of carrying its own vocabulary, so there is one
    place where "is this a decision?" is answered. It returns only VIOLATION matches - an
    UNCLASSIFIED lexical hit is not a decision leak, and E7's threshold of zero is only meaningful
    if what it counts are assertions.
    """
    return tuple(m for m in classify_investment_language(text)
                 if m.verdict == LanguageVerdict.VIOLATION)


def classify_investment_language(text: str) -> tuple[LanguageMatch, ...]:
    """Every lexical trigger in `text`, each resolved to SAFE / VIOLATION / UNCLASSIFIED.

    A caller that wants a single pass/fail (as R8/prohibited-language ultimately needs) treats
    `VIOLATION` as a hit and `UNCLASSIFIED` as worth a manual look, never as a silent pass - see
    `H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` §G for why "fair value" defaults to SAFE absent an
    opinion marker rather than the reverse.
    """
    matches: list[LanguageMatch] = []
    for m in re.finditer(r"\bfair value\b", text, re.IGNORECASE):
        window = text[max(0, m.start() - 60):m.end() + 60]
        if _FV_OPINION.search(window) and not _denied_in_clause(text, m.start()):
            verdict = LanguageVerdict.VIOLATION
        elif _FV_SAFE_OBJECT.search(window) or _FV_SAFE_FRAME.search(window):
            verdict = LanguageVerdict.SAFE
        else:
            verdict = LanguageVerdict.UNCLASSIFIED
        matches.append(LanguageMatch("fair value", m.span(), window, verdict))
    for m in re.finditer(r"\bapprov\w*\b|\breject\w*\b", text, re.IGNORECASE):
        window = text[max(0, m.start() - 60):m.end() + 60]
        if _APPROVE_OPINION.search(window) and not _denied_in_clause(text, m.start()):
            verdict = LanguageVerdict.VIOLATION
        elif _APPROVE_SAFE.search(window):
            verdict = LanguageVerdict.SAFE
        else:
            continue  # bare "approved the acquisition" etc. - not a lexical trigger at all
        matches.append(LanguageMatch(m.group(0), m.span(), window, verdict))
    matches.extend(_decision_matches(text))
    return tuple(matches)


# ---------------------------------------------------------------------------------------------
# Future Business ontology (brief §17-§20) - documentation table builder, no rule change
# ---------------------------------------------------------------------------------------------
#
# The stage floor itself is NOT redefined here (brief §17: "폐기하거나 느슨하게 만들지 않는다").
# `AUDIT_STAGE_MIN_FLAGS` below is the same table `audit_strategy_h_v2_d3_1.py` already carries,
# restated so this module does not import from the V1 audit script - the two audits must be able to
# disagree about method without one silently depending on the other's constants.
AUDIT_STAGE_MIN_FLAGS: dict[str, int] = {
    "STORY": 0, "EARLY_EVIDENCE": 1, "COMMERCIALIZING": 2, "REAL_BUSINESS": 2, "MATURE": 2,
}
#: Economic meaning of each stage, independent of any repair-rate outcome (brief §19) - what
#: distinguishes them is the kind of commercial evidence they claim, not how often a batch hits one.
STAGE_DEFINITIONS: dict[str, str] = {
    "STORY": "Announcement or stated intent only - no commercial evidence of any kind yet.",
    "EARLY_EVIDENCE": "A prototype, pilot, or an early named-customer validation exists.",
    "COMMERCIALIZING": "A commercial contract, a production ramp, or a customer deployment exists, "
                       "short of durable recurring revenue.",
    "REAL_BUSINESS": "Real recurring revenue, or a material backlog/order book with "
                     "commercialization evidence behind it.",
    "MATURE": "The business line is an established, ongoing part of the company's operations.",
}
FLAG_FIELDS = ("current_revenue_evidence", "order_backlog_evidence", "customer_evidence",
              "capacity_evidence", "margin_evidence")
NEEDS_REVENUE_OR_BACKLOG = {"REAL_BUSINESS", "MATURE"}


@dataclass(frozen=True)
class StageAuditRow:
    ticker: str
    item_index: int
    proposed_stage: str
    flags_present: int
    flags_required: int
    rejection_reason: str
    repaired_stage: str | None
    """None if the candidate never produced a final valid output (e.g. MRVI)."""

    @property
    def repaired_stage_reflects_evidence(self) -> bool | None:
        if self.repaired_stage is None:
            return None
        required = AUDIT_STAGE_MIN_FLAGS.get(self.repaired_stage, 0)
        return self.flags_present >= required
