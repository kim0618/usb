"""The nightly splits refresh: window, validation, idempotency, and what A reads afterwards.

Offline. The fake client is shaped like ``MassiveAggregatesClient`` as far as ``fetch_splits``
touches it, so the real ``fetch_splits`` (the unchanged collector) runs end to end.
"""

from datetime import date, datetime, timedelta
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.backtest.mover_scanner_v1 import universe as U
from app.backtest.strategy_c_selection import raw_fetch as RF
from app.dev import collect_splits as S
from app.market.calendar import MarketCalendar

ET = ZoneInfo("America/New_York")
MONDAY_NIGHT = datetime(2026, 10, 5, 0, 55, tzinfo=ET)


class Accounting:
    http_requests = 0


class FakeClient:
    _base_url = "https://api.example"

    def __init__(self, results=None, *, fail=False):
        self.results, self.fail, self.accounting, self.params = results, fail, Accounting(), []

    def _get(self, url, params):
        self.accounting.http_requests += 1
        self.params.append(params)
        if self.fail:
            raise RF.MassiveError("NETWORK_ERROR", "down")
        return {"results": self.results, "status": "OK"}


def ev(ticker, day, a=1, b=2, ident=None):
    return {"ticker": ticker, "execution_date": day, "split_from": a, "split_to": b,
            "id": ident or f"E{ticker}{day}"}


def good(day=date(2026, 10, 6)):
    return [ev("AAA", day.isoformat()), ev("BBB", (day + timedelta(days=1)).isoformat(), 10, 1)]


def put(repo, start, end, results, *, declared_end=None):
    body = {"format": RF.RAW_FORMAT_VERSION, "start": start.isoformat(),
            "end": declared_end or end.isoformat(), "pages": 1, "results": results}
    RF._write_json_gz(RF.splits_path(repo / S.RAW_ROOT, start, end), body)


def go(repo, client, now=MONDAY_NIGHT, **kw):
    return S.run(repo, now=now, client_factory=lambda: client, **kw)


# -- window -------------------------------------------------------------------------------------

def test_window_is_back_seven_ahead_fourteen_in_new_york_time():
    assert S.window_for(MONDAY_NIGHT) == (date(2026, 9, 28), date(2026, 10, 19))


def test_window_uses_the_new_york_date_not_the_utc_one():
    # 00:55 ET is 04:55 UTC: same day. 23:30 ET is next day in UTC; the ET date must win.
    late = datetime(2026, 10, 5, 23, 30, tzinfo=ET)
    assert S.window_for(late.astimezone(ZoneInfo("UTC")))[0] == date(2026, 9, 28)


@pytest.mark.parametrize("night", [datetime(2026, 3, 8, 0, 55, tzinfo=ET),   # US spring forward
                                   datetime(2026, 11, 1, 0, 55, tzinfo=ET)])  # fall back
def test_window_is_stable_across_dst_changes(night):
    start, end = S.window_for(night)
    assert (end - start).days == S.WINDOW_BACK_DAYS + S.WINDOW_AHEAD_DAYS
    assert start == night.date() - timedelta(days=7)


def test_session_over_weekend_and_holiday():
    cal = MarketCalendar("America/New_York")
    assert S.current_session(cal, date(2026, 10, 3)) == date(2026, 10, 5)    # Saturday
    assert S.current_session(cal, date(2026, 10, 5)) == date(2026, 10, 5)
    assert S.current_session(cal, date(2026, 9, 7)) == date(2026, 9, 8)      # Labor Day


# -- collection, idempotency, refusal -----------------------------------------------------------

def test_collects_one_request_into_the_store_a_reads(tmp_path):
    client = FakeClient(good())
    out = go(tmp_path, client)
    path = tmp_path / "data/runtime/strategy_c/raw/splits/splits_2026-09-28_2026-10-19.json.gz"
    assert out["status"] == "OK" and path.is_file() and client.accounting.http_requests == 1
    assert client.params[0]["execution_date.gte"] == "2026-09-28"
    assert client.params[0]["execution_date.lte"] == "2026-10-19"
    assert out["events"] == 2 and out["coverage"]["days_ahead_covered"] == 14


def test_rerun_reuses_without_a_request_and_leaves_bytes_alone(tmp_path):
    go(tmp_path, FakeClient(good()))
    path = tmp_path / "data/runtime/strategy_c/raw/splits/splits_2026-09-28_2026-10-19.json.gz"
    before = path.read_bytes()
    client = FakeClient(good())
    out = go(tmp_path, client)
    assert out["status"] == "NO_WORK" and out["outcome"] == "REUSED"
    assert client.accounting.http_requests == 0 and path.read_bytes() == before


def test_repeated_run_leaves_a_single_file_and_ledger_lines_accumulate(tmp_path):
    for _ in range(3):
        go(tmp_path, FakeClient(good()))
    assert len(list((tmp_path / S.RAW_ROOT / "splits").glob("splits_*.json.gz"))) == 1
    lines = (tmp_path / S.LEDGER_DIR / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["status"] == "OK"


def test_network_failure_leaves_no_valid_looking_file(tmp_path):
    out = go(tmp_path, FakeClient(fail=True))
    assert out["status"] == "INCOMPLETE" and "FETCH_FAILED" in out["outcome"]
    assert not (tmp_path / S.RAW_ROOT / "splits").exists() or not any(
        p.suffix == ".gz" for p in (tmp_path / S.RAW_ROOT / "splits").iterdir())


@pytest.mark.parametrize("results,problem", [
    ([], "ZERO_EVENTS"),
    ([ev("AAA", "2026-10-06"), ev("AAA", "2026-10-06")], "DUPLICATE_EVENT_IDS"),
    ([{"ticker": "AAA", "execution_date": "not-a-date", "split_from": 1, "split_to": 2, "id": "x"}],
     "MALFORMED_EVENTS"),
    ([ev("AAA", "2026-10-06", 0, 2)], "MALFORMED_EVENTS"),
    ([ev("AAA", "2027-01-01")], "MALFORMED_EVENTS"),                  # outside the requested range
    ([{"execution_date": "2026-10-06", "split_from": 1, "split_to": 2, "id": "x"}], "MALFORMED_EVENTS"),
])
def test_malformed_response_is_refused_and_quarantined(tmp_path, results, problem):
    out = go(tmp_path, FakeClient(results))
    assert out["status"] == "INCOMPLETE" and problem in json.dumps(out["validation"]["problems"])
    assert Path(out["quarantined_to"]).is_file()
    assert not (tmp_path / S.RAW_ROOT / "splits/splits_2026-09-28_2026-10-19.json.gz").exists()
    assert out["coverage"]["covers_current_session"] is False


def test_corrupt_existing_file_is_quarantined_then_refetched(tmp_path):
    p = tmp_path / S.RAW_ROOT / "splits/splits_2026-09-28_2026-10-19.json.gz"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"not gzip")
    out = go(tmp_path, FakeClient(good()))
    assert out["status"] == "OK" and out["quarantined_to"] and p.is_file()


def test_existing_file_with_wrong_range_is_not_trusted(tmp_path):
    put(tmp_path, date(2026, 9, 28), date(2026, 10, 19), good(), declared_end="2026-10-09")
    out = go(tmp_path, FakeClient(good()))
    assert out["quarantined_to"] and out["status"] == "OK"


def test_plan_only_touches_nothing_and_asks_nothing(tmp_path):
    client = FakeClient(good())
    out = go(tmp_path, client, dry_run=True)
    assert out["status"] == "DRY_RUN" and client.accounting.http_requests == 0
    assert not (tmp_path / "data").exists()


# -- A's view -----------------------------------------------------------------------------------

def test_a_reads_the_new_file_alongside_the_old_and_prunes_the_split_symbol(tmp_path):
    put(tmp_path, date(2026, 9, 1), date(2026, 10, 2), [ev("OLD", "2026-09-10")])   # old store
    go(tmp_path, FakeClient([ev("SPLT", "2026-10-12", 1, 10), ev("OLD", "2026-10-05")]),
       now=datetime(2026, 10, 9, 0, 55, tzinfo=ET))
    mapping = U.split_sessions(tmp_path)
    assert date(2026, 10, 12) in mapping["SPLT"] and date(2026, 9, 10) in mapping["OLD"]
    base = type("B", (), {"symbols": frozenset({"SPLT", "PLAIN"})})()
    kept = U.eligible_symbols(base, ["SPLT", "PLAIN"], mapping, date(2026, 10, 12))
    assert kept == ("PLAIN",)                                  # split symbol pruned, other unaffected
    assert U.eligible_symbols(base, ["SPLT", "PLAIN"], mapping, date(2026, 10, 13)) == ("PLAIN", "SPLT")


def test_coverage_reaches_past_the_old_end_date_and_reports_days_ahead(tmp_path):
    put(tmp_path, date(2026, 10, 3), date(2026, 10, 9), good())
    before = S.coverage(tmp_path, MarketCalendar("America/New_York"), datetime(2026, 10, 9, 0, 55, tzinfo=ET))
    assert before["contiguous_through_from_session"] == "2026-10-09"
    go(tmp_path, FakeClient([ev("AAA", "2026-10-12")]), now=datetime(2026, 10, 9, 0, 55, tzinfo=ET))
    after = S.coverage(tmp_path, MarketCalendar("America/New_York"), datetime(2026, 10, 9, 0, 55, tzinfo=ET))
    assert after["contiguous_through_from_session"] == "2026-10-23" and after["days_ahead_covered"] == 14


def test_a_gap_in_coverage_is_reported_not_papered_over(tmp_path):
    put(tmp_path, date(2026, 9, 1), date(2026, 10, 2), good(date(2026, 9, 10)))
    cov = S.coverage(tmp_path, MarketCalendar("America/New_York"), MONDAY_NIGHT)
    assert cov["covers_current_session"] is False and cov["days_ahead_covered"] is None
    assert cov["latest_split_coverage_date"] == "2026-10-02"


def test_overlap_parity_reports_a_provider_revision_without_failing(tmp_path):
    put(tmp_path, date(2026, 9, 27), date(2026, 10, 3), [ev("AAA", "2026-10-01", 1, 2)])
    out = go(tmp_path, FakeClient([ev("AAA", "2026-10-01", 1, 3, ident="other")]))
    assert out["status"] == "OK" and out["parity"]["only_in_new_count"] == 1
    assert out["parity"]["only_in_existing_count"] == 1


def test_two_trees_are_mirrored_by_hardlink_and_a_conflict_is_left_alone(tmp_path):
    main, other = tmp_path / "main", tmp_path / "other"
    out = go(main, FakeClient(good()), mirrors=(other,))
    a = main / S.RAW_ROOT / "splits/splits_2026-09-28_2026-10-19.json.gz"
    b = other / S.RAW_ROOT / "splits/splits_2026-09-28_2026-10-19.json.gz"
    assert out["mirrors"][0]["status"] == "LINKED" and a.stat().st_ino == b.stat().st_ino
    other2 = tmp_path / "other2"
    (other2 / S.RAW_ROOT / "splits").mkdir(parents=True)
    c = other2 / S.RAW_ROOT / "splits/splits_2026-09-28_2026-10-19.json.gz"
    c.write_bytes(gzip.compress(b"{}"))
    assert S.mirror(a, (other2,), digest="x")[0]["status"] == "MIRROR_CONFLICT_LEFT_ALONE"
    assert c.read_bytes() == gzip.compress(b"{}")


def test_authentic_production_shape_passes_validation(tmp_path):
    # the stored 2026-10-03..10-09 file is the shape the provider really returns
    real = Path(__file__).resolve().parents[2] / "data/runtime/strategy_c/raw/splits/splits_2026-10-03_2026-10-09.json.gz"
    if not real.exists():
        pytest.skip("production sample not present")
    assert S.validate(real, date(2026, 10, 3), date(2026, 10, 9))["ok"]
