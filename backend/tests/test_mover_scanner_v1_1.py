"""A-MOVER-SCANNER-V1.1 unit tests. No network, no database, no provider, no replay.

Three things need proving and the rest follows from V1's own suite.

**The mask is read, not written.** Section N: the gap band must come from ``StrategyConfig``, so
a changed config must change the handoff with no code edit, in both directions and on both
bounds. The test that matters most is the grep-style one at the bottom: the module may not
contain the literals 2% and 15% at all.

**Re-ranking is from the pool, not from the TOP8.** Section E's whole point is that a candidate
at discovery rank 9-25 can take the slot an unactionable name was holding. A hand-built pool
with unactionable names at the top proves the replacement happens and that the surviving order
is still the discovery order.

**The handoff shrinks without breaking the prompt.** Section M: 8, 5, 1 and 0 candidates all
have to render through the unchanged ``ResearchPromptService`` with contiguous ranks, unique
symbols and the right stated count.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtest.mover_scanner_v1 import actionability as A
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.handoff_study import DECISION, described_from
from app.backtest.mover_scanner_v1.scan import GateReading, MoverCandidate
from app.core.exceptions import DataError
from app.research.prompt import ResearchPromptService
from app.research.versions import TOP8_PROMPT_VERSION
from app.strategy.config import GapDirection, StrategyConfig

SESSION = date(2026, 6, 15)
MODULE = Path(A.__file__)


def candidate(symbol: str, gap_pct: float, total_score: float, pool_rank: int = 1,
              ) -> MoverCandidate:
    """One scored pool member. Only the fields the mask and the re-rank read are meaningful."""
    raw = {"pm_dollar_volume": 1_000_000.0, "pm_rvol": 10.0, "gap_quality": 0.5,
           "pm_momentum": 0.5, "tradability": 0.5}
    return MoverCandidate(
        session_date=SESSION, symbol=symbol, gap_pct=gap_pct, pm_bars=20,
        pm_volume=100_000.0, pm_dollar_volume=1_000_000.0, pm_rvol=10.0, pm_momentum=0.5,
        pm_range=0.05, last_price=10.0, previous_close=10.0 / (1.0 + gap_pct),
        adv20_shares=1_000_000.0, addv20_dollar=20_000_000.0, rvol_baseline_median=10_000.0,
        rvol_baseline_sessions=20, gap_quality=0.5, tradability_score=0.5,
        momentum_detail={"pm_momentum": 0.5}, tradability_detail={"tradability_score": 0.5},
        gate=GateReading(gap_pct, 0.08, 0.02 <= gap_pct <= 0.15, True), raw_components=raw,
        normalized_components={key: 0.0 for key in raw},
        component_scores={key: total_score / len(raw) for key in raw},
        total_score=total_score, pool_score=total_score, candidate_pool_rank=pool_rank)


def pool(*pairs: tuple[str, float]) -> list[MoverCandidate]:
    """A pool whose discovery order is the order given, highest score first."""
    return [candidate(symbol, gap, total_score=100.0 - index, pool_rank=index + 1)
            for index, (symbol, gap) in enumerate(pairs)]


# ---- the mask (section C, D) -------------------------------------------------------------


def test_the_mask_admits_only_the_current_execution_band():
    rule = A.HandoffRule.current()
    assert rule.verdict(0.05) is A.Actionability.PASS
    assert rule.verdict(0.02) is A.Actionability.PASS
    assert rule.verdict(0.15) is A.Actionability.PASS
    assert rule.verdict(0.019) is A.Actionability.GAP_TOO_LOW_FOR_HANDOFF
    assert rule.verdict(0.1501) is A.Actionability.GAP_TOO_HIGH_FOR_HANDOFF
    assert rule.verdict(0.0) is A.Actionability.DIRECTION_NOT_ACTIONABLE
    assert rule.verdict(-0.08) is A.Actionability.DIRECTION_NOT_ACTIONABLE


def test_the_band_edges_are_exact_rather_than_binary_float_approximations():
    """0.02 and 0.15 are not representable in binary, so the edge is compared as a Decimal."""
    rule = A.HandoffRule.current()
    assert rule.is_actionable(float(Decimal("0.02")))
    assert rule.is_actionable(float(Decimal("0.15")))
    assert rule.gap_size(0.07) == Decimal(repr(0.07))


def test_direction_is_tested_before_the_band_so_a_down_gap_is_named_as_one():
    rule = A.HandoffRule.current()
    assert rule.direction is GapDirection.UP
    # A -8% gap is inside the band by magnitude and still unactionable for an UP contract.
    assert rule.verdict(-0.08) is A.Actionability.DIRECTION_NOT_ACTIONABLE


def test_a_down_or_any_contract_reads_the_band_on_the_gap_size():
    down = A.HandoffRule.current(strategy=StrategyConfig(premarket_gap_direction="DOWN"))
    assert down.verdict(-0.05) is A.Actionability.PASS
    assert down.verdict(0.05) is A.Actionability.DIRECTION_NOT_ACTIONABLE
    assert down.verdict(-0.20) is A.Actionability.GAP_TOO_HIGH_FOR_HANDOFF
    both = A.HandoffRule.current(strategy=StrategyConfig(premarket_gap_direction="ANY"))
    assert both.verdict(-0.05) is A.Actionability.PASS
    assert both.verdict(0.05) is A.Actionability.PASS
    assert both.verdict(0.0) is A.Actionability.GAP_TOO_LOW_FOR_HANDOFF


def test_the_mask_refuses_a_gap_it_cannot_read():
    with pytest.raises(DataError):
        A.HandoffRule.current().verdict(float("nan"))


def test_the_mask_applies_no_volume_filter(caplog):
    """Section D: a 5%-of-ADV premarket is the entry gate's call, not the handoff's."""
    rule = A.HandoffRule.current()
    assert rule.applies_volume_filter is False
    assert rule.mask_conditions == ("direction", "gap_min", "gap_max")
    thin = replace(candidate("THIN", 0.06, 10.0), pm_volume=1.0, pm_rvol=0.01,
                   gate=GateReading(0.06, 0.0001, True, False))
    selection = A.select(SESSION, [thin], rule)
    assert [item.symbol for item in selection.handoff] == ["THIN"]


# ---- config safety (section N) -----------------------------------------------------------


def test_the_handoff_follows_the_config_gap_minimum_with_no_code_change():
    members = pool(("AAA", 0.03), ("BBB", 0.06), ("CCC", 0.12))
    base = A.HandoffRule.current()
    assert [item.symbol for item in A.select(SESSION, members, base).handoff] == \
        ["AAA", "BBB", "CCC"]
    raised = A.HandoffRule.current(strategy=StrategyConfig(premarket_gap_min_pct=Decimal("0.05")))
    assert [item.symbol for item in A.select(SESSION, members, raised).handoff] == ["BBB", "CCC"]
    lowered = A.HandoffRule.current(
        strategy=StrategyConfig(premarket_gap_min_pct=Decimal("0.01")))
    assert [item.symbol for item in A.select(SESSION, members, lowered).handoff] == \
        ["AAA", "BBB", "CCC"]


def test_the_handoff_follows_the_config_gap_maximum_with_no_code_change():
    members = pool(("AAA", 0.03), ("BBB", 0.09), ("CCC", 0.14))
    tightened = A.HandoffRule.current(
        strategy=StrategyConfig(premarket_gap_max_pct=Decimal("0.10")))
    assert [item.symbol for item in A.select(SESSION, members, tightened).handoff] == \
        ["AAA", "BBB"]
    widened = A.HandoffRule.current(
        strategy=StrategyConfig(premarket_gap_max_pct=Decimal("0.30")))
    assert [item.symbol for item in A.select(SESSION, members, widened).handoff] == \
        ["AAA", "BBB", "CCC"]


def test_a_moved_band_moves_the_checksum_so_an_artifact_names_its_own_rule():
    base = A.HandoffRule.current()
    assert base.checksum != MoverScannerConfig().checksum
    assert base.discovery_rules_checksum == MoverScannerConfig().checksum
    moved = A.HandoffRule.current(strategy=StrategyConfig(premarket_gap_max_pct=Decimal("0.20")))
    assert moved.checksum != base.checksum
    assert moved.discovery_rules_checksum == base.discovery_rules_checksum
    assert A.HandoffRule.current().checksum == base.checksum


def test_the_handoff_maximum_is_read_from_the_scanner_config():
    members = pool(*[(f"S{index:02d}", 0.05) for index in range(12)])
    assert len(A.select(SESSION, members, A.HandoffRule.current()).handoff) == 8
    narrow = A.HandoffRule.current(config=MoverScannerConfig(top_count=3))
    assert len(A.select(SESSION, members, narrow).handoff) == 3


def test_the_module_declares_no_gap_threshold_of_its_own():
    """Section N: 2 and 15 may appear in the prose, never as a value the code can use.

    Parsed rather than grepped, so the explanation of the band in the docstrings is allowed
    and a literal that could actually be compared against a gap is not.
    """
    import ast

    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    numbers = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
               and not isinstance(node.value, bool)]
    for forbidden in (0.02, 0.15, 2.0, 15.0, 2, 15):
        assert forbidden not in numbers, f"{forbidden} is a literal in the actionability layer"
    decimal_literals = [argument.value for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "Decimal"
                        for argument in node.args if isinstance(argument, ast.Constant)]
    assert all(value in ("0",) for value in decimal_literals), decimal_literals
    body = MODULE.read_text(encoding="utf-8")
    assert "premarket_gap_min_pct" in body and "premarket_gap_max_pct" in body


def test_the_discovery_rules_are_not_touched_by_the_handoff_layer():
    """The V1 checksum is the published one; V1.1 adds a contract, it does not edit V1's."""
    assert MoverScannerConfig().checksum == \
        "d900dffd7b23fea1224584e390f75de7b71d198dbf9c01c0586287fd4b112a2a"
    assert MoverScannerConfig().contract_version == "a-mover-scanner-v1"
    assert A.CONTRACT_VERSION == "a-mover-scanner-v1.1"
    assert A.HandoffRule.current().score_version == MoverScannerConfig().score_version


# ---- re-ranking and slot recovery (section E, K) -----------------------------------------


def test_a_candidate_below_the_v1_cut_takes_an_unactionable_slot():
    members = pool(("TOOBIG1", 0.40), ("TOOBIG2", 0.30), ("DOWN1", -0.05),
                   ("LOW1", 0.005), ("OK1", 0.05), ("OK2", 0.06), ("OK3", 0.07),
                   ("OK4", 0.08), ("OK5", 0.09), ("OK6", 0.10), ("OK7", 0.11),
                   ("OK8", 0.12), ("OK9", 0.13))
    selection = A.select(SESSION, members, A.HandoffRule.current())
    # V1 would have handed off the first eight, four of which the gate cannot admit.
    assert [item.symbol for item in selection.discovery_top] == \
        ["TOOBIG1", "TOOBIG2", "DOWN1", "LOW1", "OK1", "OK2", "OK3", "OK4"]
    assert selection.invalid_removed == 4
    # V1.1 fills the eight from the actionable pool, in the same discovery order.
    assert [item.symbol for item in selection.handoff] == \
        ["OK1", "OK2", "OK3", "OK4", "OK5", "OK6", "OK7", "OK8"]
    assert selection.replacement_added == 4
    assert [item.rank for item in selection.handoff] == list(range(1, 9))


def test_masking_the_top8_instead_of_the_pool_is_what_this_avoids():
    """Section E's forbidden order would have produced four names; the pool order produces 8."""
    members = pool(("TOOBIG1", 0.40), ("TOOBIG2", 0.30), ("DOWN1", -0.05), ("LOW1", 0.005),
                   *[(f"OK{index}", 0.05) for index in range(1, 10)])
    rule = A.HandoffRule.current()
    selection = A.select(SESSION, members, rule)
    masked_top8 = [item for item in selection.discovery_top if rule.is_actionable(item.gap_pct)]
    assert len(masked_top8) == 4
    assert selection.handoff_size == 8


def test_the_surviving_order_is_the_discovery_order_and_nothing_is_rescored():
    members = pool(("A", 0.03), ("B", 0.04), ("C", 0.05))
    selection = A.select(SESSION, members, A.HandoffRule.current())
    scores = [item.total_score for item in selection.handoff]
    assert scores == sorted(scores, reverse=True)
    for item in selection.handoff:
        original = next(member for member in members if member.symbol == item.symbol)
        assert item.total_score == original.total_score
        assert dict(item.component_scores) == dict(original.component_scores)
        assert item.candidate_pool_rank == original.candidate_pool_rank


def test_the_output_is_never_padded_to_the_maximum():
    rule = A.HandoffRule.current()
    for actionable in (8, 5, 2, 0):
        members = pool(*[(f"OK{index}", 0.05) for index in range(actionable)],
                       *[(f"BIG{index}", 0.40) for index in range(6)])
        selection = A.select(SESSION, members, rule)
        assert selection.handoff_size == min(actionable, rule.handoff_maximum)
        assert selection.actionable_pool_size == actionable


def test_an_audit_row_names_why_a_pool_member_was_not_handed_off():
    members = pool(("BIG", 0.40), ("OK", 0.05), ("LOW", 0.001), ("DOWN", -0.03))
    selection = A.select(SESSION, members, A.HandoffRule.current())
    rows = {row["symbol"]: row for row in A.audit_rows(selection, MoverScannerConfig())}
    assert rows["OK"]["actionable"] is True
    assert rows["OK"]["actionability_reason"] == "PASS"
    assert rows["OK"]["rejection_reason"] is None
    assert rows["OK"]["rank"] == 1
    assert rows["BIG"]["actionable"] is False
    assert rows["BIG"]["rejection_reason"] == "GAP_TOO_HIGH_FOR_HANDOFF"
    assert rows["BIG"]["rank"] is None
    assert rows["LOW"]["rejection_reason"] == "GAP_TOO_LOW_FOR_HANDOFF"
    assert rows["DOWN"]["rejection_reason"] == "DIRECTION_NOT_ACTIONABLE"
    assert selection.rejection_counts == {"GAP_TOO_HIGH_FOR_HANDOFF": 1,
                                          "GAP_TOO_LOW_FOR_HANDOFF": 1,
                                          "DIRECTION_NOT_ACTIONABLE": 1}


def test_the_output_row_carries_every_declared_field():
    selection = A.select(SESSION, pool(("OK", 0.05)), A.HandoffRule.current())
    row = A.handoff_rows(selection, MoverScannerConfig())[0]
    for key in ("session_date", "scan_time", "rank", "symbol", "discovery_pool_rank",
                "discovery_total_score", "gap_pct", "pm_volume", "pm_dollar_volume",
                "pm_rvol", "pm_momentum", "pm_range", "tradability_score", "component_scores",
                "actionable", "actionability_reason", "scanner_version", "scanner_checksum"):
        assert key in row, key
    assert row["scan_time"] == "09:15 ET"
    assert row["scanner_version"] == "a-mover-scanner-v1.1"
    assert row["scanner_checksum"] == A.HandoffRule.current().checksum
    assert row["discovery_rules_checksum"] == MoverScannerConfig().checksum


# ---- GPT handoff (section M) -------------------------------------------------------------


class _Repository:
    def __init__(self, candidates):
        self._candidates = candidates

    def get_top8(self, run_id):
        return self._candidates


@dataclass
class _Run:
    id: int
    trading_date: date
    status: str
    score_version: str


@dataclass
class _Candidate:
    scanner_run_id: int
    symbol: str
    rank: int
    score: float
    score_components_json: dict


def _render(selection: A.SessionHandoff) -> str:
    rows = A.candidate_rows(selection, datetime(2026, 6, 15, 13, 15, tzinfo=timezone.utc))
    run = _Run(id=11, trading_date=SESSION, status="COMPLETED",
               score_version=MoverScannerConfig().score_version)
    candidates = [_Candidate(11, row.symbol, row.rank, row.score, row.score_components)
                  for row in rows]
    return ResearchPromptService(_Repository(candidates)).generate_top_for_run(run)


@pytest.mark.parametrize("actionable", [8, 5, 1])
def test_the_unchanged_prompt_renders_for_any_handoff_size(actionable):
    members = pool(*[(f"OK{index}", 0.05 + index / 1000) for index in range(actionable)],
                   *[(f"BIG{index}", 0.40) for index in range(10)])
    selection = A.select(SESSION, members, A.HandoffRule.current())
    assert selection.handoff_size == actionable
    prompt = _render(selection)
    assert f"prompt_version: {TOP8_PROMPT_VERSION}" in prompt
    assert f"Include exactly the same {actionable} symbols" in prompt
    assert f"ranks 1..{actionable}" in prompt
    for item in selection.handoff:
        assert f'"symbol": "{item.symbol}"' in prompt
    for index in range(10):
        assert f'"symbol": "BIG{index}"' not in prompt
    assert "pm_dollar_volume" in prompt and "pm_rvol" in prompt


def test_an_empty_handoff_renders_no_prompt_rather_than_an_empty_one():
    """A zero-candidate session has nothing to research, and the service already says so."""
    members = pool(*[(f"BIG{index}", 0.40) for index in range(10)])
    selection = A.select(SESSION, members, A.HandoffRule.current())
    assert selection.handoff_size == 0
    assert A.candidate_rows(selection, datetime.now(timezone.utc)) == []
    from app.core.exceptions import ResearchError
    with pytest.raises(ResearchError):
        _render(selection)


def test_the_handed_off_ranks_are_contiguous_unique_and_capped():
    members = pool(*[(f"OK{index}", 0.05) for index in range(20)])
    selection = A.select(SESSION, members, A.HandoffRule.current())
    rows = A.candidate_rows(selection, datetime.now(timezone.utc))
    assert [row.rank for row in rows] == list(range(1, 9))
    assert len({row.symbol for row in rows}) == 8
    assert all(row.is_top8 for row in rows)


def test_the_handoff_payload_names_both_contracts_and_the_mask_outcome():
    members = pool(("BIG", 0.40), ("OK1", 0.05), ("OK2", 0.06))
    config = MoverScannerConfig()
    selection = A.select(SESSION, members, A.HandoffRule.current())
    payload = A.handoff_payload(selection, config, datetime.now(timezone.utc))
    assert payload["scanner"] == "a-mover-scanner-v1.1"
    assert payload["rules_checksum"] == selection.rule.checksum
    assert payload["discovery_contract_version"] == config.contract_version
    assert payload["discovery_rules_checksum"] == config.checksum
    assert payload["candidate_count"] == 2
    assert payload["top_count_maximum"] == config.top_count
    assert payload["actionability"]["actionable_pool_size"] == 2
    assert payload["actionability"]["rejections"] == {"GAP_TOO_HIGH_FOR_HANDOFF": 1}
    assert [entry["symbol"] for entry in payload["candidates"]] == ["OK1", "OK2"]
    assert all(entry["actionable"] for entry in payload["candidates"])
    assert [entry["discovery_output_rank"] for entry in payload["candidates"]] == [2, 3]


# ---- the study's own arithmetic ----------------------------------------------------------


def test_a_pool_candidate_describes_itself_without_a_second_computation():
    item = candidate("AAA", 0.06, 10.0)
    described = described_from(item)
    assert described.covered is True
    assert described.gap_pct == item.gap_pct
    assert described.pm_rvol == item.pm_rvol
    assert described.pm_dollar_volume == item.pm_dollar_volume
    assert described.addv20_dollar == item.addv20_dollar
    assert described.gate is item.gate


def test_the_decision_thresholds_are_declared_rather_than_derived():
    assert DECISION["waste_invalid_slot_rate_max"] == 0.0
    assert DECISION["waste_must_beat_v1"] is True
    assert set(DECISION) == {
        "starvation_both_pass_per_session_min",
        "starvation_sessions_with_any_both_pass_share_min",
        "waste_invalid_slot_rate_max", "waste_must_beat_v1",
        "actionable_pool_median_min", "output_at_least_five_share_min",
        "diversity_repeat_ratio_max", "diversity_turnover_min",
        "diversity_unique_symbols_min", "diversity_top_repeated_share_max"}
