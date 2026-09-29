# Pre-freeze QC #2: episode counts per candidate definition. Feature values only; no forward returns.
import sys, json, numpy as np
sys.path.insert(0,'backend')
from app.crypto.research import dataset, features as F
from app.crypto.research.long_horizon import ms, SAMPLE_START, FOLD_STARTS, END
g=dataset.load(); ts=g['ts']; n=len(ts); n_days=n//F.DAY_BARS
feats=F.compute_features(g)
e3=feats['fund_premium']; a1=feats['trend_ret_60']; b3=feats['mom_ret_15']
bE3=F.bucketize(e3,n_days); bA1=F.bucketize(a1,n_days); bB3=F.bucketize(b3,n_days)
lnOI=np.log(g['oi']); oi1h=lnOI-np.concatenate([np.full(60,np.nan),lnOI[:-60]])
bOI=F.bucketize(oi1h,n_days)
bounds=np.array([ms(SAMPLE_START)]+[ms(d) for d in FOLD_STARTS]+[ms(END)])
f1=(ts>=bounds[0])&(ts<bounds[1]); reg=F.regime_labels(g,f1); vol=reg['vol']
win=(ts>=ms(SAMPLE_START))&(ts<ms(END))
valid=win&(bE3>=0)&(bA1>=0)&(bOI>=0)&(vol>=0)
per=np.searchsorted(bounds,ts,side='right')-1
yr=(ts//1000).astype('datetime64[s]').astype('datetime64[Y]').astype(int)+1970
D1=(bE3==0)          # basis discount decile
D2=(bE3<=1)          # bottom 30%
U =(oi1h<0)          # OI contraction
Uq=(bOI==0)          # OI contraction decile
X =(bA1==0)          # 1h drop decile
X2=(bA1<=1)
VH=(vol==0); VnL=(vol!=2)
defs={
 'C1(D5.2 as-is): D1&U&HIGHvol': D1&U&VH,
 'A1: D1&U&notLOWvol': D1&U&VnL,
 'A2: D1&U (no vol gate)': D1&U,
 'A3: D2&U&notLOW': D2&U&VnL,
 'A4: D1&Uq&notLOW': D1&Uq&VnL,
 'B1: D1&X&notLOW': D1&X&VnL,
 'B2: D1&(U|X)&notLOW': D1&(U|X)&VnL,
 'B3: D2&X2&U&notLOW': D2&X2&U&VnL,
 'C: D1 only': D1,
}
def episodes(m, hold=240, cool=60):
    idx=np.where(m)[0]; out=[]; nxt=-1
    for i in idx:
        if i>=nxt: out.append(i); nxt=i+1+hold+cool
    return np.array(out,dtype=int)
rows=[]
for name,m in defs.items():
    m=m&valid
    ep=episodes(m); epo=ep[(per[ep]>=1)&(per[ep]<=9)]
    byf={k:int((per[epo]==k).sum()) for k in range(1,10)}
    byy={int(k):int((yr[epo]==k).sum()) for k in np.unique(yr[epo])}
    judge=sum(1 for v in byf.values() if v>=20)
    rows.append((name,len(ep),len(epo),min(byf.values()),judge,byf,byy,int(len(np.unique(ts[epo]//86400000)))))
    print(f"{name:34s} ep_all {len(ep):5d} OOS {len(epo):5d} min_fold {min(byf.values()):3d} folds>=20 {judge}/9 days {rows[-1][-1]:4d}")
    print("    by fold",byf)
    print("    by year",byy)
json.dump([{ 'name':r[0],'ep_all':r[1],'ep_oos':r[2],'min_fold':r[3],'folds_ge20':r[4],'by_fold':r[5],'by_year':r[6],'days':r[7]} for r in rows], open('data/runtime/crypto/d6/qc2.json','w'), indent=1)
