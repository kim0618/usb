"""PAPER multi-symbol restart recovery: runs that share one wallet are replayed together.

Each scenario drives real paper sessions on one shared wallet the way the terminal does, then
restarts and compares the recovered state with the state the process held before it stopped:
every member's ledger bytes, position, realised PnL, fees, funding and the wallet figures.

The control in each case is the old behaviour: the HOT run recovered alone is refused, because
its ledger carries wallet figures that include BTC's money. No test touches the network.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.crypto import symbols as symbols_module
from app.crypto.hot_coins import lock as hot_lock
from app.crypto.hot_coins import market as hot_market
from app.crypto.paper.account import SharedCash
from app.crypto.paper.instrument import RiskTierTable
from app.crypto.paper.persistence import RecoveryRefused, recover
from app.crypto.paper.wallet_recovery import (SOURCE_WALLET_REPLAY, WalletMember,
                                              recover_wallet, wallet_event_times)
from app.crypto.terminal.api import dynamic_run_config
from app.crypto.terminal.session import PaperSession
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD, make_config, quote
from tests.crypto.test_hot_symbol_v1 import exchange_row, hot_risk_payload

HOT, HOT2 = "LUMIAUSDT", "KAIAUSDT"
T0 = 1_791_600_000_000          # 2026-10-10, an hour before a funding boundary
FUNDING_MS = 8 * 3_600_000


@pytest.fixture(autouse=True)
def clean_dynamic(monkeypatch):
    monkeypatch.setattr(symbols_module, "_dynamic", set())


def btc_quote(ts: int, mid: str = "100000.0", funding: str | None = None):
    bid = Decimal(mid)
    return quote(ts, bid=str(bid), ask=str(bid + Decimal("0.1")), mark=str(bid),
                 funding_rate=funding)


def hot_quote(ts: int, mid: str = "0.12000", funding: str | None = None):
    bid = Decimal(mid)
    return quote(ts, bid=str(bid), ask=str(bid + Decimal("0.00001")), mark=str(bid),
                 bid_qty="1000000", ask_qty="1000000", funding_rate=funding)


class Wallet:
    """A terminal's paper runtime reduced to what recovery is about: sessions and one purse."""

    def __init__(self, root: Path, *, segment_records: int = 20_000) -> None:
        self.root = root
        self.segment_records = segment_records
        self.base = make_config(leverage="10")
        self.tiers = RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="test", source_sha256="x")
        self.cash = SharedCash(owner="BTCUSDT")
        self.sessions: dict[str, PaperSession] = {}
        self.configs: dict[str, Any] = {"BTCUSDT": (self.base, self.tiers)}
        self._open("BTCUSDT", T0)

    def _open(self, symbol: str, ts: int) -> None:
        config, tiers = self.configs[symbol]
        session = PaperSession(config=config, tiers=tiers, root=self.root,
                               segment_records=self.segment_records)
        session.start(ts)
        self.sessions[symbol] = session
        self.cash.join(symbol, session.engine.account)

    def add_hot(self, symbol: str, ts: int) -> None:
        hot_market.write_reference(self.root, symbol, exchange_row=exchange_row(symbol),
                                   funding_minutes=480, risk_payload=hot_risk_payload(symbol))
        self.configs[symbol] = dynamic_run_config(self.base, symbol, self.root)
        self._open(symbol, ts)

    def q(self, symbol: str, ts: int, mid: str | None = None, funding: str | None = None):
        if symbol == "BTCUSDT":
            return btc_quote(ts, mid or "100000.0", funding)
        return hot_quote(ts, mid or "0.12000", funding)

    def tick(self, symbol: str, ts: int, mid: str | None = None, funding: str | None = None):
        self.sessions[symbol].observe(self.q(symbol, ts, mid, funding), force=True)

    def order(self, symbol: str, ts: int, side: str, qty: str, intent: str = "OPEN",
              mid: str | None = None) -> dict[str, Any]:
        result = self.sessions[symbol].command(
            {"command": "ORDER", "ts_ms": ts, "side": side, "qty": qty, "intent": intent,
             "request_id": f"{symbol}-{ts}", "reason": "MANUAL"},
            quote=self.q(symbol, ts, mid))
        assert result["rejection"] is None, result["rejection"]
        return result

    def member(self, symbol: str) -> WalletMember:
        config, tiers = self.configs[symbol]
        return WalletMember(symbol, self.root / config.run_id, config, tiers)

    def restart(self):
        return recover_wallet(self.member("BTCUSDT"),
                              [self.member(s) for s in self.sessions if s != "BTCUSDT"])

    def ledger_hashes(self) -> dict[str, str]:
        return {s: hashlib.sha256((self.member(s).run_dir / "ledger.jsonl").read_bytes()).hexdigest()
                for s in self.sessions}


def account_view(account) -> dict[str, Any]:
    position = account.position
    return {"realized": account.realized_pnl, "fees": account.cumulative_fees,
            "funding": account.cumulative_funding_paid, "qty": position.signed_qty,
            "entry": position.avg_entry, "leverage": position.leverage,
            "wallet": account.wallet_balance, "available": account.available_balance,
            "used_margin": account.used_margin}


def assert_parity(wallet: Wallet, recovered) -> None:
    assert recovered is not None
    for symbol, session in wallet.sessions.items():
        engine, result = recovered.engines[symbol]
        assert result.source == SOURCE_WALLET_REPLAY
        assert result.ledger_tail_rewritten == 0
        assert engine.ledger.bytes() == session.engine.ledger.bytes(), symbol
        assert account_view(engine.account) == account_view(session.engine.account), symbol
        fills = [e for e in engine.ledger.events if e["event_type"] == "FILL"]
        live_fills = [e for e in session.engine.ledger.events if e["event_type"] == "FILL"]
        assert fills == live_fills
    assert recovered.cash.wallet_balance == wallet.cash.wallet_balance
    assert recovered.cash.available_balance == wallet.cash.available_balance
    assert recovered.cash.used_margin == wallet.cash.used_margin


def hot_alone_is_refused(wallet: Wallet, symbol: str = HOT) -> None:
    """The control: what recovery did before this change."""
    member = wallet.member(symbol)
    with pytest.raises(RecoveryRefused):
        recover(member.run_dir, member.config, member.tiers, use_checkpoint=False)


# --------------------------------------------------------------------------- A..F


def test_a_btc_only_keeps_the_single_run_path_byte_for_byte(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.tick("BTCUSDT", T0 + 2_000, "100050.0")
    before = wallet.ledger_hashes()
    assert wallet.restart() is None
    engine, _ = recover(wallet.member("BTCUSDT").run_dir, wallet.base, wallet.tiers)
    assert engine.ledger.bytes() == wallet.sessions["BTCUSDT"].engine.ledger.bytes()
    assert wallet.ledger_hashes() == before


def test_a_hot_run_that_never_traded_keeps_the_single_run_path(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.add_hot(HOT, T0 + 2_000)
    wallet.tick(HOT, T0 + 3_000)
    assert wallet_event_times(wallet.member(HOT).run_dir) == []
    assert wallet.restart() is None


def test_b_hot_position_only(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.add_hot(HOT, T0 + 1_000)
    wallet.order(HOT, T0 + 2_000, "LONG", "600.0")
    wallet.tick(HOT, T0 + 3_000, "0.12100")
    wallet.tick("BTCUSDT", T0 + 3_500)
    assert_parity(wallet, wallet.restart())


@pytest.mark.parametrize("hot_side", ["LONG", "SHORT"])
def test_c_d_btc_long_with_hot_long_or_short(tmp_path, hot_side) -> None:
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.add_hot(HOT, T0 + 2_000)
    wallet.order(HOT, T0 + 3_000, hot_side, "600.0")
    wallet.tick("BTCUSDT", T0 + 4_000, "100200.0")
    wallet.tick(HOT, T0 + 4_500, "0.11900")
    before = wallet.ledger_hashes()
    hot_alone_is_refused(wallet)
    recovered = wallet.restart()
    assert_parity(wallet, recovered)
    assert recovered.engines["BTCUSDT"][0].account.position.signed_qty == Decimal("0.010")
    sign = 1 if hot_side == "LONG" else -1
    assert recovered.engines[HOT][0].account.position.signed_qty == sign * Decimal("600.0")
    assert wallet.ledger_hashes() == before, "recovery must not rewrite a ledger"


def test_e_btc_then_hot_then_btc(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.add_hot(HOT, T0 + 2_000)
    wallet.order(HOT, T0 + 3_000, "LONG", "600.0")
    wallet.order(HOT, T0 + 4_000, "LONG", "600.0", intent="CLOSE", mid="0.12300")
    wallet.order("BTCUSDT", T0 + 5_000, "LONG", "0.005")       # sees HOT's realised PnL
    wallet.order("BTCUSDT", T0 + 6_000, "LONG", "0.015", intent="CLOSE", mid="99900.0")
    hot_alone_is_refused(wallet)
    with pytest.raises(RecoveryRefused):     # and BTC alone is refused too, the other order
        recover(wallet.member("BTCUSDT").run_dir, wallet.base, wallet.tiers, use_checkpoint=False)
    assert_parity(wallet, wallet.restart())


def test_f_hot_then_btc_then_hot_close(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.add_hot(HOT, T0 + 1_000)
    wallet.order(HOT, T0 + 2_000, "SHORT", "600.0")
    wallet.order("BTCUSDT", T0 + 3_000, "LONG", "0.010")
    wallet.order(HOT, T0 + 4_000, "SHORT", "600.0", intent="CLOSE", mid="0.11800")
    wallet.tick("BTCUSDT", T0 + 5_000)
    assert_parity(wallet, wallet.restart())


def test_funding_on_both_symbols_across_a_boundary(tmp_path) -> None:
    """Funding is charged on a market record; the other symbol's next fill must see it."""
    wallet = Wallet(tmp_path)
    boundary = (T0 // FUNDING_MS + 1) * FUNDING_MS
    wallet.order("BTCUSDT", boundary - 60_000, "LONG", "0.010")
    wallet.add_hot(HOT, boundary - 50_000)
    wallet.order(HOT, boundary - 40_000, "LONG", "600.0")
    wallet.tick("BTCUSDT", boundary - 30_000, funding="0.0001")
    wallet.tick(HOT, boundary - 20_000, funding="-0.0003")
    wallet.tick("BTCUSDT", boundary + 1_000, funding="0.0001")
    wallet.tick(HOT, boundary + 2_000, funding="-0.0003")
    wallet.order(HOT, boundary + 3_000, "LONG", "600.0")
    wallet.order("BTCUSDT", boundary + 4_000, "LONG", "0.010", intent="CLOSE")
    assert any(e["event_type"] == "FUNDING" for e in wallet.sessions[HOT].engine.ledger.events)
    assert_parity(wallet, wallet.restart())


def test_two_hot_runs_one_retired(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.add_hot(HOT, T0 + 2_000)
    wallet.order(HOT, T0 + 3_000, "LONG", "600.0")
    wallet.order(HOT, T0 + 4_000, "LONG", "600.0", intent="CLOSE", mid="0.12200")
    wallet.add_hot(HOT2, T0 + 5_000)
    wallet.order(HOT2, T0 + 6_000, "SHORT", "600.0")
    wallet.order("BTCUSDT", T0 + 7_000, "LONG", "0.002")
    assert_parity(wallet, wallet.restart())


# --------------------------------------------------------------------------- determinism


def test_restarting_twice_is_restarting_once(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.add_hot(HOT, T0 + 2_000)
    wallet.order(HOT, T0 + 3_000, "LONG", "600.0")
    first, second = wallet.restart(), wallet.restart()
    for symbol in wallet.sessions:
        assert first.engines[symbol][0].ledger.bytes() == second.engines[symbol][0].ledger.bytes()
        assert account_view(first.engines[symbol][0].account) == \
            account_view(second.engines[symbol][0].account)


def test_member_join_order_does_not_change_a_wallet_figure(tmp_path) -> None:
    """The live process joins members as they are first used; a restart joins them in another
    order. Sums over the purse run owner-first-then-symbol, so the bytes are the same."""
    wallet = Wallet(tmp_path)
    wallet.add_hot(HOT2, T0 + 1_000)       # KAIA joins before LUMIA in the live process
    wallet.order(HOT2, T0 + 2_000, "LONG", "333.3", mid="0.13337")
    wallet.add_hot(HOT, T0 + 3_000)
    wallet.order(HOT, T0 + 4_000, "LONG", "777.7", mid="0.11113")
    wallet.order("BTCUSDT", T0 + 5_000, "LONG", "0.007", mid="100003.3")
    reordered = SharedCash(owner="BTCUSDT")
    for symbol in ["BTCUSDT", HOT, HOT2]:
        reordered.join(symbol, wallet.sessions[symbol].engine.account)
    assert reordered.available_balance == wallet.cash.available_balance
    assert_parity(wallet, wallet.restart())


def test_purse_sums_do_not_depend_on_join_order() -> None:
    """Values chosen so the 28-digit rounding differs with the order of addition."""
    from app.crypto.paper.account import Account

    def purse(order: list[str]) -> SharedCash:
        accounts = {"BTCUSDT": Account(starting_capital_usdt=Decimal("1000")),
                    "ZZZUSDT": Account(starting_capital_usdt=Decimal(0),
                                       realized_pnl=Decimal("1000000.000000000000000000000")),
                    "AAAUSDT": Account(starting_capital_usdt=Decimal(0),
                                       realized_pnl=Decimal("6E-22")),
                    "BBBUSDT": Account(starting_capital_usdt=Decimal(0),
                                       realized_pnl=Decimal("6E-22"))}
        cash = SharedCash(owner="BTCUSDT")
        for symbol in order:
            cash.join(symbol, accounts[symbol])
        return cash

    live = purse(["BTCUSDT", "ZZZUSDT", "AAAUSDT", "BBBUSDT"])
    restarted = purse(["BTCUSDT", "AAAUSDT", "BBBUSDT", "ZZZUSDT"])
    assert live.realized_delta == restarted.realized_delta
    assert live.wallet_balance == restarted.wallet_balance


def test_same_timestamp_puts_the_owner_first(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.add_hot(HOT, T0 + 1_000)
    wallet.order("BTCUSDT", T0 + 2_000, "LONG", "0.010")
    wallet.order(HOT, T0 + 2_000, "LONG", "600.0")       # same quote time, BTC first
    assert_parity(wallet, wallet.restart())


def test_an_order_the_timestamps_cannot_tell_apart_is_refused_not_guessed(tmp_path) -> None:
    """The residual case: two symbols' quote clocks put a later fill at an earlier time. The
    merge cannot know, the rebuilt bytes differ, and recovery refuses rather than invent."""
    wallet = Wallet(tmp_path)
    wallet.add_hot(HOT, T0 + 1_000)
    wallet.order(HOT, T0 + 3_000, "LONG", "600.0")
    wallet.order("BTCUSDT", T0 + 2_500, "LONG", "0.010")   # processed later, stamped earlier
    before = wallet.ledger_hashes()
    with pytest.raises(RecoveryRefused) as refused:
        wallet.restart()
    assert refused.value.code == "LEDGER_DIVERGENCE"
    assert wallet.ledger_hashes() == before


def test_a_tampered_ledger_is_refused_and_nothing_is_written(tmp_path) -> None:
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.add_hot(HOT, T0 + 2_000)
    wallet.order(HOT, T0 + 3_000, "LONG", "600.0")
    path = wallet.member(HOT).run_dir / "ledger.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"600.0"', b'"601.0"', 1))
    before = wallet.ledger_hashes()
    with pytest.raises(RecoveryRefused):
        wallet.restart()
    assert wallet.ledger_hashes() == before


# --------------------------------------------------------------------------- crash recovery


def test_kill_dash_nine_torn_tape_and_lagging_ledger(tmp_path) -> None:
    """The process died after a HOT order reached the tape and before its ledger lines did,
    and a market record was half written. The tape is authority; the ledger tail is rebuilt."""
    wallet = Wallet(tmp_path)
    wallet.order("BTCUSDT", T0 + 1_000, "LONG", "0.010")
    wallet.add_hot(HOT, T0 + 2_000)
    wallet.order(HOT, T0 + 3_000, "LONG", "600.0")
    hot_dir = wallet.member(HOT).run_dir
    full = (hot_dir / "ledger.jsonl").read_bytes()
    lines = full.splitlines(keepends=True)
    keep = next(i for i, line in enumerate(lines) if b'"ORDER_SUBMITTED"' in line)
    (hot_dir / "ledger.jsonl").write_bytes(b"".join(lines[:keep]))
    with (wallet.member("BTCUSDT").run_dir / "input.jsonl").open("ab") as tape:
        tape.write(b'{"kind":"MARKET","payload":{"ts_ms":')
    recovered = wallet.restart()
    engine, result = recovered.engines[HOT]
    assert result.ledger_tail_rewritten == len(full) - len(b"".join(lines[:keep]))
    assert (hot_dir / "ledger.jsonl").read_bytes() == full
    assert engine.account.position.signed_qty == Decimal("600.0")


def test_checkpoints_cut_the_replay_and_an_older_one_is_used_when_the_newest_is_torn(tmp_path) -> None:
    wallet = Wallet(tmp_path, segment_records=4)
    for i in range(12):                                  # BTC rotates several times first
        wallet.tick("BTCUSDT", T0 + 1_000 * (i + 1))
    wallet.order("BTCUSDT", T0 + 20_000, "LONG", "0.010")
    for i in range(12):
        wallet.tick("BTCUSDT", T0 + 21_000 + 1_000 * i)
    wallet.add_hot(HOT, T0 + 40_000)
    wallet.order(HOT, T0 + 41_000, "LONG", "600.0")
    for i in range(10):
        wallet.tick(HOT, T0 + 42_000 + 1_000 * i)
        wallet.tick("BTCUSDT", T0 + 42_500 + 1_000 * i)
    recovered = wallet.restart()
    assert_parity(wallet, recovered)
    btc_start = recovered.engines["BTCUSDT"][1].checkpoint_segment
    assert btc_start is not None, "BTC must start from a checkpoint, not its first record"
    assert recovered.cut_ts_ms is not None

    checkpoints = sorted((wallet.member("BTCUSDT").run_dir / "checkpoints").glob("*.json"))
    checkpoints[-1].write_text("{torn")
    again = wallet.restart()
    assert_parity(wallet, again)


def test_no_usable_start_for_the_owner_is_refused(tmp_path) -> None:
    """The deployed BTC run cannot be replayed from its first record (CASE B). With every
    checkpoint unusable the wallet replay refuses instead of guessing."""
    wallet = Wallet(tmp_path, segment_records=4)
    for i in range(8):
        wallet.tick("BTCUSDT", T0 + 1_000 * (i + 1))
    wallet.add_hot(HOT, T0 + 10_000)
    wallet.order(HOT, T0 + 11_000, "LONG", "600.0")
    btc = wallet.member("BTCUSDT").run_dir
    for path in (btc / "checkpoints").glob("*.json"):
        path.unlink()
    ledger = btc / "ledger.jsonl"
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    events[0]["ledger_schema_version"] = "crypto.paper.ledger.v2"
    from app.crypto.paper.ledger import canonical
    ledger.write_bytes(b"".join(canonical(e) for e in events))
    with pytest.raises(RecoveryRefused) as refused:
        wallet.restart()
    assert refused.value.code == "LEDGER_SCHEMA_MISMATCH"


# --------------------------------------------------------------------------- the real Runtime


def _runtime_config(tmp_path: Path) -> Path:
    risk = sorted(Path("data/runtime/crypto/BTCUSDT/reference").glob("risk_limit_*.json"))
    if not risk:
        pytest.skip("BTC risk-limit reference is not in this checkout")
    path = tmp_path / "run_config.json"
    path.write_text(json.dumps({
        "run_id": "wallet-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1341.00",
        "fx_source": "test", "fx_asof_utc": "2026-10-04T00:00:00Z", "fee_version": "v",
        "fee_taker_rate": "0.00055", "fee_maker_rate": "0.0002", "fee_source": "s",
        "fee_effective_date": "2026-09-02", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk[-1].resolve())}))
    return path


@pytest.fixture
def boot(monkeypatch, tmp_path):
    from app.crypto.terminal.api import Runtime
    monkeypatch.setattr(Runtime, "feed_for", lambda self, symbol=None: None)
    config_path, root = _runtime_config(tmp_path), tmp_path / "run"
    root.mkdir()

    def make() -> Runtime:
        monkeypatch.setattr(symbols_module, "_dynamic", set())
        runtime = Runtime()
        runtime.build(config_path, root)
        assert runtime.error is None
        return runtime
    return make, root


def _hot_session(runtime, root: Path, symbol: str):
    hot_market.write_reference(root, symbol, exchange_row=exchange_row(symbol),
                               funding_minutes=480, risk_payload=hot_risk_payload(symbol))
    symbols_module.admit_dynamic(symbol)
    session = runtime.session_for(symbol)
    session.start(T0 + 2_000)
    return session


def _lock(runtime, symbol: str):
    from app.crypto.terminal import api as terminal_api
    from app.crypto.terminal.hot_routes import paper_exposure
    original = terminal_api.runtime
    terminal_api.runtime = runtime
    try:
        import app.crypto.terminal.hot_routes as hot_routes
        hot_routes.runtime = runtime
        return hot_lock.scan_permission(symbol, paper_exposure)
    finally:
        terminal_api.runtime = original
        hot_routes.runtime = original


def _order(session, ts, side, qty, intent="OPEN", q=None):
    result = session.command({"command": "ORDER", "ts_ms": ts, "side": side, "qty": qty,
                              "intent": intent, "request_id": f"r{ts}", "reason": "MANUAL"},
                             quote=q)
    assert result["rejection"] is None, result["rejection"]


def test_g_h_runtime_restart_keeps_both_positions_and_the_hot_lock(boot) -> None:
    make, root = boot
    first = make()
    btc = first.session
    btc_price = btc.config.instrument.tick_size * 1_000_000
    if not btc.is_started:
        btc.start(T0)
    bq = btc_quote(T0 + 1_000, str(btc_price))
    _order(btc, T0 + 1_000, "LONG", "0.010", q=bq)
    hot = _hot_session(first, root, HOT)
    _order(hot, T0 + 3_000, "LONG", "600.0", q=hot_quote(T0 + 3_000))
    before = {s: account_view(x.engine.account) for s, x in first.sessions.items()}

    second = make()                                        # G: HOT holds a position
    assert HOT in second.sessions and second.held_symbols() == [HOT]
    assert second.sessions[HOT].recovery.source == SOURCE_WALLET_REPLAY
    after = {s: account_view(x.engine.account) for s, x in second.sessions.items()}
    assert after == before
    assert _lock(second, HOT).code == hot_lock.HOT_POSITION_OPEN
    assert _lock(second, "BTCUSDT").code == hot_lock.NO_ACTIVE_HOT_SYMBOL

    hot2 = second.sessions[HOT]                            # H: HOT closes, restart, scan allowed
    _order(hot2, T0 + 5_000, "LONG", "600.0", intent="CLOSE", q=hot_quote(T0 + 5_000, "0.12100"))
    asyncio_run(second.retire(HOT))
    third = make()
    assert HOT in third.retired and HOT not in third.sessions
    assert _lock(third, HOT).code == hot_lock.HOT_SYMBOL_FLAT
    assert third.cash.wallet_balance == second.cash.wallet_balance
    assert third.session.engine.account.position.signed_qty == Decimal("0.010")


def test_retiring_a_traded_hot_run_checkpoints_it(boot) -> None:
    make, root = boot
    runtime = make()
    hot = _hot_session(runtime, root, HOT)
    _order(hot, T0 + 3_000, "LONG", "600.0", q=hot_quote(T0 + 3_000))
    _order(hot, T0 + 4_000, "LONG", "600.0", intent="CLOSE", q=hot_quote(T0 + 4_000))
    asyncio_run(runtime.retire(HOT))
    assert list((root / f"wallet-test-{HOT}" / "checkpoints").glob("*.checkpoint.json"))


def asyncio_run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)
