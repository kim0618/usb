"""H-V2 paper execution: APPROVE -> pending entry -> position -> TP / downgrade / invalidation / Bear exit.

The engine is a pure, deterministic replay. Its inputs are the paper launch record, H's forward
ledger (where every decision and every transition is written, append-only, by the research side)
and H's own daily price store. Its output is the complete list of paper events from the launch to
the last stored session. ``run`` compares that list with the stored ``events.jsonl``: the stored
events must be reproduced exactly as a prefix, and only the new tail is appended. A stored event
that the inputs no longer reproduce is an integrity failure, never a silent rewrite.

Rules come from ``docs/operations/h_paper_execution_v1.json`` and nowhere else:

* only an APPROVE written strictly after the paper launch is an entry signal; WATCH, REJECT and
  everything before the launch are not (no backfill);
* an entry fills at the regular-session open of the first session whose open is strictly after the
  decision time, through ``SimBroker`` under ``execution_v0`` - the same broker, cost model and
  accounting contract A uses - so a decision can never fill at a price it already knew;
* at most three positions, one third of NAV each (whole shares, E's floor convention); no
  pyramiding, no averaging down, no forced replacement; an APPROVE with no free slot waits;
* exits are TP1 (half), TP2 (rest), a decision downgrade, an explicit thesis invalidation, and a
  Bear breach where a Bear exists. A refused Bear (VRRM) is ``NOT_EVALUATED``: no stop is invented.

Everything here works on Decimal money and string-serialised numbers, so a stored event and its
recomputation compare byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.broker.accounting import fill_cash_charges
from app.broker.sim import SimBroker
from app.execution.config import ExecutionConfig
from app.execution.costs import buy_effective_price
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.risk.domain import Currency
from app.strategies.h_forward import contract as FC
from app.strategies.h_paper import contract as PC

STRATEGY = FC.STRATEGY_H
VERSION = "H-V2 PAPER V1"
SCOPE = "STRATEGY_H_V2-PAPER"
ET = ZoneInfo("America/New_York")

TP1, TP2, BEAR = "TP1", "TP2", "BEAR_BREACH"
DOWNGRADE_WATCH, DOWNGRADE_REJECT = "DECISION_DOWNGRADE_WATCH", "DECISION_DOWNGRADE_REJECT"
INVALIDATED = "THESIS_INVALIDATED"
SELL_ACTION = {TP1: "SELL_TP1", TP2: "SELL_TP2", BEAR: "SELL_BEAR", DOWNGRADE_WATCH: "SELL_DECISION_DOWNGRADE",
               DOWNGRADE_REJECT: "SELL_DECISION_DOWNGRADE", INVALIDATED: "SELL_INVALIDATION"}
OPEN, PARTIAL, CLOSED = "OPEN", "PARTIAL", "CLOSED"
NOT_EVALUATED = "NOT_EVALUATED"


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _parse(text: str) -> datetime:
    moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"decision time {text!r} is not timezone-aware")
    return moment


@dataclass
class Position:
    position_id: str
    ticker: str
    signal: Mapping[str, Any]
    entry_session: str
    entry_price: Decimal                # fill price (spread/slippage embedded)
    raw_entry: Decimal
    initial_shares: Decimal
    shares: Decimal
    tp1: Decimal | None
    tp2: Decimal | None
    bear: Decimal | None
    entry_charges: Decimal
    tp1_done: bool = False
    exit_charges: Decimal = Decimal(0)
    realized_gross: Decimal = Decimal(0)
    exit_pending: str | None = None
    exit_pending_since: str | None = None


@dataclass
class Pending:
    signal: Mapping[str, Any]
    key: str
    earliest_session: str
    waiting_recorded: bool = False


@dataclass
class Result:
    events: list[dict[str, Any]]
    processed_through: str | None
    stalled_at: str | None
    pending: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    cash: Decimal = Decimal(0)


def signal_key(row: Mapping[str, Any]) -> str:
    return f"{row.get('ticker')}|{row.get('thesis_version')}|{row.get('decision_time')}"


def execution_session(decision_time: str, calendar: MarketCalendar) -> str:
    """The first regular session whose open is strictly after the decision time."""
    moment = _parse(decision_time)
    day = moment.astimezone(ET).date()
    for _ in range(30):
        if calendar.is_trading_day(day):
            opens = calendar.regular_market_open(day)
            if opens is not None and opens > moment:
                return day.isoformat()
        day = calendar.next_trading_day(day)
    raise ValueError(f"no regular session found after {decision_time}")


def _bar_minute(ticker: str, raw: Decimal, at: datetime) -> MinuteBar:
    price = float(raw)
    return MinuteBar(symbol=ticker, open=price, high=price, low=price, close=price, volume=0,
                     observed_at=at, available_at=at, timestamp=at, session=MarketSession.REGULAR)


class Engine:
    def __init__(self, launch: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]],
                 sessions: Sequence[str], bars: Callable[[str], Mapping[str, Any]],
                 *, calendar: MarketCalendar | None = None, identities: Mapping[str, Mapping[str, Any]] | None = None,
                 config: ExecutionConfig | None = None) -> None:
        self.cal = calendar or MarketCalendar()
        self.launch = launch
        self.launched_at = _parse(launch["launched_at"])
        self.initial = Decimal(str(launch["initial_equity"]))
        self.config = config or ExecutionConfig()
        if self.config.version != "execution_v0":
            raise PC.PaperContractError("H paper V1 executes under execution_v0 only")
        self.broker = SimBroker(self.initial, Currency.USD, self.config, execution_scope=SCOPE)
        self.sessions = sorted(sessions)
        self.bars = bars
        self.identities = identities or {}
        # Only rows strictly after the launch are signals; earlier rows are the shadow's past.
        self.signals = [r for r in ledger if r.get("decision_time") and _parse(r["decision_time"]) > self.launched_at]
        self.signals.sort(key=lambda r: _parse(r["decision_time"]))
        self.events: list[dict[str, Any]] = []
        self.positions: dict[str, Position] = {}
        self.pending: list[Pending] = []
        self.seen_keys: set[str] = set()
        self.entered_keys: set[str] = set()
        self.equity_basis = self.initial
        self.max_positions = PC.max_open_positions()
        self.slot = PC.slot_fraction()

    # -- helpers ----------------------------------------------------------------------------------
    def _emit(self, event: str, event_id: str, **body: Any) -> None:
        self.events.append({"event_id": event_id, "event": event, "strategy_id": STRATEGY,
                            "contract_id": PC.contract_id(), **body})

    def _opens(self, session: str) -> datetime:
        opens = self.cal.regular_market_open(date.fromisoformat(session))
        if opens is None:
            raise ValueError(f"{session} is not a regular session")
        return opens

    def _identity(self, ticker: str) -> dict[str, Any]:
        ident = self.identities.get(ticker) or {}
        return {"cik": ident.get("cik"), "security_id": ident.get("security_id")}

    def _expected(self) -> list[str]:
        """Sessions after the launch, by calendar, through the last stored one."""
        if not self.sessions:
            return []
        last = date.fromisoformat(self.sessions[-1])
        day = date.fromisoformat(execution_session(self.launch["launched_at"], self.cal))
        out = []
        while day <= last:
            out.append(day.isoformat())
            day = self.cal.next_trading_day(day)
        return out

    # -- signals ----------------------------------------------------------------------------------
    def _absorb(self, row: Mapping[str, Any]) -> None:
        key = signal_key(row)
        ticker = row["ticker"]
        decision = row.get("decision")
        held = self.positions.get(ticker)
        base = {"ticker": ticker, "signal_key": key, "thesis_version": row.get("thesis_version"),
                "decision_time": row.get("decision_time"), "decision": decision,
                "previous_decision": row.get("previous_decision"), "cause": row.get("cause")}
        if row.get("cause") == INVALIDATED:
            self._cancel_pending(ticker, key, "THESIS_INVALIDATED")
            if held is not None:
                self._request_exit(held, INVALIDATED, row)
            else:
                self._emit("SIGNAL_NO_ACTION", f"{key}|NO_ACTION", **base, paper_action="NONE",
                           reason="THESIS_INVALIDATED_WITHOUT_POSITION")
            return
        if decision != PC.entry_decision():
            self._cancel_pending(ticker, key, f"DECISION_{decision}")
            if held is not None:
                reason = DOWNGRADE_REJECT if decision == FC.REJECT else DOWNGRADE_WATCH
                self._request_exit(held, reason, row)
            else:
                self._emit("SIGNAL_NO_ACTION", f"{key}|NO_ACTION", **base, paper_action="NONE",
                           reason=f"{decision}_NEVER_ENTERS")
            return
        if row.get("d5_contract") != PC.required_d5_contract():
            self._emit("APPROVE_REFUSED_D5_CONTRACT", f"{key}|REFUSED", **base, paper_action="NONE",
                       d5_contract=row.get("d5_contract"), required=PC.required_d5_contract())
            return
        if key in self.seen_keys:
            self._emit("APPROVE_DUPLICATE_IGNORED", f"{key}|DUPLICATE", **base, paper_action="NONE")
            return
        self.seen_keys.add(key)
        if held is not None or any(p.signal["ticker"] == ticker for p in self.pending):
            self._emit("APPROVE_IGNORED_ALREADY_OPEN", f"{key}|ALREADY_OPEN", **base, paper_action="NONE",
                       reason="NO_PYRAMIDING")
            return
        earliest = execution_session(row["decision_time"], self.cal)
        self.pending.append(Pending(row, key, earliest))
        self._emit("PENDING_ENTRY", f"{key}|PENDING_ENTRY", **base, **self._identity(ticker),
                   paper_action="BUY_PENDING", entry_effective_session=earliest,
                   tp1=row.get("tp1"), tp2=row.get("tp2"), bear=row.get("bear"),
                   bear_refusal=row.get("bear_refusal"), valuation_confidence=row.get("valuation_confidence"),
                   d5_contract=row.get("d5_contract"))

    def _cancel_pending(self, ticker: str, cause_key: str, why: str) -> None:
        for item in [p for p in self.pending if p.signal["ticker"] == ticker]:
            self.pending.remove(item)
            self._emit("APPROVE_CANCELLED_DECISION_CHANGED", f"{item.key}|CANCELLED", ticker=ticker,
                       signal_key=item.key, cancelled_by=cause_key, reason=why, paper_action="CANCEL")

    def _request_exit(self, pos: Position, reason: str, row: Mapping[str, Any]) -> None:
        if pos.exit_pending == INVALIDATED:
            return
        pos.exit_pending = reason
        pos.exit_pending_since = row.get("decision_time")
        self._emit("EXIT_PENDING", f"{pos.position_id}|EXIT_PENDING|{reason}|{row.get('decision_time')}",
                   ticker=pos.ticker, position_id=pos.position_id, exit_reason=reason,
                   signal_key=signal_key(row), decision=row.get("decision"),
                   decision_time=row.get("decision_time"), paper_action="CLOSE_PENDING",
                   exit_effective_session=execution_session(row["decision_time"], self.cal))

    # -- fills ------------------------------------------------------------------------------------
    def _submit(self, ticker: str, side: OrderSide, qty: Decimal, raw: Decimal, at: datetime,
                reason: str) -> tuple[Any, Any]:
        intent = OrderIntent(symbol=ticker, side=side,
                             intent_type=IntentType.BASE_ENTRY if side is OrderSide.BUY else IntentType.EXIT,
                             quantity=qty, reference_price=raw, notional=qty * raw,
                             account_notional=self.equity_basis, account_currency="USD",
                             instrument_currency="USD", strategy_version=VERSION, risk_amount=qty * raw,
                             initial_stop=Decimal(0), market_as_of=at - timedelta(seconds=1), created_at=at,
                             reason=reason)
        order = self.broker.submit_order(intent, [_bar_minute(ticker, raw, at)])
        fills = self.broker.get_fills(order.id)
        return order, (fills[0] if fills else None)

    def _fill_body(self, fill: Any) -> dict[str, Any]:
        return {"quantity": _s(fill.quantity), "raw_price": _s(fill.raw_market_price),
                "fill_price": _s(fill.fill_price), "spread_cost": _s(fill.spread_cost),
                "slippage_cost": _s(fill.slippage_cost), "commission": _s(fill.commission),
                "fx_cost": _s(fill.fx_cost), "total_cost": _s(fill.total_cost),
                "cash_after": _s(self.broker.cash), "execution_version": self.config.version}

    def _enter(self, item: Pending, session: str, bar: Any) -> bool:
        """Try one pending entry at this session's open. True when it left the queue."""
        row, ticker = item.signal, item.signal["ticker"]
        if bar is None or getattr(bar, "open", None) is None:
            self._emit("ENTRY_PRICE_MISSING", f"{item.key}|{session}|ENTRY_PRICE_MISSING", ticker=ticker,
                       signal_key=item.key, session=session, reason="NO_OPEN_IN_PRICE_STORE")
            return False
        raw = Decimal(str(bar.open))
        budget = min(self.equity_basis * self.slot, self.broker.cash)
        qty = (budget / buy_effective_price(raw, self.config)).to_integral_value(rounding=ROUND_DOWN)
        if qty <= 0:
            self._emit("ENTRY_REJECTED_ZERO_QUANTITY", f"{item.key}|REJECTED", ticker=ticker,
                       signal_key=item.key, session=session, raw_price=_s(raw), slot_budget=_s(budget))
            return True
        at = self._opens(session)
        order, fill = self._submit(ticker, OrderSide.BUY, qty, raw, at, "H_APPROVE_ENTRY")
        if fill is None:
            reason = getattr(order.rejection_reason, "value", str(order.rejection_reason))
            self._emit(f"ENTRY_REJECTED_{reason}", f"{item.key}|REJECTED", ticker=ticker,
                       signal_key=item.key, session=session, raw_price=_s(raw), quantity=_s(qty))
            return True
        position_id = f"{STRATEGY}|{session}|{ticker}"
        pos = Position(position_id=position_id, ticker=ticker, signal=row, entry_session=session,
                       entry_price=fill.fill_price, raw_entry=raw, initial_shares=qty, shares=qty,
                       tp1=_d(row.get("tp1")), tp2=_d(row.get("tp2")), bear=_d(row.get("bear")),
                       entry_charges=fill_cash_charges(fill))
        self.positions[ticker] = pos
        self.entered_keys.add(item.key)
        self._emit("BUY", f"{position_id}|BUY", ticker=ticker, position_id=position_id, session=session,
                   signal_key=item.key, thesis_version=row.get("thesis_version"),
                   decision_time=row.get("decision_time"), entry_signal_time=row.get("decision_time"),
                   decision_effective_session=item.earliest_session, entry_session=session,
                   execution_delay_sessions=self._delay(item.earliest_session, session),
                   **self._identity(ticker), trade_action="BUY", paper_action="BUY",
                   equity_basis=_s(self.equity_basis), slot_budget=_s(budget),
                   notional=_s(fill.fill_price * fill.quantity),
                   tp1=_s(pos.tp1), tp2=_s(pos.tp2), bear=_s(pos.bear),
                   bear_rule=("EVALUATED" if pos.bear is not None else NOT_EVALUATED),
                   bear_refusal=row.get("bear_refusal"), valuation_confidence=row.get("valuation_confidence"),
                   d5_contract=row.get("d5_contract"), **self._fill_body(fill))
        return True

    def _delay(self, earliest: str, actual: str) -> int:
        if earliest == actual:
            return 0
        n, day = 0, date.fromisoformat(earliest)
        while day.isoformat() < actual:
            day = self.cal.next_trading_day(day)
            n += 1
        return n

    def _sell(self, pos: Position, session: str, qty: Decimal, raw: Decimal, at: datetime, reason: str,
              *, trigger: str) -> None:
        avg = pos.entry_price
        order, fill = self._submit(pos.ticker, OrderSide.SELL, qty, raw, at, reason)
        if fill is None:                                    # cannot happen for a held quantity
            raise PC.PaperContractError(f"{pos.ticker} sell refused: {order.rejection_reason}")
        gross = (fill.fill_price - avg) * fill.quantity
        pos.shares -= fill.quantity
        pos.realized_gross += gross
        pos.exit_charges += fill_cash_charges(fill)
        status = CLOSED if pos.shares == 0 else PARTIAL
        body: dict[str, Any] = {"ticker": pos.ticker, "position_id": pos.position_id, "session": session,
                                "trade_action": SELL_ACTION[reason], "exit_reason": reason, "trigger": trigger,
                                "remaining_shares": _s(pos.shares),
                                "remaining_fraction": _s(pos.shares / pos.initial_shares),
                                "leg_gross_pnl": _s(gross), "position_status": status,
                                **self._fill_body(fill)}
        if status == CLOSED:
            charges = pos.entry_charges + pos.exit_charges
            body |= {"realized_gross_pnl": _s(pos.realized_gross), "cash_charges": _s(charges),
                     "realized_net_pnl": _s(pos.realized_gross - charges)}
            del self.positions[pos.ticker]
        self._emit(SELL_ACTION[reason], f"{pos.position_id}|{SELL_ACTION[reason]}"
                   + (f"|{reason}" if reason in (DOWNGRADE_WATCH, DOWNGRADE_REJECT) else ""), **body)

    def _intrabar(self, pos: Position, session: str, bar: Any) -> None:
        o, h, l = (_d(getattr(bar, name, None)) if bar is not None else None for name in ("open", "high", "low"))
        if o is None or h is None or l is None:
            self._emit("EXIT_CHECK_DATA_MISSING", f"{pos.position_id}|{session}|EXIT_CHECK_DATA_MISSING",
                       ticker=pos.ticker, position_id=pos.position_id, session=session)
            return
        opens = self._opens(session)
        level_at = opens + timedelta(minutes=1)
        half = (pos.initial_shares / 2).to_integral_value(rounding=ROUND_DOWN)

        def tp1_sale(raw: Decimal, at: datetime, trigger: str) -> None:
            pos.tp1_done = True
            qty = min(half, pos.shares)
            if qty <= 0:
                self._emit("TP1_REACHED_NO_SHARES", f"{pos.position_id}|TP1_REACHED_NO_SHARES",
                           ticker=pos.ticker, position_id=pos.position_id, session=session,
                           initial_shares=_s(pos.initial_shares))
                return
            self._sell(pos, session, qty, raw, at, TP1, trigger=trigger)

        # At the open: a gap through a level fills at the open.
        if pos.bear is not None and o <= pos.bear:
            self._sell(pos, session, pos.shares, o, opens, BEAR, trigger="OPEN_AT_OR_BELOW_BEAR")
            return
        if pos.tp1 is not None and not pos.tp1_done and o >= pos.tp1:
            tp1_sale(o, opens, "OPEN_AT_OR_ABOVE_TP1")
        if pos.ticker in self.positions and pos.tp2 is not None and o >= pos.tp2:
            self._sell(pos, session, pos.shares, o, opens, TP2, trigger="OPEN_AT_OR_ABOVE_TP2")
            return
        if pos.ticker not in self.positions:
            return
        # Inside the bar: WORST_CASE when Bear and a TP are both touched.
        tp1_touch = pos.tp1 is not None and not pos.tp1_done and h >= pos.tp1
        tp2_touch = pos.tp2 is not None and h >= pos.tp2
        bear_touch = pos.bear is not None and l <= pos.bear
        if bear_touch:
            trigger = "LOW_AT_OR_BELOW_BEAR" + ("_AMBIGUOUS_WORST_CASE" if (tp1_touch or tp2_touch) else "")
            self._sell(pos, session, pos.shares, pos.bear, level_at, BEAR, trigger=trigger)
            return
        if tp1_touch:
            tp1_sale(pos.tp1, level_at, "HIGH_AT_OR_ABOVE_TP1")
        if pos.ticker in self.positions and tp2_touch:
            self._sell(pos, session, pos.shares, pos.tp2, level_at + timedelta(minutes=1), TP2,
                       trigger="HIGH_AT_OR_ABOVE_TP2")

    # -- the session loop -------------------------------------------------------------------------
    def run(self) -> Result:
        expected = self._expected()
        stored = set(self.sessions)
        processed, stalled = None, None
        queue = list(self.signals)
        for session in expected:
            if session not in stored:
                stalled = session
                break
            opens = self._opens(session)
            while queue and _parse(queue[0]["decision_time"]) < opens:
                self._absorb(queue.pop(0))
            bars = self.bars(session)
            # 1. exits requested by a decision (downgrade / invalidation) fill at this open
            for pos in sorted(self.positions.values(), key=lambda p: p.ticker):
                if pos.exit_pending and execution_session(pos.exit_pending_since, self.cal) <= session:
                    bar = bars.get(pos.ticker)
                    if bar is None or getattr(bar, "open", None) is None:
                        self._emit("EXIT_PRICE_MISSING", f"{pos.position_id}|{session}|EXIT_PRICE_MISSING",
                                   ticker=pos.ticker, position_id=pos.position_id, session=session)
                        continue
                    self._sell(pos, session, pos.shares, Decimal(str(bar.open)), opens, pos.exit_pending,
                               trigger="NEXT_SESSION_OPEN")
            # 2. entries, in decision order, while a slot is free
            for item in list(self.pending):
                if item.earliest_session > session:
                    continue
                if len(self.positions) >= self.max_positions:
                    if not item.waiting_recorded:
                        item.waiting_recorded = True
                        self._emit("APPROVE_WAITING_SLOT", f"{item.key}|WAITING_SLOT", ticker=item.signal["ticker"],
                                   signal_key=item.key, session=session, open_positions=len(self.positions),
                                   max_open_positions=self.max_positions, paper_action="WAIT")
                    continue
                if self._enter(item, session, bars.get(item.signal["ticker"])):
                    self.pending.remove(item)
            # 3. level exits inside the bar, for positions opened before this session
            for pos in sorted(self.positions.values(), key=lambda p: p.ticker):
                if pos.entry_session < session and not pos.exit_pending:
                    self._intrabar(pos, session, bars.get(pos.ticker))
            # 4. mark to the close
            self._nav(session, bars)
            processed = session
        # Signals after the last processed open are pending now; their fill session is in the future.
        while queue:
            self._absorb(queue.pop(0))
        return Result(self.events, processed, stalled,
                      pending=[{"signal_key": p.key, "ticker": p.signal["ticker"],
                                "entry_effective_session": p.earliest_session,
                                "state": "APPROVE_WAITING_SLOT" if p.waiting_recorded else "PENDING_ENTRY"}
                               for p in self.pending],
                      positions=[self.position_view(p) for p in self.positions.values()],
                      cash=self.broker.cash)

    def _nav(self, session: str, bars: Mapping[str, Any]) -> None:
        value, missing = Decimal(0), []
        for pos in self.positions.values():
            bar = bars.get(pos.ticker)
            if bar is None or getattr(bar, "close", None) is None:
                missing.append(pos.ticker)
                continue
            value += pos.shares * Decimal(str(bar.close))
        if missing:
            self._emit("NAV_INCOMPLETE", f"NAV|{session}", session=session, cash=_s(self.broker.cash),
                       missing_close=sorted(missing))
            return
        equity = self.broker.cash + value
        self._emit("NAV", f"NAV|{session}", session=session, cash=_s(self.broker.cash),
                   positions_value=_s(value), equity=_s(equity), open_positions=len(self.positions))
        self.equity_basis = equity

    @staticmethod
    def position_view(pos: Position) -> dict[str, Any]:
        return {"position_id": pos.position_id, "ticker": pos.ticker, "entry_session": pos.entry_session,
                "entry_price": _s(pos.entry_price), "initial_shares": _s(pos.initial_shares),
                "current_shares": _s(pos.shares), "tp1": _s(pos.tp1), "tp2": _s(pos.tp2), "bear": _s(pos.bear),
                "tp1_done": pos.tp1_done, "status": OPEN if pos.shares == pos.initial_shares else PARTIAL,
                "realized_gross_pnl": _s(pos.realized_gross),
                "realized_net_pnl": _s(pos.realized_gross - pos.entry_charges - pos.exit_charges),
                "exit_pending": pos.exit_pending,
                "thesis_version": pos.signal.get("thesis_version"),
                "decision_time": pos.signal.get("decision_time"),
                "valuation_confidence": pos.signal.get("valuation_confidence"),
                "bear_rule": "EVALUATED" if pos.bear is not None else NOT_EVALUATED,
                "bear_refusal": pos.signal.get("bear_refusal")}


def reconcile(stored: Sequence[Mapping[str, Any]], computed: Sequence[Mapping[str, Any]],
              canonical: Callable[[Mapping[str, Any]], str]) -> tuple[list[dict[str, Any]], list[str]]:
    """The new tail to append, and the problems. Stored events must be a prefix of the recomputation."""
    problems: list[str] = []
    if len(stored) > len(computed):
        problems.append(f"stored events ({len(stored)}) exceed the recomputation ({len(computed)})")
    for index, (old, new) in enumerate(zip(stored, computed)):
        if canonical(old) != canonical(new):
            problems.append(f"event {index} ({old.get('event_id')}) is no longer reproduced")
            break
    ids = [e["event_id"] for e in computed]
    if len(ids) != len(set(ids)):
        problems.append("duplicate event ids in the recomputation")
    if problems:
        return [], problems
    return [dict(e) for e in computed[len(stored):]], []


def tickers_of(ledger: Iterable[Mapping[str, Any]]) -> set[str]:
    return {r["ticker"] for r in ledger if isinstance(r.get("ticker"), str)}
