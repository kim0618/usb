"""Fixed-number Stage 5 money, risk, separation, and persistence tests."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base, create_db_engine
from app.execution.domain import IntentType
from app.models.risk import DailySymbolState
from app.repositories.risk import DailyRiskRepository
from app.risk.config import RiskConfig
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, FxRate, PortfolioSnapshot,
    PositionSnapshot, RiskRejectionReason, decimal_from,
)
from app.risk.engine import RiskEngine
from app.services.risk import RiskService
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility

NOW = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
DAY = date(2026, 8, 31)


def account(equity: str = "100000", cash: str | None = None, currency: Currency = Currency.USD) -> AccountSnapshot:
    return AccountSnapshot(Decimal(equity), Decimal(cash or equity), currency, NOW)


def portfolio(*positions: PositionSnapshot, base: str = "0", pyramid: str = "0") -> PortfolioSnapshot:
    return PortfolioSnapshot(tuple(positions), Decimal(base), Decimal(pyramid), NOW)


def decision(symbol: str = "AAA", kind: DecisionType = DecisionType.ENTER) -> StrategyDecision:
    return StrategyDecision(symbol, kind, "FIXED_TEST", NOW)


def evaluate(engine: RiskEngine | None = None, **overrides):  # type: ignore[no-untyped-def]
    values = dict(
        decision=decision(), eligibility=TradingEligibility(True), account=account(),
        portfolio=portfolio(), daily_state=DailyTradingState(DAY),
        entry_price=Decimal("100"), stop_price=Decimal("98"),
        instrument_currency=Currency.USD, created_at=NOW,
    )
    values.update(overrides)
    return (engine or RiskEngine()).evaluate_base_entry(**values)


def test_decimal_boundary_and_one_r_have_no_float_artifact() -> None:
    assert decimal_from(10.1) == Decimal("10.1")
    assert RiskEngine().one_r(account("1000000")) == Decimal("5000.000")
    with pytest.raises(ValueError):
        account("-1")
    assert evaluate(account=account("0")).rejection_reason is RiskRejectionReason.INVALID_ACCOUNT_STATE


def test_exact_same_currency_position_sizing() -> None:
    result = evaluate()
    assert result.approved and result.order_intent is not None and result.metrics is not None
    assert result.metrics.one_r == Decimal("500.000")
    assert result.metrics.per_share_risk == Decimal("2")
    assert result.order_intent.quantity == Decimal("250.000")
    assert result.order_intent.account_notional == Decimal("25000.000")
    assert result.order_intent.intent_type is IntentType.BASE_ENTRY


def test_fx_position_sizing_uses_account_currency_for_exposure() -> None:
    result = evaluate(
        account=account("1000000", currency=Currency.KRW),
        instrument_currency=Currency.USD,
        fx_rate=FxRate(Currency.USD, Currency.KRW, Decimal("1350")),
    )
    assert result.approved and result.metrics is not None
    assert result.metrics.per_share_risk == Decimal("2700")
    assert result.metrics.requested_quantity == Decimal("5000.000") / Decimal("2700")
    assert result.metrics.requested_notional_account_ccy == result.metrics.requested_quantity * Decimal("135000")


def test_currency_mismatch_and_invalid_fx_are_rejected() -> None:
    assert evaluate(
        account=account("1000000", currency=Currency.KRW), instrument_currency=Currency.USD
    ).rejection_reason is RiskRejectionReason.CURRENCY_MISMATCH
    with pytest.raises(ValueError):
        FxRate(Currency.USD, Currency.KRW, Decimal("0"))


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"entry_price": "0"}, RiskRejectionReason.INVALID_ENTRY_PRICE),
        ({"stop_price": "0"}, RiskRejectionReason.INVALID_STOP_PRICE),
        ({"stop_price": "100"}, RiskRejectionReason.INVALID_STOP_PRICE),
        ({"stop_price": "101"}, RiskRejectionReason.INVALID_STOP_PRICE),
    ],
)
def test_long_entry_and_stop_validation(kwargs, expected) -> None:  # type: ignore[no-untyped-def]
    assert evaluate(**kwargs).rejection_reason is expected


def test_base_symbol_and_cash_caps_apply_independently() -> None:
    wide_stop = dict(entry_price="100", stop_price="99.9")
    base = evaluate(engine=RiskEngine(RiskConfig(max_symbol_exposure_pct=Decimal("1"))), **wide_stop)
    symbol = evaluate(**wide_stop)
    cash = evaluate(account=account("100000", "10000"), **wide_stop)
    assert base.order_intent and base.order_intent.account_notional == Decimal("80000.00")
    assert symbol.order_intent and symbol.order_intent.account_notional == Decimal("60000.00")
    assert cash.order_intent and cash.order_intent.account_notional == Decimal("10000")
    assert base.order_intent.account_notional <= base.order_intent.quantity * Decimal("100")


def test_base_entry_never_consumes_pyramid_reserve() -> None:
    result = evaluate(
        engine=RiskEngine(RiskConfig(max_symbol_exposure_pct=Decimal("1"))),
        entry_price="100", stop_price="99.9", portfolio=portfolio(base="79000"),
    )
    assert result.order_intent and result.order_intent.account_notional == Decimal("1000.00")


def test_human_approval_enter_and_safe_mode_are_independent_gates() -> None:
    assert evaluate(decision=decision(kind=DecisionType.HOLD)).rejection_reason is RiskRejectionReason.NOT_ENTER_DECISION
    assert evaluate(eligibility=TradingEligibility(False)).rejection_reason is RiskRejectionReason.HUMAN_NOT_APPROVED
    assert evaluate(eligibility=TradingEligibility(True, safe_mode=True)).rejection_reason is RiskRejectionReason.SAFE_MODE
    assert evaluate().approved


def test_daily_attempt_symbol_and_planned_risk_guards() -> None:
    assert evaluate(
        daily_state=DailyTradingState(DAY, attempted_symbols=frozenset({"AAA"}))
    ).rejection_reason is RiskRejectionReason.SYMBOL_ALREADY_ATTEMPTED
    assert evaluate(
        daily_state=DailyTradingState(DAY, attempted_symbols=frozenset({"X", "Y"}))
    ).rejection_reason is RiskRejectionReason.DAILY_SYMBOL_LIMIT
    relaxed = RiskEngine(RiskConfig(max_new_symbols_per_day=10))
    state = DailyTradingState(DAY, attempted_symbols=frozenset({"X", "Y"}), planned_risk_reserved=Decimal("1000"))
    assert evaluate(engine=relaxed, daily_state=state).rejection_reason is RiskRejectionReason.DAILY_RISK_LIMIT


def test_pyramid_is_winner_only_once_and_reserve_limited() -> None:
    # The stop-risk budget is deliberately slack here so the reserve stays the
    # binding cap; the budget itself is exercised in the pyramid contract module.
    losing = PositionSnapshot("AAA", Decimal("10"), Decimal("100"), Decimal("99"), Currency.USD,
                              initial_stop=Decimal("98"))
    kwargs = dict(
        decision=decision(kind=DecisionType.ADD), eligibility=TradingEligibility(True),
        account=account(), daily_state=DailyTradingState(DAY), created_at=NOW,
        planned_initial_risk=Decimal("5000"),
        requested_notional_account_ccy=Decimal("30000"),
    )
    engine = RiskEngine()
    assert engine.evaluate_pyramid_add(portfolio=portfolio(losing), **kwargs).rejection_reason is RiskRejectionReason.POSITION_NOT_PROFITABLE
    winner = PositionSnapshot("AAA", Decimal("10"), Decimal("100"), Decimal("110"), Currency.USD,
                              initial_stop=Decimal("98"))
    approved = engine.evaluate_pyramid_add(portfolio=portfolio(winner), **kwargs)
    assert approved.order_intent and approved.order_intent.account_notional == Decimal("20000.00")
    added = PositionSnapshot("AAA", Decimal("10"), Decimal("100"), Decimal("110"), Currency.USD,
                             initial_stop=Decimal("98"), add_count=1)
    assert engine.evaluate_pyramid_add(portfolio=portfolio(added), **kwargs).rejection_reason is RiskRejectionReason.PYRAMID_LIMIT


def test_overnight_stress_fixed_numbers_and_position_limit() -> None:
    engine = RiskEngine()
    result = engine.overnight_stress("1000000", "300000", "-0.20")
    assert result.estimated_loss == Decimal("60000.00")
    assert result.account_loss_pct == Decimal("0.06")
    assert result.max_notional_by_stress == Decimal("250000")
    assert not result.within_limit
    for gap in ("0.10", "0.30"):
        assert engine.overnight_stress("1000000", "100000", gap).estimated_loss == Decimal("100000") * Decimal(gap)
    with pytest.raises(ValueError):
        engine.overnight_stress("1000000", "0", "0")
    overnight = PositionSnapshot("AAA", Decimal("1"), Decimal("100"), Decimal("100"), Currency.USD, overnight=True)
    assert not engine.can_add_overnight_position(portfolio(overnight))


def _service(db_path: Path, repository_type=DailyRiskRepository):  # type: ignore[no-untyped-def]
    db_engine = create_db_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(db_engine)
    session = Session(db_engine)
    return db_engine, session, RiskService(RiskEngine(), repository_type(session))


def test_persistent_reservations_survive_reload_and_block_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "risk.sqlite3"
    db_engine, session, service = _service(path)
    first = service.prepare_base_entry(
        trading_date=DAY, decision=decision(), eligibility=TradingEligibility(True),
        account=account(), portfolio=portfolio(), entry_price="100", stop_price="98",
        instrument_currency=Currency.USD, created_at=NOW,
    )
    assert first.approved
    session.close()
    with Session(db_engine) as restarted:
        restarted_service = RiskService(RiskEngine(), DailyRiskRepository(restarted))
        duplicate = restarted_service.prepare_base_entry(
            trading_date=DAY, decision=decision(), eligibility=TradingEligibility(True),
            account=account(), portfolio=portfolio(), entry_price="100", stop_price="98",
            instrument_currency=Currency.USD, created_at=NOW,
        )
        assert duplicate.rejection_reason is RiskRejectionReason.SYMBOL_ALREADY_ATTEMPTED
        loaded = DailyRiskRepository(restarted).load(DAY)
        assert loaded.planned_risk_reserved == Decimal("500.000")
        assert loaded.base_notional_reserved == Decimal("25000.000")
    db_engine.dispose()


def test_service_two_r_atomic_reservation_and_third_rejection(tmp_path: Path) -> None:
    db_engine, session, service = _service(tmp_path / "2r.sqlite3")
    for symbol in ("AAA", "BBB"):
        result = service.prepare_base_entry(
            trading_date=DAY, decision=decision(symbol), eligibility=TradingEligibility(True),
            account=account(), portfolio=portfolio(), entry_price="100", stop_price="98",
            instrument_currency=Currency.USD, created_at=NOW,
        )
        assert result.approved
    third = service.prepare_base_entry(
        trading_date=DAY, decision=decision("CCC"), eligibility=TradingEligibility(True),
        account=account(), portfolio=portfolio(), entry_price="100", stop_price="98",
        instrument_currency=Currency.USD, created_at=NOW,
    )
    assert third.rejection_reason is RiskRejectionReason.DAILY_RISK_LIMIT
    assert session.scalar(select(func.count()).select_from(DailySymbolState)) == 2
    session.close()
    db_engine.dispose()


def test_persistence_failure_rolls_back_reservation(tmp_path: Path) -> None:
    class FailingRepository(DailyRiskRepository):
        def reserve_entry(self, **kwargs):  # type: ignore[no-untyped-def]
            super().reserve_entry(**kwargs)
            raise RuntimeError("injected")

    db_engine, session, service = _service(tmp_path / "rollback.sqlite3", FailingRepository)
    with pytest.raises(RuntimeError, match="injected"):
        service.prepare_base_entry(
            trading_date=DAY, decision=decision(), eligibility=TradingEligibility(True),
            account=account(), portfolio=portfolio(), entry_price="100", stop_price="98",
            instrument_currency=Currency.USD, created_at=NOW,
        )
    assert session.scalar(select(func.count()).select_from(DailySymbolState)) == 0
    session.close()
    db_engine.dispose()


def test_risk_engine_has_no_sqlalchemy_or_research_dependency() -> None:
    import app.risk.engine as risk_module
    import app.strategy.engine as strategy_module

    risk_names = set(risk_module.__dict__)
    strategy_names = set(strategy_module.__dict__)
    assert "Session" not in risk_names and "Broker" not in risk_names and "GPTAnalysis" not in risk_names
    assert "Session" not in strategy_names and "Broker" not in strategy_names
