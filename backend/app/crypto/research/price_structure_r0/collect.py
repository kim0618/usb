"""Read-only public fetch of recent BTCUSDT 1m klines for Price Structure R0.

The frozen D2 history (``data/runtime/crypto/BTCUSDT/historical/kline_1m``) ends at
2026-09-23T01:33Z, so the replay window needs the last stretch from the public
Bybit endpoint. This module only reads: no key, no order path, no write outside
``data/research/crypto/price_structure_r0/raw``.

Run from the repository root::

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r0.collect
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
OUT = ROOT / "data/research/crypto/price_structure_r0/raw"
URL = "https://api.bybit.com/v5/market/kline"
MINUTE_MS = 60_000
PAGE = 1000

WINDOW_START_MS = 1_786_492_800_000  # 2026-08-12T00:00:00Z inclusive (warmup start)
WINDOW_END_MS = 1_791_072_000_000  # 2026-10-04T00:00:00Z exclusive (replay end)


def _page(start_ms: int, end_ms: int) -> list[list[str]]:
    q = (f"{URL}?category=linear&symbol=BTCUSDT&interval=1"
         f"&start={start_ms}&end={end_ms - 1}&limit={PAGE}")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(q, timeout=20) as fh:
                payload = json.load(fh)
            if payload.get("retCode") != 0:
                raise RuntimeError(f"retCode={payload.get('retCode')} {payload.get('retMsg')}")
            return payload["result"]["list"]
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def fetch(start_ms: int = WINDOW_START_MS, end_ms: int = WINDOW_END_MS) -> Path:
    rows: dict[int, list[str]] = {}
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + PAGE * MINUTE_MS, end_ms)
        got = _page(cursor, chunk_end)
        if not got:
            raise RuntimeError(f"empty page at {cursor}")
        for r in got:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                rows[ts] = r
        cursor = chunk_end
    want = list(range(start_ms, end_ms, MINUTE_MS))
    missing = [t for t in want if t not in rows]
    if missing:
        raise RuntimeError(f"{len(missing)} minutes missing, first {missing[0]}")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"kline_1m_{start_ms}_{end_ms}.jsonl"
    with open(path, "w") as fh:
        for t in want:
            r = rows[t]
            fh.write(json.dumps({"timestamp_ms": t, "open": r[1], "high": r[2], "low": r[3],
                                 "close": r[4], "volume": r[5], "turnover": r[6]},
                                sort_keys=True) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (path.with_suffix(".manifest.json")).write_text(json.dumps(
        {"source": "bybit v5 /market/kline category=linear symbol=BTCUSDT interval=1",
         "fetched_at_ms": int(time.time() * 1000), "rows": len(want),
         "first_timestamp_ms": want[0], "last_timestamp_ms": want[-1],
         "sha256": digest}, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path} rows={len(want)} sha256={digest}")
    return path


if __name__ == "__main__":
    fetch()
