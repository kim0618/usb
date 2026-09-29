"""Binance LIVE manual adapter: credentials, REST, filters, account, orders, preview, mirror.

No test here reaches the network. Every response comes from `binance_fixtures.FakeBinance`, and
the order tests prove the refusal as carefully as they prove the request, because in V1 the
refusal is the behaviour that ships.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.live import account as account_mod
from app.crypto.live import endpoints, mirror as mirror_mod, models, orders, preview, signing
from app.crypto.live.account import AccountReader
from app.crypto.live.adapter import BinanceLiveAdapter, PaperAdapter
from app.crypto.live.credentials import (CredentialsMissing, Credentials, load_config,
                                         load_credentials, trading_enabled)
from app.crypto.live.filters import QuantityRejected, SymbolFilters
from app.crypto.live.mirror import LiveEvent, LiveMirror, MirrorPathRefused
from app.crypto.live.orders import LiveOrderRouter, OrderRefused
from app.crypto.live.rest import BinanceError, BinanceFuturesClient, TradingDisabled
from app.crypto.live.stream import UserDataStream
from tests.crypto.binance_fixtures import (ACCOUNT, COMMISSION_RATE, DEPTH, EXCHANGE_INFO,
                                           FakeBinance, MARK_PRICE, POSITION_MODE_HEDGE,
                                           POSITION_RISK_FLAT, POSITION_RISK_LONG, SYMBOL,
                                           make_client, make_config)

D = Decimal
KEY = "test-api-key-0123456789"
SECRET = "test-api-secret-abcdef"


# ------------------------------------------------------------------ credentials and flags

def test_the_secret_never_appears_in_a_repr_a_str_or_a_redacted_message() -> None:
    credentials = Credentials(api_key=KEY, api_secret=SECRET)
    assert SECRET not in repr(credentials) and SECRET not in str(credentials)
    assert KEY not in repr(credentials)
    leaked = f"request failed for key {KEY} with secret {SECRET}"
    cleaned = credentials.redact(leaked)
    assert SECRET not in cleaned and KEY not in cleaned


def test_the_fingerprint_identifies_a_key_without_revealing_any_of_it() -> None:
    credentials = Credentials(api_key=KEY, api_secret=SECRET)
    assert credentials.fingerprint == hashlib.sha256(KEY.encode()).hexdigest()[:8]
    assert credentials.fingerprint not in KEY


def test_trading_is_off_by_default_and_a_typo_in_the_flag_does_not_arm_it() -> None:
    assert trading_enabled({}) is False
    assert trading_enabled({"BINANCE_LIVE_TRADING_ENABLED": "ture"}) is False
    assert trading_enabled({"BINANCE_LIVE_TRADING_ENABLED": "TRUE"}) is True


def test_a_config_without_a_key_still_loads_so_the_status_route_can_explain_itself() -> None:
    config = load_config({})
    assert config.credentials_present is False and config.trading_enabled is False
    assert config.fingerprint is None
    # The view may name a fingerprint field; it must never carry a key or a secret value.
    assert KEY not in json.dumps(config.view()) and SECRET not in json.dumps(config.view())
    with pytest.raises(CredentialsMissing):
        load_credentials({})


def test_v1_refuses_a_symbol_other_than_btcusdt_rather_than_attempting_it() -> None:
    with pytest.raises(ValueError):
        load_config({"BINANCE_LIVE_SYMBOL": "ETHUSDT"})


def test_recv_window_is_bounded_by_binances_own_ceiling() -> None:
    assert load_config({"BINANCE_RECV_WINDOW_MS": "60000"}).recv_window_ms == 60_000
    with pytest.raises(ValueError):
        load_config({"BINANCE_RECV_WINDOW_MS": "60001"})
    with pytest.raises(ValueError):
        load_config({"BINANCE_RECV_WINDOW_MS": "0"})


# ------------------------------------------------------------------ endpoint registry

def test_the_registry_holds_the_paths_that_were_probed_against_the_live_api() -> None:
    # `/fapi/v1/positionRisk` is gone and `/fapi/v1/bookTicker` never existed; both were wrong in
    # the first draft of this registry and both would have 404ed on the first real call.
    assert endpoints.resolve("position_risk").path == "/fapi/v3/positionRisk"
    assert endpoints.resolve("book_ticker").path == "/fapi/v1/ticker/bookTicker"
    assert endpoints.resolve("account").path == "/fapi/v3/account"
    assert endpoints.resolve("symbol_config").path == "/fapi/v1/symbolConfig"


@pytest.mark.parametrize("method,path", [
    ("POST", "/sapi/v1/capital/withdraw/apply"),
    ("POST", "/sapi/v1/futures/transfer"),
    ("POST", "/api/v3/order"),
    ("POST", "/sapi/v1/margin/order"),
    ("POST", "/fapi/v1/marginType"),
    ("POST", "/fapi/v1/positionSide/dual"),
    ("GET", "/sapi/v1/sub-account/list"),
])
def test_every_forbidden_family_is_refused_by_the_guard(method: str, path: str) -> None:
    with pytest.raises(endpoints.EndpointForbidden):
        endpoints.guard(method, path)


def test_an_endpoint_name_that_is_not_in_the_registry_cannot_be_called() -> None:
    with pytest.raises(endpoints.EndpointForbidden):
        endpoints.resolve("withdraw_everything")


def test_no_registry_row_points_outside_the_usds_m_futures_prefix() -> None:
    for endpoint in endpoints.ENDPOINTS.values():
        assert endpoint.path.startswith("/fapi/")
        assert endpoint.doc.startswith("https://developers.binance.com/")


# ------------------------------------------------------------------ signing

def test_the_signature_covers_the_exact_string_that_is_sent() -> None:
    params = {"symbol": SYMBOL, "side": "BUY", "timestamp": 1_790_639_000_000}
    query = signing.signed_query(params, secret=SECRET)
    payload, _, signature = query.rpartition("&signature=")
    assert payload == "symbol=BTCUSDT&side=BUY&timestamp=1790639000000"
    assert signature == hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def test_a_caller_may_not_supply_its_own_signature() -> None:
    with pytest.raises(ValueError):
        signing.signed_query({"signature": "forged"}, secret=SECRET)


def test_none_values_are_dropped_and_booleans_are_lowercased() -> None:
    assert signing.canonical({"a": None, "b": True, "c": False}) == "b=true&c=false"


# ------------------------------------------------------------------ rest client

def test_a_signed_request_carries_the_key_header_the_timestamp_and_the_recv_window() -> None:
    client, fake = make_client()
    client.call("account")
    _, path, params = fake.calls[-1]
    assert path == "/fapi/v3/account"
    assert params["recvWindow"] == "5000" and "signature" in params and params["timestamp"]


def test_a_public_request_is_not_signed_at_all() -> None:
    client, fake = make_client()
    client.call("exchange_info", {"symbol": SYMBOL})
    _, _, params = fake.calls[-1]
    assert "signature" not in params and "timestamp" not in params


def test_a_trade_endpoint_is_refused_before_a_request_is_built_when_the_client_is_not_armed() -> None:
    client, fake = make_client(trading_enabled=False)
    with pytest.raises(TradingDisabled):
        client.call("new_order", {"symbol": SYMBOL, "side": "BUY", "type": "MARKET",
                                  "quantity": "0.001"})
    assert fake.calls == []  # nothing left the process


def test_the_clock_offset_is_measured_and_applied_to_signed_requests() -> None:
    client, fake = make_client()
    offset = client.sync_clock()
    assert client.telemetry.clock_offset_ms == offset
    client.telemetry.clock_offset_ms = 5_000
    client.call("account")
    _, _, params = fake.calls[-1]
    import time as _time
    assert int(params["timestamp"]) > int(_time.time() * 1000) + 4_000


def test_a_read_refused_for_clock_skew_remeasures_the_offset_and_asks_once_more() -> None:
    """Binance refuses a timestamp in its own future whatever `recvWindow` says, so a machine
    whose clock runs fast gets -1021 on a read that is otherwise fine. One re-measure and one
    repeat is the difference between a recoverable clock and a dead LIVE panel."""
    client, fake = make_client()
    fake.fail_once("GET", "/fapi/v3/account", 400, -1021,
                   "Timestamp for this request was 1000ms ahead of the server's time.")
    payload = client.call("account")
    assert payload["assets"][0]["asset"] == "USDT"
    assert client.telemetry.clock_resyncs == 1
    assert fake.count("/fapi/v1/time") == 1  # the offset was re-measured, not guessed
    assert fake.count("/fapi/v3/account") == 2


def test_a_read_refused_twice_for_clock_skew_gives_up_rather_than_looping() -> None:
    client, fake = make_client()
    fake.fail("GET", "/fapi/v3/account", 400, -1021, "Timestamp for this request was ahead.")
    with pytest.raises(BinanceError) as caught:
        client.call("account")
    assert caught.value.is_clock_skew
    assert fake.count("/fapi/v3/account") == 2  # one attempt, one retry, and no third


def test_a_trade_refused_for_clock_skew_is_never_repeated() -> None:
    """The retry is deliberately excluded here: the first order may have reached the matching
    engine before the clock was checked, and a second request would be a second position."""
    client, fake = make_client(trading_enabled=True)
    fake.fail_once("POST", "/fapi/v1/order", 400, -1021, "Timestamp for this request was ahead.")
    with pytest.raises(BinanceError) as caught:
        client.call("new_order", {"symbol": SYMBOL, "side": "BUY", "type": "MARKET",
                                  "quantity": "0.001"})
    assert caught.value.is_clock_skew
    assert fake.count("/fapi/v1/order") == 1
    assert client.telemetry.clock_resyncs == 0


def test_a_binance_error_is_surfaced_with_its_code_and_without_the_credential() -> None:
    client, fake = make_client()
    fake.fail("GET", "/fapi/v3/account", 401, -2015, f"Invalid API-key for {KEY}")
    with pytest.raises(BinanceError) as caught:
        client.call("account")
    assert caught.value.code == -2015 and caught.value.is_auth
    assert KEY not in str(caught.value)
    assert KEY not in (client.telemetry.last_error or "")


def test_a_transport_failure_never_carries_the_signed_url() -> None:
    import httpx

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = BinanceFuturesClient(credentials=Credentials(api_key=KEY, api_secret=SECRET),
                                  base_url="https://fapi.binance.com", recv_window_ms=5_000,
                                  transport=httpx.MockTransport(boom))
    with pytest.raises(BinanceError) as caught:
        client.call("account")
    assert "signature" not in str(caught.value) and KEY not in str(caught.value)


def test_telemetry_records_which_endpoints_were_touched() -> None:
    client, _ = make_client()
    client.call("exchange_info", {"symbol": SYMBOL})
    client.call("account")
    view = client.telemetry.view()
    assert view["endpoints_called"] == ["exchange_info", "account"]
    assert view["trade_requests"] == 0 and view["signed_requests"] == 1
    assert view["used_weight_1m"] == 42


# ------------------------------------------------------------------ filters

@pytest.fixture
def filters() -> SymbolFilters:
    return SymbolFilters.from_exchange_info(EXCHANGE_INFO, SYMBOL, fetched_at_ms=1_000)


def test_filters_come_from_exchange_info_and_record_when_they_were_read(filters: SymbolFilters) -> None:
    assert filters.qty_step == D("0.001") and filters.min_notional == D("100")
    assert filters.market_max_qty == D("120") and filters.quantity_precision == 3
    assert filters.tradable and filters.fetched_at_ms == 1_000
    assert filters.view()["source"] == "binance GET /fapi/v1/exchangeInfo"


def test_a_missing_filter_is_refused_rather_than_defaulted() -> None:
    payload = {"symbols": [{"symbol": SYMBOL, "status": "TRADING", "pricePrecision": 2,
                            "quantityPrecision": 3, "filters": []}]}
    with pytest.raises(ValueError):
        SymbolFilters.from_exchange_info(payload, SYMBOL)


def test_quantities_are_floored_to_the_step_never_rounded_up(filters: SymbolFilters) -> None:
    assert filters.quantize(D("0.0019")) == D("0.001")
    assert filters.qty_from_notional(D("1000"), reference_price=D("83500")) == D("0.011")


@pytest.mark.parametrize("qty,code", [
    (D("0.0005"), "QTY_BELOW_MINIMUM"),
    (D("0.0015"), "QTY_OFF_GRID"),
    (D("200"), "QTY_ABOVE_MARKET_MAXIMUM"),
])
def test_a_size_binance_would_refuse_is_refused_here_with_the_engines_own_code(
        filters: SymbolFilters, qty: Decimal, code: str) -> None:
    with pytest.raises(QuantityRejected) as caught:
        filters.validate_market_qty(qty, reference_price=D("83500"))
    assert caught.value.code == code


def test_a_size_below_the_minimum_notional_is_refused(filters: SymbolFilters) -> None:
    with pytest.raises(QuantityRejected) as caught:
        filters.validate_market_qty(D("0.001"), reference_price=D("50"))
    assert caught.value.code == "NOTIONAL_BELOW_MINIMUM"


# ------------------------------------------------------------------ models

def test_the_balance_comes_from_the_usdt_asset_row_not_the_usd_valuation() -> None:
    """Found against the real account: the top-level totals are the account valued in USD and
    they drift with the peg while the wallet is idle. The USDT row is the balance."""
    balance = models.AccountBalance.from_account(ACCOUNT)
    assert balance.wallet_balance == D("1000.00000000")      # assets[USDT].walletBalance
    assert balance.available_balance == D("875.00000000")
    assert balance.unrealized_pnl == D("12.50000000")
    assert balance.account_wallet_usd == D("999.40000000")   # top level, kept but labelled
    assert balance.account_available_usd == D("874.47500000")
    assert balance.usd_valuation_ratio == D("999.40000000") / D("1000.00000000")
    assert "assets[USDT]" in balance.view()["balance_source"]


def test_an_account_without_the_asset_row_refuses_rather_than_falling_back_to_usd() -> None:
    payload = {key: value for key, value in ACCOUNT.items() if key != "assets"}
    with pytest.raises(models.LiveFieldMissing):
        models.AccountBalance.from_account(payload)
    payload = {**ACCOUNT, "assets": [{"asset": "BNB", "walletBalance": "1"}]}
    with pytest.raises(models.LiveFieldMissing) as caught:
        models.AccountBalance.from_account(payload)
    assert "USDT" in caught.value.field


def test_a_missing_account_field_raises_rather_than_showing_a_zero() -> None:
    payload = {**ACCOUNT, "assets": [{key: value for key, value in ACCOUNT["assets"][0].items()
                                      if key != "availableBalance"}]}
    with pytest.raises(models.LiveFieldMissing) as caught:
        models.AccountBalance.from_account(payload)
    assert caught.value.field == "availableBalance"


def test_a_long_position_is_parsed_with_its_liquidation_price_and_entry() -> None:
    position = models.LivePosition.from_rows(POSITION_RISK_LONG, SYMBOL)
    assert position.side == "LONG" and position.qty == D("0.015")
    assert position.entry_price == D("82666.66666667")
    assert position.liquidation_price == D("75100.10")
    assert position.is_flat is False


def test_a_flat_row_reports_no_side_and_no_liquidation_price() -> None:
    position = models.LivePosition.from_rows(POSITION_RISK_FLAT, SYMBOL)
    assert position.is_flat and position.side is None
    assert position.liquidation_price is None and position.entry_price is None


def test_an_account_that_never_traded_the_symbol_is_flat_not_an_error() -> None:
    assert models.LivePosition.from_rows([], SYMBOL).is_flat


def test_leverage_and_margin_mode_come_from_symbol_config_because_v3_dropped_them() -> None:
    assert "leverage" not in POSITION_RISK_LONG[0]
    config = models.SymbolConfig.from_rows(
        [{"symbol": SYMBOL, "marginType": "CROSSED", "leverage": 10}], SYMBOL)
    assert config.leverage == D("10") and config.margin_type == "CROSSED"


def test_hedge_mode_is_reported_rather_than_normalised() -> None:
    assert models.PositionMode.from_payload(POSITION_MODE_HEDGE).dual_side is True
    assert models.PositionMode.from_payload({"dualSidePosition": False}).one_way is True


def test_a_fill_keeps_binances_commission_and_realised_pnl() -> None:
    from tests.crypto.binance_fixtures import USER_TRADES

    trade = models.UserTrade.from_payload(USER_TRADES[0])
    assert trade.commission == D("0.50100000") and trade.commission_asset == "USDT"
    assert trade.realized_pnl == D("12.10000000")


def test_funding_rows_are_read_from_income_history() -> None:
    from tests.crypto.binance_fixtures import INCOME_FUNDING

    row = models.IncomeRow.from_payload(INCOME_FUNDING[0])
    assert row.income_type == "FUNDING_FEE" and row.income == D("-0.12500000")


# ------------------------------------------------------------------ account reader

def reader(fake: FakeBinance | None = None, **config_kwargs) -> tuple[AccountReader, FakeBinance]:
    client, fake = make_client(fake)
    return AccountReader(client, make_config(**config_kwargs)), fake


def test_a_full_snapshot_carries_balance_position_leverage_margin_mode_and_mark() -> None:
    read, _ = reader()
    snapshot = read.snapshot()
    assert snapshot.ready and not snapshot.blockers
    assert snapshot.balance.wallet_balance == D("1000.00000000")   # the USDT row
    assert snapshot.position.side == "LONG"
    assert snapshot.symbol_config.leverage == D("10")
    assert snapshot.symbol_config.margin_type == "CROSSED"
    assert snapshot.position_mode.one_way
    assert snapshot.mark.mark_price == D("83500.00000000")
    assert snapshot.commission.taker == D("0.000400")
    assert snapshot.view()["source"] == "BINANCE_LIVE"


def test_without_a_key_the_snapshot_says_so_and_never_opens_a_connection() -> None:
    client, fake = make_client()
    read = AccountReader(client, make_config(credentials=False))
    snapshot = read.snapshot()
    assert not snapshot.ready
    assert snapshot.blockers[0].code == account_mod.CREDENTIALS_MISSING
    assert fake.calls == []


def test_hedge_mode_blocks_live_and_names_the_reason() -> None:
    fake = FakeBinance()
    fake.position_mode = POSITION_MODE_HEDGE
    read, _ = reader(fake)
    snapshot = read.snapshot()
    assert not snapshot.ready
    assert snapshot.blockers[0].code == account_mod.HEDGE_MODE_UNSUPPORTED


def test_an_auth_failure_becomes_a_blocker_not_an_exception() -> None:
    fake = FakeBinance()
    fake.fail("GET", "/fapi/v3/account", 401, -2015, "Invalid API-key, IP, or permissions")
    read, _ = reader(fake)
    snapshot = read.snapshot()
    assert snapshot.blockers[0].code == account_mod.BINANCE_AUTH_FAILED


def test_clock_skew_is_named_so_the_operator_knows_to_fix_the_clock() -> None:
    fake = FakeBinance()
    fake.fail("GET", "/fapi/v3/account", 400, -1021,
              "Timestamp for this request is outside of the recvWindow")
    read, _ = reader(fake)
    assert read.snapshot().blockers[0].code == account_mod.CLOCK_SKEW


def test_a_changed_response_shape_is_a_blocker_that_names_the_field() -> None:
    fake = FakeBinance()
    fake.routes[("GET", "/fapi/v3/account")] = {"availableBalance": "1"}
    read, _ = reader(fake)
    blocker = read.snapshot().blockers[0]
    assert blocker.code == account_mod.RESPONSE_SHAPE_CHANGED and "account." in blocker.message


def test_a_symbol_that_is_not_trading_blocks_live() -> None:
    fake = FakeBinance()
    halted = json.loads(json.dumps(EXCHANGE_INFO))
    halted["symbols"][0]["status"] = "BREAK"
    fake.routes[("GET", "/fapi/v1/exchangeInfo")] = halted
    read, _ = reader(fake)
    assert read.snapshot().blockers[0].code == account_mod.SYMBOL_NOT_TRADING


# ------------------------------------------------------------------ orders

def router(fake: FakeBinance | None = None, *, armed: bool = False,
           tmp_path: Path | None = None,
           max_open_qty: str | None = None) -> tuple[LiveOrderRouter, FakeBinance, LiveMirror | None]:
    client, fake = make_client(fake, trading_enabled=armed)
    config = make_config(trading_enabled=armed, max_open_qty=max_open_qty)
    read = AccountReader(client, config)
    ledger = (LiveMirror(path=tmp_path / "live" / "binance_live_events.jsonl",
                         account_fingerprint=config.fingerprint) if tmp_path else None)
    return LiveOrderRouter(reader=read, client=client, config=config, mirror=ledger), fake, ledger


def test_an_open_sizes_from_a_notional_with_binances_own_step() -> None:
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    route, _, _ = router(fake)
    plan = route.plan(side="LONG", intent="OPEN", notional_usdt="1000")
    assert plan.qty == D("0.011") and plan.order_side == "BUY" and plan.reduce_only is False
    assert plan.reference_price == D("83500.10")  # the ask, because a LONG lifts it


def test_an_open_against_an_existing_opposite_position_is_refused_not_reversed() -> None:
    route, _, _ = router()  # fixture holds a LONG
    with pytest.raises(OrderRefused) as caught:
        route.plan(side="SHORT", intent="OPEN", qty="0.001")
    assert caught.value.code == orders.REVERSE_NOT_ALLOWED


def test_a_close_reads_the_position_again_instead_of_trusting_the_snapshot() -> None:
    fake = FakeBinance()
    route, _, _ = router(fake)
    snapshot = route.reader.snapshot()
    before = fake.count("/fapi/v3/positionRisk")
    # The position shrank after the screen was rendered; the plan must use the new size.
    fake.position_rows = [{**POSITION_RISK_LONG[0], "positionAmt": "0.008"}]
    plan = route.plan(side="LONG", intent="CLOSE", snapshot=snapshot)
    assert fake.count("/fapi/v3/positionRisk") == before + 1
    assert plan.qty == D("0.008") and plan.order_side == "SELL" and plan.reduce_only is True


def test_closing_a_flat_account_is_refused() -> None:
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    route, _, _ = router(fake)
    with pytest.raises(OrderRefused) as caught:
        route.plan(side="LONG", intent="CLOSE")
    assert caught.value.code == orders.NO_POSITION_TO_CLOSE


def test_an_order_is_refused_while_the_flag_is_off_and_nothing_is_sent(tmp_path: Path) -> None:
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    route, fake, ledger = router(fake, armed=False, tmp_path=tmp_path)
    plan = route.plan(side="LONG", intent="OPEN", qty="0.002")
    with pytest.raises(OrderRefused) as caught:
        route.submit(plan)
    assert caught.value.code == orders.LIVE_TRADING_DISABLED
    assert fake.count("/fapi/v1/order") == 0
    kinds = [(event["event_type"], event.get("stage")) for event in ledger.events]
    # Every audit line says which stage refused it, so the file reads without the code beside it.
    assert (LiveEvent.ORDER_INTENT, "PLAN") in kinds and (LiveEvent.ORDER_INTENT, "SUBMIT") in kinds
    assert (LiveEvent.ORDER_REFUSED, "GATE") in kinds
    assert LiveEvent.ORDER_SENT not in [kind for kind, _ in kinds]


def test_a_refusal_before_the_gate_still_leaves_a_record_of_the_button_press(tmp_path: Path) -> None:
    """Found with a real key attached: an account that cannot be read refuses at the plan stage,
    and that used to leave the audit file empty for an order the operator had asked for."""
    fake = FakeBinance()
    fake.fail("GET", "/fapi/v3/account", 401, -2015, "Invalid API-key, IP, or permissions")
    route, fake, ledger = router(fake, armed=False, tmp_path=tmp_path)
    with pytest.raises(OrderRefused) as caught:
        route.plan(side="LONG", intent="OPEN", qty="0.002")
    assert caught.value.code == orders.ACCOUNT_NOT_READY
    kinds = [(event["event_type"], event.get("stage")) for event in ledger.events]
    assert (LiveEvent.ORDER_INTENT, "PLAN") in kinds
    assert (LiveEvent.ORDER_REFUSED, "PLAN") in kinds
    assert fake.count("/fapi/v1/order") == 0


def test_arming_only_the_environment_flag_is_not_enough(tmp_path: Path) -> None:
    client, fake = make_client(trading_enabled=False)          # client not armed
    config = make_config(trading_enabled=True)                  # env flag on
    route = LiveOrderRouter(reader=AccountReader(client, config), client=client, config=config)
    assert route.armed is False


def test_arming_only_the_client_is_not_enough_either() -> None:
    """The mirror image of the test above. Both directions are asserted because a regression
    that collapses the two gates into one reads correctly from whichever side is not tested."""
    client, _ = make_client(trading_enabled=True)                                  # client armed
    config = make_config(trading_enabled=False, client_armed=True)                 # env flag off
    route = LiveOrderRouter(reader=AccountReader(client, config), client=client, config=config)
    assert route.armed is False


def test_the_two_arming_variables_are_separate_and_both_default_to_false() -> None:
    """`BINANCE_LIVE_CLIENT_ARMED` must not be satisfied by `BINANCE_LIVE_TRADING_ENABLED`:
    one variable would mean one mistake arms a real account."""
    base = {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s"}
    bare = load_config(base)
    assert bare.trading_enabled is False and bare.client_armed is False and bare.armed is False

    only_flag = load_config({**base, "BINANCE_LIVE_TRADING_ENABLED": "true"})
    assert only_flag.trading_enabled is True and only_flag.client_armed is False
    assert only_flag.armed is False

    only_arm = load_config({**base, "BINANCE_LIVE_CLIENT_ARMED": "true"})
    assert only_arm.trading_enabled is False and only_arm.client_armed is True
    assert only_arm.armed is False

    both = load_config({**base, "BINANCE_LIVE_TRADING_ENABLED": "true",
                        "BINANCE_LIVE_CLIENT_ARMED": "true"})
    assert both.armed is True
    # A typo is not an affirmative, in either variable.
    assert load_config({**base, "BINANCE_LIVE_TRADING_ENABLED": "ture",
                        "BINANCE_LIVE_CLIENT_ARMED": "yes"}).armed is False


def test_an_open_above_the_self_imposed_ceiling_is_refused_before_binance_is_asked() -> None:
    """The exchange would accept 0.002 BTC on this account. `BINANCE_LIVE_MAX_QTY` is the
    operator's own bound for a validation run, and it has to bite before the order is built:
    the LIVE ticket's default size is larger than the size a minimum-order test may send."""
    route, fake, _ = router(armed=True, max_open_qty="0.001")
    fake.position_rows = POSITION_RISK_FLAT
    with pytest.raises(OrderRefused) as caught:
        route.plan(side="LONG", intent=orders.OPEN, qty="0.002")
    assert caught.value.code == orders.QTY_ABOVE_LOCAL_MAXIMUM
    assert fake.count("/fapi/v1/order") == 0


def test_the_ceiling_also_bounds_an_open_sized_from_a_notional() -> None:
    route, fake, _ = router(armed=True, max_open_qty="0.001")
    fake.position_rows = POSITION_RISK_FLAT
    with pytest.raises(OrderRefused) as caught:
        # 835 USDT at the fixture's 83,500 is 0.01 BTC, ten times the ceiling.
        route.plan(side="LONG", intent=orders.OPEN, notional_usdt="835")
    assert caught.value.code == orders.QTY_ABOVE_LOCAL_MAXIMUM
    assert fake.count("/fapi/v1/order") == 0


def test_an_open_at_the_ceiling_exactly_is_allowed() -> None:
    # 0.002 rather than 0.001 because the fixture's MIN_NOTIONAL is 100 USDT; the point of the
    # test is the boundary being inclusive, not the particular size.
    route, fake, _ = router(armed=True, max_open_qty="0.002")
    fake.position_rows = POSITION_RISK_FLAT
    plan = route.plan(side="LONG", intent=orders.OPEN, qty="0.002")
    assert plan.qty == D("0.002") and plan.order_side == "BUY"


def test_the_ceiling_never_blocks_a_close() -> None:
    """A position may be larger than the ceiling - opened before it was set, or in the Binance
    app - and it must still be closable. The ceiling bounds entries, not exits."""
    route, fake, _ = router(armed=True, max_open_qty="0.001")
    fake.position_rows = POSITION_RISK_LONG          # 0.015 BTC, fifteen times the ceiling
    plan = route.plan(side="", intent=orders.CLOSE)
    assert plan.qty == D("0.015") and plan.reduce_only is True and plan.order_side == "SELL"


def test_no_ceiling_leaves_the_exchange_filters_as_the_only_bound() -> None:
    route, fake, _ = router(armed=True)
    fake.position_rows = POSITION_RISK_FLAT
    plan = route.plan(side="LONG", intent=orders.OPEN, qty="0.002")
    assert plan.qty == D("0.002")


def test_a_malformed_ceiling_is_refused_rather_than_read_as_no_limit() -> None:
    base = {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s"}
    for bad in ("abc", "0", "-0.001", "nan"):
        with pytest.raises(ValueError):
            load_config({**base, "BINANCE_LIVE_MAX_QTY": bad})


def test_the_gate_view_names_both_variables_and_the_ceiling() -> None:
    route, _, _ = router(armed=True, max_open_qty="0.001")
    view = route.gate_view()
    assert view["env_flag_name"] == "BINANCE_LIVE_TRADING_ENABLED"
    assert view["client_arm_env_name"] == "BINANCE_LIVE_CLIENT_ARMED"
    assert view["max_open_qty"] == D("0.001")
    assert view["armed"] is True


def test_an_armed_router_sends_exactly_the_documented_market_order(tmp_path: Path) -> None:
    """Never run against a real account in V1: the client here talks to a mock transport."""
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    route, fake, ledger = router(fake, armed=True, tmp_path=tmp_path)
    plan = route.plan(side="SHORT", intent="OPEN", qty="0.002")
    route.submit(plan)
    _, path, params = fake.calls[-1]
    assert path == "/fapi/v1/order"
    assert params["type"] == "MARKET" and params["side"] == "SELL"
    assert params["quantity"] == "0.002" and params["newOrderRespType"] == "RESULT"
    assert "reduceOnly" not in params
    assert params["newClientOrderId"].startswith("usbm-open-")
    assert [event["event_type"] for event in ledger.events][-1] == LiveEvent.ORDER_RESULT


def test_an_armed_close_carries_reduce_only(tmp_path: Path) -> None:
    route, fake, _ = router(armed=True, tmp_path=tmp_path)
    plan = route.plan(side="LONG", intent="CLOSE")
    route.submit(plan)
    _, _, params = fake.calls[-1]
    assert params["reduceOnly"] == "true" and params["side"] == "SELL"


def test_leverage_changes_are_gated_by_the_same_flag(tmp_path: Path) -> None:
    route, fake, _ = router(armed=False, tmp_path=tmp_path)
    with pytest.raises(OrderRefused) as caught:
        route.set_leverage(5)
    assert caught.value.code == orders.LIVE_TRADING_DISABLED
    assert fake.count("/fapi/v1/leverage") == 0


def test_an_order_is_refused_while_the_account_is_blocked() -> None:
    fake = FakeBinance()
    fake.position_mode = POSITION_MODE_HEDGE
    route, _, _ = router(fake)
    with pytest.raises(OrderRefused) as caught:
        route.plan(side="LONG", intent="OPEN", qty="0.002")
    assert caught.value.code == orders.ACCOUNT_NOT_READY


# ------------------------------------------------------------------ preview

def test_the_preview_walks_the_visible_book_for_the_size() -> None:
    assert preview.walk(DEPTH["asks"], D("1.5")) == (D("83500.10") * 1 + D("83500.20") * D("0.5")) / D("1.5")


def test_a_size_the_visible_depth_cannot_fill_is_refused_not_extrapolated() -> None:
    with pytest.raises(preview.NoLiquidity):
        preview.walk(DEPTH["asks"], D("100"))


def test_the_round_trip_uses_the_accounts_own_taker_rate() -> None:
    filters = SymbolFilters.from_exchange_info(EXCHANGE_INFO, SYMBOL)
    row = preview.round_trip(side="LONG", qty=D("0.002"), depth=DEPTH,
                             mark=models.MarkPrice.from_payload(MARK_PRICE),
                             commission=models.CommissionRate.from_payload(COMMISSION_RATE),
                             filters=filters, leverage=D("10"))
    assert row["feasible"] and row["fee_rate"] == D("0.000400")
    assert row["entry_fee"] == row["notional"] * D("0.000400")
    assert row["required_margin"] == row["notional"] / D("10")
    assert row["fee_source"] == "binance GET /fapi/v1/commissionRate"


def test_the_breakeven_exit_actually_nets_zero() -> None:
    filters = SymbolFilters.from_exchange_info(EXCHANGE_INFO, SYMBOL)
    qty = D("0.002")
    row = preview.round_trip(side="LONG", qty=qty, depth=DEPTH,
                             mark=models.MarkPrice.from_payload(MARK_PRICE),
                             commission=models.CommissionRate.from_payload(COMMISSION_RATE),
                             filters=filters, leverage=D("10"))
    exit_price = row["breakeven_exit_fill_price"]
    gross = (exit_price - row["entry_fill_price"]) * qty
    net = gross - row["entry_fee"] - exit_price * qty * row["fee_rate"]
    assert abs(net) < D("0.00000001")


def test_a_size_below_the_exchange_minimum_is_refused_by_the_preview() -> None:
    filters = SymbolFilters.from_exchange_info(EXCHANGE_INFO, SYMBOL)
    row = preview.round_trip(side="LONG", qty=D("0.0005"), depth=DEPTH,
                             mark=models.MarkPrice.from_payload(MARK_PRICE),
                             commission=models.CommissionRate.from_payload(COMMISSION_RATE),
                             filters=filters, leverage=D("10"))
    assert row["feasible"] is False and row["reject_code"] == "QTY_BELOW_MINIMUM"


# ------------------------------------------------------------------ mirror

def test_the_mirror_refuses_to_be_written_inside_a_paper_run(tmp_path: Path) -> None:
    with pytest.raises(MirrorPathRefused):
        LiveMirror(path=tmp_path / "crypto" / "paper" / "run-1" / "binance_live_events.jsonl")


def test_the_mirror_appends_one_json_line_per_event_and_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "live" / "binance_live_events.jsonl"
    first = LiveMirror(path=path, account_fingerprint="abcd1234")
    first.append(LiveEvent.ORDER_INTENT, side="LONG", qty=D("0.002"))
    second = LiveMirror(path=path, account_fingerprint="abcd1234")
    second.append(LiveEvent.ORDER_REFUSED, code="LIVE_TRADING_DISABLED")
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["seq"] for row in lines] == [1, 2]
    assert lines[0]["qty"] == "0.002"  # decimals stay decimal strings
    assert second.recent()[0]["event_type"] == LiveEvent.ORDER_REFUSED


def test_the_mirror_says_it_is_never_replayed(tmp_path: Path) -> None:
    ledger = LiveMirror(path=tmp_path / "live" / "events.jsonl")
    assert "never replayed" in ledger.view()["role"]


# ------------------------------------------------------------------ adapter

def adapter(fake: FakeBinance | None = None, *, tmp_path: Path | None = None,
            armed: bool = False) -> tuple[BinanceLiveAdapter, FakeBinance]:
    client, fake = make_client(fake, trading_enabled=armed)
    config = make_config(trading_enabled=armed)
    ledger = (LiveMirror(path=tmp_path / "live" / "events.jsonl") if tmp_path else None)
    return BinanceLiveAdapter(config=config, client=client, mirror=ledger), fake


def test_the_fast_tier_refreshes_price_and_position_without_re_reading_the_whole_account() -> None:
    live, fake = adapter()
    live.snapshot()
    account_calls = fake.count("/fapi/v3/account")
    live._fast_at_ms = 0  # the next poll is due
    live.snapshot()
    assert fake.count("/fapi/v3/account") == account_calls      # slow tier untouched
    assert fake.count("/fapi/v3/positionRisk") >= 2             # fast tier refreshed


def test_a_stream_event_forces_a_full_re_read() -> None:
    live, fake = adapter()
    live.snapshot()
    account_calls = fake.count("/fapi/v3/account")
    live.stream = UserDataStream(client=live.client, config=live.config)
    live.stream.handle({"e": "ORDER_TRADE_UPDATE", "o": {"s": SYMBOL, "X": "FILLED"}})
    assert live.dirty is True
    live.snapshot()
    assert fake.count("/fapi/v3/account") == account_calls + 1
    assert live.dirty is False


def test_resync_throws_away_everything_local_and_asks_binance(tmp_path: Path) -> None:
    live, fake = adapter(tmp_path=tmp_path)
    live.snapshot()
    fake.position_rows = POSITION_RISK_FLAT
    after = live.resync()
    assert after.position.is_flat
    kinds = [event["event_type"] for event in live.mirror.events]
    assert LiveEvent.RECONCILE in kinds


def test_the_adapter_reports_leverage_and_margin_mode_with_the_position() -> None:
    live, _ = adapter()
    position = live.get_position()
    assert position["leverage"] == D("10") and position["margin_type"] == "CROSSED"


def test_the_paper_adapter_only_reads_and_refuses_to_route_an_order() -> None:
    class FakeSession:
        def snapshot(self) -> dict:
            return {"account": {"wallet_balance": "100", "position_side": None},
                    "quote": {"mark_price": "83500"}}

    paper = PaperAdapter(session=FakeSession())
    assert paper.get_account()["wallet_balance"] == "100"
    with pytest.raises(Exception):
        paper.place_order("LONG", "OPEN", "0.001")


# ------------------------------------------------------------------ user data stream

def test_an_account_event_marks_the_adapter_dirty_but_does_not_move_a_balance() -> None:
    client, _ = make_client()
    stream = UserDataStream(client=client, config=make_config())
    stream.handle({"e": "ACCOUNT_UPDATE", "a": {"B": [{"a": "USDT", "wb": "999999"}]}})
    assert stream.telemetry.dirty is True and stream.telemetry.account_events == 1
    assert "999999" not in json.dumps(stream.view())


def test_an_expired_listen_key_drops_the_key_so_the_loop_mints_a_new_one() -> None:
    client, _ = make_client()
    stream = UserDataStream(client=client, config=make_config())
    stream.open_key()
    assert stream.listen_key is not None
    stream.handle({"e": "listenKeyExpired"})
    assert stream.listen_key is None and stream.telemetry.expired_events == 1


def test_the_stream_url_appends_the_listen_key_to_the_configured_base() -> None:
    client, _ = make_client()
    stream = UserDataStream(client=client, config=make_config())
    assert stream.url("abc") == "wss://fstream.binance.com/ws/abc"


def test_the_stream_says_in_its_own_view_that_it_is_only_a_signal() -> None:
    client, _ = make_client()
    stream = UserDataStream(client=client, config=make_config())
    assert "CHANGE_SIGNAL_ONLY" in stream.view()["role"]
