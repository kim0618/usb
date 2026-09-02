"""Validated market-data domain types."""

from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.market.symbols import normalize_symbol


class MarketSession(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    POSTMARKET = "POSTMARKET"


class _Bar(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    observed_at: datetime
    available_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_symbol(value)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_price(cls, value: float) -> float:
        if not isfinite(value) or value <= 0:
            raise ValueError("prices must be finite and positive")
        return value

    @field_validator("volume")
    @classmethod
    def validate_volume(cls, value: int) -> int:
        if value < 0:
            raise ValueError("volume must be non-negative")
        return value

    @field_validator("observed_at", "available_at")
    @classmethod
    def validate_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must be greater than or equal to OHLC values")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must be less than or equal to OHLC values")
        if self.available_at < self.observed_at:
            raise ValueError("available_at must be on or after observed_at")
        return self


class DailyBar(_Bar):
    """One daily OHLCV bar and its point-in-time availability metadata."""

    trading_date: date


class MinuteBar(_Bar):
    """One minute OHLCV bar; timestamp is the timezone-aware bar-open time."""

    timestamp: datetime
    session: MarketSession

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_observation_order(self) -> Self:
        if self.observed_at < self.timestamp:
            raise ValueError("observed_at must be on or after timestamp")
        return self
