"""Build an input tape from D2's historical files and drive it through the same engine.

The point of this module is that it produces *the same kind of tape* the live session writes.
Nothing downstream can tell a historical record from a live one, so replay exercises the real
execution and accounting path rather than a parallel backtest implementation.

One thing has to be invented, and it is flagged everywhere it appears: **there is no historical
order book.** D1 established that Bybit publishes book and trade data in realtime only. A 1m
bar therefore cannot say what the bid and ask were. The tape synthesises a two-sided book
around each price with an explicit assumed spread and depth, and stamps `book_synthetic: true`
on every record so no reader can mistake a historical fill for a measured one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Sequence

from .ledger import InputKind

SYNTHETIC_BOOK_NOTE = (
    "Historical replay: Bybit publishes orderbook only in realtime, so bid/ask are synthesised "
    "around the 1m price with an assumed spread. Fills from this tape are model output."
)


@dataclass(frozen=True)
class SyntheticBook:
    """The assumption, stated once and carried on every record it touches."""
    spread: Decimal = Decimal("0.10")      # D2 measured exactly one tick for its whole window
    depth_btc: Decimal = Decimal("5")      # per side, flat
    tick: Decimal = Decimal("0.10")

    def quantize(self, price: Decimal) -> Decimal:
        return (price / self.tick).quantize(Decimal(1)) * self.tick

    def sides(self, price: Decimal) -> tuple[list[list[str]], list[list[str]]]:
        half = self.spread / 2
        bid = self.quantize(price - half)
        ask = self.quantize(price + half)
        if ask <= bid:
            ask = bid + self.tick
        return ([[str(bid), str(self.depth_btc)]], [[str(ask), str(self.depth_btc)]])

    def describe(self) -> dict[str, str]:
        return {"spread": str(self.spread), "depth_btc": str(self.depth_btc),
                "tick": str(self.tick), "note": SYNTHETIC_BOOK_NOTE}


def _read_jsonl(path: Path, start_ms: int, end_ms: int) -> Iterator[dict[str, Any]]:
    """Stream a D2 dataset inside a window. The files hold millions of rows; none is held."""
    with path.open() as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            ts = int(row["timestamp_ms"])
            if ts < start_ms:
                continue
            if ts > end_ms:
                return
            yield row


@dataclass
class HistoricalTapeBuilder:
    """Turn D2 kline/mark/funding files into engine input records.

    Each 1m bar becomes **three** market records, in the fixed order low, high, close. Visiting
    both extremes is what lets a liquidation inside the bar be detected at all; the order is
    fixed so the tape stays deterministic. It does mean the intrabar *path* is a model: a bar
    that touched its low after its high replays in the wrong order. For a single position only
    one extreme can be the liquidating one, so detection is unaffected; the unrealised PnL
    sequence within the bar is not real.
    """
    kline_path: Path
    mark_path: Path
    funding_path: Path | None = None
    index_path: Path | None = None
    open_interest_path: Path | None = None
    book: SyntheticBook = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.book is None:
            self.book = SyntheticBook()

    def build(self, *, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        marks = {int(row["timestamp_ms"]): row
                 for row in _read_jsonl(self.mark_path, start_ms, end_ms)}
        indexes = ({int(row["timestamp_ms"]): row
                    for row in _read_jsonl(self.index_path, start_ms, end_ms)}
                   if self.index_path else {})
        # Open interest is published every 5 minutes, so a 1m bar carries the most recent
        # reading at or before it rather than an interpolation. It is research context and
        # never reaches the engine: `quote_from_payload` ignores keys it does not know, which
        # is what keeps the execution path identical to the live one.
        open_interest = sorted(
            (int(row["timestamp_ms"]), str(row["open_interest"]))
            for row in _read_jsonl(self.open_interest_path, start_ms - 3_600_000, end_ms)
        ) if self.open_interest_path else []
        funding = sorted((int(row["timestamp_ms"]), Decimal(row["funding_rate"]))
                         for row in _read_jsonl(self.funding_path, start_ms - 8 * 3_600_000, end_ms)
                         ) if self.funding_path else []
        records: list[dict[str, Any]] = []
        seq = 0
        for bar in _read_jsonl(self.kline_path, start_ms, end_ms):
            ts = int(bar["timestamp_ms"])
            mark_bar = marks.get(ts)
            if mark_bar is None:
                continue     # a trade bar with no mark bar cannot be valued; skip, do not guess
            rate = _rate_at(funding, ts)
            index_bar = indexes.get(ts)
            oi = _latest_at(open_interest, ts)
            for label, price_key in (("low", "low"), ("high", "high"), ("close", "close")):
                seq += 1
                price = Decimal(bar[price_key])
                mark = Decimal(mark_bar[price_key])
                bids, asks = self.book.sides(price)
                records.append({
                    "ts_ms": ts + {"low": 0, "high": 20_000, "close": 59_999}[label],
                    "bids": bids, "asks": asks,
                    "mark_price": str(mark), "last_price": str(price),
                    "index_price": str(Decimal(index_bar[price_key])) if index_bar else None,
                    "open_interest": oi,
                    "funding_rate": str(rate) if rate is not None else None,
                    "next_funding_time_ms": None,
                    "book_synthetic": True, "bar_ts_ms": ts, "bar_point": label,
                })
        return records

    def source_note(self) -> dict[str, Any]:
        return {
            "kline": self.kline_path.as_posix(), "mark": self.mark_path.as_posix(),
            "funding": self.funding_path.as_posix() if self.funding_path else None,
            "index": self.index_path.as_posix() if self.index_path else None,
            "open_interest": (self.open_interest_path.as_posix()
                              if self.open_interest_path else None),
            "open_interest_alignment": "last reading at or before the bar (published every 5m)",
            "book": self.book.describe(),
            "records_per_bar": 3, "bar_point_order": ["low", "high", "close"],
        }


def _latest_at(series: Sequence[tuple[int, str]], ts_ms: int) -> str | None:
    """The most recent reading at or before `ts_ms`. Never looks forward."""
    latest: str | None = None
    for stamped, value in series:
        if stamped > ts_ms:
            break
        latest = value
    return latest


def _rate_at(funding: Sequence[tuple[int, Decimal]], ts_ms: int) -> Decimal | None:
    """The most recent settled funding rate at or before `ts_ms`."""
    latest: Decimal | None = None
    for settled_ts, rate in funding:
        if settled_ts > ts_ms:
            break
        latest = rate
    return latest


#: Commands that set the run up rather than act on a quote. They sort before the market
#: record sharing their timestamp; everything else sorts after it.
LIFECYCLE_COMMANDS = ("START", "SET_LEVERAGE")


def as_tape_records(market_records: Sequence[dict[str, Any]],
                    commands: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Interleave market records with commands by timestamp.

    A trading command at time T acts on the market as of T, so it sorts *after* the market
    record carrying T: the engine must have seen the quote before it is asked to fill against
    it. A lifecycle command sorts before, because the run has to exist before a quote arrives.
    """
    entries: list[tuple[int, int, dict[str, Any]]] = []
    for record in market_records:
        entries.append((int(record["ts_ms"]), 0, {"kind": InputKind.MARKET, "payload": record}))
    for command in commands:
        order = -1 if command.get("command") in LIFECYCLE_COMMANDS else 1
        entries.append((int(command["ts_ms"]), order,
                        {"kind": InputKind.COMMAND, "payload": command}))
    entries.sort(key=lambda item: (item[0], item[1]))
    return [{"seq": index + 1, **entry} for index, (_, _, entry) in enumerate(entries)]
