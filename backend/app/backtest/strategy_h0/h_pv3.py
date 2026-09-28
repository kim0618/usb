"""Frozen H-PV3 Quality-only primitives."""
from __future__ import annotations
from collections import Counter
from datetime import date,datetime,timedelta
import math
from typing import Iterable
import numpy as np
from app.backtest.strategy_h0.facts import CanonicalFact,FIELD_SPECS

DISCRETE={'Q1':(70,110),'Q2':(70,110),'Q3':(70,110),'FY':(300,400)}
YTD={'Q1':(70,110),'Q2':(160,200),'Q3':(250,300),'FY':(300,400)}
FEATURES=['operating_margin','fcf_margin','roa','cash_conversion','cash_assets']

def _family(f:CanonicalFact,families:dict)->str|None:
 if f.start is None:return None
 fp=f.fiscal_period; days=(f.end-f.start).days
 if fp not in families:return None
 if fp=='FY' and f.form not in {'10-K','10-K/A'}:return None
 if fp!='FY' and f.form not in {'10-Q','10-Q/A'}:return None
 lo,hi=families[fp]
 return fp if lo<=days<=hi else None

def _winner(rows:list[CanonicalFact],field:str)->CanonicalFact|None:
 if not rows:return None
 spec=FIELD_SPECS[field];p=min(spec.tags.index(f.tag) for f in rows);rows=[f for f in rows if spec.tags.index(f.tag)==p]
 a=max(f.accepted_at for f in rows);rows=[f for f in rows if f.accepted_at==a];acc=max(f.accession for f in rows);rows=[f for f in rows if f.accession==acc]
 return rows[0] if len({(f.value,f.start,f.end,f.unit) for f in rows})==1 else None

def matched_duration(facts:Iterable[CanonicalFact],fields:list[str],cutoff:datetime,decision:date,families:dict)->tuple[dict[str,CanonicalFact]|None,str]:
 fs=list(facts); candidates=[]
 for f in fs:
  if f.field==fields[0] and f.accepted_at<=cutoff and f.end<=decision and _family(f,families):candidates.append((f.end,f.start,_family(f,families)))
 for end,start,family in sorted(set(candidates),reverse=True):
  got={x:_winner([f for f in fs if f.field==x and f.start==start and f.end==end and f.accepted_at<=cutoff and _family(f,families)==family],x) for x in fields}
  if all(got.values()):return got,'OK'
 return None,'MISSING_OR_DURATION_MISMATCH'

def instant_pair(facts:Iterable[CanonicalFact],fields:list[str],cutoff:datetime,decision:date)->tuple[dict[str,CanonicalFact]|None,str]:
 fs=list(facts); ends=sorted({f.end for f in fs if f.field==fields[0] and f.start is None and f.end<=decision and f.accepted_at<=cutoff},reverse=True)
 for end in ends:
  got={x:_winner([f for f in fs if f.field==x and f.start is None and f.end==end and f.accepted_at<=cutoff],x) for x in fields}
  if all(got.values()):return got,'OK'
 return None,'MISSING_OR_INSTANT_MISMATCH'

def operating_margin(facts,cutoff,decision):
 x,s=matched_duration(facts,['revenue','operating_income'],cutoff,decision,DISCRETE)
 if not x:return None,s,{}
 if x['revenue'].value<=0:return None,'INVALID_REVENUE',{}
 return x['operating_income'].value/x['revenue'].value,'OK',provenance(x)

def fcf_margin(facts,cutoff,decision):
 x,s=matched_duration(facts,['revenue','operating_cash_flow','capex'],cutoff,decision,YTD)
 if not x:return None,s,{}
 if x['revenue'].value<=0:return None,'INVALID_REVENUE',{}
 return (x['operating_cash_flow'].value-x['capex'].value)/x['revenue'].value,'OK',provenance(x)

def cash_conversion(facts,cutoff,decision):
 x,s=matched_duration(facts,['net_income','operating_cash_flow'],cutoff,decision,YTD)
 if not x:return None,s,{}
 if x['net_income'].value<=1_000_000:return None,'INVALID_NET_INCOME',{}
 return x['operating_cash_flow'].value/x['net_income'].value,'OK',provenance(x)

def cash_assets(facts,cutoff,decision):
 x,s=instant_pair(facts,['assets','cash'],cutoff,decision)
 if not x:return None,s,{}
 if x['assets'].value<=0 or x['cash'].value<0:return None,'INVALID_BALANCE',{}
 return x['cash'].value/x['assets'].value,'OK',provenance(x)

def roa(facts,cutoff,decision):
 x,s=matched_duration(facts,['net_income'],cutoff,decision,DISCRETE)
 if not x:return None,s,{}
 flow=x['net_income'];fs=list(facts)
 def balance(target):
  for day in (target,target-timedelta(days=1)):
   f=_winner([z for z in fs if z.field=='assets' and z.start is None and z.end==day and z.accepted_at<=cutoff],'assets')
   if f:return f
  return None
 begin,end=balance(flow.start),balance(flow.end)
 if not begin or not end:return None,'MISSING_AVERAGE_ASSETS',{}
 avg=(begin.value+end.value)/2
 if begin.value<=0 or end.value<=0 or avg<=0:return None,'INVALID_ASSETS',{}
 days=(flow.end-flow.start).days
 return flow.value*(365/days)/avg,'OK',provenance({'net_income':flow,'assets_begin':begin,'assets_end':end})

def provenance(values):return {k:{'accession':v.accession,'accepted':v.accepted_at.isoformat(),'start':v.start.isoformat() if v.start else None,'end':v.end.isoformat(),'tag':v.tag} for k,v in values.items()}

def feature_quality(rows,dates,features=FEATURES):
 report={}
 for f in features:
  counts=[sum(r.get(f) is not None for r in rows if r['decision_date']==d) for d in dates];n=sum(counts);coverage=n/len(rows) if rows else 0
  report[f]={'count':n,'coverage':coverage,'median_per_date':float(np.median(counts)) if counts else 0,'passed':coverage>=.40 and np.median(counts)>=10}
 return [f for f in features if report[f]['passed']],report

def quality_scores(rows,features):
 out=[dict(r) for r in rows]
 for f in features:
  vals=np.asarray([r[f] for r in out if r.get(f) is not None],float)
  if not len(vals):continue
  lo,hi=np.quantile(vals,[.025,.975]);order=sorted((min(max(r[f],lo),hi),i) for i,r in enumerate(out) if r.get(f) is not None);n=len(order);p=0
  while p<n:
   q=p+1
   while q<n and order[q][0]==order[p][0]:q+=1
   rank=((p+q-1)/2)/(n-1) if n>1 else .5
   for _,i in order[p:q]:out[i][f+'_rank']=rank
   p=q
 for r in out:
  ranks=[r.get(f+'_rank') for f in features if r.get(f+'_rank') is not None];r['quality_score']=sum(ranks)/len(ranks) if len(ranks)>=2 else None
 return out

def verdict(gates):
 if all(gates[f'P{i}']=='PASS' for i in range(1,9)):return 'PROMISING'
 if gates['P8']=='PASS' and sum(gates[f'P{i}']=='PASS' for i in range(1,5))<=1:return 'UNPROMISING'
 return 'INCONCLUSIVE'
