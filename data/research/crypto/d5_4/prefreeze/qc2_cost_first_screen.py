"""D5.4 pre-freeze QC #2: the COST-FIRST anchor.
At each candidate threshold, how large is the event's own displacement, in multiples of the
11.25bp round-trip cost? Feature magnitudes only. NO forward returns, NO PnL."""
import sys, collections
sys.path.insert(0,'backend')
import numpy as np
from app.crypto.research.d6c import data as D
from app.crypto.research.d6 import features as F

DAY=1440; MIN=60_000; COST_BP=11.25
g=D.load_inputs(); ts=g['ts']
ln=np.log(g['close']); lnoi=np.log(g['oi'])
def lag(x,k):
    out=np.full(len(x),np.nan); out[k:]=x[:-k]; return out
disp15=F._clean(ln-lag(ln,15)); oichg15=F._clean(lnoi-lag(lnoi,15))
basis=F._clean(ln-np.log(g['index_close']))
rv15=F._rstd(ln-lag(ln,1),15); rv1440=F._rstd(ln-lag(ln,1),DAY)
with np.errstate(divide='ignore',invalid='ignore'): volratio=F._clean(rv15/rv1440)

def robust_z(x, ts, wd=30):
    out=np.full(len(x),np.nan)
    d0=int(ts[0]//(DAY*MIN)); d1=int(ts[-1]//(DAY*MIN))
    for day in range(d0+wd,d1+1):
        lo=int(np.searchsorted(ts,(day-wd)*DAY*MIN)); hi=int(np.searchsorted(ts,day*DAY*MIN))
        nxt=int(np.searchsorted(ts,(day+1)*DAY*MIN))
        h=x[lo:hi]; h=h[np.isfinite(h)]
        if len(h)<wd*DAY*0.5: continue
        med=np.median(h); mad=np.median(np.abs(h-med))
        if mad<=0: continue
        out[hi:nxt]=(x[hi:nxt]-med)/(1.4826*mad)
    return out
zd=robust_z(disp15,ts); zo=robust_z(oichg15,ts); zb=robust_z(basis,ts)
valid=np.isfinite(zd)&np.isfinite(zo)&np.isfinite(zb)&np.isfinite(volratio)

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

FOLD0=1640995200000; FOLD9=1790121600000
def anchor(name, mask, magnitude, unit):
    s=episodes(mask&valid)
    s=s[(ts[s]>=FOLD0)&(ts[s]<FOLD9)]
    if not len(s): print(f"  {name:40s} no events"); return
    m=np.abs(magnitude[s])*1e4
    print(f"  {name:40s} n={len(s):5d}  |{unit}| bp: p25={np.percentile(m,25):7.1f} "
          f"med={np.median(m):7.1f} p75={np.percentile(m,75):7.1f}  cost_mult(med)={np.median(m)/COST_BP:5.2f}x")

print(f"round-trip cost anchor = {COST_BP} bp (D6-C measured)")
print("=== E1 LONG: event displacement magnitude ===")
for Z,Zo in ((3.0,2.5),(4.0,2.5),(5.0,2.5),(6.0,2.5),(8.0,2.5)):
    anchor(f"E1L zd<=-{Z} zo<=-{Zo}", (zd<=-Z)&(zo<=-Zo), disp15, "disp15")
print("=== E1 SHORT ===")
for Z,Zo in ((4.0,2.5),(6.0,2.5),(8.0,2.5)):
    anchor(f"E1S zd>=+{Z} zo<=-{Zo}", (zd>=Z)&(zo<=-Zo), disp15, "disp15")
print("=== E2: basis magnitude at the event ===")
for Z in (4.0,5.0,6.0,8.0,10.0):
    anchor(f"E2L zb<=-{Z}", zb<=-Z, basis, "basis")
    anchor(f"E2S zb>=+{Z}", zb>=Z, basis, "basis")
print("=== E3: displacement at vol-shock events ===")
for Z,R in ((4.0,3.0),(5.0,4.0),(6.0,4.0),(8.0,4.0)):
    anchor(f"E3 zd<=-{Z} vr>={R}", (zd<=-Z)&(volratio>=R), disp15, "disp15")
print("=== E4 multi-factor: displacement ===")
for Z,Zo,Zb in ((4.0,2.0,2.0),(5.0,2.0,2.0),(6.0,2.0,2.0),(5.0,2.5,3.0)):
    anchor(f"E4 zd<=-{Z} zo<=-{Zo} zb<=-{Zb}", (zd<=-Z)&(zo<=-Zo)&(zb<=-Zb), disp15, "disp15")
