"""Live session: the seam between a moving market and a deterministic engine.

Everything the engine sees passes through `observe` or `command`, and both write to the input
tape before the engine is touched. That ordering is what makes the tape complete, which is
what makes the replay proof meaningful rather than decorative.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..paper.book import Quote
from ..paper.config import PaperRunConfig
from ..paper.engine import PaperEngine, apply_tape_record, quote_to_payload
from ..paper.instrument import RiskTierTable
from ..paper.ledger import EventType, InputKind, InputTape, Ledger
from ..paper.persistence import RecoveryResult, recover
from ..paper.segments import DEFAULT_SEGMENT_RECORDS, SegmentedTape, load_manifests

MARKET_RECORD_INTERVAL_MS = 1_000


@dataclass
class PaperSession:
    config: PaperRunConfig
    tiers: RiskTierTable
    root: Path
    engine: PaperEngine = None  # type: ignore[assignment]
    tape: InputTape = None  # type: ignore[assignment]
    started_at_ms: int | None = None
    recovery: RecoveryResult | None = None
    segment_records: int = DEFAULT_SEGMENT_RECORDS
    compress_segments: bool = False
    segments: SegmentedTape | None = None
    _last_recorded_ms: int | None = None

    def __post_init__(self) -> None:
        run_dir = self.root / self.config.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        # Recovery first: it may truncate a torn line, so the tape must not be opened for
        # appending until the files on disk have been made consistent.
        self.engine, self.recovery = recover(run_dir, self.config, self.tiers)
        # `retain=False`: a live session appends at 1 Hz for as long as the service runs, and
        # keeping a copy of every record in memory as well as on disk is what the tape file is
        # for. Only the count is needed here, to continue the sequence and to report progress.
        self.tape = InputTape(path=run_dir / "input.jsonl", retain=False)
        if self.recovery.restored:
            summary = InputTape.scan(run_dir / "input.jsonl")
            # Only the active tape's own count; the segments' records are added below.
            self.tape.count = summary.count + sum(
                manifest.records for manifest in load_manifests(run_dir))
            # RUN_START is in the ledger, which is always loaded whole, so this survives the
            # active tape being rotated away. Scanning the tape for it used to return None
            # after a rotation, and the caller would then start the run a second time.
            self.started_at_ms = next(
                (int(event["ts_ms"]) for event in self.engine.ledger.events
                 if event["event_type"] == EventType.RUN_START), summary.first_start_ms)
            # The engine tracked this while replaying; it is the same value the tape would give.
            self._last_recorded_ms = (self.engine.last_market_ts_ms
                                      if self.engine.last_market_ts_ms is not None
                                      else summary.last_market_ms)
        self.segments = SegmentedTape(run_dir=run_dir, segment_records=self.segment_records,
                                      compress=self.compress_segments)
        (run_dir / "run_config.json").write_text(
            __import__("json").dumps(self.config.snapshot(), indent=2, sort_keys=True) + "\n")

    @property
    def is_started(self) -> bool:
        return self.started_at_ms is not None

    @property
    def run_dir(self) -> Path:
        return self.root / self.config.run_id

    def start(self, ts_ms: int | None = None) -> None:
        ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
        self._apply_command({"command": "START", "ts_ms": ts})
        self.started_at_ms = ts

    def observe(self, quote: Quote, *, force: bool = False) -> bool:
        """Record at 1 Hz, or immediately when a command is about to read the quote."""
        if not force and self._last_recorded_ms is not None:
            if quote.ts_ms - self._last_recorded_ms < MARKET_RECORD_INTERVAL_MS:
                return False
        payload = quote_to_payload(quote)
        self.tape.record(InputKind.MARKET, payload)
        apply_tape_record(self.engine, {"kind": InputKind.MARKET, "payload": payload})
        self._last_recorded_ms = quote.ts_ms
        self.maybe_rotate()
        return True

    def maybe_rotate(self) -> bool:
        """Close the active tape when it is long enough, and checkpoint at the cut.

        Rotation happens after a market record rather than after a command, so a cut never
        lands between a command and the quote it acted on. The active tape is only ever grown
        by appends, so closing it is a rename: nothing is rewritten and nothing is deleted.
        """
        if self.segments is None or not self.segments.should_rotate():
            return False
        self.segments.rotate(self.engine, tape_records=len(self.tape))
        return True

    def _apply_command(self, payload: dict[str, Any]) -> None:
        self.tape.record(InputKind.COMMAND, payload)
        apply_tape_record(self.engine, {"kind": InputKind.COMMAND, "payload": payload})

    def command(self, payload: dict[str, Any], *, quote: Quote | None = None) -> dict[str, Any]:
        """Pin the quote the command will act on, then run it through the same tape path.

        The engine call is repeated outside the tape helper only to surface the rejection to
        the caller; the ledger entry was already written by the tape path, so the HTTP error
        and the ledger never disagree.
        """
        if quote is not None:
            self.observe(quote, force=True)
        before = len(self.engine.ledger.events)
        self._apply_command(payload)
        produced = self.engine.ledger.events[before:]
        rejection = next((event for event in produced if event["event_type"] == "ORDER_REJECTED"), None)
        return {"events": produced, "rejection": rejection}

    def snapshot(self) -> dict[str, Any]:
        state = self.engine.snapshot()
        state["run_dir"] = self.run_dir.as_posix()
        state["started_at_ms"] = self.started_at_ms
        state["input_record_count"] = len(self.tape)
        state["storage"] = self.segments.storage() if self.segments is not None else None
        state["recovery"] = self.recovery.view() if self.recovery is not None else None
        state["fx"] = {"krw_per_usdt": self.config.fx.krw_per_usdt,
                       "source": self.config.fx.source, "asof_utc": self.config.fx.asof_utc}
        state["fees"] = {"version": self.config.fees.version, "taker_rate": self.config.fees.taker_rate,
                         "maker_rate": self.config.fees.maker_rate, "source": self.config.fees.source,
                         "effective_date": self.config.fees.effective_date,
                         "basis": self.config.fees.basis}
        state["slippage"] = {"model": self.config.slippage.model, "bps": self.config.slippage.bps}
        state["starting_capital_krw"] = self.config.starting_capital_krw
        return state

    def to_krw(self, usdt: Decimal) -> Decimal:
        return usdt * self.config.fx.krw_per_usdt
