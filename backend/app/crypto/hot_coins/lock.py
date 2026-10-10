"""When a new HOT scan may replace the current HOT symbol.

The rule is per symbol, not per account. BTC is the main instrument and is never the HOT
symbol, so a BTC position or a BTC order never blocks a scan. What blocks is exposure on the
*current HOT symbol*: a position, a resting order, or an order request this process has sent and
not yet seen answered. Replacing the HOT symbol under any of those would point the screen away
from an instrument that still has the operator's money in it.

V0 is read-only. Nothing in this build can open a HOT position, so the server holds no current
HOT symbol and every scan is decided `NO_ACTIVE_HOT_SYMBOL` without reading an account. The
contract and its race rule exist now so that Phase 2 plugs an account-backed `ExposureSource`
into a decision that is already written and tested, rather than inventing one next to an order
path.

Unknown is blocked. An exposure source that raises, or answers with a field it could not read,
blocks the scan: "could not tell whether the old HOT symbol is flat" must never read as "flat".
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterator, Protocol, TypeVar

ALLOWED = "ALLOWED"
BLOCKED = "BLOCKED"

NO_ACTIVE_HOT_SYMBOL = "NO_ACTIVE_HOT_SYMBOL"
HOT_SYMBOL_FLAT = "HOT_SYMBOL_FLAT"
HOT_POSITION_OPEN = "HOT_POSITION_OPEN"
HOT_OPEN_ORDER = "HOT_OPEN_ORDER"
HOT_ORDER_IN_FLIGHT = "HOT_ORDER_IN_FLIGHT"
HOT_STATE_UNKNOWN = "HOT_STATE_UNKNOWN"
HOT_STATE_CHANGED_DURING_SCAN = "HOT_STATE_CHANGED_DURING_SCAN"

#: The main instrument. Not lockable by HOT, whatever is open on it.
MAIN_SYMBOL = "BTCUSDT"

_MESSAGES = {
    NO_ACTIVE_HOT_SYMBOL: "현재 HOT 심볼이 없어 탐색할 수 있습니다.",
    HOT_SYMBOL_FLAT: "현재 HOT 심볼에 포지션·미체결·전송 중 주문이 없습니다.",
    HOT_POSITION_OPEN: "현재 HOT 심볼에 포지션이 있어 탐색할 수 없습니다.",
    HOT_OPEN_ORDER: "현재 HOT 심볼에 미체결 주문이 있어 탐색할 수 없습니다.",
    HOT_ORDER_IN_FLIGHT: "현재 HOT 심볼 주문이 전송 중이라 탐색할 수 없습니다.",
    HOT_STATE_UNKNOWN: "현재 HOT 심볼 상태를 확인하지 못해 탐색을 막았습니다.",
    HOT_STATE_CHANGED_DURING_SCAN: "탐색 중 현재 HOT 심볼 상태가 바뀌어 기존 HOT을 유지합니다.",
}


@dataclass(frozen=True)
class SymbolExposure:
    """One symbol's exposure. `None` in any field means it could not be read."""
    symbol: str
    position_amt: Decimal | None
    open_orders: int | None
    in_flight: int | None


class ExposureSource(Protocol):
    def exposure(self, symbol: str) -> SymbolExposure:
        """Exposure on exactly this symbol. May raise; a raise is read as unknown."""


@dataclass(frozen=True)
class LockDecision:
    state: str
    code: str
    symbol: str | None

    @property
    def allowed(self) -> bool:
        return self.state == ALLOWED

    @property
    def message(self) -> str:
        return _MESSAGES[self.code]

    def as_dict(self) -> dict[str, object]:
        return {"state": self.state, "code": self.code, "symbol": self.symbol,
                "message": self.message}


def scan_permission(current_hot: str | None, source: ExposureSource | None) -> LockDecision:
    """May a scan start (or its result be applied) while `current_hot` is the HOT symbol?"""
    if not current_hot or current_hot == MAIN_SYMBOL:
        return LockDecision(ALLOWED, NO_ACTIVE_HOT_SYMBOL, None)
    if source is None:
        return LockDecision(BLOCKED, HOT_STATE_UNKNOWN, current_hot)
    try:
        exposure = source.exposure(current_hot)
    except Exception:
        return LockDecision(BLOCKED, HOT_STATE_UNKNOWN, current_hot)
    if exposure.symbol != current_hot or None in (
            exposure.position_amt, exposure.open_orders, exposure.in_flight):
        return LockDecision(BLOCKED, HOT_STATE_UNKNOWN, current_hot)
    if exposure.in_flight > 0:
        return LockDecision(BLOCKED, HOT_ORDER_IN_FLIGHT, current_hot)
    if exposure.position_amt != 0:
        return LockDecision(BLOCKED, HOT_POSITION_OPEN, current_hot)
    if exposure.open_orders > 0:
        return LockDecision(BLOCKED, HOT_OPEN_ORDER, current_hot)
    return LockDecision(ALLOWED, HOT_SYMBOL_FLAT, current_hot)


T = TypeVar("T")


@dataclass(frozen=True)
class GuardedOutcome:
    """`applied` False means the caller keeps its current HOT and discards `result`."""
    applied: bool
    before: LockDecision
    after: LockDecision | None
    result: object | None

    @property
    def decision(self) -> LockDecision:
        if not self.before.allowed:
            return self.before
        assert self.after is not None
        if self.after.allowed:
            return self.after
        return LockDecision(BLOCKED, HOT_STATE_CHANGED_DURING_SCAN, self.after.symbol)


def guarded_scan(current_hot: str | None, source: ExposureSource | None,
                 scan: Callable[[], T]) -> GuardedOutcome:
    """Check, scan, check again; apply only if the old HOT symbol was flat at both ends.

    The second check is the race rule. A scan takes seconds; an order on the old HOT symbol can
    be sent from another tab or by a guard thread in that time. A result computed while the old
    symbol was flat is not permission to replace it once it is not.
    """
    before = scan_permission(current_hot, source)
    if not before.allowed:
        return GuardedOutcome(False, before, None, None)
    result = scan()
    after = scan_permission(current_hot, source)
    return GuardedOutcome(after.allowed, before, after, result if after.allowed else None)


class InFlightRegistry:
    """Order requests sent and not yet answered, per symbol. Phase 2's router and exit guard would
    share one; V0 has no order path, so nothing registers here in this build.

    A request that raised (timeout, dropped connection) has an unknown outcome: Binance may have
    filled it. It therefore stays counted until `reconciled()` is called by whoever has since
    asked Binance what happened, which keeps the HOT symbol locked in the meantime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._unresolved: dict[str, int] = {}

    @contextmanager
    def sending(self, symbol: str, *, unknown_on_error: bool = True) -> Iterator[None]:
        """`unknown_on_error=False` for a local engine (paper), whose outcome is never unknown:
        an exception there means nothing was filled, so it must not leave the symbol locked."""
        with self._lock:
            self._counts[symbol] = self._counts.get(symbol, 0) + 1
        answered = False
        try:
            yield
            answered = True
        finally:
            with self._lock:
                left = self._counts.get(symbol, 1) - 1
                if left:
                    self._counts[symbol] = left
                else:
                    self._counts.pop(symbol, None)
                if not answered and unknown_on_error:
                    self._unresolved[symbol] = self._unresolved.get(symbol, 0) + 1

    def reconciled(self, symbol: str) -> None:
        with self._lock:
            self._unresolved.pop(symbol, None)

    def count(self, symbol: str) -> int:
        with self._lock:
            return self._counts.get(symbol, 0) + self._unresolved.get(symbol, 0)


__all__ = [
    "ALLOWED", "BLOCKED", "ExposureSource", "GuardedOutcome", "InFlightRegistry", "LockDecision",
    "SymbolExposure", "guarded_scan", "scan_permission",
]
