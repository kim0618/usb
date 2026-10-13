import numpy as np

from app.crypto.research.c1_exit.lifecycle import _recent_extreme, classify


def test_three_states_are_semantic_and_sparse():
    assert classify(above=True, premium_normalized=False, oi_recovered=False, new_high=True, new_low=False) == "HOLD"
    assert classify(above=False, premium_normalized=True, oi_recovered=False, new_high=False, new_low=False) == "EXIT_WATCH"
    assert classify(above=False, premium_normalized=True, oi_recovered=True, new_high=False, new_low=True) == "THESIS_BROKEN"
    assert classify(above=True, premium_normalized=True, oi_recovered=True, new_high=False, new_low=False) == "UNCLASSIFIED"


def test_recent_extreme_uses_only_observed_prefix():
    values = np.array([10., 11., 10., 9., 10., 12., 11.])
    assert _recent_extreme(values, 7, high=True)
    assert _recent_extreme(values, 5, high=False)
