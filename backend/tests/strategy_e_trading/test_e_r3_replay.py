"""E-R3 V1.1 development replay tests.

Synthetic fixtures for the replay mechanics, plus identity checks of the committed result
artifact. No historical tape is read here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e_d6 import metrics as X
from app.backtest.strategy_e_d6 import run as R6
from app.backtest.strategy_e_r3 import build as B
from app.backtest.strategy_e_r3 import run as R3
from app.market.calendar import MarketCalendar
from app.strategy_e.execution import MISSING_ENTRY_BAR, NOT_SELECTED_CAPACITY
from app.strategy_e_v1_1 import decision as D
from app.strategy_e_v1_1 import replay_protocol as RP
from app.strategy_e_v1_1 import universe as U


ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_candidate"
RESULT = DOCS / "strategy_e_v1_1_replay_result.json"
CAL = MarketCalendar("America/New_York")
SESSION = date(2026, 9, 15)


def _prior(count: int = 8) -> list[date]:
    days, day = [], SESSION
    for _ in range(count):
        day = CAL.previous_trading_day(day)
        days.append(day)
    return sorted(days)


def _tape(symbol: str, *, d_open: bool = True, exit_bar: bool = True, later: bool = False,
          open_price: float = 10.0) -> SymbolTape:
    days, minutes, prices, volumes = [], [], [], []
    plan = [(d, 1_000.0) for d in _prior()] + [(SESSION, 5_000.0)]
    if later:
        plan.append((CAL.next_trading_day(SESSION), 9_999.0))
    for day, volume in plan:
        pre = [4 * 60 + 5, 7 * 60, 8 * 60 + 30] + list(range(9 * 60, 9 * 60 + 25, 4))
        regular = []
        if day != SESSION or d_open:
            regular = [P.OPEN_MIN, P.OPEN_MIN + 1, P.OPEN_MIN + 2, P.OPEN_MIN + 3]
            if day != SESSION or exit_bar:
                regular.append(P.OPEN_MIN + 4)
        for k, minute in enumerate(pre + regular):
            price = 10.2 + 0.01 * k if minute < P.OPEN_MIN else open_price * (1 + 0.001 * (minute - P.OPEN_MIN))
            days.append(ordinal(day)); minutes.append(minute); prices.append(price); volumes.append(volume)
    p = np.array(prices)
    return SymbolTape(symbol=symbol, et_day=np.array(days, dtype=np.int64),
                      minute=np.array(minutes, dtype=np.int64), open=p, high=p * 1.0001,
                      low=p * 0.9999, close=p, volume=np.array(volumes), vwap=p, sources={},
                      overlap_sessions=0)


def _daily(n: int, *, open_d: bool = True) -> B.DailySession:
    return B.DailySession(np.ones(n, bool), np.full(n, open_d), np.full(n, 10.0), np.full(n, 5e7))


def _setup(tapes: dict[str, SymbolTape], open_d: dict[str, bool] | None = None):
    rows = {s: B.symbol_rows(t, {SESSION}) for s, t in tapes.items()}
    columns = {s: j for j, s in enumerate(sorted(tapes))}
    daily = _daily(len(columns))
    opens = np.array([(open_d or {}).get(s, True) for s in sorted(tapes)])
    daily = B.DailySession(daily.eligible, opens, daily.close_prev, daily.dollar_volume_prev)
    frame = B.frame_for(SESSION, rows, daily, columns, spy_close_previous=499.0)
    origins = {s: B.origin(bool(opens[columns[s]]), rows[s][SESSION].has_0930_open)
               for s in frame.symbols}
    return rows, frame, origins


# 4/5. corrections A and B --------------------------------------------------------------------
@pytest.mark.parametrize("has_open,has_0930,expected", [
    (True, True, "V1"), (False, True, "A"), (True, False, "B"), (False, False, "AB")])
def test_origin_attributes_each_row_to_exactly_the_removed_tests(has_open, has_0930, expected):
    assert B.origin(has_open, has_0930) == expected


def test_variants_admit_exactly_their_corrections() -> None:
    assert B.VARIANTS == {"V1": ("V1",), "A_only": ("V1", "A"), "B_only": ("V1", "B"),
                          "V1_1": ("V1", "A", "B", "AB")}


def test_symbol_rows_keep_post_cutoff_facts_out_of_the_row() -> None:
    with_bar = B.symbol_rows(_tape("A"), {SESSION})[SESSION]
    without = B.symbol_rows(_tape("A", d_open=False), {SESSION})[SESSION]
    assert with_bar.has_0930_open and not without.has_0930_open
    assert with_bar.row == without.row == U.premarket_row(_tape("A"), SESSION)
    assert [b.bar_start_et for b in with_bar.bars["09:30"]] == ["09:30"]
    assert without.bars["09:30"] == [] and np.isnan(without.labels["R_5m"])


def test_structural_delta_counts_each_variant() -> None:
    _, frame, origins = _setup({"A": _tape("A"), "B": _tape("B", d_open=False),
                                "C": _tape("C"), "D": _tape("D")}, open_d={"C": False})
    delta = R3.structural_delta({SESSION: frame}, {SESSION: origins})
    v = delta["variants"]
    assert delta["rows_by_origin"] == {"V1": 2, "A": 1, "B": 1, "AB": 0}
    assert (v["V1"]["h5_candidates"], v["A_only"]["h5_candidates"],
            v["B_only"]["h5_candidates"], v["V1_1"]["h5_candidates"]) == (2, 3, 3, 4)
    assert v["V1_1"]["selected_candidates"] == 3
    assert v["V1_1"]["selection_changed_sessions_vs_v1"] == 1


# 3/6. superset identity, no other drift -------------------------------------------------------
def _v1_like(frame, origins, drop: str | None = None, nudge: str | None = None):
    keep = [k for k, s in enumerate(frame.symbols) if origins[s] == "V1" and s != drop]
    features = {n: frame.features[n][keep].copy() for n in SEALED_FEATURES}
    if nudge is not None:
        features["premarket_rvol"][[frame.symbols[k] for k in keep].index(nudge)] += 1e-12
    symbols = np.array([frame.symbols[k] for k in keep], dtype=object)
    return _Rows(np.array([SESSION] * len(keep), dtype=object), symbols, features)


class _Rows:
    def __init__(self, sessions, tickers, features):
        self.sessions, self.tickers, self.features = sessions, tickers, features

    def __len__(self):
        return len(self.sessions)


def test_attribution_passes_only_for_an_exact_v1_subset() -> None:
    _, frame, origins = _setup({"A": _tape("A"), "B": _tape("B", d_open=False), "C": _tape("C")})
    frames, o = {SESSION: frame}, {SESSION: origins}
    assert R3.attribute(_v1_like(frame, origins), frames, o)["pass"]
    assert not R3.attribute(_v1_like(frame, origins, drop="A"), frames, o)["pass"]
    nudged = R3.attribute(_v1_like(frame, origins, nudge="C"), frames, o)
    assert not nudged["pass"] and nudged["feature_mismatches"] == 1


# 7. PIT -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["noise", "delete"])
def test_poison_after_0924_leaves_the_row_unchanged(mode) -> None:
    tape = _tape("A", later=True)
    assert U.premarket_row(B.poisoned(tape, SESSION, mode), SESSION) == U.premarket_row(tape, SESSION)
    if mode == "delete":
        cut = B.poisoned(tape, SESSION, mode)
        assert not ((cut.et_day == ordinal(SESSION)) & (cut.minute >= P.OPEN_MIN)).any()


def test_daily_poison_after_d_minus_1_changes_nothing() -> None:
    t_len, n = 26, 3
    sessions, day = [], date(2026, 6, 1)
    for _ in range(t_len):
        sessions.append(day)
        day = CAL.next_trading_day(day)
    rng = np.random.default_rng(1)
    close = rng.uniform(20, 30, (t_len, n))
    factor = np.ones((t_len, n))
    member = np.ones((t_len, n), bool)
    panel = SimpleNamespace(close=close, volume=np.full((t_len, n), 1e6), open=close.copy(),
                            split_arrays=lambda: (factor, None, None), membership=lambda: member)
    clean = B.daily_sessions(panel, sessions, [22, 24], CAL)
    dirty = B.daily_sessions(panel, sessions, [22, 24], CAL, poison=True)
    for s in clean:
        assert np.array_equal(clean[s].eligible, dirty[s].eligible)


def test_pit_sample_is_the_e_d6_rule() -> None:
    symbols = [f"S{i:04d}" for i in range(1902)]
    assert B.pit_sample(symbols) == sorted(symbols)[::47]


# 8/13/15. no backfill, sizing, session portfolio ---------------------------------------------
def test_replay_sealed_never_promotes_candidate_four_and_weights_the_executed() -> None:
    tapes = {"A": _tape("A"), "B": _tape("B", d_open=False), "C": _tape("C", open_price=20.0),
             "D": _tape("D")}
    rows, frame, origins = _setup(tapes)
    sealed = D.seal(frame, source_digest="s")
    assert sealed.selected == ("A", "B", "C") and sealed.not_selected == ("D",)
    result = R3.replay_sealed(sealed, frame, rows, origins, CAL)
    by = {r["symbol"]: r for r in result["records"]}
    assert by["B"]["entry_status"] == MISSING_ENTRY_BAR and not by["B"]["standard_pnl"]
    assert by["D"]["entry_status"] == NOT_SELECTED_CAPACITY
    assert by["A"]["weight"] == by["C"]["weight"] == "1/2"
    expected = Decimal(1) / 2 * by["A"]["COST_10BP"] + Decimal(1) / 2 * by["C"]["COST_10BP"]
    assert result["returns"]["COST_10BP"] == expected
    assert by["B"]["universe_origin"] == "B"
    diag = R3.entry_invalid_diagnostics([result], {SESSION.isoformat(): sealed})
    assert diag["entry_invalid"] == 1 and diag["no_backfill_affected_sessions"] == 1


def test_missing_exit_is_unresolved_not_substituted() -> None:
    rows, frame, origins = _setup({"A": _tape("A", exit_bar=False)})
    result = R3.replay_sealed(D.seal(frame, source_digest="s"), frame, rows, origins, CAL)
    record = result["records"][0]
    assert record["exit_status"] == "INVALID_EXIT" and not record["standard_pnl"]
    assert result["returns"]["COST_10BP"] == 0


def test_code_digest_covers_the_replay_packages() -> None:
    assert len(R3.code_digest()) == 64


# -- committed result artifact --------------------------------------------------------------------

@pytest.fixture(scope="module")
def result():
    return json.loads(RESULT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def protocol():
    return RP.load_rules()


def test_result_file_matches_its_checksum_and_repeat_check(result) -> None:
    recorded = json.loads((DOCS / "strategy_e_v1_1_replay_result.sha256").read_text("utf-8"))
    assert recorded["file_sha256"] == hashlib.sha256(RESULT.read_bytes()).hexdigest()
    assert recorded["run_id"] == result["run_id"]
    check = recorded["repeat_check"]
    assert check["identical"] and check["artifacts_identical"]
    assert check["first_result_digest"] == check["this_result_digest"] == recorded["file_sha256"]


def test_result_dataset_is_the_frozen_binding(result, protocol) -> None:
    data, found = protocol["dataset"], result["dataset"]
    assert found["minute_tape"]["digest"] == data["minute_tape_digest"]
    assert (found["minute_tape"]["files"], found["minute_tape"]["symbols"]) == (2152, 1902)
    assert found["minute_tape"]["legacy_minute_digest"] == data["legacy_minute_digest"]
    assert found["daily"]["freeze_digest"] == data["daily_freeze_digest"]
    assert found["daily"]["read_set_digest"] == data["daily_read_set_digest"]
    assert found["minute_tape"]["last_bound_file_date"] < "2026-09-17"
    assert (found["evaluation_sessions"], found["evaluation_first"], found["evaluation_last"]) == (
        480, "2024-10-16", "2026-09-16")


def test_v1_baseline_was_reproduced_before_any_return(result) -> None:
    repro = result["prechecks"]["v1_reproduction"]
    assert repro["match"] and repro["found"] == {"universe_rows": 70738, "h5_rows": 1729}


def test_v1_1_is_a_bit_identical_superset_with_no_other_drift(result) -> None:
    att = result["prechecks"]["attribution"]
    assert att["pass"] and att["v1_variant_matches_e_d6"]
    assert att["v1_rows_missing_from_v1_1"] == att["origin_v1_rows_not_in_v1"] == 0
    assert att["feature_mismatches"] == att["h5_flag_mismatches"] == 0
    delta = result["structural_delta"]
    assert sum(delta["rows_by_origin"].values()) == result["dataset"]["v1_1_universe_rows"]
    assert delta["variants"]["V1"]["universe_rows"] == 70738
    assert delta["variants"]["V1"]["h5_candidates"] == 1729
    assert delta["variants"]["V1_1"]["h5_candidates"] == result["funnel"]["H5_candidates"]["count"]


def test_pit_poison_passed_on_both_modes(result) -> None:
    pit = result["prechecks"]["pit_poison"]
    assert pit["verdict"] == "PASS" and pit["daily_poison_eligibility_unchanged"]
    assert all(m["seal_digests_moved"] == 0 and m["sessions_compared"] == 480
               for m in pit["modes"].values())


def test_coverage_is_standard_trades_over_valid_entries(result) -> None:
    funnel = result["funnel"]
    assert funnel["standard_pnl_coverage"] == (funnel["standard_pnl_trades"]["count"]
                                               / funnel["valid_entries"]["count"])
    assert result["entry_invalid"]["entry_invalid"] == funnel["invalid_entries"]["count"]


def test_bootstrap_and_blocks_follow_the_protocol(result, protocol) -> None:
    gate = result["primary_gate"]
    boot = gate["bootstrap"]
    assert (boot["seed"], boot["replicates"]) == (X.BOOTSTRAP_SEED, X.BOOTSTRAP_REPLICATES)
    assert [[b["first"], b["last"]] for b in gate["blocks"]] == protocol["statistics"]["expected_block_bounds"]
    assert gate["positive_blocks"] == sum(b["mean"] > 0 for b in gate["blocks"])
    assert gate["scenario"] == "COST_10BP"


def test_verdict_is_the_frozen_gate_applied_to_the_numbers(result, protocol) -> None:
    gate = result["primary_gate"]
    recomputed = X.verdict(integrity=result["integrity"]["pass"],
                           coverage=result["funnel"]["standard_pnl_coverage"],
                           mean_10bp=gate["all_session_mean"],
                           profit_factor_10bp=gate["profit_factor"],
                           ci_low=gate["bootstrap"]["ci_low"],
                           positive_blocks=gate["positive_blocks"])["verdict"]
    assert recomputed == result["verdict"]["e_d6_function_output"]
    assert protocol["verdict_labels"][recomputed] == result["verdict"]["label"]


def test_concentration_keeps_every_symbol(result) -> None:
    conc = result["concentration"]["cost_10bp"]
    assert {"unique_symbols", "top1", "top5", "top10", "hhi_trade_count", "trades_per_symbol"} <= set(conc)
    assert conc["top1"]["share_of_total"] == pytest.approx(
        conc["top1"]["contribution"] / conc["total_contribution"])
    assert result["concentration"]["gate"] == "NONE; DIAGNOSTIC ONLY"


def test_output_schema(result, protocol) -> None:
    assert set(protocol["outputs"]["result_json_sections"]) <= set(result)
    layers = result["evidence_layers"]
    assert set(layers) >= {"A_research_h5", "B_trading_v1_e_d6", "C_trading_v1_1_e_r3"}


def test_frozen_artifacts_untouched_by_e_r3() -> None:
    names = ("strategy_e_trading_v1_1_rules.json", "strategy_e_forward_feature_context_v1.json",
             "strategy_e_v1_1_replay_rules.json", "strategy_e_d6_result_v1.json",
             "strategy_e_backtest_rules_v1.json", "strategy_e_d6_development_tape_v1.json")
    for name in names:
        frozen = subprocess.run(["git", "-C", str(ROOT), "show",
                                 f"fa4258e:docs/backtest/strategy_e_candidate/{name}"],
                                capture_output=True, check=True).stdout
        assert (DOCS / name).read_bytes() == frozen
