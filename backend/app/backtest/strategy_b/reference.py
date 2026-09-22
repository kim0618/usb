"""Per-symbol reference inputs for Strategy B: PIT ticker metadata, split records, daily bars.

One JSON bundle per symbol under ``market_data/metadata/strategy_b/reference/``, written with
``safe_write`` under the writer lock and carrying a checksum over its own canonical payload.
It is not a manifest entry: it is a few KB read whole, and people audit it with grep and diff
(the same reasoning as the authority store).

What each part may be used for is fixed by the accessors, not by caller discipline:

* ``metadata_before(D)`` returns the latest observation dated **before** D. An observation is
  a ticker-details response requested with ``date=`` that day, so on D the scope sees D-1
  metadata at the latest. Market cap is not stored.
* ``daily_before(D)`` returns raw (``adjusted=false``) daily close/volume strictly before D -
  the scope's only price and liquidity input.
* ``splits`` holds every split record fetched, including ones executed after a replay date;
  the research layer applies only ``execution_date <= D`` and the PIT tests add future splits
  on purpose.
* ``official_daily(D)`` is D's own daily bar. It exists for the post-session sparse audit and
  for nothing else; no replay-time input accepts it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from typing import Any

from app.backtest.collector.daily_dataset import DAILY_DATA_KIND, DAILY_PROVIDER, DAILY_TIMEFRAME
from app.backtest.collector.daily_storage import existing_rows
from app.backtest.workspace.errors import WorkspaceError
from app.backtest.workspace.guards import assert_no_secret_like
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import writer_lock
from app.backtest.workspace.manifest import entries_of_kind, files_match, manifest_connection
from app.backtest.workspace.safe_write import safe_write
from app.market.symbols import normalize_symbol
from app.strategy_b.models import OfficialDailyBar
from app.strategy_b.scope import ListingStatus, PriorDailyBar, TickerMetadataAsOf
from app.strategy_b.split_adjustment import SplitRecord

REFERENCE_FORMAT = "strategy-b-reference-v1"
REFERENCE_ROOT = "market_data/metadata/strategy_b/reference"


class ReferenceError(WorkspaceError):
    code = "STRATEGY_B_REFERENCE_ERROR"


class ReferenceCorrupt(ReferenceError):
    code = "STRATEGY_B_REFERENCE_CORRUPT"


class ReferenceMissing(ReferenceError):
    code = "STRATEGY_B_REFERENCE_MISSING"


@dataclass(frozen=True, slots=True)
class DailyReferenceBar:
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class MetadataObservation:
    as_of_date: date
    metadata: TickerMetadataAsOf | None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if (self.metadata is None) == (self.unavailable_reason is None):
            raise ValueError("an observation has metadata or an unavailable reason, not both")


@dataclass(frozen=True)
class ReferenceBundle:
    symbol: str
    daily: tuple[DailyReferenceBar, ...]
    metadata: tuple[MetadataObservation, ...]
    splits: tuple[SplitRecord, ...]
    sources: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, key in (("daily", "session_date"), ("metadata", "as_of_date"),
                          ("splits", "execution_date")):
            stamps = [getattr(item, key) for item in getattr(self, name)]
            if stamps != sorted(set(stamps)):
                raise ReferenceCorrupt(f"{self.symbol} {name} must be strictly increasing")

    # --- PIT accessors ---------------------------------------------------------------------

    def metadata_before(self, day: date) -> MetadataObservation | None:
        earlier = [item for item in self.metadata if item.as_of_date < day]
        return earlier[-1] if earlier else None

    def daily_before(self, day: date) -> tuple[PriorDailyBar, ...]:
        return tuple(PriorDailyBar(bar.session_date, bar.close, bar.volume)
                     for bar in self.daily if bar.session_date < day)

    def official_daily(self, day: date) -> OfficialDailyBar | None:
        """AUDIT ONLY: D's own daily bar, which does not exist until D's tape is over."""
        for bar in self.daily:
            if bar.session_date == day:
                return OfficialDailyBar(day, bar.open, bar.high, bar.low, bar.close, bar.volume)
        return None

    # --- serialization ----------------------------------------------------------------------

    def payload(self) -> dict[str, Any]:
        return {
            "format": REFERENCE_FORMAT, "symbol": self.symbol, "sources": dict(sorted(self.sources.items())),
            "daily": [[bar.session_date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume]
                      for bar in self.daily],
            "metadata": [_observation_payload(item) for item in self.metadata],
            "splits": [[item.execution_date.isoformat(), item.split_from, item.split_to]
                       for item in self.splits],
        }

    @property
    def checksum(self) -> str:
        return hashlib.sha256(_canonical(self.payload()).encode()).hexdigest()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ReferenceBundle":
        if payload.get("format") != REFERENCE_FORMAT:
            raise ReferenceCorrupt(f"reference format {payload.get('format')!r} is not {REFERENCE_FORMAT}")
        try:
            return cls(
                symbol=str(payload["symbol"]),
                daily=tuple(DailyReferenceBar(date.fromisoformat(d), float(o), float(h), float(l),
                                              float(c), float(v))
                            for d, o, h, l, c, v in payload["daily"]),
                metadata=tuple(_observation_from(str(payload["symbol"]), item)
                               for item in payload["metadata"]),
                splits=tuple(SplitRecord(date.fromisoformat(d), float(f), float(t))
                             for d, f, t in payload["splits"]),
                sources=dict(payload["sources"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ReferenceCorrupt(f"reference payload is malformed: {error}") from None


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _observation_payload(item: MetadataObservation) -> dict[str, Any]:
    meta = item.metadata
    if meta is None:
        return {"as_of_date": item.as_of_date.isoformat(), "unavailable_reason": item.unavailable_reason}
    return {"as_of_date": item.as_of_date.isoformat(), "security_type": meta.security_type,
            "primary_exchange": meta.primary_exchange, "market": meta.market,
            "listing_status": meta.listing_status.value,
            "list_date": None if meta.list_date is None else meta.list_date.isoformat(),
            "test_issue": meta.test_issue}


def _observation_from(symbol: str, item: Mapping[str, Any]) -> MetadataObservation:
    day = date.fromisoformat(item["as_of_date"])
    if "unavailable_reason" in item:
        return MetadataObservation(day, None, str(item["unavailable_reason"]))
    return MetadataObservation(day, TickerMetadataAsOf(
        symbol=symbol, as_of_date=day, security_type=item["security_type"],
        primary_exchange=item["primary_exchange"], market=item["market"],
        listing_status=ListingStatus(item["listing_status"]),
        list_date=None if item["list_date"] is None else date.fromisoformat(item["list_date"]),
        test_issue=item["test_issue"]))


def relative_path(symbol: str) -> str:
    return f"{REFERENCE_ROOT}/{normalize_symbol(symbol)}.json"


def write_bundle(workspace: Workspace, bundle: ReferenceBundle) -> str:
    """Write one bundle under the writer lock. Returns its checksum."""
    document = {**bundle.payload(), "checksum": bundle.checksum}
    assert_no_secret_like(document, where=f"reference {bundle.symbol}")
    with writer_lock(workspace, purpose=f"strategy_b_reference:{bundle.symbol}"):
        with safe_write(workspace.root / relative_path(bundle.symbol)) as handle:
            handle.partial_path.write_bytes(
                (json.dumps(document, sort_keys=True, indent=1, allow_nan=False) + "\n").encode())
    return bundle.checksum


def load_bundle(workspace: Workspace, symbol: str) -> ReferenceBundle:
    path = workspace.root / relative_path(symbol)
    if not path.is_file():
        raise ReferenceMissing(f"{relative_path(symbol)} does not exist")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReferenceCorrupt(f"{relative_path(symbol)} is unreadable: {error}") from None
    stored = document.pop("checksum", None)
    symbol = normalize_symbol(symbol)
    if document.get("symbol") != symbol:
        raise ReferenceCorrupt(f"{relative_path(symbol)} holds {document.get('symbol')!r}")
    bundle = ReferenceBundle.from_payload(document)
    if bundle.checksum != stored:
        raise ReferenceCorrupt(f"{relative_path(symbol)} checksum does not match its content")
    return bundle


# --- builders -------------------------------------------------------------------------------


def metadata_from_details(symbol: str, details: Mapping[str, Mapping[str, Any]]) -> tuple[MetadataObservation, ...]:
    """Ticker details responses requested with ``date=`` -> PIT observations, oldest first."""
    observations = []
    for stamp in sorted(details):
        day = date.fromisoformat(stamp)
        item = details[stamp]
        if "error" in item:
            observations.append(MetadataObservation(day, None, f"DETAILS_{item['error']}"))
            continue
        if item.get("type") is None or item.get("primary_exchange") is None or item.get("active") is None:
            observations.append(MetadataObservation(day, None, "DETAILS_INCOMPLETE"))
            continue
        observations.append(MetadataObservation(day, TickerMetadataAsOf(
            symbol=symbol, as_of_date=day, security_type=str(item["type"]),
            primary_exchange=str(item["primary_exchange"]), market=str(item.get("market") or ""),
            listing_status=ListingStatus.ACTIVE if item["active"] else ListingStatus.INACTIVE,
            list_date=None if not item.get("list_date") else date.fromisoformat(str(item["list_date"])),
            # Massive ticker details carry no test-issue field; None means "source has none".
            test_issue=None)))
    return tuple(observations)


def splits_from_raw(items: Sequence[Mapping[str, Any]]) -> tuple[SplitRecord, ...]:
    records = [SplitRecord(date.fromisoformat(str(item["execution_date"])), float(item["split_from"]),
                           float(item["split_to"])) for item in items]
    return tuple(sorted(records, key=lambda record: record.execution_date))


def daily_from_raw(rows: Sequence[Sequence[Any]]) -> tuple[DailyReferenceBar, ...]:
    """Raw daily aggregates ``[bar_start_iso, o, h, l, c, v, vw, n]``; the ET date is the session."""
    from datetime import datetime

    from app.integrations.massive.minute_bars import ET

    bars = []
    for stamp, open_, high, low, close, volume, *_ in rows:
        if None in (open_, high, low, close, volume):
            raise ReferenceCorrupt(f"daily row {stamp} has null OHLCV")
        day = datetime.fromisoformat(stamp).astimezone(ET).date()
        bars.append(DailyReferenceBar(day, float(open_), float(high), float(low), float(close), float(volume)))
    return tuple(bars)


def daily_from_workspace(workspace: Workspace, symbol: str) -> tuple[tuple[DailyReferenceBar, ...], str]:
    """A verified A ``daily_bars`` entry (raw, adjusted=false), read-only, volume unrounded."""
    symbol = normalize_symbol(symbol)
    with manifest_connection(workspace, create=False) as connection:
        complete = [row for row in entries_of_kind(connection, provider=DAILY_PROVIDER,
                                                   data_kind=DAILY_DATA_KIND, timeframe=DAILY_TIMEFRAME)
                    if str(row["symbol"]) == symbol and str(row["status"]) == "COMPLETE"]
        if len(complete) != 1:
            raise ReferenceMissing(f"{symbol} has {len(complete)} COMPLETE daily entries, expected 1")
        matched, detail = files_match(connection, int(complete[0]["id"]), workspace=workspace)
        if not matched:
            raise ReferenceCorrupt(f"{symbol} daily entry: {detail}")
        source = (f"workspace:{DAILY_DATA_KIND}/{DAILY_TIMEFRAME}:entry={complete[0]['id']}:"
                  f"checksum={complete[0]['checksum']}")
    bars = tuple(DailyReferenceBar(row.session_date, row.open, row.high, row.low, row.close, row.volume)
                 for row in existing_rows(workspace, DAILY_PROVIDER, symbol))
    return bars, source
