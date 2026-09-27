"""ACCOUNTING_V1, Strategy E's frozen cost contract, and the official / legacy split.

The invariants pinned here:

* a V1 closed trade's net PnL is exactly the account's cash change (no cost charged twice);
* A keeps ``execution_v0`` unchanged; E runs its own frozen COST_10BP round trip, read from the
  frozen files, and the 25 bp-per-leg config is nowhere on E's official path;
* an E V1 session reproduces the contract formula ``exposure x (gross - cost)`` to the cent;
* V0 rows stay as recorded, are LEGACY, and never enter official metrics, the gate or the portfolio.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app.broker.accounting import reconciles
from app.broker.sim import SimBroker
from app.execution.config import ExecutionConfig, RoundTripCostConfig
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.domain import MarketSession, MinuteBar
from app.strategies import ledger as L
from app.strategies import official as OFF
from app.strategies import paper_gate as GATE
from app.strategy_e_max_rt import cost as COST
from app.strategy_e_max_rt import engine as ENG
from app.strategy_e_max_rt import paper as PAPER
from app.dev import run_e_rt2_dryrun as RUNNER

from tests.strategy_e_max.test_e_paper_provisional import D, run_stage

UTC = timezone.utc


def _bar(symbol: str, minute: int, price: str) -> MinuteBar:
    ts = datetime(2026, 9, 28, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
    p = float(price)
    return MinuteBar(symbol=symbol, timestamp=ts, open=p, high=p, low=p, close=p, volume=10_000,
                     session=MarketSession.REGULAR, observed_at=ts + timedelta(minutes=1),
                     available_at=ts + timedelta(minutes=1))


def _intent(side: OrderSide, qty: str, ref: str, minute: int) -> OrderIntent:
    ts = datetime(2026, 9, 28, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
    q, r = Decimal(qty), Decimal(ref)
    return OrderIntent(symbol="NVDA", side=side,
                       intent_type=IntentType.BASE_ENTRY if side is OrderSide.BUY else IntentType.EXIT,
                       quantity=q, reference_price=r, notional=q * r, account_notional=Decimal("100000"),
                       account_currency="USD", instrument_currency="USD", strategy_version="TEST",
                       risk_amount=q * r, initial_stop=Decimal("0"), market_as_of=ts - timedelta(minutes=1),
                       created_at=ts, reason="TEST")


def round_trip(config: ExecutionConfig, entry: str = "100", exit_: str = "103", qty: str = "50"):
    broker = SimBroker(Decimal("100000"), config=config)
    broker.submit_order(_intent(OrderSide.BUY, qty, entry, 0), [_bar("NVDA", 0, entry)])
    broker.submit_order(_intent(OrderSide.SELL, qty, exit_, 5), [_bar("NVDA", 5, exit_)])
    return broker, broker.get_trade("NVDA")


# -- ACCOUNTING_V1 -----------------------------------------------------------------------------

def test_v1_net_is_the_cash_change_under_a_execution_v0() -> None:
    broker, trade = round_trip(ExecutionConfig())
    assert reconciles(broker.cash - Decimal("100000"), trade.net_pnl)
    charges = sum((f.commission + f.fx_cost for f in broker._fills), Decimal(0))
    embedded = sum((f.spread_cost + f.slippage_cost for f in broker._fills), Decimal(0))
    assert reconciles(trade.net_pnl, trade.gross_pnl - charges)
    # The V0 formula would have charged the price-embedded part a second time:
    assert reconciles((trade.gross_pnl - trade.total_cost) - trade.net_pnl, -embedded)
    assert L.detect_accounting(trade.gross_pnl, trade.net_pnl, trade.total_cost) == L.ACCOUNTING_V1


def test_a_cost_contract_is_unchanged() -> None:
    config = ExecutionConfig()
    assert (config.version, config.default_spread_bps, config.default_slippage_bps, config.commission_bps,
            config.fx_cost_bps) == ("execution_v0", 10, 5, 10, 0)
    assert type(config) is ExecutionConfig and not hasattr(config, "round_trip_cost_bps")


def test_the_a_v0_gap_is_exactly_the_price_embedded_share() -> None:
    """Server A rows: ledger 72.3555 vs equity +80.4785. The gap is 15/25 of total_cost."""
    total_cost = Decimal("13.53843641759622432247311215")
    assert round(total_cost * Decimal("15") / Decimal("25"), 4) == Decimal("8.1231")
    assert L.A_CASH_SHARE == Decimal("0.4")


# -- E cost contract ---------------------------------------------------------------------------

def test_e_official_cost_is_the_frozen_primary_round_trip() -> None:
    body = COST.contract()
    assert body["official_scenario"] == "COST_10BP" and body["official_round_trip_bp"] == 10
    assert body["round_trip"] is True and body["leg_allocation"].startswith("NONE")
    assert body["stress_scenarios_bp"] == {"COST_05BP": 5, "COST_15BP": 15, "COST_20BP": 20}
    assert body["version"] == "STRATEGY_E_COST_V1:COST_10BP"


def test_e_cost_is_read_from_checksummed_files(monkeypatch, tmp_path) -> None:
    data = json.loads(COST.COST_RULES.read_text(encoding="utf-8"))
    data["cost_model"]["stress_scenarios_bp"]["COST_10BP"] = 3
    tampered = tmp_path / "cost.json"
    tampered.write_text(json.dumps(data), encoding="utf-8")
    COST.contract.cache_clear()
    monkeypatch.setattr(COST, "COST_RULES", tampered)
    with pytest.raises(COST.CostContractError):
        COST.contract()
    COST.contract.cache_clear()


def test_round_trip_config_charges_the_total_once_at_raw_prices() -> None:
    broker, trade = round_trip(COST.official_execution(), entry="100", exit_="103", qty="50")
    fills = list(broker._fills)
    assert [f.fill_price for f in fills] == [f.raw_market_price for f in fills]      # no price adjustment
    assert [f.commission for f in fills] == [Decimal("5.0"), Decimal("0")]          # 10 bp of 5,000, once
    assert trade.net_pnl == Decimal("150") - Decimal("5")                             # 50 x 3 - 0.001 x 5000
    assert reconciles(broker.cash - Decimal("100000"), trade.net_pnl)


def test_round_trip_config_refuses_any_second_cost() -> None:
    with pytest.raises(ValueError):
        RoundTripCostConfig(round_trip_cost_bps=Decimal("10"), cost_contract="X")    # spread 10 by default
    with pytest.raises(ValueError):
        RoundTripCostConfig(default_spread_bps=Decimal(0), default_slippage_bps=Decimal(0),
                            commission_bps=Decimal(0), round_trip_cost_bps=Decimal("10"))   # unnamed contract


def test_the_25bp_per_leg_config_is_not_on_es_official_path() -> None:
    execution = COST.official_execution()
    assert (execution.default_spread_bps, execution.default_slippage_bps, execution.commission_bps) == (0, 0, 0)
    assert ENG.Engine.__dataclass_fields__["execution"].default_factory is COST.official_execution
    source = inspect.getsource(RUNNER._paper_stage)
    assert "COST.official_execution()" in source and "execution=execution" in source
    assert "ExecutionConfig(" not in source


# -- E V1 end to end ----------------------------------------------------------------------------

def test_e_v1_session_matches_the_contract_formula_and_reconciles(tmp_path) -> None:
    run_stage(tmp_path, ["AAA", "BBB"], status=PAPER.OFFICIAL)      # 2.0x, 100 -> 101 on both names
    root = tmp_path / RUNNER.PAPER_STATE_V1 / PAPER.OFFICIAL / "STRATEGY_E_MAX_V1"
    book = json.loads((root / "book.json").read_text())
    state = json.loads((root / "sessions" / f"{D.isoformat()}.json").read_text())
    assert book["accounting_version"] == "V1" and book["cost_contract_version"] == "STRATEGY_E_COST_V1:COST_10BP"
    assert book["execution_version"] == COST.EXECUTION_VERSION
    # exposure x (gross - cost) x equity = 2 x (0.01 - 0.001) x 10,000 = 180, to the cent
    assert Decimal(state["summary"]["sim_realized_pnl"]) == Decimal("180")
    assert Decimal(state["summary"]["sim_cost"]) == Decimal("20")
    assert Decimal(book["equity"]) - Decimal(book["initial_equity"]) == Decimal("180")
    out = L.e_trade_records(state, strategy_id="STRATEGY_E_MAX_V1", strategy_version="E-MAX V1",
                            book=PAPER.OFFICIAL, book_session=book["sessions"][D.isoformat()])
    assert out["reconciliation"]["status"] == "RECONCILED" and out["reconciliation"]["accounting"] == L.ACCOUNTING_V1
    assert sum(Decimal(t["net_pnl"]) for t in out["trades"]) == Decimal("180")
    assert {t["accounting_version"] for t in out["trades"]} == {"V1"}
    # the recorded 10 bp view and the paper figure agree, so E's gate divergence is 0 here
    assert Decimal(state["summary"]["fixed_bp_session_return_views"]["net_10bp"]) == Decimal("0.018")


def test_the_engine_never_continues_a_legacy_book(tmp_path) -> None:
    from app.strategy_e_max_rt import config as CFG
    store = ENG.Store(tmp_path, CFG.STRATEGY_ID)
    legacy = {"strategy_id": CFG.STRATEGY_ID, "initial_equity": "10000", "equity": "9876.84581720",
              "realized_pnl": "-123.15418280", "sessions": {"2026-09-25": {"sim_realized_pnl": "-123.15418280"}}}
    store.save_book(legacy)
    before = (store.dir / "book.json").read_bytes()
    from app.market.calendar import MarketCalendar
    engine = ENG.Engine(CFG.RuntimeConfig(enabled=True, state_dir=tmp_path, initial_equity=Decimal("10000")), lambda: None, None,
                        MarketCalendar("America/New_York"), store)
    state = engine.tick(datetime(2026, 9, 28, 13, 20, tzinfo=UTC))
    assert state["phase"] == ENG.Phase.ERROR and state["error"] == "BOOK_EXECUTION_VERSION_MISMATCH"
    assert (store.dir / "book.json").read_bytes() == before                     # the V0 book is untouched


# -- the official clock ------------------------------------------------------------------------

def test_classification(monkeypatch) -> None:
    monkeypatch.delenv(OFF.START_ENV, raising=False)
    assert OFF.classify("2026-10-01", "V1") == OFF.PRE_OFFICIAL and OFF.state()["status"] == "NOT_STARTED"
    monkeypatch.setenv(OFF.START_ENV, "2026-09-29")
    assert OFF.classify("2026-09-28", "V1") == OFF.PRE_OFFICIAL
    assert OFF.classify("2026-09-29", "V1") == OFF.OFFICIAL
    assert OFF.classify("2026-10-05", "V0") == OFF.LEGACY                       # V0 is never official
    monkeypatch.setenv(OFF.START_ENV, "29/09/2026")
    with pytest.raises(OFF.OfficialContractError):
        OFF.start()


def test_contracts_are_frozen() -> None:
    OFF.contract.cache_clear()
    GATE.contract.cache_clear()
    body = OFF.contract()
    assert body["declaration"]["paper_evaluation_version"] == "AE_PAPER_V1"
    assert body["gate"]["canonical_sha256"] == Path(GATE.CHECKSUM).read_text().strip() == (
        "7a139d6b389cd547cd4384801697680279949f81d96c5765919059560eddd2dd")
    gate = GATE.contract()
    assert gate["strategies"]["STRATEGY_A"]["min_operating_sessions"] == 60
    assert gate["strategies"]["STRATEGY_A"]["min_closed_trades"] == 30
    assert gate["strategies"]["STRATEGY_E_MAX_V1"]["min_closed_trades"] == 60
    assert body["cost_contracts"]["STRATEGY_A"]["execution_version"] == ExecutionConfig().version
    assert body["cost_contracts"]["STRATEGY_E_MAX_V1"]["cost_contract_version"] == COST.contract()["version"]
