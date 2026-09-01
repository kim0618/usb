"""Shared exception hierarchy for USB."""


class USBError(Exception):
    """Base exception for expected USB application errors."""


class ConfigurationError(USBError):
    """Raised when application configuration is invalid or unusable."""


class DataError(USBError):
    """Raised when required data is missing, invalid, or inconsistent."""

