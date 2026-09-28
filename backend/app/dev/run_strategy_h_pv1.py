"""Execute frozen H-PV1 against local daily and existing SEC raw stores."""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
import gzip,hashlib,json,math
from pathlib import Path
import numpy as np
from app.backtest.strategy_h0.facts import extract_companyfacts
from app.backtest.strategy_h0.h0_5 import historical_market_cap,market_cap_lane,resolve_pit_shares
from app.backtest.strategy_h0.h_pv1 import ValueInputs,annual_fact,assign_deciles,percentile_scores,positive_concentration,raw_features,spearman
from app.backtest.strategy_h0.pilot import acceptance_index,read_gzip_json
from app.backtest.strategy_eqm_v0.xbrl_store import read_facts
from app.market.calendar import MarketCalendar

DAILY=Path('data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily')
REFERENCE=Path('data/runtime/research_universe_u1/reference/tickers/CS_2024-10-25.json.gz')
FACTS=Path('data/runtime/strategy_h/h0/raw')
SUBMISSIONS=Path('data/runtime/strategy_c/e0/raw/submissions')
SPLITS=Path('data/runtime/strategy_b_e0/mirror/market_data/raw/massive/splits/splits_2024-09-16_2026-09-16.json.gz')
OUTPUT=Path('data/runtime/strategy_h/h_pv1')

def load_json_gz(p):return json.loads(gzip.decompress(p.read_bytes()))
def sample_rows():
 d=load_json_gz(REFERENCE); ciks={p.name[3:13] for p in (FACTS/'companyfacts').glob('CIK*.json.gz')}
 return sorted([r for p in d['pages'] for r in p.get('results',[]) if r.get('type')=='CS' and r.get('market')=='stocks' and r.get('locale')=='us' and r.get('primary_exchange') in {'XNYS','XNAS','XASE'} and r.get('cik') in ciks and (r.get('share_class_figi') or r.get('composite_figi'))],key=lambda r:(r['ticker'],r['cik']))
def load_prices(symbols):
 out={s:{} for s in symbols}
 quality={'files':0,'rows':0,'duplicates':0,'invalid':0}
 for p in sorted(DAILY.rglob('*.json.gz')):
  d=load_json_gz(p)
  if 'body' not in d or 'session' not in d:continue
  day=date.fromisoformat(d['session']); quality['files']+=1; seen=set()
  for r in d['body'].get('results',[]):
   quality['rows']+=1; t=r.get('T')
   if t in seen:quality['duplicates']+=1
   seen.add(t)
   if t in out:
    vals=[r.get(k) for k in ('o','h','l','c','v')]
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in vals) or r['h']<max(r['o'],r['l'],r['c']) or r['l']>min(r['o'],r['h'],r['c']):quality['invalid']+=1
    else:out[t][day]=float(r['c'])
 return out,quality
def month_ends(sessions):
 by={}
 for d in sessions:
  if d>=date(2024,10,1):by[(d.year,d.month)]=d
 return sorted(by.values())
def summarize(rows):
 def mean(field,subset):
  v=[r[field] for r in subset if r.get(field) is not None];return sum(v)/len(v) if v else None
 d10=[r for r in rows if r['decile']==10]; d1=[r for r in rows if r['decile']==1]
 a=mean('excess_63',d10); b=mean('excess_63',d1)
 return {'rows':len(rows),'d10_n':len(d10),'d1_n':len(d1),'d10_gross_excess':a,'d1_gross_excess':b,
         'd10_net_excess_10bp':None if a is None else a-.001,
         'd10_d1_net_spread_10bp':None if a is None or b is None else a-b-.002}
def main():
 refs=sample_rows(); by_cik=defaultdict(list)
 for r in refs:by_cik[r['cik']].append(r)
 multi={c for c,rs in by_cik.items() if len(rs)>1}; primary=[r for r in refs if r['cik'] not in multi]
 symbols={r['ticker'] for r in refs}|{'SPY'}; prices,quality=load_prices(symbols)
 sessions=sorted(prices['SPY']); index={d:i for i,d in enumerate(sessions)}; opens=[MarketCalendar().regular_market_open(d) for d in sessions]; opens=[x for x in opens if x]
 split_doc=load_json_gz(SPLITS); split_dates=defaultdict(list)
 for r in split_doc['results']:split_dates[r['ticker']].append(date.fromisoformat(r['execution_date']))
 facts_by_cik={}
 for cik in by_cik:
  sub=SUBMISSIONS/f'CIK{cik}'/f'CIK{cik}.json.gz'; doc=read_facts(FACTS,cik)
  facts_by_cik[cik]=extract_companyfacts(doc,acceptance_index(read_gzip_json(sub))) if doc and sub.exists() else []
 decisions=[d for d in month_ends(sessions) if d>=date(2024,10,31) and index[d]+63<len(sessions)]
 raw=[]; feature_counts=Counter(); shares_reasons=Counter()
 for d in decisions:
  cutoff=MarketCalendar().regular_market_open(d).astimezone(timezone.utc)
  dayrows=[]
  for ref in primary:
   ticker=ref['ticker']; cik=ref['cik']; px=prices[ticker].get(d); fs=facts_by_cik[cik]
   sr=resolve_pit_shares(fs,d,opens,split_dates=split_dates[ticker]); shares_reasons[sr.reason]+=1
   cap=historical_market_cap(px,sr)
   vals={f:annual_fact(fs,f,cutoff,d) for f in ('net_income','operating_cash_flow','capex','revenue')}
   features=raw_features(ValueInputs(cap,*[vals[f].value if vals[f] else None for f in ('net_income','operating_cash_flow','capex','revenue')]))
   for k,v in features.items():feature_counts[k]+=v is not None
   row={'decision_date':d.isoformat(),'security_id':ref.get('share_class_figi') or ref['composite_figi'],'ticker':ticker,'cik':cik,'close':px,'market_cap':cap,'lane':market_cap_lane(cap).value,**features}
   dayrows.append(row)
  raw.extend(percentile_scores(dayrows))
 # labels and date-level deciles
 evaluated=[]; monthly=[]
 for d in decisions:
  rows=[r for r in raw if r['decision_date']==d.isoformat()]; i=index[d]; spy63=prices['SPY'].get(sessions[i+63])/prices['SPY'][d]-1; spy21=prices['SPY'].get(sessions[i+21])/prices['SPY'][d]-1
  valid=[]
  for r in rows:
   p0=prices[r['ticker']].get(d); p63=prices[r['ticker']].get(sessions[i+63]); p21=prices[r['ticker']].get(sessions[i+21])
   if r['value_score'] is None or p0 is None or p63 is None or p0<=0:continue
   r['return_63']=p63/p0-1;r['excess_63']=r['return_63']-spy63;r['net_excess_63']=r['excess_63']-.001
   r['return_21']=p21/p0-1 if p21 is not None else None;r['excess_21']=None if r['return_21'] is None else r['return_21']-spy21
   valid.append(r)
  dec=assign_deciles(valid); evaluated.extend(dec)
  ic=spearman([r['value_score'] for r in valid],[r['excess_63'] for r in valid]) if len(valid)>=2 else None
  monthly.append({'decision_date':d.isoformat(),'eligible':len(rows),'scored_labeled':len(valid),'deciled':len(dec),'ic':ic,**summarize(dec)})
 base=summarize(evaluated); ics=[m['ic'] for m in monthly if m['ic'] is not None]
 # Required descriptive statistics; none changes a gate or sample.
 def descriptive(rs,field):
  vs=np.asarray([r[field] for r in rs if r.get(field) is not None],dtype=float)
  return {'n':len(vs),'mean':float(np.mean(vs)) if len(vs) else None,'median':float(np.median(vs)) if len(vs) else None,'std':float(np.std(vs,ddof=1)) if len(vs)>1 else None,'hit_rate':float(np.mean(vs>0)) if len(vs) else None}
 d10rows=[r for r in evaluated if r['decile']==10]; d1rows=[r for r in evaluated if r['decile']==1]
 descriptions={'all_3m_excess':descriptive(evaluated,'excess_63'),'d10_3m_excess':descriptive(d10rows,'excess_63'),'d1_3m_excess':descriptive(d1rows,'excess_63'),'all_1m_excess':descriptive(evaluated,'excess_21')}
 rng=np.random.default_rng(20260927); ids=sorted({r['security_id'] for r in d10rows}); boot=[]
 for _ in range(10000):
  draw=rng.choice(ids,size=len(ids),replace=True); vals=[]
  for sid in draw:vals.extend(r['net_excess_63'] for r in d10rows if r['security_id']==sid)
  if vals:boot.append(float(np.mean(vals)))
 cluster_ci=[float(x) for x in np.quantile(boot,[.025,.975])] if boot else [None,None]
 base['d10_net_excess_20bp']=None if base['d10_gross_excess'] is None else base['d10_gross_excess']-.002
 base['d10_d1_net_spread_20bp']=None if base['d10_gross_excess'] is None or base['d1_gross_excess'] is None else base['d10_gross_excess']-base['d1_gross_excess']-.004
 decmeans={d:np.mean([r['excess_63'] for r in evaluated if r['decile']==d]) for d in range(1,11) if any(r['decile']==d for r in evaluated)}
 mono=spearman(list(decmeans),list(decmeans.values())) if len(decmeans)==10 else None
 # top groups
 top={}
 for frac,name in ((.10,'top10pct'),(.20,'top20pct')):
  selected=[]
  for day in decisions:
   rs=sorted([r for r in evaluated if r['decision_date']==day.isoformat()],key=lambda r:(r['value_score'],r['security_id']),reverse=True); selected+=rs[:max(1,math.ceil(len(rs)*frac))]
  top[name]={'n':len(selected),'mean_net_excess_63':float(np.mean([r['net_excess_63'] for r in selected])) if selected else None}
 # robustness by fixed exclusions
 ordered=sorted(evaluated,key=lambda r:r['excess_63'],reverse=True); n1=max(1,math.ceil(len(ordered)*.01))
 contribution=defaultdict(float)
 for r in evaluated:contribution[r['security_id']]+=max(r['net_excess_63'],0)
 topsecs={k for k,_ in sorted(contribution.items(),key=lambda kv:kv[1],reverse=True)[:10]}
 robustness={'remove_top_1pct':summarize(ordered[n1:]),'remove_top_5':summarize(ordered[5:]),'remove_top_10_securities':summarize([r for r in evaluated if r['security_id'] not in topsecs])}
 conc=positive_concentration([r for r in evaluated if r['decile']==10])
 coverage=len(evaluated)/(len(decisions)*len(primary)) if decisions and primary else 0
 gates={
  'P1':'PASS' if base['d10_net_excess_10bp'] is not None and base['d10_net_excess_10bp']>0 else 'FAIL',
  'P2':'PASS' if base['d10_d1_net_spread_10bp'] is not None and base['d10_d1_net_spread_10bp']>0 else 'FAIL',
  'P3':'PASS' if ics and np.mean(ics)>0 else 'FAIL',
  'P4':'PASS' if mono is not None and mono>=.30 else 'FAIL',
  'P5':'PASS' if all(x['d10_net_excess_10bp'] is not None and x['d10_net_excess_10bp']>0 and x['d10_d1_net_spread_10bp'] is not None and x['d10_d1_net_spread_10bp']>0 for x in robustness.values()) else 'FAIL',
  'P6':'PASS' if conc['single'] is not None and conc['single']<=.35 and conc['top5']<=.75 and conc['top10']<=.90 else 'FAIL',
  'P7':'PASS' if base['d10_net_excess_10bp'] is not None and base['d10_net_excess_10bp']>0 and base['d10_d1_net_spread_10bp'] is not None and base['d10_d1_net_spread_10bp']>0 else 'FAIL',
  'P8':'PASS' if len(decisions)>=15 and np.median([m['scored_labeled'] for m in monthly])>=20 and coverage>=.50 else 'FAIL'}
 if all(v=='PASS' for v in gates.values()):verdict='PROMISING'
 elif gates['P8']=='PASS' and sum(gates[f'P{i}']=='PASS' for i in range(1,5))<=1:verdict='UNPROMISING'
 else:verdict='INCONCLUSIVE'
 quarters=defaultdict(list)
 for m in monthly:
  if m['d10_d1_net_spread_10bp'] is not None:
   d=date.fromisoformat(m['decision_date']);quarters[f'{d.year}-Q{(d.month-1)//3+1}'].append(m['d10_d1_net_spread_10bp'])
 result={'schema':'STRATEGY_H_PV1_RESULT_V1','data_quality':quality,'window':{'daily_start':sessions[0].isoformat(),'daily_end':sessions[-1].isoformat(),'sessions':len(sessions),'decision_start':decisions[0].isoformat(),'decision_end':decisions[-1].isoformat(),'decision_dates':len(decisions)},'universe':{'reference_rows':len(refs),'primary_single_class':len(primary),'multi_class_rows':len(refs)-len(primary),'multi_class_ciks':sorted(multi),'tickers':[r['ticker'] for r in refs]},'feature_coverage':{k:{'count':v,'fraction':v/(len(decisions)*len(primary))} for k,v in feature_counts.items()},'shares_reasons':dict(shares_reasons),'evaluation':{'eligible_rows':len(decisions)*len(primary),'evaluated_rows':len(evaluated),'coverage':coverage,'base':base,'descriptive':descriptions,'d10_issuer_cluster_bootstrap_95ci':cluster_ci,'mean_ic':float(np.mean(ics)) if ics else None,'ic_positive_fraction':sum(x>0 for x in ics)/len(ics) if ics else None,'decile_means':{str(k):float(v) for k,v in decmeans.items()},'monotonicity':mono,'top':top,'robustness':robustness,'concentration':conc,'quarterly_spread':{k:float(np.mean(v)) for k,v in quarters.items()},'monthly_positive_spread_fraction':float(np.mean([m['d10_d1_net_spread_10bp']>0 for m in monthly if m['d10_d1_net_spread_10bp'] is not None])),'lane_counts':dict(Counter(r['lane'] for r in evaluated)),'lane_mean_net_excess_63':{lane:float(np.mean([r['net_excess_63'] for r in evaluated if r['lane']==lane])) for lane in sorted({r['lane'] for r in evaluated})},'feature_distributions':{f:{'min':float(np.min(v)),'p025':float(np.quantile(v,.025)),'median':float(np.median(v)),'p975':float(np.quantile(v,.975)),'max':float(np.max(v))} for f in ('earnings_yield','fcf_yield','sales_yield') for v in [[r[f] for r in raw if r.get(f) is not None]] if v},'quarterly_positive_fraction':float(np.mean([np.mean(v)>0 for v in quarters.values()])) if quarters else None,'monthly':monthly},'gates':gates,'verdict':verdict,'constraints':{'official_h1':False,'gpt_used':False,'paid_data_used':False,'post_hoc_tuning':False}}
 OUTPUT.mkdir(parents=True,exist_ok=True); rawjson=json.dumps(result,indent=2,sort_keys=True)+'\n';(OUTPUT/'result.json').write_text(rawjson);(OUTPUT/'result.sha256').write_text(hashlib.sha256(rawjson.encode()).hexdigest()+'\n')
 print(json.dumps({'verdict':verdict,'gates':gates,'window':result['window'],'universe':result['universe'],'feature_coverage':result['feature_coverage'],'evaluation':{k:v for k,v in result['evaluation'].items() if k!='monthly'}},indent=2))
if __name__=='__main__':main()
