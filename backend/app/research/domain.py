"""Validated manual GPT research JSON contract."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.market.symbols import normalize_symbol


class CatalystDuration(StrEnum):
    INTRADAY = "INTRADAY"
    ONE_TO_TWO_DAYS = "ONE_TO_TWO_DAYS"
    MULTI_DAY = "MULTI_DAY"
    UNKNOWN = "UNKNOWN"


class StopProfile(StrEnum):
    TIGHT = "TIGHT"
    NORMAL = "NORMAL"
    WIDE = "WIDE"
    UNKNOWN = "UNKNOWN"


class TrailingProfile(StrEnum):
    TIGHT = "TIGHT"
    NORMAL = "NORMAL"
    WIDE = "WIDE"
    UNKNOWN = "UNKNOWN"


class OvernightSuitability(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class HumanDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class SourceType(StrEnum):
    SEC = "SEC"
    IR = "IR"
    EXCHANGE = "EXCHANGE"
    OFFICIAL = "OFFICIAL"
    NEWS = "NEWS"
    OTHER = "OTHER"


class ResearchStatus(StrEnum):
    IMPORTED = "IMPORTED"


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    claim: str = Field(min_length=1, max_length=128)
    url: AnyHttpUrl
    type: SourceType
    title: str = Field(min_length=1, max_length=500)
    published_at: datetime | None = None

    @field_validator("published_at")
    @classmethod
    def aware_published_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("published_at must be timezone-aware")
        return value


class GPTCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ticker: str
    gpt_rank: int = Field(ge=1)
    overall_score: float = Field(ge=0, le=100)
    catalyst_score: float = Field(ge=0, le=100)
    fundamental_score: float = Field(ge=0, le=100)
    momentum_score: float = Field(ge=0, le=100)
    risk_score: float = Field(ge=0, le=100, description="Higher means safer/better risk-reward")
    catalyst_duration: CatalystDuration
    stop_profile: StopProfile
    trailing_profile: TrailingProfile
    overnight_suitability: OvernightSuitability
    company_summary: str = Field(min_length=1, max_length=20_000)
    catalyst_summary: str = Field(min_length=1, max_length=20_000)
    risk_summary: str = Field(min_length=1, max_length=20_000)
    invalidation_summary: str = Field(min_length=1, max_length=20_000)
    unknown_fields: list[str] = Field(default_factory=list, max_length=100)
    sources: list[ResearchSource] = Field(default_factory=list, max_length=200)

    @field_validator("ticker")
    @classmethod
    def valid_ticker(cls, value: str) -> str:
        return normalize_symbol(value)

    @field_validator("unknown_fields")
    @classmethod
    def normalize_unknowns(cls, values: list[str]) -> list[str]:
        cleaned = {value.strip() for value in values if value.strip()}
        return sorted(cleaned)


class GPTResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    analysis_at: datetime
    trading_date: date
    scanner_run_id: int = Field(gt=0)
    candidates: list[GPTCandidateResult] = Field(min_length=1, max_length=8)

    @field_validator("analysis_at")
    @classmethod
    def aware_analysis_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_at must be timezone-aware")
        return value

