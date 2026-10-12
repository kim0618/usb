"""Aggressive buy/sell flow over trailing 10 m / 1 h / 4 h / 1 d, exact, outside the API process.

**Source.** Binance USD-M public 1m klines (`/fapi/v1/klines`, already on `market_context_v1.venue`'s
allow-list and already read once a minute by `structure_worker`). For each closed minute the venue
reports the quote volume and the *taker buy* quote volume: the notional of trades in which the buyer
was the aggressor. Aggressive sell notional is quote volume minus taker buy. These are the venue's
own per-minute sums, so minutes never overlap and a trailing window is exactly the sum of whole
minutes.

**Why not the Context Collector's journal.** Its `context` rows carry rolling 5/15/60 s windows
measured on the collector's receipt clock. Summing them double counts; tiling with 60 s windows is
not exact either (sample spacing varies by +-1.6 ms on the monotonic clock and a sample goes missing
for seconds now and then), and the raw trades are dropped by design. See
`docs/crypto/btc_flow_horizon_v1/SOURCE_AUDIT.md`.

**What is published.** For each horizon the buy and sell notional, their shares, a `lean`
(BUY / SELL / EVEN, from the exact notionals, so an integer-rounded 50/50 is not mistaken for a tie)
and the coverage counted in minutes. A horizon with a missing minute, or a zero total, publishes
`None` figures. There is no threshold here: coverage must be complete, which is the definition of
"the sum of the last N minutes", not a cut-off.

**Cost.** One small JSON (`flow_horizons.json`) is all the API reads. The worker keeps 1440 minute
rows (`flow_series.json`, about 60 kB) and, after the first build (one klines page), fetches only
the minutes that closed since the last run. No journal is read, no writer is touched.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..market_context_v1 import contract as MC
from ..market_context_v1 import venue as VN

SYMBOL = "BTCUSDT"
MINUTE_MS = 60_000
#: Trailing windows, in minutes. Fixed; independent of the chart's timeframe.
HORIZONS: tuple[tuple[str, int], ...] = (("10m", 10), ("1h", 60), ("4h", 240), ("1d", 1440))
SERIES_MINUTES = max(minutes for _, minutes in HORIZONS)

SERIES_NAME = "flow_series.json"
SNAPSHOT_NAME = "flow_horizons.json"
LOCK_NAME = ".flow_horizon_worker.lock"
#: The API reads this file only; it holds six small numbers per horizon.
SNAPSHOT_MAX_BYTES = 64 * 1024

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
UNKNOWN = "UNKNOWN"

Row = tuple[int, str, str]          # (open_ms, quote_volume, taker_buy_quote_volume)


def last_closed_end_ms(now_ms: int) -> int:
    """The open time of the minute in progress = the close of the last closed minute."""
    return now_ms - now_ms % MINUTE_MS


def _dec(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _shares(buy: Decimal, sell: Decimal) -> tuple[int, int]:
    """Whole-percent shares that add to 100 (the larger side keeps its rounding)."""
    total = buy + sell
    buy_pct = int((buy / total * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return buy_pct, 100 - buy_pct


def horizon_view(rows: dict[int, Row], end_ms: int, minutes: int) -> dict[str, Any]:
    """One trailing window: the closed minutes in `[end_ms - minutes*60 s, end_ms)`."""
    expected = minutes
    start = end_ms - minutes * MINUTE_MS
    present = 0
    buy = sell = Decimal(0)
    for open_ms in range(start, end_ms, MINUTE_MS):
        row = rows.get(open_ms)
        if row is None:
            continue
        quote, taker_buy = _dec(row[1]), _dec(row[2])
        if quote is None or taker_buy is None or taker_buy > quote:
            continue                                     # an unreadable minute is a missing one
        present += 1
        buy += taker_buy
        sell += quote - taker_buy
    out: dict[str, Any] = {
        "minutes": minutes, "expected_seconds": expected * 60, "coverage_seconds": present * 60,
        "coverage_ratio": round(present / expected, 4),
        "buy_notional": None, "sell_notional": None, "buy_pct": None, "sell_pct": None,
        "lean": None, "quality": COMPLETE if present == expected else PARTIAL,
    }
    if present != expected:
        return out
    if buy + sell == 0:
        out["quality"] = UNKNOWN
        return out
    buy_pct, sell_pct = _shares(buy, sell)
    out.update(buy_notional=_money(buy), sell_notional=_money(sell), buy_pct=buy_pct,
               sell_pct=sell_pct, lean="BUY" if buy > sell else "SELL" if sell > buy else "EVEN")
    return out


def snapshot_payload(rows: dict[int, Row], end_ms: int, *, written_ms: int) -> dict[str, Any]:
    return {"symbol": SYMBOL, "version": "btc-flow-horizon.v1", "end_ms": end_ms,
            "written_ms": written_ms,
            "horizons": {label: horizon_view(rows, end_ms, minutes) for label, minutes in HORIZONS}}


# --------------------------------------------------------------------------- files


def _atomic_write(path: Path, payload: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_series(path: Path) -> dict[int, Row]:
    try:
        body = json.loads(path.read_text())
        if body.get("symbol") != SYMBOL:
            return {}
        return {int(r[0]): (int(r[0]), str(r[1]), str(r[2])) for r in body["rows"]}
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return {}


def save_series(path: Path, rows: dict[int, Row]) -> None:
    _atomic_write(path, {"symbol": SYMBOL, "rows": [list(rows[k]) for k in sorted(rows)]})


# --------------------------------------------------------------------------- worker


def refresh(state_dir: Path, *, now_ms: int, opener: Any = None) -> dict[str, Any]:
    """One refresh under the caller's lock. Public klines only; writes two files."""
    end = last_closed_end_ms(now_ms)
    floor = end - SERIES_MINUTES * MINUTE_MS
    series_path = state_dir / SERIES_NAME
    rows = {k: v for k, v in load_series(series_path).items() if floor <= k < end}
    start = floor if not rows else max(floor, max(rows) + MINUTE_MS)
    fetched = 0
    if start < end:
        try:
            bars = VN.klines(SYMBOL, start, end, opener=opener)
        except VN.VenueError as caught:
            # The venue has not published the minute that just closed yet: nothing new, not a fault.
            if not str(caught).startswith("KLINES_EMPTY_PAGE") or not rows:
                raise
            bars = []
        for bar in bars:
            rows[int(bar["timestamp_ms"])] = (int(bar["timestamp_ms"]), str(bar["turnover"]),
                                              str(bar["taker_buy_quote"]))
            fetched += 1
        if bars:
            save_series(series_path, rows)
    # The window ends at the newest minute the venue has published, so a venue that is a few
    # seconds late does not blank the screen; a venue that stays away ages the snapshot (stale).
    end = min(end, max(rows) + MINUTE_MS) if rows else end
    payload = snapshot_payload(rows, end, written_ms=int(time.time() * 1000))
    _atomic_write(state_dir / SNAPSHOT_NAME, payload)
    return {"result": "UPDATED" if fetched else "UP_TO_DATE", "fetched": fetched, "rows": len(rows),
            "end_ms": end,
            "coverage": {k: v["coverage_ratio"] for k, v in payload["horizons"].items()}}


def run_once(state_dir: Path, *, now_ms: int | None = None, opener: Any = None) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"result": "LOCKED"}
        try:
            return refresh(state_dir, now_ms=int(time.time() * 1000) if now_ms is None else now_ms,
                           opener=opener)
        except VN.VenueError as caught:
            # The snapshot on disk ages and the API reads it as stale: nothing is invented.
            return {"result": "VENUE_ERROR", "error": str(caught)}
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- API side


def read_snapshot(path: Path | None) -> dict[str, Any] | None:
    """The worker's last snapshot, or `None` if missing, oversized, not JSON or not BTC."""
    if path is None:
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read(SNAPSHOT_MAX_BYTES + 1)
        if len(raw) > SNAPSHOT_MAX_BYTES:
            return None
        snap = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict) or snap.get("symbol") != SYMBOL:
        return None
    if not isinstance(snap.get("end_ms"), int) or not isinstance(snap.get("horizons"), dict):
        return None
    return snap


def horizons_section(snap: dict[str, Any] | None, now_ms: int) -> dict[str, Any]:
    """What `/snapshot` publishes as `flow_horizons`.

    LIVE while the newest closed minute is within Market Context V1's own structure grace (the same
    rule STRUCTURE uses: one minute plus `STRUCTURE_GRACE_MS`). Otherwise every horizon's figures
    are `None` and the screen shows `--`.
    """
    empty = {label: {"minutes": minutes, "buy_notional": None, "sell_notional": None,
                     "buy_pct": None, "sell_pct": None, "lean": None, "quality": UNKNOWN,
                     "coverage_seconds": None, "expected_seconds": minutes * 60,
                     "coverage_ratio": None}
             for label, minutes in HORIZONS}
    if snap is None:
        return {"live": False, "end_ms": None, "horizons": empty}
    age_ms = max(0, now_ms - int(snap["end_ms"]))
    live = age_ms <= MINUTE_MS + MC.STRUCTURE_GRACE_MS
    if not live:
        return {"live": False, "end_ms": int(snap["end_ms"]), "horizons": empty}
    horizons = {}
    for label, minutes in HORIZONS:
        item = snap["horizons"].get(label)
        horizons[label] = item if isinstance(item, dict) else empty[label]
    return {"live": True, "end_ms": int(snap["end_ms"]), "horizons": horizons}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.btc_context_ui_v1.flow_horizon")
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    started = time.monotonic()
    outcome = run_once(Path(args.state_dir).expanduser().resolve())
    outcome["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    print(json.dumps(outcome, sort_keys=True), flush=True)
    return 0 if outcome["result"] in ("UPDATED", "UP_TO_DATE", "LOCKED") else 1


if __name__ == "__main__":
    sys.exit(main())
