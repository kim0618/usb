"""Tests for the BTC-VOL-A Deribit free-data audit.

The audit's whole value is that it reads a real option price at a real instant, so these protect
the things that would quietly corrupt that: a quote from after the decision, a stale book, a
strike or expiry chosen to flatter the result, and a verdict that does not come from the frozen
gates.
"""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.btc_vol_a import chain as CH
from app.crypto.research.btc_vol_a import contract as C
from app.crypto.research.btc_vol_a import runner as R

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS = REPO_ROOT / "data/research/crypto/btc_vol_a/results_v1.json"

MICROS = 1_000_000
HEADER = ["exchange", "symbol", "timestamp", "local_timestamp", "type", "strike_price",
          "expiration", "open_interest", "last_price", "bid_price", "bid_amount", "bid_iv",
          "ask_price", "ask_amount", "ask_iv", "mark_price", "mark_iv", "underlying_index",
          "underlying_price", "delta", "gamma", "vega", "theta", "rho"]


def _row(symbol: str, stamp_us: int, kind: str, strike: float, expiry_us: int, *,
         bid: float = 0.02, ask: float = 0.025, mark: float = 0.0225,
         bid_iv: float = 60.0, ask_iv: float = 70.0, mark_iv: float = 65.0,
         underlying: float = 30_000.0) -> list[str]:
    values = {"exchange": "deribit", "symbol": symbol, "timestamp": str(stamp_us),
              "local_timestamp": str(stamp_us), "type": kind, "strike_price": str(strike),
              "expiration": str(expiry_us), "open_interest": "1", "last_price": "0.02",
              "bid_price": str(bid), "bid_amount": "1", "bid_iv": str(bid_iv),
              "ask_price": str(ask), "ask_amount": "1", "ask_iv": str(ask_iv),
              "mark_price": str(mark), "mark_iv": str(mark_iv),
              "underlying_index": "SYN.BTC", "underlying_price": str(underlying),
              "delta": "0.5", "gamma": "0", "vega": "0", "theta": "0", "rho": "0"}
    return [values[name] for name in HEADER]


def _write(path: Path, rows: list[list[str]]) -> Path:
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


# --- contract -----------------------------------------------------------------------------

def test_the_contract_states_every_frozen_value():
    C.verify_bindings()


def test_the_contract_hash_matches_the_freeze_record():
    assert C.require_frozen() == C.sha256()


def test_an_edited_contract_is_refused():
    body = C.CONTRACT.read_text(encoding="utf-8").replace("| stale 허용치 | **300초** |",
                                                          "| stale 허용치 | **9999초** |")
    with pytest.raises(C.ContractMismatch):
        C.verify_bindings(body)


def test_the_freeze_record_declares_what_was_forbidden_before_it():
    payload = json.loads(Path(C.FREEZE).read_text())
    assert payload["frozen_before_any_option_price_was_read"] is True
    for banned in ("implied volatility", "premium", "PnL"):
        assert banned in payload["forbidden_before_freeze"]
    assert payload["orders"] == 0 and payload["api_keys"] == 0


def test_the_primary_threshold_matches_vol_p0s_high_state():
    # VOL-P0 defined its high state at 0.75; a different level would make the comparison
    # meaningless because the break-even figures were computed on that subset.
    assert C.PRIMARY_THRESHOLD == 0.75
    assert C.PRIMARY_COMBO == (720, 100)
    assert (720, 200) in C.EXCLUDED_COMBOS


def test_the_dataset_url_is_the_free_deribit_chain():
    url = C.DATASET_URL.format(year=2022, month=7, day=1)
    assert url == ("https://datasets.tardis.dev/v1/deribit/options_chain/"
                   "2022/07/01/OPTIONS.csv.gz")


# --- free dates ---------------------------------------------------------------------------

def test_free_dates_are_the_first_of_each_month_inside_the_p1_window():
    dates = R.free_dates()
    assert len(dates) == 57
    assert dates[0] == "2022-01-01"
    assert dates[-1] == "2026-09-01"
    assert all(day.endswith("-01") for day in dates)


# --- streaming and point in time ------------------------------------------------------------

def test_a_quote_after_the_decision_instant_is_never_yielded(tmp_path):
    instant = 1_000_000 * MICROS
    rows = [
        _row("BTC-1JUL22-30000-C", instant - 10 * MICROS, "call", 30000, instant + 10**10),
        _row("BTC-1JUL22-30000-C", instant + 1, "call", 30000, instant + 10**10),
        _row("BTC-1JUL22-30000-C", instant + 60 * MICROS, "call", 30000, instant + 10**10),
    ]
    path = _write(tmp_path / "c.csv.gz", rows)
    got = list(CH.stream(path, wanted_us=[instant], tolerance_us=300 * MICROS))
    assert len(got) == 1
    assert got[0].timestamp_us == instant - 10 * MICROS


def test_a_quote_older_than_the_tolerance_is_excluded(tmp_path):
    instant = 1_000_000 * MICROS
    rows = [_row("BTC-1JUL22-30000-C", instant - 400 * MICROS, "call", 30000,
                 instant + 10**10)]
    path = _write(tmp_path / "c.csv.gz", rows)
    assert list(CH.stream(path, wanted_us=[instant], tolerance_us=300 * MICROS)) == []


def test_non_btc_instruments_are_skipped(tmp_path):
    instant = 1_000_000 * MICROS
    rows = [_row("ETH-1JUL22-1900-C", instant - 10, "call", 1900, instant + 10**10),
            _row("BTC-1JUL22-30000-C", instant - 10, "call", 30000, instant + 10**10)]
    path = _write(tmp_path / "c.csv.gz", rows)
    got = list(CH.stream(path, wanted_us=[instant], tolerance_us=300 * MICROS))
    assert [q.symbol for q in got] == ["BTC-1JUL22-30000-C"]


def test_the_freshest_quote_per_instrument_wins(tmp_path):
    instant = 1_000_000 * MICROS
    rows = [_row("BTC-1JUL22-30000-C", instant - 200 * MICROS, "call", 30000,
                 instant + 10**10, ask=0.05),
            _row("BTC-1JUL22-30000-C", instant - 5 * MICROS, "call", 30000,
                 instant + 10**10, ask=0.03)]
    path = _write(tmp_path / "c.csv.gz", rows)
    book = CH.latest_before(path, [instant], 300 * MICROS)
    assert book[instant]["BTC-1JUL22-30000-C"].ask == 0.03


def test_quote_usability_rejects_missing_and_crossed_markets():
    base = dict(symbol="BTC-X", timestamp_us=0, option_type="call", strike=30000.0,
                expiration_us=1, bid_iv=1.0, ask_iv=1.0, mark_iv=1.0, mark=0.02)
    assert CH.Quote(**base, bid=0.02, ask=0.03, underlying=30000.0).usable
    assert not CH.Quote(**base, bid=0.0, ask=0.03, underlying=30000.0).usable
    assert not CH.Quote(**base, bid=0.02, ask=0.0, underlying=30000.0).usable
    assert not CH.Quote(**base, bid=0.05, ask=0.03, underlying=30000.0).usable   # crossed
    assert not CH.Quote(**base, bid=0.02, ask=0.03, underlying=0.0).usable


# --- expiry and strike selection ------------------------------------------------------------

def _book(instant_us: int, expiries: list[int], strikes: list[float],
          underlying: float = 30_000.0) -> dict[str, CH.Quote]:
    out: dict[str, CH.Quote] = {}
    for expiry in expiries:
        for strike in strikes:
            for kind in ("call", "put"):
                symbol = f"BTC-{expiry}-{strike:.0f}-{kind[0].upper()}"
                out[symbol] = CH.Quote(symbol=symbol, timestamp_us=instant_us - 1,
                                       option_type=kind, strike=strike, expiration_us=expiry,
                                       bid=0.02, ask=0.025, mark=0.0225, bid_iv=60.0,
                                       ask_iv=70.0, mark_iv=65.0, underlying=underlying)
    return out


def test_expiry_must_clear_the_horizon_plus_the_buffer():
    instant = 1_000_000 * MICROS
    horizon = 720
    too_close = instant + (horizon + C.EXPIRY_BUFFER_MINUTES - 1) * 60 * MICROS
    just_right = instant + (horizon + C.EXPIRY_BUFFER_MINUTES) * 60 * MICROS
    far = instant + (horizon + 2000) * 60 * MICROS
    book = _book(instant, [too_close, just_right, far], [30000.0])
    assert R._pick_expiry(book, instant, horizon) == just_right


def test_no_expiry_clearing_the_buffer_returns_none():
    instant = 1_000_000 * MICROS
    book = _book(instant, [instant + 60 * MICROS], [30000.0])
    assert R._pick_expiry(book, instant, 720) is None


def test_the_atm_strike_is_the_one_nearest_the_underlying():
    instant = 1_000_000 * MICROS
    expiry = instant + 10**10
    book = _book(instant, [expiry], [28000.0, 30500.0, 33000.0], underlying=30_000.0)
    call, put = R._pick_atm(book, expiry)
    assert call.strike == 30500.0 and put.strike == 30500.0


def test_a_strike_without_both_legs_is_not_selected():
    instant = 1_000_000 * MICROS
    expiry = instant + 10**10
    book = _book(instant, [expiry], [30000.0])
    del book["BTC-{}-30000-P".format(expiry)]
    assert R._pick_atm(book, expiry) is None


def test_only_the_chosen_expiry_is_considered():
    instant = 1_000_000 * MICROS
    near, far = instant + 10**10, instant + 2 * 10**10
    book = _book(instant, [near, far], [30000.0])
    call, _ = R._pick_atm(book, far)
    assert call.expiration_us == far


# --- realised move over the option's own life ------------------------------------------------

def _grid(n: int = 3000) -> dict[str, np.ndarray]:
    ts = 1_656_633_600_000 + np.arange(n, dtype=np.int64) * 60_000
    close = np.full(n, 100.0)
    close[600:] = 103.0
    return {"ts": ts, "close": close, "high": close * 1.01, "low": close * 0.99}


def test_the_realised_move_is_measured_over_the_options_own_tenor():
    grid = _grid()
    got = R._endpoint_move(grid, int(grid["ts"][0]), 600)
    assert got["endpoint_abs"] == pytest.approx(0.03)


def test_the_realised_window_starts_after_the_decision_bar():
    grid = _grid()
    grid["high"][0] = 1000.0            # a spike on the decision bar itself
    got = R._endpoint_move(grid, int(grid["ts"][0]), 600)
    assert got["max_excursion"] < 0.2


def test_a_window_running_past_the_grid_is_not_measurable():
    grid = _grid(100)
    assert R._endpoint_move(grid, int(grid["ts"][0]), 5000) is None


# --- measurement ------------------------------------------------------------------------------

def test_a_full_measurement_computes_the_frozen_quantities():
    grid = _grid()
    instant_ms = int(grid["ts"][0])
    instant_us = instant_ms * 1000
    expiry = instant_us + 900 * 60 * MICROS
    book = _book(instant_us, [expiry], [100.0], underlying=100.0)
    event = {"ts_ms": instant_ms, "day": "2022-07-01", "utc": "x", "horizon_minutes": 720,
             "threshold_bp": 100, "p_large_move": 0.8, "p_up": 0.4, "p_down": 0.8}
    got = R.measure(event, book, grid)
    assert got["status"] == "OK"
    assert got["straddle_ask"] == pytest.approx(0.05)          # 0.025 + 0.025
    assert got["straddle_mid"] == pytest.approx(0.045)
    assert got["atm_mark_iv"] == pytest.approx(0.65)           # percent to fraction
    assert got["atm_ask_iv"] == pytest.approx(0.70)
    assert got["check_b_edge"] == pytest.approx(got["realized_endpoint_abs"] - 0.05)


def test_an_empty_book_is_not_measurable():
    got = R.measure({"ts_ms": 1_656_633_600_000, "horizon_minutes": 720, "threshold_bp": 100},
                    {}, _grid())
    assert got["status"] == C.NOT_MEASURABLE


def test_implied_volatility_is_converted_from_percent():
    # Tardis reports 65.0 for 65 percent; leaving it unscaled would put the break-even
    # comparison out by a factor of a hundred.
    grid = _grid()
    instant_ms = int(grid["ts"][0])
    book = _book(instant_ms * 1000, [instant_ms * 1000 + 900 * 60 * MICROS], [100.0],
                 underlying=100.0)
    got = R.measure({"ts_ms": instant_ms, "horizon_minutes": 720, "threshold_bp": 100},
                    book, grid)
    assert 0.1 < got["atm_mark_iv"] < 5.0


# --- verdict gates ----------------------------------------------------------------------------

def _measured(n: int, edge: float, excursion_gap: float) -> list[dict]:
    return [{"status": "OK", "check_b_edge": edge, "excursion_minus_ask": excursion_gap}
            for _ in range(n)]


def test_too_few_measurable_events_is_inconclusive():
    assert R.verdict(_measured(4, 0.05, 0.05), 4)["verdict"] == C.INCONCLUSIVE


def test_mostly_unmeasurable_is_inconclusive():
    measured = _measured(6, 0.05, 0.05) + [{"status": C.NOT_MEASURABLE, "reason": "x"}] * 20
    assert R.verdict(measured, 26)["verdict"] == C.INCONCLUSIVE


def test_sample_checks_run_before_the_economics():
    assert R.verdict(_measured(2, 9.0, 9.0), 2)["verdict"] == C.INCONCLUSIVE


def test_a_negative_edge_confirms_the_earlier_finding():
    out = R.verdict(_measured(10, -0.01, 0.02), 10)
    assert out["verdict"] == C.CONFIRMS_UNPROMISING


def test_a_zero_edge_confirms_rather_than_passes():
    assert R.verdict(_measured(10, 0.0, 0.05), 10)["verdict"] == C.CONFIRMS_UNPROMISING


def test_early_exit_needs_both_conditions():
    assert R.verdict(_measured(10, 0.01, 0.02), 10)["verdict"] == C.EARLY_EXIT_WORTH_STUDYING
    # A positive edge whose excursion does not clear the ask is not enough.
    assert R.verdict(_measured(10, 0.01, -0.02), 10)["verdict"] == C.CONFIRMS_UNPROMISING


# --- scope --------------------------------------------------------------------------------

PACKAGE = Path(R.__file__).parent


def _code_only(path: Path) -> str:
    import io
    import tokenize

    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return " ".join(kept)


def test_nothing_reaches_the_trading_engine_or_other_studies():
    banned = ("crypto.paper", "crypto.terminal", "submit_order", "liquidation_forward",
              "manual_r1", "manual_r2", "btc_p1_forward", "btc_vol_p0")
    for path in sorted(PACKAGE.glob("*.py")):
        code = _code_only(path)
        for needle in banned:
            assert needle not in code, f"{path.name} references {needle}"


def test_no_early_exit_is_computed():
    # The contract forbids it outright; an exit optimised after seeing the data is mining.
    banned = ("exit_after", "best_exit", "optimal_exit", "exit_minutes")
    for path in sorted(PACKAGE.glob("*.py")):
        code = _code_only(path)
        for needle in banned:
            assert needle not in code, f"{path.name} references {needle}"


def test_no_api_key_is_used():
    code = _code_only(PACKAGE / "chain.py")
    for banned in ("api_key", "apiKey", "Authorization", "Bearer"):
        assert banned not in code


@pytest.mark.skipif(not RESULTS.exists(), reason="audit not run")
def test_the_results_declare_the_audit_touched_nothing():
    payload = json.loads(RESULTS.read_text())
    assert payload["orders"] == 0
    assert payload["api_keys"] == 0
    assert payload["data_purchased"] is False
    assert payload["deployed"] is False
    assert payload["early_exit_computed"] is False


@pytest.mark.skipif(not RESULTS.exists(), reason="audit not run")
def test_every_measured_quote_predates_its_decision():
    payload = json.loads(RESULTS.read_text())
    for levels in payload["combos"].values():
        for entry in levels.values():
            for row in entry.get("measured", []):
                if row["status"] != "OK":
                    continue
                assert 0 <= row["quote_staleness_seconds"] <= C.STALE_TOLERANCE_SECONDS


@pytest.mark.skipif(not RESULTS.exists(), reason="audit not run")
def test_every_measured_expiry_clears_the_buffer():
    payload = json.loads(RESULTS.read_text())
    for levels in payload["combos"].values():
        for entry in levels.values():
            for row in entry.get("measured", []):
                if row["status"] != "OK":
                    continue
                assert row["time_to_expiry_minutes"] >= \
                    row["horizon_minutes"] + C.EXPIRY_BUFFER_MINUTES
