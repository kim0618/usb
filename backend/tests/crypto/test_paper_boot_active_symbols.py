"""PAPER boot: historical accounting is not an active market-data session.

Every run on disk joins the shared wallet at boot, because its realised PnL and charges are money
the purse holds whether or not anybody is looking at that symbol. Before this change ETHUSDT and
SOLUSDT were also *activated* at boot - a session on the 1 Hz loop, a Bybit socket, a tape growing
every second - only because their run directory existed. Now a flat ETH/SOL run is recovered into
`retired` exactly as a former HOT run is: in the wallet, off the loop, no feed, revived with the
same session on its first request. BTC is unchanged; a run that still holds a position stays
active so it can be seen and closed.

Real `Runtime`, real sessions, real files; the public feed is stubbed so no test opens a socket.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

from typing import Any

import pytest

from app.crypto import symbols as symbols_module
from tests.crypto.conftest import quote
from tests.crypto.test_paper_wallet_recovery import account_view

T0 = 1_791_600_000_000
ETH, SOL, BTC = "ETHUSDT", "SOLUSDT", "BTCUSDT"
PRICES = {BTC: "100000.0", ETH: "3000.00", SOL: "150.000"}
QTY = {BTC: "0.010", ETH: "0.10", SOL: "1.0"}


def _config(tmp_path: Path) -> Path:
    risk = sorted(Path("data/runtime/crypto/BTCUSDT/reference").glob("risk_limit_*.json"))
    if not risk or not Path("data/runtime/crypto/ETHUSDT/reference").is_dir() \
            or not Path("data/runtime/crypto/SOLUSDT/reference").is_dir():
        pytest.skip("BTC/ETH/SOL references are not in this checkout")
    path = tmp_path / "run_config.json"
    path.write_text(json.dumps({
        "run_id": "boot-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1341.00",
        "fx_source": "test", "fx_asof_utc": "2026-10-04T00:00:00Z", "fee_version": "v",
        "fee_taker_rate": "0.00055", "fee_maker_rate": "0.0002", "fee_source": "s",
        "fee_effective_date": "2026-09-02", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk[-1].resolve())}))
    return path


@pytest.fixture
def boot(monkeypatch, tmp_path):
    """`make()` boots a fresh Runtime over the same run root; `feeds` records feed requests."""
    from app.crypto.terminal.api import Runtime
    feeds: list[str | None] = []
    monkeypatch.setattr(Runtime, "feed_for", lambda self, symbol=None: feeds.append(symbol))
    config_path, root = _config(tmp_path), tmp_path / "run"
    root.mkdir()

    def make() -> Runtime:
        monkeypatch.setattr(symbols_module, "_dynamic", set())
        feeds.clear()
        runtime = Runtime()
        runtime.build(config_path, root)
        assert runtime.error is None
        return runtime
    return make, root, feeds


def _q(symbol: str, ts: int, mid: str | None = None):
    price = Decimal(mid or PRICES[symbol])
    step = {BTC: Decimal("0.1"), ETH: Decimal("0.01"), SOL: Decimal("0.001")}[symbol]
    return quote(ts, bid=str(price), ask=str(price + step), mark=str(price),
                 bid_qty="1000", ask_qty="1000")


def _order(session, ts: int, side: str, qty: str, intent: str = "OPEN", q=None) -> None:
    result = session.command({"command": "ORDER", "ts_ms": ts, "side": side, "qty": qty,
                              "intent": intent, "request_id": f"r{ts}", "reason": "MANUAL"},
                             quote=q)
    assert result["rejection"] is None, result["rejection"]


def _round_trip(runtime, symbol: str, ts: int, exit_mid: str) -> None:
    """Open and close one position: the run gains realised PnL and fees and ends flat."""
    session = runtime.session_for(symbol)
    if not session.is_started:
        session.start(ts)
    _order(session, ts + 1_000, "LONG", QTY[symbol], q=_q(symbol, ts + 1_000))
    _order(session, ts + 2_000, "LONG", QTY[symbol], intent="CLOSE",
           q=_q(symbol, ts + 2_000, exit_mid))
    assert session.engine.account.position.is_flat


def _history(make):
    """First process: ETH and SOL each trade to flat, BTC is left holding a position."""
    first = make()
    _round_trip(first, ETH, T0, "3030.00")
    _round_trip(first, SOL, T0 + 10_000, "148.500")
    btc = first.session
    if not btc.is_started:
        btc.start(T0 + 20_000)
    _order(btc, T0 + 21_000, "SHORT", QTY[BTC], q=_q(BTC, T0 + 21_000))
    return first


#: Marks away from every entry, so equity carries a real unrealised term.
MARKS = {BTC: Decimal("99000.0"), ETH: Decimal("3100.00"), SOL: Decimal("140.000")}


def _views(runtime) -> dict[str, Any]:
    """Every member's account (the wallet-recovery tests' view) and the purse's figures."""
    cash = runtime.cash
    return {"members": {symbol: account_view(account)
                        for symbol, account in sorted(cash.members.items())},
            "wallet": cash.wallet_balance, "available": cash.available_balance,
            "used_margin": cash.used_margin,
            "equity": cash.equity({s: MARKS[s] for s in cash.members})}


def _tape_state(root: Path, run_id: str) -> tuple:
    run = root / run_id
    tape = run / "input.jsonl"
    segments = run / "segments"
    return (tape.stat().st_size if tape.exists() else None,
            tape.stat().st_mtime_ns if tape.exists() else None,
            sorted(p.name for p in segments.iterdir()) if segments.is_dir() else [],
            (run / "ledger.jsonl").read_bytes() if (run / "ledger.jsonl").exists() else b"")


# --------------------------------------------------------------------------- boot contract


def test_btc_auto_starts_and_flat_eth_sol_do_not(boot) -> None:
    make, _root, feeds = boot
    _history(make)
    second = make()
    assert BTC in second.sessions and second.session is second.sessions[BTC]
    assert ETH not in second.sessions and SOL not in second.sessions
    assert ETH in second.retired and SOL in second.retired
    assert ETH not in feeds and SOL not in feeds, feeds
    assert second.held_symbols() == []


def test_flat_eth_sol_history_still_contributes_to_the_shared_wallet(boot) -> None:
    make, _root, _feeds = boot
    first = _history(make)
    before = _views(first)
    eth_realised = first.sessions[ETH].engine.account.realized_pnl
    sol_realised = first.sessions[SOL].engine.account.realized_pnl
    assert eth_realised != 0 and sol_realised != 0       # the history really moved money

    second = make()
    assert sorted(second.cash.members) == [BTC, ETH, SOL]
    assert second.cash.wallet_balance == first.cash.wallet_balance
    assert _views(second) == before                    # wallet, equity, available, used margin
    assert second.retired[ETH].engine.account.realized_pnl == eth_realised
    assert second.retired[SOL].engine.account.realized_pnl == sol_realised
    assert second.cash.members[ETH] is second.retired[ETH].engine.account


def test_wallet_matches_the_old_boot_path_figure_for_figure(boot, monkeypatch) -> None:
    """The control: the pre-change boot (every traded symbol activated) gives the same money."""
    from app.crypto.terminal.api import Runtime
    make, _root, _feeds = boot
    _history(make)
    new = make()
    new_views, new_ledgers = _views(new), {s: x.engine.ledger.bytes()
                                           for s, x in {**new.retired, **new.sessions}.items()}

    def old_join(self) -> None:                         # api.py before this change
        for symbol in (ETH, SOL):
            if (self.root / f"{self.base_config.run_id}-{symbol}").is_dir():
                self.session_for(symbol)
        self._join_dynamic_runs_with_history()

    monkeypatch.setattr(Runtime, "_join_symbols_with_history", old_join)
    old = make()
    assert ETH in old.sessions and SOL in old.sessions
    assert _views(old) == new_views
    assert {s: x.engine.ledger.bytes() for s, x in old.sessions.items()} == new_ledgers


def test_an_eth_run_that_still_holds_a_position_stays_active(boot) -> None:
    make, _root, feeds = boot
    first = make()
    if not first.session.is_started:
        first.session.start(T0)
    eth = first.session_for(ETH)
    eth.start(T0)
    _order(eth, T0 + 1_000, "LONG", QTY[ETH], q=_q(ETH, T0 + 1_000))
    before = _views(first)

    second = make()
    assert ETH in second.sessions and ETH not in second.retired
    assert ETH in feeds
    assert second.held_symbols() == [ETH]
    assert _views(second) == before


def test_the_first_request_revives_the_same_session_without_a_replay(boot) -> None:
    make, _root, feeds = boot
    _history(make)
    second = make()
    parked = second.retired[ETH]
    events = len(parked.engine.ledger.events)
    feeds.clear()

    revived = second.session_for(ETH)
    assert revived is parked
    assert ETH in second.sessions and ETH not in second.retired
    assert feeds == [ETH]
    assert len(revived.engine.ledger.events) == events
    assert second.cash.members[ETH] is revived.engine.account


def test_restarting_again_never_recreates_eth_sol_feeds(boot) -> None:
    make, _root, feeds = boot
    _history(make)
    for _ in range(2):
        runtime = make()
        assert ETH not in runtime.sessions and SOL not in runtime.sessions
        assert ETH not in feeds and SOL not in feeds


def test_btc_position_and_recovery_are_unaffected(boot) -> None:
    make, _root, _feeds = boot
    first = _history(make)
    btc_before = first.session.engine.ledger.bytes()
    second = make()
    assert second.session.engine.account.position.signed_qty == Decimal("-" + QTY[BTC])
    assert second.session.engine.ledger.bytes() == btc_before
    third = make()
    assert third.session.engine.ledger.bytes() == btc_before
    assert _views(third) == _views(second)


# --------------------------------------------------------------------------- data writes


class _FakeFeed:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.ts = T0 + 100_000

    def quote(self):
        self.ts += 1_000                                # one market second per tick
        return _q(self.symbol, self.ts)


def test_after_boot_only_btc_writes_tape(boot) -> None:
    make, root, _feeds = boot
    _history(make)
    second = make()
    run_id = second.base_config.run_id
    eth_before = _tape_state(root, f"{run_id}-{ETH}")
    sol_before = _tape_state(root, f"{run_id}-{SOL}")
    btc_before = _tape_state(root, run_id)
    # Feeds for every symbol, as if each had a socket: only the loop's membership decides.
    second.feeds = {symbol: _FakeFeed(symbol) for symbol in (BTC, ETH, SOL)}

    async def run_loop() -> None:
        task = asyncio.create_task(second._observe_loop())
        await asyncio.sleep(1.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_loop())
    assert second.error is None
    assert _tape_state(root, f"{run_id}-{ETH}") == eth_before
    assert _tape_state(root, f"{run_id}-{SOL}") == sol_before
    assert _tape_state(root, run_id)[0] > btc_before[0]
