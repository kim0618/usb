"""H-V2 Hierarchical Industry Intake R0 (offline research): the pure functions behave as preregistered."""

from datetime import date

from app.backtest.strategy_h_v2 import industry_intake_r0 as R


def test_industry_of_uses_the_sic_code_and_rejects_missing():
    assert R.industry_of("3826") == "3826" and R.industry_of(100) == "0100"
    assert R.industry_of(None) is None and R.industry_of("") is None and R.industry_of("0") is None


def test_representatives_are_top3_by_dollar_volume_ties_by_ticker():
    m = {"A": 5.0, "B": 9.0, "C": 9.0, "D": 1.0, "E": None}
    assert R.representatives(m) == ["B", "C", "A"]
    assert R.representatives({"X": 1.0}) == ["X"]


def test_window_return_and_basket_rs_need_both_closes():
    days = [date(2026, 1, d) for d in range(1, 30)]
    series = {d: 100.0 + i for i, d in enumerate(days)}
    assert R.window_return(series, days, days[21]) == series[days[21]] / series[days[0]] - 1
    assert R.window_return(series, days, days[5]) is None
    assert R.basket_rs(["A"], {"A": series}, days, days[21], spy=0.0) == R.window_return(series, days, days[21])
    assert R.basket_rs(["A"], {}, days, days[21], spy=0.0) is None


def test_radar_variants():
    rs = {"a": 0.05, "b": -0.01, "c": 0.02, "d": None}
    assert R.radar_states(rs, "RS_SIGN") == {"a": "ACTIVE", "b": "DORMANT", "c": "ACTIVE", "d": "UNKNOWN"}
    assert R.radar_states(rs, "RANK_TOP50") == {"a": "ACTIVE", "c": "ACTIVE", "b": "DORMANT", "d": "UNKNOWN"}


def test_rotation_covers_every_industry_once_per_cycle():
    inds = [f"{i:04d}" for i in range(23)]
    for n in (5, 10):
        seen = [R.rotation_industries(inds, i, n) for i in range(n)]
        assert set().union(*seen) == set(inds) and sum(len(s) for s in seen) == len(inds)
    assert R.rotation_industries(inds, 3, None) == set()


def test_deep_scan_set_is_active_or_rotation_or_bypass():
    ind = {"A": "1", "B": "1", "C": "2", "D": None, "E": "3"}
    out = R.deep_scan_set(pool=["A", "B", "C", "D", "E"], industry=ind, states={"1": "ACTIVE", "2": "DORMANT", "3": "DORMANT"},
                          rotation={"3"}, bypass={"D": True})
    assert out == {"A", "B", "D", "E"}


def test_recent_is_e3_window():
    assert R.recent(14) and not R.recent(15) and not R.recent(None)
