"""D3 output validation: JSON parsing, orchestration-metadata injection, and the evidence
cross-checks a Pydantic model alone cannot see (it does not have the input package in scope).
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from pydantic import ValidationError

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.research.prompt_builder import METADATA_FIELDS
from app.backtest.strategy_h_v2.research.schema import HResearchInterpretationV1


class ExtractionError(ValueError):
    pass


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """The model is instructed to return exactly one bare JSON object. This tolerates a leading
    code fence (some models wrap JSON in ```json even when told not to) but does not attempt to
    repair malformed JSON itself - that is what the bounded repair loop is for."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ExtractionError(f"JSON_PARSE_ERROR: {error}") from error


def package_checksum(package: AIResearchInputV1) -> str:
    canonical = package.model_dump_json(exclude={"generated_at"})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def valid_source_ids(package: AIResearchInputV1) -> set[str]:
    ids = {s.source_id for s in package.source_manifest}
    ids |= {s.source_id for s in package.evidence_bundle.filings}
    ids |= {s.source_id for s in package.evidence_bundle.source_manifest}
    ids |= {c.source_id for c in package.chunks}
    return ids


def valid_evidence_ids(package: AIResearchInputV1) -> set[str]:
    """The chunk IDs that actually exist *in this candidate's package*.

    Built per-candidate on purpose (D3.1 §2.1): because another company's chunks are simply absent
    from this set, a cross-company citation is rejected by the same check that rejects an invented
    chunk index.
    """
    return {chunk.evidence_id for chunk in package.chunks}


def cross_check_fundamental_change_states(
    content: dict[str, Any], package: AIResearchInputV1,
) -> list[str]:
    """The model must not restate `fundamental_change[].code_owned_state` differently from what
    D1's E2 actually computed - this is the one place D3 could silently override a code-owned
    fact, so it is checked explicitly rather than trusted (D3 brief §8: "AI는 code-owned state를
    변경하지 않는다")."""
    errors: list[str] = []
    bundle_states = {
        metric: (data or {}).get("state")
        for metric, data in (package.evidence_bundle.fundamental_changes or {}).items()
    }
    for item in content.get("fundamental_change", []) or []:
        metric = item.get("metric")
        claimed = item.get("code_owned_state")
        actual = bundle_states.get(metric)
        if actual is not None and claimed != actual:
            errors.append(
                f"fundamental_change[{metric!r}].code_owned_state={claimed!r} does not match "
                f"the evidence bundle's actual state {actual!r}"
            )
    return errors


def assemble_and_validate(
    raw_text: str,
    package: AIResearchInputV1,
    *,
    research_id: str,
    version: int,
    model: str,
    model_version: str | None,
    prompt_version: str,
    created_at: datetime,
) -> tuple[HResearchInterpretationV1 | None, list[str]]:
    """Returns (validated_output, errors). `validated_output` is `None` if either JSON parsing,
    the fundamental-change cross-check, or Pydantic schema/context validation failed - `errors` is
    always populated in that case for the repair loop to act on."""
    try:
        content = extract_json_object(raw_text)
    except ExtractionError as error:
        return None, [str(error)]

    injected = {k: v for k, v in content.items() if k not in METADATA_FIELDS}
    cross_errors = cross_check_fundamental_change_states(injected, package)
    if cross_errors:
        return None, cross_errors

    bundle = package.evidence_bundle
    full_record = {
        **injected,
        "research_id": research_id,
        "version": version,
        "company_id": bundle.identity.get("cik") or "UNKNOWN",
        "ticker": bundle.ticker,
        "decision_time": bundle.data_cutoff.isoformat(),
        "input_package_id": f"{package.run_id}:{bundle.ticker}",
        "input_package_checksum": package_checksum(package),
        "model": model,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "created_at": created_at.isoformat(),
    }
    try:
        validated = HResearchInterpretationV1.model_validate(
            full_record,
            context={
                "valid_source_ids": valid_source_ids(package),
                "valid_evidence_ids": valid_evidence_ids(package),
            },
        )
    except ValidationError as error:
        return None, [str(err["msg"]) + " @ " + ".".join(str(p) for p in err["loc"])
                       for err in error.errors()]
    return validated, []
