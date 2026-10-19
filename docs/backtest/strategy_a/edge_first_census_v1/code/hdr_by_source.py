import socket
socket.socket=None
import gzip,json,re,os,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ET=ZoneInfo("America/New_York"); RT=Path.home()/"usb/data/runtime"; HA=Path.home()/"h_auto_completion"; SV=Path(sys.argv[1])
src={"BASE_C0":[RT/"strategy_h_v2/d1_1/sec_raw/submissions",RT/"strategy_h/h_pv2c/sec_raw/submissions",RT/"strategy_h/h0_5/sec_raw/submissions"],
     "STRATEGY_C_E0":[RT/"strategy_c/e0/raw/submissions"],
     "H_AUTO_LOCAL":[HA/"store/auto/sec/2026-10-13/submissions",HA/"store_e2e/auto/sec/2026-10-13/submissions",HA/"measure_sec/2026-10-13/submissions",HA/"store_e2e/sec_submissions/2026-10-13/submissions"],
     "H_D7_SERVER":list((SV/"auto/sec").glob("*/submissions"))+list((SV/"sec_submissions").glob("*/submissions"))}
hdr={}
for p in sorted((RT/"strategy_c/e0/raw/acceptance_headers").glob("*.txt.gz")):
    t=gzip.decompress(p.read_bytes()).decode("latin-1")
    hdr[re.search(r"<ACCESSION-NUMBER>(\S+)",t).group(1)]=(re.search(r"<CIK>(\d+)",t).group(1).zfill(10), datetime.strptime(re.search(r"<ACCEPTANCE-DATETIME>(\d{14})",t).group(1),"%Y%m%d%H%M%S").replace(tzinfo=ET))
out={}
for name,dirs in src.items():
    res={"utc_match":0,"et_match":0,"neither":0,"absent":0,"fetched":set()}
    for acc,(cik,et) in hdr.items():
        found=None
        for d in dirs:
            for p in list(d.glob(f"CIK{cik}/CIK{cik}*.json.gz"))+list(d.glob(f"CIK{cik}*.json.gz")):
                b=json.loads(gzip.decompress(p.read_bytes())); b=b.get("body",b); rec=b["filings"]["recent"] if "filings" in b else b
                if acc in rec["accessionNumber"]:
                    found=rec["acceptanceDateTime"][rec["accessionNumber"].index(acc)]
                    lp=Path(str(p)+".request.json")
                    if lp.exists(): res["fetched"].add(json.loads(lp.read_text()).get("fetched_at","")[:10])
                    break
            if found: break
        if not found: res["absent"]+=1; continue
        naive=datetime.fromisoformat(found.replace("Z","").replace(".000",""))
        if naive.replace(tzinfo=ZoneInfo("UTC")).astimezone(ET)==et: res["utc_match"]+=1
        elif naive.replace(tzinfo=ET)==et: res["et_match"]+=1
        else: res["neither"]+=1
    res["fetched"]=sorted(res["fetched"]); out[name]=res; print(name,res)
