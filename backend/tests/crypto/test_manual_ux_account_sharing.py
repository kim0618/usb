"""Manual UX Final V1: one account per mode, shared by every symbol (BTC and the HOT symbol).

account key = mode, position key = mode + symbol. PAPER is one purse (`SharedCash`) behind every
symbol's engine; LIVE is one Binance futures account behind every symbol's adapter. A HOT switch
changes the symbol on screen and nothing about either account. The two modes never share money.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.symbols import admit_dynamic
from tests.crypto import test_crypto_multisymbol as ms
from tests.crypto import test_hot_symbol_v1 as hot
from tests.crypto.test_hot_symbol_v1 import clean_dynamic, open_hot, order, paper  # noqa: F401

D = Decimal
ARC = "ARCUSDT"


@pytest.fixture
def paper10k(monkeypatch, request):
    """`test_hot_symbol_v1.paper` on a 10,000 USDT PAPER account (10,000,000 KRW at 1,000)."""
    original = hot.make_config
    monkeypatch.setattr(hot, "make_config",
                        lambda **kw: original(capital_krw="10000000", fx="1000", **kw))
    return request.getfixturevalue("paper")


def state(client, symbol: str) -> dict:
    response = client.get(f"/api/crypto/state?symbol={symbol}")
    assert response.status_code == 200, response.text
    return response.json()


def sizing(client, symbol: str) -> dict:
    response = client.get(f"/api/crypto/sizing?symbol={symbol}")
    assert response.status_code == 200, response.text
    return response.json()


def to_arc(client, runtime, use_market) -> None:
    use_market(ARC)
    scanned = client.post("/api/crypto/hot/scan")
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["hot"]["symbol"] == ARC
    open_hot(client, runtime, ARC)
    # The 1 Hz loop that makes a fresh session's first observation is not running in tests.
    runtime.sessions[ARC].observe(runtime.feeds[ARC].quote(), force=True)


# ------------------------------------------------------------------ PAPER: one purse

def test_1_a_btc_order_shrinks_the_one_paper_purse_and_arc_sizes_from_what_is_left(paper10k) -> None:
    client, runtime, use_market, _ = paper10k
    assert runtime.base_config.starting_capital_usdt == D("10000")
    assert order(client, "BTCUSDT", "LONG", qty="0.200").status_code == 200  # 20,000 notional, 10x
    btc_available = D(state(client, "BTCUSDT")["account"]["available_balance"])
    assert btc_available < D("8000")  # 2,000 margin plus the fee left the purse

    to_arc(client, runtime, use_market)
    arc = state(client, ARC)["account"]
    assert D(arc["available_balance"]) == btc_available
    assert arc["cash"]["shared"] is True and set(arc["cash"]["symbols"]) >= {"BTCUSDT", ARC}

    ladder = sizing(client, ARC)
    assert D(ladder["available_balance"]) == btc_available
    long = ladder["sides"]["LONG"]
    max_qty = D(long["max_qty"])
    ask = D("0.12001")
    leverage = D(ladder["leverage"])
    # MAX fits in what BTC left, and is close to it: not the untouched 10,000.
    assert max_qty * ask / leverage <= btc_available
    assert max_qty * ask / leverage > btc_available * D("0.95")
    assert max_qty * ask / leverage < D("10000") * D("0.9")
    half = next(row for row in long["presets"] if row["label"] == "HALF")
    assert D(half["qty"]) == (max_qty * D("0.5") / D("0.1")).to_integral_value(rounding="ROUND_DOWN") * D("0.1")


def test_2_btc_and_arc_positions_sit_in_one_equity_and_one_cash(paper10k) -> None:
    client, runtime, use_market, _ = paper10k
    assert order(client, "BTCUSDT", "LONG", qty="0.100").status_code == 200
    to_arc(client, runtime, use_market)
    assert order(client, ARC, "LONG", qty="1000.0").status_code == 200
    btc, arc = state(client, "BTCUSDT")["account"], state(client, ARC)["account"]
    for key in ("wallet_balance", "available_balance", "used_margin"):
        assert btc["cash"][key] == arc["cash"][key]
    used = {symbol: D(value) for symbol, value in btc["cash"]["used_margin_by_symbol"].items()}
    assert used["BTCUSDT"] > 0 and used[ARC] > 0
    assert D(btc["cash"]["used_margin"]) == used["BTCUSDT"] + used[ARC]
    assert D(btc["available_balance"]) == D(arc["available_balance"])
    assert runtime.sessions["BTCUSDT"].engine.account.cash is runtime.sessions[ARC].engine.account.cash
    assert runtime.cash.members.keys() >= {"BTCUSDT", ARC}


def test_4_btc_arc_btc_round_trip_resets_nothing(paper10k) -> None:
    client, runtime, use_market, _ = paper10k
    assert order(client, "BTCUSDT", "LONG", qty="0.050").status_code == 200
    before = state(client, "BTCUSDT")["account"]
    to_arc(client, runtime, use_market)
    state(client, ARC)
    after = state(client, "BTCUSDT")["account"]
    for key in ("capital_base_usdt", "wallet_balance", "realized_pnl", "position_qty", "avg_entry",
                "reset_count"):
        assert after[key] == before[key], key
    assert after["reset_count"] == 0


def test_5_a_hot_scan_changes_the_symbol_not_the_account(paper10k) -> None:
    client, runtime, use_market, _ = paper10k
    assert order(client, "BTCUSDT", "SHORT", qty="0.050").status_code == 200
    purse = runtime.cash
    btc_account = runtime.sessions["BTCUSDT"].engine.account
    before = state(client, "BTCUSDT")["account"]
    to_arc(client, runtime, use_market)
    assert runtime.cash is purse
    assert runtime.sessions["BTCUSDT"].engine.account is btc_account
    after = state(client, "BTCUSDT")["account"]
    assert after["position_signed_qty"] == before["position_signed_qty"]
    assert after["capital_base_usdt"] == before["capital_base_usdt"]


def test_paper_account_count_is_one(paper10k) -> None:
    client, runtime, use_market, _ = paper10k
    order(client, "BTCUSDT", "LONG", qty="0.010")
    to_arc(client, runtime, use_market)
    order(client, ARC, "LONG", qty="100.0")
    purses = {id(session.engine.account.cash) for session in runtime.sessions.values()}
    assert purses == {id(runtime.cash)}


# ------------------------------------------------------------------ LIVE: one Binance account

def _live_with_arc(monkeypatch, fake: ms.MultiSymbolBinance):
    monkeypatch.setitem(ms.SPECS, ARC, {"base": "ARC", "tick": "0.00001", "step": "1",
                                        "min_qty": "1", "market_max": "5000000", "notional": "5",
                                        "price": "0.12000", "qty_precision": 0, "price_precision": 5})
    admit_dynamic(ARC)
    client, config, _ = ms.make(fake)
    return client, config


def test_3_live_arc_sizes_from_the_same_binance_available_balance_as_btc(monkeypatch) -> None:
    fake = ms.MultiSymbolBinance(available="100.00000000",
                                 positions={"BTCUSDT": ms.held("BTCUSDT", "LONG", "0.058")})
    client, config = _live_with_arc(monkeypatch, fake)
    btc = ms.adapter_for("BTCUSDT", client, config)
    arc = ms.adapter_for(ARC, client, config)
    btc_ladder, arc_ladder = btc.get_sizing(), arc.get_sizing()
    assert arc_ladder["available"], arc_ladder
    assert arc_ladder["sides"]["LONG"]["available_balance"] == D("100.00000000")
    assert btc_ladder["sides"]["LONG"]["available_balance"] == arc_ladder["sides"]["LONG"]["available_balance"]
    # One client, one key, one account: the two adapters are views of it.
    assert btc.client is arc.client and btc.config.fingerprint == arc.config.fingerprint
    assert arc_ladder["sides"]["LONG"]["max_qty"] > 0


def test_live_arc_ladder_shrinks_when_btc_uses_the_account(monkeypatch) -> None:
    wide_client, wide_config = _live_with_arc(monkeypatch, ms.MultiSymbolBinance(available="10000.00000000"))
    narrow_client, narrow_config = _live_with_arc(monkeypatch, ms.MultiSymbolBinance(
        available="100.00000000", positions={"BTCUSDT": ms.held("BTCUSDT", "LONG", "0.058")}))
    wide = ms.adapter_for(ARC, wide_client, wide_config).get_sizing()
    narrow = ms.adapter_for(ARC, narrow_client, narrow_config).get_sizing()
    assert wide["sides"]["LONG"]["max_qty"] > narrow["sides"]["LONG"]["max_qty"] > 0


# ------------------------------------------------------------------ PAPER and LIVE never share

def test_6_paper_and_live_are_separate_accounts(paper10k, monkeypatch) -> None:
    client, runtime, use_market, _ = paper10k
    to_arc(client, runtime, use_market)
    live_client, live_config = _live_with_arc(monkeypatch, ms.MultiSymbolBinance(available="500.00000000"))
    live_ladder = ms.adapter_for(ARC, live_client, live_config).get_sizing()
    paper_ladder = sizing(client, ARC)
    assert live_ladder["sides"]["LONG"]["available_balance"] == D("500.00000000")
    assert D(paper_ladder["available_balance"]) == D("10000")
    # A PAPER fill moves the PAPER purse and leaves the Binance figure where it was.
    assert order(client, ARC, "LONG", qty="1000.0").status_code == 200
    assert D(sizing(client, ARC)["available_balance"]) < D("10000")
    assert ms.adapter_for(ARC, live_client, live_config).get_sizing()["sides"]["LONG"]["available_balance"] \
        == D("500.00000000")
