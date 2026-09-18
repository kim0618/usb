"""Historical Research Scanner: the Production rank, over a universe that was declared.

Everything this package produces is ``RESEARCH_UNIVERSE`` / ``ASSUMED`` /
``RESEARCH_ONLY``. It is not a reconstruction of the universe Production scanned.
"""

from app.backtest.research.contract import (
    AUTHORITY_LABEL, CANDIDATE_MODE, CANDIDATE_SOURCE, DAILY_DATA_VERSION,
    HISTORICAL_RESEARCH_SCANNER_VERSION, RANK_SOURCE, SCAN_TIME_CONTRACT,
)

__all__ = ["AUTHORITY_LABEL", "CANDIDATE_MODE", "CANDIDATE_SOURCE", "DAILY_DATA_VERSION",
           "HISTORICAL_RESEARCH_SCANNER_VERSION", "RANK_SOURCE", "SCAN_TIME_CONTRACT"]
