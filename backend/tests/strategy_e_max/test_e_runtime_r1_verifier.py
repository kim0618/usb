"""E-RUNTIME-R1 shadow gate: the independent recomputation agrees 100% with a session produced by
the runner's own functions, and it fails on any single altered step."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.dev import run_e_rt2_dryrun as RUN
from app.dev import verify_e_runtime_session as V
from app.market.calendar import MarketCalendar
from app.strategy_e_max_rt import availability as AV, decision as DEC, finalizer as FZ, paper as PAPER
from app.strategy_e_max_rt import rvol_store as RS, universe_build as UB
from app.strategy_e_v1_1.universe import DecisionFrame

from tests.strategy_e_max.test_e_rt0_runtime import FakeProvider, bar

ET = RS.ET
D = date(2026, 10, 20)                                  # a Tuesday session
CAL = MarketCalendar("America/New_York")


def _bars(index: int, *, strong: bool) -> dict[int, list[float]]:
    """Premarket 04:00-09:24; the 'strong' ones rise into the high of their range and trade heavily."""
    out = {}
    base = 20.0 + index * 0.1
    for minute in range(240, 565, 3):
        drift = (minute - 240) / 324.0
        price = base * (1 + (0.04 if strong else -0.01) * drift)
        volume = (4000.0 + 37 * index) if strong else (300.0 + index)
        out[minute] = [price, price * 1.001, price * 0.999, price, volume]
    return out


def _session(tmp_path: Path, n: int = 120, strong=(3, 7, 11, 19, 23, 31, 42, 57, 64, 88)):
    run_root, universe_dir = tmp_path / "run", tmp_path / "universe"
    run_dir = run_root / D.isoformat()
    run_dir.mkdir(parents=True)
    universe_dir.mkdir()
    symbols = [f"S{i:03d}" for i in range(n)] + ["PS"]
    previous = CAL.previous_trading_day(D)
    art = {"format": UB.ARTIFACT_FORMAT, "target_session": D.isoformat(), "asof_session": previous.isoformat(),
           "rules_version": "fixture", "inputs": {}, "symbols": symbols,
           "close_d_minus_1": {s: 20.0 + i * 0.1 for i, s in enumerate(symbols)},
           "dollar_volume_d_minus_1": {s: 5e7 for s in symbols}, "spy_close_d_minus_1": 500.0}
    art["digest"] = UB.digest_of(art)
    (universe_dir / f"universe_{D.isoformat()}.json").write_text(json.dumps(art))
    caches = {}
    for i, s in enumerate(symbols[:-1]):
        cache = FZ.SymbolCache(s, "NA", s)
        cache.bars = _bars(i, strong=i in strong)
        caches[s] = cache
    caches["PS"] = FZ.SymbolCache("PS", "NA", "PS")
    spy = FZ.SymbolCache("SPY", "NA", "SPY")
    spy.bars = {m: [500.0, 500.5, 499.5, 500.2, 1e5] for m in range(240, 565)}
    statuses = {s: AV.FEATURE_COMPLETE for s in symbols[:-1]} | {"PS": AV.STALE}
    # the RVOL store: 25 prior staged sessions per symbol, written long before the seal
    store_path = tmp_path / "rvol.sqlite3"
    store = RS.RvolStore(store_path, clock=lambda: datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc))
    day, prior = previous, []
    while len(prior) < 25:
        if CAL.session(day) is not None:
            prior.append(day)
        day -= timedelta(days=1)
    written = "2026-10-19T23:00:00+00:00"                        # one batch, long before the seal
    batch = []
    for i, s in enumerate(symbols[:-1]):
        for k, p in enumerate(prior):
            record = RS.SessionRecord(s, p, 60_000.0 + 1000 * ((i + k) % 7), 50, True)
            batch.append((s, p.isoformat(), record.pm_dollar_volume, 50, 1, 1, "KIWOOM", 1, record.checksum(), written))
    store.connection.executemany("INSERT INTO kiwoom_premarket_sessions VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
    store.connection.commit()
    store.note_pass("PS", staged_count=0, oldest_session=None, history_exhausted=False,
                    error="MARKET_DATA_UNAVAILABLE")
    seal = datetime.combine(D, time(9, 29, 40), tzinfo=ET)
    rows, unknown = RUN._feature_rows(caches, spy, statuses, art, store.preload(list(caches), D))
    names = tuple(sorted(rows))
    frame = DecisionFrame(D, names, {f: np.array([rows[x][f] for x in names], dtype=float) for f in SEALED_FEATURES}, {})
    decision = DEC.decide_from_frame(frame, source_digest=art["digest"], source="FIXTURE", decided_at=seal)
    RUN._persist_inputs(run_dir, D, caches, spy, statuses, rows)
    documented = {s for s, st in store.collection_state().items() if st.get("last_error") in RUN.DOCUMENTED_REFUSALS}
    gate = RUN.execution_gate(decided_at=seal, open_et=datetime.combine(D, time(9, 30), tzinfo=ET),
                              statuses=statuses, documented=documented, rvol_unknown=unknown,
                              unreachable=store.unreachable_symbols(), pending=[])
    t0 = datetime.combine(D, time(9, 25, 0, 30000), tzinfo=ET)
    record = PAPER.session_record(
        D, states=statuses, h5_by_symbol={s: AV.h5_status(statuses[s], s in decision.candidates) for s in caches},
        eligible_rows=decision.universe_rows, candidates=decision.candidates, selected=decision.selected,
        rvol_ready_rows=len(names) - len(unknown), rvol_missing_rows=len(unknown), bootstrap_complete=True,
        decision_digest=decision.digest,
        extra={"universe_digest": art["digest"], "execution_gate": gate,
               "timeline": {"T0": t0.isoformat(), "seal": seal.isoformat()}})
    (run_dir / "session_record.json").write_text(json.dumps(record, default=str))
    (run_dir / "run.json").write_text(json.dumps({"universe_digest": art["digest"], "universe": {"unmapped": []},
                                                  "cutoff_audit": {"future_cutoff_violation_count": 0}}))
    # the paper stage, through the runner's own function, on synthetic regular-session bars
    market = []
    for s in decision.selected:
        ref = decision.reference_prices[s]
        market += [bar(s, 9, 30, ref * 1.001, ref * 1.002, day=D)]
        market += [bar(s, 9, m, ref * 1.002, ref * 1.002, day=D) for m in (31, 32, 33)]
        market += [bar(s, 9, 34, ref * 1.003, ref * 1.011, day=D), bar(s, 9, 35, ref * 1.02, ref * 1.02, day=D)]
    clock = {"t": datetime.combine(D, time(9, 30), tzinfo=ET)}

    def now():
        clock["t"] += timedelta(minutes=1)
        return clock["t"]

    provider = FakeProvider(market, lambda: clock["t"])
    out = RUN._paper_stage(decision, D, record, run_root, CAL, now, lambda t: datetime.combine(D, t, tzinfo=ET),
                           lambda *a: None, "10000", provider_factory=lambda: provider, interval=0.0,
                           book=RUN.BOOK_SHADOW, gate=gate)
    assert out["ran"] and out["fills"] == len(decision.selected)
    store.close()
    return {"run_dir": run_dir, "universe": universe_dir, "store": store_path,
            "book_root": run_root / RUN.PAPER_STATE_V2 / RUN.BOOK_SHADOW, "decision": decision, "gate": gate}


def test_a_runner_produced_session_passes_every_check(tmp_path) -> None:
    s = _session(tmp_path)
    assert s["gate"]["authorized"] and s["gate"]["documented_exceptions"] == ["PS"]
    assert len(s["decision"].selected) == 3                       # the fixture really selects
    body = V.verify(s["run_dir"], s["universe"], s["store"], s["book_root"])
    assert body["verdict"] == V.PASS_LABEL, body["failed"]
    names = {c["check"] for c in body["checks"]}
    assert {"rvol_exact_prior_only", "h5_exact", "top3_exact_no_backfill", "breadth_exposure_exact",
            "entry_exact_0930_open", "exit_exact_0934_close", "cost_exact_10bp", "book_reconciles",
            "seal_before_0930", "universe_complete", "no_future_bars"} <= names


def test_a_later_repair_of_the_store_does_not_change_the_recomputation(tmp_path) -> None:
    """The gate reads the store as it stood at the seal, so tonight's repair cannot fail last session."""
    s = _session(tmp_path)
    store = RS.RvolStore(s["store"], clock=lambda: datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc))
    symbol = s["decision"].selected[0]
    row = store.connection.execute("SELECT session, checksum FROM kiwoom_premarket_sessions WHERE symbol=? "
                                   "ORDER BY session DESC LIMIT 1", (symbol,)).fetchone()
    # fake a premature row then repair it after the seal (the audit log keeps the old value)
    store.connection.execute("UPDATE kiwoom_premarket_sessions SET collected_at=? WHERE symbol=? AND session=?",
                             (f"{row[0]}T04:00:00+00:00", symbol, row[0]))
    store.connection.commit()
    assert store.repair(RS.SessionRecord(symbol, date.fromisoformat(row[0]), 9e9, 50, True),
                        reason=RS.REPAIR_PREMATURE_DAILY_APPEND, expected_old_checksum=row[1]) == "REPAIRED"
    store.close()
    assert V.verify(s["run_dir"], s["universe"], s["store"], s["book_root"])["verdict"] == V.PASS_LABEL


@pytest.mark.parametrize("tamper, check", [
    ("selected", "top3_exact_no_backfill"),
    ("feature", "features_exact"),
    ("seal", "seal_before_0930"),
    ("stale", "universe_complete"),
    ("exit", "exit_exact_0934_close"),
])
def test_any_single_altered_step_fails_the_gate(tmp_path, tamper, check) -> None:
    s = _session(tmp_path)
    record_path = s["run_dir"] / "session_record.json"
    record = json.loads(record_path.read_text())
    if tamper == "selected":
        record["selected"] = list(reversed(record["selected"]))
    elif tamper == "seal":
        record["timeline"]["seal"] = datetime.combine(D, time(9, 30, 1), tzinfo=ET).isoformat()
    elif tamper == "stale":
        record["execution_gate"]["documented_exceptions"] = []
    record_path.write_text(json.dumps(record))
    if tamper == "feature":
        path = s["run_dir"] / "feature_frame.csv"
        rows = list(csv.reader(path.open()))
        col = rows[0].index("premarket_gap")
        rows[1][col] = repr(float(rows[1][col]) + 1e-6)
        with path.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
    if tamper == "exit":
        state_path = s["book_root"] / "STRATEGY_E_MAX_V1" / "sessions" / f"{D.isoformat()}.json"
        state = json.loads(state_path.read_text())
        symbol = next(iter(state["exits"]))
        state["exits"][symbol]["fill_bar"]["timestamp"] = state["exits"][symbol]["fill_bar"]["timestamp"].replace("09:34", "09:35")
        state_path.write_text(json.dumps(state))
    body = V.verify(s["run_dir"], s["universe"], s["store"], s["book_root"])
    assert body["verdict"] == V.FAIL_LABEL and check in body["failed"]
