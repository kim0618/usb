"""Binance USD-M adapter for the C1 recheck: the D5 grid's keys, filled from Binance files only.

Contract: docs/crypto/c1_binance_recheck_v1/CONTRACT_C1_BINANCE_RECHECK_V1.md section 2.
Every path read here lives under the D5.2 Binance archive. The Bybit D2 folder
(`data/runtime/crypto/BTCUSDT/historical`) is never opened; `SOURCES_READ` records each file so a
test can prove it.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import dataset
from .. import derivatives_flow as DF

BIN = DF.BIN
MIN = dataset.MINUTE_MS
FIVE = 5 * MIN
CACHE = Path("data/research/crypto/c1_binance_recheck_v1/cache/binance_grid_v1.npz")
FUNDING_END_MS = 1_788_220_800_000          # 2026-09-01T00:00:00Z: last local Binance funding month is 2026-08
SOURCES_READ: list[str] = []


def _rows(path: Path) -> list[list[str]]:
    SOURCES_READ.append(str(path))
    with zipfile.ZipFile(path) as z:
        text = z.read(z.namelist()[0]).decode()
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] and not rows[0][0].strip()[:1].lstrip("-").isdigit():
        rows = rows[1:]
    return rows


def _klines(sub: str, cols: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    ts, vals = [], []
    for freq in ("monthly", "daily"):
        d = BIN / f"futures/um/{freq}/{sub}/BTCUSDT/1m"
        for p in sorted(d.glob("*.zip")):
            for r in _rows(p):
                ts.append(int(r[0])); vals.append([float(r[c]) for c in cols])
    t = DF.norm_ms(np.array(ts, dtype=np.int64)); v = np.array(vals)
    o = np.argsort(t, kind="stable"); t, v = t[o], v[o]
    keep = np.r_[t[1:] != t[:-1], True]
    return t[keep], v[keep]


def _on_grid(t: np.ndarray, v: np.ndarray, ts: np.ndarray) -> tuple[np.ndarray, int]:
    """Exact-minute join; a missing minute stays NaN (contract section 2: never filled)."""
    idx = np.searchsorted(t, ts)
    ok = (idx < len(t)) & (t[np.clip(idx, 0, len(t) - 1)] == ts)
    out = np.full((len(ts), v.shape[1]), np.nan)
    out[ok] = v[idx[ok]]
    return out, int((~ok).sum())


def funding() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ts, fr, iv = [], [], []
    for p in sorted((BIN / "futures/um/monthly/fundingRate/BTCUSDT").glob("*.zip")):
        for r in _rows(p):
            ts.append(int(r[0])); iv.append(int(r[1])); fr.append(float(r[2]))
    raw = np.array(ts, dtype=np.int64)
    t = (raw // MIN) * MIN                     # calc_time carries +1..+N ms; settlement is the minute
    o = np.argsort(t, kind="stable")
    return t[o], np.array(fr)[o], np.array(iv)[o]


def open_interest() -> tuple[np.ndarray, np.ndarray]:
    ts, oi = [], []
    for p in sorted((BIN / "futures/um/daily/metrics/BTCUSDT").glob("*.zip")):
        for r in _rows(p):
            dt = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            ts.append(int(dt.timestamp() * 1000)); oi.append(float(r[2]) if r[2] else np.nan)
    t = np.array(ts, dtype=np.int64); v = np.array(oi)
    o = np.argsort(t, kind="stable"); t, v = t[o], v[o]
    keep = np.r_[t[1:] != t[:-1], True] & np.isfinite(v)
    return t[keep], v[keep]


def build() -> tuple[dict[str, np.ndarray], dict]:
    ts = np.arange(dataset.RESEARCH_START_MS, dataset.RESEARCH_END_MS, MIN, dtype=np.int64)
    close_ms = ts + MIN
    qc = {}
    t, v = _klines("klines", (1, 2, 3, 4, 5, 7))
    k, miss = _on_grid(t, v, ts); qc["perp_1m_missing"] = miss
    g = {"ts": ts, "open": k[:, 0], "high": k[:, 1], "low": k[:, 2], "close": k[:, 3],
         "volume": k[:, 4], "turnover": k[:, 5]}
    t, v = _klines("markPriceKlines", (2, 3, 4))
    m, miss = _on_grid(t, v, ts); qc["mark_1m_missing"] = miss
    g["mark_high"], g["mark_low"], g["mark_close"] = m[:, 0], m[:, 1], m[:, 2]
    t, v = _klines("indexPriceKlines", (4,))
    x, miss = _on_grid(t, v, ts); qc["index_1m_missing"] = miss
    g["index_close"] = x[:, 0]
    ot, ov = open_interest()
    g["oi"] = dataset._asof(ot, ov, ot + dataset.OI_DELAY_MS, close_ms)
    g["oi_record_ts"] = _oi_stamp(ot, close_ms)
    exp5 = (dataset.RESEARCH_END_MS - dataset.RESEARCH_START_MS) // FIVE
    w = (ot >= dataset.RESEARCH_START_MS) & (ot < dataset.RESEARCH_END_MS)
    qc["oi_5m_records"] = int(w.sum()); qc["oi_5m_expected"] = int(exp5)
    qc["oi_stale_gt15m_minutes"] = int(((close_ms - g["oi_record_ts"]) > 15 * MIN).sum())
    ft, fr, fi = funding()
    g["funding_last"] = dataset._asof(ft, fr, ft, close_ms)
    keep = (ft >= dataset.RESEARCH_START_MS) & (ft <= dataset.RESEARCH_END_MS + 3 * 3_600_000)
    g["funding_ts"], g["funding_rate"] = ft[keep], fr[keep]
    qc["funding_records"] = int(keep.sum()); qc["funding_last_ms"] = int(ft.max())
    qc["funding_interval_hours"] = sorted({int(a) for a in fi})
    qc["funding_off_8h_grid"] = int((ft % (8 * 3_600_000) != 0).sum())
    return g, qc


def _oi_stamp(ot: np.ndarray, close_ms: np.ndarray) -> np.ndarray:
    pos = np.searchsorted(ot + dataset.OI_DELAY_MS, close_ms, side="right") - 1
    out = np.full(len(close_ms), -1, dtype=np.int64)
    out[pos >= 0] = ot[pos[pos >= 0]]
    return out


def load(rebuild: bool = False) -> tuple[dict[str, np.ndarray], dict]:
    import json
    meta = CACHE.with_suffix(".qc.json")
    if CACHE.exists() and meta.exists() and not rebuild:
        with np.load(CACHE) as z:
            return {k: z[k] for k in z.files}, json.loads(meta.read_text())
    g, qc = build()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, **g)
    meta.write_text(json.dumps(qc, indent=1))
    return g, qc


def five_minute_inputs(g: dict) -> dict[str, np.ndarray]:
    """Only what S1 needs, on the D5.2 5m grid: Binance perp last and Binance spot."""
    B = DF.grid5()
    t, c = DF.binance_series("last")
    sp_t, sp_c = DF.binance_spot()
    return {"B": B, "perp_last": DF.carry_one(DF.close5_from_1m(t, c, B)),
            "spot": DF.carry_one(DF.close5_from_1m(sp_t, sp_c, B))}
