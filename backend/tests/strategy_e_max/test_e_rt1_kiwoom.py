"""E-RT1: measured Kiwoom capabilities, the full-universe feasibility arithmetic, and the FE rolling state."""

from __future__ import annotations

from datetime import date, datetime
import inspect
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.backtest.strategy_e1_premarket.premarket import DECISION_LAST_BAR, premarket_block
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.strategy_e_max import breadth, v1
from app.strategy_e_max_forward import rules as FR
from app.strategy_e_max_rt import decision as DEC, kiwoom_capability as KC, premarket_state as PS, runtime as RT

ROOT = Path(__file__).resolve().parents[3]
D = date(2026, 9, 23)


# -- measured capabilities -----------------------------------------------------------------------

def test_manifest_records_the_measured_limits() -> None:
    caps = KC.load()
    assert caps["rest"]["rate_limit"]["limit_per_api_id_per_second"] == 5
    assert caps["websocket"]["subscription_limit"]["per_session"] == 200
    assert caps["websocket"]["sessions_per_app_key"]["reliable_concurrent_subscribing_sessions"] == 1
    assert set(caps["rest"]["rankings"].values()) >= {1000}
    assert caps["orders"]["real_order_sent"] is False


def test_every_mode_is_infeasible_for_the_canonical_universe() -> None:
    verdicts = KC.evaluate(2553)
    assert not any(v.feasible for v in verdicts.values())
    assert "511 s > 300 s" in verdicts["D_REST_ROLLING_MINUTE"].reasons[0]
    assert "2553 symbols > 200" in verdicts["FULL_WEBSOCKET"].reasons[0]
    assert len(KC.blockers(2553)) == 5


def test_arithmetic_would_pass_if_the_limits_allowed() -> None:
    caps = json.loads(json.dumps(KC.load()))
    caps["websocket"]["max_realtime_symbols_per_app_key"] = 3000
    assert KC.evaluate(2553, caps)["FULL_WEBSOCKET"].feasible and KC.blockers(2553, caps) == []
    small = KC.evaluate(1000)                                    # 1000 / 5 = 200 s <= 300 s
    assert small["D_REST_ROLLING_MINUTE"].feasible


def test_no_new_threshold_and_canonical_breadth_denominator() -> None:
    src = inspect.getsource(KC) + inspect.getsource(PS)
    for banned in ("0.02", "rvol >= 4", "gap >= "):
        assert banned not in src
    # B2 is always evaluated on the canonical universe rows, never on a prefiltered count
    assert breadth.multiplier(70, 2553) == 1 and breadth.multiplier(70, 2553 // 4) != breadth.multiplier(70, 2553)


def test_runtime_gate_fails_closed_with_the_measured_blockers() -> None:
    cap = RT.KiwoomMeasuredCapacity(2553)
    reasons = cap.blockers()
    assert any("FULL_WEBSOCKET" in r for r in reasons) and any("RVOL denominator" in r for r in reasons)
    assert RT.KiwoomMeasuredCapacity(None).blockers() == ["canonical D-1 universe size unknown in this runtime"]
    gate = DEC.RealtimeSourceGate(lambda s: cap, source="KIWOOM_NATIVE")
    from app.strategy_e_v1_1 import context
    with pytest.raises(context.FeatureContextIncomplete):
        gate.decide(D, datetime(2026, 9, 23, 9, 25, tzinfo=ZoneInfo("America/New_York")))


# -- FE rolling state -----------------------------------------------------------------------------

def _book():
    book = PS.PremarketBook(D)
    book.subscribe("X", PS.PREMARKET_START)
    return book


def test_cumulative_volume_duplicates_and_stale_events() -> None:
    book = _book()
    assert book.event("X", "0400", 10.0, 100) == "OK"
    assert book.event("X", "0400", 10.0, 100) == "DUPLICATE"
    assert book.event("X", "0401", 10.2, 250) == "OK"
    assert book.event("X", "0400", 10.1, 180) == "STALE"          # an older snapshot arriving late
    f = book.features("X", close_previous=10.0)
    assert f["pm_volume"] == 250 and f["duplicates"] == 1 and f["stale"] == 1
    assert f["pm_dollar_volume"] == pytest.approx(100 * 10.0 + 150 * 10.2)


def test_cutoff_excludes_0925_and_matches_the_frozen_block() -> None:
    book = _book()
    bars = [(4 * 60, 10.0), (6 * 60, 9.5), (9 * 60, 10.1), (9 * 60 + 10, 10.6), (9 * 60 + 24, 10.4)]
    cum = 0
    for minute, price in bars:
        cum += 100
        book.event("X", f"{minute // 60:02d}{minute % 60:02d}", price, cum)
    assert book.event("X", "0925", 99.0, cum + 5000) == "OUTSIDE_WINDOW"
    f = book.features("X", close_previous=10.0)
    arr = np.array([[m, p, p, p, p, 100, float("nan")] for m, p in bars], dtype=float)
    ref = premarket_block(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5], arr[:, 6], DECISION_LAST_BAR)
    assert f["pm_last_price"] == ref["pm_last_price"] and f["pm_high"] == ref["pm_high"] and f["pm_low"] == ref["pm_low"]
    assert f["pm_volume"] == ref["pm_volume"]
    assert f["return_0900_0925"] == pytest.approx(ref["return_0900_0925"])
    assert f["premarket_gap"] == pytest.approx(0.04) and f["position_in_premarket_range"] == pytest.approx(0.9 / 1.1)


def test_reconnect_gap_and_late_subscription_fail_closed() -> None:
    book = _book()
    book.event("X", "0400", 10.0, 100)
    book.disconnected(7 * 60 + 5, 7 * 60 + 9)
    f = book.features("X", 10.0)
    assert f["status"] == PS.INCOMPLETE and "feed gap 07:05-07:09" in f["reasons"][0]
    late = PS.PremarketBook(D)
    late.subscribe("Y", 8 * 60)
    late.event("Y", "0801", 5.0, 10)
    assert late.features("Y", 5.0)["status"] == PS.INCOMPLETE
    after = PS.PremarketBook(D)
    after.subscribe("Z", PS.PREMARKET_START)
    after.disconnected(9 * 60 + 40, 9 * 60 + 42)                       # after the cutoff: irrelevant
    after.event("Z", "0500", 5.0, 10)
    assert after.features("Z", 5.0)["status"] == "OK"


def test_0900_window_needs_two_minutes() -> None:
    book = _book()
    book.event("X", "0500", 10.0, 10)
    book.event("X", "0905", 10.2, 20)
    assert math.isnan(book.features("X", 10.0)["return_0900_0925"])
    book.event("X", "0910", 10.4, 30)
    assert book.features("X", 10.0)["return_0900_0925"] == pytest.approx(10.4 / 10.2 - 1)


# -- execution / broker route ---------------------------------------------------------------------

def test_kiwoom_client_cannot_reach_order_endpoints() -> None:
    endpoints = {path for _, path in KiwoomMarketDataClient.ALLOWED_ENDPOINTS}
    assert endpoints <= {"/api/us/stkinfo", "/api/us/mrkcond", "/api/us/rkinfo", "/api/us/chart"}
    probe = (ROOT / "backend/app/dev/run_e_rt1_kiwoom_probe.py").read_text()
    assert '"00"' not in probe.split("MARKET_TYPES")[1].split("\n")[0] and 'MARKET_TYPES = ("FE",)' in probe


def test_frozen_identity_unchanged() -> None:
    v1.load_rules()
    assert FR.provenance_closure(FR.load_rules())["checks"]["pass"]
