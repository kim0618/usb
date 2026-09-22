"""E-RT1 read-only Kiwoom capability probe (market data only; no order or account TR, no Kiwoom order stream).

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_e_rt1_kiwoom_probe rest-rate
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_e_rt1_kiwoom_probe ws-capacity [--symbols 3000]
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_e_rt1_kiwoom_probe ws-sessions
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_e_rt1_kiwoom_probe minute-pages [--symbol AAPL --session 2026-09-18]

Caution: the WebSocket probes open a session with the shared app key; a second LOGIN with the same key
closed an existing subscribing session of that key in 4 of 6 trials (measured). Run them only when no
other WebSocket consumer of the key is live.
The REST burst reaches the 5 / s limit briefly; run it outside regular hours.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import time

import httpx
from websockets.asyncio.client import connect

from app.core.config import get_settings
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits

WS_URL = "wss://api.kiwoom.com:10000/api/us/websocket"
MARKET_TYPES = ("FE",)                     # never an order / balance stream (00, 04, F4, F5)


def clients():
    s = get_settings()
    limits = KiwoomRateLimits()
    auth = KiwoomAuthClient(base_url=s.kiwoom_base_url, app_key=s.kiwoom_app_key.get_secret_value(),
                            app_secret=s.kiwoom_app_secret.get_secret_value(), limiter=limits.auth)
    return s, auth, KiwoomMarketDataClient(base_url=s.kiwoom_base_url, auth=auth, rate_limits=limits, max_retries=0)


def listed(client) -> list[tuple[str, str]]:
    out = []
    for ex in ("ND", "NY", "NA"):
        for r in client._collect("usa10099", "/api/us/stkinfo", {"stex_tp": ex}, row_key="list", max_pages=50):  # noqa: SLF001
            out.append((str(r["stk_cd"]).strip(), ex))
    return out


def rest_rate() -> dict:
    s, auth, _ = clients()
    headers = {"authorization": f"Bearer {auth.access_token()}", "api-id": "usa20100",
               "Content-Type": "application/json;charset=UTF-8"}
    http, report = httpx.Client(timeout=10), {}
    for rate in (3, 5, 8, 12):
        codes, start = Counter(), time.monotonic()
        for k in range(30):
            delay = start + k / rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            r = http.post(f"{s.kiwoom_base_url}/api/us/mrkcond", headers=headers, json={"stex_tp": "ND", "stk_cd": "AAPL"})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            codes[f"{r.status_code}/{body.get('return_code')}"] += 1
            if r.status_code != 200:
                report[f"message_at_{rate}"] = body.get("return_msg")
        report[f"rate_{rate}"] = dict(codes)
        time.sleep(3)
    return report


async def _ws(auth, symbols, batch: int, listen: float) -> dict:
    acks, events = Counter(), Counter()
    async with connect(WS_URL, max_size=2**24, ping_interval=None) as ws:
        await ws.send(json.dumps({"trnm": "LOGIN", "token": auth.access_token()}))
        await ws.recv()
        for i in range(0, len(symbols), batch):
            await ws.send(json.dumps({"trnm": "REG", "grp_no": str(1 + i // batch), "refresh": "1", "data": [
                {"item": [{"jmcode": a, "stex_tp": e} for a, e in symbols[i:i + batch]], "type": list(MARKET_TYPES)}]}))
            await asyncio.sleep(1.05)
        end = time.monotonic() + listen
        while time.monotonic() < end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 3))
            except asyncio.TimeoutError:
                continue
            if m.get("trnm") == "PING":
                await ws.send(json.dumps(m))
            elif m.get("trnm") == "REG":
                acks[(m.get("return_code"), m.get("return_msg"))] += 1
            elif m.get("trnm") == "REAL":
                for d in m.get("data", []):
                    events[d.get("item")] += 1
    return {"registered_requested": len(symbols), "acks": [[list(k), v] for k, v in acks.items()],
            "symbols_with_events": len(events)}


async def _sessions(auth) -> list:
    log, t0 = [], time.monotonic()

    async def one(name, start, hold):
        await asyncio.sleep(start)
        try:
            async with connect(WS_URL, ping_interval=None) as ws:
                await ws.send(json.dumps({"trnm": "LOGIN", "token": auth.access_token()}))
                log.append((round(time.monotonic() - t0, 1), name, "login", json.loads(await ws.recv()).get("return_code")))
                # a session holding registrations is the case that gets closed; register one symbol
                await ws.send(json.dumps({"trnm": "REG", "grp_no": "1", "refresh": "1", "data": [
                    {"item": [{"jmcode": "AAPL", "stex_tp": "ND"}], "type": list(MARKET_TYPES)}]}))
                end = time.monotonic() + hold
                while time.monotonic() < end:
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    except asyncio.TimeoutError:
                        continue
                    if m.get("trnm") == "PING":
                        await ws.send(json.dumps(m))
                    elif m.get("trnm") == "SYSTEM":
                        log.append((round(time.monotonic() - t0, 1), name, "SYSTEM"))
                log.append((round(time.monotonic() - t0, 1), name, "held"))
        except Exception as error:
            log.append((round(time.monotonic() - t0, 1), name, "closed", repr(error)[:100]))
    await asyncio.gather(one("A", 0, 30), one("B", 10, 15))
    return sorted(log)


def minute_pages(symbol: str, session: str) -> list:
    _, _, client = clients()
    body = {"stex_tp": "ND", "stk_cd": symbol, "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0",
            "strt_dt": session.replace("-", "")}
    page, seen = None, []
    for i in range(1, 25):
        page = client.request("usa06011", "/api/us/chart", body, continuation=page)
        rows = page.body.get("result_list", [])
        seen.append([i, rows[0]["cntr_tm"], rows[-1]["cntr_tm"], len(rows)])
        if rows[-1]["bus_dt"] == session.replace("-", "") and rows[-1]["cntr_tm"][8:] <= "040000" or not page.continuation:
            break
    return seen


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E-RT1 Kiwoom capability probe (read-only)")
    parser.add_argument("command", choices=("rest-rate", "ws-capacity", "ws-sessions", "minute-pages"))
    parser.add_argument("--symbols", type=int, default=3000)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--session", default="2026-09-18")
    args = parser.parse_args(argv)
    if args.command == "rest-rate":
        out = rest_rate()
    elif args.command == "ws-capacity":
        _, auth, client = clients()
        out = asyncio.run(_ws(auth, listed(client)[:args.symbols], 100, 30))
    elif args.command == "ws-sessions":
        _, auth, _ = clients()
        out = asyncio.run(_sessions(auth))
    else:
        out = minute_pages(args.symbol, args.session)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
