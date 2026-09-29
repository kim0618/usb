"""Shared paper-engine fixtures. The fee and FX figures here are test constants, deliberately
not the production ones, so a test can never silently become the source of a real rate."""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

import pytest

from app.crypto.paper.book import BookSide, Quote
from app.crypto.paper.config import build_config
from app.crypto.paper.engine import PaperEngine
from app.crypto.paper.instrument import RiskTierTable

RISK_LIMIT_PAYLOAD = {
    "retCode": 0,
    "result": {"list": [
        {"id": 1, "riskLimitValue": "300000", "initialMargin": "0.0066",
         "maintenanceMargin": "0.0033", "maxLeverage": "150.00", "mmDeduction": ""},
        {"id": 2, "riskLimitValue": "2000000", "initialMargin": "0.01",
         "maintenanceMargin": "0.005", "maxLeverage": "100.00", "mmDeduction": "510"},
        {"id": 3, "riskLimitValue": "2600000", "initialMargin": "0.0111",
         "maintenanceMargin": "0.0056", "maxLeverage": "90.00", "mmDeduction": "1710"},
    ]},
}


@pytest.fixture
def tiers() -> RiskTierTable:
    return RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="test", source_sha256="deadbeef")


def make_config(*, leverage: str = "10", slippage_model: str = "NONE", slippage_bps: str = "0",
                taker: str = "0.0006", maker: str = "0.0002", capital_krw: str = "1000000",
                fx: str = "1000") -> object:
    return build_config(
        run_id="test-run", starting_capital_krw=capital_krw, fx_krw_per_usdt=fx,
        fx_source="test fixture", fx_asof_utc="2026-09-23T00:00:00Z",
        fee_version="test-fees-v1", fee_taker_rate=taker, fee_maker_rate=maker,
        fee_source="test fixture", fee_effective_date="2026-01-01",
        slippage_model=slippage_model, slippage_bps=slippage_bps, leverage=leverage,
        risk_limit_source="test", risk_limit_sha256="deadbeef")


@pytest.fixture
def config() -> object:
    return make_config()


def quote(ts_ms: int, *, bid: str = "100000.0", ask: str = "100000.1", mark: str | None = None,
          bid_qty: str = "10", ask_qty: str = "10", last: str | None = None,
          funding_rate: str | None = None, next_funding_time_ms: int | None = None,
          bids: Sequence[Sequence[str]] | None = None,
          asks: Sequence[Sequence[str]] | None = None) -> Quote:
    bid_rows = list(bids) if bids is not None else [[bid, bid_qty]]
    ask_rows = list(asks) if asks is not None else [[ask, ask_qty]]
    reference = mark if mark is not None else bid
    return Quote(
        ts_ms=ts_ms,
        bids=BookSide.from_rows(bid_rows, descending=True),
        asks=BookSide.from_rows(ask_rows, descending=False),
        mark_price=Decimal(reference),
        last_price=Decimal(last) if last is not None else Decimal(reference),
        funding_rate=Decimal(funding_rate) if funding_rate is not None else None,
        next_funding_time_ms=next_funding_time_ms)


def started_engine(config: object, tiers: RiskTierTable, *, ts_ms: int = 1_000,
                   first_quote: Quote | None = None) -> PaperEngine:
    engine = PaperEngine(config, tiers)  # type: ignore[arg-type]
    engine.start(ts_ms)
    engine.apply_market(first_quote if first_quote is not None else quote(ts_ms))
    return engine


@pytest.fixture
def engine(config: object, tiers: RiskTierTable) -> PaperEngine:
    return started_engine(config, tiers)


@pytest.fixture(autouse=True)
def _no_live_trade_socket(monkeypatch):
    """The 15 s trade feed opens its own socket on app startup; tests never touch the network."""
    from app.crypto.terminal import trade_candles
    monkeypatch.setattr(trade_candles.TradeCandleFeed, "start", lambda self: None)
