"""D4.3R M8 repair: what a digit in a claim's text is FOR, before asking whether it agrees with a
code-owned fact.

D4.3A's audit found six false positives (`H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md` M8
diagnosis): claims that cite a `RETURN_FRACTION` code fact and restate no number for it at all, but
whose text happens to contain a digit for something else entirely - "Q2", "2Q26", "63 sessions",
"C1", "6-month". The old `_matches_fact` treated every digit sequence in the sentence as a
candidate restatement of the cited fact, so a session count or a fiscal-period label that never
matched the fact's value was read as a MISMATCH rather than as "not a restatement in the first
place". M8's own requirement (§6 of the D4.3R brief) is "no claim cites a code-owned fact while
stating a DIFFERENT number" - a claim stating no number for the fact at all does not meet that
description regardless of what other digits its sentence contains.

This module answers "does this text actually restate the code-owned value" as two separate steps:
which numbers in the text play the VALUE_RESTATEMENT role (candidates for comparison) versus a role
that carries no fact-comparison weight at all (a fiscal-period label, a session count, an
identifier, a date, a range bound). Only VALUE_RESTATEMENT tokens are ever compared to the fact.

No new number parser. `parse_numeric_tokens` (D3.2's `research/validation_v2.py`) already extracts
currency/percent/bps/multiple/share tokens with the file-format tolerance the batch audits found
necessary (thousands separators, scale words, table-cell whitespace) and already excludes ordinary
filing dates and `Q[1-4]`/`FY####`/`10-K` style labels before a bare-digit scan ever runs. This
module reuses that tokenizer unchanged and adds the additional exclusions D3.2 had no reason to
know about, because D3.2 is about matching a claim's number against the EVIDENCE CHUNK it cites,
never against a D4 code fact: a reversed fiscal-period label ("2Q26"), a session/day/month count
describing a WINDOW rather than a value ("63 sessions", "6-month"), and a gate/rule/stage identifier
("C1", "M8", "D4.3A").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from app.backtest.strategy_h_v2.research.validation_v2 import (
    DATE_RE,
    NumericToken,
    NumericUnit,
    parse_numeric_tokens,
)


class NumericRole(StrEnum):
    VALUE_RESTATEMENT = "VALUE_RESTATEMENT"
    """A candidate restatement of a code-owned fact's value - the only role ever compared."""
    FISCAL_PERIOD = "FISCAL_PERIOD"
    """"Q2", "2Q26", "FY2026" - a period label, not a value."""
    SESSION_COUNT = "SESSION_COUNT"
    """"63 sessions", "20-session", "6-month" - the size of a window, not the fact computed over
    it."""
    DATE = "DATE"
    """A calendar date ("August 18, 2026")."""
    IDENTIFIER = "IDENTIFIER"
    """A gate, rule or stage code ("C1", "M8", "D4.3A") - the digit names a rule, not a quantity."""
    RANGE = "RANGE"
    """One bound of a two-sided range ("20-30%", "\\$5.0-9.0 million") - a range states a bound,
    not the fact's single value, so neither side is compared."""
    OTHER = "OTHER"
    """A bare token this module has no reason to treat as a restatement candidate - currently just
    a bare year, D3.2's own `NumericUnit.UNKNOWN`."""


#: The forward, ordinary form ("Q2", "FY26") D3.2's `LABEL_RE` already excludes from tokenization
#: entirely, so it never reaches this module as a token. Restated here as its own pattern only for
#: role REPORTING - it is masked out before the D3.2 tokenizer runs, exactly like every other
#: excluded role below, so the two can never disagree about whether it counts as a value.
_FORWARD_FISCAL_PERIOD = re.compile(r"\bQ[1-4]\b|\bH[1-2]\b|\bFY\s?\d{2,4}\b", re.IGNORECASE)

#: The reversed form ("2Q26", "4Q25") D3.2 has no reason to know about: D3.2 matches a claim's
#: number against its own cited filing chunk, and a filing does not usually label a quarter this
#: way. D4.3A's own SCCO false positive is exactly this - "2Q26" read as the bare digits 2 and 26.
_REVERSED_FISCAL_PERIOD = re.compile(r"\b[1-4]Q\d{2,4}\b", re.IGNORECASE)

#: A count of sessions, trading days, or a month/day/year window LENGTH - "63 sessions",
#: "20-session", "6-month", "3-day". Not a fact value: D4's own code facts documentation
#: (`code_facts.py`) labels its return windows by session count ("candidate return over the last
#: 63 sessions"), so a claim describing the same window in prose necessarily contains that same
#: number, and it is not the number the fact evaluates to.
_SESSION_COUNT = re.compile(
    r"\b\d+(?:\.\d+)?[\s-](?:trading[\s-])?sessions?\b"
    r"|\b\d+(?:\.\d+)?-(?:day|month|year)s?\b",
    re.IGNORECASE,
)

#: A gate, rule or stage code: a single letter from a known D4/H-V2 family directly followed by a
#: digit with no separating space - "C1" (contract rule), "M8" (mechanical gate), "E5" (D4.1 gate),
#: "D4" (stage). Never separated from the letter by whitespace, which is what keeps this from
#: matching an ordinary quantity like "D 4" or "over 4 days".
_IDENTIFIER = re.compile(r"\b[CEMD]\d+(?:\.\d+[A-Z]?)?\b")

#: Two numbers joined by a hyphen, en-dash or "to", both already recognized as a value by the D3.2
#: tokenizer (so this never invents a number the tokenizer would not otherwise have found) - "20-30
#: %", "\$5.0-9.0 million". Marked only once both spans are known, in `_mark_ranges` below, because
#: it is a relationship between two tokens rather than a property of either span alone.
_RANGE_JOIN = re.compile(r"\s*(?:-|–|~|\bto\b)\s*")


@dataclass(frozen=True)
class NumericRoleFinding:
    span: tuple[int, int]
    raw: str
    role: NumericRole
    token: NumericToken | None = None
    """Set only for VALUE_RESTATEMENT/RANGE/OTHER - the roles the D3.2 tokenizer itself produced a
    token for. The regex-only exclusion roles (FISCAL_PERIOD, SESSION_COUNT, DATE, IDENTIFIER) have
    no D3.2 token because their digits are masked out before the tokenizer ever runs."""


def _mask_digits(text: str, spans: list[tuple[int, int]]) -> str:
    """Replace every digit in each span with `#`, leaving length and every other character (units,
    punctuation, surrounding words) untouched so the D3.2 tokenizer's own offsets stay valid for
    whatever is left."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            if chars[i].isdigit():
                chars[i] = "#"
    return "".join(chars)


def _mark_ranges(tokens: list[NumericToken], text: str) -> set[tuple[int, int]]:
    """Which D3.2 tokens are one bound of a two-sided range, by checking whether the text between
    two adjacent tokens is exactly a range join and nothing else."""
    ranged: set[tuple[int, int]] = set()
    ordered = sorted(tokens, key=lambda t: t.span[0])
    for left, right in zip(ordered, ordered[1:]):
        between = text[left.span[1]:right.span[0]]
        if _RANGE_JOIN.fullmatch(between):
            ranged.add(left.span)
            ranged.add(right.span)
    return ranged


def classify_roles(text: str) -> list[NumericRoleFinding]:
    """Every numeric span in `text`, labelled with its role. Only VALUE_RESTATEMENT is ever a
    candidate for a code-fact equality check; every other role is reported for visibility but
    carries no comparison weight."""
    text = text or ""
    excluded: list[tuple[int, int, NumericRole]] = []
    for pattern, role in (
        (DATE_RE, NumericRole.DATE),
        (_FORWARD_FISCAL_PERIOD, NumericRole.FISCAL_PERIOD),
        (_REVERSED_FISCAL_PERIOD, NumericRole.FISCAL_PERIOD),
        (_SESSION_COUNT, NumericRole.SESSION_COUNT),
        (_IDENTIFIER, NumericRole.IDENTIFIER),
    ):
        for m in pattern.finditer(text):
            excluded.append((m.start(), m.end(), role))

    masked = _mask_digits(text, [(s, e) for s, e, _ in excluded])
    tokens = parse_numeric_tokens(masked)
    ranged = _mark_ranges(tokens, text)

    findings: list[NumericRoleFinding] = [
        NumericRoleFinding((s, e), text[s:e], role) for s, e, role in excluded
    ]
    for tok in tokens:
        if tok.span in ranged:
            role = NumericRole.RANGE
        elif tok.unit == NumericUnit.UNKNOWN:
            role = NumericRole.OTHER
        else:
            role = NumericRole.VALUE_RESTATEMENT
        findings.append(NumericRoleFinding(tok.span, tok.raw, role, tok))
    return sorted(findings, key=lambda f: f.span[0])


#: Same scale expansion `_matches_fact` used, kept here rather than reinvented (brief §8): a
#: RETURN_FRACTION or ANNUALIZED_STDEV fact is commonly restated as a percentage (multiply by 100),
#: and a USD fact as millions/billions/thousands.
#:
#: Each candidate carries its OWN tolerance rather than sharing one formula across every scale.
#: The raw, unscaled candidate (a bare fraction like "0.123") needs a tight, ~1%-relative tolerance
#: - it is the fact's own value at 4-decimal precision, and a 0.05 ABSOLUTE tolerance (fine for a
#: percentage-scale candidate around 10-90) would call "0.123" and "0.150" the same restatement,
#: which they are not. The scaled candidates (percentage-form, USD-scale) keep the wider 0.05 floor
#: they always had, because those are routinely reported to only 1-2 decimals.
def _fact_candidates(value: float, unit: str) -> list[tuple[float, float]]:
    v = abs(value)
    raw = round(v, 4)
    candidates: list[tuple[float, float]] = [(raw, max(0.0005, raw * 0.01))]
    if unit == "RETURN_FRACTION":
        for pct in (round(v * 100, 1), round(v * 100, 2)):
            candidates.append((pct, max(0.05, pct * 0.01)))
    elif unit == "ANNUALIZED_STDEV":
        pct = round(v * 100, 1)
        candidates.append((pct, max(0.05, pct * 0.01)))
    elif unit == "USD":
        for scale in (1e9, 1e6, 1e3):
            for scaled in (round(v / scale, 1), round(v / scale, 2)):
                candidates.append((scaled, max(0.05, scaled * 0.01)))
    return candidates


def _token_values(token: NumericToken, text: str) -> set[float]:
    """The reading(s) a VALUE_RESTATEMENT token could plausibly mean. A USD token may carry a
    million/billion/thousand scale word right after it (`scaled_value`); every other unit is
    compared at face value - a PERCENT token's `12.3` already means 12.3%, comparable directly to
    `_fact_candidates`'s `* 100` entries, and a bare COUNT token may be the fraction itself
    (brief §9's "return was 0.123")."""
    values = {token.value}
    if token.unit == NumericUnit.USD:
        values.add(token.scaled_value(text))
    return values


def fact_is_restated(text: str, value: object, unit: str) -> bool:
    """Does `text` restate the code-owned fact `value` (of `unit`), or state a different number for
    it? True also when `text` states no number for the fact at all - a claim that is purely
    qualitative about a code-owned fact has not violated numeric ownership, whatever OTHER digits
    (a fiscal period, a session count, a rule id) its sentence happens to contain.
    """
    if unit == "STATE_TOKEN":
        return str(value).upper() in text.upper() or not any(c.isdigit() for c in text)
    if not isinstance(value, (int, float)):
        return True
    restatements = [f.token for f in classify_roles(text)
                    if f.role == NumericRole.VALUE_RESTATEMENT and f.token is not None]
    if not restatements:
        return True
    candidates = _fact_candidates(float(value), unit)
    for token in restatements:
        for v in _token_values(token, text):
            for c, tolerance in candidates:
                if abs(abs(v) - c) <= tolerance:
                    return True
    return False
