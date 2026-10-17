"""Multi-entry chart labels V1: every fill is its own numbered execution row.

`entry_no` numbers the entries of one position (1 for the first entry or a flip's opening leg,
one more per add, back to 1 after a full close); closes carry none. `seq` stays the row's
identity. The average entry is the account's own: the PAPER engine's `avg_entry`, Binance's
`entryPrice` on LIVE - the rows are never averaged into it.

PAPER through the order route on a hand-fed app; LIVE on mirror lines written here. No order
reaches any exchange.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.terminal import api as terminal_api
from app.crypto.terminal.executions import (
    ADD_ENTRY, FULL_CLOSE, INITIAL_ENTRY, PARTIAL_CLOSE, live_execution_audit, live_executions)
from tests.crypto.test_live_execution_price import _ack, _intent, _result
from tests.crypto.test_manual_ux_account_sharing import ARC, to_arc
from tests.crypto.test_manual_ux_final_v1 import account, client, executions, order  # noqa: F401
from tests.crypto.test_hot_symbol_v1 import clean_dynamic, paper  # noqa: F401
from tests.crypto.test_hot_symbol_v1 import order as hot_order
from tests.crypto.test_paper_terminal_api import snapshot_message, ticker_snapshot

D = Decimal
_clock = {"ts": 2_000}


def move(ask: str) -> None:
    """Re-quote the hand-fed BTC book so the next fill happens at `ask` (bid one tick under)."""
    _clock["ts"] += 1_000
    bid = str(D(ask) - D("0.1"))
    runtime = terminal_api.runtime
    runtime.feed.handle(snapshot_message(bid=bid, ask=ask), _clock["ts"])
    runtime.feed.handle(ticker_snapshot(mark=bid, ts=_clock["ts"]), _clock["ts"])
    runtime.session.observe(runtime.feed.quote(), force=True)


def numbered(rows: list[dict]) -> list[tuple]:
    return [(r["seq"], r["event_type"], r["entry_no"], r["side"], r["qty"]) for r in rows]


# ------------------------------------------------------------------ PAPER


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_paper_one_two_three_entries_are_numbered_1_2_3(client, side) -> None:
    order(client, side, "0.010")
    assert numbered(executions(client)) == [(1, INITIAL_ENTRY, 1, side, "0.010")]
    order(client, side, "0.020")
    assert [r["entry_no"] for r in executions(client)] == [1, 2]
    order(client, side, "0.015")
    rows = executions(client)
    assert numbered(rows) == [(1, INITIAL_ENTRY, 1, side, "0.010"), (2, ADD_ENTRY, 2, side, "0.020"),
                              (3, ADD_ENTRY, 3, side, "0.015")]
    assert all(r["mode"] == "PAPER" and r["symbol"] == "BTCUSDT" for r in rows)


def test_paper_three_entries_in_the_same_minute_are_three_rows(client) -> None:
    # The hand-fed clock does not move between these: same quote, same minute.
    for qty in ("0.010", "0.010", "0.010"):
        order(client, "LONG", qty)
    rows = executions(client)
    assert len(rows) == 3 and len({r["seq"] for r in rows}) == 3
    assert max(r["ts_ms"] for r in rows) - min(r["ts_ms"] for r in rows) < 60_000
    assert [r["entry_no"] for r in rows] == [1, 2, 3]


def test_paper_average_entry_is_the_engines_and_moves_with_each_add(client) -> None:
    averages = []
    for ask, qty in (("100000.1", "0.010"), ("101000.1", "0.010"), ("99000.1", "0.020")):
        move(ask)
        order(client, "LONG", qty)
        held = account(client)
        engine = terminal_api.runtime.session.engine.account.position
        assert D(held["avg_entry"]) == engine.avg_entry      # the state carries the engine's figure
        averages.append(D(held["avg_entry"]))
    assert averages[0] != averages[1] != averages[2]         # it moved after every add
    rows = executions(client)
    assert [D(r["price"]) for r in rows] == [D("100000.1"), D("101000.1"), D("99000.1")]
    assert [r["entry_no"] for r in rows] == [1, 2, 3]


def test_paper_half_close_keeps_the_entries_and_the_average(client) -> None:
    order(client, "LONG", "0.040")
    move("100500.1")
    order(client, "LONG", "0.020")
    before = account(client)["avg_entry"]
    order(client, "LONG", "0.030", intent="CLOSE")
    assert account(client)["avg_entry"] == before
    rows = executions(client)
    assert numbered(rows) == [(1, INITIAL_ENTRY, 1, "LONG", "0.040"), (2, ADD_ENTRY, 2, "LONG", "0.020"),
                              (3, PARTIAL_CLOSE, None, "LONG", "0.030")]


def test_paper_full_close_ends_the_numbering_and_the_next_entry_is_1(client) -> None:
    order(client, "SHORT", "0.010")
    order(client, "SHORT", "0.010")
    order(client, "SHORT", "0.020", intent="CLOSE")
    assert account(client)["position_side"] is None and account(client)["avg_entry"] is None
    order(client, "SHORT", "0.010")
    assert numbered(executions(client)) == [
        (1, INITIAL_ENTRY, 1, "SHORT", "0.010"), (2, ADD_ENTRY, 2, "SHORT", "0.010"),
        (3, FULL_CLOSE, None, "SHORT", "0.020"), (4, INITIAL_ENTRY, 1, "SHORT", "0.010")]


def test_paper_flip_opening_leg_is_entry_1_of_the_new_position(client) -> None:
    order(client, "LONG", "0.030")
    order(client, "LONG", "0.010")
    order(client, "SHORT", "0.060")
    assert [(r["event_type"], r["entry_no"], r["side"]) for r in executions(client)] == [
        (INITIAL_ENTRY, 1, "LONG"), (ADD_ENTRY, 2, "LONG"), (FULL_CLOSE, None, "LONG"),
        ("POSITION_FLIP", 1, "SHORT")]


def test_paper_reload_returns_the_same_rows(client) -> None:
    for qty in ("0.010", "0.020", "0.030"):
        order(client, "LONG", qty)
    first = executions(client)
    assert executions(client) == first and len(first) == 3


def test_paper_symbols_number_their_own_entries(paper) -> None:  # noqa: F811
    client_, runtime, use_market, _ = paper
    assert hot_order(client_, "BTCUSDT", "LONG", qty="0.010").status_code == 200
    to_arc(client_, runtime, use_market)
    for qty in ("1000.0", "500.0"):
        assert hot_order(client_, ARC, "LONG", qty=qty).status_code == 200
    assert hot_order(client_, "BTCUSDT", "LONG", qty="0.020").status_code == 200
    btc = client_.get("/api/crypto/executions", params={"symbol": "BTCUSDT"}).json()["executions"]
    arc = client_.get("/api/crypto/executions", params={"symbol": ARC}).json()["executions"]
    assert [(r["symbol"], r["entry_no"], r["qty"]) for r in btc] == [
        ("BTCUSDT", 1, "0.010"), ("BTCUSDT", 2, "0.020")]
    assert [(r["symbol"], r["entry_no"], r["qty"]) for r in arc] == [(ARC, 1, "1000.0"), (ARC, 2, "500.0")]


# ------------------------------------------------------------------ LIVE


def _priced(cid: str, order_id: int, qty: str, price: str, *, side: str = "BUY", at: int,
            reduce_only: bool = False, symbol: str = "BTCUSDT") -> dict:
    return {**_ack(cid, order_id, qty, side=side, reduce_only=reduce_only, at=at),
            "avgPrice": price, "symbol": symbol}


def _live_long_series(side: str = "LONG") -> list[dict]:
    buy, sell = ("BUY", "SELL") if side == "LONG" else ("SELL", "BUY")
    minute = 1_792_000_020_000
    return [
        _intent("o1", side=side), _result("o1", _priced("o1", 1, "0.010", "84000", side=buy, at=minute)),
        _intent("o2", side=side, held="0.010"),
        _result("o2", _priced("o2", 2, "0.020", "84100", side=buy, at=minute + 5_000)),
        _intent("o3", side=side, held="0.030"),
        _result("o3", _priced("o3", 3, "0.015", "83900", side=buy, at=minute + 9_000)),
        _intent("h", intent="CLOSE", side=side, held="0.045"),
        _result("h", _priced("h", 4, "0.022", "84500", side=sell, reduce_only=True, at=minute + 30_000)),
    ]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_entries_in_one_minute_are_1_2_3_then_a_partial_close(side) -> None:
    rows = live_executions(_live_long_series(side), symbol="BTCUSDT", account="acct")
    assert numbered(rows) == [(1, INITIAL_ENTRY, 1, side, "0.010"), (2, ADD_ENTRY, 2, side, "0.020"),
                              (3, ADD_ENTRY, 3, side, "0.015"), (4, PARTIAL_CLOSE, None, side, "0.022")]
    assert [r["price"] for r in rows] == ["84000", "84100", "83900", "84500"]
    assert all(r["mode"] == "LIVE" for r in rows)


def test_live_full_close_then_reentry_starts_at_1() -> None:
    lines = _live_long_series() + [
        _intent("c", intent="CLOSE", held="0.023"),
        _result("c", _priced("c", 5, "0.023", "84400", side="SELL", reduce_only=True, at=1_792_000_100_000)),
        _intent("n"), _result("n", _priced("n", 6, "0.005", "84300", at=1_792_000_200_000))]
    rows = live_executions(lines, symbol="BTCUSDT", account="acct")
    assert [(r["event_type"], r["entry_no"]) for r in rows][-2:] == [(FULL_CLOSE, None), (INITIAL_ENTRY, 1)]


def test_live_a_fill_left_off_for_want_of_a_price_still_counts() -> None:
    """#2 had no price anywhere: it is not drawn, and the next add is still #3."""
    lines = _live_long_series()
    lines[3] = _result("o2", _ack("o2", 2, "0.020", at=1_792_000_025_000))  # ACK, no price, no cache
    rows, dropped = live_execution_audit(lines, symbol="BTCUSDT", account="acct", trades={})
    assert [(r["client_order_id"], r["entry_no"]) for r in rows[:2]] == [("o1", 1), ("o3", 3)]
    assert [d["client_order_id"] for d in dropped] == ["o2"]


def test_live_symbols_and_accounts_do_not_mix() -> None:
    other = [_intent("e1", symbol="ETHUSDT"),
             _result("e1", _priced("e1", 9, "0.5", "3100", at=1_792_000_021_000, symbol="ETHUSDT")),
             {**_intent("x1"), "account": "someone-else"},
             {**_result("x1", _priced("x1", 10, "0.010", "84000", at=1_792_000_022_000)), "account": "someone-else"}]
    lines = _live_long_series() + other
    btc = live_executions(lines, symbol="BTCUSDT", account="acct")
    eth = live_executions(lines, symbol="ETHUSDT", account="acct")
    assert [r["client_order_id"] for r in btc] == ["o1", "o2", "o3", "h"]
    assert [(r["symbol"], r["entry_no"]) for r in eth] == [("ETHUSDT", 1)]


def test_live_reload_returns_the_same_rows() -> None:
    lines = _live_long_series()
    assert live_executions(lines, symbol="BTCUSDT", account="acct") == \
        live_executions(lines, symbol="BTCUSDT", account="acct")
