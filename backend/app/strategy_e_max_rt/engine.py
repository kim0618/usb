"""E-MAX V1 realtime virtual trading engine: one tick function over a persisted session state.

Lifecycle per XNYS session (ET):

    WAITING --09:25--> DECIDED --09:30 bar--> POSITION_OPEN --09:35 bar--> COMPLETE
       |                  |                                               ^
       +--> NO_DECISION   +--> COMPLETE (no executable entry) ------------+
    any step --exception--> ERROR (fail closed for this strategy only)

Common components reused: ``MarketDataProvider`` (the runtime's provider), ``MarketCalendar``,
``SimBroker`` with ``ExecutionConfig`` (A's order / fill / cost convention: next-bar open), and
``OrderIntent``. Nothing of A's strategy, risk engine, lifecycle services or tables is used.

Fill semantics (no look-ahead):

* entry: a BUY with ``market_as_of = 09:29`` fills on the first bar after it, the 09:30 bar, at its
  open; it is submitted only once that bar exists (after 09:31) and was never known before 09:30;
* exit: a SELL with ``market_as_of = 09:34`` fills on the 09:35 bar open, the first executable
  price after the development proxy (the 09:34 close), which is recorded beside it, not as the fill.

Idempotency: every action is keyed ``strategy_id|session|symbol|action`` and written to the state
before the next step; a restart rebuilds the in-memory SimBroker by replaying the recorded intents on
their recorded bars (deterministic), so a restarted process neither re-decides nor double-enters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from fractions import Fraction
import json
import logging
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.broker.sim import SimBroker
from app.execution.config import ExecutionConfig
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.risk.domain import Currency
from app.strategy_e_max_rt import config as CFG, decision as DEC
from app.strategy_e_v1_1 import context

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
COST_BP = (0, 5, 10, 15, 20)
MINUTE = timedelta(minutes=1)


class Phase(StrEnum):
    IDLE = "IDLE"                    # not an XNYS session
    WAITING = "WAITING"
    DECIDED = "DECIDED"
    NO_DECISION = "NO_DECISION"      # FEATURE_CONTEXT_INCOMPLETE or window missed: no trade, fail closed
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


TERMINAL = {Phase.NO_DECISION, Phase.COMPLETE, Phase.ERROR}


def _t(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


def _at(session: date, text: str) -> datetime:
    return datetime.combine(session, _t(text), tzinfo=ET)


# -- persistence ------------------------------------------------------------------------------------

class Store:
    """JSON state per strategy, separate from A's database: sessions/<D>.json and book.json."""

    def __init__(self, root: Path, strategy_id: str):
        self.dir = root / strategy_id
        (self.dir / "sessions").mkdir(parents=True, exist_ok=True)

    def _write(self, path: Path, body: Mapping[str, Any]) -> None:
        tmp = path.with_name(path.name + ".partial")
        tmp.write_text(json.dumps(body, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def session_path(self, session: date) -> Path:
        return self.dir / "sessions" / f"{session.isoformat()}.json"

    def load_session(self, session: date) -> dict[str, Any] | None:
        path = self.session_path(session)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save_session(self, state: Mapping[str, Any]) -> None:
        self._write(self.session_path(date.fromisoformat(state["session"])), state)

    def load_book(self, initial_equity: Decimal) -> dict[str, Any]:
        path = self.dir / "book.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"strategy_id": self.dir.name, "initial_equity": str(initial_equity), "equity": str(initial_equity),
                "realized_pnl": "0", "sessions": {}, "live_margin_approved": CFG.LIVE_MARGIN_APPROVED}

    def save_book(self, book: Mapping[str, Any]) -> None:
        self._write(self.dir / "book.json", book)


# -- engine -------------------------------------------------------------------------------------------

@dataclass
class Engine:
    config: CFG.RuntimeConfig
    provider_factory: Callable[[], MarketDataProvider]
    decision_source: DEC.DecisionSource
    calendar: MarketCalendar
    store: Store
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    # ---- helpers
    def key(self, session: date, symbol: str, action: str) -> str:
        return f"{self.config.strategy_id}|{session.isoformat()}|{symbol}|{action}"

    def new_state(self, session: date, equity: Decimal) -> dict[str, Any]:
        return {"strategy_id": self.config.strategy_id, "session": session.isoformat(), "phase": Phase.WAITING,
                "equity_at_open": str(equity), "decision": None, "entries": {}, "exits": {}, "keys": [],
                "events": [], "mode": "SIMULATION_VIRTUAL_ONLY", "risk_profile": self.config.profile.name,
                "live_margin_approved": CFG.LIVE_MARGIN_APPROVED}

    @staticmethod
    def _event(state: dict[str, Any], now: datetime, text: str) -> None:
        state["events"].append({"at": now.isoformat(), "event": text})

    def _broker(self, state: Mapping[str, Any]) -> SimBroker:
        """A fresh SimBroker funded with the profile's buying power, replaying recorded fills."""
        broker = SimBroker(self.config.profile.buying_power(Decimal(state["equity_at_open"])), Currency.USD,
                           self.execution, execution_scope=f"{self.config.strategy_id}-{state['session']}")
        for bucket in ("entries", "exits"):
            for record in state[bucket].values():
                if record.get("status") == "FILLED":
                    order = broker.submit_order(_intent_from(record["intent"]), [_bar_from(record["fill_bar"])])
                    if order.status.value != "FILLED":
                        raise RuntimeError(f"replay of {record['key']} did not reproduce its fill")
        return broker

    def _regular_bars(self, symbols: Sequence[str], session: date, start: str, end: str) -> list[MinuteBar]:
        provider = self.provider_factory()
        bars = provider.get_minute_bars(list(symbols), start=_at(session, start), end=_at(session, end))
        return [b for b in bars if b.session is MarketSession.REGULAR]

    # ---- the tick
    def tick(self, now: datetime) -> dict[str, Any]:
        now_et = now.astimezone(ET)
        session = now_et.date()
        if not self.config.enabled:
            return {"strategy_id": self.config.strategy_id, "phase": Phase.DISABLED, "session": session.isoformat()}
        if self.calendar.session(session) is None:
            return {"strategy_id": self.config.strategy_id, "phase": Phase.IDLE, "session": session.isoformat()}
        book = self.store.load_book(self.config.initial_equity)
        state = self.store.load_session(session) or self.new_state(session, Decimal(book["equity"]))
        if Phase(state["phase"]) in TERMINAL:
            return state
        try:
            self._advance(state, session, now_et, book)
        except Exception as error:     # fail closed for E only; the caller's other strategies continue
            logger.exception("E-MAX runtime error")
            state["phase"] = Phase.ERROR
            self._event(state, now_et, f"ERROR {type(error).__name__}: {error}")
        self.store.save_session(state)
        return state

    def _advance(self, state: dict[str, Any], session: date, now: datetime, book: dict[str, Any]) -> None:
        cfg = self.config
        if Phase(state["phase"]) is Phase.WAITING and now >= _at(session, cfg.decision_cutoff):
            if now >= _at(session, cfg.entry_bar):
                state["phase"] = Phase.NO_DECISION
                self._event(state, now, "DECISION_WINDOW_MISSED: runtime was not up between 09:25 and 09:30")
                return
            try:
                decision = self.decision_source.decide(session, now)
            except context.FeatureContextIncomplete as refusal:
                state["phase"] = Phase.NO_DECISION
                state["no_decision"] = {"status": refusal.status, "missing": list(refusal.missing)}
                self._event(state, now, f"{refusal.status}: {'; '.join(refusal.missing)}")
                return
            DEC.verify(decision)
            state["decision"] = decision.to_json()
            state["phase"] = Phase.DECIDED
            self._event(state, now, f"DECIDED selected={list(decision.selected)} exposure={decision.final_exposure}")
            self.store.save_session(state)        # the decision is durable before any order exists
        if Phase(state["phase"]) is Phase.DECIDED and now >= _at(session, cfg.entry_bar) + MINUTE:
            self._enter(state, session, now)
        if Phase(state["phase"]) is Phase.POSITION_OPEN and now >= _at(session, cfg.exit_bar) + 2 * MINUTE:
            self._exit(state, session, now)
        if Phase(state["phase"]) is Phase.COMPLETE:
            self._finalize(state, session, book, now)

    def _enter(self, state: dict[str, Any], session: date, now: datetime) -> None:
        decision = DEC.EDecision.from_json(state["decision"])
        DEC.verify(decision)
        entry_bar = _at(session, self.config.entry_bar)
        bars = self._regular_bars(decision.selected, session, "09:29", "09:31")
        found = {b.symbol: b for b in bars if b.timestamp == entry_bar}
        pending = [s for s in decision.selected if s not in state["entries"]]
        resolved = now >= _at(session, self.config.entry_resolve_by)
        if any(s not in found for s in pending) and not resolved:
            return                                       # wait for every selected 09:30 bar (sizing needs n)
        for symbol in pending:
            if symbol not in found:
                state["entries"][symbol] = {"key": self.key(session, symbol, "ENTRY"), "status": "ENTRY_INVALID",
                                            "reason": "NO_EXACT_0930_BAR", "resolved_at": now.isoformat()}
        if "executable" not in state:
            # n is fixed once, before the first order, so a restart cannot resize the book
            state["executable"] = [s for s in decision.selected if s in found]
            self.store.save_session(state)
        executable = state["executable"]
        final = Fraction(decision.final_exposure)
        broker = self._broker(state)
        equity = Decimal(state["equity_at_open"])
        for symbol in executable:
            key = self.key(session, symbol, "ENTRY")
            if key in state["keys"]:
                continue
            weight = final / len(executable)
            ref = Decimal(str(decision.reference_prices[symbol]))
            notional = equity * Decimal(weight.numerator) / Decimal(weight.denominator)
            quantity = (notional / ref).to_integral_value(rounding=ROUND_DOWN)
            if quantity <= 0:
                state["entries"][symbol] = {"key": key, "status": "ENTRY_INVALID", "reason": "ZERO_QUANTITY"}
                state["keys"].append(key)
                continue
            intent = OrderIntent(symbol=symbol, side=OrderSide.BUY, intent_type=IntentType.BASE_ENTRY,
                                 quantity=quantity, reference_price=ref, notional=quantity * ref,
                                 account_notional=equity, account_currency="USD", instrument_currency="USD",
                                 strategy_version=self.config.strategy_id, risk_amount=quantity * ref,
                                 initial_stop=Decimal("0"), market_as_of=entry_bar - MINUTE, created_at=now,
                                 reason="E_MAX_V1_ENTRY_0930_OPEN")
            order = broker.submit_order(intent, [found[symbol]])
            fill = broker.get_fills(order.id)
            state["entries"][symbol] = {
                "key": key, "status": order.status.value, "rejection": getattr(order.rejection_reason, "value", None),
                "weight": f"{weight.numerator}/{weight.denominator}", "target_notional": str(notional),
                "intent": _intent_to(intent), "fill_bar": _bar_to(found[symbol]),
                "signal_at": decision.decided_at, "order_at": now.isoformat(),
                "fill_at": fill[0].filled_at.isoformat() if fill else None,
                "raw_market_price": str(fill[0].raw_market_price) if fill else None,
                "fill_price": str(fill[0].fill_price) if fill else None,
                "fill_cost": str(fill[0].total_cost) if fill else None,
                "fill_semantics": "SimBroker next-bar open: the 09:30 bar open, submitted after the bar existed",
                "development_proxy": {"entry": "exact 09:30 bar open", "value": found[symbol].open},
                "bar_volume": found[symbol].volume, "bid": None, "ask": None, "spread": None,
                "quote_status": "QUOTES_NOT_AVAILABLE_FROM_PROVIDER"}
            state["keys"].append(key)
            self._event(state, now, f"ENTRY {symbol} {order.status.value} qty={quantity}")
        filled = [s for s, r in state["entries"].items() if r["status"] == "FILLED"]
        state["phase"] = Phase.POSITION_OPEN if filled else Phase.COMPLETE

    def _exit(self, state: dict[str, Any], session: date, now: datetime) -> None:
        exit_proxy = _at(session, self.config.exit_bar)
        open_symbols = [s for s, r in state["entries"].items() if r["status"] == "FILLED" and s not in state["exits"]]
        if not open_symbols:
            state["phase"] = Phase.COMPLETE
            return
        bars = self._regular_bars(open_symbols, session, "09:34", "09:59")
        broker = self._broker(state)
        late = now >= _at(session, self.config.exit_late_after)
        for symbol in open_symbols:
            key = self.key(session, symbol, "EXIT")
            if key in state["keys"]:
                continue
            mine = sorted((b for b in bars if b.symbol == symbol), key=lambda b: b.timestamp)
            proxy = next((b for b in mine if b.timestamp == exit_proxy), None)
            after = [b for b in mine if b.timestamp > exit_proxy]
            if not after or (after[0].timestamp != exit_proxy + MINUTE and not late):
                continue                                   # wait for the 09:35 bar
            position = broker.get_position(symbol)
            entry = state["entries"][symbol]
            intent = OrderIntent(symbol=symbol, side=OrderSide.SELL, intent_type=IntentType.EXIT,
                                 quantity=position.quantity, reference_price=Decimal(str(after[0].open)),
                                 notional=position.quantity * Decimal(str(after[0].open)),
                                 account_notional=Decimal(state["equity_at_open"]), account_currency="USD",
                                 instrument_currency="USD", strategy_version=self.config.strategy_id,
                                 risk_amount=position.quantity * Decimal(str(after[0].open)),
                                 initial_stop=Decimal("0"), market_as_of=after[0].timestamp - MINUTE,
                                 created_at=now, reason="E_MAX_V1_EXIT_AFTER_0934")
            order = broker.submit_order(intent, [after[0]])
            fill = broker.get_fills(order.id)
            entry_raw = Decimal(entry["raw_market_price"])
            exit_raw = fill[0].raw_market_price if fill else None
            gross = (exit_raw / entry_raw - 1) if exit_raw else None
            weight = Fraction(entry["weight"])
            w = Decimal(weight.numerator) / Decimal(weight.denominator)
            state["exits"][symbol] = {
                "key": key, "status": order.status.value, "intent": _intent_to(intent), "fill_bar": _bar_to(after[0]),
                "order_at": now.isoformat(), "fill_at": fill[0].filled_at.isoformat() if fill else None,
                "raw_market_price": str(exit_raw) if exit_raw else None,
                "fill_price": str(fill[0].fill_price) if fill else None,
                "fill_cost": str(fill[0].total_cost) if fill else None,
                "exit_flag": "ON_TIME_0935_OPEN" if after[0].timestamp == exit_proxy + MINUTE else "LATE_UNRESOLVED_EXIT",
                "development_proxy": {"exit": "exact 09:34 bar close", "value": proxy.close if proxy else None,
                                      "status": "VALID" if proxy else "UNRESOLVED_EXIT"},
                "bar_volume": after[0].volume, "bid": None, "ask": None, "spread": None,
                "fixed_bp_views": _fixed_views(w, gross)}
            state["keys"].append(key)
            self._event(state, now, f"EXIT {symbol} {order.status.value}")
        if all(s in state["exits"] for s in open_symbols):
            state["phase"] = Phase.COMPLETE

    def _finalize(self, state: dict[str, Any], session: date, book: dict[str, Any], now: datetime) -> None:
        if session.isoformat() in book["sessions"]:
            return                                          # idempotent
        broker = self._broker(state)
        trades = [t for t in (broker.get_trade(s) for s in state["entries"]) if t is not None]
        realized = sum((t.net_pnl for t in trades), Decimal(0))
        views = {f"net_{bp:02d}bp": str(sum((Decimal(x["fixed_bp_views"][f"net_{bp:02d}bp"])
                                             for x in state["exits"].values() if x.get("fixed_bp_views")),
                                            Decimal(0))) for bp in COST_BP}
        equity = Decimal(book["equity"]) + realized
        state["summary"] = {"sim_realized_pnl": str(realized), "equity_after": str(equity),
                            "fixed_bp_session_return_views": views,
                            "gross_exposure": state["decision"]["final_exposure"] if state["decision"] else "0"}
        book["sessions"][session.isoformat()] = {"sim_realized_pnl": str(realized), "equity_after": str(equity),
                                                 "phase": Phase.COMPLETE}
        book["equity"] = str(equity)
        book["realized_pnl"] = str(Decimal(book["realized_pnl"]) + realized)
        self.store.save_book(book)
        self._event(state, now, f"COMPLETE realized={realized}")


def _fixed_views(weight: Decimal, gross: Decimal | None) -> dict[str, str] | None:
    """Development-comparable views: weight x (raw gross - fixed round-trip bp); not the sim fill PnL."""
    if gross is None:
        return None
    return {f"net_{bp:02d}bp": str(weight * (gross - Decimal(bp) / Decimal(10000))) for bp in COST_BP}


# -- (de)serialisation of intents and bars for deterministic replay -------------------------------------

def _intent_to(intent: OrderIntent) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, (Decimal, datetime)) or hasattr(v, "value") else v)
            for k, v in intent.__dict__.items()}


def _intent_from(body: Mapping[str, Any]) -> OrderIntent:
    d = dict(body)
    for k in ("quantity", "reference_price", "notional", "account_notional", "risk_amount", "initial_stop"):
        d[k] = Decimal(d[k])
    if d.get("max_execution_price") not in (None, "None"):
        d["max_execution_price"] = Decimal(d["max_execution_price"])
    else:
        d["max_execution_price"] = None
    d["side"], d["intent_type"] = OrderSide(d["side"]), IntentType(d["intent_type"])
    for k in ("market_as_of", "created_at"):
        d[k] = datetime.fromisoformat(d[k])
    return OrderIntent(**d)


def _bar_to(bar: MinuteBar) -> dict[str, Any]:
    return json.loads(bar.model_dump_json())


def _bar_from(body: Mapping[str, Any]) -> MinuteBar:
    return MinuteBar.model_validate(body)
