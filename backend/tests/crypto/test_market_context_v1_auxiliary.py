"""The AUXILIARY layer: BTC only, read-only, and no probability computed for now.

Two properties carry the weight. The C1 row must not be able to change anything about C1 - not its
contract, not its files, not even its directory. And the directional row must be a *reading of a
frozen artifact*, not an inference: section 1's ban B2 is only true if there is no model to load.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from app.crypto.market_context_v1 import auxiliary as A
from app.crypto.market_context_v1 import contract as C

NOW = int(time.time() * 1000)


def c1_store(root, *, decided_ms=None, signals=0, c1x=0, state=True):
    root.mkdir(parents=True, exist_ok=True)
    if state:
        (root / "state.json").write_text(json.dumps({
            "armed": True, "bars": 47_520, "signals": signals,
            "last_decided_at_ms": NOW if decided_ms is None else decided_ms,
            "schema_version": "C1_SIGNAL_V1", "contract_sha256": "c49c"}))
    if signals:
        lines = [json.dumps({"signal_id": f"c1-{index}", "triggered_at_ms": NOW - index * 60_000,
                             "signal_bar_ms": NOW - index * 60_000, "state": "TRIGGERED",
                             "strategy": "C1", "direction": "x", "horizon_min": 240,
                             "signal_price": 86_000.0, "official_entry_price": 86_010.0,
                             "schema_version": "C1_SIGNAL_V1", "contract_sha256": "c49c"})
                 for index in range(signals)]
        (root / "signals.jsonl").write_text("\n".join(lines) + "\n")
    if c1x:
        (root / "c1x.jsonl").write_text(json.dumps({
            "signal_id": "c1-0", "parent_signal_id": "c1-0", "status": "TRIGGERED",
            "confirmation_count": 2, "premium_value": 0.5, "premium_bucket": 3,
            "schema_version": "C1_SIGNAL_V1"}) + "\n")
    return root


def research_root(tmp_path, body):
    root = tmp_path / "research"
    (root / "directional_probability_r0").mkdir(parents=True)
    (root / A.DIRECTIONAL_RELATIVE).write_text(json.dumps(body))
    return root


DIRECTIONAL = {
    "verdict": "WEAK_DIRECTIONAL_MODEL", "usable_horizons": [], "weak_horizons": [30, 60, 120],
    "horizons": {"60": {"n": 496_354, "first": "2022-01-01", "last": "2026-09-22",
                        "UP": {"probability": {"auc": 0.5647, "bss": 0.0127,
                                               "base_rate": 0.384, "ece": 0.0045}},
                        "DOWN": {"probability": {"auc": 0.5581, "bss": 0.0109,
                                                 "base_rate": 0.389, "ece": 0.0108}},
                        "gates": {"ignored": True}}},
}


# --- C1 is read, never touched -------------------------------------------------------------------

def test_the_c1_row_does_not_create_the_store_it_cannot_find(tmp_path):
    missing = tmp_path / "nope"
    view = A.c1_view(NOW, root=missing)
    assert view["state"] == C.UNKNOWN
    assert view["root_exists"] is False
    assert missing.exists() is False


def test_the_c1_row_writes_nothing_into_a_store_it_can_find(tmp_path):
    root = c1_store(tmp_path / "c1", signals=2, c1x=1)
    before = {path.name: path.stat().st_mtime_ns for path in root.iterdir()}
    A.c1_view(NOW, root=root)
    after = {path.name: path.stat().st_mtime_ns for path in root.iterdir()}
    assert before == after
    assert sorted(before) == ["c1x.jsonl", "signals.jsonl", "state.json"]


def test_a_current_engine_reads_live_and_an_idle_one_reads_stale(tmp_path):
    fresh = A.c1_view(NOW, root=c1_store(tmp_path / "fresh"))
    assert fresh["state"] == C.LIVE and fresh["reasons"] == [
        "signals.jsonl:FILE_NOT_FOUND", "c1x.jsonl:FILE_NOT_FOUND"]
    idle = A.c1_view(NOW, root=c1_store(tmp_path / "idle",
                                        decided_ms=NOW - A.C1_STALE_MS - 1))
    assert idle["state"] == C.STALE
    assert "C1_ENGINE_LAST_DECIDED_OLDER_THAN_FRESHNESS_BOUND" in idle["reasons"]


def test_freshness_is_judged_on_the_engines_own_last_decision_not_the_file_mtime(tmp_path):
    """An atomic rewrite touches the mtime whether or not a bar was decided."""
    root = c1_store(tmp_path / "c1", decided_ms=NOW - 3 * 60 * 60_000)
    os.utime(root / "state.json", None)              # as a rewrite would
    view = A.c1_view(NOW, root=root)
    assert view["state"] == C.STALE
    assert view["engine_state_age_ms"] is not None and view["engine_state_age_ms"] < 5_000
    assert view["last_decision_age_ms"] > 60_000


def test_an_empty_store_reads_unknown_rather_than_a_quiet_engine(tmp_path):
    root = tmp_path / "c1"
    root.mkdir()
    view = A.c1_view(NOW, root=root)
    assert view["state"] == C.UNKNOWN
    assert "C1_STORE_EMPTY_OR_ABSENT" in view["reasons"]


def test_a_torn_final_line_is_dropped_rather_than_repaired(tmp_path):
    root = c1_store(tmp_path / "c1", signals=2)
    with open(root / "signals.jsonl", "a") as handle:
        handle.write('{"signal_id": "c1-torn", "trig')
    view = A.c1_view(NOW, root=root)
    assert view["signal_records"] == 2
    assert view["latest_signal"]["signal_id"] == "c1-1"


def test_the_row_carries_a_fixed_key_list_so_a_schema_change_shows_as_an_absence(tmp_path):
    root = c1_store(tmp_path / "c1", signals=1)
    view = A.c1_view(NOW, root=root)
    assert set(view["latest_signal"]) == set(A.SIGNAL_KEYS)
    assert view["read_mode"] == "BYTES_ONLY_NO_C1_MODULE_IMPORTED"
    assert view["contract_evaluated"] is False


def test_the_row_does_not_carry_the_shadow_trades_plan(tmp_path):
    """A context panel carrying an entry price reads as a proposal however it is labelled."""
    root = c1_store(tmp_path / "c1", signals=1)
    view = A.c1_view(NOW, root=root)
    for banned in ("official_entry_price", "official_entry_at_ms", "planned_exit_at_ms",
                   "signal_price"):
        assert banned not in view["latest_signal"], banned
    assert "86010" not in json.dumps(view)


def test_the_weight_label_is_in_the_payload_not_only_in_the_prose(tmp_path):
    view = A.c1_view(NOW, root=c1_store(tmp_path / "c1"))
    assert view["weight"] == C.WEIGHT_AUXILIARY == "auxiliary"


# --- the directional row is a reading, not an inference -------------------------------------------

def test_the_verdict_and_its_scope_are_read_verbatim(tmp_path):
    view = A.directional_view(root=research_root(tmp_path, DIRECTIONAL))
    assert view["verdict"] == "WEAK_DIRECTIONAL_MODEL"
    assert view["usable_horizons"] == []
    assert view["weak_horizons"] == [30, 60, 120]
    assert view["weight"] == C.WEIGHT_WEAK_AUXILIARY == "weak_auxiliary"


def test_no_probability_is_computed_for_the_current_moment(tmp_path):
    view = A.directional_view(root=research_root(tmp_path, DIRECTIONAL))
    assert view["computed_for_now"] is False
    assert view["inference_run"] is False


def test_the_bucket_names_come_out_of_the_artifact_rather_than_being_spelled_here(tmp_path):
    """Which is both why the panel is independent of the study's labelling and why the panel's own
    source stays free of the direction vocabulary it is banned from containing."""
    view = A.directional_view(root=research_root(tmp_path, DIRECTIONAL))
    assert set(view["horizons"]["60"]["buckets"]) == {"UP", "DOWN"}
    renamed = json.loads(json.dumps(DIRECTIONAL).replace("UP", "ALPHA").replace("DOWN", "BETA"))
    other = A.directional_view(root=research_root(tmp_path / "second", renamed))
    assert set(other["horizons"]["60"]["buckets"]) == {"ALPHA", "BETA"}


def test_a_sub_object_that_is_not_a_probability_block_is_not_mistaken_for_one(tmp_path):
    view = A.directional_view(root=research_root(tmp_path, DIRECTIONAL))
    assert "gates" not in view["horizons"]["60"]["buckets"]


def test_the_later_studies_that_bound_the_reading_travel_with_it(tmp_path):
    """An AUC of 0.56 shown without them will be over-read."""
    view = A.directional_view(root=research_root(tmp_path, DIRECTIONAL))
    studies = {bound["study"] for bound in view["bounds"]}
    assert studies == {"BTC-P1", "BTC-P2"}
    text = json.dumps(view["bounds"])
    assert "0.502" in text and "0.533" in text
    assert "STRONG 0/64" in text and "P3 NOT_AUTHORIZED" in text


def test_an_unreadable_artifact_is_a_screen_state(tmp_path):
    view = A.directional_view(root=tmp_path / "nowhere")
    assert view["state"] == C.UNKNOWN
    assert view["reasons"] == ["ARTIFACT_NOT_READABLE"]
    assert view["verdict"] is None
    assert view["computed_for_now"] is False


def test_the_real_frozen_artifact_still_says_what_the_contract_quotes():
    """Section 9 quotes this artifact. If it moved, the contract's sentence became wrong."""
    view = A.directional_view()
    if view["state"] == C.UNKNOWN:
        pytest.skip("the frozen artifact is not in this tree")
    assert view["verdict"] == "WEAK_DIRECTIONAL_MODEL"
    assert view["usable_horizons"] == []
    assert "WEAK_DIRECTIONAL_MODEL" in C.DIRECTIONAL_NOTE


# --- BTC only ------------------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT"])
def test_the_layer_is_absent_for_a_non_btc_symbol_rather_than_empty(symbol, tmp_path):
    view = A.auxiliary_view(symbol=symbol, now_ms=NOW, c1_root=c1_store(tmp_path / "c1",
                                                                       signals=1))
    assert view["state"] == C.UNAVAILABLE
    assert view["rendered"] is False
    assert view["c1"] is None and view["directional"] is None
    assert view["reasons"] == [C.REASON_RESEARCH_BTC_ONLY]
    # And nothing from the BTC store reached it, which is the leak the matrix forbids.
    assert "c1-0" not in json.dumps(view)


def test_btc_gets_both_rows_with_their_own_states(tmp_path):
    view = A.auxiliary_view(symbol=C.BTC, now_ms=NOW,
                            c1_root=c1_store(tmp_path / "c1", signals=1, c1x=1),
                            research_root=research_root(tmp_path, DIRECTIONAL))
    assert view["rendered"] is True
    assert view["c1"]["state"] == C.LIVE
    assert view["directional"]["state"] == C.LIVE
    assert view["state"] == C.LIVE


def test_one_row_stale_makes_the_layer_stale_rather_than_live(tmp_path):
    view = A.auxiliary_view(symbol=C.BTC, now_ms=NOW,
                            c1_root=c1_store(tmp_path / "c1",
                                             decided_ms=NOW - A.C1_STALE_MS - 1),
                            research_root=research_root(tmp_path, DIRECTIONAL))
    assert view["state"] == C.STALE


def test_one_row_missing_makes_the_layer_partial(tmp_path):
    view = A.auxiliary_view(symbol=C.BTC, now_ms=NOW, c1_root=tmp_path / "nope",
                            research_root=research_root(tmp_path, DIRECTIONAL))
    assert view["state"] == C.PARTIAL
