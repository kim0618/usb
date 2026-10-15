"""H_GUIDANCE_TRANSCRIPTION_V2: structured-context guidance transcription, under
``H_D3D4_DETERMINISTIC_PRODUCTION_V3`` (contract section ``guidance_parser_v2``).

V1 (``guidance_parser``) read prose sentences only. On the stored corpus it transcribed 3 values and
paired none, because companies state guidance in outlook sections: a heading that carries the period
("2026 Guidance", "FY2027 Outlook"), then bullets, table rows or label/value lines that do not repeat it.
V2 reads that structure, still as transcription only:

* **scope**: a guidance scope opens at a heading-like line (or a table header row) that the frozen
  ``GUIDANCE_TERMS`` locate; its period is the single period that heading states. A subheading that
  states one period replaces it; a heading with two periods leaves it ambiguous. The scope ends at the
  next guidance heading, at a prose line the term list does not locate, or at the end of the source.
  Nothing is inherited from further back than the enclosing scope.
* **inside a scope**: bullets, table data rows and label/value line pairs whose values carry a currency,
  percent or basis-point marker are transcribed; a row whose values cannot be assigned to one column
  period without guessing is recorded as ``GUIDANCE_PERIOD_UNKNOWN``.
* **prose**: V1's templates, plus a period stated right after a range ("for the third quarter of 2026"),
  coordinated ranges and list continuations in the same sentence, plus the scope period when the
  sentence states none.
* the metric is the label as written; the comparison key adds only lexical normalisation (Adj. ->
  adjusted, revenues -> revenue, & -> and, footnote markers and punctuation removed);
* a bare-year period ("2026 Outlook") equals the fiscal year when the issuer's SEC fiscal year end is
  12/31 (submissions ``fiscalYearEnd``), and is kept apart otherwise.

Direction is V1's ``assess`` (``classify_range_movement`` via ``MOVEMENT_TO_STATE``), unchanged.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable, Mapping

from app.strategies.h_auto import guidance_parser as V1
from app.strategies.h_auto.guidance_parser import (
    CONFLICT, INCOMPLETE, INFORMATIVE, MALFORMED, PERIOD_UNKNOWN, Transcription, _GUIDANCE_RE, _Q, _RANGE,
    _parse_range, _periods, _unit)

from app.backtest.strategy_h_v2.expectation.gap_contract import GuidanceState

PARSER = "H_GUIDANCE_TRANSCRIPTION_V2"
AMBIGUOUS_COLUMN = "AMBIGUOUS_COLUMN"
BULLETS = frozenset("•●·▪■◦‣–—-*o")

_RANGE_RE = re.compile(_RANGE, re.IGNORECASE)
_MARKED = re.compile(r"[$€£%]|\bpercent\b|\bper\s+cent\b|\bbasis\s+points\b|\bbps\b", re.IGNORECASE)
_PERIOD_STRIP = V1._PERIOD_STRIP
_FOOTNOTE = re.compile(r"\(\s*\d{1,2}\s*\)|\[\s*\d{1,2}\s*\]|\*+")
_DIGIT = re.compile(r"\d")
_LETTER = re.compile(r"[A-Za-z]")
_TRAIL_PERIOD = re.compile(r"^\s*(?:for|in)\s+(?:the\s+)?(?P<phrase>[^,;]+?)(?=$|[,;.]|\s+and\s)", re.IGNORECASE)
_CONT = re.compile(
    rf"^\s*,?\s*(?:and\s+)?(?:(?P<label>[A-Za-z][\w&/'’\-\. ]{{1,60}}?)\s+(?:of|in\s+the\s+range\s+of|to\s+be|at)\s+"
    rf"|(?=between\s))(?P<range>{_RANGE})", re.IGNORECASE)
_BULLET_BODY = re.compile(
    rf"^(?P<label>[A-Za-z][\w&/,'’\-\.() ]{{0,90}}?)\s*(?::|—|–|-|\bof\b|\bin\s+the\s+range\s+of\b|"
    rf"\bto\s+be\b|\bat\b|\bis\b|\bare\b)?\s*(?P<range>{_RANGE})(?P<tail>.*)$", re.IGNORECASE)
_LEXICAL = ((re.compile(r"\badj\b\.?", re.I), "adjusted"), (re.compile(r"\brevenues\b", re.I), "revenue"),
            (re.compile(r"&"), " and "))


def metric_key(label: str) -> str:
    text = _FOOTNOTE.sub(" ", label)
    for pattern, repl in _LEXICAL:
        text = pattern.sub(repl, text)
    key = V1.metric_key(text)
    key = re.sub(r"[^a-z0-9 ]+", " ", key)
    return " ".join(key.split())


def _single_period(text: str) -> str | None:
    found = _periods(text)
    return found[0] if len(found) == 1 else None


_QUARTER_WORD = re.compile(r"\b(?:first|second|third|fourth)\s+quarter\b|\bQ[1-4]\b", re.IGNORECASE)


def _heading_period(text: str) -> str | None:
    """A heading's scope period: its single stated period; none when it states two, or when it also names
    a quarter without a year ("Third Quarter and Full Year 2026 Outlook" covers two periods)."""
    period = _single_period(text)
    if period and not period.startswith("Q") and _QUARTER_WORD.search(text):
        return None
    return period


_YEAR_VALUE = re.compile(r"(?:19|20)\d{2}")


def _is_year_value(t: Transcription) -> bool:
    """Class-D fix D-2 (2026-10-15 replay, WEN 10-Q: '... throughout the remainder of 2026' was transcribed
    as a guidance value of 2026): a bare four-digit year with no currency, percent, bps or scale is a period
    expression, never a value."""
    return t.low is not None and t.currency is None and t.low.unit_word is None and t.low.scale_word is None \
        and bool(_YEAR_VALUE.fullmatch(t.low.raw.strip()))


def _value_text_ok(text: str) -> bool:
    return bool(_MARKED.search(text))


_ABBREVIATION = re.compile(r"\b([A-Za-z]{1,4})\.(?=\s)")


def _label_ok(label: str) -> bool:
    words = label.split()
    plain = _ABBREVIATION.sub(r"\1", label)                    # "Adj. EBITDA" is a label, not two sentences
    return (bool(words) and len(words) <= 10 and bool(_LETTER.search(label)) and not V1._LABEL_REJECT.search(plain)
            and not V1._LABEL_CONNECTOR_END.search(label) and bool(metric_key(label)))


def _make(label: str, cur: Mapping[str, Any], *, period: str, template: str, source_text: str,
          meta: Mapping[str, Any], after: str = "", notes: Iterable[str] = ()) -> Transcription:
    unit, raw_unit, currency = _unit(cur["low"], label, after)
    return Transcription(metric=label, metric_key=metric_key(label), period=period, template=template,
                         kind=cur["kind"], unit=unit, raw_unit=raw_unit, currency=currency, low=cur["low"],
                         high=cur["high"], single_value=bool(cur.get("single")), previous=None,
                         source_text=source_text, notes=list(notes), **meta)


# -- prose --------------------------------------------------------------------------------------------

def transcribe_prose(sentence: str, *, meta: Mapping[str, Any], scope_period: str | None) -> list[Transcription]:
    """V1 templates, then the V2 sentence extensions (trailing period, coordination, scope period)."""
    out: list[Transcription] = []
    for t in V1.transcribe_sentence(sentence, meta=dict(meta)):
        if _is_year_value(t):
            continue
        start = sentence.find(t.low.raw) if t.low else -1
        tail_from = sentence.find(t.high.raw, start) + len(t.high.raw) if t.high and start >= 0 else -1
        period = t.period
        notes = list(t.notes)
        if tail_from >= 0:
            trail = _TRAIL_PERIOD.match(sentence[tail_from:])
            own = _single_period(trail.group("phrase")) if trail else None
            if own and not _periods(t.metric):
                period, notes = own, notes + ["PERIOD_AFTER_RANGE"]
            if period == PERIOD_UNKNOWN and not _periods(sentence) and scope_period:
                period, notes = scope_period, notes + ["PERIOD_FROM_SCOPE_HEADING"]
            out.append(replace(t, period=period, notes=notes))
            # coordinated ranges / list continuations after this one
            rest, label = sentence[tail_from:], t.metric
            if trail:
                rest = rest[trail.end():]
            while True:
                m = _CONT.match(rest)
                if not m:
                    break
                lab = (m.group("label") or "").strip(" ,") or label
                if not _label_ok(lab):
                    break
                cur = _parse_range(m.group("range"))
                if cur is None:
                    break
                rest = rest[m.end():]
                trail2 = _TRAIL_PERIOD.match(rest)
                own2 = _single_period(trail2.group("phrase")) if trail2 else None
                per = _single_period(lab) or own2 or (
                    scope_period if not _periods(sentence) and scope_period else PERIOD_UNKNOWN)
                if per == PERIOD_UNKNOWN and not own2 and _single_period(sentence) and not _periods(lab):
                    per = _single_period(sentence)
                out.append(_make(lab, cur, period=per, template="T5_continuation", source_text=sentence,
                                 meta=meta, after=rest, notes=["LIST_CONTINUATION"]))
                if trail2:
                    rest = rest[trail2.end():]
                label = lab
        else:
            out.append(t)
    return out


# -- structure --------------------------------------------------------------------------------------

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.split("|") if c.strip()]


def _strip_bullet(line: str) -> tuple[bool, str]:
    if line and line[0] in BULLETS and (len(line) == 1 or line[1] == " "):
        return True, line[1:].strip()
    return False, line


def _heading_like(line: str) -> bool:
    if not _LETTER.search(line) or line.endswith((".", ";", ",")):
        return False
    return not _DIGIT.search(_PERIOD_STRIP.sub(" ", line))


def _value_only(line: str) -> dict[str, Any] | None:
    body = _FOOTNOTE.sub(" ", line).strip()
    m = _RANGE_RE.fullmatch(body) or _RANGE_RE.fullmatch(body.rstrip(". "))
    if not m or not _value_text_ok(body):
        return None
    return _parse_range(m.group(0))


def _blocks(chunks: list[Any]) -> list[dict[str, Any]]:
    """Lines of one source in document order, with bullets merged and table cells split."""
    out: list[dict[str, Any]] = []
    pending_bullet = False
    for chunk in chunks:
        for raw in chunk.text.split("\n"):
            line = " ".join(raw.replace("\xa0", " ").split())
            if not line:
                continue
            row = {"evidence_id": chunk.evidence_id, "text": line, "table": "|" in line, "bullet": False}
            if row["table"]:
                cells = _cells(line)
                if cells and len(cells[0]) == 1 and cells[0] in BULLETS:
                    row.update(table=False, bullet=True, text=" ".join(cells[1:]))
                else:
                    row["cells"] = cells
            else:
                is_bullet, text = _strip_bullet(line)
                if is_bullet and not text:
                    pending_bullet = True
                    continue
                row.update(bullet=is_bullet or pending_bullet, text=text)
            pending_bullet = False
            if row["text"]:
                out.append(row)
    return out


def transcribe_source(chunks: list[Any], meta_of) -> tuple[list[Transcription], dict[str, int]]:
    stats = {"guidance_scopes": 0, "candidate_blocks": 0, "located_prose": 0, "table_rows_ambiguous": 0}
    rows: list[Transcription] = []
    blocks = _blocks(chunks)
    scope = False
    scope_period: str | None = None
    columns: list[str | None] | None = None
    low_high = False
    i = 0
    while i < len(blocks):
        b = blocks[i]
        meta = meta_of(b["evidence_id"])
        text = b["text"]
        located = bool(_GUIDANCE_RE.search(text))
        if b["table"]:
            cells = b.get("cells") or []
            values_text = " ".join(cells[1:]) if len(cells) > 1 else ""
            numeric = [c for c in cells if _DIGIT.search(_PERIOD_STRIP.sub(" ", c))]
            if cells and not numeric:                                   # header row
                header_periods = [_single_period(c) for c in cells]
                if located:
                    scope, scope_period = True, _heading_period(" ".join(cells))
                    stats["guidance_scopes"] += 1
                if any(header_periods):
                    columns = [p for p in header_periods if p]
                if [c.lower() for c in cells[-2:]] == ["low", "high"]:
                    low_high = True
                i += 1
                continue
            if scope and cells and _LETTER.search(cells[0]) and not _DIGIT.search(_PERIOD_STRIP.sub(" ", cells[0])) \
                    and _value_text_ok(values_text):
                stats["candidate_blocks"] += 1
                label = cells[0].strip(" :")
                found = [m.group(0) for m in _RANGE_RE.finditer(_FOOTNOTE.sub(" ", values_text))
                         if _value_text_ok(m.group(0)) or _DIGIT.search(m.group(0))]
                parsed = [p for p in (_parse_range(f) for f in found) if p is not None]
                if low_high and len(parsed) == 2 and all(p["kind"] == "SINGLE" for p in parsed):
                    lo, hi = parsed[0]["low"], parsed[1]["low"]
                    parsed = [{"kind": "RANGE" if hi.normalized >= lo.normalized else MALFORMED, "low": lo,
                               "high": hi, "single": False}]
                if _label_ok(label):
                    own = _single_period(label)
                    if len(parsed) == 1:
                        per = own or (columns[0] if columns and len(columns) == 1 else None) or scope_period
                        rows.append(_make(label, parsed[0], period=per or PERIOD_UNKNOWN, template="S3_table_row",
                                          source_text=text, meta=meta))
                    elif parsed and columns and len(columns) == len(parsed) and not own:
                        for per, p in zip(columns, parsed):
                            rows.append(_make(label, p, period=per, template="S3_table_row_column",
                                              source_text=text, meta=meta))
                    else:
                        stats["table_rows_ambiguous"] += 1
                        for p in parsed:
                            rows.append(_make(label, p, period=PERIOD_UNKNOWN, template="S3_table_row",
                                              source_text=text, meta=meta, notes=[AMBIGUOUS_COLUMN]))
            i += 1
            continue
        columns, low_high = None, False
        if b["bullet"]:
            prose = transcribe_prose(text, meta=meta, scope_period=scope_period if scope else None) if located else []
            if prose:
                stats["candidate_blocks"] += 1
                rows.extend(prose)
            elif scope:
                m = _BULLET_BODY.match(_FOOTNOTE.sub(" ", text))
                if m and _value_text_ok(m.group("range")) and _label_ok(m.group("label").strip(" :,-")):
                    cur = _parse_range(m.group("range"))
                    label = m.group("label").strip(" :,-")
                    tail = m.group("tail")
                    if cur is not None and (not tail.strip() or tail.lstrip()[:1] in ",;(." or _TRAIL_PERIOD.match(tail)):
                        stats["candidate_blocks"] += 1
                        trail = _TRAIL_PERIOD.match(tail)
                        per = _single_period(label) or (_single_period(trail.group("phrase")) if trail else None) \
                            or scope_period or PERIOD_UNKNOWN
                        rows.append(_make(label, cur, period=per, template="S2_bullet", source_text=text, meta=meta,
                                          after=tail))
            i += 1
            continue
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        value = _value_only(nxt["text"]) if nxt and not nxt["table"] and not nxt["bullet"] else None
        if scope and value is not None and _heading_like(text) and _label_ok(text.strip(" :")):
            stats["candidate_blocks"] += 1
            label = text.strip(" :")
            per = _single_period(label) or scope_period or PERIOD_UNKNOWN
            rows.append(_make(label, value, period=per, template="S4_label_value", source_text=f"{text} / {nxt['text']}",
                              meta=meta_of(nxt["evidence_id"])))
            i += 2
            continue
        if _heading_like(text):
            periods = _periods(text)
            if located:
                scope, scope_period = True, _heading_period(text)
                stats["guidance_scopes"] += 1
            elif scope and len(periods) == 1:
                scope_period = _heading_period(text)                     # subheading with its own period
            elif scope and len(periods) > 1:
                scope_period = None
            i += 1
            continue
        if located or scope:
            # inside a scope the heading located the section, so its prose is read even when the sentence
            # does not repeat a term ("AudioEye expects ..." under "Financial Outlook")
            stats["located_prose"] += int(located)
            produced = False
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(\"“])", text):
                if scope or _GUIDANCE_RE.search(sentence):
                    found = transcribe_prose(sentence, meta=meta, scope_period=scope_period if scope else None)
                    if found:
                        stats["candidate_blocks"] += 1
                        produced = True
                    rows.extend(found)
            if scope and not located and not produced:
                scope, scope_period = False, None
        else:
            scope, scope_period = False, None
        i += 1
    return rows, stats


def _normalise_period(period: str, fiscal_year_end: str | None) -> str:
    if period.startswith("YEAR") and fiscal_year_end == "1231":
        return "FY" + period[4:]
    return period


def transcribe(package: Any, *, fiscal_year_end: str | None = None) -> dict[str, Any]:
    sources = {s.source_id: s for s in package.source_manifest}
    cutoff = package.evidence_bundle.data_cutoff
    by_source: dict[str, list[Any]] = {}
    for chunk in package.chunks:
        if not chunk.pit_eligible or (chunk.available_at is not None and chunk.available_at > cutoff):
            continue
        by_source.setdefault(chunk.source_id, []).append(chunk)
    rows: list[Transcription] = []
    totals = {"guidance_scopes": 0, "candidate_blocks": 0, "located_prose": 0, "table_rows_ambiguous": 0}
    for source_id in sorted(by_source):
        chunks = sorted(by_source[source_id], key=lambda c: c.chunk_index)
        root = ":".join(source_id.split(":")[:3])
        source = sources.get(source_id) or sources.get(root)
        title = str(getattr(source, "title", "") or "")
        published = chunks[0].published_at
        base = {"source_id": source_id,
                "accession": getattr(source, "accession", None) or (root.split(":")[2] if root.count(":") == 2 else None),
                "form": title.split(" ", 1)[0] if title else None,
                "filing_date": published.date().isoformat() if published else None,
                "published_at": published.isoformat() if published else None}
        found, stats = transcribe_source(chunks, lambda ev: base | {"evidence_id": ev})
        for key, value in stats.items():
            totals[key] += value
        rows.extend(replace(r, period=_normalise_period(r.period, fiscal_year_end)) for r in found)
    return {"parser": PARSER, "transcriptions": rows, **totals}


def guidance_evidence(package: Any, *, fiscal_year_end: str | None = None) -> dict[str, Any]:
    found = transcribe(package, fiscal_year_end=fiscal_year_end)
    rows: list[Transcription] = found["transcriptions"]
    assessments = V1.assess(rows)
    states = [a["state"] for a in assessments]
    definite = {s for s in states if s not in ("UNKNOWN", "NOT_PROVIDED")}
    conflicts = [a for a in assessments if CONFLICT in a["reasons"]]
    overall = ("UNKNOWN" if not definite else "MIXED" if len(definite) > 1 else next(iter(definite)))
    return {
        "parser": PARSER, "fiscal_year_end": fiscal_year_end,
        **{k: found[k] for k in ("guidance_scopes", "candidate_blocks", "located_prose", "table_rows_ambiguous")},
        "transcriptions": [r.to_dict() for r in rows],
        "templates": {t: sum(r.template == t for r in rows) for t in sorted({r.template for r in rows})},
        "period_unknown": sum(r.period == PERIOD_UNKNOWN for r in rows),
        "incomplete_or_malformed": sum(r.kind in (INCOMPLETE, MALFORMED) for r in rows),
        "assessments": assessments,
        "paired": sum(1 for a in assessments if a.get("movement")),
        "state_counts": {s: states.count(s) for s in sorted(set(states))},
        "non_price_items": sum(GuidanceState(s) in INFORMATIVE for s in states),
        "overall_state": overall,
        "conflicts": len(conflicts),
        "result_vs_guidance": "NOT_PRODUCED",
    }
