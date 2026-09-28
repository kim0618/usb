"""Frozen H-PV2 comparable-period Growth primitives."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from datetime import date,datetime
from typing import Iterable
import numpy as np
from app.backtest.strategy_h0.facts import CanonicalFact,FIELD_SPECS

@dataclass(frozen=True)
class ComparablePair:
    current:CanonicalFact
    prior:CanonicalFact
    family:str

def period_family(f:CanonicalFact,*,fcf:bool=False)->str|None:
    if f.start is None:return None
    days=(f.end-f.start).days
    fp=f.fiscal_period
    if f.form in {'10-K','10-K/A'} and fp=='FY' and 300<=days<=400:return 'FY'
    if f.form not in {'10-Q','10-Q/A'}:return None
    if fp=='Q1' and 70<=days<=110:return 'Q1'
    if not fcf and fp in {'Q2','Q3'} and 70<=days<=110:return fp
    if fcf and fp=='Q2' and 160<=days<=200:return 'Q2_YTD'
    if fcf and fp=='Q3' and 250<=days<=300:return 'Q3_YTD'
    return None

def _winner(rows:list[CanonicalFact],field:str)->CanonicalFact|None:
    if not rows:return None
    spec=FIELD_SPECS[field]; p=min(spec.tags.index(f.tag) for f in rows);rows=[f for f in rows if spec.tags.index(f.tag)==p]
    accepted=max(f.accepted_at for f in rows);rows=[f for f in rows if f.accepted_at==accepted]
    accession=max(f.accession for f in rows);rows=[f for f in rows if f.accession==accession]
    return rows[0] if len({(f.value,f.start,f.end,f.unit) for f in rows})==1 else None

def comparable_pair(facts:Iterable[CanonicalFact],field:str,cutoff:datetime,decision:date,*,fcf:bool=False)->ComparablePair|None:
    rows=[f for f in facts if f.field==field and f.accepted_at<=cutoff and f.end<=decision and period_family(f,fcf=fcf)]
    for end in sorted({f.end for f in rows},reverse=True):
        currents=[f for f in rows if f.end==end]; current=_winner(currents,field)
        if current is None:continue
        family=period_family(current,fcf=fcf); duration=(current.end-current.start).days
        priors=[f for f in rows if f.tag==current.tag and f.unit==current.unit and period_family(f,fcf=fcf)==family
                and 345<=(current.end-f.end).days<=385 and abs((f.end-f.start).days-duration)<= (15 if family=='FY' else 7)]
        prior=_winner(priors,field)
        if prior is not None:return ComparablePair(current,prior,family)
    return None

def growth_rate(current:float,prior:float,*,eps:bool=False)->tuple[float|None,str]:
    floor=.05 if eps else 0.0
    if prior<=floor:
        return None,'LOSS_TO_PROFIT' if current>0 else 'NONPOSITIVE_PRIOR'
    if current<=0:return None,'PROFIT_TO_LOSS'
    return current/prior-1,'OK'

def fcf_pair(facts:Iterable[CanonicalFact],cutoff:datetime,decision:date)->tuple[float|None,str,dict]:
    ocf=comparable_pair(facts,'operating_cash_flow',cutoff,decision,fcf=True)
    capex=comparable_pair(facts,'capex',cutoff,decision,fcf=True)
    if ocf is None or capex is None:return None,'MISSING_COMPARABLE',{}
    if (ocf.current.start,ocf.current.end,ocf.family)!=(capex.current.start,capex.current.end,capex.family) or (ocf.prior.start,ocf.prior.end,ocf.family)!=(capex.prior.start,capex.prior.end,capex.family):return None,'DURATION_MISMATCH',{}
    current=ocf.current.value-capex.current.value;prior=ocf.prior.value-capex.prior.value
    value,status=growth_rate(current,prior)
    return value,('NEGATIVE_FCF_TO_POSITIVE' if status=='LOSS_TO_PROFIT' else 'POSITIVE_FCF_TO_NEGATIVE' if status=='PROFIT_TO_LOSS' else status),{'current_accession':ocf.current.accession,'prior_accession':ocf.prior.accession,'family':ocf.family}

def growth_rows_scores(rows:list[dict],features:list[str])->list[dict]:
    out=[dict(r) for r in rows]
    for field in features:
        vals=np.asarray([r[field] for r in out if r.get(field) is not None],dtype=float)
        if not len(vals):continue
        lo,hi=np.quantile(vals,[.025,.975]); clipped=[None if r.get(field) is None else min(max(r[field],lo),hi) for r in out]
        order=sorted((v,i) for i,v in enumerate(clipped) if v is not None);n=len(order);p=0
        while p<n:
            q=p+1
            while q<n and order[q][0]==order[p][0]:q+=1
            rank=((p+q-1)/2)/(n-1) if n>1 else .5
            for _,i in order[p:q]:out[i][field+'_rank']=rank
            p=q
    for r in out:
        ranks=[r.get(f+'_rank') for f in features if r.get(f+'_rank') is not None]
        r['growth_score']=sum(ranks)/len(ranks) if len(ranks)>=2 else None
    return out

def quality_features(rows:list[dict],dates:list[str],features:list[str])->tuple[list[str],dict]:
    report={}
    for f in features:
        counts=[sum(r.get(f) is not None for r in rows if r['decision_date']==d) for d in dates]
        total=sum(counts); coverage=total/len(rows) if rows else 0
        report[f]={'count':total,'coverage':coverage,'median_per_date':float(np.median(counts)) if counts else 0,'passed':coverage>=.40 and np.median(counts)>=10}
    return [f for f in features if report[f]['passed']],report
