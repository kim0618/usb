"""Tests for the E1-H5 forward holdout infrastructure.

The properties under test are the ones that make a forward test forward: the calendar boundary,
the seal, append-only writing, and the point-in-time cutoff. All synthetic.
"""

from datetime import date, datetime, timezone

import numpy as np
import pytest

from app.backtest.strategy_e1_forward import (
    audit, checkpoint, labels as labels_mod, layout, plan, registry, seal as seal_mod,
)
from app.backtest.strategy_e1_forward.layout import ForwardViolation
from app.backtest.strategy_e1_premarket import evaluate as e1_evaluate

SESSION = date(2026, 9, 17)


def _columns(n=6, **overrides):
    rng = np.random.default_rng(3)
    base = {name: rng.uniform(1.0, 5.0, n) for name in seal_mod.SEALED_FEATURES}
    base["premarket_gap"] = np.array([0.02, -0.01, 0.03, 0.04, 0.01, 0.02])[:n]
    base["premarket_rvol"] = np.array([5.0, 5.0, 1.0, 5.0, 5.0, 5.0])[:n]
    base["position_in_premarket_range"] = np.array([0.9, 0.9, 0.9, 0.5, 0.9, 0.9])[:n]
    base["return_0900_0925"] = np.array([0.01, 0.01, 0.01, 0.01, -0.01, 0.01])[:n]
    base.update({k: np.asarray(v)[:n] for k, v in overrides.items()})
    return base


def _names(n=6):
    return [f"S{i}" for i in range(n)]


def _seal(**kwargs):
    return seal_mod.build(SESSION, _names(), _columns(), provenance=seal_mod.RECONSTRUCTED,
                          sources={"minute": "d"}, rules_digest="r", **kwargs)


# -- the calendar boundary ----------------------------------------------------------------------

def test_forward_start_is_the_day_after_the_historical_boundary():
    assert layout.HISTORICAL_LAST_SESSION == date(2026, 9, 16)
    assert layout.FORWARD_HOLDOUT_START == date(2026, 9, 17)


@pytest.mark.parametrize("day", [date(2026, 9, 16), date(2026, 9, 15), date(2024, 1, 2)])
def test_a_pre_boundary_session_is_refused_everywhere(day):
    with pytest.raises(ForwardViolation):
        layout.require_forward_session(day)
    with pytest.raises(ForwardViolation):
        seal_mod.build(day, _names(), _columns(), provenance=seal_mod.RECONSTRUCTED,
                       sources={}, rules_digest="r")


def test_the_boundary_day_itself_is_accepted():
    assert layout.require_forward_session(date(2026, 9, 17)) == date(2026, 9, 17)


# -- the seal ------------------------------------------------------------------------------------

def test_seal_uses_the_e1_mask_not_a_reimplementation():
    columns = _columns()
    built = _seal()
    expected = e1_evaluate.mask("H5", columns)
    assert built.h5_rows == int(expected.sum())
    assert [row["h5"] for row in built.payload["rows"]] == expected.tolist()


def test_seal_refuses_an_unknown_provenance():
    with pytest.raises(ForwardViolation):
        seal_mod.build(SESSION, _names(), _columns(), provenance="MAYBE",
                       sources={}, rules_digest="r")


def test_seal_refuses_missing_declared_columns():
    columns = _columns()
    del columns["premarket_rvol"]
    with pytest.raises(ForwardViolation):
        seal_mod.build(SESSION, _names(), columns, provenance=seal_mod.RECONSTRUCTED,
                       sources={}, rules_digest="r")


def test_a_seal_is_never_rewritten(tmp_path):
    path = tmp_path / "seal.json"
    seal_mod.write(path, _seal())
    with pytest.raises(ForwardViolation):
        seal_mod.write(path, _seal())


def test_reading_a_tampered_seal_fails(tmp_path):
    import json
    path = tmp_path / "seal.json"
    seal_mod.write(path, _seal())
    payload = json.loads(path.read_text())
    payload["rows"][0]["h5"] = not payload["rows"][0]["h5"]
    path.write_text(json.dumps(payload))
    with pytest.raises(ForwardViolation):
        seal_mod.read(path)


def test_seal_digest_changes_when_a_feature_changes():
    first = _seal()
    moved = _columns()
    moved["premarket_gap"] = moved["premarket_gap"] + 0.001
    second = seal_mod.build(SESSION, _names(), moved, provenance=seal_mod.RECONSTRUCTED,
                            sources={"minute": "d"}, rules_digest="r")
    assert first.decision_digest != second.decision_digest


def test_seal_digest_is_order_independent():
    """The same rows in a different order are the same decision."""
    forward = _seal()
    order = [3, 1, 0, 5, 2, 4]
    columns = _columns()
    shuffled = seal_mod.build(SESSION, [_names()[i] for i in order],
                              {k: v[order] for k, v in columns.items()},
                              provenance=seal_mod.RECONSTRUCTED, sources={"minute": "d"},
                              rules_digest="r")
    assert forward.decision_digest == shuffled.decision_digest


# -- labels --------------------------------------------------------------------------------------

def _labels(n=6):
    rng = np.random.default_rng(5)
    return {name: rng.normal(0, 0.004, n) for name in labels_mod.LABEL_COLUMNS}


def test_labels_must_match_the_sealed_universe():
    stored = _seal().payload
    with pytest.raises(ForwardViolation):
        labels_mod.build(SESSION, stored, _names(5), _labels(5), sources={})


def test_labels_refuse_a_different_session():
    stored = _seal().payload
    with pytest.raises(ForwardViolation):
        labels_mod.build(date(2026, 9, 18), stored, _names(), _labels(), sources={})


def test_labels_refuse_missing_columns():
    stored = _seal().payload
    values = _labels()
    del values["R_5m"]
    with pytest.raises(ForwardViolation):
        labels_mod.build(SESSION, stored, _names(), values, sources={})


def test_labels_are_written_once(tmp_path):
    stored = _seal().payload
    payload = labels_mod.build(SESSION, stored, _names(), _labels(), sources={})
    path = tmp_path / "labels.json"
    labels_mod.write(path, payload)
    with pytest.raises(ForwardViolation):
        labels_mod.write(path, payload)


# -- registry --------------------------------------------------------------------------------------

def test_registry_appends_every_declared_field(tmp_path):
    stored = _seal().payload
    payload = labels_mod.build(SESSION, stored, _names(), _labels(), sources={})
    path = tmp_path / "observations.jsonl"
    written = registry.append_session(path, SESSION, stored, payload)
    rows = registry.read(path)
    assert written == len(rows) == 6
    for row in rows:
        assert set(row) == set(registry.FIELDS)
        assert row["decision_time"] == "09:25 ET"
        assert row["session_date"] == SESSION.isoformat()


def test_registry_refuses_a_session_it_already_holds(tmp_path):
    stored = _seal().payload
    payload = labels_mod.build(SESSION, stored, _names(), _labels(), sources={})
    path = tmp_path / "observations.jsonl"
    registry.append_session(path, SESSION, stored, payload)
    with pytest.raises(ForwardViolation):
        registry.append_session(path, SESSION, stored, payload)


def test_registry_refuses_labels_from_a_different_decision(tmp_path):
    stored = _seal().payload
    payload = labels_mod.build(SESSION, stored, _names(), _labels(), sources={})
    payload["decision_digest"] = "0" * 64
    with pytest.raises(ForwardViolation):
        registry.append_session(tmp_path / "o.jsonl", SESSION, stored, payload)


# -- checkpoints -----------------------------------------------------------------------------------

def test_no_checkpoint_is_due_below_the_first_threshold(tmp_path):
    assert checkpoint.due(249, []) is None
    assert checkpoint.status(249, tmp_path)["verdict_may_be_restated"] is False


def test_only_the_largest_reached_checkpoint_is_due(tmp_path):
    assert checkpoint.due(1200, []) == 1000
    assert checkpoint.due(1200, [250, 500, 1000]) is None


def test_only_the_final_checkpoint_may_promote(tmp_path):
    assert checkpoint.status(500, tmp_path)["may_promote_now"] is False
    assert checkpoint.status(2000, tmp_path)["may_promote_now"] is True
    assert checkpoint.PROMOTION_CHECKPOINT == 2000


def test_a_checkpoint_is_recorded_once(tmp_path):
    checkpoint.write(tmp_path, 250, {"matched_mean_lift": 0.001})
    with pytest.raises(FileExistsError):
        checkpoint.write(tmp_path, 250, {"matched_mean_lift": 0.002})


# -- the point-in-time audit ------------------------------------------------------------------------

def test_poison_preserves_positivity_and_missingness():
    minute = np.array([540, 560, 570, 575])
    close = np.array([10.0, np.nan, 11.0, 12.0])
    out = audit.poison_tape_after_cutoff(minute, {"close": close})["close"]
    assert out[0] == 10.0 and np.isnan(out[1])          # before the cutoff, untouched
    assert out[2] != 11.0 and out[3] != 12.0            # at and after 09:25, replaced
    assert np.isfinite(out[2]) and out[2] > 0


def test_feature_cutoff_audit_passes_when_nothing_reads_the_future():
    columns = _columns()

    def build(poisoned: bool):
        return _names(), columns          # a builder that ignores the poison entirely

    result = audit.feature_cutoff(SESSION, build, rules_digest="r")
    assert result["verdict"] == "PASS"
    assert result["moved_features"] == []


def test_feature_cutoff_audit_catches_a_feature_that_reads_the_future():
    clean = _columns()
    leaked = _columns()
    leaked["premarket_gap"] = leaked["premarket_gap"] + 1.0     # as if it had read the open

    def build(poisoned: bool):
        return _names(), (leaked if poisoned else clean)

    result = audit.feature_cutoff(SESSION, build, rules_digest="r")
    assert result["verdict"] == "FAIL"
    assert "premarket_gap" in result["moved_features"]


def test_audit_fails_when_the_universe_itself_moves():
    def build(poisoned: bool):
        return (_names(5), _columns(5)) if poisoned else (_names(6), _columns(6))

    result = audit.feature_cutoff(SESSION, build, rules_digest="r")
    assert result["verdict"] == "FAIL"


# -- the fetch plan ----------------------------------------------------------------------------------

def test_session_plan_is_one_minute_call_per_symbol_plus_three_market_wide():
    built = plan.session_plan(1000, reference_pages=6)
    assert built.minute_calls == 1000
    assert built.total_calls == 1000 + 1 + 6 + 1
    assert built.hours_at_basic_limit == pytest.approx(built.total_calls / 5 / 60)


def test_accumulation_scales_with_coverage():
    """Compare the unrounded projection; the reported dict rounds to two decimals."""
    narrow = plan.projected_h5_per_session(1000)
    wide = plan.projected_h5_per_session(2000)
    assert wide == pytest.approx(2 * narrow, rel=1e-9)
    assert plan.accumulation(2557)["sessions_to"]["2000"] > 0
    assert plan.accumulation(2557)["h5_rows_per_session"] > plan.accumulation(1612)["h5_rows_per_session"]


def test_projection_reproduces_the_measured_rate_at_measured_coverage():
    """At the coverage actually measured, the projection must return the measured H5 rate."""
    projected = plan.projected_h5_per_session(plan.MEASURED["covered_symbols_in_window"])
    assert projected == pytest.approx(plan.MEASURED["h5_rows_per_session_at_current_coverage"],
                                      rel=0.02)


def test_reading_a_missing_seal_is_refused(tmp_path):
    """Labelling before sealing is the failure the whole protocol exists to prevent."""
    with pytest.raises(ForwardViolation):
        seal_mod.read(tmp_path / "never_sealed.json")


def test_registry_is_append_only_across_sessions(tmp_path):
    """A second session appends beside the first rather than replacing the file."""
    path = tmp_path / "observations.jsonl"
    for day in (date(2026, 9, 17), date(2026, 9, 18)):
        built = seal_mod.build(day, _names(), _columns(), provenance=seal_mod.RECONSTRUCTED,
                               sources={"minute": "d"}, rules_digest="r")
        payload = labels_mod.build(day, built.payload, _names(), _labels(), sources={})
        registry.append_session(path, day, built.payload, payload)
    rows = registry.read(path)
    assert len(rows) == 12
    assert sorted({row["session_date"] for row in rows}) == ["2026-09-17", "2026-09-18"]


def test_provenance_is_carried_into_every_registry_row(tmp_path):
    """LIVE and RECONSTRUCTED evidence must stay distinguishable after the fact."""
    built = seal_mod.build(SESSION, _names(), _columns(), provenance=seal_mod.LIVE,
                           sources={"minute": "d"}, rules_digest="r")
    payload = labels_mod.build(SESSION, built.payload, _names(), _labels(), sources={})
    path = tmp_path / "observations.jsonl"
    registry.append_session(path, SESSION, built.payload, payload)
    assert {row["provenance"] for row in registry.read(path)} == {seal_mod.LIVE}
