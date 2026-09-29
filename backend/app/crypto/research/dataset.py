"""Aligned 1m research grid for D5.

Every array is indexed by the research grid: one row per UTC minute from the research window
start (inclusive) to the research end (exclusive). Row i describes the bar that *opens* at
``ts[i]`` and closes at ``ts[i] + 60_000``. Nothing here looks forward: slower series are joined
by the time they would have been known at the bar's close, not by their own timestamp.

PIT rules (frozen in CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1 section 3):
- kline / mark / index 1m: known at bar close.
- open_interest_5m record stamped T: treated as known at T + 5 min (one full interval late,
  conservative about what the stamp means).
- funding record stamped T (settlement time): known at T.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.json as pj

MINUTE_MS = 60_000
RESEARCH_START_MS = 1_612_137_600_000  # 2021-02-01T00:00:00Z (CRYPTO_RESEARCH_WINDOW_V1 W1)
RESEARCH_END_MS = 1_790_121_600_000  # 2026-09-23T00:00:00Z exclusive (D2 overlap end, funding-bound)
OI_DELAY_MS = 5 * MINUTE_MS

ROOT = Path("data/runtime/crypto/BTCUSDT/historical")
CACHE = Path("data/runtime/crypto/d5/grid_v1.npz")


def _read(dataset: str, fields: list[str]) -> dict[str, np.ndarray]:
    files = sorted((ROOT / dataset).glob("*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"{dataset}: expected exactly one D2 file, found {len(files)}")
    schema = pa.schema([("timestamp_ms", pa.int64())] + [(f, pa.string()) for f in fields])
    table = pj.read_json(files[0], parse_options=pj.ParseOptions(explicit_schema=schema,
                                                                  unexpected_field_behavior="ignore"))
    out = {"timestamp_ms": table.column("timestamp_ms").to_numpy()}
    for f in fields:
        out[f] = table.column(f).cast(pa.float64()).to_numpy()
    return out


def _on_grid(src: dict[str, np.ndarray], ts: np.ndarray, fields: list[str], name: str) -> dict[str, np.ndarray]:
    idx = np.searchsorted(src["timestamp_ms"], ts)
    ok = (idx < len(src["timestamp_ms"])) & (src["timestamp_ms"][np.minimum(idx, len(idx) and len(src["timestamp_ms"]) - 1)] == ts)
    if not ok.all():
        raise RuntimeError(f"{name}: {int((~ok).sum())} grid minutes missing")
    return {f: src[f][idx] for f in fields}


def _asof(src_ts: np.ndarray, src_val: np.ndarray, known_at: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Last value whose known_at <= query. NaN when nothing is known yet."""
    order = np.argsort(known_at, kind="stable")
    ka, val = known_at[order], src_val[order]
    pos = np.searchsorted(ka, query, side="right") - 1
    out = np.full(len(query), np.nan)
    good = pos >= 0
    out[good] = val[pos[good]]
    return out


def build() -> dict[str, np.ndarray]:
    ts = np.arange(RESEARCH_START_MS, RESEARCH_END_MS, MINUTE_MS, dtype=np.int64)
    close_ms = ts + MINUTE_MS
    k = _read("kline_1m", ["open", "high", "low", "close", "volume", "turnover"])
    grid = _on_grid(k, ts, ["open", "high", "low", "close", "volume", "turnover"], "kline_1m")
    grid["mark_close"] = _on_grid(_read("mark_1m", ["close"]), ts, ["close"], "mark_1m")["close"]
    grid["index_close"] = _on_grid(_read("index_1m", ["close"]), ts, ["close"], "index_1m")["close"]
    oi = _read("open_interest_5m", ["open_interest"])
    grid["oi"] = _asof(oi["timestamp_ms"], oi["open_interest"], oi["timestamp_ms"] + OI_DELAY_MS, close_ms)
    fr = _read("funding", ["funding_rate"])
    grid["funding_last"] = _asof(fr["timestamp_ms"], fr["funding_rate"], fr["timestamp_ms"], close_ms)
    grid["ts"] = ts
    # Funding schedule for cost accounting (not a feature): settlement times + rates in the window.
    keep = (fr["timestamp_ms"] >= RESEARCH_START_MS) & (fr["timestamp_ms"] <= RESEARCH_END_MS + 3 * 3_600_000)
    grid["funding_ts"] = fr["timestamp_ms"][keep]
    grid["funding_rate"] = fr["funding_rate"][keep]
    return grid


def load(rebuild: bool = False) -> dict[str, np.ndarray]:
    if CACHE.exists() and not rebuild:
        with np.load(CACHE) as z:
            return {k: z[k] for k in z.files}
    grid = build()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, **grid)
    return grid


def source_manifest() -> list[dict]:
    rows = []
    for d in ["kline_1m", "mark_1m", "index_1m", "open_interest_5m", "funding"]:
        f = sorted((ROOT / d).glob("*.jsonl"))[0]
        man = Path("data/runtime/crypto/BTCUSDT/manifest") / (f.stem.replace(f.stem, d + "_" + f.stem) + ".json")
        checksum = json.loads(man.read_text())["checksum"] if man.exists() else None
        rows.append({"dataset": d, "file": str(f), "d2_manifest_checksum": checksum})
    return rows


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
