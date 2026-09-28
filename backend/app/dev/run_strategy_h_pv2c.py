"""Execute frozen H-PV2 Growth-only prevalidation using PV1 infrastructure."""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import date,timezone
import gzip,hashlib,json,math
from pathlib import Path
import numpy as np
from app.backtest.strategy_h0.facts import extract_companyfacts
from app.backtest.strategy_h0.h0_5 import historical_market_cap,market_cap_lane,resolve_pit_shares
from app.backtest.strategy_h0.h_pv1 import assign_deciles,positive_concentration,spearman
from app.backtest.strategy_h0.h_pv2 import comparable_pair,fcf_pair,growth_rate,growth_rows_scores,quality_features
from app.backtest.strategy_h0.h_pv2c import assert_no_leakage,confirmation_verdict,stable_id
from app.backtest.strategy_h0.pilot import acceptance_index,read_gzip_json
from app.backtest.strategy_eqm_v0.xbrl_store import read_facts
from app.dev.run_strategy_h_pv1 import DAILY,FACTS,REFERENCE,SPLITS,SUBMISSIONS,load_json_gz,load_prices,month_ends,sample_rows
from app.market.calendar import MarketCalendar
OUTPUT=Path('data/runtime/strategy_h/h_pv2c')
CONF_FACTS=OUTPUT/'sec_raw'
FEATURES=['revenue_growth','operating_income_growth','eps_growth','fcf_growth']
def growth_deciles(rows):
 adapted=[{**r,'value_score':r.get('growth_score')} for r in rows];out=assign_deciles(adapted)
 for r in out:r.pop('value_score',None)
 return out
def summarize(rows):
 def mean(field,rs):
  v=[r[field] for r in rs if r.get(field) is not None];return float(np.mean(v)) if v else None
 d10=[r for r in rows if r['decile']==10];d1=[r for r in rows if r['decile']==1];a=mean('excess_63',d10);b=mean('excess_63',d1)
 return {'rows':len(rows),'d10_n':len(d10),'d1_n':len(d1),'d10_gross_excess':a,'d1_gross_excess':b,'d10_net_excess_10bp':None if a is None else a-.001,'d10_d1_net_spread_10bp':None if a is None or b is None else a-b-.002,'d10_net_excess_20bp':None if a is None else a-.002,'d10_d1_net_spread_20bp':None if a is None or b is None else a-b-.004}
def fact_growth(fs,field,cutoff,decision,*,eps=False):
 pair=comparable_pair(fs,field,cutoff,decision)
 if pair is None:return None,'MISSING_COMPARABLE',{}
 value,status=growth_rate(pair.current.value,pair.prior.value,eps=eps)
 return value,status,{'current_accession':pair.current.accession,'current_accepted':pair.current.accepted_at.isoformat(),'prior_accession':pair.prior.accession,'prior_accepted':pair.prior.accepted_at.isoformat(),'family':pair.family}
def main():
 refs=json.load(open(OUTPUT/'sample.json'))['rows'];primary=refs;by_cik=defaultdict(list)
 for r in refs:by_cik[r['cik']].append(r)
 # Reconstruct and enforce exact PV2 development identities before any label.
 dev=sample_rows();dev_by=defaultdict(list)
 for r in dev:dev_by[r['cik']].append(r)
 dev=[r for r in dev if len(dev_by[r['cik']])==1];assert_no_leakage(primary,{r['cik'] for r in dev},{stable_id(r) for r in dev});multi=set()
 symbols={r['ticker'] for r in refs}|{'SPY'};prices,quality=load_prices(symbols);sessions=sorted(prices['SPY']);idx={d:i for i,d in enumerate(sessions)}
 cal=MarketCalendar();opens=[cal.regular_market_open(d) for d in sessions];opens=[x for x in opens if x]
 split_dates=defaultdict(list)
 for r in load_json_gz(SPLITS)['results']:split_dates[r['ticker']].append(date.fromisoformat(r['execution_date']))
 facts_by={}
 for cik in by_cik:
  sub=CONF_FACTS/'submissions'/f'CIK{cik}'/f'CIK{cik}.json.gz';doc=read_facts(CONF_FACTS,cik);facts_by[cik]=extract_companyfacts(doc,acceptance_index(read_gzip_json(sub))) if doc and sub.exists() else []
 decisions=[d for d in month_ends(sessions) if d>=date(2024,10,31) and idx[d]+63<len(sessions)];raw=[];statuses={f:Counter() for f in FEATURES};provenance=[]
 for d in decisions:
  cutoff=cal.regular_market_open(d).astimezone(timezone.utc);day=[]
  for ref in primary:
   fs=facts_by[ref['cik']];ticker=ref['ticker'];sr=resolve_pit_shares(fs,d,opens,split_dates=split_dates[ticker]);cap=historical_market_cap(prices[ticker].get(d),sr)
   vals={};prov={}
   for name,field,eps in [('revenue_growth','revenue',False),('operating_income_growth','operating_income',False),('eps_growth','eps_diluted',True)]:
    vals[name],status,p=fact_growth(fs,field,cutoff,d,eps=eps);statuses[name][status]+=1;prov[name]=p|{'status':status}
   vals['fcf_growth'],status,p=fcf_pair(fs,cutoff,d);statuses['fcf_growth'][status]+=1;prov['fcf_growth']=p|{'status':status}
   sid=ref.get('share_class_figi') or ref['composite_figi'];row={'decision_date':d.isoformat(),'security_id':sid,'company_id':ref['cik'],'ticker':ticker,'market_cap':cap,'lane':market_cap_lane(cap).value,**vals};day.append(row);provenance.append({'decision_date':d.isoformat(),'security_id':sid,'company_id':ref['cik'],'features':prov})
  raw.extend(day)
 dates=[d.isoformat() for d in decisions];included,quality_report=quality_features(raw,dates,FEATURES);scored=[]
 for d in dates:scored.extend(growth_rows_scores([r for r in raw if r['decision_date']==d],included))
 evaluated=[];monthly=[];factor_ics={f:[] for f in FEATURES}
 for d in decisions:
  i=idx[d];spy63=prices['SPY'][sessions[i+63]]/prices['SPY'][d]-1;spy21=prices['SPY'][sessions[i+21]]/prices['SPY'][d]-1;valid=[]
  for r in [x for x in scored if x['decision_date']==d.isoformat()]:
   p0=prices[r['ticker']].get(d);p63=prices[r['ticker']].get(sessions[i+63]);p21=prices[r['ticker']].get(sessions[i+21])
   if r['growth_score'] is None or p0 is None or p63 is None or p0<=0:continue
   r['excess_63']=p63/p0-1-spy63;r['net_excess_63']=r['excess_63']-.001;r['excess_21']=None if p21 is None else p21/p0-1-spy21;valid.append(r)
  dec=growth_deciles(valid);evaluated.extend(dec);ic=spearman([r['growth_score'] for r in valid],[r['excess_63'] for r in valid]) if len(valid)>1 else None
  for f in FEATURES:
   rs=[r for r in valid if r.get(f) is not None];fic=spearman([r[f] for r in rs],[r['excess_63'] for r in rs]) if len(rs)>1 else None
   if fic is not None:factor_ics[f].append(fic)
  monthly.append({'decision_date':d.isoformat(),'scored_labeled':len(valid),'ic':ic,**summarize(dec)})
 base=summarize(evaluated);ics=[m['ic'] for m in monthly if m['ic'] is not None];decmeans={d:float(np.mean([r['excess_63'] for r in evaluated if r['decile']==d])) for d in range(1,11)};mono=spearman(list(decmeans),list(decmeans.values()))
 top={}
 for frac,name in ((.10,'top10pct'),(.20,'top20pct')):
  selected=[]
  for d in dates:
   rs=sorted([r for r in evaluated if r['decision_date']==d],key=lambda r:(r['growth_score'],r['security_id']),reverse=True);selected+=rs[:max(1,math.ceil(len(rs)*frac))]
  top[name]={'n':len(selected),'mean_net_excess_63':float(np.mean([r['net_excess_63'] for r in selected])) if selected else None}
 ordered=sorted(evaluated,key=lambda r:r['excess_63'],reverse=True);n1=max(1,math.ceil(len(ordered)*.01));contrib=defaultdict(float)
 for r in evaluated:contrib[r['company_id']]+=max(r['net_excess_63'],0)
 topissuers={k for k,_ in sorted(contrib.items(),key=lambda x:x[1],reverse=True)[:10]};robust={'remove_top_1pct':summarize(ordered[n1:]),'remove_top_5':summarize(ordered[5:]),'remove_top_10_issuers':summarize([r for r in evaluated if r['company_id'] not in topissuers])}
 conc=positive_concentration([{**r,'security_id':r['company_id']} for r in evaluated if r['decile']==10]);coverage=len(evaluated)/(len(decisions)*len(primary));quarters=defaultdict(list)
 for m in monthly:
  dt=date.fromisoformat(m['decision_date']);quarters[f'{dt.year}-Q{(dt.month-1)//3+1}'].append(m['d10_d1_net_spread_10bp'])
 gates={'C1':'PASS' if base['d10_net_excess_10bp']>0 else 'FAIL','C2':'PASS' if base['d10_d1_net_spread_10bp']>0 else 'FAIL','C3':'PASS' if np.mean(ics)>0 else 'FAIL','C4':'PASS' if mono>=.30 else 'FAIL','C5':'PASS' if all(x['d10_net_excess_10bp']>0 and x['d10_d1_net_spread_10bp']>0 for x in robust.values()) else 'FAIL','C6':'PASS' if conc['single'] is not None and conc['single']<=.35 and conc['top5']<=.75 and conc['top10']<=.90 else 'FAIL','C7':'PASS' if base['d10_net_excess_10bp']>0 and base['d10_d1_net_spread_10bp']>0 else 'FAIL','C8':'PASS' if len(decisions)>=15 and np.median([m['scored_labeled'] for m in monthly])>=20 and coverage>=.50 else 'FAIL'}
 verdict=confirmation_verdict(gates)
 d10=[r for r in evaluated if r['decile']==10];rng=np.random.default_rng(20260928);ids=sorted({r['company_id'] for r in d10});boot=[]
 for _ in range(10000):
  draw=rng.choice(ids,len(ids),replace=True);v=[]
  for company in draw:v += [r['net_excess_63'] for r in d10 if r['company_id']==company]
  boot.append(float(np.mean(v)))
 result={'schema':'STRATEGY_H_PV2C_RESULT_V1','data_quality':quality,'window':{'start':sessions[0].isoformat(),'end':sessions[-1].isoformat(),'sessions':len(sessions),'decision_start':dates[0],'decision_end':dates[-1],'decision_dates':len(dates)},'universe':{'reference':len(refs),'primary':len(primary),'multi_class_rows':0,'development_cik_overlap':0,'development_id_overlap':0},'feature_quality':quality_report,'included_features':included,'invalid_transition_counts':{f:dict(v) for f,v in statuses.items()},'evaluation':{'eligible_rows':len(decisions)*len(primary),'rows':len(evaluated),'coverage':coverage,'base':base,'decile_means':{str(k):v for k,v in decmeans.items()},'mean_ic':float(np.mean(ics)),'median_ic':float(np.median(ics)),'positive_ic_fraction':float(np.mean(np.asarray(ics)>0)),'ic_95_interval':[float(x) for x in np.quantile(ics,[.025,.975])],'monotonicity':mono,'d10_cluster_bootstrap_95ci':[float(x) for x in np.quantile(boot,[.025,.975])],'individual_factor_ic':{f:{'mean':float(np.mean(v)) if v else None,'median':float(np.median(v)) if v else None,'n':len(v)} for f,v in factor_ics.items()},'top':top,'robustness':robust,'concentration':conc,'monthly_positive_fraction':float(np.mean([m['d10_d1_net_spread_10bp']>0 for m in monthly])),'quarterly':{k:float(np.mean(v)) for k,v in quarters.items()},'quarterly_positive_fraction':float(np.mean([np.mean(v)>0 for v in quarters.values()])),'lane_counts':dict(Counter(r['lane'] for r in evaluated)),'lane_mean_net_excess':{lane:float(np.mean([r['net_excess_63'] for r in evaluated if r['lane']==lane])) for lane in sorted({r['lane'] for r in evaluated})},'monthly':monthly},'gates':gates,'verdict':verdict,'constraints':{'pv1_modified':False,'pv2_modified':False,'development_issuers_reused':False,'growth_model_changed':False,'value_score_used':False,'gpt_used':False,'paid_data_used':False,'official_h1':False,'post_hoc_tuning':False}}
 OUTPUT.mkdir(parents=True,exist_ok=True);rawjson=json.dumps(result,indent=2,sort_keys=True,default=lambda x:x.item())+'\n';(OUTPUT/'result.json').write_text(rawjson);(OUTPUT/'result.sha256').write_text(hashlib.sha256(rawjson.encode()).hexdigest()+'\n')
 with gzip.open(OUTPUT/'provenance.json.gz','wt',encoding='utf-8') as h:json.dump(provenance,h,sort_keys=True)
 print(json.dumps({'verdict':verdict,'gates':gates,'included_features':included,'feature_quality':quality_report,'evaluation':{k:v for k,v in result['evaluation'].items() if k!='monthly'}},indent=2,default=lambda x:x.item()))
if __name__=='__main__':main()
