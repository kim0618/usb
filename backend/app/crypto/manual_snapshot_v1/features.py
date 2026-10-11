"""Snapshot fields (contract section 4), every one computed from data known at `as_of_ms`."""
from __future__ import annotations

import bisect
import json

from .market import MINUTE_MS, BudgetExhausted, Market, VenueBackoff

LOOKBACK = 20
ATR_PERIOD = 14
TF_MS = {"15s": 15_000, "1m": 60_000, "10m": 600_000, "1h": 3_600_000, "4h": 14_400_000,
         "1d": 86_400_000}


def _fetch_reason(exc: Exception) -> str:
    """The venue's own error code when it gave one (aggTrades answers -4166 for anything older
    than two days), else the exception type."""
    try:
        body = json.loads(exc.read())          # urllib HTTPError
        return f"VENUE_{body.get('code')}:{body.get('msg')}"
    except Exception:
        return f"FETCH:{type(exc).__name__}"


def wilder_atr(bars: list[tuple]) -> float | None:
    """Wilder ATR(14) of the last bar, as Price Structure R0 computes it; None under 15 bars."""
    if len(bars) < ATR_PERIOD + 1:
        return None
    tr = [max(b[2] - b[3], abs(b[2] - a[4]), abs(b[3] - a[4])) for a, b in zip(bars, bars[1:])]
    atr = sum(tr[:ATR_PERIOD]) / ATR_PERIOD
    for x in tr[ATR_PERIOD:]:
        atr = (atr * (ATR_PERIOD - 1) + x) / ATR_PERIOD
    return atr


def tf_features(bars: list[tuple]) -> dict:
    """The last LOOKBACK closed bars of one timeframe. `bars` are already cut at as_of."""
    if not bars:
        return {"status": "NOT_AVAILABLE", "reason": "NO_CLOSED_BARS"}
    w = bars[-LOOKBACK:]
    c0, c1 = w[0][4], w[-1][4]
    hi, lo = max(b[2] for b in w), min(b[3] for b in w)
    atr = wilder_atr(bars[-(LOOKBACK + ATR_PERIOD):])
    return {"status": "OK" if len(w) == LOOKBACK else "PARTIAL", "bars": len(w),
            "last_open_ms": int(w[-1][0]),
            "ret_bp": round((c1 / c0 - 1) * 1e4, 4), "range_bp": round((hi - lo) / c1 * 1e4, 4),
            "atr14": None if atr is None else round(atr, 4),
            "volume": round(sum(b[5] for b in w), 6),
            "dist_high_bp": round((hi - c1) / c1 * 1e4, 4), "dist_low_bp": round((c1 - lo) / c1 * 1e4, 4)}


def aggregate(bars_1m: list[tuple], step_ms: int) -> list[tuple]:
    """Complete UTC-aligned groups of 1m bars only."""
    out, cur = [], None
    need = step_ms // MINUTE_MS
    for b in bars_1m:
        k = (b[0] // step_ms) * step_ms
        if cur is None or cur[0] != k:
            if cur is not None and cur[6] == need:
                out.append(tuple(cur[:6]))
            cur = [k, b[1], b[2], b[3], b[4], b[5], 1]
        else:
            cur[2] = max(cur[2], b[2]); cur[3] = min(cur[3], b[3]); cur[4] = b[4]
            cur[5] += b[5]; cur[6] += 1
    if cur is not None and cur[6] == need:
        out.append(tuple(cur[:6]))
    return out


def closed_upto(bars: list[tuple], step_ms: int, as_of_ms: int) -> list[tuple]:
    """Bars whose close (open + step) is at or before as_of."""
    keys = [b[0] for b in bars]
    i = bisect.bisect_right(keys, as_of_ms - step_ms)
    return bars[:i]


class Window:
    """All series one run needs, fetched once over [start, end], then cut per snapshot."""

    def __init__(self, market: Market, start_ms: int, end_ms: int,
                 path_from_ms: int | None = None) -> None:
        self.m = market
        span = (LOOKBACK + ATR_PERIOD + 2)
        # 1m bars back far enough for the 10m features and for every MFE/MAE path that starts
        # earlier (a hold snapshot's path runs from the episode's open).
        first_1m = start_ms - span * 600_000
        if path_from_ms is not None:
            first_1m = min(first_1m, (path_from_ms // MINUTE_MS) * MINUTE_MS)
        self.b1m = market.klines("1m", first_1m, end_ms)
        self.b1h = market.klines("1h", start_ms - span * 3_600_000, end_ms)
        self.b4h = market.klines("4h", start_ms - span * 14_400_000, end_ms)
        self.b1d = market.klines("1d", start_ms - span * 86_400_000, end_ms)
        self.mark = market.klines("1m", start_ms - 10 * MINUTE_MS, end_ms, kind="mark")
        self.index = market.klines("1m", start_ms - 10 * MINUTE_MS, end_ms, kind="index")
        self.fund = market.funding(start_ms - 3 * 86_400_000, end_ms)
        self.oi = market.open_interest(start_ms - 2 * 3_600_000, end_ms)
        self.b10m = aggregate(self.b1m, 600_000)

    def series(self, tf: str) -> list[tuple]:
        return {"1m": self.b1m, "10m": self.b10m, "1h": self.b1h, "4h": self.b4h, "1d": self.b1d}[tf]

    def last_close(self, as_of_ms: int) -> float | None:
        b = closed_upto(self.b1m, MINUTE_MS, as_of_ms)
        return b[-1][4] if b else None

    def market_fields(self, as_of_ms: int) -> tuple[dict, str, str | None]:
        out: dict = {}
        b = closed_upto(self.b1m, MINUTE_MS, as_of_ms)
        out["last_1m_close"] = b[-1][4] if b else None
        out["last_1m_open_ms"] = int(b[-1][0]) if b else None
        mk = closed_upto(self.mark, MINUTE_MS, as_of_ms)
        ix = closed_upto(self.index, MINUTE_MS, as_of_ms)
        out["mark"] = mk[-1][4] if mk else None
        out["index"] = ix[-1][4] if ix else None
        out["basis_bp"] = (round((out["mark"] - out["index"]) / out["index"] * 1e4, 4)
                           if out["mark"] and out["index"] else None)
        f = [r for r in self.fund if r[0] <= as_of_ms]
        out["funding_rate_last"] = f[-1][1] if f else None
        out["funding_time_last"] = f[-1][0] if f else None
        out["next_funding_ms"] = ((as_of_ms // 28_800_000) + 1) * 28_800_000
        oi = [r for r in self.oi if r[0] + 300_000 <= as_of_ms]
        out["oi_last_value_usdt"] = oi[-1][1] if oi else None
        hour_ago = [r for r in oi if r[0] <= oi[-1][0] - 3_600_000] if oi else []
        out["oi_change_1h_pct"] = (round((oi[-1][1] / hour_ago[-1][1] - 1) * 100, 4)
                                   if oi and hour_ago and hour_ago[-1][1] else None)
        missing = [k for k in ("last_1m_close", "mark", "index") if out[k] is None]
        if missing:
            return out, "NOT_AVAILABLE", "MISSING:" + ",".join(missing)
        deriv_missing = [k for k in ("funding_rate_last", "oi_last_value_usdt") if out[k] is None]
        return out, ("PARTIAL" if deriv_missing else "OK"), (
            "MISSING:" + ",".join(deriv_missing) if deriv_missing else None)

    def timeframes(self, as_of_ms: int, with_15s: bool) -> dict:
        out = {}
        if with_15s:
            try:
                out["15s"] = tf_features(self.m.agg_bars_15s(as_of_ms, LOOKBACK + ATR_PERIOD))
            except (BudgetExhausted, VenueBackoff):
                raise                      # transient: the snapshot waits, it is not written as absent
            except Exception as exc:       # the venue refused (e.g. older than 2 days): absent
                out["15s"] = {"status": "NOT_AVAILABLE", "reason": _fetch_reason(exc)}
        else:
            out["15s"] = {"status": "NOT_COLLECTED", "reason": "EVENT_SNAPSHOTS_ONLY"}
        for tf in ("1m", "10m", "1h", "4h", "1d"):
            out[tf] = tf_features(closed_upto(self.series(tf), TF_MS[tf], as_of_ms))
        return out

    def path(self, start_ms: int, end_ms: int, ref: float, direction: str) -> dict:
        """MFE/MAE in bp vs ref over the 1m bars that overlap [start, end]; the end minutes are
        flagged because the order of high and low inside them is unknown."""
        s0 = (start_ms // MINUTE_MS) * MINUTE_MS
        e0 = (end_ms // MINUTE_MS) * MINUTE_MS
        bars = [b for b in self.b1m if s0 <= b[0] <= e0]
        if not bars or not ref:
            return {"mfe_bp": None, "mae_bp": None, "extreme_in_fill_minute": None, "bars": 0}
        d = 1 if direction == "LONG" else -1
        hi = max(bars, key=lambda b: b[2]); lo = min(bars, key=lambda b: b[3])
        fav, adv = (hi, lo) if d == 1 else (lo, hi)
        mfe = (hi[2] - ref) / ref * 1e4 if d == 1 else (ref - lo[3]) / ref * 1e4
        mae = (ref - lo[3]) / ref * 1e4 if d == 1 else (hi[2] - ref) / ref * 1e4
        ends = {s0, e0}
        return {"mfe_bp": round(max(mfe, 0.0), 4), "mae_bp": round(max(mae, 0.0), 4),
                "extreme_in_fill_minute": fav[0] in ends or adv[0] in ends, "bars": len(bars)}
