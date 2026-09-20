"""D-V2A-1 end to end on a synthetic frozen store: the whole phase, no Drive and no real prices.

The fixture store cannot carry the declared freeze digests, so the run is executed with the
binding check off - and the test requires that such a run can never report PASS. Everything
else the phase measures (coordinates, rank frame, B0, library eligibility, truncation audit,
determinism, read-set immutability, the alpha firewall) is asserted on the produced report.
"""

import json

import pytest

from app.backtest.strategy_d_v2 import d1
from app.backtest.strategy_d_v2.config import FEATURE_NAMES, load_rules
from app.backtest.strategy_d_v2.models import HardFail
from tests.strategy_d import fixtures as store_fixtures

RULES = load_rules()


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("v2a_store")
    store_fixtures.build(root)
    return root


@pytest.fixture(scope="module")
def result(store):
    return d1.execute(store, store_fixtures.SNAPSHOT_ID, rules=RULES, log=lambda _: None,
                      audit_dates=4, bind_to_declaration=False)


def test_an_unbound_run_can_never_pass(result):
    assert result.report["freeze_binding"]["enforced"] is False
    assert result.report["hard_checks"]["freeze_binding_match"] is False
    assert result.verdict == "FAIL"


def test_a_bound_run_refuses_a_foreign_dataset(store):
    """R2: the declaration names one dataset, and the fixture is not it."""
    with pytest.raises(HardFail):
        d1.execute(store, store_fixtures.SNAPSHOT_ID, rules=RULES, log=lambda _: None,
                   audit_dates=2)


def test_every_other_hard_check_holds(result):
    checks = result.report["hard_checks"]
    assert checks["rules_checksum_match"]
    assert checks["read_set_immutable"]
    assert checks["truncation_bit_identical"]
    assert checks["determinism"]
    assert checks["alpha_firewall_clean"]


def test_the_phase_produced_coordinates_ranks_and_b0(result):
    report = result.report
    assert report["coordinates"]["names"] == list(FEATURE_NAMES)
    assert report["coordinates"]["max_lookback"] == 60
    assert report["coordinates"]["defined_vectors"] > 0
    assert report["query_sample"]["rows"] > 0
    assert report["b0"]["rows"] == report["query_sample"]["vectors_defined"]
    assert report["b0"]["future_label_access"] == "NO"
    assert set(report["b0"]["signs"]) == set(FEATURE_NAMES)


def test_the_library_is_counted_but_never_searched(result):
    library = result.report["library"]
    assert library["horizon"] == RULES.primary_horizon
    assert library["stride"] == RULES.library_stride
    assert library["rows_total"] > 0
    assert library["last_usable_end_idx"] == result.report["universe"]["eval_range"][1] - 60 - 5
    assert set(library["exclusions"]) == {"NOT_ELIGIBLE", "VECTOR_UNDEFINED", "LABEL_INVALID"}


def test_the_alpha_firewall_is_recorded_and_empty(result):
    assert result.report["alpha_firewall"] == {
        "ic_calculated": "NO", "quintiles": "NO", "analog_search": "NO",
        "neighbor_selection": "NO", "labels_read": "NO"}


def test_no_label_value_reaches_the_artifact(result):
    body = json.dumps(result.report).lower()
    for banned in ("close_return", "excess_return", "mfe", "mae"):
        assert banned not in body


def test_the_truncation_audit_actually_ran(result):
    audit = result.report["pit"]["truncation_audit"]
    assert len(audit["dates"]) == 4
    assert audit["coordinate_rows_checked"] == 4 * len(FEATURE_NAMES)
    assert audit["mismatches"] == []


def test_power_is_recomputed_at_the_measured_date_count(result):
    power = result.report["power"]
    assert power["n_dates_measured"] == result.report["universe"]["eval_dates"]
    assert power["s5_threshold"] == RULES.delta_threshold
    assert power["mde_range"][0] < power["mde_range"][1]


def test_writing_a_failed_run_leaves_no_complete_token(result, store, tmp_path):
    run_dir = d1.write_run(result, d1.run_context(store, store_fixtures.SNAPSHOT_ID, None),
                           runs_dir=tmp_path)
    assert (run_dir / "d1_report.json").exists()
    assert (run_dir / "run_identity.json").exists()
    assert not (run_dir / "COMPLETE.json").exists()
