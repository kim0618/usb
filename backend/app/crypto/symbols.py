"""The instruments Manual LIVE may trade, and the one rule that decides membership.

This module is the whitelist and nothing else. It holds no account state, makes no request and
imports nothing at all, so every layer that has to judge a symbol - the LIVE config, the
adapter registry, the routes, the order router, the PAPER runtime, the chart history - can ask
the same question of the same answer rather than each keeping its own tuple.

It sits at the crypto package root rather than inside `live/` on purpose. `live/` is the only
package allowed to touch a real Binance account, and a structural test asserts that nothing
outside it imports from it - the PAPER terminal must not be able to reach the account code even
transitively. The whitelist is needed on both sides of that line, so it belongs on neither: it
is a fact about which instruments this build is about, not about any account.

Why a whitelist rather than "whatever Binance lists": every symbol added here costs real reads
(filters, commission, bracket table, book) and a real screen that has to be correct about
quantity precision and minimum notional. Binance lists hundreds of perpetuals. Three were asked
for, three are supported, and a fourth is a decision somebody makes on purpose by editing this
tuple - not something a query parameter can do.

`DEFAULT_SYMBOL` is first in the tuple on purpose: it is what every route answers when the
caller names nothing, and it is BTCUSDT because that is the instrument the existing audit
mirror, performance cursor and paper ledger are about. A deployment that defaulted elsewhere
would silently re-point those files.
"""
from __future__ import annotations

#: Ordered. The screen's tabs read left to right in this order, and `DEFAULT_SYMBOL` is the
#: first entry rather than a second constant that could drift from it.
SUPPORTED_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

DEFAULT_SYMBOL = SUPPORTED_SYMBOLS[0]

#: Reject code, carried to the API so a refusal reads the same wherever it was raised.
SYMBOL_NOT_SUPPORTED = "SYMBOL_NOT_SUPPORTED"


class SymbolNotSupported(ValueError):
    """A symbol outside the whitelist. Raised before anything is read or sent."""

    code = SYMBOL_NOT_SUPPORTED

    def __init__(self, symbol: object) -> None:
        self.symbol = symbol
        super().__init__(
            f"{symbol!r}는 지원 심볼이 아닙니다. "
            f"지원: {', '.join(SUPPORTED_SYMBOLS)}")
        self.message = str(self)


def is_supported(symbol: object) -> bool:
    return isinstance(symbol, str) and symbol.strip().upper() in SUPPORTED_SYMBOLS


def resolve(symbol: object | None) -> str:
    """The canonical symbol for a request, or a refusal.

    `None` and the empty string mean "the caller did not choose", which is the default symbol -
    that is what keeps every existing single-symbol caller working unchanged. Anything else is
    normalised (trimmed, upper-cased) and then has to be in the whitelist; a near miss like
    "btcusd" or "BTCUSDT " is accepted or refused on its normalised form, never half-matched.
    """
    if symbol is None:
        return DEFAULT_SYMBOL
    if not isinstance(symbol, str):
        raise SymbolNotSupported(symbol)
    candidate = symbol.strip().upper()
    if not candidate:
        return DEFAULT_SYMBOL
    if candidate not in SUPPORTED_SYMBOLS:
        raise SymbolNotSupported(symbol)
    return candidate


def base_asset(symbol: str) -> str:
    """The coin a quantity is denominated in, for a label only.

    Derived by stripping the USDT quote because these three are all USDT-quoted perpetuals, and
    it exists for the one case where a screen needs a unit *before* `exchangeInfo` has been
    read. Once a snapshot exists, `SymbolFilters.base_asset` is Binance's own answer and is the
    one the panel shows; this is the fallback, not the authority.
    """
    candidate = (symbol or "").strip().upper()
    return candidate[:-4] if candidate.endswith("USDT") else candidate


__all__ = ["SUPPORTED_SYMBOLS", "DEFAULT_SYMBOL", "SYMBOL_NOT_SUPPORTED", "SymbolNotSupported",
           "base_asset", "is_supported", "resolve"]
