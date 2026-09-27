"""E-MAX V1 provisional paper: RVOL-gated H5, session record, evidence status and the E-only book."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import inspect
import json
from zoneinfo import ZoneInfo

import pytest

from app.market.provider import MarketDataProvider
from app.market.domain import MarketSession, MinuteBar
from app.strategy_e_max_rt import availability as AV, engine as ENG, paper as PAPER
from app.strategy_e_max_rt import decision as DEC
from app.dev.run_e_rt2_dryrun import _paper_stage

ET = ZoneInfo("America/New_York")
D = date(2026, 9, 23)


def at(t: str) -> datetime:
    h, m = (int(x) for x in t.split(":"))
    return datetime(2026, 9, 23, h, m, tzinfo=ET)


# -- the frozen threshold and the RVOL gate -----------------------------------------------------

def test_threshold_is_read_from_the_frozen_rules_and_is_3() -> None:
    assert PAPER.frozen_rvol_threshold() == 3.0
    body = inspect.getsource(PAPER)
    assert "3.0" not in body                       # the number is never restated in code


def test_a_served_symbol_without_history_is_insufficient_not_a_signal() -> None:
    assert PAPER.rvol_state(AV.FEATURE_COMPLETE, None) == AV.RVOL_HISTORY_INSUFFICIENT
    assert PAPER.rvol_state(AV.FEATURE_COMPLETE, 1234.0) == AV.FEATURE_COMPLETE
    assert PAPER.rvol_state(AV.MARKET_DATA_UNAVAILABLE, None) == AV.MARKET_DATA_UNAVAILABLE
    assert PAPER.rvol_state(AV.SPARSE_NO_PREMARKET, None) == AV.SPARSE_NO_PREMARKET


def test_insufficient_history_is_h5_unknown_and_cannot_be_selected() -> None:
    assert AV.h5_status(AV.RVOL_HISTORY_INSUFFICIENT, True) == AV.H5_UNKNOWN
    assert AV.h5_status(AV.RVOL_HISTORY_INSUFFICIENT, None) == AV.H5_UNKNOWN
    assert not AV.executable(AV.RVOL_HISTORY_INSUFFICIENT)


# -- the session record --------------------------------------------------------------------------

def record(**kw):
    base = dict(states={"A": AV.FEATURE_COMPLETE, "B": AV.RVOL_HISTORY_INSUFFICIENT,
                        "PS": AV.MARKET_DATA_UNAVAILABLE},
                h5_by_symbol={"A": AV.H5_TRUE, "B": AV.H5_UNKNOWN, "PS": AV.H5_UNKNOWN},
                eligible_rows=854, candidates=["A"], selected=["A"], rvol_ready_rows=1,
                rvol_missing_rows=1, bootstrap_complete=False)
    return PAPER.session_record(D, **(base | kw))


def test_session_record_carries_every_required_count() -> None:
    body = record()
    for key in ("canonical_universe", "rvol_ready", "rvol_missing", "market_data_unavailable",
                "h5_true", "h5_false", "h5_unknown", "selected", "paper_evidence_status"):
        assert key in body, key
    assert body["canonical_universe"] == 3 and body["market_data_unavailable"] == 1
    assert body["h5_true"] == 1 and body["h5_unknown"] == 2 and body["selected"] == ["A"]
    assert body["rvol_threshold_frozen"] == 3.0 and body["breadth_denominator_eligible_rows"] == 854
    assert json.loads(json.dumps(body))            # serializable as written


def test_evidence_status_is_provisional_until_the_bootstrap_finishes_cleanly() -> None:
    assert record()["paper_evidence_status"] == PAPER.PROVISIONAL
    assert record(rvol_missing_rows=0)["paper_evidence_status"] == PAPER.PROVISIONAL
    assert record(bootstrap_complete=True)["paper_evidence_status"] == PAPER.PROVISIONAL
    assert record(rvol_missing_rows=0, bootstrap_complete=True)["paper_evidence_status"] == PAPER.OFFICIAL


def test_provisional_and_official_evidence_never_share_a_book() -> None:
    assert PAPER.PROVISIONAL != PAPER.OFFICIAL
    body = record()
    assert "never summed into the official record" in body["promotion_rule"]
    source = inspect.getsource(_paper_stage)
    assert 'out_root / PAPER_STATE_V1 / record["paper_evidence_status"]' in source


# -- the equivalence artifact ---------------------------------------------------------------------

def test_evidence_rows_hold_both_sources_and_the_frozen_pass_fail(tmp_path) -> None:
    rows = PAPER.evidence_rows(
        D, features={"PASS": {"premarket_rvol": 4.0, "premarket_dollar_volume": 4e6},
                     "FAIL": {"premarket_rvol": 2.99, "premarket_dollar_volume": 1e6},
                     "NONE": {"premarket_rvol": float("nan"), "premarket_dollar_volume": 5e5}},
        denominators={"PASS": 1e6, "FAIL": 334448.0, "NONE": None},
        staged_counts={"PASS": 25, "FAIL": 20, "NONE": 0},
        states={"PASS": AV.FEATURE_COMPLETE, "FAIL": AV.FEATURE_COMPLETE,
                "NONE": AV.RVOL_HISTORY_INSUFFICIENT})
    by = {r["symbol"]: r for r in rows}
    assert by["PASS"]["kiwoom_pass"] is True and by["FAIL"]["kiwoom_pass"] is False
    assert by["NONE"]["kiwoom_rvol"] is None and by["NONE"]["kiwoom_pass"] is None
    assert by["PASS"]["staged_sessions_used"] == 20              # capped at the frozen window
    assert all(r["massive_rvol"] is None and "massive_pass" in r for r in rows)
    assert all(r["threshold"] == 3.0 and r["source"] == "KIWOOM" for r in rows)
    out = tmp_path / "rvol_evidence.csv"
    assert PAPER.write_evidence(out, rows) == 3 and "kiwoom_denominator" in out.read_text()


# -- the paper stage ------------------------------------------------------------------------------

class FakeProvider(MarketDataProvider):
    def __init__(self, bars):
        self.bars = bars

    def get_minute_bars(self, symbols, *, start, end):
        return [b for b in self.bars if b.symbol in symbols and start <= b.timestamp <= end]

    def get_daily_bars(self, symbol, *, start, end):      # pragma: no cover - unused here
        return []


def bar(symbol, t, price, volume=10_000):
    ts = at(t)
    return MinuteBar(symbol=symbol, timestamp=ts, open=price, high=price, low=price, close=price,
                     volume=volume, session=MarketSession.REGULAR,
                     observed_at=ts + timedelta(minutes=1), available_at=ts + timedelta(minutes=1))


def decision_for(selected, *, exposure="2"):
    body = DEC.EDecision(
        strategy_id="STRATEGY_E_MAX_V1", session=D.isoformat(), decided_at=at("09:25").isoformat(),
        source="KIWOOM_NATIVE_PAPER", universe_rows=854, h5_count=len(selected), h5_rate=0.01,
        high_breadth=False, breadth_multiplier="1", global_multiplier="2", final_exposure=exposure,
        candidates=tuple(selected), r1_order=tuple(selected), selected=tuple(selected),
        reference_prices={s: 100.0 for s in selected}, features={s: {} for s in selected},
        v1_1_seal_digest="seal", v1_rules_digest="rules")
    return DEC.EDecision(**{**body.__dict__, "digest": DEC.digest_of(body)})


class Clock:
    def __init__(self, start): self.t = start
    def __call__(self): 
        self.t += timedelta(minutes=1)
        return self.t


def run_stage(tmp_path, selected, status=PAPER.PROVISIONAL, bars=None):
    from app.market.calendar import MarketCalendar
    decision = decision_for(selected)
    clock = Clock(at("09:30"))
    rec = {"paper_evidence_status": status}
    provider = FakeProvider(bars if bars is not None else
                            [bar(s, t, 100.0 if t == "09:30" else 101.0)
                             for s in selected for t in ("09:30", "09:34", "09:35")])
    return decision, _paper_stage(decision, D, rec, tmp_path, MarketCalendar("America/New_York"),
                                  clock, lambda t: datetime.combine(D, t, tzinfo=ET),
                                  lambda *a: None, "10000", provider_factory=lambda: provider,
                                  interval=0.0)


def test_paper_stage_enters_and_exits_the_frozen_way_in_simulation_only(tmp_path) -> None:
    decision, summary = run_stage(tmp_path, ["AAA", "BBB"])
    assert summary["ran"] and summary["phase"] == ENG.Phase.COMPLETE
    assert summary["entries"] == 2 and summary["fills"] == 2 and summary["exits"] == 2
    assert summary["real_orders"] == 0 and summary["live_margin_approved"] is False
    assert summary["mode"] == "SIMULATION_VIRTUAL_ONLY"
    book = json.loads((tmp_path / "paper_state_v1" / PAPER.PROVISIONAL / "STRATEGY_E_MAX_V1" / "book.json")
                      .read_text())
    assert book["live_margin_approved"] is False and D.isoformat() in book["sessions"]


def test_provisional_and_official_books_are_separate_files(tmp_path) -> None:
    run_stage(tmp_path, ["AAA"], status=PAPER.PROVISIONAL)
    run_stage(tmp_path, ["AAA"], status=PAPER.OFFICIAL)
    root = tmp_path / "paper_state_v1"
    provisional = json.loads((root / PAPER.PROVISIONAL / "STRATEGY_E_MAX_V1" / "book.json").read_text())
    official = json.loads((root / PAPER.OFFICIAL / "STRATEGY_E_MAX_V1" / "book.json").read_text())
    assert set(provisional["sessions"]) == set(official["sessions"]) == {D.isoformat()}
    assert official["initial_equity"] == "10000"        # the official book starts fresh, not carried over


def test_a_selected_symbol_without_a_0930_bar_is_not_backfilled(tmp_path) -> None:
    bars = [bar("AAA", t, 100.0) for t in ("09:30", "09:34", "09:35")]
    bars += [bar("BBB", t, 50.0) for t in ("09:34", "09:35")]        # no 09:30 bar for BBB
    decision, summary = run_stage(tmp_path, ["AAA", "BBB"], bars=bars)
    state = json.loads((tmp_path / "paper_state_v1" / PAPER.PROVISIONAL / "STRATEGY_E_MAX_V1" /
                        "sessions" / f"{D.isoformat()}.json").read_text())
    assert state["entries"]["BBB"]["status"] == "ENTRY_INVALID"
    assert state["executable"] == ["AAA"]                            # nobody takes BBB's slot
    assert summary["fills"] == 1


def test_paper_stage_touches_no_strategy_a_state() -> None:
    source = inspect.getsource(_paper_stage)
    for banned in ("usb_paper_trading", "strategy_states", "RiskEngine", "usb-backend", "submit_real"):
        assert banned not in source


class FakeChartClient:
    """Records what Kiwoom was actually asked for."""

    def __init__(self, rows=None, refuse=()):
        self.asked, self.rows, self.refuse = [], rows or {}, set(refuse)

    def minute_chart(self, symbol, exchange, start=None):
        from types import SimpleNamespace
        from app.core.exceptions import MarketDataError
        self.asked.append((symbol, exchange))
        if symbol in self.refuse:
            raise MarketDataError("MARKET_DATA_UNAVAILABLE", "rejected")
        return SimpleNamespace(rows=self.rows.get(symbol, []))


def chart_row(t: str, price: float, volume: int = 1000):
    h, m = t.split(":")
    return {"cntr_tm": f"20260923{int(h):02d}{int(m):02d}00", "bus_dt": "20260923",
            "open_pric": str(price), "high_pric": str(price), "low_pric": str(price),
            "cur_prc": str(price), "trde_qty": str(volume)}


def test_entry_asks_kiwoom_with_the_listing_code_and_exchange() -> None:
    """The common provider uppercases and defaults to one exchange; E must not inherit that."""
    client = FakeChartClient(rows={"BRKb": [chart_row("09:30", 500.0)], "CRH": [chart_row("09:30", 90.0)]})
    provider = PAPER.ListingMinuteProvider(client, codes={"BRK.B": "BRKb", "CRH": "CRH"},
                                           exchanges={"BRKb": "NY", "CRH": "NY"})
    bars = provider.get_minute_bars(["BRK.B", "CRH"], start=at("09:29"), end=at("09:31"))
    assert sorted(client.asked) == [("BRKb", "NY"), ("CRH", "NY")]      # not BRK.B, not ND
    assert {bar.symbol for bar in bars} == {"BRK.B", "CRH"}             # returned under the decision's name


def test_a_refused_symbol_leaves_the_others_trading() -> None:
    client = FakeChartClient(rows={"AAA": [chart_row("09:30", 10.0)]}, refuse={"PS"})
    provider = PAPER.ListingMinuteProvider(client, codes={}, exchanges={"AAA": "ND", "PS": "ND"})
    bars = provider.get_minute_bars(["AAA", "PS"], start=at("09:29"), end=at("09:31"))
    assert [bar.symbol for bar in bars] == ["AAA"]
    assert provider.failures == {"PS": "MARKET_DATA_UNAVAILABLE"}       # recorded, not swallowed silently


def test_a_transport_failure_is_not_treated_as_a_refusal() -> None:
    from app.core.exceptions import MarketDataError

    class Timeouts(FakeChartClient):
        def minute_chart(self, symbol, exchange, start=None):
            raise MarketDataError("PROVIDER_TIMEOUT", "timed out")

    provider = PAPER.ListingMinuteProvider(Timeouts(), codes={}, exchanges={})
    with pytest.raises(MarketDataError):
        provider.get_minute_bars(["AAA"], start=at("09:29"), end=at("09:31"))
