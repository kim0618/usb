# Pre-freeze QC #3: candidate score rule -> episode counts + stop-distance distribution.
# Feature values and volatility only. NO forward returns, NO PnL.
import sys, json, numpy as np
sys.path.insert(0,'backend')
from app.crypto.research import dataset, features as F
from app.crypto.research.long_horizon import ms, SAMPLE_START, FOLD_STARTS, END
g=dataset.load(); ts=g['ts']; n=len(ts); n_days=n//F.DAY_BARS
feats=F.compute_features(g)
bE3=F.bucketize(feats['fund_premium'],n_days); bA1=F.bucketize(feats['trend_ret_60'],n_days)
lnOI=np.log(g['oi']); oi1h=lnOI-np.concatenate([np.full(60,np.nan),lnOI[:-60]]); bOI=F.bucketize(oi1h,n_days)
bounds=np.array([ms(SAMPLE_START)]+[ms(d) for d in FOLD_STARTS]+[ms(END)])
f1=(ts>=bounds[0])&(ts<bounds[1]); reg=F.regime_labels(g,f1); vol=reg['vol']
rv=F._rstd(np.log(g['close'])-np.concatenate([[np.nan],np.log(g['close'])[:-1]]),F.DAY_BARS)
win=(ts>=ms(SAMPLE_START))&(ts<ms(END))
valid=win&(bE3>=0)&(bA1>=0)&(bOI>=0)&(vol>=0)&np.isfinite(oi1h)&np.isfinite(rv)
MS=np.where(bE3==0,25,np.where(bE3==1,12,0))                      # basis discount
PO=np.where(bOI==0,25,np.where(oi1h<0,12,0))                      # OI contraction
VE=np.where(vol==0,25,np.where(vol==1,12,0))                      # volatility state
PX=np.where(bA1==0,25,np.where(bA1==1,12,0))                      # 1h price drop
score=MS+PO+VE+PX
mand=(MS>=12)&(VE>=12)
per=np.searchsorted(bounds,ts,side='right')-1
yr=(ts//1000).astype('datetime64[s]').astype('datetime64[Y]').astype(int)+1970
def episodes(m,hold=240,cool=60):
    idx=np.where(m)[0]; out=[]; nxt=-1
    for i in idx:
        if i>=nxt: out.append(i); nxt=i+1+hold+cool
    return np.array(out,dtype=int)
res={}
for th in (37,50,62,75):
    m=valid&mand&(score>=th)
    ep=episodes(m); epo=ep[(per[ep]>=1)&(per[ep]<=9)]
    byf={k:int((per[epo]==k).sum()) for k in range(1,10)}
    byy={int(k):int((yr[epo]==k).sum()) for k in np.unique(yr[epo])}
    res[th]={'ep_all':len(ep),'ep_oos':len(epo),'by_fold':byf,'by_year':byy,'min_fold':min(byf.values()),
             'folds_ge20':sum(1 for v in byf.values() if v>=20),'days':int(len(np.unique(ts[epo]//86400000))),
             'bars_frac':float(m[valid].mean())}
    print(f"threshold {th}: all {len(ep)} OOS {len(epo)} minfold {res[th]['min_fold']} folds>=20 {res[th]['folds_ge20']}/9 days {res[th]['days']} barfrac {res[th]['bars_frac']:.3%}")
    print("   fold",byf); print("   year",byy)
# stop distance distribution (volatility only)
m=valid&mand&(score>=50); e=episodes(m); e=e[(per[e]>=1)&(per[e]<=9)]
s4=rv[e]*np.sqrt(240)
for k in (2.0,2.5,3.0):
    d=k*s4; print(f"stop k={k}: distance pct p05 {np.percentile(d,5)*100:.2f} p50 {np.percentile(d,50)*100:.2f} p95 {np.percentile(d,95)*100:.2f} max {d.max()*100:.2f}")
print("sigma_4h at entry: p05 %.4f p50 %.4f p95 %.4f"%tuple(np.percentile(s4,[5,50,95])))
# notional from 0.5% risk budget at k=2.5
d=2.5*s4; notf=0.005/d
print("notional/equity (0.5%% risk, k=2.5): p05 %.3f p50 %.3f p95 %.3f  capped@1.0 share %.1f%%"%(*np.percentile(notf,[5,50,95]),100*(notf>1).mean()))
res['stop']={'sigma4h_pct':[float(x) for x in np.percentile(s4,[5,50,95])*100],
             'k2.5_dist_pct':[float(x) for x in np.percentile(2.5*s4,[5,50,95])*100],
             'notional_over_equity':[float(x) for x in np.percentile(notf,[5,50,95])],
             'capped_share':float((notf>1).mean())}
json.dump(res,open('data/runtime/crypto/d6/qc3.json','w'),indent=1)
