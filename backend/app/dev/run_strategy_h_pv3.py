"""Execute the frozen H-PV3 Quality-only prevalidation."""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import date,timezone
import gzip,hashlib,json,math
from pathlib import Path
import numpy as np
from app.backtest.strategy_h0.facts import extract_companyfacts
from app.backtest.strategy_h0.h0_5 import historical_market_cap,market_cap_lane,resolve_pit_shares
from app.backtest.strategy_h0.h_pv1 import assign_deciles,positive_concentration,spearman
from app.backtest.strategy_h0.h_pv3 import FEATURES,cash_assets,cash_conversion,fcf_margin,feature_quality,operating_margin,quality_scores,roa,verdict
from app.backtest.strategy_h0.pilot import acceptance_index,read_gzip_json
from app.backtest.strategy_eqm_v0.xbrl_store import read_facts
from app.dev.run_strategy_h_pv1 import SPLITS,load_json_gz,load_prices,month_ends
from app.market.calendar import MarketCalendar

SOURCE=Path('data/runtime/strategy_h/h_pv2c');OUTPUT=Path('data/runtime/strategy_h/h_pv3')
FUNCS={'operating_margin':operating_margin,'fcf_margin':fcf_margin,'roa':roa,'cash_conversion':cash_conversion,'cash_assets':cash_assets}
def deciles(rows):
 out=assign_deciles([{**r,'value_score':r.get('quality_score')} for r in rows])
 for r in out:r.pop('value_score',None)
 return out
def summary(rows):
 def avg(field,rs):
  x=[r[field] for r in rs if r.get(field) is not None];return float(np.mean(x)) if x else None
 a=avg('excess_63',[r for r in rows if r.get('decile')==10]);b=avg('excess_63',[r for r in rows if r.get('decile')==1])
 return {'rows':len(rows),'d10_n':sum(r.get('decile')==10 for r in rows),'d1_n':sum(r.get('decile')==1 for r in rows),'d10_gross_excess':a,'d1_gross_excess':b,'d10_net_excess_10bp':None if a is None else a-.001,'d10_d1_net_spread_10bp':None if a is None or b is None else a-b-.002,'d10_net_excess_20bp':None if a is None else a-.002,'d10_d1_net_spread_20bp':None if a is None or b is None else a-b-.004}
def main():
 refs=json.load(open(SOURCE/'sample.json'))['rows'];by_cik=defaultdict(list)
 for r in refs:by_cik[r['cik']].append(r)
 prices,market_quality=load_prices({r['ticker'] for r in refs}|{'SPY'});sessions=sorted(prices['SPY']);index={d:i for i,d in enumerate(sessions)};cal=MarketCalendar();opens=[x for d in sessions if (x:=cal.regular_market_open(d))]
 split_dates=defaultdict(list)
 for r in load_json_gz(SPLITS)['results']:split_dates[r['ticker']].append(date.fromisoformat(r['execution_date']))
 facts={}
 for cik in by_cik:
  sub=SOURCE/'sec_raw'/'submissions'/f'CIK{cik}'/f'CIK{cik}.json.gz';doc=read_facts(SOURCE/'sec_raw',cik);facts[cik]=extract_companyfacts(doc,acceptance_index(read_gzip_json(sub))) if doc and sub.exists() else []
 decisions=[d for d in month_ends(sessions) if d>=date(2024,10,31) and index[d]+63<len(sessions)];raw=[];statuses={f:Counter() for f in FEATURES};prov=[]
 for d in decisions:
  cutoff=cal.regular_market_open(d).astimezone(timezone.utc)
  for ref in refs:
   fs=facts[ref['cik']];ticker=ref['ticker'];sr=resolve_pit_shares(fs,d,opens,split_dates=split_dates[ticker]);cap=historical_market_cap(prices[ticker].get(d),sr);values={};fp={}
   for name,fn in FUNCS.items():
    values[name],status,p=fn(fs,cutoff,d);statuses[name][status]+=1;fp[name]={'status':status,'facts':p}
   sid=ref.get('share_class_figi') or ref['composite_figi'];raw.append({'decision_date':d.isoformat(),'security_id':sid,'company_id':ref['cik'],'ticker':ticker,'market_cap':cap,'lane':market_cap_lane(cap).value,**values});prov.append({'decision_date':d.isoformat(),'security_id':sid,'company_id':ref['cik'],'features':fp})
 dates=[d.isoformat() for d in decisions];included,qreport=feature_quality(raw,dates);scored=[]
 for ds in dates:scored.extend(quality_scores([r for r in raw if r['decision_date']==ds],included))
 evaluated=[];monthly=[];factor_ics={f:[] for f in FEATURES}
 for d in decisions:
  i=index[d];spy63=prices['SPY'][sessions[i+63]]/prices['SPY'][d]-1;spy21=prices['SPY'][sessions[i+21]]/prices['SPY'][d]-1;valid=[]
  for r in [x for x in scored if x['decision_date']==d.isoformat()]:
   p0=prices[r['ticker']].get(d);p63=prices[r['ticker']].get(sessions[i+63]);p21=prices[r['ticker']].get(sessions[i+21])
   if r['quality_score'] is None or p0 is None or p63 is None or p0<=0:continue
   r['excess_63']=p63/p0-1-spy63;r['net_excess_63']=r['excess_63']-.001;r['excess_21']=None if p21 is None else p21/p0-1-spy21;valid.append(r)
  ds=deciles(valid);evaluated+=ds;ic=spearman([r['quality_score'] for r in valid],[r['excess_63'] for r in valid]) if len(valid)>1 else None
  for f in FEATURES:
   z=[r for r in valid if r.get(f) is not None];v=spearman([r[f] for r in z],[r['excess_63'] for r in z]) if len(z)>1 else None
   if v is not None:factor_ics[f].append(v)
  monthly.append({'decision_date':d.isoformat(),'scored_labeled':len(valid),'ic':ic,**summary(ds)})
 base=summary(evaluated);ics=[m['ic'] for m in monthly if m['ic'] is not None];means={d:float(np.mean([r['excess_63'] for r in evaluated if r['decile']==d])) for d in range(1,11)};mono=spearman(list(means),list(means.values()))
 top={}
 for frac,name in ((.10,'top10pct'),(.20,'top20pct')):
  z=[]
  for ds in dates:
   rs=sorted([r for r in evaluated if r['decision_date']==ds],key=lambda r:(r['quality_score'],r['security_id']),reverse=True);z+=rs[:max(1,math.ceil(len(rs)*frac))]
  top[name]={'n':len(z),'mean_net_excess_63':float(np.mean([r['net_excess_63'] for r in z])) if z else None}
 ordered=sorted(evaluated,key=lambda r:r['excess_63'],reverse=True);n1=max(1,math.ceil(len(ordered)*.01));contrib=defaultdict(float)
 for r in evaluated:contrib[r['company_id']]+=max(r['net_excess_63'],0)
 drop={k for k,_ in sorted(contrib.items(),key=lambda x:x[1],reverse=True)[:10]};robust={'remove_top_1pct':summary(ordered[n1:]),'remove_top_5':summary(ordered[5:]),'remove_top_10_issuers':summary([r for r in evaluated if r['company_id'] not in drop])};conc=positive_concentration([{**r,'security_id':r['company_id']} for r in evaluated if r['decile']==10]);coverage=len(evaluated)/(len(decisions)*len(refs));quarters=defaultdict(list)
 for m in monthly:
  dt=date.fromisoformat(m['decision_date']);quarters[f'{dt.year}-Q{(dt.month-1)//3+1}'].append(m['d10_d1_net_spread_10bp'])
 gates={'P1':'PASS' if base['d10_net_excess_10bp']>0 else 'FAIL','P2':'PASS' if base['d10_d1_net_spread_10bp']>0 else 'FAIL','P3':'PASS' if np.mean(ics)>0 else 'FAIL','P4':'PASS' if mono>=.30 else 'FAIL','P5':'PASS' if all(x['d10_net_excess_10bp']>0 and x['d10_d1_net_spread_10bp']>0 for x in robust.values()) else 'FAIL','P6':'PASS' if conc['single'] is not None and conc['single']<=.35 and conc['top5']<=.75 and conc['top10']<=.90 else 'FAIL','P7':'PASS' if base['d10_net_excess_10bp']>0 and base['d10_d1_net_spread_10bp']>0 else 'FAIL','P8':'PASS' if len(decisions)>=15 and np.median([m['scored_labeled'] for m in monthly])>=20 and coverage>=.50 else 'FAIL'}
 d10=[r for r in evaluated if r['decile']==10];rng=np.random.default_rng(20260928);ids=sorted({r['company_id'] for r in d10});boot=[]
 for _ in range(10000):
  vals=[]
  for company in rng.choice(ids,len(ids),replace=True):vals += [r['net_excess_63'] for r in d10 if r['company_id']==company]
  boot.append(float(np.mean(vals)))
 result={'schema':'STRATEGY_H_PV3_RESULT_V1','market_data_quality':market_quality,'window':{'start':sessions[0].isoformat(),'end':sessions[-1].isoformat(),'sessions':len(sessions),'decision_start':dates[0],'decision_end':dates[-1],'decision_dates':len(dates)},'universe':{'count':len(refs),'source':'PV2C frozen sample','checksum':'6517b10e0fcec3a04a5e553a96e4b1c41052af94321434832533d1d6327fd729'},'feature_quality':qreport,'included_features':included,'invalid_counts':{f:dict(v) for f,v in statuses.items()},'evaluation':{'eligible_rows':len(decisions)*len(refs),'rows':len(evaluated),'coverage':coverage,'base':base,'decile_means':{str(k):v for k,v in means.items()},'mean_ic':float(np.mean(ics)),'median_ic':float(np.median(ics)),'positive_ic_fraction':float(np.mean(np.asarray(ics)>0)),'ic_95_interval':[float(x) for x in np.quantile(ics,[.025,.975])],'monotonicity':mono,'d10_cluster_bootstrap_95ci':[float(x) for x in np.quantile(boot,[.025,.975])],'individual_factor_ic':{f:{'mean':float(np.mean(v)) if v else None,'median':float(np.median(v)) if v else None,'n':len(v)} for f,v in factor_ics.items()},'top':top,'robustness':robust,'concentration':conc,'monthly_positive_fraction':float(np.mean([m['d10_d1_net_spread_10bp']>0 for m in monthly])),'quarterly':{k:float(np.mean(v)) for k,v in quarters.items()},'quarterly_positive_fraction':float(np.mean([np.mean(v)>0 for v in quarters.values()])),'lane_counts':dict(Counter(r['lane'] for r in evaluated)),'lane_mean_net_excess':{x:float(np.mean([r['net_excess_63'] for r in evaluated if r['lane']==x])) for x in sorted({r['lane'] for r in evaluated})},'monthly':monthly},'gates':gates,'verdict':verdict(gates),'limitation':'DATA' if gates['P8']=='FAIL' else 'SIGNAL','constraints':{'pv1_modified':False,'pv2_modified':False,'pv2c_modified':False,'value_used':False,'growth_used':False,'gpt_used':False,'paid_data_used':False,'official_h1':False,'post_hoc_tuning':False}}
 OUTPUT.mkdir(parents=True,exist_ok=True);rawjson=json.dumps(result,indent=2,sort_keys=True,default=lambda x:x.item())+'\n';(OUTPUT/'result.json').write_text(rawjson);(OUTPUT/'result.sha256').write_text(hashlib.sha256(rawjson.encode()).hexdigest()+'\n')
 with gzip.open(OUTPUT/'provenance.json.gz','wt',encoding='utf-8') as h:json.dump(prov,h,sort_keys=True)
 print(json.dumps({'verdict':result['verdict'],'gates':gates,'included_features':included,'feature_quality':qreport,'evaluation':{k:v for k,v in result['evaluation'].items() if k!='monthly'}},indent=2,default=lambda x:x.item()))
if __name__=='__main__':main()
