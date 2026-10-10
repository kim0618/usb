"""Leverage brackets and the effective leverage an order is priced at.

Binance limits leverage by position notional: a symbol's `GET /fapi/v1/leverageBracket` lists
notional bands, each with the most leverage allowed inside it. The account's configured
leverage (`symbolConfig.leverage`) is a setting, and it can be above what the band an order
lands in allows - GTCUSDT reads 20x configured against a 10x first band on the real account.
Pricing margin at the configured figure there halves the required margin and roughly doubles
Safe MAX, so every margin figure in this package is priced at

    effective = min(configured, allowed(bracket_notional))

where `bracket_notional` is the whole position the order would leave on that side (what is
already held plus the order) valued at the higher of the fill and the mark price, so a band
boundary is judged conservatively.

No iteration is needed. Safe MAX searches over quantities and prices each candidate on its own:
the candidate's notional fixes its band, the band fixes the leverage, the leverage fixes the
margin. Bands are validated to allow non-increasing leverage as notional grows, so required
margin never decreases as quantity grows, feasibility is monotone and the search converges
without oscillating at a boundary.

Unknown is refused. No table, a malformed table, or a notional past the last band all return a
refusal with a reason; nothing here falls back to the configured leverage on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

LEVERAGE_BRACKET_UNKNOWN = "LEVERAGE_BRACKET_UNKNOWN"
NOTIONAL_ABOVE_MAXIMUM = "NOTIONAL_ABOVE_MAXIMUM"


class BracketsMalformed(ValueError):
    """The bracket response cannot be trusted to price margin."""


@dataclass(frozen=True)
class Bracket:
    bracket: int
    floor: Decimal
    cap: Decimal
    max_leverage: Decimal
    maint_margin_ratio: Decimal

    def view(self) -> dict[str, Any]:
        return {"bracket": self.bracket, "notional_floor": self.floor, "notional_cap": self.cap,
                "max_leverage": self.max_leverage, "maint_margin_ratio": self.maint_margin_ratio}


def _dec(row: Mapping[str, Any], key: str) -> Decimal:
    value = row.get(key)
    if value in (None, ""):
        raise BracketsMalformed(f"leverageBracket row is missing {key}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BracketsMalformed(f"leverageBracket {key} is not a number: {value!r}") from exc
    if not parsed.is_finite():
        raise BracketsMalformed(f"leverageBracket {key} is not finite")
    return parsed


@dataclass(frozen=True)
class LeverageBrackets:
    symbol: str
    brackets: tuple[Bracket, ...]

    @classmethod
    def from_payload(cls, payload: Any, symbol: str) -> "LeverageBrackets":
        rows = payload if isinstance(payload, list) else [payload]
        row = next((item for item in rows if isinstance(item, Mapping) and item.get("symbol") == symbol), None)
        if row is None:
            raise BracketsMalformed(f"leverageBracket carries no {symbol} row")
        raw = row.get("brackets")
        if not isinstance(raw, list) or not raw:
            raise BracketsMalformed(f"{symbol} leverageBracket has no brackets")
        parsed = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise BracketsMalformed(f"{symbol} leverageBracket entry is not an object")
            parsed.append(Bracket(bracket=int(_dec(item, "bracket")), floor=_dec(item, "notionalFloor"),
                                  cap=_dec(item, "notionalCap"), max_leverage=_dec(item, "initialLeverage"),
                                  maint_margin_ratio=_dec(item, "maintMarginRatio")))
        parsed.sort(key=lambda b: b.floor)
        if parsed[0].floor != 0:
            raise BracketsMalformed(f"{symbol} first bracket does not start at notional 0")
        for band in parsed:
            if band.max_leverage <= 0 or band.cap <= band.floor:
                raise BracketsMalformed(f"{symbol} bracket {band.bracket} is not a valid band")
        for lower, upper in zip(parsed, parsed[1:]):
            if upper.floor != lower.cap:
                raise BracketsMalformed(f"{symbol} brackets {lower.bracket}/{upper.bracket} are not contiguous")
            if upper.max_leverage > lower.max_leverage:
                # Monotone leverage is what makes the Safe MAX search converge; a table that
                # breaks it is refused rather than searched.
                raise BracketsMalformed(f"{symbol} bracket {upper.bracket} allows more leverage than a lower band")
        return cls(symbol=symbol, brackets=tuple(parsed))

    @property
    def max_leverage(self) -> Decimal:
        return self.brackets[0].max_leverage

    @property
    def max_notional(self) -> Decimal:
        return self.brackets[-1].cap

    def band(self, notional: Decimal) -> Bracket | None:
        """The band a position of `notional` sits in; a boundary belongs to the lower band."""
        if notional < 0:
            return None
        for band in self.brackets:
            if notional <= band.cap:
                return band
        return None

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "max_leverage": self.max_leverage,
                "max_notional": self.max_notional, "count": len(self.brackets),
                "brackets": [band.view() for band in self.brackets],
                "source": "binance GET /fapi/v1/leverageBracket"}


@dataclass(frozen=True)
class EffectiveLeverage:
    configured: Decimal
    allowed: Decimal
    effective: Decimal
    bracket: int
    bracket_notional: Decimal

    @property
    def capped(self) -> bool:
        return self.effective < self.configured

    def view(self) -> dict[str, Any]:
        return {"configured_leverage": self.configured, "bracket_max_leverage": self.allowed,
                "leverage": self.effective, "leverage_capped_by_bracket": self.capped,
                "bracket": self.bracket, "bracket_notional": self.bracket_notional}


@dataclass(frozen=True)
class LeverageRefused:
    code: str
    message: str


def effective_leverage(configured: Decimal, brackets: LeverageBrackets | None,
                       bracket_notional: Decimal) -> EffectiveLeverage | LeverageRefused:
    if brackets is None:
        return LeverageRefused(LEVERAGE_BRACKET_UNKNOWN,
                               "레버리지 구간(leverageBracket)을 읽지 못해 필요 증거금을 계산하지 않습니다.")
    band = brackets.band(bracket_notional)
    if band is None:
        return LeverageRefused(NOTIONAL_ABOVE_MAXIMUM,
                               f"포지션 명목 {bracket_notional}이 {brackets.symbol}의 최대 구간 "
                               f"{brackets.max_notional}을 넘습니다.")
    return EffectiveLeverage(configured=configured, allowed=band.max_leverage,
                             effective=min(configured, band.max_leverage), bracket=band.bracket,
                             bracket_notional=bracket_notional)


__all__ = ["Bracket", "BracketsMalformed", "EffectiveLeverage", "LEVERAGE_BRACKET_UNKNOWN",
           "LeverageBrackets", "LeverageRefused", "NOTIONAL_ABOVE_MAXIMUM", "effective_leverage"]
