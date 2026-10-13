import numpy as np

from app.crypto.research.r0_regime import _funding_numerator, _prior_30d_q75, classify, forward


def _grid(days: int = 32) -> dict[str, np.ndarray]:
    n = days * 1440
    close = 100 * np.exp(np.arange(n) * 1e-6)
    return {"ts": np.arange(n, dtype=np.int64) * 60_000,
            "open": close.copy(), "close": close,
            "mark_close": close * 1.0001, "index_close": close,
            "oi": 1000 * np.exp(np.arange(n) * 1e-7),
            "funding_last": np.full(n, 0.0001),
            "funding_ts": np.array([], dtype=np.int64),
            "funding_rate": np.array([], dtype=float)}


def test_prior_quantile_never_uses_current_day():
    x = np.ones(32 * 1440)
    x[30 * 1440:31 * 1440] = 100
    q = _prior_30d_q75(x)
    assert q[30 * 1440] == 1
    assert q[31 * 1440] == 1


def test_classification_is_exhaustive_only_after_eligibility():
    regime, features = classify(_grid())
    assert np.all(regime[features["eligible"]] != "UNCLASSIFIED")
    assert np.all(regime[~features["eligible"]] == "UNCLASSIFIED")
    assert set(np.unique(regime)) <= {"R1", "R2", "R3", "R4", "R5", "UNCLASSIFIED"}


def test_forward_long_short_and_cost_identity_without_funding():
    g = _grid()
    cumulative = np.zeros(len(g["ts"]) + 1)
    out = forward(g, 30, cumulative)
    i = 100
    ratio = g["open"][i + 31] / g["open"][i + 1]
    assert np.isclose(out["long_gross"][i], ratio - 1)
    assert np.isclose(out["short_gross"][i], 1 / ratio - 1)
    assert np.isclose(out["edge"][i], out["long_gross"][i] - out["short_gross"][i])


def test_funding_interval_includes_entry_and_excludes_exit():
    g = _grid()
    g["funding_ts"] = np.array([g["ts"][1], g["ts"][31]])
    g["funding_rate"] = np.array([0.001, 0.5])
    cumulative = _funding_numerator(g)
    out = forward(g, 30, cumulative)
    # Decision row 0 enters at index 1 and exits at index 31. Only the first settlement applies.
    ratio = g["open"][31] / g["open"][1]
    no_funding_long = ratio - 1 - 0.00055 * (1 + ratio) - 0.00000118
    expected_funding = 0.001 * g["mark_close"][1] / g["open"][1]
    assert np.isclose(out["long_net"][0], no_funding_long - expected_funding)
