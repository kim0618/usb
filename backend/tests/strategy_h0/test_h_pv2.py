from datetime import date,datetime,timezone
import pytest
from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h_pv2 import comparable_pair,fcf_pair,growth_rate,growth_rows_scores,period_family,quality_features
UTC=timezone.utc
def fact(**kw):
 d=dict(field='revenue',taxonomy='us-gaap',tag='Revenues',unit='USD',value=120,start=date(2024,1,1),end=date(2024,3,31),filed=date(2024,4,20),accepted_at=datetime(2024,4,20,tzinfo=UTC),accession='cur',form='10-Q',fiscal_year=2024,fiscal_period='Q1',frame=None);d.update(kw);return CanonicalFact(**d)
def test_comparable_quarter_and_growth():
 prior=fact(value=100,start=date(2023,1,1),end=date(2023,3,31),accepted_at=datetime(2023,4,20,tzinfo=UTC),accession='prior')
 pair=comparable_pair([prior,fact()],'revenue',datetime(2024,5,1,tzinfo=UTC),date(2024,5,1));assert pair.family=='Q1';assert growth_rate(pair.current.value,pair.prior.value)[0]==pytest.approx(.2)
def test_duration_mismatch_and_annual_quarter_contamination_rejected():
 bad=fact(start=date(2023,1,1),end=date(2023,6,30),fiscal_period='Q2')
 assert comparable_pair([bad,fact(fiscal_period='Q2')],'revenue',datetime(2024,5,1,tzinfo=UTC),date(2024,5,1)) is None
 assert period_family(fact(form='10-K',fiscal_period='FY')) is None
def test_future_filing_rejected():
 prior=fact(start=date(2023,1,1),end=date(2023,3,31),accepted_at=datetime(2023,4,1,tzinfo=UTC))
 assert comparable_pair([prior,fact(accepted_at=datetime(2024,6,1,tzinfo=UTC))],'revenue',datetime(2024,5,1,tzinfo=UTC),date(2024,5,1)) is None
def test_negative_and_transition_handling():
 assert growth_rate(1,-1)==(None,'LOSS_TO_PROFIT');assert growth_rate(-1,1)==(None,'PROFIT_TO_LOSS');assert growth_rate(.1,.04,eps=True)[0] is None
def test_ytd_fcf_exact_period_matching():
 rows=[]
 for field,cur,old in [('operating_cash_flow',120,100),('capex',20,20)]:
  tag='NetCashProvidedByUsedInOperatingActivities' if field=='operating_cash_flow' else 'PaymentsToAcquirePropertyPlantAndEquipment'
  rows += [fact(field=field,tag=tag,value=cur,start=date(2024,1,1),end=date(2024,6,30),fiscal_period='Q2'),fact(field=field,tag=tag,value=old,start=date(2023,1,1),end=date(2023,6,30),fiscal_period='Q2',accepted_at=datetime(2023,7,1,tzinfo=UTC),accession='old')]
 value,status,_=fcf_pair(rows,datetime(2024,8,1,tzinfo=UTC),date(2024,8,1));assert status=='OK';assert value==pytest.approx(.25)
def test_quality_gate_and_composite_minimum_two():
 rows=[{'decision_date':'d','security_id':str(i),'a':i,'b':i,'c':None} for i in range(20)]
 features,report=quality_features(rows,['d'],['a','b','c']);assert features==['a','b'];assert not report['c']['passed']
 scored=growth_rows_scores(rows,features);assert all(r['growth_score'] is not None for r in scored)
def test_winsor_rank_determinism():
 rows=[{'decision_date':'d','security_id':str(i),'a':x,'b':x} for i,x in enumerate([1,2,3,1000])]
 assert growth_rows_scores(rows,['a','b'])==growth_rows_scores(list(reversed(rows)),['a','b'])[::-1]
