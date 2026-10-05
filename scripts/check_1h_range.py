"""Recompute the tab strip's 1H range from raw Bybit 1m klines, independently of the frontend.

    python3 scripts/check_1h_range.py                       # print today's figures
    python3 scripts/check_1h_range.py --write-fixture PATH   # also refresh the test fixture

This is the check, not the implementation. It shares no code with `lib/crypto-volatility.ts` and
uses Decimal rather than float, so agreement between the two is evidence rather than a tautology.
It reproduces only the two rules the backend already owns: UTC-epoch 1m buckets, and
`confirmed = start_ms + 60_000 <= now`.

The fixture it writes is what `crypto-volatility.test.tsx` asserts the TypeScript against, so the
numbers in that test are a real market reading and not an invented one.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_HALF_UP

REST_URL = "https://api.bybit.com/v5/market/kline"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
BUCKET_MS = 60_000
WINDOW_BARS = 60
#: What the frontend asks for. Enough that the in-progress tail plus a short provider gap still
#: leave 60 confirmed buckets.
REQUEST_BARS = 64


def fetch(symbol: str) -> list[list[str]]:
    query = urllib.parse.urlencode({"category": "linear", "symbol": symbol, "interval": "1",
                                    "limit": REQUEST_BARS})
    with urllib.request.urlopen(f"{REST_URL}?{query}", timeout=25) as response:
        payload = json.load(response)
    if payload.get("retCode") != 0:
        raise RuntimeError(f"Bybit kline failed: {payload.get('retCode')} {payload.get('retMsg')}")
    return payload["result"]["list"]


def bars_of(rows: list[list[str]], now_ms: int) -> list[dict[str, object]]:
    """The rows as `/api/crypto/chart-history?timeframe=1m` returns them: ascending, confirmed."""
    return [{"start_ms": int(row[0]), "open": row[1], "high": row[2], "low": row[3],
             "close": row[4], "volume": row[5], "confirmed": int(row[0]) + BUCKET_MS <= now_ms}
            for row in sorted(rows, key=lambda row: int(row[0]))]


def range_1h(bars: list[dict[str, object]]) -> dict[str, object]:
    confirmed = {int(bar["start_ms"]): bar for bar in bars if bar["confirmed"]}
    if not confirmed:
        return {"state": "UNKNOWN", "reason": "NO_CONFIRMED_BAR"}
    end = max(confirmed)
    window = [end - index * BUCKET_MS for index in reversed(range(WINDOW_BARS))]
    if any(start not in confirmed for start in window):
        return {"state": "UNKNOWN", "reason": "INCOMPLETE_WINDOW"}
    reference = Decimal(str(confirmed[window[0]]["open"]))
    high = max(Decimal(str(confirmed[start]["high"])) for start in window)
    low = min(Decimal(str(confirmed[start]["low"])) for start in window)
    close = Decimal(str(confirmed[window[-1]]["close"]))
    pct = (high - low) / reference * Decimal(100)
    return {
        "state": "COMPLETE",
        "window_start_ms": window[0], "window_end_ms": window[-1],
        "reference": str(reference), "high": str(high), "low": str(low), "close": str(close),
        "range_pct": str(pct),
        "range_pct_2dp": str(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "direction": "UP" if close > reference else "DOWN" if close < reference else "FLAT",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-fixture", metavar="PATH", default=None,
                        help="also write the JSON fixture the frontend test reads")
    args = parser.parse_args()

    now_ms = int(time.time() * 1000)
    fixture: dict[str, object] = {
        "_note": ("Captured Bybit linear 1m klines, shaped exactly as"
                  " GET /api/crypto/chart-history (timeframe=1m, limit=64) returns them, plus the"
                  " expected figures computed independently in Decimal arithmetic by"
                  " scripts/check_1h_range.py. Regenerate with that script."),
        "captured_at_ms": now_ms,
        "symbols": {},
    }
    readings: dict[str, dict[str, object]] = {}
    for symbol in SYMBOLS:
        bars = bars_of(fetch(symbol), now_ms)
        reading = range_1h(bars)
        readings[symbol] = reading
        fixture["symbols"][symbol] = {"bars": bars, "expected": reading}  # type: ignore[index]

    ranked = {symbol: Decimal(reading["range_pct_2dp"])  # type: ignore[arg-type]
              for symbol, reading in readings.items() if reading["state"] == "COMPLETE"}
    hot = sorted(symbol for symbol, shown in ranked.items() if shown == max(ranked.values())) \
        if ranked else []
    fixture["expected_hot"] = hot

    for symbol in SYMBOLS:
        reading = readings[symbol]
        if reading["state"] != "COMPLETE":
            print(f"{symbol:8} --      {reading['reason']}")
            continue
        arrow = {"UP": "UP", "DOWN": "DOWN", "FLAT": "FLAT"}[reading["direction"]]  # type: ignore[index]
        print(f"{symbol:8} {reading['range_pct_2dp']:>6}% {arrow:<4}"
              f"  high {reading['high']}  low {reading['low']}  ref {reading['reference']}"
              f"  [{reading['window_start_ms']}..{reading['window_end_ms']}]")
    print(f"HOT = {', '.join(hot) if hot else '(none)'}")

    if args.write_fixture:
        with open(args.write_fixture, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(fixture, indent=1) + "\n")
        print(f"fixture written: {args.write_fixture}")


if __name__ == "__main__":
    main()
