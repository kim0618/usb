import socket; socket.socket=None
import gzip,json,os,sys,collections
from pathlib import Path
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
ET=ZoneInfo("America/New_York"); UTC=ZoneInfo("UTC")
RT=Path.home()/"usb/data/runtime"; HA=Path.home()/"h_auto_completion"; SV=Path(sys.argv[1])
SRC={"BASE_C0":[RT/"strategy_h_v2/d1_1/sec_raw/submissions",RT/"strategy_h/h_pv2c/sec_raw/submissions",RT/"strategy_h/h0_5/sec_raw/submissions"],
     "STRATEGY_C_E0":[RT/"strategy_c/e0/raw/submissions"],
     "H_AUTO_LOCAL":[HA/"store/auto/sec/2026-10-13/submissions",HA/"store_e2e/auto/sec/2026-10-13/submissions",HA/"store_e2e/auto/sec/2026-10-14/submissions",HA/"store_e2e/sec_submissions/2026-10-13/submissions",HA/"measure_sec/2026-10-13/submissions"],
     "H_D7_SERVER":list((SV/"auto/sec").glob("*/submissions"))+list((SV/"sec_submissions").glob("*/submissions"))}
vals=collections.defaultdict(lambda: collections.defaultdict(set))  # acc -> source -> {(adt)}
meta={}
for name,dirs in SRC.items():
    for d in dirs:
        for root,_,fs in os.walk(d):
            for fn in fs:
                if not fn.endswith(".json.gz"): continue
                b=json.loads(gzip.decompress((Path(root)/fn).read_bytes())); b=b.get("body",b); rec=b["filings"]["recent"] if "filings" in b else b
                for a,t,fd,fm,it in zip(rec["accessionNumber"],rec["acceptanceDateTime"],rec["filingDate"],rec["form"],rec["items"]):
                    if fd < "2024-09-01" or fd > "2026-10-12": continue
                    vals[a][name].add(t); meta[a]=(fm,it or "",fd)
def is202(it): return "2.02" in it.split(",")
acc_all=len(vals); multi=set(a for a in vals if len({t for s in vals[a].values() for t in s})>1)
within=[a for a in vals if any(len(s)>1 for s in vals[a].values())]
print("accessions 2024-09..2026-10:",acc_all,"with >1 distinct acceptance value:",len(multi),"within-one-source:",len(within))
e=[a for a in vals if meta[a][0]=="8-K" and is202(meta[a][1])]
em=[a for a in e if a in multi]
print("8-K 2.02 filings:",len(e),"with conflicting acceptance:",len(em), f"{len(em)/len(e):.2%}")
# window classification under each value
def cls(t):
    x=datetime.fromisoformat(t.replace("Z","+00:00")).astimezone(ET); hm=x.time()
    return "AFTER_16" if hm>=time(16) else "PRE_0915" if hm<time(9,15) else "INTRADAY"
flip=collections.Counter()
for a in em:
    cs={cls(t) for s in vals[a].values() for t in s}
    flip["FLIPS_WINDOW" if (("INTRADAY" in cs) and len(cs)>1) else "SAME_WINDOW_SIDE"]+=1
print("conflicting 2.02 filings window effect:",dict(flip))
# hour histogram (UTC reading) of single-valued 2.02 filings: suspicious bands
h=collections.Counter()
for a in e:
    if a in multi: continue
    t=next(iter(next(iter(vals[a].values()))))
    x=datetime.fromisoformat(t.replace("Z","+00:00")).astimezone(ET); h[x.hour]+=1
print("ET hour (UTC reading), non-conflicting 2.02:",dict(sorted(h.items())))
sus=sum(h[k] for k in (11,12,1,2,3,4)); print("in ET 11-12h or 01-04h (ET-as-Z signature):",sus, f"{sus/sum(h.values()):.2%}")
json.dump({"accessions":acc_all,"conflicting":len(multi),"e202":len(e),"e202_conflicting":len(em),"flip":flip,"hour_hist":h,"suspicious":sus},open("pit_tz_impact.json","w"),default=str,indent=1)
