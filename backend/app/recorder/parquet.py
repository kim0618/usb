"""Filesystem-backed Parquet storage for validated market bars."""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.core.exceptions import DataError
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.symbols import normalize_symbol


DAILY_COLUMNS = [
    "symbol", "trading_date", "open", "high", "low", "close", "volume",
    "observed_at", "available_at",
]
MINUTE_COLUMNS = [
    "symbol", "timestamp", "session", "open", "high", "low", "close", "volume",
    "observed_at", "available_at",
]


class ParquetMarketDataStorage:
    """Merge-on-write storage with stable key ordering and last-write-wins upserts."""

    def __init__(self, root: Path, source: str = "unknown") -> None:
        self.root = Path(root)
        self.source = source

    def write_daily_bars(self, bars: Iterable[DailyBar]) -> int:
        grouped: dict[tuple[int, str], list[DailyBar]] = {}
        for bar in bars:
            grouped.setdefault((bar.trading_date.year, bar.symbol), []).append(bar)
        for (year, symbol), group in grouped.items():
            path = self.root / "daily" / str(year) / f"{symbol}.parquet"
            frame = pd.DataFrame([self._daily_row(bar) for bar in group], columns=DAILY_COLUMNS)
            self._merge_write(path, frame, ["symbol", "trading_date"], ["trading_date", "symbol"])
        return sum(len(group) for group in grouped.values())

    def write_minute_bars(self, bars: Iterable[MinuteBar]) -> int:
        grouped: dict[tuple[date, str], list[MinuteBar]] = {}
        for bar in bars:
            grouped.setdefault((bar.timestamp.date(), bar.symbol), []).append(bar)
        for (day, symbol), group in grouped.items():
            path = (
                self.root / "minute" / f"{day.year:04d}" / f"{day.month:02d}"
                / f"{day.day:02d}" / f"{symbol}.parquet"
            )
            frame = pd.DataFrame([self._minute_row(bar) for bar in group], columns=MINUTE_COLUMNS)
            self._merge_write(path, frame, ["symbol", "timestamp"], ["timestamp", "symbol"])
        return sum(len(group) for group in grouped.values())

    def read_daily_bars(
        self,
        symbols: Sequence[str],
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        wanted = {normalize_symbol(symbol) for symbol in symbols}
        frames = [self._read(path) for path in sorted((self.root / "daily").glob("*/*.parquet"))]
        frame = self._concat(frames, DAILY_COLUMNS)
        if frame.empty:
            return []
        frame = frame[frame["symbol"].isin(wanted)]
        if start is not None:
            frame = frame[frame["trading_date"] >= start]
        if end is not None:
            frame = frame[frame["trading_date"] <= end]
        frame = frame.sort_values(["trading_date", "symbol"], kind="stable")
        return [DailyBar.model_validate(row) for row in frame.to_dict("records")]

    def read_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime | None = None,
        end: datetime | None = None,
        session: MarketSession | None = None,
    ) -> list[MinuteBar]:
        wanted = {normalize_symbol(symbol) for symbol in symbols}
        frames = [self._read(path) for path in sorted((self.root / "minute").glob("*/*/*/*.parquet"))]
        frame = self._concat(frames, MINUTE_COLUMNS)
        if frame.empty:
            return []
        frame = frame[frame["symbol"].isin(wanted)]
        if start is not None:
            frame = frame[frame["timestamp"] >= self._utc(start)]
        if end is not None:
            frame = frame[frame["timestamp"] <= self._utc(end)]
        if session is not None:
            frame = frame[frame["session"] == session.value]
        frame = frame.sort_values(["timestamp", "symbol"], kind="stable")
        return [MinuteBar.model_validate(row) for row in frame.to_dict("records")]

    def _merge_write(
        self,
        path: Path,
        incoming: pd.DataFrame,
        keys: list[str],
        ordering: list[str],
    ) -> None:
        try:
            existing = self._read(path) if path.exists() else None
            incoming = incoming.drop_duplicates(subset=keys, keep="last")
            if existing is not None and not existing.empty:
                current = existing[keys + ["available_at"]].rename(
                    columns={"available_at": "existing_available_at"}
                )
                corrections = incoming.merge(current, on=keys, how="inner")
                if not corrections.empty and (
                    corrections["available_at"] < corrections["existing_available_at"]
                ).any():
                    raise DataError("Correction available_at must not move backward")
            merged = (
                pd.concat([existing, incoming], ignore_index=True)
                if existing is not None and not existing.empty
                else incoming.copy()
            )
            merged = merged.drop_duplicates(subset=keys, keep="last")
            merged = merged.sort_values(ordering, kind="stable").reset_index(drop=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pandas(merged, preserve_index=False)
            metadata = dict(table.schema.metadata or {})
            metadata[b"usb.source"] = self.source.encode("utf-8")
            table = table.replace_schema_metadata(metadata)
            temporary = path.with_suffix(".parquet.tmp")
            pq.write_table(table, temporary)
            temporary.replace(path)
        except DataError:
            raise
        except Exception as exc:
            raise DataError(f"Unable to write market data to {path}") from exc

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            raise DataError(f"Unable to read market data from {path}") from exc

    @staticmethod
    def _concat(frames: list[pd.DataFrame], columns: list[str]) -> pd.DataFrame:
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)

    @classmethod
    def _daily_row(cls, bar: DailyBar) -> dict[str, object]:
        row = bar.model_dump()
        row["observed_at"] = cls._utc(bar.observed_at)
        row["available_at"] = cls._utc(bar.available_at)
        return row

    @classmethod
    def _minute_row(cls, bar: MinuteBar) -> dict[str, object]:
        row = bar.model_dump(mode="json")
        row["timestamp"] = cls._utc(bar.timestamp)
        row["observed_at"] = cls._utc(bar.observed_at)
        row["available_at"] = cls._utc(bar.available_at)
        row["session"] = bar.session.value
        return row

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise DataError("Parquet datetime bounds must be timezone-aware")
        return value.astimezone(timezone.utc)
