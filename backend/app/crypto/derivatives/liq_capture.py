"""D5.2 research-only liquidation stream capture (Bybit, Binance, OKX) with health streams.

Separate process and directory from the paper terminal feed. Writes one JSONL per venue, one line per
message with the local receive time, flushed per line. Never touches paper runs.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.derivatives.liq_capture --minutes 45 --out data/runtime/crypto/d5_2/capture_v2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import websockets

VENUES = {
    "bybit": {
        "url": "wss://stream.bybit.com/v5/public/linear",
        "sub": [{"op": "subscribe", "args": ["allLiquidation.BTCUSDT", "tickers.BTCUSDT"]}],
        "ping": json.dumps({"op": "ping"}),
    },
    "binance": {
        # USDT-M futures: forceOrder and markPrice are only served on /market/ (D5.2 preflight trap 1)
        "url": "wss://fstream.binance.com/market/stream?streams=btcusdt@forceOrder/!forceOrder@arr/btcusdt@markPrice@1s",
        "sub": [],
        "ping": None,
    },
    "okx": {
        "url": "wss://ws.okx.com:8443/ws/v5/public",
        "sub": [{"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"},
                                             {"channel": "mark-price", "instId": "BTC-USDT-SWAP"}]}],
        "ping": "ping",
    },
}


async def run_venue(name: str, spec: dict, out: Path, deadline: float) -> None:
    path = out / f"{name}.jsonl"
    with open(path, "a", buffering=1) as f:
        while time.time() < deadline:
            try:
                async with websockets.connect(spec["url"], ping_interval=None, max_size=2**22,
                                              user_agent_header="Mozilla/5.0 (research capture)") as ws:
                    f.write(json.dumps({"_meta": "connected", "recv_ms": int(time.time() * 1000)}) + "\n")
                    for s in spec["sub"]:
                        await ws.send(json.dumps(s))
                    last_ping = time.time()
                    while time.time() < deadline:
                        if spec["ping"] and time.time() - last_ping > 20:
                            await ws.send(spec["ping"])
                            last_ping = time.time()
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        f.write(json.dumps({"recv_ms": int(time.time() * 1000), "raw": msg}) + "\n")
            except Exception as e:  # noqa: BLE001 (reconnect and record)
                f.write(json.dumps({"_meta": "error", "recv_ms": int(time.time() * 1000), "err": repr(e)}) + "\n")
                await asyncio.sleep(3)


async def main_async(minutes: float, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60
    (out / "capture_meta.json").write_text(json.dumps({"start_ms": int(time.time() * 1000), "minutes": minutes,
                                                       "venues": {k: v["url"] for k, v in VENUES.items()}}))
    await asyncio.gather(*(run_venue(n, s, out, deadline) for n, s in VENUES.items()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--out", type=Path, default=Path("data/runtime/crypto/d5_2/capture_v2"))
    a = ap.parse_args()
    asyncio.run(main_async(a.minutes, a.out))
