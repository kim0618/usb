"""Durable Manual Binance LIVE realised-performance aggregation."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .account import AccountReader
from .models import IncomeRow, UserTrade

KST = ZoneInfo("Asia/Seoul")
MANUAL_PREFIX = "usbm-"
PAGE_LIMIT = 1000
WINDOW_MS = 7 * 24 * 60 * 60 * 1000 - 1
REFRESH_TTL_MS = 30_000


class ManualLivePerformance:
    """Binance-authoritative fills with a local cache/cursor, never a wallet delta."""

    def __init__(self, *, reader: AccountReader, path: Path,
                 krw_rate: Callable[[], Decimal | None]) -> None:
        self.reader, self.path, self.krw_rate = reader, path, krw_rate
        self._lock = threading.RLock()
        self.data = self._load()

    @staticmethod
    def _blank() -> dict[str, Any]:
        return {"version": 1, "trades": [], "funding": [], "last_trade_id": None,
                "last_funding_time_ms": None, "last_calculated_ms": None,
                "classification_complete": True, "last_error": None}

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._blank()
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("unsupported performance state")
            return {**self._blank(), **raw}
        except Exception as exc:
            return {**self._blank(), "classification_complete": False,
                    "last_error": f"STATE_IGNORED: {type(exc).__name__}"}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(
            self.data, ensure_ascii=False, sort_keys=True, default=str))
        os.replace(temporary, self.path)

    @staticmethod
    def _trade_row(trade: UserTrade, manual: bool | None,
                   client_order_id: str | None) -> dict[str, Any]:
        return {**trade.view(), "manual": manual, "client_order_id": client_order_id}

    def _sync_trades(self) -> None:
        # A transient queryOrder failure must not turn into a permanent "external" verdict.
        # Keep the fill, retry its metadata on every reconcile, and expose completeness.
        for row in self.data["trades"]:
            if row.get("manual") is not None:
                continue
            try:
                payload = self.reader.order(int(row["order_id"]))
                client_id = str(payload.get("clientOrderId") or "")
                row.update(client_order_id=client_id,
                           manual=client_id.startswith(MANUAL_PREFIX))
            except Exception:
                pass

        seen = {int(row["id"]) for row in self.data["trades"]}
        last_id = self.data.get("last_trade_id")
        next_id = (int(last_id) if last_id is not None else -1) + 1
        while True:
            page = self.reader.trade_history(from_id=max(0, next_id), limit=PAGE_LIMIT)
            if not page:
                break
            orders: dict[int, str | None] = {}
            for trade in page:
                if trade.id in seen:
                    continue
                if trade.order_id not in orders:
                    try:
                        payload = self.reader.order(trade.order_id)
                        orders[trade.order_id] = str(payload.get("clientOrderId") or "")
                    except Exception:
                        orders[trade.order_id] = None
                        self.data["classification_complete"] = False
                client_id = orders[trade.order_id]
                manual = (client_id.startswith(MANUAL_PREFIX)
                          if client_id is not None else None)
                self.data["trades"].append(self._trade_row(trade, manual, client_id))
                seen.add(trade.id)
            highest = max(trade.id for trade in page)
            current = self.data.get("last_trade_id")
            self.data["last_trade_id"] = max(
                int(current) if current is not None else -1, highest)
            if len(page) < PAGE_LIMIT:
                break
            next_id = highest + 1
        self.data["classification_complete"] = all(
            row.get("manual") is not None for row in self.data["trades"])

    def _sync_funding(self, now_ms: int) -> None:
        manual_times = [int(row["time_ms"]) for row in self.data["trades"] if row["manual"]]
        if not manual_times:
            return
        start = int(self.data["last_funding_time_ms"] or min(manual_times))
        seen = {(str(row["tran_id"]), int(row["time_ms"])) for row in self.data["funding"]}
        cursor = start
        while cursor <= now_ms:
            end = min(now_ms, cursor + WINDOW_MS)
            for income in self.reader.income_window(start_ms=cursor, end_ms=end,
                                                    income_type=IncomeRow.FUNDING):
                key = (income.tran_id, income.time_ms)
                if key not in seen:
                    self.data["funding"].append(income.view())
                    seen.add(key)
            cursor = end + 1
        self.data["last_funding_time_ms"] = now_ms

    @staticmethod
    def _signed(row: dict[str, Any]) -> Decimal:
        qty = Decimal(str(row["qty"]))
        return qty if row["buyer"] else -qty

    def _attributed_funding(self) -> list[dict[str, Any]]:
        """Only funding where the entire reconstructed position is Manual LIVE."""
        trades = sorted(self.data["trades"], key=lambda row: (row["time_ms"], row["id"]))
        result: list[dict[str, Any]] = []
        index, total_qty, manual_qty = 0, Decimal(0), Decimal(0)
        for funding in sorted(self.data["funding"], key=lambda row: row["time_ms"]):
            while index < len(trades) and trades[index]["time_ms"] <= funding["time_ms"]:
                delta = self._signed(trades[index])
                total_qty += delta
                if trades[index]["manual"]:
                    manual_qty += delta
                index += 1
            if manual_qty != 0 and total_qty == manual_qty:
                result.append(funding)
        return result

    def refresh(self, *, now_ms: int | None = None, force: bool = False) -> dict[str, Any]:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            last = self.data.get("last_calculated_ms")
            if not force and last is not None and now - int(last) < REFRESH_TTL_MS:
                return self.summary(now_ms=now)
            try:
                self._sync_trades()
                self._sync_funding(now)
                self.data.update(last_calculated_ms=now, last_error=None)
                self._save()
            except Exception as exc:
                self.data.update(last_calculated_ms=now,
                                 last_error=f"RECONCILE: {type(exc).__name__}")
                self._save()
            return self.summary(now_ms=now)

    def summary(self, *, now_ms: int | None = None) -> dict[str, Any]:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        manual = [row for row in self.data["trades"] if row["manual"]]
        if not manual:
            return {"available": True, "has_trades": False,
                    "classification_complete": self.data["classification_complete"],
                    "last_error": self.data["last_error"],
                    "last_calculated_ms": self.data["last_calculated_ms"]}
        first_ms = min(int(row["time_ms"]) for row in manual)
        first_date = datetime.fromtimestamp(first_ms / 1000, KST).date()
        today = datetime.fromtimestamp(now / 1000, KST).date()
        running_day = (today - first_date).days + 1
        funding = self._attributed_funding()
        realised = sum((Decimal(str(row["realized_pnl"])) for row in manual), Decimal(0))
        commissions = sum((Decimal(str(row["commission"])) for row in manual), Decimal(0))
        funding_total = sum((Decimal(str(row["income"])) for row in funding), Decimal(0))
        today_trades = [row for row in manual
                        if datetime.fromtimestamp(row["time_ms"] / 1000, KST).date() == today]
        today_funding = [row for row in funding
                         if datetime.fromtimestamp(row["time_ms"] / 1000, KST).date() == today]
        today_net = (sum((Decimal(str(row["realized_pnl"])) for row in today_trades), Decimal(0))
                     - sum((Decimal(str(row["commission"])) for row in today_trades), Decimal(0))
                     + sum((Decimal(str(row["income"])) for row in today_funding), Decimal(0)))
        net = realised - commissions + funding_total
        rate = self.krw_rate()
        return {"available": True, "has_trades": True, "first_trade_at_ms": first_ms,
                "first_trade_kst_date": first_date.isoformat(), "running_day": running_day,
                "realized_pnl": realised, "commissions": commissions,
                "attributed_funding": funding_total, "cumulative_net_usdt": net,
                "today_net_usdt": today_net,
                "cumulative_net_krw": net * rate if rate is not None else None,
                "today_net_krw": today_net * rate if rate is not None else None,
                "krw_per_usdt": rate, "manual_trade_count": len(manual),
                "classification_complete": self.data["classification_complete"],
                "last_error": self.data["last_error"],
                "last_trade_id": self.data["last_trade_id"],
                "last_calculated_ms": self.data["last_calculated_ms"]}


__all__ = ["ManualLivePerformance", "MANUAL_PREFIX", "KST"]
