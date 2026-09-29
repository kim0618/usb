"""Current-segment PnL versus lifetime PnL.

A reset moves the capital line; it does not erase anything. So the screen has two different
true answers to "how am I doing", and the bug this pins is showing the lifetime one where the
operator expects the one since the reset. Every test here checks that the two stay separate
and that the current one agrees with the wallet the engine actually holds.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.paper.analytics import reset_anchor, summarize
from app.crypto.paper.engine import PaperEngine
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


def engine_with(tiers, **kwargs) -> PaperEngine:
    return started_engine(make_config(**kwargs), tiers, first_quote=quote(
        1_000, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]))


def win(engine: PaperEngine, ts: int, *, qty: str = "0.010") -> None:
    engine.submit_order(ts_ms=ts, side="LONG", qty=D(qty), intent="OPEN", request_id=f"o{ts}")
    engine.apply_market(quote(ts + 50, bid="100500.0", ask="100500.1"))
    engine.submit_order(ts_ms=ts + 100, side="LONG", qty=D(qty), intent="CLOSE",
                        request_id=f"c{ts}")
    engine.apply_market(quote(ts + 150, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]))


def loss(engine: PaperEngine, ts: int, *, qty: str = "0.010") -> None:
    engine.submit_order(ts_ms=ts, side="LONG", qty=D(qty), intent="OPEN", request_id=f"o{ts}")
    engine.apply_market(quote(ts + 50, bid="99500.0", ask="99500.1"))
    engine.submit_order(ts_ms=ts + 100, side="LONG", qty=D(qty), intent="CLOSE",
                        request_id=f"c{ts}")
    engine.apply_market(quote(ts + 150, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]))


def current(engine: PaperEngine) -> dict:
    return summarize(engine.ledger.events,
                     starting_capital=engine.account.starting_capital_usdt)["current_segment"]


def lifetime(engine: PaperEngine) -> dict:
    return summarize(engine.ledger.events,
                     starting_capital=engine.account.starting_capital_usdt)


# ------------------------------------------------------------------ the invariant

def test_current_segment_net_always_equals_what_the_wallet_moved(tiers) -> None:
    """The one identity the display rests on. If this breaks, the header is lying."""
    engine = engine_with(tiers)
    for step, ts in enumerate((1_100, 1_400, 1_700)):
        (win if step % 2 == 0 else loss)(engine, ts)
        moved = engine.account.wallet_balance - engine.account.capital_base_usdt
        assert D(current(engine)["current_segment_net_pnl"]) == moved

    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    assert D(current(engine)["current_segment_net_pnl"]) == 0

    for ts in (6_000, 6_400):
        win(engine, ts)
        moved = engine.account.wallet_balance - engine.account.capital_base_usdt
        assert D(current(engine)["current_segment_net_pnl"]) == moved


# ------------------------------------------------------------------ before and after a reset

def test_before_any_reset_the_two_scopes_agree(tiers) -> None:
    engine = engine_with(tiers)
    win(engine, 1_100)
    loss(engine, 1_400)
    summary = lifetime(engine)
    # With no reset the current segment is the whole run, so they must not differ.
    assert D(summary["current_segment"]["current_segment_net_pnl"]) == D(summary["net_pnl"])
    assert summary["current_segment"]["reset_count"] == 0


def test_a_reset_zeroes_the_current_scope_and_leaves_the_lifetime_one_alone(tiers) -> None:
    engine = engine_with(tiers)
    win(engine, 1_100)
    loss(engine, 1_400)
    before = lifetime(engine)
    assert D(before["net_pnl"]) != 0

    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    after = lifetime(engine)

    assert D(after["current_segment"]["current_segment_net_pnl"]) == 0
    assert D(after["current_segment"]["current_segment_realized_pnl"]) == 0
    assert D(after["current_segment"]["current_segment_fees"]) == 0
    assert D(after["current_segment"]["current_segment_funding"]) == 0
    # The reason the reset exists is that none of this is lost.
    assert after["net_pnl"] == before["net_pnl"]
    assert after["fees"] == before["fees"]
    assert after["trades"] == before["trades"]


def test_after_the_reset_a_new_trade_shows_only_itself_in_the_current_scope(tiers) -> None:
    engine = engine_with(tiers)
    loss(engine, 1_100)
    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    lifetime_before = D(lifetime(engine)["net_pnl"])

    win(engine, 6_000)
    summary = lifetime(engine)
    segment = summary["current_segment"]

    trades = [t for t in __import__("app.crypto.paper.analytics", fromlist=["x"])
              .build_trades(engine.ledger.events)]
    newest = trades[-1]
    assert D(segment["current_segment_net_pnl"]) == newest.net_pnl
    assert segment["trades"] == 1
    # Lifetime moved by the same trade but started from the older figure.
    assert D(summary["net_pnl"]) == lifetime_before + newest.net_pnl
    assert summary["trades"] == 2


def test_a_losing_trade_after_a_reset_is_negative_in_the_current_scope(tiers) -> None:
    engine = engine_with(tiers)
    win(engine, 1_100)
    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    loss(engine, 6_000)
    segment = current(engine)
    assert D(segment["current_segment_net_pnl"]) < 0
    assert D(segment["current_segment_fees"]) > 0


def test_fees_from_before_the_reset_never_land_in_the_current_scope(tiers) -> None:
    engine = engine_with(tiers)
    for ts in (1_100, 1_400, 1_700):
        win(engine, ts)
    fees_before = engine.account.cumulative_fees
    assert fees_before > 0

    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    assert D(current(engine)["current_segment_fees"]) == 0

    win(engine, 6_000)
    segment_fees = D(current(engine)["current_segment_fees"])
    assert 0 < segment_fees < fees_before
    assert D(lifetime(engine)["fees"]) == engine.account.cumulative_fees


def test_funding_is_split_the_same_way(tiers) -> None:
    engine = engine_with(tiers)
    # Hold a position across a funding settlement so funding is non-zero.
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o1")
    engine.apply_market(quote(8 * 3_600_000, funding_rate="0.0001",
                              next_funding_time_ms=8 * 3_600_000))
    engine.submit_order(ts_ms=8 * 3_600_000 + 100, side="LONG", qty=D("0.010"), intent="CLOSE",
                        request_id="c1")
    funding_before = engine.account.cumulative_funding_paid

    engine.reset_account(ts_ms=9 * 3_600_000, target_krw=D("10000000"))
    assert D(current(engine)["current_segment_funding"]) == 0
    assert D(lifetime(engine)["funding"]) == funding_before


# ------------------------------------------------------------------ anchors and repeats

def test_the_anchor_is_read_from_the_reset_event_itself(tiers) -> None:
    engine = engine_with(tiers)
    win(engine, 1_100)
    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    event = engine.ledger.events[-1]

    anchor = reset_anchor(engine.ledger.events)
    assert anchor["realized_pnl"] == D(event["preserved_realized_pnl"])
    assert anchor["fees"] == D(event["preserved_fees"])
    assert anchor["funding"] == D(event["preserved_funding"])
    assert anchor["since_ts_ms"] == 5_000
    assert anchor["reset_count"] == 1


def test_with_no_reset_the_anchor_points_at_the_run_start(tiers) -> None:
    engine = engine_with(tiers)
    win(engine, 1_100)
    anchor = reset_anchor(engine.ledger.events)
    assert anchor["reset_count"] == 0
    assert anchor["realized_pnl"] == 0 and anchor["fees"] == 0
    assert anchor["since_ts_ms"] == 1_000      # RUN_START


def test_only_the_most_recent_reset_defines_the_current_scope(tiers) -> None:
    engine = engine_with(tiers)
    win(engine, 1_100)
    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    loss(engine, 6_000)
    engine.reset_account(ts_ms=9_000, target_krw=D("5000000"))
    win(engine, 10_000)

    summary = lifetime(engine)
    segment = summary["current_segment"]
    assert segment["reset_count"] == 2
    assert segment["since_ts_ms"] == 9_000
    assert segment["trades"] == 1                      # only the trade after reset #2
    assert summary["trades"] == 3                      # every trade ever
    assert summary["capital_resets"] == 2
    assert len(summary["segments"]) == 3
    assert D(segment["current_segment_net_pnl"]) == (
        engine.account.wallet_balance - engine.account.capital_base_usdt)


def test_the_current_scope_agrees_with_the_last_d4_segment(tiers) -> None:
    engine = engine_with(tiers)
    win(engine, 1_100)
    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    win(engine, 6_000)
    loss(engine, 6_400)

    summary = lifetime(engine)
    # The trade statistics are D4's segment fold, reused rather than computed again.
    assert summary["current_segment"]["trades"] == summary["segments"][-1]["trades"]
    assert summary["current_segment"]["wins"] == summary["segments"][-1]["wins"]
    assert summary["current_segment"]["max_drawdown"] == summary["segments"][-1]["max_drawdown"]


def test_starting_capital_of_the_current_scope_is_what_the_reset_set(tiers) -> None:
    engine = engine_with(tiers, fx="1000")
    win(engine, 1_100)
    engine.reset_account(ts_ms=5_000, target_krw=D("10000000"))
    segment = current(engine)
    assert D(segment["starting_capital_usdt"]) == D("10000")
    assert D(segment["starting_capital_krw"]) == D("10000000")
    assert D(segment["starting_capital_usdt"]) == engine.account.capital_base_usdt


# ------------------------------------------------------------------ restart

def test_a_restart_does_not_turn_the_current_scope_back_into_the_lifetime_one(config, tiers,
                                                                              tmp_path) -> None:
    from app.crypto.terminal.session import PaperSession

    first = PaperSession(config=config, tiers=tiers, root=tmp_path)
    first.start(1_000)
    first.observe(quote(1_000, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]),
                  force=True)
    first.command({"command": "ORDER", "ts_ms": 1_100, "side": "LONG", "qty": "0.010",
                   "intent": "OPEN", "request_id": "o1"},
                  quote=quote(1_100, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]))
    first.command({"command": "ORDER", "ts_ms": 1_300, "side": "LONG", "qty": "0.010",
                   "intent": "CLOSE", "request_id": "c1"},
                  quote=quote(1_300, bids=[["100500.0", "100"]], asks=[["100500.1", "100"]]))
    first.command({"command": "ACCOUNT_RESET", "ts_ms": 5_000, "target_krw": "10000000"},
                  quote=quote(5_000, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]))

    before_current = current(first.engine)
    before_lifetime = lifetime(first.engine)
    assert D(before_current["current_segment_net_pnl"]) == 0
    assert D(before_lifetime["net_pnl"]) != 0

    second = PaperSession(config=config, tiers=tiers, root=tmp_path)
    assert second.recovery is not None and second.recovery.restored
    after_current = current(second.engine)
    after_lifetime = lifetime(second.engine)

    # The bug this forbids: the header reverting to lifetime after a restart.
    assert after_current["current_segment_net_pnl"] == before_current["current_segment_net_pnl"]
    assert after_current["reset_count"] == 1
    assert after_lifetime["net_pnl"] == before_lifetime["net_pnl"]
    assert after_lifetime["trades"] == before_lifetime["trades"]
    assert second.engine.account.reset_count == 1
