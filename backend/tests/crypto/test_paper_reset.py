"""Balance reset.

The rule every test here circles is the same one: a reset changes what is spendable and nothing
else. If any of these start failing by "losing" a trade, a fee or a funding payment, the feature
has become the thing it was explicitly not supposed to be.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.paper import analytics
from app.crypto.paper.config import DEFAULT_STARTING_CAPITAL_KRW
from app.crypto.paper.engine import OrderRejected, PaperEngine
from app.crypto.paper.ledger import EventType, InputKind, InputTape
from app.crypto.paper.instrument import RiskTierTable
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


def deep(tiers: RiskTierTable, **kwargs) -> PaperEngine:
    return started_engine(make_config(**kwargs), tiers, first_quote=quote(
        1_000, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]]))


def round_trip(engine: PaperEngine, ts: int, *, side: str = "LONG", qty: str = "0.010") -> None:
    engine.submit_order(ts_ms=ts, side=side, qty=D(qty), intent="OPEN", request_id=f"o{ts}")
    engine.apply_market(quote(ts + 50, bid="100100.0", ask="100100.1"))
    engine.submit_order(ts_ms=ts + 100, side=side, qty=D(qty), intent="CLOSE",
                        request_id=f"c{ts}")


# ------------------------------------------------------------------ default capital

def test_the_default_virtual_account_is_ten_million_won() -> None:
    assert DEFAULT_STARTING_CAPITAL_KRW == D("10000000")


def test_a_run_configured_with_the_default_starts_at_ten_million(tiers) -> None:
    engine = deep(tiers, capital_krw=str(DEFAULT_STARTING_CAPITAL_KRW), fx="1000")
    assert engine.account.starting_capital_usdt == D("10000")
    assert engine.account.equity(D("100000")) == D("10000")


# ------------------------------------------------------------------ mechanics

def test_a_reset_sets_the_balance_to_exactly_the_target(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")   # 1000 USDT
    round_trip(engine, 1_100)
    assert engine.account.equity(D("100100.0")) != D("10000")

    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    # Flat, so equity is the balance and the balance is exactly the target.
    assert engine.account.wallet_balance == D("10000")
    assert engine.account.equity(D("100100.0")) == D("10000")
    assert engine.account.available_balance == D("10000")
    assert engine.account.unrealized_pnl(D("100100.0")) == 0


def test_a_reset_preserves_every_cumulative_figure(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    realized = engine.account.realized_pnl
    fees = engine.account.cumulative_fees
    funding = engine.account.cumulative_funding_paid
    assert fees > 0

    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))

    # The record is the point. None of this is rewound.
    assert engine.account.realized_pnl == realized
    assert engine.account.cumulative_fees == fees
    assert engine.account.cumulative_funding_paid == funding
    assert engine.account.starting_capital_usdt == D("1000")


def test_a_reset_appends_one_event_and_deletes_none(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    before_events = list(engine.ledger.events)

    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))

    assert engine.ledger.events[:len(before_events)] == before_events
    assert len(engine.ledger.events) == len(before_events) + 1
    event = engine.ledger.events[-1]
    assert event["event_type"] == EventType.ACCOUNT_RESET


def test_the_reset_event_carries_the_before_and_after_and_what_it_kept(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    before_equity = engine.account.equity(D("100100.0"))

    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"), reason="USER_RESET")
    event = engine.ledger.events[-1]

    assert D(event["before_equity"]) == before_equity
    assert D(event["after_equity"]) == D("10000")
    assert D(event["after_equity_krw"]) == D("10000000")
    assert D(event["fixed_fx_rate"]) == D("1000")
    assert event["reason"] == "USER_RESET"
    assert D(event["preserved_realized_pnl"]) == engine.account.realized_pnl
    assert D(event["preserved_fees"]) == engine.account.cumulative_fees
    assert event["reset_count"] == 1


def test_the_account_invariants_still_hold_after_a_reset(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    engine.account.assert_invariants(D("100100.0"))   # raises if A6 or P5 broke

    # And trading continues from the new base.
    round_trip(engine, 3_000)
    engine.account.assert_invariants(D("100100.0"))


def test_trading_after_a_reset_moves_from_the_new_base_not_the_old(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    base = engine.account.wallet_balance
    round_trip(engine, 3_000)
    # The only change since the reset is this one trade's net result.
    moved = engine.account.wallet_balance - base
    trades = analytics.build_trades(engine.ledger.events)
    assert moved == trades[-1].net_pnl


def test_resetting_twice_works_and_is_counted(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    round_trip(engine, 3_000)
    engine.reset_account(ts_ms=4_000, target_krw=D("5000000"))

    assert engine.account.reset_count == 2
    assert engine.account.wallet_balance == D("5000")
    assert engine.account.last_reset_ts_ms == 4_000
    assert len(analytics.build_trades(engine.ledger.events)) == 2


# ------------------------------------------------------------------ safety

def test_a_reset_is_refused_while_a_position_is_open(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    with pytest.raises(OrderRejected) as raised:
        engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    assert raised.value.code == "RESET_BLOCKED_OPEN_POSITION"


def test_a_refused_reset_changes_nothing_at_all(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    balance = engine.account.wallet_balance
    events = len(engine.ledger.events)
    with pytest.raises(OrderRejected):
        engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    # No balance moved, no event written: a refusal is not a half-reset.
    assert engine.account.wallet_balance == balance
    assert len(engine.ledger.events) == events
    assert engine.account.reset_count == 0


def test_the_open_position_is_never_closed_to_make_a_reset_succeed(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    with pytest.raises(OrderRejected):
        engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    # Closing on the operator's behalf would be a trade at a price nobody chose.
    assert engine.account.position.abs_qty == D("0.010")


@pytest.mark.parametrize("target", ["0", "-1"])
def test_a_non_positive_target_is_refused(tiers, target: str) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    with pytest.raises(OrderRejected) as raised:
        engine.reset_account(ts_ms=2_000, target_krw=D(target))
    assert raised.value.code == "RESET_TARGET_INVALID"


# ------------------------------------------------------------------ replay

def test_a_reset_replays_from_the_tape_byte_for_byte(config, tiers, tmp_path) -> None:
    from app.crypto.paper.engine import apply_tape_record, quote_to_payload
    from app.crypto.paper.ledger import Ledger

    tape = InputTape(path=tmp_path / "input.jsonl")
    engine = PaperEngine(make_config(capital_krw="1000000", fx="1000"), tiers,
                         ledger=Ledger(path=tmp_path / "ledger.jsonl"))
    first = quote(1_000, bids=[["100000.0", "100"]], asks=[["100000.1", "100"]])

    def market(bar) -> None:
        payload = quote_to_payload(bar)
        tape.record(InputKind.MARKET, payload)
        apply_tape_record(engine, {"kind": InputKind.MARKET, "payload": payload})

    def command(payload) -> None:
        tape.record(InputKind.COMMAND, payload)
        apply_tape_record(engine, {"kind": InputKind.COMMAND, "payload": payload})

    command({"command": "START", "ts_ms": 1_000})
    market(first)
    command({"command": "ORDER", "ts_ms": 1_100, "side": "LONG", "qty": "0.010",
             "intent": "OPEN", "request_id": "o1", "reason": "MANUAL"})
    market(quote(1_150, bid="100100.0", ask="100100.1"))
    command({"command": "ORDER", "ts_ms": 1_200, "side": "LONG", "qty": "0.010",
             "intent": "CLOSE", "request_id": "c1", "reason": "MANUAL"})
    command({"command": "ACCOUNT_RESET", "ts_ms": 2_000, "target_krw": "10000000",
             "reason": "USER_RESET"})

    rebuilt = PaperEngine(make_config(capital_krw="1000000", fx="1000"), tiers, ledger=Ledger())
    for record in InputTape.read(tmp_path / "input.jsonl"):
        apply_tape_record(rebuilt, record)

    # A restart must land on the same balance and the same ledger, not re-run the reset.
    assert rebuilt.ledger.bytes() == engine.ledger.bytes()
    assert rebuilt.account.wallet_balance == engine.account.wallet_balance == D("10000")
    assert rebuilt.account.reset_count == 1
    assert rebuilt.account.realized_pnl == engine.account.realized_pnl


def test_a_restart_does_not_invent_a_reset(config, tiers) -> None:
    from app.crypto.paper.engine import replay_tape
    tape = InputTape()
    tape.record(InputKind.COMMAND, {"command": "START", "ts_ms": 1_000})
    tape.record(InputKind.MARKET, __import__("app.crypto.paper.engine", fromlist=["x"])
                .quote_to_payload(quote(1_000)))
    rebuilt = replay_tape(make_config(capital_krw="1000000", fx="1000"), tiers, tape)
    assert rebuilt.account.reset_count == 0
    assert rebuilt.account.wallet_balance == D("1000")


# ------------------------------------------------------------------ analytics

def test_analytics_still_sees_every_trade_from_before_the_reset(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    round_trip(engine, 1_400)
    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    round_trip(engine, 3_000)

    summary = analytics.summarize(engine.ledger.events,
                                  starting_capital=engine.account.starting_capital_usdt)
    # Three round trips across a reset boundary, all still counted.
    assert summary["trades"] == 3
    assert summary["capital_resets"] == 1
    assert D(summary["fees"]) == engine.account.cumulative_fees


def test_the_equity_path_is_cut_at_the_reset_rather_than_stepping_over_it(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    round_trip(engine, 3_000)

    summary = analytics.summarize(engine.ledger.events,
                                  starting_capital=engine.account.starting_capital_usdt)
    segments = summary["segments"]
    assert len(segments) == 2
    assert D(segments[0]["starting_capital"]) == D("1000")
    assert D(segments[1]["starting_capital"]) == D("10000")
    assert segments[0]["trades"] == 1 and segments[1]["trades"] == 1
    # The +9000 top-up is not a profit, so it must not appear in the ending equity as one.
    assert D(summary["ending_equity"]) == engine.account.wallet_balance


def test_the_top_up_is_not_counted_as_a_drawdown_recovery(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    # A loser first, so segment one really is under water.
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(1_150, bid="99000.0", ask="99000.1"))
    engine.submit_order(ts_ms=1_200, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")
    losing_dd = analytics.summarize(
        engine.ledger.events, starting_capital=D("1000"))["max_drawdown"]
    assert D(losing_dd) > 0

    engine.apply_market(quote(1_900, bid="100000.0", ask="100000.1"))
    engine.reset_account(ts_ms=2_000, target_krw=D("10000000"))
    summary = analytics.summarize(engine.ledger.events, starting_capital=D("1000"))
    # The drawdown that happened still happened; the reset neither erases nor deepens it.
    assert D(summary["max_drawdown"]) == D(losing_dd)


def test_a_run_with_no_reset_reports_exactly_one_segment(tiers) -> None:
    engine = deep(tiers, capital_krw="1000000", fx="1000")
    round_trip(engine, 1_100)
    summary = analytics.summarize(engine.ledger.events, starting_capital=D("1000"))
    assert summary["capital_resets"] == 0
    assert len(summary["segments"]) == 1
    assert D(summary["segments"][0]["ending_equity"]) == D(summary["ending_equity"])


# ------------------------------------------------------------------ tape memory

def test_a_live_session_does_not_accumulate_the_tape_in_memory(config, tiers, tmp_path) -> None:
    """The file is the tape. Keeping a second copy in RAM grew the process without bound: after
    a day of 1 Hz appends the list alone was enough to stop the service starting."""
    from app.crypto.terminal.session import PaperSession

    session = PaperSession(config=config, tiers=tiers, root=tmp_path)
    session.start(1_000)
    for i in range(50):
        session.observe(quote(2_000 + i * 1_000), force=True)

    assert session.tape.retain is False
    assert session.tape.records == []
    # The count still advances, because the sequence numbers and the progress figure need it.
    assert len(session.tape) == 51
    written = InputTape.read(session.run_dir / "input.jsonl").records
    assert len(written) == 51
    assert [record["seq"] for record in written] == list(range(1, 52))


def test_a_restored_session_continues_the_sequence_without_loading_the_tape(config, tiers,
                                                                           tmp_path) -> None:
    from app.crypto.terminal.session import PaperSession

    first = PaperSession(config=config, tiers=tiers, root=tmp_path)
    first.start(1_000)
    for i in range(20):
        first.observe(quote(2_000 + i * 1_000), force=True)
    before = len(first.tape)

    second = PaperSession(config=config, tiers=tiers, root=tmp_path)
    assert second.tape.records == []
    assert len(second.tape) == before
    assert second.started_at_ms == 1_000

    second.observe(quote(99_000), force=True)
    written = InputTape.read(second.run_dir / "input.jsonl").records
    # No gap and no repeat across the restart.
    assert [record["seq"] for record in written] == list(range(1, before + 2))


def test_scanning_a_tape_finds_the_start_and_the_last_market_tick(config, tiers, tmp_path) -> None:
    from app.crypto.terminal.session import PaperSession

    session = PaperSession(config=config, tiers=tiers, root=tmp_path)
    session.start(1_000)
    session.observe(quote(5_000), force=True)
    session.observe(quote(9_000), force=True)

    summary = InputTape.scan(session.run_dir / "input.jsonl")
    assert summary.count == 3
    assert summary.first_start_ms == 1_000
    assert summary.last_market_ms == 9_000
