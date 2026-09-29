"""Determinism and point-in-time discipline for historical replay.

These are the properties D5 will lean on. If any of them stops holding, a research result
stops meaning anything, so they are pinned rather than assumed.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.paper.historical import HistoricalTapeBuilder, as_tape_records
from app.crypto.paper.ledger import InputKind
from app.crypto.paper.replay import ACCELERATED, REALTIME, STEP, ReplayDriver, replay_records
from tests.crypto.conftest import make_config, quote

D = Decimal
ROOT = Path("data/runtime/crypto/BTCUSDT/historical")
RESEARCH_START_MS = 1_612_137_600_000        # 2021-02-01T00:00:00Z


def newest(name: str) -> Path:
    files = sorted((ROOT / name).glob("*.jsonl"))
    if not files:
        pytest.skip(f"no D2 {name} dataset in this workspace")
    return files[-1]


@pytest.fixture
def builder() -> HistoricalTapeBuilder:
    return HistoricalTapeBuilder(
        kline_path=newest("kline_1m"), mark_path=newest("mark_1m"),
        funding_path=newest("funding"), index_path=newest("index_1m"),
        open_interest_path=newest("open_interest_5m"))


@pytest.fixture
def window(builder: HistoricalTapeBuilder) -> list[dict]:
    return builder.build(start_ms=RESEARCH_START_MS, end_ms=RESEARCH_START_MS + 60 * 60_000)


def commands(start_ms: int) -> list[dict]:
    return [
        {"command": "START", "ts_ms": start_ms},
        {"command": "SET_LEVERAGE", "ts_ms": start_ms, "leverage": "5"},
        {"command": "ORDER", "ts_ms": start_ms + 10 * 60_000, "side": "LONG", "qty": "0.010",
         "intent": "OPEN", "request_id": "o1", "reason": "MANUAL"},
        {"command": "ORDER", "ts_ms": start_ms + 30 * 60_000, "side": "LONG", "qty": "0.010",
         "intent": "CLOSE", "request_id": "c1", "reason": "MANUAL"},
    ]


# ------------------------------------------------------------------ determinism

def test_the_same_tape_replays_to_the_same_ledger_every_time(window, tiers) -> None:
    records = as_tape_records(window, commands(RESEARCH_START_MS))
    runs = [replay_records(make_config(), tiers, records).ledger.bytes() for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert len(runs[0]) > 0


def test_pacing_mode_cannot_change_the_result(window, tiers) -> None:
    records = as_tape_records(window, commands(RESEARCH_START_MS))
    ledgers = {}
    for mode in (STEP, ACCELERATED, REALTIME):
        driver = ReplayDriver(config=make_config(), tiers=tiers, records=records, mode=mode,
                              speed=D("1000000"), sleeper=lambda _seconds: None)
        driver.run()
        ledgers[mode] = driver.engine.ledger.bytes()
    # The engine's clock is the tape; wall clock is only how fast records are handed over.
    assert ledgers[STEP] == ledgers[ACCELERATED] == ledgers[REALTIME]


def test_stepping_one_record_at_a_time_matches_running_straight_through(window, tiers) -> None:
    records = as_tape_records(window, commands(RESEARCH_START_MS))
    stepped = ReplayDriver(config=make_config(), tiers=tiers, records=records, mode=STEP)
    while not stepped.finished:
        stepped.step(1)
    straight = replay_records(make_config(), tiers, records)
    assert stepped.engine.ledger.bytes() == straight.ledger.bytes()


def test_a_tape_written_to_disk_reproduces_the_same_ledger(window, tiers, tmp_path) -> None:
    from app.crypto.paper.ledger import InputTape
    from app.crypto.paper.replay import write_tape

    records = as_tape_records(window, commands(RESEARCH_START_MS))
    original = replay_records(make_config(), tiers, records).ledger.bytes()
    write_tape(records, tmp_path / "input.jsonl")
    reloaded = replay_records(make_config(), tiers,
                              InputTape.read(tmp_path / "input.jsonl").records).ledger.bytes()
    assert reloaded == original


# ------------------------------------------------------------------ point in time

def test_records_are_handed_over_in_non_decreasing_time_order(window) -> None:
    records = as_tape_records(window, commands(RESEARCH_START_MS))
    stamps = [int(record["payload"]["ts_ms"]) for record in records]
    assert stamps == sorted(stamps)


def test_the_engine_never_sees_a_price_stamped_after_the_command_it_is_filling(window, tiers) -> None:
    records = as_tape_records(window, commands(RESEARCH_START_MS))
    driver = ReplayDriver(config=make_config(), tiers=tiers, records=records, mode=STEP)
    while not driver.finished:
        record = driver.records[driver.cursor]
        driver.step(1)
        engine_quote = driver.engine.quote
        if engine_quote is not None:
            # A quote later than the record just applied would be a look into the future.
            assert engine_quote.ts_ms <= int(record["payload"]["ts_ms"])


def test_every_fill_is_priced_at_or_before_its_own_timestamp(window, tiers) -> None:
    records = as_tape_records(window, commands(RESEARCH_START_MS))
    engine = replay_records(make_config(), tiers, records)
    fills = [event for event in engine.ledger.events if event["event_type"] == "FILL"]
    assert fills
    for fill in fills:
        assert int(fill["ts_ms"]) <= RESEARCH_START_MS + 60 * 60_000


def test_funding_on_a_record_is_never_a_rate_settled_later(builder) -> None:
    window = builder.build(start_ms=RESEARCH_START_MS, end_ms=RESEARCH_START_MS + 30 * 60_000)
    funding_rows = sorted(
        (int(json.loads(line)["timestamp_ms"]), json.loads(line)["funding_rate"])
        for line in newest("funding").open()
        if line.strip() and RESEARCH_START_MS - 86_400_000 <= int(json.loads(line)["timestamp_ms"])
        <= RESEARCH_START_MS + 30 * 60_000)
    for record in window:
        if record["funding_rate"] is None:
            continue
        expected = [rate for ts, rate in funding_rows if ts <= int(record["bar_ts_ms"])]
        assert record["funding_rate"] == str(D(expected[-1]))


def test_open_interest_is_the_last_reading_at_or_before_the_bar(builder) -> None:
    window = builder.build(start_ms=RESEARCH_START_MS, end_ms=RESEARCH_START_MS + 30 * 60_000)
    readings = sorted(
        (int(json.loads(line)["timestamp_ms"]), json.loads(line)["open_interest"])
        for line in newest("open_interest_5m").open()
        if line.strip() and RESEARCH_START_MS - 3_600_000 <= int(json.loads(line)["timestamp_ms"])
        <= RESEARCH_START_MS + 30 * 60_000)
    assert readings
    for record in window:
        if record["open_interest"] is None:
            continue
        expected = [value for ts, value in readings if ts <= int(record["bar_ts_ms"])]
        assert record["open_interest"] == expected[-1]


def test_index_and_mark_come_from_their_own_series_not_from_the_trade_price(builder) -> None:
    window = builder.build(start_ms=RESEARCH_START_MS, end_ms=RESEARCH_START_MS + 10 * 60_000)
    assert window
    assert all(record["index_price"] is not None for record in window)
    # If any of the three were the same series the replay would be valuing positions with the
    # wrong number, which is the failure D2 spent its effort making impossible.
    assert any(record["index_price"] != record["mark_price"] for record in window)
    assert any(record["mark_price"] != record["last_price"] for record in window)


def test_the_window_never_reaches_outside_the_bounds_it_was_given(builder) -> None:
    start = RESEARCH_START_MS + 120 * 60_000
    end = start + 20 * 60_000
    window = builder.build(start_ms=start, end_ms=end)
    assert window
    assert all(start <= int(record["bar_ts_ms"]) <= end for record in window)


def test_a_historical_record_is_always_marked_as_a_modelled_book(window) -> None:
    # A fill from a synthesised book is model output, and nothing downstream may forget that.
    assert window and all(record["book_synthetic"] is True for record in window)
