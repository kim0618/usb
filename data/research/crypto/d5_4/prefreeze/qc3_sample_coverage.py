"""D5.4 pre-freeze QC #3: sample coverage at the extreme end, to pick the final thresholds.
Selection rule (fixed before this ran): the MOST EXTREME threshold that still meets the
preregistered sample floor - 200 OOS events, >=20 per calendar year, >=3 per fold.
Feature magnitudes and counts only. NO forward returns, NO PnL."""
import sys, json, collections
sys.path.insert(0,'backend')
import numpy as np
from app.crypto.research.d6c import data as D
from app.crypto.research.d6 import features as F
DAY=1440; MIN=60_000; COST_BP=11.25
g=D.load_inputs(); ts=g['ts']
ln=np.log(g['close']); lnoi=np.log(g['oi'])
def lag(x,k):
    o=np.full(len(x),np.nan); o[k:]=x[:-k]; return o
disp15=F._clean(ln-lag(ln,15)); oichg15=F._clean(lnoi-lag(lnoi,15))
basis=F._clean(ln-np.log(g['index_close']))
rv15=F._rstd(ln-lag(ln,1),15); rv1440=F._rstd(ln-lag(ln,1),DAY)
with np.errstate(divide='ignore',invalid='ignore'): volratio=F._clean(rv15/rv1440)
def rz(x,wd=30):
    out=np.full(len(x),np.nan); d0=int(ts[0]//(DAY*MIN)); d1=int(ts[-1]//(DAY*MIN))
    for day in range(d0+wd,d1+1):
        lo=int(np.searchsorted(ts,(day-wd)*DAY*MIN)); hi=int(np.searchsorted(ts,day*DAY*MIN))
        nx=int(np.searchsorted(ts,(day+1)*DAY*MIN)); h=x[lo:hi]; h=h[np.isfinite(h)]
        if len(h)<wd*DAY*0.5: continue
        m=np.median(h); mad=np.median(np.abs(h-m))
        if mad<=0: continue
        out[hi:nx]=(x[hi:nx]-m)/(1.4826*mad)
    return out
zd=rz(disp15); zo=rz(oichg15); zb=rz(basis)
valid=np.isfinite(zd)&np.isfinite(zo)&np.isfinite(zb)&np.isfinite(volratio)
FOLD=[1640995200000,1656633600000,1672531200000,1688169600000,1704067200000,
      1719792000000,1735689600000,1751328000000,1767225600000,1790121600000]
yr=(ts//1000).astype('datetime64[s]').astype('datetime64[Y]').astype(int)+1970
def episodes(mask,cool=240):
    idx=np.nonzero(mask)[0]
    if not len(idx): return np.array([],dtype=int)
    keep=[]; nxt=-1; i=0
    while i<len(idx):
        s=idx[i]; e=s
        while i+1<len(idx) and idx[i+1]==idx[i]+1: i+=1; e=idx[i]
        if s>=nxt: keep.append(s); nxt=e+1+cool
        i+=1
    return np.array(keep)
def check(name,mask,mag):
    s=episodes(mask&valid); s=s[(ts[s]>=FOLD[0])&(ts[s]<FOLD[-1])]
    if len(s)==0: print(f"  {name:38s} none"); return None
    byy=collections.Counter(yr[s].tolist()); fold=np.searchsorted(FOLD,ts[s],side='right')
    byf=collections.Counter(fold.tolist())
    m=np.median(np.abs(mag[s]))*1e4
    ok = len(s)>=200 and min(byy.values())>=20 and all(byf.get(f,0)>=3 for f in range(1,10))
    print(f"  {name:38s} n={len(s):5d} minyr={min(byy.values()):3d} minfold={min(byf.get(f,0) for f in range(1,10)):3d} "
          f"med_disp={m:6.1f}bp ({m/COST_BP:5.2f}x)  FLOOR={'OK' if ok else 'FAIL'}")
    return ok
print("sample floor: >=200 OOS events, >=20 per year, >=3 per fold")
print("=== E1-LONG ===")
for Z in (4.0,5.0,6.0,7.0,8.0,10.0): check(f"E1L zd<=-{Z} zo<=-2.5",(zd<=-Z)&(zo<=-2.5),disp15)
print("=== E1-SHORT ===")
for Z in (4.0,5.0,6.0,7.0,8.0,10.0): check(f"E1S zd>=+{Z} zo<=-2.5",(zd>=Z)&(zo<=-2.5),disp15)
print("=== E3 vol shock ===")
for Z,R in ((4.0,4.0),(5.0,4.0),(6.0,4.0),(5.0,5.0),(6.0,5.0)): check(f"E3 zd<=-{Z} vr>={R}",(zd<=-Z)&(volratio>=R),disp15)
print("=== E4 multi-factor ===")
for Z in (4.0,5.0,6.0,7.0,8.0): check(f"E4 zd<=-{Z} zo<=-2.0 zb<=-2.0",(zd<=-Z)&(zo<=-2.0)&(zb<=-2.0),disp15)
print("=== E2 cost-first screen (for the record) ===")
for Z in (8.0,10.0,12.0):
    check(f"E2L zb<=-{Z}", zb<=-Z, basis); check(f"E2S zb>=+{Z}", zb>=Z, basis)
