"""Kiwoom payload to USB canonical market model mapping."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
import exchange_calendars as xcals
import pandas as pd
from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.timestamps import ET, minute_timestamp
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.reference import SymbolMetadata
from app.market.symbols import normalize_symbol

EXCHANGES = {"ND": "NASDAQ", "NY": "NYSE", "NA": "AMEX"}
EXCHANGE_CODES = {name: code for code, name in EXCHANGES.items()}
US_CALENDAR = xcals.get_calendar("XNYS")


def canonical_exchange(raw: object) -> str:
    return EXCHANGES.get(str(raw).strip().upper(), "UNKNOWN")


def exchange_code(raw: object) -> str:
    """Resolve a stored exchange authority to the Kiwoom code its lookups require.

    Accepts either the canonical market name a candidate snapshot carries (NASDAQ,
    NYSE, AMEX) or an already-resolved Kiwoom code, so the scanner and the entry
    runtime share one contract. Anything else fails closed: a silent NASDAQ default
    is what sent NYSE symbols to the wrong endpoint.
    """
    value = str(raw or "").strip().upper()
    if value in EXCHANGES:
        return value
    code = EXCHANGE_CODES.get(value)
    if code is None:
        raise MarketDataError(
            "UNSUPPORTED_EXCHANGE",
            f"Exchange {value or 'MISSING'} is not a supported US exchange",
        )
    return code


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
        # A completed bar is knowable once it closes. FUTURE_DATA above already proves
        # close <= received_at; tying availability to receipt instead would hide every
        # bar from a live consumer whose as_of was captured just before the fetch.
        available_at=market_close,
    )


def map_minute_bar(symbol: str, row: dict[str, Any], received_at: datetime) -> MinuteBar:
    timestamp = minute_timestamp(row)
    observed_at = timestamp + timedelta(minutes=1)
    if observed_at > received_at.astimezone(ET):
        raise MarketDataError("FUTURE_DATA", "Incomplete or future minute bar was excluded")
    label = pd.Timestamp(timestamp.date())
    local_time = timestamp.timetz().replace(tzinfo=None)
    if not US_CALENDAR.is_session(label) or local_time < time(4) or local_time >= time(20):
        raise MarketDataError("OUTSIDE_SESSION", "Minute bar is outside supported US sessions")
    market_open = US_CALENDAR.session_open(label).to_pydatetime().astimezone(ET)
    market_close = US_CALENDAR.session_close(label).to_pydatetime().astimezone(ET)
    session = (MarketSession.PREMARKET if timestamp < market_open
               else MarketSession.REGULAR if timestamp < market_close
               else MarketSession.POSTMARKET)
    return MinuteBar(
        symbol=normalize_symbol(symbol), timestamp=timestamp, session=session,
        open=float(number(row["open_pric"], "open_pric")),
        high=float(number(row["high_pric"], "high_pric")),
        low=float(number(row["low_pric"], "low_pric")),
        close=float(number(row["cur_prc"], "cur_prc")),
        volume=int(number(row["trde_qty"], "trde_qty")),
        observed_at=observed_at,
        available_at=observed_at,  # same completed-bar contract as daily bars
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
