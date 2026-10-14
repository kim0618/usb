"""D5.2 Phase 1 section 6: what the three liquidation streams actually deliver.

RAW vs AGGREGATED: OKX WS vs OKX REST (24h rolling, BTC-USDT-SWAP) over the capture window;
Binance: at most one event per symbol-second observed?; Bybit: events per second.
Side semantics: sign of the mark move over the 60 s before each event, per venue and side.
Storage: bytes per day extrapolated from the capture (raw JSONL, all messages / liquidation-only).

    PYTHONPATH=backend .venv/bin/python -m app.crypto.derivatives.capture_report data/runtime/crypto/d5_2/capture_v2
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np

from . import normalize as N


def _marks(venue: str, path: Path) -> tuple[np.ndarray, np.ndarray]:
    ts, px = [], []
    for line in open(path):
        rec = json.loads(line)
        if "raw" not in rec or rec["raw"] == "pong":
            continue
        try:
            m = json.loads(rec["raw"])
        except json.JSONDecodeError:
            continue
        if venue == "bybit" and m.get("topic") == "tickers.BTCUSDT" and "markPrice" in m.get("data", {}):
            ts.append(int(m["ts"])); px.append(float(m["data"]["markPrice"]))
        elif venue == "binance" and m.get("data", {}).get("e") == "markPriceUpdate":
            ts.append(int(m["data"]["E"])); px.append(float(m["data"]["p"]))
        elif venue == "okx" and m.get("arg", {}).get("channel") == "mark-price":
            for d in m.get("data", []):
                ts.append(int(d["ts"])); px.append(float(d["markPx"]))
    o = np.argsort(ts)
    return np.array(ts)[o], np.array(px)[o]


def okx_rest_window(t0: int, t1: int) -> list[dict]:
    """OKX REST liquidation-orders for BTC-USDT-SWAP, filled, paged back to t0."""
    rows, after = [], None
    for _ in range(200):
        url = "https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled&limit=100"
        if after:
            url += f"&after={after}"
        j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
        dets = [d for inst in j.get("data", []) if inst.get("instId") == "BTC-USDT-SWAP" for d in inst.get("details", [])]
        if not dets:
            break
        rows += dets
        oldest = min(int(d["ts"]) for d in dets)
        if oldest < t0:
            break
        after = oldest
        time.sleep(0.25)
    return [d for d in rows if t0 <= int(d["ts"]) <= t1]


def run(capdir: Path) -> dict:
    meta = json.loads((capdir / "capture_meta.json").read_text())
    out = {"capture": meta, "venues": {}}
    liqs = {}
    for venue in ("bybit", "binance", "okx"):
        p = capdir / f"{venue}.jsonl"
        lines = p.read_text().splitlines()
        rows = []
        for ln in lines:
            rows += N.parse_capture_line(venue, ln)
        rows = N.dedupe(rows)
        btc = [r for r in rows if r["symbol"] == N.VENUE_SYMBOL[venue]]
        rec_ms = [json.loads(ln).get("recv_ms") for ln in lines]
        rec_ms = [r for r in rec_ms if r]
        span_h = (max(rec_ms) - min(rec_ms)) / 3.6e6
        liq_bytes = sum(len(ln) for ln in lines if "iquidation" in ln or "forceOrder" in ln)
        errors = sum(1 for ln in lines if '"_meta": "error"' in ln)
        per_sec = Counter(r["event_ts_ms"] // 1000 for r in btc)
        out["venues"][venue] = {
            "lines": len(lines), "hours": span_h, "reconnect_errors": errors,
            "liq_events_all_symbols": len(rows), "liq_events_btc": len(btc),
            "btc_per_hour": len(btc) / span_h if span_h else None,
            "btc_notional_usd": float(sum(r["notional_quote"] or 0 for r in btc)),
            "btc_by_side": Counter(r["liquidated_side"] for r in btc),
            "btc_max_events_same_second": max(per_sec.values()) if per_sec else 0,
            "bytes_per_day_all_messages": p.stat().st_size / span_h * 24 if span_h else None,
            "bytes_per_day_liquidation_messages": liq_bytes / span_h * 24 if span_h else None,
        }
        liqs[venue] = btc
    # side semantics: mark move over the 60 s before each BTC event (own venue's mark)
    sem = {}
    for venue in liqs:
        mt, mp = _marks(venue, capdir / f"{venue}.jsonl")
        res = {}
        for side in ("LONG", "SHORT"):
            moves = []
            for r in liqs[venue]:
                if r["liquidated_side"] != side or not len(mt):
                    continue
                t = r["event_ts_ms"]
                i1 = np.searchsorted(mt, t, "right") - 1
                i0 = np.searchsorted(mt, t - 60_000, "right") - 1
                if i0 >= 0 and i1 >= 0 and mt[i1] - mt[i0] > 30_000:
                    moves.append(mp[i1] / mp[i0] - 1)
            res[side] = {"n": len(moves), "share_price_fell": float(np.mean(np.array(moves) < 0)) if moves else None,
                         "median_move_bp": float(np.median(moves) * 1e4) if moves else None}
        sem[venue] = res
    out["side_semantics_mark_move_60s"] = sem
    # OKX RAW check
    ws = liqs["okx"]
    if ws:
        t0, t1 = min(r["event_ts_ms"] for r in ws), max(r["event_ts_ms"] for r in ws)
        rest = okx_rest_window(t0, t1)
        ws_keys = {(r["event_ts_ms"], round(r["price"], 4)) for r in ws}
        rest_keys = {(int(d["ts"]), round(float(d["bkPx"]), 4)) for d in rest}
        out["okx_ws_vs_rest"] = {"window_ms": [t0, t1], "ws_events": len(ws_keys), "rest_events": len(rest_keys),
                                 "matched": len(ws_keys & rest_keys), "rest_only": len(rest_keys - ws_keys),
                                 "ws_only": len(ws_keys - rest_keys)}
    (capdir / "capture_report.json").write_text(json.dumps(out, indent=1, default=lambda o: dict(o)))
    return out


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1])), indent=1, default=lambda o: dict(o)))
