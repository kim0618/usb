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


# -- recorded exceptions survive the nightly append (E-RUNTIME-R1 follow-up) ---------------------
#
# 2026-10-17: the first 00:35 ET append after the repair cleared the exception marks of BH.A, DPC,
# DSGR, ETRA and OBX. The append walks with target=1, so its own verdict ("not exhausted below 1
# staged session") overwrote the bootstrap's; the symbols turned pending until the 10:00 ET run, i.e.
# across the 09:25 decision, where pending means RVOL_BOOTSTRAP_INCOMPLETE.

MON, TUE = date(2026, 10, 19), date(2026, 10, 20)


class ChartLanes:
    """One Kiwoom chart per code; a code in ``refuse`` answers MARKET_DATA_UNAVAILABLE."""

    api_id = "usa06011"

    def __init__(self, charts: dict[str, list[dict]], refuse: set[str] = frozenset()):
        self.lanes = {code: FakeLane(rows) for code, rows in charts.items()}
        self.refuse = set(refuse)

    def page(self, code, exchange, continuation=None):
        if code in self.refuse:
            from app.core.exceptions import MarketDataError
            raise MarketDataError("MARKET_DATA_UNAVAILABLE", "refused")
        return self.lanes[code].page(code, exchange, continuation=continuation)


def _overnight(day: date) -> list[dict]:
    """Bars already stamped with the new session at 00:35 ET: the original defect's input."""
    return [_row(day, 0, 5), _row(day, 0, 10)]


def _run_pass(monkeypatch, store, lanes, symbols, *, at, before, target):
    import functools
    clock = lambda: at
    store.clock = clock
    monkeypatch.setattr(BS, "collect_symbol", functools.partial(_COLLECT, now=clock))
    work = [(s, s, "NA") for s in symbols]
    return R._pass(store, lanes, work, before=before, target=target, workers=1,
                   deadline=lambda: False, label="test")


_COLLECT = BS.collect_symbol


def _night_of_monday(tmp_path, monkeypatch):
    """Bootstrap on Monday after the open, then the Tuesday 00:35 ET append, as the timer runs them."""
    full = _weekdays(date(2026, 10, 16), 25)
    short = _weekdays(date(2026, 10, 16), 12)
    charts = {"NORM": _history(full), "EXH": _history(short), "REF": _history(full), "PLIM": _history(short)}
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    monday = datetime(2026, 10, 19, 10, 0, tzinfo=ET)
    _run_pass(monkeypatch, store, ChartLanes(charts, refuse={"REF"}), ["NORM", "EXH", "REF"],
              at=monday, before=MON, target=RS.RVOL_WINDOW)
    _run_pass(monkeypatch, store, ChartLanes(charts), ["PLIM"], at=monday, before=MON, target=RS.RVOL_WINDOW)
    store.note_pass("PLIM", staged_count=store.staged_count("PLIM", MON), oldest_session=None,
                    history_exhausted=False, error="HISTORY_PAGE_LIMIT")     # what a page-capped walk records
    store.note_pass("CONF", staged_count=0, oldest_session=None, history_exhausted=False,
                    error=RS.STORE_CONFLICT)
    before = store.collection_state()
    # Monday's session and the overnight bars stamped Tuesday are on the chart at 00:35 ET Tuesday
    night = {code: rows + _session_rows(MON) + _overnight(TUE) for code, rows in charts.items()}
    night["CONF"] = _session_rows(MON)
    _run_pass(monkeypatch, store, ChartLanes(night, refuse={"REF"}), ["NORM", "EXH", "REF", "PLIM", "CONF"],
              at=datetime(2026, 10, 20, 0, 35, tzinfo=ET), before=TUE + timedelta(days=1), target=1)
    return store, before


def test_the_nightly_append_keeps_every_recorded_exception(tmp_path, monkeypatch) -> None:
    store, before = _night_of_monday(tmp_path, monkeypatch)
    assert {s for s, v in before.items() if v["history_exhausted"]} == {"EXH"}
    assert before["REF"]["last_error"] == "MARKET_DATA_UNAVAILABLE"
    after = store.collection_state()
    assert store.exhausted_symbols() == {"EXH"}
    assert store.unreachable_symbols() == {"EXH", "REF", "PLIM"}
    assert store.conflicted_symbols() == {"CONF"}
    for symbol in ("EXH", "REF", "PLIM", "CONF"):
        assert (after[symbol]["history_exhausted"], after[symbol]["last_error"]) == \
               (before[symbol]["history_exhausted"], before[symbol]["last_error"]), symbol
    assert after["EXH"]["staged_count"] == before["EXH"]["staged_count"] + 1     # the append still ran
    assert store.row("EXH", MON)["staged"] == 1
    assert store.row("NORM", TUE) is None                                         # nothing premature


def test_after_the_append_nothing_is_queued_and_0925_is_complete(tmp_path, monkeypatch) -> None:
    from app.dev import run_e_rt2_dryrun as RUN
    store, _ = _night_of_monday(tmp_path, monkeypatch)
    universe = ["NORM", "EXH", "REF", "PLIM"]
    codes = {s: s for s in universe}
    work, fresh = R._queue(store, universe, {}, codes, {s: "NA" for s in universe},
                           before=TUE + timedelta(days=1), target=RS.RVOL_WINDOW, skip_exhausted=True)
    assert work == [] and len(fresh) == len(universe)                             # no re-walk, no loop
    # 09:25 ET Tuesday: what the runtime gates on
    pending = store.bootstrap_pending(universe, TUE)
    assert pending == []
    gate = RUN.execution_gate(decided_at=datetime(2026, 10, 20, 9, 25, tzinfo=ET),
                              open_et=datetime(2026, 10, 20, 9, 30, tzinfo=ET), statuses={},
                              documented=set(), rvol_unknown=["EXH", "REF", "PLIM"],
                              unreachable=store.unreachable_symbols(), pending=pending)
    assert "RVOL_BOOTSTRAP_INCOMPLETE" not in gate["reasons"] and gate["authorized"]
    assert store.denominator("NORM", TUE) is not None
    # a parked conflict is still pending on purpose (fail closed until repaired)
    assert store.bootstrap_pending(universe + ["CONF"], TUE) == ["CONF"]


def test_an_exception_is_lifted_only_by_real_data_or_a_full_window_walk(tmp_path, monkeypatch) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    nineteen = _weekdays(date(2026, 10, 16), 19)
    _run_pass(monkeypatch, store, ChartLanes({"X": _history(nineteen)}), ["X"],
              at=datetime(2026, 10, 19, 10, 0, tzinfo=ET), before=MON, target=RS.RVOL_WINDOW)
    assert store.exhausted_symbols() == {"X"}
    # Monday's real session brings it to the window: the append may lift the mark now
    _run_pass(monkeypatch, store, ChartLanes({"X": _history(nineteen) + _session_rows(MON)}), ["X"],
              at=datetime(2026, 10, 20, 0, 35, tzinfo=ET), before=TUE + timedelta(days=1), target=1)
    assert store.staged_count("X", TUE) == RS.RVOL_WINDOW
    assert store.exhausted_symbols() == set() and store.bootstrap_pending(["X"], TUE) == []
    # a refusal is overturned by a full-window walk that the source answers
    store.note_pass("Y", staged_count=0, oldest_session=None, history_exhausted=False,
                    error="MARKET_DATA_UNAVAILABLE")
    _run_pass(monkeypatch, store, ChartLanes({"Y": _history(_weekdays(date(2026, 10, 16), 25))}), ["Y"],
              at=datetime(2026, 10, 19, 10, 0, tzinfo=ET), before=MON, target=RS.RVOL_WINDOW)
    assert store.collection_state()["Y"]["last_error"] is None and "Y" not in store.unreachable_symbols()


def test_a_repair_resolves_a_conflict_but_not_an_exhausted_history(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    store.note_pass("OIG", staged_count=12, oldest_session="2026-09-18", history_exhausted=True, error=None)
    store.note_pass("KHC", staged_count=11, oldest_session=None, history_exhausted=False, error=RS.STORE_CONFLICT)
    for symbol, staged in (("OIG", 13), ("KHC", 13)):                              # what repair/replay record
        store.note_pass(symbol, staged_count=staged, oldest_session=None, history_exhausted=False, error=None,
                        authoritative=False, clears_conflict=True)
    state = store.collection_state()
    assert state["OIG"]["history_exhausted"] and state["OIG"]["oldest_session"] == "2026-09-18"
    assert state["KHC"]["last_error"] is None and store.conflicted_symbols() == set()
    assert store.unreachable_symbols() == {"OIG"}
