"""Read-only public fetch of Binance USD-M BTCUSDT 1m klines for Market Context R0.

Why a second venue series exists at all: Layer B (order book) and Layer C (aggressive trades)
only exist as the ``binance_usdm`` journal that the Market Structure V0 collector wrote. Price
Structure R0/R1/R2 ran on Bybit. Joining a Bybit level book to a Binance order book would put a
cross venue basis inside a tolerance that is itself only about 0.25 percent wide, so the structure
layer is recomputed on the Binance series instead and every layer in this study reads one venue.

The series is a single file so that `data.load_1m` can insist on exactly one, the same way R0 does.
Nothing here writes outside ``data/research/crypto/market_context_r0/raw`` and nothing here reads a
credential or an order path.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.market_context_r0.collect
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
OUT = ROOT / "data/research/crypto/market_context_r0/raw"
URL = "https://fapi.binance.com/fapi/v1/klines"
MINUTE_MS = 60_000
PAGE = 1500

#: Same warmup start as R0 so the frozen thetas see the same amount of history.
WINDOW_START_MS = 1_786_492_800_000  # 2026-08-12T00:00:00Z inclusive
#: Past the end of the journal (2026-10-04T07:34:24Z) so forward outcomes have room.
WINDOW_END_MS = 1_791_100_800_000  # 2026-10-04T08:00:00Z exclusive

FIELDS = ("open", "high", "low", "close", "volume", "turnover", "trades",
          "taker_buy_base", "taker_buy_quote")


def _page(start_ms: int, end_ms: int) -> list[list]:
    q = f"{URL}?symbol=BTCUSDT&interval=1m&startTime={start_ms}&endTime={end_ms - 1}&limit={PAGE}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(q, timeout=25) as fh:
                return json.load(fh)
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def fetch(start_ms: int = WINDOW_START_MS, end_ms: int = WINDOW_END_MS) -> Path:
    rows: dict[int, list] = {}
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
        time.sleep(0.12)
    want = list(range(start_ms, end_ms, MINUTE_MS))
    missing = [t for t in want if t not in rows]
    if missing:
        raise RuntimeError(f"{len(missing)} minutes missing, first {missing[0]}")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"binance_kline_1m_{start_ms}_{end_ms}.jsonl"
    with open(path, "w") as fh:
        for t in want:
            r = rows[t]
            rec = {"timestamp_ms": t, "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                   "volume": r[5], "turnover": r[7], "trades": r[8],
                   "taker_buy_base": r[9], "taker_buy_quote": r[10]}
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".manifest.json").write_text(json.dumps(
        {"source": "binance usdm /fapi/v1/klines symbol=BTCUSDT interval=1m",
         "fetched_at_ms": int(time.time() * 1000), "rows": len(want),
         "first_timestamp_ms": want[0], "last_timestamp_ms": want[-1],
         "fields": list(FIELDS), "sha256": digest}, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path} rows={len(want)} sha256={digest}")
    return path


if __name__ == "__main__":
    fetch()
