"""PAPER sizing depth: twenty levels for sizing and for the fill a command walks, five on the tape.

The production case: WALUSDT on the HOT slot, where the account could carry about 316k WAL and
Safe MAX said about 99k, because the feed handed the engine five levels a side and five levels
of a thin perpetual is not much. The safety contract (entry AND an immediate full exit on the
same snapshot) is unchanged; it is now judged on twenty levels.

The snapshot in `fixtures/paper_sizing_depth/` is real: Binance depth20 for WALUSDT, Bybit's
50-level BTCUSDT book, and the PAPER purse as production reported it, each read once.
"""
from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.paper import sizing, trade_preview
from app.crypto.paper.book import SIZING_BOOK_DEPTH, BookSide, NoLiquidity, Quote
from app.crypto.paper.engine import (
    TAPE_BOOK_DEPTH, PaperEngine, apply_tape_record, quote_from_payload, quote_to_payload,
)
from app.crypto.paper.instrument import InstrumentSpec, RiskTierTable
from app.crypto.paper.ledger import InputKind
from app.crypto.terminal.session import PaperSession
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD, make_config
from tests.crypto.test_hot_symbol_v1 import (  # noqa: F401 - fixtures
    NOW_MS, FakeFeed, clean_dynamic, open_hot, order, paper,
)
from tests.crypto.test_manual_ux_account_sharing import paper10k, state  # noqa: F401
from tests.crypto.test_manual_ux_account_sharing import sizing as sizing_route

D = Decimal
SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "paper_sizing_depth"
                       / "snapshot_20261016.json").read_text())
WAL = SNAPSHOT["wal"]
BTC = SNAPSHOT["btc"]
ACCOUNT = SNAPSHOT["paper_account"]
FX = ACCOUNT["krw_per_usdt"]
TAKER = "0.00055"


def tiers() -> RiskTierTable:
    return RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="test", source_sha256="x")


def wal_spec() -> InstrumentSpec:
    f = WAL["filters"]
    return InstrumentSpec(symbol="WALUSDT", contract_type="BinancePerpetual", tick_size=D(f["tick"]),
                          qty_step=D(f["step"]), min_order_qty=D(f["min_qty"]),
                          max_order_qty=D(f["lot_max"]), max_mkt_order_qty=D(f["market_max"]),
                          min_notional_value=D(f["min_notional"]), max_leverage=D("25"),
                          source="test fixture: Binance exchangeInfo WALUSDT 2026-10-16")


def snapshot_quote(side: dict, depth: int, *, ts_ms: int | None = None) -> Quote:
    return Quote(ts_ms=ts_ms if ts_ms is not None else int(side["ts_ms"]),
                 bids=BookSide.from_rows(side["bids"][:depth], descending=True),
                 asks=BookSide.from_rows(side["asks"][:depth], descending=False),
                 mark_price=D(side["mark_price"]), last_price=D(side["mark_price"]),
                 index_price=D(side["index_price"]))


def engine_on(side: dict, depth: int, *, leverage: str, available_usdt: str,
              spec: InstrumentSpec | None = None) -> PaperEngine:
    capital_krw = str((D(available_usdt) * D(FX)).quantize(D("1")))
    config = make_config(leverage=leverage, capital_krw=capital_krw, fx=FX, taker=TAKER)
    if spec is not None:
        config = dataclasses.replace(config, instrument=spec)
    engine = PaperEngine(config, tiers())
    engine.start(int(side["ts_ms"]) - 1)
    engine.apply_market(snapshot_quote(side, depth))
    return engine


def depth(side: dict, book: str, levels: int) -> Decimal:
    return sum((D(q) for _, q in side[book][:levels]), D(0))


# ------------------------------------------------------------------ 1-4 WAL: depth binds, safely

def test_1_wal_on_five_levels_is_liquidity_limited() -> None:
    engine = engine_on(WAL, 5, leverage="10", available_usdt=ACCOUNT["available_balance"],
                       spec=wal_spec())
    ceiling = sizing.max_entry(engine, "LONG")
    account_capacity = D(ACCOUNT["available_balance"]) * 10 / D(WAL["asks"][0][0])
    assert ceiling.feasible
    # LONG exits into the bid: five levels of it bind, far below what the purse could carry.
    assert ceiling.qty <= depth(WAL, "bids", 5)
    assert ceiling.qty < account_capacity / 5
    beyond = sizing.probe(engine, "LONG", ceiling.qty + 1)
    assert beyond.feasible is False and beyond.reject_code == "NO_LIQUIDITY"


def test_2_wal_on_twenty_levels_has_a_much_larger_safe_max() -> None:
    five = sizing.max_entry(engine_on(WAL, 5, leverage="10", spec=wal_spec(),
                                      available_usdt=ACCOUNT["available_balance"]), "LONG")
    twenty_engine = engine_on(WAL, SIZING_BOOK_DEPTH, leverage="10", spec=wal_spec(),
                              available_usdt=ACCOUNT["available_balance"])
    twenty = sizing.max_entry(twenty_engine, "LONG")
    assert twenty.feasible
    assert twenty.qty > five.qty * 5
    # Still bounded by the visible twenty levels: the entry walks the ask, the exit the bid.
    assert twenty.qty <= min(depth(WAL, "asks", 20), depth(WAL, "bids", 20))


def test_3_the_immediate_exit_is_still_required_on_twenty_levels() -> None:
    """SHORT enters on the deeper bid and exits into the thinner ask: the exit binds."""
    engine = engine_on(WAL, SIZING_BOOK_DEPTH, leverage="25", available_usdt="1000000",
                       spec=wal_spec())
    ask20, bid20 = depth(WAL, "asks", 20), depth(WAL, "bids", 20)
    assert bid20 > ask20
    ceiling = sizing.max_entry(engine, "SHORT")
    assert ceiling.qty == ask20                       # the whole ask, not the deeper bid
    over = sizing.probe(engine, "SHORT", ask20 + 1)
    assert over.entry_feasible is True                # it could be entered on the bid
    assert over.exit_feasible is False                # and could not be closed: refused
    assert over.exit_reject_code == "NO_LIQUIDITY"


def test_4_an_order_deeper_than_twenty_levels_is_refused() -> None:
    engine = engine_on(WAL, SIZING_BOOK_DEPTH, leverage="25", available_usdt="1000000",
                       spec=wal_spec())
    too_deep = depth(WAL, "asks", 20) + 1
    probe = sizing.probe(engine, "LONG", too_deep, require_exit=False)
    assert probe.feasible is False and probe.reject_code == "NO_LIQUIDITY"
    # The engine itself, not only the sizing probe, refuses it.
    with pytest.raises(NoLiquidity):
        engine.submit_order(ts_ms=engine.quote.ts_ms, side="LONG", qty=too_deep, intent="OPEN",
                            request_id="too-deep", reason="TEST")
    assert engine.ledger.events[-1]["code"] == "NO_LIQUIDITY"
    assert engine.account.position.is_flat


# ------------------------------------------------------------------ 5-6 BTC and the presets

def test_5_btc_account_limited_max_is_the_same_on_five_and_twenty_levels() -> None:
    """At 10x and a 1,000 USDT purse BTC is margin-bound, so depth must not move MAX."""
    five = sizing.max_entry(engine_on(BTC, 5, leverage="10", available_usdt="1000"), "SHORT")
    twenty = sizing.max_entry(engine_on(BTC, 20, leverage="10", available_usdt="1000"), "SHORT")
    assert five.feasible and twenty.feasible
    assert five.qty == twenty.qty
    assert five.qty < depth(BTC, "bids", 5) and five.qty < depth(BTC, "asks", 5)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_6_presets_are_exact_fractions_of_safe_max_floored_to_the_step(side: str) -> None:
    engine = engine_on(WAL, SIZING_BOOK_DEPTH, leverage="10", spec=wal_spec(),
                       available_usdt=ACCOUNT["available_balance"])
    payload = sizing.presets(engine, side)
    max_qty = payload["max_qty"]
    rows = {row["label"]: row for row in payload["presets"]}
    for label, fraction in (("25%", D("0.25")), ("HALF", D("0.5")), ("75%", D("0.75"))):
        assert rows[label]["qty"] == sizing.floor_to_step(max_qty * fraction, D("1"))
        assert rows[label]["feasible"] is True
    assert rows["MAX"]["qty"] == max_qty
    assert payload["entry_depth"] == depth(WAL, "asks" if side == "LONG" else "bids", 20)
    assert payload["exit_depth"] == depth(WAL, "bids" if side == "LONG" else "asks", 20)


# ------------------------------------------------------------------ 9-11 tape and replay

def test_9_the_1hz_record_keeps_five_levels_and_a_command_record_twenty(tmp_path) -> None:
    session = PaperSession(config=make_config(leverage="10"), tiers=tiers(), root=tmp_path)
    session.start(int(BTC["ts_ms"]) - 1)
    session.observe(snapshot_quote(BTC, 20))
    session.observe(snapshot_quote(BTC, 20, ts_ms=int(BTC["ts_ms"]) + 1_000))
    session.observe(snapshot_quote(BTC, 20, ts_ms=int(BTC["ts_ms"]) + 1_100), force=True)
    records = [json.loads(line) for line in (session.run_dir / "input.jsonl").read_text().splitlines()]
    market = [r["payload"] for r in records if r["kind"] == InputKind.MARKET]
    assert [len(p["bids"]) for p in market] == [TAPE_BOOK_DEPTH, TAPE_BOOK_DEPTH, SIZING_BOOK_DEPTH]
    assert [len(p["asks"]) for p in market] == [TAPE_BOOK_DEPTH, TAPE_BOOK_DEPTH, SIZING_BOOK_DEPTH]
    assert TAPE_BOOK_DEPTH == 5 and SIZING_BOOK_DEPTH == 20


def test_10_the_1hz_record_is_byte_identical_to_the_previous_format() -> None:
    quote = snapshot_quote(BTC, 20)
    previous = {  # the payload as written before this change: five levels, same keys, same order
        "ts_ms": quote.ts_ms,
        "bids": [[str(p), str(q)] for p, q in quote.bids.levels[:5]],
        "asks": [[str(p), str(q)] for p, q in quote.asks.levels[:5]],
        "mark_price": str(quote.mark_price), "last_price": str(quote.last_price),
        "index_price": str(quote.index_price), "funding_rate": None, "next_funding_time_ms": None,
    }
    assert json.dumps(quote_to_payload(quote)) == json.dumps(previous)


def test_11_an_order_sized_past_five_levels_replays_and_recovers_identically(tmp_path) -> None:
    config = dataclasses.replace(
        make_config(leverage="10", capital_krw=str(D("100000") * D(FX)), fx=FX, taker=TAKER),
        instrument=wal_spec())
    session = PaperSession(config=config, tiers=tiers(), root=tmp_path)
    t0 = int(WAL["ts_ms"])
    session.start(t0 - 1)
    session.observe(snapshot_quote(WAL, 20))                      # 1 Hz: five levels on the tape
    deep = snapshot_quote(WAL, 20, ts_ms=t0 + 400)
    session.observe(deep, force=True)                             # what the order route does
    qty = sizing.max_entry(session.engine, "LONG").qty
    assert qty > depth(WAL, "bids", 5)                            # needs more than five levels
    result = session.command({"command": "ORDER", "ts_ms": deep.ts_ms, "side": "LONG",
                              "qty": str(qty), "intent": "OPEN", "request_id": "deep-open",
                              "reason": "TEST"})
    assert result["rejection"] is None
    session.observe(snapshot_quote(WAL, 20, ts_ms=t0 + 1_500))
    close = snapshot_quote(WAL, 20, ts_ms=t0 + 1_800)
    session.command({"command": "ORDER", "ts_ms": close.ts_ms, "side": "LONG", "qty": str(qty),
                     "intent": "CLOSE", "request_id": "deep-close", "reason": "TEST"}, quote=close)
    assert session.engine.account.position.is_flat
    live_events = [json.dumps(e, sort_keys=True, default=str) for e in session.engine.ledger.events]

    replayed = PaperEngine(config, tiers())
    for line in (session.run_dir / "input.jsonl").read_text().splitlines():
        apply_tape_record(replayed, json.loads(line))
    assert [json.dumps(e, sort_keys=True, default=str) for e in replayed.ledger.events] == live_events
    assert replayed.account.realized_pnl == session.engine.account.realized_pnl
    assert replayed.account.cumulative_fees == session.engine.account.cumulative_fees

    recovered = PaperSession(config=config, tiers=tiers(), root=tmp_path)
    assert recovered.engine.account.realized_pnl == session.engine.account.realized_pnl
    assert recovered.engine.account.cumulative_fees == session.engine.account.cumulative_fees
    assert len(recovered.engine.ledger.events) == len(session.engine.ledger.events)


def test_the_preview_on_the_recorded_tick_prices_on_the_feeds_twenty_levels() -> None:
    """The 1 Hz record kept five levels. The sizing route's clone, given the feed's quote of the
    same tick, must price on twenty: that is the book a command on this tick will fill against."""
    engine = engine_on(WAL, SIZING_BOOK_DEPTH, leverage="10", spec=wal_spec(),
                       available_usdt=ACCOUNT["available_balance"])
    engine.apply_market(quote_from_payload(quote_to_payload(snapshot_quote(WAL, 20, ts_ms=int(WAL["ts_ms"]) + 1))))
    assert len(engine.quote.bids.levels) == 5
    feed_quote = snapshot_quote(WAL, 20, ts_ms=engine.quote.ts_ms)
    clone = trade_preview.advanced(engine, feed_quote)
    assert len(clone.quote.bids.levels) == 20
    assert sizing.max_entry(clone, "LONG").qty > depth(WAL, "bids", 5)
    assert len(engine.quote.bids.levels) == 5                    # the real engine is untouched


# ------------------------------------------------------------------ 12 the feeds: no new I/O

def test_12_both_feeds_hand_out_twenty_levels_from_what_they_already_hold(monkeypatch) -> None:
    import httpx
    import websockets

    from app.crypto.hot_coins.market import BinancePublicFeed
    from app.crypto.terminal.feed import BybitPublicFeed

    def forbidden(*_a, **_k):
        raise AssertionError("quote() must not touch the network")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(websockets, "connect", forbidden)

    bybit = BybitPublicFeed()
    bybit.book.apply("snapshot", {"s": "BTCUSDT", "b": BTC["bids"], "a": BTC["asks"], "u": 1, "seq": 1})
    bybit.ticker = {"markPrice": BTC["mark_price"], "lastPrice": BTC["mark_price"]}
    bybit.last_ticker_ms = int(BTC["ts_ms"])
    assert len(BTC["bids"]) == 50
    quote = bybit.quote()
    assert len(quote.bids.levels) == 20 and len(quote.asks.levels) == 20

    binance = BinancePublicFeed(symbol="WALUSDT")
    binance.handle({"data": {"e": "depthUpdate", "s": "WALUSDT", "b": WAL["bids"], "a": WAL["asks"]}}, 0)
    binance.handle({"data": {"e": "markPriceUpdate", "s": "WALUSDT", "p": WAL["mark_price"],
                             "E": int(WAL["ts_ms"])}}, 0)
    quote = binance.quote()
    assert len(quote.bids.levels) == 20 and len(quote.asks.levels) == 20


# ------------------------------------------------------------------ 7 the shared purse, end to end

class WalFeed(FakeFeed):
    """The real WAL depth20 snapshot, as the Binance feed now hands it to the runtime."""

    def __init__(self) -> None:
        super().__init__("WALUSDT", WAL["bids"][0][0], WAL["asks"][0][0])

    def quote(self) -> Quote:
        return snapshot_quote(WAL, SIZING_BOOK_DEPTH, ts_ms=NOW_MS)


def test_7_a_btc_position_shrinks_wal_and_wal_sizes_past_five_levels_through_the_routes(paper10k) -> None:
    client, runtime, use_market, _ = paper10k
    assert order(client, "BTCUSDT", "LONG", qty="0.200").status_code == 200
    btc_available = D(state(client, "BTCUSDT")["account"]["available_balance"])
    assert btc_available < D("8000")

    runtime.feeds["WALUSDT"] = WalFeed()
    use_market("WALUSDT")
    assert client.post("/api/crypto/hot/scan").json()["hot"]["symbol"] == "WALUSDT"
    open_hot(client, runtime, "WALUSDT")
    runtime.sessions["WALUSDT"].observe(runtime.feeds["WALUSDT"].quote())   # a 1 Hz record
    assert runtime.cash is not None and runtime.sessions["WALUSDT"].engine.account.cash is runtime.cash
    # 3x puts the purse between five and twenty levels of WAL, below the fixture's market max.
    assert client.post("/api/crypto/leverage",
                       json={"leverage": "3", "symbol": "WALUSDT"}).status_code == 200

    ladder = sizing_route(client, "WALUSDT")
    assert D(ladder["available_balance"]) == btc_available          # the same purse, what BTC left
    long = ladder["sides"]["LONG"]
    max_qty = D(long["max_qty"])
    ask = D(WAL["asks"][0][0])
    assert max_qty > depth(WAL, "bids", 5)                          # past the old five-level cap
    assert max_qty * ask / 3 <= btc_available                       # and inside the purse
    assert max_qty * ask / 3 > btc_available * D("0.95")            # which is now what binds
    assert D(long["exit_depth"]) == depth(WAL, "bids", 20)

    response = order(client, "WALUSDT", "LONG", qty=str(max_qty))
    assert response.status_code == 200, response.text
    assert D(state(client, "WALUSDT")["account"]["position_qty"]) == max_qty
    records = [json.loads(line) for line in
               (runtime.sessions["WALUSDT"].run_dir / "input.jsonl").read_text().splitlines()]
    market = [r["payload"] for r in records if r["kind"] == InputKind.MARKET]
    assert len(market[0]["bids"]) == TAPE_BOOK_DEPTH                # the 1 Hz record: five
    assert len(market[-1]["bids"]) == SIZING_BOOK_DEPTH             # the one the order filled on
