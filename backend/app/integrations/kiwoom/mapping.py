"""Kiwoom payload to USB canonical market model mapping."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.timestamps import ET, minute_timestamp
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.reference import SymbolMetadata
from app.market.symbols import normalize_symbol

EXCHANGES = {"ND": "NASDAQ", "NY": "NYSE", "NA": "AMEX"}


def canonical_exchange(raw: object) -> str:
    return EXCHANGES.get(str(raw).strip().upper(), "UNKNOWN")


def number(raw: object, field: str) -> Decimal:
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise MarketDataError("MARKET_DATA_UNAVAILABLE", f"Kiwoom field {field} is invalid") from exc
    return abs(value)


def map_daily_bar(symbol: str, row: dict[str, Any], received_at: datetime) -> DailyBar:
    trading_date = datetime.strptime(str(row["dt"]), "%Y%m%d").date()
    market_close = datetime.combine(trading_date, time(16, 0), ET)
    if market_close > received_at.astimezone(ET):
        raise MarketDataError("FUTURE_DATA", "Incomplete or future daily bar was excluded")
    return DailyBar(
        symbol=normalize_symbol(symbol),
        trading_date=trading_date,
        open=float(number(row["open_pric"], "open_pric")),
        high=float(number(row["high_pric"], "high_pric")),
        low=float(number(row["low_pric"], "low_pric")),
        close=float(number(row["cur_prc"], "cur_prc")),
        volume=int(number(row["acc_trde_qty"], "acc_trde_qty")),
        observed_at=market_close,
        available_at=max(market_close, received_at.astimezone(ET)),
    )


def map_minute_bar(symbol: str, row: dict[str, Any], received_at: datetime) -> MinuteBar:
    timestamp = minute_timestamp(row)
    observed_at = timestamp + timedelta(minutes=1)
    if observed_at > received_at.astimezone(ET):
        raise MarketDataError("FUTURE_DATA", "Incomplete or future minute bar was excluded")
    local_time = timestamp.timetz().replace(tzinfo=None)
    session = (
        MarketSession.PREMARKET if local_time < time(9, 30)
        else MarketSession.REGULAR if local_time < time(16, 0)
        else MarketSession.POSTMARKET
    )
    return MinuteBar(
        symbol=normalize_symbol(symbol), timestamp=timestamp, session=session,
        open=float(number(row["open_pric"], "open_pric")),
        high=float(number(row["high_pric"], "high_pric")),
        low=float(number(row["low_pric"], "low_pric")),
        close=float(number(row["cur_prc"], "cur_prc")),
        volume=int(number(row["trde_qty"], "trde_qty")),
        observed_at=observed_at,
        available_at=max(observed_at, received_at.astimezone(ET)),
    )


def map_metadata(symbol: str, quote: dict[str, Any], received_at: datetime) -> SymbolMetadata:
    exchange = canonical_exchange(quote.get("stex_tp"))
    raw_market_cap = quote.get("mac")
    return SymbolMetadata(
        symbol=normalize_symbol(symbol),
        company_name=quote.get("stk_enm") or quote.get("stk_nm"),
        market_cap=None if raw_market_cap in (None, "") else float(number(raw_market_cap, "mac")),
        exchange=exchange,
        active=str(quote.get("trd_susp_tp", "N")).strip().upper() not in {"Y", "1"},
        observed_at=received_at,
        available_at=received_at,
    )
