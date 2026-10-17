"""Leverage brackets and the leverage an order is margined at.

Binance limits leverage by position notional: a symbol's `GET /fapi/v1/leverageBracket` lists
notional bands, each with the most leverage allowed inside it. The account's configured
leverage (`symbolConfig.leverage`) is a setting, and it can be above what the symbol's bands
allow at all - SIRENUSDT read 20x configured against a 10x first band on the real account.

Binance does not lower the setting to fit. An order is margined at the configured leverage, and
the position it leaves may not exceed the largest band cap whose leverage reaches that setting
(`max_notional_at`). Above every band - 20x on a 10x symbol - that is zero, so every opening order
is refused (-2027 "Exceeded the maximum allowable position at current leverage"; on 2026-10-17 it
refused 23,994 SIREN, about 735 USDT, at 20x, then filled 36,014 at 10x). The first version of
this module priced margin at min(configured, band) and offered sizes Binance then refused.

So every margin figure in this package is priced at the configured leverage, and a size is
refused here, before anything is sent, when

* the configured leverage is above the symbol's maximum (`LEVERAGE_INVALID_FOR_SYMBOL`): the
  operator has to choose a leverage again; nothing here changes it, or
* the whole position the order would leave on that side (what is held plus the order, valued at
  the higher of the fill and the mark price) is past the band cap that leverage allows
  (`LEVERAGE_TIER_EXCEEDED`).

Feasibility stays monotone in quantity, so the Safe MAX search over quantities converges.

Unknown is refused. No table, a malformed table, or a notional past the last band all return a
refusal with a reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

LEVERAGE_BRACKET_UNKNOWN = "LEVERAGE_BRACKET_UNKNOWN"
NOTIONAL_ABOVE_MAXIMUM = "NOTIONAL_ABOVE_MAXIMUM"
LEVERAGE_INVALID_FOR_SYMBOL = "LEVERAGE_INVALID_FOR_SYMBOL"
LEVERAGE_TIER_EXCEEDED = "LEVERAGE_TIER_EXCEEDED"


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

    def max_notional_at(self, leverage: Decimal) -> Decimal:
        """The largest position Binance accepts at `leverage`: the cap of the last band whose
        leverage reaches it. Zero when no band does."""
        allowed = [band.cap for band in self.brackets if band.max_leverage >= leverage]
        return max(allowed) if allowed else Decimal(0)

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
    """The leverage an order is margined at - always the configured one - and the band its
    resulting position lands in. `capped` is kept for the response shape and is always false:
    Binance never lowers the setting to fit a band."""
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


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def leverage_invalid_message(symbol: str, maximum: Decimal) -> str:
    return f"현재 심볼({symbol})은 최대 {_fmt(maximum)}x입니다. 레버리지를 다시 선택하세요."


def effective_leverage(configured: Decimal, brackets: LeverageBrackets | None,
                       bracket_notional: Decimal) -> EffectiveLeverage | LeverageRefused:
    if brackets is None:
        return LeverageRefused(LEVERAGE_BRACKET_UNKNOWN,
                               "레버리지 구간(leverageBracket)을 읽지 못해 필요 증거금을 계산하지 않습니다.")
    if configured > brackets.max_leverage:
        return LeverageRefused(LEVERAGE_INVALID_FOR_SYMBOL,
                               leverage_invalid_message(brackets.symbol, brackets.max_leverage))
    band = brackets.band(bracket_notional)
    if band is None:
        return LeverageRefused(NOTIONAL_ABOVE_MAXIMUM,
                               f"포지션 명목 {bracket_notional}이 {brackets.symbol}의 최대 구간 "
                               f"{brackets.max_notional}을 넘습니다.")
    if configured > band.max_leverage:
        return LeverageRefused(LEVERAGE_TIER_EXCEEDED,
                               f"{_fmt(configured)}x에서는 포지션 명목 "
                               f"{_fmt(brackets.max_notional_at(configured))} USDT까지 열 수 있습니다. "
                               f"이 주문 후 포지션은 {bracket_notional.quantize(Decimal('0.01'))} USDT입니다.")
    return EffectiveLeverage(configured=configured, allowed=band.max_leverage,
                             effective=configured, bracket=band.bracket,
                             bracket_notional=bracket_notional)


__all__ = ["Bracket", "BracketsMalformed", "EffectiveLeverage", "LEVERAGE_BRACKET_UNKNOWN",
           "LEVERAGE_INVALID_FOR_SYMBOL", "LEVERAGE_TIER_EXCEEDED", "LeverageBrackets",
           "LeverageRefused", "NOTIONAL_ABOVE_MAXIMUM", "effective_leverage",
           "leverage_invalid_message"]
