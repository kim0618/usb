"""D5.4 pre-freeze QC: event frequency, feature distribution, coverage.
NO forward returns. NO PnL. NO trade simulation."""
import sys, json, collections
sys.path.insert(0,'backend')
import numpy as np
from app.crypto.research.d6c import data as D
from app.crypto.research.d6 import features as F

DAY=1440; MIN=60_000
g=D.load_inputs(); ts=g['ts']; n=len(ts)
ln=np.log(g['close']); lnoi=np.log(g['oi'])

def lag(x,k):
    out=np.full(len(x),np.nan); out[k:]=x[:-k]; return out

feat={}
feat['disp15']  = ln - lag(ln,15)
feat['oichg15'] = lnoi - lag(lnoi,15)
feat['basis']   = ln - np.log(g['index_close'])
rv15  = F._rstd(ln-lag(ln,1),15)
rv1440= F._rstd(ln-lag(ln,1),DAY)
with np.errstate(divide='ignore',invalid='ignore'):
    feat['volratio']= rv15/rv1440
for k in feat: feat[k]=F._clean(feat[k])

def robust_z(x, ts, window_days=30):
    """Daily PIT robust z: median and MAD from the previous 30 complete UTC days."""
    out=np.full(len(x),np.nan)
    d0=int(ts[0]//(DAY*MIN)); d1=int(ts[-1]//(DAY*MIN))
    for day in range(d0+window_days, d1+1):
        lo=int(np.searchsorted(ts,(day-window_days)*DAY*MIN)); hi=int(np.searchsorted(ts,day*DAY*MIN))
        nxt=int(np.searchsorted(ts,(day+1)*DAY*MIN))
        h=x[lo:hi]; h=h[np.isfinite(h)]
        if len(h) < window_days*DAY*0.5: continue
        med=np.median(h); mad=np.median(np.abs(h-med))
        if mad<=0: continue
        out[hi:nxt]=(x[hi:nxt]-med)/(1.4826*mad)
    return out

z={k:robust_z(feat[k],ts) for k in feat}
print("=== feature coverage (finite z fraction) ===")
for k in z: print(f"  {k:9s} {np.isfinite(z[k]).mean():.4f}")
print("=== |z| tail quantiles ===")
for k in z:
    v=z[k][np.isfinite(z[k])]
    print(f"  {k:9s} p0.1={np.percentile(v,0.1):+7.2f} p1={np.percentile(v,1):+6.2f} p50={np.percentile(v,50):+5.2f} "
          f"p99={np.percentile(v,99):+6.2f} p99.9={np.percentile(v,99.9):+7.2f} max={v.max():.1f}")

def episodes(mask, cooldown_bars):
    """Consecutive bars -> one event. Next event only after the run ends + cooldown."""
    idx=np.nonzero(mask)[0]
    if len(idx)==0: return np.array([],dtype=int), np.array([],dtype=int)
    starts=[]; ends=[]; i=0
    while i < len(idx):
        s=idx[i]; e=s
        while i+1 < len(idx) and idx[i+1]==idx[i]+1:
            i+=1; e=idx[i]
        starts.append(s); ends.append(e); i+=1
    starts=np.array(starts); ends=np.array(ends)
    keep=[]; nxt=-1
    for s,e in zip(starts,ends):
        if s>=nxt: keep.append((s,e)); nxt=e+1+cooldown_bars
    return np.array([k[0] for k in keep]), np.array([k[1] for k in keep])

yr=(ts//1000).astype('datetime64[s]').astype('datetime64[Y]').astype(int)+1970
FOLD=[1640995200000,1656633600000,1672531200000,1688169600000,1704067200000,
      1719792000000,1735689600000,1751328000000,1767225600000,1790121600000]
def report(name, mask, cooldown=240):
    valid=np.isfinite(z['disp15'])&np.isfinite(z['oichg15'])&np.isfinite(z['basis'])&np.isfinite(z['volratio'])
    s,e=episodes(mask&valid, cooldown)
    oos=s[(ts[s]>=FOLD[0])&(ts[s]<FOLD[-1])]
    byy=collections.Counter(yr[oos].tolist())
    fold=np.searchsorted(FOLD,ts[oos],side='right')
    byf=collections.Counter(fold.tolist())
    dur=(e[:len(s)]-s+1)
    print(f"  {name:38s} bars={int((mask&valid).sum()):7d} events={len(s):5d} OOS={len(oos):5d} "
          f"yrs={sorted(byy)} minyr={min(byy.values()) if byy else 0} folds={len([f for f in range(1,10) if byf.get(f,0)>=3])}/9 "
          f"med_dur={int(np.median(dur)) if len(dur) else 0}")
    return len(oos), byy

print("=== E1 EXTREME_DELEVERAGING (disp down + OI contraction) ===")
for Z in (2.5,3.0,3.5,4.0,5.0):
    report(f"E1 z_disp<=-{Z} & z_oi<=-{Z}", (z['disp15']<=-Z)&(z['oichg15']<=-Z))
print("=== E1 asymmetric OI threshold ===")
for Z,Zo in ((3.0,2.0),(4.0,2.0),(4.0,2.5),(5.0,2.0)):
    report(f"E1 z_disp<=-{Z} & z_oi<=-{Zo}", (z['disp15']<=-Z)&(z['oichg15']<=-Zo))
print("=== E1-SHORT (disp up + OI contraction) ===")
for Z,Zo in ((4.0,2.0),(5.0,2.0)):
    report(f"E1S z_disp>=+{Z} & z_oi<=-{Zo}", (z['disp15']>=Z)&(z['oichg15']<=-Zo))
print("=== E2 EXTREME_BASIS_DISLOCATION ===")
for Z in (3.0,4.0,5.0,6.0,8.0):
    report(f"E2L z_basis<=-{Z}", z['basis']<=-Z)
    report(f"E2S z_basis>=+{Z}", z['basis']>=Z)
print("=== E3 VOLATILITY SHOCK (down displacement + vol expansion) ===")
for Z,R in ((3.0,3.0),(4.0,3.0),(4.0,4.0),(5.0,3.0)):
    report(f"E3 z_disp<=-{Z} & volratio>={R}", (z['disp15']<=-Z)&(feat['volratio']>=R))
print("=== E4 MULTI-FACTOR ===")
for Z,Zo,Zb in ((3.0,2.0,2.0),(3.5,2.0,2.0),(4.0,2.0,2.0),(3.0,1.5,1.5)):
    report(f"E4 disp<=-{Z} oi<=-{Zo} basis<=-{Zb}", (z['disp15']<=-Z)&(z['oichg15']<=-Zo)&(z['basis']<=-Zb))
