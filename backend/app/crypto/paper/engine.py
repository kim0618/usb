"""The paper engine: the only place account state changes.

Determinism (contract T1): the engine reads no clock, no socket and no random source. Every
timestamp comes from the input it was handed, so `config + input tape` fully determines the
ledger bytes. `replay_tape` exercises exactly that.

Cost classification (contract C1/C2/C3) is carried, not just commented:
  spread, slippage, book depth -> PRICE_EMBEDDED, already inside the fill price
  fee, funding               -> CASH_SEPARATE, deducted from cash exactly once
Nothing marked PRICE_EMBEDDED is ever subtracted from PnL again.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from . import LEDGER_SCHEMA_VERSION, PAPER_ENGINE_VERSION
from .account import Account, Position, SIDE_LONG, SIDE_SHORT
from .book import BookSide, NoLiquidity, Quote, walk_book
from .config import PaperRunConfig
from .instrument import RiskTierTable
from .ledger import EventType, InputKind, InputTape, Ledger
from .state import (
    AUTO_OFF, AUTO_ON, EMERGENCY, EMERGENCY_ON, EMERGENCY_RELEASE, POSITION_FLAT,
    TerminalState, TransitionRejected,
)

FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000  # UTC 00:00 / 08:00 / 16:00, invariant since listing
PRICE_EMBEDDED = "PRICE_EMBEDDED"
CASH_SEPARATE = "CASH_SEPARATE"


class OrderRejected(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FillOutcome:
    fill_price: Decimal
    fill_qty: Decimal
    book_avg_price: Decimal
    reference_price: Decimal
    levels_consumed: int
    slippage_component: Decimal
    fee: Decimal


def quote_from_payload(payload: dict[str, Any]) -> Quote:
    """Rebuild a quote from a tape record. The tape stores decimal strings, never floats."""
    def optional(key: str) -> Decimal | None:
        value = payload.get(key)
        return Decimal(str(value)) if value is not None else None

    return Quote(
        ts_ms=int(payload["ts_ms"]),
        bids=BookSide.from_rows(payload.get("bids", []), descending=True),
        asks=BookSide.from_rows(payload.get("asks", []), descending=False),
        mark_price=Decimal(str(payload["mark_price"])),
        last_price=optional("last_price"),
        index_price=optional("index_price"),
        funding_rate=optional("funding_rate"),
        next_funding_time_ms=int(payload["next_funding_time_ms"]) if payload.get("next_funding_time_ms") else None,
    )


def quote_to_payload(quote: Quote, *, depth: int = 5) -> dict[str, Any]:
    return {
        "ts_ms": quote.ts_ms,
        "bids": [[str(price), str(qty)] for price, qty in quote.bids.levels[:depth]],
        "asks": [[str(price), str(qty)] for price, qty in quote.asks.levels[:depth]],
        "mark_price": str(quote.mark_price),
        "last_price": str(quote.last_price) if quote.last_price is not None else None,
        "index_price": str(quote.index_price) if quote.index_price is not None else None,
        "funding_rate": str(quote.funding_rate) if quote.funding_rate is not None else None,
        "next_funding_time_ms": quote.next_funding_time_ms,
    }


class PaperEngine:
    def __init__(self, config: PaperRunConfig, tiers: RiskTierTable, *, ledger: Ledger | None = None) -> None:
        self.config = config
        self.tiers = tiers
        self.ledger = ledger if ledger is not None else Ledger()
        self.account = Account(starting_capital_usdt=config.starting_capital_usdt)
        self.state = TerminalState()
        self.leverage = config.leverage
        self.quote: Quote | None = None
        self.last_market_ts_ms: int | None = None
        self.liquidation_count = 0
        self.funding_grid_mismatches = 0
        self.funding_catch_ups = 0
        self._started = False

    # ---------------------------------------------------------------- lifecycle

    def start(self, ts_ms: int) -> None:
        if self._started:
            raise RuntimeError("engine already started")
        self._started = True
        self.ledger.append(ts_ms=ts_ms, event_type=EventType.RUN_START,
                           ledger_schema_version=LEDGER_SCHEMA_VERSION,
                           paper_engine_version=PAPER_ENGINE_VERSION,
                           config=self.config.snapshot(),
                           risk_tier_count=len(self.tiers.tiers),
                           risk_tier_source_sha256=self.tiers.source_sha256,
                           mode=self.state.mode,
                           starting_capital_usdt=self.account.starting_capital_usdt)

    # ------------------------------------------------------------------ market

    def apply_market(self, quote: Quote) -> None:
        """Funding first, then liquidation: a settlement can be what tips a position over."""
        if not self._started:
            raise RuntimeError("engine has not started")
        previous_ts = self.last_market_ts_ms
        self.quote = quote
        self.last_market_ts_ms = quote.ts_ms
        self._settle_funding(quote, previous_ts)
        self.account.position.observe_excursion(quote.mark_price)
        self._check_liquidation(quote)
        self.account.assert_invariants(quote.mark_price)

    def _settle_funding(self, quote: Quote, previous_ts: int | None) -> None:
        if previous_ts is None or self.account.position.is_flat:
            return
        if quote.funding_rate is None:
            return
        first = (previous_ts // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
        boundary = first
        while boundary <= quote.ts_ms:
            position = self.account.position
            if position.is_flat:
                break
            notional = position.notional_at(quote.mark_price)
            paid = quote.funding_rate * notional * Decimal(position.sign)
            self.account.cumulative_funding_paid += paid
            expected = quote.next_funding_time_ms
            mismatch = expected is not None and expected != boundary + FUNDING_INTERVAL_MS and expected != boundary
            if mismatch:
                self.funding_grid_mismatches += 1
            # A settlement more than one interval behind the previous observation was missed
            # while nothing was watching: the process was down, or the feed was gone. The rate
            # and the notional used here are *today's*, not the ones that applied back then, so
            # the amount is an estimate. Saying so is the only honest option, because the real
            # rate for a past settlement is not on any live endpoint.
            catch_up = quote.ts_ms - boundary > FUNDING_INTERVAL_MS
            if catch_up:
                self.funding_catch_ups += 1
            self.ledger.append(
                ts_ms=boundary, event_type=EventType.FUNDING, settlement_ts_ms=boundary,
                funding_rate=quote.funding_rate, position_signed_qty=position.signed_qty,
                mark_price=quote.mark_price, notional=notional, amount_paid=paid,
                direction="PAID" if paid > 0 else ("RECEIVED" if paid < 0 else "ZERO"),
                cost_class=CASH_SEPARATE, provider_next_funding_time_ms=expected,
                provider_grid_mismatch=mismatch, catch_up=catch_up,
                observation_gap_ms=quote.ts_ms - boundary,
                rate_basis=("CURRENT_RATE_APPLIED_TO_PAST_SETTLEMENT" if catch_up
                            else "RATE_AT_SETTLEMENT"),
                cumulative_funding_paid=self.account.cumulative_funding_paid)
            boundary += FUNDING_INTERVAL_MS

    def _check_liquidation(self, quote: Quote) -> None:
        if not self.account.is_liquidatable(self.tiers, quote.mark_price):
            return
        position = self.account.position
        liquidation_price = self.account.liquidation_price(self.tiers, quote.mark_price)
        self.ledger.append(
            ts_ms=quote.ts_ms, event_type=EventType.LIQUIDATION,
            trigger="MARK_PRICE", mark_price=quote.mark_price,
            computed_liquidation_price=liquidation_price,
            position_signed_qty=position.signed_qty, avg_entry=position.avg_entry,
            used_margin=self.account.used_margin,
            unrealized_pnl=self.account.unrealized_pnl(quote.mark_price),
            maintenance_margin=self.account.maintenance_margin(self.tiers, quote.mark_price),
            fill_basis=self.config.liquidation_fill_basis)
        self.liquidation_count += 1
        # MARK_AT_TRIGGER: the trigger is mark based, so the forced close uses the same
        # authority. If the mark gapped past the computed liquidation price the loss is larger,
        # which is the honest outcome rather than a convenient one.
        self._settle_reduce(quote.ts_ms, fill_price=quote.mark_price, close_qty=position.abs_qty,
                            reason="LIQUIDATION", liquidity="TAKER", mark_price=quote.mark_price)

    def _fee_for(self, price: Decimal, qty: Decimal, liquidity: str) -> Decimal:
        """The only fee arithmetic in the engine (contract C6). Sizing, filling, settling and
        the margin check all read this one function instead of repeating a bps expression."""
        return price * qty * self.config.fees.rate_for(liquidity)

    # ------------------------------------------------------------------ orders

    def submit_order(self, *, ts_ms: int, side: str, qty: Decimal, intent: str,
                     request_id: str, reason: str = "MANUAL", confirmed: bool = False) -> dict[str, Any]:
        quote = self._require_quote()
        submitted = self.ledger.append(
            ts_ms=ts_ms, event_type=EventType.ORDER_SUBMITTED, request_id=request_id,
            side=side, order_type="MARKET", qty=qty, intent=intent, reason=reason,
            mode=self.state.mode, leverage=self.leverage,
            best_bid=quote.best_bid, best_ask=quote.best_ask, mark_price=quote.mark_price)
        try:
            return self._execute(ts_ms=ts_ms, side=side, qty=qty, intent=intent,
                                 request_id=request_id, reason=reason, quote=quote,
                                 confirmed=confirmed)
        except (OrderRejected, NoLiquidity, ValueError) as exc:
            code = exc.code if isinstance(exc, OrderRejected) else (
                "NO_LIQUIDITY" if isinstance(exc, NoLiquidity) else "INVALID_ORDER")
            self.ledger.append(ts_ms=ts_ms, event_type=EventType.ORDER_REJECTED,
                               request_id=request_id, side=side, qty=qty, intent=intent,
                               code=code, message=str(exc), submitted_seq=submitted["seq"])
            raise

    def _execute(self, *, ts_ms: int, side: str, qty: Decimal, intent: str, request_id: str,
                 reason: str, quote: Quote, confirmed: bool) -> dict[str, Any]:
        spec = self.config.instrument
        position = self.account.position

        if intent not in {"OPEN", "CLOSE"}:
            raise OrderRejected("UNKNOWN_INTENT", f"unknown intent {intent}")
        if side not in {SIDE_LONG, SIDE_SHORT}:
            raise OrderRejected("UNKNOWN_SIDE", f"unknown side {side}")

        if intent == "CLOSE":
            if position.is_flat:
                raise OrderRejected("NO_POSITION", "there is no position to close")
            if position.side != side:
                raise OrderRejected("CLOSE_SIDE_MISMATCH",
                                    f"position is {position.side}, close requested for {side}")
            delta = -position.signed_qty if qty is None else Decimal(qty) * Decimal(-position.sign)
        else:
            blocked = self.state.blocks_new_entry_reason()
            if blocked is not None:
                raise OrderRejected(blocked, f"new entries are blocked in {self.state.mode}")
            if not position.is_flat and position.side != side:
                # Contract S4 plus the D3 reverse policy: flipping through zero is not an order.
                raise OrderRejected("REVERSE_NOT_ALLOWED",
                                    f"position is {position.side}; close it before opening {side}")
            delta = Decimal(qty) * (Decimal(1) if side == SIDE_LONG else Decimal(-1))

        order_qty = abs(delta)
        if order_qty <= 0:
            raise OrderRejected("QTY_NOT_POSITIVE", "quantity must be positive")
        if order_qty < spec.min_order_qty:
            raise OrderRejected("QTY_BELOW_MINIMUM",
                                f"quantity {order_qty} is below minOrderQty {spec.min_order_qty}")
        if (order_qty / spec.qty_step) % 1 != 0:
            raise OrderRejected("QTY_OFF_GRID",
                                f"quantity {order_qty} is not on the {spec.qty_step} grid")
        if order_qty > spec.max_mkt_order_qty:
            raise OrderRejected("QTY_ABOVE_MARKET_MAXIMUM",
                                f"quantity {order_qty} exceeds maxMktOrderQty {spec.max_mkt_order_qty}")
        reducing = not position.is_flat and (position.sign * (1 if delta > 0 else -1)) < 0
        if reducing and order_qty > position.abs_qty:
            raise OrderRejected("REVERSE_NOT_ALLOWED",
                                f"reducing {order_qty} exceeds the open {position.abs_qty}")

        outcome = self._fill(quote, delta, order_qty, reducing)
        notional = outcome.fill_price * order_qty
        if not reducing and notional < spec.min_notional_value:
            raise OrderRejected("NOTIONAL_BELOW_MINIMUM",
                                f"notional {notional} is below minNotionalValue {spec.min_notional_value}")

        if not reducing:
            self._check_margin(position, outcome, order_qty, delta)

        self.ledger.append(
            ts_ms=ts_ms, event_type=EventType.FILL, request_id=request_id, side=side, intent=intent,
            fill_price=outcome.fill_price, fill_qty=order_qty, signed_delta=delta,
            notional=notional, book_avg_price=outcome.book_avg_price,
            reference_price=outcome.reference_price, levels_consumed=outcome.levels_consumed,
            slippage_component=outcome.slippage_component, liquidity="TAKER",
            spread=quote.spread, best_bid=quote.best_bid, best_ask=quote.best_ask,
            mark_price=quote.mark_price, price_embedded_costs=PRICE_EMBEDDED, reason=reason)

        if reducing:
            self._settle_reduce(ts_ms, fill_price=outcome.fill_price, close_qty=order_qty,
                                reason=reason, liquidity="TAKER", mark_price=quote.mark_price,
                                fee=outcome.fee, request_id=request_id)
        else:
            self._settle_increase(ts_ms, fill_price=outcome.fill_price, delta=delta,
                                  reason=reason, liquidity="TAKER", mark_price=quote.mark_price,
                                  fee=outcome.fee, request_id=request_id)
        self.account.assert_invariants(quote.mark_price)
        return {"fill_price": outcome.fill_price, "fill_qty": order_qty,
                "fee": outcome.fee, "levels_consumed": outcome.levels_consumed}

    def _fill(self, quote: Quote, delta: Decimal, order_qty: Decimal, reducing: bool) -> FillOutcome:
        buying = delta > 0
        side = quote.asks if buying else quote.bids
        reference = quote.best_ask if buying else quote.best_bid
        if reference is None:
            raise OrderRejected("NO_QUOTE", "the book side needed for this order is empty")
        walked = walk_book(side, order_qty)
        adjusted = self.config.slippage.adjust(walked.avg_price, 1 if buying else -1)
        fee = self._fee_for(adjusted, order_qty, "TAKER")
        return FillOutcome(fill_price=adjusted, fill_qty=order_qty, book_avg_price=walked.avg_price,
                           reference_price=reference, levels_consumed=walked.levels_consumed,
                           slippage_component=adjusted - walked.avg_price, fee=fee)

    def _check_margin(self, position: Position, outcome: FillOutcome, order_qty: Decimal,
                      delta: Decimal) -> None:
        new_abs = position.abs_qty + order_qty
        new_avg = ((position.entry_notional + outcome.fill_price * order_qty) / new_abs
                   if new_abs > 0 else Decimal(0))
        new_margin = new_abs * new_avg / self.leverage
        required = new_margin - position.initial_margin + outcome.fee
        if required > self.account.available_balance:
            raise OrderRejected(
                "INSUFFICIENT_MARGIN",
                f"requires {required} but available balance is {self.account.available_balance}")
        notional = new_abs * new_avg
        highest = self.tiers.tiers[-1].upper_inclusive
        if notional > highest:
            raise OrderRejected("RISK_LIMIT_EXCEEDED",
                                f"notional {notional} exceeds the highest risk limit {highest}")
        tier = self.tiers.tier_for_notional(notional)
        if self.leverage > tier.max_leverage:
            raise OrderRejected(
                "LEVERAGE_ABOVE_TIER",
                f"leverage {self.leverage} exceeds tier {tier.risk_id} maximum {tier.max_leverage}")

    def _charge_fee(self, ts_ms: int, *, amount: Decimal, liquidity: str, reason: str,
                    request_id: str | None) -> None:
        self.account.cumulative_fees += amount
        self.ledger.append(ts_ms=ts_ms, event_type=EventType.FEE, amount=amount, liquidity=liquidity,
                           fee_rate=self.config.fees.rate_for(liquidity),
                           fee_schedule_version=self.config.fees.version, cost_class=CASH_SEPARATE,
                           reason=reason, request_id=request_id,
                           cumulative_fees=self.account.cumulative_fees)

    def _settle_increase(self, ts_ms: int, *, fill_price: Decimal, delta: Decimal, reason: str,
                         liquidity: str, mark_price: Decimal, fee: Decimal,
                         request_id: str | None) -> None:
        position = self.account.position
        order_qty = abs(delta)
        opening = position.is_flat
        new_abs = position.abs_qty + order_qty
        position.avg_entry = (position.entry_notional + fill_price * order_qty) / new_abs
        position.signed_qty = position.signed_qty + delta
        position.leverage = self.leverage
        if opening:
            position.opened_ts_ms = ts_ms
            position.reset_excursions()
        position.observe_excursion(mark_price)
        self._charge_fee(ts_ms, amount=fee, liquidity=liquidity, reason=reason, request_id=request_id)
        self.ledger.append(
            ts_ms=ts_ms, event_type=EventType.POSITION_OPEN if opening else EventType.POSITION_INCREASE,
            request_id=request_id, signed_qty=position.signed_qty, avg_entry=position.avg_entry,
            leverage=position.leverage, initial_margin=position.initial_margin,
            used_margin=self.account.used_margin, available_balance=self.account.available_balance,
            equity=self.account.equity(mark_price), mark_price=mark_price,
            liquidation_price=self.account.liquidation_price(self.tiers, mark_price),
            risk_tier=self.tiers.tier_for_notional(position.notional_at(mark_price)).risk_id,
            reason=reason)

    def _settle_reduce(self, ts_ms: int, *, fill_price: Decimal, close_qty: Decimal, reason: str,
                       liquidity: str, mark_price: Decimal, fee: Decimal | None = None,
                       request_id: str | None = None) -> None:
        position = self.account.position
        sign = position.sign
        gross = (fill_price - position.avg_entry) * close_qty * Decimal(sign)
        self.account.realized_pnl += gross
        if fee is None:
            fee = self._fee_for(fill_price, close_qty, liquidity)
        entry_avg = position.avg_entry
        opened_ts_ms = position.opened_ts_ms
        mae, mfe = position.max_adverse_excursion, position.max_favourable_excursion
        mae_price, mfe_price = position.mae_price, position.mfe_price
        position.signed_qty = position.signed_qty + Decimal(-sign) * close_qty
        closed_out = position.signed_qty == 0
        if closed_out:
            position.avg_entry = Decimal(0)
            position.opened_ts_ms = None
            position.reset_excursions()
        self._charge_fee(ts_ms, amount=fee, liquidity=liquidity, reason=reason, request_id=request_id)
        self.ledger.append(
            ts_ms=ts_ms,
            event_type=EventType.POSITION_CLOSE if closed_out else EventType.POSITION_REDUCE,
            request_id=request_id, closed_qty=close_qty, entry_avg=entry_avg, exit_price=fill_price,
            gross_pnl=gross, fee=fee, net_pnl=gross - fee, remaining_signed_qty=position.signed_qty,
            realized_pnl=self.account.realized_pnl, used_margin=self.account.used_margin,
            available_balance=self.account.available_balance,
            equity=self.account.equity(mark_price), mark_price=mark_price, reason=reason,
            side=("LONG" if sign > 0 else "SHORT"), leverage=self.leverage,
            opened_ts_ms=opened_ts_ms,
            hold_ms=(ts_ms - opened_ts_ms) if opened_ts_ms is not None else None,
            max_adverse_excursion=mae, max_favourable_excursion=mfe,
            mae_mark_price=mae_price, mfe_mark_price=mfe_price,
            cost_class_note="gross_pnl is fill based; fee is the only cash charge here")
        if closed_out and self.state.mode == "AUTO_STOPPING":
            previous = self.state.transition(POSITION_FLAT)
            self.ledger.append(ts_ms=ts_ms, event_type=EventType.MODE_CHANGE, action=POSITION_FLAT,
                               previous_mode=previous, mode=self.state.mode, reason="FLAT_REACHED")

    # ------------------------------------------------------------------ controls

    def set_leverage(self, *, ts_ms: int, leverage: Decimal) -> None:
        if not self.account.position.is_flat:
            raise OrderRejected("LEVERAGE_LOCKED_WHILE_OPEN",
                                "leverage can only change while the position is flat")
        self.config.validate_leverage(leverage)
        previous = self.leverage
        self.leverage = leverage
        self.ledger.append(ts_ms=ts_ms, event_type=EventType.LEVERAGE_CHANGE,
                           previous_leverage=previous, leverage=leverage)

    def set_mode(self, *, ts_ms: int, action: str, confirmed: bool = False,
                 reason: str = "OPERATOR") -> dict[str, Any]:
        previous = self.state.transition(action, confirmed=confirmed)
        self.ledger.append(ts_ms=ts_ms, event_type=EventType.MODE_CHANGE, action=action,
                           previous_mode=previous, mode=self.state.mode, reason=reason,
                           confirmed=confirmed)
        result: dict[str, Any] = {"previous_mode": previous, "mode": self.state.mode,
                                  "closed": None, "failed": None}
        if self.state.mode == EMERGENCY:
            # Contract E2: the block is already in place because the mode changed first.
            result.update(self.emergency_close(ts_ms=ts_ms, reason="EMERGENCY"))
        elif action == AUTO_OFF and self.account.position.is_flat:
            previous_stopping = self.state.transition(POSITION_FLAT)
            self.ledger.append(ts_ms=ts_ms, event_type=EventType.MODE_CHANGE, action=POSITION_FLAT,
                               previous_mode=previous_stopping, mode=self.state.mode,
                               reason="FLAT_AT_AUTO_OFF")
            result["mode"] = self.state.mode
        return result

    def reset_account(self, *, ts_ms: int, target_krw: Decimal,
                      reason: str = "USER_RESET") -> dict[str, Any]:
        """Restore the spendable balance to `target_krw` without touching the record.

        Refused while a position is open, and the position is not closed on the operator's
        behalf: an automatic liquidation to make a balance reset succeed would turn a
        bookkeeping action into a trade, at a price nobody chose. The operator closes first.
        """
        if not self.account.position.is_flat:
            raise OrderRejected(
                "RESET_BLOCKED_OPEN_POSITION",
                f"a {self.account.position.side} position is open; close it before resetting")
        if target_krw <= 0:
            raise OrderRejected("RESET_TARGET_INVALID", f"reset target must be positive: {target_krw}")

        target_usdt = self.config.fx.to_usdt(target_krw)
        quote = self.quote
        mark = quote.mark_price if quote is not None else Decimal(0)
        rate = self.config.fx.krw_per_usdt
        before = self.account.equity(mark)
        # On a shared wallet the reset is the wallet's, not this symbol's share of it:
        # re-anchoring one member would leave the others' accumulated deltas in the purse and
        # the balance would not land on the target.
        if self.account.cash is not None:
            self.account.cash.apply_capital_reset(target_usdt, ts_ms)
        else:
            self.account.apply_capital_reset(target_usdt, ts_ms)
        after = self.account.equity(mark)

        self.ledger.append(
            ts_ms=ts_ms, event_type=EventType.ACCOUNT_RESET, reason=reason,
            before_equity=before, after_equity=after,
            before_equity_krw=before * rate, after_equity_krw=target_krw,
            target_capital_usdt=target_usdt, target_capital_krw=target_krw,
            fixed_fx_rate=rate,
            # Carried so a reader of this one line can see that nothing was erased.
            preserved_realized_pnl=self.account.realized_pnl,
            preserved_fees=self.account.cumulative_fees,
            preserved_funding=self.account.cumulative_funding_paid,
            reset_count=self.account.reset_count, mode=self.state.mode,
            starting_capital_usdt=self.account.starting_capital_usdt)
        self.account.assert_invariants(mark)
        return {"before_equity": before, "after_equity": after,
                "reset_count": self.account.reset_count}

    def emergency_close(self, *, ts_ms: int, reason: str = "EMERGENCY") -> dict[str, Any]:
        """Close whatever is open, and report a failure instead of swallowing it (E4)."""
        if self.account.position.is_flat:
            return {"closed": None, "failed": None}
        position = self.account.position
        side = position.side
        qty = position.abs_qty
        try:
            outcome = self.submit_order(ts_ms=ts_ms, side=side, qty=qty, intent="CLOSE",
                                        request_id=f"emergency-{ts_ms}", reason=reason)
        except Exception as exc:  # E4: a position that could not be closed must be visible
            failure = {"side": side, "qty": str(qty), "error": str(exc),
                       "code": getattr(exc, "code", type(exc).__name__)}
            self.ledger.append(ts_ms=ts_ms, event_type=EventType.ORDER_REJECTED,
                               request_id=f"emergency-{ts_ms}", side=side, qty=qty, intent="CLOSE",
                               code=failure["code"], message=failure["error"],
                               emergency_failure=True)
            return {"closed": None, "failed": failure}
        return {"closed": {"side": side, "qty": str(qty), "fill_price": str(outcome["fill_price"])},
                "failed": None}

    # ------------------------------------------------------------------- views

    def _require_quote(self) -> Quote:
        if self.quote is None:
            raise OrderRejected("NO_QUOTE", "no market quote has been observed yet")
        return self.quote

    def snapshot(self) -> dict[str, Any]:
        quote = self.quote
        mark = quote.mark_price if quote is not None else Decimal(0)
        account = self.account.view(self.tiers, mark) if quote is not None else None
        return {
            "run_id": self.config.run_id,
            "state": self.state.view(),
            "leverage": self.leverage,
            "quote": ({"ts_ms": quote.ts_ms, "best_bid": quote.best_bid, "best_ask": quote.best_ask,
                       "spread": quote.spread, "mid": quote.mid, "mark_price": quote.mark_price,
                       "last_price": quote.last_price, "index_price": quote.index_price,
                       "funding_rate": quote.funding_rate,
                       "next_funding_time_ms": quote.next_funding_time_ms,
                       "bid_depth_top5": quote.bids.depth(5), "ask_depth_top5": quote.asks.depth(5)}
                      if quote is not None else None),
            "account": account,
            "liquidation_count": self.liquidation_count,
            "funding_grid_mismatches": self.funding_grid_mismatches,
            "funding_catch_ups": self.funding_catch_ups,
            "ledger_event_count": len(self.ledger.events),
        }


def apply_tape_record(engine: PaperEngine, record: dict[str, Any]) -> None:
    """Apply one tape record. This is the only path a replay uses."""
    kind = record["kind"]
    payload = record["payload"]
    if kind == InputKind.MARKET:
        engine.apply_market(quote_from_payload(payload))
        return
    if kind != InputKind.COMMAND:
        raise ValueError(f"unknown tape record kind: {kind}")
    command = payload["command"]
    ts_ms = int(payload["ts_ms"])
    if command == "START":
        # A tape can carry a second START: before the session learned to read its start time
        # from the ledger, a restart after a segment rotation could not find the original and
        # issued another. Replaying it would raise and the run would refuse to load, so the
        # later one is ignored. The first START is the one that happened.
        if not engine._started:
            engine.start(ts_ms)
    elif command == "ORDER":
        try:
            engine.submit_order(ts_ms=ts_ms, side=payload["side"], qty=Decimal(str(payload["qty"])),
                                intent=payload["intent"], request_id=payload["request_id"],
                                reason=payload.get("reason", "MANUAL"))
        except (OrderRejected, NoLiquidity, ValueError):
            pass  # the rejection is already a ledger event; a replay must not diverge here
    elif command == "SET_LEVERAGE":
        try:
            engine.set_leverage(ts_ms=ts_ms, leverage=Decimal(str(payload["leverage"])))
        except (OrderRejected, ValueError):
            pass
    elif command == "SET_MODE":
        try:
            engine.set_mode(ts_ms=ts_ms, action=payload["action"],
                            confirmed=bool(payload.get("confirmed", False)),
                            reason=payload.get("reason", "OPERATOR"))
        except TransitionRejected:
            pass
    elif command == "ACCOUNT_RESET":
        try:
            engine.reset_account(ts_ms=ts_ms, target_krw=Decimal(str(payload["target_krw"])),
                                 reason=payload.get("reason", "USER_RESET"))
        except (OrderRejected, ValueError):
            pass  # the refusal is not a ledger event, and a replay must not diverge here
    elif command == "EMERGENCY_CLOSE":
        engine.emergency_close(ts_ms=ts_ms, reason=payload.get("reason", "EMERGENCY"))
    else:
        raise ValueError(f"unknown command: {command}")


def replay_tape(config: PaperRunConfig, tiers: RiskTierTable, tape: InputTape) -> PaperEngine:
    """Rebuild an engine from its recorded inputs. Used to prove ledger determinism (T1/T8)."""
    engine = PaperEngine(config, tiers)
    for record in tape:
        apply_tape_record(engine, record)
    return engine
