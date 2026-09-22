"""Historical (U1) research data eligibility, read from a frozen Common Historical Store snapshot.

Rule ``ru2-pit-adv-sector-cap-dataeligible-v2`` is unchanged here. Only the tape the minute and
daily steps read is different: V2 asks the collector manifest about legacy Parquet, and U1 asks
``A_STRICT_V1`` about ``USB-HIST-V1``. That is the same strict contract - a session enters only
when the frozen session audit says every regular minute is there - so no requirement is weakened:

1. **metadata** - the same ``metadata_record`` built from the dated ticker details the pool step
   already read, so a U1 record is DATED_REFERENCE at the U1 reference session.
2. **daily** - the snapshot holds a per-symbol daily member covering the scan range and its
   warmup (``MASSIVE_TICKER_AGGREGATE``, never grouped).
3. **minute** - every session of ``minute_required_start..minute_required_end`` is a clean audit
   session for that symbol. One excluded session is a FAILED verdict, exactly as one missing
   regular minute is for V2.

Nothing here collects: a symbol whose snapshot tape does not reach is FAILED, and filling it is a
collector run against the Common Historical Store, never a decision of this walk. A snapshot read
that cannot reach a verdict (a member whose checksum no longer matches, a corrupt page) raises
``DataEligibilityUnresolved`` and stops the walk, so a storage fault never selects a universe.

No backtest result, trade, signal or summary is opened on this path.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.backtest.collector.range import sessions_between
from app.backtest.historical_store.a_view import A_STRICT_VIEW, CommonSnapshot
from app.backtest.replay.errors import ReplayError
from app.backtest.research.daily import WINDOW_MARGIN_SESSIONS
from app.backtest.research.errors import MetadataInvalid
from app.backtest.research.universe_selection import (
    Candidate, DataEligibility, DataEligibilityUnresolved,
)
from app.backtest.research.data_eligibility import metadata_record
from app.market.calendar import MarketCalendar
from app.market.symbols import normalize_symbol
from app.scanner.config import ScannerConfig

#: Which snapshot members answer the daily step. The grouped tape is never read here
#: (USB-DAILY-AUTHORITY-V1); it only ranks liquidity, one step earlier and outside this module.
DAILY_AUTHORITY = "MASSIVE_TICKER_AGGREGATE"


@dataclass(frozen=True)
class U1Ranges:
    """The sessions U1 requires of every candidate, derived from the segment, never typed in."""

    #: First and last entry session of the U1 segment.
    entry_start: date
    entry_end: date
    #: The scanner's daily coverage, including its warmup.
    daily_start: date
    daily_end: date
    #: The minute tape the replay reads: premarket history before the first entry through the
    #: settlement session after the last one.
    minute_start: date
    minute_end: date

    def as_dict(self) -> dict[str, Any]:
        return {"entry_range": f"{self.entry_start}..{self.entry_end}",
                "daily_scan_range": f"{self.daily_start}..{self.daily_end}",
                "minute_required_range": f"{self.minute_start}..{self.minute_end}"}


def _step(calendar: MarketCalendar, day: date, sessions: int) -> date:
    for _ in range(abs(sessions)):
        day = (calendar.next_trading_day(day) if sessions > 0
               else calendar.previous_trading_day(day))
    return day


def u1_ranges(calendar: MarketCalendar, *, entry_start: date, entry_end: date,
              premarket_history_sessions: int, settlement_sessions: int,
              config: ScannerConfig | None = None) -> U1Ranges:
    """The U1 requirement window, walked off the exchange calendar from the segment alone."""
    warmup = (config or ScannerConfig()).required_history + WINDOW_MARGIN_SESSIONS - 1
    return U1Ranges(
        entry_start=entry_start, entry_end=entry_end,
        daily_start=_step(calendar, entry_start, -(warmup + 1)),
        daily_end=_step(calendar, entry_end, -1),
        minute_start=_step(calendar, entry_start, -premarket_history_sessions),
        minute_end=_step(calendar, entry_end, settlement_sessions))


class SnapshotEligibilityChecker:
    """``eligibility_for`` of the v2 walk, answered from one frozen snapshot."""

    def __init__(self, snapshot: CommonSnapshot, ranges: U1Ranges, *,
                 calendar: MarketCalendar) -> None:
        self.snapshot = snapshot
        self.ranges = ranges
        self._calendar = calendar
        self._minute_grid = tuple(window.session_date for window in
                                  sessions_between(calendar, ranges.minute_start, ranges.minute_end))
        self._daily_grid = tuple(window.session_date for window in
                                 sessions_between(calendar, ranges.daily_start, ranges.daily_end))
        self.checked: list[str] = []

    # -- daily (per-symbol ticker aggregate members of the snapshot)
    def _daily(self, symbol: str) -> tuple[bool, str, dict[str, Any]]:
        from app.backtest.historical_store.a_view import load_ticker_daily
        try:
            rows, members = load_ticker_daily(self.snapshot, symbol)
        except ReplayError as error:
            raise DataEligibilityUnresolved(
                symbol, f"snapshot daily: {getattr(error, 'code', type(error).__name__)}: {error}")
        if not members:
            return False, "DAILY:NO_SNAPSHOT_MEMBER", {"members": 0}
        held = {row.session_date for row in rows}
        missing = [day for day in self._daily_grid if day not in held]
        evidence = {"members": len(members), "authority": DAILY_AUTHORITY,
                    "required_sessions": len(self._daily_grid), "missing_sessions": len(missing),
                    "first_missing": missing[0].isoformat() if missing else None,
                    "range": f"{rows[0].session_date}..{rows[-1].session_date}" if rows else None}
        if missing:
            return False, (f"DAILY:MISSING_DATA: {len(missing)} of {len(self._daily_grid)} "
                           f"sessions absent from the snapshot, first {missing[0]}"), evidence
        return True, "COMPLETE", evidence

    # -- minute (the frozen session audit, A STRICT)
    def _minute(self, symbol: str) -> tuple[bool, str, dict[str, Any]]:
        if not self.snapshot.audit.get(symbol):
            return False, "MINUTE:NOT_IN_SNAPSHOT: the snapshot holds no minute tape for the symbol", \
                {"view": A_STRICT_VIEW, "audit_rows": 0,
                 "required_sessions": len(self._minute_grid)}
        kept, excluded = self.snapshot.strict_sessions(symbol, self._minute_grid)
        reasons = sorted({reason for reason in excluded.values()})
        first = min(excluded) if excluded else None
        evidence = {"view": A_STRICT_VIEW, "snapshot_id": self.snapshot.snapshot_id,
                    "required_sessions": len(self._minute_grid), "clean_sessions": len(kept),
                    "excluded_sessions": len(excluded), "exclusion_reasons": reasons,
                    "first_excluded": first.isoformat() if first else None}
        if excluded:
            return False, (f"MINUTE:REGULAR_MINUTES_MISSING: {A_STRICT_VIEW} excludes "
                           f"{len(excluded)} of {len(self._minute_grid)} required sessions "
                           f"({','.join(reasons)}), first {first}"), evidence
        return True, "COMPLETE", evidence

    def __call__(self, candidate: Candidate) -> DataEligibility:
        symbol = normalize_symbol(candidate.symbol)
        self.checked.append(symbol)
        try:
            record = metadata_record(candidate)
        except MetadataInvalid as error:
            return DataEligibility.failed("metadata", f"METADATA:{error}")
        evidence: dict[str, Any] = {"metadata": {
            "source": "DATED_REFERENCE",
            "market_cap_as_of": record.market_cap_as_of.isoformat()
            if record.market_cap_as_of else None}}
        ok, detail, daily_evidence = self._daily(symbol)
        evidence["daily"] = daily_evidence
        if not ok:
            return DataEligibility.failed("daily", detail, evidence)
        ok, detail, minute_evidence = self._minute(symbol)
        evidence["minute"] = minute_evidence
        if not ok:
            return DataEligibility.failed("minute", detail, evidence)
        return DataEligibility.complete(evidence)


def snapshot_symbols_with_minute(snapshot: CommonSnapshot) -> tuple[str, ...]:
    """Every symbol the snapshot's session audit describes (the only minute tape U1 can read)."""
    return tuple(sorted(snapshot.audit))


def missing_minute_symbols(snapshot: CommonSnapshot, symbols: Sequence[str]) -> tuple[str, ...]:
    """Candidates with no minute tape at all in the snapshot, in the order given."""
    have = set(snapshot.audit)
    return tuple(symbol for symbol in symbols if normalize_symbol(symbol) not in have)
