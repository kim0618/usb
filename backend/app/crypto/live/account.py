"""Reads the Binance account and decides whether LIVE may be shown at all.

Every snapshot is built from REST responses taken in one pass. Nothing is restored from disk,
which is the whole of the restart contract: after a process restart the panel shows what Binance
says, and a local file can neither contradict it nor stand in for it. The mirror ledger
(`mirror.py`) records what was seen; it is never read back into a snapshot.

The gate is separate from the snapshot on purpose. `SnapshotGate` answers one question - may the
operator be shown a LIVE account and, later, be allowed to trade it - and it answers with a list
of blockers rather than a bare false, because "hedge mode" and "no API key" need different
actions from the operator.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .credentials import LiveConfig
from .filters import SymbolFilters
from .models import (AccountBalance, BookTop, CommissionRate, IncomeRow, LiveFieldMissing,
                     LivePosition, MarkPrice, PositionMode, SymbolConfig, UserTrade)
from .rest import BinanceError, BinanceFuturesClient

#: Blocker codes. Every one of these keeps the LIVE panel from showing account figures, and all
#: of them also keep an order from being built even when the trading flag is on.
CREDENTIALS_MISSING = "CREDENTIALS_MISSING"
HEDGE_MODE_UNSUPPORTED = "HEDGE_MODE_UNSUPPORTED"
SYMBOL_NOT_TRADING = "SYMBOL_NOT_TRADING"
BINANCE_UNREACHABLE = "BINANCE_UNREACHABLE"
BINANCE_AUTH_FAILED = "BINANCE_AUTH_FAILED"
CLOCK_SKEW = "CLOCK_SKEW"
RESPONSE_SHAPE_CHANGED = "RESPONSE_SHAPE_CHANGED"

#: A snapshot older than this is shown as stale rather than as the current account.
SNAPSHOT_STALE_MS = 15_000


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str

    def view(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class LiveSnapshot:
    """One consistent read of the account. `fetched_at_ms` is this server's clock, not
    Binance's, because staleness is judged the same way the paper terminal judges its feed."""
    symbol: str
    fetched_at_ms: int
    balance: AccountBalance | None = None
    position: LivePosition | None = None
    symbol_config: SymbolConfig | None = None
    position_mode: PositionMode | None = None
    commission: CommissionRate | None = None
    mark: MarkPrice | None = None
    book: BookTop | None = None
    filters: SymbolFilters | None = None
    blockers: list[Blocker] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """True when the account may be displayed. Displaying and trading share this gate; the
        trading flag is an additional condition on top of it, never a substitute."""
        return not self.blockers

    def age_ms(self, now_ms: int | None = None) -> int:
        return (int(time.time() * 1000) if now_ms is None else now_ms) - self.fetched_at_ms

    def view(self, now_ms: int | None = None) -> dict[str, Any]:
        age = self.age_ms(now_ms)
        return {
            "symbol": self.symbol,
            "ready": self.ready,
            "blockers": [item.view() for item in self.blockers],
            "fetched_at_ms": self.fetched_at_ms,
            "age_ms": age,
            "stale": age > SNAPSHOT_STALE_MS,
            "balance": self.balance.view() if self.balance else None,
            "position": self.position.view() if self.position else None,
            "symbol_config": self.symbol_config.view() if self.symbol_config else None,
            "position_mode": self.position_mode.view() if self.position_mode else None,
            "commission": self.commission.view() if self.commission else None,
            "mark": self.mark.view() if self.mark else None,
            "book": self.book.view() if self.book else None,
            "filters": self.filters.view() if self.filters else None,
            "source": "BINANCE_LIVE",
        }


class AccountReader:
    """Read-only access to one Binance futures account.

    Read-only is structural, not a promise: this class never resolves a TRADE endpoint. Orders
    live in `orders.py`, behind their own two gates.
    """

    def __init__(self, client: BinanceFuturesClient, config: LiveConfig) -> None:
        self.client = client
        self.config = config
        self.symbol = config.symbol
        self._filters: SymbolFilters | None = None
        self._filters_fetched_ms: int | None = None
        #: Filters change rarely; refetching them every second would spend weight for nothing.
        self.filters_ttl_ms = 3_600_000

    # ------------------------------------------------------------------ pieces

    def filters(self, *, force: bool = False) -> SymbolFilters:
        now = int(time.time() * 1000)
        if (force or self._filters is None or self._filters_fetched_ms is None
                or now - self._filters_fetched_ms > self.filters_ttl_ms):
            payload = self.client.call("exchange_info", {"symbol": self.symbol})
            self._filters = SymbolFilters.from_exchange_info(payload, self.symbol, fetched_at_ms=now)
            self._filters_fetched_ms = now
        return self._filters

    def position(self) -> LivePosition:
        rows = self.client.call("position_risk", {"symbol": self.symbol})
        return LivePosition.from_rows(rows, self.symbol)

    def recent_fills(self, limit: int = 50) -> list[UserTrade]:
        rows = self.client.call("user_trades", {"symbol": self.symbol, "limit": min(limit, 1000)})
        return [UserTrade.from_payload(row) for row in rows]

    def trade_history(self, *, from_id: int, limit: int = 1000) -> list[UserTrade]:
        """Chronological trade page used by the durable Manual LIVE performance cursor."""
        rows = self.client.call("user_trades", {
            "symbol": self.symbol, "fromId": from_id, "limit": min(limit, 1000)})
        return [UserTrade.from_payload(row) for row in rows]

    def order(self, order_id: int) -> dict[str, Any]:
        return self.client.call("query_order", {"symbol": self.symbol, "orderId": order_id})

    def income_window(self, *, start_ms: int, end_ms: int,
                      income_type: str = IncomeRow.FUNDING,
                      limit: int = 1000) -> list[IncomeRow]:
        rows = self.client.call("income", {"symbol": self.symbol, "incomeType": income_type,
                                           "startTime": start_ms, "endTime": end_ms,
                                           "limit": min(limit, 1000)})
        return [IncomeRow.from_payload(row) for row in rows]

    def income(self, limit: int = 100, income_type: str | None = None) -> list[IncomeRow]:
        params: dict[str, Any] = {"symbol": self.symbol, "limit": min(limit, 1000)}
        if income_type is not None:
            params["incomeType"] = income_type
        rows = self.client.call("income", params)
        return [IncomeRow.from_payload(row) for row in rows]

    def funding(self, limit: int = 100) -> list[IncomeRow]:
        return self.income(limit=limit, income_type=IncomeRow.FUNDING)

    # ------------------------------------------------------------------ the whole account

    def snapshot(self) -> LiveSnapshot:
        """One pass over the account. A failure is captured as a blocker rather than raised:
        the panel has to be able to say *why* LIVE is unavailable, and an exception escaping
        here would leave it saying nothing.
        """
        now = int(time.time() * 1000)
        snapshot = LiveSnapshot(symbol=self.symbol, fetched_at_ms=now)
        if not self.config.credentials_present:
            snapshot.blockers.append(Blocker(
                CREDENTIALS_MISSING,
                "BINANCE_API_KEY / BINANCE_API_SECRET가 설정되지 않았습니다."))
            return snapshot
        try:
            snapshot.filters = self.filters()
            if not snapshot.filters.tradable:
                snapshot.blockers.append(Blocker(
                    SYMBOL_NOT_TRADING,
                    f"{self.symbol} 상태가 {snapshot.filters.status}이라 거래할 수 없습니다."))
            snapshot.mark = MarkPrice.from_payload(self.client.call("mark_price", {"symbol": self.symbol}))
            snapshot.book = BookTop.from_payload(self.client.call("book_ticker", {"symbol": self.symbol}))
            snapshot.position_mode = PositionMode.from_payload(self.client.call("position_mode"))
            if snapshot.position_mode.dual_side:
                # Refused, not converted: `POST /fapi/v1/positionSide/dual` is on the deny list
                # and the operator changes the account setting themselves if they want LIVE.
                snapshot.blockers.append(Blocker(
                    HEDGE_MODE_UNSUPPORTED,
                    "계정이 Hedge Mode입니다. V1은 One-way Mode만 지원하며 모드를 자동으로 바꾸지 않습니다."))
            snapshot.balance = AccountBalance.from_account(self.client.call("account"))
            snapshot.position = LivePosition.from_rows(
                self.client.call("position_risk", {"symbol": self.symbol}), self.symbol)
            snapshot.symbol_config = SymbolConfig.from_rows(
                self.client.call("symbol_config", {"symbol": self.symbol}), self.symbol)
            snapshot.commission = CommissionRate.from_payload(
                self.client.call("commission_rate", {"symbol": self.symbol}))
        except BinanceError as exc:
            snapshot.blockers.append(Blocker(*_binance_blocker(exc)))
        except LiveFieldMissing as exc:
            snapshot.blockers.append(Blocker(
                RESPONSE_SHAPE_CHANGED,
                f"Binance 응답에 필요한 값이 없습니다: {exc.endpoint}.{exc.field}"))
        except ValueError as exc:
            snapshot.blockers.append(Blocker(RESPONSE_SHAPE_CHANGED, str(exc)))
        return snapshot


def _binance_blocker(exc: BinanceError) -> tuple[str, str]:
    if exc.is_clock_skew:
        return (CLOCK_SKEW, "서버 시각이 Binance recvWindow를 벗어났습니다. 시계를 동기화한 뒤 다시 시도하세요.")
    if exc.is_auth:
        return (BINANCE_AUTH_FAILED,
                f"Binance 인증 거부 ({exc.code}): 키 권한과 IP 화이트리스트를 확인하세요.")
    if exc.status == 0:
        return (BINANCE_UNREACHABLE, f"Binance에 연결하지 못했습니다: {exc.message}")
    return (BINANCE_UNREACHABLE, f"Binance 오류 {exc.code} (HTTP {exc.status}): {exc.message}")


def usdt_to_krw(amount: Decimal | None, rate: Decimal) -> Decimal | None:
    """Display only. USDT is the accounting authority for a Binance account; won is shown
    beside it with the same fixed rate the paper run uses, so the two screens read alike."""
    return None if amount is None else amount * rate


__all__ = ["AccountReader", "Blocker", "LiveSnapshot", "SNAPSHOT_STALE_MS", "usdt_to_krw",
           "CREDENTIALS_MISSING", "HEDGE_MODE_UNSUPPORTED", "SYMBOL_NOT_TRADING",
           "BINANCE_UNREACHABLE", "BINANCE_AUTH_FAILED", "CLOCK_SKEW", "RESPONSE_SHAPE_CHANGED"]
