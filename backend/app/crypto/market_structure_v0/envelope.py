"""The record envelope, the session identity, and the decimal discipline.

One rule governs every number that leaves this package: a price, a quantity or anything computed
from them is a **decimal string**, and it never passes through a binary float. Counts and times
are integers. A value that was not observed is `null` and is never a zero, because a zero that
means "nothing seen" is indistinguishable from a zero that means "nothing there", and the whole
point of the coverage vocabulary is to keep those apart.

`seq` is strictly increasing within a session and is assigned at the moment a record is built,
so the envelope order is the order the collector decided things, independently of what the
writer later does with its buffers. `receive_ms` is wall-clock receipt and is what a human
reads; `mono_ns` is the monotonic capture time and is what every age, window and freshness
decision is actually computed from, because wall clocks step and monotonic clocks do not.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from . import COLLECTOR_VERSION, EXCHANGE, SYMBOL, VERSION


def provider_decimal(value: Any) -> str:
    """Validate a number the exchange sent and keep its own text.

    The provider's formatting is evidence: `"0.001"` and `"1E-3"` are the same number but not the
    same frame. Only an exact zero is canonicalized, so a zero-delete reads the same whatever
    Binance wrote.
    """
    if isinstance(value, float):
        value = repr(value)
    if not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"not a decimal value: {value!r}")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    return "0" if parsed == 0 else str(value)


def decimal_out(value: Decimal | None) -> str | None:
    """Canonical plain-decimal text for a value this package computed.

    Exponent notation is removed deliberately: these strings are read by humans and by a later
    Parquet conversion, and `1E+2` round-trips as a number but not as a column of prices.
    """
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def ratio_out(numerator: Decimal, denominator: Decimal, decimals: int) -> str | None:
    """A normalized ratio, or `None` when the denominator is zero.

    A zero denominator is the empty case, not a zero ratio, so it is null. The division is
    inexact, which is why the number of decimals is fixed here rather than left to context.
    """
    if denominator == 0:
        return None
    quantum = Decimal(1).scaleb(-decimals)
    return decimal_out((numerator / denominator).quantize(quantum))


def now_ms() -> int:
    return int(time.time() * 1000)


def now_ns() -> int:
    return time.monotonic_ns()


def ms_from_ns(delta_ns: int) -> int:
    return delta_ns // 1_000_000


@dataclass
class Session:
    """Identity and the strictly increasing sequence, shared by every record of one process."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_ms: int = field(default_factory=now_ms)
    started_ns: int = field(default_factory=now_ns)
    _seq: int = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @property
    def seq(self) -> int:
        return self._seq

    def record(self, kind: str, payload: dict[str, Any], *, receive_ms: int | None = None,
               mono_ns: int | None = None, connection_id: str | None = None) -> dict[str, Any]:
        """Wrap a payload in the contract envelope.

        `receive_ms`/`mono_ns` are passed in by the caller whenever the record describes something
        that arrived, so the stored time is when the frame was received rather than when the
        record happened to be built. Only records about the collector itself fall back to now.
        """
        return {
            "version": VERSION,
            "collector_version": COLLECTOR_VERSION,
            "exchange": EXCHANGE,
            "symbol": SYMBOL,
            "session_id": self.session_id,
            "seq": self.next_seq(),
            "kind": kind,
            "receive_ms": now_ms() if receive_ms is None else receive_ms,
            "mono_ns": now_ns() if mono_ns is None else mono_ns,
            "connection_id": connection_id,
            "payload": payload,
        }

    def elapsed_s(self, at_ns: int | None = None) -> float:
        return ((now_ns() if at_ns is None else at_ns) - self.started_ns) / 1e9


__all__ = ["Session", "provider_decimal", "decimal_out", "ratio_out", "now_ms", "now_ns",
           "ms_from_ns"]
