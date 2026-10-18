"""A's snapshot consumer: the cut downstream exactly as ``integration.run_cut``, fail closed, no Kiwoom."""

import ast
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ae_common import a_consumer as AC
from app.ae_common import snapshot as SS
from app.strategy_a_mover_live import acquisition as AQ
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import features as FEAT
from app.strategy_a_mover_live import gpt_handoff as GPT
from app.strategy_a_mover_live import integration as INT
from app.strategy_a_mover_live import isolation as ISO
from app.strategy_a_mover_live import scanner as SCAN
from app.strategy_a_mover_live import universe as UNI
from tests.ae_common.helpers import SESSION, Clock, at
from tests.ae_common.test_snapshot import A_SYMBOLS, sealed_0915

ON = {CFG.ENV_FLAG: "true"}
BACKEND = Path(__file__).resolve().parents[2]


def union_of(symbols):
    return UNI.UnionUniverse(session=SESSION, a_symbols=tuple(sorted(symbols)), e_symbols=(),
                             union=tuple(sorted(symbols)), reference_as_of=date(2026, 9, 1),
                             reference_checksum="r", reference_active_rows=len(symbols),
                             pruned_no_daily_baseline=0, pruned_split_session=0, e_artifact=None)


def make_handle(repo, clock, symbols=A_SYMBOLS, unmapped=()):
    client = AC.NoKiwoomClient()
    from app.strategy_e_max_rt import finalizer as FZ
    handle = INT.SharedCollectorIntegration(
        session=SESSION, repo=repo, caches={}, shard_minute=(), shard_tick=(),
        lane_minute=FZ.Lane("A", FZ.MINUTE_API, client), lane_tick=FZ.Lane("B", FZ.TICK_API, client),
        now=clock, union=union_of(symbols), exchanges={s: "ND" for s in symbols}, log=lambda text: None)
    handle.unmapped = tuple(unmapped)
    return handle, client


@pytest.fixture
def stubbed(monkeypatch):
    """Stub the scan stages so both paths can be compared step by step; record the call order."""
    calls = []
    panels = SimpleNamespace(declaration=lambda: {"panels": "stub"})
    live = SimpleNamespace(metadata=lambda: {"status": "RAN"}, candidate_count=2,
                           selection=SimpleNamespace(handoff=()))

    def assemble(repo, session, snapshots, baselines, union):
        calls.append(("assemble", tuple(sorted(snapshots)), len(baselines)))
        return panels

    def register(item):
        calls.append(("register",))
        return "LIVE_FEED"

    def run_from_source(source, session, *, observed_at):
        calls.append(("scan", type(source).__name__, observed_at.isoformat()))
        return live
    monkeypatch.setattr(FEAT, "assemble", assemble)
    monkeypatch.setattr(FEAT, "register", register)
    monkeypatch.setattr(SCAN, "run_from_source", run_from_source)
    monkeypatch.setattr(GPT, "candidate_metadata", lambda item: [{"symbol": "X"}, {"symbol": "Y"}])
    return calls


def test_complete_cut_is_integration_run_cut_after_the_pass(tmp_path, monkeypatch, stubbed):
    _, body, _ = sealed_0915(tmp_path / "snap", start=time(9, 20))
    caches, passed, observed = SS.a_caches_from(body)
    clock = Clock(observed)

    legacy, _ = make_handle(tmp_path / "legacy", clock)
    legacy.extra_caches = dict(caches)
    monkeypatch.setattr(AQ, "run_a_pass", lambda *args, **kwargs: passed)
    expected = legacy.run_cut()
    expected_calls = list(stubbed)
    stubbed.clear()

    handle, client = make_handle(tmp_path / "consumer", clock)
    handle.extra_caches = dict(caches)
    got = AC.complete_cut(handle, passed, observed)

    digests = got.pop("snapshot_row_digests")
    for item in (expected, got):
        item["raw_persistence"].pop("path")
    assert got == expected
    assert stubbed == expected_calls
    assert len(digests) == len(passed.usable) and client.attempts == 0


def _write_consumer_inputs(tmp_path, **kw):
    snapshots = tmp_path / "snapshots"
    sealed_0915(snapshots / SESSION.isoformat(), start=time(9, 20), **kw)
    return snapshots


def _run(tmp_path, monkeypatch, snapshots, *, symbols=A_SYMBOLS, unmapped=(), clock_at=time(9, 26)):
    clock = Clock(at(clock_at))
    handle, client = make_handle(tmp_path / "repo", clock, symbols=symbols, unmapped=unmapped)
    seen = {}

    def attach_isolated(**kwargs):
        seen.update(kwargs)
        return ISO.IsolatedAttach(active=True, handle=handle, session=SESSION, repo=tmp_path / "repo",
                                  log=lambda text: None)
    monkeypatch.setattr(INT, "attach_isolated", attach_isolated)
    calendar = SimpleNamespace(session=lambda day: object())
    report = AC.run(snapshots, tmp_path / "out", repo=tmp_path / "repo", environ=ON, session_factory=None,
                    calendar=calendar, clock=clock, sleeper=clock.tick, wait_until=time(9, 27))
    return report, handle, client, seen


def test_the_consumer_scans_from_the_snapshot_with_no_kiwoom_request(tmp_path, monkeypatch, stubbed):
    snapshots = _write_consumer_inputs(tmp_path)
    report, handle, client, seen = _run(tmp_path, monkeypatch, snapshots)
    assert report["status"] == "COMPLETE" and report["kiwoom_requests"] == 0 and client.attempts == 0
    assert report["coverage"]["complete"] is True
    assert report["a_mover_live"]["scan"]["candidates"] == 2
    assert seen["exchanges"] == {s: "ND" for s in A_SYMBOLS}       # the producer's listing, not a new one
    assert handle.caches == {}                                       # A pass symbols live in extra_caches
    parity = SS.load(snapshots / SESSION.isoformat(), SS.SNAPSHOT_0915).body
    caches, passed, observed = SS.a_caches_from(parity)
    expected = SS.row_digests(SS.a_snapshot_rows(caches, passed, session=SESSION, observed_at=observed))
    assert report["a_mover_live"]["snapshot_row_digests"] == expected
    assert (tmp_path / "out" / SESSION.isoformat() / "a_consumer.json").is_file()


def test_no_snapshot_by_the_wait_limit_fails_closed_without_a_scan(tmp_path, monkeypatch, stubbed):
    report, _, _, _ = _run(tmp_path, monkeypatch, tmp_path / "empty")
    assert report["status"] == "FAILED_CLOSED"
    assert report["a_mover_live"]["refusal"] == str(CFG.Refusal.SCANNER_NOT_RUN)
    assert report["a_mover_live"]["reason"] == AC.REASON_UNAVAILABLE
    assert report["a_mover_live"]["candidates"] == 0 and not stubbed


def test_an_incomplete_snapshot_is_never_scanned_as_a_partial_universe(tmp_path, monkeypatch, stubbed):
    snapshots = tmp_path / "snapshots"
    sealed_0915(snapshots / SESSION.isoformat(), start=time(9, 24, 58))       # the sweep runs out of time
    report, _, _, _ = _run(tmp_path, monkeypatch, snapshots)
    assert report["status"] == "FAILED_CLOSED"
    assert report["a_mover_live"]["reason"] == AC.REASON_INCOMPLETE
    assert report["coverage"]["unexpected"] > 0 and not stubbed


def test_a_snapshot_planned_over_another_union_is_refused(tmp_path, monkeypatch, stubbed):
    snapshots = _write_consumer_inputs(tmp_path)
    report, _, _, _ = _run(tmp_path, monkeypatch, snapshots, symbols=A_SYMBOLS + ["NEW"])
    assert report["status"] == "FAILED_CLOSED" and report["a_mover_live"]["reason"] == AC.REASON_UNIVERSE
    assert not stubbed


def test_a_tampered_snapshot_is_refused(tmp_path, monkeypatch, stubbed):
    snapshots = _write_consumer_inputs(tmp_path)
    manifest = SS.paths(snapshots / SESSION.isoformat(), SS.SNAPSHOT_0915)[1]
    manifest.chmod(0o644)
    manifest.write_text(manifest.read_text().replace('"sha256": "', '"sha256": "0'))
    report, _, _, _ = _run(tmp_path, monkeypatch, snapshots)
    assert report["status"] == "FAILED_CLOSED" and report["a_mover_live"]["reason"] == AC.REASON_INVALID
    assert not stubbed


def test_a_disabled_a_touches_nothing(tmp_path):
    assert AC.run(tmp_path, tmp_path / "out", environ={}) == {"status": "DISABLED"}
    assert not (tmp_path / "out").exists()


#: Modules the consumer may import: each is byte-identical in the production A/E operations tree,
#: or is the A code that tree already runs. Nothing from E's runtime, paper, broker or Kiwoom client.
ALLOWED = {"app.ae_common", "app.ae_common.snapshot", "app.market.calendar", "app.core.database",
           "app.services.mover_scanner_source", "app.strategy_e_max_rt.finalizer",
           "app.strategy_e_max_rt", "app.strategy_a_mover_live"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            found |= {node.module} if node.module != "app.ae_common" else {f"app.ae_common.{a.name}" for a in node.names}
            if node.module in ("app.strategy_a_mover_live", "app.strategy_e_max_rt"):
                found |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("app")}
    return found


@pytest.mark.parametrize("name", ["a_consumer.py", "snapshot.py"])
def test_the_consumer_side_imports_only_code_the_operations_tree_already_has(name):
    found = _imports(BACKEND / "app" / "ae_common" / name)
    for module in found:
        assert (module in ALLOWED or module.startswith("app.strategy_a_mover_live.")
                or module == "app.strategy_e_max_rt.finalizer"), module
    assert not any(m.startswith(("app.broker", "app.execution", "app.integrations.kiwoom"))
                   or m in ("app.strategy_e_max_rt.paper", "app.strategy_e_max_rt.engine") for m in found)
