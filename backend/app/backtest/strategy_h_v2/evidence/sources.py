"""D2 source types, provenance schema, and the fixed SEC Form 8-K item taxonomy.

The 8-K item table below is SEC's own published General Instructions to Form 8-K - a fixed,
public reference table, reused the same way `strategy_h0.facts.FIELD_SPECS` reuses SEC's XBRL tag
taxonomy. D2 maps an item code to its official label and a coarse category; it never infers what an
item *means* for a specific filer - that interpretation belongs to a future D3 AI Research Engine,
never to this module (D1.1 brief §10: "SEC metadata에서 확실한 것만 structured field로 저장").
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "h_v2_evidence_sources_v1"

#: Every source a D3 reader sees carries this boundary marker. Reused verbatim from the existing
#: Strategy A/E research-prompt convention: external document text is data, never an instruction.
SOURCE_BOUNDARY = "UNTRUSTED_RESEARCH_DATA"


class SourceType(StrEnum):
    SEC_10K = "SEC_10K"
    SEC_10Q = "SEC_10Q"
    SEC_8K = "SEC_8K"
    EARNINGS_RELEASE = "EARNINGS_RELEASE"
    EARNINGS_CALL = "EARNINGS_CALL"
    IR_PRESENTATION = "IR_PRESENTATION"
    MAJOR_NEWS = "MAJOR_NEWS"
    USER_SUPPLIED = "USER_SUPPLIED"
    OTHER = "OTHER"


class SourceConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class DatePrecision(StrEnum):
    DATETIME = "DATETIME"
    """`published_at` carries a real, source-reported time (e.g. SEC `acceptanceDateTime`)."""
    DATE_ONLY = "DATE_ONLY"
    """Only a calendar date is known; `published_at` is midnight UTC of that date and must not be
    read as a real timestamp."""
    UNKNOWN = "UNKNOWN"
    """No usable publication time is known at all; `published_at` is `None`."""


#: SEC Form 8-K item code -> official label (General Instructions to Form 8-K). Fixed reference
#: data, not an inference.
FORM_8K_ITEM_LABELS: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety - Reporting of Shutdowns and Patterns of Violations",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation or an Obligation under an "
            "Off-Balance Sheet Arrangement",
    "2.04": "Triggering Events That Accelerate or Increase a Direct Financial Obligation "
            "or an Obligation under an Off-Balance Sheet Arrangement",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule; "
            "Transfer of Listing",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements or a Related "
            "Audit Report or Completed Interim Review",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Certain Officers; Election of Directors; "
            "Appointment of Certain Officers; Compensatory Arrangements",
    "5.03": "Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
    "5.04": "Temporary Suspension of Trading Under Registrant's Employee Benefit Plans",
    "5.05": "Amendments to the Registrant's Code of Ethics, or Waiver of a Provision",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "6.01": "ABS Informational and Computational Material",
    "6.02": "Change of Servicer or Trustee",
    "6.03": "Change in Credit Enhancement or Other External Support",
    "6.04": "Failure to Make a Required Distribution",
    "6.05": "Securities Act Updating Disclosure",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

FORM_8K_ITEM_CATEGORY: dict[str, str] = {
    "1.01": "MATERIAL_AGREEMENT", "1.02": "MATERIAL_AGREEMENT",
    "1.03": "BANKRUPTCY",
    "1.04": "OTHER_EVENTS",
    "1.05": "CYBERSECURITY",
    "2.01": "ACQUISITION_OR_DISPOSITION",
    "2.02": "EARNINGS_RESULTS",
    "2.03": "DEBT_FINANCING", "2.04": "DEBT_FINANCING",
    "2.05": "EXIT_OR_DISPOSAL_COSTS",
    "2.06": "IMPAIRMENT",
    "3.01": "DELISTING_OR_LISTING",
    "3.02": "DEBT_FINANCING",
    "3.03": "GOVERNANCE",
    "4.01": "GOVERNANCE", "4.02": "GOVERNANCE",
    "5.01": "GOVERNANCE", "5.02": "EXECUTIVE_CHANGES", "5.03": "GOVERNANCE",
    "5.04": "GOVERNANCE", "5.05": "GOVERNANCE", "5.06": "GOVERNANCE",
    "5.07": "GOVERNANCE", "5.08": "GOVERNANCE",
    "6.01": "ASSET_BACKED_SECURITIES", "6.02": "ASSET_BACKED_SECURITIES",
    "6.03": "ASSET_BACKED_SECURITIES", "6.04": "ASSET_BACKED_SECURITIES",
    "6.05": "ASSET_BACKED_SECURITIES",
    "7.01": "REGULATION_FD_DISCLOSURE",
    "8.01": "OTHER_EVENTS",
    "9.01": "FINANCIAL_STATEMENTS_AND_EXHIBITS",
}

#: Category rollups that, per D0/D1.1's official-source-priority contract, are candidate carriers
#: for an earnings release (attached exhibit to a 2.02 8-K) or an investor presentation (commonly
#: filed as a 7.01 Reg FD exhibit). This is a *candidate* identification, not a claim that the
#: exhibit is in fact an earnings release or a deck - D2 does not fetch or read exhibit content.
EARNINGS_RELEASE_CATEGORY = "EARNINGS_RESULTS"
IR_PRESENTATION_CATEGORY = "REGULATION_FD_DISCLOSURE"


def item_codes_of(items_field: str) -> list[str]:
    return [code.strip() for code in (items_field or "").split(",") if code.strip()]


def classify_8k_items(items_field: str) -> list[dict[str, str]]:
    """One entry per item code, using only the fixed lookup tables above. An item code this table
    does not recognize is reported honestly as `UNKNOWN_ITEM_CODE`, never guessed."""
    out = []
    for code in item_codes_of(items_field):
        out.append({
            "code": code,
            "label": FORM_8K_ITEM_LABELS.get(code, "Unrecognized item code"),
            "category": FORM_8K_ITEM_CATEGORY.get(code, "UNKNOWN_ITEM_CODE"),
        })
    return out


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: SourceType
    publisher: str
    title: str
    url: AnyHttpUrl | None = None
    published_at: datetime | None
    date_precision: DatePrecision
    available_at: datetime | None
    fetched_at: datetime
    checksum: str | None = None
    accession: str | None = None
    confidence: SourceConfidence
    pit_eligible: bool
    verified: bool = True
    """`False` only for `USER_SUPPLIED` sources (D1.1 brief §32); every official source D2 itself
    collects is `True` by construction (it came from SEC's own systems)."""
    source_boundary: str = SOURCE_BOUNDARY

    @field_validator("published_at", "available_at", "fetched_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("must be timezone-aware")
        return value

    @field_validator("source_boundary")
    @classmethod
    def _boundary_is_fixed(cls, value: str) -> str:
        if value != SOURCE_BOUNDARY:
            raise ValueError("source_boundary is a fixed constant, not a per-source choice")
        return value


def user_supplied_source(
    *, source_id: str, title: str, url: str | None, note: str, fetched_at: datetime,
) -> SourceProvenance:
    """The only path that may produce `verified=False`. `note` is stored as the source's title
    prefix so a D3 reader cannot mistake it for an official source without reading `source_type`."""
    return SourceProvenance(
        source_id=source_id, source_type=SourceType.USER_SUPPLIED, publisher="USER",
        title=f"[user-supplied, unverified] {title} - {note}",
        url=url, published_at=None, date_precision=DatePrecision.UNKNOWN, available_at=None,
        fetched_at=fetched_at, checksum=None, accession=None, confidence=SourceConfidence.UNKNOWN,
        pit_eligible=False, verified=False,
    )
