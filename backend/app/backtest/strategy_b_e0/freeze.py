"""The B-E0 contract's FROZEN state: what it binds, how it is checked, and the one way to reach it.

A draft contract can be edited and re-hashed as often as the evidence needs, because no result
exists yet. A frozen one cannot. Three things must agree before any code treats the contract as
frozen, so that no single edited line can fake the state:

* the JSON carries ``contract_state = FROZEN`` and a ``freeze`` block (``frozen_at``, the source
  commit, the pre-freeze checksum and the identity it binds);
* the ``.sha256`` ledger's status line says FROZEN;
* the ledger's ``frozen`` line equals the JSON's canonical checksum, which the loader has already
  compared with the ledger's ``canonical`` line.

``freeze`` is the only writer. It refuses unless every precondition passed, keeps the draft's
last checksum in the ledger history, and appends one machine-readable record to the freeze
ledger. Freezing is a state change of the contract, not a change of any rule, threshold or policy
in it; ``freeze`` asserts that by comparing everything outside the added keys.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path

from app.backtest.strategy_b_e0.contract import Contract, ContractInvalid, load_contract
from app.backtest.strategy_c_selection.rules import canonical_checksum

FROZEN = "FROZEN"
DRAFT = "DRAFT_PENDING_APPROVAL"
FREEZE_KEYS = ("contract_state", "freeze")
LEDGER_FORMAT = "b-e0-freeze-ledger-v1"


class ContractNotFrozen(RuntimeError):
    """An authoritative B-E0 run needs a FROZEN contract. Preflight does not."""


class FreezeRefused(RuntimeError):
    """A freeze precondition failed, so the contract stays a draft."""


@dataclass(frozen=True, slots=True)
class Binding:
    """The exact identity a FROZEN contract allows an authoritative run to use."""

    contract_sha256: str
    universe_sha256: str
    dataset_digest: str
    preflight_status: str
    preflight_artifact_sha256: str
    required_pairs: int
    scope_sessions: int
    warnings: tuple[str, ...]
    code_digest: str | None = None
    """The working-tree code identity recorded at freeze (None while the contract is a draft)."""


@dataclass(frozen=True, slots=True)
class Precondition:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def ledger_status(checksum_path: Path) -> str:
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# status:"):
            return line.split(":", 1)[1].strip().split(".")[0].split(",")[0].strip()
    return "UNKNOWN"


def ledger_frozen_checksum(checksum_path: Path) -> str | None:
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("frozen "):
            return line.split()[1]
    return None


def contract_state(contract: Contract) -> str:
    return str(contract.raw.get("contract_state", DRAFT))


def binding(contract: Contract) -> Binding:
    """The run identity the contract's recorded evidence names."""
    evidence = contract.raw["data_adapter"]["evidence"]
    return Binding(
        contract_sha256=contract.canonical_checksum,
        universe_sha256=str(evidence["universe_sha256"]),
        dataset_digest=str(evidence["dataset_digest"]),
        preflight_status=str(evidence["preflight_status"]),
        preflight_artifact_sha256=str(evidence["preflight_artifact_sha256"]),
        required_pairs=int(evidence["required_pairs"]),
        scope_sessions=int(contract.raw["run_window"]["scope_sessions"]),
        warnings=tuple(str(w) for w in evidence["warnings"]),
        code_digest=contract.raw.get("freeze", {}).get("code_digest"))


def require_frozen(contract: Contract, checksum_path: Path) -> Binding:
    """The binding of a FROZEN contract, or ContractNotFrozen naming which of the three disagreed."""
    problems = []
    if contract_state(contract) != FROZEN:
        problems.append(f"contract_state is {contract_state(contract)}")
    if "freeze" not in contract.raw:
        problems.append("the contract has no freeze block")
    status = ledger_status(checksum_path)
    if status != FROZEN:
        problems.append(f"the checksum ledger status is {status}")
    frozen = ledger_frozen_checksum(checksum_path)
    if frozen != contract.canonical_checksum:
        problems.append(f"the ledger's frozen checksum {frozen} is not the contract's "
                        f"{contract.canonical_checksum}")
    if problems:
        raise ContractNotFrozen("an authoritative B-E0 run needs a FROZEN contract: "
                                + "; ".join(problems))
    return binding(contract)


# ---- preconditions ------------------------------------------------------------------------------


def preconditions(contract: Contract, *, universe_sha256: str, universe_sessions: int,
                  preflight: Mapping[str, object], preflight_sha256: str,
                  config_agreement_passed: bool, differential_verdict: str,
                  strategy_logic_changed: bool) -> list[Precondition]:
    """Every condition the freeze requires, evaluated from what is on disk now."""
    bound = binding(contract)
    gate = contract.raw["data_adapter"]["readiness"]
    gap = contract.raw["execution_model"]["fill_rule"]["implementation_gap"]["status"]
    failed = [c for c in preflight["preflight"]["checks"] if not c["passed"]]

    def check(name: str, passed: bool, detail: str) -> Precondition:
        return Precondition(name, bool(passed), detail)

    return [
        check("contract_state_is_draft", contract_state(contract) == DRAFT,
              f"state {contract_state(contract)}"),
        check("config_agreement", config_agreement_passed, "contract restated values == config"),
        check("universe_sha256", universe_sha256 == bound.universe_sha256,
              f"artifact {universe_sha256} vs evidence {bound.universe_sha256}"),
        check("universe_sessions", universe_sessions == bound.scope_sessions,
              f"{universe_sessions} vs {bound.scope_sessions}"),
        check("dataset_digest", preflight["dataset_digest"] == bound.dataset_digest,
              f"preflight {preflight['dataset_digest']} vs evidence {bound.dataset_digest}"),
        check("preflight_artifact_sha256", preflight_sha256 == bound.preflight_artifact_sha256,
              f"{preflight_sha256} vs evidence {bound.preflight_artifact_sha256}"),
        check("preflight_verdict", preflight["verdict"] == bound.preflight_status
              and preflight["verdict"] in gate["runnable"], f"{preflight['verdict']}"),
        check("preflight_failed_checks", not failed, f"{len(failed)} failed"),
        check("coverage", preflight["covered_pairs"] == preflight["required_pairs"]
              == bound.required_pairs,
              f"{preflight['covered_pairs']} / {preflight['required_pairs']}"),
        check("warmup_below_minimum", preflight["warmup_below_minimum_count"] == 0,
              f"{preflight['warmup_below_minimum_count']}"),
        check("quality_gate", preflight["quality_exclusion_ratio"]
              <= gate["overall_quality_exclusion_limit"]
              and preflight["max_session_quality_exclusion_ratio"]
              <= gate["session_quality_exclusion_limit"],
              f"overall {preflight['quality_exclusion_ratio']}, max session "
              f"{preflight['max_session_quality_exclusion_ratio']}"),
        check("early_close_sessions", not preflight["early_close_sessions"],
              f"{preflight['early_close_sessions']}"),
        check("signal_ttl_implementation_gap", gap == "RESOLVED", gap),
        check("execution_equivalence", differential_verdict == "PASS", differential_verdict),
        check("strategy_logic_unchanged", not strategy_logic_changed,
              "rules checksum and strategy config unchanged"),
    ]


# ---- the freeze -----------------------------------------------------------------------------------


def freeze(contract_path: Path, checksum_path: Path, ledger_path: Path, *,
           checks: Sequence[Precondition], frozen_at: str, source_commit: str,
           pre_freeze_checksum: str, preflight_sha256: str,
           execution: Mapping[str, object], code: Mapping[str, object] | None = None,
           config_fingerprint: str | None = None,
           freeze_reason: str = "execution parity and all preregistration gates satisfied") -> Contract:
    """DRAFT -> FROZEN. Refuses unless every precondition passed and the draft is the expected one."""
    failed = [c.name for c in checks if not c.passed]
    if failed:
        raise FreezeRefused(f"freeze preconditions failed: {failed}")
    draft = load_contract(contract_path)
    if draft.canonical_checksum != pre_freeze_checksum:
        raise FreezeRefused(f"the draft is {draft.canonical_checksum}, not the approved "
                            f"{pre_freeze_checksum}")
    if contract_state(draft) != DRAFT:
        raise FreezeRefused(f"the contract is already {contract_state(draft)}")
    bound = binding(draft)

    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    raw["contract_state"] = FROZEN
    raw["freeze"] = {
        "frozen_at": frozen_at,
        "freeze_reason": freeze_reason,
        "source_commit": source_commit,
        "source_commit_meaning": "git HEAD at freeze. The working tree was not clean, so HEAD alone "
                                 "does not identify the code; code_digest does.",
        "code_digest": None if code is None else code["code_digest"],
        "code_identity_version": None if code is None else code["version"],
        "code_file_count": None if code is None else code["file_count"],
        "config_fingerprint": config_fingerprint,
        "execution_differential_digest": execution.get("digest"),
        "pre_freeze_checksum": pre_freeze_checksum,
        "binds": {"universe_sha256": bound.universe_sha256,
                  "dataset_digest": bound.dataset_digest,
                  "preflight_status": bound.preflight_status,
                  "preflight_artifact_sha256": preflight_sha256},
        "meaning": "from frozen_at on, every term of this contract is fixed. A result, good or "
                   "bad, never edits it; a change is b_e0_contract_v2.json with a new declaration.",
    }
    unchanged = {k: v for k, v in raw.items() if k not in FREEZE_KEYS}
    before = json.loads(contract_path.read_text(encoding="utf-8"))
    if unchanged != {k: v for k, v in before.items() if k not in FREEZE_KEYS}:
        raise ContractInvalid("the freeze may add only the state and freeze block")
    new_checksum = canonical_checksum(raw)

    text = json.dumps(raw, indent=1, ensure_ascii=False) + "\n"
    contract_path.write_text(text, encoding="utf-8")
    _rewrite_checksum_ledger(checksum_path, contract_path.name, pre_freeze_checksum,
                             new_checksum, frozen_at)
    frozen = load_contract(contract_path)
    require_frozen(frozen, checksum_path)

    record = {
        "format": LEDGER_FORMAT,
        "strategy_id": frozen.strategy_id,
        "contract_id": frozen.contract_id,
        "contract_version": int(frozen.raw["schema_version"]),
        "contract_sha256": new_checksum,
        "pre_freeze_checksum": pre_freeze_checksum,
        "frozen_at": frozen_at,
        "source_commit": source_commit,
        "universe_sha256": bound.universe_sha256,
        "dataset_digest": bound.dataset_digest,
        "preflight_digest": preflight_sha256,
        "preflight_verdict": bound.preflight_status,
        "initial_capital": frozen.initial_capital_usd,
        "execution_model": frozen.raw["execution_model"]["primary"],
        "signal_ttl_minutes": int(frozen.raw["signal_ttl"]["signal_ttl_minutes"]),
        "execution_differential": dict(execution),
        "execution_differential_digest": execution.get("digest"),
        "code_digest": None if code is None else code["code_digest"],
        "code_files": None if code is None else dict(code["files"]),
        "config_fingerprint": config_fingerprint,
        "freeze_reason": freeze_reason,
        "preconditions": [c.as_dict() for c in checks],
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return frozen


def _rewrite_checksum_ledger(path: Path, name: str, draft: str, frozen: str,
                             frozen_at: str) -> None:
    """Status -> FROZEN, the draft's last hash kept in history, canonical and frozen lines set."""
    import hashlib

    lines = path.read_text(encoding="utf-8").splitlines()
    out, skipping = [], False
    for line in lines:
        if line.startswith("# status:"):
            out.append(f"# status:    {FROZEN}. Frozen at {frozen_at}; the contract is a declaration.")
            skipping = True
            continue
        if skipping and line.startswith("#            "):
            continue
        skipping = False
        if line.startswith(("canonical ", "file ", "frozen ")):
            continue
        out.append(line)
    stamp = frozen_at.replace("T", " ")[:16]
    out += [
        f"#   {stamp}  {draft}",
        "#       DRAFT final pre-freeze. The user approved the freeze; the only edit is the added",
        "#       contract_state = FROZEN and freeze block (frozen_at, source commit, the bound",
        "#       universe, dataset and preflight identity). No rule, threshold or policy changed.",
        f"#   {stamp}  {frozen}  FROZEN authoritative contract",
        f"canonical  {frozen}  {name}",
        f"file       {hashlib.sha256((path.parent / name).read_bytes()).hexdigest()}  {name}",
        f"frozen     {frozen}  {name}",
    ]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
