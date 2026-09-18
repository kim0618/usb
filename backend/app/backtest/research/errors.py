"""Typed refusals for the Historical Research Scanner.

Every one of them is a refusal to produce a result, never a degraded result. A research
scan that cannot state exactly which symbols it was given, which daily bars it read, and
that the session it scanned was finished has nothing worth ranking.
"""


class ResearchScannerError(RuntimeError):
    """Base class: the research scan cannot run on the input it was handed."""


class UniverseInvalid(ResearchScannerError):
    """The declared Research Universe is empty, duplicated, malformed, or holds the
    benchmark as a tradable candidate."""


class MetadataInvalid(ResearchScannerError):
    """Candidate metadata is missing, contradicts itself, or names a symbol the universe
    does not contain."""


class DailyDataMissing(ResearchScannerError):
    """A daily bar the scan needs is absent from the daily authority."""


class ScanTimeContractViolation(ResearchScannerError):
    """The scan was asked for a session that is not a completed one, or at a moment
    before that session's tape had finished."""
