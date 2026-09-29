"""Fee scenarios and their provenance.

The rates are official and the tier is not. Everything here exists so that distinction cannot
be lost between the capture and a research conclusion.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.paper.config import FeeSchedule, build_config
from app.crypto.paper.fees import (DEFAULT_SCENARIO, NO_COST, TIER_ASSUMED, TIER_HYPOTHETICAL,
                                   describe, load_scenarios)

D = Decimal
CAPTURE = Path("data/runtime/crypto/reference/fee_source_verification_v1.json")


@pytest.fixture
def scenarios():
    if not CAPTURE.exists():
        pytest.skip("no captured fee verification record in this workspace")
    return load_scenarios(CAPTURE)


def test_every_published_vip_row_becomes_a_scenario(scenarios) -> None:
    raw = json.loads(CAPTURE.read_text())
    assert len(scenarios) == len(raw["vip_table"]) + 1     # +1 for the zero-cost bound


def test_the_default_scenario_is_the_rate_the_deployed_run_uses(scenarios) -> None:
    baseline = scenarios[DEFAULT_SCENARIO]
    assert baseline.taker_rate == D("0.00055")
    assert baseline.maker_rate == D("0.0002")


def test_only_the_entry_tier_is_assumed_and_the_rest_are_marked_hypothetical(scenarios) -> None:
    assert scenarios["VIP_0"].basis == TIER_ASSUMED
    hypothetical = [s for name, s in scenarios.items() if name not in ("VIP_0", "ZERO")]
    assert hypothetical and all(s.basis == TIER_HYPOTHETICAL for s in hypothetical)
    # None of them claims to be measured: US-B holds no account, so no tier is a fact.
    assert all("assum" in s.note.lower() or "not reached" in s.note.lower()
               or "sensitivity" in s.note.lower() for s in scenarios.values() if s.name != "ZERO")


def test_the_zero_scenario_is_labelled_as_a_bound_not_as_a_fee(scenarios) -> None:
    zero = scenarios["ZERO"]
    assert zero.taker_rate == 0 and zero.maker_rate == 0
    assert zero.basis == NO_COST
    assert "bound" in zero.source.lower()


def test_every_scenario_carries_the_capture_it_came_from(scenarios) -> None:
    for name, scenario in scenarios.items():
        if name == "ZERO":
            continue
        assert "OFFICIAL" in scenario.source
        assert "html sha256" in scenario.source
        assert scenario.effective_date
        assert scenario.version


def test_a_scenario_basis_is_one_the_config_already_knows(scenarios) -> None:
    # A basis outside the config's closed vocabulary would be a fee assumption nobody named.
    for scenario in scenarios.values():
        assert scenario.basis in FeeSchedule.BASES


def test_a_scenario_builds_a_usable_fee_schedule(scenarios) -> None:
    scenario = scenarios["SUPREME_VIP"]
    config = build_config(
        run_id="fee-test", starting_capital_krw="10000000", fx_krw_per_usdt="1341",
        fx_source="test", fx_asof_utc="2026-09-24T00:00:00Z", fee_version=scenario.version,
        fee_taker_rate=str(scenario.taker_rate), fee_maker_rate=str(scenario.maker_rate),
        fee_source=scenario.source, fee_effective_date=scenario.effective_date,
        slippage_model="NONE", slippage_bps="0", leverage="10",
        risk_limit_source="test", risk_limit_sha256="deadbeef", fee_basis=scenario.basis)
    assert config.fees.taker_rate == D("0.0003")
    assert config.fees.basis == TIER_HYPOTHETICAL


def test_scenarios_are_ordered_from_most_expensive_to_least(scenarios) -> None:
    rates = [scenarios[name].taker_rate for name in scenarios if name != "ZERO"]
    assert rates == sorted(rates, reverse=True)
    assert scenarios["ZERO"].taker_rate < min(rates)


def test_describe_is_serialisable_for_a_report(scenarios) -> None:
    payload = describe(scenarios)
    assert json.loads(json.dumps(payload))
    assert {"name", "taker_rate", "basis", "source"} <= set(payload[0])
