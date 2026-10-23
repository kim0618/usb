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
                 error_category: str | None = None, provider_code: str | None = None) -> None:
        self.code = code
        self.provider = provider
        self.error_category = error_category
        self.provider_code = provider_code
        super().__init__(message)


class ResearchError(USBError):
    """Raised when GPT research input or workflow state is invalid."""
