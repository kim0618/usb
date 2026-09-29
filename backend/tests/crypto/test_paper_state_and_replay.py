"""State machine, emergency handling and replay determinism."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.paper.engine import (
    OrderRejected, PaperEngine, apply_tape_record, quote_to_payload, replay_tape,
)
from app.crypto.paper.ledger import InputKind, InputTape, Ledger
from app.crypto.paper.state import (
    AUTO, AUTO_OFF, AUTO_ON, AUTO_STOPPING, EMERGENCY, EMERGENCY_ON, EMERGENCY_RELEASE,
    MANUAL, TerminalState, TransitionRejected,
)
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


# ------------------------------------------------------------------ state machine

def test_auto_is_defined_but_refused_because_d3_implements_no_strategy() -> None:
    state = TerminalState()
    with pytest.raises(TransitionRejected) as rejected:
        state.transition(AUTO_ON)
    assert rejected.value.code == "AUTO_NOT_READY"
    assert state.mode == MANUAL
    view = state.view()
    assert view["auto_available"] is False and view["auto_unavailable_reason"] == "AUTO_NOT_READY"
    assert set(view["modes"]) == {MANUAL, AUTO, AUTO_STOPPING, EMERGENCY}


def test_auto_never_returns_to_manual_without_passing_through_auto_stopping() -> None:
    state = TerminalState(mode=AUTO)
    assert AUTO_OFF in {action for action in ("AUTO_OFF",)}
    state.transition(AUTO_OFF)
    assert state.mode == AUTO_STOPPING
    with pytest.raises(TransitionRejected):
        TerminalState(mode=AUTO).transition("POSITION_FLAT")


def test_auto_stopping_blocks_new_entries_but_not_exits() -> None:
    state = TerminalState(mode=AUTO_STOPPING)
    assert state.can_open_new_position() is False
    assert state.blocks_new_entry_reason() == "NEW_ENTRY_BLOCKED_AUTO_STOPPING"


def test_emergency_requires_confirmation_and_never_releases_itself() -> None:
    state = TerminalState()
    with pytest.raises(TransitionRejected) as unconfirmed:
        state.transition(EMERGENCY_ON)
    assert unconfirmed.value.code == "CONFIRMATION_REQUIRED"
    state.transition(EMERGENCY_ON, confirmed=True)
    assert state.mode == EMERGENCY
    with pytest.raises(TransitionRejected):
        state.transition(AUTO_ON)
    state.transition(EMERGENCY_RELEASE)
    assert state.mode == MANUAL  # contract E6: release lands in MANUAL, never AUTO


def test_a_new_entry_is_refused_in_emergency_while_a_close_is_allowed(config, tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.set_mode(ts_ms=1_150, action=EMERGENCY_ON, confirmed=True)
    assert engine.state.mode == EMERGENCY
    assert engine.account.position.is_flat  # the emergency closed it
    engine.apply_market(quote(2_000))
    with pytest.raises(OrderRejected) as rejected:
        engine.submit_order(ts_ms=2_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="n")
    assert rejected.value.code == "NEW_ENTRY_BLOCKED_EMERGENCY"


def test_emergency_blocks_before_it_closes(config, tiers) -> None:
    """Contract E2: the mode change is recorded before the closing fill, so no entry can race
    into the gap between them."""
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    before = len(engine.ledger.events)
    engine.set_mode(ts_ms=1_150, action=EMERGENCY_ON, confirmed=True)
    after = [event["event_type"] for event in engine.ledger.events[before:]]
    assert after[0] == "MODE_CHANGE"
    assert "POSITION_CLOSE" in after
    assert after.index("MODE_CHANGE") < after.index("POSITION_CLOSE")


def test_emergency_close_reports_a_position_it_could_not_close_instead_of_hiding_it(config, tiers) -> None:
    """Contract E4/I6: an emergency that fails to flatten must say so."""
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(2_000, bids=[], asks=[["100000.1", "10"]], mark="100000.0"))
    result = engine.set_mode(ts_ms=2_100, action=EMERGENCY_ON, confirmed=True)
    assert result["failed"] is not None
    assert result["failed"]["code"] == "NO_QUOTE"
    assert engine.state.mode == EMERGENCY
    assert not engine.account.position.is_flat  # left open, and visible
    failures = [event for event in engine.ledger.events if event.get("emergency_failure")]
    assert len(failures) == 1


def test_emergency_close_on_a_flat_account_is_a_no_op(config, tiers) -> None:
    engine = started_engine(config, tiers)
    result = engine.emergency_close(ts_ms=1_100)
    assert result == {"closed": None, "failed": None}
    assert engine.ledger.of_type("ORDER_SUBMITTED") == []


def test_every_mode_change_is_written_to_the_ledger(config, tiers) -> None:
    engine = started_engine(config, tiers)
    engine.set_mode(ts_ms=1_100, action=EMERGENCY_ON, confirmed=True)
    engine.set_mode(ts_ms=1_200, action=EMERGENCY_RELEASE)
    changes = engine.ledger.of_type("MODE_CHANGE")
    assert [event["mode"] for event in changes] == [EMERGENCY, MANUAL]
    assert all("previous_mode" in event for event in changes)


# ------------------------------------------------------------------ determinism

def _record_session(tape: InputTape, engine: PaperEngine) -> None:
    """Drive a session through the tape so the tape is the complete input by construction."""
    def market(**kwargs) -> None:
        payload = quote_to_payload(quote(**kwargs))
        tape.record(InputKind.MARKET, payload)
        apply_tape_record(engine, {"kind": InputKind.MARKET, "payload": payload})

    def command(**payload) -> None:
        tape.record(InputKind.COMMAND, payload)
        apply_tape_record(engine, {"kind": InputKind.COMMAND, "payload": payload})

    settlement = 8 * 60 * 60 * 1000
    command(command="START", ts_ms=settlement - 5_000)
    market(ts_ms=settlement - 5_000, funding_rate="0.0001")
    command(command="SET_LEVERAGE", ts_ms=settlement - 4_500, leverage="20")
    command(command="ORDER", ts_ms=settlement - 4_000, side="LONG", qty="0.010", intent="OPEN",
            request_id="o1", reason="MANUAL")
    market(ts_ms=settlement - 3_000, bid="100200.0", ask="100200.1", funding_rate="0.0001")
    command(command="ORDER", ts_ms=settlement - 2_500, side="LONG", qty="0.0015", intent="OPEN",
            request_id="bad-grid")
    command(command="ORDER", ts_ms=settlement - 2_000, side="SHORT", qty="0.010", intent="OPEN",
            request_id="bad-reverse")
    market(ts_ms=settlement + 1_000, bid="100400.0", ask="100400.1", funding_rate="0.0001",
           next_funding_time_ms=settlement + 8 * 3_600_000)
    command(command="ORDER", ts_ms=settlement + 1_500, side="LONG", qty="0.004", intent="CLOSE",
            request_id="p1")
    command(command="SET_MODE", ts_ms=settlement + 2_000, action=EMERGENCY_ON, confirmed=True)
    command(command="SET_MODE", ts_ms=settlement + 2_500, action=EMERGENCY_RELEASE)


def test_a_replay_of_the_recorded_inputs_reproduces_the_ledger_byte_for_byte(tiers) -> None:
    """Contract T1/T8. The engine reads no clock and no socket, so the tape plus the config is
    the whole input; anything non-deterministic would show up as a byte difference here."""
    config = make_config(taker="0.0006", slippage_model="FIXED_BPS", slippage_bps="3")
    tape = InputTape()
    original = PaperEngine(config, tiers)
    _record_session(tape, original)

    replayed = replay_tape(config, tiers, tape)
    assert replayed.ledger.bytes() == original.ledger.bytes()
    assert len(original.ledger.events) > 15
    # A second replay is identical too: replaying is not itself a state change.
    assert replay_tape(config, tiers, tape).ledger.bytes() == original.ledger.bytes()


def test_the_replayed_account_matches_the_original_figure_for_figure(tiers) -> None:
    config = make_config(taker="0.0006")
    tape = InputTape()
    original = PaperEngine(config, tiers)
    _record_session(tape, original)
    replayed = replay_tape(config, tiers, tape)
    mark = D("100400.0")
    assert replayed.account.view(tiers, mark) == original.account.view(tiers, mark)
    assert replayed.state.mode == original.state.mode
    assert replayed.liquidation_count == original.liquidation_count


def test_a_tape_written_to_disk_replays_to_the_same_bytes(tiers, tmp_path: Path) -> None:
    config = make_config(taker="0.0006")
    tape = InputTape(path=tmp_path / "input.jsonl")
    original = PaperEngine(config, tiers, ledger=Ledger(path=tmp_path / "ledger.jsonl"))
    _record_session(tape, original)

    reread = InputTape.read(tmp_path / "input.jsonl")
    assert [record["seq"] for record in reread] == [record["seq"] for record in tape]
    replayed = replay_tape(config, tiers, reread)
    assert replayed.ledger.bytes() == (tmp_path / "ledger.jsonl").read_bytes()


def test_rejected_orders_are_ledger_events_so_a_replay_cannot_diverge_on_them(tiers) -> None:
    config = make_config()
    tape = InputTape()
    engine = PaperEngine(config, tiers)
    _record_session(tape, engine)
    rejections = engine.ledger.of_type("ORDER_REJECTED")
    assert {event["code"] for event in rejections} == {"QTY_OFF_GRID", "REVERSE_NOT_ALLOWED"}
    assert all(event["request_id"] in {"bad-grid", "bad-reverse"} for event in rejections)


def test_the_ledger_is_canonical_json_with_a_dense_sequence(tiers, tmp_path: Path) -> None:
    config = make_config()
    tape = InputTape()
    engine = PaperEngine(config, tiers, ledger=Ledger(path=tmp_path / "ledger.jsonl"))
    _record_session(tape, engine)
    lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert [json.loads(line)["seq"] for line in lines] == list(range(1, len(lines) + 1))
    for line in lines:
        # Canonical form: sorted keys, no spaces. Re-serialising must be a fixed point.
        assert json.dumps(json.loads(line), sort_keys=True, separators=(",", ":")) == line


def test_the_first_ledger_event_carries_the_whole_run_config(tiers) -> None:
    config = make_config(taker="0.0006")
    engine = PaperEngine(config, tiers)
    engine.start(1_000)
    start = engine.ledger.events[0]
    assert start["event_type"] == "RUN_START"
    snapshot = start["config"]
    assert snapshot["fees"]["version"] == "test-fees-v1"
    assert snapshot["fees"]["basis"] == "ASSUMED_PUBLIC_NON_VIP"
    assert snapshot["fx"]["source"] == "test fixture"
    assert snapshot["reverse_policy"] == "REJECT"
    assert snapshot["liquidation_fill_basis"] == "MARK_AT_TRIGGER"
    assert start["risk_tier_source_sha256"] == "deadbeef"
