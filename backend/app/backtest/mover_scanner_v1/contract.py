"""The frozen A-MOVER-SCANNER-V1.2 contract, as one declaration both arms read.

V1.2's selected pool size lives in the research runner that measured it
(``app.dev.run_mover_pool_research``), which is the right place for a study and the wrong
place for a runtime to import a production rule from. This module is that rule and nothing
else: the pool size, the two contract versions and the two checksums the FINAL report
published, built from the same ``MoverScannerConfig``/``HandoffRule`` the research path uses.

So the forward runtime does not re-implement the scanner, and it cannot drift away from the
artifact either. :func:`verify` recomputes both checksums from the live configuration and
raises :class:`ContractDrift` when either one moves, which is what makes the version stamped
on a persisted run a claim about the rules rather than a label. The handoff checksum includes
the Strategy A gap band, so moving that band stops the forward scanner instead of quietly
handing GPT a differently masked set under the frozen name.

``RUN_SCORE_VERSION`` is the discriminator this contract gets inside ``scanner_runs``. It is
how a row says which scanner produced it; the deployed trade-value scanner keeps ``quant_v0``
and no existing row is touched or rewritten.
"""

from dataclasses import replace

from app.backtest.mover_scanner_v1 import actionability as A
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.strategy.config import StrategyConfig

#: The FINAL contract name, carried by both the discovery config and the handoff rule.
CONTRACT_VERSION = "a-mover-scanner-v1.2"
#: Selected by the P25/P35/P50 study: the smallest authorized size meeting every bar.
POOL_SIZE = 35
#: Published in A_MOVER_SCANNER_V1_2.md and in ``scanner_contract.sha256``.
HANDOFF_CHECKSUM = "f05e53cce5a431e8e132a0fc1698085b64f8e1d015e11028a77dc62754a11f25"
DISCOVERY_CHECKSUM = "aa6d80cd4000546758f816f800d1386c539d66c4db7f3cf194b6b7147e63d95d"
#: ``ScannerRun.score_version`` for this scanner. The deployed scanner's stays ``quant_v0``.
RUN_SCORE_VERSION = "mover_v1.2"
#: ET minute of day the scan is taken at, restated for a reader; the config is the authority.
SCAN_CUT_MINUTE = 555


class ContractDrift(RuntimeError):
    """A live configuration no longer reproduces the frozen V1.2 checksums."""


def final_config() -> MoverScannerConfig:
    """The frozen discovery configuration: V1's rules at the selected pool size."""
    return replace(MoverScannerConfig(), pool_size=POOL_SIZE,
                   contract_version=CONTRACT_VERSION)


def final_rule(strategy: StrategyConfig | None = None) -> A.HandoffRule:
    """The frozen handoff rule, with every bound still read from the deployed config."""
    return replace(A.HandoffRule.current(final_config(), strategy),
                   contract_version=CONTRACT_VERSION)


def declaration() -> dict:
    """The whole frozen contract as plain data, for a run record or a report."""
    config, rule = final_config(), final_rule()
    return {
        "contract_version": CONTRACT_VERSION,
        "scanner_version": CONTRACT_VERSION,
        "scanner_checksum": rule.checksum,
        "discovery_checksum": config.checksum,
        "run_score_version": RUN_SCORE_VERSION,
        "pool_size": config.pool_size,
        "top_count": config.top_count,
        "scan_cut_minute": config.scan_cut_minute,
        "scan_time_et": A.scan_time_label(config),
        "handoff_rule": rule.declaration(),
    }


def verify() -> dict:
    """Recompute both checksums from the live configuration, or refuse to run.

    The discovery checksum moves only if a scanner threshold moves. The handoff checksum also
    moves if Strategy A's gap band or direction moves, because the mask reads them. Either is
    a reviewable event, so neither is absorbed here.
    """
    config, rule = final_config(), final_rule()
    if config.pool_size != POOL_SIZE:
        raise ContractDrift(f"the frozen pool size is {POOL_SIZE}, not {config.pool_size}")
    if config.checksum != DISCOVERY_CHECKSUM:
        raise ContractDrift(
            f"the discovery checksum is {config.checksum}, not the frozen {DISCOVERY_CHECKSUM}; "
            "a scanner threshold moved and the V1.2 artifact no longer describes it")
    if rule.checksum != HANDOFF_CHECKSUM:
        raise ContractDrift(
            f"the handoff checksum is {rule.checksum}, not the frozen {HANDOFF_CHECKSUM}; "
            "the Strategy A gap band or direction moved and the frozen mask is not in force")
    return declaration()
