"""Pre-trade preview and the fast live view. Every preview figure is held to what the real
engine then does on the same quote, and no preview may leave a trace on the live engine."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.paper import pnl_breakdown, sizing, trade_preview
from app.crypto.paper.analytics import build_trades
from app.crypto.paper.engine import PaperEngine
from app.crypto.terminal import api as terminal_api
from app.crypto.terminal import server as terminal_server
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD, make_config, quote, started_engine
from tests.crypto.test_paper_terminal_api import snapshot_message, ticker_snapshot

D = Decimal


def _deep(tiers, **kw) -> PaperEngine:
    return started_engine(make_config(**kw), tiers, first_quote=quote(
        1_000, bids=[["100000.0", "50"]], asks=[["100000.1", "50"]], mark="100000.05"))


def _state(engine: PaperEngine) -> tuple:
    a = engine.account
    return (len(engine.ledger.events), a.position.signed_qty, a.cumulative_fees,
            a.cumulative_funding_paid, a.realized_pnl, a.wallet_balance, engine.quote)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_round_trip_equals_a_real_open_then_close(tiers, side) -> None:
    engine = _deep(tiers)
    p = trade_preview.round_trip(engine, side, D("0.050"))
    assert p["feasible"]
    opened = engine.submit_order(ts_ms=1_000, side=side, qty=D("0.050"), intent="OPEN", request_id="o")
    closed = engine.submit_order(ts_ms=1_000, side=side, qty=D("0.050"), intent="CLOSE", request_id="c")
    trade = build_trades(engine.ledger.events)[-1]
    assert p["entry_fill_price"] == opened["fill_price"] and p["entry_fee"] == opened["fee"]
    assert p["exit_fill_price"] == closed["fill_price"] and p["exit_fee"] == closed["fee"]
    assert p["immediate_round_trip_net"] == trade.net_pnl
    assert p["round_trip_cost"] == -p["immediate_round_trip_net"]


def test_preview_leaves_the_live_engine_untouched(tiers) -> None:
    engine = _deep(tiers)
    before = _state(engine)
    trade_preview.round_trip(engine, "LONG", D("0.050"))
    trade_preview.round_trip(engine, "SHORT", D("100"))  # rejected path runs max_entry too
    assert _state(engine) == before


def test_slippage_split_is_measured_against_mark(tiers) -> None:
    engine = started_engine(make_config(), tiers, first_quote=quote(
        1_000, mark="100000.00", bids=[["99999.0", "0.02"], ["99990.0", "5"]],
        asks=[["100001.0", "0.02"], ["100010.0", "5"]]))
    p = trade_preview.round_trip(engine, "LONG", D("0.050"))
    entry = (D("100001.0") * D("0.02") + D("100010.0") * D("0.03")) / D("0.050")
    exit_ = (D("99999.0") * D("0.02") + D("99990.0") * D("0.03")) / D("0.050")
    assert p["entry_fill_price"] == entry and p["exit_fill_price"] == exit_
    assert p["entry_slippage"] == (entry - D("100000.00")) * D("0.050")
    assert p["exit_slippage"] == (D("100000.00") - exit_) * D("0.050")
    assert p["entry_slippage"] > 0 and p["exit_slippage"] > 0


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_breakeven_move_really_nets_zero(tiers, side) -> None:
    """Shift the whole book by the reported move, close for real: the net must be zero."""
    engine = _deep(tiers)
    p = trade_preview.round_trip(engine, side, D("0.050"))
    move = p["breakeven_move"] if side == "LONG" else -p["breakeven_move"]
    assert p["breakeven_move"] > 0 and p["breakeven_move_pct"] > 0
    engine.submit_order(ts_ms=1_000, side=side, qty=D("0.050"), intent="OPEN", request_id="o")
    engine.apply_market(quote(2_000, bids=[[str(D("100000.0") + move), "50"]],
                              asks=[[str(D("100000.1") + move), "50"]],
                              mark=str(D("100000.05") + move)))
    engine.submit_order(ts_ms=2_000, side=side, qty=D("0.050"), intent="CLOSE", request_id="c")
    net = build_trades(engine.ledger.events)[-1].net_pnl
    assert abs(net) < D("0.000000001")
    assert p["breakeven_mark_price"] == D("100000.05") + move


def test_safe_max_is_previewable_and_one_step_more_is_refused_with_the_safe_size(tiers) -> None:
    engine = _deep(tiers)
    ceiling = sizing.max_entry(engine, "LONG")
    assert trade_preview.round_trip(engine, "LONG", ceiling.qty)["feasible"]
    beyond = trade_preview.round_trip(engine, "LONG", ceiling.qty + engine.config.instrument.qty_step)
    assert not beyond["feasible"]
    assert beyond["safe_max_qty"] == ceiling.qty


def test_thin_exit_side_is_refused_at_the_exit_stage(tiers) -> None:
    engine = started_engine(make_config(capital_krw="100000000"), tiers, first_quote=quote(
        1_000, bids=[["100000.0", "0.1"]], asks=[["100000.1", "10"]]))
    p = trade_preview.round_trip(engine, "LONG", D("1.000"))
    assert not p["feasible"]
    assert (p["reject_stage"], p["reject_code"]) == ("EXIT", "NO_LIQUIDITY")
    assert "entry_fee" not in p and "round_trip_cost" not in p  # nothing invented


def test_preview_is_for_a_flat_account(engine: PaperEngine) -> None:
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    p = trade_preview.round_trip(engine, "LONG", D("0.010"))
    assert not p["feasible"] and p["reject_code"] == "POSITION_OPEN"


def test_fifty_x_round_trip_cost_is_large_against_margin(tiers) -> None:
    engine = _deep(tiers, leverage="50", capital_krw="10000000")
    p = trade_preview.round_trip(engine, "LONG", D("2.000"))
    assert p["feasible"]
    # Two taker fees on the notional are a visible share of a 2% margin.
    assert p["round_trip_cost"] / p["required_margin"] > D("0.05")
    assert p["entry_fee"] + p["exit_fee"] <= p["round_trip_cost"]


def test_krw_fields_for_the_round_trip(tiers) -> None:
    engine = _deep(tiers)
    p = trade_preview.round_trip(engine, "LONG", D("0.050"))
    krw = pnl_breakdown.to_krw(p, D("1344"))
    assert krw["round_trip_cost"] == p["round_trip_cost"] * D("1344")
    assert krw["immediate_round_trip_net"] == p["immediate_round_trip_net"] * D("1344")
    assert "breakeven_move" not in krw and "entry_fill_price" not in krw


# ------------------------------------------------------------------ live view


def test_live_view_follows_a_newer_quote_without_recording_it(tiers) -> None:
    engine = _deep(tiers)
    engine.submit_order(ts_ms=1_000, side="LONG", qty=D("0.050"), intent="OPEN", request_id="o")
    before = _state(engine)
    newer = quote(1_500, bids=[["100100.0", "50"]], asks=[["100100.1", "50"]], mark="100100.05")
    live = trade_preview.live_position(engine, newer)
    assert _state(engine) == before
    assert live["mark_price"] == D("100100.05") and live["quote_ts_ms"] == 1_500
    assert live["unrealized_pnl"] == (D("100100.05") - D("100000.1")) * D("0.050")
    # Confirmed costs still come from the live ledger even though the clone's own is empty.
    assert live["entry_fee"] == engine.account.cumulative_fees
    assert live["unrealized_pct_of_margin"] == live["unrealized_pnl"] / engine.account.used_margin
    # And it is what the engine itself reports once it observes that quote.
    engine.apply_market(newer)
    assert live["expected_position_net_if_closed"] == \
        pnl_breakdown.open_position_preview(engine)["expected_position_net_if_closed"]


def test_live_view_includes_funding_the_newer_quote_would_settle(tiers) -> None:
    engine = started_engine(make_config(), tiers, first_quote=quote(
        28_799_000, funding_rate="0.0001", next_funding_time_ms=28_800_000))
    engine.submit_order(ts_ms=28_799_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    live = trade_preview.live_position(engine, quote(28_800_500, funding_rate="0.0001",
                                                     next_funding_time_ms=57_600_000))
    assert live["funding"] > 0 and engine.account.cumulative_funding_paid == 0


def test_an_older_quote_is_not_applied(tiers) -> None:
    engine = _deep(tiers)
    clone = trade_preview.advanced(engine, quote(500, mark="1.0"))
    assert clone.quote == engine.quote


# ------------------------------------------------------------------ routes


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    config_path = tmp_path / "run_config.json"
    config_path.write_text(json.dumps({
        "run_id": "preview-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-23T00:00:00Z", "fee_version": "test-v1",
        "fee_taker_rate": "0.0006", "fee_maker_rate": "0.0002", "fee_source": "test",
        "fee_effective_date": "2026-01-01", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk)}))
    monkeypatch.setenv(terminal_api.CONFIG_ENV, str(config_path))
    monkeypatch.setenv("CRYPTO_PAPER_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "seed_klines", lambda self, **kw: None)
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(terminal_api.Runtime, "start", lambda self: None)
    with TestClient(terminal_server.app) as test_client:
        terminal_api.runtime.feed.handle(snapshot_message(), 1_001)
        terminal_api.runtime.feed.handle(ticker_snapshot(), 1_001)
        terminal_api.runtime.session.start(1_000)
        terminal_api.runtime.session.observe(terminal_api.runtime.feed.quote(), force=True)
        yield test_client


def test_order_preview_route_prices_both_sides_and_records_nothing(client: TestClient) -> None:
    session = terminal_api.runtime.session
    tape_before, ledger_before = len(session.tape), len(session.engine.ledger.events)
    body = client.get("/api/crypto/order-preview?long_qty=0.010&short_qty=0.010").json()
    assert set(body["sides"]) == {"LONG", "SHORT"}
    for side in ("LONG", "SHORT"):
        row = body["sides"][side]
        assert row["feasible"], row
        assert row["krw"]["round_trip_cost"] is not None and row["quote_ts_ms"] == body["quote_ts_ms"]
    assert (len(session.tape), len(session.engine.ledger.events)) == (tape_before, ledger_before)


def test_order_preview_converts_notional_like_the_order_route(client: TestClient) -> None:
    body = client.get("/api/crypto/order-preview?notional_usdt=1000").json()
    quote_ = terminal_api.runtime.feed.quote()
    step = terminal_api.runtime.session.config.instrument.qty_step
    expected = (D("1000") / quote_.best_ask / step).to_integral_value(rounding="ROUND_DOWN") * step
    assert D(body["sides"]["LONG"]["qty"]) == expected


def test_order_preview_rejects_bad_input_without_inventing_numbers(client: TestClient) -> None:
    body = client.get("/api/crypto/order-preview?long_qty=abc&short_qty=-1").json()
    assert body["sides"]["LONG"]["reject_code"] == "INVALID_QTY"
    assert body["sides"]["SHORT"]["reject_code"] == "QTY_NOT_POSITIVE"
    assert "round_trip_cost" not in body["sides"]["LONG"]


def test_live_route_is_small_and_flat_safe(client: TestClient) -> None:
    body = client.get("/api/crypto/live").json()
    assert body["position_open"] is False
    assert body["feed_connected"] is not None and body["server_time_ms"] > 0
    assert "feed_last_message_ms" in body
    session = terminal_api.runtime.session
    session.command({"command": "ORDER", "ts_ms": session.engine.quote.ts_ms, "side": "LONG",
                     "qty": "0.010", "intent": "OPEN", "request_id": "t-1", "reason": "MANUAL"})
    tape_before = len(session.tape)
    body = client.get("/api/crypto/live").json()
    assert body["position_open"] is True
    assert set(body) >= {"unrealized_pnl", "unrealized_pct_of_margin", "expected_position_net_if_closed",
                         "expected_segment_net_if_closed", "quote_ts_ms", "krw"}
    assert len(session.tape) == tape_before


def test_signed_effect_fields_add_up_to_the_immediate_net(tiers) -> None:
    engine = _deep(tiers)
    p = trade_preview.round_trip(engine, "SHORT", D("0.050"))
    assert p["entry_slippage_pnl"] == -p["entry_slippage"]
    assert p["exit_slippage_pnl"] == -p["exit_slippage"]
    assert (p["entry_slippage_pnl"] + p["exit_slippage_pnl"] - p["entry_fee"] - p["exit_fee"]) \
        == p["immediate_round_trip_net"]
