import gzip, json, glob, sys, collections, datetime as dt
ROOT=sys.argv[1]
REV=["Revenues","RevenueFromContractWithCustomerExcludingAssessedTax","SalesRevenueNet","RevenueFromContractWithCustomerIncludingAssessedTax"]
TAGS={"REV":REV,"EPS":["EarningsPerShareDiluted","EarningsPerShareBasic"],"NI":["NetIncomeLoss","ProfitLoss"],
      "OPI":["OperatingIncomeLoss"],"CFO":["NetCashProvidedByUsedInOperatingActivities"],"CAPEX":["PaymentsToAcquirePropertyPlantAndEquipment"]}
W0,W1="2024-10-01","2026-10-09"
def d(s): return dt.date.fromisoformat(s)
files=[f for f in glob.glob(ROOT+"/CIK*.json.gz")]
tot=collections.Counter(); ciks_with_event=0; per_cik=[]; filed_dows=collections.Counter(); forms=collections.Counter()
for p in files:
    try: j=json.loads(gzip.open(p).read())
    except Exception: tot["bad_file"]+=1; continue
    g=j.get("facts",{}).get("us-gaap",{})
    # filings in window
    filings={}
    for t,v in g.items():
        for u,rows in v.get("units",{}).items():
            for r in rows:
                if r.get("form") in ("10-Q","10-K") and W0<=r.get("filed","")<=W1:
                    filings[r["accn"]]=(r["form"],r["filed"])
    if not filings: tot["cik_no_filing"]+=1; continue
    ciks_with_event+=1; per_cik.append(len(filings))
    for accn,(form,filed) in filings.items():
        tot["filings"]+=1; forms[form]+=1
        for k,tags in TAGS.items():
            ok=False; yoy=False
            for t in tags:
                for u,rows in g.get(t,{}).get("units",{}).items():
                    cur=[r for r in rows if r.get("accn")==accn and "start" in r]
                    # primary period: duration ~quarter for 10-Q, ~year for 10-K, latest end
                    want=(80,100) if form=="10-Q" else (350,380)
                    cur=[r for r in cur if want[0]<=(d(r["end"])-d(r["start"])).days<=want[1]]
                    if not cur: continue
                    c=max(cur,key=lambda r:r["end"]); ok=True
                    ce=d(c["end"]); L=(d(c["end"])-d(c["start"])).days
                    prior=[r for r in rows if "start" in r and r.get("filed","")<=filed and abs((ce-d(r["end"])).days-365)<=20 and abs((d(r["end"])-d(r["start"])).days-L)<=15]
                    if prior: yoy=True
                    break
                if ok: break
            if ok: tot[k+"_cur"]+=1
            if yoy: tot[k+"_yoy"]+=1
        filed_dows[d(filed).weekday()]+=1
print("companyfacts files", len(files), "ciks with >=1 10-Q/10-K in window", ciks_with_event, dict(tot))
print("forms", dict(forms), "filings per cik median", sorted(per_cik)[len(per_cik)//2] if per_cik else 0)
f=tot["filings"]
for k in TAGS: print(f"{k}: current {tot[k+'_cur']/f:.1%}  yoy-comparable (PIT) {tot[k+'_yoy']/f:.1%}")
print("filed weekday (0=Mon)", dict(sorted(filed_dows.items())))
