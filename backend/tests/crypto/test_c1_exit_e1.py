import numpy as np

from app.crypto.research.c1_exit import e1


def test_episode_starts_counts_false_to_true_only():
    c1 = np.array([True, True, False, True, False, False, True, True, True])
    assert e1.episode_starts(c1).tolist() == [0, 3, 6]
    assert e1.episode_lengths(c1, e1.episode_starts(c1)).tolist() == [2, 1, 3]


def test_episode_starts_all_false_and_trailing_true():
    assert e1.episode_starts(np.zeros(5, bool)).tolist() == []
    c1 = np.array([False, False, True])
    assert e1.episode_starts(c1).tolist() == [2]
    assert e1.episode_lengths(c1, e1.episode_starts(c1)).tolist() == [1]


def test_gate_boundaries():
    assert e1.gate({"lo": -1.0, "hi": 0.0}) == ("E1_NEGATIVE", "NO")
    assert e1.gate({"lo": -1.0, "hi": 0.1}) == ("E1_INCONCLUSIVE", "EXPLORATORY_ONLY")
    assert e1.gate({"lo": 0.0, "hi": 3.0}) == ("E1_INCONCLUSIVE", "EXPLORATORY_ONLY")
    assert e1.gate({"lo": 0.1, "hi": 3.0}) == ("E1_POSITIVE_BASELINE", "YES")


def test_top_removal_uses_largest_net():
    net = np.array([100.0, -5.0, -5.0, 1.0] + [0.0] * 96)
    out = e1.top_removal(net)
    assert out["top1"]["removed"] == 1 and out["top1"]["sign"] == "-"
    assert out["top1pct"]["removed"] == 1
    assert out["top5pct"]["removed"] == 5


def test_cluster_bootstrap_is_deterministic():
    ts = np.arange(40, dtype=np.int64) * e1.DAY_MS // 3
    net = np.linspace(-10, 12, 40)
    a = e1.day_cluster_ci(ts, net, 1)
    b = e1.day_cluster_ci(ts, net, 1)
    assert a == b and a["lo"] <= a["mean"] <= a["hi"]
