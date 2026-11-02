"""BTC TSMOM V3 앙상블 섀도우 실행기. 주문 없음, 인증키 없음(공개 REST만).

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_btc_tsmom_shadow [--capital-krw 100000] [--status] [--store DIR]

매 실행마다 바이낸스 USDⓈ-M BTCUSDT 4시간봉(최근 300개)과 펀딩비를 받아,
마지막 기록 이후 마감된 봉들을 순서대로 반영한다(멱등: 같은 봉을 두 번 반영하지 않음).
장부: data/runtime/crypto/tsmom_shadow/ledger.jsonl (봉 1개 = 1줄).
수익 = 비중 × 봉 수익률 − 비중 × 펀딩비 − 0.05% × |비중 변화| (V3 사전등록과 같은 계산).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import httpx

from app.crypto.tsmom.signal import H4_MS, weights

BASE = "https://fapi.binance.com"
FEE = 0.0005
ROOT = Path(__file__).resolve().parents[3] / "data/runtime/crypto/tsmom_shadow"
LEDGER = ROOT / "ledger.jsonl"


def set_store(path: str) -> None:
    global ROOT, LEDGER
    ROOT = Path(path)
    LEDGER = ROOT / "ledger.jsonl"


def fetch(client: httpx.Client):
    k = client.get(f"{BASE}/fapi/v1/klines", params={"symbol": "BTCUSDT", "interval": "4h", "limit": 300}).json()
    now = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    bars = [(r[0], float(r[4])) for r in k if r[0] + H4_MS <= now]          # 마감된 봉만
    f = client.get(f"{BASE}/fapi/v1/fundingRate",
                   params={"symbol": "BTCUSDT", "startTime": bars[0][0], "limit": 1000}).json()
    funding = [(x["fundingTime"], float(x["fundingRate"])) for x in f]
    return bars, funding


def load():
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")


def step(capital_krw: float) -> dict:
    with httpx.Client(timeout=20, headers={"User-Agent": "usb-tsmom-shadow/1"}) as c:
        bars, funding = fetch(c)
    ws = weights(bars)
    led = load()
    ROOT.mkdir(parents=True, exist_ok=True)
    last_end = led[-1]["bar_end_ms"] if led else None
    eq = led[-1]["equity"] if led else 1.0
    w_prev = led[-1]["weight_next"] if led else None
    added = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        first = len(bars) - 1 if last_end is None else 1     # 첫 실행은 가장 최근 마감 봉에서 시작
        for i in range(first, len(bars)):
            end = bars[i][0] + H4_MS
            if last_end is not None and end <= last_end:
                continue
            w_dec = ws[i]
            if w_dec is None:
                continue
            if w_prev is None:                       # 첫 기록: 이 봉 마감에서 시작, 수익 없음
                rec = {"bar_end_ms": end, "bar_end": iso(end), "close": bars[i][1], "weight_held": 0.0,
                       "bar_return": 0.0, "funding": 0.0, "fee": FEE * w_dec.weight, "equity": eq * (1 - FEE * w_dec.weight),
                       "weight_next": w_dec.weight, "longs": w_dec.longs}
            else:
                r = bars[i][1] / bars[i - 1][1] - 1
                fsum = sum(x for t, x in funding if bars[i - 1][0] + H4_MS < t <= end)
                fee = FEE * abs(w_dec.weight - w_prev)
                eq = eq * (1 + w_prev * r - w_prev * fsum - fee)
                rec = {"bar_end_ms": end, "bar_end": iso(end), "close": bars[i][1], "weight_held": w_prev,
                       "bar_return": r, "funding": fsum, "fee": fee, "equity": eq, "weight_next": w_dec.weight,
                       "longs": w_dec.longs}
            eq = rec["equity"]
            w_prev = rec["weight_next"]
            fh.write(json.dumps(rec) + "\n")
            added += 1
            if last_end is None:                     # 첫 실행은 가장 최근 봉 하나만 시작점으로
                break
    led = load()
    cur = led[-1]
    return {"added_bars": added, "as_of": cur["bar_end"], "btc_close": cur["close"], "longs_of_36": cur["longs"],
            "target_weight": cur["weight_next"], "target_krw": round(capital_krw * cur["equity"] * cur["weight_next"]),
            "equity_krw": round(capital_krw * cur["equity"]), "bars_in_ledger": len(led), "orders_sent": 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital-krw", type=float, default=100_000)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--store", default=None, help="장부 디렉터리(기본 data/runtime/crypto/tsmom_shadow)")
    a = ap.parse_args(argv)
    if a.store:
        set_store(a.store)
    if a.status:
        led = load()
        print(json.dumps(led[-1] if led else {"ledger": "empty"}, ensure_ascii=False))
        return 0
    print(json.dumps(step(a.capital_krw), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
