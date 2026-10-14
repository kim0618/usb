"""H_GUIDANCE_TRANSCRIPTION_V1: deterministic transcription of company guidance, under
``H_D3D4_DETERMINISTIC_PRODUCTION_V2`` (contract section ``guidance_parser``, operator decision B).

D4 §E split guidance into three jobs: code LOCATES (frozen ``GUIDANCE_TERMS``), the AI TRANSCRIBES the
two ranges, code DECIDES the direction (``classify_range_movement``). This module replaces only the
middle job, and only for sentences whose grammar states the numbers explicitly:

* a sentence is read only if the frozen term list locates it, and only in the D2.1 package D4 reads;
* the metric is the label exactly as written (no vocabulary exists in the repository, so none is
  invented: two labels compare only when their normalised text is identical);
* every number keeps its raw text, raw value and scale word next to the normalised value;
* a period that is not stated, a bound that is open ("at least"), a table row, or two different values
  at the same publication time are recorded and never compared;
* the direction is ``classify_range_movement`` through ``MOVEMENT_TO_STATE``, unchanged.

No economic reading happens here: RAISED is arithmetic on two transcribed ranges, nothing more.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.backtest.strategy_h_v2.expectation.gap_contract import GuidanceState
from app.backtest.strategy_h_v2.expectation.guidance_arithmetic import (
    GUIDANCE_TERMS, MOVEMENT_TO_STATE, GuidanceRange, GuidanceUnit, RangeMovement, classify_range_movement)

PARSER = "H_GUIDANCE_TRANSCRIPTION_V1"
PERIOD_UNKNOWN = "GUIDANCE_PERIOD_UNKNOWN"
INCOMPLETE = "INCOMPLETE_BOUND"
MALFORMED = "MALFORMED_RANGE"
CONFLICT = "GUIDANCE_CONFLICT"
INFORMATIVE = frozenset({GuidanceState.RAISED, GuidanceState.LOWERED, GuidanceState.MAINTAINED, GuidanceState.MIXED})

_GUIDANCE_RE = re.compile("|".join(GUIDANCE_TERMS), re.IGNORECASE)

# -- numbers and ranges ---------------------------------------------------------------------------
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9}
_Q = (r"\(?-?[$€£]?\s?\(?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?"
      r"(?:\s?(?:thousand|million|billion|mn|bn)\b|[KMB]\b)?"
      r"(?:\s?(?:%|percent\b|per\s+cent\b|basis\s+points\b|bps\b))?")
_Q_PARTS = re.compile(
    r"(?P<lp>\()?(?P<neg>-)?(?P<cur>[$€£])?\s?(?P<lp2>\()?(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<rp>\))?"
    r"(?:\s?(?P<scale>thousand|million|billion|mn|bn)\b|(?P<scale1>[KMB])\b)?"
    r"(?:\s?(?P<pct>%|percent\b|per\s+cent\b|basis\s+points\b|bps\b))?", re.IGNORECASE)
_DASH = r"\s?[-–—]\s?"
_RANGE = (rf"(?:between\s+{_Q}\s+and\s+{_Q}|from\s+{_Q}\s+to\s+{_Q}|{_Q}\s+to\s+{_Q}|{_Q}{_DASH}{_Q}"
          rf"|(?:approximately|about|around|roughly)\s+{_Q}"
          rf"|(?:at\s+least|up\s+to|more\s+than|less\s+than|in\s+excess\s+of|exceeding|below|above)\s+{_Q}"
          rf"|{_Q})")
_OPEN = re.compile(r"^(?:at\s+least|up\s+to|more\s+than|less\s+than|in\s+excess\s+of|exceeding|below|above)\b",
                   re.IGNORECASE)

_VERB = (r"(?:expects?|anticipates?|projects?|forecasts?|reaffirm(?:s|ed|ing)?|reiterat(?:es|ed|ing)|"
         r"rais(?:es|ed|ing)|lower(?:s|ed|ing)?|updat(?:es|ed|ing)|narrow(?:s|ed|ing)?|increas(?:es|ed|ing)|"
         r"maintain(?:s|ed|ing)?|provid(?:es|ed|ing)|issu(?:es|ed|ing)|guid(?:es|ed|ing))")
_LABEL = r"(?P<label>[A-Za-z0-9][\w&/,'’\-\. ]{1,90}?)"
_PREV_TAIL = (rf"(?:\s*,?\s*(?:up|down)?\s*(?:from|compared\s+(?:to|with)|versus|vs\.?)\s+(?:the\s+|its\s+|our\s+)?"
              rf"(?:prior|previous|earlier)\s+(?:guidance|outlook|range|expectation)s?\s*(?:range\s+)?(?:of\s+)?"
              rf"(?P<prev>{_RANGE}))?")
_TEMPLATES = (
    ("T1_verb_object", re.compile(
        rf"\b{_VERB}\s+(?:its\s+|our\s+|the\s+)?{_LABEL}\s+(?:guidance\s+|outlook\s+)?"
        rf"(?:(?:from\s+(?P<prev_from>{_RANGE})\s+to\s+(?P<cur_from>{_RANGE}))"
        rf"|(?:to\s+be\s+|of\s+|at\s+|in\s+the\s+range\s+of\s+|within\s+a\s+range\s+of\s+|to\s+range\s+(?:from\s+)?|to\s+(?=[$€£(\d])|(?=between\s))"
        rf"(?P<range>{_RANGE}){_PREV_TAIL})", re.IGNORECASE)),
    ("T2_subject_passive", re.compile(
        rf"(?:^|(?<=[\s,;:(]))(?P<label2>[A-Za-z0-9][\w&/,'’\-\. ]{{1,90}}?)\s+(?:guidance\s+)?(?:is|are)\s+(?:now\s+)?"
        rf"(?:expected|anticipated|projected|forecasted|forecast|estimated)\s+to\s+"
        rf"(?:be\s+|range\s+(?:from\s+)?|total\s+|grow\s+(?:by\s+)?|increase\s+(?:by\s+)?|decrease\s+(?:by\s+)?|decline\s+(?:by\s+)?)?"
        rf"(?:approximately\s+(?=[$€£(\d])|about\s+(?=[$€£(\d])|in\s+the\s+range\s+of\s+|(?=between\s))?"
        rf"(?P<range2>{_RANGE}){_PREV_TAIL.replace('(?P<prev>', '(?P<prev2>')}", re.IGNORECASE)),
    ("T3_guidance_noun", re.compile(
        rf"(?:(?:guidance|outlook)\s+for\s+(?:its\s+|our\s+|the\s+)?(?P<label3>[A-Za-z0-9][\w&/,'’\-\. ]{{1,90}}?)\s+"
        rf"(?:of|to\s+be|is|at|to)\s+(?P<range3>{_RANGE}){_PREV_TAIL.replace('(?P<prev>', '(?P<prev3>')})"
        rf"|(?:(?:^|(?<=[\s,;:(]))(?:its\s+|our\s+|the\s+)?(?P<label4>[A-Za-z0-9][\w&/,'’\-\. ]{{1,90}}?)\s+(?:guidance|outlook)\s+"
        rf"(?:of|is|at|range\s+of|to\s+a\s+range\s+of|to)\s+(?P<range4>{_RANGE}){_PREV_TAIL.replace('(?P<prev>', '(?P<prev4>')})",
        re.IGNORECASE)),
)

# -- periods ---------------------------------------------------------------------------------------
_QN = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_PERIOD_PATTERNS = (
    (re.compile(r"\b(first|second|third|fourth)\s+quarter(?:\s+of)?(?:\s+fiscal(?:\s+year)?)?\s+'?(20\d{2})\b", re.I),
     lambda m: f"Q{_QN[m.group(1).lower()]}-{m.group(2)}"),
    (re.compile(r"\b(20\d{2})\s+(first|second|third|fourth)\s+quarter\b", re.I),
     lambda m: f"Q{_QN[m.group(2).lower()]}-{m.group(1)}"),
    (re.compile(r"\bQ([1-4])\s*(?:FY|fiscal)?\s*'?(20\d{2}|\d{2})\b", re.I),
     lambda m: f"Q{m.group(1)}-{m.group(2) if len(m.group(2)) == 4 else '20' + m.group(2)}"),
    (re.compile(r"\b(?:full[- ]year|fiscal(?:\s+year)?|FY)\s*'?(20\d{2}|\d{2})\b", re.I),
     lambda m: f"FY{m.group(1) if len(m.group(1)) == 4 else '20' + m.group(1)}"),
    (re.compile(r"\b(20\d{2})\s+(?:full[- ]year|fiscal\s+year)\b", re.I), lambda m: f"FY{m.group(1)}"),
)
_BARE_YEAR = re.compile(r"\b(20\d{2})\b")
_PERIOD_STRIP = re.compile(
    r"\b(?:(?:first|second|third|fourth)\s+quarter(?:\s+of)?(?:\s+fiscal(?:\s+year)?)?\s+'?20\d{2}|20\d{2}\s+(?:first|second|third|fourth)\s+quarter"
    r"|Q[1-4]\s*(?:FY|fiscal)?\s*'?(?:20\d{2}|\d{2})|(?:full[- ]year|fiscal(?:\s+year)?|FY)\s*'?(?:20\d{2}|\d{2})"
    r"|20\d{2}\s+(?:full[- ]year|fiscal\s+year)|20\d{2}|full[- ]year|fiscal\s+year)\b", re.I)
_DETERMINERS = re.compile(r"^(?:(?:its|our|the|their)\s+)+", re.I)
_TRAILING = re.compile(r"\s+(?:guidance|outlook)$", re.I)
_PER_SHARE = re.compile(r"\b(?:eps|per\s+(?:diluted\s+|basic\s+)?share|earnings\s+per)\b", re.I)
_LABEL_REJECT = re.compile(rf"\b{_VERB}\b|^(?:to|that|which|and|or|we|it|they|company)\b|[.;:]\s", re.I)
#: A label never ends in a template connector (class-D fix 2026-10-14: "raised the full year 2026 guidance to
#: between $775 million and $845 million" was transcribed with the metric "guidance to").
_LABEL_CONNECTOR_END = re.compile(r"\b(?:to|of|at|be|between|from|in|is|are|range)$", re.I)


@dataclass(frozen=True)
class Value:
    raw: str
    raw_value: float
    scale_word: str | None
    normalized: float
    currency: str | None
    unit_word: str | None


@dataclass
class Transcription:
    metric: str
    metric_key: str
    period: str
    template: str
    kind: str                       # RANGE | SINGLE | INCOMPLETE_BOUND | MALFORMED_RANGE
    unit: str
    raw_unit: str | None
    currency: str | None
    low: Value | None
    high: Value | None
    single_value: bool
    previous: dict[str, Any] | None
    source_text: str
    source_id: str
    evidence_id: str
    accession: str | None
    form: str | None
    filing_date: str | None
    published_at: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def family(self) -> tuple:
        return (self.unit, self.raw_unit, self.currency)

    def bounds(self) -> tuple[float, float] | None:
        if self.kind not in ("RANGE", "SINGLE"):
            return None
        return (self.low.normalized, self.high.normalized)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["family"] = list(self.family)
        return out


def _value(raw: str, *, scale_from: str | None = None, pct_from: str | None = None,
           cur_from: str | None = None) -> tuple[Value, str | None] | None:
    m = _Q_PARTS.fullmatch(raw.strip())
    if not m:
        return None
    number = float(m.group("num").replace(",", ""))
    negative = bool(m.group("neg")) or bool((m.group("lp") or m.group("lp2")) and m.group("rp"))
    raw_value = -number if negative else number
    scale_word = m.group("scale") or m.group("scale1") or scale_from
    pct = m.group("pct") or pct_from
    currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(m.group("cur") or "") or cur_from
    factor = _SCALE.get((scale_word or "").lower(), 1.0)
    unit_word = None
    if pct:
        unit_word = "BPS" if pct.lower().startswith(("basis", "bps")) else "PERCENT"
    normalized = raw_value * factor
    if unit_word == "BPS":
        normalized = raw_value / 100.0
    return Value(raw=raw.strip(), raw_value=raw_value, scale_word=scale_word, normalized=normalized,
                 currency=currency, unit_word=unit_word), pct


def _parse_range(text: str) -> dict[str, Any] | None:
    """(kind, low, high, single) from a matched ``_RANGE`` text. Raw strings are slices of ``text``."""
    text = text.strip()
    if _OPEN.match(text):
        rest = _OPEN.sub("", text).strip()
        parsed = _value(rest)
        return None if parsed is None else {"kind": INCOMPLETE, "low": parsed[0], "high": None, "single": False}
    body = re.sub(r"^(?:approximately|about|around|roughly)\s+", "", text, flags=re.I)
    single_marker = body != text
    pair = (re.fullmatch(rf"between\s+({_Q})\s+and\s+({_Q})", body, re.I)
            or re.fullmatch(rf"from\s+({_Q})\s+to\s+({_Q})", body, re.I)
            or re.fullmatch(rf"({_Q})\s+to\s+({_Q})", body, re.I)
            or re.fullmatch(rf"({_Q}){_DASH}({_Q})", body, re.I))
    if pair:
        second = _value(pair.group(2))
        if second is None:
            return None
        hi, pct = second
        first = _value(pair.group(1), scale_from=None, pct_from=None)
        if first is None:
            return None
        lo = first[0]
        # a scale / percent / currency written only on the second number applies to the first
        if lo.scale_word is None and hi.scale_word is not None:
            lo = _value(pair.group(1), scale_from=hi.scale_word, pct_from=pct if lo.unit_word is None else None,
                        cur_from=hi.currency if lo.currency is None else None)[0]
        elif lo.unit_word is None and hi.unit_word is not None:
            lo = _value(pair.group(1), pct_from=pct, cur_from=hi.currency if lo.currency is None else None)[0]
        elif lo.currency is None and hi.currency is not None:
            lo = _value(pair.group(1), cur_from=hi.currency)[0]
        if hi.currency is None and lo.currency is not None:
            hi = _value(pair.group(2), cur_from=lo.currency)[0]
        if hi.normalized < lo.normalized:
            return {"kind": MALFORMED, "low": lo, "high": hi, "single": False}
        return {"kind": "RANGE", "low": lo, "high": hi, "single": False}
    parsed = _value(body)
    if parsed is None:
        return None
    return {"kind": "SINGLE", "low": parsed[0], "high": parsed[0], "single": True, "marker": single_marker}


def _periods(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    covered: list[tuple[int, int]] = []
    for pattern, key in _PERIOD_PATTERNS:
        for m in pattern.finditer(text):
            if any(s <= m.start() < e for s, e in covered):
                continue
            covered.append((m.start(), m.end()))
            found.append((m.start(), key(m)))
    if not found:
        found = [(m.start(), f"YEAR{m.group(1)}") for m in _BARE_YEAR.finditer(text)]
    seen: list[str] = []
    for _, key in sorted(found):
        if key not in seen:
            seen.append(key)
    return seen


def metric_key(label: str) -> str:
    text = _PERIOD_STRIP.sub(" ", label)
    text = " ".join(text.replace("’", "'").split()).strip(" ,-")
    text = _DETERMINERS.sub("", text)
    text = _TRAILING.sub("", text)
    return " ".join(text.lower().split()).strip(" ,-")


def _unit(low: Value, label: str, raw_after: str) -> tuple[str, str | None, str | None]:
    if low.unit_word == "PERCENT":
        return GuidanceUnit.PERCENT.value, "%", None
    if low.unit_word == "BPS":
        return "BPS", "bps", None
    if low.currency == "USD":
        return (GuidanceUnit.USD_PER_SHARE.value if _PER_SHARE.search(label) else GuidanceUnit.USD.value), None, "USD"
    if low.currency is not None:
        return GuidanceUnit.OTHER.value, None, low.currency
    word = re.match(r"\s*([A-Za-z]+)", raw_after)
    return GuidanceUnit.OTHER.value, (word.group(1).lower() if word else None), None


def _sentences(chunk_text: str) -> Iterable[tuple[str, bool]]:
    """(sentence, is_table_line) in document order; NBSP folded, whitespace collapsed."""
    for line in chunk_text.split("\n"):
        line = " ".join(line.replace("\xa0", " ").split())
        if not line:
            continue
        if "|" in line:
            yield line, True
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(\"“])", line):
            yield sentence, False


def transcribe_sentence(sentence: str, *, meta: dict[str, Any]) -> list[Transcription]:
    out: list[Transcription] = []
    taken: list[tuple[int, int]] = []
    for name, pattern in _TEMPLATES:
        for m in pattern.finditer(sentence):
            groups = {k: v for k, v in m.groupdict().items() if v is not None}
            label = next((groups[k] for k in ("label", "label2", "label3", "label4") if k in groups), None)
            cur_text = next((groups[k] for k in ("cur_from", "range", "range2", "range3", "range4") if k in groups), None)
            prev_text = next((groups[k] for k in ("prev_from", "prev", "prev2", "prev3", "prev4") if k in groups), None)
            if label is None or cur_text is None:
                continue
            span = m.span()
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            label = label.strip(" ,")
            words = label.split()
            if (not words or len(words) > 10 or _LABEL_REJECT.search(label) or _LABEL_CONNECTOR_END.search(label)
                    or not re.search(r"[A-Za-z]", label)):
                continue
            key = metric_key(label)
            if not key or not re.search(r"[a-z]", key):
                continue
            cur = _parse_range(cur_text)
            if cur is None:
                continue
            label_periods = _periods(label)
            sentence_periods = _periods(sentence)
            period = (label_periods[0] if len(label_periods) == 1 else
                      sentence_periods[0] if not label_periods and len(sentence_periods) == 1 else PERIOD_UNKNOWN)
            after = sentence[m.end(next(k for k in ("cur_from", "range", "range2", "range3", "range4") if k in groups)):]
            unit, raw_unit, currency = _unit(cur["low"], label, after)
            previous = None
            if prev_text:
                prev = _parse_range(prev_text)
                if prev is not None:
                    p_unit = _unit(prev["low"], label, "")
                    previous = {"kind": prev["kind"], "raw": prev_text.strip(), "unit": p_unit[0],
                                "raw_unit": p_unit[1] if p_unit[1] is not None else raw_unit, "currency": p_unit[2],
                                "low": asdict(prev["low"]) if prev["low"] else None,
                                "high": asdict(prev["high"]) if prev["high"] else None}
            out.append(Transcription(
                metric=label, metric_key=key, period=period, template=name, kind=cur["kind"], unit=unit,
                raw_unit=raw_unit, currency=currency, low=cur["low"], high=cur["high"],
                single_value=bool(cur.get("single")), previous=previous, source_text=sentence, **meta))
            taken.append(span)
    return out


def transcribe(package: Any) -> dict[str, Any]:
    """Every transcription in the package's PIT-eligible chunks, plus what was located and not read."""
    sources = {s.source_id: s for s in package.source_manifest}
    cutoff = package.evidence_bundle.data_cutoff
    rows: list[Transcription] = []
    table_lines = located = 0
    for chunk in sorted(package.chunks, key=lambda c: (c.source_id, c.chunk_index)):
        if not chunk.pit_eligible or (chunk.available_at is not None and chunk.available_at > cutoff):
            continue
        root = ":".join(chunk.source_id.split(":")[:3])
        source = sources.get(chunk.source_id) or sources.get(root)
        title = str(getattr(source, "title", "") or "")
        meta = {"source_id": chunk.source_id, "evidence_id": chunk.evidence_id,
                "accession": getattr(source, "accession", None) or (root.split(":")[2] if root.count(":") == 2 else None),
                "form": title.split(" ", 1)[0] if title else None,
                "filing_date": chunk.published_at.date().isoformat() if chunk.published_at else None,
                "published_at": chunk.published_at.isoformat() if chunk.published_at else None}
        for sentence, is_table in _sentences(chunk.text):
            if not _GUIDANCE_RE.search(sentence):
                continue
            located += 1
            if is_table:
                table_lines += 1
                continue
            rows.extend(transcribe_sentence(sentence, meta=meta))
    return {"parser": PARSER, "sentences_located": located, "table_lines_with_guidance_terms": table_lines,
            "transcriptions": rows}


def _distinct(values: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _previous_bounds(t: Transcription) -> tuple[float, float] | None:
    p = t.previous
    if not p or p["kind"] not in ("RANGE", "SINGLE"):
        return None
    if (p["unit"], p["raw_unit"], p["currency"]) != t.family:
        return None
    return (p["low"]["normalized"], p["high"]["normalized"])


def assess(transcriptions: list[Transcription]) -> list[dict[str, Any]]:
    """Per (metric key, period, unit family): current vs previous and the code-owned state."""
    groups: dict[tuple, list[Transcription]] = {}
    for t in transcriptions:
        if t.period == PERIOD_UNKNOWN:
            continue
        groups.setdefault((t.metric_key, t.period) + t.family, []).append(t)
    out = []
    for key, rows in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        times = sorted({r.published_at or "" for r in rows}, reverse=True)
        current_rows = [r for r in rows if (r.published_at or "") == times[0]]
        record: dict[str, Any] = {"metric_key": key[0], "period": key[1], "unit": key[2], "raw_unit": key[3],
                                  "currency": key[4], "metric_labels": sorted({r.metric for r in rows}),
                                  "current_evidence_ids": sorted({r.evidence_id for r in current_rows}),
                                  "previous_evidence_ids": [], "reasons": []}
        cur_bounds = [r.bounds() for r in current_rows]
        if any(b is None for b in cur_bounds):
            record["reasons"].append(f"CURRENT_{'/'.join(sorted({r.kind for r in current_rows if r.bounds() is None}))}")
        cur = _distinct(b for b in cur_bounds if b is not None)
        stated_prev = _distinct(b for b in (_previous_bounds(r) for r in current_rows) if b is not None)
        prev: list[tuple[float, float]] = []
        if stated_prev:
            prev = stated_prev
            record["previous_source"] = "IN_SENTENCE"
            record["previous_evidence_ids"] = sorted({r.evidence_id for r in current_rows if _previous_bounds(r)})
        elif len(times) > 1:
            prev_rows = [r for r in rows if (r.published_at or "") == times[1]]
            if any(r.bounds() is None for r in prev_rows):
                record["reasons"].append("PREVIOUS_" + "/".join(sorted({r.kind for r in prev_rows if r.bounds() is None})))
            prev = _distinct(b for b in (r.bounds() for r in prev_rows) if b is not None)
            record["previous_source"] = "EARLIER_PUBLICATION"
            record["previous_evidence_ids"] = sorted({r.evidence_id for r in prev_rows})
        record["current"] = [list(b) for b in cur]
        record["previous"] = [list(b) for b in prev]
        if len(cur) > 1 or len(prev) > 1:
            record["reasons"].append(CONFLICT)
            state = GuidanceState.UNKNOWN
        elif not cur or not prev:
            record["reasons"].append("ONE_SIDED" if (cur or prev) else "NO_COMPLETE_BOUNDS")
            state = GuidanceState.UNKNOWN
        else:
            unit = GuidanceUnit(key[2]) if key[2] in GuidanceUnit.__members__ else GuidanceUnit.OTHER
            comparison = classify_range_movement(GuidanceRange(prev[0][0], prev[0][1], unit),
                                                 GuidanceRange(cur[0][0], cur[0][1], unit))
            record["movement"] = comparison.to_dict()
            state = MOVEMENT_TO_STATE.get(comparison.movement, GuidanceState.UNKNOWN)
            if comparison.movement is RangeMovement.NOT_COMPARABLE:
                record["reasons"].append("NOT_COMPARABLE")
        record["state"] = state.value
        out.append(record)
    return out


def guidance_evidence(package: Any) -> dict[str, Any]:
    found = transcribe(package)
    rows: list[Transcription] = found["transcriptions"]
    assessments = assess(rows)
    states = [a["state"] for a in assessments]
    return {
        "parser": PARSER, "sentences_located": found["sentences_located"],
        "table_lines_with_guidance_terms": found["table_lines_with_guidance_terms"],
        "transcriptions": [r.to_dict() for r in rows],
        "period_unknown": sum(r.period == PERIOD_UNKNOWN for r in rows),
        "incomplete_or_malformed": sum(r.kind in (INCOMPLETE, MALFORMED) for r in rows),
        "assessments": assessments,
        "state_counts": {s: states.count(s) for s in sorted(set(states))},
        "non_price_items": sum(GuidanceState(s) in INFORMATIVE for s in states),
        "result_vs_guidance": "NOT_PRODUCED (reported-actual transcription is outside operator decision B)",
    }
