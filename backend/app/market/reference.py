"""Reference-data abstraction kept separate from high-volume market bars."""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from datetime import datetime
from math import isfinite

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class SymbolMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    company_name: str | None = None
    market_cap: float
    exchange: str
    active: bool = True
    observed_at: datetime
    available_at: datetime

    @field_validator("symbol", "exchange")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol and exchange must not be empty")
        return normalized

    @field_validator("market_cap")
    @classmethod
    def validate_market_cap(cls, value: float) -> float:
        if not isfinite(value) or value < 0:
            raise ValueError("market_cap must be finite and non-negative")
        return value

    @field_validator("observed_at", "available_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("metadata timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> "SymbolMetadata":
        if self.available_at < self.observed_at:
            raise ValueError("available_at must be on or after observed_at")
        return self


class SymbolMetadataProvider(ABC):
    @abstractmethod
    def get_metadata(
        self, symbols: Sequence[str], as_of: datetime
    ) -> dict[str, SymbolMetadata]:
        """Return only metadata available at ``as_of``, keyed by symbol."""


class FakeSymbolMetadataProvider(SymbolMetadataProvider):
    """Deterministic reference provider backed by explicit fixtures."""

    def __init__(self, metadata: Iterable[SymbolMetadata]) -> None:
        self._metadata = {item.symbol: item for item in metadata}

    def get_metadata(
        self, symbols: Sequence[str], as_of: datetime
    ) -> dict[str, SymbolMetadata]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        wanted = {symbol.strip().upper() for symbol in symbols}
        return {
            symbol: self._metadata[symbol]
            for symbol in sorted(wanted)
            if symbol in self._metadata and self._metadata[symbol].available_at <= as_of
        }

