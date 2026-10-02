"""Persistent PAPER-only C1 automation over the shared PaperSession.

The controller never imports the Binance package and never reads C1x.  C1 supplies entry
identity and the official four-hour timestamp; PaperEngine remains the only execution path.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from . import sizing
from .book import Quote
from ..terminal.session import PaperSession

AUTO_SOURCE = "PAPER_C1_AUTO"
MANUAL_SOURCE = "PAPER_MANUAL"
AUTO_4H_EXIT = "AUTO_4H_EXIT"
MANUAL_CLOSE_DURING_AUTO = "MANUAL_CLOSE_DURING_AUTO"
AUTO_TO_MANUAL_CLOSE = "AUTO_TO_MANUAL_CLOSE"
STATE_FILE = "c1_auto_state.json"


@dataclass
class AutoState:
    enabled: bool = False
    active_signal_id: str | None = None
    active_trade_id: str | None = None
    enabled_at: int | None = None
    disabled_at: int | None = None
    last_seen_signal_id: str | None = None
    last_seen_signal_at: int | None = None
    active_benchmark_at: int | None = None

    def view(self) -> dict[str, Any]:
        return {**self.__dict__, "source": AUTO_SOURCE, "leverage": "10"}


class C1AutoController:
    def __init__(self, *, session: PaperSession) -> None:
        # AUTO is a control/origin flag on the one PAPER account, never another run.
        self.session = session
        self.root = session.run_dir
        self.state_path = self.root / STATE_FILE
        self.state = self._load()
        self._recover_active_link()

    def _recover_active_link(self) -> None:
        """Repair the narrow crash window between a durable fill and state-file replace."""
        position = self.session.engine.account.position
        if position.is_flat or self.state.active_signal_id is not None:
            return
        opened = next((event for event in reversed(self.session.engine.ledger.events)
                       if event.get("event_type") == "POSITION_OPEN"
                       and event.get("reason") == AUTO_SOURCE), None)
        request_id = str((opened or {}).get("request_id", ""))
        prefix = "c1-auto-open-"
        if request_id.startswith(prefix):
            self.state.active_signal_id = request_id[len(prefix):]
            self.state.active_trade_id = request_id
            self.state.last_seen_signal_id = self.state.active_signal_id
            self.state.last_seen_signal_at = int((opened or {}).get("ts_ms", 0))
            self._save()

    def _load(self) -> AutoState:
        if not self.state_path.exists():
            return AutoState()
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return AutoState(**{key: raw.get(key) for key in AutoState.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return AutoState()

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root,
                                             prefix=".auto-", delete=False)
        try:
            json.dump(self.state.view(), handle, sort_keys=True)
            handle.flush(); os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, self.state_path)

    def observe(self, quote: Quote) -> None:
        if not self.session.is_started:
            self.session.start(quote.ts_ms)
        self.session.observe(quote)

    def enable(self, quote: Quote) -> dict[str, Any]:
        self.observe(quote)
        if self.state.enabled:
            return self.view()
        self.state.enabled = True
        self.state.enabled_at = quote.ts_ms
        self.state.disabled_at = None
        self._save()
        return self.view()

    def close(self, quote: Quote, reason: str) -> bool:
        self.observe(quote)
        position = self.session.engine.account.position
        if position.is_flat:
            self.state.active_signal_id = None
            self.state.active_trade_id = None
            self._save()
            return True
        result = self.session.command({"command": "ORDER", "ts_ms": quote.ts_ms,
            "side": position.side, "qty": str(position.abs_qty), "intent": "CLOSE",
            "request_id": f"c1-auto-close-{quote.ts_ms}", "reason": reason}, quote=None)
        if result["rejection"] is not None:
            return False
        self.state.active_signal_id = None
        self.state.active_trade_id = None
        self.state.active_benchmark_at = None
        self._save()
        return True

    def owns_position(self) -> bool:
        """Ownership is the origin of the current flat-to-open lifecycle, not AUTO state."""
        if self.session.engine.account.position.is_flat:
            return False
        for event in reversed(self.session.engine.ledger.events):
            if event.get("event_type") == "POSITION_OPEN":
                return event.get("reason") == AUTO_SOURCE
            if event.get("event_type") == "POSITION_CLOSE":
                return False
        return False

    def disable(self, quote: Quote) -> tuple[bool, dict[str, Any]]:
        if not self.state.enabled:
            return True, self.view()
        if self.owns_position() and not self.close(quote, AUTO_TO_MANUAL_CLOSE):
            return False, self.view()
        self.state.enabled = False
        self.state.disabled_at = quote.ts_ms
        self._save()
        return True, self.view()

    def reconcile(self, quote: Quote, signals: Iterable[Any]) -> None:
        self.observe(quote)
        if not self.state.enabled:
            return
        ordered = sorted((row for row in signals
                          if self.state.enabled_at is not None
                          and row.triggered_at_ms >= self.state.enabled_at),
                         key=lambda row: (row.triggered_at_ms, row.signal_id))
        unseen = [row for row in ordered
                  if self.state.last_seen_signal_at is None
                  or (row.triggered_at_ms, row.signal_id)
                  > (self.state.last_seen_signal_at, self.state.last_seen_signal_id or "")]
        position = self.session.engine.account.position
        if not position.is_flat:
            # A MANUAL position remains MANUAL. Consume all signals seen while it is open so
            # flattening it cannot cause a stale C1 entry; only the next C1 may enter.
            if not self.owns_position():
                if unseen:
                    last = unseen[-1]
                    self.state.last_seen_signal_id = last.signal_id
                    self.state.last_seen_signal_at = last.triggered_at_ms
                    self._save()
                return
            active = next((row for row in ordered
                           if row.signal_id == self.state.active_signal_id), None)
            benchmark = (active.planned_exit_at_ms if active is not None
                         else self.state.active_benchmark_at)
            if unseen:
                last = unseen[-1]
                self.state.last_seen_signal_id = last.signal_id
                self.state.last_seen_signal_at = last.triggered_at_ms
                self._save()
            if benchmark is not None and quote.ts_ms >= benchmark:
                self.close(quote, AUTO_4H_EXIT)
            return
        if not unseen:
            return
        signal = unseen[0]
        last = unseen[-1]
        self.state.last_seen_signal_id = last.signal_id
        self.state.last_seen_signal_at = last.triggered_at_ms
        if self.session.engine.leverage != Decimal(10):
            self.session.command({"command": "SET_LEVERAGE", "ts_ms": quote.ts_ms,
                                  "leverage": "10"}, quote=None)
        quote_size = sizing.max_entry(self.session.engine, "LONG")
        if not quote_size.feasible or quote_size.qty <= 0:
            self._save()
            return
        result = self.session.command({"command": "ORDER", "ts_ms": quote.ts_ms,
            "side": "LONG", "qty": str(quote_size.qty), "intent": "OPEN",
            "request_id": f"c1-auto-open-{signal.signal_id}", "reason": AUTO_SOURCE}, quote=None)
        if result["rejection"] is None:
            self.state.active_signal_id = signal.signal_id
            self.state.active_trade_id = f"c1-auto-open-{signal.signal_id}"
            self.state.active_benchmark_at = signal.planned_exit_at_ms
        self._save()

    def view(self) -> dict[str, Any]:
        position = self.session.engine.account.position
        return {**self.state.view(), "has_position": not position.is_flat,
                "entry_at": position.opened_ts_ms,
                "benchmark_at": self.state.active_benchmark_at}
