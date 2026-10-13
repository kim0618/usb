"""The DERIVATIVES layer: raw figures, one subtraction, and no third reading anywhere.

Driven through a fake opener rather than the venue, so the arithmetic and the failure handling are
checked exactly rather than against whatever the market happens to be doing.
"""
from __future__ import annotations

import ast
import io
import json
from pathlib import Path

import pytest

from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import derivatives as D
from app.crypto.market_context_v1 import venue as VN

PACKAGE = Path(__file__).resolve().parents[2] / "app" / "crypto" / "market_context_v1"
NOW = 1_791_000_000_000


class Opener:
    """Answers the three endpoints from a dict, and can be told to fail one of them."""

    def __init__(self, *, premium=None, open_interest=None, hist=None, fail=()):
        self.premium = premium
        self.open_interest = open_interest
        self.hist = hist
        self.fail = set(fail)
        self.calls: list[str] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        for name, path, body in (("premium", "/fapi/v1/premiumIndex", self.premium),
                                 ("open_interest", "/fapi/v1/openInterest", self.open_interest),
                                 ("hist", "/futures/data/openInterestHist", self.hist)):
            if path in url:
                self.calls.append(name)
                if name in self.fail:
                    raise OSError("refused")
                return _Response(body)
        raise AssertionError(url)


class _Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def premium(mark="86000.5", index="86010.5", funding="0.00006540", time_ms=NOW):
    return {"symbol": "BTCUSDT", "markPrice": mark, "indexPrice": index,
            "estimatedSettlePrice": "86020.0", "lastFundingRate": funding,
            "interestRate": "0.00010000", "nextFundingTime": NOW + 3_600_000, "time": time_ms}


def hist(values, *, start_ms=NOW - 60 * 300_000):
    """`len(values)` ascending 5 m buckets, oldest first, as the endpoint returns them."""
    return [{"symbol": "BTCUSDT", "sumOpenInterest": f"{value:.8f}",
             "sumOpenInterestValue": f"{value * 86000:.8f}",
             "timestamp": start_ms + index * 300_000}
            for index, value in enumerate(values)]


# --- the arithmetic ------------------------------------------------------------------------------

def test_basis_and_premium_are_the_two_published_prices_and_nothing_else():
    read = D.read("BTCUSDT", NOW, opener=Opener(premium=premium(), open_interest={
        "openInterest": "98500.0", "time": NOW}, hist=hist([100.0] * 60)))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    assert view["basis"]["basis_usdt"] == "-10.0"
    assert view["basis"]["premium_pct"] == "-0.011626"
    assert view["basis"]["formula"] == \
        "basis = mark - index; premium_pct = (mark - index) / index * 100"


def test_the_open_interest_change_is_a_difference_of_closed_venue_buckets():
    # 60 buckets rising by one each: 5 m back is -1, 15 m back is -3, 1 h is -12, 4 h is -48.
    read = D.read("BTCUSDT", NOW, opener=Opener(
        premium=premium(), open_interest={"openInterest": "160.0", "time": NOW},
        hist=hist([100.0 + index for index in range(60)])))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    windows = view["open_interest_change"]["windows"]
    assert [windows[label]["base"] for label in C.OI_CHANGE_WINDOWS] == \
        ["1.00000000", "3.00000000", "12.00000000", "48.00000000"]
    assert view["open_interest_change"]["source"] == "CLOSED_VENUE_BUCKETS_NOT_LOCAL_POLLS"
    assert view["open_interest_change"]["period"] == "5m"


def test_a_falling_open_interest_keeps_its_sign():
    read = D.read("BTCUSDT", NOW, opener=Opener(
        premium=premium(), open_interest={"openInterest": "100.0", "time": NOW},
        hist=hist([160.0 - index for index in range(60)])))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    windows = view["open_interest_change"]["windows"]
    assert windows["5m"]["base"] == "-1.00000000"
    assert windows["4h"]["base"] == "-48.00000000"
    assert windows["5m"]["base_pct"].startswith("-")


def test_too_few_buckets_is_named_per_horizon_rather_than_filled_in():
    read = D.read("BTCUSDT", NOW, opener=Opener(
        premium=premium(), open_interest={"openInterest": "100.0", "time": NOW},
        hist=hist([100.0 + index for index in range(5)])))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    windows = view["open_interest_change"]["windows"]
    assert windows["5m"]["available"] is True
    for label in ("1h", "4h"):
        assert windows[label]["available"] is False
        assert windows[label]["unavailable_reason"] == "NOT_ENOUGH_CLOSED_BUCKETS"


def test_history_arriving_in_the_wrong_order_is_refused_rather_than_differenced():
    """A silent reversal of the endpoint's order would flip the sign of every row here."""
    rows = hist([100.0 + index for index in range(60)])
    rows.reverse()
    read = D.read("BTCUSDT", NOW, opener=Opener(
        premium=premium(), open_interest={"openInterest": "100.0", "time": NOW}, hist=rows))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    assert view["open_interest_change"]["available"] is False
    assert view["open_interest_change"]["unavailable_reason"] == "HISTORY_NOT_ASCENDING"


# --- failure handling ----------------------------------------------------------------------------

def test_each_endpoint_fails_on_its_own_and_the_half_that_arrived_is_shown():
    read = D.read("BTCUSDT", NOW, opener=Opener(
        premium=premium(), open_interest={"openInterest": "98500.0", "time": NOW},
        hist=None, fail={"hist"}))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    assert view["state"] == C.PARTIAL
    assert any(reason.startswith("OPEN_INTEREST_HIST:") for reason in view["reasons"])
    assert view["funding"]["last_rate"] is not None
    assert view["open_interest"]["base"] == "98500.0"
    assert view["open_interest_change"]["available"] is False


def test_everything_failing_reads_unknown_rather_than_a_row_of_zeroes():
    read = D.read("BTCUSDT", NOW, opener=Opener(
        fail={"premium", "open_interest", "hist"}))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    assert view["state"] == C.UNKNOWN
    assert view["open_interest"]["base"] is None
    assert view["funding"]["last_rate"] is None
    assert view["basis"]["basis_usdt"] is None
    assert "0" not in {view["open_interest"]["base"], view["funding"]["last_rate"]}


def test_a_venue_timestamp_older_than_the_bound_reads_stale():
    read = D.read("BTCUSDT", NOW, opener=Opener(
        premium=premium(time_ms=NOW - C.DERIVATIVES_STALE_MS - 1),
        open_interest={"openInterest": "98500.0", "time": NOW}, hist=hist([100.0] * 60)))
    view = D.derivatives_view(read, symbol="BTCUSDT", now_ms=NOW)
    assert view["state"] == C.STALE
    assert "VENUE_TIMESTAMP_OLDER_THAN_FRESHNESS_BOUND" in view["reasons"]


def test_nothing_read_yet_is_a_screen_state():
    view = D.derivatives_view(None, symbol="BTCUSDT", now_ms=NOW)
    assert view["state"] == C.UNKNOWN and view["reasons"] == ["NOT_READ_YET"]


# --- per symbol, with no shared state ------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
def test_the_layer_exists_for_every_symbol_and_asks_the_venue_about_that_symbol(symbol):
    opener = Opener(premium=premium(), open_interest={"openInterest": "1.0", "time": NOW},
                    hist=hist([1.0] * 60))
    read = D.read(symbol, NOW, opener=opener)
    view = D.derivatives_view(read, symbol=symbol, now_ms=NOW)
    assert view["state"] == C.LIVE
    assert view["symbol"] == symbol
    assert opener.calls == ["premium", "open_interest", "hist"]


def test_the_requests_carry_the_symbol_they_are_asked_about():
    seen: list[str] = []

    class Spy(Opener):
        def __call__(self, request, timeout=None):
            seen.append(request.full_url)
            return super().__call__(request, timeout=timeout)

    spy = Spy(premium=premium(), open_interest={"openInterest": "1.0", "time": NOW},
              hist=hist([1.0] * 60))
    D.read("SOLUSDT", NOW, opener=spy)
    assert len(seen) == 3
    assert all("symbol=SOLUSDT" in url for url in seen), seen


# --- no new derivatives signal -------------------------------------------------------------------

def test_the_module_has_no_function_that_combines_two_derivatives_fields():
    """Section 8's ban, checked by shape: the only arithmetic over two published fields is the
    subtraction the contract names, and it lives in one expression."""
    source = (PACKAGE / "derivatives.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    subtractions = [node for node in ast.walk(tree)
                    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub)]
    rendered = {ast.unparse(node) for node in subtractions}
    # `mark - index` for the basis, `now - then` for each open-interest difference, and the index
    # arithmetic that walks back through the buckets. Nothing that mixes a *kind* of figure with
    # another kind.
    assert "mark - index" in rendered
    assert "now_oi - then_oi" in rendered
    assert "now_value - then_value" in rendered
    for expression in rendered:
        assert "funding" not in expression, expression
        assert "price" not in expression or expression == "mark - index", expression


def test_the_module_reads_no_other_layer():
    source = (PACKAGE / "derivatives.py").read_text(encoding="utf-8")
    for name in ("liquidity", "structure", "flow", "auxiliary", "assemble"):
        assert f"from . import {name}" not in source
        assert f"import {name}" not in source.replace("from . import contract as C", "")


def test_the_only_paths_used_are_the_three_public_derivatives_endpoints():
    opener = Opener(premium=premium(), open_interest={"openInterest": "1.0", "time": NOW},
                    hist=hist([1.0] * 60))
    seen: list[str] = []

    class Spy(Opener):
        def __call__(self, request, timeout=None):
            seen.append(request.selector.split("?")[0])
            return super().__call__(request, timeout=timeout)

    D.read("BTCUSDT", NOW, opener=Spy(premium=opener.premium,
                                      open_interest=opener.open_interest, hist=opener.hist))
    assert seen == ["/fapi/v1/premiumIndex", "/fapi/v1/openInterest",
                    "/futures/data/openInterestHist"]
    for path in seen:
        assert path in VN.ALLOWED_PATHS
