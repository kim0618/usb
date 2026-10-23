"""Shared, conservative market-data error semantics. Raw provider messages are never persisted."""

from dataclasses import asdict, dataclass
from typing import Any, Mapping

SOURCE_REFUSAL = "SOURCE_REFUSAL"
TRANSIENT_PROVIDER_ERROR = "TRANSIENT_PROVIDER_ERROR"
MAPPING_ERROR = "MAPPING_ERROR"
DATA_INCOMPLETE = "DATA_INCOMPLETE"
SOURCE_REFUSAL_CODES = frozenset({"INVALID_SYMBOL", "MARKET_DATA_UNAVAILABLE"})
FIELDS = ("provider", "error_class", "error_code", "error_category", "error_message", "provider_code")


@dataclass(frozen=True)
class ProviderError:
    provider: str
    error_class: str
    error_code: str
    error_category: str
    error_message: str
    provider_code: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def source_refusal(self) -> bool:
        return self.error_category == SOURCE_REFUSAL and self.error_code in SOURCE_REFUSAL_CODES


def classify(error: BaseException, *, provider: str = "KIWOOM") -> ProviderError:
    code = getattr(error, "code", None) or type(error).__name__
    category = getattr(error, "error_category", None)
    if category is None:
        if code in {"INVALID_SYMBOL", "UNSUPPORTED_EXCHANGE", "UNMAPPED_SYMBOL"}:
            category = MAPPING_ERROR
        elif code in {"KIWOOM_DEADLINE_REACHED", "INSUFFICIENT_HISTORY", "PAGINATION_INCOMPLETE"}:
            category = DATA_INCOMPLETE
        else:
            category = TRANSIENT_PROVIDER_ERROR
    # A fixed category description avoids copying credentials or account data from exceptions.
    return ProviderError(getattr(error, "provider", None) or provider, type(error).__name__, code,
                         category, category.lower().replace("_", " "),
                         getattr(error, "provider_code", None))


def detail_from_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Old snapshots remain readable; a class name or a bare ambiguous code proves no refusal."""
    return {k: row.get(k) for k in FIELDS} if row.get("error_category") else None


def is_source_refusal(detail: Mapping[str, Any] | None) -> bool:
    return bool(detail and detail.get("provider") == "KIWOOM"
                and detail.get("error_category") == SOURCE_REFUSAL
                and detail.get("error_code") in SOURCE_REFUSAL_CODES)
