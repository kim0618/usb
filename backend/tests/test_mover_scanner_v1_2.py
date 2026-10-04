"""Pool-only contract, deterministic selection, and unchanged renderer edge tests."""
from dataclasses import replace
import pytest
from app.dev.run_mover_pool_research import variant_config, rule_for, prompt_check
from app.backtest.mover_scanner_v1 import actionability as A
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from tests.test_mover_scanner_v1_1 import candidate, SESSION

@pytest.mark.parametrize('n', [25,35,50])
def test_only_pool_and_version_change(n):
    baseline = MoverScannerConfig().declaration()
    variant = variant_config(n).declaration()
    assert {k for k in baseline if baseline[k] != variant[k]} <= {'pool_size','contract_version'}
    assert variant['pool_size'] == n
    assert rule_for(n).gap_min_pct == A.HandoffRule.current().gap_min_pct
    assert rule_for(n).gap_max_pct == A.HandoffRule.current().gap_max_pct
    assert rule_for(n).direction == A.HandoffRule.current().direction
    assert not rule_for(n).applies_volume_filter

@pytest.mark.parametrize('n', [35,50])
def test_deep_rank_candidates_enter_and_selection_is_deterministic(n):
    members = [candidate(f'S{i:02}', .40 if i <= 25 else .05, 100-i, i)
               for i in range(1,n+1)]
    rule = rule_for(n)
    first = A.select(SESSION, members, rule)
    assert first == A.select(SESSION, list(reversed(members)), rule)
    assert len(first.handoff) == 8
    assert all(c.candidate_pool_rank > 25 for c in first.handoff)

@pytest.mark.parametrize('count', [8,5,1,0])
def test_versioned_payload_renderer_edges(count):
    members = [candidate(f'S{i:02}', .05, 100-i, i) for i in range(1,9)]
    selected = A.select(SESSION, members, rule_for(50))
    result = prompt_check(selected, variant_config(50), count)
    assert result['passed']
    assert result['payload']['scanner'] == 'a-mover-scanner-v1.2'


def test_extra_pool_value_is_rejected():
    with pytest.raises(ValueError):
        variant_config(40)
