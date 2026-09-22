"""The runner end to end on fixtures: readiness refusal, artifacts and determinism.

No real bars and no real store. The point is that the orchestration behaves before the data
exists: a run on an incomplete dataset stops at DATASET_NOT_READY, a run that proceeds writes
every artifact the contract requires, and a source that tries to hand a session a symbol from
outside that session's scope set is refused rather than quietly traded.
"""

from datetime import date, timedelta
import json

import pytest

from app.backtest.strategy_b_e0 import run as runner
from app.backtest.strategy_b_e0.artifacts import ArtifactExists, RunWriter, session_row
from app.backtest.strategy_b_e0.contract import CONTRACT_PATH, load_contract
from app.backtest.strategy_b_e0.gate import Verdict
from app.backtest.strategy_b_e0.identity import RunMode
from app.backtest.strategy_b_e0.universe import UniverseMember, write_universe
from app.backtest.strategy_b.engine import SessionReport, SymbolSession
from app.strategy_b.session import SessionBoundaries
from tests.strategy_b.fixtures import boundaries, dense_regular_session, make_tape

CONTRACT = load_contract()
SPEC = CONTRACT.run(CONTRACT.authoritative_label)
START = date.fromisoformat(CONTRACT.scope_start)
END = date.fromisoformat(CONTRACT.scope_end)


class Facts:
    def __init__(self, **holes):
        self.holes = holes

    def missing_scope_pairs(self, pairs):
        return self.holes.get("pairs", ())

    def symbols_missing_warmup(self, universe, sessions):
        return ()

    def symbols_missing_splits(self, symbols):
        return ()

    def sessions_missing_daily(self, sessions):
        return ()

    def schema_mismatches(self):
        return {}

    def checksum_mismatches(self, pairs):
        return ()

    def collection_completeness(self):
        return {"completeness_pct": 100.0, "absent_symbols": []}


class Source:
    """One dense session for BTEST, and nothing for any other day."""

    def __init__(self, day: date, *, smuggle: str | None = None):
        self.day = day
        self.smuggle = smuggle

    def boundaries(self, session: date) -> SessionBoundaries:
        return SessionBoundaries.standard(session)

    def symbol_sessions(self, session, symbols):
        if session != self.day:
            return ()
        wanted = list(symbols) + ([self.smuggle] if self.smuggle else [])
        return [SymbolSession(tape=make_tape(dense_regular_session(session), day=session,
                                             symbol=symbol), scope=None)
                for symbol in wanted]

    def dataset_identity(self):
        return "fixture-dataset"

    def dataset_digest(self):
        return "fixture-digest"


def universe(tmp_path, *, days=(START,), symbol="BTEST"):
    member = UniverseMember(symbol, tuple(days), min(days), max(days))
    return write_universe(tmp_path / "u.json", scope_start=START, scope_end=END,
                          sessions=list(days), members=[member], exclusions={"CON": "reserved"},
                          built_from={"document": "b_fetch_universe_q1.json", "digest": "abc"})


def test_an_incomplete_dataset_stops_the_run_at_dataset_not_ready(tmp_path):
    out = tmp_path / "runs"
    universe_ = universe(tmp_path)
    outcome = runner.execute(CONTRACT, SPEC, universe_, Source(START),
                             Facts(pairs=[("BTEST", START)]), out_root=out,
                             contract_path=CONTRACT_PATH)
    assert outcome.verdict is Verdict.DATASET_NOT_READY
    assert not outcome.preflight.ready
    assert outcome.sessions == [] and outcome.trades == []
    gate = json.loads((outcome.root / "gate_result.json").read_text(encoding="utf-8"))
    assert gate["verdict"] == "DATASET_NOT_READY"


def test_preflight_only_writes_nothing(tmp_path):
    out = tmp_path / "runs"
    outcome = runner.execute(CONTRACT, SPEC, universe(tmp_path), Source(START), Facts(),
                             out_root=out, mode=RunMode("PREFLIGHT_ONLY"),
                             contract_path=CONTRACT_PATH)
    assert outcome.preflight.ready
    assert outcome.root is None and not out.exists()


def test_a_complete_run_writes_every_required_artifact(tmp_path):
    out = tmp_path / "runs"
    universe_ = universe(tmp_path)
    outcome = runner.execute(CONTRACT, SPEC, universe_, Source(START), Facts(),
                             out_root=out, contract_path=CONTRACT_PATH)
    for name in CONTRACT.required_artifacts:
        assert (outcome.root / name).exists(), name
    manifest = json.loads((outcome.root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["initial_capital_usd"] == "7428.92"
    assert manifest["fill_scenario"] == "NEXT_BAR_OPEN"
    assert manifest["cost_level"] == "BASE"
    assert manifest["universe_sha256"] == universe_.sha256
    assert manifest["universe_symbol_count"] == universe_.symbol_count == 1
    assert manifest["universe_exclusions"] == {"CON": "reserved"}
    identity = json.loads((outcome.root / "identity.json").read_text(encoding="utf-8"))
    assert f"universe_sha256={universe_.sha256}" in identity["lines"]
    assert "provenance" in identity and identity["run_id"] == outcome.run_id
    # one session was supplied, so the sample gate cannot be met and the verdict says so
    assert outcome.verdict is Verdict.INSUFFICIENT_SAMPLE


def test_the_run_id_is_stable_across_identical_runs(tmp_path):
    first = runner.execute(CONTRACT, SPEC, universe(tmp_path / "a"), Source(START), Facts(),
                           out_root=tmp_path / "ra", contract_path=CONTRACT_PATH)
    second = runner.execute(CONTRACT, SPEC, universe(tmp_path / "b"), Source(START), Facts(),
                            out_root=tmp_path / "rb", contract_path=CONTRACT_PATH)
    assert first.run_id == second.run_id


def test_a_different_cost_level_is_a_different_run_id(tmp_path):
    base = runner.execute(CONTRACT, SPEC, universe(tmp_path / "a"), Source(START), Facts(),
                          out_root=tmp_path / "ra", contract_path=CONTRACT_PATH)
    stressed = runner.execute(CONTRACT, CONTRACT.run("STRESS_50"), universe(tmp_path / "b"),
                              Source(START), Facts(), out_root=tmp_path / "rb",
                              contract_path=CONTRACT_PATH)
    assert base.run_id != stressed.run_id


def test_a_source_smuggling_an_out_of_scope_symbol_is_refused(tmp_path):
    with pytest.raises(ValueError, match="look-ahead"):
        runner.execute(CONTRACT, SPEC, universe(tmp_path), Source(START, smuggle="SNEAK"),
                       Facts(), out_root=tmp_path / "runs", contract_path=CONTRACT_PATH)


def test_cost_model_comes_from_the_contract_level(tmp_path):
    base = runner.cost_model(CONTRACT, SPEC)
    assert (base.fee_bps_per_side, base.slippage_bps_per_side) == (10.0, 15.0)
    stressed = runner.cost_model(CONTRACT, CONTRACT.run("STRESS_50"))
    assert (stressed.fee_bps_per_side, stressed.slippage_bps_per_side) == (10.0, 40.0)


def test_artifacts_are_never_overwritten(tmp_path):
    writer = RunWriter(tmp_path / "run")
    writer.write_json("metrics.json", {"a": 1})
    with pytest.raises(ArtifactExists):
        writer.write_json("metrics.json", {"a": 2})


def test_artifact_bytes_are_deterministic(tmp_path):
    body = {"b": 2, "a": [1, 2], "c": {"z": 1, "y": 2}}
    first = RunWriter(tmp_path / "one").write_json("metrics.json", body)
    second = RunWriter(tmp_path / "two").write_json("metrics.json", dict(reversed(body.items())))
    assert first.read_bytes() == second.read_bytes()


def test_session_row_serialises_a_report():
    report = SessionReport(START, ticks=10, prefiltered_minutes=3, candidates_opened=2, entries=1)
    report.refusals = {"MAX_POSITIONS": 2}
    row = session_row(report)
    assert row["session_date"] == START.isoformat() and row["refusals"] == {"MAX_POSITIONS": 2}
    json.dumps(row)  # must be serialisable as written
