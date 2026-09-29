"""Golden snapshots on real D2 data, and the PIT properties of the loader that feeds them.

The snapshots pin eight decisions taken from the canonical BTCUSDT series: raw inputs, feature
values, bucket labels, category scores and the decision. They contain no forward return and no
PnL, so a snapshot can only break if the engine's reading of the contract changed.

These tests need the D2 grid, which is gitignored runtime data. Where it is absent they skip
rather than pass quietly.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.d6 import data as D
from app.crypto.research.d6 import runner as R
from app.crypto.research.d6.contract import load as load_contract

GOLDEN = (Path(__file__).resolve().parents[3]
          / "data/research/crypto/d6/d6b_golden_snapshots_v1.json")
GRID_CACHE = (Path(__file__).resolve().parents[3]
              / "data/runtime/crypto/d5/grid_v1.npz")


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN.exists():
        pytest.skip("golden snapshots not generated")
    return json.loads(GOLDEN.read_text())


@pytest.fixture(scope="module")
def grid():
    if not GRID_CACHE.exists():
        pytest.skip("D2 research grid is not present in this checkout")
    return D.load_inputs()


@pytest.fixture(scope="module")
def contract():
    return load_contract()


# --- the snapshots themselves ----------------------------------------------------------------

def test_golden_file_declares_what_it_does_not_contain(golden):
    assert golden["record"] == "CRYPTO_D6_B_GOLDEN_SNAPSHOTS_V1"
    assert "No forward return, no PnL" in golden["declaration"]
    assert len(golden["decisions"]) >= 5


def test_golden_file_matches_the_frozen_contract(golden, contract):
    assert golden["contract_sha256"] == contract.sha256
    for row in golden["decisions"]:
        assert row["contract_hash"] == contract.sha256


def test_every_golden_decision_reproduces_exactly(golden, grid, contract):
    """Compared through JSON, because that is the form the snapshot was stored in.

    A tuple in the engine and a list in the file are the same snapshot; round-tripping both sides
    keeps the comparison about values rather than about container types.
    """
    for row in golden["decisions"]:
        result = R.decide_at(grid, contract, R.parse_ts(row["golden_label"]))
        actual = json.loads(result.to_json())
        expected = {k: v for k, v in row.items() if k != "golden_label"}
        assert actual == expected, row["golden_label"]


def test_golden_snapshots_cover_both_outcomes(golden):
    decisions = {row["decision"] for row in golden["decisions"]}
    assert decisions == {"LONG", "HOLD"}


def test_golden_snapshots_cover_the_interesting_hold_reasons(golden):
    """A threshold miss, a mandatory-minimum miss and a low-volatility block are all represented."""
    reasons = {code for row in golden["decisions"] for code in row["reason_codes"]}
    assert "SCORE_BELOW_THRESHOLD" in reasons
    assert "M1_MARKET_STRUCTURE_BELOW_MIN" in reasons
    assert "H5_VOL_LOW" in reasons


def test_a_score_of_fifty_can_still_be_a_hold(golden):
    """2022-11-09 scores exactly the threshold but has no basis discount, so M1 blocks it.

    This is the case that shows the mandatory minimums are doing work the total cannot do.
    """
    row = next(r for r in golden["decisions"] if r["long_score"] == 50)
    assert row["decision"] == "HOLD"
    assert "M1_MARKET_STRUCTURE_BELOW_MIN" in row["reason_codes"]


def test_no_golden_row_carries_a_performance_field(golden):
    """The H10 guard reports a supplied daily figure, so `pnl` appears; a computed one would not.

    What must be absent is any measurement of how the trade went: a realised return, a factor, a
    drawdown, an equity path.
    """
    banned = ("profit_factor", "win_rate", "drawdown", "equity_curve", "forward_return",
              "trade_pnl", "gross_pnl", "net_pnl", "realized_return")
    text = json.dumps(golden["decisions"]).lower()
    for word in banned:
        assert word not in text, word
    for row in golden["decisions"]:
        guard = next(f for f in row["hard_filters"] if f["id"] == "H10")
        assert guard["observed"]["day_realized_pnl_pct"] == 0.0   # the supplied input, unused


def test_short_is_disabled_in_every_golden_row(golden):
    for row in golden["decisions"]:
        assert row["short_score"] is None
        assert row["short_state"] == "SHORT_DISABLED_FOR_V1"


def test_realtime_only_filters_are_skipped_in_every_golden_row(golden):
    for row in golden["decisions"]:
        skipped = {f["id"] for f in row["hard_filters"] if f["status"] == "NOT_EVALUATED"}
        assert skipped == {"H6", "H7"}


# --- loader PIT properties -------------------------------------------------------------------

def test_oi_record_timestamps_respect_the_five_minute_delay(grid):
    """A record may only be in force at least one full delay after its own stamp."""
    known = grid["oi_record_ts"] > 0
    sample = np.where(known)[0][::100_000]
    lag = (grid["ts"][sample] + 60_000) - grid["oi_record_ts"][sample]
    assert lag.min() >= D.OI_DELAY_MS


def test_oi_record_timestamps_are_monotonic(grid):
    known = grid["oi_record_ts"][grid["oi_record_ts"] > 0]
    assert np.all(np.diff(known) >= 0)


def test_next_funding_is_always_in_the_future_of_the_bar_close(grid):
    known = grid["next_funding_ts"] > 0
    sample = np.where(known)[0][::100_000]
    assert np.all(grid["next_funding_ts"][sample] > grid["ts"][sample] + 60_000)


def test_next_funding_is_derived_from_past_settlements_only():
    """Two *past* settlements give the interval; the answer is stepped forward from the later one.

    The table also holds 16:00 and 24:00, and the function must not look at them: at 12:59 the
    answer has to come from the 00:00 and 08:00 pair, which happens to give the right 16:00.
    """
    settlements = np.array([0, 8, 16, 24], dtype=np.int64) * 3_600_000
    ts = np.array([1, 5, 9, 13], dtype=np.int64) * 3_600_000 - 60_000  # 00:59, 04:59, 08:59, 12:59
    out = D.next_funding_timestamps(ts, settlements)
    assert out[0] == -1                    # 00:59: only 00:00 is past, no interval yet
    assert out[1] == -1                    # 04:59: still only 00:00 is past
    assert out[2] == 16 * 3_600_000        # 08:59: 00:00 and 08:00 past -> 8h -> 16:00
    assert out[3] == 16 * 3_600_000        # 12:59: same pair, still 16:00


def test_next_funding_ignores_settlements_it_should_not_have_seen():
    """Deleting the future rows must not change any answer, which is the PIT property itself."""
    full = np.array([0, 8, 16, 24], dtype=np.int64) * 3_600_000
    past_only = np.array([0, 8], dtype=np.int64) * 3_600_000
    ts = np.array([9, 13], dtype=np.int64) * 3_600_000 - 60_000
    np.testing.assert_array_equal(D.next_funding_timestamps(ts, full),
                                  D.next_funding_timestamps(ts, past_only))


def test_next_funding_steps_forward_over_a_missed_settlement():
    settlements = np.array([0, 8], dtype=np.int64) * 3_600_000
    ts = np.array([30], dtype=np.int64) * 3_600_000
    out = D.next_funding_timestamps(ts, settlements)
    assert out[0] == 32 * 3_600_000


def test_window_at_ends_on_the_requested_bar(grid):
    index = 1_000_000
    win = D.window_at(grid, index, 5_000)
    assert win.decision_ts_ms == int(grid["ts"][index])
    assert len(win.ts_ms) == 5_000
    assert win.ts_ms[-1] == grid["ts"][index]


def test_window_at_refuses_an_index_outside_the_grid(grid):
    with pytest.raises(IndexError):
        D.window_at(grid, len(grid["ts"]), 10)


def test_index_of_rejects_a_timestamp_off_the_grid(grid):
    with pytest.raises(KeyError):
        D.index_of(grid, int(grid["ts"][10]) + 1)


def test_market_at_uses_the_decision_time_price_not_the_next_open(grid):
    index = 1_000_000
    market = D.market_at(grid, index)
    assert market.entry_reference_price == float(grid["close"][index])
    assert market.now_ms == int(grid["ts"][index]) + 60_000


def test_the_runner_lookback_covers_both_the_volatility_and_the_bucket_window():
    assert R.LOOKBACK_BARS >= 30 * 1440 + 1441
