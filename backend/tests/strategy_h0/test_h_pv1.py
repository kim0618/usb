from datetime import date,datetime,timezone
import pytest
from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h_pv1 import ValueInputs,aligned_excess,annual_fact,apply_cost,assert_unique_security_dates,assign_deciles,forward_label,percentile_scores,positive_concentration,raw_features,remove_top_observations,spearman
UTC=timezone.utc
def fact(**kw):
 d=dict(field='net_income',taxonomy='us-gaap',tag='NetIncomeLoss',unit='USD',value=10,start=date(2023,1,1),end=date(2023,12,31),filed=date(2024,2,1),accepted_at=datetime(2024,2,1,tzinfo=UTC),accession='a',form='10-K',fiscal_year=2023,fiscal_period='FY',frame=None);d.update(kw);return CanonicalFact(**d)
def test_pit_cutoff_rejects_future_and_versions_amendment():
 old=fact(); future=fact(value=12,accepted_at=datetime(2024,5,1,tzinfo=UTC),accession='b',form='10-K/A')
 assert annual_fact([old,future],'net_income',datetime(2024,3,1,tzinfo=UTC),date(2024,3,1)).value==10
 assert annual_fact([old,future],'net_income',datetime(2024,6,1,tzinfo=UTC),date(2024,6,1)).value==12
def test_invalid_denominators_and_negative_values_are_missing():
 assert all(v is None for v in raw_features(ValueInputs(0,1,2,1,3)).values())
 x=raw_features(ValueInputs(10,-1,1,2,5)); assert x['earnings_yield'] is None and x['fcf_yield'] is None and x['sales_yield']==.5
def test_rank_and_deciles_are_deterministic():
 rows=[{'security_id':f's{i:02}','earnings_yield':i,'fcf_yield':i,'sales_yield':i} for i in range(20)]
 ranked=percentile_scores(rows); dec=assign_deciles(list(reversed(ranked)))
 assert [r['security_id'] for r in dec if r['decile']==10]==['s18','s19']
def test_missing_values_require_two_features():
 row=percentile_scores([{'security_id':'a','earnings_yield':1,'fcf_yield':None,'sales_yield':None}])[0]
 assert row['value_score'] is None
def test_forward_label_truncates_and_never_uses_last_available_substitute():
 assert forward_label([10,11,12],0,2)==pytest.approx(.2)
 assert forward_label([10,11,12],1,2) is None
 assert forward_label([10,None,12],1,1) is None
def test_cost_once_and_long_short_twice():
 assert apply_cost(.01,10)==pytest.approx(.009)
 assert apply_cost(.01,10,2)==pytest.approx(.008)
def test_spearman_and_contribution():
 assert spearman([1,2,3],[3,2,1])==pytest.approx(-1)
 c=positive_concentration([{'security_id':'a','net_excess_63':2},{'security_id':'b','net_excess_63':1},{'security_id':'c','net_excess_63':-5}])
 assert c['single']==pytest.approx(2/3)


def test_duplicate_security_date_rejected():
    rows=[{'decision_date':'2025-01-31','security_id':'x'}]*2
    with pytest.raises(ValueError,match='duplicate security/date'):
        assert_unique_security_dates(rows)

def test_benchmark_uses_identical_start_and_endpoint():
    assert aligned_excess([10,11,12],[20,20,22],0,2)==pytest.approx(.1)
    assert aligned_excess([10,11,12],[20,20,None],0,2) is None

def test_extreme_removal_is_deterministic_and_non_mutating():
    rows=[{'security_id':'a','x':3},{'security_id':'b','x':1},{'security_id':'c','x':2}]
    assert [r['security_id'] for r in remove_top_observations(rows,'x',2)]==['b']
    assert len(rows)==3
