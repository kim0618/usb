"""E-RUNTIME-R1: the RVOL store never takes a row for a session that has not closed, and a row it
took before the fix is found and repaired explicitly, with an audit trail.

The defect (2026-09-24 .. 2026-10-16): the collector ran after ET midnight, the chart already held
overnight bars dated with the new session D, and the walk wrote D as a zero-premarket, not-staged,
complete row. The append-only store then kept that row and threw D's real data away as a CONFLICT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dev import run_e_rt3_rvol as R
from app.strategy_e_max_rt import rvol_bootstrap as BS, rvol_store as RS

ET = RS.ET
D = date(2026, 10, 14)                         # a Wednesday session


def _clock(day: date, hh: int, mm: int):
    moment = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)
    return lambda: moment


def _row(day: date, hh: int, mm: int, price: float = 10.0, qty: float = 100.0) -> dict:
    stamp = f"{day:%Y%m%d}"
    return {"cntr_tm": f"{stamp}{hh:02d}{mm:02d}00", "bus_dt": stamp, "cur_prc": str(price),
            "trde_qty": str(qty), "open_pric": str(price), "high_pric": str(price), "low_pric": str(price)}


def _session_rows(day: date, *, pm_bars: int = 3, with_open: bool = True) -> list[dict]:
    rows = [_row(day, 4, n) for n in range(pm_bars)]
    if with_open:
        rows.append(_row(day, 9, 30))
    rows.append(_row(day, 15, 59))
    return rows


@dataclass
class _Page:
    body: dict
    continuation: object


class FakeLane:
    """Pages newest-first, the way the Kiwoom chart answers, ``per_page`` rows at a time."""

    api_id = "usa06011"

    def __init__(self, rows: list[dict], per_page: int = 1000):
        self.rows = sorted(rows, key=lambda r: r["cntr_tm"], reverse=True)
        self.per_page = per_page
        self.calls = 0

    def page(self, code, exchange, continuation=None):
        self.calls += 1
        # the real lane is handed the previous page back and reads its continuation key
        start = continuation.continuation if isinstance(continuation, _Page) else (continuation or 0)
        chunk = self.rows[start:start + self.per_page]
        nxt = start + self.per_page
        return _Page({"result_list": chunk}, nxt if nxt < len(self.rows) else None)


def _history(days: list[date]) -> list[dict]:
    return [row for day in days for row in _session_rows(day)]


def _weekdays(end: date, n: int) -> list[date]:
    out, day = [], end
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return sorted(out)


# -- the store refuses a premature row -----------------------------------------------------------

def test_store_rejects_a_row_before_its_session_closes(tmp_path) -> None:
    early = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D, 0, 35))
    assert early.put(RS.SessionRecord("X", D, 0.0, 0, False)) == RS.PREMATURE_REJECTED
    assert early.row("X", D) is None
    just_before = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D, 9, 34))
    assert just_before.put(RS.SessionRecord("X", D, 100.0, 3, True)) == RS.PREMATURE_REJECTED
    closed = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D, 9, 35))
    assert closed.put(RS.SessionRecord("X", D, 100.0, 3, True)) == "WRITTEN"
    # the append-only contract is unchanged for final rows
    assert closed.put(RS.SessionRecord("X", D, 100.0, 3, True)) == "KEPT"
    assert closed.put(RS.SessionRecord("X", D, 999.0, 3, True)) == "CONFLICT"


def test_overnight_bars_of_the_new_session_are_never_written(tmp_path) -> None:
    """00:35 ET of D: the chart holds D-1 in full and D's overnight bars; only D-1 is final."""
    prior = _weekdays(D - timedelta(days=1), 3)
    overnight = [_row(D, 0, m) for m in range(5)]                 # dated D, before D's premarket
    lane = FakeLane(_history(prior) + overnight)
    store = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D, 0, 35))
    result = BS.collect_symbol(lane, "X", "X", "NA", store=store, before=D + timedelta(days=1),
                               target=1, now=_clock(D, 0, 35))
    assert store.row("X", D) is None
    assert store.row("X", prior[-1])["staged"] == 1                 # D-1 is complete and staged
    assert result.premature_skipped == 0 and result.conflicts == 0


def test_the_same_walk_after_the_open_writes_the_real_session(tmp_path) -> None:
    prior = _weekdays(D - timedelta(days=1), 3)
    lane = FakeLane(_history(prior + [D]))
    store = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D, 10, 0))
    BS.collect_symbol(lane, "X", "X", "NA", store=store, before=D + timedelta(days=1), target=1,
                      now=_clock(D, 10, 0))
    row = store.row("X", D)
    assert row["staged"] == 1 and row["pm_bars"] == 3 and row["has_open_0930"] == 1


# -- the infinite bootstrap is parked, not re-walked ---------------------------------------------

def test_a_walk_that_only_meets_conflicts_parks_the_symbol(tmp_path, monkeypatch) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    days = _weekdays(D - timedelta(days=1), 25)
    for day in days:                                              # the old defect: zeros everywhere
        store.connection.execute(
            "INSERT INTO kiwoom_premarket_sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("KHC", day.isoformat(), 0.0, 0, 0, 0, "KIWOOM", 1,
             RS.SessionRecord("KHC", day, 0.0, 0, False).checksum(),
             datetime.combine(day, datetime.min.time(), tzinfo=ET).astimezone(timezone.utc).isoformat()))
    store.connection.commit()
    lane = FakeLane(_history(days))
    result = BS.collect_symbol(lane, "KHC", "KHC", "NA", store=store, before=D, target=20)
    assert result.conflicts > 0 and result.sessions_written == 0
    # what the CLI's progress callback records
    error = "STORE_CONFLICT" if result.conflicts and store.staged_count("KHC", D) < 20 else None
    store.note_pass("KHC", staged_count=store.staged_count("KHC", D), oldest_session=None,
                    history_exhausted=False, error=error)
    assert store.conflicted_symbols() == {"KHC"}
    assert "KHC" not in store.unreachable_symbols()               # still pending: fail closed
    work, _ = R._queue(store, ["KHC"], {}, {"KHC": "KHC"}, {"KHC": "NA"}, before=D, target=20,
                       skip_exhausted=True)
    assert work == []                                             # not requeued every 24 seconds


# -- inventory and targeted repair -----------------------------------------------------------------

def _seed_premature(store: RS.RvolStore, symbol: str, day: date, *, at=(0, 40)) -> str:
    record = RS.SessionRecord(symbol, day, 0.0, 0, False)
    collected = datetime(day.year, day.month, day.day, *at, tzinfo=ET).astimezone(timezone.utc)
    store.connection.execute(
        "INSERT OR REPLACE INTO kiwoom_premarket_sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
        (symbol, day.isoformat(), 0.0, 0, 0, 0, "KIWOOM", 1, record.checksum(),
         collected.isoformat(timespec="seconds")))
    store.connection.commit()
    return record.checksum()


def test_inventory_lists_exactly_the_premature_rows(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D + timedelta(days=2), 12, 0))
    good_day = D - timedelta(days=1)
    store.put(RS.SessionRecord("X", good_day, 100.0, 3, True))                 # written after close
    _seed_premature(store, "X", D)
    store.put(RS.SessionRecord("Y", D, 0.0, 0, True))                            # legit no-premarket
    rows = store.premature_rows()
    assert [(r["symbol"], r["session"]) for r in rows] == [("X", D.isoformat())]
    assert rows[0]["staged"] is False and rows[0]["reason_corrupted"] == "COLLECTED_BEFORE_SESSION_CLOSE"


def test_targeted_repair_keeps_an_audit_row_and_refuses_stale_inventory(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D + timedelta(days=1), 12, 0))
    old = _seed_premature(store, "X", D)
    fresh = RS.SessionRecord("X", D, 3000.0, 3, True)
    assert store.repair(fresh, reason=RS.REPAIR_PREMATURE_DAILY_APPEND, expected_old_checksum="nope") \
        == "OLD_ROW_CHANGED"
    assert store.repair(fresh, reason=RS.REPAIR_PREMATURE_DAILY_APPEND, expected_old_checksum=old) == "REPAIRED"
    row = store.row("X", D)
    assert row["staged"] == 1 and row["pm_dollar_volume"] == 3000.0 and row["checksum"] == fresh.checksum()
    (log,) = store.repair_log()
    assert log["repair_reason"] == "PREMATURE_DAILY_APPEND_V1"
    assert log["old_checksum"] == old and log["new_checksum"] == fresh.checksum()
    assert log["old_staged"] == 0 and log["new_staged"] == 1
    # a repaired row is final: it is neither premature nor repairable again
    assert store.premature_rows() == []
    assert store.repair(fresh, reason=RS.REPAIR_PREMATURE_DAILY_APPEND, expected_old_checksum=old) \
        == "OLD_ROW_CHANGED"


def test_a_row_written_after_close_is_never_repaired(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D, 10, 0))
    original = RS.SessionRecord("X", D, 100.0, 3, True)
    store.put(original)
    assert store.repair(RS.SessionRecord("X", D, 5.0, 3, True), reason=RS.REPAIR_PREMATURE_DAILY_APPEND,
                        expected_old_checksum=original.checksum()) == "NOT_PREMATURE"


def test_rolling_median_after_repair_uses_the_real_recent_sessions(tmp_path) -> None:
    """Before repair the window reaches back past the zeroed days; after it, it is the last 20."""
    days = _weekdays(D - timedelta(days=1), 30)
    store = RS.RvolStore(tmp_path / "s.sqlite3", clock=_clock(D, 12, 0))
    real = {day: 1000.0 + 10 * n for n, day in enumerate(days)}
    checksums = {}
    for n, day in enumerate(days):
        if n >= 20 and day.weekday() != 0:                     # the last 10 sessions: Tue..Fri zeroed
            checksums[day] = _seed_premature(store, "X", day)
        else:
            store.put(RS.SessionRecord("X", day, real[day], 3, True))
    broken = store.denominator("X", D)
    for day, old in checksums.items():
        assert store.repair(RS.SessionRecord("X", day, real[day], 3, True),
                            reason=RS.REPAIR_PREMATURE_DAILY_APPEND, expected_old_checksum=old) == "REPAIRED"
    import statistics
    assert store.denominator("X", D) == statistics.median([real[d] for d in days[-20:]])
    assert broken != store.denominator("X", D)
    assert store.preload(["X"], D)["X"] == store.denominator("X", D)


def test_walk_sessions_stops_once_the_oldest_bad_session_is_complete() -> None:
    days = _weekdays(D, 12)
    lane = FakeLane(_history(days), per_page=5)
    records, pages, exhausted, error = BS.walk_sessions(lane, "X", "NA", older_than=days[-4],
                                                        now=_clock(D, 12, 0))
    assert error is None and not exhausted
    assert days[-4] in records and records[days[-4]].staged
    total_pages = -(-len(lane.rows) // lane.per_page)
    assert pages == lane.calls and lane.calls < total_pages             # it did not page to the start


def test_repair_cli_is_targeted_and_dry_run_writes_nothing(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "s.sqlite3"
    store = RS.RvolStore(store_path, clock=_clock(D + timedelta(days=1), 12, 0))
    days = _weekdays(D, 8)
    for day in days[:-1]:
        store.put(RS.SessionRecord("X", day, 1000.0, 3, True))
    old = _seed_premature(store, "X", days[-1])
    store.close()
    inventory = tmp_path / "inv.json"
    inventory.write_text(json.dumps({"rows": [{"symbol": "X", "session": days[-1].isoformat(), "checksum": old}]}))
    lane = FakeLane(_history(days))
    monkeypatch.setattr(R, "_client", lambda rate: object())
    monkeypatch.setattr(R, "_listing", lambda client: {"X": "NA", "Z": "NA"})
    monkeypatch.setattr(R.BS, "Lane", lambda name, api, client: lane)
    args = SimpleNamespace(inventory=inventory, store=store_path, rate=4.6, workers=1, apply=False,
                           only="", limit=0, out=tmp_path / "dry.json")
    assert R.repair(args) == 0
    dry = json.loads((tmp_path / "dry.json").read_text())
    assert dry["outcomes"] == {"WOULD_REPAIR": 1}
    assert RS.RvolStore(store_path).row("X", days[-1])["checksum"] == old
    args.apply, args.out = True, tmp_path / "apply.json"
    assert R.repair(args) == 0
    assert json.loads(args.out.read_text())["outcomes"] == {"REPAIRED": 1}
    assert RS.RvolStore(store_path).row("X", days[-1])["staged"] == 1


def test_replay_applies_the_staged_repairs_without_any_api_call(tmp_path) -> None:
    clock = _clock(D + timedelta(days=1), 12, 0)
    staged, prod = RS.RvolStore(tmp_path / "staged.sqlite3", clock=clock), RS.RvolStore(tmp_path / "p.sqlite3", clock=clock)
    old = _seed_premature(staged, "X", D)
    assert _seed_premature(prod, "X", D) == old
    _seed_premature(prod, "Y", D)                                   # prod has a row staging never saw
    staged.repair(RS.SessionRecord("X", D, 77.0, 3, True), reason=RS.REPAIR_PREMATURE_DAILY_APPEND,
                  expected_old_checksum=old)
    staged.close()
    prod.close()
    args = SimpleNamespace(source=tmp_path / "staged.sqlite3", store=tmp_path / "p.sqlite3", apply=True, out=None)
    assert R.repair_replay(args) == 0
    prod = RS.RvolStore(tmp_path / "p.sqlite3")
    assert prod.row("X", D)["pm_dollar_volume"] == 77.0 and len(prod.repair_log()) == 1
    assert prod.row("Y", D)["staged"] == 0                           # untouched, still in the inventory
