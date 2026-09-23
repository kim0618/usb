"""The canonical universe artifact: D-1 identity, a reproducible digest and a faithful base slice."""

from __future__ import annotations

from datetime import date
import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import SplitEvent
from app.market.calendar import MarketCalendar
from app.strategy_e_max_forward.forward_daily import DailyBase
from app.strategy_e_max_rt import universe_build as UB

CAL = MarketCalendar("America/New_York")
D = date(2026, 9, 23)          # Wednesday
PREV = date(2026, 9, 22)


def artifact(**over):
    body = {"format": UB.ARTIFACT_FORMAT, "target_session": D.isoformat(), "asof_session": PREV.isoformat(),
            "source": "MASSIVE", "rules_version": "e0-daily-eligibility/x", "inputs": {"a": "b"},
            "symbols": ["AAPL", "MSFT"], "symbol_count": 2,
            "close_d_minus_1": {"AAPL": 1.0, "MSFT": 2.0},
            "dollar_volume_d_minus_1": {"AAPL": 10.0, "MSFT": 20.0}, "spy_close_d_minus_1": 773.5} | over
    body["digest"] = UB.digest_of(body)
    body["generated_at"] = "2026-09-23T04:20:00+00:00"
    return body


def test_d_minus_1_identity_accepts_only_the_real_previous_session() -> None:
    ok, why = UB.d_minus_1_identity(artifact(), D, CAL)
    assert ok, why
    stale, why = UB.d_minus_1_identity(artifact(), date(2026, 9, 24), CAL)
    assert not stale and "targets" in why
    wrong, why = UB.d_minus_1_identity(artifact(asof_session="2026-09-18"), D, CAL)
    assert not wrong and "as-of" in why


def test_a_tampered_artifact_is_refused() -> None:
    body = artifact()
    body["symbols"] = ["AAPL", "MSFT", "NVDA"]          # content changed after the digest was taken
    ok, why = UB.d_minus_1_identity(body, D, CAL)
    assert not ok and "digest" in why


def test_the_digest_is_reproducible_and_excludes_generated_at() -> None:
    first, second = artifact(), artifact()
    second["generated_at"] = "2026-09-23T23:59:59+00:00"
    assert UB.digest_of(first) == UB.digest_of(second) == first["digest"]
    same, problems = UB.identical(first, second)
    assert same and problems == []


def test_two_builds_that_differ_are_reported_field_by_field() -> None:
    same, problems = UB.identical(artifact(), artifact(symbols=["AAPL"]))
    assert not same and "symbols" in problems and "digest" in problems


def synthetic_base() -> DailyBase:
    sessions = [date(2026, 9, 14 + i) for i in range(6)]
    tickers = ("AAPL", "MSFT", "SPY")
    close = np.arange(len(sessions) * len(tickers), dtype=float).reshape(len(sessions), len(tickers))
    return DailyBase(sessions=sessions, tickers=tickers, close=close, volume=close * 10,
                     splits=(SplitEvent("AAPL", date(2026, 6, 1), 1.0, 4.0),),
                     snapshots={date(2026, 9, 16): frozenset({"AAPL", "SPY"}),
                                date(2026, 9, 18): frozenset({"AAPL", "MSFT", "SPY"})},
                     allowed_exchanges=frozenset({"XNAS", "XNYS"}))


def test_the_base_slice_carries_the_tail_unchanged(tmp_path) -> None:
    base = synthetic_base()
    out = tmp_path / "slice.npz"
    summary = UB.export_base_slice(tmp_path, out, sessions=3, snapshots=1, base=base)
    assert summary["sessions"] == 3 and summary["tickers"] == 3
    loaded = UB.load_base_slice(out)
    assert loaded.sessions == base.sessions[-3:]
    assert loaded.tickers == base.tickers
    assert np.array_equal(loaded.close, base.close[-3:])
    assert np.array_equal(loaded.volume, base.volume[-3:])
    assert loaded.splits == base.splits
    assert loaded.allowed_exchanges == base.allowed_exchanges
    assert list(loaded.snapshots) == [date(2026, 9, 18)]          # only the newest membership snapshot
    assert loaded.snapshots[date(2026, 9, 18)] == base.snapshots[date(2026, 9, 18)]


def test_a_foreign_file_is_not_accepted_as_a_slice(tmp_path) -> None:
    path = tmp_path / "other.npz"
    np.savez_compressed(path, close=np.zeros(1), volume=np.zeros(1),
                        meta=np.array(['{"format": "something-else"}'], dtype=object), allow_pickle=True)
    with pytest.raises(ValueError):
        UB.load_base_slice(path)


def test_a_pre_v2_artifact_is_read_but_its_digest_is_not_re_derived() -> None:
    legacy = {"format": "e-rt2-canonical-universe-v1", "session": D.isoformat(),
              "d_minus_1": PREV.isoformat(), "symbols": ["AAPL"], "digest": "old"}
    ok, why = UB.d_minus_1_identity(legacy, D, CAL)
    assert ok and "pre-v2" in why
