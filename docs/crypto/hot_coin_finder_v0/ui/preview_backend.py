"""Preview backend for HOT V0 UI QA. Port 8199. Records every request it receives.

/api/crypto/hot-coins -> the real hot_routes handler (real Binance public scan).
other GET /api/crypto/* -> committed paper fixture. Anything else -> 503. Non-GET -> 405 + logged.
"""
import json, sys, time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, "/home/tjd618/usb-hot-coin-finder-r0/backend")
from app.crypto.terminal import hot_routes  # noqa: E402

LOG = Path(sys.argv[1] if len(sys.argv) > 1 else "requests.jsonl")
FIX = json.load(open("/home/tjd618/usb-hot-coin-finder-r0/frontend/components/__fixtures__/crypto-terminal-paper.json"))
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def record(request: Request, call_next):
    if request.method != "OPTIONS":
        with LOG.open("a") as f:
            f.write(json.dumps({"t": time.time(), "method": request.method, "path": request.url.path,
                                "query": str(request.url.query)}, ensure_ascii=False) + "\n")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse({"error": {"code": "PREVIEW_READ_ONLY", "message": "no writes"}}, status_code=405)
    return await call_next(request)


app.add_api_route("/api/crypto/hot-coins", hot_routes.hot_coins, methods=["GET"])


@app.get("/api/crypto/{rest:path}")
async def fixture(rest: str, request: Request):
    symbol = request.query_params.get("symbol", "BTCUSDT")
    rows = FIX.get(symbol)
    dead = JSONResponse({"error": {"code": "OFFLINE", "message": "not part of preview"}}, status_code=503)
    if not rows:
        return dead
    key = {"chart-history": "chart_history", "state": "state", "sizing": "sizing", "performance": "performance",
           "ledger": "ledger", "trades": "trades", "pnl-breakdown": "pnl_breakdown"}.get(rest)
    if rest == "chart":
        return {"symbol": symbol, "bars": rows["chart_history"]["bars"]}
    if key and key in rows:
        return rows[key]
    return dead
