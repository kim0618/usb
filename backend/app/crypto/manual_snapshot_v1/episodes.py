"""Order events and position episodes, derived from S1 fills (contract sections 2 and 3).

Pure functions of their inputs: the same fills always give the same events and episodes, which is
what makes the store idempotent (each run re-derives everything and appends missing keys only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .sources import MANUAL_PREFIX, S1, S2, Fill

SETTLE_MS = 60_000
ZERO = Decimal(0)

MANUAL_TERMINAL = "MANUAL_TERMINAL"
EXITGUARD = "EXITGUARD"
USBM_UNVERIFIED = "USBM_UNVERIFIED"
EXTERNAL = "EXTERNAL"
UNKNOWN = "UNKNOWN"


def origin_of(client_order_id: str | None, first_fill_ms: int, s2: S2) -> str:
    if not client_order_id:
        return UNKNOWN
    if not client_order_id.startswith(MANUAL_PREFIX):
        return EXTERNAL
    if client_order_id in s2.auto_exit_clients:
        return EXITGUARD
    if client_order_id in s2.sent_ms:
        return MANUAL_TERMINAL
    if s2.first_ms is None or first_fill_ms < s2.first_ms:
        return USBM_UNVERIFIED
    # The mirror covers this time but has no send for this id: the mirror is the only record of
    # the AUTO_EXIT permission, so without its line the origin cannot be confirmed either way.
    return USBM_UNVERIFIED


@dataclass
class OrderEvent:
    event_id: str                 # "<order_id>" (or "<order_id>:open" for the open half of a reverse)
    order_id: int
    event_type: str               # OPEN_LONG / OPEN_SHORT / ADD / REDUCE / CLOSE
    origin: str
    direction: str                # LONG / SHORT: the position's side the event belongs to
    first_fill_ms: int
    last_fill_ms: int
    qty: Decimal
    vwap: Decimal
    fills: int
    commission: Decimal
    realized_pnl: Decimal
    client_order_id: str | None
    qty_before: Decimal           # signed
    qty_after: Decimal            # signed
    vwap_entry_before: Decimal | None
    vwap_entry_after: Decimal | None
    episode_realized_before: Decimal
    episode_id: str = ""

    def row(self) -> dict:
        s = lambda d: None if d is None else format(d, "f")
        return {"event_id": self.event_id, "order_id": self.order_id, "event_type": self.event_type,
                "origin": self.origin, "direction": self.direction, "episode_id": self.episode_id,
                "first_fill_ms": self.first_fill_ms, "last_fill_ms": self.last_fill_ms,
                "qty": s(self.qty), "vwap": s(self.vwap), "fills": self.fills,
                "commission": s(self.commission), "realized_pnl": s(self.realized_pnl),
                "client_order_id": self.client_order_id, "qty_before": s(self.qty_before),
                "qty_after": s(self.qty_after), "vwap_entry_before": s(self.vwap_entry_before),
                "vwap_entry_after": s(self.vwap_entry_after),
                "episode_realized_before": s(self.episode_realized_before)}


@dataclass
class Episode:
    episode_id: str
    direction: str
    events: list[OrderEvent] = field(default_factory=list)
    closed: bool = False


def _settled(s1: S1) -> list[list[Fill]]:
    """Fills grouped by order id, in first-fill order, keeping only orders whose last fill is at
    least SETTLE_MS older than the cache's last refresh (all of a market order's fills are in)."""
    by_order: dict[int, list[Fill]] = {}
    for f in s1.fills:
        by_order.setdefault(f.order_id, []).append(f)
    horizon = (s1.last_calculated_ms or 0) - SETTLE_MS
    groups = [g for g in by_order.values() if max(f.time_ms for f in g) <= horizon]
    return sorted(groups, key=lambda g: (min(f.time_ms for f in g), min(f.id for f in g)))


def derive(s1: S1, s2: S2) -> tuple[list[OrderEvent], list[Episode]]:
    events: list[OrderEvent] = []
    episodes: list[Episode] = []
    pos = ZERO
    cost = ZERO                     # sum(price * qty) of the open side, for the VWAP entry
    open_qty = ZERO                 # absolute qty bought (long) / sold (short) into the position
    ep: Episode | None = None
    realized = ZERO

    def vwap_entry() -> Decimal | None:
        return (cost / open_qty) if open_qty else None

    for group in _settled(s1):
        group = sorted(group, key=lambda f: (f.time_ms, f.id))
        oid = group[0].order_id
        cid = next((f.client_order_id for f in group if f.client_order_id), None)
        if cid is None:
            # S1 has not classified this order yet (its queryOrder failed and is retried on
            # every refresh). Writing it now as UNKNOWN would freeze a wrong origin; skipping it
            # would break the position arithmetic of every later order. Stop here and wait.
            break
        first, last = group[0].time_ms, group[-1].time_ms
        origin = origin_of(cid, first, s2)
        signed = sum((f.signed_qty for f in group), ZERO)
        qty = abs(signed)
        if qty == 0:
            continue
        vwap = sum((f.price * f.qty for f in group), ZERO) / sum((f.qty for f in group), ZERO)
        commission = sum((f.commission for f in group), ZERO)
        rpnl = sum((f.realized_pnl for f in group), ZERO)
        before = pos
        after = pos + signed

        def emit(event_id: str, etype: str, direction: str, q: Decimal, b: Decimal, a: Decimal,
                 c: Decimal, r: Decimal, entry_before: Decimal | None, entry_after: Decimal | None,
                 realized_before: Decimal) -> OrderEvent:
            ev = OrderEvent(event_id, oid, etype, origin, direction, first, last, q, vwap, len(group),
                            c, r, cid, b, a, entry_before, entry_after, realized_before)
            events.append(ev)
            return ev

        if before == 0:
            direction = "LONG" if signed > 0 else "SHORT"
            ep = Episode(f"ep-{oid}", direction)
            episodes.append(ep)
            realized = ZERO
            cost, open_qty = vwap * qty, qty
            ev = emit(str(oid), f"OPEN_{direction}", direction, qty, before, after, commission, rpnl,
                      None, vwap_entry(), realized)
            ev.episode_id = ep.episode_id
            ep.events.append(ev)
            realized += rpnl
        elif (before > 0) == (signed > 0):
            entry_before = vwap_entry()
            cost += vwap * qty
            open_qty += qty
            ev = emit(str(oid), "ADD", ep.direction, qty, before, after, commission, rpnl,
                      entry_before, vwap_entry(), realized)
            ev.episode_id = ep.episode_id
            ep.events.append(ev)
            realized += rpnl
        elif after == 0 or (before > 0) == (after > 0):
            entry = vwap_entry()
            ev = emit(str(oid), "CLOSE" if after == 0 else "REDUCE", ep.direction, qty, before, after,
                      commission, rpnl, entry, entry if after != 0 else None, realized)
            ev.episode_id = ep.episode_id
            ep.events.append(ev)
            realized += rpnl
            if after == 0:
                ep.closed = True
                ep = None
                cost, open_qty = ZERO, ZERO
        else:
            # Reverse: the part up to flat closes the episode, the rest opens a new one at the same
            # price. Commission and realised PnL stay with the close (Binance books them there).
            close_q = abs(before)
            entry = vwap_entry()
            ev = emit(str(oid), "CLOSE", ep.direction, close_q, before, ZERO, commission, rpnl,
                      entry, None, realized)
            ev.episode_id = ep.episode_id
            ep.events.append(ev)
            ep.closed = True
            direction = "LONG" if after > 0 else "SHORT"
            ep = Episode(f"ep-{oid}:open", direction)
            episodes.append(ep)
            realized = ZERO
            cost, open_qty = vwap * abs(after), abs(after)
            ev2 = emit(f"{oid}:open", f"OPEN_{direction}", direction, abs(after), ZERO, after, ZERO,
                       ZERO, None, vwap_entry(), realized)
            ev2.episode_id = ep.episode_id
            ep.events.append(ev2)
        pos = after
    return events, episodes


def unclassified_orders(s1: S1) -> list[int]:
    """Settled orders S1 still has no client order id for; derivation waits at the first one."""
    return [g[0].order_id for g in _settled(s1)
            if next((f.client_order_id for f in g if f.client_order_id), None) is None]


def position_at(events: list[OrderEvent], ms: int) -> Decimal:
    """Signed position after every event whose last fill is at or before ms."""
    q = ZERO
    for ev in events:
        if ev.last_fill_ms <= ms:
            q = ev.qty_after
        else:
            break
    return q


def episode_summary(ep: Episode, funding: list[tuple[int, Decimal]], path: dict | None) -> dict:
    """Contract section 3. `path` carries MFE/MAE from 1m bars, computed by the caller."""
    evs = ep.events
    opening = [e for e in evs if e.event_type.startswith("OPEN") or e.event_type == "ADD"]
    closing = [e for e in evs if e.event_type in ("REDUCE", "CLOSE")]
    open_ms, close_ms = evs[0].first_fill_ms, evs[-1].last_fill_ms
    oq = sum((e.qty for e in opening), ZERO)
    entry = sum((e.vwap * e.qty for e in opening), ZERO) / oq if oq else None
    cq = sum((e.qty for e in closing), ZERO)
    exit_ = sum((e.vwap * e.qty for e in closing), ZERO) / cq if cq else None
    gross = sum((e.realized_pnl for e in evs), ZERO)
    fees = sum((e.commission for e in evs), ZERO)
    fund = sum((inc for t, inc in funding if open_ms < t <= close_ms), ZERO)
    max_size = max(abs(e.qty_after) for e in evs)
    d = 1 if ep.direction == "LONG" else -1
    exit_ret = (d * (exit_ - entry) / entry * 10_000) if entry and exit_ else None
    counts: dict[str, int] = {}
    for e in evs:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
    s = lambda x: None if x is None else format(x, "f")
    origins = sorted({e.origin for e in evs})
    return {
        "episode_id": ep.episode_id, "direction": ep.direction, "origin_mix": origins,
        "manual_only": origins == [MANUAL_TERMINAL],
        "open_ms": open_ms, "close_ms": close_ms, "hold_ms": close_ms - open_ms,
        "first_entry_price": s(evs[0].vwap), "vwap_entry": s(entry), "vwap_exit": s(exit_),
        "max_size": s(max_size), "events": counts,
        "gross_usdt": s(gross), "fees_usdt": s(fees), "funding_usdt": s(fund),
        "net_usdt": s(gross - fees + fund),
        "exit_return_bp": None if exit_ret is None else float(round(exit_ret, 4)),
        "mfe_bp": (path or {}).get("mfe_bp"), "mae_bp": (path or {}).get("mae_bp"),
        "extreme_in_fill_minute": (path or {}).get("extreme_in_fill_minute"),
        "giveback_bp": (None if path is None or path.get("mfe_bp") is None or exit_ret is None
                        else round(path["mfe_bp"] - float(exit_ret), 4)),
        "open_snapshot_id": f"ev:{evs[0].event_id}", "close_snapshot_id": f"ev:{evs[-1].event_id}",
    }
