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
STRATEGY_H_V2 = "STRATEGY_H_V2"
STRATEGY_B = "STRATEGY_B"
STRATEGY_C = "STRATEGY_C"
STRATEGY_D = "STRATEGY_D"

# Research and operations are two different questions and are never folded into one status.
# ``research_lifecycle`` says what the evidence concluded; ``lifecycle`` says what operations does
# with the strategy today. The live operational state (waiting, position open, data error) is
# computed per request by the API, not stored here.
RESEARCH_PASSED_TO_PAPER = "PASSED_TO_PAPER"
RESEARCH_CLOSED = "CLOSED"
LIFECYCLE_PAPER = "PAPER"
LIFECYCLE_FORWARD_SHADOW = "FORWARD_SHADOW"
LIFECYCLE_RETIRED = "RETIRED"

#: What a strategy does with capital. A and E place simulated orders; H observes its own decisions on
#: future data and holds no capital book. Both are operating modes and both appear on the paper
#: screens, so screens select on this set rather than on a single hardcoded string.
MODE_SIMULATION_PAPER = "SIMULATION_PAPER"
MODE_FORWARD_SHADOW = "FORWARD_SHADOW"
PAPER_MODES = (MODE_SIMULATION_PAPER, MODE_FORWARD_SHADOW)


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
    # The one-letter family the operator speaks of ("A", "E"). Internal ids stay as they are:
    # E's ledgers, books and runtime files are keyed by STRATEGY_E_MAX_V1 and are not renamed.
    short_name: str = ""
    variant_label: str = ""
    research_lifecycle: str = RESEARCH_PASSED_TO_PAPER
    lifecycle: str = LIFECYCLE_PAPER
    closed_on: str | None = None
    closeout: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "display_name": self.display_name, "version": self.version,
                "enabled": self.enabled, "mode": self.mode, "adapter": self.adapter,
                "market_data_source": self.market_data_source, "note": self.note,
                "short_name": self.short_name, "variant_label": self.variant_label,
                "research_lifecycle": self.research_lifecycle, "lifecycle": self.lifecycle,
                "closed_on": self.closed_on, "closeout": self.closeout, **self.extra}


REGISTRY: tuple[StrategyMeta, ...] = (
    StrategyMeta(
        strategy_id=STRATEGY_A, display_name="Strategy A", version="V0", enabled=True,
        mode="SIMULATION_PAPER", adapter="paper_db", market_data_source="KIWOOM",
        note="운영 중인 기존 페이퍼 전략. 계정·포지션·손익은 페이퍼 DB가 정본.",
        short_name="A", variant_label="기존 전략"),
    StrategyMeta(
        strategy_id=STRATEGY_E_MAX_V1, display_name="Strategy E", version="E-MAX V1", enabled=True,
        mode="SIMULATION_PAPER", adapter="e_runtime_files", market_data_source="KIWOOM",
        note="09:25 결정, 09:30 진입, 09:34 청산. 증거 등급은 세션 기록이 정한다.",
        short_name="E", variant_label="E-MAX V1"),
    # H reached paper as a forward shadow rather than as an order-placing strategy: its output is a
    # decision per issuer (APPROVE / WATCH / REJECT), and only an APPROVE could ever become a
    # position. It operates beside A and E on the same screens; it shares none of their books.
    StrategyMeta(
        strategy_id=STRATEGY_H_V2, display_name="Strategy H", version="H-V2 D7", enabled=True,
        mode=MODE_FORWARD_SHADOW, adapter="h_forward_files", market_data_source="MASSIVE",
        note="D1~D6 연구 종결. 미래 데이터만으로 결정을 관찰한다. WATCH·REJECT는 포지션을 만들지 않는다.",
        short_name="H", variant_label="Forward Shadow", lifecycle=LIFECYCLE_FORWARD_SHADOW),
    # Closed research. Listed so the history screen can say so; never an operating tab, never a card.
    StrategyMeta(
        strategy_id=STRATEGY_B, display_name="Strategy B", version="E1-A", enabled=False,
        mode="RESEARCH_CLOSED", adapter="none", market_data_source="KIWOOM",
        note="B-E0 FAIL(SIGNAL_EDGE_WEAK) 후 NO_GO. 화면과 연구 자산은 남기고 운영에서만 내린다.",
        short_name="B", variant_label="실시간 모멘텀", research_lifecycle=RESEARCH_CLOSED,
        lifecycle=LIFECYCLE_RETIRED, closed_on="2026-09-23",
        closeout="docs/backtest/strategy_b/B_FINAL_CLOSEOUT_V1.md"),
    StrategyMeta(
        strategy_id=STRATEGY_C, display_name="Strategy C", version="C-ATTACK-V0", enabled=False,
        mode="RESEARCH_CLOSED", adapter="none", market_data_source="MASSIVE",
        note="C-ATTACK-V0 FAIL로 C 계열 전체 종결(FULLY CLOSED).",
        short_name="C", variant_label="롱 사이드 계열", research_lifecycle=RESEARCH_CLOSED,
        lifecycle=LIFECYCLE_RETIRED, closed_on="2026-09-21",
        closeout="docs/backtest/strategy_c/C_FINAL_CLOSEOUT_V1.md"),
    StrategyMeta(
        strategy_id=STRATEGY_D, display_name="Strategy D", version="D-V2A", enabled=False,
        mode="RESEARCH_CLOSED", adapter="none", market_data_source="MASSIVE",
        note="D-AGG BACKTEST_FAIL, D-V2A SCREENING_FAIL로 종결. 장기 데이터 구매 후보 아님.",
        short_name="D", variant_label="애널로그 계열", research_lifecycle=RESEARCH_CLOSED,
        lifecycle=LIFECYCLE_RETIRED, closed_on="2026-09-21",
        closeout="docs/backtest/strategy_d_v2/D_V2A_D4_RESULTS_V1.md"),
)


def get(strategy_id: str) -> StrategyMeta | None:
    return next((s for s in REGISTRY if s.strategy_id == strategy_id), None)


def enabled() -> tuple[StrategyMeta, ...]:
    return tuple(s for s in REGISTRY if s.enabled)
