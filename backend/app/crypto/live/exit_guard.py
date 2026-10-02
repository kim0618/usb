"""Durable backend net-PnL exit guard for one Binance LIVE position."""
from __future__ import annotations
import json
import os
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from .adapter import BinanceLiveAdapter

OFF, ARMED, TRIGGERING, CLOSING, COMPLETE, ERROR = (
    "OFF", "ARMED", "TRIGGERING", "CLOSING", "COMPLETE", "ERROR")


class ExitGuard:
    def __init__(self, *, adapter: BinanceLiveAdapter, path: Path,
                 krw_rate: Callable[[], Decimal | None], interval_s: float = 1.0) -> None:
        self.adapter, self.path, self.krw_rate = adapter, path, krw_rate
        self.interval_s = interval_s
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._thread: threading.Thread | None = None
        self.data = self._blank()
        self._recover()

    @staticmethod
    def _blank() -> dict[str, Any]:
        return {"state": OFF, "enabled": False, "symbol": None, "side": None,
                "position_qty": None, "configured_qty": None, "opened_at_ms": None,
                "take_profit_krw": None,
                "stop_loss_krw": None, "current_net_usdt": None,
                "current_net_krw": None, "created_at_ms": None, "updated_at_ms": None,
                "last_error": None}

    @staticmethod
    def _now() -> int:
        return int(time.time() * 1000)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, sort_keys=True))
        os.replace(temporary, self.path)

    def _off(self, reason: str | None = None) -> None:
        self.data.update(state=OFF, enabled=False, updated_at_ms=self._now(), last_error=reason)
        self._save()

    def _recover(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict) or not raw.get("enabled"):
                return
            card = self.adapter.get_position_card()
            # Restart recovery stays strict about the size, and deliberately so. A live
            # scale-in is handled while the process is up - `_matches` keys on the side and the
            # opening fill, which an add-on does not change, and `tick` carries the new size
            # through - but a size that moved while this process was *not* watching was not
            # observed by anything, so the guard comes back OFF with `RECOVERY_DISABLED` on
            # screen rather than resuming onto a position it never saw change. Off and visible
            # beats armed on an assumption.
            matches = (card.get("open") and card.get("side") == raw.get("side")
                       and str(card.get("qty")) == str(raw.get("position_qty"))
                       and card.get("opened_at_ms") == raw.get("opened_at_ms"))
            required = ("take_profit_krw", "stop_loss_krw", "opened_at_ms")
            if not matches or any(raw.get(key) in (None, "") for key in required):
                raise ValueError("stored guard does not match the current open cycle")
            self.data = {**self._blank(), **raw, "state": ARMED, "enabled": True,
                         "configured_qty": (raw.get("configured_qty")
                                            or raw.get("position_qty")),
                         "updated_at_ms": self._now(), "last_error": None}
            self._save()
        except Exception as exc:
            self.data = {**self._blank(),
                         "last_error": f"RECOVERY_DISABLED: {type(exc).__name__}"}
            self._save()

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="binance-exit-guard")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_s * 2))
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.tick()
            except Exception as exc:
                with self._lock:
                    if self.data["state"] not in (OFF, COMPLETE):
                        self.data.update(state=ERROR, enabled=False,
                                         last_error=f"MONITOR: {type(exc).__name__}",
                                         updated_at_ms=self._now())
                        self._save()

    def view(self) -> dict[str, Any]:
        """The guard as stored, plus whether the position has changed size since it was set.

        A same-side scale-in does not invalidate the guard - the thresholds are amounts of money
        and the CLOSE always re-reads the real position and sends it `reduceOnly` - but it does
        change what those amounts mean in price terms, and the operator who typed them against a
        smaller position is the one who needs to know.
        """
        with self._lock:
            data = dict(self.data)
        configured, current = data.get("configured_qty"), data.get("position_qty")
        data["scaled_in"] = bool(data.get("enabled") and configured not in (None, "")
                                 and current not in (None, "")
                                 and Decimal(str(current)) > Decimal(str(configured)))
        return data

    def configure(self, *, take_profit_krw: Decimal,
                  stop_loss_krw: Decimal) -> dict[str, Any]:
        if take_profit_krw <= 0 or stop_loss_krw <= 0:
            raise ValueError("익절/손절 금액은 0보다 커야 합니다.")
        if self.krw_rate() is None:
            raise ValueError("KRW 환산 기준을 사용할 수 없습니다.")
        card = self.adapter.get_position_card()
        if not card.get("open") or not card.get("net_complete") or card.get("opened_at_ms") is None:
            raise ValueError("현재 포지션의 순손익 또는 open cycle을 확정할 수 없습니다.")
        now = self._now()
        with self._lock:
            self.data = {**self._blank(), "state": ARMED, "enabled": True,
                         "symbol": self.adapter.config.symbol, "side": card["side"],
                         "position_qty": str(card["qty"]),
                         #: The size the thresholds were chosen against, frozen here. The live
                         #: size below keeps moving with a scale-in; this one does not, so the
                         #: screen can say the two have diverged.
                         "configured_qty": str(card["qty"]),
                         "opened_at_ms": card["opened_at_ms"],
                         "take_profit_krw": str(take_profit_krw),
                         "stop_loss_krw": str(stop_loss_krw),
                         "created_at_ms": now, "updated_at_ms": now}
            self._save()
            return dict(self.data)

    def disable(self) -> dict[str, Any]:
        with self._lock:
            self._off()
            return dict(self.data)

    def _matches(self, card: dict[str, Any]) -> bool:
        return bool(card.get("open") and card.get("side") == self.data["side"]
                    and card.get("opened_at_ms") == self.data["opened_at_ms"])

    def _hit(self, net: Decimal) -> bool:
        return (net >= Decimal(self.data["take_profit_krw"])
                or net <= -abs(Decimal(self.data["stop_loss_krw"])))

    def tick(self) -> None:
        with self._lock:
            if not self.data["enabled"] or self.data["state"] != ARMED:
                return
            card = self.adapter.get_position_card()
            if not self._matches(card):
                self._off("POSITION_DISAPPEARED_OR_CHANGED")
                return
            if not card.get("net_complete") or card.get("net_if_closed") is None:
                return
            rate = self.krw_rate()
            if rate is None:
                self._off("KRW_RATE_UNAVAILABLE")
                return
            net_usdt = Decimal(str(card["net_if_closed"]))
            net_krw = net_usdt * rate
            self.data.update(current_net_usdt=str(net_usdt), current_net_krw=str(net_krw),
                             position_qty=str(card["qty"]), updated_at_ms=self._now())
            self._save()
            if not self._hit(net_krw):
                return
            self.data["state"] = TRIGGERING
            self._save()
            self.adapter.resync()
            fresh = self.adapter.get_position_card()
            if not self._matches(fresh) or not fresh.get("net_complete"):
                self._off("POSITION_CHANGED_DURING_REVALIDATION")
                return
            fresh_net = Decimal(str(fresh["net_if_closed"])) * rate
            if not self._hit(fresh_net):
                self.data.update(state=ARMED, current_net_krw=str(fresh_net),
                                 updated_at_ms=self._now())
                self._save()
                return
            self.data["state"] = CLOSING
            self._save()
            try:
                plan = self.adapter.plan_order("", "CLOSE")
                self.adapter.router.submit_close_only(plan)
            except Exception as exc:
                after = self.adapter.resync().position
                flat = after is not None and after.is_flat
                self.data.update(state=COMPLETE if flat else ERROR, enabled=False,
                                 last_error=None if flat else
                                 f"CLOSE_RECONCILE_REQUIRED: {type(exc).__name__}",
                                 updated_at_ms=self._now())
                self._save()
                return
            after = self.adapter.resync().position
            flat = after is not None and after.is_flat
            self.data.update(state=COMPLETE if flat else ERROR, enabled=False,
                             last_error=None if flat else "CLOSE_ACK_BUT_POSITION_OPEN",
                             updated_at_ms=self._now())
            self._save()


__all__ = ["ExitGuard", "OFF", "ARMED", "TRIGGERING", "CLOSING", "COMPLETE", "ERROR"]
