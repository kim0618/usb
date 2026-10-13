import numpy as np

from app.crypto.research.c1_regime_r1 import metrics, top_removal, verdict


def test_metrics_and_top_removal_are_trade_weighted():
    x = np.array([-2.0, 1.0, 4.0, 7.0])
    assert metrics(x)["net_mean_bp"] == 2.5
    assert metrics(x)["pf"] == 6.0
    assert top_removal(x)["top1"]["net_mean_bp"] == 1.0


def test_high_mean_cannot_improve_with_low_retention():
    base = {"net_mean_bp": 1.0, "net_median_bp": 0.0, "pf": 1.1, "win_rate": .5,
            "positive_folds": 5, "positive_years": 3,
            "top_removal": {"top1": {"net_mean_bp": 0.0}}}
    row = {**base, "net_mean_bp": 10.0, "retention_pct": 40.0,
           "paired_delta_clustered_95_ci_bp": [1.0, 2.0],
           "excluded": {"total_opportunity_loss_bp": -5.0,
                        "baseline_large_winner_removal_pct": 40.0, "overall_exclusion_pct": 60.0}}
    status, reasons = verdict(row, base)
    assert status == "INCONCLUSIVE"
    assert "retention_at_least_60pct" in reasons


def test_positive_opportunity_loss_is_worse():
    base = {"net_mean_bp": 1.0}
    row = {"net_mean_bp": 2.0, "paired_delta_clustered_95_ci_bp": [-2.0, 2.0],
           "excluded": {"total_opportunity_loss_bp": 3.0}}
    assert verdict(row, base)[0] == "WORSE_THAN_BASELINE"
