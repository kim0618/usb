"""D3.2R Repair Telemetry + Raw Response Retention contracts (brief §14/§15).

Data contracts only, in this stage - not wired into `run_strategy_h_v2_d3.py` or
`run_strategy_h_v2_d3_1.py` (both pre-existing dirty files this stage does not touch, D3.2R brief
§0/§26). Wiring these into an actual runner is a D3.3 prerequisite (§N of the D3.2R document), and
is stated as such rather than implied by these contracts existing.

Why this exists: MRVI (D3.1's one unrepaired failure) is `NOT_AUDITABLE` beyond its validation error
today (`H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` §J.7) because `repair.RepairRecord` truncates
`rejected_output_preview` to 1,500 characters and no full raw response is stored anywhere - by the
time anyone asked what MRVI's `future_business` section actually contained, it was gone. These two
contracts exist so that stops being possible for the next run: a full-fidelity, checksummed,
end-to-end-reproducible repair trail (`RepairTelemetryRecordV1`) and untruncated raw-response
storage (`RawResponseRecordV1`) for every attempt, initial and repaired alike.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Matches the "... @ some.field.path" suffix `schema.py`'s Pydantic validators consistently raise
#: (see `Claim._material_claim_needs_a_source`, `FutureBusinessItem._stage_matches_evidence_floor`,
#: every `ValueError` in `schema.py`/`schema_v2.py`) and the "@ path" suffix
#: `validate.assemble_and_validate` appends to every Pydantic `ValidationError.errors()` entry.
_FIELD_PATH_RE = re.compile(r"@\s*([\w.\[\]]+)\s*$")


def extract_failure_field(error_message: str) -> str | None:
    """The field path a validation error names, if it names one - `None` for an error with no
    such suffix (a JSON parse failure has no field path to point at)."""
    match = _FIELD_PATH_RE.search(error_message.strip())
    return match.group(1) if match else None


@dataclass(frozen=True)
class RepairTelemetryRecordV1:
    """One repair round, reproducible end to end without re-reading a truncated preview.

    Distinct from `repair.RepairRecord` (D3.1's existing per-round record, unchanged by this
    stage): `RepairRecord` is what a live run already collects (`reason`, `errors`, a 1,500-char
    `rejected_output_preview`) and stays exactly as it is (D3.2R brief §0 forbids touching it
    outside a new file). This is the superset a D3.3 runner should persist instead - every
    `RepairRecord` maps onto one of these (`from_repair_record`, kept as a bridge so this contract
    can be exercised against D3.1's own real repair data without a live call), but the mapping is
    lossy exactly where D3.1's own record already was (`repaired_output_checksum` cannot be
    computed from a 1,500-char preview - see `raw_response_ref`).
    """

    candidate_id: str
    initial_output_checksum: str
    validation_contract_version: str
    failure_codes: list[str] = field(default_factory=list)
    """`repair.RepairReason` values, as strings - kept as strings here so this module does not
    depend on `repair.py`."""
    failure_fields: list[str] = field(default_factory=list)
    repair_attempt: int = 0
    repair_prompt_version: str = ""
    repaired_output_checksum: str | None = None
    """`None` when no repaired output exists yet, or when only a truncated preview was ever
    stored and a checksum of it would be a checksum of the wrong (truncated) text, not the actual
    model response - see `raw_response_ref`."""
    final_status: str = "PENDING"
    raw_response_ref: str | None = None
    """Points at a `RawResponseRecordV1.checksum` for the full untruncated text this round
    produced. `None` means brief §15's gap: this round's real output was never fully retained -
    the honest signal to keep, not a checksum manufactured from a preview."""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "initial_output_checksum": self.initial_output_checksum,
            "validation_contract_version": self.validation_contract_version,
            "failure_codes": self.failure_codes,
            "failure_fields": self.failure_fields,
            "repair_attempt": self.repair_attempt,
            "repair_prompt_version": self.repair_prompt_version,
            "repaired_output_checksum": self.repaired_output_checksum,
            "final_status": self.final_status,
            "raw_response_ref": self.raw_response_ref,
        }

    @classmethod
    def from_repair_record(
        cls, *, candidate_id: str, initial_output_checksum: str, validation_contract_version: str,
        repair_prompt_version: str, final_status: str, reason: str, errors: list[str],
        attempt: int, rejected_output_preview: str,
    ) -> "RepairTelemetryRecordV1":
        """Bridge from D3.1's existing `RepairRecord.to_dict()` shape - lets this contract be
        exercised against Batch 2's own real repair rounds with 0 model calls. Deliberately does
        NOT compute `repaired_output_checksum` from `rejected_output_preview`: that preview is
        truncated to 1,500 characters, so its checksum would not match the checksum of what the
        model actually returned, and a telemetry record whose checksum cannot be trusted is worse
        than one that honestly has none.
        """
        return cls(
            candidate_id=candidate_id, initial_output_checksum=initial_output_checksum,
            validation_contract_version=validation_contract_version,
            failure_codes=[reason], failure_fields=[
                f for f in (extract_failure_field(e) for e in errors) if f is not None
            ],
            repair_attempt=attempt, repair_prompt_version=repair_prompt_version,
            repaired_output_checksum=None, final_status=final_status, raw_response_ref=None,
        )


@dataclass(frozen=True)
class RawResponseRecordV1:
    """One model response, in full, immutable once stored. No truncation, ever - the 1,500-char
    preview that made MRVI unauditable is a *summary* for humans reading a manifest, never the
    system of record."""

    candidate_id: str
    attempt: int
    role: str
    """"initial" or "repair" - which call in the bounded loop produced this text."""
    raw_text: str
    checksum: str
    prompt_version: str

    @classmethod
    def capture(cls, *, candidate_id: str, attempt: int, role: str, raw_text: str,
                prompt_version: str) -> "RawResponseRecordV1":
        if role not in ("initial", "repair"):
            raise ValueError(f"role must be 'initial' or 'repair', got {role!r}")
        return cls(candidate_id=candidate_id, attempt=attempt, role=role, raw_text=raw_text,
                   checksum=checksum(raw_text), prompt_version=prompt_version)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "attempt": self.attempt, "role": self.role,
            "raw_text": self.raw_text, "checksum": self.checksum,
            "prompt_version": self.prompt_version,
        }
