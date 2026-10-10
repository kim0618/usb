"""H-V2-D7 operations repair: SEC material refresh is its own failure domain.

On 2026-10-10 the production host had no SEC submissions cache, the daily update's refresh scan
rewrote all eight issuers as ``NO_SUBMISSIONS_CACHE``, and AEYE's real ``REFRESH_DUE`` was lost. The
fixtures here are that day's real files: the launch snapshot and the refresh queue (sha256
``43123c04...``) as they stood before the incident, and AEYE's real submissions page fetched on
2026-09-28, which carries the 2026-09-18 8-K.

Every test uses a temporary store and an in-memory SEC transport. Nothing here reaches the network,
the real store, or anything A or E owns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from app.backtest.strategy_c_e0 import sec_store
from app.dev import run_h_v2_d7 as D7
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import sec_refresh as SR
from app.strategies.h_forward import store as ST
from app.strategies.h_forward import views as VW

FIXTURES = Path(__file__).parent / "fixtures"
QUEUE_FIXTURE = FIXTURES / "refresh_queue_20261004.json"
SNAPSHOT_FIXTURE = FIXTURES / "launch_snapshot_20261004.jsonl"
AEYE_PAGE = FIXTURES / "aeye_submissions_20260928.json.gz"
QUEUE_SHA = "43123c04ed7278aabd1e8b3ab530d76435e5789d364b0012215d122998275393"
AEYE_CIK = "0001362190"
AEYE_8K = "0001104659-26-108940"

BASELINE = "2026-10-02"
AFTER = ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The real 2026-10-04 snapshot and queue, in a temporary store."""
    root = tmp_path / "d7"
    root.mkdir()
    monkeypatch.setenv(ST.ROOT_ENV, str(root))
    (root / ST.LAUNCH_SNAPSHOT).write_bytes(SNAPSHOT_FIXTURE.read_bytes())
    (root / D7.REFRESH_QUEUE).write_bytes(QUEUE_FIXTURE.read_bytes())
    assert sha(root / D7.REFRESH_QUEUE) == QUEUE_SHA
    # Any real SEC client is a test bug: fail loudly rather than reach the network.
    monkeypatch.setattr(SR, "_default_client", _no_network)
    return root


def _no_network():
    raise AssertionError("a test reached for the real SEC client")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def queue(root: Path) -> dict:
    return json.loads((root / D7.REFRESH_QUEUE).read_text(encoding="utf-8"))


def cohort() -> dict[str, str]:
    return {row["ticker"]: sec_store.cik10(row["cik"]) for row in ST.launch_rows()}


def page(cik: str) -> bytes:
    """AEYE's real page; for the others, a page with filings only before the decision session."""
    if cik == AEYE_CIK:
        return gzip.decompress(AEYE_PAGE.read_bytes())
    return json.dumps({"cik": cik, "filings": {"recent": {
        "accessionNumber": [f"{cik}-26-000002", f"{cik}-26-000001"],
        "form": ["10-Q", "4"], "filingDate": ["2026-08-07", "2026-09-20"],
        "acceptanceDateTime": ["", ""], "items": ["", ""], "primaryDocument": ["", ""]}, "files": []}}).encode()


class Sec:
    """An in-memory SEC: serves the cohort's primary pages and records every URL asked for."""

    def __init__(self, *, fail: set[str] = frozenset(), pages=page) -> None:
        self.fail, self.pages, self.urls = set(fail), pages, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        cik = request.url.path.rsplit("CIK", 1)[1].split(".")[0]
        if cik in self.fail:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, content=self.pages(cik))

    def factory(self):
        return lambda: sec_store.SecClient(
            "USB Research test@example.com", sleeper=lambda _s: None,
            client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def use(monkeypatch, sec: Sec) -> Sec:
    monkeypatch.setattr(SR, "_default_client", sec.factory())
    return sec


def seed_cache(root_day: str, *, fetched_at: str, skip: str | None = None) -> None:
    """A complete dated root as sec_store would have written it, without HTTP."""
    base = SR.sec_root() / root_day
    for ticker, cik in cohort().items():
        if ticker == skip:
            continue
        path = SR.primary_path(base, cik)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(page(cik)))
        sec_store.ledger_path(path).write_text(json.dumps({"fetched_at": fetched_at}), encoding="utf-8")


def stub_prices(monkeypatch) -> None:
    """collect_prices without Massive: the baseline and five sessions after it, for the cohort and SPY."""
    def collect(**_):
        symbols = D7.cohort_symbols()
        for n, session in enumerate((BASELINE,) + AFTER):
            PR.write_session(session, {s: {"close": 10.0 + n, "high": 10.0 + n, "low": 10.0 + n,
                                           "volume": 1.0} for s in symbols},
                             fetched_at="2026-10-10T05:10:00+00:00")
        return {"written": list((BASELINE,) + AFTER), "failed": {}, "latest_session": PR.latest_session()}
    monkeypatch.setattr(D7, "collect_prices", collect)


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# -- the fixture is the real incident -----------------------------------------------------------------

def test_the_fixture_is_the_real_pre_incident_queue_with_aeye_refresh_due(store) -> None:
    body = queue(store)
    assert body["refresh_due"] == ["AEYE"]
    aeye = next(r for r in body["issuers"] if r["ticker"] == "AEYE")
    assert aeye["state"] == "REFRESH_DUE"
    assert {f["accession"] for f in aeye["new_material_filings"]} == {AEYE_8K}
    assert sorted(cohort()) == ["AEYE", "COLL", "DORM", "FG", "IDCC", "SCCO", "TG", "VRRM"]


# -- Case A: the cache is missing -------------------------------------------------------------------

def test_a_missing_cache_leaves_the_queue_byte_identical_and_aeye_refresh_due(store) -> None:
    result = D7.refresh_scan()
    assert result["scan_performed"] is False and result["replaced"] is False
    assert result["refresh_data"] == SR.DATA_NOT_READY == "REFRESH_DATA_NOT_READY"
    assert "NO_COMPLETE_SUBMISSIONS_CACHE" in result["refresh_data_reasons"]
    # research state and infrastructure state coexist
    assert result["refresh_due"] == ["AEYE"]
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA
    assert "NO_SUBMISSIONS_CACHE" not in (store / D7.REFRESH_QUEUE).read_text(encoding="utf-8")


def test_the_daily_update_with_no_sec_reachable_keeps_the_queue_and_says_degraded(store, monkeypatch) -> None:
    stub_prices(monkeypatch)
    use(monkeypatch, Sec(fail=set(cohort().values())))
    body = D7.update()
    assert body["refresh"]["refresh_run"] == SR.RUN_DEGRADED
    assert body["refresh"]["refresh_data"] == SR.DATA_NOT_READY
    assert body["refresh_due"] == ["AEYE"]
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA
    status = SR.read_status()
    assert status["refresh_run"] == "REFRESH_DEGRADED" and status["queue_replaced"] is False
    assert status["queue_sha256"] == QUEUE_SHA and status["research_refresh_due"] == ["AEYE"]


# -- prices and outcomes do not depend on SEC ---------------------------------------------------------

def test_prices_and_outcomes_mature_without_any_sec_cache(store, monkeypatch) -> None:
    stub_prices(monkeypatch)
    use(monkeypatch, Sec(fail=set(cohort().values())))
    body = D7.update()
    assert PR.stored_sessions() == [BASELINE, *AFTER]
    assert body["integrity"] == "PASS"
    maturity = VW.forward()["maturity"]
    assert maturity["1D"]["matured"] == 8 and maturity["5D"]["matured"] == 8
    assert maturity["21D"]["matured"] == 0 and maturity["63D"]["matured"] == 0
    assert body["decision_counts"] == {"APPROVE": 0, "WATCH": 6, "REJECT": 2}


def test_a_crashing_scan_does_not_touch_prices_or_the_exit_code(store, monkeypatch, capsys) -> None:
    stub_prices(monkeypatch)
    use(monkeypatch, Sec())

    def boom(**_):
        raise RuntimeError("scan exploded")
    monkeypatch.setattr(D7, "refresh_scan", boom)
    assert D7.main(["update"]) == 0
    captured = capsys.readouterr()
    assert "REFRESH_DEGRADED" in captured.err and "MATERIAL_SCAN_ERROR" in captured.err
    assert PR.stored_sessions() == [BASELINE, *AFTER]
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA


def test_the_existing_price_sessions_are_not_rewritten_by_a_rerun(store, monkeypatch) -> None:
    stub_prices(monkeypatch)
    use(monkeypatch, Sec(fail=set(cohort().values())))
    D7.update()
    before = {s: sha(PR.session_path(s)) for s in AFTER}
    D7.update()
    assert {s: sha(PR.session_path(s)) for s in AFTER} == before


def test_an_integrity_failure_is_the_units_failure(store, monkeypatch, capsys) -> None:
    stub_prices(monkeypatch)
    use(monkeypatch, Sec(fail=set(cohort().values())))
    rows = [json.loads(line) for line in SNAPSHOT_FIXTURE.read_text(encoding="utf-8").splitlines()]
    rows[0]["position"] = {"qty": 1}
    (store / ST.LAUNCH_SNAPSHOT).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert D7.main(["update"]) == D7.EXIT_INTEGRITY_FAILURE
    assert "H_FORWARD_INTEGRITY_FAILURE" in capsys.readouterr().err


# -- stale or partial caches -------------------------------------------------------------------------

def test_a_stale_cache_is_not_ready_and_does_not_rescan(store) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=SR.MAX_CACHE_AGE_DAYS + 1))
    seed_cache(old.date().isoformat(), fetched_at=old.isoformat())
    result = D7.refresh_scan()
    assert result["scan_performed"] is False
    assert any(r.startswith("CACHE_STALE:") for r in result["refresh_data_reasons"])
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA


def test_a_root_missing_one_issuer_is_not_ready(store) -> None:
    seed_cache(today(), fetched_at=datetime.now(timezone.utc).isoformat(), skip="TG")
    result = D7.refresh_scan()
    assert result["scan_performed"] is False
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA


def test_a_partial_sec_failure_preserves_the_queue_and_scans_nothing_older(store, monkeypatch) -> None:
    # yesterday's complete root exists; today one issuer fails - the scan must not fall back to it
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    seed_cache(yesterday.date().isoformat(), fetched_at=yesterday.isoformat())
    sec = use(monkeypatch, Sec(fail={cohort()["TG"]}))
    status = D7._material_refresh()
    assert status["refresh_run"] == SR.RUN_DEGRADED
    assert any(r.startswith("SEC_FETCH_INCOMPLETE:TG") for r in status["reasons"])
    assert status["queue_replaced"] is False and status["fetch"]["complete"] is False
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA
    assert len({u for u in sec.urls}) == 8


# -- Case B: a valid cache -----------------------------------------------------------------------------

def test_all_issuers_fetched_rescans_and_atomically_replaces_the_queue(store, monkeypatch) -> None:
    sec = use(monkeypatch, Sec())
    status = D7._material_refresh()
    assert status["refresh_run"] == SR.RUN_OK and status["reasons"] == []
    assert status["refresh_data"] == SR.DATA_READY and status["queue_replaced"] is True
    body = queue(store)
    assert status["queue_sha256"] == sha(store / D7.REFRESH_QUEUE) != QUEUE_SHA
    states = {r["ticker"]: r["state"] for r in body["issuers"]}
    assert states.pop("AEYE") == "REFRESH_DUE"
    assert set(states.values()) == {"NO_NEW_MATERIAL_EVIDENCE"}       # fetched today, nothing after 09-16
    aeye = next(r for r in body["issuers"] if r["ticker"] == "AEYE")
    assert AEYE_8K in {f["accession"] for f in aeye["new_material_filings"]}
    assert body["refresh_due"] == ["AEYE"] and body["cache_as_of"] == today()
    # no temp file is left beside the queue
    assert [p.name for p in store.iterdir() if p.name.endswith(".tmp")] == []
    # exactly one primary page per cohort CIK, nothing else
    expected = {sec_store.SUBMISSIONS_URL.format(name=f"CIK{c}.json") for c in cohort().values()}
    assert set(sec.urls) == expected and len(sec.urls) == 8


def test_the_scan_is_deterministic_and_a_same_day_rerun_makes_no_request(store, monkeypatch) -> None:
    sec = use(monkeypatch, Sec())
    D7._material_refresh()
    first = queue(store)
    D7._material_refresh()
    second = queue(store)
    assert len(sec.urls) == 8                                  # the second run reused today's root
    strip = lambda b: {k: v for k, v in b.items() if k != "generated_at"}  # noqa: E731
    assert strip(first) == strip(second)


def test_a_dry_run_scan_never_writes_the_queue(store, monkeypatch) -> None:
    seed_cache(today(), fetched_at=datetime.now(timezone.utc).isoformat())
    result = D7.refresh_scan(dry_run=True)
    assert result["scan_performed"] is True and result["replaced"] is False
    assert result["candidate_refresh_due"] == ["AEYE"]
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA


def test_a_scan_that_would_lose_a_known_filing_is_refused(store, monkeypatch) -> None:
    def without_the_8k(cik: str) -> bytes:
        if cik != AEYE_CIK:
            return page(cik)
        body = json.loads(gzip.decompress(AEYE_PAGE.read_bytes()))
        recent = body["filings"]["recent"]
        keep = [i for i, a in enumerate(recent["accessionNumber"]) if a != AEYE_8K]
        body["filings"]["recent"] = {k: [v[i] for i in keep] for k, v in recent.items()}
        return json.dumps(body).encode()
    use(monkeypatch, Sec(pages=without_the_8k))
    status = D7._material_refresh()
    assert status["refresh_run"] == SR.RUN_DEGRADED
    assert any("KNOWN_FILING_LOST:AEYE" in r for r in status["reasons"])
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA


def test_a_failure_during_the_atomic_replace_keeps_the_old_queue(store, monkeypatch) -> None:
    seed_cache(today(), fetched_at=datetime.now(timezone.utc).isoformat())

    def refuse(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(SR.os, "replace", refuse)
    with pytest.raises(OSError):
        D7.refresh_scan()
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA
    assert [p.name for p in store.iterdir() if p.name.endswith(".tmp")] == []


def test_a_refused_user_agent_is_a_degraded_refresh_not_a_crash(store, monkeypatch) -> None:
    def refuse():
        raise sec_store.SecUserAgentMissing("no contact")
    monkeypatch.setattr(SR, "_default_client", refuse)
    status = D7._material_refresh()
    assert status["refresh_run"] == SR.RUN_DEGRADED
    assert sha(store / D7.REFRESH_QUEUE) == QUEUE_SHA


# -- the cache's own housekeeping --------------------------------------------------------------------

def test_the_cache_lives_under_the_store_root_not_the_working_directory(store, monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    assert SR.sec_root().is_absolute() and SR.sec_root().parent == ST.root()


def test_the_cache_reuses_the_d1_1_user_agent(monkeypatch) -> None:
    from app.dev.acquire_strategy_h_v2_fundamentals import USER_AGENT
    assert sec_store.check_user_agent(USER_AGENT) == USER_AGENT


def test_only_the_oldest_dated_roots_are_pruned(store) -> None:
    for n in range(SR.KEEP_DATED_ROOTS + 3):
        (SR.sec_root() / f"2026-09-{n + 1:02d}").mkdir(parents=True)
    removed = SR.prune()
    assert removed == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert len(SR.dated_roots()) == SR.KEEP_DATED_ROOTS


def test_the_freshness_is_read_from_the_request_ledger_not_the_file_mtime(store) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=30)
    seed_cache(today(), fetched_at=old.isoformat())         # the directory says today, the ledger does not
    for path in SR.sec_root().rglob("*.gz"):
        os.utime(path)
    assert D7.refresh_scan()["scan_performed"] is False
