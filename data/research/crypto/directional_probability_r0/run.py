"""Isolated, frozen R0 research. Run from repository root with PYTHONPATH=backend."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from app.crypto.research import dataset
from app.crypto.research.btc_p1 import windows as W, models as M, metrics as MT
from app.crypto.research.btc_p2 import external

OUT = Path(__file__).resolve().parent
DOC = Path('docs/crypto/directional_probability_r0')
MIN = 60_000
DAY = 1440 * MIN
HORIZONS = (30, 60, 120, 240)
STRESS = 2.525318158974054
NAMES = ['ret_z_5m','ret_z_30m','ret_z_60m','ret_z_240m','range_position_4h',
         'log_rv_1h','log_rv_24h','rv_ratio','oi_1h','oi_4h','oi_24h','funding_bp',
         'basis_bp','basis_chg_1h','perp_spot_bp','perp_spot_chg_1h',
         'bybit_binance_ret_z_1h','spot_perp_ret_z_1h','hour_sin','hour_cos']


def ms(s: str) -> int:
    return int(pd.Timestamp(s, tz='UTC').timestamp()*1000)


def features(g: dict, e: dict, rows: np.ndarray) -> np.ndarray:
    c = np.log(g['close'])
    r = np.r_[0., np.diff(c)]
    rv = W.rolling_std(r, 1440)*np.sqrt(1440)*1e4
    rv1 = W.rolling_std(r, 60)*np.sqrt(60)*1e4
    out = [(c-W.shift(c,h))*1e4/np.maximum(rv,1e-9) for h in (5,30,60,240)]
    hi,lo = W.rolling_max(g['high'],240),W.rolling_min(g['low'],240)
    out += [(g['close']-lo)/np.maximum(hi-lo,1e-9),np.log(np.maximum(rv1,1e-9)),
            np.log(np.maximum(rv,1e-9)),rv1/np.maximum(rv,1e-9)]
    oi = np.log(g['oi'])
    out += [oi-W.shift(oi,h) for h in (60,240,1440)]
    b = (g['mark_close']/g['index_close']-1)*1e4
    p = (e['um_close']/e['spot_close']-1)*1e4
    um,spot = np.log(e['um_close']),np.log(e['spot_close'])
    out += [g['funding_last']*1e4,b,b-W.shift(b,60),p,p-W.shift(p,60),
            ((c-W.shift(c,60))-(um-W.shift(um,60)))*1e4/np.maximum(rv,1e-9),
            ((spot-W.shift(spot,60))-(um-W.shift(um,60)))*1e4/np.maximum(rv,1e-9)]
    hour = ((g['ts']+MIN)//MIN%1440)/1440
    out += [np.sin(2*np.pi*hour),np.cos(2*np.pi*hour)]
    assert len(out)==len(NAMES)
    return np.column_stack([v[rows] for v in out])


def sides(p: np.ndarray) -> tuple[np.ndarray,np.ndarray]:
    # Same round-half-even tick rule as Decimal SyntheticBook for archived 0.1-tick prices.
    bid = np.round(np.round((p-.05)/.1,8))*.1
    ask = np.round(np.round((p+.05)/.1,8))*.1
    return bid,np.maximum(ask,bid+.1)


def outcomes(g: dict, rows: np.ndarray, h: int) -> dict:
    entry,exit_ = g['open'][rows+1],g['open'][rows+1+h]
    u = np.maximum(0,(W.forward_max(g['high'],h)[rows]/entry-1)*1e4)
    d = np.maximum(0,(1-W.forward_min(g['low'],h)[rows]/entry)*1e4)
    eb,ea = sides(entry); xb,xa = sides(exit_)
    gross = (exit_/entry-1)*1e4
    # D6-C/E1 historical accounting uses mark_close of the settlement-stamped bar.
    # This is realized-label accounting only, never a PIT feature.
    fi = np.searchsorted(g['ts'],g['funding_ts'])
    fv = g['funding_rate']*g['mark_close'][np.clip(fi,0,len(g['ts'])-1)]
    cf = np.r_[0.,np.cumsum(fv)]
    st = g['ts'][rows+1]; en = g['ts'][rows+1+h]
    fund = (cf[np.searchsorted(g['funding_ts'],en,side='right')]-
            cf[np.searchsorted(g['funding_ts'],st,side='right')])/entry*1e4
    netl = (xb-ea)/entry*1e4-.00055*(ea+xb)/entry*1e4-fund
    nets = (eb-xa)/entry*1e4-.00055*(eb+xa)/entry*1e4+fund
    costl,costs = gross-netl,-gross-nets
    expected_cost = 11+.2/entry*1e4+np.abs(g['funding_last'][rows])*1e4*h/480+STRESS
    return dict(u=u,d=d,gross=gross,net_LONG=netl,net_SHORT=nets,cost_LONG=costl,
                cost_SHORT=costs,y_LONG=(u-d>np.maximum(0,costl)).astype(float),
                y_SHORT=(d-u>np.maximum(0,costs)).astype(float),expected_cost=expected_cost,
                entry=entry,reference_gap_bp=(entry/g['close'][rows]-1)*1e4)


def c1_labels(g: dict,e: dict) -> tuple[np.ndarray,dict]:
    from app.crypto.research import derivatives_flow as DF, features as F, long_horizon as LH
    from app.crypto.research.c1_exit import e1
    B = DF.grid5()
    by = DF.carry_one(DF.close5_from_1m(g['ts'],g['close'],B))
    # Exclude cache-carried observations before reproducing original archive semantics.
    ok = e['spot_age_minutes']==0
    spot = DF.carry_one(DF.close5_from_1m(e['ts'][ok],e['spot_close'][ok],B))
    s1 = DF.to_1m(np.log(by)-np.log(spot),B,g['ts'])
    bounds=np.array([LH.ms(LH.SAMPLE_START)]+[LH.ms(x) for x in LH.FOLD_STARTS]+[LH.ms(LH.END)])
    reg=F.regime_labels(g,(g['ts']>=bounds[0])&(g['ts']<bounds[1]))
    c1=(F.bucketize(s1,len(g['ts'])//1440)==0)&(np.log(g['oi']/F._lag(g['oi'],60))<0)&(reg['vol']==0)
    parity=e1.parity(g,dict(c1=c1,bounds=bounds,vol_cutoffs=reg['vol_cutoffs']))
    assert parity['pass'],parity
    return c1,parity


def distribution(xfit: np.ndarray,yfit: np.ndarray,xcal: np.ndarray,ycal: np.ndarray,
                 xtest: np.ndarray,volfit: np.ndarray,volcal: np.ndarray,voltest: np.ndarray) -> dict:
    scaler=M.RobustScaler().fit(xfit)
    def design(x): return np.column_stack([np.ones(len(x)),scaler.transform(x)])
    a=design(xfit); penalty=np.eye(a.shape[1])*10;penalty[0,0]=0
    coef=np.linalg.solve(a.T@a+penalty,a.T@np.log1p(yfit))
    cal=design(xcal)@coef; pred=design(xtest)@coef
    cuts=np.quantile(volfit,[1/3,2/3])
    gc=np.searchsorted(cuts,volcal);gt=np.searchsorted(cuts,voltest)
    out={k:np.zeros(len(xtest)) for k in ('q50','q80','p40','p80','mean')}
    for group in range(3):
        residual=np.sort(np.log1p(ycal[gc==group])-cal[gc==group])
        assert len(residual)>100
        take=gt==group; z=pred[take]
        for q in (.5,.8):out[f'q{int(q*100)}'][take]=np.maximum(0,np.expm1(z+np.quantile(residual,q)))
        for threshold in (40,80):
            out[f'p{threshold}'][take]=1-np.searchsorted(residual,np.log1p(threshold)-z,side='left')/len(residual)
        # Exact empirical expectation after flooring exp(z+residual)-1 at zero.
        exp_res=np.exp(residual);cum=np.r_[0.,np.cumsum(exp_res)]
        k=np.searchsorted(residual,-z,side='right')
        out['mean'][take]=(np.exp(z)*(cum[-1]-cum[k])-(len(residual)-k))/len(residual)
    out['base_q50']=np.full(len(xtest),np.quantile(ycal,.5))
    out['base_q80']=np.full(len(xtest),np.quantile(ycal,.8))
    out['base_p40']=np.full(len(xtest),(ycal>=40).mean())
    out['base_p80']=np.full(len(xtest),(ycal>=80).mean())
    return out


def ci(frame: pd.DataFrame,values: dict[str,np.ndarray]) -> dict:
    if not len(frame):return {}
    # Resample complete calendar-week clusters independently within each test fold.
    # Monday-start UTC calendar weeks (epoch 1970-01-01 was Thursday).
    keys=pd.MultiIndex.from_arrays([frame['fold'].to_numpy(),(frame.ts.to_numpy()//DAY+3)//7])
    codes,unique=pd.factorize(keys,sort=True)
    n=np.bincount(codes).astype(float)
    sums=np.column_stack([np.bincount(codes,weights=np.asarray(v,float)) for v in values.values()])
    rng=np.random.default_rng(20261003); draws=np.zeros((2000,len(values)));den=np.zeros(2000)
    folds=np.array([k[0] for k in unique])
    for fold in np.unique(folds):
        ids=np.flatnonzero(folds==fold)
        pick=ids[rng.integers(0,len(ids),(2000,len(ids)))]
        draws+=sums[pick].sum(axis=1);den+=n[pick].sum(axis=1)
    draws/=den[:,None]
    effective=len(unique)-len(np.unique(folds))
    reliable=len(unique)>=20 and effective>=10
    result={'clusters':len(unique),'n':len(frame),'effective_resampling_clusters':effective,
            'ci_status':'ESTIMABLE' if reliable else 'INSUFFICIENT_CLUSTERS'}
    for j,name in enumerate(values):
        result[name]={'mean':float(np.mean(values[name])),
                     'ci95':np.quantile(draws[:,j],[.025,.975]).tolist() if reliable else [None,None],
                     'ci9875':np.quantile(draws[:,j],[.00625,.99375]).tolist() if reliable else [None,None]}
    if 'positive_pnl' in values and 'negative_pnl' in values:
        names=list(values);a=names.index('positive_pnl');b=names.index('negative_pnl')
        ok=reliable and np.all(draws[:,b]>0)
        ratio=draws[:,a]/np.maximum(draws[:,b],1e-20)
        result['pf']={'ci95':np.quantile(ratio,[.025,.975]).tolist() if ok else [None,None]}
    return result


def economics(d: pd.DataFrame,side: str,bootstrap: bool=True) -> dict:
    if len(d)==0:return {'n':0}
    net=d[f'net_{side}'].to_numpy();gross=d.gross.to_numpy()*(1 if side=='LONG' else -1)
    mfe=d.u.to_numpy() if side=='LONG' else d.d.to_numpy()
    mae=d.d.to_numpy() if side=='LONG' else d.u.to_numpy()
    out=dict(n=len(d),net_bp=float(net.mean()),stress_net_bp=float(net.mean()-STRESS),
             gross_bp=float(gross.mean()),win_rate=float((net>0).mean()),
             pf=float(net[net>0].sum()/-net[net<0].sum()) if (net<0).any() else None,
             pf_status='FINITE' if (net<0).any() else 'UNDEFINED_NO_LOSSES',
             mean_mfe_bp=float(mfe.mean()),mean_mae_bp=float(mae.mean()),
             cost_bp=float((gross-net).mean()))
    if bootstrap:
        vals={'net_bp':net,'stress_net_bp':net-STRESS,'win_rate':(net>0).astype(float),
              'positive_pnl':np.maximum(net,0),'negative_pnl':np.maximum(-net,0)}
        out['cluster_ci']=ci(d,vals)
    return out


def probability(d: pd.DataFrame,side: str,bootstrap: bool=True) -> dict:
    y=d[f'y_{side}'].to_numpy();p=d[f'p_{side}'].to_numpy();b=d[f'base_{side}'].to_numpy()
    bins=MT.reliability(y,p,10)
    populated=[r for r in bins if r['n']>=500]
    ranks=pd.Series([r['actual_rate'] for r in populated]).rank().to_numpy()
    rho=float(np.corrcoef(np.arange(len(ranks)),ranks)[0,1]) if len(ranks)>1 and np.std(ranks)>0 else 0.
    out=dict(n=len(d),base_rate=float(y.mean()),mean_p=float(p.mean()),brier=MT.brier(y,p),
             bss=MT.brier_skill(y,p,b),auc=MT.roc_auc(y,p),ece=MT.expected_calibration_error(y,p,10),
             log_loss=MT.log_loss(y,p),bucket_spearman=rho,populated_buckets=len(populated),
             adjacent_violations=sum(populated[i]['actual_rate']>populated[i+1]['actual_rate'] for i in range(len(populated)-1)))
    if bootstrap:out['cluster_ci']=ci(d,{'brier_improvement':(y-b)**2-(y-p)**2,'calibration_gap':y-p})
    return out


def pinball(y,q,tau):
    err=y-q
    return np.maximum(tau*err,(tau-1)*err)


def targets_metrics(d:pd.DataFrame,side:str,bootstrap:bool=True)->dict:
    y=d.u.to_numpy() if side=='LONG' else d.d.to_numpy()
    out={}
    for quantile in (50,80):
        tau=quantile/100;q=d[f'{side}_q{quantile}'].to_numpy();base=d[f'{side}_base_q{quantile}'].to_numpy()
        loss=pinball(y,q,tau);bloss=pinball(y,base,tau);hit=(y>=q).astype(float)
        part={'mean_target_bp':float(q.mean()),'claimed_hit':1-tau,'actual_hit':float(hit.mean()),
              'pinball_bp':float(loss.mean()),'pinball_skill':float(1-loss.mean()/bloss.mean()),
              'below_forecast_cost_fraction':float((q<=d.expected_cost.to_numpy()).mean())}
        if bootstrap:part['cluster_ci']=ci(d,{'hit_rate':hit,'pinball_improvement':bloss-loss})
        out[f'q{quantile}']=part
    for threshold in (40,80):
        yhit=(y>=threshold).astype(float);p=d[f'{side}_p{threshold}'].to_numpy();b=d[f'{side}_base_p{threshold}'].to_numpy()
        out[f'fixed_{threshold}bp']={'mean_p':float(p.mean()),'actual_hit':float(yhit.mean()),
                                    'brier':MT.brier(yhit,p),'bss':MT.brier_skill(yhit,p,b),
                                    'ece':MT.expected_calibration_error(yhit,p,10),
                                    'curve':MT.reliability(yhit,p,10)}
    return out


def run()->None:
    assert hashlib.sha256((DOC/'CONTRACT.md').read_bytes()).hexdigest()==json.loads((OUT/'freeze.json').read_text())['sha256']
    print('Loading existing archive',flush=True)
    g=dataset.load();e=external.build()
    assert np.array_equal(g['ts'],e['ts']) and np.all(np.diff(g['ts'])==MIN)
    c1,parity=c1_labels(g,e)
    starts=np.flatnonzero(c1&~np.r_[False,c1[:-1]])
    grid=np.flatnonzero((g['ts']+MIN)%(5*MIN)==0)
    candidates=np.union1d(grid,starts)
    candidates=candidates[(g['ts'][candidates]+MIN>=ms('2021-03-04'))&(candidates+241<len(g['ts']))]
    x=features(g,e,candidates)
    age=np.maximum(e['um_age_minutes'],e['spot_age_minutes'])
    stale=W.rolling_max(age.astype(float),1440)[candidates]>5
    valid=np.isfinite(x).all(axis=1)&~stale
    rows=candidates[valid];x=x[valid];ts=g['ts'][rows]+MIN
    meta=dict(feature_names=NAMES,candidates=len(candidates),excluded=int((~valid).sum()),
              stale_excluded=int(stale.sum()),grid_rows=int(np.isin(rows,grid).sum()),
              c1_parity=parity,external_coverage=external.coverage(e),
              evidence='MODEL_OOS_NOT_UNTOUCHED_HOLDOUT',freeze=json.loads((OUT/'freeze.json').read_text()))
    # Truncation checks: only past arrays are supplied to the feature builder.
    trunc=[]
    for cut in (100000,1000000,2000000):
        r=rows[(rows<=cut)&(rows>=cut-60)]
        gt={k:v[:cut+1] for k,v in g.items() if len(v)==len(g['ts'])}
        et={k:v[:cut+1] for k,v in e.items()}
        short=features(gt,et,r);full=x[np.searchsorted(rows,r)]
        trunc.append(bool(np.allclose(short,full,rtol=1e-10,atol=1e-10)))
    assert all(trunc);meta['feature_truncation_pass']=trunc
    (OUT/'audit.json').write_text(json.dumps(meta,indent=2))
    boundaries=[ms(s) for s in ['2022-01-01','2022-07-01','2023-01-01','2023-07-01',
                                '2024-01-01','2024-07-01','2025-01-01','2025-07-01','2026-01-01','2026-09-23']]
    ys={h:outcomes(g,rows,h) for h in HORIZONS};splits=[]
    all_parts=[]
    for fold,(begin,end) in enumerate(zip(boundaries[:-1],boundaries[1:]),1):
        calstart=begin-90*DAY
        fit=(ts<calstart-240*MIN)&np.isin(rows,grid)
        cal=(ts>=calstart)&(ts<begin-240*MIN)&np.isin(rows,grid)
        test=(ts>=begin)&(ts<end-240*MIN)
        assert ts[fit].max()+240*MIN<ts[cal].min() and ts[cal].max()+240*MIN<ts[test].min()
        splits.append({'fold':fold,'fit_n':int(fit.sum()),'cal_n':int(cal.sum()),'test_grid_n':int((test&np.isin(rows,grid)).sum()),
                       'fit_last':int(ts[fit].max()),'cal_first':int(ts[cal].min()),'cal_last':int(ts[cal].max()),
                       'test_first':int(ts[test].min()),'test_last':int(ts[test].max())})
        for h in HORIZONS:
            start=time.monotonic();y=ys[h]
            frame=pd.DataFrame({'row':rows[test],'ts':ts[test],'fold':fold,'horizon':h,
                                'is_grid':np.isin(rows[test],grid),'c1':c1[rows[test]],
                                'c1_start':np.isin(rows[test],starts),'close':g['close'][rows[test]]})
            for k,v in y.items():frame[k]=v[test]
            for side,ky in [('LONG','u'),('SHORT','d')]:
                model=M.RidgeLogistic(l2=10).fit(x[fit],y[f'y_{side}'][fit])
                rawcal=model.predict(x[cal]);rawtest=model.predict(x[test])
                calibrator=M.RidgeLogistic(l2=1,scale_inputs=False).fit(M.logit(rawcal)[:,None],y[f'y_{side}'][cal])
                frame[f'p_{side}']=calibrator.predict(M.logit(rawtest)[:,None])
                frame[f'raw_{side}']=rawtest
                frame[f'base_{side}']=y[f'y_{side}'][cal].mean()
                dist=distribution(x[fit],y[ky][fit],x[cal],y[ky][cal],x[test],x[fit,6],x[cal,6],x[test,6])
                for k,v in dist.items():frame[f'{side}_{k}']=v
                for q in (50,80):frame[f'{side}_price_q{q}']=frame.close*(1+(1 if side=='LONG' else -1)*dist[f'q{q}']/1e4)
            for side,other in [('LONG','SHORT'),('SHORT','LONG')]:
                frame[f'edge_{side}']=(frame[f'p_{side}']>=.6)&(frame[f'{side}_mean']-frame[f'{other}_mean']>frame.expected_cost)
            frame.to_parquet(OUT/f'predictions_fold{fold}_h{h}.parquet',index=False)
            all_parts.append(frame)
            print(f'fold={fold} horizon={h} rows={len(frame)} seconds={time.monotonic()-start:.1f}',flush=True)
    (OUT/'splits.json').write_text(json.dumps(splits,indent=2))
    all_=pd.concat(all_parts,ignore_index=True)
    summarize(all_)


def summarize(all_:pd.DataFrame)->None:
    results={};buckets=[];folds=[];years=[];target_ranges=[]
    for h in HORIZONS:
        d=all_[(all_.horizon==h)&all_.is_grid].copy();d['year']=pd.to_datetime(d.ts,unit='ms',utc=True).dt.year
        res={'n':len(d),'first':str(pd.to_datetime(d.ts.min(),unit='ms',utc=True)),
             'last':str(pd.to_datetime(d.ts.max(),unit='ms',utc=True)),
             'reference_gap_abs_bp':float(d.reference_gap_bp.abs().mean()),
             'probability_sum_over_one':int((d.p_LONG+d.p_SHORT>1).sum())}
        for side in ('LONG','SHORT'):
            p=probability(d,side);t=targets_metrics(d,side)
            non=d[d.ts%(240*MIN)==0];edge=d[d[f'edge_{side}']];ne=non[non[f'edge_{side}']]
            res[side]={'probability':p,'targets':t,'all_economics':economics(d,side),
                       'nonoverlap':{'probability':probability(non,side),'economics':economics(non,side)},
                       'edge':economics(edge,side),'edge_nonoverlap':economics(ne,side)}
            for f,part in d.groupby('fold'):
                fm=probability(part,side,False);tm=targets_metrics(part,side,False)
                folds.append(dict(horizon=h,side=side,fold=int(f),**fm,
                                  hit50=tm['q50']['actual_hit'],hit80=tm['q80']['actual_hit'],
                                  net_bp=float(part[f'net_{side}'].mean())))
            for year,part in d.groupby('year'):
                years.append(dict(horizon=h,side=side,year=int(year),**probability(part,side,False),
                                  net_bp=float(part[f'net_{side}'].mean())))
            for bucket in range(10):
                take=np.minimum((d[f'p_{side}']*10).astype(int),9)==bucket;part=d[take]
                if not len(part):continue
                econ=economics(part,side);cl=ci(part,{'actual':part[f'y_{side}'].to_numpy()})
                buckets.append(dict(horizon=h,side=side,bucket=f'{bucket/10:.1f}-{(bucket+1)/10:.1f}',
                                    mean_p=float(part[f'p_{side}'].mean()),actual=float(part[f'y_{side}'].mean()),
                                    actual_ci_low=cl['actual']['ci95'][0],actual_ci_high=cl['actual']['ci95'][1],
                                    **{k:v for k,v in econ.items() if k!='cluster_ci'},
                                    net_ci_low=econ['cluster_ci']['net_bp']['ci95'][0],
                                    net_ci_high=econ['cluster_ci']['net_bp']['ci95'][1]))
            for q in (50,80):
                for lo,hi in zip((0,20,40,80,160),(20,40,80,160,np.inf)):
                    part=d[(d[f'{side}_q{q}']>=lo)&(d[f'{side}_q{q}']<hi)]
                    if not len(part):continue
                    actual=part.u if side=='LONG' else part.d
                    target_ranges.append(dict(horizon=h,side=side,q=q,low=lo,high=hi,n=len(part),
                                              predicted_bp=float(part[f'{side}_q{q}'].mean()),
                                              actual_mfe_bp=float(actual.mean()),
                                              hit=float((actual>=part[f'{side}_q{q}']).mean())))
        c=all_[(all_.horizon==h)&all_.c1_start]
        state=d[d.c1]
        res['c1']={'episode_n':len(c),'state_grid_n':len(state),
                   'episode_long_mean':float(c.p_LONG.mean()),'episode_long_quantiles':c.p_LONG.quantile([.1,.5,.9]).to_dict(),
                   'state_long_mean':float(state.p_LONG.mean()),
                   'episode_probability':probability(c,'LONG'),
                   'episode_economics':economics(c,'LONG'),
                   'episode_p60_fraction':float((c.p_LONG>=.6).mean())}
        results[str(h)]=res
    fd=pd.DataFrame(folds);yd=pd.DataFrame(years)
    usable=[];weak=[]
    for h in HORIZONS:
        checks={}
        for side in ('LONG','SHORT'):
            r=results[str(h)][side];p=r['probability'];ff=fd[(fd.horizon==h)&(fd.side==side)];yy=yd[(yd.horizon==h)&(yd.side==side)]
            econ=r['edge_nonoverlap']
            checks[side]={'bss':p['bss']>.01,'brier_ci':p['cluster_ci']['brier_improvement']['ci9875'][0]>0,
                          'ece':p['ece']<=.05,'monotonic':p['bucket_spearman']>=.8 and p['populated_buckets']>=3,
                          'folds':int((ff.bss>0).sum())>=7,'years':int((yy.bss>0).sum())>=4,
                          'targets':all(abs(r['targets'][f'q{q}']['actual_hit']-(1-q/100))<=.05 and
                                        r['targets'][f'q{q}']['pinball_skill']>0 and
                                        int((abs(ff[f'hit{q}']-(1-q/100))<=.05).sum())>=7 for q in (50,80)),
                          'economics':econ['n']>=200 and econ['cluster_ci']['net_bp']['ci9875'][0] is not None and econ['cluster_ci']['net_bp']['ci9875'][0]>0}
        results[str(h)]['gates']=checks
        if all(all(c.values()) for c in checks.values()):usable.append(h)
        if all(results[str(h)][s]['probability']['bss']>0 and results[str(h)][s]['probability']['auc']>.52 and
               results[str(h)][s]['probability']['bucket_spearman']>0 for s in ('LONG','SHORT')):weak.append(h)
    payload={'verdict':'USABLE_FOR_FORWARD_MODEL' if usable else 'WEAK_DIRECTIONAL_MODEL' if weak else 'NO_DIRECTIONAL_MODEL',
             'usable_horizons':usable,'weak_horizons':weak,'horizons':results}
    (OUT/'results.json').write_text(json.dumps(payload,indent=2,allow_nan=False))
    pd.DataFrame(buckets).to_csv(OUT/'probability_buckets.csv',index=False)
    fd.to_csv(OUT/'fold_metrics.csv',index=False);yd.to_csv(OUT/'year_metrics.csv',index=False)
    pd.DataFrame(target_ranges).to_csv(OUT/'target_ranges.csv',index=False)
    all_[all_.c1_start].to_parquet(OUT/'c1_episode_predictions.parquet',index=False)
    print(payload['verdict'],flush=True)


if __name__=='__main__':
    run()
