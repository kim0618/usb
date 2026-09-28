import json
import pytest
from app.backtest.strategy_h0.h_pv2c import PV2_DECISIONS,assert_no_leakage,confirmation_verdict,select_confirmation
from app.backtest.strategy_h0.h_pv2 import growth_rate

def row(i,exchange='XNYS',cik=None,figi=None):return {'ticker':f'T{i}','cik':cik or f'{i:010}','share_class_figi':figi or f'F{i}','type':'CS','market':'stocks','locale':'us','primary_exchange':exchange}
def test_development_cik_and_figi_leakage_rejected():
 with pytest.raises(ValueError):assert_no_leakage([row(1)],{'0000000001'},set())
 with pytest.raises(ValueError):assert_no_leakage([row(1)],set(),{'F1'})
def test_selection_is_deterministic_disjoint_and_quota_fixed():
 rows=[row(i,'XNYS') for i in range(100)]+[row(100+i,'XNAS') for i in range(100)]+[row(200+i,'XASE') for i in range(10)]
 counts={r['ticker']:501 for r in rows};dec={r['ticker'] for r in rows}
 a=select_confirmation(rows,counts,dec,{'0000000001'},{'F2'});b=select_confirmation(list(reversed(rows)),counts,dec,{'0000000001'},{'F2'})
 assert [r['ticker'] for r in a]==[r['ticker'] for r in b];assert len(a)==120;assert all(r['cik'] not in {'0000000001'} for r in a)
def test_confirmation_sample_rejects_duplicate_cik():
 with pytest.raises(ValueError):assert_no_leakage([row(1),row(2,cik='0000000001')],set(),set())
def test_pv2_growth_formula_equality():assert growth_rate(120,100)[0]==pytest.approx(.2) and growth_rate(120,100)[1]=='OK'
def test_pv2_decision_dates_exact():assert len(PV2_DECISIONS)==20 and PV2_DECISIONS[0]=='2024-10-31' and PV2_DECISIONS[-1]=='2026-05-29'
def test_gate_aggregation_and_concentration_failure():
 g={f'C{i}':'PASS' for i in range(1,9)};assert confirmation_verdict(g)=='CONFIRMED';g['C6']='FAIL';assert confirmation_verdict(g)=='INCONCLUSIVE'
def test_contract_equality_for_cost_and_growth(tmp_path):
 pv2=json.load(open('docs/backtest/strategy_h_candidate/h_pv2_2y_growth_contract_v1.json'));c=json.load(open('docs/backtest/strategy_h_candidate/h_pv2c_2y_growth_confirmation_contract_v1.json'))
 assert {k:pv2['cost_bp'][k] for k in ('base','stress')}==c['cost_bp'];assert set(pv2['features'])==set(c['features']);assert c['growth_contract'].endswith('17464dd446601d960863c79692efb133f659355263d0ee591f9745f304040eeb')
