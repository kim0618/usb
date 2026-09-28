from datetime import date,datetime,timezone
import pytest
from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h_pv1 import assign_deciles,positive_concentration,spearman
from app.backtest.strategy_h0.h_pv3 import *

D=date(2024,3,31);S=date(2024,1,1);T=datetime(2024,4,30,tzinfo=timezone.utc)
def fact(field,value,start=S,end=D,fp='Q1',tag=None):
 tags={'revenue':'Revenues','operating_income':'OperatingIncomeLoss','operating_cash_flow':'NetCashProvidedByUsedInOperatingActivities','capex':'PaymentsToAcquirePropertyPlantAndEquipment','net_income':'NetIncomeLoss','assets':'Assets','cash':'CashAndCashEquivalentsAtCarryingValue'}
 return CanonicalFact(field,'us-gaap',tag or tags[field],'USD',value,start,end,date(2024,4,20),T,'a','10-Q',2024,fp,None)
def test_margins_and_period_matching():
 fs=[fact('revenue',100),fact('operating_income',20),fact('operating_cash_flow',30),fact('capex',5)]
 assert operating_margin(fs,T,D)[0]==pytest.approx(.2);assert fcf_margin(fs,T,D)[0]==pytest.approx(.25)
 assert fcf_margin(fs+[fact('revenue',100,date(2023,10,1))],T,D)[0]==pytest.approx(.25)
def test_cash_conversion_invalid_denominator_and_negative_ocf():
 assert cash_conversion([fact('net_income',1_000_000),fact('operating_cash_flow',2)],T,D)[0] is None
 assert cash_conversion([fact('net_income',2_000_000),fact('operating_cash_flow',-1_000_000)],T,D)[0]==pytest.approx(-.5)
def test_roa_average_balance_and_denominator():
 fs=[fact('net_income',10),fact('assets',100,None,S-timedelta(days=1)),fact('assets',300,None,D)]
 assert roa(fs,T,D)[0]==pytest.approx(10*(365/90)/200)
 fs[-1]=fact('assets',-300,None,D);assert roa(fs,T,D)[0] is None
def test_cash_assets_formula_and_exact_instant():
 assert cash_assets([fact('assets',200,None,D),fact('cash',50,None,D)],T,D)[0]==pytest.approx(.25)
 assert cash_assets([fact('assets',200,None,D),fact('cash',50,None,D-timedelta(days=1))],T,D)[0] is None
def test_quality_gate_winsor_rank_composite_and_minimum():
 rows=[]
 for d in ('a','b'):
  rows += [{'decision_date':d,'security_id':str(i),'operating_margin':float(i),'fcf_margin':float(i),'roa':None,'cash_conversion':None,'cash_assets':None} for i in range(10)]
 included,report=feature_quality(rows,['a','b']);assert included==['operating_margin','fcf_margin'];assert report['roa']['passed'] is False
 scored=quality_scores(rows[:10],included);assert scored[0]['quality_score']==0 and scored[-1]['quality_score']==1
 assert quality_scores([{'operating_margin':1,'fcf_margin':None}],included)[0]['quality_score'] is None
def test_decile_ic_monotonicity_concentration_and_gate_aggregation():
 rows=[{'value_score':i,'security_id':str(i),'net_excess_63':i} for i in range(20)];assert len(assign_deciles(rows))==20;assert spearman(range(10),range(10))==pytest.approx(1)
 assert positive_concentration(rows)['single']<.35
 gates={f'P{i}':'PASS' for i in range(1,9)};assert verdict(gates)=='PROMISING';gates['P1']=gates['P2']=gates['P3']='FAIL';assert verdict(gates)=='UNPROMISING'
