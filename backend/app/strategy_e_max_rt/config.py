"""E-MAX V1 runtime configuration and its explicit simulation-only risk profile.

Common Risk V1 (``app.risk``) sizes A's stop-based trades and has no leverage; E-MAX V1 has no stop
and needs 2.0x / 3.0x normalized gross exposure. E therefore runs under its own, explicitly named
simulation profile. Nothing here touches ``RiskEngine`` or A's configuration, and nothing here can
route a real order: the book is a ``SimBroker`` and ``LIVE_MARGIN_APPROVED`` is False by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path

from app.strategy_e_max import v1

REPO_ROOT = Path(__file__).resolve().parents[3]
STRATEGY_ID = v1.STRATEGY_ID                      # "STRATEGY_E_MAX_V1"
ENABLED_ENV = "STRATEGY_E_MAX_ENABLED"
STATE_ENV = "STRATEGY_E_MAX_STATE_DIR"
EQUITY_ENV = "STRATEGY_E_MAX_INITIAL_EQUITY"
DEFAULT_STATE_DIR = REPO_ROOT / "data/runtime/strategy_e_max/rt"
DEFAULT_INITIAL_EQUITY = Decimal("10000")
LIVE_MARGIN_APPROVED = False
RISK_CLASSIFICATION = "REQUIRES_STRATEGY_SPECIFIC_SIM_RISK_PROFILE"


@dataclass(frozen=True)
class SimRiskProfile:
    """Virtual normalized exposure for E only; never merged with A's risk or a real account."""

    name: str = "E_MAX_V1_SIM_EXPOSURE_PROFILE_V1"
    normal_exposure: Decimal = Decimal(v1.FINAL_EXPOSURE["normal"].numerator)          # 2
    high_breadth_exposure: Decimal = Decimal(v1.FINAL_EXPOSURE["high_breadth"].numerator)  # 3
    #: SimBroker refuses a BUY above its cash, so the E book is funded with virtual buying power
    #: = equity x 3.0 x (1 + buffer); the buffer absorbs open-price drift against the 09:25 reference
    #: and the broker's own cost model. It is buying power, not equity.
    buying_power_buffer: Decimal = Decimal("0.05")
    live_margin_approved: bool = LIVE_MARGIN_APPROVED

    def buying_power(self, equity: Decimal) -> Decimal:
        return equity * self.high_breadth_exposure * (Decimal(1) + self.buying_power_buffer)


@dataclass(frozen=True)
class RuntimeConfig:
    enabled: bool
    state_dir: Path
    initial_equity: Decimal
    profile: SimRiskProfile = SimRiskProfile()
    strategy_id: str = STRATEGY_ID
    #: ET clock boundaries (frozen E-MAX V1 schedule; realtime settlement windows only)
    decision_cutoff: str = "09:25"
    entry_bar: str = "09:30"
    entry_resolve_by: str = "09:33"     # a selected symbol without a 09:30 bar by then is ENTRY_INVALID
    exit_bar: str = "09:34"             # development proxy: exact 09:34 close
    exit_late_after: str = "09:45"


def from_env(environ=os.environ) -> RuntimeConfig:
    return RuntimeConfig(enabled=environ.get(ENABLED_ENV, "false").strip().lower() in {"1", "true", "yes"},
                         state_dir=Path(environ.get(STATE_ENV, str(DEFAULT_STATE_DIR))),
                         initial_equity=Decimal(environ.get(EQUITY_ENV, str(DEFAULT_INITIAL_EQUITY))))
