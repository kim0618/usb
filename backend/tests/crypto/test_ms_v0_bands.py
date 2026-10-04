"""Band depth, imbalance, and the coverage states that keep a missing number out of the data.

The test this file is built around is `test_a_band_beyond_the_snapshot_bound_is_partial_not_zero`.
Every other coverage test is a variation on it. A collector that answered `0` there would produce
a dataset in which thin liquidity and unobserved liquidity are the same number.
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.market_structure_v0 import bands as BD
from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0.contract import COMPLETE, PARTIAL, UNKNOWN

from tests.crypto.ms_v0_fixtures import S, snapshot, synced_book, wide_snapshot

FRESH = 500_000_000  # 0.5 s of monotonic age


def band(view: dict, label: str) -> dict:
    return next(item for item in view["bands"] if item["band_pct"] == label)


# --- depth computation ------------------------------------------------------------------------

def test_band_quantity_and_notional_are_summed_over_the_prices_inside_the_band():
    book = synced_book()
    view = BD.band_view(book, at_ns=FRESH)
    inner = band(view, "0.1")
    # mid 84755, so bids in [84670.245, 84755]: 84750 down to 84680, nine levels of size 1.
    assert inner["bid"]["coverage"] == COMPLETE
    assert inner["bid"]["qty"] == "8"
    assert inner["bid"]["levels"] == 8
    assert Decimal(inner["bid"]["notional"]) == sum(
        Decimal(price) for price in ("84750", "84740", "84730", "84720", "84710", "84700",
                                     "84690", "84680"))


def test_mid_is_the_book_mid_and_never_a_trade_or_mark_price():
    book = synced_book()
    view = BD.band_view(book, at_ns=FRESH)
    assert view["mid"] == "84755"
    assert view["best_bid"] == "84750" and view["best_ask"] == "84760"


def test_imbalance_is_zero_on_a_symmetric_book_and_signed_on_a_lopsided_one():
    book = synced_book(snapshot_payload=wide_snapshot())
    assert band(BD.band_view(book, at_ns=FRESH), "1")["imbalance_btc"] == "0"
    book.bids[Decimal("84750")] = Decimal("100")
    lopsided = band(BD.band_view(book, at_ns=FRESH), "1")
    assert Decimal(lopsided["imbalance_btc"]) > 0
    assert Decimal(lopsided["imbalance_usdt"]) > 0


def test_btc_and_usdt_imbalance_are_reported_separately():
    book = synced_book(snapshot_payload=wide_snapshot())
    view = band(BD.band_view(book, at_ns=FRESH), "1")
    assert view["bid"]["qty"] is not None and view["bid"]["notional"] is not None
    assert view["imbalance_btc"] is not None and view["imbalance_usdt"] is not None


def test_every_contract_band_is_present_once():
    view = BD.band_view(synced_book(), at_ns=FRESH)
    assert [item["band_pct"] for item in view["bands"]] == ["0.1", "0.25", "0.5", "1"]


# --- coverage ---------------------------------------------------------------------------------

def test_a_band_beyond_the_snapshot_bound_is_partial_not_zero():
    """The measured live case: a `limit=1000` snapshot reaches about +-0.15% of mid."""
    book = synced_book()
    view = BD.band_view(book, at_ns=FRESH)
    assert band(view, "0.1")["bid"]["coverage"] == COMPLETE
    for label in ("0.25", "0.5", "1"):
        side = band(view, label)["bid"]
        assert side["coverage"] == PARTIAL
        # Canonical values are withheld, the observed lower bound is given and labelled.
        assert side["qty"] is None and side["notional"] is None
        assert side["observed_qty"] == "13" and side["observed_is_lower_bound"] is True


def test_imbalance_needs_both_sides_complete():
    book = synced_book()
    assert band(BD.band_view(book, at_ns=FRESH), "0.1")["imbalance_btc"] is not None
    assert band(BD.band_view(book, at_ns=FRESH), "1")["imbalance_btc"] is None


def test_one_complete_side_does_not_license_an_imbalance():
    book = synced_book()
    # Push the bid bound out so the bid side of +-0.25% is complete while the ask side is not.
    book.known_low = Decimal("80000")
    view = band(BD.band_view(book, at_ns=FRESH), "0.25")
    assert view["bid"]["coverage"] == COMPLETE and view["ask"]["coverage"] == PARTIAL
    assert view["imbalance_btc"] is None and view["imbalance_usdt"] is None


def test_a_stale_book_is_unknown_everywhere_with_no_observed_values():
    book = synced_book()
    view = BD.band_view(book, at_ns=5 * S)
    assert view["state"] == B.STALE and view["fresh"] is False
    for item in view["bands"]:
        for side in ("bid", "ask"):
            assert item[side]["coverage"] == UNKNOWN
            assert item[side]["qty"] is None and item[side]["observed_qty"] is None
        assert item["imbalance_btc"] is None


def test_an_unsynced_book_is_unknown_and_says_why():
    book = synced_book()
    book.invalidate(B.GAP_PU_MISMATCH)
    view = BD.band_view(book, at_ns=FRESH)
    assert view["state"] == B.UNSYNCED
    assert view["last_invalidation"] == B.GAP_PU_MISMATCH
    assert view["mid"] is None
    assert all(item["bid"]["coverage"] == UNKNOWN for item in view["bands"])


def test_an_empty_band_on_a_complete_side_is_an_observed_zero():
    """Zero is a real answer when the side-band is known and nothing rests in it.

    It takes a spread wider than the band to construct: the best bid sits `spread / 2` below mid,
    so it falls outside the +-0.1% band only once the spread exceeds 0.2% of mid. That is exactly
    the market state where a zero matters, and it must not be confused with PARTIAL.
    """
    book = B.DepthBook()
    book.apply_snapshot({"lastUpdateId": 1, "E": 1_000,
                         "bids": [["84000", "2"], ["80000", "1"]],
                         "asks": [["85000", "2"], ["90000", "1"]]}, 1_000, 0)
    view = band(BD.band_view(book, at_ns=FRESH), "0.1")
    assert book.mid() == Decimal("84500")
    assert view["bid"]["coverage"] == COMPLETE
    assert view["bid"]["qty"] == "0" and view["bid"]["notional"] == "0"
    assert view["bid"]["levels"] == 0
    # A zero denominator on a complete side is still not a zero ratio.
    assert view["ask"]["qty"] == "0"
    assert view["imbalance_btc"] is None


def test_band_boundaries_are_reported_so_a_reader_can_recompute():
    view = band(BD.band_view(synced_book(), at_ns=FRESH), "0.1")
    assert view["bid"]["price_low"] is not None and view["bid"]["price_high"] == "84755"
    assert view["ask"]["price_low"] == "84755"


# --- lag --------------------------------------------------------------------------------------

def test_lag_is_recorded_separately_from_freshness():
    book = synced_book(receive_ms=1_150, mono_ns=0)
    view = BD.band_view(book, at_ns=FRESH)
    assert view["lag_ms"] == 150 and view["lag_state"] == COMPLETE
    assert view["age_ms"] == 500 and view["fresh"] is True


def test_an_implausible_lag_is_unknown_rather_than_a_number():
    assert BD.lag_state(None) == UNKNOWN
    assert BD.lag_state(6_000) == UNKNOWN
    assert BD.lag_state(-2_000) == UNKNOWN
    assert BD.lag_state(150) == COMPLETE


def test_a_snapshot_only_book_has_no_lag_because_it_has_no_event_time():
    book = B.DepthBook()
    book.apply_snapshot(snapshot(event_ms=None), 1_000, 0)
    assert book.lag_ms() is None
    assert BD.band_view(book, at_ns=FRESH)["lag_state"] == UNKNOWN
