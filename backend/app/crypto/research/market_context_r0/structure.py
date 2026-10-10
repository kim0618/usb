"""Layer A: the R1 curated price structure, recomputed on the Binance series.

Nothing about the level book changes here. The R1 engine, the R1 level lifecycle, the R1 retest
pool and the thetas calibrated on R0's warmup window are imported and used as they are; this module
only (a) feeds them the Binance 1m series, (b) keeps every minute's snapshot instead of only the
hourly ones, and (c) adds the previous UTC session high and low as a plain context field that never
enters the level book.

The thetas are the frozen pair from R0's held out calibration. ``warmup_theta_ladder`` re-runs that
same held out procedure on the Binance warmup window as a transfer check: it reports whether the
procedure would have chosen the same pair, and the frozen pair is used either way.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..price_structure_r0.data import Bars
from ..price_structure_r0.swings import THETA_LADDER, ZigZag
from ..price_structure_r1.engine import Engine, EngineConfig
from . import data

#: Frozen by R0's warmup calibration (``data/research/crypto/price_structure_r0/calibration_v1.json``).
THETA_1H = 3.0
THETA_5M = 6.0

DAY_MS = 86_400_000
MINUTE_MS = 60_000


def warmup_theta_ladder() -> dict:
    """R0 contract section 16 procedure, re-run on the Binance warmup window. Reported, not used."""
    bars = data.load()
    end = data.REPLAY_START_MS
    target = {"1h": 2.0, "5m": 8.0}
    out = {}
    for tf in ("1h", "5m"):
        rows = []
        b = bars[tf]
        for th in THETA_LADDER:
            zz = ZigZag(tf, th, label_frame=(tf == "1h"))
            n = 0
            for i in range(len(b)):
                if b.close_ms(i) > end:
                    break
                n += len(zz.on_bar(b, i))
            days = (end - int(b.ts[0])) / DAY_MS
            rows.append({"theta": th, "confirmations": n, "per_day": round(n / days, 3)})
        ok = [r for r in rows if r["per_day"] <= target[tf]]
        pick = (ok[0] if ok else rows[-1])["theta"]
        out[tf] = {"ladder": rows, "would_choose": pick, "frozen": THETA_1H if tf == "1h" else THETA_5M,
                   "agrees": pick == (THETA_1H if tf == "1h" else THETA_5M)}
    return out


def prev_session_levels(b1: Bars) -> dict[int, dict]:
    """High and low of the previous UTC day, keyed by the day start it becomes known at.

    R2 measured this source as worth one reversal out of twenty seven when merged into the book, so
    it stays out of the book. It is carried as context only, exactly as the task allows.
    """
    day_of = (b1.ts // DAY_MS) * DAY_MS
    out: dict[int, dict] = {}
    days = np.unique(day_of)
    for prev, cur in zip(days[:-1], days[1:]):
        m = day_of == prev
        out[int(cur)] = {"prev_session_high": round(float(b1.high[m].max()), 2),
                         "prev_session_low": round(float(b1.low[m].min()), 2),
                         "prev_session_start_ms": int(prev)}
    return out


def _lvl(v: dict | None, tag: str) -> dict:
    if v is None:
        return {f"{tag}_price": None, f"{tag}_distance_pct": None, f"{tag}_lifecycle": None,
                f"{tag}_strength": None, f"{tag}_touches": None, f"{tag}_confirm_tf": None,
                f"{tag}_sources": None, f"{tag}_origin": None, f"{tag}_id": None,
                f"{tag}_age_ms": None}
    return {f"{tag}_price": v["price"], f"{tag}_distance_pct": v["distance_pct"],
            f"{tag}_lifecycle": v["lifecycle"], f"{tag}_strength": v["strength"],
            f"{tag}_touches": v["touches"], f"{tag}_confirm_tf": v["confirm_tf"],
            f"{tag}_sources": ",".join(v["sources"]), f"{tag}_origin": v["origin_kind"],
            f"{tag}_id": v["id"], f"{tag}_age_ms": None}


@dataclass
class ContextRow:
    pass


class ContextEngine(Engine):
    """R1's engine, keeping a compact projection of every published minute."""

    def __init__(self, bars: dict[str, Bars], cfg: EngineConfig) -> None:
        Engine.__init__(self, bars, cfg)
        self.rows: list[dict] = []
        self._prev_sessions = prev_session_levels(bars["1m"])
        self._session_cache: dict | None = None
        self._session_day: int | None = None

    def snapshot(self, i: int, at_ms: int) -> dict:
        snap = Engine.snapshot(self, i, at_ms)
        ts = snap["ts"]
        day = (ts // DAY_MS) * DAY_MS
        if day != self._session_day:
            self._session_day = day
            self._session_cache = self._prev_sessions.get(day)
        ps = self._session_cache
        cl = snap["close"]
        bo = snap["last_breakout"]
        sw = snap["last_sweep"]
        rt = snap["retest"]
        row = {"ts": ts, "info_ts": at_ms, "close": cl,
               "trend_structure": snap["trend_structure"],
               "last_high_label": snap["last_high_label"],
               "last_low_label": snap["last_low_label"],
               "range_state": snap["range_state"],
               "range_high": snap["range_high"], "range_low": snap["range_low"],
               "range_position": None,
               "retest_state": snap["retest_state"],
               "retest_level_id": rt["level_id"] if rt else None,
               "retest_direction": rt["direction"] if rt else None,
               "failed_break_state": snap["failed_break_state"],
               "last_breakout_level_id": bo["level_id"] if bo else None,
               "last_breakout_direction": bo["direction"] if bo else None,
               "last_breakout_at": bo["confirmed_at"] if bo else None,
               "last_breakout_age_min": bo["age_minutes"] if bo else None,
               "last_breakout_confirm_tf": bo["tf"] if bo else None,
               "last_sweep_direction": sw["direction"] if sw else None,
               "last_sweep_at": sw["confirmed_at"] if sw else None,
               "last_sweep_age_min": sw["age_minutes"] if sw else None,
               "last_sweep_level_id": sw["level_id"] if sw else None,
               "vwap_z": snap["vwap_z"], "vwap_position": snap["vwap_position"],
               "active_level_count": snap["active_level_count"],
               "atr_5m": snap["atr_5m"], "atr_1h": snap["atr_1h"],
               "published_prices": [l["price"] for l in snap["published_levels"]]}
        row.update(_lvl(snap["nearest_resistance"], "res"))
        row.update(_lvl(snap["nearest_support"], "sup"))
        if ps:
            row["prev_session_high"] = ps["prev_session_high"]
            row["prev_session_low"] = ps["prev_session_low"]
            row["prev_session_high_distance_pct"] = round(
                (ps["prev_session_high"] - cl) / cl * 100.0, 4)
            row["prev_session_low_distance_pct"] = round(
                (ps["prev_session_low"] - cl) / cl * 100.0, 4)
        else:
            row["prev_session_high"] = row["prev_session_low"] = None
            row["prev_session_high_distance_pct"] = row["prev_session_low_distance_pct"] = None
        if row["range_high"] is not None and row["range_low"] is not None \
                and row["range_high"] > row["range_low"]:
            row["range_position"] = round(
                (cl - row["range_low"]) / (row["range_high"] - row["range_low"]), 4)
        self.rows.append(row)
        return snap


def run(from_ms: int, to_ms: int) -> ContextEngine:
    bars = data.load()
    eng = ContextEngine(bars, EngineConfig(THETA_1H, THETA_5M, from_ms, to_ms))
    eng.run()
    return eng
