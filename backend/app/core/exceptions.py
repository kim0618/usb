"""Shared exception hierarchy for USB."""


class USBError(Exception):
    """Base exception for expected USB application errors."""


class ConfigurationError(USBError):
    """Raised when application configuration is invalid or unusable."""


class DataError(USBError):
    """Raised when required data is missing, invalid, or inconsistent."""


class MarketDataError(DataError):
    """Normalized failure raised by an external market-data provider."""

    def __init__(self, code: str, message: str, *, provider: str | None = None,
                 error_category: str | None = None, provider_code: str | None = None,
                 http_status: int | None = None, provider_return_code: str | None = None,
                 provider_subcode: str | None = None, sanitized_return_msg: str | None = None,
                 requested_exchange: str | None = None) -> None:
        self.code = code
        self.provider = provider
        self.error_category = error_category
        self.provider_code = provider_code
        # What the provider answered, for telemetry; the message is sanitized before it is stored.
        self.http_status = http_status
        self.provider_return_code = provider_return_code
        self.provider_subcode = provider_subcode
        self.sanitized_return_msg = sanitized_return_msg
        self.requested_exchange = requested_exchange
        super().__init__(message)


class ResearchError(USBError):
    """Raised when GPT research input or workflow state is invalid."""
