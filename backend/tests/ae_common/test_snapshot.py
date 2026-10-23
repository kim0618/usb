"""Sealed snapshots: written once, verified on read, and exactly the inputs the live cache gives."""

from datetime import datetime, time
import gzip
import json
import os
import stat

import pytest

from app.ae_common import e_shadow as ES
from app.ae_common import snapshot as SS
from app.ae_common import sweep as SW
from app.strategy_a_mover_live import acquisition as AQ
from app.strategy_e_max_rt import finalizer as FZ
from tests.ae_common.helpers import SESSION, Clock, at, lanes, minute_rows

A_SYMBOLS = [f"S{i:02d}" for i in range(24)]
E_SYMBOLS = A_SYMBOLS[:10] + ["EONLY"]


def swept_caches(symbols, *, start=time(9, 15), deadline=time(9, 25), sparse=()):
    """Caches as rolling + the sweep leave them: bars past 09:15 included (E's cutoff merge)."""
    clock = Clock(at(time(9, 10)))
    rows = {s: minute_rows(SESSION, 9 * 60 + 30, every=7 if s in sparse else 1) for s in symbols}
    lane_minute, lane_tick, _, limiters = lanes(rows, clock)
    caches = {s: FZ.SymbolCache(s, "ND", s) for s in symbols}
    for c in caches.values():
        FZ.refresh(lane_minute, c, SESSION, clock)        # 09:10 roll: complete through 09:09+
    clock.tick((at(start) - clock()).total_seconds())
    _, tick = FZ.hash_lanes(E_SYMBOLS)
    plan = SW.plan(A_SYMBOLS, E_SYMBOLS, tick)
    for lim in limiters:
        lim.begin_phase("a_sweep", at(deadline))
    observed = clock()
    swept = SW.run(lane_minute, lane_tick, caches, plan, session=SESSION, now=clock, deadline=at(deadline))
    return caches, swept, plan, observed, clock


def sealed_0915(tmp_path, *, unmapped=(), **kw):
    caches, swept, plan, observed, clock = swept_caches(sorted(set(A_SYMBOLS) | set(E_SYMBOLS)), **kw)
    frozen = SS.freeze_a(caches, swept.a_pass, plan.symbols, session=SESSION, observed_at=observed)
    body = SS.body_0915(frozen, a_symbols=A_SYMBOLS, union_symbols=A_SYMBOLS, union_checksum="u",
                        unmapped=unmapped, exchanges={s: "ND" for s in A_SYMBOLS}, sweep=swept.declaration())
    manifest = SS.seal(tmp_path, SS.SNAPSHOT_0915, body, sealed_at=clock(), summary=SS.summary_0915(body))
    return frozen, body, manifest


def test_a_seal_is_read_back_identical_and_cannot_be_written_twice(tmp_path):
    body = {"format": "f", "session": SESSION.isoformat(), "x": [1.5, 2.25, 1e-7]}
    manifest = SS.seal(tmp_path, "snap", body, sealed_at=at(time(9, 25)))
    assert SS.load(tmp_path, "snap").body == body
    with pytest.raises(SS.SnapshotExists):
        SS.seal(tmp_path, "snap", body | {"x": [0]}, sealed_at=at(time(9, 26)))
    body_path, manifest_path = SS.paths(tmp_path, "snap")
    assert stat.S_IMODE(os.stat(body_path).st_mode) == 0o444
    assert stat.S_IMODE(os.stat(manifest_path).st_mode) == 0o444
    assert manifest["sha256"] == SS.digest(body)
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".partial")]


def test_a_tampered_or_foreign_snapshot_is_refused(tmp_path):
    body = {"format": SS.FORMAT_0915, "session": SESSION.isoformat(), "symbols": {}}
    SS.seal(tmp_path, SS.SNAPSHOT_0915, body, sealed_at=at(time(9, 25)))
    with pytest.raises(SS.SnapshotInvalid):
        SS.load(tmp_path, SS.SNAPSHOT_0915, fmt=SS.FORMAT_0924)
    with pytest.raises(SS.SnapshotInvalid):
        SS.load(tmp_path, SS.SNAPSHOT_0915, session=SESSION.replace(day=16))
    body_path, _ = SS.paths(tmp_path, SS.SNAPSHOT_0915)
    os.chmod(body_path, 0o644)
    body_path.write_bytes(gzip.compress(SS.canonical(body | {"symbols": {"X": {}}})))
    with pytest.raises(SS.SnapshotInvalid, match="digest"):
        SS.load(tmp_path, SS.SNAPSHOT_0915)


def test_snapshot_0915_holds_no_minute_at_or_after_the_cut_although_the_cache_does(tmp_path):
    frozen, body, _ = sealed_0915(tmp_path, start=time(9, 20))      # sweep reads minutes to 09:19
    assert any(m >= SS.A_CUT_MINUTE for c in frozen.caches.values() for m in c.bars)   # live cache has them
    assert all(row[0] < SS.A_CUT_MINUTE for e in body["symbols"].values() for row in e.get("bars", ()))


def test_a_snapshots_from_the_file_are_exactly_the_legacy_filter_at_read_snapshots(tmp_path):
    frozen, _, _ = sealed_0915(tmp_path, start=time(9, 20), sparse=("S03", "S11"))
    legacy = SS.row_digests(SS.a_snapshot_rows(frozen.caches, frozen.a_pass, session=SESSION,
                                               observed_at=frozen.observed_at))
    caches, passed, observed = SS.a_caches_from(SS.load(tmp_path, SS.SNAPSHOT_0915).body)
    from_file = SS.row_digests(SS.a_snapshot_rows(caches, passed, session=SESSION, observed_at=observed))
    result = SS.compare_digests(legacy, from_file)
    assert result["exact"] and result["compared"] == len(frozen.a_pass.usable) > 0
    assert passed.usable == frozen.a_pass.usable


def test_the_raw_rows_a_persists_are_the_same_from_the_file(tmp_path):
    frozen, _, _ = sealed_0915(tmp_path, start=time(9, 20))
    caches, passed, observed = SS.a_caches_from(SS.load(tmp_path, SS.SNAPSHOT_0915).body)
    legacy = AQ.raw_session_from(frozen.caches, frozen.a_pass, session=SESSION, observed_at=frozen.observed_at)
    again = AQ.raw_session_from(caches, passed, session=SESSION, observed_at=observed)
    assert len(legacy) > 0 and legacy.payload() == again.payload()


def test_every_a_symbol_has_a_status_and_unfinished_ones_make_the_file_incomplete(tmp_path):
    _, body, manifest = sealed_0915(tmp_path, start=time(9, 24, 58), unmapped=("S23",))
    statuses = {s: e["status"] for s, e in body["symbols"].items() if e["a_member"]}
    assert set(statuses) == set(A_SYMBOLS)
    assert statuses["S23"] == SS.UNMAPPED
    assert any(v in (SS.NOT_REACHED, SS.INTERRUPTED) for v in statuses.values())
    assert manifest["summary"]["complete_for_a"] is False and manifest["summary"]["unexpected"] > 0
    assert manifest["summary"]["permanent_exceptions"] == 1                 # the unmapped one


def test_the_status_of_a_failed_and_a_stale_symbol():
    outcomes = {"F": AQ.APassOutcome("F", "A", 1, None, at(time(9, 16)), None, "MarketDataError"),
                "S": AQ.APassOutcome("S", "A", 1, False, at(time(9, 16)), 500, None),
                "I": AQ.APassOutcome("I", "B", 0, None, at(time(9, 25)), None, "KIWOOM_DEADLINE_REACHED"),
                "U": AQ.APassOutcome("U", "A", 1, True, at(time(9, 16)), 554, None)}
    got = {s: SS.a_status(s, outcomes, {"M"}) for s in ("F", "S", "I", "U", "M", "N")}
    assert got == {"F": SS.FAILED, "S": SS.STALE, "I": SS.INTERRUPTED, "U": SS.USABLE,
                   "M": SS.UNMAPPED, "N": SS.NOT_REACHED}


def test_snapshot_0924_gives_e_the_same_status_classification_and_bars(tmp_path):
    caches, _, _, _, clock = swept_caches(sorted(set(A_SYMBOLS) | set(E_SYMBOLS)))
    clock.tick((at(time(9, 25)) - clock()).total_seconds())
    lane_minute, _, _, _ = lanes({s: minute_rows(SESSION, 9 * 60 + 30) for s in E_SYMBOLS + ["SPY"]}, clock)
    spy = FZ.SymbolCache("SPY", "NA")
    for symbol in E_SYMBOLS[:-1]:
        FZ.finalize_minute(lane_minute, caches[symbol], SESSION, clock)
    FZ.finalize_minute(lane_minute, spy, SESSION, clock)
    caches["EONLY"].error = "MarketDataError"                     # one refusal, one never finalized
    body = SS.body_0924(SESSION, caches, spy, universe=E_SYMBOLS, unmapped=["GONE"], universe_digest="d",
                        finalization={})
    SS.seal(tmp_path, SS.SNAPSHOT_0924, body, sealed_at=clock())
    file_caches, file_spy = SS.e_caches_from(SS.load(tmp_path, SS.SNAPSHOT_0924, fmt=SS.FORMAT_0924).body)
    deadline = at(time(9, 29, 45))
    live = {s: caches[s] for s in E_SYMBOLS}
    assert ES.statuses_of(file_caches, ["GONE"], deadline) == ES.statuses_of(live, ["GONE"], deadline)
    assert all(m <= SS.E_CUTOFF_MINUTE for c in file_caches.values() for m in c.bars)
    assert {s: {m: v for m, v in c.bars.items() if m <= 564} for s, c in live.items()} == \
        {s: c.bars for s, c in file_caches.items()}
    assert file_spy.bars == {m: v for m, v in spy.bars.items() if m <= 564}


# -- the expected-processable universe ------------------------------------------------------------

def _outcome(symbol, error=None, usable=True):
    return AQ.APassOutcome(symbol, "A", 1, usable if error is None else None, at(time(9, 16)), 554, error)


def _served(symbol, served=True):
    cache = FZ.SymbolCache(symbol, "ND", symbol)
    cache.first_fetch = at(time(4, 1)) if served else None
    return cache


def test_a_source_refusal_on_a_symbol_never_served_all_morning_is_market_data_unavailable():
    from app.ae_common.sweep import error_label, REJECTED_MESSAGE
    from app.core.exceptions import MarketDataError
    refusal = error_label(MarketDataError("INVALID_SYMBOL", REJECTED_MESSAGE, provider="KIWOOM", error_category="SOURCE_REFUSAL"))
    assert refusal == "SOURCE_REFUSAL:INVALID_SYMBOL"
    assert SS.a_status("PS", {"PS": _outcome("PS", refusal)}, set(), cache=_served("PS", False)) == \
        SS.MARKET_DATA_UNAVAILABLE
    # refused now but served earlier today: not permanent, an unexpected failure
    assert SS.a_status("X", {"X": _outcome("X", refusal)}, set(), cache=_served("X", True)) == SS.FAILED


def test_a_transport_failure_is_never_a_permanent_exception():
    from app.ae_common.sweep import error_label
    from app.core.exceptions import MarketDataError
    for error in (MarketDataError("MARKET_DATA_UNAVAILABLE", "Kiwoom market data is unavailable"),
                  MarketDataError("MARKET_DATA_UNAVAILABLE", "Kiwoom market-data request failed"),
                  MarketDataError("RATE_LIMITED", "Kiwoom market-data request failed"), TimeoutError()):
        label = error_label(error)
        assert not label.startswith(SS.SOURCE_REFUSAL_PREFIX)
        assert SS.a_status("T", {"T": _outcome("T", label)}, set(), cache=_served("T", False)) == SS.FAILED


def test_a_documented_refusal_not_served_today_is_excluded_and_one_served_today_is_usable():
    assert SS.a_status("PS", {}, set(), documented=frozenset({"PS"})) == SS.DOCUMENTED_REFUSAL
    assert SS.a_status("PS", {"PS": _outcome("PS")}, set(), documented=frozenset({"PS"})) == SS.USABLE


def _summary(statuses):
    body = {"a_symbols": sorted(statuses), "symbols": {s: {"status": v, "a_member": True, "bars": []}
                                                       for s, v in statuses.items()}}
    return SS.summary_0915(body)


def test_coverage_is_complete_only_when_every_expected_symbol_is_usable():
    ok = _summary({"A": SS.USABLE, "B": SS.UNMAPPED, "C": SS.MARKET_DATA_UNAVAILABLE, "D": SS.DOCUMENTED_REFUSAL})
    assert ok["complete_for_a"] and ok["expected_processable"] == 1 and ok["permanent_exceptions"] == 3
    for bad in (SS.STALE, SS.FAILED, SS.INTERRUPTED, SS.NOT_REACHED):
        summary = _summary({"A": SS.USABLE, "B": bad, "C": SS.UNMAPPED})
        assert not summary["complete_for_a"] and summary["unexpected_examples"] == {"B": bad}


def test_a_refusal_category_is_explicit_and_never_inferred_from_the_message():
    from app.ae_common.sweep import error_label, REJECTED_MESSAGE
    from app.core.exceptions import MarketDataError
    assert error_label(MarketDataError("INVALID_SYMBOL", REJECTED_MESSAGE)) == "MarketDataError:INVALID_SYMBOL"
    assert error_label(MarketDataError("MARKET_DATA_UNAVAILABLE", REJECTED_MESSAGE)) == "MarketDataError:MARKET_DATA_UNAVAILABLE"
