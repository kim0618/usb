"""D5.2 public archive fetch: data.binance.vision (zip + CHECKSUM) and OKX REST history paging.

Every file is verified (Binance: published .CHECKSUM sha256; OKX: page continuity) and recorded in a
manifest with bytes and sha256. Research-only; no keys, no accounts.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.derivatives.archive binance
    PYTHONPATH=backend .venv/bin/python -m app.crypto.derivatives.archive okx
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path("data/runtime/crypto/d5_2/archive")
VISION_S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
VISION = "https://data.binance.vision"
UA = {"User-Agent": "Mozilla/5.0 (research archive)"}
FIRST_MONTH, LAST_MONTH = "2021-02", "2026-08"          # monthly files; September 2026 from daily files
DAILY_TAIL = ("2026-09-01", "2026-09-22")

BINANCE_SERIES = {
    "um_klines": "data/futures/um/{freq}/klines/BTCUSDT/1m/",
    "um_mark": "data/futures/um/{freq}/markPriceKlines/BTCUSDT/1m/",
    "um_index": "data/futures/um/{freq}/indexPriceKlines/BTCUSDT/1m/",
    "um_funding": "data/futures/um/monthly/fundingRate/BTCUSDT/",
    "spot_klines": "data/spot/{freq}/klines/BTCUSDT/1m/",
    "um_metrics": "data/futures/um/daily/metrics/BTCUSDT/",
}


def _get(url: str, retries: int = 5) -> bytes:
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:  # noqa: BLE001
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError


def s3_list(prefix: str, delimiter: str | None = None) -> list[str]:
    keys, marker = [], ""
    while True:
        url = f"{VISION_S3}?prefix={urllib.parse.quote(prefix)}&marker={urllib.parse.quote(marker)}"
        if delimiter:
            url += f"&delimiter={urllib.parse.quote(delimiter)}"
        x = _get(url).decode()
        page = re.findall(r"<Key>([^<]*)</Key>", x) + re.findall(r"<Prefix>([^<]*)</Prefix>", x)[1:]
        keys += page
        if "<IsTruncated>true</IsTruncated>" not in x or not page:
            return keys
        marker = page[-1]


def _want(key: str) -> bool:
    if not key.endswith(".zip"):
        return False
    m = re.search(r"(\d{4}-\d{2})(-\d{2})?\.zip$", key)
    if not m:
        return False
    if m.group(2):                                   # daily
        d = m.group(1) + m.group(2)
        return "2021-02-01" <= d <= DAILY_TAIL[1] if "metrics" in key else DAILY_TAIL[0] <= d <= DAILY_TAIL[1]
    return FIRST_MONTH <= m.group(1) <= LAST_MONTH


def fetch_vision(key: str) -> dict:
    dest = ROOT / "binance" / key.replace("data/", "")
    rec = {"key": key}
    if dest.exists() and dest.with_suffix(".sha256ok").exists():
        rec.update(json.loads(dest.with_suffix(".sha256ok").read_text()))
        return rec
    body = _get(f"{VISION}/{key}")
    published = _get(f"{VISION}/{key}.CHECKSUM").decode().split()[0]
    sha = hashlib.sha256(body).hexdigest()
    if sha != published:
        raise IOError(f"checksum mismatch {key}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    info = {"bytes": len(body), "sha256": sha, "checksum_verified": True}
    dest.with_suffix(".sha256ok").write_text(json.dumps(info))
    rec.update(info)
    return rec


def binance(workers: int = 8) -> dict:
    keys = []
    for name, pat in BINANCE_SERIES.items():
        for freq in ("monthly", "daily"):
            if "{freq}" not in pat and freq == "daily" and "metrics" not in pat:
                continue
            pre = pat.format(freq=freq)
            keys += [k for k in s3_list(pre) if _want(k)]
    # quarterly USDT-M contracts alive in the window
    dirs = s3_list("data/futures/um/monthly/klines/", delimiter="/")
    quarterly = sorted({d.split("/")[-2] for d in dirs if re.search(r"/BTCUSDT_\d{6}/$", d)})
    quarterly = [c for c in quarterly if "210326" <= c.split("_")[1] <= "270326"]
    for c in quarterly:
        for freq in ("monthly", "daily"):
            keys += [k for k in s3_list(f"data/futures/um/{freq}/klines/{c}/1m/") if _want(k)]
    keys = sorted(set(keys))
    with ThreadPoolExecutor(workers) as ex:
        recs = list(ex.map(fetch_vision, keys))
    man = {"source": VISION, "files": len(recs), "bytes": sum(r["bytes"] for r in recs),
           "quarterly_contracts": quarterly, "records": recs}
    (ROOT / "binance_manifest.json").write_text(json.dumps(man, indent=1))
    return {k: v for k, v in man.items() if k != "records"}


# ------------------------------------------------------------------ OKX (REST history, 5m)
OKX = "https://www.okx.com"
OKX_SERIES = {
    "okx_swap_last": ("/api/v5/market/history-candles", "BTC-USDT-SWAP", 0.12),
    "okx_swap_mark": ("/api/v5/market/history-mark-price-candles", "BTC-USDT-SWAP", 0.22),
    "okx_index": ("/api/v5/market/history-index-candles", "BTC-USDT", 0.22),
}
OKX_START_MS = 1_612_137_600_000          # 2021-02-01
OKX_END_MS = 1_790_121_600_000            # 2026-09-23


def okx(series: str) -> dict:
    path, inst, pause = OKX_SERIES[series]
    out = ROOT / "okx" / f"{series}_5m.jsonl.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, after, pages = {}, OKX_END_MS, 0
    while after > OKX_START_MS:
        url = f"{OKX}{path}?instId={inst}&bar=5m&limit=100&after={after}"
        j = json.loads(_get(url))
        if j.get("code") != "0":
            raise IOError(f"{series}: {j}")
        data = j["data"]
        pages += 1
        if not data:
            break
        for r in data:
            rows[int(r[0])] = r
        after = min(int(r[0]) for r in data)
        time.sleep(pause)
    keep = sorted(t for t in rows if OKX_START_MS <= t < OKX_END_MS)
    with gzip.open(out, "wt") as f:
        for t in keep:
            f.write(json.dumps(rows[t]) + "\n")
    ts = keep
    gaps = sum(1 for a, b in zip(ts, ts[1:]) if b - a != 300_000)
    info = {"series": series, "endpoint": path, "instId": inst, "bar": "5m", "pages": pages, "rows": len(keep),
            "first": keep[0] if keep else None, "last": keep[-1] if keep else None, "gaps": gaps,
            "bytes": out.stat().st_size, "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
    (ROOT / "okx" / f"{series}_manifest.json").write_text(json.dumps(info, indent=1))
    return info




def binance_cm(workers: int = 8) -> dict:
    """COIN-M BTCUSD quarterlies + COIN-M BTCUSD index (term structure; USDT-M far leg is SHORT_HISTORY)."""
    keys = []
    for freq in ("monthly", "daily"):
        keys += [k for k in s3_list(f"data/futures/cm/{freq}/indexPriceKlines/BTCUSD/1m/") if _want(k)]
    dirs = s3_list("data/futures/cm/monthly/klines/", delimiter="/")
    quarterly = sorted({d.split("/")[-2] for d in dirs if re.search(r"/BTCUSD_\d{6}/$", d)})
    quarterly = [c for c in quarterly if "210326" <= c.split("_")[1] <= "270326"]
    for c in quarterly:
        for freq in ("monthly", "daily"):
            keys += [k for k in s3_list(f"data/futures/cm/{freq}/klines/{c}/1m/") if _want(k)]
    keys = sorted(set(keys))
    with ThreadPoolExecutor(workers) as ex:
        recs = list(ex.map(fetch_vision, keys))
    man = {"source": VISION, "files": len(recs), "bytes": sum(r["bytes"] for r in recs),
           "quarterly_contracts": quarterly, "records": recs}
    (ROOT / "binance_cm_manifest.json").write_text(json.dumps(man, indent=1))
    return {k: v for k, v in man.items() if k != "records"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["binance", "binance_cm", "okx"])
    a = ap.parse_args()
    if a.what == "binance":
        print(json.dumps(binance(), indent=1))
    elif a.what == "binance_cm":
        print(json.dumps(binance_cm(), indent=1))
    else:
        with ThreadPoolExecutor(3) as ex:
            print(json.dumps(list(ex.map(okx, OKX_SERIES)), indent=1))
