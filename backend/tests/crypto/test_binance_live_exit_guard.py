from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.crypto.live.exit_guard import ARMED, COMPLETE, ERROR, OFF, ExitGuard
from app.crypto.live.filters import SymbolFilters
from app.crypto.live.models import CommissionRate, MarkPrice
from app.crypto.live.preview import round_trip
from tests.crypto.binance_fixtures import EXCHANGE_INFO


def filters() -> SymbolFilters:
    return SymbolFilters.from_exchange_info(EXCHANGE_INFO)


def test_entry_cost_uses_best_quote_to_vwap_market_impact() -> None:
    result = round_trip(
        side="LONG", qty=Decimal("2"),
        depth={"asks": [["100", "1"], ["102", "1"]], "bids": [["99", "2"]]},
        mark=MarkPrice.from_payload({"symbol": "BTCUSDT", "markPrice": "99",
                                    "time": 1}),
        commission=CommissionRate.from_payload({"symbol": "BTCUSDT",
                                                "makerCommissionRate": "0.0002",
                                                "takerCommissionRate": "0.0005"}),
        filters=filters(), leverage=Decimal("10"))
    assert result["expected_entry_vwap"] == Decimal("101")
    assert result["expected_entry_notional"] == Decimal("202")
    assert result["expected_entry_fee"] == Decimal("0.1010")
    assert result["expected_entry_slippage_cost"] == Decimal("2")
    assert result["expected_entry_total_cost"] == Decimal("2.1010")


def card(net: str = "100", *, side: str = "LONG", qty: str = "1",
         opened: int = 10) -> dict:
    return {"open": True, "net_complete": True, "net_if_closed": Decimal(net),
            "side": side, "qty": Decimal(qty), "opened_at_ms": opened}


class Router:
    def __init__(self, adapter: "Adapter", fail: bool = False) -> None:
        self.adapter, self.fail, self.calls = adapter, fail, 0

    def submit_close_only(self, _plan: object) -> dict:
        self.calls += 1
        if self.fail:
            raise TimeoutError("ambiguous")
        self.adapter.flat = True
        return {"status": "FILLED"}


class Adapter:
    def __init__(self, cards: list[dict], *, fail: bool = False) -> None:
        self.cards = cards
        self.index = 0
        self.flat = False
        self.config = SimpleNamespace(symbol="BTCUSDT")
        self.router = Router(self, fail)

    def get_position_card(self) -> dict:
        row = self.cards[min(self.index, len(self.cards) - 1)]
        self.index += 1
        return row

    def resync(self) -> SimpleNamespace:
        return SimpleNamespace(position=SimpleNamespace(is_flat=self.flat))

    def plan_order(self, side: str, intent: str) -> object:
        assert side == "" and intent == "CLOSE"
        return object()


def guard(tmp_path: Path, adapter: Adapter) -> ExitGuard:
    return ExitGuard(adapter=adapter, path=tmp_path / "guard.json",
                     krw_rate=lambda: Decimal("1000"), interval_s=100)


def test_take_profit_revalidates_and_closes_only_once(tmp_path: Path) -> None:
    adapter = Adapter([card("0"), card("0.2"), card("0.2")])
    subject = guard(tmp_path, adapter)
    subject.configure(take_profit_krw=Decimal("100"), stop_loss_krw=Decimal("50"))
    subject.tick()
    subject.tick()
    assert subject.view()["state"] == COMPLETE
    assert adapter.router.calls == 1


def test_stop_loss_trigger_and_ambiguous_close_does_not_retry(tmp_path: Path) -> None:
    adapter = Adapter([card("0"), card("-0.1"), card("-0.1")], fail=True)
    subject = guard(tmp_path, adapter)
    subject.configure(take_profit_krw=Decimal("100"), stop_loss_krw=Decimal("50"))
    subject.tick()
    subject.tick()
    assert subject.view()["state"] == ERROR
    assert "RECONCILE_REQUIRED" in subject.view()["last_error"]
    assert adapter.router.calls == 1


def test_revalidation_failure_returns_to_armed(tmp_path: Path) -> None:
    adapter = Adapter([card("0"), card("0.2"), card("0.01")])
    subject = guard(tmp_path, adapter)
    subject.configure(take_profit_krw=Decimal("100"), stop_loss_krw=Decimal("50"))
    subject.tick()
    assert subject.view()["state"] == ARMED
    assert adapter.router.calls == 0


def test_position_cycle_change_disables_guard(tmp_path: Path) -> None:
    adapter = Adapter([card("0"), card("0.2", side="SHORT")])
    subject = guard(tmp_path, adapter)
    subject.configure(take_profit_krw=Decimal("100"), stop_loss_krw=Decimal("50"))
    subject.tick()
    assert subject.view()["state"] == OFF
    assert adapter.router.calls == 0


def test_restart_mismatch_and_corrupt_state_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "guard.json"
    path.write_text(json.dumps({"enabled": True, "state": ARMED, "symbol": "BTCUSDT",
                                "side": "LONG", "position_qty": "2", "opened_at_ms": 10,
                                "take_profit_krw": "100", "stop_loss_krw": "50"}))
    mismatch = ExitGuard(adapter=Adapter([card(qty="1")]), path=path,
                         krw_rate=lambda: Decimal("1000"))
    assert mismatch.view()["state"] == OFF
    assert mismatch.view()["last_error"].startswith("RECOVERY_DISABLED")
    path.write_text("{broken")
    corrupt = ExitGuard(adapter=Adapter([card()]), path=path,
                        krw_rate=lambda: Decimal("1000"))
    assert corrupt.view()["state"] == OFF
    assert corrupt.view()["last_error"].startswith("RECOVERY_DISABLED")


def test_position_card_and_exit_guard_share_expected_close_net_conversion(tmp_path: Path) -> None:
    adapter = Adapter([card("8.88"), card("8.88")])
    subject = guard(tmp_path, adapter)
    subject.configure(take_profit_krw=Decimal("999999"), stop_loss_krw=Decimal("999999"))
    subject.tick()
    expected_card_krw = Decimal("8.88") * Decimal("1000")
    assert Decimal(subject.view()["current_net_krw"]) == expected_card_krw
    assert subject.view()["current_net_usdt"] == "8.88"
