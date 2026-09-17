"""Refusals raised by the Strategy B research layer.

A refusal names what was wrong and never repairs the input. A silently repaired input
would produce a feature value that no stored tape can reproduce.
"""


class StrategyBError(ValueError):
    """Base class for every Strategy B research-layer refusal."""


class PointInTimeViolation(StrategyBError):
    """An input carries information that was not available at the decision time."""


class InvalidTape(StrategyBError):
    """Bars that cannot form one ordered, single-day, actual-trade tape."""


class SyntheticBarMisuse(StrategyBError):
    """A synthetic clock bar reached a consumer that needs actual trades."""


class InvalidConfig(StrategyBError):
    """A Strategy B config that does not match the declared schema."""
