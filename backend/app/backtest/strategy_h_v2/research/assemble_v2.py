"""D3.2F: `validate.assemble_and_validate`'s counterpart for Schema V2.

`validate.py` (D3.1's assembly/validation glue) is unmodified - its package-generic helpers
(`extract_json_object`, `package_checksum`, `valid_source_ids`, `valid_evidence_ids`,
`cross_check_fundamental_change_states`) are reused unchanged here, since none of them are
V1-schema-specific. Only the final validation call, which hardcodes `HResearchInterpretationV1`, is
restated against `HResearchInterpretationV2`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.research.prompt_builder import METADATA_FIELDS
from app.backtest.strategy_h_v2.research.schema_v2 import HResearchInterpretationV2
from app.backtest.strategy_h_v2.research.validate import (
    ExtractionError,
    cross_check_fundamental_change_states,
    extract_json_object,
    package_checksum,
    valid_evidence_ids,
    valid_source_ids,
)


def assemble_and_validate_v2(
    raw_text: str,
    package: AIResearchInputV1,
    *,
    research_id: str,
    version: int,
    model: str,
    model_version: str | None,
    prompt_version: str,
    created_at: datetime,
) -> tuple[HResearchInterpretationV2 | None, list[str]]:
    """Same contract as `validate.assemble_and_validate`: returns `(validated_output, errors)`,
    `validated_output` is `None` on any failure with `errors` populated for the repair loop."""
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
        validated = HResearchInterpretationV2.model_validate(
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
