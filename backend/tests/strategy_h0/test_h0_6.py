from app.backtest.strategy_h0.h0_6 import H06Metrics, H06Verdict, verdict


def complete(**overrides):
    values = dict(entitlement_years_ok=10, dated_snapshots_ok=9,
                  later_inactive_members=2, identity_stable=True,
                  daily_completeness=0.98, pit_shares_coverage=0.70,
                  market_cap_coverage=0.60, actions_consistent=True)
    values.update(overrides)
    return H06Metrics(**values)


def test_active_credential_that_fails_annual_probes_fails_paid_audit():
    assert verdict(H06Metrics(entitlement_years_ok=2)) is H06Verdict.FAIL


def test_unmeasured_downstream_coverage_is_inconclusive_after_entitlement_passes():
    assert verdict(H06Metrics(entitlement_years_ok=10)) is H06Verdict.INCONCLUSIVE


def test_exact_frozen_thresholds_pass():
    assert verdict(complete()) is H06Verdict.PASS


def test_each_numeric_threshold_fails_closed():
    assert verdict(complete(daily_completeness=0.9799)) is H06Verdict.INCONCLUSIVE
    assert verdict(complete(pit_shares_coverage=0.6999)) is H06Verdict.INCONCLUSIVE
    assert verdict(complete(market_cap_coverage=0.5999)) is H06Verdict.INCONCLUSIVE


def test_missing_inactive_identity_actions_or_unknown_policy_fails_closed():
    assert verdict(complete(later_inactive_members=1)) is H06Verdict.INCONCLUSIVE
    assert verdict(complete(identity_stable=False)) is H06Verdict.INCONCLUSIVE
    assert verdict(complete(actions_consistent=False)) is H06Verdict.INCONCLUSIVE
    assert verdict(complete(unknown_policy_deterministic=False)) is H06Verdict.INCONCLUSIVE
