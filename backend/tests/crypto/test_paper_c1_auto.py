from __future__ import annotations

from app.crypto.c1.models import Signal
from app.crypto.paper import sizing
from app.crypto.paper.c1_auto import (
    AUTO_4H_EXIT, AUTO_SOURCE, AUTO_TO_MANUAL_CLOSE, MANUAL_CLOSE_DURING_AUTO,
    C1AutoController,
)

from tests.crypto.conftest import make_config, quote


def signal(name: str, triggered: int, exit_at: int) -> Signal:
    return Signal(signal_id=name, triggered_at_ms=triggered, signal_bar_ms=triggered,
                  signal_price=100_000, official_entry_at_ms=triggered + 60_000,
                  planned_exit_at_ms=exit_at)


def controller(tmp_path, tiers) -> C1AutoController:
    return C1AutoController(manual_config=make_config(), tiers=tiers, root=tmp_path)


def reasons(auto: C1AutoController, event_type: str) -> list[str]:
    return [row["reason"] for row in auto.session.engine.ledger.events
            if row["event_type"] == event_type]


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


def test_restart_recovers_state_position_and_separate_ledger(tmp_path, tiers):
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
    assert recovered.root != manual_root
    assert all(row.get("reason") != "MANUAL" for row in
               recovered.session.engine.ledger.events if row["event_type"] == "POSITION_OPEN")
