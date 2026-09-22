"""What makes one B-E0 run the same run as another, and what deliberately does not.

Everything that could change a number belongs in the identity: the contract, the rules, the
capital, the universe, the dataset, the cost level, the fill scenario and the bootstrap
settings. Everything that cannot -- when it ran, on whose machine, with what uncommitted edits
-- stays out of the digest and goes to provenance instead, so a rerun of the same study lands
on the same run id rather than on a new one for having happened on a Tuesday.

Costs and the fill scenario are inside the digest on purpose. Two runs priced differently are
structurally different runs and can never be compared by accident.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.backtest.engine.identity import RunIdentity, code_digest, package_files, run_identity
from app.backtest.strategy_b_e0.contract import Contract, RunSpec
from app.backtest.strategy_b_e0.universe import RunUniverse

NAMESPACE = "strategy-b-e0"
RUN_PREFIX = "be0"
STRATEGY_VERSION = "REALTIME_MOMENTUM_V1"

#: The packages whose bytes decide the result. A change in any of them is a different run.
DIGEST_PACKAGES = ("app/strategy_b", "app/backtest/strategy_b", "app/backtest/strategy_b_e0")


def engine_code_digest(backend_root: Path) -> str:
    packages = [backend_root / package for package in DIGEST_PACKAGES]
    return code_digest(package_files(*packages), root=backend_root)


@dataclass(frozen=True, slots=True)
class RunMode:
    name: str

    def __post_init__(self) -> None:
        if self.name not in MODES:
            raise ValueError(f"unknown run mode {self.name!r}; authoritative results come from "
                             "STRICT and nothing else")

    @property
    def authoritative(self) -> bool:
        return self.name == "STRICT"


#: STRICT is the only mode whose result can feed the verdict. SMOKE replays an explicit, short
#: session list through the same code to check the plumbing; its identity carries that list, so a
#: smoke run can never share a run id with an authoritative one.
MODES = frozenset({"STRICT", "PREFLIGHT_ONLY", "SMOKE"})


def identity_lines(contract: Contract, spec: RunSpec, universe: RunUniverse, *,
                   dataset_identity: str, dataset_digest: str, code: str,
                   mode: RunMode, config_fingerprint: str | None = None,
                   sessions: Sequence[date] | None = None,
                   working_tree_code_digest: str | None = None) -> tuple[str, ...]:
    level = contract.cost_level(spec.cost_level)
    stats = contract.statistics
    replayed = tuple(sessions) if sessions is not None else tuple(universe.sessions)
    if mode.authoritative and replayed != tuple(universe.sessions):
        raise ValueError("an authoritative run replays every session of the universe")
    extra = [] if config_fingerprint is None else [f"config_fingerprint={config_fingerprint}"]
    if working_tree_code_digest is not None:
        extra.append(f"code_digest={working_tree_code_digest}")
    if mode.name == "SMOKE":
        extra.append("smoke_sessions=" + ",".join(day.isoformat() for day in replayed))
    return (
        f"strategy_id={contract.strategy_id}",
        f"strategy_version={STRATEGY_VERSION}",
        f"contract_id={contract.contract_id}",
        f"contract_canonical_checksum={contract.canonical_checksum}",
        f"rules_canonical_checksum={contract.rules_checksum}",
        f"initial_capital_usd={contract.initial_capital_usd}",
        f"fx_model={contract.fx_model}",
        f"universe_artifact={universe.path.name}",
        f"universe_sha256={universe.sha256}",
        f"universe_symbol_count={universe.symbol_count}",
        f"dataset_identity={dataset_identity}",
        f"dataset_digest={dataset_digest}",
        f"scope_start={contract.scope_start}",
        f"scope_end={contract.scope_end}",
        f"run_label={spec.label}",
        f"fill_scenario={spec.fill_scenario}",
        f"cost_level={level.name}",
        f"commission_bps_per_side={level.commission_bps_per_side}",
        f"execution_cost_bps_per_side={level.execution_cost_bps_per_side}",
        f"engine_code_digest={code}",
        f"bootstrap_seed={stats.seed}",
        f"bootstrap_replicates={stats.replicates}",
        f"bootstrap_block_length={stats.block_length}",
        f"run_mode={mode.name}",
        f"contract_state={contract.raw.get('contract_state', 'DRAFT_PENDING_APPROVAL')}",
        f"execution_model={contract.raw['execution_model']['primary']}",
        f"signal_ttl_minutes={contract.raw['signal_ttl']['signal_ttl_minutes']}",
        *extra,
    )


def build(contract: Contract, spec: RunSpec, universe: RunUniverse, *, dataset_identity: str,
          dataset_digest: str, code: str, mode: RunMode, config_fingerprint: str | None = None,
          sessions: Sequence[date] | None = None,
          working_tree_code_digest: str | None = None) -> RunIdentity:
    lines = identity_lines(contract, spec, universe, dataset_identity=dataset_identity,
                           dataset_digest=dataset_digest, code=code, mode=mode,
                           config_fingerprint=config_fingerprint, sessions=sessions,
                           working_tree_code_digest=working_tree_code_digest)
    return run_identity(NAMESPACE, RUN_PREFIX, lines)


def as_document(identity: RunIdentity, provenance: Mapping[str, object],
                extra: Mapping[str, object] | None = None) -> dict[str, object]:
    """identity.json: the digest, the lines that produced it, and the non-digest facts apart."""
    body: dict[str, object] = {
        "run_id": identity.run_id,
        "digest": identity.digest,
        "lines": list(identity.lines),
        "provenance": dict(provenance),
    }
    if extra:
        body.update(extra)
    return body


def differing_lines(left: Sequence[str], right: Sequence[str]) -> tuple[str, ...]:
    """Which identity lines two runs disagree on, for a rerun that expected to match."""
    return tuple(sorted(set(left) ^ set(right)))
