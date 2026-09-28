"""Frozen H-PV1 two-year Value prevalidation primitives."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable, Sequence
import numpy as np
from app.backtest.strategy_h0.facts import CanonicalFact, FIELD_SPECS

ANNUAL_MIN_DAYS=300
ANNUAL_MAX_DAYS=400
BASE_COST=0.001
STRESS_COST=0.002

@dataclass(frozen=True)
class ValueInputs:
    market_cap: float|None
    net_income: float|None
    operating_cash_flow: float|None
    capex: float|None
    revenue: float|None

def annual_fact(facts: Iterable[CanonicalFact], field: str, cutoff: datetime, decision: date) -> CanonicalFact|None:
    spec=FIELD_SPECS[field]
    rows=[f for f in facts if f.field==field and f.form in {'10-K','10-K/A'} and f.start is not None
          and ANNUAL_MIN_DAYS <= (f.end-f.start).days <= ANNUAL_MAX_DAYS
          and f.end<=decision and f.accepted_at<=cutoff]
    if not rows:return None
    end=max(f.end for f in rows); rows=[f for f in rows if f.end==end]
    priority=min(spec.tags.index(f.tag) for f in rows); rows=[f for f in rows if spec.tags.index(f.tag)==priority]
    accepted=max(f.accepted_at for f in rows); rows=[f for f in rows if f.accepted_at==accepted]
    accession=max(f.accession for f in rows); rows=[f for f in rows if f.accession==accession]
    values={f.value for f in rows}
    return rows[0] if len(values)==1 else None

def raw_features(x: ValueInputs) -> dict[str,float|None]:
    cap=x.market_cap
    if cap is None or not math.isfinite(cap) or cap<=0:
        return {'earnings_yield':None,'fcf_yield':None,'sales_yield':None}
    earnings=x.net_income/cap if x.net_income is not None and x.net_income>0 else None
    fcf=None
    if x.operating_cash_flow is not None and x.capex is not None:
        value=x.operating_cash_flow-x.capex
        fcf=value/cap if value>0 else None
    sales=x.revenue/cap if x.revenue is not None and x.revenue>0 else None
    return {'earnings_yield':earnings,'fcf_yield':fcf,'sales_yield':sales}

def percentile_scores(rows: list[dict]) -> list[dict]:
    out=[dict(r) for r in rows]
    for field in ('earnings_yield','fcf_yield','sales_yield'):
        vals=np.array([r[field] for r in out if r.get(field) is not None],dtype=float)
        if not len(vals):continue
        lo,hi=np.quantile(vals,[.025,.975])
        clipped=[min(max(r[field],lo),hi) if r.get(field) is not None else None for r in out]
        present=sorted((v,i) for i,v in enumerate(clipped) if v is not None)
        n=len(present); pos=0
        while pos<n:
            end=pos+1
            while end<n and present[end][0]==present[pos][0]:end+=1
            pct=((pos+end-1)/2)/(n-1) if n>1 else .5
            for _,i in present[pos:end]:out[i][field+'_rank']=pct
            pos=end
    for r in out:
        ranks=[r.get(f+'_rank') for f in ('earnings_yield','fcf_yield','sales_yield') if r.get(f+'_rank') is not None]
        r['value_score']=sum(ranks)/len(ranks) if len(ranks)>=2 else None
    return out

def assign_deciles(rows:list[dict]) -> list[dict]:
    valid=[dict(r) for r in rows if r.get('value_score') is not None]
    valid.sort(key=lambda r:(r['value_score'],r['security_id']))
    if len(valid)<20:return []
    for decile,idxs in enumerate(np.array_split(np.arange(len(valid)),10),1):
        for i in idxs:valid[int(i)]['decile']=decile
    return valid

def forward_label(prices: Sequence[float|None], index:int, horizon:int) -> float|None:
    target=index+horizon
    if target>=len(prices) or prices[index] is None or prices[target] is None or prices[index]<=0:return None
    return prices[target]/prices[index]-1

def apply_cost(value:float|None,bp:int,legs:int=1)->float|None:
    return None if value is None else value-bp/10000*legs

def spearman(xs:Sequence[float],ys:Sequence[float])->float|None:
    if len(xs)<2:return None
    def ranks(values):
        order=sorted(range(len(values)),key=lambda i:values[i]); result=[0.0]*len(values); p=0
        while p<len(order):
            q=p+1
            while q<len(order) and values[order[q]]==values[order[p]]:q+=1
            rank=(p+q-1)/2
            for j in order[p:q]:result[j]=rank
            p=q
        return result
    a=np.asarray(ranks(xs)); b=np.asarray(ranks(ys))
    if np.std(a)==0 or np.std(b)==0:return None
    return float(np.corrcoef(a,b)[0,1])

def positive_concentration(rows:Sequence[dict],field:str='net_excess_63')->dict[str,float|None]:
    by=defaultdict(float)
    for r in rows:
        if r.get(field) is not None and r[field]>0:by[r['security_id']]+=r[field]
    values=sorted(by.values(),reverse=True); total=sum(values)
    if total<=0:return {'single':None,'top5':None,'top10':None}
    return {'single':values[0]/total,'top5':sum(values[:5])/total,'top10':sum(values[:10])/total}


def assert_unique_security_dates(rows: Sequence[dict]) -> None:
    keys=[(r['decision_date'],r['security_id']) for r in rows]
    if len(keys)!=len(set(keys)):
        raise ValueError('duplicate security/date')

def aligned_excess(asset_prices: Sequence[float|None], benchmark_prices: Sequence[float|None], index: int, horizon: int) -> float|None:
    asset=forward_label(asset_prices,index,horizon); benchmark=forward_label(benchmark_prices,index,horizon)
    return None if asset is None or benchmark is None else asset-benchmark

def remove_top_observations(rows: Sequence[dict], field: str, count: int) -> list[dict]:
    if count<0: raise ValueError('count must be non-negative')
    ordered=sorted(rows,key=lambda r:(r[field],r.get('security_id','')),reverse=True)
    removed={id(r) for r in ordered[:count]}
    return [r for r in rows if id(r) not in removed]
