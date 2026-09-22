"""E-MAX-F1 forward collection tests: fake Massive client, temporary workspace, no network."""

from __future__ import annotations

from datetime import date, datetime, timezone
import gzip
import hashlib
import inspect
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.backtest.strategy_e1_forward import layout
from app.integrations.massive.client import MassiveError
from app.market.calendar import MarketCalendar
from app.strategy_e_max import m0, v1
from app.strategy_e_max_forward import collect as C, collection_rules as CR, readiness as RD, rules as F, storage as ST

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_max"
CAL = MarketCalendar("America/New_York")
ET = ZoneInfo("America/New_York")
D1, D2 = date(2026, 9, 17), date(2026, 9, 18)


def _ms(day: date, hh: int, mm: int) -> int:
    return int(datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).timestamp() * 1000)


class Accounting:
    http_requests = 0


class FakeClient:
    """Enough of MassiveAggregatesClient for the reused C / raw_fetch fetchers."""

    _base_url = "https://api.example"

    def __init__(self, capture=None, *, minute_bars=None, fail=None):
        self.capture = capture if capture is not None else []
        self.accounting = Accounting()
        self.minute_bars = minute_bars or {}
        self.fail = list(fail or [])
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        self.accounting.http_requests += 1
        if self.fail:
            raise MassiveError(self.fail.pop(0), "fake")

    def grouped_daily(self, session):
        self._maybe_fail()
        rows = [{"T": t, "o": 10.0, "h": 11.0, "l": 9.5, "c": 10.5, "v": 1e6, "t": _ms(session, 16, 0)}
                for t in ("AAA", "BBB", "SPY")]
        return {"adjusted": False, "resultsCount": len(rows), "results": rows}

    def _get(self, url, params):
        self._maybe_fail()
        return {"results": [{"ticker": "AAA", "execution_date": "2026-09-10", "split_from": 1, "split_to": 2}]}

    def _trusted_reference_url(self, url, path):
        return url

    def reference_tickers(self, as_of, *, security_type):
        self._maybe_fail()
        return ({"results": [{"ticker": "AAA", "type": "CS", "market": "stocks", "active": True,
                              "primary_exchange": "XNAS"}]},)

    def minute_aggregates(self, symbol, start, end, *, max_pages, keep_pages):
        self._maybe_fail()
        bars = self.minute_bars.get(symbol) or [
            {"t": _ms(d, 9, m), "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100}
            for d in (D1, D2) if start.date() <= d <= end.date() for m in (24, 30, 34)]
        self.capture.append(json.dumps({"ticker": symbol, "adjusted": False, "results": bars}).encode())


@pytest.fixture
def run(tmp_path):
    return C.new_run(tmp_path, lambda m: None)


# -- identity ---------------------------------------------------------------------------------------

def test_contract_identities() -> None:
    CR.load_rules()
    recorded = json.loads((DOCS / "strategy_e_max_forward_collection_rules_v1.sha256").read_text("utf-8"))
    assert recorded["canonical_sha256"] == CR.RULES_CANONICAL_SHA256
    assert recorded["file_sha256"] == hashlib.sha256(
        (DOCS / "strategy_e_max_forward_collection_rules_v1.json").read_bytes()).hexdigest()
    F.load_rules()
    assert v1.RULES_CANONICAL_SHA256.startswith("b30a3e95")


def test_m6_code_identity_unchanged() -> None:
    result = json.loads(F.M6_RESULT_PATH.read_text("utf-8"))
    assert F.m6_code_identity() == result["identity"]["code"]
    for pkg in ("strategy_e_max", "backtest/strategy_e_max"):
        names = {p.name for p in (ROOT / "backend/app" / pkg).glob("*.py")}
        assert not any("forward" in n or "collect" in n for n in names)


# -- boundary / isolation ---------------------------------------------------------------------------

def test_forward_boundary_and_closed_sessions() -> None:
    closed, open_ = C.closed_sessions(date(2026, 9, 1), date(2026, 9, 22), date(2026, 9, 22), CAL)
    assert closed == [date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21)]
    assert open_ == [date(2026, 9, 22)]


def test_development_manifest_holds_no_forward_path() -> None:
    manifest = json.loads((ROOT / "docs/backtest/strategy_e_candidate/strategy_e_d6_development_tape_v1.json").read_text())
    text = json.dumps(manifest)
    assert "market_data/forward" not in text and "rvol_context" not in text


def test_writes_outside_forward_tree_are_refused(tmp_path) -> None:
    with pytest.raises(ST.ForwardStoreError):
        ST.require_forward_tree(tmp_path, tmp_path / "market_data/raw/massive/minute/AAA/x.json.gz")
    ST.require_forward_tree(tmp_path, tmp_path / ST.MINUTE / "AAA/x.json.gz")


# -- destinations and mapping ----------------------------------------------------------------------

def test_destinations(tmp_path) -> None:
    assert ST.grouped_path(tmp_path, D1) == tmp_path / "market_data/forward/massive/grouped_daily/2026/2026-09-17.json.gz"
    assert ST.splits_asof_path(tmp_path, D1).name == "splits_asof_2026-09-17.json.gz"
    stem = ST.minute_stem(tmp_path, ST.MINUTE, "CON", D1, D2)
    assert stem == tmp_path / "market_data/forward/massive/minute/_CON/CON_2026-09-17_2026-09-18"


@pytest.mark.parametrize("symbol,storage", [("CON", "_CON"), ("PRN", "_PRN"), ("COM1", "_COM1"), ("AAPL", "AAPL"),
                                            ("CONX", "CONX"), ("BRK.B", "BRK.B")])
def test_symbol_mapping_round_trip(symbol, storage) -> None:
    assert ST.symbol_to_storage(symbol) == storage
    assert ST.storage_to_symbol(storage) == symbol
    assert RD.forward_minute_dir(symbol) == storage


def test_no_symbol_identity_mutation(run) -> None:
    capture: list[bytes] = []
    client = FakeClient(capture)
    req = C.ForwardMinuteRequest("minute", "CON", D1, D2, 2)
    assert C.collect_minute(run, client, capture, req, secret="", calendar=CAL).startswith("ok")
    side = ST.read_sidecar(req.ledger_path(run.root))
    assert side["symbol"] == "CON" and side["storage_directory"] == "_CON"
    assert all(f["file"].startswith("CON_") for f in side["files"])


# -- small files -----------------------------------------------------------------------------------

def test_grouped_splits_reference_collection_and_resume(run) -> None:
    client = FakeClient()
    assert C.collect_grouped(run, client, D1).startswith("ok")
    assert C.collect_splits(run, client, D1).startswith("ok")
    assert C.collect_reference(run, client, date(2026, 10, 1)).startswith("ok")
    calls = client.calls
    assert C.collect_grouped(run, client, D1) == "verified" and client.calls == calls      # resume: zero calls
    side = ST.read_sidecar(ST.grouped_path(run.root, D1))
    assert side["source"] == "MASSIVE" and side["mode"] == "RECONSTRUCTED" and side["evidence_class"] == "SECONDARY"
    assert side["sha256"] == ST.sha256_file(ST.grouped_path(run.root, D1))
    assert side["validation"]["symbols"] == 3


def test_corrupt_file_is_never_overwritten(run) -> None:
    client = FakeClient()
    C.collect_grouped(run, client, D1)
    path = ST.grouped_path(run.root, D1)
    path.write_bytes(gzip.compress(b"{}"))
    with pytest.raises(ST.ForwardStoreError):
        C.collect_grouped(run, client, D1)


def test_append_only_sidecar(run) -> None:
    C.collect_grouped(run, FakeClient(), D1)
    with pytest.raises(ST.ForwardStoreError):
        ST.write_sidecar(run.root, ST.grouped_path(run.root, D1), {"status": "PASS"})


def test_future_daily_row_rejected_and_quarantined(run, monkeypatch) -> None:
    client = FakeClient()
    original = client.grouped_daily

    def future(session):
        body = original(session)
        body["results"][0]["t"] = _ms(D2, 16, 0)
        return body
    client.grouped_daily = future
    outcome = C.collect_grouped(run, client, D1)
    assert outcome.startswith("quarantined")
    assert not ST.grouped_path(run.root, D1).exists()
    assert any((run.root / ST.QUARANTINE).rglob("2026-09-17.json.gz"))


def test_splits_after_d_rejected(tmp_path) -> None:
    path = tmp_path / "s.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"start": "2026-09-01", "end": "2026-09-17", "results": [
        {"ticker": "X", "execution_date": "2026-09-18", "split_from": 1, "split_to": 2}]}).encode()))
    report = ST.validate_splits(path, D1, date(2026, 9, 1))
    assert report["status"] == ST.FAIL and "no publication-time" in report["pit_semantics"]


# -- minute ------------------------------------------------------------------------------------------

def test_minute_collection_resume_and_checksum(run) -> None:
    capture: list[bytes] = []
    client = FakeClient(capture)
    req = C.ForwardMinuteRequest("minute", "AAA", D1, D2, 2)
    assert C.collect_minute(run, client, capture, req, secret="", calendar=CAL).startswith("ok")
    calls = client.calls
    assert C.collect_minute(run, client, capture, req, secret="", calendar=CAL) == "verified" and client.calls == calls
    side = ST.read_sidecar(req.ledger_path(run.root))
    for f in side["files"]:
        assert ST.sha256_file(req.ledger_path(run.root).with_name(f["file"])) == f["sha256"]
    assert C.covered_sessions(run.root, ST.MINUTE, "AAA", CAL) == {D1, D2}
    assert C.minute_requests(run.root, ["AAA", "BBB"], [D1, D2], CAL) == [C.ForwardMinuteRequest("minute", "BBB", D1, D2, 2)]


def test_duplicate_minute_rejected(run) -> None:
    capture: list[bytes] = []
    bar = {"t": _ms(D1, 9, 30), "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100}
    client = FakeClient(capture, minute_bars={"AAA": [bar, dict(bar)]})
    req = C.ForwardMinuteRequest("minute", "AAA", D1, D1, 1)
    outcome = C.collect_minute(run, client, capture, req, secret="", calendar=CAL)
    assert outcome.startswith("quarantined") and "duplicate_or_unsorted" in outcome
    assert not req.ledger_path(run.root).exists()


@pytest.mark.parametrize("bar,flag", [
    ({"t": _ms(D2, 9, 30), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1}, "out_of_session"),
    ({"t": _ms(D1, 21, 0), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1}, "out_of_window"),
    ({"t": _ms(D1, 9, 30), "o": float("nan"), "h": 1.0, "l": 1.0, "c": 1.0, "v": 1}, "non_finite_ohlc"),
    ({"t": _ms(D1, 9, 30), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": -5}, "negative_volume"),
])
def test_minute_validation_flags(tmp_path, bar, flag) -> None:
    page = tmp_path / "p.json.gz"
    page.write_bytes(gzip.compress(json.dumps({"ticker": "AAA", "adjusted": False, "results": [bar]}).encode()))
    report = ST.validate_minute([page], "AAA", D1, D1, [D1])
    assert report["status"] == ST.FAIL and report[flag] == 1
    wrong = ST.validate_minute([page], "BBB", D1, D1, [D1])
    assert wrong["symbol_mismatch"] == 1


# -- retries / rate limit -----------------------------------------------------------------------------

def test_rate_limit_retry_and_fatal(run) -> None:
    sleeps = []
    # RATE_LIMITED first meets the reused fetchers' own 120 s cooldown; the collector's backoff is
    # exercised with the other transient codes so the test does not sleep.
    client = FakeClient(fail=["PROVIDER_TIMEOUT", "NETWORK_ERROR"])
    out = C.with_retries(run, "grouped", lambda: C.collect_grouped(run, client, D1), sleeper=sleeps.append)
    assert out.startswith("ok") and run.retries == 2 and sleeps == [60, 120]
    with pytest.raises(MassiveError):
        C.with_retries(run, "x", lambda: (_ for _ in ()).throw(MassiveError("NOT_AUTHORIZED", "k")))
    assert C.DEFAULT_SPACING_SECONDS >= 60 / 5


def test_manifest_immutable(run) -> None:
    C.collect_grouped(run, FakeClient(), D1)
    path = C.write_manifest(run, sessions={"closed": ["2026-09-17"]}, requested={}, status="COMPLETE")
    body = json.loads(path.read_text())
    assert body["source"] == "MASSIVE" and body["mode"] == "RECONSTRUCTED" and body["files_verified"] == 1
    with pytest.raises(ST.ForwardStoreError):
        C.write_manifest(run, sessions={}, requested={}, status="COMPLETE")


# -- readiness ------------------------------------------------------------------------------------

def _inventory(universe=("AAA", "BBB")):
    inv = RD.Inventory()
    day = D1
    for _ in range(25):
        day = CAL.previous_trading_day(day)
        inv.daily_sessions.add(day)
    inv.reference_dates = {date(2026, 7, 1)}
    inv.splits_asof = {D1}
    inv.minute_ranges = {s: [(date(2026, 5, 18), D1)] for s in (*universe, "SPY")}
    inv.eligible_universe[D1] = tuple(universe)
    return inv


def test_rvol_readiness_and_con_mapping() -> None:
    inv = _inventory(("AAA", "CON"))
    del inv.minute_ranges["CON"]
    row = RD.assess(D1, inv, today_et=date(2026, 9, 22), calendar=CAL)
    assert row["state"] == "NOT_READY" and row["missing_symbols"]["rvol_history"] == ["CON"]
    inv.minute_ranges["CON"] = [(date(2026, 7, 6), date(2026, 9, 16)), (D1, D1)]      # rvol_context + forward
    assert RD.assess(D1, inv, today_et=date(2026, 9, 22), calendar=CAL)["state"] == "READY"


def test_gap_in_forward_history_is_not_ready() -> None:
    inv = _inventory()
    d3 = date(2026, 9, 21)
    for d in (D1, D2):
        inv.daily_sessions.add(d)
    inv.splits_asof.add(d3)
    inv.eligible_universe[d3] = ("AAA", "BBB")
    inv.minute_ranges = {s: [(date(2026, 5, 18), date(2026, 9, 16)), (d3, d3)] for s in ("AAA", "BBB", "SPY")}
    row = RD.assess(d3, inv, today_et=date(2026, 9, 22), calendar=CAL)
    assert "MISSING_RVOL_HISTORY" in row["reasons"]                     # 09-17 / 09-18 pages missing


def test_session_not_closed_and_fail_closed() -> None:
    row = RD.assess(date(2026, 9, 22), RD.Inventory(), today_et=date(2026, 9, 22), calendar=CAL)
    assert "SESSION_NOT_CLOSED" in row["reasons"] and row["status"] == "FEATURE_CONTEXT_INCOMPLETE"
    assert set(RD.REASONS) == set(CR.load_rules()["readiness"]["reasons"])


def test_ready_needs_verified_forward_files(tmp_path) -> None:
    path = ST.grouped_path(tmp_path, D1)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    assert not ST.verified(path)
    assert RD._verified_dates(tmp_path / layout.GROUPED_DAILY, "*.json.gz") == set()


# -- source separation / no performance path -----------------------------------------------------------

def test_source_labels_and_no_kiwoom_or_performance() -> None:
    rules = CR.load_rules()
    assert rules["source"]["mode"] == "RECONSTRUCTED" and rules["source"]["evidence_class"] == "SECONDARY"
    for module in (C, ST, RD):
        text = inspect.getsource(module).lower()
        assert "kiwoom" not in text or module is C and "kiwoom.rate_limit" not in text
    for module in (C, ST, RD):
        text = inspect.getsource(module)
        for banned in ("COST_10BP", "capacity.execute", "shadow_outcome", "sharpe", "profit_factor"):
            assert banned not in text
