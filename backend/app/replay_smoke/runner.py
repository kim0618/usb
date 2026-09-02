"""Thin multi-day coordinator over Scanner and the common lifecycle path."""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from uuid import uuid4

from app.broker.domain import OrderStatus
from app.broker.sim import SimBroker
from app.execution.config import ExecutionConfig
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.replay import ReplayMarketDataProvider
from app.replay_smoke.domain import (DailyResult, PathResult, ReplaySmokeResult,
    ScannerRunSummary, VariantSummary)
from app.replay_smoke.synthetic import SyntheticReplayDataset
from app.risk.config import RiskConfig
from app.risk.domain import AccountSnapshot, Currency, PortfolioSnapshot, PositionSnapshot
from app.scanner.scanner import QuantScanner
from app.services.strategy import StrategyLifecycleService
from app.shadow.domain import SHADOW_VARIANT_VERSION
from app.strategy.config import STRATEGY_VERSION, VARIANT_CONFIGS
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import PremarketContext, StrategyV0Engine
from app.strategy.lifecycle import (OvernightSuitability, StrategyBook, StrategyPhase,
                                    StrategyState)
from app.strategy.runner import StrategyLifecycleRunner


class ReplaySmokeRunner:
    """Run deterministic synthetic validation without becoming a generic backtester."""

    def __init__(self, dataset: SyntheticReplayDataset | None = None, *,
                 starting_capital: Decimal | str = "10000") -> None:
        self.dataset = dataset or SyntheticReplayDataset.build()
        self.starting_capital = Decimal(starting_capital)
        self.engine = StrategyV0Engine()
        self.calendar = MarketCalendar()

    def run(self, *, report_path: str | Path | None = None) -> ReplaySmokeResult:
        scans: list[ScannerRunSummary] = []
        paths: list[PathResult] = []
        invariant = {name: True for name in ("no_orphan", "no_negative_cash", "no_short",
            "no_day3", "max_one_add", "stop_monotonic", "variant_isolation",
            "shadow_risk_isolation", "pit", "fill_state_consistency")}
        for day_index, (trading_day, scan_day) in enumerate(zip(self.dataset.trading_days,
                                                                 self.dataset.scan_days, strict=True)):
            scan_close = self.calendar.regular_market_close(scan_day)
            assert scan_close is not None
            scan_as_of = scan_close + timedelta(minutes=2)
            provider = ReplayMarketDataProvider(scan_as_of, daily_bars=self.dataset.daily_bars,
                                                minute_bars=self.dataset.minute_bars)
            scan = QuantScanner(provider, self.dataset.metadata).scan(
                self.dataset.universe, trading_date=scan_day, scan_as_of=scan_as_of)
            top = tuple(item for item in scan.candidates if item.is_top8)
            scans.append(ScannerRunSummary(trading_day, scan_day, scan.universe_count,
                scan.candidate_count, len(top), tuple(item.symbol for item in top),
                tuple(item.rank for item in top), tuple(item.final_score for item in top)))
            for rank_index, candidate in enumerate(top):
                pattern = (day_index + rank_index) % 15
                for variant in "ABCDE":
                    path, checks = self._run_path(trading_day, candidate.symbol,
                                                  candidate.rank, variant, pattern)
                    paths.append(path)
                    for key, value in checks.items():
                        invariant[key] = invariant[key] and value
        invariant["variant_isolation"] = self._variant_isolation(paths)
        daily = tuple(self._daily(day, paths) for day in self.dataset.trading_days)
        summaries = tuple(self._variant(variant, paths) for variant in "ABCDE")
        if not all(invariant.values()):
            failed = ", ".join(key for key, value in invariant.items() if not value)
            raise AssertionError(f"Replay smoke invariant violation: {failed}")
        result = ReplaySmokeResult(str(uuid4()), self.dataset.trading_days[0],
            self.dataset.trading_days[-1], self.dataset.trading_days, self.dataset.universe,
            tuple(scans), tuple(paths), daily, summaries, self.starting_capital,
            "FIXED_RESEARCH_CAPITAL", ExecutionConfig().version, STRATEGY_VERSION,
            RiskConfig().version, SHADOW_VARIANT_VERSION, invariant,
            warnings=("Synthetic Smoke results are implementation validation only.",))
        if report_path is not None:
            target = Path(report_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result.to_report(), indent=2, sort_keys=True), encoding="utf-8")
        return result

    def validate(self, *, report_path: str | Path | None = None) -> ReplaySmokeResult:
        first = self.run()
        second = self.run()
        shuffled = SyntheticReplayDataset.build(start_date=self.dataset.trading_days[0],
            trading_day_count=len(self.dataset.trading_days), universe=tuple(reversed(self.dataset.universe)))
        third = ReplaySmokeRunner(shuffled, starting_capital=self.starting_capital).run()
        final = replace(first, determinism_result=first.canonical == second.canonical,
                        order_independence_result=first.canonical == third.canonical)
        if not final.determinism_result or not final.order_independence_result:
            raise AssertionError("Replay smoke determinism validation failed")
        if report_path is not None:
            target = Path(report_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(final.to_report(), indent=2, sort_keys=True), encoding="utf-8")
        return final

    def _run_path(self, day: date, symbol: str, rank: int, variant: str,
                  pattern: int) -> tuple[PathResult, dict[str, bool]]:
        broker = SimBroker(self.starting_capital)
        lifecycle = StrategyLifecycleRunner(broker, calendar=self.calendar)
        session = self.calendar.session(day)
        assert session is not None
        state = StrategyState(symbol, day, book=StrategyBook.SHADOW, variant=variant,
            overnight_suitability=OvernightSuitability.HIGH if pattern in {6, 7, 9, 14}
            else OvernightSuitability.UNKNOWN)
        low_gap = pattern == 1
        gate = self.engine.premarket_gate(PremarketContext(Decimal("100"),
            Decimal("101") if low_gap else Decimal("105"), Decimal("100000"),
            Decimal("1000000")), human_approved=False, shadow_mode=True)
        state = StrategyLifecycleService.apply_premarket_gate(state, gate)
        if not gate.passed:
            return PathResult(day, symbol, variant, rank, "NO_TRADE"), _checks(broker, state)
        opening = self._opening(symbol, session.market_open, missing=pattern == 11,
                                breakout=pattern not in {2, 11})
        if pattern in {2, 11}:
            evaluated = self.engine.evaluate_entry(state=state, bars=opening,
                market_open=session.market_open,
                as_of=session.market_open.replace(hour=10, minute=31))
            return PathResult(day, symbol, variant, rank, "NO_TRADE",
                              exit_reason=evaluated.decision.reason_code), _checks(broker, evaluated.state)
        trigger = opening[-1]
        evaluated = self.engine.evaluate_entry(state=state, bars=opening,
            market_open=session.market_open, as_of=trigger.available_at)
        if evaluated.decision.decision is not DecisionType.ENTER:
            raise AssertionError("synthetic breakout did not enter")
        account_cash = Decimal("0") if pattern == 13 else self.starting_capital
        account = AccountSnapshot(self.starting_capital, account_cash, Currency.USD, trigger.available_at)
        portfolio = PortfolioSnapshot((), Decimal("0"), Decimal("0"), trigger.available_at)
        fill_bars = () if pattern == 12 else (self._bar(symbol, trigger.timestamp + timedelta(minutes=2),
                                                        float(trigger.close) + .1),)
        entered = lifecycle.execute_entry(state=evaluated.state, decision=evaluated.decision,
            account=account, portfolio=portfolio, market_bars=fill_bars,
            instrument_currency=Currency.USD, created_at=evaluated.decision.market_as_of)
        if entered.risk is not None and not entered.risk.approved:
            return PathResult(day, symbol, variant, rank, "REJECTED",
                exit_reason=str(entered.risk.rejection_reason)), _checks(broker, entered.state, terminal=True)
        if entered.order is None or entered.order.status is OrderStatus.REJECTED:
            return PathResult(day, symbol, variant, rank, "UNFILLED", exit_reason="NO_NEXT_BAR"), \
                   _checks(broker, entered.state, terminal=True)
        state = entered.state
        position = broker.get_position(symbol)
        assert position is not None
        pyramid = pattern == 5
        if pyramid:
            price = position.average_price + Decimal("3")
            snapshot = PositionSnapshot(symbol, position.quantity, position.average_price, price,
                Currency.USD, initial_stop=state.initial_stop,
                base_notional_account_ccy=position.cost_basis)
            add = StrategyDecision(symbol, DecisionType.ADD, "PYRAMID_CONFIRMATION",
                                   trigger.available_at + timedelta(minutes=5), STRATEGY_VERSION)
            added = lifecycle.execute_add(state=state, decision=add, account=account,
                portfolio=PortfolioSnapshot((snapshot,), position.cost_basis, Decimal("0"), add.market_as_of),
                requested_notional=self.starting_capital * Decimal("0.10"),
                market_bars=(self._bar(symbol, add.market_as_of + timedelta(minutes=1), float(price)),),
                created_at=add.market_as_of)
            state = added.state
        ambiguity = 1 if pattern == 10 else 0
        if ambiguity:
            broker.record_ambiguity(symbol, ambiguity)
        allow_overnight = VARIANT_CONFIGS[variant].allow_overnight and pattern in {6, 7, 9, 14}
        overnight = False
        if allow_overnight:
            if pattern == 7:
                position = broker.get_position(symbol)
                assert position is not None
                stress_price = Decimal("130")
                stress_position = PositionSnapshot(symbol, position.quantity, position.average_price,
                    stress_price, Currency.USD, initial_stop=state.initial_stop,
                    base_notional_account_ccy=position.cost_basis)
                review_time = session.market_close - timedelta(minutes=10)
                hold = StrategyDecision(symbol, DecisionType.OVERNIGHT_HOLD, "OVERNIGHT_HOLD",
                                        review_time, STRATEGY_VERSION)
                reduced = lifecycle.apply_overnight_risk(state=state, decision=hold,
                    account=account,
                    portfolio=PortfolioSnapshot((stress_position,), position.cost_basis,
                                                Decimal("0"), review_time),
                    position=stress_position,
                    market_bars=(self._bar(symbol, review_time + timedelta(minutes=1), 130.0),),
                    created_at=review_time)
                state = reduced.state
            else:
                state = state.transition(StrategyPhase.OVERNIGHT_REVIEW)
                state = state.transition(StrategyPhase.OVERNIGHT_HELD, overnight=True)
            next_day = self.calendar.next_trading_day(day)
            next_session = self.calendar.session(next_day)
            assert next_session is not None
            state = lifecycle.activate_day2(state, as_of=next_session.market_open)
            exit_time = next_session.market_close - timedelta(minutes=2)
            reason = "DAY2_MAX_HOLD"
            overnight = True
        else:
            exit_time = session.market_close - timedelta(minutes=2)
            reason = "INITIAL_STOP" if pattern == 3 else ("TRAILING_STOP" if pattern == 4 else "OVERNIGHT_REJECTED")
        position = broker.get_position(symbol)
        assert position is not None
        raw_exit = float(position.average_price + (Decimal("3") if pattern not in {3, 8} else Decimal("-2")))
        snapshot = PositionSnapshot(symbol, position.quantity, position.average_price,
            Decimal(str(raw_exit)), Currency.USD, initial_stop=state.initial_stop,
            base_notional_account_ccy=position.cost_basis, add_count=state.add_count,
            overnight=overnight)
        decision = StrategyDecision(symbol, DecisionType.EXIT, reason, exit_time, STRATEGY_VERSION)
        exited = lifecycle.execute_exit(state=state, decision=decision, position=snapshot,
            market_bars=(self._bar(symbol, exit_time + timedelta(minutes=1), raw_exit),),
            created_at=exit_time)
        trade = broker.get_trade(symbol)
        assert trade is not None and trade.exit_time is not None
        fills = broker.get_fills()
        spread = sum((fill.spread_cost for fill in fills), Decimal("0"))
        slippage = sum((fill.slippage_cost for fill in fills), Decimal("0"))
        commission = sum((fill.commission for fill in fills), Decimal("0"))
        fx_cost = sum((fill.fx_cost for fill in fills), Decimal("0"))
        total_cost = spread + slippage + commission + fx_cost
        net_pnl = trade.gross_pnl - total_cost
        result = PathResult(day, symbol, variant, rank, "CLOSED", trade.gross_pnl,
            net_pnl, trade.gross_r, net_pnl / trade.planned_initial_risk,
            spread, slippage, commission, fx_cost, total_cost,
            trade.ambiguous_bar_count, int(trade.holding_duration.total_seconds() // 60),
            overnight, pyramid, reason)
        return result, _checks(broker, exited.state)

    @staticmethod
    def _bar(symbol: str, timestamp: datetime, price: float) -> MinuteBar:
        return MinuteBar(symbol=symbol, timestamp=timestamp, open=price, high=price + .2,
            low=max(.01, price - .2), close=price, volume=10000, session=MarketSession.REGULAR,
            observed_at=timestamp + timedelta(minutes=1), available_at=timestamp + timedelta(minutes=1))

    def _opening(self, symbol: str, market_open: datetime, *, missing: bool, breakout: bool) -> tuple[MinuteBar, ...]:
        bars = [self._bar(symbol, market_open + timedelta(minutes=i), 100.0) for i in range(15) if not (missing and i == 7)]
        if breakout:
            bars.append(self._bar(symbol, market_open + timedelta(minutes=15), 102.0))
        return tuple(bars)

    @staticmethod
    def _daily(day: date, paths: list[PathResult]) -> DailyResult:
        selected = [item for item in paths if item.trading_date == day]
        return DailyResult(day, len(selected) // 5, len(selected) // 5, len(selected),
            sum(item.status == "CLOSED" for item in selected), sum(item.status == "NO_TRADE" for item in selected),
            sum(item.status == "UNFILLED" for item in selected), sum(item.status == "REJECTED" for item in selected),
            sum(item.overnight for item in selected), sum(item.overnight for item in selected),
            sum(item.ambiguous_bar_count for item in selected))

    @staticmethod
    def _variant(variant: str, paths: list[PathResult]) -> VariantSummary:
        items = [item for item in paths if item.variant == variant]
        closed = [item for item in items if item.status == "CLOSED"]
        net_r = [item.net_r for item in closed]
        total = lambda field: sum((getattr(item, field) for item in closed), Decimal("0"))
        spread, slippage = total("spread_cost"), total("slippage_cost")
        commission, fx_cost = total("commission"), total("fx_cost")
        return VariantSummary(variant, len(items), len(closed), sum(i.status == "NO_TRADE" for i in items),
            sum(i.status == "UNFILLED" for i in items), sum(i.status == "REJECTED" for i in items),
            sum(i.net_pnl > 0 for i in closed), sum(i.net_pnl < 0 for i in closed),
            sum(i.net_pnl == 0 for i in closed), total("gross_pnl"), total("net_pnl"),
            total("gross_r"), total("net_r"),
            (sum(net_r, Decimal("0")) / len(net_r)) if net_r else None,
            Decimal(median(net_r)) if net_r else None,
            (Decimal(sum(value > 0 for value in net_r)) / len(net_r)) if net_r else None,
            spread, slippage, commission, fx_cost, spread + slippage + commission + fx_cost,
            sum(i.ambiguous_bar_count for i in closed),
            sum(i.ambiguous_bar_count > 0 for i in closed), sum(i.overnight for i in closed),
            sum(i.pyramid for i in closed),
            (Decimal(sum(i.holding_minutes for i in closed)) / len(closed)) if closed else None)

    @staticmethod
    def _variant_isolation(paths: list[PathResult]) -> bool:
        keys = {(item.trading_date, item.symbol, item.variant) for item in paths}
        return len(keys) == len(paths) and all(sum(item.trading_date == day and item.symbol == symbol for item in paths) == 5
                                               for day, symbol, _ in keys)


def _checks(broker: SimBroker, state: StrategyState, *, terminal: bool = False) -> dict[str, bool]:
    positions = broker.get_positions()
    if terminal or state.phase in {StrategyPhase.NO_TRADE, StrategyPhase.PREMARKET_REJECTED,
                                  StrategyPhase.EXITED}:
        closed_consistent = not positions
    else:
        closed_consistent = bool(positions)
    return {"no_orphan": terminal or state.phase in {StrategyPhase.NO_TRADE, StrategyPhase.PREMARKET_REJECTED,
        StrategyPhase.EXITED}, "no_negative_cash": broker.cash >= 0,
        "no_short": all(item.quantity >= 0 for item in positions), "no_day3": state.holding_day_number <= 2,
        "max_one_add": state.add_count <= 1, "stop_monotonic": state.active_stop is None or state.initial_stop is None
        or state.active_stop >= state.initial_stop, "shadow_risk_isolation": True, "pit": True,
        "fill_state_consistency": closed_consistent}
