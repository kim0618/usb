"""D-AGG-1 tests: excursion geometry, thresholds, PIT, parent binding, isolation, alpha firewall.

Every expected value is written by hand on synthetic panels; no Drive, no real prices.
"""

import ast
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pyarrow as pa
import pytest

from app.backtest.strategy_c_selection.panel import Panel, SplitEvent, with_changes
from app.backtest.strategy_d_agg import config, excursions, parent, pit, universe
from app.backtest.strategy_d_agg.d1 import assert_no_alpha
from app.backtest.strategy_d_agg.models import STATUS_CODE, HardFail
from tests.strategy_d_v2.fixtures import make_panel, sessions

PACKAGE = Path(config.__file__).resolve().parent
GEOMETRY = {"horizon": 5, "up": 0.10, "down": -0.10, "ca_ratio": 3.0}
D = 10          # signal session in the hand panels
T = 24


def hand_panel(highs=(101, 108, 110, 109, 105), lows=(99, 96, 94, 97, 98), entry=100.0,
               splits=(), extra=None) -> Panel:
    """One ticker, flat at ``entry`` except the D+1..D+5 window given by hand. O = C inside L..H."""
    grid = sessions(T)
    close = np.full((T, 1), float(entry))
    open_ = close.copy()
    high = close * 1.001
    low = close * 0.999
    for k, (h, l) in enumerate(zip(highs, lows), start=1):
        high[D + k, 0], low[D + k, 0] = h, l
        mid = min(max(entry, l), h)
        open_[D + k, 0] = close[D + k, 0] = mid
    open_[D + 1, 0] = entry
    high[D + 1, 0] = max(high[D + 1, 0], entry)
    low[D + 1, 0] = min(low[D + 1, 0], entry)
    panel = Panel(grid, ("AAA",), open_, high, low, close, np.full((T, 1), 1e6), tuple(splits),
                  {grid[0]: frozenset({"AAA"})})
    if extra:
        panel = extra(panel)
    return panel


def geo(panel: Panel, row: int = D) -> dict:
    ex = excursions.compute(panel, **GEOMETRY)
    return {k: v[0] for k, v in ex.gather(np.array([row]), np.array([0])).items()}


def set_cell(panel: Panel, field: str, row: int, value: float) -> Panel:
    array = getattr(panel, field).copy()
    array[row, 0] = value
    return with_changes(panel, **{field: array})


def drop_bar(panel: Panel, row: int) -> Panel:
    out = {}
    for name in ("open", "high", "low", "close", "volume"):
        array = getattr(panel, name).copy()
        array[row, 0] = np.nan
        out[name] = array
    return with_changes(panel, **out)


# -- rules ------------------------------------------------------------------------------------------

def test_rules_checksum_matches_declaration():
    rules = config.load_rules()
    assert rules.checksum == config.DECLARED_CANONICAL
    assert (rules.horizon, rules.up_threshold, rules.down_threshold) == (5, 0.10, -0.10)


def test_edited_rules_are_a_different_study(tmp_path):
    raw = json.loads(config.RULES_PATH.read_text(encoding="utf-8"))
    raw["setup"]["primary_quantile"] = 0.05
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HardFail):
        config.load_rules(path)


# -- parent binding ---------------------------------------------------------------------------------

def signal_table(analog=(0.01, np.nan), b0=(0.1, 0.2)) -> pa.Table:
    return pa.table({"query_date_idx": [260, 260], "query_ticker_col": [3, 7],
                     "query_ticker": ["AAA", "BBB"], "sample_rank": [0, 1],
                     "analog_signal_A": list(analog), "signal_status": ["OK", "OK"],
                     "b0": list(b0)})


def test_binding_ignores_b0_and_tracks_a():
    cols = config.load_rules().signal_binding_columns
    base = parent.a_binding_digest(signal_table(), cols)
    assert parent.a_binding_digest(signal_table(b0=(0.1 + 2.8e-17, 0.2)), cols) == base
    assert parent.a_binding_digest(signal_table(analog=(0.011, np.nan)), cols) != base


def test_binding_nan_policy_and_column_order():
    cols = config.load_rules().signal_binding_columns
    quiet = parent.a_binding_digest(signal_table(analog=(0.01, np.nan)), cols)
    other_nan = np.frombuffer(np.uint64(0x7FF8000000000001).tobytes(), dtype=np.float64)[0]
    assert parent.a_binding_digest(signal_table(analog=(0.01, other_nan)), cols) == quiet
    with pytest.raises(HardFail):
        parent.a_binding_digest(signal_table(), tuple(reversed(cols)))


def test_binding_rejects_null_strings():
    table = signal_table().set_column(2, "query_ticker", pa.array(["AAA", None]))
    with pytest.raises(HardFail):
        parent.a_binding_digest(table, config.load_rules().signal_binding_columns)


def test_parent_file_digest_detects_mutation(tmp_path):
    for phase, run in config.V2A_LINEAGE.items():
        (tmp_path / run).mkdir()
        (tmp_path / run / "x.json").write_text("{}", encoding="utf-8")
    before = parent.parent_files(tmp_path, config.V2A_LINEAGE)
    (tmp_path / config.V2A_LINEAGE["D3"] / "x.json").write_text('{"a":1}', encoding="utf-8")
    assert parent.parent_files(tmp_path, config.V2A_LINEAGE) != before


# -- formula ----------------------------------------------------------------------------------------

def test_mfe_mae_exact_on_the_declared_example():
    g = geo(hand_panel())
    assert g["entry_open"] == 100.0
    assert g["mfe"] == pytest.approx(0.10, abs=1e-15) and g["mae"] == pytest.approx(-0.06, abs=1e-15)
    assert g["up"] and not g["down"]
    assert g["valid"] and g["status"] == STATUS_CODE["VALID"] and g["window_bars"] == 5


def test_up10_inclusive_under_float_error():
    assert 3.3 / 3.0 - 1.0 < 0.10          # the float fact the tolerance exists for
    g = geo(hand_panel(highs=(3.05, 3.1, 3.3, 3.2, 3.1), lows=(2.95,) * 5, entry=3.0))
    assert g["up"] and g["mfe"] < 0.10
    strict, _ = excursions.threshold_flags(np.array([g["mfe"]]), np.array([0.0]), np.array([True]),
                                           up=0.10, down=-0.10, eps=0.0)
    assert not strict[0]                    # without the declared tolerance +10.00% would be lost


def test_up10_rejects_just_below():
    g = geo(hand_panel(highs=(101, 105, 109.999, 104, 103)))
    assert g["mfe"] == pytest.approx(0.09999, abs=1e-12) and not g["up"]


def test_dn10_inclusive_and_just_above():
    assert 2.7 / 3.0 - 1.0 > -0.10
    assert geo(hand_panel(highs=(3.05,) * 5, lows=(2.95, 2.9, 2.7, 2.8, 2.9), entry=3.0))["down"]
    g = geo(hand_panel(lows=(99, 95, 90.001, 97, 98)))
    assert g["mae"] == pytest.approx(-0.09999, abs=1e-12) and not g["down"]


def test_threshold_flags_force_invalid_false():
    up, down = excursions.threshold_flags(np.array([0.2, np.nan, 0.2]), np.array([-0.2, np.nan, -0.2]),
                                          np.array([True, True, False]), up=0.10, down=-0.10)
    assert up.tolist() == [True, False, False] and down.tolist() == [True, False, False]


def test_up10_and_dn10_are_not_exclusive():
    g = geo(hand_panel(highs=(101, 111, 104, 103, 102), lows=(99, 97, 89, 95, 96)))
    assert g["up"] and g["down"]


def test_signal_session_is_excluded():
    base = geo(hand_panel())
    moved = geo(set_cell(set_cell(hand_panel(), "high", D, 500.0), "low", D, 1.0))
    assert moved["mfe"] == base["mfe"] and moved["mae"] == base["mae"]


def test_entry_session_and_horizon_session_are_included():
    assert geo(hand_panel(highs=(125, 101, 101, 101, 101)))["mfe"] == pytest.approx(0.25)
    assert geo(hand_panel(highs=(101, 101, 101, 101, 130)))["mfe"] == pytest.approx(0.30)
    assert geo(hand_panel(lows=(80, 99, 99, 99, 99)))["mae"] == pytest.approx(-0.20)


def test_session_after_horizon_is_excluded():
    base = geo(hand_panel())
    moved = geo(set_cell(set_cell(hand_panel(), "high", D + 6, 900.0), "low", D + 6, 90.0))
    assert moved["mfe"] == base["mfe"] and moved["mae"] == base["mae"]


def test_entry_open_mutation_moves_both():
    base = geo(hand_panel())
    moved = geo(hand_panel(entry=102.0))
    assert moved["entry_open"] == 102.0
    assert moved["mfe"] != base["mfe"] and moved["mae"] != base["mae"]


# -- PIT --------------------------------------------------------------------------------------------

def test_future_after_window_leaves_values_and_ca_window_rule():
    base = geo(hand_panel())
    wild = hand_panel()
    for row in range(D + 6, T):
        for field, factor in (("open", 7.0), ("high", 9.0), ("low", 0.05), ("close", 7.0)):
            wild = set_cell(wild, field, row, getattr(wild, field)[row, 0] * factor)
    moved = geo(wild)
    # values never read D+6..; validity does, by the frozen V2-A CA window D..D+10
    assert moved["entry_open"] == base["entry_open"]
    assert moved["status"] == STATUS_CODE["LABEL_CA_SUSPECT"] and not moved["valid"]
    late = hand_panel()
    for row in range(D + 11, T):
        late = set_cell(late, "close", row, 700.0)
        late = set_cell(late, "high", row, 900.0)
    moved_late = geo(late)
    assert all(np.array_equal(moved_late[k], base[k], equal_nan=True) for k in base)


def test_high_and_low_positive_controls():
    base = geo(hand_panel())
    high = geo(set_cell(hand_panel(), "high", D + 3, 150.0))
    low = geo(set_cell(hand_panel(), "low", D + 4, 60.0))
    assert high["mfe"] > base["mfe"] and high["mae"] == base["mae"]
    assert low["mae"] < base["mae"] and low["mfe"] == base["mfe"]


def test_audit_one_on_synthetic_panel_has_witnesses_and_no_findings():
    panel = make_panel(80)
    result = pit.audit_one(panel, 40, GEOMETRY, panel.tickers)
    assert result["findings"] == []
    for name in ("HIGH_CONTROL", "LOW_CONTROL", "HORIZON_CONTROL", "ENTRY_CONTROL_mfe",
                 "ENTRY_CONTROL_mae"):
        assert result["witness"][name] > 0
    full = excursions.compute(panel, **GEOMETRY)
    assert pit.slice_equivalence(panel, full, 40, GEOMETRY)


# -- split / CA ------------------------------------------------------------------------------------

def _split_from(panel: Panel, row: int, ratio_from: float, ratio_to: float) -> Panel:
    factor = ratio_from / ratio_to
    out = {}
    for name in ("open", "high", "low", "close"):
        array = getattr(panel, name).copy()
        array[row:] = array[row:] * factor
        out[name] = array
    event = SplitEvent("AAA", panel.sessions[row], ratio_from, ratio_to)
    return with_changes(panel, splits=panel.splits + (event,), **out)


@pytest.mark.parametrize("row,frm,to", [(D - 3, 1.0, 2.0), (D + 1, 1.0, 2.0), (D + 3, 1.0, 3.0),
                                        (D + 3, 10.0, 1.0), (D + 5, 1.0, 4.0), (D + 8, 1.0, 2.0)])
def test_split_anywhere_leaves_geometry_on_one_basis(row, frm, to):
    base = geo(hand_panel())
    split = geo(_split_from(hand_panel(), row, frm, to))
    assert split["valid"] and split["status"] == base["status"]
    assert split["mfe"] == pytest.approx(base["mfe"], abs=1e-12)
    assert split["mae"] == pytest.approx(base["mae"], abs=1e-12)
    assert split["up"] == base["up"] and split["down"] == base["down"]


def test_unrecorded_price_jump_inside_ca_window_invalidates():
    jumped = hand_panel()
    for row in range(D + 2, T):
        for field in ("open", "high", "low", "close"):
            jumped = set_cell(jumped, field, row, getattr(jumped, field)[row, 0] * 4.0)
    g = geo(jumped)
    assert g["status"] == STATUS_CODE["LABEL_CA_SUSPECT"] and not g["valid"] and not g["up"]


# -- missing / boundary ------------------------------------------------------------------------------

def test_missing_entry_bar():
    g = geo(drop_bar(hand_panel(), D + 1))
    assert g["status"] == STATUS_CODE["NO_ENTRY_BAR"] and not g["valid"]
    assert np.isnan(g["mfe"]) and np.isnan(g["entry_open"]) and not g["up"] and not g["down"]


def test_missing_middle_session_is_valid_gap_without_imputation():
    panel = set_cell(hand_panel(), "high", D + 6, 500.0)       # D+6 must not fill the hole
    g = geo(drop_bar(panel, D + 3))
    assert g["valid"] and g["status"] == STATUS_CODE["VALID_GAP"] and g["window_bars"] == 4
    assert g["mfe"] == pytest.approx(0.09) and g["mae"] == pytest.approx(-0.04)


def test_missing_horizon_bar_and_its_descriptive_subclass():
    halted = geo(drop_bar(hand_panel(), D + 5))
    assert halted["status"] == STATUS_CODE["MISSING_HORIZON_BAR"] and not halted["valid"]
    assert halted["missing_subclass"] == 1                    # a later bar exists
    gone = hand_panel()
    for row in range(D + 5, T):
        gone = drop_bar(gone, row)
    delisted = geo(gone)
    assert delisted["status"] == STATUS_CODE["MISSING_HORIZON_BAR"]
    assert delisted["missing_subclass"] == 2                  # no later bar


def test_dataset_boundary():
    g = geo(hand_panel(), row=T - 3)
    assert g["status"] == STATUS_CODE["BOUNDARY"] and not g["valid"]


# -- universe as-of --------------------------------------------------------------------------------

def test_universe_keeps_future_invalid_members():
    panel = drop_bar(make_panel(40, tickers=("AAA", "BBB", "CCC")), 26)
    eligible = np.zeros(panel.close.shape, dtype=bool)
    eligible[20:25] = True
    ex = excursions.compute(panel, **GEOMETRY)
    s, j = universe.rows(eligible, 20, 24)
    assert s.size == int(eligible[20:25].sum())               # nothing dropped for its future
    table = universe.per_session(s, ex.status[s, j], 20, 24,
                                 tuple(d.isoformat() for d in panel.sessions))
    counts = table.to_pydict()
    assert counts["eligible_count"] == [3] * 5
    assert all(v + i == 3 for v, i in zip(counts["future_valid_count"], counts["future_invalid_count"]))
    assert not {"up10", "dn10", "mfe_5"} & set(counts)


def test_universe_rows_ignore_geometry():
    eligible = np.zeros((30, 4), dtype=bool)
    eligible[10, [0, 2]] = True
    s, j = universe.rows(eligible, 10, 12)
    assert s.tolist() == [10, 10] and j.tolist() == [0, 2]


# -- determinism -----------------------------------------------------------------------------------

def test_geometry_is_deterministic():
    panel = make_panel(90)
    a, b = excursions.compute(panel, **GEOMETRY), excursions.compute(panel, **GEOMETRY)
    for name in ("entry_open", "mfe", "mae", "up", "down", "valid", "status", "window_bars"):
        assert np.array_equal(getattr(a, name), getattr(b, name), equal_nan=True)


# -- isolation and firewalls -------------------------------------------------------------------------

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
    return out


def test_v2a_package_does_not_know_d_agg():
    v2a = PACKAGE.parent / "strategy_d_v2"
    for path in v2a.rglob("*.py"):
        assert not any("strategy_d_agg" in m for m in _imports(path)), path


def test_v2a_package_and_docs_have_no_working_tree_changes():
    repo = config.REPO_ROOT
    out = subprocess.run(["git", "status", "--porcelain", "--", "backend/app/backtest/strategy_d_v2",
                          "docs/backtest/strategy_d_v2"], cwd=repo, capture_output=True, text=True)
    assert out.stdout.strip() == ""


def test_excursions_see_no_signal_and_no_selection():
    imports = _imports(PACKAGE / "excursions.py")
    assert not any(m.endswith(("analog_signal", "parent", "d3", "d4", "b0_composite")) for m in imports)


def test_d_agg_reads_no_v2a_gate_and_no_v1_resample_constants():
    for path in PACKAGE.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "screening_gate" not in text and "decide(" not in text, path
        assert not re.search(r"resample\.(SEED|BLOCK_LENGTH|REPLICATES)", text), path


def test_no_outcome_aggregate_in_source():
    pattern = re.compile(r"(\b(up|down|up_flag|down_flag)\b|\[\"(up|down|mfe|mae)\"\]|"
                         r"\.(up|down|mfe|mae)\b)[\]\)]*\.(sum|mean|any)\(")
    for name in ("d1.py", "universe.py", "excursions.py", "parent.py"):
        text = (PACKAGE / name).read_text(encoding="utf-8")
        assert not pattern.search(text), name
        assert not re.search(r"np\.(mean|nanmean|median|nanmedian|count_nonzero)\([^)]*(mfe|mae|up|down)",
                             text), name


def test_alpha_firewall_rejects_aggregate_keys():
    assert_no_alpha({"strategy_id": "x", "query_geometry": {"status": {"VALID": 3}}})
    for key in ("up10_rate", "tail_lift", "mean_mfe", "dn10_count", "top10_outcome"):
        with pytest.raises(HardFail):
            assert_no_alpha({"nested": [{key: 1}]})
