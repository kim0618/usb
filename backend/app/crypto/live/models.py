"""Normalised value types for everything read off the Binance account.

Two rules hold throughout, and both exist because a wrong number here is money rather than a
display bug:

* **Nothing is defaulted.** A figure the panel shows comes from `_required`, which raises when
  Binance did not send the field. A field Binance documents as conditional goes through
  `_optional` and arrives as `None`, which the UI prints as "-". There is no third case where a
  missing field becomes a zero.
* **Every amount is a `Decimal` parsed from Binance's own string.** Binance sends money as
  strings and this package never lets one become a float, which is the rule the paper engine
  follows for the same reason.

Field names were taken from the official documentation where the page could be read
(`userTrades`, `income`, `account` V3, `order`) and, where the docs site served a truncated
page, cross-checked against the request/response samples in the ccxt Binance implementation;
`docs/crypto/binance_live/CRYPTO_BINANCE_LIVE_MANUAL_V1_ARCHITECTURE.md` records which is which.
The endpoint *paths* were not taken from either: they were probed against the live API.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

LONG = "LONG"
SHORT = "SHORT"
#: Binance's One-way mode name for the single position of a symbol.
BOTH = "BOTH"


class LiveFieldMissing(RuntimeError):
    """Binance answered without a field this package needs. Named so the report says which
    endpoint changed shape rather than showing a plausible zero."""

    def __init__(self, endpoint: str, field: str) -> None:
        super().__init__(f"{endpoint} response carries no {field!r}")
        self.endpoint = endpoint
        self.field = field


def _decimal(value: Any, endpoint: str, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LiveFieldMissing(endpoint, f"{field} (not a number: {value!r})") from exc
    if not parsed.is_finite():
        raise LiveFieldMissing(endpoint, f"{field} (not finite: {value!r})")
    return parsed


def _required(payload: Mapping[str, Any], field: str, *, endpoint: str) -> Decimal:
    if field not in payload or payload[field] in (None, ""):
        raise LiveFieldMissing(endpoint, field)
    return _decimal(payload[field], endpoint, field)


def _optional(payload: Mapping[str, Any], field: str, *, endpoint: str) -> Decimal | None:
    if field not in payload or payload[field] in (None, ""):
        return None
    return _decimal(payload[field], endpoint, field)


def _required_str(payload: Mapping[str, Any], field: str, *, endpoint: str) -> str:
    value = payload.get(field)
    if value in (None, ""):
        raise LiveFieldMissing(endpoint, field)
    return str(value)


def _required_int(payload: Mapping[str, Any], field: str, *, endpoint: str) -> int:
    if field not in payload or payload[field] in (None, ""):
        raise LiveFieldMissing(endpoint, field)
    try:
        return int(payload[field])
    except (TypeError, ValueError) as exc:
        raise LiveFieldMissing(endpoint, f"{field} (not an integer)") from exc


@dataclass(frozen=True)
class AccountBalance:
    """The USDⓈ-M futures wallet, from `GET /fapi/v3/account`.

    Binance answers this question twice in one response, in two different units, and the
    difference is not rounding:

    * `assets[asset=USDT]` is the balance **in USDT**. It is what the Binance app shows and it
      does not move while the account is idle.
    * the top-level `totalWalletBalance` / `availableBalance` are the account **valued in USD**.
      Measured on a wallet holding only USDT, they read 0.99940 of the USDT row and drift with
      the peg between two reads seconds apart, with no trade, no fee and no funding in between.

    A screen that labels the top-level figure "USDT" therefore shows a number that disagrees
    with the exchange's own app and wobbles for no reason the operator can see. So the
    asset-denominated fields below come from the asset row, and the USD valuation is kept
    beside them under names that say what it is.

    `available_balance` is Binance's own figure either way; it is never recomputed here.
    """
    asset: str
    #: Denominated in `asset`, from the asset row.
    wallet_balance: Decimal
    available_balance: Decimal
    margin_balance: Decimal
    unrealized_pnl: Decimal
    max_withdraw: Decimal
    initial_margin: Decimal
    maint_margin: Decimal
    #: The whole account valued in USD, from the response's top level.
    account_wallet_usd: Decimal
    account_available_usd: Decimal
    account_margin_usd: Decimal
    account_unrealized_usd: Decimal
    update_time_ms: int | None

    ENDPOINT = "account"

    @property
    def usd_valuation_ratio(self) -> Decimal | None:
        """USD per unit of `asset`, as Binance valued it on this response. Shown so the two
        figures can be reconciled on screen instead of looking like a bug."""
        if self.wallet_balance == 0:
            return None
        return self.account_wallet_usd / self.wallet_balance

    @classmethod
    def from_account(cls, payload: Mapping[str, Any], *, asset: str = "USDT") -> "AccountBalance":
        endpoint = cls.ENDPOINT
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise LiveFieldMissing(endpoint, "assets")
        row = next((item for item in assets if isinstance(item, Mapping)
                    and item.get("asset") == asset), None)
        if row is None:
            # Not defaulted to the top-level figure: that would silently put a USD number back
            # under a USDT label, which is the bug this class exists to prevent.
            raise LiveFieldMissing(endpoint, f"assets[asset={asset}]")
        raw_update = row.get("updateTime")
        return cls(
            asset=asset,
            wallet_balance=_required(row, "walletBalance", endpoint=endpoint),
            available_balance=_required(row, "availableBalance", endpoint=endpoint),
            margin_balance=_required(row, "marginBalance", endpoint=endpoint),
            unrealized_pnl=_required(row, "unrealizedProfit", endpoint=endpoint),
            max_withdraw=_required(row, "maxWithdrawAmount", endpoint=endpoint),
            initial_margin=_required(row, "initialMargin", endpoint=endpoint),
            maint_margin=_required(row, "maintMargin", endpoint=endpoint),
            account_wallet_usd=_required(payload, "totalWalletBalance", endpoint=endpoint),
            account_available_usd=_required(payload, "availableBalance", endpoint=endpoint),
            account_margin_usd=_required(payload, "totalMarginBalance", endpoint=endpoint),
            account_unrealized_usd=_required(payload, "totalUnrealizedProfit", endpoint=endpoint),
            update_time_ms=int(raw_update) if raw_update not in (None, "") else None)

    def view(self) -> dict[str, Any]:
        return {"asset": self.asset, "wallet_balance": self.wallet_balance,
                "available_balance": self.available_balance, "margin_balance": self.margin_balance,
                "unrealized_pnl": self.unrealized_pnl, "max_withdraw": self.max_withdraw,
                "initial_margin": self.initial_margin, "maint_margin": self.maint_margin,
                "account_wallet_usd": self.account_wallet_usd,
                "account_available_usd": self.account_available_usd,
                "account_margin_usd": self.account_margin_usd,
                "account_unrealized_usd": self.account_unrealized_usd,
                "usd_valuation_ratio": self.usd_valuation_ratio,
                "update_time_ms": self.update_time_ms,
                "balance_source": "binance GET /fapi/v3/account assets[USDT] (asset-denominated); "
                                  "account_* fields are the top-level USD valuation"}


@dataclass(frozen=True)
class LivePosition:
    """One row of `GET /fapi/v3/positionRisk`, or the flat state when the row is absent.

    V3 does not carry `leverage` or `marginType`; those live on `SymbolConfig`. `liquidation_price`
    arrives as "0" when there is no position, and is normalised to `None` there rather than being
    shown as a price of zero.
    """
    symbol: str
    position_side: str
    signed_qty: Decimal
    entry_price: Decimal | None
    break_even_price: Decimal | None
    mark_price: Decimal | None
    unrealized_pnl: Decimal
    liquidation_price: Decimal | None
    isolated_margin: Decimal | None
    notional: Decimal | None
    initial_margin: Decimal | None
    maint_margin: Decimal | None
    adl: int | None
    update_time_ms: int | None

    ENDPOINT = "position_risk"

    @property
    def is_flat(self) -> bool:
        return self.signed_qty == 0

    @property
    def side(self) -> str | None:
        if self.signed_qty > 0:
            return LONG
        if self.signed_qty < 0:
            return SHORT
        return None

    @property
    def qty(self) -> Decimal:
        return abs(self.signed_qty)

    @classmethod
    def flat(cls, symbol: str) -> "LivePosition":
        return cls(symbol=symbol, position_side=BOTH, signed_qty=Decimal(0), entry_price=None,
                   break_even_price=None, mark_price=None, unrealized_pnl=Decimal(0),
                   liquidation_price=None, isolated_margin=None, notional=None,
                   initial_margin=None, maint_margin=None, adl=None, update_time_ms=None)

    @classmethod
    def from_rows(cls, rows: Any, symbol: str) -> "LivePosition":
        """The symbol's row, or flat when Binance sends none.

        An account that has never traded the symbol gets no row at all, and that is a flat
        position rather than an error. Hedge mode produces two rows for one symbol; this picks
        neither and the caller refuses to go LIVE (see `PositionMode`), so a hedged account is
        never rendered as if it were one-way.
        """
        if not isinstance(rows, list):
            raise LiveFieldMissing(cls.ENDPOINT, "response is not a list")
        matching = [row for row in rows if isinstance(row, Mapping) and row.get("symbol") == symbol]
        if not matching:
            return cls.flat(symbol)
        row = matching[0]
        endpoint = cls.ENDPOINT
        liquidation = _optional(row, "liquidationPrice", endpoint=endpoint)
        entry = _optional(row, "entryPrice", endpoint=endpoint)
        adl_raw = row.get("adl")
        return cls(
            symbol=symbol,
            position_side=str(row.get("positionSide") or BOTH),
            signed_qty=_required(row, "positionAmt", endpoint=endpoint),
            entry_price=entry if entry not in (None, Decimal(0)) else None,
            break_even_price=_optional(row, "breakEvenPrice", endpoint=endpoint),
            mark_price=_optional(row, "markPrice", endpoint=endpoint),
            unrealized_pnl=_required(row, "unRealizedProfit", endpoint=endpoint),
            liquidation_price=liquidation if liquidation not in (None, Decimal(0)) else None,
            isolated_margin=_optional(row, "isolatedMargin", endpoint=endpoint),
            notional=_optional(row, "notional", endpoint=endpoint),
            initial_margin=_optional(row, "initialMargin", endpoint=endpoint),
            maint_margin=_optional(row, "maintMargin", endpoint=endpoint),
            adl=int(adl_raw) if adl_raw not in (None, "") else None,
            update_time_ms=int(row["updateTime"]) if row.get("updateTime") not in (None, "") else None)

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "side": self.side, "position_side": self.position_side,
                "qty": self.qty, "signed_qty": self.signed_qty, "entry_price": self.entry_price,
                "break_even_price": self.break_even_price, "mark_price": self.mark_price,
                "unrealized_pnl": self.unrealized_pnl, "liquidation_price": self.liquidation_price,
                "isolated_margin": self.isolated_margin, "notional": self.notional,
                "initial_margin": self.initial_margin, "maint_margin": self.maint_margin,
                "adl": self.adl, "update_time_ms": self.update_time_ms, "is_flat": self.is_flat}


@dataclass(frozen=True)
class SymbolConfig:
    """`GET /fapi/v1/symbolConfig` - the account's leverage and margin mode for one symbol.

    V1 reads this and never writes it. `POST /fapi/v1/marginType` is on the endpoint deny list,
    so the margin mode shown here cannot be changed by this process even by mistake.
    """
    symbol: str
    margin_type: str
    leverage: Decimal
    max_notional: Decimal | None
    is_auto_add_margin: bool | None

    ENDPOINT = "symbol_config"

    @classmethod
    def from_rows(cls, rows: Any, symbol: str) -> "SymbolConfig":
        if not isinstance(rows, list):
            raise LiveFieldMissing(cls.ENDPOINT, "response is not a list")
        row = next((item for item in rows if isinstance(item, Mapping)
                    and item.get("symbol") == symbol), None)
        if row is None:
            raise LiveFieldMissing(cls.ENDPOINT, f"no row for {symbol}")
        auto_add = row.get("isAutoAddMargin")
        return cls(symbol=symbol,
                   margin_type=_required_str(row, "marginType", endpoint=cls.ENDPOINT),
                   leverage=_required(row, "leverage", endpoint=cls.ENDPOINT),
                   max_notional=_optional(row, "maxNotionalValue", endpoint=cls.ENDPOINT),
                   is_auto_add_margin=(None if auto_add is None
                                       else str(auto_add).lower() in {"true", "1"}))

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "margin_type": self.margin_type, "leverage": self.leverage,
                "max_notional": self.max_notional, "is_auto_add_margin": self.is_auto_add_margin}


@dataclass(frozen=True)
class PositionMode:
    """`GET /fapi/v1/positionSide/dual`. Hedge mode is refused rather than converted."""
    dual_side: bool

    ENDPOINT = "position_mode"

    @property
    def one_way(self) -> bool:
        return not self.dual_side

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PositionMode":
        if "dualSidePosition" not in payload:
            raise LiveFieldMissing(cls.ENDPOINT, "dualSidePosition")
        raw = payload["dualSidePosition"]
        return cls(dual_side=raw if isinstance(raw, bool) else str(raw).lower() in {"true", "1"})

    def view(self) -> dict[str, Any]:
        return {"dual_side": self.dual_side, "mode": "HEDGE" if self.dual_side else "ONE_WAY"}


@dataclass(frozen=True)
class CommissionRate:
    """`GET /fapi/v1/commissionRate`. The account's own rate, which is the whole point: the
    paper run's Bybit fee constants must not price a Binance order."""
    symbol: str
    maker: Decimal
    taker: Decimal

    ENDPOINT = "commission_rate"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CommissionRate":
        return cls(symbol=_required_str(payload, "symbol", endpoint=cls.ENDPOINT),
                   maker=_required(payload, "makerCommissionRate", endpoint=cls.ENDPOINT),
                   taker=_required(payload, "takerCommissionRate", endpoint=cls.ENDPOINT))

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "maker": self.maker, "taker": self.taker,
                "source": "binance GET /fapi/v1/commissionRate"}


@dataclass(frozen=True)
class MarkPrice:
    """`GET /fapi/v1/premiumIndex`. The mark is what liquidation and unrealised PnL use, so it
    is the price the LIVE panel leads with - never the last trade, and never Bybit's mark."""
    symbol: str
    mark_price: Decimal
    index_price: Decimal | None
    last_funding_rate: Decimal | None
    next_funding_time_ms: int | None
    time_ms: int | None

    ENDPOINT = "mark_price"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "MarkPrice":
        next_funding = payload.get("nextFundingTime")
        time_ms = payload.get("time")
        return cls(symbol=_required_str(payload, "symbol", endpoint=cls.ENDPOINT),
                   mark_price=_required(payload, "markPrice", endpoint=cls.ENDPOINT),
                   index_price=_optional(payload, "indexPrice", endpoint=cls.ENDPOINT),
                   last_funding_rate=_optional(payload, "lastFundingRate", endpoint=cls.ENDPOINT),
                   next_funding_time_ms=int(next_funding) if next_funding not in (None, "", 0) else None,
                   time_ms=int(time_ms) if time_ms not in (None, "") else None)

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "mark_price": self.mark_price,
                "index_price": self.index_price, "last_funding_rate": self.last_funding_rate,
                "next_funding_time_ms": self.next_funding_time_ms, "time_ms": self.time_ms}


@dataclass(frozen=True)
class BookTop:
    """`GET /fapi/v1/ticker/bookTicker`: best bid and ask with their sizes."""
    symbol: str
    best_bid: Decimal
    best_ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal
    time_ms: int | None

    ENDPOINT = "book_ticker"

    @property
    def spread(self) -> Decimal:
        return self.best_ask - self.best_bid

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "BookTop":
        time_ms = payload.get("time")
        return cls(symbol=_required_str(payload, "symbol", endpoint=cls.ENDPOINT),
                   best_bid=_required(payload, "bidPrice", endpoint=cls.ENDPOINT),
                   best_ask=_required(payload, "askPrice", endpoint=cls.ENDPOINT),
                   bid_qty=_required(payload, "bidQty", endpoint=cls.ENDPOINT),
                   ask_qty=_required(payload, "askQty", endpoint=cls.ENDPOINT),
                   time_ms=int(time_ms) if time_ms not in (None, "") else None)

    def reference(self, side: str) -> Decimal:
        """The price a market order of that side is measured against: an entry LONG lifts the
        ask, an entry SHORT hits the bid."""
        return self.best_ask if side == LONG else self.best_bid

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "best_bid": self.best_bid, "best_ask": self.best_ask,
                "bid_qty": self.bid_qty, "ask_qty": self.ask_qty, "spread": self.spread,
                "time_ms": self.time_ms}


@dataclass(frozen=True)
class UserTrade:
    """One fill from `GET /fapi/v1/userTrades`. `commission` and `realizedPnl` are Binance's
    figures for that fill and are the authority for cost and realised PnL - nothing here is
    recomputed from price and quantity."""
    id: int
    order_id: int
    symbol: str
    side: str
    position_side: str
    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    realized_pnl: Decimal
    commission: Decimal
    commission_asset: str
    maker: bool
    buyer: bool
    time_ms: int

    ENDPOINT = "user_trades"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "UserTrade":
        endpoint = cls.ENDPOINT
        return cls(id=_required_int(payload, "id", endpoint=endpoint),
                   order_id=_required_int(payload, "orderId", endpoint=endpoint),
                   symbol=_required_str(payload, "symbol", endpoint=endpoint),
                   side=_required_str(payload, "side", endpoint=endpoint),
                   position_side=str(payload.get("positionSide") or BOTH),
                   price=_required(payload, "price", endpoint=endpoint),
                   qty=_required(payload, "qty", endpoint=endpoint),
                   quote_qty=_required(payload, "quoteQty", endpoint=endpoint),
                   realized_pnl=_required(payload, "realizedPnl", endpoint=endpoint),
                   commission=_required(payload, "commission", endpoint=endpoint),
                   commission_asset=_required_str(payload, "commissionAsset", endpoint=endpoint),
                   maker=bool(payload.get("maker")), buyer=bool(payload.get("buyer")),
                   time_ms=_required_int(payload, "time", endpoint=endpoint))

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "order_id": self.order_id, "symbol": self.symbol, "side": self.side,
                "position_side": self.position_side, "price": self.price, "qty": self.qty,
                "quote_qty": self.quote_qty, "realized_pnl": self.realized_pnl,
                "commission": self.commission, "commission_asset": self.commission_asset,
                "maker": self.maker, "buyer": self.buyer, "time_ms": self.time_ms}


@dataclass(frozen=True)
class IncomeRow:
    """One row of `GET /fapi/v1/income`: funding, commission, realised PnL and transfers, as
    Binance booked them. LIVE funding is read from here and never simulated."""
    symbol: str
    income_type: str
    income: Decimal
    asset: str
    info: str
    time_ms: int
    tran_id: str

    ENDPOINT = "income"
    FUNDING = "FUNDING_FEE"
    COMMISSION = "COMMISSION"
    REALIZED_PNL = "REALIZED_PNL"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "IncomeRow":
        endpoint = cls.ENDPOINT
        return cls(symbol=str(payload.get("symbol") or ""),
                   income_type=_required_str(payload, "incomeType", endpoint=endpoint),
                   income=_required(payload, "income", endpoint=endpoint),
                   asset=_required_str(payload, "asset", endpoint=endpoint),
                   info=str(payload.get("info") or ""),
                   time_ms=_required_int(payload, "time", endpoint=endpoint),
                   tran_id=str(payload.get("tranId") or ""))

    def view(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "income_type": self.income_type, "income": self.income,
                "asset": self.asset, "info": self.info, "time_ms": self.time_ms,
                "tran_id": self.tran_id}


__all__ = ["AccountBalance", "BookTop", "CommissionRate", "IncomeRow", "LiveFieldMissing",
           "LivePosition", "MarkPrice", "PositionMode", "SymbolConfig", "UserTrade",
           "LONG", "SHORT", "BOTH"]
