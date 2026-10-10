"""H-V2-D7: the forward shadow contract, the append-only store, outcome maturation and isolation.

Every test runs against a temporary store (``STRATEGY_H_FORWARD_DIR``), so none of them reads or
writes the real launch snapshot, and none of them touches Strategy A's paper database or Strategy E's
runtime files - which is itself one of the things under test.
"""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import time

import pytest

from app.strategies import performance as PERF
from app.strategies import registry as REG
from app.strategies.h_forward import cohort as CO
from app.strategies.h_forward import contract as C
from app.strategies.h_forward import outcomes as OUT
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import store as ST
from app.strategies.h_forward import views as VW

A, E, H = REG.STRATEGY_A, REG.STRATEGY_E_MAX_V1, REG.STRATEGY_H_V2

DECISION_SESSION = "2026-09-16"
BASELINE = "2026-10-02"
#: Sessions 1..5 after BASELINE, by the market calendar (asserted in its own test).
AFTER = ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(ST.ROOT_ENV, str(tmp_path / "d7"))
    return tmp_path / "d7"


def snapshot_row(ticker: str, decision: str, *, tp1: float | None = 12.0, tp2: float | None = 18.0,
                 bear: float | None = 6.0, bear_refusal: str | None = None,
                 confidence: str = "MEDIUM", baseline: str = BASELINE,
                 launch_price: float | None = 10.0) -> dict:
    return {
        "record": "LAUNCH", "strategy_id": H, "ticker": ticker, "cohort_tag": C.INITIAL_COHORT,
        "cik": "0000000001", "security_id": f"BBG{ticker}", "d5_contract": C.required_d5_contract(),
        "d6_contract": "h_v2_d6_integrated_decision_v1",
        "decision_session": DECISION_SESSION, "decision_close": 10.0,
        "decision": decision, "eligibility": "DECISION_ELIGIBLE",
        "thesis_version": f"T1:{ticker}",
        "d3": {"provenance": "VALID", "checksum": "d3"},
        "d4": {"provenance": "EVALUATED", "expectation_gap": "NEUTRAL", "gap_confidence": "LOW",
               "checksum": "d4"},
        "d4_expectation_gap": "NEUTRAL", "d4_gap_confidence": "LOW",
        "valuation": {"status": "VALUED", "primary_method": "EV/EBIT", "secondary_method": "EV/Sales",
                      "contract_window": "FULL_2Y", "confidence": confidence,
                      "tp1": tp1, "tp2": tp2, "bear": bear, "bear_refusal": bear_refusal,
                      "upside_to_tp1": 0.2, "upside_to_tp2": 0.8, "downside_to_bear": -0.4,
                      "range_complete": bear is not None},
        "d6": {"decision": decision, "contract_version": "h_v2_d6_integrated_decision_v1"},
        "approve_blockers": ["expectation_gap_permits_approve"], "reject_fired": [],
        "watch_matched": ["thesis_needs_confirmation_from_the_next_print"],
        "key_binding_clause": "APPROVE_BLOCKED:expectation_gap_permits_approve",
        "checksums": {"d3_output": "d3", "d4_output": "d4", "d5_row": "d5", "d6_decision": "d6"},
        "launch_timestamp": "2026-10-04T08:00:00+00:00",
        "launch_baseline_session": baseline, "launch_price": launch_price,
        "position": None, "position_reason": CO.position_reason(decision),
    }


def ledger_row(ticker: str, decision: str, *, previous: str | None = None,
               cause: str = "LAUNCH", when: str = "2026-10-04T08:00:00+00:00",
               thesis: str | None = None) -> dict:
    return {"strategy_id": H, "ticker": ticker, "thesis_version": thesis or f"T1:{ticker}",
            "decision_time": when, "record": "LAUNCH_STATE", "decision": decision,
            "previous_decision": previous, "cause": cause, "effective_session": BASELINE}


def write_prices(sessions, bars_by_session) -> None:
    for session in sessions:
        PR.write_session(session, bars_by_session[session], fetched_at="2026-10-04T00:00:00+00:00")


def flat(close: float, high: float | None = None, low: float | None = None) -> dict:
    return {"close": close, "high": high if high is not None else close,
            "low": low if low is not None else close, "volume": 1000.0}


# -- the frozen contract ---------------------------------------------------------------------------

def test_the_contract_is_frozen_by_checksum(monkeypatch, tmp_path) -> None:
    body = json.loads(C.CONTRACT.read_text(encoding="utf-8"))
    body["outcomes"]["horizons_sessions"] = [1, 5, 21, 63, 126]
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(body), encoding="utf-8")
    C.contract.cache_clear()
    monkeypatch.setattr(C, "CONTRACT", tampered)
    with pytest.raises(C.ForwardContractError):
        C.contract()
    C.contract.cache_clear()


def test_d5_d2r_is_required_and_the_superseded_rule_is_refused() -> None:
    assert C.required_d5_contract() == "D5_D2R_V1"
    C.require_d5_contract("D5_D2R_V1")                       # the repaired window rule launches
    with pytest.raises(C.ForwardContractError):
        C.require_d5_contract("D5_D2_V1")                    # the rule VRRM disproved does not


def test_no_decision_state_may_create_a_position_and_only_approve_is_a_candidate() -> None:
    assert not any(C.creates_position(state) for state in C.DECISION_STATES)
    assert C.creates_entry_candidate(C.APPROVE)
    assert not C.creates_entry_candidate(C.WATCH)
    assert not C.creates_entry_candidate(C.REJECT)
    # Sizing is undefined on purpose: A sizes from a stop and E from a same-day exit, and D6
    # produces neither, so an APPROVE is a candidate rather than a position.
    assert C.sizing_contract() == "NOT_DEFINED" and not C.sizing_is_defined()


def test_horizons_and_benchmark_come_from_the_contract() -> None:
    assert C.horizons() == (1, 5, 21, 63)
    assert C.benchmark() == "SPY"


# -- registry and isolation ------------------------------------------------------------------------

def test_a_e_and_h_are_all_enabled_and_h_is_a_forward_shadow() -> None:
    assert [m.strategy_id for m in REG.enabled()] == [A, E, H]
    meta = REG.get(H)
    assert meta.mode == REG.MODE_FORWARD_SHADOW and meta.lifecycle == REG.LIFECYCLE_FORWARD_SHADOW
    assert meta.research_lifecycle == REG.RESEARCH_PASSED_TO_PAPER
    assert (meta.short_name, meta.display_name) == ("H", "Strategy H")
    # A's and E's rows are untouched by H's arrival.
    assert REG.get(A).mode == REG.MODE_SIMULATION_PAPER
    assert REG.get(E).strategy_id == "STRATEGY_E_MAX_V1" and REG.get(E).mode == REG.MODE_SIMULATION_PAPER


def test_h_identity_includes_the_strategy_so_one_ticker_can_sit_in_several_books() -> None:
    assert ST.LEDGER_IDENTITY[0] == "strategy_id"
    assert "ticker" in ST.LEDGER_IDENTITY and "thesis_version" in ST.LEDGER_IDENTITY


def test_the_same_ticker_in_a_e_and_h_stays_in_its_own_book(store) -> None:
    ST.append_snapshots([snapshot_row("XYZ", C.WATCH)])
    ST.append_ledger([ledger_row("XYZ", C.WATCH)])
    # H's record for XYZ exists and carries H's id; it is not a trade and creates no position.
    assert [r["strategy_id"] for r in ST.launch_rows()] == [H]
    assert VW.positions() == [] and VW.trades() == []
    # H's store holds nothing that belongs to A or E.
    for path in ST.iter_files():
        text = path.read_text(encoding="utf-8")
        assert A not in text and "STRATEGY_E_MAX_V1" not in text


# -- append-only -----------------------------------------------------------------------------------

def test_the_launch_snapshot_cannot_be_written_twice_for_one_issuer(store) -> None:
    ST.append_snapshots([snapshot_row("TG", C.WATCH)])
    with pytest.raises(ST.AppendOnlyViolation):
        ST.append_snapshots([snapshot_row("TG", C.REJECT)])
    assert len(ST.launch_rows()) == 1 and ST.launch_rows()[0]["decision"] == C.WATCH


def test_a_changed_decision_appends_and_the_old_thesis_stays(store) -> None:
    ST.append_ledger([ledger_row("TG", C.WATCH)])
    ST.append_ledger([ledger_row("TG", C.APPROVE, previous=C.WATCH, cause="NEW_10Q",
                                 when="2026-11-02T21:00:00+00:00", thesis="T2:TG")])
    history = ST.history("TG")
    assert [r["decision"] for r in history] == [C.WATCH, C.APPROVE]
    assert history[0]["thesis_version"] == "T1:TG"            # the old thesis is still there
    assert ST.latest_per_ticker()["TG"]["decision"] == C.APPROVE


def test_a_stored_price_session_is_never_rewritten_with_other_numbers(store) -> None:
    PR.write_session(BASELINE, {"TG": flat(7.0)}, fetched_at="t")
    assert PR.write_session(BASELINE, {"TG": flat(7.0)}, fetched_at="t2") == 0   # idempotent
    with pytest.raises(ST.AppendOnlyViolation):
        PR.write_session(BASELINE, {"TG": flat(9.0)}, fetched_at="t3")


# -- no position from a WATCH or a REJECT ----------------------------------------------------------

@pytest.mark.parametrize("decision", [C.WATCH, C.REJECT])
def test_watch_and_reject_create_no_position(store, decision) -> None:
    ST.append_snapshots([snapshot_row("TG", decision)])
    ST.append_ledger([ledger_row("TG", decision)])
    PR.write_session(BASELINE, {"TG": flat(10.0), "SPY": flat(500.0)}, fetched_at="t")
    row = CO.rows()[0]
    assert row["decision"] == decision
    assert row["position"] is None and row["open_positions"] == 0
    assert "포지션을 만들지 않는다" in row["position_reason"]
    assert VW.account()["open_positions"] == 0 and VW.positions() == []


def test_an_approve_is_an_entry_candidate_and_is_fail_closed_without_a_sizing_contract(store) -> None:
    ST.append_snapshots([snapshot_row("TG", C.APPROVE)])
    ST.append_ledger([ledger_row("TG", C.APPROVE)])
    PR.write_session(BASELINE, {"TG": flat(10.0), "SPY": flat(500.0)}, fetched_at="t")
    row = CO.rows()[0]
    assert row["decision"] == C.APPROVE
    assert row["position"] is None                            # a candidate, not a position
    assert row["position_reason"] == C.SIZING_CONTRACT_REQUIRED
    assert VW.positions() == []


# -- VRRM's refused Bear leg -----------------------------------------------------------------------

def test_a_refused_bear_is_na_with_its_reason_and_never_zero(store) -> None:
    ST.append_snapshots([snapshot_row("VRRM", C.WATCH, bear=None,
                                      bear_refusal=C.NEGATIVE_IMPLIED_EQUITY)])
    PR.write_session(BASELINE, {"VRRM": flat(2.81), "SPY": flat(500.0)}, fetched_at="t")
    row = CO.rows()[0]
    assert row["bear"] is None                                 # not 0, not 0.72
    assert row["bear_na_reason"] == C.NEGATIVE_IMPLIED_EQUITY
    assert row["bear_distance"] is None
    assert row["range_complete"] is False
    levels = CO.levels_of(ST.launch_rows()[0])
    assert levels.bear is None and levels.bear_unavailable_reason == C.NEGATIVE_IMPLIED_EQUITY


def test_a_breach_of_an_absent_bear_is_unknown_not_false(store) -> None:
    body = OUT.outcome(ticker="VRRM", baseline=BASELINE, horizon=1,
                       levels=OUT.Levels(tp1=4.4, tp2=5.7, bear=None,
                                         bear_unavailable_reason=C.NEGATIVE_IMPLIED_EQUITY),
                       security={BASELINE: PR.Bar(BASELINE, 2.81, 2.9, 2.7, 1.0),
                                 AFTER[0]: PR.Bar(AFTER[0], 2.5, 2.6, 2.4, 1.0)},
                       benchmark={BASELINE: PR.Bar(BASELINE, 500.0, 501.0, 499.0, 1.0),
                                  AFTER[0]: PR.Bar(AFTER[0], 505.0, 506.0, 504.0, 1.0)})
    assert body["state"] == OUT.MATURED
    assert body["bear_breach"] is None and body["bear_na_reason"] == C.NEGATIVE_IMPLIED_EQUITY


# -- exact maturation ------------------------------------------------------------------------------

def test_the_horizon_window_is_the_market_calendar_not_calendar_days() -> None:
    assert OUT.horizon_sessions(BASELINE, 5) == list(AFTER)     # skips the weekend
    assert len(OUT.horizon_sessions(BASELINE, 21)) == 21
    assert OUT.horizon_sessions(BASELINE, 21)[-1] == "2026-11-02"
    assert OUT.horizon_sessions(BASELINE, 63)[-1] == "2027-01-04"


def test_an_unmatured_horizon_is_pending_and_borrows_no_price(store) -> None:
    security = {BASELINE: PR.Bar(BASELINE, 10.0, 10.0, 10.0, 1.0)}
    benchmark = {BASELINE: PR.Bar(BASELINE, 500.0, 500.0, 500.0, 1.0)}
    for horizon in C.horizons():
        body = OUT.outcome(ticker="TG", baseline=BASELINE, horizon=horizon,
                           levels=OUT.Levels(12.0, 18.0, 6.0),
                           security=security, benchmark=benchmark)
        assert body["state"] == OUT.PENDING
        assert "security_return" not in body                   # nothing is computed from nothing
        assert body["maturity_session"] == OUT.horizon_sessions(BASELINE, horizon)[-1]


def test_exact_21d_and_63d_mature_only_on_their_own_session() -> None:
    sessions = OUT.horizon_sessions(BASELINE, 63)
    security = {BASELINE: PR.Bar(BASELINE, 10.0, 10.0, 10.0, 1.0)}
    benchmark = dict(security)
    for index, session in enumerate(sessions, start=1):
        security[session] = PR.Bar(session, 10.0 + index, 10.0 + index, 10.0 + index, 1.0)
        benchmark[session] = PR.Bar(session, 500.0, 500.0, 500.0, 1.0)
        for horizon in (21, 63):
            body = OUT.outcome(ticker="TG", baseline=BASELINE, horizon=horizon,
                               levels=OUT.Levels(None, None, None),
                               security=security, benchmark=benchmark)
            assert body["state"] == (OUT.MATURED if index >= horizon else OUT.PENDING)
            if body["state"] == OUT.MATURED:
                # the maturity price is that session's close, not the newest one
                assert body["maturity_price"] == 10.0 + horizon


def test_a_gap_inside_the_window_is_incomplete_and_the_last_price_is_not_substituted() -> None:
    window = OUT.horizon_sessions(BASELINE, 5)
    security = {BASELINE: PR.Bar(BASELINE, 10.0, 10.0, 10.0, 1.0)}
    benchmark = {BASELINE: PR.Bar(BASELINE, 500.0, 500.0, 500.0, 1.0)}
    for session in window:
        security[session] = PR.Bar(session, 11.0, 11.0, 11.0, 1.0)
        benchmark[session] = PR.Bar(session, 505.0, 505.0, 505.0, 1.0)
    del security[window[2]]                                     # one session never collected
    body = OUT.outcome(ticker="TG", baseline=BASELINE, horizon=5,
                       levels=OUT.Levels(None, None, None),
                       security=security, benchmark=benchmark)
    assert body["state"] == OUT.INCOMPLETE and body["missing_sessions"] == [window[2]]
    assert "security_return" not in body


def test_spy_is_the_benchmark_on_the_same_sessions_and_excess_is_the_difference() -> None:
    window = OUT.horizon_sessions(BASELINE, 1)
    security = {BASELINE: PR.Bar(BASELINE, 10.0, 10.0, 10.0, 1.0),
                window[0]: PR.Bar(window[0], 11.0, 11.5, 10.5, 1.0)}
    benchmark = {BASELINE: PR.Bar(BASELINE, 500.0, 500.0, 500.0, 1.0),
                 window[0]: PR.Bar(window[0], 510.0, 511.0, 509.0, 1.0)}
    body = OUT.outcome(ticker="TG", baseline=BASELINE, horizon=1, levels=OUT.Levels(11.4, 20.0, 9.0),
                       security=security, benchmark=benchmark)
    assert body["security_return"] == pytest.approx(0.10)
    assert body["benchmark_return"] == pytest.approx(0.02)
    assert body["excess_return"] == pytest.approx(0.08)
    assert body["mfe"] == pytest.approx(0.15) and body["mae"] == pytest.approx(0.05)
    assert body["tp1_hit"] is True                              # the high touched 11.5 > 11.4
    assert body["tp2_hit"] is False and body["bear_breach"] is False


def test_a_missing_baseline_is_named_rather_than_assumed() -> None:
    body = OUT.outcome(ticker="TG", baseline=BASELINE, horizon=1, levels=OUT.Levels(None, None, None),
                       security={}, benchmark={})
    assert body["state"] == OUT.NO_BASELINE


# -- pre-launch drift is not a forward result ------------------------------------------------------

def test_the_interval_between_the_decision_and_the_launch_is_not_forward_evidence(store) -> None:
    ST.append_snapshots([snapshot_row("VRRM", C.WATCH, launch_price=2.81)])
    PR.write_session(BASELINE, {"VRRM": flat(2.81), "SPY": flat(500.0)}, fetched_at="t")
    row = CO.rows()[0]
    drift = row["pre_launch_drift"]
    assert drift["is_forward_evidence"] is False
    assert drift["decision_session"] == DECISION_SESSION and drift["baseline_session"] == BASELINE
    assert drift["return_since_decision"] == pytest.approx(2.81 / 10.0 - 1.0)
    # and it is reported separately from every horizon, all of which are still pending
    assert {body["state"] for body in row["forward"].values()} == {OUT.PENDING}


# -- the read layer answers the same six questions -------------------------------------------------

def test_h_answers_the_six_questions_with_no_invented_zero(store) -> None:
    ST.append_snapshots([snapshot_row("TG", C.WATCH)])
    ST.append_ledger([ledger_row("TG", C.WATCH)])
    PR.write_session(BASELINE, {"TG": flat(10.0), "SPY": flat(500.0)}, fetched_at="t")
    account = VW.account()
    assert account["initial_equity"] is None and account["current_equity"] is None
    assert account["total_pnl"] is None and account["today_pnl"] is None
    assert account["na"]["current_equity"] == VW.NO_CAPITAL_BOOK
    assert VW.equity()["points"] == [] and VW.equity()["baseline"] is None
    assert VW.status()["runtime_status"] == "FORWARD_SHADOW_RUNNING"
    assert VW.card()["forward"]["decision_counts"][C.WATCH] == 1


def test_h_metrics_use_the_shared_calculator_and_report_na_with_reasons(store) -> None:
    ST.append_snapshots([snapshot_row("TG", C.WATCH)])
    PR.write_session(BASELINE, {"TG": flat(10.0)}, fetched_at="t")
    metrics = PERF.strategy_metrics(VW.book())
    assert metrics["strategy_id"] == H and metrics["trades"] == 0
    for key in ("net_pnl", "return", "win_rate", "pf", "expectancy"):
        assert metrics[key] is None                            # never 0
        assert metrics["na"][key] == "NO_CLOSED_TRADE"
    assert metrics["operating_sessions"] == 1                  # the baseline session was observed


def test_zero_positions_and_zero_approve_are_states_not_errors(store) -> None:
    rows = [snapshot_row(t, C.WATCH) for t in ("A1", "A2", "A3", "A4", "A5", "A6")]
    rows += [snapshot_row(t, C.REJECT) for t in ("R1", "R2")]
    ST.append_snapshots(rows)
    PR.write_session(BASELINE, {r["ticker"]: flat(10.0) for r in rows} | {"SPY": flat(500.0)},
                     fetched_at="t")
    counts = CO.counts()
    assert counts == {C.APPROVE: 0, C.WATCH: 6, C.REJECT: 2}
    evaluation = CO.evaluation()
    assert evaluation["state"] == C.RUNNING and evaluation["verdict"] == C.INCONCLUSIVE
    assert evaluation["under_ae_paper_gate"] is False
    assert evaluation["reasons"] == ["SAMPLE_NOT_REACHED"]
    assert CO.maturity()["21D"]["pending"] == 8 and CO.maturity()["21D"]["matured"] == 0


def test_the_cohort_is_ordered_approve_then_watch_then_reject(store) -> None:
    ST.append_snapshots([snapshot_row("ZZ", C.REJECT), snapshot_row("MM", C.WATCH),
                         snapshot_row("AA", C.APPROVE)])
    PR.write_session(BASELINE, {"ZZ": flat(1.0), "MM": flat(1.0), "AA": flat(1.0)}, fetched_at="t")
    assert [r["ticker"] for r in CO.rows()] == ["AA", "MM", "ZZ"]


# -- state transitions -----------------------------------------------------------------------------

def test_every_contract_transition_is_representable_and_a_price_is_never_the_cause() -> None:
    allowed = C.allowed_transitions()
    assert (C.WATCH, C.APPROVE) in allowed and (C.REJECT, C.APPROVE) in allowed
    assert (C.APPROVE, C.REJECT) in allowed and (C.WATCH, C.WATCH) in allowed
    assert C.contract()["transitions"]["price_alone_is_not_a_cause"] is True
    assert C.contract()["transitions"]["cause_required"].startswith("D3->D4->D5->D6")


def test_a_transition_shows_its_previous_state_in_the_cohort(store) -> None:
    ST.append_snapshots([snapshot_row("TG", C.WATCH)])
    ST.append_ledger([ledger_row("TG", C.WATCH)])
    ST.append_ledger([ledger_row("TG", C.REJECT, previous=C.WATCH, cause="NEW_8K",
                                 when="2026-10-20T21:00:00+00:00", thesis="T2:TG")])
    row = CO.rows()[0]
    assert row["decision"] == C.REJECT and row["previous_decision"] == C.WATCH
    assert row["decision_at_launch"] == C.WATCH                # the snapshot is not rewritten
    assert row["decision_changes"] == 1


# -- material-event refresh ------------------------------------------------------------------------

def test_the_refresh_trigger_list_is_the_contract_not_a_schedule() -> None:
    events = C.material_events()
    assert {"10-Q", "10-K", "8-K", "EARNINGS_RELEASE"} <= set(events)
    assert C.contract()["refresh"]["daily_full_rerun"] is False
    assert "change_detection" in C.contract()["refresh"]["change_detection"]


# -- the price store is H's own ---------------------------------------------------------------------

def test_h_prices_live_in_hs_own_store_and_not_in_the_frozen_mirror(store) -> None:
    PR.write_session(BASELINE, {"TG": flat(7.0)}, fetched_at="t")
    written = list(Path(store).rglob("*.json"))
    assert written and all(str(store) in str(p) for p in written)
    assert "strategy_b_e0" not in str(store)                   # USB-HIST-V1 is frozen
    assert PR.stored_sessions() == [BASELINE] and PR.latest_session() == BASELINE


def test_expected_sessions_are_exclusive_of_the_baseline() -> None:
    sessions = PR.expected_sessions(BASELINE, "2026-10-09")
    assert sessions == list(AFTER) and BASELINE not in sessions
    assert PR.expected_sessions(BASELINE, BASELINE) == []


def test_a_gap_in_the_store_is_reported_rather_than_filled(store) -> None:
    PR.write_session(AFTER[0], {"TG": flat(1.0)}, fetched_at="t")
    assert PR.gaps(BASELINE, AFTER[2]) == [AFTER[1], AFTER[2]]


# -- the refresh queue says only what its caches license ---------------------------------------------
#
# The scan reads H's own dated SEC cache and runs only when every cohort CIK is present and fresh, so
# these tests hand it a ready cache directly. Readiness, queue preservation and the AEYE regression
# live in test_d7_refresh_independence.py.

def _ready(caches: dict, *, root: str = "test") -> dict:
    from app.strategies.h_forward import sec_refresh as SR
    return {"state": SR.DATA_READY, "reasons": [], "root": root,
            "as_of": min(c.fetched_on for c in caches.values()), "caches": caches}


def _cache(rows: list, *, days_old: int):
    from datetime import datetime, timedelta, timezone
    from app.strategies.h_forward import sec_refresh as SR
    fetched = (datetime.now(timezone.utc) - timedelta(days=days_old)).date().isoformat()
    return SR.Cache(rows=rows, path=f"/cache/{days_old}", fetched_on=fetched)


def test_a_stale_cache_cannot_claim_there_is_no_new_evidence(store) -> None:
    """A cache dated before today licenses "nothing new as of the cache", not "nothing new"."""
    from app.dev import run_h_v2_d7 as D7

    ST.append_snapshots([snapshot_row("TG", C.WATCH), snapshot_row("ZZ", C.WATCH)])
    caches = {
        # nothing after the decision session, and the cache is two days old
        "TG": _cache([{"form": "10-Q", "filingDate": "2026-08-01", "accessionNumber": "a"}], days_old=2),
        # a filing that landed after the thesis was settled
        "ZZ": _cache([{"form": "8-K", "filingDate": "2026-09-18", "accessionNumber": "b"}], days_old=1),
    }
    body = D7.refresh_scan(ready=_ready(caches))
    by_ticker = {r["ticker"]: r for r in body["issuers"]}
    assert body["fetch_performed"] is False                  # the scan never fetches
    assert by_ticker["TG"]["state"] == "NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE"
    assert by_ticker["TG"]["evidence_known_through"] is not None
    assert by_ticker["ZZ"]["state"] == "REFRESH_DUE"
    assert by_ticker["ZZ"]["new_material_filings"] == [
        {"form": "8-K", "filingDate": "2026-09-18", "accession": "b"}]
    assert body["refresh_due"] == ["ZZ"] and body["unverified_since_cache"] == ["TG"]
    assert "캐시 시점까지" in body["claim_limit"]


def test_a_cache_older_than_the_thesis_says_it_could_not_have_seen_anything(store) -> None:
    from app.dev import run_h_v2_d7 as D7

    ST.append_snapshots([snapshot_row("TG", C.WATCH)])
    # the decision session is 2026-09-16; a cache from well before it proves nothing either way
    caches = {"TG": _cache([{"form": "10-K", "filingDate": "2025-02-01", "accessionNumber": "a"}],
                           days_old=400)}
    body = D7.refresh_scan(ready=_ready(caches))
    assert body["issuers"][0]["state"] == "CACHE_NOT_NEWER_THAN_THESIS"
    assert body["refresh_due"] == []


def test_an_issuer_with_no_cache_is_not_scanned_and_nothing_is_written(store) -> None:
    """Formerly written into the queue as NO_SUBMISSIONS_CACHE - the 2026-10-10 incident."""
    from app.dev import run_h_v2_d7 as D7
    from app.strategies.h_forward import sec_refresh as SR

    ST.append_snapshots([snapshot_row("TG", C.WATCH)])
    body = D7.refresh_scan()
    assert body["scan_performed"] is False and body["refresh_data"] == SR.DATA_NOT_READY
    assert not ST.path(D7.REFRESH_QUEUE).exists()
