"""Parse the mirrored minute pages once, into one memory-mapped row store plus a coverage index.

The source is one file per symbol covering four months, and a replay walks sessions, so reading
the source directly would parse every symbol's whole file on every one of 84 sessions. This
module parses each symbol once and appends its rows to two flat binary files (timestamps and
values), recording where each (symbol, session) slice starts and ends. A session then loads by
slicing a memory map: no parse, and nothing held in RAM beyond the rows being used. That matters
here, since the machine has about 5 GB free and the window holds tens of millions of rows.

Coverage is decided by ledgers, never by the presence of bars. A session a COMPLETE ledger's range
covers is covered whether or not it has a bar, which is how a genuinely quiet session is told
apart from one that was never fetched. When two COMPLETE ledgers cover the same session, the one
collected last is used and the overlap is counted; bars are never merged across ledgers.

The cache is a derived runtime artifact and not a source of truth. It lives in a directory named by
the source dataset digest, so a changed mirror can never be read through an old cache.

Sanitation is recorded, not hidden: a row outside 04:00-20:00 ET, a timestamp that is not an exact
minute, or a non-positive price is dropped, and a non-positive vwap or negative transaction count
is blanked, and every such action is counted in the cache metadata.
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil

import numpy as np

from app.backtest.strategy_b.dataset import boundaries_for
from app.market.calendar import MarketCalendar
from app.strategy_b.models import MomentumBar
from app.strategy_b.session import ET, SessionBoundaries

CACHE_VERSION = "b-e0-session-cache-v1"
VALUE_COLUMNS = ("open", "high", "low", "close", "volume", "vwap", "transactions")
MS_PER_MINUTE = 60_000


class CacheInvalid(RuntimeError):
    """The cache does not belong to the mirror it is being read against."""


# ---- windows ----------------------------------------------------------------------------------


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def session_windows(calendar: MarketCalendar, sessions: Sequence[date]) -> dict[str, tuple[int, ...]]:
    """Per session: (ET midnight, 04:00, regular open, regular close, 20:00, next ET midnight) in ms."""
    out = {}
    for day in sessions:
        bounds = boundaries_for(calendar, day)
        midnight = datetime.combine(day, time(0, 0), tzinfo=ET)
        out[day.isoformat()] = (
            _ms(midnight), _ms(bounds.premarket_start), _ms(bounds.regular_open),
            _ms(bounds.regular_close), _ms(bounds.after_close), _ms(midnight + timedelta(days=1)))
    return out


# ---- worker (one member) ----------------------------------------------------------------------


def _assign_sessions(ledgers: Sequence[Mapping], sessions: Sequence[str]) -> tuple[dict, list, int]:
    """session -> ledger name. Latest collected_at wins; ties break by name. Bars never merge."""
    chosen: dict[str, str] = {}
    overlaps = 0
    ranked = sorted(ledgers, key=lambda l: (l.get("collected_at") or "", l["_name"]), reverse=True)
    for day in sessions:
        covering = [l for l in ranked
                    if l["start"] <= day <= l["end"]
                    and day not in set(l.get("unavailable_rolling_window") or ())]
        if len(covering) > 1:
            overlaps += 1
        if covering:
            chosen[day] = covering[0]["_name"]
    uncovered = [day for day in sessions if day not in chosen]
    return chosen, uncovered, overlaps


def _column(rows: list, key: str, default: float) -> np.ndarray:
    return np.fromiter((row.get(key, default) if row.get(key) is not None else default
                        for row in rows), dtype=np.float64, count=len(rows))


def parse_member(mirror_root: str, symbol: str, ledger_paths: Sequence[str],
                 sessions: Sequence[str], windows: Mapping[str, tuple[int, ...]]) -> dict:
    """Everything one member contributes. Runs in a worker process; returns plain arrays."""
    root = Path(mirror_root)
    ledgers = []
    for rel in ledger_paths:
        ledger = json.loads((root / rel).read_text(encoding="utf-8"))
        ledger["_name"] = rel
        ledgers.append(ledger)
    chosen, uncovered, overlaps = _assign_sessions(ledgers, sessions)

    counts = {"outside_window": 0, "non_minute": 0, "bad_price": 0, "vwap_blanked": 0,
              "transactions_blanked": 0}
    t_parts, v_parts = [], []
    for ledger in ledgers:
        owned = sorted(day for day, name in chosen.items() if name == ledger["_name"])
        if not owned:
            continue
        folder = (root / ledger["_name"]).parent
        rows: list = []
        for page in ledger.get("pages") or ():
            body = json.loads(gzip.decompress((folder / page["file"]).read_bytes()))
            rows.extend(body.get("results") or ())
        if not rows:
            continue
        t = np.fromiter((row["t"] for row in rows), dtype=np.int64, count=len(rows))
        values = np.column_stack([
            _column(rows, "o", math.nan), _column(rows, "h", math.nan), _column(rows, "l", math.nan),
            _column(rows, "c", math.nan), _column(rows, "v", math.nan),
            _column(rows, "vw", math.nan), _column(rows, "n", math.nan)])
        keep = np.zeros(len(t), dtype=bool)
        for day in owned:
            midnight, start, _, _, stop, next_midnight = windows[day]
            on_day = (t >= midnight) & (t < next_midnight)
            inside = on_day & (t >= start) & (t < stop)
            counts["outside_window"] += int(np.count_nonzero(on_day & ~inside))
            keep |= inside
        exact = (t % MS_PER_MINUTE) == 0
        counts["non_minute"] += int(np.count_nonzero(keep & ~exact))
        prices_ok = np.all(np.isfinite(values[:, :4]) & (values[:, :4] > 0), axis=1)
        volume_ok = np.isfinite(values[:, 4]) & (values[:, 4] >= 0)
        counts["bad_price"] += int(np.count_nonzero(keep & exact & ~(prices_ok & volume_ok)))
        keep &= exact & prices_ok & volume_ok
        t, values = t[keep], values[keep]
        bad_vwap = ~np.isnan(values[:, 5]) & ~(values[:, 5] > 0)
        counts["vwap_blanked"] += int(np.count_nonzero(bad_vwap))
        values[bad_vwap, 5] = math.nan
        bad_n = ~np.isnan(values[:, 6]) & ~(values[:, 6] >= 0)
        counts["transactions_blanked"] += int(np.count_nonzero(bad_n))
        values[bad_n, 6] = math.nan
        t_parts.append(t)
        v_parts.append(values)

    t_all = np.concatenate(t_parts) if t_parts else np.zeros(0, dtype=np.int64)
    v_all = np.concatenate(v_parts) if v_parts else np.zeros((0, len(VALUE_COLUMNS)))
    order = np.argsort(t_all, kind="stable")
    t_all, v_all = t_all[order], v_all[order]
    if t_all.size > 1 and np.any(np.diff(t_all) <= 0):
        raise CacheInvalid(f"{symbol}: timestamps are not strictly increasing after assignment")

    stats = []
    for day in sessions:
        if day not in chosen:
            continue
        midnight, start, open_, close, stop, _ = windows[day]
        lo, hi = np.searchsorted(t_all, start, "left"), np.searchsorted(t_all, stop, "left")
        regular = (t_all[lo:hi] >= open_) & (t_all[lo:hi] < close)
        volume = v_all[lo:hi, 4]
        stats.append((day, int(lo), int(hi), int(hi - lo), int(np.count_nonzero(regular)),
                      float(volume.sum()), float(volume[regular].sum()),
                      int(t_all[lo]) if hi > lo else -1, int(t_all[hi - 1]) if hi > lo else -1))
    return {"symbol": symbol, "t": t_all, "v": v_all, "stats": stats, "uncovered": uncovered,
            "overlap_sessions": overlaps, "counts": counts}


# ---- build ------------------------------------------------------------------------------------


def cache_dir(cache_root: Path, source_digest: str) -> Path:
    return cache_root / source_digest[:16]


def build(mirror_root: Path, cache_root: Path, *, manifest: Mapping, universe, calendar: MarketCalendar,
          workers: int | None = None, progress=None) -> Path:
    """Build (or reuse) the cache for this mirror. Returns its directory."""
    source_digest = manifest["dataset_digest"]
    target = cache_dir(cache_root, source_digest)
    meta_path = target / "cache_meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_dataset_digest") == source_digest and meta.get("cache_version") == CACHE_VERSION:
            return target

    members = universe.symbols
    first = min(universe.members[s].fetch_start for s in members)
    last = max(universe.members[s].fetch_end for s in members)
    from app.dev.fetch_strategy_c_selection_raw import sessions_between
    sessions = sessions_between(calendar, first, last)
    windows = session_windows(calendar, sessions)
    session_index = {day.isoformat(): i for i, day in enumerate(sessions)}

    building = target.with_name(target.name + ".building")
    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)
    ledgers_by_member = manifest["ledgers_by_member"]

    rows_written = 0
    index: dict[str, list] = {name: [] for name in (
        "symbol", "session", "start", "end", "rows", "regular_rows", "volume", "regular_volume",
        "first_ts", "last_ts")}
    uncovered: dict[str, list[str]] = {}
    overlaps: dict[str, int] = {}
    totals = {"outside_window": 0, "non_minute": 0, "bad_price": 0, "vwap_blanked": 0,
              "transactions_blanked": 0}
    t_hash, v_hash = hashlib.sha256(), hashlib.sha256()
    workers = workers or min(4, os.cpu_count() or 1)
    in_flight = max(2, workers * 3)
    """Futures are consumed strictly in symbol order and at most ``in_flight`` are outstanding.
    Submitting all 3,882 at once would let finished results pile up behind a slow one, a few MB
    each, which this machine cannot hold."""

    def consume(result: dict) -> None:
        nonlocal rows_written
        symbol, t, v = result["symbol"], result["t"], result["v"]
        t_bytes, v_bytes = t.astype("<i8").tobytes(), v.astype("<f8").tobytes()
        t_file.write(t_bytes)
        v_file.write(v_bytes)
        t_hash.update(t_bytes)
        v_hash.update(v_bytes)
        for day, lo, hi, rows, regular, volume, regular_volume, first_ts, last_ts in result["stats"]:
            index["symbol"].append(symbol)
            index["session"].append(session_index[day])
            index["start"].append(rows_written + lo)
            index["end"].append(rows_written + hi)
            index["rows"].append(rows)
            index["regular_rows"].append(regular)
            index["volume"].append(volume)
            index["regular_volume"].append(regular_volume)
            index["first_ts"].append(first_ts)
            index["last_ts"].append(last_ts)
        rows_written += len(t)
        if result["uncovered"]:
            uncovered[symbol] = result["uncovered"]
        if result["overlap_sessions"]:
            overlaps[symbol] = result["overlap_sessions"]
        for key, value in result["counts"].items():
            totals[key] += value

    from collections import deque
    pending: deque = deque()
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool, \
            (building / "t.bin").open("wb") as t_file, (building / "v.bin").open("wb") as v_file:
        for symbol in members:
            member = universe.members[symbol]
            needed = [d.isoformat() for d in sessions if member.fetch_start <= d <= member.fetch_end]
            pending.append(pool.submit(parse_member, str(mirror_root), symbol,
                                       ledgers_by_member.get(symbol, ()), needed,
                                       {d: windows[d] for d in needed}))
            while len(pending) >= in_flight:
                consume(pending.popleft().result())
                done += 1
                if progress is not None and done % 250 == 0:
                    progress(done, len(members))
        while pending:
            consume(pending.popleft().result())
            done += 1
        if progress is not None:
            progress(done, len(members))

    symbols = list(members)
    column = {s: j for j, s in enumerate(symbols)}
    np.savez(building / "coverage_index.npz",
             symbol=np.array([column[s] for s in index["symbol"]], dtype=np.int32),
             session=np.array(index["session"], dtype=np.int32),
             start=np.array(index["start"], dtype=np.int64),
             end=np.array(index["end"], dtype=np.int64),
             rows=np.array(index["rows"], dtype=np.int64),
             regular_rows=np.array(index["regular_rows"], dtype=np.int64),
             volume=np.array(index["volume"], dtype=np.float64),
             regular_volume=np.array(index["regular_volume"], dtype=np.float64),
             first_ts=np.array(index["first_ts"], dtype=np.int64),
             last_ts=np.array(index["last_ts"], dtype=np.int64))
    index_sha = hashlib.sha256((building / "coverage_index.npz").read_bytes()).hexdigest()
    meta = {
        "cache_version": CACHE_VERSION,
        "source_dataset_digest": source_digest,
        "universe_sha256": universe.sha256,
        "symbols": symbols,
        "sessions": [d.isoformat() for d in sessions],
        "rows": rows_written,
        "value_columns": list(VALUE_COLUMNS),
        "t_sha256": t_hash.hexdigest(),
        "v_sha256": v_hash.hexdigest(),
        "index_sha256": index_sha,
        "uncovered_sessions": uncovered,
        "overlap_sessions": overlaps,
        "sanitation": totals,
    }
    meta["cache_digest"] = hashlib.sha256(
        f"{meta['t_sha256']}\n{meta['v_sha256']}\n{index_sha}".encode()).hexdigest()
    (building / "cache_meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n",
                                              encoding="utf-8")
    if target.exists():
        shutil.rmtree(target)
    building.replace(target)
    return target


# ---- read -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverageRow:
    """What the cache knows about one covered (symbol, session), without loading a bar."""

    start: int
    end: int
    rows: int
    regular_rows: int
    volume: float
    regular_volume: float
    first_ts: int
    last_ts: int


class SessionCache:
    """Memory-mapped rows plus the coverage index. Slices are read lazily per (symbol, session)."""

    def __init__(self, directory: Path, *, expected_source_digest: str | None = None) -> None:
        self.directory = directory
        self.meta = json.loads((directory / "cache_meta.json").read_text(encoding="utf-8"))
        if self.meta.get("cache_version") != CACHE_VERSION:
            raise CacheInvalid(f"cache version {self.meta.get('cache_version')} != {CACHE_VERSION}")
        if expected_source_digest is not None and \
                self.meta["source_dataset_digest"] != expected_source_digest:
            raise CacheInvalid("the cache was built from a different mirror")
        rows = int(self.meta["rows"])
        self._t = np.memmap(directory / "t.bin", dtype="<i8", mode="r", shape=(rows,)) if rows else \
            np.zeros(0, dtype=np.int64)
        self._v = np.memmap(directory / "v.bin", dtype="<f8", mode="r",
                            shape=(rows, len(VALUE_COLUMNS))) if rows else \
            np.zeros((0, len(VALUE_COLUMNS)))
        self.symbols: tuple[str, ...] = tuple(self.meta["symbols"])
        self.sessions: tuple[date, ...] = tuple(date.fromisoformat(d) for d in self.meta["sessions"])
        with np.load(directory / "coverage_index.npz") as index:
            arrays = {name: index[name] for name in index.files}
        self._rows: dict[tuple[str, date], CoverageRow] = {}
        for k in range(len(arrays["symbol"])):
            key = (self.symbols[int(arrays["symbol"][k])], self.sessions[int(arrays["session"][k])])
            self._rows[key] = CoverageRow(
                int(arrays["start"][k]), int(arrays["end"][k]), int(arrays["rows"][k]),
                int(arrays["regular_rows"][k]), float(arrays["volume"][k]),
                float(arrays["regular_volume"][k]), int(arrays["first_ts"][k]),
                int(arrays["last_ts"][k]))

    @property
    def digest(self) -> str:
        return self.meta["cache_digest"]

    def coverage(self, symbol: str, session: date) -> CoverageRow | None:
        """None means no COMPLETE ledger covers this session. A covered empty session has rows=0."""
        return self._rows.get((symbol, session))

    def covered_pairs(self) -> int:
        return len(self._rows)

    def bars(self, symbol: str, session: date, boundaries: SessionBoundaries) -> tuple[MomentumBar, ...]:
        """The session's actual bars, in order. Never a synthetic or filled bar."""
        row = self._rows.get((symbol, session))
        if row is None:
            raise KeyError(f"{symbol} {session} is not covered by the mirror")
        t = np.asarray(self._t[row.start:row.end])
        v = np.asarray(self._v[row.start:row.end])
        out = []
        for k in range(row.rows):
            stamp = datetime.fromtimestamp(int(t[k]) / 1000, tz=ET)
            o, h, l, c, vol, vwap, n = (float(x) for x in v[k])
            out.append(MomentumBar(
                timestamp=stamp, open=o, high=h, low=l, close=c, volume=vol,
                session=boundaries.classify(stamp),
                vwap=None if math.isnan(vwap) else vwap,
                transactions=None if math.isnan(n) else n))
        return tuple(out)
