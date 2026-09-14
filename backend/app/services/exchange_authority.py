"""The one durable answer to which venue a symbol's market data is read from.

A listing venue is not a property of a ticker. It is what the scanner stored on the
candidate a trade came from, and every runtime that reads market data binds it
before the lookup:

- an approved entry candidate carries its scanner row directly;
- a held position reaches that same row through the single open strategy state the
  position belongs to, which is exactly what a restart finds in the database.

Nothing here keeps a process-local answer and nothing guesses a venue. A broken link
is a typed ``EXCHANGE_AUTHORITY_MISSING`` failure, and an unsupported stored value is
refused by the provider's own binding as ``UNSUPPORTED_EXCHANGE``. A provider that
does not route lookups by venue needs neither, so it is left alone.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import MarketDataError
from app.market.provider import ExchangeAwareProvider, MarketDataProvider
from app.market.symbols import normalize_symbol
from app.models.scanner import ScannerCandidate
from app.repositories.strategy import StrategyStateRepository

EXCHANGE_AUTHORITY_MISSING = "EXCHANGE_AUTHORITY_MISSING"
UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"
AUTHORITY_FAILURE_CODES = frozenset({EXCHANGE_AUTHORITY_MISSING, UNSUPPORTED_EXCHANGE})


def stored_exchange(candidate: ScannerCandidate) -> str:
    """The exchange a scanner snapshot stored, carried as stored.

    An empty or unknown value is passed on unchanged so the provider binding fails
    closed on it rather than this function choosing a venue.
    """
    parts = candidate.score_components_json
    raw = parts.get("exchange") if isinstance(parts, dict) else None
    return str(raw or "").strip().upper()


def bind_exchange(provider: MarketDataProvider, symbol: str, exchange: str) -> None:
    """Hand one symbol's exchange to a provider that routes lookups by venue."""
    if isinstance(provider, ExchangeAwareProvider):
        provider.bind_exchange(symbol, exchange)


def open_position_exchange(session: Session, symbol: str) -> str:
    """The stored exchange of the scanner candidate that opened this held position.

    Resolved through the position's own open strategy state, never by looking the
    ticker up across runs: the same symbol can appear in many scanner runs, and only
    the one that produced this trade is its authority.
    """
    symbol = normalize_symbol(symbol)
    states = StrategyStateRepository(session).list_open(symbol)
    if len(states) != 1:
        raise MarketDataError(EXCHANGE_AUTHORITY_MISSING,
                              f"{symbol} has {len(states)} open strategy states")
    state = states[0]
    if state.scanner_candidate_id is None:
        raise MarketDataError(EXCHANGE_AUTHORITY_MISSING,
                              f"{symbol} open strategy state records no scanner candidate")
    candidate = session.get(ScannerCandidate, state.scanner_candidate_id)
    if candidate is None or normalize_symbol(candidate.symbol) != symbol:
        raise MarketDataError(
            EXCHANGE_AUTHORITY_MISSING,
            f"{symbol} scanner candidate {state.scanner_candidate_id} is not this symbol's")
    return stored_exchange(candidate)


def bind_open_position(provider: MarketDataProvider, runtime: Any, symbol: str) -> None:
    """Bind a held symbol to its trade's exchange before the provider is queried.

    ``runtime`` is only read when the provider routes by venue, so a provider that
    does not is never made to touch the database. Resolution is a database read and
    binding is an in-memory assignment: neither issues a market-data request.
    """
    if not isinstance(provider, ExchangeAwareProvider):
        return
    with runtime.session_factory() as session:
        exchange = open_position_exchange(session, symbol)
    provider.bind_exchange(symbol, exchange)
