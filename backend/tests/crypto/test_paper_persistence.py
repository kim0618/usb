"""Restart recovery: torn writes, lagging ledgers, and restarting twice."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.paper import LEDGER_SCHEMA_VERSION
from app.crypto.paper.engine import PaperEngine, apply_tape_record, quote_to_payload
from app.crypto.paper.ledger import InputKind, InputTape, Ledger
from app.crypto.paper.persistence import (
    RecoveryRefused, recover, truncate_to_last_complete_line,
)
from app.crypto.terminal.session import PaperSession
from tests.crypto.conftest import make_config, quote

D = Decimal


def drive(run_dir: Path, config, tiers, *, close: bool = False) -> PaperEngine:
    """Write a session the way the live path does: tape first, then the engine."""
    tape = InputTape(path=run_dir / "input.jsonl")
    engine = PaperEngine(config, tiers, ledger=Ledger(path=run_dir / "ledger.jsonl"))

    def market(**kwargs) -> None:
        payload = quote_to_payload(quote(**kwargs))
        tape.record(InputKind.MARKET, payload)
        apply_tape_record(engine, {"kind": InputKind.MARKET, "payload": payload})

    def command(**payload) -> None:
        tape.record(InputKind.COMMAND, payload)
        apply_tape_record(engine, {"kind": InputKind.COMMAND, "payload": payload})

    command(command="START", ts_ms=1_000)
    market(ts_ms=1_000)
    command(command="SET_LEVERAGE", ts_ms=1_050, leverage="20")
    command(command="ORDER", ts_ms=1_100, side="LONG", qty="0.010", intent="OPEN",
            request_id="o1", reason="MANUAL")
    market(ts_ms=2_000, bid="100400.0", ask="100400.1")
    if close:
        command(command="ORDER", ts_ms=2_100, side="LONG", qty="0.010", intent="CLOSE",
                request_id="c1", reason="MANUAL")
    return engine


def test_a_clean_restart_restores_the_position_the_mode_and_every_running_total(tmp_path, config, tiers) -> None:
    original = drive(tmp_path / "run", config, tiers)
    before = original.account.view(tiers, D("100400.0"))

    engine, result = recover(tmp_path / "run", config, tiers)
    assert result.restored is True
    assert result.ledger_tail_rewritten == 0
    assert engine.account.view(tiers, D("100400.0")) == before
    assert engine.leverage == D("20")
    assert engine.state.mode == original.state.mode
    assert engine.account.position.signed_qty == D("0.010")
    assert engine.account.position.avg_entry == original.account.position.avg_entry
    assert engine.account.cumulative_fees == original.account.cumulative_fees
    assert engine.ledger.bytes() == (tmp_path / "run" / "ledger.jsonl").read_bytes()


def test_restarting_twice_lands_in_the_same_place_as_restarting_once(tmp_path, config, tiers) -> None:
    drive(tmp_path / "run", config, tiers)
    ledger_after_first = None
    for _ in range(3):
        engine, result = recover(tmp_path / "run", config, tiers)
        current = (tmp_path / "run" / "ledger.jsonl").read_bytes()
        if ledger_after_first is None:
            ledger_after_first = current
        assert current == ledger_after_first     # idempotent: nothing is appended again
        assert result.ledger_tail_rewritten == 0
        assert engine.account.position.signed_qty == D("0.010")


def test_a_torn_tape_line_is_dropped_and_the_engine_recovers_without_it(tmp_path, config, tiers) -> None:
    run = tmp_path / "run"
    drive(run, config, tiers)
    with (run / "input.jsonl").open("ab") as stream:
        stream.write(b'{"seq":99,"kind":"COMMAND","payload":{"command":"ORDER"')  # no newline

    engine, result = recover(run, config, tiers)
    torn = [item for item in result.truncations if item.was_torn]
    assert len(torn) == 1 and torn[0].path.endswith("input.jsonl")
    assert engine.account.position.signed_qty == D("0.010")
    assert result.view()["torn_writes_repaired"][0]["dropped_bytes"] > 0


def test_a_ledger_that_is_behind_the_tape_gets_exactly_its_missing_tail(tmp_path, config, tiers) -> None:
    """The tape is fsynced before the engine runs, so a crash in between leaves this shape."""
    run = tmp_path / "run"
    original = drive(run, config, tiers)
    full = (run / "ledger.jsonl").read_bytes()
    lines = full.splitlines(keepends=True)
    truncated = b"".join(lines[:-3])
    (run / "ledger.jsonl").write_bytes(truncated)

    engine, result = recover(run, config, tiers)
    assert result.ledger_tail_rewritten == len(full) - len(truncated)
    assert (run / "ledger.jsonl").read_bytes() == full
    assert engine.account.view(tiers, D("100400.0")) == original.account.view(tiers, D("100400.0"))


def test_a_torn_ledger_line_is_repaired_from_the_tape(tmp_path, config, tiers) -> None:
    run = tmp_path / "run"
    drive(run, config, tiers)
    full = (run / "ledger.jsonl").read_bytes()
    (run / "ledger.jsonl").write_bytes(full[:-40])   # last line cut mid-way

    engine, result = recover(run, config, tiers)
    assert any(item.was_torn for item in result.truncations)
    assert (run / "ledger.jsonl").read_bytes() == full


def test_a_ledger_that_does_not_belong_to_the_tape_is_refused_not_patched(tmp_path, config, tiers) -> None:
    run = tmp_path / "run"
    drive(run, config, tiers)
    corrupted = (run / "ledger.jsonl").read_bytes().replace(b'"100000.1"', b'"999999.9"', 1)
    (run / "ledger.jsonl").write_bytes(corrupted)

    with pytest.raises(RecoveryRefused) as refused:
        recover(run, config, tiers)
    assert refused.value.code == "LEDGER_DIVERGENCE"


def test_a_ledger_from_another_schema_version_is_refused(tmp_path, config, tiers) -> None:
    run = tmp_path / "run"
    drive(run, config, tiers)
    lines = (run / "ledger.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["ledger_schema_version"] = "crypto.paper.ledger.v0"
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    (run / "ledger.jsonl").write_text("\n".join(lines) + "\n")

    with pytest.raises(RecoveryRefused) as refused:
        recover(run, config, tiers)
    assert refused.value.code == "LEDGER_SCHEMA_MISMATCH"
    assert LEDGER_SCHEMA_VERSION in str(refused.value)


def test_an_empty_run_directory_starts_fresh_rather_than_failing(tmp_path, config, tiers) -> None:
    engine, result = recover(tmp_path / "fresh", config, tiers)
    assert result.restored is False and result.tape_records == 0
    assert engine.account.position.is_flat


def test_truncation_leaves_a_well_formed_file_alone(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n')
    report = truncate_to_last_complete_line(path)
    assert report.was_torn is False and path.read_bytes() == b'{"a":1}\n{"a":2}\n'


def test_the_session_reports_what_it_restored_so_the_screen_can_say_so(tmp_path, config, tiers) -> None:
    root = tmp_path / "runs"
    first = PaperSession(config=config, tiers=tiers, root=root)
    assert first.recovery is not None and first.recovery.restored is False
    first.start(1_000)
    first.observe(quote(1_000), force=True)
    first.command({"command": "ORDER", "ts_ms": 1_100, "side": "LONG", "qty": "0.010",
                   "intent": "OPEN", "request_id": "o1"}, quote=quote(1_100))

    second = PaperSession(config=config, tiers=tiers, root=root)
    assert second.recovery is not None and second.recovery.restored is True
    assert second.is_started is True
    assert second.started_at_ms == 1_000
    assert second.engine.account.position.signed_qty == D("0.010")
    view = second.snapshot()["recovery"]
    assert view["restored"] is True
    assert view["position_signed_qty"] == "0.010"
    assert view["ledger_offset_bytes"] > 0
    assert view["ledger_schema_version"] == LEDGER_SCHEMA_VERSION


def test_a_restored_session_keeps_appending_to_the_same_files(tmp_path, config, tiers) -> None:
    root = tmp_path / "runs"
    first = PaperSession(config=config, tiers=tiers, root=root)
    first.start(1_000)
    first.observe(quote(1_000), force=True)
    first.command({"command": "ORDER", "ts_ms": 1_100, "side": "LONG", "qty": "0.010",
                   "intent": "OPEN", "request_id": "o1"}, quote=quote(1_100))
    tape_before = len(first.tape)

    second = PaperSession(config=config, tiers=tiers, root=root)
    second.command({"command": "ORDER", "ts_ms": 2_100, "side": "LONG", "qty": "0.010",
                    "intent": "CLOSE", "request_id": "c1"},
                   quote=quote(2_100, bid="100500.0", ask="100500.1"))
    assert second.engine.account.position.is_flat
    # A live session does not keep its records in memory, so the count and the file are what
    # "kept appending" has to be read from.
    assert len(second.tape) > tape_before
    assert len(InputTape.read(root / config.run_id / "input.jsonl").records) == len(second.tape)

    third = PaperSession(config=config, tiers=tiers, root=root)
    assert third.engine.account.position.is_flat
    assert third.engine.account.realized_pnl == second.engine.account.realized_pnl
    assert third.engine.ledger.bytes() == (root / config.run_id / "ledger.jsonl").read_bytes()
