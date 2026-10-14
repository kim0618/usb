"""Download 5 m klines and funding for every USDT perpetual in the data.binance.vision archive.

Reuses the D5.2 fetch (sha256 against the published CHECKSUM). Downloads all months; the reader enforces
TRAIN. Run from backend/: python -m app.crypto.research.hot_edge_r0.download
"""
from __future__ import annotations

import hashlib
import urllib.parse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ...derivatives.archive import VISION, _get, s3_list

ROOT = Path(__file__).resolve().parents[5] / "data/runtime/crypto/hot_edge_r0/archive"
KLINES = "data/futures/um/monthly/klines/"
FUNDING = "data/futures/um/monthly/fundingRate/"


def fetch(key: str) -> dict:
    dest = ROOT / "binance" / key.replace("data/", "")
    ok = dest.with_suffix(".sha256ok")
    if dest.exists() and ok.exists():
        return {"key": key, **json.loads(ok.read_text())}
    body = _get(f"{VISION}/{urllib.parse.quote(key)}")
    published = _get(f"{VISION}/{urllib.parse.quote(key)}.CHECKSUM").decode().split()[0]
    sha = hashlib.sha256(body).hexdigest()
    if sha != published:
        raise IOError(f"checksum mismatch {key}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    info = {"bytes": len(body), "sha256": sha, "checksum_verified": True}
    ok.write_text(json.dumps(info))
    return {"key": key, **info}


def symbols() -> list[str]:
    dirs = s3_list(KLINES, delimiter="/")
    return sorted({d.split("/")[-2] for d in dirs if d.endswith("USDT/")})


def keys_for(sym: str) -> list[str]:
    out = [k for k in s3_list(f"{KLINES}{sym}/5m/") if k.endswith(".zip")]
    out += [k for k in s3_list(f"{FUNDING}{sym}/") if k.endswith(".zip")]
    return out


def main(workers: int = 12) -> dict:
    t0 = time.time()
    syms = symbols()
    print(f"symbols {len(syms)}", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        keys = sorted({k for ks in ex.map(keys_for, syms) for k in ks})
    print(f"keys {len(keys)} listed in {time.time() - t0:.0f}s", flush=True)
    recs, done = [], 0
    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(fetch, keys):
            recs.append(r)
            done += 1
            if done % 500 == 0:
                print(f"{done}/{len(keys)} {sum(x['bytes'] for x in recs) / 1e9:.2f} GB {time.time() - t0:.0f}s", flush=True)
    man = {"source": VISION, "symbols": syms, "files": len(recs), "bytes": sum(r["bytes"] for r in recs),
           "seconds": round(time.time() - t0), "records": recs}
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "manifest.json").write_text(json.dumps(man, indent=1))
    print(json.dumps({k: v for k, v in man.items() if k not in ("records", "symbols")}), flush=True)
    return man


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
