"""The panel API through the HTTP surface a browser actually hits.

Includes the operational cases rather than only the happy path: no journal root, a stale journal, a
symbol the panel does not support, a symbol switch, and the replay route - which exists so that
"verified near resistance, near support, on a breakout and inside a range" is a navigation to real
minutes rather than a wait for the market to oblige.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.liquidity_map import api as LMAPI
from app.crypto.market_context_v1 import api as API
from app.crypto.market_context_v1 import assemble as ASM
from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import derivatives as D
from app.crypto.market_context_v1 import structure as S
from app.crypto.market_context_v1 import venue as VN

from tests.crypto.liquidity_map_fixtures import Handwriter, default_bands

MINUTE = 60_000


def synthetic_rows(count, start_ms):
    rows = []
    price = 86_000.0
    for index in range(count):
        price += ((index * 29) % 13 - 6) * 2.0
        rows.append({"timestamp_ms": start_ms + index * MINUTE,
                     "open": f"{price:.2f}", "high": f"{price + 15:.2f}",
                     "low": f"{price - 14:.2f}", "close": f"{price + 2:.2f}",
                     "volume": "9.0", "turnover": "780000.0", "trades": 90,
                     "taker_buy_base": "4.0", "taker_buy_quote": "350000.0"})
    return rows


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """A fresh panel, a journal root nobody else writes to, and no venue reached.

    The klines fetch and the derivatives read are both replaced: a test that depended on the real
    venue would fail for reasons that have nothing to do with the panel.
    """
    API.state = API.PanelState()
    LMAPI.state = LMAPI.PreviewState()
    monkeypatch.delenv(LMAPI.ROOT_ENV, raising=False)
    monkeypatch.setenv("MC_V1_JOURNAL_ROOT", str(tmp_path / "tape"))
    monkeypatch.setenv("MC_V1_ANCHOR_DAYS", "3")

    anchor = S.default_anchor_ms(int(time.time() * 1000), 3)

    def klines(symbol, start_ms, end_ms, *, opener=None):
        count = max(0, (end_ms - start_ms) // MINUTE)
        return synthetic_rows(count, start_ms) if count else []

    monkeypatch.setattr(VN, "klines", klines)
    # Per-symbol figures. With one shared mark price the leak test cannot distinguish "ETH shows a
    # BTC number" from "both stubs happen to say the same thing".
    marks = {"BTCUSDT": "86000.5", "ETHUSDT": "3111.77", "SOLUSDT": "144.22"}
    monkeypatch.setattr(D, "read", lambda symbol, now_ms, opener=None: D.DerivativesRead(
        symbol=symbol, read_at_ms=now_ms,
        premium={"markPrice": marks[symbol], "indexPrice": marks[symbol],
                 "lastFundingRate": "0.00006", "interestRate": "0.0001",
                 "nextFundingTime": now_ms + 3_600_000, "time": now_ms},
        open_interest={"openInterest": "98500.0", "time": now_ms},
        hist=[{"sumOpenInterest": f"{100 + index}.0",
               "sumOpenInterestValue": f"{(100 + index) * 86000}.0",
               "timestamp": now_ms - (60 - index) * 300_000} for index in range(60)]))
    yield anchor


@pytest.fixture
def client():
    with TestClient(API.app) as session:
        yield session


def live_journal(root, *, now_ms=None, walls=True):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    writer = Handwriter(root)
    writer.session_start(started_ms=now - 60_000)
    if walls:
        writer.wall(side="ASK", price="86100", event="OPENED", status="ACTIVE",
                    receive_ms=now - 30_000, first_seen_ms=now - 90_000)
        writer.wall(side="BID", price="85900", event="OPENED", status="ACTIVE",
                    receive_ms=now - 30_000, first_seen_ms=now - 90_000)
    writer.stats(active=2 if walls else 0, receive_ms=now - 20_000)
    writer.derived(receive_ms=now, mid="86017.37", bands=default_bands())
    return now


# --- the surface ------------------------------------------------------------------------------------

def test_health_answers_without_any_root_configured(client):
    body = client.get("/api/market-context/health").json()
    assert body["mode"] == "READ_ONLY_CONTEXT_PANEL"
    assert body["contract"]["contract_version"] == "mc-display.v1"
    assert body["contract"]["agrees"] is True
    assert body["layer_order"] == list(C.LAYERS)
    assert body["supported_symbols"] == list(C.SUPPORTED_SYMBOLS)


def test_the_symbol_support_matrix_is_published_so_the_screen_does_not_decide_per_layer(client):
    matrix = client.get("/api/market-context/health").json()["symbol_support"]
    assert matrix["BTCUSDT"]["PRICE"]["supported"] is True
    for symbol in ("ETHUSDT", "SOLUSDT"):
        assert matrix[symbol]["DERIVATIVES"]["supported"] is True
        for layer in ("PRICE", "LIQUIDITY", "FLOW", "AUXILIARY"):
            assert matrix[symbol][layer]["supported"] is False
            assert matrix[symbol][layer]["reason"] is not None


def test_an_unsupported_symbol_is_a_four_hundred_and_not_a_btc_payload(client):
    response = client.get("/api/market-context/panel?symbol=DOGEUSDT")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SYMBOL_NOT_SUPPORTED"


@pytest.mark.parametrize("path", ["/api/market-context/panel", "/api/market-context/health",
                                  "/api/market-context/structure/states"])
def test_no_route_accepts_a_post(client, path):
    assert client.post(path).status_code == 405


# --- the panel payload -------------------------------------------------------------------------------

def test_a_poll_answers_with_the_five_layers_in_priority_order(client, monkeypatch, tmp_path):
    monkeypatch.setenv(LMAPI.ROOT_ENV, str(tmp_path / "journal"))
    # One poll first, so the structure layer's series is already built. Without it the journal
    # would be written, then the *first* poll would spend its time folding a three-day bar series,
    # and by the time the liquidity layer is read the sample is older than the 3 s freshness bound
    # - a STALE reading that is correct about the clock and has nothing to do with this assertion.
    # It only showed up in a full-suite run, where everything is slower.
    client.get("/api/market-context/panel")
    live_journal(tmp_path / "journal")
    body = client.get("/api/market-context/panel").json()
    assert body["layer_order"] == ["PRICE", "LIQUIDITY", "FLOW", "DERIVATIVES", "AUXILIARY"]
    assert list(body["layers"]) == body["layer_order"]
    assert body["layer_states"]["PRICE"] == C.LIVE
    assert body["layer_states"]["LIQUIDITY"] == C.LIVE
    assert body["layer_states"]["DERIVATIVES"] == C.LIVE


def test_the_payload_states_that_there_is_no_roll_up(client):
    body = client.get("/api/market-context/panel").json()
    assert body["rollup"]["exists"] is False
    assert "A_NO_CONTEXT_EDGE" in body["rollup"]["measurement"]
    assert "NEUTRAL" in body["rollup"]["note"]
    # And there is no key anywhere that looks like one.
    for key in ("overall", "overall_state", "verdict", "combined", "agreement", "confluence"):
        assert key not in body, key


def test_a_stale_journal_does_not_lend_its_freshness_to_anything(client, monkeypatch, tmp_path):
    monkeypatch.setenv(LMAPI.ROOT_ENV, str(tmp_path / "journal"))
    live_journal(tmp_path / "journal", now_ms=int(time.time() * 1000) - 10 * 60_000)
    body = client.get("/api/market-context/panel").json()
    assert body["layer_states"]["LIQUIDITY"] == C.STALE
    assert body["layer_states"]["FLOW"] == C.STALE
    # The layers that do not come from the journal are unaffected, which is the point of five
    # words rather than one.
    assert body["layer_states"]["PRICE"] == C.LIVE
    assert body["layer_states"]["DERIVATIVES"] == C.LIVE


def test_no_journal_root_is_a_screen_state_and_not_an_error(client):
    body = client.get("/api/market-context/panel").json()
    assert body["layer_states"]["LIQUIDITY"] == C.UNKNOWN
    assert "MS_V0_ROOT_NOT_SET" in body["layers"]["LIQUIDITY"]["reasons"]
    assert body["layer_states"]["PRICE"] == C.LIVE


def test_the_missing_value_marker_and_every_note_travel_with_the_payload(client):
    body = client.get("/api/market-context/panel").json()
    assert body["missing_value"] == "-"
    assert set(body["notes"]) == {key for key, _ in C.NOTES}


# --- symbol switch -----------------------------------------------------------------------------------

def test_switching_symbol_switches_every_layer_and_leaks_no_btc_figure(client, monkeypatch,
                                                                      tmp_path):
    monkeypatch.setenv(LMAPI.ROOT_ENV, str(tmp_path / "journal"))
    live_journal(tmp_path / "journal")
    btc = client.get("/api/market-context/panel?symbol=BTCUSDT").json()
    assert btc["layers"]["PRICE"]["row"] is not None
    btc_close = btc["layers"]["PRICE"]["row"]["close"]
    btc_mid = btc["layers"]["LIQUIDITY"]["book"]["mid"]

    for symbol in ("ETHUSDT", "SOLUSDT"):
        other = client.get(f"/api/market-context/panel?symbol={symbol}").json()
        assert other["symbol"] == symbol
        for layer in ("PRICE", "LIQUIDITY", "FLOW", "AUXILIARY"):
            assert other["layer_states"][layer] == C.UNAVAILABLE, layer
        assert other["layer_states"]["DERIVATIVES"] == C.LIVE
        assert other["layers"]["AUXILIARY"]["rendered"] is False
        text = json.dumps(other)
        assert str(btc_close) not in text
        assert str(btc_mid) not in text


def test_switching_back_to_btc_still_answers_about_btc(client, monkeypatch, tmp_path):
    monkeypatch.setenv(LMAPI.ROOT_ENV, str(tmp_path / "journal"))
    live_journal(tmp_path / "journal")
    client.get("/api/market-context/panel?symbol=BTCUSDT")
    client.get("/api/market-context/panel?symbol=ETHUSDT")
    back = client.get("/api/market-context/panel?symbol=BTCUSDT").json()
    assert back["symbol"] == "BTCUSDT"
    assert back["layers"]["PRICE"]["row"] is not None
    assert back["layers"]["LIQUIDITY"]["sides"] is not None


def test_the_derivatives_read_is_per_symbol_and_not_shared(client):
    for symbol in C.SUPPORTED_SYMBOLS:
        body = client.get(f"/api/market-context/panel?symbol={symbol}").json()
        assert body["layers"]["DERIVATIVES"]["symbol"] == symbol
    panel = API.state.get()
    assert sorted(panel.derivatives) == sorted(C.SUPPORTED_SYMBOLS)


# --- the forward journal ------------------------------------------------------------------------------

def test_a_poll_writes_one_tape_line_and_a_repeat_poll_does_not(client, monkeypatch, tmp_path):
    monkeypatch.setenv(LMAPI.ROOT_ENV, str(tmp_path / "journal"))
    live_journal(tmp_path / "journal")
    first = client.get("/api/market-context/panel").json()
    assert first["journal"]["enabled"] is True
    assert first["journal"]["wrote_this_poll"] is True
    second = client.get("/api/market-context/panel").json()
    assert second["journal"]["records_written"] >= 1
    assert second["journal"]["linked_to_orders"] is False


def test_no_http_call_can_cause_a_write_when_the_journal_is_off(monkeypatch, tmp_path):
    """Deliberately does not take the `client` fixture.

    That fixture starts the app with the journal enabled, and its lifespan timer keeps writing -
    so a test that unset the variable afterwards would be reading a directory another client is
    still appending to, and would fail for a reason that has nothing to do with the claim.
    """
    monkeypatch.delenv("MC_V1_JOURNAL_ROOT", raising=False)
    API.state = API.PanelState()
    with TestClient(API.app) as session:
        body = session.get("/api/market-context/panel").json()
    assert body["journal"]["enabled"] is False
    assert body["journal"]["wrote_this_poll"] is False
    assert body["journal"]["records_written"] == 0
    assert not (tmp_path / "tape").exists()


# --- the structure state index and the replay route -----------------------------------------------------

def test_the_state_index_names_minutes_for_each_case_the_preview_has_to_show(client):
    body = client.get("/api/market-context/structure/states").json()
    assert body["available"] is True
    assert body["published_minutes"] > 0
    assert set(body["cases"]) == {"NEAR_RESISTANCE", "NEAR_SUPPORT", "BREAKOUT_FRESH", "IN_RANGE",
                                  "RETEST_OPEN", "FAILED_BREAK", "NO_LEVEL_ABOVE",
                                  "NO_LEVEL_BELOW"}
    for case in body["cases"].values():
        assert case["minutes"] >= 0
        if case["minutes"]:
            assert case["first"]["info_ts"] <= case["last"]["info_ts"]


def test_the_replay_route_is_labelled_a_replay_and_carries_only_the_structure_layer(client):
    states = client.get("/api/market-context/structure/states").json()
    populated = [case for case in states["cases"].values() if case["minutes"]]
    assert populated, states
    info_ts = populated[0]["first"]["info_ts"]
    body = client.get(f"/api/market-context/structure/replay?info_ts={info_ts}").json()
    assert body["mode"] == "READ_ONLY_STRUCTURE_REPLAY"
    assert body["replay"]["is_replay"] is True
    assert body["replay"]["layers_replayed"] == ["PRICE"]
    assert set(body["replay"]["layers_not_replayed"]) == {"LIQUIDITY", "FLOW", "DERIVATIVES",
                                                          "AUXILIARY"}
    assert list(body["layers"]) == ["PRICE"]
    assert body["layers"]["PRICE"]["row"]["bar_close_ms"] == info_ts


def test_a_replayed_minute_outside_the_published_window_is_a_four_oh_four(client):
    client.get("/api/market-context/structure/states")
    assert client.get("/api/market-context/structure/replay?info_ts=1").status_code == 404


def test_the_replay_route_refuses_a_symbol_with_no_structure_layer(client):
    response = client.get("/api/market-context/structure/replay?info_ts=1&symbol=ETHUSDT")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REPLAY_IS_BTC_ONLY"


def test_extending_the_series_does_not_move_a_published_minute(client):
    body = client.get("/api/market-context/structure/prefix-check?cut=30").json()
    assert body["compared_minutes"] > 0
    assert body["identical"] is True, body["differences"]


def test_the_prefix_check_refuses_a_cut_outside_its_range(client):
    for cut in (0, 5_000):
        assert client.get(f"/api/market-context/structure/prefix-check?cut={cut}") \
            .status_code == 400


# --- resilience ----------------------------------------------------------------------------------------

def test_a_failed_klines_fetch_keeps_the_last_run_on_screen_with_its_reason(client, monkeypatch):
    client.get("/api/market-context/panel")
    panel = API.state.get()
    assert panel.runs.get(C.BTC) is not None

    def refuse(*args, **kwargs):
        raise VN.VenueError("FETCH_FAILED:/fapi/v1/klines:URLError")

    monkeypatch.setattr(VN, "klines", refuse)
    panel.series[C.BTC].rows = panel.series[C.BTC].rows[:-1]      # force an extend
    body = client.get("/api/market-context/panel").json()
    assert body["layers"]["PRICE"]["row"] is not None
    assert any("FETCH_FAILED" in reason for reason in body["layers"]["PRICE"]["reasons"])


def test_the_anchor_is_fixed_for_the_life_of_the_process(client):
    first = client.get("/api/market-context/panel").json()["panel"]["anchor_ms"]
    second = client.get("/api/market-context/panel").json()["panel"]["anchor_ms"]
    assert first == second == API.state.get().anchor_ms
    assert first % C.DAY_MS == 0
