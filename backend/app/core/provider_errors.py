"""Shared, conservative market-data error semantics. Raw provider messages are never persisted:
only :func:`sanitize_message` output (bounded, token- and account-like runs redacted) is."""

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping

SOURCE_REFUSAL = "SOURCE_REFUSAL"
TRANSIENT_PROVIDER_ERROR = "TRANSIENT_PROVIDER_ERROR"
MAPPING_ERROR = "MAPPING_ERROR"
DATA_INCOMPLETE = "DATA_INCOMPLETE"
SOURCE_REFUSAL_CODES = frozenset({"INVALID_SYMBOL", "MARKET_DATA_UNAVAILABLE"})
#: Kiwoom's generic rejection envelope. 1903 (mapping) also arrives under it, with a subcode.
GENERIC_REJECTION_RETURN_CODE = "7"
FIELDS = ("provider", "error_class", "error_code", "error_category", "error_message", "provider_code",
          "http_status", "provider_return_code", "provider_subcode", "sanitized_return_msg")
#: Retry bookkeeping a phase may add to a detail; carried through a snapshot only when present.
RETRY_FIELDS = ("attempts", "max_attempts", "retry_outcome")
MAX_MESSAGE = 160


def sanitize_message(text: Any) -> str | None:
    """A provider message safe to store: long token-like and digit runs redacted, one line, bounded."""
    if text is None or str(text).strip() == "":
        return None
    value = re.sub(r"[A-Za-z0-9+/=_\-.]{24,}", "<redacted>", str(text))
    value = re.sub(r"\d{6,}", "<num>", value)
    return " ".join(value.split())[:MAX_MESSAGE]


@dataclass(frozen=True)
class ProviderError:
    provider: str
    error_class: str
    error_code: str
    error_category: str
    error_message: str
    provider_code: str | None = None
    http_status: int | None = None
    provider_return_code: str | None = None
    provider_subcode: str | None = None
    sanitized_return_msg: str | None = None

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
                         getattr(error, "provider_code", None), getattr(error, "http_status", None),
                         getattr(error, "provider_return_code", None), getattr(error, "provider_subcode", None),
                         getattr(error, "sanitized_return_msg", None))


def generic_rejection(error: BaseException) -> bool:
    """Kiwoom's bare generic rejection: HTTP 200, return_code 7, no subcode, not a mapping error and
    not an explicit refusal. The only provider answer the E finalization retries."""
    return (getattr(error, "provider", None) == "KIWOOM"
            and getattr(error, "error_category", None) == TRANSIENT_PROVIDER_ERROR
            and getattr(error, "code", None) == "MARKET_DATA_UNAVAILABLE"
            and getattr(error, "http_status", None) == 200
            and str(getattr(error, "provider_return_code", None)) == GENERIC_REJECTION_RETURN_CODE
            and getattr(error, "provider_subcode", None) is None)


def detail_from_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Old snapshots remain readable; a class name or a bare ambiguous code proves no refusal."""
    if not row.get("error_category"):
        return None
    return {k: row.get(k) for k in FIELDS} | {k: row[k] for k in RETRY_FIELDS if k in row}


def is_source_refusal(detail: Mapping[str, Any] | None) -> bool:
    return bool(detail and detail.get("provider") == "KIWOOM"
                and detail.get("error_category") == SOURCE_REFUSAL
                and detail.get("error_code") in SOURCE_REFUSAL_CODES)
