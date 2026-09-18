"""The normalized row: what one Massive minute bar looks like in the durable store.

Columns follow the collector contract: provider, symbol, both timestamps, the trading
date, the session part, OHLCV, and the two optional provider extras. The timestamp is
the bar start, as Massive documents it, and no availability column is written at this
stage - when a bar became visible to a live strategy is a replay question, and inventing
an answer now would be worse than leaving the column out.

Numbers are stored as 64-bit floats, and ``audit_precision`` proves that choice for every
byte the provider sent: each numeric literal in the response is converted to a float and
back, and a value that does not come back identical stops the collection instead of being
rounded into the store. Volume is never cast to an integer - Massive reports fractional
share volume on most minutes.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import re
from typing import Any

import pyarrow as pa

from app.backtest.collector.errors import PrecisionLoss
from app.integrations.massive.minute_bars import ET, MinuteBar, classify
from app.market.calendar import TradingSessionWindow


DATASET_SCHEMA_VERSION = 1
TIMEFRAME = "1minute"
DATA_KIND = "minute_bars"

SCHEMA = pa.schema([
    pa.field("provider", pa.string(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("timestamp_utc", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("timestamp_et", pa.timestamp("us", tz="America/New_York"), nullable=False),
    pa.field("trading_date", pa.date32(), nullable=False),
    pa.field("session", pa.string(), nullable=False),
    pa.field("open", pa.float64()),
    pa.field("high", pa.float64()),
    pa.field("low", pa.float64()),
    pa.field("close", pa.float64()),
    pa.field("volume", pa.float64()),
    pa.field("transactions", pa.float64()),
    pa.field("vwap", pa.float64()),
], metadata={
    b"dataset_schema_version": str(DATASET_SCHEMA_VERSION).encode(),
    b"timestamp_authority": b"bar_start",
    b"bar_interval": b"PT1M",
    b"adjusted": b"false",
    b"vwap_authority": b"none: provider vw leaves the [low, high] range outside regular hours",
    b"volume_semantics": b"provider value as sent, fractional shares preserved",
})

# Only the numeric fields of an aggregate row. ``n`` is a count and needs no audit.
AUDITED_FIELDS = ("o", "h", "l", "c", "v", "vw")
NUMERIC_LITERAL = re.compile(
    rb'"(' + b"|".join(name.encode() for name in AUDITED_FIELDS)
    + rb')"\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)')
VIOLATION_SAMPLE_LIMIT = 5


@dataclass
class PrecisionAudit:
    """Evidence that float64 storage returns exactly what the provider sent."""

    values_checked: int = 0
    max_decimal_places: int = 0
    max_significant_digits: int = 0
    violations: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.violations


def audit_precision(body: bytes, audit: PrecisionAudit) -> None:
    """Round-trip every numeric literal in one response body through float64.

    A literal that does not come back identical raises, because the alternative is a
    store whose prices differ from the provider's own in a digit nobody looked at.
    """
    for name, literal in NUMERIC_LITERAL.findall(body):
        audit.values_checked += 1
        text = literal.decode("ascii")
        value = Decimal(text)
        audit.max_decimal_places = max(audit.max_decimal_places, max(-value.as_tuple().exponent, 0))
        audit.max_significant_digits = max(audit.max_significant_digits, len(value.as_tuple().digits))
        as_float = float(text)
        if repr(as_float) == text or Decimal(repr(as_float)) == value:
            continue
        if len(audit.violations) < VIOLATION_SAMPLE_LIMIT:
            audit.violations.append(f"field={name.decode()} decimal_places="
                                    f"{max(-value.as_tuple().exponent, 0)} "
                                    f"significant_digits={len(value.as_tuple().digits)}")
    if audit.violations:
        raise PrecisionLoss(
            "a provider number does not survive float64 storage: "
            + "; ".join(audit.violations))


def session_label(bar: MinuteBar, window: TradingSessionWindow) -> str:
    return classify(bar.bar_start, window).value


@dataclass
class RowBuffer:
    """Columnar accumulator: one Arrow table per flush, no per-row Python object kept."""

    provider: str
    symbol: str
    columns: dict[str, list[Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.clear()

    def __len__(self) -> int:
        return len(self.columns["timestamp_utc"])

    def clear(self) -> None:
        self.columns = {name: [] for name in SCHEMA.names}

    def add(self, bar: MinuteBar, window: TradingSessionWindow) -> None:
        moment: datetime = bar.bar_start
        local = moment.astimezone(ET)
        append = self.columns
        append["provider"].append(self.provider)
        append["symbol"].append(self.symbol)
        append["timestamp_utc"].append(moment)
        append["timestamp_et"].append(local)
        append["trading_date"].append(local.date())
        append["session"].append(session_label(bar, window))
        append["open"].append(bar.open)
        append["high"].append(bar.high)
        append["low"].append(bar.low)
        append["close"].append(bar.close)
        append["volume"].append(bar.volume)
        append["transactions"].append(bar.trades)
        append["vwap"].append(bar.vwap)

    def to_table(self) -> pa.Table:
        return pa.table({name: pa.array(values, type=SCHEMA.field(name).type)
                         for name, values in self.columns.items()}, schema=SCHEMA)


def describe_schema() -> tuple[str, ...]:
    return tuple(f"{field.name}:{field.type}" for field in SCHEMA)


def table_row_count(paths: Sequence[Any]) -> int:  # pragma: no cover - convenience for the CLI
    import pyarrow.parquet as pq

    return sum(pq.ParquetFile(path).metadata.num_rows for path in paths)
