"""The PRICE layer: read-only parity with the research engine, and the no-repaint property.

Two claims are worth real tests here and the rest is projection.

**Parity.** The panel does not re-implement the R1 curation, so the thing that can silently differ
is everything *around* the engine: how the klines become `Bars`, and which minutes the publish
window lands on. Both are checked against `market_context_r0.structure.run` over that study's own
frozen series, row for row.

**No repaint.** The panel's one promise about the structure layer is that extending the series
forward does not move a line already on the screen. With a fixed anchor that follows from R1's
prefix-truncation invariance, and `prefix_check` measures it rather than assuming it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import structure as S
from app.crypto.market_context_v1 import venue as VN

REPO = Path(__file__).resolve().parents[3]
FROZEN = REPO / "data/research/crypto/market_context_r0/raw"
MINUTE = 60_000


def frozen_rows() -> list[dict]:
    files = sorted(FROZEN.glob("binance_kline_1m_*.jsonl"))
    if len(files) != 1:
        pytest.skip("the Market Context R0 frozen series is not in this tree")
    with open(files[0]) as handle:
        return [json.loads(line) for line in handle]


def synthetic_rows(count: int, *, start_ms: int = 1_786_492_800_000) -> list[dict]:
    """A deterministic 1m series on a UTC day boundary, long enough for the folds and the ATR."""
    rows = []
    price = 80_000.0
    for index in range(count):
        price += ((index * 37) % 17 - 8) * 1.5
        high, low = price + 12.0, price - 11.0
        rows.append({"timestamp_ms": start_ms + index * MINUTE,
                     "open": f"{price:.2f}", "high": f"{high:.2f}", "low": f"{low:.2f}",
                     "close": f"{price + 1.0:.2f}", "volume": "10.0", "turnover": "800000.0",
                     "trades": 100, "taker_buy_base": "5.0", "taker_buy_quote": "400000.0"})
    return rows


# --- bars construction -------------------------------------------------------------------------

def test_the_bars_are_byte_identical_to_the_research_loaders_own():
    rows = frozen_rows()
    from app.crypto.research.market_context_r0 import data as R0D

    mine = S.build_bars(rows)
    theirs = R0D.load()
    assert set(mine) == set(theirs) == {"1m", "5m", "1h"}
    for timeframe in ("1m", "5m", "1h"):
        a, b = mine[timeframe], theirs[timeframe]
        assert a.tf == b.tf and a.step_ms == b.step_ms and len(a) == len(b)
        for field in ("ts", "open", "high", "low", "close", "volume", "turnover", "atr"):
            left, right = getattr(a, field), getattr(b, field)
            assert np.array_equal(left, right, equal_nan=True), (timeframe, field)


def test_a_gap_in_the_minute_grid_is_an_error_not_a_repair():
    rows = synthetic_rows(200)
    del rows[100]
    with pytest.raises(VN.VenueError, match="SERIES_GRID_HAS_A_GAP"):
        S.build_bars(rows)


def test_a_series_that_does_not_start_on_a_day_boundary_is_refused_by_the_fold():
    rows = synthetic_rows(200, start_ms=1_786_492_800_000 + 7 * MINUTE)
    with pytest.raises(RuntimeError):
        S.build_bars(rows)


def test_an_empty_series_is_named_rather_than_crashing_on_an_index():
    with pytest.raises(VN.VenueError, match="SERIES_HAS_TOO_FEW_BARS"):
        S.build_bars([])


# --- publish window ----------------------------------------------------------------------------

def test_the_published_minute_is_the_last_closed_one_and_never_a_partial_bar():
    now = 1_791_000_000_000 + 37_000          # 37 s into a minute
    last = S.last_closed_open_ms(now)
    assert last % MINUTE == 0
    # The bar that opened at `last` closed at `last + 60s`, which is in the past. The bar that
    # opened after it has not closed, so it cannot appear.
    assert last + MINUTE <= now
    assert last + 2 * MINUTE > now


def test_the_default_anchor_lands_on_a_utc_day_boundary():
    for offset in (0, 1, 59_999, 86_399_999):
        anchor = S.default_anchor_ms(1_791_000_000_000 + offset, 30)
        assert anchor % C.DAY_MS == 0


# --- parity with the research engine -----------------------------------------------------------

def test_the_published_rows_are_identical_to_the_research_engines_own():
    """The parity claim, measured on the study's own series over its own publish window."""
    from app.crypto.research.market_context_r0 import data as R0D
    from app.crypto.research.market_context_r0 import structure as R0S

    rows = frozen_rows()
    series = S.Series("BTCUSDT", anchor_ms=int(rows[0]["timestamp_ms"]))
    series.rows = rows
    # 90 minutes keeps the comparison cheap; the engine still walks all 76,800 bars either way, so
    # the book both sides publish from has the whole series behind it.
    window = 90 * MINUTE
    mine = series.run(publish_window_ms=window)
    publish_to = int(rows[-1]["timestamp_ms"]) + MINUTE
    theirs = R0S.run(publish_to - window, publish_to)

    assert mine.publish_to_ms == publish_to
    assert len(mine.rows) == len(theirs.rows) > 60
    assert [row["info_ts"] for row in mine.rows] == [row["info_ts"] for row in theirs.rows]
    assert mine.rows == theirs.rows
    assert R0D.REPLAY_START_MS  # the frozen window really is the study's


def test_the_repaint_audit_is_clean_on_the_frozen_series():
    rows = frozen_rows()
    series = S.Series("BTCUSDT", anchor_ms=int(rows[0]["timestamp_ms"]))
    series.rows = rows
    run = series.run(publish_window_ms=6 * 60 * MINUTE)
    assert run.repaint_violations == 0
    assert run.vanished_levels == 0


# --- no repaint across an extension ------------------------------------------------------------

@pytest.mark.parametrize("cut", [1, 7, 60])
def test_extending_the_series_forward_does_not_move_an_already_published_minute(cut):
    rows = synthetic_rows(4 * 1440)
    result = S.prefix_check(rows, cut=cut, publish_window_ms=6 * 60 * MINUTE)
    assert result["compared_minutes"] > 100, result
    assert result["identical"], result["differences"]


def test_the_prefix_check_refuses_a_cut_that_would_compare_nothing():
    rows = synthetic_rows(200)
    for cut in (0, -1, 200, 500):
        with pytest.raises(ValueError):
            S.prefix_check(rows, cut=cut)


# --- the display projection --------------------------------------------------------------------

def built_run(count: int = 3 * 1440) -> S.StructureRun:
    rows = synthetic_rows(count)
    series = S.Series("BTCUSDT", anchor_ms=int(rows[0]["timestamp_ms"]))
    series.rows = rows
    return series.run(publish_window_ms=120 * MINUTE)


def test_a_fresh_bar_reads_live_and_an_old_one_reads_stale():
    run = built_run()
    close_ms = run.rows[-1]["info_ts"]
    fresh = S.structure_view(run, symbol=C.BTC, now_ms=close_ms + 30_000)
    assert fresh["state"] == C.LIVE and fresh["reasons"] == []
    stale = S.structure_view(run, symbol=C.BTC,
                             now_ms=close_ms + C.MINUTE_MS + C.STRUCTURE_GRACE_MS + 1)
    assert stale["state"] == C.STALE
    assert "LAST_CLOSED_BAR_OLDER_THAN_GRACE" in stale["reasons"]


def test_the_layer_carries_the_series_basis_so_every_figure_is_attributable():
    run = built_run()
    view = S.structure_view(run, symbol=C.BTC, now_ms=run.rows[-1]["info_ts"])
    series = view["series"]
    assert series["anchor_rule"] == "FIXED_AT_PROCESS_START_EXTENDED_FORWARD_ONLY"
    assert (series["theta_1h"], series["theta_5m"]) == (C.THETA_1H, C.THETA_5M)
    assert series["venue"] == "binance_usdm"
    assert series["repaint_violations"] == 0


def test_a_side_with_no_published_level_says_so_rather_than_showing_a_zero():
    run = built_run()
    view = S.structure_view(run, symbol=C.BTC, now_ms=run.rows[-1]["info_ts"])
    for side in ("resistance", "support"):
        level = view["levels"][side]
        if not level["available"]:
            assert level["price"] is None
            assert level["unavailable_reason"] == "NO_PUBLISHED_LEVEL_ON_THIS_SIDE"
        else:
            assert level["price"] is not None and level["distance_pct"] is not None


def test_the_previous_session_block_is_outside_the_level_book():
    """Ban B8: R2 measured this source at one reversal in twenty-seven, so it stays context."""
    run = built_run()
    view = S.structure_view(run, symbol=C.BTC, now_ms=run.rows[-1]["info_ts"])
    previous = view["prev_session"]
    assert previous["in_level_book"] is False
    assert "27" in previous["note"]
    if previous["high"] is not None:
        assert previous["high"] not in (view["row"]["published_prices"] or [])


@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT"])
def test_a_non_btc_symbol_gets_no_structure_and_no_btc_figure(symbol):
    run = built_run()
    view = S.structure_view(run, symbol=symbol, now_ms=run.rows[-1]["info_ts"])
    assert view["state"] == C.UNAVAILABLE
    assert view["reasons"] == [C.REASON_NOT_CALIBRATED]
    for key in ("series", "row", "levels", "events", "range", "prev_session"):
        assert view[key] is None, key
    # And the leak that would actually matter: no figure from the BTC run reaches this payload.
    # Checked by serialising the whole view and looking for the BTC close and level prices, rather
    # than by trusting the None checks above - a later refactor could fill one of those keys with a
    # "harmless" default and the None checks would be the only thing standing in the way.
    serialised = json.dumps(view, default=str)
    btc_row = run.rows[-1]
    for figure in [btc_row["close"], *(btc_row["published_prices"] or [])]:
        assert str(figure) not in serialised, figure
    assert view["symbol"] == symbol


def test_no_series_reads_unknown_rather_than_empty():
    view = S.structure_view(None, symbol=C.BTC, now_ms=1_791_000_000_000,
                            reasons=["FETCH_FAILED:/fapi/v1/klines:URLError"])
    assert view["state"] == C.UNKNOWN
    assert view["reasons"][0].startswith("FETCH_FAILED")
