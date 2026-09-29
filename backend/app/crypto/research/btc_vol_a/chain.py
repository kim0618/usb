"""Pull the option quotes that matter out of a 28-million-row daily chain file.

Each Tardis daily file is about 1.1 GB compressed and holds every quote update for every
instrument on Deribit. Only a handful of instants matter: the P1 high-confidence decision hours.
So the file is streamed once, decompressed on the fly, and everything except BTC rows at or just
before those instants is discarded without ever being stored.

The point-in-time rule is enforced while reading rather than afterwards. A row whose timestamp is
past the decision instant is dropped immediately, so a future quote cannot reach the selection
logic even by mistake.
"""
from __future__ import annotations

import csv
import gzip
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import contract as C

CACHE = Path("data/runtime/crypto/btc_vol_a/chains")
MICROS_PER_SECOND = 1_000_000

#: Retry budget for a rate-limited download, and how long to wait between attempts.
RETRIES = 5
BACKOFF_SECONDS = (60, 180, 420, 900)


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp_us: int
    option_type: str
    strike: float
    expiration_us: int
    bid: float
    ask: float
    mark: float
    bid_iv: float
    ask_iv: float
    mark_iv: float
    underlying: float

    @property
    def usable(self) -> bool:
        """Both sides quoted, not crossed, and an underlying to measure moneyness against."""
        return (self.bid > 0 and self.ask > 0 and self.ask >= self.bid
                and self.underlying > 0)


def url_for(day: str) -> str:
    year, month, date = (int(part) for part in day.split("-"))
    return C.DATASET_URL.format(year=year, month=month, day=date)


def local_path(day: str) -> Path:
    return CACHE / f"deribit_options_chain_{day}.csv.gz"


def filtered_path(day: str) -> Path:
    return CACHE / f"btc_windows_{day}.csv.gz"


def download(day: str, *, force: bool = False) -> Path:
    """Fetch one whole free daily file. Plain GET only: range and HEAD requests are blocked."""
    target = local_path(day)
    if target.exists() and not force and target.stat().st_size > 0:
        return target
    CACHE.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--max-time", "1800",
                    "-o", str(partial), url_for(day)], check=True)
    partial.replace(target)
    return target


def fetch_windows(day: str, wanted_us: list[int], tolerance_us: int, *,
                  force: bool = False) -> Path:
    """Download one day and keep only BTC rows inside the lookback windows.

    A daily file has grown from about 2 GB in 2022 to over 10 GB in 2026, and only a few minutes
    of each day are ever read. Filtering inside the download pipe keeps roughly one row in a
    thousand, so nothing large is ever written to disk and the scan afterwards is trivial.

    The filter is applied by awk on the decompressed stream because a Python CSV pass over tens
    of millions of rows is the slow part, not the network.
    """
    target = filtered_path(day)
    if target.exists() and not force and target.stat().st_size > 0:
        return target
    CACHE.mkdir(parents=True, exist_ok=True)

    clauses = " || ".join(f"($3>={t - tolerance_us} && $3<={t})" for t in sorted(wanted_us))
    program = f'NR==1 {{print; next}} $2 ~ /^BTC-/ && ({clauses}) {{print}}'
    partial = target.with_suffix(".part")
    command = (f"curl --fail --silent --show-error --max-time 1800 {url_for(day)!r} "
               f"| gzip -dc | awk -F, {program!r} | gzip -c > {str(partial)!r}")

    # Pulling a dozen multi-gigabyte files in a row earns a 429. Back off and retry rather than
    # losing the whole run to the last date, and give the service room between attempts.
    last: subprocess.CompletedProcess | None = None
    for attempt in range(RETRIES):
        last = subprocess.run(["bash", "-o", "pipefail", "-c", command],
                              capture_output=True, text=True)
        if last.returncode == 0 and partial.exists() and partial.stat().st_size > 0:
            partial.replace(target)
            return target
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    partial.unlink(missing_ok=True)
    raise RuntimeError(
        f"{day}: download failed after {RETRIES} attempts; "
        f"last stderr: {(last.stderr or '').strip()[:200]}")


def _float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def stream(path: Path, *, wanted_us: list[int], tolerance_us: int) -> Iterator[Quote]:
    """Yield BTC quotes that sit inside one of the lookback windows before a wanted instant.

    Windows are half-open and end at the instant itself, so nothing after a decision time is ever
    emitted.
    """
    windows = sorted((t - tolerance_us, t) for t in wanted_us)
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        # Fixed column offsets rather than a DictReader: a daily file holds about 28 million
        # rows, and building a dict per row roughly doubles the scan time for no benefit.
        at = {name: header.index(name) for name in (
            "symbol", "timestamp", "type", "strike_price", "expiration", "bid_price",
            "ask_price", "mark_price", "bid_iv", "ask_iv", "mark_iv", "underlying_price")}
        symbol_at, stamp_at = at["symbol"], at["timestamp"]
        index = 0
        for row in reader:
            symbol = row[symbol_at]
            if not symbol.startswith("BTC-"):
                continue
            stamp = int(row[stamp_at])
            while index < len(windows) and stamp > windows[index][1]:
                index += 1
            if index >= len(windows):
                break
            low, _high = windows[index]
            if stamp < low:
                continue
            expiration = row[at["expiration"]]
            yield Quote(
                symbol=symbol, timestamp_us=stamp, option_type=row[at["type"]],
                strike=_float(row[at["strike_price"]]),
                expiration_us=int(expiration) if expiration else 0,
                bid=_float(row[at["bid_price"]]), ask=_float(row[at["ask_price"]]),
                mark=_float(row[at["mark_price"]]), bid_iv=_float(row[at["bid_iv"]]),
                ask_iv=_float(row[at["ask_iv"]]), mark_iv=_float(row[at["mark_iv"]]),
                underlying=_float(row[at["underlying_price"]]))


def latest_before(path: Path, wanted_us: list[int], tolerance_us: int
                  ) -> dict[int, dict[str, Quote]]:
    """The freshest quote per instrument at or before each wanted instant.

    Instruments are keyed by symbol, so a later row for the same symbol simply replaces an
    earlier one; because the stream is time-ordered and cut at the instant, what remains is the
    book as it stood then.
    """
    out: dict[int, dict[str, Quote]] = {t: {} for t in wanted_us}
    ordered = sorted(wanted_us)
    for quote in stream(path, wanted_us=ordered, tolerance_us=tolerance_us):
        for instant in ordered:
            if instant - tolerance_us <= quote.timestamp_us <= instant:
                out[instant][quote.symbol] = quote
    return out


def describe(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", newline="") as handle:
        header = handle.readline().strip().split(",")
    return {"file": str(path), "bytes": path.stat().st_size, "columns": header}


def peek_rows(path: Path, limit: int = 3) -> list[dict[str, str]]:
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.DictReader(handle)
        return [row for _, row in zip(range(limit), reader)]

