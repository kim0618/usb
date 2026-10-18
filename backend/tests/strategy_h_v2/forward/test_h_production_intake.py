"""H-V2 AUTONOMOUS PIPELINE V1 completion: new-candidate intake on production sources.

Covers ``h_auto.sources`` (A's stores, read-only), ``h_auto.facts_cache`` (SEC on demand),
``h_auto.method_judgement`` (code-owned judgement from the frozen section-17 rules), the SCAN/GATE
intake and the executor's new-candidate path. Every test runs on temporary directories; no SEC, no
Massive, no model is called. The frozen E1/E2/E3 functions, D6 ``decide`` and the paper engine are
the real ones.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import executor as EX
from app.strategies.h_auto import facts_cache as FC
from app.strategies.h_auto import inputs as IN
from app.strategies.h_auto import intake as INT
from app.strategies.h_auto import method_judgement as MJ
from app.strategies.h_auto import orchestrator as ORC
from app.strategies.h_auto import queue as Q
from app.strategies.h_auto import sources as SRC
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import research_queue as RQ
from app.strategies.h_forward import sec_refresh as SR
from app.strategies.h_forward import store as ST
from app.strategies.h_paper import service as HP
from tests.strategy_h_v2.forward import test_h_autonomous_pipeline as T

REAL_SCREEN = INT.screen_method_judgement
REAL_CONTRACT = AC.contract
RUN_AT = datetime(2026, 10, 13, 21, 30, tzinfo=timezone.utc)      # Tue 13 Oct 17:30 ET -> publishable 10-12
PUBLISHABLE = date(2026, 10, 12)


# -- fixtures: a tiny production-shaped A store ------------------------------------------------------

def gz(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(body).encode()))


def reference_doc(as_of: str, rows: list[dict]) -> dict:
    return {"format": "strategy-c-selection-raw-v1", "as_of": as_of, "security_type": "CS", "pages": 1, "results": rows}


def ref_row(ticker: str, cik: str | None = "1234", **extra) -> dict:
    return {"ticker": ticker, "type": "CS", "market": "stocks", "locale": "us", "active": True, "cik": cik,
            "primary_exchange": "XNYS", "share_class_figi": f"BBG{ticker}", "composite_figi": None} | extra


def sessions_back(n: int, until: date = PUBLISHABLE) -> list[date]:
    from app.market.calendar import MarketCalendar
    cal = MarketCalendar()
    out, day = [], until
    while len(out) < n:
        out.append(day)
        day = cal.previous_trading_day(day)
    return sorted(out)


def grouped_store(root: Path, bars: dict[str, tuple[float, float]], *, sessions: int = 300,
                  until: date = PUBLISHABLE) -> Path:
    """One grouped file per session; every ticker prints the same (close, volume) each day."""
    directory = root / "grouped"
    for day in sessions_back(sessions, until):
        results = [{"T": t, "c": c, "v": v, "o": c, "h": c, "l": c} for t, (c, v) in bars.items()]
        gz(directory / f"{day.isoformat()}.json.gz",
           {"format": "strategy-c-selection-raw-v1", "session": day.isoformat(), "body": {"results": results}})
    return directory


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(ST.ROOT_ENV, str(tmp_path / "d7"))
    return tmp_path / "d7"


@pytest.fixture
def a_store(tmp_path, monkeypatch):
    """A's three stores under tmp, wired into sources/intake; H's own dirs under the H store."""
    root = tmp_path / "a_raw"
    grouped_store(root, {"SPY": (500.0, 1e8), "GOOD": (20.0, 1e6), "PENNY": (0.5, 1e7), "THIN": (30.0, 100.0)})
    gz(root / "tickers" / "CS_2026-10-01.json.gz", reference_doc("2026-10-01", [
        ref_row("GOOD", "0000000777"), ref_row("PENNY", "0000000888"), ref_row("THIN", "0000000999"),
        ref_row("NOCIK", None), ref_row("NEWIPO", "0000000555"),
        ref_row("FG", T.CIK["FG"]), ref_row("ETF", "1", type="ETF")]))
    gz(root / "splits" / "splits_2026-10-05_2026-10-26.json.gz",
       {"format": "strategy-c-selection-raw-v1", "start": "2026-10-05", "end": "2026-10-26", "pages": 1,
        "results": [{"ticker": "GOOD", "execution_date": "2026-10-06", "split_from": 2, "split_to": 1}]})
    monkeypatch.setattr(SRC, "A_RAW_ROOT", root)
    monkeypatch.setattr(SRC, "A_REFERENCE_DIR", root / "tickers")
    monkeypatch.setattr(SRC, "A_GROUPED_DIR", root / "grouped")
    monkeypatch.setattr(SRC, "A_SPLITS_DIR", root / "splits")
    return root


def tree_sha(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


# == sources ======================================================================================

def test_reference_in_force_is_newest_on_or_before_and_h_same_day_wins(store, a_store):
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [ref_row("GOOD", "0000000777")]))
    gz(SRC.h_reference_dir() / "CS_2026-10-01.json.gz", reference_doc("2026-10-01", [ref_row("GOOD", "0000000777")]))
    snaps = SRC.reference_snapshots()
    assert [(s.as_of.isoformat(), s.owner) for s in snaps] == [("2026-10-01", "H"), ("2026-10-12", "H")]
    assert SRC.reference_in_force(date(2026, 10, 9), snaps).as_of == date(2026, 10, 1)
    assert SRC.reference_in_force(date(2026, 10, 12), snaps).as_of == date(2026, 10, 12)
    assert SRC.reference_in_force(date(2026, 9, 1), snaps) is None
    assert SRC.cik_map(snaps[-1]) == {"GOOD": "0000000777"}


def test_universe_rows_use_the_frozen_build_universe_and_identity_rules(store, a_store):
    snap = SRC.reference_in_force(PUBLISHABLE)
    rows = {r.ticker: r for r in SRC.universe_rows(snap, known_at=RUN_AT)}
    assert "ETF" not in rows                                    # not an active US CS row
    assert rows["GOOD"].cik == "0000000777" and rows["GOOD"].security_id == "BBGGOOD"
    assert rows["GOOD"].snapshot_date == "2026-10-01" and rows["GOOD"].exchange_supported
    assert INT._identity_state(rows["NOCIK"]) == INT.REFERENCE_NOT_READY
    assert INT._identity_state(rows["GOOD"]) == INT.SCANNED


def test_refresh_reference_uses_a_fetcher_writes_only_h_dir_and_never_a_store(store, a_store, tmp_path):
    before = tree_sha(a_store)

    class Client:
        def reference_tickers(self, as_of, *, security_type, max_pages=10):
            assert (as_of, security_type) == (PUBLISHABLE, "CS")
            return ({"results": [ref_row("GOOD", "0000000777"), ref_row("NEW", "0000000111")]},)

        def close(self):
            pass

    out = SRC.refresh_reference(RUN_AT, client_factory=Client)
    assert out["as_of"] == "2026-10-12" and out["rows"] == 2
    path = SRC.h_reference_dir() / "CS_2026-10-12.json.gz"
    assert path.is_file() and path.is_relative_to(store)
    assert tree_sha(a_store) == before
    assert SRC.refresh_reference(RUN_AT, client_factory=lambda: pytest.fail("cached: no fetch"))["status"] == "cached"
    assert not (AS.root() / "reference_staging" / "tickers" / "CS_2026-10-12.json.gz").exists()


def test_refresh_reference_rejects_duplicate_tickers(store, a_store):
    class Client:
        def reference_tickers(self, as_of, *, security_type, max_pages=10):
            return ({"results": [ref_row("GOOD", "1"), ref_row("GOOD", "1")]},)

    out = SRC.refresh_reference(RUN_AT, client_factory=Client)
    assert out["status"].startswith("REJECTED") and not list(SRC.h_reference_dir().glob("*"))


def test_market_gate_matches_the_frozen_market_snapshot(store, a_store):
    from app.dev.run_strategy_h_v2_d1 import market_snapshot
    gates = SRC.market_gate(["GOOD", "PENNY", "THIN", "SPY"])
    series = SRC.series_for(["GOOD"])
    snap = market_snapshot(series["GOOD"])
    assert gates["GOOD"].sessions == snap.trailing_sessions == 300
    assert gates["GOOD"].latest_close == snap.latest_close == 20.0
    assert gates["GOOD"].trailing_avg_dollar_volume == pytest.approx(snap.trailing_avg_dollar_volume)
    assert gates["GOOD"].latest_session == PUBLISHABLE
    assert INT._market_state(gates["GOOD"]) == INT.SCANNED
    assert INT._market_state(gates["PENNY"]) == INT.MARKET_INELIGIBLE
    assert INT._market_state(gates["THIN"]) == INT.MARKET_INELIGIBLE
    assert INT._market_state(None) == INT.PRICE_NOT_READY
    assert SRC.splits_index() == {"GOOD": [date(2026, 10, 6)]}
    assert sorted(series) == ["GOOD", "SPY"]


def test_price_readiness_sample_reports_coverage(store, a_store):
    report = SRC.price_readiness(RUN_AT, ["GOOD", "NEWIPO"])
    assert report["latest_is_publishable"] and report["history_sufficient"] and report["files"] == 300
    by = {r["ticker"]: r for r in report["sample"]}
    assert by["GOOD"]["usable_for_h"] and by["GOOD"]["sessions"] == 300 and by["GOOD"]["splits_in_window"] == ["2026-10-06"]
    assert not by["NEWIPO"]["usable_for_h"] and by["NEWIPO"]["sessions"] == 0
    assert SRC.deterministic_sample([f"T{i:03d}" for i in range(100)], 5) == ["T000", "T020", "T040", "T060", "T080"]


# == freshness gate ================================================================================

def test_gate_refuses_stale_reference_stale_prices_and_short_history(store, a_store, tmp_path):
    gate = INT.freshness(RUN_AT)
    assert gate["state"] == INT.DATA_NOT_READY
    assert gate["reasons"] == ["REFERENCE_SNAPSHOT_STALE:2026-10-01:11d>7d"]
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [ref_row("GOOD", "0000000777")]))
    assert INT.freshness(RUN_AT)["state"] == INT.READY
    short = grouped_store(tmp_path / "short", {"SPY": (1.0, 1.0)}, sessions=40, until=date(2026, 10, 9))
    gate = INT.freshness(RUN_AT, grouped=short)
    assert any(r.startswith("LATEST_PRICE_SESSION_STALE:2026-10-09") for r in gate["reasons"])
    assert any(r.startswith("PRICE_HISTORY_INSUFFICIENT:40<252") for r in gate["reasons"])
    assert "companyfacts" not in json.dumps(gate)              # fundamentals are per issuer, not a gate


def test_gate_mode_refreshes_reference_and_never_touches_sec_or_the_intake_file(store, a_store):
    SR.atomic_write_json(ST.path(INT.NEW_CANDIDATES), {"candidates": [], "marker": "before"})
    fetched = []

    class Client:
        def reference_tickers(self, as_of, *, security_type, max_pages=10):
            fetched.append(as_of)
            return ({"results": [ref_row("GOOD", "0000000777")]},)

    status = INT.intake_step(now=RUN_AT, mode=INT.GATE, massive_client_factory=Client,
                             sec_client_factory=lambda: pytest.fail("GATE makes no SEC call"))
    assert fetched == [PUBLISHABLE] and status["state"] == INT.READY
    assert status["new_candidate_scan"] == "GATE_READY_SCAN_IN_RESEARCH_UNIT"
    assert SR.read_json(ST.path(INT.NEW_CANDIDATES))["marker"] == "before"
    assert SR.read_json(ST.path(INT.INTAKE_STATUS))["reference_refresh"]["rows"] == 1


def test_scan_mode_on_data_not_ready_leaves_intake_untouched(store, a_store):
    SR.atomic_write_json(ST.path(INT.NEW_CANDIDATES), {"candidates": [], "marker": "before"})
    status = INT.intake_step(now=RUN_AT, mode=INT.SCAN, sec_client_factory=lambda: pytest.fail("no SEC"))
    assert status["new_candidate_scan"] == INT.DATA_NOT_READY
    assert SR.read_json(ST.path(INT.NEW_CANDIDATES))["marker"] == "before"


# == facts cache ===================================================================================

def test_facts_cache_plan_prefers_missing_then_oldest_and_prunes_roots(store, monkeypatch):
    today = date(2026, 10, 13)
    entries = {"1": FC.CacheEntry("1", "2026-10-10", datetime(2026, 10, 10, tzinfo=timezone.utc), True),
               "2": FC.CacheEntry("2", "2026-10-01", datetime(2026, 10, 1, tzinfo=timezone.utc), True),
               "3": FC.CacheEntry("3", "2026-10-03", datetime(2026, 10, 3, tzinfo=timezone.utc), True)}
    assert FC.state_of(entries["1"], today) == FC.FRESH and FC.state_of(entries["2"], today) == FC.STALE
    assert FC.plan(["1", "2", "3", "9", "8"], today, cap=10, entries=entries) == ["8", "9", "2", "3"]
    assert FC.plan(["1", "2", "3", "9", "8"], today, cap=2, entries=entries) == ["8", "9"]
    for day in ("2026-09-01", "2026-09-02", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-10", "2026-10-13"):
        (FC.sec_dir() / day / "submissions").mkdir(parents=True)
    assert FC.prune(keep=6) == ["2026-09-01"]
    assert [r.name for r in FC.dated_roots()][0] == "2026-09-02"


def test_facts_cache_refresh_records_not_found_and_stops_on_transport_failure(store, monkeypatch):
    calls = []

    def fake_fetch(cik, day, *, client):
        calls.append(cik)
        if cik == "2":
            raise IN.InputsNotReady("SEC_SUBMISSIONS_NOT_FOUND", cik)
        if cik == "4":
            raise IN.SecUnavailable("SEC_FETCH_ERROR:503")
        return "FETCHED"

    monkeypatch.setattr(IN, "fetch_sec_files", fake_fetch)
    monkeypatch.setattr(IN, "fetch_sec", lambda *a, **k: pytest.fail("refresh must not parse the documents"))
    closed = []
    report = FC.refresh(["1", "2", "3", "4", "5"], day="2026-10-13", client_factory=lambda: SimpleNamespace(close=lambda: closed.append(1)))
    assert report["fetched"] == ["1", "3"] and report["not_found"] == [{"cik": "2", "why": "SEC_SUBMISSIONS_NOT_FOUND"}]
    assert report["degraded"]["cik"] == "4" and calls == ["1", "2", "3", "4"] and closed == [1]


def test_fetch_sec_files_makes_exactly_fetch_secs_store_calls(store, monkeypatch):
    """Memory repair: the refresh step downloads without parsing; the store calls (and so the bytes and
    ledgers written) are the same two calls ``fetch_sec`` makes."""
    from app.backtest.strategy_c_e0 import sec_store
    from app.backtest.strategy_eqm_v0 import xbrl_store
    seen = {"files": [], "full": []}
    bucket = ["files"]
    monkeypatch.setattr(sec_store, "fetch_cik", lambda client, root, cik, required_from: seen[bucket[0]].append(
        ("sub", str(root), cik, required_from)) or SimpleNamespace(status="OK", pages=()))
    monkeypatch.setattr(xbrl_store, "fetch_cik", lambda client, root, cik: seen[bucket[0]].append(
        ("facts", str(root), cik)) or "FETCHED")
    assert IN.fetch_sec_files("123", "2026-10-13", client=object()) == "FETCHED"
    bucket[0] = "full"
    monkeypatch.setattr(sec_store, "read_gz_json", lambda path: (_ for _ in ()).throw(StopIteration("parse begins")))
    with pytest.raises(StopIteration):
        IN.fetch_sec("123", "2026-10-13", client=object())
    assert seen["files"] == seen["full"] and len(seen["files"]) == 2


# == method judgement ==============================================================================

def issuer_session(*, results: dict[str, float | None], net_debt=50.0, ev=1000.0, revenue=400.0, oi=40.0,
                   fcf=30.0, equity=100.0):
    def item(v):
        return SimpleNamespace(ok=v is not None, value=v)
    return SimpleNamespace(session=PUBLISHABLE, price=10.0, market_cap=950.0, shares=95.0, net_debt=net_debt,
                           enterprise_value=ev, share_class_confidence="HIGH",
                           results={m: SimpleNamespace(ok=v is not None, value=v) for m, v in results.items()},
                           ttm={"revenue": item(revenue), "operating_income": item(oi), "free_cash_flow": item(fcf)},
                           equity=item(equity))


ALL = {"P/B": 9.5, "P/FCF": 31.0, "EV/Sales": 2.5, "EV/EBIT": 25.0, "EV/EBITDA": 20.0, "EV/FCF": 33.0, "P/E": 28.0}


def test_code_owned_judgement_follows_section_17_in_method_order_and_withholds_pb():
    from app.backtest.strategy_h_v2.valuation.multiples import METHOD_ORDER, method_suitability
    row = issuer_session(results=ALL)
    d = MJ.derive("NEWCO", row, sic="7372", sic_description="Services-Prepackaged Software")
    assert d.state == MJ.AUTO_READY
    j = d.judgement
    expected_primary = [m for m in METHOD_ORDER if m != "P/B" and method_suitability(
        m, net_debt=50.0, enterprise_value=1000.0, ttm_revenue=400.0, ttm_operating_income=40.0)[0].value == "ECONOMICALLY_REASONABLE"]
    assert list(j.primary_preference) == expected_primary == ["P/FCF", "P/E"]
    assert list(j.secondary_preference) == ["EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF"]   # net debt 5% of EV
    assert "P/B" not in j.primary_preference + j.secondary_preference and j.unsuitable == {}
    assert d.suitability["P/B"]["suitability"] == "WITHHELD"
    assert "Services-Prepackaged Software" in j.business and "10.0% margin" in j.business
    assert j.valuation_risk[0].startswith(MJ.VERSION)
    report = d.report()
    assert report["version"] == MJ.VERSION and report["judgement"]["primary_preference"] == ["P/FCF", "P/E"]


def test_code_owned_judgement_is_deterministic_and_the_frozen_table_is_untouched():
    from app.dev.run_strategy_h_v2_d6 import METHOD_JUDGEMENTS
    before = {t: (j.primary_preference, j.secondary_preference, dict(j.unsuitable)) for t, j in METHOD_JUDGEMENTS.items()}
    a = MJ.derive("X", issuer_session(results=ALL), sic="3714").report()
    b = MJ.derive("X", issuer_session(results=ALL), sic="3714").report()
    assert a == b
    assert {t: (j.primary_preference, j.secondary_preference, dict(j.unsuitable)) for t, j in METHOD_JUDGEMENTS.items()} == before
    assert "X" not in METHOD_JUDGEMENTS


@pytest.mark.parametrize("kwargs, sic, reason", [
    ({"results": ALL}, "6311", "FINANCIAL_SECTOR"),
    ({"results": ALL}, None, "SIC_UNRESOLVED"),
    ({"results": {m: None for m in ALL}}, "3714", "NO_COMPUTABLE_METHOD"),
    ({"results": ALL, "oi": 2.0}, "3714", "NO_ECONOMICALLY_REASONABLE_METHOD"),   # 0.5% margin: all SECONDARY_ONLY
    ({"results": {"P/B": 1.2, "EV/Sales": None}}, "3714", "NO_ECONOMICALLY_REASONABLE_METHOD"),  # P/B only
])
def test_ambiguous_issuers_fail_closed(kwargs, sic, reason):
    d = MJ.derive("AMB", issuer_session(**kwargs), sic=sic)
    assert d.state == MJ.NOT_READY and d.judgement is None
    assert any(r.startswith(reason) for r in d.reasons)
    cov = MJ.coverage([d, MJ.derive("OK", issuer_session(results=ALL), sic="3714")])
    assert cov[MJ.AUTO_READY] == 1 and cov[MJ.NOT_READY] == 1 and cov["reasons"].get(reason) == 1


def test_pre_profit_issuer_gets_ev_sales_as_primary():
    d = MJ.derive("BIO", issuer_session(results={"EV/Sales": 8.0, "P/B": 3.0, "P/FCF": None}, oi=-50.0, fcf=-40.0,
                                        net_debt=300.0), sic="2834")                      # net debt 30% of EV
    assert d.state == MJ.AUTO_READY and list(d.judgement.primary_preference) == ["EV/Sales"]
    assert d.judgement.secondary_preference == ()


# == SCAN end to end on fakes ======================================================================

def fake_sec(cik: str, *, facts_fetched: bool = True, latest: str = "2026-10-09") -> IN.SecInputs:
    doc = {"cik": cik, "sic": "3714", "sicDescription": "Motor Vehicle Parts",
           "filings": {"recent": {"accessionNumber": [f"{cik}-26-000001"], "acceptanceDateTime": [f"{latest}T21:00:00.000Z"]}}}
    return IN.SecInputs(cik=cik, root=Path("x"), submissions_rows=[], submissions_doc=doc, submissions_checksum="s",
                        submissions_fetched_at=RUN_AT, facts_fetched=facts_fetched, facts=[], facts_document=None)


def wire_fakes(monkeypatch, *, fetched: list[str], scan_result):
    """SEC: every planned CIK is 'fetched' into today's root (body + ledger, as the stores write them);
    E1->E3: ``scan_result`` per row."""
    def fake_refresh(ciks, *, day, client_factory, log=lambda _m: None):
        for cik in ciks:
            root = FC.sec_dir() / day
            (root / "submissions" / f"CIK{cik}").mkdir(parents=True, exist_ok=True)
            (root / "submissions" / f"CIK{cik}" / f"CIK{cik}.json.gz").write_bytes(gzip.compress(b"{}"))
            (root / "submissions" / f"CIK{cik}" / f"CIK{cik}.json.gz.request.json").write_text(
                json.dumps({"fetched_at": RUN_AT.isoformat()}))
            (root / "companyfacts").mkdir(parents=True, exist_ok=True)
            (root / "companyfacts" / f"CIK{cik}.json.gz").write_bytes(gzip.compress(b"{}"))
            (root / "companyfacts" / f"CIK{cik}.json.gz.request.json").write_text("{}")
            fetched.append(cik)
        return {"day": day, "requested": len(ciks), "fetched": list(ciks), "not_found": [], "degraded": None}

    monkeypatch.setattr(FC, "refresh", fake_refresh)
    monkeypatch.setattr(FC, "load", lambda cik, entry, **_: fake_sec(cik))
    monkeypatch.setattr(INT, "scan", scan_result)

    def screen_all_ready(ordered, *, index, now, publishable, room, exclude, sec_client_factory, limit=40, log=None):
        chosen = [dict(c) | {"method_judgement": MJ.AUTO_READY} for c in ordered if c["ticker"] not in set(exclude)][:room]
        return {"chosen": chosen, "judged_this_run": len(chosen), "counts": {}, "room": room, "limit": limit,
                "excluded_already_queued": len(set(exclude))}

    monkeypatch.setattr(INT, "screen_method_judgement", screen_all_ready)


def scan_everything_p1(inputs, *, now, run_id):
    cands, recs = [], []
    for row in inputs.rows:
        facts, fetched = inputs.facts_for(row.cik)
        assert fetched and inputs.series_for(row.ticker) and inputs.latest_filing(row.cik) == date(2026, 10, 9)
        pri = "P1_HIGH" if row.ticker == "GOOD" else "P2_MEDIUM"
        recs.append({"ticker": row.ticker, "cik": row.cik, "security_id": row.security_id, "exchange": row.exchange,
                     "snapshot_date": row.snapshot_date, "scanned_at": now.isoformat(), "run_id": run_id,
                     "eligibility": "ELIGIBLE", "eligibility_reasons": [], "e3_priority": pri,
                     "days_since_latest_filing": 4, "latest_filing": "2026-10-09", "trailing_sessions": 300})
        cands.append({"ticker": row.ticker, "cik": row.cik, "e3_priority": pri, "days_since_latest_filing": 4})
    return INT.order(cands), recs


def test_scan_mode_fetches_only_market_passing_ciks_and_writes_ordered_candidates(store, a_store, monkeypatch):
    T.seed({"FG": "WATCH"})                                  # FG is in the ledger: refreshed, never admitted
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [
        ref_row("GOOD", "0000000777"), ref_row("ALSO", "0000000666"), ref_row("PENNY", "0000000888"),
        ref_row("THIN", "0000000999"), ref_row("NOCIK", None), ref_row("NEWIPO", "0000000555"), ref_row("FG", T.CIK["FG"])]))
    grouped_store(a_store, {"SPY": (500.0, 1e8), "GOOD": (20.0, 1e6), "ALSO": (15.0, 2e6), "PENNY": (0.5, 1e7),
                            "THIN": (30.0, 100.0), "FG": (40.0, 1e6)})
    fetched: list[str] = []
    wire_fakes(monkeypatch, fetched=fetched, scan_result=scan_everything_p1)
    before = tree_sha(a_store)
    status = INT.intake_step(now=RUN_AT, mode=INT.SCAN, sec_client_factory=lambda: None)
    assert status["new_candidate_scan"] == INT.READY
    scan = status["scan"]
    assert sorted(fetched) == ["0000000666", "0000000777"]            # PENNY/THIN/NEWIPO/NOCIK/FG: no SEC request
    assert scan["readiness_counts"] == {INT.SCANNED: 2, INT.MARKET_INELIGIBLE: 2, INT.PRICE_NOT_READY: 1,
                                        INT.REFERENCE_NOT_READY: 1, INT.COHORT: 1}
    body = SR.read_json(ST.path(INT.NEW_CANDIDATES))
    assert [c["ticker"] for c in body["candidates"]] == ["GOOD", "ALSO"]
    assert body["candidates"][0]["intake_order"] == 1 and body["candidates"][0]["readiness"] == INT.SCANNED
    assert body["source"]["reference"] == {"as_of": "2026-10-12", "owner": "H"}
    index = INT.read_index()
    assert index["GOOD"]["facts_day"] == "2026-10-13" and index["GOOD"]["readiness"] == INT.SCANNED
    assert index["PENNY"]["readiness"] == INT.MARKET_INELIGIBLE if "PENNY" in index else True
    assert tree_sha(a_store) == before                                # A's stores: byte-identical
    assert scan["e3_counts"] == {"P1_HIGH": 1, "P2_MEDIUM": 1} and scan["model_calls"] == 0
    # second run: facts are fresh, nothing refetched, nothing rescanned, candidates unchanged
    fetched.clear()
    status2 = INT.intake_step(now=RUN_AT, mode=INT.SCAN, sec_client_factory=lambda: None)
    assert fetched == [] and status2["scan"]["scanned_this_run"] == 0 and status2["written"] == 2
    # the queue takes them after the refreshes, in intake order
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    plan = RQ.build(caches=T.CACHES, index=T.INDEX)
    assert plan["queued"] == ["FG", "GOOD", "ALSO"]


def test_scan_respects_the_fetch_cap_and_reports_fetch_required(store, a_store, monkeypatch):
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [
        ref_row("GOOD", "0000000777"), ref_row("ALSO", "0000000666")]))
    grouped_store(a_store, {"SPY": (500.0, 1e8), "GOOD": (20.0, 1e6), "ALSO": (15.0, 2e6)})
    fetched: list[str] = []
    wire_fakes(monkeypatch, fetched=fetched, scan_result=scan_everything_p1)
    monkeypatch.setattr(AC, "intake_fetch_cap", lambda: 1)
    status = INT.intake_step(now=RUN_AT, mode=INT.SCAN, sec_client_factory=lambda: None)
    assert fetched == ["0000000666"]                                   # by CIK, deterministic
    assert status["scan"]["readiness_counts"] == {INT.SCANNED: 1, INT.FUNDAMENTALS_FETCH_REQUIRED: 1}
    assert [c["ticker"] for c in SR.read_json(ST.path(INT.NEW_CANDIDATES))["candidates"]] == ["ALSO"]


def test_scan_records_sec_not_found_and_stale_facts_are_not_candidates(store, a_store, monkeypatch):
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [ref_row("GOOD", "0000000777")]))
    grouped_store(a_store, {"SPY": (500.0, 1e8), "GOOD": (20.0, 1e6)})
    monkeypatch.setattr(FC, "refresh", lambda ciks, *, day, client_factory, log=None: {
        "day": day, "requested": 1, "fetched": [], "not_found": [{"cik": "0000000777", "why": "SEC_SUBMISSIONS_NOT_FOUND"}],
        "degraded": None})
    status = INT.intake_step(now=RUN_AT, mode=INT.SCAN, sec_client_factory=lambda: None)
    assert status["scan"]["readiness_counts"] == {INT.SEC_NOT_READY: 1} and status["written"] == 0


# == executor: new candidate through the code-owned judgement ======================================

def new_candidate_queue(ticker: str, cik: str) -> None:
    SR.atomic_write_json(ST.path(RQ.NEW_CANDIDATES), {"generated_at": "x", "candidates": [
        {"ticker": ticker, "cik": cik, "e3_priority": "P1_HIGH", "intake_order": 1}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))


def real_prepare_shape(item, deps, *, work, run_id):
    """The real ``prepare`` with its data stages stubbed: identity, panel and package are fakes, the
    judgement branch is the real one (``EX.derive_judgement`` is patched per test)."""
    ticker = item["ticker"]
    frozen = ticker in EX._judgements(deps)
    prep = T.fake_prepare(item, deps, *[], work=work, run_id=run_id) if frozen else None
    if prep is None:
        cik = T.CIK.get(ticker, "0000000001")
        prep = {"identity": IN.Identity(ticker, cik, None, "XNYS", None), "session": PUBLISHABLE, "panel": None,
                "coverage": {"sources": {"a_grouped": 300}}, "sec": SimpleNamespace(submissions_fetched_at=RUN_AT,
                                                                                    submissions_doc={"sic": "3714"}),
                "package": T.FakePackage(cik, ["0000000777-26-000001"]), "package_meta": {"package_sha256": "pkg"},
                "package_path": work / "packages" / f"{ticker}.json", "cutoff": RUN_AT, "triggers_chunked": [],
                "judgements": EX._judgements(deps), "method_judgement": {"source": "FROZEN_TABLE", "ticker": ticker}}
        derived = EX.derive_judgement(prep, deps, work=work)
        prep["judgements"] = dict(EX._judgements(deps)) | {ticker: derived["judgement"]}
        prep["method_judgement"] = {"source": "CODE_OWNED_V1", "ticker": ticker, "version": derived["report"]["version"],
                                    "state": derived["report"]["state"],
                                    "primary_preference": derived["report"]["judgement"]["primary_preference"],
                                    "secondary_preference": derived["report"]["judgement"]["secondary_preference"]}
    return prep


def test_new_candidate_not_ready_judgement_is_data_not_ready_before_d3(store, monkeypatch):
    T.seed({"FG": "WATCH"})
    new_candidate_queue("AMBIG", "0000000444")
    def not_ready(prep, deps, *, work):
        (work / "method_judgement.json").write_text(json.dumps({"state": MJ.NOT_READY}))
        raise IN.InputsNotReady(MJ.NOT_READY, "AMBIG: FINANCIAL_SECTOR:SIC_6311")

    monkeypatch.setattr(EX, "derive_judgement", not_ready)
    d = T.deps(judgements=("FG", "VRRM", "AEYE"))
    d.prepare_fn = real_prepare_shape
    out = ORC.evaluation_step(now=RUN_AT, deps=d)
    item = Q.fold()["NEW_CANDIDATE:AMBIG:1"]
    assert item["status"] == Q.METHOD_NOT_READY and item["status_reason"] == MJ.NOT_READY
    assert "FINANCIAL_SECTOR" in item["last_error"] and out["ai_calls"] == 0 and ST.latest_per_ticker().keys() == {"FG"}


def test_new_candidate_auto_ready_runs_d5_d2r_d6_admits_and_enters_paper(store, monkeypatch):
    T.seed({"FG": "WATCH"})
    HP.launch(at=T.PAPER_AT)
    new_candidate_queue("NEWCO", "0000000777")
    judged = MJ.derive("NEWCO", issuer_session(results=ALL), sic="3714", sic_description="Motor Vehicle Parts")
    monkeypatch.setattr(EX, "derive_judgement", lambda prep, deps, *, work: {"judgement": judged.judgement,
                                                                               "report": judged.report()})
    seen = {}

    def d5d6(prep, d3, d4, deps):
        seen["judgements"] = prep["judgements"]
        return T.real_d6(prep, d3, d4, deps)

    d = T.deps(gap="WIDE_POSITIVE", judgements=("FG", "VRRM", "AEYE"))
    d.prepare_fn, d.d5d6_fn = real_prepare_shape, d5d6
    out = ORC.evaluation_step(now=RUN_AT, deps=d)
    result = out["results"][0]
    assert result["status"] == Q.COMPLETE and result["ledger_written"] and result["final_decision"] == "APPROVE"
    assert seen["judgements"]["NEWCO"] is judged.judgement and "FG" in seen["judgements"]
    item = Q.fold()["NEW_CANDIDATE:NEWCO:1"]
    assert item["method_judgement"]["source"] == "CODE_OWNED_V1" and item["method_judgement"]["primary_preference"] == ["P/FCF", "P/E"]
    row = ST.latest_per_ticker()["NEWCO"]
    assert row["cohort_tag"] == "NEW_FORWARD_CANDIDATE" and row["evidence"]["method_judgement"]["source"] == "CODE_OWNED_V1"
    assert AC.D5_CONTRACT == "D5_D2R_V1" == row["d5_contract"]
    for s, bars in (("2026-10-13", (10, 10, 10, 10)), ("2026-10-14", (10, 10.4, 9.9, 10.2))):
        T.store_bars(s, {"NEWCO": bars, "FG": (10, 10, 10, 10), "SPY": (700, 700, 700, 700)})
    HP.run()
    kinds = [e["event"] for e in HP.events() if e.get("ticker") == "NEWCO"]
    assert "PENDING_ENTRY" in kinds and "BUY" in kinds                 # APPROVE -> next session open


def test_cohort_ticker_never_takes_a_code_owned_judgement(store, monkeypatch):
    T.seed({"FG": "WATCH"})
    T.refresh_queue({"FG": [{"form": "8-K", "filingDate": "2026-10-05", "accession": T.FG_8K}]})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    monkeypatch.setattr(EX, "derive_judgement", lambda *a, **k: pytest.fail("a cohort issuer uses the frozen table"))
    d = T.deps(gap="NEUTRAL")
    d.prepare_fn = real_prepare_shape
    out = ORC.evaluation_step(now=RUN_AT, deps=d)
    assert out["results"][0]["status"] == Q.COMPLETE
    assert Q.fold()["REFRESH_DUE:FG:1"]["method_judgement"] == {"source": "FROZEN_TABLE"}


# == price panel: a candidate outside the pinned cohort base ========================================

def test_price_base_lacking_a_ticker_falls_back_to_the_grouped_store(store, a_store, tmp_path):
    def pin(bars: dict) -> None:
        base = AS.root() / "price_base"
        base.mkdir(parents=True, exist_ok=True)
        body = {"schema": "h_price_base_v1", "tickers": sorted(bars), "bars": bars}
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        (base / "H_PRICE_BASE_V1.json.gz").write_bytes(gzip.compress(raw))
        (base / "H_PRICE_BASE_V1.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")

    pin({"SPY": {"2026-10-09": [500.0, 1e8]}})                       # the cohort pin lacks GOOD
    panel = IN.load_panel(["GOOD"], PUBLISHABLE, mirror=tmp_path / "no-mirror", grouped=SRC.A_GROUPED_DIR, use_d7=False)
    assert len(panel.closes("GOOD")) == 300 and panel.sources["a_grouped"] > 0 and panel.sources["price_base_lacks"] == 1
    assert IN.check_panel(panel, "GOOD", PUBLISHABLE)["sessions"] == 300
    pin({"GOOD": {"2026-10-09": [21.0, 1.0]}})                       # a pinned close that disagrees is refused
    with pytest.raises(IN.InputsNotReady, match="PRICE_SOURCE_CONFLICT"):
        IN.load_panel(["GOOD"], PUBLISHABLE, mirror=tmp_path / "no-mirror", grouped=SRC.A_GROUPED_DIR, use_d7=False)
    none = IN.load_panel(["GOOD"], PUBLISHABLE, mirror=tmp_path / "no-mirror", grouped=tmp_path / "no-grouped", use_d7=False)
    assert none.sources.get("no_mirror_no_price_base") is None and none.sources["price_base"] == 1
    (AS.root() / "price_base" / "H_PRICE_BASE_V1.json.gz").unlink()
    bare = IN.load_panel(["GOOD"], PUBLISHABLE, mirror=tmp_path / "no-mirror", grouped=tmp_path / "no-grouped", use_d7=False)
    assert bare.sources == {"no_mirror_no_price_base": 1} and bare.bars == {}
    with pytest.raises(IN.InputsNotReady, match="DECISION_SESSION_UNPRICED"):
        IN.check_panel(bare, "GOOD", PUBLISHABLE)


# == contract and isolation ========================================================================

def test_contract_v2_keeps_production_sources_and_has_no_budget():
    body = AC.contract()
    assert "budget" not in body and AC.state()["production_ai_calls"] == 0
    assert AC.intake_fetch_cap() == 700 and AC.reference_max_age_days() == 7
    sources = body["new_candidate_intake"]["production_sources"]
    assert "strategy_c/raw/tickers" in sources["active_universe"] and "strategy_c/raw/grouped" in sources["daily_prices"]
    assert body["new_candidate_intake"]["sec_on_demand"]["max_age_days"] == FC.FACTS_MAX_AGE_DAYS
    assert body["new_candidate_intake"]["sec_on_demand"]["dated_roots_kept"] == FC.KEEP_DATED_ROOTS
    assert "METHOD_JUDGEMENT_NOT_READY" in body["new_candidate_intake"]["d5_method_judgement"]


def test_sources_and_intake_import_no_a_or_e_runtime_module():
    import ast
    imported = set()
    for name in ("sources", "facts_cache", "method_judgement", "intake"):
        path = Path(AS.__file__).parent / f"{name}.py"
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
    forbidden = ("app.backtest.mover_scanner_v1", "app.strategy_e", "app.strategies.official", "app.strategies.paper_gate",
                 "app.services", "app.strategy.", "app.dev.collect_grouped_daily", "app.dev.run_e_")
    assert not [m for m in imported if m.startswith(forbidden)], imported


def test_compact_drops_non_candidate_bodies_and_keeps_ledgers(store):
    root = FC.sec_dir() / "2026-10-13"
    for cik in ("0000000001", "0000000002"):
        sub = root / "submissions" / f"CIK{cik}"
        sub.mkdir(parents=True)
        (sub / f"CIK{cik}.json.gz").write_bytes(b"x")
        (sub / f"CIK{cik}.json.gz.request.json").write_text(json.dumps({"fetched_at": "2026-10-13T05:00:00+00:00"}))
        (root / "companyfacts").mkdir(exist_ok=True)
        (root / "companyfacts" / f"CIK{cik}.json.gz").write_bytes(b"x")
        (root / "companyfacts" / f"CIK{cik}.json.gz.request.json").write_text("{}")
    before = FC.index()
    assert FC.compact(["0000000002"]) == {"bodies_dropped": 2, "bodies_kept": 2}
    assert not (root / "companyfacts" / "CIK0000000001.json.gz").exists()
    assert (root / "companyfacts" / "CIK0000000001.json.gz.request.json").exists()
    after = FC.index()                                # the ledger stays, but a ledger alone is not a copy
    assert after["0000000002"] == before["0000000002"] and after["0000000002"].usable
    assert not after["0000000001"].usable and after["0000000001"].fetched_at == before["0000000001"].fetched_at
    assert FC.state_of(after["0000000001"], date(2026, 10, 13)) == FC.EVICTED


def test_scan_status_with_an_ineligible_row_is_json_serialisable(store, a_store, monkeypatch):
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [ref_row("GOOD", "0000000777")]))
    grouped_store(a_store, {"SPY": (500.0, 1e8), "GOOD": (20.0, 1e6)})

    def ineligible(inputs, *, now, run_id):
        return [], [{"ticker": r.ticker, "cik": r.cik, "scanned_at": now.isoformat(), "run_id": run_id,
                     "eligibility": "DATA_NOT_READY", "e3_priority": None} for r in inputs.rows]

    wire_fakes(monkeypatch, fetched=[], scan_result=ineligible)
    status = INT.intake_step(now=RUN_AT, mode=INT.SCAN, sec_client_factory=lambda: None)
    assert status["scan"]["e3_counts"] == {"None": 1} and SR.read_json(ST.path(INT.INTAKE_STATUS))["written"] == 0


# == MethodJudgement screen in the intake (the open-slot blocker) ===================================

def screen_fixture(monkeypatch, verdicts: dict[str, str], *, stub_series: bool = True):
    """Real ``screen_method_judgement`` with SEC, the D5 panel and ``derive`` stubbed per ticker."""
    from app.dev import run_strategy_h_v2_d5_d2 as D52
    calls = {"fetch": [], "panel": []}

    def fake_fetch(cik, day, *, client):
        calls["fetch"].append(cik)
        root = IN.sec_root(day)
        sub = root / "submissions" / f"CIK{cik}"
        sub.mkdir(parents=True, exist_ok=True)
        gz(sub / f"CIK{cik}.json.gz", {"sic": "3714", "sicDescription": "Parts"})
        (sub / f"CIK{cik}.json.request.json").write_text("{}")
        return SimpleNamespace(cik=cik)

    monkeypatch.setattr(IN, "fetch_sec", fake_fetch)
    monkeypatch.setattr(IN, "fetch_sec_files", fake_fetch)
    monkeypatch.setattr(D52, "build_panel", lambda t, closes, until: calls["panel"].append(t) or ({}, SimpleNamespace(t=t), {}))
    monkeypatch.setattr(MJ, "derive", lambda t, row, sic=None, sic_description=None: MJ.Derived(
        t, verdicts[t], () if verdicts[t] == MJ.AUTO_READY else ("NO_COMPUTABLE_METHOD: x",), object()))
    if stub_series:
        monkeypatch.setattr(SRC, "series_for", lambda tickers, until=None: {})
    return calls


def ordered_rows(tickers):
    return [{"ticker": t, "cik": f"{n:010d}", "e3_priority": "P1_HIGH", "days_since_latest_filing": 1} for n, t in enumerate(tickers, 1)]


def test_screen_writes_only_auto_ready_and_skips_not_ready_issuers(store, monkeypatch):
    verdicts = {"A": MJ.NOT_READY, "B": MJ.AUTO_READY, "C": MJ.NOT_READY, "D": MJ.AUTO_READY, "E": MJ.AUTO_READY}
    calls = screen_fixture(monkeypatch, verdicts)
    index = {t: {"ticker": t, "facts_day": "2026-10-13"} for t in verdicts}
    out = INT.screen_method_judgement(ordered_rows("ABCDE"), index=index, now=RUN_AT, publishable=PUBLISHABLE,
                                      room=2, exclude=(), sec_client_factory=lambda: SimpleNamespace(close=lambda: None))
    assert [c["ticker"] for c in out["chosen"]] == ["B", "D"]            # A and C fail closed, E not needed
    assert calls["panel"] == ["A", "B", "C", "D"] and out["judged_this_run"] == 4
    assert index["A"]["method_judgement"] == MJ.NOT_READY and index["A"]["mj_reasons"] == ["NO_COMPUTABLE_METHOD"]
    # same facts day: verdicts are reused, nothing is rebuilt; an already queued ticker is never rewritten
    calls["panel"].clear()
    calls["fetch"].clear()
    again = INT.screen_method_judgement(ordered_rows("ABCDE"), index=index, now=RUN_AT, publishable=PUBLISHABLE,
                                        room=2, exclude={"B"}, sec_client_factory=lambda: SimpleNamespace(close=lambda: None))
    assert [c["ticker"] for c in again["chosen"]] == ["D", "E"]
    assert calls["panel"] == ["E"] and calls["fetch"] == ["0000000005"]     # only the unjudged issuer
    # new facts day for A: re-judged
    index["A"]["facts_day"] = "2026-10-20"
    verdicts["A"] = MJ.AUTO_READY
    third = INT.screen_method_judgement(ordered_rows("ABCDE"), index=index, now=RUN_AT, publishable=PUBLISHABLE,
                                        room=1, exclude=(), sec_client_factory=lambda: SimpleNamespace(close=lambda: None))
    assert [c["ticker"] for c in third["chosen"]] == ["A"]


def test_screen_bounds_new_verdicts_per_run(store, monkeypatch):
    verdicts = {t: MJ.NOT_READY for t in "ABCDEFG"}
    calls = screen_fixture(monkeypatch, verdicts)
    index = {t: {"ticker": t, "facts_day": "2026-10-13"} for t in verdicts}
    out = INT.screen_method_judgement(ordered_rows("ABCDEFG"), index=index, now=RUN_AT, publishable=PUBLISHABLE,
                                      room=10, exclude=(), limit=3, sec_client_factory=lambda: SimpleNamespace(close=lambda: None))
    assert out["chosen"] == [] and calls["panel"] == ["A", "B", "C"]
    assert "method_judgement" not in index["D"]


def test_not_ready_candidates_cannot_fill_the_open_slots(store, a_store, monkeypatch):
    """Before the screen: 7 of 10 written candidates failed closed and stayed open forever."""
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [
        ref_row(t, f"{n:010d}") for n, t in enumerate(["AA", "BB", "CC", "DD"], 1)]))
    grouped_store(a_store, {"SPY": (500.0, 1e8), **{t: (20.0, 1e6) for t in ["AA", "BB", "CC", "DD"]}})
    wire_fakes(monkeypatch, fetched=[], scan_result=scan_everything_p1)
    verdicts = {"AA": MJ.NOT_READY, "BB": MJ.NOT_READY, "CC": MJ.AUTO_READY, "DD": MJ.AUTO_READY}
    monkeypatch.setattr(INT, "screen_method_judgement", REAL_SCREEN)       # the real screen, stubbed SEC/D5
    screen_fixture(monkeypatch, verdicts, stub_series=False)
    monkeypatch.setattr(AC, "contract", lambda: {**REAL_CONTRACT(), "new_candidate_intake": {
        **REAL_CONTRACT()["new_candidate_intake"], "max_open_new_candidate_items": 2}})
    status = INT.intake_step(now=RUN_AT, mode=INT.SCAN, sec_client_factory=lambda: SimpleNamespace(close=lambda: None))
    written = [c["ticker"] for c in SR.read_json(ST.path(INT.NEW_CANDIDATES))["candidates"]]
    assert written == ["CC", "DD"] and status["scan"]["method_judgement_screen"]["counts"][MJ.NOT_READY] == 2
    T.seed({"FG": "WATCH"})
    T.sync(RQ.build(caches=T.CACHES, index=T.INDEX))
    assert {i["ticker"] for i in Q.fold().values() if i["reason"] == "NEW_CANDIDATE"} == {"CC", "DD"}



# == H's pinned history under A's short production store ============================================

def test_history_fills_a_short_a_store_and_a_wins_on_shared_sessions(store, a_store, tmp_path):
    full = grouped_store(tmp_path / "local_a", {"SPY": (500.0, 1e8), "GOOD": (20.0, 1e6)}, sessions=300)
    days = sessions_back(300)
    split = days[-44]                                                 # trader-j: 44 sessions in A's store
    import shutil
    shutil.rmtree(a_store / "grouped")
    grouped_store(a_store, {"SPY": (500.0, 1e8), "GOOD": (20.0, 1e6)}, sessions=44)
    assert INT.freshness(RUN_AT)["reasons"][-1] == "PRICE_HISTORY_INSUFFICIENT:44<252"
    built = IN.build_price_history(full, before=split)
    assert built["sessions"] == 256 and built["last"] < split.isoformat()
    assert IN.verify_price_history() == {"state": "OK", "sessions": 256, "problems": []}
    gz(SRC.h_reference_dir() / "CS_2026-10-12.json.gz", reference_doc("2026-10-12", [ref_row("GOOD", "0000000777")]))
    gate = INT.freshness(RUN_AT)
    assert gate["state"] == INT.READY and gate["checks"]["grouped"]["files"] == 300
    assert SRC.market_gate(["GOOD"])["GOOD"].sessions == 300
    panel = IN.load_panel(["GOOD"], PUBLISHABLE, mirror=tmp_path / "none", grouped=SRC.A_GROUPED_DIR, use_d7=False)
    assert len(panel.closes("GOOD")) == 300 and panel.sources["h_history"] > 0 and panel.sources["a_grouped"] > 0
    assert IN.check_panel(panel, "GOOD", PUBLISHABLE)["sessions"] == 300
    # a tampered history file is detected, and a disagreeing close with A's store is refused
    victim = sorted(IN.history_dir().glob("*.json.gz"))[0]
    body = json.loads(gzip.decompress(victim.read_bytes()))
    body["rows"] = [[t, (c + 1 if t == "GOOD" else c), v] for t, c, v in body["rows"]]
    victim.write_bytes(gzip.compress(json.dumps(body).encode()))
    assert IN.verify_price_history()["problems"] == [f"CHECKSUM:{victim.name[:10]}"]
    (IN.history_dir() / f"{split.isoformat()}.json.gz").write_bytes(gzip.compress(json.dumps(
        {"format": IN.HISTORY_FORMAT, "session": split.isoformat(), "rows": [["GOOD", 99.0, 1.0]]}).encode()))
    with pytest.raises(IN.InputsNotReady, match="PRICE_SOURCE_CONFLICT"):
        IN.load_panel(["GOOD"], PUBLISHABLE, mirror=tmp_path / "none", grouped=SRC.A_GROUPED_DIR, use_d7=False)
    assert dict(SRC.session_files())[split].parent == a_store / "grouped"     # A's file wins for the scan


def test_d5_binding_reads_h_reference_history_and_restores_the_loader(store, tmp_path):
    from app.dev import run_strategy_h_v2_d5_d2 as D52
    original = D52.load_reference_snapshot
    src = tmp_path / "a_tickers"
    gz(src / "CS_2026-04-01.json.gz", reference_doc("2026-04-01", [ref_row("GOOD", "0000000777")]))
    gz(src / "CS_2026-10-01.json.gz", reference_doc("2026-10-01", [ref_row("GOOD", "0000000777")]))
    out = IN.build_reference_history(src, before=date(2026, 10, 1))
    assert list(out["files"]) == ["CS_2026-04-01.json.gz"]
    panel = IN.Panel(bars={"SPY": {PUBLISHABLE: (1.0, 1.0)}})
    with IN.d5_binding(packages_dir=tmp_path, panel=panel, facts_roots=[tmp_path], submission_roots=[tmp_path]) as bound:
        assert str(IN.reference_history_dir()) in bound["reference_dirs"]
        snap = D52.load_reference_snapshot(date(2026, 5, 1))
        assert snap is not None
    assert D52.load_reference_snapshot is original
