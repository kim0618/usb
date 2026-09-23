"""The authoritative B-E0 path: freeze state, identity enforcement, execution gate, smoke, determinism.

Every test here runs on fixtures or on temporary copies of the contract. None reads a real bar,
none replays a real session, and none edits ``docs/backtest/strategy_b/b_e0_contract_v1.json``.
The trade path is exercised by a synthetic breakout tape built for the plumbing only: it is the
engine tests' breakout moved onto contract-window dates, with no threshold changed to produce it.
"""

from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import shutil

import pytest

from app.backtest.strategy_b.engine import SymbolSession
from app.backtest.strategy_b_e0 import artifacts, execution_differential, freeze, metrics
from app.backtest.strategy_b_e0 import run as runner
from app.backtest.strategy_b_e0.contract import (
    CHECKSUM_PATH, CONTRACT_PATH, Contract, ContractChanged, load_contract)
from app.backtest.strategy_b_e0.identity import RunMode, identity_lines
from app.backtest.strategy_b_e0.universe import UniverseMember, write_universe
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.scope import ScopeDecision
from app.strategy_b.session import SessionBoundaries
from tests.strategy_b.fixtures import flat_volume_profile, make_bar, make_tape, past_session_dates
from tests.strategy_b.test_strategy_b_e0_runner import Facts

CONTRACT = load_contract()
SPEC = CONTRACT.run(CONTRACT.authoritative_label)
START = date.fromisoformat(CONTRACT.scope_start)
END = date.fromisoformat(CONTRACT.scope_end)
DAYS = (START, date(2026, 5, 19), date(2026, 5, 20))
PREVIOUS_DRAFT = "a1f2605b6bc7082b0a9704fe0a623d8fc1ab494602d74da1f9be4b1d8f3f0bb6"
PARITY_DRAFT = "fc3db6cb7ca366e921cdfcedd754a8a2f6bf15f2eec24c5b23071f334bddba52"
REGISTERED = tuple(CONTRACT.raw["data_adapter"]["evidence"]["warnings"])
REAL_UNIVERSE = CONTRACT_PATH.parents[3] / "data/runtime/strategy_b_e0/b_e0_run_universe_v1.json"


# ---- fixtures -----------------------------------------------------------------------------------


def breakout(day: date, symbol: str):
    """The engine tests' breakout (flat, a +3% minute, a shallow hold, a break of the high) on
    ``day``, followed by flat actual bars so NEXT_BAR_OPEN has a next bar and the time stop fires."""
    bars = [make_bar(9, 30 + k, 10.00, 3000.0, open_=10.00, high=10.00, low=10.00, day=day)
            for k in range(10)]
    bars.append(make_bar(9, 40, 10.30, 40_000.0, open_=10.01, high=10.35, low=10.00, day=day))
    bars += [make_bar(9, 41 + k, 10.25, 8_000.0, open_=10.26, high=10.30, low=10.20, day=day)
             for k in range(3)]
    bars.append(make_bar(9, 44, 10.45, 20_000.0, open_=10.30, high=10.50, low=10.28, day=day))
    bars += [make_bar(9 + (45 + k) // 60, (45 + k) % 60, 10.45, 5_000.0, open_=10.45,
                      high=10.46, low=10.44, day=day) for k in range(40)]
    return make_tape(bars, day=day, symbol=symbol)


class TradeSource:
    """Every requested symbol gets the breakout tape, a complete SymbolSession and a full baseline."""

    def __init__(self):
        self.dynamic, self.withheld = [], []

    def boundaries(self, session: date) -> SessionBoundaries:
        return SessionBoundaries.standard(session)

    def symbol_sessions(self, session, symbols):
        history = tuple(flat_volume_profile(day, 100.0) for day in past_session_dates(20, session))
        self.dynamic.append({"session": session.isoformat(), "requested": len(symbols)})
        return [SymbolSession(tape=breakout(session, symbol),
                              scope=ScopeDecision(symbol, session, True, (), 10.0, 5_000_000.0, 20),
                              rvol_history=history)
                for symbol in sorted(symbols)]

    def dataset_identity(self):
        return "fixture-trade-dataset"

    def dataset_digest(self):
        return "fixture-trade-digest"


class WarnedFacts(Facts):
    def __init__(self, warnings=REGISTERED, **holes):
        super().__init__(**holes)
        self._warnings = list(warnings)

    def warnings(self):
        return list(self._warnings)


def fixture_universe(tmp_path: Path, days=DAYS, symbols=("BTEST", "CTEST")):
    members = [UniverseMember(s, tuple(days), min(days), max(days)) for s in symbols]
    return write_universe(tmp_path / "u.json", scope_start=START, scope_end=END,
                          sessions=list(days), members=members, exclusions={"CON": "reserved"},
                          built_from={"document": "fixture", "digest": "abc"})


def smoke(tmp_path: Path, out: str = "runs", days=DAYS, **kwargs):
    return runner.execute(CONTRACT, SPEC, fixture_universe(tmp_path / f"u-{out}"), TradeSource(),
                          WarnedFacts(), out_root=tmp_path / out, contract_path=CONTRACT_PATH,
                          mode=RunMode("SMOKE"), sessions=days, expected_warnings=REGISTERED,
                          **kwargs)


def contract_copy(tmp_path: Path) -> tuple[Path, Path]:
    """A DRAFT copy of the real contract: the freeze keys removed and a matching ledger, so the
    freeze tests run the same way before and after the real contract is frozen."""
    import hashlib
    from app.backtest.strategy_c_selection.rules import canonical_checksum
    folder = tmp_path / "contract"
    folder.mkdir()
    raw = {k: v for k, v in CONTRACT.raw.items() if k not in freeze.FREEZE_KEYS}
    target = folder / CONTRACT_PATH.name
    target.write_text(json.dumps(raw, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    lines, skipping = [], False
    for line in CHECKSUM_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("# status:"):
            lines.append("# status:    DRAFT_PENDING_APPROVAL. Test copy.")
            skipping = True
            continue
        if skipping and line.startswith("#            "):
            continue
        skipping = False
        if not line.startswith(("canonical ", "file ", "frozen ")):
            lines.append(line)
    lines += [f"canonical  {canonical_checksum(raw)}  {target.name}",
              f"file       {hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}"]
    ledger = folder / CHECKSUM_PATH.name
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target, ledger


def passing_checks():
    return [freeze.Precondition("fixture", True, "all preconditions stubbed for the copy")]


FIXTURE_CODE = {"version": "fixture", "code_digest": "c" * 64, "file_count": 1,
                "files": {"app/x.py": "0" * 64}}


def frozen_copy(tmp_path: Path, code=FIXTURE_CODE) -> tuple[Path, Path, Contract]:
    contract_path, checksum_path = contract_copy(tmp_path)
    draft = load_contract(contract_path)
    frozen = freeze.freeze(contract_path, checksum_path, tmp_path / "ledger.jsonl",
                           checks=passing_checks(), frozen_at="2026-09-22T10:00:00+09:00",
                           source_commit="fixture", pre_freeze_checksum=draft.canonical_checksum,
                           preflight_sha256="p" * 64,
                           execution={"verdict": "PASS", "digest": "e" * 64}, code=code,
                           config_fingerprint=StrategyBConfig().fingerprint())
    return contract_path, checksum_path, frozen


# ---- freeze state ---------------------------------------------------------------------------------


def test_the_real_contract_is_frozen_from_the_parity_draft():
    assert freeze.contract_state(CONTRACT) == freeze.FROZEN
    assert CONTRACT.raw["freeze"]["pre_freeze_checksum"] == PARITY_DRAFT
    bound = freeze.require_frozen(CONTRACT, CHECKSUM_PATH)
    assert bound.code_digest and bound.contract_sha256 == CONTRACT.canonical_checksum
    ledger = CHECKSUM_PATH.read_text(encoding="utf-8")
    assert PREVIOUS_DRAFT in ledger and PARITY_DRAFT in ledger
    assert CONTRACT.raw["costs"]["commission_notional_basis"] == "PRE_SLIPPAGE_REFERENCE_PRICE"


def test_a_draft_contract_is_refused_for_an_authoritative_run(tmp_path):
    contract_path, checksum_path = contract_copy(tmp_path)
    with pytest.raises(freeze.ContractNotFrozen, match="contract_state is DRAFT"):
        freeze.require_frozen(load_contract(contract_path), checksum_path)


def test_a_frozen_contract_loads_and_binds_the_evidence_identity(tmp_path):
    contract_path, checksum_path, frozen = frozen_copy(tmp_path)
    bound = freeze.require_frozen(frozen, checksum_path)
    evidence = CONTRACT.raw["data_adapter"]["evidence"]
    draft_sha = frozen.raw["freeze"]["pre_freeze_checksum"]
    assert bound.contract_sha256 == frozen.canonical_checksum != draft_sha
    assert bound.code_digest == FIXTURE_CODE["code_digest"]
    assert bound.dataset_digest == evidence["dataset_digest"]
    assert bound.universe_sha256 == evidence["universe_sha256"]
    assert bound.warnings == REGISTERED
    ledger = checksum_path.read_text(encoding="utf-8")
    assert f"{draft_sha}\n#       DRAFT final pre-freeze" in ledger
    assert f"{frozen.canonical_checksum}  FROZEN authoritative contract" in ledger
    assert "15e71a0e5aa01cb31f5ffbb5267742795352dc6d218d52fd0bb48e961c58ee4e" in ledger
    record = json.loads((tmp_path / "ledger.jsonl").read_text(encoding="utf-8"))
    for key in ("strategy_id", "contract_version", "contract_sha256", "frozen_at", "source_commit",
                "universe_sha256", "dataset_digest", "preflight_digest", "preflight_verdict",
                "initial_capital", "execution_model", "signal_ttl_minutes", "code_digest",
                "execution_differential_digest", "config_fingerprint", "pre_freeze_checksum"):
        assert key in record, key
    assert (record["initial_capital"], record["execution_model"],
            record["signal_ttl_minutes"]) == ("7428.92", "NEXT_BAR_OPEN", 2)


def test_the_freeze_changes_nothing_but_the_state_and_its_block(tmp_path):
    _, _, frozen = frozen_copy(tmp_path)
    before = {k: v for k, v in CONTRACT.raw.items() if k not in freeze.FREEZE_KEYS}
    after = {k: v for k, v in frozen.raw.items() if k not in freeze.FREEZE_KEYS}
    assert before == after
    assert frozen.rules_checksum == CONTRACT.rules_checksum


def test_a_failed_precondition_leaves_the_draft_untouched(tmp_path):
    contract_path, checksum_path = contract_copy(tmp_path)
    before = contract_path.read_bytes(), checksum_path.read_bytes()
    with pytest.raises(freeze.FreezeRefused, match="execution_equivalence"):
        freeze.freeze(contract_path, checksum_path, tmp_path / "ledger.jsonl",
                      checks=[freeze.Precondition("execution_equivalence", False, "FAIL")],
                      frozen_at="x", source_commit="x", 
                      pre_freeze_checksum=load_contract(contract_path).canonical_checksum,
                      preflight_sha256="x", execution={})
    assert (contract_path.read_bytes(), checksum_path.read_bytes()) == before
    assert not (tmp_path / "ledger.jsonl").exists()


def test_a_frozen_contract_cannot_be_frozen_again(tmp_path):
    contract_path, checksum_path, frozen = frozen_copy(tmp_path)
    with pytest.raises(freeze.FreezeRefused):
        freeze.freeze(contract_path, checksum_path, tmp_path / "ledger.jsonl",
                      checks=passing_checks(), frozen_at="y", source_commit="y",
                      pre_freeze_checksum=frozen.canonical_checksum, preflight_sha256="y",
                      execution={})


MUTATIONS = {
    "commission_basis": lambda r: r["costs"].__setitem__("commission_notional_basis",
                                                         "AFTER_SLIPPAGE_FILL_PRICE"),
    "commission_rate": lambda r: r["costs"].__setitem__("commission_bps_per_side", 5.0),
    "signal_ttl": lambda r: r["signal_ttl"].__setitem__("signal_ttl_minutes", 3),
    "initial_capital": lambda r: r["capital_policy"].__setitem__("initial_capital_usd", "10000"),
    "cost_sensitivity": lambda r: r["cost_sensitivity"]["levels"][1].__setitem__(
        "execution_cost_bps_per_side", 25.0),
    "dataset_digest": lambda r: r["data_adapter"]["evidence"].__setitem__("dataset_digest", "0" * 64),
    "universe_sha256": lambda r: r["data_adapter"]["evidence"].__setitem__("universe_sha256", "0" * 64),
    "lift_gate": lambda r: r["lift_gate"]["measure"].__setitem__("horizon_minutes", 45),
    "sample_gate": lambda r: r["verdict"]["sample_gate"].__setitem__("min_closed_trades", 20),
    "parity_tolerance": lambda r: r["execution_parity"].__setitem__("rel_tol", "1e-6"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_any_edit_to_a_frozen_contract_is_refused(name, tmp_path):
    contract_path, checksum_path, _ = frozen_copy(tmp_path)
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    MUTATIONS[name](raw)
    contract_path.write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ContractChanged):
        load_contract(contract_path)


def test_a_state_line_alone_does_not_make_a_contract_frozen(tmp_path):
    contract_path, checksum_path = contract_copy(tmp_path)
    text = checksum_path.read_text(encoding="utf-8").replace(
        "# status:    DRAFT_PENDING_APPROVAL", "# status:    FROZEN")
    checksum_path.write_text(text, encoding="utf-8")
    with pytest.raises(freeze.ContractNotFrozen, match="contract_state"):
        freeze.require_frozen(load_contract(contract_path), checksum_path)


# ---- identity enforcement ----------------------------------------------------------------------------


def _bound():
    return freeze.binding(CONTRACT)


class _U:
    def __init__(self, sha, sessions=84, pairs=285175):
        self.sha256, self.sessions = sha, [None] * sessions
        self._pairs = pairs

    def required_pairs(self):
        return [None] * self._pairs


def _manifest(digest=None, universe=None):
    bound = _bound()
    return {"dataset_digest": digest or bound.dataset_digest,
            "universe_sha256": universe or bound.universe_sha256}


def test_the_exact_frozen_identity_is_accepted():
    from app.dev.run_strategy_b_e0 import check_binding
    check_binding(_bound(), _U(_bound().universe_sha256), _manifest())


@pytest.mark.parametrize("universe, manifest, message", [
    (_U("0" * 64), _manifest(), "universe sha256"),
    (_U(freeze.binding(CONTRACT).universe_sha256), _manifest(digest="f" * 64), "dataset digest"),
    (_U(freeze.binding(CONTRACT).universe_sha256, sessions=83), _manifest(), "universe sessions"),
    (_U(freeze.binding(CONTRACT).universe_sha256, pairs=285174), _manifest(), "required pairs"),
])
def test_any_identity_mismatch_is_run_refused(universe, manifest, message):
    from app.dev.run_strategy_b_e0 import check_binding
    with pytest.raises(runner.RunRefused, match=f"RUN_REFUSED.*{message}"):
        check_binding(_bound(), universe, manifest)


@pytest.mark.parametrize("flag", ["--force", "--ignore-contract", "--ignore-checksum",
                                  "--ignore-dataset-digest", "--ignore-universe-hash",
                                  "--allow-partial", "--skip-missing", "--ignore-preflight",
                                  "--allow-concurrent-writer", "--for", "--allow"])
def test_no_bypass_flag_exists_on_the_run_command(flag, tmp_path):
    from app.dev.run_strategy_b_e0 import main
    with pytest.raises(SystemExit) as raised:
        main(["run", "--universe", str(tmp_path / "u.json"), "--dataset", str(tmp_path),
              "--out", str(tmp_path / "o"), flag])
    assert raised.value.code == 2  # argparse usage error, before anything is read


@pytest.mark.skipif(not REAL_UNIVERSE.is_file(), reason="needs the local universe artifact")
def test_the_frozen_contract_refuses_a_run_before_writing_anything(tmp_path):
    """Whichever guard fires first, nothing is written.

    The code-digest guard runs before the dataset is touched, so once another session edits a file
    inside the frozen closure (app/strategy/* are in it) this refuses on the digest rather than on
    the missing mirror. Both are refusals before any artifact exists, which is what this pins.
    """
    from app.backtest.strategy_b_e0.mirror import MirrorFailed
    from app.dev.run_strategy_b_e0 import main
    with pytest.raises((runner.RunRefused, MirrorFailed)):
        main(["run", "--universe", str(REAL_UNIVERSE), "--dataset", str(tmp_path),
              "--out", str(tmp_path / "out"), "--smoke-sessions", "1"])
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not REAL_UNIVERSE.is_file(), reason="needs the local universe artifact")
def test_a_frozen_contract_is_refused_when_the_code_digest_moved(tmp_path):
    from app.dev.run_strategy_b_e0 import main
    contract_path, _, _ = frozen_copy(tmp_path)  # binds a fixture digest, not the real tree's
    with pytest.raises(runner.RunRefused, match="code digest"):
        main(["--contract", str(contract_path), "run", "--universe", str(REAL_UNIVERSE),
              "--dataset", str(tmp_path / "no-dataset"), "--out", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not REAL_UNIVERSE.is_file(), reason="needs the local universe artifact")
def test_a_failing_differential_refuses_even_a_fully_bound_run(tmp_path, monkeypatch):
    from app.backtest.strategy_b_e0 import code_identity
    from app.dev.run_strategy_b_e0 import main
    code = code_identity.identity(CONTRACT_PATH.parents[3] / "backend")
    contract_path, _, _ = frozen_copy(tmp_path, code=code)
    failing = execution_differential.Report(execution_differential.FAIL, "BASE", {})
    monkeypatch.setattr(execution_differential, "run", lambda *a, **k: failing)
    with pytest.raises(runner.RunRefused, match="AUTHORITATIVE_RUN_NOT_READY"):
        main(["--contract", str(contract_path), "run", "--universe", str(REAL_UNIVERSE),
              "--dataset", str(tmp_path / "no-dataset"), "--out", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_the_working_tree_code_digest_is_deterministic_and_names_moved_files():
    from app.backtest.strategy_b_e0 import code_identity
    backend = CONTRACT_PATH.parents[3] / "backend"
    first, second = code_identity.identity(backend), code_identity.identity(backend)
    assert first == second
    for name in ("app/backtest/strategy_b/portfolio.py", "app/backtest/strategy_b/costs.py",
                 "app/broker/sim.py", "app/broker/accounting.py", "app/strategy_b/fsm.py",
                 "app/strategy_b/config.py", "app/dev/run_strategy_b_e0.py",
                 "app/backtest/strategy_b_e0/run.py"):
        assert name in first["files"], name
    moved = dict(first["files"], **{"app/strategy_b/fsm.py": "0" * 64})
    assert code_identity.changed_files(first["files"], moved) == ["app/strategy_b/fsm.py"]


def test_preflight_blocked_is_not_replayed(tmp_path):
    outcome = runner.execute(CONTRACT, SPEC, fixture_universe(tmp_path), TradeSource(),
                             WarnedFacts(pairs=[("BTEST", START)]), out_root=tmp_path / "runs",
                             contract_path=CONTRACT_PATH, expected_warnings=REGISTERED)
    assert outcome.verdict.value == "DATASET_NOT_READY"
    assert outcome.sessions == [] and outcome.trades == []


def test_pass_with_warnings_is_runnable_when_the_warnings_are_registered(tmp_path):
    outcome = smoke(tmp_path, days=DAYS[:1])
    assert outcome.preflight.status == "PASS_WITH_WARNINGS" and outcome.preflight.ready
    assert len(outcome.sessions) == 1


def test_a_warning_the_contract_did_not_register_refuses_the_run(tmp_path):
    with pytest.raises(runner.RunRefused, match="did not register"):
        runner.execute(CONTRACT, SPEC, fixture_universe(tmp_path), TradeSource(),
                       WarnedFacts(warnings=[*REGISTERED, "1 new surprise"]),
                       out_root=tmp_path / "runs", contract_path=CONTRACT_PATH,
                       mode=RunMode("SMOKE"), sessions=DAYS[:1], expected_warnings=REGISTERED)
    assert not (tmp_path / "runs").exists()


def test_a_session_subset_is_smoke_only(tmp_path):
    with pytest.raises(runner.RunRefused, match="SMOKE only"):
        runner.execute(CONTRACT, SPEC, fixture_universe(tmp_path), TradeSource(), WarnedFacts(),
                       out_root=tmp_path / "runs", contract_path=CONTRACT_PATH,
                       sessions=DAYS[:1])


def test_smoke_and_strict_never_share_a_run_id(tmp_path):
    universe = fixture_universe(tmp_path)
    kwargs = dict(dataset_identity="d", dataset_digest="g", code="c", config_fingerprint="f")
    strict = identity_lines(CONTRACT, SPEC, universe, mode=RunMode("STRICT"), **kwargs)
    one = identity_lines(CONTRACT, SPEC, universe, mode=RunMode("SMOKE"), sessions=DAYS[:1], **kwargs)
    two = identity_lines(CONTRACT, SPEC, universe, mode=RunMode("SMOKE"), sessions=DAYS[:2], **kwargs)
    assert len({strict, one, two}) == 3
    assert "config_fingerprint=f" in strict  # the contract's run_identity lists it
    assert "execution_model=NEXT_BAR_OPEN" in strict and "signal_ttl_minutes=2" in strict
    with pytest.raises(ValueError, match="every session"):
        identity_lines(CONTRACT, SPEC, universe, mode=RunMode("STRICT"), sessions=DAYS[:1], **kwargs)


# ---- the execution dependency and the differential gate -------------------------------------------------


def test_the_b_execution_path_does_not_import_the_a_broker():
    root = CONTRACT_PATH.parents[3] / "backend/app"
    for package in ("strategy_b", "backtest/strategy_b"):
        for path in (root / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "from app.broker" not in text and "import app.broker" not in text, path


def test_the_differential_is_deterministic_and_compares_every_declared_field():
    first = execution_differential.run(CONTRACT, "BASE")
    second = execution_differential.run(CONTRACT, "BASE")
    assert first.as_dict() == second.as_dict()
    for fields in first.rows.values():
        assert tuple(fields) == execution_differential.FIELDS


def test_execution_equivalence_passes_on_the_three_registered_fixtures():
    report = execution_differential.run(CONTRACT, "BASE")
    assert report.verdict == execution_differential.PASS and not report.failing
    assert [case[0] for case in execution_differential.CASES] == ["win", "loss", "position_cap"]
    assert len(report.rows) * len(execution_differential.FIELDS) == 3 * 19
    for fields in report.rows.values():
        for name in execution_differential.EXACT_FIELDS:
            assert fields[name]["comparison_mode"] == "EXACT" and fields[name]["passed"], name
        for name in execution_differential.NUMERIC_FIELDS:
            row = fields[name]
            assert row["comparison_mode"] == "NUMERIC_PARITY" and row["passed"], name
            for key in ("A", "B", "absolute_difference", "relative_difference", "allowed_tolerance"):
                assert key in row


@pytest.mark.parametrize("case", ["win", "loss", "position_cap"])
@pytest.mark.parametrize("field", ["commission_entry", "commission_exit"])
def test_b_commission_equals_a_commission(case, field):
    """Commission notional basis parity: B charges on the pre-slippage price, as A does."""
    from decimal import Decimal
    row = execution_differential.run(CONTRACT, "BASE").rows[case][field]
    assert Decimal(row["A"]) == Decimal(row["B"])


def test_the_policy_in_code_is_the_one_the_contract_preregistered():
    block = CONTRACT.raw["execution_parity"]
    assert block["exact_fields"] == list(execution_differential.EXACT_FIELDS)
    assert block["numeric_parity_fields"] == list(execution_differential.NUMERIC_FIELDS)
    assert block["fields"] == list(execution_differential.FIELDS)
    assert (block["rel_tol"], block["abs_tol"]) == (str(execution_differential.REL_TOL),
                                                    str(execution_differential.ABS_TOL))
    assert [f["label"] for f in block["fixtures"]] == [c[0] for c in execution_differential.CASES]
    assert set(execution_differential.EXACT_FIELDS) | set(execution_differential.NUMERIC_FIELDS) \
        == set(execution_differential.FIELDS)


def test_numeric_tolerance_pass_fail_and_boundary():
    from decimal import Decimal
    compare, N = execution_differential.compare, execution_differential.NUMERIC_PARITY
    assert compare(Decimal("1.49931"), 1.499310000000074, N)["passed"]      # float repr noise
    assert not compare(Decimal("0.99954"), 1.00103931, N)["passed"]          # the old basis
    base = Decimal("1000")
    at_edge = base + base * execution_differential.REL_TOL
    assert compare(base, at_edge, N)["passed"]                               # boundary inclusive
    assert not compare(base, at_edge + Decimal("1e-12"), N)["passed"]
    assert compare(Decimal("0"), Decimal("1e-12"), N)["passed"]              # abs floor near zero
    assert not compare(Decimal("0"), Decimal("2e-12"), N)["passed"]
    assert not compare(Decimal("0.01"), Decimal("0.02"), N)["passed"]         # no cent tolerance


def test_exact_fields_accept_nothing_but_equality():
    E = execution_differential.EXACT
    compare = execution_differential.compare
    assert compare(81, 81, E)["passed"] and not compare(81, 82, E)["passed"]
    assert not compare(True, False, E)["passed"] and not compare("FLAT", "OPEN", E)["passed"]
    assert not compare(10, 10.000000000001, E)["passed"]


# ---- smoke on the synthetic trade fixture ------------------------------------------------------------------


def test_one_session_smoke_goes_from_source_to_every_artifact(tmp_path):
    outcome = smoke(tmp_path, days=DAYS[:1])
    assert outcome.root == tmp_path / "runs" / outcome.run_id
    assert artifacts.is_complete(outcome.root)
    for name in (*CONTRACT.required_artifacts, "lift.json", "daily_metrics.jsonl"):
        assert (outcome.root / name).exists(), name
    assert outcome.trades and all(t.is_closed for t in outcome.trades)
    assert outcome.candidates
    manifest = json.loads((outcome.root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_mode"] == "SMOKE" and manifest["verdict_eligible"] is False
    assert manifest["sessions_replayed"] == [START.isoformat()]
    assert manifest["dataset_warnings"] == list(REGISTERED)
    assert manifest["bootstrap_seed"] == CONTRACT.statistics.seed


def test_multi_session_smoke_carries_equity_and_resets_the_day(tmp_path):
    outcome = smoke(tmp_path)
    assert [r.session_date for r in outcome.sessions] == list(DAYS)
    by_day = {}
    for trade in outcome.trades:
        by_day.setdefault(trade.session_date, []).append(trade)
    assert set(by_day) == set(DAYS)  # the per-symbol entry limit reset every morning
    rows = [json.loads(line) for line in
            (outcome.root / "daily_metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    equity = float(CONTRACT.initial_capital_usd)
    for row in rows:
        equity += row["net_pnl"]
        assert row["equity_end"] == pytest.approx(equity)
    # sizing on day 2 reads day 1's realised equity (the cap binds: floor(equity * 20% / price))
    second = by_day[DAYS[1]][0]
    equity_after_day1 = rows[0]["equity_end"]
    cap = int(equity_after_day1 * CONTRACT.raw["sizing"]["max_position_pct"] / 100
              // second.entry_price)
    assert by_day[DAYS[1]][0].shares + by_day[DAYS[1]][1].shares <= 2 * cap + 1
    assert rows[0]["equity_end"] != float(CONTRACT.initial_capital_usd)
    source_log = (outcome.root / "source_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(source_log) == len(DAYS)


def test_two_identical_smoke_runs_are_byte_identical(tmp_path):
    first = smoke(tmp_path, out="one")
    second = smoke(tmp_path, out="two")
    assert first.run_id == second.run_id
    names = sorted(p.name for p in first.root.iterdir())
    assert names == sorted(p.name for p in second.root.iterdir())
    for name in names:
        assert (first.root / name).read_bytes() == (second.root / name).read_bytes(), name


def test_a_complete_run_is_never_rerun_into_place(tmp_path):
    first = smoke(tmp_path, days=DAYS[:1])
    before = {p.name: p.read_bytes() for p in first.root.iterdir()}
    with pytest.raises(artifacts.RunExists, match="already COMPLETE"):
        runner.execute(CONTRACT, SPEC, fixture_universe(tmp_path / "again"), TradeSource(),
                       WarnedFacts(), out_root=tmp_path / "runs", contract_path=CONTRACT_PATH,
                       mode=RunMode("SMOKE"), sessions=DAYS[:1], expected_warnings=REGISTERED)
    assert {p.name: p.read_bytes() for p in first.root.iterdir()} == before


def test_a_crash_before_the_marker_leaves_no_run_directory(tmp_path, monkeypatch):
    def crash(*args, **kwargs):
        raise OSError("simulated crash before COMPLETE")

    monkeypatch.setattr(artifacts, "finalize", crash)
    with pytest.raises(OSError, match="simulated"):
        smoke(tmp_path, days=DAYS[:1])
    run_dirs = [p for p in (tmp_path / "runs").iterdir()]
    assert [p.name.startswith(artifacts.STAGING_PREFIX) for p in run_dirs] == [True]
    assert not any(artifacts.is_complete(p) for p in run_dirs)
    monkeypatch.undo()
    with pytest.raises(artifacts.RunExists, match="crashed run"):
        runner.execute(CONTRACT, SPEC, fixture_universe(tmp_path / "again"), TradeSource(),
                       WarnedFacts(), out_root=tmp_path / "runs", contract_path=CONTRACT_PATH,
                       mode=RunMode("SMOKE"), sessions=DAYS[:1], expected_warnings=REGISTERED)


# ---- the real session source through the runner -----------------------------------------------------------


def test_the_local_session_source_feeds_the_runner(tmp_path, monkeypatch):
    """LocalSessionSource over the synthetic mirror, through execute(). The fixture's window is not
    the contract's, so the preflight is stubbed to ready; everything after it is the real path."""
    from app.backtest.strategy_b_e0.preflight import PreflightReport
    from tests.strategy_b.test_strategy_b_e0_adapter import SCOPE, World

    world = World(tmp_path / "world")
    report = PreflightReport()
    report.warnings = list(REGISTERED)
    report.settle()
    monkeypatch.setattr(runner, "run_preflight", lambda *a, **k: report)
    outcome = runner.execute(CONTRACT, SPEC, world.universe, world.source(), world.facts,
                             out_root=tmp_path / "runs", contract_path=CONTRACT_PATH,
                             mode=RunMode("SMOKE"), sessions=tuple(SCOPE[:2]),
                             expected_warnings=REGISTERED)
    assert [r.session_date for r in outcome.sessions] == SCOPE[:2]
    log = [json.loads(line) for line in
           (outcome.root / "source_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["record"] for row in log} == {"session_load", "withheld"}
    withheld = [row for row in log if row["record"] == "withheld"]
    assert [(row["symbol"], row["session"]) for row in withheld] == [("GAPX", "2026-06-12")]


def test_smoke_session_choice_is_deterministic_and_skips_quality_sessions():
    from app.dev.run_strategy_b_e0 import smoke_sessions

    class _Facts:
        def early_close_sessions(self):
            return []

        def quality_exclusions(self):
            return [type("E", (), {"session": date(2026, 5, 20)})()]

    class _Universe:
        sessions = tuple(START + timedelta(days=k) for k in range(5))

    chosen = smoke_sessions(_Universe(), _Facts(), 2)
    assert chosen == smoke_sessions(_Universe(), _Facts(), 2)
    assert date(2026, 5, 20) not in chosen and chosen == (date(2026, 5, 21), date(2026, 5, 22))


# ---- gate and seed wiring ---------------------------------------------------------------------------------


def test_gate_numbers_and_seed_are_read_from_the_contract():
    raw = json.loads(json.dumps(CONTRACT.raw))
    assert CONTRACT.min_closed_trades == raw["verdict"]["sample_gate"]["min_closed_trades"]
    assert CONTRACT.min_mean_net_r == raw["verdict"]["thresholds"]["min_mean_net_r"]
    assert CONTRACT.lift_horizon_minutes == raw["lift_gate"]["measure"]["horizon_minutes"]
    assert {spec.label for spec in CONTRACT.runs} >= {"BASE", "STRESS_30", "STRESS_50"}
    same = metrics.draws(10, CONTRACT.statistics)
    assert (same == metrics.draws(10, CONTRACT.statistics)).all()
    raw["statistics"]["seed"] = CONTRACT.statistics.seed + 1
    other = Contract(raw, "x").statistics
    assert not (metrics.draws(10, other) == same).all()
