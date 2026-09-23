"""One list of the strategies this deployment knows about.

The UI is built from this registry rather than from hardcoded A/B branches, so a strategy that
reaches paper later appears by adding a row here. ``enabled`` says whether the strategy is part of
current operations; a closed research strategy stays listed and inactive rather than being deleted.

Nothing here decides or executes anything: it is metadata plus the name of the read adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STRATEGY_A = "STRATEGY_A"
STRATEGY_E_MAX_V1 = "STRATEGY_E_MAX_V1"
STRATEGY_B = "STRATEGY_B"


@dataclass(frozen=True)
class StrategyMeta:
    strategy_id: str
    display_name: str
    version: str
    enabled: bool
    mode: str
    adapter: str
    market_data_source: str
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "display_name": self.display_name, "version": self.version,
                "enabled": self.enabled, "mode": self.mode, "adapter": self.adapter,
                "market_data_source": self.market_data_source, "note": self.note, **self.extra}


REGISTRY: tuple[StrategyMeta, ...] = (
    StrategyMeta(
        strategy_id=STRATEGY_A, display_name="Strategy A", version="V0", enabled=True,
        mode="SIMULATION_PAPER", adapter="paper_db", market_data_source="KIWOOM",
        note="운영 중인 기존 페이퍼 전략. 계정·포지션·손익은 페이퍼 DB가 정본."),
    StrategyMeta(
        strategy_id=STRATEGY_E_MAX_V1, display_name="Strategy E-MAX V1", version="V1", enabled=True,
        mode="SIMULATION_PAPER", adapter="e_runtime_files", market_data_source="KIWOOM",
        note="09:25 결정, 09:30 진입, 09:34 청산. 증거 등급은 세션 기록이 정한다."),
    StrategyMeta(
        strategy_id=STRATEGY_B, display_name="Strategy B", version="E1-A", enabled=False,
        mode="RESEARCH_CLOSED", adapter="none", market_data_source="KIWOOM",
        note="2026-09-23 연구 종결(NO_GO). 화면과 연구 자산은 남기고 운영에서만 내린다."),
)


def get(strategy_id: str) -> StrategyMeta | None:
    return next((s for s in REGISTRY if s.strategy_id == strategy_id), None)


def enabled() -> tuple[StrategyMeta, ...]:
    return tuple(s for s in REGISTRY if s.enabled)
