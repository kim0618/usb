from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.backtest.strategy_e_d6 import dataset as DS
from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6 import run as R
from app.backtest.strategy_e_d6.replay import (
    GROSS, PRIMARY, SCENARIOS, SessionFrame, evaluate_signals, exact_bars, replay_session,
)
from app.market.calendar import MarketCalendar
from app.strategy_e.execution import EntryBar
from app.strategy_e.signal import SignalContractError, SignalFrame, evaluate_h5_signal


CALENDAR = MarketCalendar("America/New_York")
SESSION = date(2025, 1, 6)
SOURCE = "synthetic-source-digest"


def _features(n: int, h5: list[bool]) -> dict[str, np.ndarray]:
    base = {name: np.full(n, 1.0) for name in SEALED_FEATURES}
    base["premarket_gap"] = np.array([0.02 if keep else -0.01 for keep in h5])
    base["premarket_rvol"] = np.full(n, 4.0)
    base["position_in_premarket_range"] = np.full(n, 0.9)
    base["return_0900_0925"] = np.full(n, 0.01)
    return base


def _frame(symbols: list[str], h5: list[bool], session: date = SESSION) -> SessionFrame:
    return SessionFrame(session, tuple(symbols), _features(len(symbols), h5),
                        {s: {"close_price": 50.0, "previous_day_dollar_volume": 3e7,
                             "R_5m": 0.001, "R_5m_strict": 0.001} for s in symbols})


def _bar(symbol: str, label: str, *, open_: float = 100.0, close: float = 100.0,
         session: date = SESSION) -> EntryBar:
    return EntryBar(symbol=symbol, session_date=session, bar_start_et=label, open=open_,
                    high=max(open_, close), low=min(open_, close), close=close, volume=1000.0)


def _bars(symbol: str, entry: float | None, exit_: float | None,
          session: date = SESSION) -> dict[str, list[EntryBar]]:
    return {"09:30": [] if entry is None else [_bar(symbol, "09:30", open_=entry, session=session)],
            "09:34": [] if exit_ is None else [_bar(symbol, "09:34", close=exit_, session=session)]}


def _replay(frame: SessionFrame, bars: dict) -> dict:
    [signal] = evaluate_signals([frame], SOURCE)
    return replay_session(signal, frame, bars, CALENDAR)


# 1. frozen checksum verification ---------------------------------------------------------------

def test_frozen_checksums_load_through_every_fail_closed_loader() -> None:
    found = R.frozen_checksums()
    assert found["e_d6_backtest_rules"] == R.RULES_CANONICAL_SHA256
    assert found["e_d5_risk_rules"] == (
        "2e1e796d7e3563da4b1fd4d4a007314b49d3c3ac9a55e63519076b8ac8d104ee")


def test_tampered_protocol_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(R.RULES_PATH.read_text(encoding="utf-8"))
    payload["costs"]["primary_scenario"] = "COST_05BP"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(R.PreflightError, match="digest mismatch"):
        R.load_backtest_rules(path)


# 2. full provenance chain ------------------------------------------------------------------------

def test_every_chain_commit_is_an_ancestor_of_head() -> None:
    assert all(R.chain_ancestry().values())


def test_development_manifest_reproduces_the_bound_e1_digest(tmp_path: Path) -> None:
    manifest = DS.load_manifest()
    assert manifest.digest == DS.DEVELOPMENT_TAPE_DIGEST
    assert manifest.digest.startswith("d12ff28a98cf9cc6")
    assert (len(manifest.files), len(manifest.symbols)) == (2152, 1902)
    payload = json.loads(DS.MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["files"][0]["size"] += 1
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DS.DatasetIntegrityError):
        DS.load_manifest(path)


def test_view_exposes_only_bound_files(tmp_path: Path) -> None:
    root, view = tmp_path / "ws", tmp_path / "view"
    folder = root / M.RAW_DIR / "AAA"
    folder.mkdir(parents=True)
    (folder / "AAA_2026-04-20_2026-09-16.p01.json.gz").write_bytes(b"x" * 10)
    (folder / "AAA_2024-09-25_2024-12-04.p01.json.gz").write_bytes(b"y" * 7)   # added later
    files = (("AAA", "AAA_2026-04-20_2026-09-16.p01.json.gz", 10),)
    manifest = DS.TapeManifest(DS.manifest_digest(files), files)
    info = DS.build_view(root, manifest, view, {})
    assert info["tape_files"] == 1
    assert sorted(p.name for p in (view / M.RAW_DIR / "AAA").iterdir()) == [files[0][1]]
    assert DS.verify_view(view, manifest)
    (folder / files[0][1]).write_bytes(b"x" * 11)
    assert not DS.verify_view(view, manifest)


# 3. deterministic H5 replay --------------------------------------------------------------------

def test_h5_replay_is_deterministic() -> None:
    frame = _frame(["AAA", "BBB", "CCC"], [True, False, True])
    first, second = evaluate_signals([frame], SOURCE), evaluate_signals([frame], SOURCE)
    assert first == second
    assert first[0].candidate_symbols == ("AAA", "CCC")


# 4-5. candidate selection and no replacement ------------------------------------------------------

def test_first_three_candidates_in_symbol_order_are_selected() -> None:
    symbols = ["EEE", "AAA", "DDD", "BBB", "CCC"]
    frame = _frame(sorted(symbols), [True] * 5)
    bars = {s: _bars(s, 100.0, 101.0) for s in symbols}
    out = _replay(frame, bars)
    chosen = [r["symbol"] for r in out["records"] if r["selected"]]
    skipped = [r["symbol"] for r in out["records"] if not r["selected"]]
    assert chosen == ["AAA", "BBB", "CCC"] and skipped == ["DDD", "EEE"]
    assert all(r["entry_status"] == "NOT_SELECTED_CAPACITY" for r in out["records"]
               if not r["selected"])


def test_invalid_selected_candidate_is_not_replaced() -> None:
    frame = _frame(["AAA", "BBB", "CCC", "DDD"], [True] * 4)
    bars = {s: _bars(s, 100.0, 101.0) for s in ("AAA", "BBB", "CCC", "DDD")}
    bars["BBB"] = _bars("BBB", None, 101.0)            # no 09:30 bar
    out = _replay(frame, bars)
    by = {r["symbol"]: r for r in out["records"]}
    assert by["BBB"]["entry_status"] == "NO_TRADE_MISSING_ENTRY_BAR"
    assert not by["DDD"]["selected"] and not by["DDD"]["standard_pnl"]
    assert by["AAA"]["weight"] == by["CCC"]["weight"] == "1/2"


# 6-7. entry and exit mapping ---------------------------------------------------------------------

def test_entry_is_exact_0930_open_and_exit_exact_0934_close() -> None:
    frame = _frame(["AAA"], [True])
    bars = {"AAA": {"09:30": [_bar("AAA", "09:30", open_=50.0, close=99.0)],
                    "09:34": [_bar("AAA", "09:34", open_=77.0, close=51.0)]}}
    [record] = _replay(frame, bars)["records"]
    assert (record["entry_price"], record["exit_price"]) == (50.0, 51.0)
    assert record[GROSS] == Decimal("51.0") / Decimal("50.0") - 1


def test_missing_0934_bar_is_unresolved_without_fallback() -> None:
    frame = _frame(["AAA"], [True])
    bars = {"AAA": {"09:30": [_bar("AAA", "09:30", open_=50.0)],
                    "09:34": []}}
    out = _replay(frame, bars)
    [record] = out["records"]
    assert record["exit_status"] == "INVALID_EXIT"
    assert record["exit_reason"] == "NO_TRADE_MISSING_EXIT_BAR"
    assert record["risk_status"] == "UNRESOLVED_EXIT"
    assert record["skip_reason"] == "INVALID_FOR_STANDARD_PNL"
    assert not record["standard_pnl"] and GROSS not in record
    assert all(value == 0 for value in out["returns"].values()) and not out["active"]


def test_exact_bars_takes_only_0930_and_0934_and_keeps_duplicates() -> None:
    day = M.ordinal(SESSION)
    minutes = np.array([569, 570, 570, 571, 573, 574, 575], dtype=np.int32)
    n = minutes.size
    values = np.arange(1.0, n + 1.0)
    tape = M.SymbolTape(symbol="AAA", et_day=np.full(n, day), minute=minutes, open=values,
                        high=values, low=values, close=values, volume=values,
                        vwap=np.full(n, np.nan), sources={}, overlap_sessions=0)
    found = exact_bars(tape, [SESSION, date(2025, 1, 7)])
    assert [b.open for b in found[SESSION]["09:30"]] == [2.0, 3.0]
    assert [b.close for b in found[SESSION]["09:34"]] == [6.0]
    assert found[date(2025, 1, 7)] == {"09:30": [], "09:34": []}


# 8-11. costs, sizing, invalid exclusion, daily aggregation --------------------------------------------

def test_cost_scenarios_subtract_total_round_trip_once() -> None:
    frame = _frame(["AAA"], [True])
    [record] = _replay(frame, {"AAA": _bars("AAA", 100.0, 101.0)})["records"]
    for name, bp in (("COST_05BP", 5), ("COST_10BP", 10), ("COST_15BP", 15), ("COST_20BP", 20)):
        assert record[name] == record[GROSS] - Decimal(bp) / Decimal(10000)


@pytest.mark.parametrize("count", (1, 2, 3))
def test_equal_weights_and_session_return_is_weighted_sum(count: int) -> None:
    symbols = ["AAA", "BBB", "CCC"][:count]
    closes = [101.0, 99.0, 102.0][:count]
    frame = _frame(symbols, [True] * count)
    out = _replay(frame, {s: _bars(s, 100.0, c) for s, c in zip(symbols, closes)})
    assert {r["weight"] for r in out["records"]} == {f"1/{count}"}
    for name in SCENARIOS:
        expected = sum(Decimal(1) / Decimal(count) * r[name] for r in out["records"])
        assert out["returns"][name] == expected
    assert out["exposure"] == "1"


def test_unresolved_exit_is_excluded_and_weights_renormalize() -> None:
    frame = _frame(["AAA", "BBB", "CCC"], [True] * 3)
    bars = {"AAA": _bars("AAA", 100.0, 102.0), "BBB": _bars("BBB", 100.0, None),
            "CCC": _bars("CCC", 100.0, 98.0)}
    out = _replay(frame, bars)
    standard = [r for r in out["records"] if r["standard_pnl"]]
    assert [r["symbol"] for r in standard] == ["AAA", "CCC"]
    assert out["returns"][GROSS] == Decimal("0.5") * (Decimal("0.02") + Decimal("-0.02"))


# 12. no-trade days ------------------------------------------------------------------------------

def _two_session_run():
    first = _frame(["AAA"], [True], date(2025, 1, 6))
    second = _frame(["AAA"], [False], date(2025, 1, 8))
    frames = [first, second]
    signals = evaluate_signals(frames, SOURCE)
    bars = {"AAA": {"2025-01-06": _bars("AAA", 100.0, 101.0, date(2025, 1, 6))}}
    replayed = R.replay_all(frames, signals, bars, CALENDAR)
    grid = [date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8),
            date(2025, 1, 10)]
    sessions = R.timeline(grid, frames, CALENDAR)
    return replayed, sessions, frames


def test_no_trade_sessions_stay_in_the_timeline_as_zero() -> None:
    replayed, sessions, _ = _two_session_run()
    assert sessions == ["2025-01-06", "2025-01-07", "2025-01-08"]
    out = R.evaluate(replayed, sessions, eligible_rows=2, integrity=True)
    gross = out["scenarios"][GROSS]
    assert gross["sessions"] == 3 and gross["active_sessions"] == 1
    assert gross["all_session_mean"] == pytest.approx(0.01 / 3)
    assert gross["active_session_mean"] == pytest.approx(0.01)
    assert gross["mean"] == pytest.approx(0.01)


# 13-14. bootstrap and blocks ---------------------------------------------------------------------

def test_bootstrap_is_deterministic_under_the_frozen_seed() -> None:
    values = np.random.default_rng(1).normal(0.0005, 0.01, 300)
    first, second = X.bootstrap_mean_ci(values), X.bootstrap_mean_ci(values)
    assert first == second and first["seed"] == 20260921 and first["replicates"] == 10000
    assert X.bootstrap_mean_ci(values, seed=1) != first
    assert first["ci_low"] < first["mean"] < first["ci_high"]


def test_chronological_blocks_are_contiguous_and_equal_count() -> None:
    sessions = [f"2025-01-{d:02d}" for d in range(1, 11)]
    blocks = X.chronological_blocks(list(reversed(sessions)))
    assert [len(b) for b in blocks] == [3, 3, 2, 2]
    assert sum(blocks, []) == sessions


# 15-16. funnel and monthly aggregation -----------------------------------------------------------------

def test_funnel_counts_every_stage() -> None:
    frame = _frame(["AAA", "BBB", "CCC", "DDD", "EEE"], [True, True, True, True, False])
    bars = {"AAA": _bars("AAA", 100.0, 101.0), "BBB": _bars("BBB", None, 101.0),
            "CCC": _bars("CCC", 100.0, None), "DDD": _bars("DDD", 100.0, 101.0)}
    flow = R.funnel(5, [_replay(frame, bars)])
    assert flow["eligible_universe_rows"]["count"] == 5
    assert flow["H5_candidates"]["count"] == 4
    assert flow["selected_candidates"]["count"] == 3
    assert flow["capacity_skipped"]["count"] == 1
    assert flow["valid_entries"]["count"] == 2
    assert flow["invalid_entries"]["reasons"] == {"NO_TRADE_MISSING_ENTRY_BAR": 1}
    assert flow["valid_exact_exits"]["count"] == 1
    assert flow["unresolved_exits"]["reasons"] == {"NO_TRADE_MISSING_EXIT_BAR": 1}
    assert flow["standard_pnl_trades"]["count"] == 1
    assert flow["standard_pnl_coverage"] == 0.5


def test_monthly_and_quarterly_tables_compound_every_session() -> None:
    sessions = ["2025-01-30", "2025-01-31", "2025-02-03", "2025-04-01"]
    returns = {"GROSS_0BP": dict(zip(sessions, (0.01, 0.0, -0.02, 0.03))),
               "COST_10BP": dict(zip(sessions, (0.009, 0.0, -0.021, 0.029)))}
    trades = {"2025-01-30": 1, "2025-02-03": 2, "2025-04-01": 1}
    month = X.period_table(sessions, returns, trades, "month")
    assert [m["month"] for m in month] == ["2025-01", "2025-02", "2025-04"]
    assert month[0]["return_10bp"] == pytest.approx(0.009) and month[0]["active_sessions"] == 1
    assert month[-1]["cumulative_10bp"] == pytest.approx(1.009 * 0.979 * 1.029 - 1)
    quarter = X.period_table(sessions, returns, trades, "quarter")
    assert [q["quarter"] for q in quarter] == ["2025Q1", "2025Q2"]
    assert quarter[0]["trades"] == 3


# 17. repeated-run digest identity ----------------------------------------------------------------

def test_repeated_replay_and_evaluation_are_byte_identical() -> None:
    a_replayed, sessions, _ = _two_session_run()
    b_replayed, _, _ = _two_session_run()
    assert R.replay_digest(a_replayed) == R.replay_digest(b_replayed)
    a = R.canonical_json(R.evaluate(a_replayed, sessions, 2, integrity=True))
    b = R.canonical_json(R.evaluate(b_replayed, sessions, 2, integrity=True))
    assert a == b
    assert R.trades_csv(a_replayed) == R.trades_csv(b_replayed)
    assert R.daily_csv(a_replayed, sessions) == R.daily_csv(b_replayed, sessions)


# 18. no post-hoc filtering -------------------------------------------------------------------------

def test_buckets_report_rows_outside_declared_edges_instead_of_dropping() -> None:
    price = np.array([4.0, 7.0, 250.0, np.nan])
    gross = np.array([0.01, -0.01, 0.02, 0.0])
    table = X.bucket_table(price, gross, gross - 0.001, X.PRICE_BUCKETS)
    assert sum(row["trades"] for row in table) == 4
    assert table[-1]["bucket"] == "outside declared buckets" and table[-1]["trades"] == 2
    assert [row["bucket"] for row in table[:6]] == ["5-10", "10-20", "20-50", "50-100",
                                                    "100-200", "200+"]


def test_code_constants_equal_the_frozen_protocol() -> None:
    rules = R.load_backtest_rules()
    assert PRIMARY == rules["costs"]["primary_scenario"]
    assert list(SCENARIOS) == rules["reporting"]["cost_scenarios"]
    assert X.LIQUIDITY_BUCKETS == ((5e6, 2e7), (2e7, 1e8), (1e8, 5e8), (5e8, None))


# 19. no post-09:25 decision feature -----------------------------------------------------------------

def test_signal_sees_only_sealed_pre_0925_features() -> None:
    frame = _frame(["AAA", "BBB"], [True, True])
    assert set(frame.features) == set(SEALED_FEATURES)
    assert not any(name.startswith(("R_", "MFE_", "MAE_", "open_")) for name in SEALED_FEATURES)
    with pytest.raises(SignalContractError, match="after the 09:25"):
        evaluate_h5_signal(SignalFrame(session=SESSION, symbols=frame.symbols,
                                       features=frame.features, source_digest=SOURCE,
                                       latest_feature_bar_start_et="09:25"))


def test_post_decision_bars_and_labels_cannot_change_the_decision() -> None:
    frame = _frame(["AAA", "BBB", "CCC", "DDD"], [True] * 4)
    good = _replay(frame, {s: _bars(s, 100.0, 110.0) for s in frame.symbols})
    bad = _replay(frame, {s: _bars(s, 100.0, 90.0) for s in frame.symbols})
    assert good["digests"]["signal"] == bad["digests"]["signal"]
    assert ([r["symbol"] for r in good["records"] if r["selected"]]
            == [r["symbol"] for r in bad["records"] if r["selected"]])
    relabeled = SessionFrame(frame.session, frame.symbols, frame.features,
                             {s: {**v, "R_5m": -1.0} for s, v in frame.descriptors.items()})
    assert evaluate_signals([relabeled], SOURCE) == evaluate_signals([frame], SOURCE)


# 20. output schema and verdict precedence ------------------------------------------------------------

def test_output_schema_covers_the_protocol_reporting_lists() -> None:
    rules = R.load_backtest_rules()
    replayed, sessions, _ = _two_session_run()
    out = R.evaluate(replayed, sessions, 2, integrity=True)
    assert list(out["funnel"])[:9] == rules["reporting"]["funnel"]
    metric_names = {"mean": "mean", "median": "median", "win_rate": "win_rate",
                    "profit_factor": "profit_factor", "cumulative_return": "cumulative_return",
                    "maximum_drawdown": "maximum_drawdown", "sharpe": "sharpe",
                    "sortino": "sortino"}
    for scenario in rules["reporting"]["cost_scenarios"]:
        assert set(metric_names) <= set(out["scenarios"][scenario])
    for key in ("primary_gate", "stability", "buckets", "concentration", "break_even",
                "implementation_delta"):
        assert key in out
    assert set(out["primary_gate"]["gates"]) == {"integrity", "data_quality", "net_economics",
                                                  "statistical_support",
                                                  "chronological_robustness"}


@pytest.mark.parametrize(("kwargs", "expected"), (
    ({"integrity": False}, X.INTEGRITY_BLOCK),
    ({"coverage": 0.94}, X.INCONCLUSIVE_DATA),
    ({"mean_10bp": 0.0}, X.FAIL),
    ({"profit_factor_10bp": 1.0}, X.FAIL),
    ({"ci_low": 0.0}, X.INCONCLUSIVE_STATISTICAL),
    ({"positive_blocks": 2}, X.INCONCLUSIVE_STATISTICAL),
    ({}, X.PASS),
))
def test_verdict_follows_the_frozen_precedence(kwargs, expected) -> None:
    base = {"integrity": True, "coverage": 0.99, "mean_10bp": 0.001, "profit_factor_10bp": 1.2,
            "ci_low": 0.0001, "positive_blocks": 3}
    assert X.verdict(**{**base, **kwargs})["verdict"] == expected


def test_equity_metrics_follow_the_declared_formulas() -> None:
    values = np.array([0.01, -0.02, 0.0, 0.03])
    stats = X.equity_stats(values)
    equity = np.cumprod(1 + values)
    assert stats["cumulative_return"] == pytest.approx(equity[-1] - 1)
    assert stats["maximum_drawdown"] == pytest.approx(equity[1] / equity[0] - 1)
    assert stats["sharpe"] == pytest.approx(np.sqrt(252) * values.mean() / values.std(ddof=1))
    assert stats["sortino"] == pytest.approx(
        np.sqrt(252) * values.mean() / np.sqrt(np.mean(np.minimum(values, 0) ** 2)))
    assert X.profit_factor(np.array([0.01, 0.02])) is None
