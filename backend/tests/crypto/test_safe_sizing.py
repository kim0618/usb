"""Two-sided (entry and exit) sizing.

The production failure these pin: 1.082 BTC entered against 10.7 BTC of ask, and could not be
closed against 0.157 BTC of bid. A size you can get into but not out of is not a size.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.paper import sizing
from app.crypto.paper.engine import OrderRejected, PaperEngine
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal
STEP = D("0.001")


def book(bid_qty: str, ask_qty: str, *, capital: str = "10000000", leverage: str = "10",
         fx: str = "1341") -> PaperEngine:
    return started_engine(
        make_config(leverage=leverage, capital_krw=capital, fx=fx), _tiers(),
        first_quote=quote(1_000, mark="83800.0",
                          bids=[["83800.0", bid_qty]], asks=[["83800.1", ask_qty]]))


def _tiers():
    from app.crypto.paper.instrument import RiskTierTable
    from tests.crypto.conftest import RISK_LIMIT_PAYLOAD
    return RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="test", source_sha256="deadbeef")


# ------------------------------------------------------------------ the production case

def test_the_production_failure_is_now_refused_at_sizing_time() -> None:
    """1.082 BTC against 0.157 BTC of bid: entered fine, could not be closed.

    At the leverage the operator actually had selected (50x), the margin was comfortable and
    the ask was ten times deep enough. Only the bid was not, and nothing was looking at it.
    """
    engine = book(bid_qty="0.157", ask_qty="10.740", leverage="50")
    oversized = sizing.probe(engine, "LONG", D("1.082"))
    assert oversized.entry_feasible is True          # the entry half still passes
    assert oversized.exit_feasible is False          # and the exit half is why it is refused
    assert oversized.feasible is False
    assert oversized.exit_reject_code == "NO_LIQUIDITY"


def test_max_is_bounded_by_the_exit_side_when_that_is_the_thin_one() -> None:
    engine = book(bid_qty="0.157", ask_qty="10.740", leverage="50")
    ceiling = sizing.max_entry(engine, "LONG")
    assert ceiling.feasible
    assert ceiling.qty == D("0.157")                 # the whole bid, not the deep ask
    assert ceiling.exit_feasible is True


def test_entry_only_sizing_would_have_allowed_more() -> None:
    engine = book(bid_qty="0.157", ask_qty="10.740", leverage="50")
    safe = sizing.max_entry(engine, "LONG")
    beyond = sizing.probe(engine, "LONG", safe.qty + STEP, require_exit=False)
    # Proof the change is doing something: the old rule still accepts what the new one refuses.
    assert beyond.feasible is True
    assert sizing.probe(engine, "LONG", safe.qty + STEP).feasible is False


# ------------------------------------------------------------------ both directions

@pytest.mark.parametrize("bid_qty,ask_qty", [("0.200", "9.000"), ("9.000", "0.200")])
def test_the_thinner_side_bounds_both_directions(bid_qty: str, ask_qty: str) -> None:
    """A consequence of requiring both halves, and the reason LONG and SHORT MAX converge.

    A round trip touches both sides of the book whichever way round it goes: a LONG takes the
    ask then gives back to the bid, a SHORT does the reverse. So once the exit is required, the
    binding constraint is the *thinner* side, and it binds both directions equally.
    """
    engine = book(bid_qty=bid_qty, ask_qty=ask_qty)
    thinner = min(D(bid_qty), D(ask_qty))
    assert sizing.max_entry(engine, "LONG").qty == thinner
    assert sizing.max_entry(engine, "SHORT").qty == thinner


def test_each_direction_reports_its_own_entry_and_exit_depth() -> None:
    engine = book(bid_qty="0.300", ask_qty="7.000")
    payload_long = sizing.presets(engine, "LONG")
    payload_short = sizing.presets(engine, "SHORT")
    # The sides are still computed separately even where the answer coincides.
    assert payload_long["entry_depth"] == D("7.000")
    assert payload_long["exit_depth"] == D("0.300")
    assert payload_short["entry_depth"] == D("0.300")
    assert payload_short["exit_depth"] == D("7.000")


# ------------------------------------------------------------------ the ceiling is exact

def test_max_opens_and_closes_for_real_on_the_same_snapshot() -> None:
    engine = book(bid_qty="0.157", ask_qty="10.740", leverage="50")
    ceiling = sizing.max_entry(engine, "LONG")
    engine.submit_order(ts_ms=1_100, side="LONG", qty=ceiling.qty, intent="OPEN",
                        request_id="o1")
    assert engine.account.position.abs_qty == ceiling.qty
    engine.submit_order(ts_ms=1_200, side="LONG", qty=ceiling.qty, intent="CLOSE",
                        request_id="c1")
    assert engine.account.position.is_flat


def test_one_step_above_max_cannot_be_closed_after_it_opens() -> None:
    engine = book(bid_qty="0.157", ask_qty="10.740", leverage="50")
    ceiling = sizing.max_entry(engine, "LONG")
    over = ceiling.qty + STEP
    engine.submit_order(ts_ms=1_100, side="LONG", qty=over, intent="OPEN", request_id="o1")
    from app.crypto.paper.book import NoLiquidity
    with pytest.raises((NoLiquidity, OrderRejected)):
        engine.submit_order(ts_ms=1_200, side="LONG", qty=over, intent="CLOSE",
                            request_id="c1")


@pytest.mark.parametrize("label", ["25%", "HALF", "75%", "MAX"])
def test_every_preset_opens_and_closes_for_real(label: str) -> None:
    payload = sizing.presets(book(bid_qty="0.400", ask_qty="6.000"), "LONG")
    row = next(item for item in payload["presets"] if item["label"] == label)
    if not row["feasible"]:
        pytest.skip(f"{label} is not available on this book")
    engine = book(bid_qty="0.400", ask_qty="6.000")
    engine.submit_order(ts_ms=1_100, side="LONG", qty=row["qty"], intent="OPEN",
                        request_id="o1")
    engine.submit_order(ts_ms=1_200, side="LONG", qty=row["qty"], intent="CLOSE",
                        request_id="c1")
    assert engine.account.position.is_flat


# ------------------------------------------------------------------ the other constraints

def test_margin_still_binds_when_the_book_is_deep() -> None:
    engine = book(bid_qty="500", ask_qty="500", capital="1000000", leverage="5")
    ceiling = sizing.max_entry(engine, "LONG")
    assert ceiling.feasible
    # Nothing to do with depth here: the wallet is the limit.
    assert ceiling.required_total <= engine.account.available_balance
    step_over = sizing.probe(engine, "LONG", ceiling.qty + STEP)
    assert step_over.reject_code == "INSUFFICIENT_MARGIN"


def test_the_ceiling_lands_on_the_quantity_grid() -> None:
    ceiling = sizing.max_entry(book(bid_qty="0.3337", ask_qty="9"), "LONG")
    assert ceiling.qty % STEP == 0


def test_a_book_too_thin_for_the_minimum_order_has_no_max() -> None:
    engine = book(bid_qty="0.0005", ask_qty="9")
    ceiling = sizing.max_entry(engine, "LONG")
    assert ceiling.feasible is False
    assert ceiling.qty == 0
    assert ceiling.reject_code in {"NO_LIQUIDITY", "QTY_BELOW_MINIMUM"}


def test_fees_are_still_reserved_inside_the_safe_ceiling() -> None:
    engine = book(bid_qty="500", ask_qty="500", capital="1000000", leverage="5")
    ceiling = sizing.max_entry(engine, "LONG")
    assert ceiling.fee > 0
    assert ceiling.required_total == ceiling.reserved_margin + ceiling.fee


# ------------------------------------------------------------------ provenance and purity

def test_a_size_carries_the_snapshot_it_was_priced_on() -> None:
    engine = book(bid_qty="0.400", ask_qty="6.000")
    ceiling = sizing.max_entry(engine, "LONG")
    assert ceiling.quote_ts_ms == 1_000
    assert ceiling.best_bid == D("83800.0")
    assert ceiling.best_ask == D("83800.1")
    assert ceiling.bid_depth == D("0.400") and ceiling.ask_depth == D("6.000")
    # The entry fill is priced off the ask, never off the mark.
    assert ceiling.fill_price == D("83800.1")
    assert ceiling.exit_fill_price == D("83800.0")


def test_the_payload_says_what_max_now_means() -> None:
    payload = sizing.presets(book(bid_qty="0.400", ask_qty="6.000"), "LONG")
    assert payload["max_definition"] == "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT"
    assert payload["quote_ts_ms"] == 1_000
    assert payload["entry_depth"] == D("6.000")
    assert payload["exit_depth"] == D("0.400")


def test_probing_writes_no_ledger_event_and_moves_no_money() -> None:
    engine = book(bid_qty="0.400", ask_qty="6.000")
    events = len(engine.ledger.events)
    qty_before = engine.account.position.signed_qty
    fees_before = engine.account.cumulative_fees

    sizing.presets(engine, "LONG")
    sizing.presets(engine, "SHORT")
    sizing.max_entry(engine, "LONG")

    # The exit half runs a second clone; neither may touch the real account.
    assert len(engine.ledger.events) == events
    assert engine.account.position.signed_qty == qty_before
    assert engine.account.cumulative_fees == fees_before


def test_sizing_without_a_quote_is_unavailable_rather_than_guessed() -> None:
    engine = book(bid_qty="0.400", ask_qty="6.000")
    engine.quote = None
    result = sizing.probe(engine, "LONG", D("0.010"))
    assert result.feasible is False and result.reject_code == "NO_QUOTE"


def test_adding_to_an_open_position_is_sized_on_the_whole_resulting_position() -> None:
    engine = book(bid_qty="0.300", ask_qty="9.000")
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.200"), intent="OPEN",
                        request_id="o1")
    # 0.200 held plus 0.150 more would be 0.350, past the 0.300 of bid it must exit through.
    adding = sizing.probe(engine, "LONG", D("0.150"))
    assert adding.entry_feasible is True
    assert adding.exit_feasible is False
    ceiling = sizing.max_entry(engine, "LONG")
    assert engine.account.position.abs_qty + ceiling.qty <= D("0.300")
