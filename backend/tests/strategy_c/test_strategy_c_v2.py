"""C-V2A/C-V2B contracts: declared checksum, hand-computed forward measures, classification order."""

import json

import numpy as np
import pytest

from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_v2 import analyze
from app.backtest.strategy_c_v2.measures import compute_measures
from app.backtest.strategy_c_v2.rules import V2_RULES_PATH, V2RulesChanged, load_v2_rules
from tests.strategy_c.test_strategy_c_selection_pit import D, make_panel


def test_v2_rules_checksum_is_enforced(tmp_path):
    raw, _ = load_v2_rules()
    assert raw["baseline"]["rules_checksum"].startswith("c769aea5")
    changed = json.loads(V2_RULES_PATH.read_text(encoding="utf-8"))
    changed["c_v2a_short_horizon"]["supported_rule"]["eligible_horizons"] = [1, 2, 3, 4, 5, 7]
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(V2RulesChanged):
        load_v2_rules(path)


def test_forward_measures_match_hand_calculation():
    panel = make_panel()
    j = panel.column("MOM")
    labels = compute_labels(panel, (1, 3, 5), 3.0)
    atr = np.full(panel.shape, 0.02)
    m = compute_measures(panel, labels, atr, (3,), mfe_horizon=5)
    o, h, lo, c = panel.open[:, j], panel.high[:, j], panel.low[:, j], panel.close[:, j]
    p0 = o[D + 1]
    window = slice(D + 1, D + 4)
    assert m["range_3"][D, j] == pytest.approx((h[window].max() - lo[window].min()) / p0)
    log_returns = np.log(np.r_[c[D + 1] / p0, c[D + 2] / c[D + 1], c[D + 3] / c[D + 2]])
    assert m["realized_vol_3"][D, j] == pytest.approx(np.std(log_returns, ddof=1))
    prev = np.r_[c[D], c[D + 1], c[D + 2]]
    tr = np.fmax(h[window], prev) - np.fmin(lo[window], prev)
    assert m["atr_expansion_3"][D, j] == pytest.approx(tr.mean() / p0 / 0.02)
    assert m["log_asym_3"][D, j] == pytest.approx(np.log(h[window].max() / p0) + np.log(lo[window].min() / p0))
    assert m["overnight_gap"][D, j] == pytest.approx(p0 / c[D] - 1)
    t_mfe = int(labels.time_to_mfe[5][D, j])
    peak = h[D + 1:D + 6].max()
    assert m["post_mfe_retrace_1"][D, j] == pytest.approx(c[D + t_mfe + 1] / peak - 1)


def test_realized_vol_needs_every_bar():
    panel = make_panel()
    labels = compute_labels(panel, (5,), 3.0)
    m = compute_measures(panel, labels, np.full(panel.shape, 0.02), (5,), mfe_horizon=5)
    assert np.isnan(m["realized_vol_5"][D, panel.column("DEL")])  # DEL has no bars after D+2


@pytest.mark.parametrize("signs,label", [
    ({"log_up_10": "zero", "log_down_10": "zero", "log_asym_10": "zero", "range_10": "zero", "close_10": "zero"},
     "NEITHER"),
    ({"log_up_10": "pos", "log_down_10": "pos", "log_asym_10": "pos", "range_10": "pos", "close_10": "zero"},
     "BOTH"),
    ({"log_up_10": "pos", "log_down_10": "zero", "log_asym_10": "pos", "range_10": "zero", "close_10": "zero"},
     "DIRECTIONAL"),
    ({"log_up_10": "pos", "log_down_10": "pos", "log_asym_10": "zero", "range_10": "pos", "close_10": "zero"},
     "VOLATILITY"),
])
def test_classification_order(signs, label):
    assert analyze.classify(signs) == label
    assert analyze.overall([label, label, "MIXED"]) == label
