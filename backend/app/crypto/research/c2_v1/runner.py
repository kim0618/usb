"""C2 V1 fixed-candidate, event-trade historical prevalidation."""
from __future__ import annotations

import hashlib, json
from pathlib import Path
import numpy as np

from .. import dataset, derivatives_flow as DF
from ..d5_4.events import lag, merge, robust_z

ROOT = Path(__file__).resolve().parents[5]
DOC = ROOT / "docs/crypto/C2_SHORT_V1_E0_CONTRACT.md"
MACHINE = ROOT / "data/research/crypto/c2_v1/contract_v1.json"
FREEZE = ROOT / "data/research/crypto/c2_v1/contract_freeze_v1.json"
OUT = ROOT / "data/research/crypto/c2_v1"
FOLDS = ("2022-01-01", "2022-07-01", "2023-01-01", "2023-07-01", "2024-01-01",
         "2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01", "2026-09-23")

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def freeze() -> dict:
    body = {"document_sha256": digest(DOC), "machine_sha256": digest(MACHINE)}
    FREEZE.write_text(json.dumps(body, indent=1) + "\n")
    return body

def contract() -> dict:
    frozen = json.loads(FREEZE.read_text())
    if digest(DOC) != frozen["document_sha256"] or digest(MACHINE) != frozen["machine_sha256"]:
        raise RuntimeError("C2 contract changed after freeze")
    return json.loads(MACHINE.read_text())

def _rolling_high_before(x: np.ndarray, width: int) -> np.ndarray:
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) > width:
        out[width:] = np.max(sliding_window_view(x, width)[:-1], axis=1)
    return out

def signals(g: dict, c: dict) -> tuple[list, dict]:
    x5 = DF.load_inputs(g); f5 = DF.features5(x5)
    premium = DF.to_1m(f5["spot_basis_bybit"], x5["B"], g["ts"])
    with np.errstate(divide="ignore", invalid="ignore"):
        log_close, log_oi = np.log(g["close"]), np.log(g["oi"])
    oi60 = log_oi - lag(log_oi, 60)
    ret5 = log_close - lag(log_close, 5)
    pz = robust_z(premium, g["ts"]); oz = robust_z(oi60, g["ts"])
    high = _rolling_high_before(g["high"], 30)
    failure = np.log(g["close"] / high) <= -25 / 10_000
    mask = (pz >= 3.0) & (oz >= 2.0) & failure & (ret5 < 0)
    events = merge(mask, c["event"]["cooldown_min"])
    coverage = {"bars": len(mask), "premium_valid": int(np.isfinite(premium).sum()),
                "oi_change_valid": int(np.isfinite(oi60).sum()), "condition_bars": int(mask.sum()),
                "events_all": len(events)}
    return events, {"premium": premium, "premium_z": pz, "oi_change_60m": oi60,
                    "oi_z": oz, "failure_from_high": np.log(g["close"] / high),
                    "ret5": ret5, "coverage": coverage}

def _funding(g: dict, start: int, end: int) -> float:
    m = (g["funding_ts"] > start) & (g["funding_ts"] <= end)
    return float(np.sum(g["funding_rate"][m]))

def trades(g: dict, events: list, c: dict) -> list[dict]:
    start_ms = int(np.datetime64(c["window"]["start"], "ms").astype(np.int64))
    end_ms = int(np.datetime64(c["window"]["end_exclusive"], "ms").astype(np.int64))
    h = c["execution"]["horizon_min"]; fee = c["cost"]["taker_rate"]
    micro = c["cost"]["micro_base_fraction"]
    rows=[]
    for e in events:
        i=e.start
        if i+1+h >= len(g["ts"]) or not (start_ms <= g["ts"][i] < end_ms): continue
        entry=float(g["open"][i+1]); exit_=float(g["open"][i+1+h])
        gross=1-exit_/entry; funding=-_funding(g,int(g["ts"][i+1]),int(g["ts"][i+1+h]))
        cost=fee*(1+exit_/entry)+micro+funding; net=gross-cost
        held=slice(i+1,i+1+h+1)
        mfe=1-float(np.min(g["low"][held]))/entry
        mae=1-float(np.max(g["high"][held]))/entry
        rows.append({"signal_bar_ms":int(g["ts"][i]),"entry_at_ms":int(g["ts"][i+1]),
          "exit_at_ms":int(g["ts"][i+1+h]),"entry_price":entry,"exit_price":exit_,
          "gross":gross,"fee":fee*(1+exit_/entry),"micro":micro,"funding":funding,
          "net":net,"mfe":mfe,"mae":mae})
    return rows

def _summary(rows: list[dict], c: dict) -> dict:
    net=np.array([r["net"] for r in rows]); gross=np.array([r["gross"] for r in rows])
    pos=net[net>0].sum(); neg=-net[net<0].sum()
    fold=[]
    for a,b in zip(FOLDS,FOLDS[1:]):
        lo=int(np.datetime64(a,"ms").astype(np.int64)); hi=int(np.datetime64(b,"ms").astype(np.int64))
        v=np.array([r["net"] for r in rows if lo <= r["entry_at_ms"] < hi])
        fold.append({"start":a,"trades":len(v),"mean_net_bp":float(v.mean()*1e4) if len(v) else None})
    # Cluster bootstrap over seven-day signal-time buckets.
    day=86_400_000; block=c["bootstrap"]["cluster_days"]*day
    groups={}
    for r in rows: groups.setdefault(r["entry_at_ms"]//block,[]).append(r["net"])
    clusters=list(groups.values()); rng=np.random.default_rng(c["bootstrap"]["seed"]); boots=[]
    for _ in range(c["bootstrap"]["iterations"]):
        picked=rng.integers(0,len(clusters),len(clusters)); values=[x for j in picked for x in clusters[j]]
        boots.append(np.mean(values))
    cutoff=np.quantile(net,.95); trimmed=net[net<cutoff]
    ci=[float(x*1e4) for x in np.quantile(boots,[.025,.975])]
    mean=float(net.mean()*1e4); pf=float(pos/neg) if neg else None
    positive=sum(1 for f in fold if f["mean_net_bp"] is not None and f["mean_net_bp"]>0)
    trimmed_mean=float(trimmed.mean()*1e4) if len(trimmed) else None
    positive_pre=(mean>0 and ci[0]>0 and pf and pf>1 and positive>=5 and trimmed_mean>0)
    plausible=(mean>0 and pf and pf>1 and positive>=4 and trimmed_mean>=0)
    verdict="POSITIVE_PREVALIDATION" if positive_pre else ("INCONCLUSIVE_BUT_PLAUSIBLE" if plausible else "CLEARLY_NEGATIVE")
    return {"trades":len(rows),"gross_mean_bp":float(gross.mean()*1e4),
      "cost_mean_bp":float((gross-net).mean()*1e4),"net_mean_bp":mean,
      "net_median_bp":float(np.median(net)*1e4),"win_rate":float((net>0).mean()),
      "profit_factor":pf,"clustered_ci95_bp":ci,"folds":fold,"positive_folds":positive,
      "top_5pct_removed_mean_bp":trimmed_mean,"mfe_mean_bp":float(np.mean([r['mfe'] for r in rows])*1e4),
      "mae_mean_bp":float(np.mean([r['mae'] for r in rows])*1e4),"verdict":verdict}

def run() -> dict:
    c=contract(); g=dataset.load(); ev,features=signals(g,c); rows=trades(g,ev,c)
    payload={"id":c["id"],"contract":json.loads(FREEZE.read_text()),"coverage":features["coverage"],
             "summary":_summary(rows,c),"trades":rows}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"results_v1.json").write_text(json.dumps({k:v for k,v in payload.items() if k!='trades'},indent=1)+"\n")
    import pyarrow as pa, pyarrow.parquet as pq
    pq.write_table(pa.Table.from_pylist(rows),OUT/"trades_v1.parquet")
    return payload

if __name__ == "__main__":
    import sys
    if len(sys.argv)>1 and sys.argv[1]=="freeze": print(json.dumps(freeze(),indent=1))
    else: print(json.dumps(run()["summary"],indent=1))
