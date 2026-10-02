"""The shadow ledger: the official 4 h trade, the observations, and recovery after a restart.

The arithmetic is checked against the contract by hand here - entry, exit, taker on both
notionals, microstructure, funding - rather than against the engine's own helper, so a change to
the cost formula has to be agreed with this file.
"""
from __future__ import annotations

from app.crypto.c1 import contract as K
from app.crypto.c1.engine import C1Engine
from app.crypto.c1.models import C1xEvent, ShadowTrade, Signal
from app.crypto.c1.runtime import C1Runtime
from app.crypto.c1.shadow import funding_paid, open_shadow, settle, summarize
from app.crypto.c1.store import C1Store
from tests.crypto.c1_fixtures import basis_dipping, build, falling_oi, window_bars

LAST = window_bars() - 1
# A trigger early enough that all 480 minutes of observation exist inside the window.
TRIGGER = LAST - 600 - ((LAST - 600) % 5) + 4


def _fire(funding=()):
    grid, _ = build(basis=basis_dipping([TRIGGER]), oi=falling_oi(TRIGGER))
    engine = C1Engine()
    signal = next(iter(engine.advance(grid, start_index=TRIGGER - 2, stop_index=TRIGGER + 1)))
    trade = open_shadow(signal, grid, list(funding))
    return grid, signal, trade


def test_overlapping_signals_keep_independent_sequence_shadow_and_c1x(tmp_path) -> None:
    runtime = C1Runtime(tmp_path)
    _, first, first_trade = _fire()
    second = Signal.from_json({**first.to_json(), "signal_id": f"{first.signal_id}-B",
                               "triggered_at_ms": first.triggered_at_ms + 60_000,
                               "signal_bar_ms": first.signal_bar_ms + 60_000})
    runtime.signals = {second.signal_id: second, first.signal_id: first}
    runtime.trades = {first.signal_id: first_trade,
                      second.signal_id: ShadowTrade(signal_id=second.signal_id, status="ACTIVE")}
    runtime.c1x = {first.signal_id: C1xEvent(signal_id=first.signal_id, status="TRIGGERED",
                                             triggered_at_ms=first.triggered_at_ms + 120_000)}

    assert runtime.display_sequences() == {first.signal_id: 1, second.signal_id: 2}
    rows = {row["signal_id"]: row for row in runtime.markers()}
    assert rows[first.signal_id]["display_seq"] == 1
    assert rows[first.signal_id]["c1x"]["display_seq"] == 1
    assert rows[first.signal_id]["c1x"]["parent_signal_id"] == first.signal_id
    assert rows[second.signal_id]["display_seq"] == 2
    assert rows[second.signal_id]["c1x"] is None

    # Persistence order and a process restart cannot become the UI identity.
    runtime.store.append_signals([second, first])
    restarted = C1Runtime(tmp_path)
    restarted.signals = restarted.store.signals()
    assert restarted.display_sequences() == {first.signal_id: 1, second.signal_id: 2}


def test_entry_is_the_open_of_the_bar_after_the_decision() -> None:
    grid, signal, trade = _fire()
    entry_index = grid.index_of(signal.official_entry_at_ms)
    assert entry_index == grid.index_of(signal.signal_bar_ms) + 1
    assert trade.entry_price == grid.opens[entry_index]


def test_official_exit_is_the_open_240_bars_later_and_the_return_is_long() -> None:
    grid, signal, trade = _fire()
    settle(trade, signal, grid, [])
    entry_index = grid.index_of(trade.entry_at_ms)
    exit_index = entry_index + K.OFFICIAL_HORIZON_MIN
    assert trade.status == "SETTLED"
    assert trade.exit_at_ms == grid.ts(exit_index)
    assert trade.exit_price == grid.opens[exit_index]
    assert trade.gross_return == grid.opens[exit_index] / grid.opens[entry_index] - 1


def test_cost_is_taker_on_both_notionals_plus_microstructure_plus_funding() -> None:
    """Contract section 6, VIP0_BASE. `taker * (1 + X/E)` is the round trip, which is why the
    results document calls it 11 bp at X ~ E rather than a flat constant."""
    grid, signal, trade = _fire()
    settle(trade, signal, grid, [])
    ratio = trade.exit_price / trade.entry_price
    expected = K.TAKER_RATE * (1 + ratio) + K.MICRO_BASE_FRAC
    assert trade.cost == expected
    assert trade.net_return == trade.gross_return - trade.cost
    assert abs(expected * 10_000 - 11.0) < 0.5


def test_funding_settled_while_held_is_charged_to_a_long() -> None:
    grid, signal, trade = _fire()
    entry = signal.official_entry_at_ms
    exit_at = signal.planned_exit_at_ms
    schedule = [(entry - K.MINUTE_MS, 0.001),      # before entry: not ours
                (entry, 0.0002),                   # exactly at entry: ours
                (entry + 60 * K.MINUTE_MS, 0.0003),
                (exit_at, 0.009)]                  # exactly at exit: not ours
    assert funding_paid(schedule, entry, exit_at) == 0.0002 + 0.0003
    settle(trade, signal, grid, schedule)
    assert trade.funding_paid == 0.0002 + 0.0003
    assert trade.cost == (K.TAKER_RATE * (1 + trade.exit_price / trade.entry_price)
                          + K.MICRO_BASE_FRAC + trade.funding_paid)


def test_every_observation_horizon_is_recorded_and_none_of_them_is_the_result() -> None:
    grid, signal, trade = _fire()
    settle(trade, signal, grid, [])
    for horizon in K.OBSERVATION_HORIZONS_MIN:
        assert f"{horizon}m_gross" in trade.observations
        assert f"{horizon}m_net" in trade.observations
    # The 4 h observation and the official result are the same trade seen twice; everything else
    # is a different holding period and must not be mixed into the official figure.
    assert trade.observations["240m_net"] == trade.net_return
    assert trade.horizon_min == K.OFFICIAL_HORIZON_MIN
    summary = summarize([trade])
    assert summary["horizon_min"] == 240
    assert summary["settled"] == 1
    assert "360m_net" not in summary and "480m_net" not in summary


def test_six_hour_observation_is_flagged_as_outside_the_research_horizons() -> None:
    """360 minutes is not a D5.2 horizon at all; it is kept because a later holding-period study
    asked for it, and the contract says so in one place."""
    assert 360 in K.OBSERVATION_HORIZONS_MIN
    assert 360 not in K.RESEARCH_HORIZONS_MIN
    assert K.HORIZONS_OUTSIDE_RESEARCH_MIN == (360,)
    assert K.RESEARCH_REFERENCE_HORIZON_MIN == 480


def test_excursions_span_the_bars_actually_held() -> None:
    grid, signal, trade = _fire()
    settle(trade, signal, grid, [])
    entry_index = grid.index_of(trade.entry_at_ms)
    held = range(entry_index, entry_index + K.OFFICIAL_HORIZON_MIN)
    assert trade.mfe == max(grid.highs[i] for i in held) / trade.entry_price - 1
    assert trade.mae == min(grid.lows[i] for i in held) / trade.entry_price - 1
    assert trade.mfe >= 0 >= trade.mae


def test_a_signal_whose_window_has_not_finished_stays_active() -> None:
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    engine = C1Engine()
    signal = next(iter(engine.advance(grid, start_index=LAST - 2, stop_index=LAST + 1)))
    trade = open_shadow(signal, grid, [])
    settle(trade, signal, grid, [])
    assert trade.status in ("ACTIVE", "AWAITING_ENTRY")
    assert trade.net_return is None and trade.exit_price is None


def test_settling_twice_changes_nothing() -> None:
    grid, signal, trade = _fire()
    settle(trade, signal, grid, [])
    first = trade.to_json()
    settle(trade, signal, grid, [])
    assert trade.to_json() == first


def test_the_shadow_is_recorded_even_though_no_order_was_placed() -> None:
    """The point of the ledger: it accumulates whether or not the operator acted."""
    grid, signal, trade = _fire()
    settle(trade, signal, grid, [])
    assert trade.status == "SETTLED"
    assert K.PLACES_ORDERS is False and K.MUTATES_ACCOUNT is False


def test_restart_recovery_settles_an_overdue_exit_from_the_historical_bar(tmp_path) -> None:
    """A 4 h window that ended while the process was down has one correct exit, and it is in the
    candles - not at the current price."""
    grid, signal, trade = _fire()
    store = C1Store(tmp_path)
    store.append_signals([signal])
    store.append_shadow([trade])                    # written while still open
    store.write_cursor(last_decided_at_ms=signal.triggered_at_ms, armed=False)

    reloaded_signal = store.signals()[signal.signal_id]
    reloaded_trade = store.shadow()[signal.signal_id]
    assert reloaded_trade.status in ("OPEN", "AWAITING_ENTRY")
    settle(reloaded_trade, reloaded_signal, grid, [])
    assert reloaded_trade.status == "SETTLED"

    direct = open_shadow(signal, grid, [])
    settle(direct, signal, grid, [])
    assert reloaded_trade.to_json() == direct.to_json()
    assert store.cursor()["armed"] is False


def test_the_ledger_keeps_one_row_per_signal_however_often_it_is_written(tmp_path) -> None:
    grid, signal, trade = _fire()
    store = C1Store(tmp_path)
    store.append_shadow([trade])
    settle(trade, signal, grid, [])
    store.append_shadow([trade])
    store.append_shadow([trade])
    assert len(store.shadow()) == 1
    assert store.shadow()[signal.signal_id].status == "SETTLED"


def test_summary_ignores_unsettled_trades() -> None:
    settled = ShadowTrade(signal_id="a", status="SETTLED", net_return=0.001, gross_return=0.002)
    open_one = ShadowTrade(signal_id="b", status="ACTIVE")
    summary = summarize([settled, open_one])
    assert summary["settled"] == 1
    assert summary["net_mean_bp"] == 10.0
