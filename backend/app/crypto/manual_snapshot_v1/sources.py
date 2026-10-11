"""Readers for S1 (terminal fill cache) and S2 (LIVE audit mirror). Read only, never locked, never
rewritten. Contract section 1."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

SYMBOL = "BTCUSDT"
MANUAL_PREFIX = "usbm-"
AUTO_EXIT_PERMISSION = "AUTO_EXIT_CLOSE_ONLY"


@dataclass(frozen=True)
class Fill:
    id: int
    order_id: int
    symbol: str
    side: str            # BUY / SELL
    price: Decimal
    qty: Decimal
    realized_pnl: Decimal
    commission: Decimal
    time_ms: int
    client_order_id: str | None

    @property
    def signed_qty(self) -> Decimal:
        return self.qty if self.side == "BUY" else -self.qty


@dataclass
class S1:
    fills: list[Fill]
    funding: list[tuple[int, Decimal]]      # (time_ms, income)
    last_calculated_ms: int | None
    classification_complete: bool
    error: str | None = None


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def read_s1(path: Path, symbol: str = SYMBOL) -> S1:
    """The terminal's cache. A missing or unreadable file is an empty S1 with the reason; the
    caller writes nothing new from it (and deletes nothing it already wrote)."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return S1([], [], None, False, "S1_MISSING")
    except (OSError, ValueError) as exc:
        return S1([], [], None, False, f"S1_UNREADABLE:{type(exc).__name__}")
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return S1([], [], None, False, "S1_UNSUPPORTED_VERSION")
    fills: dict[int, Fill] = {}
    for row in raw.get("trades") or []:
        try:
            if str(row.get("symbol") or symbol) != symbol:
                continue
            f = Fill(int(row["id"]), int(row["order_id"]), str(row.get("symbol") or symbol),
                     str(row["side"]), _dec(row["price"]), _dec(row["qty"]),
                     _dec(row.get("realized_pnl", "0")), _dec(row.get("commission", "0")),
                     int(row["time_ms"]), row.get("client_order_id"))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
        fills.setdefault(f.id, f)          # a duplicated trade id is one fill
    funding = []
    for row in raw.get("funding") or []:
        try:
            if str(row.get("symbol") or symbol) != symbol:
                continue
            funding.append((int(row["time_ms"]), _dec(row["income"])))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    ordered = sorted(fills.values(), key=lambda f: (f.time_ms, f.id))
    last = raw.get("last_calculated_ms")
    return S1(ordered, sorted(set(funding)), int(last) if last is not None else None,
              bool(raw.get("classification_complete")))


@dataclass
class S2:
    first_ms: int | None = None
    auto_exit_clients: set[str] = field(default_factory=set)
    sent_ms: dict[str, int] = field(default_factory=dict)
    intents: list[tuple[int, str, str]] = field(default_factory=list)   # (ts, side, intent) stage PLAN
    leverage: list[tuple[int, str]] = field(default_factory=list)       # (ts, leverage) results
    lines: int = 0


_WANTED = (b"LIVE_ORDER_SENT", b"LIVE_ORDER_INTENT", b"LIVE_LEVERAGE_RESULT")


def _ingest(out: "S2", line: bytes, symbol: str) -> None:
    out.lines += 1
    if out.first_ms is None:
        try:
            out.first_ms = int(json.loads(line)["ts_ms"])
        except (ValueError, KeyError, TypeError):
            pass
    if not any(w in line for w in _WANTED):
        return
    try:
        r = json.loads(line)
    except ValueError:
        return
    et, ts = r.get("event_type"), r.get("ts_ms")
    if not isinstance(ts, int):
        return
    if et == "LIVE_ORDER_SENT":
        cid = r.get("client_order_id")
        params = r.get("params") or {}
        if params.get("symbol") not in (None, symbol) or not cid:
            return
        out.sent_ms.setdefault(cid, ts)
        if r.get("permission") == AUTO_EXIT_PERMISSION:
            out.auto_exit_clients.add(cid)
    elif et == "LIVE_ORDER_INTENT" and r.get("stage") == "PLAN":
        if r.get("symbol") not in (None, symbol):
            return
        out.intents.append((ts, str(r.get("side") or ""), str(r.get("intent") or "")))
    elif et == "LIVE_LEVERAGE_RESULT":
        if r.get("symbol") not in (None, symbol):
            return
        lev = r.get("leverage") or (r.get("response") or {}).get("leverage")
        if lev is not None:
            out.leverage.append((ts, str(lev)))


def read_s2(path: Path, symbol: str = SYMBOL, index_path: Path | None = None) -> S2:
    """The mirror's three needed event types. With `index_path`, what was already read is kept
    there with its byte offset and only new complete lines are read; a replaced, truncated or
    rewritten file (inode, size or first line differ) is read again from the start. A torn last
    line is left for the next run. Absent file = empty S2 (every usbm- fill is then
    USBM_UNVERIFIED)."""
    out = S2()
    try:
        st = os.stat(path)
    except OSError:
        return out
    offset = 0
    head = b""
    if index_path is not None and index_path.exists():
        try:
            idx = json.loads(index_path.read_text())
            with open(path, "rb") as fh:
                head = fh.readline()
            if (idx.get("inode") == st.st_ino and idx.get("offset", 0) <= st.st_size
                    and idx.get("head") == hashlib.sha256(head).hexdigest()
                    and idx.get("symbol") == symbol):
                out = S2(idx.get("first_ms"), set(idx.get("auto_exit_clients") or []),
                         dict(idx.get("sent_ms") or {}),
                         [tuple(x) for x in idx.get("intents") or []],
                         [tuple(x) for x in idx.get("leverage") or []], int(idx.get("lines") or 0))
                offset = int(idx["offset"])
        except (OSError, ValueError, KeyError, TypeError):
            out, offset = S2(), 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        if offset == 0:
            head = b""
        for line in fh:
            if not line.endswith(b"\n"):
                break                      # torn tail: read it next time, complete
            if offset == 0 and not head:
                head = line
            _ingest(out, line, symbol)
            offset += len(line)
    if index_path is not None:
        if not head:
            with open(path, "rb") as fh:
                head = fh.readline()
        tmp = index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"inode": st.st_ino, "offset": offset, "symbol": symbol,
                                   "head": hashlib.sha256(head).hexdigest(),
                                   "first_ms": out.first_ms, "lines": out.lines,
                                   "auto_exit_clients": sorted(out.auto_exit_clients),
                                   "sent_ms": out.sent_ms, "intents": out.intents,
                                   "leverage": out.leverage}))
        os.replace(tmp, index_path)
    return out
