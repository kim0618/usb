"""What a Historical Research Scanner run is, and what it is forbidden from calling itself.

One sentence carries the whole module: this scanner ranks an **explicitly declared**
symbol list over completed daily sessions, so its candidate set is an assumption and is
labelled as one everywhere it is read. It does not reconstruct the universe Production
scanned on that date - Production's universe came from a Kiwoom ranking endpoint that
leaves no historical record - so ``AuthoritySource.RECONSTRUCTED`` is not reachable from
here, and ``assert_no_reconstructed`` is the guard that keeps it that way.

The scan-time contract is named rather than implied. Production's canonical scan runs at
18:00 ET while Kiwoom's ``acc_trde_qty`` is still partial; this scanner runs only after
the whole tape of session D has ended, which is a different point-in-time cut and a
different number. Calling it ``COMPLETED_SESSION_RESEARCH`` in every result is what stops
a rank produced here from being read as the rank Production produced.
"""

from datetime import date, datetime, time, timedelta
import hashlib

from app.backtest.authority.contract import AuthoritySource, CandidateMode
from app.backtest.research.errors import ScanTimeContractViolation
from app.integrations.massive.minute_bars import ET

#: Bumped whenever the orchestration, the input contract, or the output contract changes.
#: Two runs whose scanner version differs are not the same research scan.
HISTORICAL_RESEARCH_SCANNER_VERSION = "historical-research-scanner-v1"

#: The daily authority this scanner reads, stated exactly as it is requested.
DAILY_DATA_SOURCE = "massive"
DAILY_DATA_TIMESPAN = "day"
DAILY_DATA_ADJUSTED = "false"
DAILY_DATA_VERSION = f"{DAILY_DATA_SOURCE}:timespan={DAILY_DATA_TIMESPAN}," \
                     f"adjusted={DAILY_DATA_ADJUSTED}"

#: The point-in-time cut this scanner makes, and the only one it may claim.
SCAN_TIME_CONTRACT = "COMPLETED_SESSION_RESEARCH"
#: When a completed session's daily bar is treated as visible: the end of its whole
#: 04:00-20:00 ET tape. ASSUMED - Massive documents no publication latency - and it is
#: the same assumption ``app.backtest.basis.reconstruction`` already runs under.
TAPE_END = time(20, 0)

#: What every artifact of this scanner is labelled, without exception.
CANDIDATE_MODE = CandidateMode.RESEARCH_UNIVERSE
CANDIDATE_SOURCE = AuthoritySource.ASSUMED
RANK_SOURCE = AuthoritySource.ASSUMED
AUTHORITY_LABEL = "RESEARCH_ONLY"

#: Production's universe builder hardcodes ``trd_susp_tp="N"``, so every symbol it carries
#: is active. A research run may reproduce that contract, and says so by name rather than
#: presenting it as a fact read from a record.
ACTIVE_PRODUCTION_CONSTANT = True
ACTIVE_SOURCE_PRODUCTION_CONSTANT = "ASSUMED_PRODUCTION_CONSTANT"

#: Prefix of every checksum this package computes, so a digest can never be mistaken for
#: one another module produced over the same bytes.
CHECKSUM_NAMESPACE = "usb.research.v1"


def tape_end_at(session_date: date) -> datetime:
    """The moment session ``session_date`` is treated as finished, in market time."""
    return datetime.combine(session_date, TAPE_END, tzinfo=ET)


def default_scan_as_of(trading_date: date) -> datetime:
    """The earliest moment this scanner may run for ``trading_date``."""
    return tape_end_at(trading_date)


def metadata_visible_at(trading_date: date) -> datetime:
    """When reference metadata is treated as visible: the day before the session opens.

    Reference facts are not intraday observations, and dating them at the session's own
    tape end would make them look like something the session produced.
    """
    return datetime.combine(trading_date, time(0, 0), tzinfo=ET) - timedelta(days=1)


def checksum(namespace: str, payload: str) -> str:
    """One namespaced SHA-256 over canonical text. The only digest recipe in the package."""
    digest = hashlib.sha256()
    digest.update(f"{CHECKSUM_NAMESPACE}:{namespace}\n".encode())
    digest.update(payload.encode())
    return digest.hexdigest()


def assert_completed_session(trading_date: date, scan_as_of: datetime,
                             completed_through: date) -> None:
    """Refuse a session that is not finished, and a scan moment before its tape ended.

    ``completed_through`` is a precondition the caller supplies, never an output: it says
    which session the daily authority is known to be complete through. Keeping it out of
    the result is what lets the same inputs produce the same bytes on any day.
    """
    if scan_as_of.tzinfo is None or scan_as_of.utcoffset() is None:
        raise ScanTimeContractViolation("scan_as_of must be timezone-aware")
    if trading_date > completed_through:
        raise ScanTimeContractViolation(
            f"{trading_date} is not a completed session: the daily authority is complete "
            f"through {completed_through}, and {SCAN_TIME_CONTRACT} never scans a session "
            "whose tape is still running")
    earliest = default_scan_as_of(trading_date)
    if scan_as_of < earliest:
        raise ScanTimeContractViolation(
            f"scan_as_of {scan_as_of.isoformat()} precedes the end of the {trading_date} "
            f"tape ({earliest.isoformat()}); {SCAN_TIME_CONTRACT} does not emulate "
            "Production's partial 18:00 ET snapshot")


def assert_no_reconstructed(source: AuthoritySource | str, where: str) -> None:
    """RECONSTRUCTED is not reachable from this package, whatever the rank looks like.

    A rank that matches a recorded Production run exactly is still a rank over a declared
    universe. Promoting it would make the one number nobody can check - which symbols were
    scannable that morning - look like something this code had read.
    """
    value = source.value if isinstance(source, AuthoritySource) else str(source)
    if value == AuthoritySource.RECONSTRUCTED.value:
        raise ValueError(
            f"{where} tried to emit AuthoritySource.RECONSTRUCTED; the Historical Research "
            "Scanner declares its universe and may only emit ASSUMED")
