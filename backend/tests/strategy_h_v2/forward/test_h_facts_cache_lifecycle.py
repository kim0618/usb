"""H-V2 FACTS CACHE COMPACTION REPAIR V1: a cached SEC copy is reused only when its bodies are there.

2026-10-16 the first gated evaluation died in SCAN on ``SEC_SUBMISSIONS_NOT_FOUND: 0000006201``: the
previous run's ``compact`` had dropped the bodies and kept the ledgers, ``index`` read the ledgers as
FRESH, ``plan`` skipped the CIK and the cache-only ``load`` found no body. These tests run the real
``sec_store`` / ``xbrl_store`` / ``inputs.fetch_sec`` behind a fake HTTP transport (no SEC call) and pin
the lifecycle: fetch -> ledger + body -> index -> plan -> load -> compact -> plan -> refetch -> reuse.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_eqm_v0 import xbrl_store
from app.strategies.h_auto import facts_cache as FC
from app.strategies.h_auto import inputs as IN
from app.strategies.h_forward import store as ST

AAL = "0000006201"                                   # the CIK the 2026-10-16 SCAN died on
OTHER = "0000027904"
TODAY = datetime.now(timezone.utc).date()            # ledgers carry the real fetch time
DAY = TODAY.isoformat()


def primary_doc(cik: str, *, older: list[dict] | None = None) -> dict:
    return {"cik": cik, "sic": "4512", "sicDescription": "Air Transportation, Scheduled",
            "filings": {"recent": {"accessionNumber": [f"{cik}-26-000001"], "form": ["10-Q"],
                                   "filingDate": ["2026-09-21"], "acceptanceDateTime": ["2026-09-21T21:00:00.000Z"],
                                   "items": [""], "primaryDocument": ["x.htm"]},
                        "files": older or []}}


def facts_doc(cik: str) -> dict:
    return {"cik": int(cik), "entityName": f"ISSUER {cik}", "facts": {"us-gaap": {}}}


class FakeSEC:
    """data.sec.gov behind an httpx MockTransport: per-URL bodies, scripted failures, request counts."""

    def __init__(self) -> None:
        self.bodies: dict[str, bytes] = {}
        self.script: dict[str, list[int]] = {}       # url -> statuses to answer before the body
        self.calls: Counter = Counter()

    def issuer(self, cik: str, *, older: bool = False) -> None:
        pages = [{"name": f"CIK{cik}-submissions-001.json", "filingFrom": "2019-01-01", "filingTo": "2023-06-30"}]
        self.bodies[sec_store.SUBMISSIONS_URL.format(name=f"CIK{cik}.json")] = json.dumps(
            primary_doc(cik, older=pages if older else None)).encode()
        if older:
            self.bodies[sec_store.SUBMISSIONS_URL.format(name=pages[0]["name"])] = json.dumps(
                {"accessionNumber": [f"{cik}-22-000001"], "form": ["10-K"], "filingDate": ["2022-02-20"],
                 "acceptanceDateTime": ["2022-02-20T21:00:00.000Z"], "items": [""], "primaryDocument": ["k.htm"]}).encode()
        self.bodies[xbrl_store.COMPANYFACTS_URL.format(cik=cik)] = json.dumps(facts_doc(cik)).encode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls[url] += 1
        queued = self.script.get(url)
        if queued:
            return httpx.Response(queued.pop(0))
        if url not in self.bodies:
            return httpx.Response(404)
        return httpx.Response(200, content=self.bodies[url])

    def client(self) -> sec_store.SecClient:
        return sec_store.SecClient("H test cache-lifecycle@example.com", requests_per_second=1e9,
                                   client=httpx.Client(transport=httpx.MockTransport(self.handler)),
                                   sleeper=lambda _s: None)

    @property
    def requests(self) -> int:
        return sum(self.calls.values())


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(ST.ROOT_ENV, str(tmp_path / "d7"))
    return tmp_path / "d7"


@pytest.fixture
def sec():
    fake = FakeSEC()
    for cik in (AAL, OTHER):
        fake.issuer(cik)
    return fake


def run_plan_and_refresh(needed, sec: FakeSEC, *, day: str = DAY, today: date = TODAY, cap: int = 700) -> dict:
    planned = FC.plan(needed, today, cap=cap)
    report = FC.refresh(planned, day=day, client_factory=sec.client)
    return {"planned": planned, "report": report}


def bodies(root: Path) -> list[Path]:
    return sorted(root.rglob("CIK*.json.gz"))


def set_fetched_at(day: str, cik: str, when: datetime) -> None:
    ledger = FC.sec_dir() / day / "submissions" / f"CIK{cik}" / f"CIK{cik}.json.gz.request.json"
    body = json.loads(ledger.read_text()) | {"fetched_at": when.isoformat(timespec="seconds")}
    ledger.write_text(json.dumps(body))


# == CASE A-E: the cache contract =================================================================

def test_case_a_fresh_ledger_with_bodies_is_reused_with_zero_requests(store, sec):
    run_plan_and_refresh([AAL], sec)
    first = sec.requests
    entry = FC.index()[AAL]
    assert entry.usable and FC.state_of(entry, TODAY) == FC.FRESH
    again = run_plan_and_refresh([AAL], sec)
    assert again["planned"] == [] and sec.requests == first                # REUSE: no request
    loaded = FC.load(AAL, entry, lean=True)
    assert loaded.submissions_doc["cik"] == AAL and loaded.facts_fetched


def test_case_b_fresh_ledger_with_missing_body_is_fetch_required_once(store, sec):
    run_plan_and_refresh([AAL], sec)
    (FC.sec_dir() / DAY / "companyfacts" / f"CIK{AAL}.json.gz").unlink()   # lost outside compact
    entry = FC.index()[AAL]
    assert not entry.usable and FC.state_of(entry, TODAY) == FC.EVICTED
    sec.calls.clear()
    out = run_plan_and_refresh([AAL], sec)
    assert out["planned"] == [AAL]
    assert sec.calls == Counter({xbrl_store.COMPANYFACTS_URL.format(cik=AAL): 1})   # only the missing body
    assert FC.index()[AAL].usable


def test_case_c_corrupt_body_fails_closed_in_the_reader(store, sec):
    """Names are what ``index`` checks; damaged bytes keep the stores' existing contract - the read
    raises, the SCAN stops, nothing is scanned from the document."""
    run_plan_and_refresh([AAL], sec)
    path = sec_store.submissions_path(FC.sec_dir() / DAY, AAL, f"CIK{AAL}.json")
    path.write_bytes(b"\x1f\x8b not a gzip stream")
    entry = FC.index()[AAL]
    assert FC.state_of(entry, TODAY) == FC.FRESH
    with pytest.raises(Exception) as caught:
        FC.load(AAL, entry, lean=True)
    assert not isinstance(caught.value, IN.InputsNotReady)


def test_case_d_stale_ledger_with_bodies_keeps_the_stale_policy(store, sec):
    run_plan_and_refresh([AAL, OTHER], sec)
    set_fetched_at(DAY, AAL, datetime.now(timezone.utc) - timedelta(days=FC.FACTS_MAX_AGE_DAYS + 3))
    entries = FC.index()
    assert entries[AAL].usable and FC.state_of(entries[AAL], TODAY) == FC.STALE
    assert FC.plan([AAL, OTHER], TODAY, cap=10) == [AAL]


def test_case_e_no_ledger_no_body_is_missing_and_planned_first(store, sec):
    run_plan_and_refresh([OTHER], sec)
    set_fetched_at(DAY, OTHER, datetime.now(timezone.utc) - timedelta(days=30))
    assert FC.state_of(FC.index().get(AAL), TODAY) == FC.MISSING
    assert FC.plan([OTHER, AAL], TODAY, cap=1) == [AAL]                      # missing before stale


def test_evicted_ranks_with_stale_copies_oldest_first_so_the_cap_rotates():
    """Ranking is the existing one (missing, then oldest fetch, then CIK); an evicted copy is not a
    MISSING tie broken by CIK, which would give the same lowest CIKs the whole cap every day."""
    today = date(2026, 10, 19)
    at = lambda d: datetime(2026, 10, d, 5, tzinfo=timezone.utc)            # noqa: E731
    entries = {"1": FC.CacheEntry("1", "2026-10-16", at(16), True, False),
               "2": FC.CacheEntry("2", "2026-10-15", at(15), True, False),
               "3": FC.CacheEntry("3", "2026-10-18", at(18), True, True),
               "4": FC.CacheEntry("4", "2026-10-05", at(5), True, True)}
    assert [FC.state_of(entries[c], today) for c in "1234"] == [FC.EVICTED, FC.EVICTED, FC.FRESH, FC.STALE]
    assert FC.plan(["1", "2", "3", "4", "9"], today, cap=10, entries=entries) == ["9", "4", "2", "1"]
    assert FC.summary(today, entries)["EVICTED"] == 2


# == compact roundtrip ============================================================================

def test_compact_roundtrip_evicts_then_refetches_then_reuses(store, sec):
    root = FC.sec_dir() / DAY
    run_plan_and_refresh([AAL], sec)                                          # 1. body + ledger
    assert FC.state_of(FC.index()[AAL], TODAY) == FC.FRESH                    # 2. valid, FRESH
    assert FC.compact([]) == {"bodies_dropped": 2, "bodies_kept": 0}          # 3. compact
    assert bodies(root) == [] and len(list(root.rglob("*.request.json"))) == 2   # 4. bodies gone, ledgers kept
    assert FC.state_of(FC.index()[AAL], TODAY) == FC.EVICTED
    sec.calls.clear()
    out = run_plan_and_refresh([AAL], sec)                                    # 5./6. FETCH_REQUIRED
    assert out["planned"] == [AAL] and out["report"]["fetched"] == [AAL]
    assert sec.requests == 2                                                  # 7. one fetch per document
    entry = FC.index()[AAL]
    loaded = FC.load(AAL, entry, lean=True)                                   # 8. load succeeds
    assert loaded.submissions_doc["cik"] == AAL and loaded.facts_fetched
    sec.calls.clear()
    assert run_plan_and_refresh([AAL], sec)["planned"] == [] and sec.requests == 0   # 9. REUSE


def test_compact_of_an_issuer_with_older_pages_refetches_every_page_once(store):
    sec = FakeSEC()
    sec.issuer(AAL, older=True)
    run_plan_and_refresh([AAL], sec)
    full = FC.load(AAL, FC.index()[AAL])
    assert len(full.submissions_rows) == 2
    FC.compact([])
    sec.calls.clear()
    run_plan_and_refresh([AAL], sec)
    assert sorted(sec.calls.values()) == [1, 1, 1] and len(sec.calls) == 3   # primary, older page, facts
    again = FC.load(AAL, FC.index()[AAL])
    assert again.submissions_rows == full.submissions_rows and again.facts == full.facts


def test_compact_keeps_candidate_bodies_reusable(store, sec):
    run_plan_and_refresh([AAL, OTHER], sec)
    FC.compact([OTHER])
    entries = FC.index()
    assert FC.state_of(entries[OTHER], TODAY) == FC.FRESH and FC.state_of(entries[AAL], TODAY) == FC.EVICTED
    sec.calls.clear()
    assert run_plan_and_refresh([AAL, OTHER], sec)["planned"] == [AAL]
    assert all(str(url).endswith((f"CIK{AAL}.json",)) for url in sec.calls)


# == duplicate fetch ==============================================================================

def test_no_duplicate_fetch_within_a_run_or_on_the_next(store, sec):
    out = run_plan_and_refresh([AAL, AAL, OTHER, AAL], sec)
    assert out["planned"] == [AAL, OTHER]
    assert set(sec.calls.values()) == {1} and len(sec.calls) == 4           # 2 issuers x 2 documents
    entries = FC.index()
    for cik in (AAL, OTHER):                                                  # the scan's reads are cache-only
        FC.load(cik, entries[cik], lean=True)
    assert set(sec.calls.values()) == {1}
    sec.calls.clear()
    assert run_plan_and_refresh([AAL, OTHER], sec)["planned"] == [] and sec.requests == 0


# == the 2026-10-16 production failure ============================================================

def production_shape(sec: FakeSEC) -> None:
    """auto/sec as found on trader-j on 2026-10-18: 2026-10-15 holds AAL's ledgers only (compacted), a
    newer root holds other issuers fetched the next day."""
    run_plan_and_refresh([AAL], sec, day="2026-10-15", today=date(2026, 10, 15))
    set_fetched_at("2026-10-15", AAL, datetime(2026, 10, 15, 5, 12, 28, tzinfo=timezone.utc))
    FC.compact([])
    run_plan_and_refresh([OTHER], sec, day="2026-10-16", today=date(2026, 10, 16))


def test_production_failure_reproduces_with_the_ledger_only_reading(store, sec):
    production_shape(sec)
    old_reading = FC.CacheEntry(AAL, "2026-10-15", datetime(2026, 10, 15, 5, 12, 28, tzinfo=timezone.utc), True)
    with pytest.raises(IN.InputsNotReady, match="SEC_SUBMISSIONS_NOT_FOUND: 0000006201"):
        FC.load(AAL, old_reading, lean=True)                                  # what SCAN did on 10-16


def test_production_failure_is_now_fetch_required_then_loads(store, sec):
    production_shape(sec)
    monday = date(2026, 10, 19)
    entry = FC.index()[AAL]
    assert entry.day == "2026-10-15" and not entry.usable and FC.state_of(entry, monday) == FC.EVICTED
    assert FC.plan([AAL, OTHER], monday, cap=700) == [AAL]                    # OTHER is a usable fresh copy
    sec.calls.clear()
    out = run_plan_and_refresh([AAL, OTHER], sec, day="2026-10-19", today=monday)
    assert out["report"]["fetched"] == [AAL] and sec.requests == 2
    entry = FC.index()[AAL]
    assert entry.day == "2026-10-19" and FC.state_of(entry, monday) == FC.FRESH
    loaded = FC.load(AAL, entry, lean=True)
    assert loaded.submissions_doc["cik"] == AAL and loaded.facts_fetched
    assert not list((FC.sec_dir() / "2026-10-15").rglob("CIK*.json.gz"))   # the old root is not re-filled


# == partial failures =============================================================================

def test_429_and_5xx_are_retried_by_the_client_and_then_succeed(store, sec):
    url = sec_store.SUBMISSIONS_URL.format(name=f"CIK{AAL}.json")
    sec.script[url] = [429, 503]
    out = run_plan_and_refresh([AAL], sec)
    assert out["report"]["fetched"] == [AAL] and sec.calls[url] == 3
    assert FC.state_of(FC.index()[AAL], TODAY) == FC.FRESH


def test_exhausted_5xx_degrades_the_slice_and_leaves_no_fresh_copy(store, sec):
    url = sec_store.SUBMISSIONS_URL.format(name=f"CIK{AAL}.json")
    sec.script[url] = [503] * (sec_store.MAX_RETRIES + 1)
    out = run_plan_and_refresh([AAL, OTHER], sec)
    assert out["report"]["degraded"]["cik"] == AAL and out["report"]["fetched"] == []
    assert AAL not in FC.index() and FC.plan([AAL], TODAY, cap=10) == [AAL]  # still planned next run


def test_companyfacts_failure_after_submissions_reads_back_as_not_fetched(store, sec):
    """Submissions stored, companyfacts exhausted: there is no companyfacts ledger, which the reader
    already reports as ``facts_fetched=False`` - E1 says FUNDAMENTALS_NOT_FETCHED (DATA_NOT_READY),
    never an issuer with no facts. The slice stops there, as before."""
    sec.script[xbrl_store.COMPANYFACTS_URL.format(cik=AAL)] = [503] * (sec_store.MAX_RETRIES + 1)
    out = run_plan_and_refresh([AAL, OTHER], sec)
    assert out["report"]["degraded"]["cik"] == AAL
    loaded = FC.load(AAL, FC.index()[AAL], lean=True)
    assert loaded.facts_fetched is False and loaded.facts == []


def test_not_found_issuer_is_recorded_and_not_indexed(store, sec):
    out = run_plan_and_refresh(["0000000042"], sec)
    assert out["report"]["not_found"] == [{"cik": "0000000042", "why": "SEC_SUBMISSIONS_NOT_FOUND"}]
    assert "0000000042" not in FC.index()


def test_empty_response_fails_closed_now_and_on_the_next_read(store, sec):
    """An empty 200 is stored with its ledger before the store parses it (sec_store._page): the run
    stops, and a later cache-only read of that copy stops too - it is never scanned as an issuer."""
    sec.bodies[sec_store.SUBMISSIONS_URL.format(name=f"CIK{AAL}.json")] = b""
    with pytest.raises(json.JSONDecodeError):
        run_plan_and_refresh([AAL], sec)
    with pytest.raises(json.JSONDecodeError):
        FC.load(AAL, FC.index()[AAL], lean=True)


def test_body_written_but_ledger_not_is_never_reused(store, sec):
    run_plan_and_refresh([AAL], sec)
    sec_store.ledger_path(xbrl_store.facts_path(FC.sec_dir() / DAY, AAL)).unlink()
    entry = FC.index()[AAL]
    assert not entry.usable and FC.state_of(entry, TODAY) == FC.EVICTED
    sec.calls.clear()
    run_plan_and_refresh([AAL], sec)
    assert sec.calls == Counter({xbrl_store.COMPANYFACTS_URL.format(cik=AAL): 1})
    assert FC.load(AAL, FC.index()[AAL], lean=True).facts_fetched


def test_older_page_ledger_without_body_is_not_reused(store):
    sec = FakeSEC()
    sec.issuer(AAL, older=True)
    run_plan_and_refresh([AAL], sec)
    older = sec_store.submissions_path(FC.sec_dir() / DAY, AAL, f"CIK{AAL}-submissions-001.json")
    older.unlink()                                                            # the primary page alone remains
    assert FC.state_of(FC.index()[AAL], TODAY) == FC.EVICTED
    sec.calls.clear()
    run_plan_and_refresh([AAL], sec)
    assert list(sec.calls.values()) == [1] and len(FC.load(AAL, FC.index()[AAL]).submissions_rows) == 2


def test_interrupted_compact_leaves_each_issuer_evicted_or_whole(store, sec):
    run_plan_and_refresh([AAL, OTHER], sec)
    root = FC.sec_dir() / DAY
    sec_store.submissions_path(root, AAL, f"CIK{AAL}.json").unlink()          # compact stopped after one unlink
    entries = FC.index()
    assert FC.state_of(entries[AAL], TODAY) == FC.EVICTED and FC.state_of(entries[OTHER], TODAY) == FC.FRESH
    sec.calls.clear()
    assert run_plan_and_refresh([AAL, OTHER], sec)["planned"] == [AAL] and sec.requests == 1


def test_partial_file_from_an_interrupted_write_is_ignored(store, sec):
    run_plan_and_refresh([AAL], sec)
    root = FC.sec_dir() / DAY
    (root / "companyfacts" / f"CIK{AAL}.json.gz.partial").write_bytes(b"half")
    assert FC.state_of(FC.index()[AAL], TODAY) == FC.FRESH


def test_a_usable_older_copy_wins_over_a_newer_evicted_one(store, sec):
    run_plan_and_refresh([AAL], sec, day="2026-10-15", today=date(2026, 10, 15))
    run_plan_and_refresh([AAL], sec, day="2026-10-16", today=date(2026, 10, 16))   # forced second copy
    for body in bodies(FC.sec_dir() / "2026-10-16"):
        body.unlink()
    entry = FC.index()[AAL]
    assert entry.day == "2026-10-15" and entry.usable


# == disk contract ================================================================================

def test_compact_still_bounds_disk_after_refetch(store, sec):
    for cik in ("0000000101", "0000000102", "0000000103"):
        sec.issuer(cik)
    needed = [AAL, OTHER, "0000000101", "0000000102", "0000000103"]
    for _ in range(3):                                                        # three daily cycles, same root
        run_plan_and_refresh(needed, sec)
        FC.compact([OTHER])
        assert {p.name for p in bodies(FC.sec_dir())} == {f"CIK{OTHER}.json.gz"}
