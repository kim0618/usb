"""Small explicit mutation contracts for API V1."""

from typing import Literal

from pydantic import BaseModel, Field


class ResearchImportRequest(BaseModel):
    raw_json: str = Field(min_length=1, max_length=1_000_000)


class HumanDecisionRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    note: str | None = Field(default=None, max_length=4000)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class RecoveryRequest(BaseModel):
    acknowledged: bool


class KillSwitchRequest(BaseModel):
    confirm: bool
    reason: str = Field(min_length=1, max_length=1000)
