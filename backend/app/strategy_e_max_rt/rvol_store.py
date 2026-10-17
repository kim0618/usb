"""E-RT3: a compact Kiwoom-native store of the premarket dollar volume the frozen RVOL divides by.

The frozen definition is read from the research code, never restated by hand
(``strategy_e1_premarket.premarket``):

* a session is *staged* when it has at least one bar starting in [04:00, 09:24] **and** a 09:30 bar;
* ``pm_dollar_volume`` = sum over those premarket bars of (VWAP when finite, else close) x volume.
  Kiwoom charts carry no VWAP, so the close is used, exactly as the frozen fallback does;
* the denominator of session D is the median of the previous ``rvol_window`` staged sessions'
  ``pm_dollar_volume``, strictly before D, counting only finite values greater than zero, and it is
  undefined below ``rvol_minimum`` history entries (both taken from ``session_rows``'s own defaults);
* ``premarket_rvol`` = today's premarket dollar volume / that median; NaN when the median is
  undefined or not positive. A NaN from too little history is the frozen rule, not missing data.

A session's row is final only once the session's premarket and its 09:30 bar exist, so the store
refuses a row for session D before ``SESSION_CLOSED_AT`` (09:35 ET) of D. Before 2026-10-16 the
collector ran after ET midnight and wrote D from the overnight bars stamped with D's date - zero
premarket bars, not staged, ``complete=1`` - and the append-only ``put`` then kept that row and
discarded D's real data as a CONFLICT (E-RUNTIME-R1). ``premature_rows`` finds such rows and
``repair`` replaces one, explicitly, with an audit row in ``kiwoom_repair_log``.

The store keeps one row per (symbol, session) instead of the minute tape, so a runtime preloads the
denominator of the whole universe without re-reading bars or calling the API at 09:25. Massive volume
is never written here: the numerator and the denominator must come from the same source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from app.backtest.strategy_e1_premarket.premarket import (
    DECISION_LAST_BAR, OPEN_MIN, PREMARKET_START, session_rows,
)

SOURCE = "KIWOOM"
EVIDENCE_CLASS = "KIWOOM_NATIVE"
STORE_VERSION = "e-rt3-kiwoom-rvol-v1"
_defaults = inspect.signature(session_rows).parameters
RVOL_WINDOW = int(_defaults["rvol_window"].default)          # frozen: 20
RVOL_MINIMUM = int(_defaults["rvol_minimum"].default)        # frozen: 5

ET = ZoneInfo("America/New_York")
#: A row for session D may be written from this ET time of D on: the premarket window ends at 09:24
#: and the staging rule needs the 09:30 bar, which is complete at 09:31.
SESSION_CLOSED_AT = time(9, 35)
PREMATURE_REJECTED = "PREMATURE_REJECTED"
REPAIR_PREMATURE_DAILY_APPEND = "PREMATURE_DAILY_APPEND_V1"

#: Recorded exceptions collection cannot advance past (with ``history_exhausted``).
TERMINAL_ERRORS = ("MARKET_DATA_UNAVAILABLE", "INVALID_SYMBOL", "HISTORY_PAGE_LIMIT")
#: Stored rows disagree with the chart; pending until a repair, never re-walked meanwhile.
STORE_CONFLICT = "STORE_CONFLICT"

READY = "RVOL_READY"
NAN_BY_RULE = "RVOL_NORMAL_NAN_BY_RULE"
DATA_MISSING = "RVOL_DATA_MISSING"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kiwoom_premarket_sessions (
  symbol TEXT NOT NULL,
  session TEXT NOT NULL,
  pm_dollar_volume REAL,
  pm_bars INTEGER NOT NULL,
  has_open_0930 INTEGER NOT NULL,
  staged INTEGER NOT NULL,
  source TEXT NOT NULL,
  complete INTEGER NOT NULL,
  checksum TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  PRIMARY KEY (symbol, session)
);
CREATE INDEX IF NOT EXISTS idx_symbol_session ON kiwoom_premarket_sessions (symbol, session);
CREATE TABLE IF NOT EXISTS kiwoom_collection_state (
  symbol TEXT PRIMARY KEY,
  staged_count INTEGER NOT NULL,
  oldest_session TEXT,
  history_exhausted INTEGER NOT NULL,
  last_pass_at TEXT NOT NULL,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS kiwoom_repair_log (
  symbol TEXT NOT NULL,
  session TEXT NOT NULL,
  repair_reason TEXT NOT NULL,
  old_pm_dollar_volume REAL,
  old_pm_bars INTEGER,
  old_has_open_0930 INTEGER,
  old_staged INTEGER,
  old_collected_at TEXT,
  old_checksum TEXT NOT NULL,
  new_pm_dollar_volume REAL,
  new_pm_bars INTEGER NOT NULL,
  new_has_open_0930 INTEGER NOT NULL,
  new_staged INTEGER NOT NULL,
  new_checksum TEXT NOT NULL,
  repaired_at TEXT NOT NULL,
  PRIMARY KEY (symbol, session, repair_reason)
);
"""


def session_closed_at(session: date) -> datetime:
    """The first moment a row for ``session`` may be stored as final."""
    return datetime.combine(session, SESSION_CLOSED_AT, tzinfo=ET)


def collected_before_close(session: str | date, collected_at: str) -> bool:
    """True when a stored row was written before its own session could be complete."""
    day = date.fromisoformat(session) if isinstance(session, str) else session
    return datetime.fromisoformat(collected_at) < session_closed_at(day)


@dataclass(frozen=True)
class SessionRecord:
    symbol: str
    session: date
    pm_dollar_volume: float
    pm_bars: int
    has_open_0930: bool
    complete: bool = True
    source: str = SOURCE

    @property
    def staged(self) -> bool:
        """The frozen staging rule: a premarket block and a 09:30 bar."""
        return self.pm_bars > 0 and self.has_open_0930

    def checksum(self) -> str:
        body = json.dumps([self.symbol, self.session.isoformat(), round(self.pm_dollar_volume, 6),
                           self.pm_bars, self.has_open_0930, self.source, STORE_VERSION], sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()


def from_minute_rows(symbol: str, session: date, rows: Iterable[Mapping[str, Any]]) -> SessionRecord:
    """One session's record from Kiwoom minute rows (``cntr_tm``/``bus_dt``, no VWAP)."""
    from app.integrations.kiwoom.timestamps import minute_timestamp
    dollar, bars, has_open = 0.0, 0, False
    for row in rows:
        stamp = minute_timestamp(row)
        if stamp.date() != session:
            continue
        minute = stamp.hour * 60 + stamp.minute
        if PREMARKET_START <= minute <= DECISION_LAST_BAR:
            dollar += float(row["cur_prc"]) * float(row["trde_qty"])
            bars += 1
        elif minute == OPEN_MIN:
            has_open = True
    return SessionRecord(symbol, session, dollar, bars, has_open)


class RvolStore:
    """SQLite-backed, append-only per (symbol, session); a stored row is never silently rewritten."""

    def __init__(self, path: Path, *, clock=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # one connection shared by the collector's lane threads; every write goes through a lock
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    # -- writing
    def put(self, record: SessionRecord, *, replace: bool = False) -> str:
        if self.clock() < session_closed_at(record.session):
            return PREMATURE_REJECTED                  # D's row cannot be final before D's 09:35 ET
        existing = self.connection.execute(
            "SELECT checksum FROM kiwoom_premarket_sessions WHERE symbol=? AND session=?",
            (record.symbol, record.session.isoformat())).fetchone()
        if existing and not replace:
            return "KEPT" if existing[0] == record.checksum() else "CONFLICT"
        self.connection.execute(
            "INSERT OR REPLACE INTO kiwoom_premarket_sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record.symbol, record.session.isoformat(), record.pm_dollar_volume, record.pm_bars,
             int(record.has_open_0930), int(record.staged), record.source, int(record.complete),
             record.checksum(), self.clock().astimezone(timezone.utc).isoformat(timespec="seconds")))
        self.connection.commit()
        return "WRITTEN"

    # -- E-RUNTIME-R1 repair
    def row(self, symbol: str, session: date | str) -> dict[str, Any] | None:
        day = session.isoformat() if isinstance(session, date) else session
        found = self.connection.execute(
            "SELECT symbol, session, pm_dollar_volume, pm_bars, has_open_0930, staged, source, complete, "
            "checksum, collected_at FROM kiwoom_premarket_sessions WHERE symbol=? AND session=?",
            (symbol, day)).fetchone()
        if found is None:
            return None
        keys = ("symbol", "session", "pm_dollar_volume", "pm_bars", "has_open_0930", "staged", "source",
                "complete", "checksum", "collected_at")
        return dict(zip(keys, found))

    def premature_rows(self, sessions: Sequence[date] | None = None) -> list[dict[str, Any]]:
        """Rows stored as complete before their own session's 09:35 ET (the E-RUNTIME-R1 defect).

        ``sessions`` limits the scan to trading sessions; a row dated on a weekend is never part of
        a denominator and is reported by the caller separately, not repaired.
        """
        wanted = None if sessions is None else {d.isoformat() for d in sessions}
        out = []
        for row in self.connection.execute(
                "SELECT symbol, session, pm_dollar_volume, pm_bars, has_open_0930, staged, complete, "
                "checksum, collected_at FROM kiwoom_premarket_sessions WHERE complete=1 "
                "ORDER BY session, symbol"):
            symbol, session, pm, bars, has_open, staged, complete, checksum, collected = row
            if wanted is not None and session not in wanted:
                continue
            if not collected_before_close(session, collected):
                continue
            out.append({"symbol": symbol, "session": session, "pm_dollar_volume": pm, "pm_bars": bars,
                        "has_open_0930": bool(has_open), "staged": bool(staged), "complete": bool(complete),
                        "checksum": checksum, "collected_at": collected,
                        "session_closed_at": session_closed_at(date.fromisoformat(session)).isoformat(),
                        "reason_corrupted": "COLLECTED_BEFORE_SESSION_CLOSE"})
        return out

    def repair(self, record: SessionRecord, *, reason: str, expected_old_checksum: str) -> str:
        """Replace one premature row with the session's real record, keeping an audit row.

        Refuses unless the stored row is still the exact row the inventory found and it really was
        written before its session closed, and unless the new record is itself final. A repair that
        recomputes the same row is reported ``UNCHANGED`` and writes nothing.
        """
        if self.clock() < session_closed_at(record.session):
            return PREMATURE_REJECTED
        old = self.row(record.symbol, record.session)
        if old is None:
            return "NOT_FOUND"
        if old["checksum"] != expected_old_checksum:
            return "OLD_ROW_CHANGED"
        if not collected_before_close(record.session, old["collected_at"]):
            return "NOT_PREMATURE"
        if old["checksum"] == record.checksum():
            return "UNCHANGED"
        stamp = self.clock().astimezone(timezone.utc).isoformat(timespec="seconds")
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO kiwoom_repair_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.symbol, record.session.isoformat(), reason, old["pm_dollar_volume"], old["pm_bars"],
                 old["has_open_0930"], old["staged"], old["collected_at"], old["checksum"],
                 record.pm_dollar_volume, record.pm_bars, int(record.has_open_0930), int(record.staged),
                 record.checksum(), stamp))
            self.connection.execute(
                "UPDATE kiwoom_premarket_sessions SET pm_dollar_volume=?, pm_bars=?, has_open_0930=?, "
                "staged=?, complete=?, checksum=?, collected_at=? WHERE symbol=? AND session=?",
                (record.pm_dollar_volume, record.pm_bars, int(record.has_open_0930), int(record.staged),
                 int(record.complete), record.checksum(), stamp, record.symbol, record.session.isoformat()))
        return "REPAIRED"

    def repair_log(self) -> list[dict[str, Any]]:
        keys = ("symbol", "session", "repair_reason", "old_pm_dollar_volume", "old_pm_bars", "old_has_open_0930",
                "old_staged", "old_collected_at", "old_checksum", "new_pm_dollar_volume", "new_pm_bars",
                "new_has_open_0930", "new_staged", "new_checksum", "repaired_at")
        return [dict(zip(keys, row)) for row in self.connection.execute(
            "SELECT * FROM kiwoom_repair_log ORDER BY session, symbol")]

    def append_today(self, record: SessionRecord) -> str:
        """The daily step after a session's premarket is complete: one row, no history rebuild."""
        return self.put(record)

    def evict_before(self, symbol: str, keep_from: date) -> int:
        """Rolling window: drop staged history older than ``keep_from`` (never the recent window)."""
        cursor = self.connection.execute(
            "DELETE FROM kiwoom_premarket_sessions WHERE symbol=? AND session<?",
            (symbol, keep_from.isoformat()))
        self.connection.commit()
        return cursor.rowcount

    # -- reading
    def staged_history(self, symbol: str, before: date) -> list[float]:
        rows = self.connection.execute(
            "SELECT pm_dollar_volume FROM kiwoom_premarket_sessions "
            "WHERE symbol=? AND session<? AND staged=1 AND complete=1 ORDER BY session",
            (symbol, before.isoformat())).fetchall()
        return [float(value) for (value,) in rows if value is not None]

    def denominator(self, symbol: str, session: date) -> float | None:
        """The frozen rolling prior median, or None when the frozen rule leaves it undefined."""
        history = [v for v in self.staged_history(symbol, session) if v > 0]
        if len(history) < RVOL_MINIMUM:
            return None
        value = float(median(history[-RVOL_WINDOW:]))
        return value if value > 0 else None

    def preload(self, symbols: Sequence[str], session: date) -> dict[str, float | None]:
        """Every symbol's denominator in one pass, for the runtime to hold in memory at 09:25."""
        history: dict[str, list[float]] = {s: [] for s in symbols}
        wanted = set(symbols)
        for symbol, value in self.connection.execute(
                "SELECT symbol, pm_dollar_volume FROM kiwoom_premarket_sessions "
                "WHERE session<? AND staged=1 AND complete=1 ORDER BY symbol, session",
                (session.isoformat(),)):
            if symbol in wanted and value is not None and value > 0:
                history[symbol].append(float(value))
        return {s: (float(median(v[-RVOL_WINDOW:])) if len(v) >= RVOL_MINIMUM and median(v[-RVOL_WINDOW:]) > 0
                    else None) for s, v in history.items()}

    def rvol(self, symbol: str, session: date, today_pm_dollar_volume: float) -> float | None:
        denominator = self.denominator(symbol, session)
        return None if denominator is None else today_pm_dollar_volume / denominator

    # -- planning / readiness
    def staged_count(self, symbol: str, before: date) -> int:
        return len([v for v in self.staged_history(symbol, before) if v > 0])

    def coverage(self, symbols: Sequence[str], session: date) -> dict[str, Any]:
        counts = {s: self.staged_count(s, session) for s in symbols}
        buckets = {"0": 0, f"1-{RVOL_MINIMUM - 1}": 0, f"{RVOL_MINIMUM}-{RVOL_WINDOW - 1}": 0,
                   f"{RVOL_WINDOW}+": 0}
        for value in counts.values():
            if value == 0:
                buckets["0"] += 1
            elif value < RVOL_MINIMUM:
                buckets[f"1-{RVOL_MINIMUM - 1}"] += 1
            elif value < RVOL_WINDOW:
                buckets[f"{RVOL_MINIMUM}-{RVOL_WINDOW - 1}"] += 1
            else:
                buckets[f"{RVOL_WINDOW}+"] += 1
        return {"symbols": len(symbols), "window": RVOL_WINDOW, "minimum": RVOL_MINIMUM,
                "fully_ready": buckets[f"{RVOL_WINDOW}+"],
                "partially_ready": buckets[f"{RVOL_MINIMUM}-{RVOL_WINDOW - 1}"] + buckets[f"1-{RVOL_MINIMUM - 1}"],
                "zero_history": buckets["0"], "distribution": buckets,
                "missing_staged_sessions_total": sum(max(0, RVOL_WINDOW - v) for v in counts.values())}

    def bootstrap_plan(self, symbols: Sequence[str], session: date) -> dict[str, Any]:
        """Only what is missing: per symbol, how many staged sessions short of the frozen window."""
        need = {s: max(0, RVOL_WINDOW - self.staged_count(s, session)) for s in symbols}
        pending = {s: n for s, n in need.items() if n}
        return {"symbols_needing_history": len(pending), "sessions_needed_total": sum(pending.values()),
                "already_complete": len(symbols) - len(pending), "per_symbol": pending}

    # -- collection state (which symbols still have something to fetch)
    def note_pass(self, symbol: str, *, staged_count: int, oldest_session: str | None,
                  history_exhausted: bool, error: str | None, authoritative: bool = True,
                  clears_conflict: bool = False) -> None:
        """What a collector pass found, so the next pass does not re-walk a finished symbol.

        Only a walk aimed at the full window (``authoritative``) may overturn a recorded exception.
        A shorter pass - the daily append, a repair - never looked far enough back to know whether
        the history still runs out or the source still refuses, so it keeps the exception until
        real data brings the symbol to the window. ``clears_conflict`` is for a repair, which is
        what resolves a STORE_CONFLICT.
        """
        prior = self.collection_state().get(symbol)
        if not authoritative and prior and staged_count < RVOL_WINDOW:
            conflict = prior["last_error"] == STORE_CONFLICT
            if prior["history_exhausted"] or prior["last_error"] in TERMINAL_ERRORS or (conflict and not clears_conflict):
                history_exhausted = prior["history_exhausted"]
                error = prior["last_error"]
                oldest_session = oldest_session or prior["oldest_session"]
            elif conflict:
                error = None
        self.connection.execute(
            "INSERT OR REPLACE INTO kiwoom_collection_state VALUES (?,?,?,?,?,?)",
            (symbol, staged_count, oldest_session, int(history_exhausted),
             datetime.now(timezone.utc).isoformat(timespec="seconds"), error))
        self.connection.commit()

    def unreachable_symbols(self) -> set[str]:
        """Symbols collection can never advance: Kiwoom's history ran out, or the source refuses
        them. They are recorded exceptions, not unfinished work, so a promotion gate must not wait
        for them (the frozen rule simply leaves their RVOL undefined)."""
        marks = ",".join("?" * len(TERMINAL_ERRORS))
        return {row[0] for row in self.connection.execute(
            f"SELECT symbol FROM kiwoom_collection_state WHERE history_exhausted=1 OR last_error IN ({marks})",
            TERMINAL_ERRORS)}

    def bootstrap_pending(self, symbols: Sequence[str], session: date) -> list[str]:
        """Symbols collection can still advance and that are short of the window before ``session``.

        Empty means the bootstrap is complete (recorded exceptions aside); this is what the runtime
        gates on at 09:25.
        """
        unreachable = self.unreachable_symbols()
        return sorted(s for s in symbols if s not in unreachable and self.staged_count(s, session) < RVOL_WINDOW)

    def conflicted_symbols(self) -> set[str]:
        """Symbols whose last walk found stored rows it could not write over (a CONFLICT): the store
        disagrees with the chart, so re-walking cannot help until the rows are repaired. They are
        not unreachable either - they stay pending, which keeps the evidence grade fail-closed."""
        return {row[0] for row in self.connection.execute(
            "SELECT symbol FROM kiwoom_collection_state WHERE last_error=?", (STORE_CONFLICT,))}

    def exhausted_symbols(self) -> set[str]:
        """Symbols whose Kiwoom history ran out before the frozen window; not worth re-walking."""
        return {row[0] for row in self.connection.execute(
            "SELECT symbol FROM kiwoom_collection_state WHERE history_exhausted=1")}

    def collection_state(self) -> dict[str, dict[str, Any]]:
        return {row[0]: {"staged_count": row[1], "oldest_session": row[2], "history_exhausted": bool(row[3]),
                         "last_pass_at": row[4], "last_error": row[5]}
                for row in self.connection.execute("SELECT * FROM kiwoom_collection_state")}

    def attempted(self, symbol: str, sessions: Sequence[date]) -> list[date]:
        """Prior sessions this store has never recorded for the symbol (collection gaps, not rule NaN)."""
        have = {row[0] for row in self.connection.execute(
            "SELECT session FROM kiwoom_premarket_sessions WHERE symbol=?", (symbol,))}
        return [d for d in sessions if d.isoformat() not in have]

    def readiness(self, symbol: str, session: date, prior_sessions: Sequence[date]) -> str:
        """``RVOL_READY`` / frozen NaN / unexpected missing, kept apart on purpose.

        A gap is a session in the frozen lookback window that was never collected. Too few *staged*
        sessions in a fully collected window is the frozen NaN, not missing data.
        """
        if self.attempted(symbol, prior_sessions):
            return DATA_MISSING
        return READY if self.denominator(symbol, session) is not None else NAN_BY_RULE

    def close(self) -> None:
        self.connection.close()
