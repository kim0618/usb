"""D5.5 pre-freeze QC: event ranges and confirmation frequency.
Allowed: feature distributions, event counts, range statistics, confirmation frequency.
NOT computed: forward returns, trade PnL, win rate, PF, MDD, fee-adjusted results."""
import sys, collections
sys.path.insert(0,'backend')
import numpy as np
from app.crypto.research.d6c import data as D
from app.crypto.research.d5_4 import events as EV

COST_BP=11.25
g=D.load_inputs(); ts=g['ts']; n=len(ts)
f=EV.compute_features(g)
z={k:EV.robust_z(f[k],ts) for k in ("disp15","oichg15","basis")}
valid=np.isfinite(f["disp15"])&np.isfinite(f["volratio"])
for s in z.values(): valid&=np.isfinite(s)

# D5.4 E1 detector, applied symmetrically. Thresholds unchanged (|z|>=10, z_oi<=-2.5).
shock=(np.abs(z["disp15"])>=10.0)&(z["oichg15"]<=-2.5)&valid
events=EV.merge(shock,cooldown_bars=240)
FOLD=[1640995200000,1790121600000]
oos=[e for e in events if FOLD[0]<=int(ts[e.start])<FOLD[1]]
yr=(ts//1000).astype('datetime64[s]').astype('datetime64[Y]').astype(int)+1970
print(f"SHOCK events total={len(events)} OOS={len(oos)}")
print("  by year", dict(sorted(collections.Counter(yr[[e.start for e in oos]].tolist()).items())))
dirs=[1 if f["disp15"][e.start]>0 else -1 for e in oos]
print(f"  up-shocks={sum(1 for d in dirs if d>0)} down-shocks={sum(1 for d in dirs if d<0)}")
print(f"  event duration bars: med={np.median([e.end-e.start+1 for e in oos]):.0f} p90={np.percentile([e.end-e.start+1 for e in oos],90):.0f}")

# event range from mark extremes over the event bars
def erange(e):
    hi=float(g["mark_high"][e.start:e.end+1].max()); lo=float(g["mark_low"][e.start:e.end+1].min())
    mid=(hi+lo)/2
    return hi,lo,(hi-lo)/mid
R=np.array([erange(e)[2] for e in oos])
print(f"  event RANGE width bp: p25={np.percentile(R,25)*1e4:.0f} med={np.median(R)*1e4:.0f} p75={np.percentile(R,75)*1e4:.0f}")
print(f"    margin 0.25R bp: med={np.median(R)*0.25*1e4:.1f} (cost mult {np.median(R)*0.25*1e4/COST_BP:.2f}x)  p25={np.percentile(R,25)*0.25*1e4:.1f}")

def confirm_stats(name, fn, window):
    hit=0; up=0; dn=0; times=[]; dists=[]
    for e in oos:
        r=fn(e,window)
        if r is None: continue
        bar,side,dist=r; hit+=1
        up+= side>0; dn+= side<0
        times.append(bar-e.end); dists.append(dist)
    rate=hit/len(oos)
    t=np.array(times) if times else np.array([0]); d=np.array(dists)*1e4 if dists else np.array([0.0])
    print(f"  {name:34s} win={window:3d}m conf={hit:4d} ({rate:5.1%}) LONG={up:4d} SHORT={dn:4d} "
          f"no_trade={len(oos)-hit:4d}  t_med={np.median(t):4.0f}m  dist_med={np.median(d):6.1f}bp")
    return rate

def cand_A(e,window,k=0.25):
    hi,lo,r=erange(e); m=k*r
    up_lv=hi*(1+m); dn_lv=lo*(1-m)
    for i in range(e.end+1, min(e.end+1+window, n)):
        c=float(g["close"][i])
        if c>up_lv: return i,+1,(c-hi)/hi
        if c<dn_lv: return i,-1,(lo-c)/lo
    return None

def cand_B(e,window,k=0.25):
    d=abs(float(f["disp15"][e.start])); thr=k*d
    if float(f["disp15"][e.start])<0:      # down shock -> wait for recovery -> LONG
        L=float(g["mark_low"][e.start:e.end+1].min())
        for i in range(e.end+1, min(e.end+1+window, n)):
            L=min(L,float(g["mark_low"][i])); c=float(g["close"][i])
            if c>L*(1+thr): return i,+1,(c-L)/L
    else:                                   # up shock -> wait for rollover -> SHORT
        H=float(g["mark_high"][e.start:e.end+1].max())
        for i in range(e.end+1, min(e.end+1+window, n)):
            H=max(H,float(g["mark_high"][i])); c=float(g["close"][i])
            if c<H*(1-thr): return i,-1,(H-c)/H
    return None

def cand_C(e,window,comp=0.5):
    rv0=float(f["rv15"][e.start])
    if not np.isfinite(rv0) or rv0<=0: return None
    stop=min(e.end+1+window, n)
    for i in range(e.end+1, stop):
        if float(f["rv15"][i])<comp*rv0:            # compression reached
            CH=float(g["mark_high"][max(0,i-14):i+1].max()); CL=float(g["mark_low"][max(0,i-14):i+1].min())
            for j in range(i+1, stop):
                c=float(g["close"][j])
                if c>CH: return j,+1,(c-CH)/CH
                if c<CL: return j,-1,(CL-c)/CL
            return None
    return None

print("\n=== confirmation frequency (no returns computed) ===")
for w in (15,30,60,120):
    confirm_stats("A shock-breakout k=0.25",cand_A,w)
for w in (15,30,60,120):
    confirm_stats("B recovery k=0.25",cand_B,w)
for w in (15,30,60,120):
    confirm_stats("C compression-expansion",cand_C,w)
print("\n=== A margin sensitivity at 60m (structure only) ===")
for k in (0.15,0.25,0.40):
    confirm_stats(f"A k={k}",lambda e,w,k=k: cand_A(e,w,k),60)
print("=== B recovery fraction at 60m ===")
for k in (0.15,0.25,0.40):
    confirm_stats(f"B k={k}",lambda e,w,k=k: cand_B(e,w,k),60)
