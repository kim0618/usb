"""D5.5 pre-freeze QC #2: corrected confirmation definitions.
Range measured over the SHOCK window; B is a retracement of the shock, not a bounce off the low;
every confirmation margin is floored at a cost-anchored minimum.
Structure statistics and confirmation frequency only. NO returns, NO PnL."""
import sys, collections
sys.path.insert(0,'backend')
import numpy as np
from app.crypto.research.d6c import data as D
from app.crypto.research.d5_4 import events as EV

COST_BP=11.25; MIN_MARGIN=2*COST_BP/1e4   # 22.5bp structural floor
g=D.load_inputs(); ts=g['ts']; n=len(ts)
f=EV.compute_features(g)
z={k:EV.robust_z(f[k],ts) for k in ("disp15","oichg15","basis")}
valid=np.isfinite(f["disp15"])&np.isfinite(f["volratio"])
for s in z.values(): valid&=np.isfinite(s)
shock=(np.abs(z["disp15"])>=10.0)&(z["oichg15"]<=-2.5)&valid
events=EV.merge(shock,cooldown_bars=240)
F0,F1=1640995200000,1790121600000
oos=[e for e in events if F0<=int(ts[e.start])<F1 and e.start>=15]
print(f"SHOCK events OOS={len(oos)}")

def shock_window(e):
    """The bars that produced the displacement, plus the event run itself."""
    lo=max(0,e.start-14); hi=e.end
    H=float(g["mark_high"][lo:hi+1].max()); L=float(g["mark_low"][lo:hi+1].min())
    P0=float(g["close"][lo])                      # pre-shock price
    return H,L,P0,(H-L)/((H+L)/2)
RW=np.array([shock_window(e)[3] for e in oos])
print(f"SHOCK WINDOW range bp: p25={np.percentile(RW,25)*1e4:.0f} med={np.median(RW)*1e4:.0f} p75={np.percentile(RW,75)*1e4:.0f}")
for k in (0.15,0.25,0.40):
    m=np.median(RW)*k*1e4
    print(f"   margin {k}R med={m:.1f}bp ({m/COST_BP:.2f}x cost)   floored share="
          f"{(RW*k < MIN_MARGIN).mean():.1%}")

def stats(name,fn,window):
    hit=up=dn=0; times=[]; dists=[]
    for e in oos:
        r=fn(e,window)
        if r is None: continue
        bar,side,dist=r; hit+=1; up+=side>0; dn+=side<0
        times.append(bar-e.end); dists.append(dist)
    t=np.array(times) if times else np.array([0]); d=np.array(dists)*1e4 if dists else np.array([0.0])
    print(f"  {name:32s} w={window:3d}m conf={hit:4d} ({hit/len(oos):5.1%}) L={up:4d} S={dn:4d} "
          f"no_trade={len(oos)-hit:4d} t_med={np.median(t):4.0f}m dist_med={np.median(d):6.1f}bp")

def A(e,w,k=0.25):
    H,L,P0,R=shock_window(e); m=max(k*R,MIN_MARGIN)
    up=H*(1+m); dn=L*(1-m)
    for i in range(e.end+1,min(e.end+1+w,n)):
        c=float(g["close"][i])
        if c>up: return i,+1,(c-H)/H
        if c<dn: return i,-1,(L-c)/L
    return None

def B(e,w,k=0.5):
    """Retracement of the shock itself, measured from the pre-shock price."""
    H,L,P0,R=shock_window(e)
    down = float(f["disp15"][e.start])<0
    if down:
        lvl=L+k*(P0-L)
        if (lvl-L)/L < MIN_MARGIN: lvl=L*(1+MIN_MARGIN)
        for i in range(e.end+1,min(e.end+1+w,n)):
            if float(g["close"][i])>lvl: return i,+1,(lvl-L)/L
    else:
        lvl=H-k*(H-P0)
        if (H-lvl)/H < MIN_MARGIN: lvl=H*(1-MIN_MARGIN)
        for i in range(e.end+1,min(e.end+1+w,n)):
            if float(g["close"][i])<lvl: return i,-1,(H-lvl)/H
    return None

def C(e,w,comp=0.5):
    rv0=float(f["rv15"][e.start])
    if not np.isfinite(rv0) or rv0<=0: return None
    stop=min(e.end+1+w,n)
    for i in range(e.end+1,stop):
        if float(f["rv15"][i])<comp*rv0:
            CH=float(g["mark_high"][max(0,i-14):i+1].max()); CL=float(g["mark_low"][max(0,i-14):i+1].min())
            up=CH*(1+MIN_MARGIN); dn=CL*(1-MIN_MARGIN)
            for j in range(i+1,stop):
                c=float(g["close"][j])
                if c>up: return j,+1,(c-CH)/CH
                if c<dn: return j,-1,(CL-c)/CL
            return None
    return None

print("\n=== corrected confirmation frequency ===")
for w in (30,60,120): stats("A shock-window breakout k=.25",A,w)
for w in (30,60,120): stats("B shock retrace k=.5",B,w)
for w in (30,60,120): stats("C compression expansion",C,w)
print("\n=== B retrace fraction at 60m ===")
for k in (0.382,0.5,0.618): stats(f"B k={k}",lambda e,w,k=k: B(e,w,k),60)
print("=== A margin at 60m ===")
for k in (0.15,0.25,0.40): stats(f"A k={k}",lambda e,w,k=k: A(e,w,k),60)
