"""Ledger reconstruction, reset handling, entry context and control sampling.

The reconstruction is the load-bearing part: if fees are attributed to the wrong trade, or a
reset top-up is counted as profit, every conclusion downstream is wrong in the direction the
operator already believes. Those two are tested against hand-built ledgers where the right answer
is arithmetic, and then against the real snapshot's own running totals.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.manual_r1 import audit as A
from app.crypto.research.manual_r1 import context as C
from app.crypto.research.manual_r1 import controls as CTRL
from app.crypto.research.manual_r1 import ledger as L
from app.crypto.research.manual_r1 import runner as R

SNAPSHOT = Path(__file__).resolve().parents[3] / "data/research/crypto/manual_r1/snapshot"
PACKAGE = Path(__file__).resolve().parents[2] / "app" / "crypto" / "research" / "manual_r1"


# --- synthetic ledgers, where the right answer is known ---------------------------------

def event(kind: str, ts: int, **fields) -> dict:
    return {"event_type": kind, "ts_ms": ts, **fields}


def simple_round_trip(*, entry_fee="1.0", exit_fee="2.0", gross="10.0",
                      funding=None, side="LONG") -> list[dict]:
    """The engine writes the FEE before the POSITION_OPEN it belongs to."""
    rows = [event(L.RUN_START, 0),
            event(L.FEE, 100, amount=entry_fee),
            event(L.OPEN, 100, signed_qty="1.0", avg_entry="100.0", leverage="10", equity="1000")]
    if funding is not None:
        rows.append(event(L.FUNDING, 150, amount_paid=funding))
    rows += [event(L.FEE, 200, amount=exit_fee),
             event(L.CLOSE, 200, side=side, closed_qty="1.0", entry_avg="100.0",
                   exit_price="110.0", gross_pnl=gross, fee=exit_fee, hold_ms=100,
                   leverage="10", equity="1009", opened_ts_ms=100)]
    return rows


def test_a_round_trip_is_one_trade():
    book = L.reconstruct(simple_round_trip())
    assert len(book.trades) == 1
    assert book.open_position is None


def test_the_entry_fee_is_attributed_not_lost():
    """A fee charged while flat belongs to the position about to open."""
    book = L.reconstruct(simple_round_trip(entry_fee="1.0", exit_fee="2.0"))
    trade = book.trades[0]
    assert trade.entry_fee == pytest.approx(1.0)
    assert trade.exit_fee == pytest.approx(2.0)
    assert trade.fees == pytest.approx(3.0)
    assert book.unattributed_fees == 0.0


def test_net_is_gross_minus_fees_and_funding():
    book = L.reconstruct(simple_round_trip(gross="10.0", entry_fee="1.0", exit_fee="2.0",
                                           funding="0.5"))
    trade = book.trades[0]
    assert trade.funding == pytest.approx(0.5)
    assert trade.net_pnl == pytest.approx(10.0 - 3.0 - 0.5)


def test_the_close_events_own_net_pnl_is_not_used():
    """`POSITION_CLOSE.net_pnl` omits the entry fee, so relying on it understates cost."""
    rows = simple_round_trip(entry_fee="5.0", exit_fee="2.0", gross="10.0")
    rows[-1]["net_pnl"] = "8.0"                 # what the ledger itself records
    trade = L.reconstruct(rows).trades[0]
    assert trade.net_pnl == pytest.approx(3.0)  # 10 - 5 - 2, not 8


def test_funding_received_is_a_negative_charge():
    book = L.reconstruct(simple_round_trip(gross="0.0", entry_fee="0", exit_fee="0",
                                           funding="-4.0"))
    assert book.trades[0].net_pnl == pytest.approx(4.0)


def test_a_position_left_open_is_not_a_trade():
    rows = [event(L.RUN_START, 0),
            event(L.FEE, 100, amount="1.0"),
            event(L.OPEN, 100, signed_qty="-0.5", avg_entry="100.0", leverage="10",
                  equity="1000")]
    book = L.reconstruct(rows)
    assert book.trades == []
    assert book.open_position["side"] == "SHORT"
    assert book.open_position["fees_so_far"] == pytest.approx(1.0)


def test_side_is_read_from_the_close_not_guessed():
    book = L.reconstruct(simple_round_trip(side="SHORT"))
    assert book.trades[0].side == "SHORT"


def test_an_increase_is_folded_into_one_trade():
    rows = [event(L.RUN_START, 0),
            event(L.FEE, 100, amount="1.0"),
            event(L.OPEN, 100, signed_qty="-1.0", avg_entry="100.0", leverage="10",
                  equity="1000"),
            event(L.FEE, 150, amount="1.0"),
            event(L.INCREASE, 150, signed_qty="-2.0", avg_entry="101.0"),
            event(L.FEE, 200, amount="2.0"),
            event(L.CLOSE, 200, side="SHORT", closed_qty="2.0", entry_avg="101.0",
                  exit_price="99.0", gross_pnl="4.0", fee="2.0", hold_ms=100,
                  leverage="10", equity="1000", opened_ts_ms=100)]
    book = L.reconstruct(rows)
    assert len(book.trades) == 1
    assert book.trades[0].increases == 1
    assert book.trades[0].fees == pytest.approx(4.0)


def test_a_liquidation_marks_the_trade():
    rows = simple_round_trip(gross="-500.0")
    rows.insert(-2, event(L.LIQUIDATION, 190, mark_price="50.0"))
    book = L.reconstruct(rows)
    assert book.liquidations == 1
    assert book.trades[0].liquidated is True


# --- resets ---------------------------------------------------------------------------------

def test_a_reset_opens_a_new_segment():
    rows = simple_round_trip()
    rows.append(event(L.RESET, 300, reason="USER_RESET", before_equity="94.0",
                      after_equity="7457.0", preserved_realized_pnl="-500.0"))
    rows += simple_round_trip()[1:]
    book = L.reconstruct(rows)
    assert len(book.resets) == 1
    assert book.current_segment == 1
    assert [t.segment for t in book.trades] == [0, 1]
    assert len(book.segment_trades(1)) == 1


def test_a_reset_top_up_is_recorded_and_is_not_profit():
    rows = simple_round_trip()
    rows.append(event(L.RESET, 300, reason="USER_RESET", before_equity="94.0",
                      after_equity="7457.0", preserved_realized_pnl="-500.0"))
    book = L.reconstruct(rows)
    reset = book.resets[0]
    assert reset["topped_up"] == pytest.approx(7363.0)
    assert reset["preserved_realized_pnl"] == pytest.approx(-500.0)
    # the top-up appears nowhere in trade PnL
    assert sum(t.net_pnl for t in book.trades) == pytest.approx(10.0 - 3.0)


def test_lifetime_and_segment_are_separate():
    rows = simple_round_trip(gross="-60.0")
    rows.append(event(L.RESET, 300, reason="USER_RESET", before_equity="0",
                      after_equity="1000", preserved_realized_pnl="-63"))
    rows += simple_round_trip(gross="30.0")[1:]
    book = L.reconstruct(rows)
    lifetime = A.summarise(book.trades)
    segment = A.summarise(book.segment_trades(book.current_segment))
    assert lifetime["trades"] == 2
    assert segment["trades"] == 1
    assert segment["net_pnl"] > 0 > lifetime["net_pnl"]


# --- metrics ---------------------------------------------------------------------------------

def test_profit_factor_and_drawdown():
    assert A.profit_factor([2.0, -1.0]) == pytest.approx(2.0)
    assert A.profit_factor([1.0, 1.0]) == "INF"
    assert A.profit_factor([]) == A.NOT_MEANINGFUL
    assert A.max_drawdown([100, 120, 90, 150]) == pytest.approx(0.25)


def test_concentration_refuses_shares_on_a_negative_total():
    book = L.reconstruct(simple_round_trip(gross="-20.0"))
    block = A.concentration(book.trades)
    assert block["meaningful"] is False
    assert block["top_1_share"] == A.NOT_MEANINGFUL
    assert isinstance(block["top_1"], float)


def test_concentration_reports_removal_effects():
    trades = L.reconstruct(simple_round_trip(gross="100.0")).trades
    trades += L.reconstruct(simple_round_trip(gross="1.0")).trades
    block = A.concentration(trades)
    assert block["net_excluding_top_1"] < block["total_net"]
    assert block["net_excluding_worst_1"] > block["total_net"]


def test_streaks_are_counted():
    trades = []
    for gross in ("-5", "-5", "-5", "20", "-5"):
        trades += L.reconstruct(simple_round_trip(gross=gross)).trades
    block = A.streaks(trades)
    assert block["max_consecutive_losses"] == 3
    assert block["max_consecutive_wins"] == 1


def test_leverage_is_separated_from_direction():
    trades = L.reconstruct(simple_round_trip(gross="10.0")).trades
    block = A.leverage_vs_direction(trades)
    row = block["per_trade"][0]
    assert row["price_move_bp"] == pytest.approx(trades[0].gross_bp)
    assert row["one_x_equivalent_net"] == pytest.approx(trades[0].net_pnl / 10)


# --- entry context, and the PIT rule ------------------------------------------------------------

def fake_tape(n: int = 4000, start: int = 1_000_000_000_000) -> C.Tape:
    ts = start + np.arange(n, dtype=np.int64) * 1000
    rng = np.random.default_rng(7)
    mark = 100_000.0 * np.exp(np.cumsum(rng.normal(0.0, 2e-5, n)))
    return C.Tape(ts_ms=ts, mark=mark, last=mark, bid=mark - 0.05, ask=mark + 0.05,
                  index=mark * 1.0001, funding=np.full(n, 1e-4))


def test_entry_features_read_nothing_at_or_after_the_entry():
    tape = fake_tape()
    ts = int(tape.ts_ms[3600])
    before = C.entry_features(tape, ts)
    tampered = C.Tape(ts_ms=tape.ts_ms, mark=tape.mark.copy(), last=tape.last,
                      bid=tape.bid, ask=tape.ask, index=tape.index, funding=tape.funding)
    tampered.mark[3601:] *= 3.0
    after = C.entry_features(tampered, ts)
    assert before == after


def test_truncating_the_tape_at_the_entry_changes_nothing():
    tape = fake_tape()
    ts = int(tape.ts_ms[3600])
    full = C.entry_features(tape, ts)
    cut = 3601
    short = C.Tape(ts_ms=tape.ts_ms[:cut], mark=tape.mark[:cut], last=tape.last[:cut],
                   bid=tape.bid[:cut], ask=tape.ask[:cut], index=tape.index[:cut],
                   funding=tape.funding[:cut])
    assert C.entry_features(short, ts) == full


def test_entry_features_are_none_without_history():
    tape = fake_tape(n=3)
    assert C.entry_features(tape, int(tape.ts_ms[0])) is None


def test_missing_lookback_becomes_none_not_a_guess():
    tape = fake_tape(n=100)
    features = C.entry_features(tape, int(tape.ts_ms[-1]))
    assert features["ret_3600s_bp"] is None          # the tape is only 100 seconds long
    assert features["ret_30s_bp"] is not None


def test_position_in_range_is_between_zero_and_one():
    tape = fake_tape()
    features = C.entry_features(tape, int(tape.ts_ms[3600]))
    for name in ("pos_in_range_300s", "pos_in_range_900s", "pos_in_range_3600s"):
        assert 0.0 <= features[name] <= 1.0


def test_outcome_path_is_kept_out_of_entry_features():
    tape = fake_tape()
    ts = int(tape.ts_ms[2000])
    entry = C.entry_features(tape, ts)
    outcome = C.outcome_path(tape, ts, "SHORT", float(tape.mark[2000]))
    assert not set(entry) & set(outcome)
    assert all(key.startswith("fwd_") for key in outcome)


def test_outcome_path_signs_follow_the_side():
    tape = fake_tape()
    ts = int(tape.ts_ms[2000])
    price = float(tape.mark[2000])
    long_path = C.outcome_path(tape, ts, "LONG", price)
    short_path = C.outcome_path(tape, ts, "SHORT", price)
    for key in long_path:
        if long_path[key] is not None and short_path[key] is not None:
            assert long_path[key] == pytest.approx(-short_path[key])


def test_load_tape_drops_duplicate_timestamps_and_sorts(tmp_path):
    """Two files can overlap, and a live tail can repeat a stamp; the merge has to be clean."""
    path = tmp_path / "tape.jsonl"
    rows = [{"kind": "MARKET", "payload": {"ts_ms": ts, "mark_price": str(price),
                                           "last_price": str(price), "index_price": str(price),
                                           "funding_rate": "0.0001",
                                           "bids": [["1", "1"]], "asks": [["2", "1"]]}}
            for ts, price in ((2000, 3.0), (1000, 1.0), (1000, 2.0))]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    tape = C.load_tape([path])
    assert list(tape.ts_ms) == [1000, 2000]
    assert tape.at_or_before(1500) == 0


# --- controls -----------------------------------------------------------------------------------

def test_controls_avoid_the_entries_and_any_open_position():
    tape = fake_tape(n=40_000)
    trades = L.reconstruct(simple_round_trip()).trades
    trades[0].opened_ts_ms = int(tape.ts_ms[20_000])
    trades[0].closed_ts_ms = int(tape.ts_ms[20_100])
    points = CTRL.sample(tape, trades, per_entry=20)
    assert points
    for point in points:
        assert abs(point.ts_ms - trades[0].opened_ts_ms) >= CTRL.MIN_SEPARATION_SEC * 1000
        assert not (trades[0].opened_ts_ms <= point.ts_ms <= trades[0].closed_ts_ms)


def test_control_sampling_is_reproducible():
    tape = fake_tape(n=40_000)
    trades = L.reconstruct(simple_round_trip()).trades
    trades[0].opened_ts_ms = int(tape.ts_ms[20_000])
    trades[0].closed_ts_ms = int(tape.ts_ms[20_100])
    first = CTRL.sample(tape, trades, per_entry=20)
    second = CTRL.sample(tape, trades, per_entry=20)
    assert [p.ts_ms for p in first] == [p.ts_ms for p in second]


def test_the_control_rule_is_stated_and_fixed():
    rule = CTRL.describe_rule()
    assert rule["fixed_before_any_comparison"] is True
    assert rule["excludes_moments_with_a_position_open"] is True
    assert rule["min_separation_from_any_manual_entry_sec"] == 600


# --- the real snapshot ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def snapshot_book():
    path = SNAPSHOT / "ledger.jsonl"
    if not path.exists():
        pytest.skip("operating snapshot is not present in this checkout")
    return L.reconstruct(L.load_events(path))


def test_the_rebuild_agrees_with_the_ledgers_own_totals(snapshot_book):
    events = L.load_events(SNAPSHOT / "ledger.jsonl")
    block = R._reconcile(events, snapshot_book.trades, snapshot_book)
    assert block["gross_matches"] is True
    assert block["fees_matches"] if "fees_matches" in block else block["fees_match"] is True
    assert block["funding_match"] is True


def test_no_fee_is_left_unattributed(snapshot_book):
    assert snapshot_book.unattributed_fees == 0.0


def test_the_snapshot_has_the_expected_shape(snapshot_book):
    assert len(snapshot_book.trades) == 17
    assert len(snapshot_book.resets) == 3
    assert snapshot_book.liquidations == 1
    assert snapshot_book.open_position is not None


def test_results_declare_the_verdict_and_the_open_position():
    path = Path(__file__).resolve().parents[3] / "data/research/crypto/manual_r1/results_v1.json"
    if not path.exists():
        pytest.skip("results not generated")
    results = json.loads(path.read_text())
    assert results["lifetime"]["net_pnl"] < 0
    assert results["open_position"]["note"].startswith("UNREALISED")
    assert results["accounting_reconciliation"]["gross_matches"] is True


# --- safety ------------------------------------------------------------------------------------------

def sources() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_package_issues_no_order_and_no_reset():
    # Reading the string "ACCOUNT_RESET" is how a reset event is recognised; *issuing* one
    # would go through `reset_account`. The ban targets the verbs, not the vocabulary.
    banned = ("submit_order", "PaperEngine(", "apply_market", "reset_account",
              "emergency_close", "set_leverage", "requests.post", "requests.get", "urlopen")
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} contains {token}"


def test_the_package_does_not_reach_the_server():
    banned = ("ssh", "scp", "paramiko", "223.130.151", "traderj", "subprocess")
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} contains {token}"


def test_the_package_only_reads_the_local_snapshot():
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "/root/" not in node.value, f"{path.name} names a server path"


def test_provenance_records_that_nothing_was_written():
    path = SNAPSHOT / "PROVENANCE.txt"
    if not path.exists():
        pytest.skip("snapshot not present")
    text = path.read_text()
    assert "No write, no order, no reset, no restart was issued on the server." in text
