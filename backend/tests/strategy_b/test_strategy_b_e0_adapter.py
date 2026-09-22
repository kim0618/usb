"""The B-E0 data adapter end to end on a synthetic Drive tree: mirror, cache, facts, source, gate.

The fixture is a miniature of the real store, built in the real formats (usb-common-raw-v1 ledgers
and pages, wrapped grouped daily, the splits dump, a C-raw-style freeze manifest) so the code under
test is exactly the code the real preflight runs. Each symbol stands for one case the design names:

    AAA    normal sparse sessions
    GAPX   a scope session with no minute bar while grouped daily shows volume   (TCI, D5)
    WARM   the same gap inside the RVOL lookback, not in scope                    (BACC, D6)
    QUIET  a scope session with no bar and no grouped row: legitimately empty
    IPOX   first grouped appearance a few sessions before scope                   (D3)
    SPLX   a split executed on a scope session
    SHORT  a fetch range too short for the minimum RVOL baseline
    ZZZF   not a member; carries conflicting duplicate split records
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from app.backtest.historical_store.b_universe import GROUPED_DIR
from app.backtest.historical_store.raw_fetch import MINUTE_DIR
from app.backtest.strategy_b.engine import run_session
from app.backtest.strategy_b.costs import CostModel, FillScenario
from app.backtest.strategy_b.portfolio import Portfolio
from app.backtest.strategy_b_e0 import mirror, session_cache
from app.backtest.strategy_b_e0.dataset_facts import LocalDatasetFacts
from app.backtest.strategy_b_e0.market_inputs import SPLITS_DIR, load_grouped_daily, load_splits
from app.backtest.strategy_b_e0.preflight import (
    DATA_QUALITY_BLOCKED, DATASET_NOT_READY, PASS, PASS_WITH_WARNINGS, quality_gate)
from app.backtest.strategy_b_e0.session_source import (
    LocalSessionSource, MissingSessionInput, NotInScope, complete_symbol_session)
from app.backtest.strategy_b_e0.universe import UniverseMember, load_universe, write_universe
from app.dev.fetch_strategy_c_selection_raw import sessions_between
from app.market.calendar import MarketCalendar
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.models import CorporateActionFlag
from app.strategy_b.scope import ScopeDecision
from app.strategy_b.session import ET

CAL = MarketCalendar()
MINUTE_SESSIONS = sessions_between(CAL, date(2026, 6, 1), date(2026, 6, 16))
SCOPE = [date(2026, 6, 11), date(2026, 6, 12), date(2026, 6, 15), date(2026, 6, 16)]
GROUPED_GRID = sessions_between(CAL, date(2026, 4, 1), date(2026, 6, 16))
GAP_DAY, WARM_GAP, QUIET_DAY, SPLIT_DAY = date(2026, 6, 12), date(2026, 6, 4), date(2026, 6, 15), date(2026, 6, 12)
IPO_FIRST = date(2026, 6, 5)
MEMBERS = ("AAA", "GAPX", "IPOX", "QUIET", "SHORT", "SPLX", "WARM")
REGULAR_BARS = 30


def ms(day: date, hour: int, minute: int) -> int:
    return int(datetime.combine(day, time(hour, minute), tzinfo=ET).timestamp() * 1000)


def minute_rows(symbol: str, day: date) -> list[dict]:
    if (symbol, day) in {("GAPX", GAP_DAY), ("WARM", WARM_GAP), ("QUIET", QUIET_DAY)}:
        return []
    rows = [{"t": ms(day, 8, 0), "o": 10, "h": 10, "l": 10, "c": 10, "v": 5, "vw": 10, "n": 1}]
    for k in range(REGULAR_BARS):
        rows.append({"t": ms(day, 9, 30 + k), "o": 10.0, "h": 10.0, "l": 10.0, "c": 10.0,
                     "v": 100.5, "vw": 10.0, "n": 3})
    return rows


def grouped_row(symbol: str, day: date) -> dict | None:
    if symbol == "QUIET" and day == QUIET_DAY:
        return None
    if symbol == "IPOX" and day < IPO_FIRST:
        return None
    return {"T": symbol, "o": 10.0, "h": 10.0, "l": 10.0, "c": 10.0,
            "v": 5 + REGULAR_BARS * 100.5, "vw": 10.0, "n": 91, "t": ms(day, 0, 0)}


def gz(payload) -> bytes:
    return gzip.compress(json.dumps(payload, sort_keys=True).encode(), mtime=0)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_start(symbol: str) -> date:
    return date(2026, 6, 8) if symbol == "SHORT" else MINUTE_SESSIONS[0]


def build_drive(root: Path, *, future_split: bool = False, extra_grouped: bool = False) -> dict:
    """The fake Drive tree and a C-raw-style freeze. Returns the freeze manifest."""
    for symbol in MEMBERS:
        start, end = fetch_start(symbol), MINUTE_SESSIONS[-1]
        rows = [r for d in MINUTE_SESSIONS if start <= d <= end for r in minute_rows(symbol, d)]
        page = gz({"ticker": symbol, "adjusted": False, "results": rows, "resultsCount": len(rows),
                   "status": "OK"})
        folder = root / MINUTE_DIR / symbol
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{symbol}_{start}_{end}"
        (folder / f"{stem}.p01.json.gz").write_bytes(page)
        ledger = {"status": "COMPLETE", "adjusted": False, "format": "usb-common-raw-v1",
                  "timespan": "minute", "kind": "minute", "symbol": symbol,
                  "start": start.isoformat(), "end": end.isoformat(),
                  "collected_at": "2026-09-20T00:00:00+00:00", "unavailable_rolling_window": [],
                  "pages": [{"file": f"{stem}.p01.json.gz", "file_sha256": sha(page)}]}
        (folder / f"{stem}.request.json").write_text(json.dumps(ledger), encoding="utf-8")

    files = []
    grid = GROUPED_GRID + ([date(2026, 6, 17)] if extra_grouped else [])
    for day in grid:
        results = [row for s in MEMBERS + ("ZZZF",) if (row := grouped_row(s, day))]
        data = gz({"format": "strategy-c-selection-raw-v1", "session": day.isoformat(),
                   "body": {"adjusted": False, "results": results}})
        rel = f"{GROUPED_DIR}/{day.year:04d}/{day.isoformat()}.json.gz"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        files.append({"file_type": "GROUPED_DAILY", "common_path": rel, "sha256": sha(data)})

    splits = [
        {"ticker": "SPLX", "execution_date": SPLIT_DAY.isoformat(), "split_from": 1, "split_to": 10, "id": "a"},
        {"ticker": "ZZZF", "execution_date": "2026-06-10", "split_from": 1, "split_to": 2, "id": "b"},
        {"ticker": "ZZZF", "execution_date": "2026-06-10", "split_from": 2, "split_to": 1, "id": "c"},
    ]
    if future_split:
        splits.append({"ticker": "AAA", "execution_date": "2026-06-16", "split_from": 1,
                       "split_to": 4, "id": "d"})
    data = gz({"format": "x", "results": splits})
    rel = f"{SPLITS_DIR}/splits_2024-09-16_2026-09-16.json.gz"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)
    files.append({"file_type": "SPLITS", "common_path": rel, "sha256": sha(data)})
    return {"files": files, "freeze_digest": "fixture"}


def build_universe(path: Path):
    members = [UniverseMember(s, tuple(SCOPE), fetch_start(s), MINUTE_SESSIONS[-1]) for s in MEMBERS]
    return write_universe(path, scope_start=SCOPE[0], scope_end=SCOPE[-1], sessions=SCOPE,
                          members=members, exclusions={"CON": "reserved"}, built_from={})


class World:
    def __init__(self, base: Path, **drive_options) -> None:
        self.drive, self.local = base / "drive", base / "local"
        self.freeze = build_drive(self.drive, **drive_options)
        self.universe = build_universe(base / "universe.json")
        self.plan = mirror.plan(self.drive, self.universe, self.freeze, workers=2)
        self.manifest = mirror.run(self.drive, self.local / "mirror", self.plan, universe=self.universe,
                                   freeze_digest="fixture", workers=2)
        cache_path = session_cache.build(self.local / "mirror", self.local / "cache",
                                         manifest=self.manifest, universe=self.universe,
                                         calendar=CAL, workers=1)
        self.cache = session_cache.SessionCache(cache_path,
                                                expected_source_digest=self.manifest["dataset_digest"])
        self.grouped = load_grouped_daily(self.local / "mirror", GROUPED_GRID, self.universe.symbols)
        self.splits = load_splits(self.local / "mirror")
        self.facts = LocalDatasetFacts(universe=self.universe, cache=self.cache, grouped=self.grouped,
                                       splits=self.splits, calendar=CAL, manifest=self.manifest,
                                       mirror_problems=mirror.verify(self.local / "mirror", self.manifest))

    def source(self) -> LocalSessionSource:
        return LocalSessionSource(self.facts, dataset_digest=self.manifest["dataset_digest"])


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return World(tmp_path_factory.mktemp("adapter"))


def sessions_for(world: World, day: date) -> dict:
    source = world.source()
    out = {}
    for session in SCOPE:
        items = source.symbol_sessions(session, world.universe.symbols_for(session))
        if session == day:
            out = {item.symbol: item for item in items}
            out["_source"] = source
            return out
    return out


# ---- mirror -----------------------------------------------------------------------------------


def test_mirror_copies_only_what_the_run_needs_and_verifies_it(world):
    kinds = {e["kind"] for e in world.manifest["files"]}
    assert kinds == {"minute_ledger", "minute_page", "grouped_daily", "splits"}
    assert world.manifest["members_without_ledgers"] == []
    assert world.manifest["ledger_schema_problems"] == []
    assert mirror.verify(world.local / "mirror", world.manifest) == []


def test_mirror_is_resumable_and_its_digest_is_content_only(world):
    again = mirror.run(world.drive, world.local / "mirror", world.plan, universe=world.universe,
                       freeze_digest="fixture", workers=2)
    assert again["copied_this_run"] == 0
    assert again["dataset_digest"] == world.manifest["dataset_digest"]


def test_a_page_that_does_not_match_its_ledger_stops_the_mirror(tmp_path):
    w = World(tmp_path)
    page = next(p for p in (w.drive / MINUTE_DIR / "AAA").glob("*.p01.json.gz"))
    page.write_bytes(gz({"results": []}))
    (w.local / "mirror" / MINUTE_DIR / "AAA" / page.name).unlink()
    with pytest.raises(mirror.MirrorFailed, match="sha256"):
        mirror.run(w.drive, w.local / "mirror", w.plan, universe=w.universe,
                   freeze_digest="fixture", workers=1)


def test_a_rewritten_ledger_is_copied_again_not_trusted_stale(tmp_path):
    w = World(tmp_path)
    ledger = next((w.drive / MINUTE_DIR / "AAA").glob("*.request.json"))
    body = json.loads(ledger.read_text())
    body["collected_at"] = "2026-09-21T00:00:00+00:00"
    ledger.write_text(json.dumps(body))
    fresh = mirror.plan(w.drive, w.universe, w.freeze, workers=1)
    rerun = mirror.run(w.drive, w.local / "mirror", fresh, universe=w.universe,
                       freeze_digest="fixture", workers=1)
    assert rerun["copied_this_run"] == 1
    assert rerun["dataset_digest"] != w.manifest["dataset_digest"]


# ---- cache and the sparse contract ------------------------------------------------------------


def test_1_normal_sparse_session_holds_exactly_the_observed_bars(world):
    bounds = world.source().boundaries(SCOPE[0])
    bars = world.cache.bars("AAA", SCOPE[0], bounds)
    assert len(bars) == REGULAR_BARS + 1
    assert [b.timestamp.minute for b in bars[1:4]] == [30, 31, 32]


def test_13_no_synthetic_bar_ever_leaves_the_cache(world):
    for symbol in MEMBERS:
        for day in MINUTE_SESSIONS:
            if world.cache.coverage(symbol, day) is None:
                continue
            bars = world.cache.bars(symbol, day, world.source().boundaries(day))
            assert not any(b.synthetic for b in bars)


def test_fractional_volume_survives_as_float(world):
    bars = world.cache.bars("AAA", SCOPE[0], world.source().boundaries(SCOPE[0]))
    assert bars[1].volume == 100.5


def test_2_a_legitimately_empty_session_is_replayed_empty_not_excluded(world):
    assert world.cache.coverage("QUIET", QUIET_DAY).rows == 0
    assert ("QUIET", QUIET_DAY) not in world.facts.gap_days()
    items = sessions_for(world, QUIET_DAY)
    assert "QUIET" in items and items["QUIET"].tape.bars == ()


# ---- D5, D6, D3 -------------------------------------------------------------------------------


def test_3_tci_gap_is_a_data_quality_exclusion_and_is_withheld(world):
    quality = world.facts.quality_exclusions()
    assert [(e.symbol, e.session) for e in quality] == [("GAPX", GAP_DAY)]
    assert quality[0].reasons == ("API_LOSS_SUSPECT", "MINUTE_TAPE_EMPTY")
    items = sessions_for(world, GAP_DAY)
    assert "GAPX" not in items
    assert items["_source"].withheld[-1]["symbol"] == "GAPX"


def test_4_bacc_warmup_gap_leaves_the_baseline_instead_of_counting_as_zero(world):
    assert ("WARM", WARM_GAP) in world.facts.gap_days()
    assert WARM_GAP not in world.facts.baseline_sessions("WARM", SCOPE[0])
    assert WARM_GAP in world.facts.baseline_sessions("AAA", SCOPE[0]) or \
        WARM_GAP in world.facts.warmup_window(SCOPE[0])
    uses = world.facts.warmup_gaps_in_use()
    warm = next(u for u in uses if u["symbol"] == "WARM")
    assert warm["reason"] == "MISSING_FULL_SESSION" and warm["gap_session"] == WARM_GAP.isoformat()
    items = sessions_for(world, SCOPE[0])
    assert len(items["WARM"].rvol_history) == len(items["AAA"].rvol_history) - 1


def test_5_ipo_first_appearance_is_a_policy_exclusion_not_a_quality_one(world):
    policy = world.facts.policy_exclusions()
    ipo = [e for e in policy if e.symbol == "IPOX"]
    assert len(ipo) == len(SCOPE) and all(e.reasons == ("IPO_WARMUP",) for e in ipo)
    assert ipo[0].detail["first_appearance"] == IPO_FIRST.isoformat()
    assert all(e.kind == "POLICY_EXCLUSION" for e in policy)
    assert not any(e.symbol == "IPOX" for e in world.facts.quality_exclusions())
    items = sessions_for(world, SCOPE[0])
    assert CorporateActionFlag.IPO_WARMUP in items["IPOX"].corporate_action_flags
    assert CorporateActionFlag.IPO_WARMUP not in items["AAA"].corporate_action_flags


# ---- splits -----------------------------------------------------------------------------------


def test_6_split_day_is_flagged_and_records_stay_raw_and_known_by_d(world):
    items = sessions_for(world, SPLIT_DAY)
    assert CorporateActionFlag.SPLIT_ON_DAY in items["SPLX"].corporate_action_flags
    assert [s.execution_date for s in items["SPLX"].splits] == [SPLIT_DAY]
    before = sessions_for(world, SCOPE[0])
    assert before["SPLX"].splits == ()
    assert before["SPLX"].tape.bars[1].close == 10.0  # raw price, never adjusted by the source


def test_7_conflicting_split_outside_the_universe_is_reported_only(world):
    assert (("ZZZF", date(2026, 6, 10)) in world.splits.conflicts)
    assert world.facts.split_conflicts_in_universe() == ()
    assert world.splits.records("ZZZF") == ()


# ---- guards ----------------------------------------------------------------------------------


def test_8_a_mirror_for_another_universe_is_refused(world, tmp_path):
    from app.backtest.strategy_b_e0.universe import UniverseChanged
    with pytest.raises(UniverseChanged):
        load_universe(world.universe.path, expected_sha256="0" * 64)
    other = build_universe(tmp_path / "other.json")
    assert other.sha256 == world.universe.sha256  # same content, same hash: identity is content


def test_10_a_missing_grouped_session_is_reported(world, tmp_path):
    root = tmp_path / "mirror"
    import shutil
    shutil.copytree(world.local / "mirror", root)
    missing = SCOPE[1]
    (root / GROUPED_DIR / f"{missing.year:04d}" / f"{missing.isoformat()}.json.gz").unlink()
    grouped = load_grouped_daily(root, GROUPED_GRID, world.universe.symbols)
    assert missing in grouped.missing_sessions
    facts = LocalDatasetFacts(universe=world.universe, cache=world.cache, grouped=grouped,
                              splits=world.splits, calendar=CAL, manifest=world.manifest,
                              mirror_problems=mirror.verify(root, world.manifest))
    assert missing in facts.sessions_missing_daily(world.universe.sessions)
    assert any("missing" in p for p in facts.checksum_mismatches(()))


def test_11_warmup_below_minimum_is_reported_not_hidden(world):
    below = world.facts.warmup_below_minimum()
    assert ("SHORT", SCOPE[0], 3) in below
    items = sessions_for(world, SCOPE[0])
    assert len(items["SHORT"].rvol_history) == 3  # RVOL will report UNKNOWN; nothing is padded


def test_12_session_load_is_lazy_and_bounded(world):
    source = world.source()
    source.symbol_sessions(SCOPE[0], world.universe.symbols_for(SCOPE[0]))
    held = {day for _, day in source._bars}
    assert held == {SCOPE[0]}  # older bars are dropped once their profiles exist
    assert all(day < SCOPE[0] for _, day in source._profiles)


def test_14_runtime_modules_never_recompute_scope():
    root = Path(__file__).resolve().parents[2] / "app/backtest/strategy_b_e0"
    runtime = ("mirror.py", "market_inputs.py", "session_cache.py", "session_source.py",
               "dataset_facts.py", "preflight.py", "run.py")
    for name in runtime:
        text = (root / name).read_text(encoding="utf-8")
        for banned in ("evaluate_research_scope", "reference_sets", "scope_membership",
                       "REFERENCE_DIR", "TickerMetadataAsOf"):
            assert banned not in text, f"{name} mentions {banned}"


def _session_args(world):
    items = sessions_for(world, SCOPE[0])
    aaa = items["AAA"]
    return dict(tape=aaa.tape, scope=aaa.scope, rvol_history=aaa.rvol_history, splits=aaa.splits,
                corporate_action_flags=aaa.corporate_action_flags)


def test_15_rvol_history_cannot_be_left_to_a_default(world):
    args = _session_args(world)
    del args["rvol_history"]
    with pytest.raises(TypeError):
        complete_symbol_session(**args)
    with pytest.raises(MissingSessionInput):
        complete_symbol_session(**(_session_args(world) | {"rvol_history": None}))


def test_16_corporate_action_flags_cannot_be_left_to_a_default(world):
    args = _session_args(world)
    del args["corporate_action_flags"]
    with pytest.raises(TypeError):
        complete_symbol_session(**args)
    with pytest.raises(MissingSessionInput):
        complete_symbol_session(**(_session_args(world) | {"corporate_action_flags": set()}))


def test_scope_must_be_an_included_decision_for_the_same_pair(world):
    args = _session_args(world)
    with pytest.raises(MissingSessionInput):
        complete_symbol_session(**(args | {"scope": None}))
    wrong = ScopeDecision("AAA", SCOPE[1], True, (), None, None, 0)
    with pytest.raises(MissingSessionInput):
        complete_symbol_session(**(args | {"scope": wrong}))


def test_a_symbol_outside_the_session_scope_is_refused(world):
    with pytest.raises(NotInScope):
        world.source().symbol_sessions(SCOPE[0], ["NOPE"])


def test_sessions_must_be_requested_in_order(world):
    source = world.source()
    source.symbol_sessions(SCOPE[1], ["AAA"])
    with pytest.raises(ValueError, match="in order"):
        source.symbol_sessions(SCOPE[0], ["AAA"])


# ---- determinism ------------------------------------------------------------------------------


def test_17_dataset_facts_are_deterministic(world):
    again = LocalDatasetFacts(universe=world.universe, cache=world.cache, grouped=world.grouped,
                              splits=world.splits, calendar=CAL, manifest=world.manifest,
                              mirror_problems=())
    assert [e.as_dict() for e in again.quality_exclusions()] == \
        [e.as_dict() for e in world.facts.quality_exclusions()]
    assert [e.as_dict() for e in again.policy_exclusions()] == \
        [e.as_dict() for e in world.facts.policy_exclusions()]
    assert again.warnings() == world.facts.warnings()


def test_18_preflight_report_is_deterministic(world):
    from app.backtest.strategy_b_e0.contract import load_contract
    from app.backtest.strategy_b_e0.preflight import run_preflight
    contract = load_contract()
    first = run_preflight(contract, world.universe, world.facts).as_dict()
    second = run_preflight(contract, world.universe, world.facts).as_dict()
    assert first == second


def test_fixture_preflight_is_blocked_for_the_right_reasons(world):
    """The fixture's window is not the contract's, so the preflight must say DATASET_NOT_READY
    and name exactly that, while still computing the gate and the warnings."""
    from app.backtest.strategy_b_e0.contract import load_contract
    from app.backtest.strategy_b_e0.preflight import run_preflight
    report = run_preflight(load_contract(), world.universe, world.facts)
    assert report.status == DATASET_NOT_READY
    failed = {c.name for c in report.failures}
    assert "universe_window_matches_contract" in failed
    assert "scope_bars_present" not in failed and "file_checksums_match" not in failed
    assert report.gate.quality_exclusions == 1 and any("IPO_WARMUP" in w for w in report.warnings)


def test_source_output_runs_through_the_engine(world):
    source = world.source()
    for session in SCOPE:
        items = source.symbol_sessions(session, world.universe.symbols_for(session))
        report = run_session(items, config=StrategyBConfig(),
                             portfolio=_portfolio(), scenario=FillScenario.NEXT_BAR_OPEN,
                             boundaries=source.boundaries(session))
        assert report.session_date == session
    assert len(source.dynamic) == len(SCOPE)


def _portfolio():
    return Portfolio(equity=7428.92, costs=CostModel(fee_bps_per_side=10, slippage_bps_per_side=15),
                     risk=StrategyBConfig().risk)


# ---- the quality gate ------------------------------------------------------------------------


class _E:
    def __init__(self, session):
        self.session = session


def _gate_universe(tmp_path, members: int, sessions: int = 1):
    days = SCOPE[:sessions]
    return write_universe(tmp_path / f"g{members}_{sessions}.json", scope_start=days[0],
                          scope_end=days[-1], sessions=days,
                          members=[UniverseMember(f"S{i:04d}", tuple(days), days[0], days[-1])
                                   for i in range(members)], exclusions={}, built_from={})


def test_overall_ratio_exactly_at_the_limit_does_not_block(tmp_path):
    universe = _gate_universe(tmp_path, 1000, 2)  # 2000 required pairs
    at = quality_gate(universe, [_E(SCOPE[i % 2]) for i in range(10)], overall_limit=0.005,
                      session_limit=1.0)
    assert at.overall_ratio == 0.005 and not at.blocked
    over = quality_gate(universe, [_E(SCOPE[i % 2]) for i in range(11)], overall_limit=0.005,
                        session_limit=1.0)
    assert over.blocked and "overall" in over.reasons[0]


def test_session_ratio_exactly_at_the_limit_does_not_block(tmp_path):
    universe = _gate_universe(tmp_path, 100, 1)
    at = quality_gate(universe, [_E(SCOPE[0])] * 2, overall_limit=1.0, session_limit=0.02)
    assert at.max_session_ratio == 0.02 and not at.blocked
    over = quality_gate(universe, [_E(SCOPE[0])] * 3, overall_limit=1.0, session_limit=0.02)
    assert over.blocked and "session" in over.reasons[0]


def test_status_order_is_blocking_then_quality_then_warnings():
    from app.backtest.strategy_b_e0.preflight import Check, PreflightReport, QualityGate
    report = PreflightReport()
    report.warnings = ["w"]
    report.settle()
    assert report.status == PASS_WITH_WARNINGS and report.ready
    report.gate = QualityGate(blocked=True)
    report.settle()
    assert report.status == DATA_QUALITY_BLOCKED and not report.ready
    report.add(Check("x", False, "d"))
    report.settle()
    assert report.status == DATASET_NOT_READY
    clean = PreflightReport()
    clean.settle()
    assert clean.status == PASS


# ---- point in time ---------------------------------------------------------------------------


def _digest(items) -> str:
    rows = []
    for item in sorted(items, key=lambda i: i.symbol):
        rows.append([item.symbol, [(b.timestamp.isoformat(), b.close, b.volume) for b in item.tape.bars],
                     [p.session_date.isoformat() for p in item.rvol_history],
                     [(s.execution_date.isoformat(), s.split_from, s.split_to) for s in item.splits],
                     sorted(str(f) for f in item.corporate_action_flags)])
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def test_future_data_cannot_change_a_past_session(tmp_path):
    """A split executed after D, and grouped daily for a session after the grid, change nothing
    about D. Future minute bars are already present in the fixture (the store holds every session
    to 06-16) and D must not see them either, which the per-session slice guarantees."""
    base, mutated = World(tmp_path / "base"), World(tmp_path / "mut", future_split=True,
                                                     extra_grouped=True)
    d = SCOPE[0]
    a = base.source().symbol_sessions(d, base.universe.symbols_for(d))
    b = mutated.source().symbol_sessions(d, mutated.universe.symbols_for(d))
    assert _digest(a) == _digest(b)
    assert all(bar.timestamp.date() == d for item in a for bar in item.tape.bars)
    assert all(p.session_date < d for item in a for p in item.rvol_history)


# ---- concurrent writers ----------------------------------------------------------------------


def _lock(root: Path, *, heartbeat: datetime) -> None:
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "state/writer.lock.json").write_text(json.dumps({
        "acquired_at": heartbeat.isoformat(timespec="seconds"),
        "heartbeat_at": heartbeat.isoformat(timespec="seconds"), "lease_seconds": 1800,
        "lock_schema_version": 1, "owner_id": "x" * 32, "pid": 4242,
        "purpose": "collect_u1_minute pid=4242"}), encoding="utf-8")


def test_a_live_writer_lock_is_seen_whatever_process_holds_it(tmp_path):
    """The U1 collector was invisible to a process-name check; the lock file is not."""
    from datetime import timezone
    from app.dev.run_strategy_b_e0 import active_writer
    (tmp_path / "state").mkdir()
    assert active_writer(tmp_path) is None
    _lock(tmp_path, heartbeat=datetime.now(timezone.utc))
    writer = active_writer(tmp_path)
    assert writer is not None and writer["purpose"].startswith("collect_u1_minute")
    _lock(tmp_path, heartbeat=datetime.now(timezone.utc) - timedelta(hours=2))
    assert active_writer(tmp_path) is None  # a stale lease is not a live writer


def test_the_authoritative_run_refuses_a_contract_that_is_not_frozen(tmp_path):
    from app.dev.run_strategy_b_e0 import ContractNotFrozen, main
    universe = build_universe(tmp_path / "u.json")
    with pytest.raises((ContractNotFrozen, SystemExit)):
        main(["run", "--universe", str(universe.path), "--dataset", str(tmp_path),
              "--out", str(tmp_path / "out")])


# ---- Common Raw repair: incremental mirror and coverage refresh ---------------------------------


def test_a_repair_ledger_is_mirrored_incrementally_and_refreshes_coverage(tmp_path):
    """SHORT's early sessions stand in for a legacy-only range: absent from Common Raw, so not
    covered. Adding the repair ledger copies only the new files, leaves every old file identical,
    changes the digest, and turns the missing sessions into covered ones."""
    w = World(tmp_path)
    early = [d for d in MINUTE_SESSIONS if d < fetch_start("SHORT")]
    assert all(w.cache.coverage("SHORT", d) is None for d in early)

    rows = [r for d in early for r in minute_rows("SHORT", d)]
    page = gz({"ticker": "SHORT", "adjusted": False, "results": rows, "status": "OK"})
    stem = f"SHORT_{early[0]}_{early[-1]}"
    folder = w.drive / MINUTE_DIR / "SHORT"
    (folder / f"{stem}.p01.json.gz").write_bytes(page)
    (folder / f"{stem}.request.json").write_text(json.dumps({
        "status": "COMPLETE", "adjusted": False, "format": "usb-common-raw-v1", "timespan": "minute",
        "symbol": "SHORT", "start": early[0].isoformat(), "end": early[-1].isoformat(),
        "collected_at": "2026-09-21T09:00:00+00:00", "unavailable_rolling_window": [],
        "pages": [{"file": f"{stem}.p01.json.gz", "file_sha256": sha(page)}]}), encoding="utf-8")

    widened = build_universe(tmp_path / "wide.json")  # same members; SHORT's fetch range unchanged
    members = [replace(m, fetch_start=MINUTE_SESSIONS[0]) if m.symbol == "SHORT" else m
               for m in widened.members.values()]
    universe = write_universe(tmp_path / "wide2.json", scope_start=SCOPE[0], scope_end=SCOPE[-1],
                              sessions=SCOPE, members=members, exclusions={"CON": "reserved"},
                              built_from={})
    plan = mirror.plan(w.drive, universe, w.freeze, workers=1)
    after = mirror.run(w.drive, w.local / "mirror", plan, universe=universe,
                       freeze_digest="fixture", workers=1)
    diff = mirror.compare_manifests(w.manifest, after)
    assert diff["changed"] == [] and diff["removed"] == []
    assert sorted(diff["added"]) == sorted([f"{MINUTE_DIR}/SHORT/{stem}.p01.json.gz",
                                            f"{MINUTE_DIR}/SHORT/{stem}.request.json"])
    assert after["copied_this_run"] == 2
    assert after["dataset_digest"] != w.manifest["dataset_digest"]

    cache = session_cache.SessionCache(
        session_cache.build(w.local / "mirror", w.local / "cache", manifest=after, universe=universe,
                            calendar=CAL, workers=1),
        expected_source_digest=after["dataset_digest"])
    assert all(cache.coverage("SHORT", d) is not None for d in early)
    facts = LocalDatasetFacts(universe=universe, cache=cache, grouped=w.grouped, splits=w.splits,
                              calendar=CAL, manifest=after, mirror_problems=())
    assert ("SHORT", SCOPE[0], 3) not in facts.warmup_below_minimum()
    assert facts.missing_scope_pairs(universe.required_pairs()) == ()


def test_an_old_cache_cannot_be_read_against_a_repaired_mirror(world):
    with pytest.raises(session_cache.CacheInvalid):
        session_cache.SessionCache(world.cache.directory, expected_source_digest="0" * 64)


def test_an_unmounted_drive_is_not_mistaken_for_a_free_lock(tmp_path):
    """After a reboot the mount point exists but is empty. That must not read as 'no writer'."""
    from app.dev.run_strategy_b_e0 import DriveUnavailable, active_writer
    with pytest.raises(DriveUnavailable):
        active_writer(tmp_path / "not-mounted")
    with pytest.raises(DriveUnavailable):
        active_writer(tmp_path)  # exists, but holds no workspace
