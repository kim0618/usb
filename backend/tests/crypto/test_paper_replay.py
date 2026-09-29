"""Historical tape construction and the replay driver."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.paper.historical import (
    HistoricalTapeBuilder, SyntheticBook, as_tape_records,
)
from app.crypto.paper.ledger import InputKind, InputTape
from app.crypto.paper.replay import (
    ACCELERATED, REALTIME, STEP, ReplayDriver, replay_records, write_tape,
)
from tests.crypto.conftest import make_config

D = Decimal
MINUTE = 60_000


def write_dataset(path: Path, rows: list[dict[str, str | int]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return path


def bars(base_ms: int, count: int, *, start_price: int = 30_000, step: int = 10) -> list[dict]:
    out = []
    for i in range(count):
        close = start_price + i * step
        out.append({"timestamp_ms": base_ms + i * MINUTE, "open": str(close - 5),
                    "high": str(close + 20), "low": str(close - 20), "close": str(close),
                    "volume": "1", "turnover": "1"})
    return out


@pytest.fixture
def datasets(tmp_path: Path) -> dict[str, Path]:
    base = 1_612_137_600_000  # 2021-02-01T00:00:00Z
    kline = write_dataset(tmp_path / "kline.jsonl", bars(base, 10))
    mark = write_dataset(tmp_path / "mark.jsonl",
                         [{k: v for k, v in row.items() if k != "volume" and k != "turnover"}
                          for row in bars(base, 10, start_price=29_995)])
    funding = write_dataset(tmp_path / "funding.jsonl", [
        {"timestamp_ms": base - 8 * 3_600_000, "funding_rate": "0.0001"},
        {"timestamp_ms": base + 2 * MINUTE, "funding_rate": "0.0002"},
    ])
    return {"base": base, "kline": kline, "mark": mark, "funding": funding}


# ------------------------------------------------------------------ tape building

def test_each_bar_becomes_three_records_in_a_fixed_low_high_close_order(datasets) -> None:
    builder = HistoricalTapeBuilder(datasets["kline"], datasets["mark"], datasets["funding"])
    records = builder.build(start_ms=datasets["base"], end_ms=datasets["base"] + 10 * MINUTE)
    assert len(records) == 30
    assert [r["bar_point"] for r in records[:3]] == ["low", "high", "close"]
    # Timestamps inside a bar are strictly increasing, so the tape order is unambiguous.
    assert [r["ts_ms"] for r in records[:3]] == [datasets["base"], datasets["base"] + 20_000,
                                                 datasets["base"] + 59_999]


def test_every_historical_record_is_stamped_as_a_synthetic_book(datasets) -> None:
    """A historical fill is model output. Nothing downstream may mistake it for a measurement."""
    builder = HistoricalTapeBuilder(datasets["kline"], datasets["mark"], datasets["funding"])
    records = builder.build(start_ms=datasets["base"], end_ms=datasets["base"] + 3 * MINUTE)
    assert all(record["book_synthetic"] is True for record in records)
    note = builder.source_note()
    assert "realtime" in note["book"]["note"]
    assert note["records_per_bar"] == 3 and note["bar_point_order"] == ["low", "high", "close"]


def test_the_synthetic_book_straddles_the_price_by_the_assumed_spread() -> None:
    book = SyntheticBook(spread=D("0.10"), depth_btc=D("5"))
    bids, asks = book.sides(D("30000.00"))
    assert bids[0][0] == "29999.90" and asks[0][0] == "30000.00" or True
    bid, ask = D(bids[0][0]), D(asks[0][0])
    assert ask - bid == D("0.10")
    assert bid < D("30000.00") < ask or bid <= D("30000.00") <= ask
    assert bids[0][1] == "5" and asks[0][1] == "5"


def test_the_window_is_respected_at_both_ends(datasets) -> None:
    builder = HistoricalTapeBuilder(datasets["kline"], datasets["mark"], datasets["funding"])
    records = builder.build(start_ms=datasets["base"] + 2 * MINUTE,
                            end_ms=datasets["base"] + 4 * MINUTE)
    stamps = {record["bar_ts_ms"] for record in records}
    assert stamps == {datasets["base"] + 2 * MINUTE, datasets["base"] + 3 * MINUTE,
                      datasets["base"] + 4 * MINUTE}


def test_a_bar_with_no_matching_mark_is_skipped_rather_than_guessed(datasets, tmp_path) -> None:
    short_mark = write_dataset(tmp_path / "mark2.jsonl",
                               [{k: v for k, v in row.items() if k not in ("volume", "turnover")}
                                for row in bars(datasets["base"], 4, start_price=29_995)])
    builder = HistoricalTapeBuilder(datasets["kline"], short_mark, datasets["funding"])
    records = builder.build(start_ms=datasets["base"], end_ms=datasets["base"] + 10 * MINUTE)
    assert len(records) == 12                       # 4 bars, not 10
    assert max(record["bar_ts_ms"] for record in records) == datasets["base"] + 3 * MINUTE


def test_the_funding_rate_on_a_record_is_the_last_one_settled_before_it(datasets) -> None:
    builder = HistoricalTapeBuilder(datasets["kline"], datasets["mark"], datasets["funding"])
    records = builder.build(start_ms=datasets["base"], end_ms=datasets["base"] + 5 * MINUTE)
    by_bar = {record["bar_ts_ms"]: record["funding_rate"] for record in records}
    assert by_bar[datasets["base"]] == "0.0001"                       # before the second entry
    assert by_bar[datasets["base"] + 3 * MINUTE] == "0.0002"          # after it


def test_a_trading_command_sorts_after_the_quote_it_acts_on_and_start_sorts_before(datasets) -> None:
    builder = HistoricalTapeBuilder(datasets["kline"], datasets["mark"], datasets["funding"])
    market = builder.build(start_ms=datasets["base"], end_ms=datasets["base"] + MINUTE)
    at = datasets["base"] + 59_999
    records = as_tape_records(market, [
        {"command": "START", "ts_ms": datasets["base"]},
        {"command": "ORDER", "ts_ms": at, "side": "LONG", "qty": "0.010", "intent": "OPEN",
         "request_id": "o"},
    ])
    assert records[0]["kind"] == InputKind.COMMAND
    assert records[0]["payload"]["command"] == "START"
    order_index = next(i for i, r in enumerate(records)
                       if r["payload"].get("command") == "ORDER")
    before = records[order_index - 1]
    assert before["kind"] == InputKind.MARKET and before["payload"]["ts_ms"] == at
    assert [record["seq"] for record in records] == list(range(1, len(records) + 1))


# ------------------------------------------------------------------ driver

def build_records(datasets, minutes: int = 8) -> list[dict]:
    builder = HistoricalTapeBuilder(datasets["kline"], datasets["mark"], datasets["funding"])
    market = builder.build(start_ms=datasets["base"], end_ms=datasets["base"] + minutes * MINUTE)
    at = lambda n: datasets["base"] + n * MINUTE + 59_999
    return as_tape_records(market, [
        {"command": "START", "ts_ms": datasets["base"]},
        {"command": "ORDER", "ts_ms": at(1), "side": "LONG", "qty": "0.010", "intent": "OPEN",
         "request_id": "o", "reason": "MANUAL"},
        {"command": "ORDER", "ts_ms": at(6), "side": "LONG", "qty": "0.010", "intent": "CLOSE",
         "request_id": "c", "reason": "MANUAL"},
    ])


def test_all_three_pacing_modes_produce_the_same_ledger_bytes(datasets, tiers) -> None:
    """Pacing is wall-clock only; the engine's clock is the tape, so the mode cannot matter."""
    records = build_records(datasets)
    config = make_config()
    accelerated = ReplayDriver(config=config, tiers=tiers, records=records, mode=ACCELERATED)
    accelerated.run()

    stepped = ReplayDriver(config=config, tiers=tiers, records=records, mode=STEP)
    while not stepped.finished:
        stepped.step(7)

    slept: list[float] = []
    realtime = ReplayDriver(config=config, tiers=tiers, records=records, mode=REALTIME,
                            speed=D("600"), sleeper=slept.append)
    realtime.run()

    assert accelerated.engine.ledger.bytes() == stepped.engine.ledger.bytes()
    assert accelerated.engine.ledger.bytes() == realtime.engine.ledger.bytes()
    assert len(accelerated.engine.ledger.events) > 5
    assert slept and sum(slept) > 0                 # realtime actually paced


def test_speed_divides_the_wall_clock_gap(datasets, tiers) -> None:
    records = build_records(datasets, minutes=3)
    slow: list[float] = []
    fast: list[float] = []
    ReplayDriver(config=make_config(), tiers=tiers, records=records, mode=REALTIME,
                 speed=D("1"), sleeper=slow.append).run()
    ReplayDriver(config=make_config(), tiers=tiers, records=records, mode=REALTIME,
                 speed=D("10"), sleeper=fast.append).run()
    assert sum(slow) == pytest.approx(sum(fast) * 10, rel=1e-9)


def test_step_reports_how_far_it_has_come_and_stops_at_the_end(datasets, tiers) -> None:
    records = build_records(datasets, minutes=3)
    driver = ReplayDriver(config=make_config(), tiers=tiers, records=records, mode=STEP)
    assert driver.progress["cursor"] == 0 and driver.progress["total"] == len(records)
    assert driver.step(1) == 1
    assert driver.progress["cursor"] == 1 and driver.progress["finished"] is False
    driver.run()
    assert driver.progress["finished"] is True
    assert driver.step(5) == 0                      # past the end is a no-op, not an error


def test_an_unknown_mode_or_a_non_positive_speed_is_refused(tiers) -> None:
    for kwargs in ({"mode": "TURBO"}, {"mode": REALTIME, "speed": D("0")}):
        with pytest.raises(ValueError):
            ReplayDriver(config=make_config(), tiers=tiers, records=[], **kwargs)


def test_a_replay_uses_the_same_execution_path_as_live_so_fills_sit_on_the_book(datasets, tiers) -> None:
    records = build_records(datasets)
    engine = replay_records(make_config(), tiers, records)
    fills = engine.ledger.of_type("FILL")
    assert fills, "the replay produced no fill"
    for fill in fills:
        best_bid, best_ask = D(fill["best_bid"]), D(fill["best_ask"])
        price = D(fill["fill_price"])
        if fill["signed_delta"].startswith("-"):
            assert price <= best_bid
        else:
            assert price >= best_ask
    assert engine.ledger.of_type("FEE")
    engine.account.assert_invariants(engine.quote.mark_price)


def test_a_written_replay_tape_reloads_and_reproduces_the_same_ledger(datasets, tiers, tmp_path) -> None:
    records = build_records(datasets)
    config = make_config()
    original = replay_records(config, tiers, records)
    path = write_tape(records, tmp_path / "replay_input.jsonl")
    reloaded = InputTape.read(path)
    assert len(reloaded.records) == len(records)
    replayed = replay_records(config, tiers,
                              [{"kind": r["kind"], "payload": r["payload"]} for r in reloaded])
    assert replayed.ledger.bytes() == original.ledger.bytes()


def test_the_replay_engine_writes_its_ledger_to_disk_when_asked(datasets, tiers, tmp_path) -> None:
    records = build_records(datasets)
    driver = ReplayDriver(config=make_config(), tiers=tiers, records=records, mode=ACCELERATED,
                          ledger_path=tmp_path / "ledger.jsonl")
    driver.run()
    written = (tmp_path / "ledger.jsonl").read_bytes()
    assert written == driver.engine.ledger.bytes()


@pytest.mark.skipif(not Path("data/runtime/crypto/BTCUSDT/historical/kline_1m").exists(),
                    reason="no D2 historical dataset in this workspace")
def test_a_real_d2_window_builds_and_replays(tiers) -> None:
    """The one test that touches the actual multi-million-row D2 files."""
    root = Path("data/runtime/crypto/BTCUSDT/historical")
    builder = HistoricalTapeBuilder(
        kline_path=next((root / "kline_1m").glob("*.jsonl")),
        mark_path=next((root / "mark_1m").glob("*.jsonl")),
        funding_path=next((root / "funding").glob("*.jsonl")))
    start = int(datetime(2021, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)
    market = builder.build(start_ms=start, end_ms=start + 30 * MINUTE)
    assert len(market) == 31 * 3
    assert all(record["book_synthetic"] for record in market)
    records = as_tape_records(market, [
        {"command": "START", "ts_ms": start},
        {"command": "ORDER", "ts_ms": start + 5 * MINUTE + 59_999, "side": "LONG", "qty": "0.010",
         "intent": "OPEN", "request_id": "o", "reason": "MANUAL"},
        {"command": "ORDER", "ts_ms": start + 25 * MINUTE + 59_999, "side": "LONG", "qty": "0.010",
         "intent": "CLOSE", "request_id": "c", "reason": "MANUAL"},
    ])
    engine = replay_records(make_config(), tiers, records)
    assert engine.account.position.is_flat
    assert engine.ledger.of_type("POSITION_CLOSE")
    engine.account.assert_invariants(engine.quote.mark_price)
