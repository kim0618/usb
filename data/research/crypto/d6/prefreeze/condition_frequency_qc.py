# D6-A pre-freeze QC: feature values only. Forward returns / PnL: NOT computed.
import sys, json, numpy as np
sys.path.insert(0,'backend')
from app.crypto.research import dataset, features as F
from app.crypto.research.long_horizon import ms, SAMPLE_START, FOLD_STARTS, END
g=dataset.load(); ts=g['ts']; n=len(ts); n_days=n//F.DAY_BARS
feats=F.compute_features(g)
e3=feats['fund_premium']; a1=feats['trend_ret_60']
bE3=F.bucketize(e3,n_days); bA1=F.bucketize(a1,n_days)
lnOI=np.log(g['oi']); oi1h=lnOI-np.concatenate([np.full(60,np.nan),lnOI[:-60]])
bounds=np.array([ms(SAMPLE_START)]+[ms(d) for d in FOLD_STARTS]+[ms(END)])
f1=(ts>=bounds[0])&(ts<bounds[1])
reg=F.regime_labels(g,f1); vol=reg['vol']
print('vol cutoffs',reg['vol_cutoffs'])
win=(ts>=ms(SAMPLE_START))&(ts<ms(END))
D=(bE3==0); U=(oi1h<0); X=(bA1==0); V=(vol==0)
valid=win&(bE3>=0)&(bA1>=0)&np.isfinite(oi1h)&(vol>=0)
score=25*(D.astype(int)+U+X+V)
entry=valid&V&D&(U|X)
c1=valid&D&U&V
print('valid frac',valid[win].mean())
for name,m in [('D',D),('U',U),('X',X),('V',V),('entry V&D&(U|X)',entry),('C1-like D&U&V',c1),('D&U&X (no V)',valid&D&U&X&~V),('D&X&V (no U)',valid&D&X&V&~U),('all4',valid&D&U&X&V)]:
    print(f'{name:18s} frac of valid bars {m[valid].mean():.4%}  bars {int(m[valid].sum())}')
def episodes(m, hold=240, cool=60):
    idx=np.where(m)[0]; out=[]; nxt=-1
    for i in idx:
        if i>=nxt: out.append(i); nxt=i+1+hold+cool
    return np.array(out)
yr=(ts//1000).astype('datetime64[s]').astype('datetime64[Y]').astype(int)+1970
res={}
for name,m in [('entry',entry),('c1',c1)]:
    ep=episodes(m); y=yr[ep]; per=np.searchsorted(bounds,ts[ep],side='right')-1
    res[name]={'total':len(ep),'by_year':{int(k):int((y==k).sum()) for k in np.unique(y)},'by_period':{int(k):int((per==k).sum()) for k in np.unique(per)},
               'distinct_days':int(len(np.unique(ts[ep]//86400000)))}
    print(name,res[name])
# redundancy (feature-feature spearman on hourly sample within valid)
def spearmanr(a,b,nan_policy=None):
    m=np.isfinite(a)&np.isfinite(b); ra=np.argsort(np.argsort(a[m])); rb=np.argsort(np.argsort(b[m]))
    class R: pass
    r=R(); r.correlation=np.corrcoef(ra,rb)[0,1]; return r
s=np.where(valid)[0][::60]
rv=F._rstd(np.log(g['close'])-np.log(np.concatenate([[np.nan],g['close'][:-1]])),F.DAY_BARS)
M={'E3':e3[s],'A1':a1[s],'OI1h':oi1h[s],'RV24h':rv[s]}
ks=list(M)
for i in range(len(ks)):
    for j in range(i+1,len(ks)):
        print('spearman',ks[i],ks[j],round(spearmanr(M[ks[i]],M[ks[j]],nan_policy='omit').correlation,3))
# conditional co-occurrence
for a,b,na,nb in [(D,X,'D','X'),(D,V,'D','V'),(D,U,'D','U')]:
    print(f'P({nb}|{na})={b[valid&a].mean():.3f}  P({nb})={b[valid].mean():.3f}')
json.dump(res,open('data/runtime/crypto/d6/prefreeze_qc.json','w'))
