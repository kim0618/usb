from __future__ import annotations

from decimal import Decimal

from app.crypto.c1.models import Signal
from app.crypto.paper import sizing
from app.crypto.paper.c1_auto import (
    AUTO_4H_EXIT, AUTO_SOURCE, AUTO_TO_MANUAL_CLOSE, MANUAL_CLOSE_DURING_AUTO,
    C1AutoController,
)
from app.crypto.terminal.session import PaperSession

from tests.crypto.conftest import make_config, quote


def signal(name: str, triggered: int, exit_at: int) -> Signal:
    return Signal(signal_id=name, triggered_at_ms=triggered, signal_bar_ms=triggered,
                  signal_price=100_000, official_entry_at_ms=triggered + 60_000,
                  planned_exit_at_ms=exit_at)


def controller(tmp_path, tiers) -> C1AutoController:
    return C1AutoController(session=PaperSession(config=make_config(), tiers=tiers, root=tmp_path))


def reasons(auto: C1AutoController, event_type: str) -> list[str]:
    return [row["reason"] for row in auto.session.engine.ledger.events
            if row["event_type"] == event_type]


def open_manual(auto: C1AutoController, at: int = 1_000) -> None:
    auto.observe(quote(at))
    result = auto.session.command({
        "command": "ORDER", "ts_ms": at, "side": "LONG", "qty": "0.001",
        "intent": "OPEN", "request_id": f"manual-{at}", "reason": "PAPER_MANUAL",
    })
    assert result["rejection"] is None


def test_off_never_enters_and_on_uses_safe_max_10x(tmp_path, tiers):
    auto = controller(tmp_path, tiers)
    first = quote(1_000)
    event = signal("C1-1", 1_001, 20_000)
    auto.reconcile(first, [event])
    assert auto.session.engine.account.position.is_flat

    auto.enable(first)
    expected = sizing.max_entry(auto.session.engine, "LONG").qty
    auto.reconcile(quote(1_001), [event])
    position = auto.session.engine.account.position
    assert position.side == "LONG"
    assert position.abs_qty == expected
    assert position.leverage == 10
    assert reasons(auto, "POSITION_OPEN") == [AUTO_SOURCE]


def test_open_position_skips_overlapping_signal_and_does_not_replay_it(tmp_path, tiers):
    auto = controller(tmp_path, tiers)
    auto.enable(quote(1_000))
    one = signal("C1-1", 1_001, 5_000)
    two = signal("C1-2", 2_000, 8_000)
    auto.reconcile(quote(1_001), [one])
    qty = auto.session.engine.account.position.abs_qty
    auto.reconcile(quote(2_000), [one, two])
    assert auto.session.engine.account.position.abs_qty == qty
    assert len(reasons(auto, "POSITION_OPEN")) == 1
    auto.reconcile(quote(5_000), [one, two])
    assert auto.session.engine.account.position.is_flat
    auto.reconcile(quote(5_001), [one, two])
    assert auto.session.engine.account.position.is_flat


def test_only_benchmark_closes_and_manual_close_keeps_enabled(tmp_path, tiers):
    auto = controller(tmp_path, tiers)
    auto.enable(quote(1_000))
    event = signal("C1-1", 1_001, 5_000)
    auto.reconcile(quote(1_001), [event])
    # A C1x-like moment is not an input to the controller and cannot mutate the account.
    auto.reconcile(quote(4_999), [event])
    assert not auto.session.engine.account.position.is_flat
    auto.reconcile(quote(5_000), [event])
    assert auto.session.engine.account.position.is_flat
    assert reasons(auto, "POSITION_CLOSE")[-1] == AUTO_4H_EXIT

    next_event = signal("C1-2", 6_000, 10_000)
    auto.reconcile(quote(6_000), [event, next_event])
    assert auto.close(quote(6_100), MANUAL_CLOSE_DURING_AUTO)
    assert auto.state.enabled
    assert reasons(auto, "POSITION_CLOSE")[-1] == MANUAL_CLOSE_DURING_AUTO


def test_disable_closes_first_and_failure_keeps_auto(tmp_path, tiers, monkeypatch):
    auto = controller(tmp_path, tiers)
    auto.enable(quote(1_000))
    auto.reconcile(quote(1_001), [signal("C1-1", 1_001, 5_000)])
    ok, _ = auto.disable(quote(2_000))
    assert ok and not auto.state.enabled
    assert reasons(auto, "POSITION_CLOSE")[-1] == AUTO_TO_MANUAL_CLOSE

    auto.enable(quote(3_000))
    auto.reconcile(quote(3_001), [signal("C1-2", 3_001, 9_000)])
    monkeypatch.setattr(auto, "close", lambda *_args, **_kwargs: False)
    ok, _ = auto.disable(quote(4_000))
    assert not ok and auto.state.enabled


def test_toggle_preserves_one_account_position_pnl_and_ledger(tmp_path, tiers):
    auto = controller(tmp_path, tiers)
    open_manual(auto)
    auto.observe(quote(2_000))
    account = auto.session.engine.account
    account.realized_pnl = Decimal("122.45598")
    before = (account.wallet_balance, account.equity(quote(2_000).mark_price),
              account.position.signed_qty, account.realized_pnl,
              account.unrealized_pnl(quote(2_000).mark_price),
              list(auto.session.engine.ledger.events), auto.session.config.run_id)

    auto.enable(quote(2_000))
    ok, _ = auto.disable(quote(2_001))
    auto.enable(quote(2_002))
    ok_again, _ = auto.disable(quote(2_003))

    after = (account.wallet_balance, account.equity(quote(2_000).mark_price),
             account.position.signed_qty, account.realized_pnl,
             account.unrealized_pnl(quote(2_000).mark_price),
             list(auto.session.engine.ledger.events), auto.session.config.run_id)
    assert ok and ok_again
    assert after == before


def test_manual_position_blocks_auto_until_flat_then_next_c1_enters(tmp_path, tiers):
    auto = controller(tmp_path, tiers)
    open_manual(auto)
    auto.enable(quote(1_001))
    held_signal = signal("C1-held", 1_002, 8_000)
    auto.reconcile(quote(1_002), [held_signal])
    assert reasons(auto, "POSITION_OPEN") == ["PAPER_MANUAL"]

    position = auto.session.engine.account.position
    auto.session.command({
        "command": "ORDER", "ts_ms": 2_000, "side": position.side,
        "qty": str(position.abs_qty), "intent": "CLOSE", "request_id": "manual-close",
        "reason": "PAPER_MANUAL",
    }, quote=quote(2_000))
    auto.reconcile(quote(2_001), [held_signal])
    assert auto.session.engine.account.position.is_flat

    next_signal = signal("C1-next", 3_000, 9_000)
    auto.reconcile(quote(3_000), [held_signal, next_signal])
    assert not auto.session.engine.account.position.is_flat
    assert auto.session.engine.account.position.leverage == 10
    assert reasons(auto, "POSITION_OPEN") == ["PAPER_MANUAL", AUTO_SOURCE]


def test_restart_recovers_state_position_and_shared_ledger(tmp_path, tiers):
    manual_root = tmp_path / "test-run"
    auto = controller(tmp_path, tiers)
    auto.enable(quote(1_000))
    event = signal("C1-1", 1_001, 5_000)
    auto.reconcile(quote(1_001), [event])

    recovered = controller(tmp_path, tiers)
    assert recovered.state.enabled
    assert recovered.state.active_signal_id == "C1-1"
    assert not recovered.session.engine.account.position.is_flat
    assert recovered.state.active_benchmark_at == 5_000
    assert recovered.root == manual_root
    assert all(row.get("reason") != "MANUAL" for row in
               recovered.session.engine.ledger.events if row["event_type"] == "POSITION_OPEN")
