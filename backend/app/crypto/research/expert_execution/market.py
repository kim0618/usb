"""E1 market data: BitMEX public archive (trade, quote) -> XBTUSD-only parquet per UTC day.

Each day file holds every symbol, so it is downloaded whole, parsed as a stream (batch by batch,
filtering XBTUSD), written compactly, hashed and deleted. The per-day manifest keeps bytes, ETag and
sha256 of the original so the raw set can be re-fetched and verified. Resumable: a day whose
manifest exists is skipped.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.expert_execution.market --workers 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

ARCHIVE = "https://s3-eu-west-1.amazonaws.com/public.bitmex.com"
SYMBOL = "XBTUSD"
E1 = Path("data/research/expert_execution/e1")
MARKET = E1 / "market"
RAW_TMP = E1 / "raw_tmp"
FIRST_DAY, LAST_DAY = date(2018, 3, 4), date(2022, 1, 1)

QUOTE_TYPES = {"timestamp": pa.string(), "symbol": pa.string(), "bidSize": pa.int64(),
               "bidPrice": pa.float64(), "askPrice": pa.float64(), "askSize": pa.int64()}
TRADE_TYPES = {"timestamp": pa.string(), "symbol": pa.string(), "side": pa.string(), "size": pa.int64(),
               "price": pa.float64(), "tickDirection": pa.string(), "trdMatchID": pa.string(),
               "grossValue": pa.int64(), "homeNotional": pa.float64(), "foreignNotional": pa.float64()}


def days(first: date = FIRST_DAY, last: date = LAST_DAY) -> list[str]:
    return [(first + timedelta(i)).strftime("%Y%m%d") for i in range((last - first).days + 1)]


def list_archive(kind: str) -> dict[str, dict]:
    out, tok = {}, None
    while True:
        url = f"{ARCHIVE}?list-type=2&prefix=data/{kind}/&max-keys=1000"
        if tok:
            url += "&continuation-token=" + urllib.parse.quote(tok)
        x = urllib.request.urlopen(url, timeout=60).read().decode()
        for k, m, e, s in re.findall(
                r"<Key>([^<]*)</Key><LastModified>([^<]*)</LastModified><ETag>([^<]*)</ETag><Size>(\d+)", x):
            d = re.search(r"/(\d{8})\.csv\.gz$", k)
            if d:
                out[d.group(1)] = {"key": k, "last_modified": m, "etag": e.replace("&quot;", "").strip('"'),
                                   "size": int(s)}
        t = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", x)
        if not t:
            return out
        tok = t.group(1)


def fetch(key: str, dest: Path, expected_size: int, retries: int = 5) -> str:
    """Download to dest, check the byte count against the listing, return sha256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            h = hashlib.sha256()
            with urllib.request.urlopen(f"{ARCHIVE}/{key}", timeout=120) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    h.update(chunk)
                    f.write(chunk)
            if dest.stat().st_size != expected_size:
                raise IOError(f"size {dest.stat().st_size} != listing {expected_size}")
            return h.hexdigest()
        except Exception:  # noqa: BLE001 (network: retry, then raise)
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _ts_ns(col: pa.ChunkedArray | pa.Array) -> np.ndarray:
    """'2018-03-05D00:00:03.612716000' -> int64 ns UTC."""
    return pc.cast(pc.replace_substring(col, "D", "T"), pa.timestamp("ns")).to_numpy().astype(np.int64)


def _stream_filter(path: Path, types: dict) -> tuple[pa.Table, int]:
    reader = pacsv.open_csv(path, read_options=pacsv.ReadOptions(block_size=64 << 20),
                            convert_options=pacsv.ConvertOptions(column_types=types))
    parts, total = [], 0
    for batch in reader:
        total += batch.num_rows
        parts.append(batch.filter(pc.equal(batch.column("symbol"), SYMBOL)))
    tbl = pa.Table.from_batches(parts, schema=reader.schema) if parts else reader.schema.empty_table()
    return tbl, total


def clean_quotes(ts: np.ndarray, bid, ask, bsz, asz) -> tuple[dict, dict]:
    """Drop exact duplicate rows, then keep the LAST row of each identical timestamp (file order).
    Sort (stable) if the file is not time-ordered. Crossed/locked rows are kept but counted; the
    analysis treats them as invalid quotes."""
    n = len(ts)
    stats = {"rows": int(n), "nonmonotonic_steps": int((np.diff(ts) < 0).sum()) if n > 1 else 0}
    if stats["nonmonotonic_steps"]:
        o = np.argsort(ts, kind="stable")
        ts, bid, ask, bsz, asz = ts[o], bid[o], ask[o], bsz[o], asz[o]
    rec = pd.DataFrame({"ts": ts, "bid": bid, "ask": ask, "bid_size": bsz, "ask_size": asz})
    dup = rec.duplicated()
    stats["exact_duplicates"] = int(dup.sum())
    rec = rec[~dup]
    last_of_ts = np.r_[rec.ts.to_numpy()[1:] != rec.ts.to_numpy()[:-1], True] if len(rec) else np.array([], bool)
    stats["same_ts_superseded"] = int((~last_of_ts).sum())
    rec = rec[last_of_ts]
    stats["kept"] = int(len(rec))
    stats["crossed_or_locked"] = int((rec.bid >= rec.ask).sum())
    stats["nonpositive"] = int(((rec.bid <= 0) | (rec.ask <= 0)).sum())
    g = np.diff(rec.ts.to_numpy()) / 1e9
    stats["max_gap_s"] = float(g.max()) if len(g) else None
    stats["gaps_over_60s"] = int((g > 60).sum()) if len(g) else 0
    return {c: rec[c].to_numpy() for c in rec.columns}, stats


def process_day(d: str, listing: dict, ledger_ids: dict[str, float]) -> dict:
    """ledger_ids: trdMatchID -> ledger price for this day's XBTUSD fills."""
    man_path = MARKET / "manifest" / f"{d}.json"
    if man_path.exists():
        return json.loads(man_path.read_text())
    rec = {"date": d}
    for kind in ("quote", "trade"):
        info = listing[kind].get(d)
        if info is None:
            rec[kind] = {"missing": True}
            continue
        raw = RAW_TMP / kind / f"{d}.csv.gz"
        t0 = time.time()
        sha = fetch(info["key"], raw, info["size"])
        tbl, total = _stream_filter(raw, QUOTE_TYPES if kind == "quote" else TRADE_TYPES)
        ts = _ts_ns(tbl.column("timestamp"))
        r = {"key": info["key"], "bytes": info["size"], "etag": info["etag"], "sha256": sha,
             "rows_all_symbols": int(total)}
        if kind == "quote":
            cols, st = clean_quotes(ts, tbl.column("bidPrice").to_numpy(), tbl.column("askPrice").to_numpy(),
                                    tbl.column("bidSize").to_numpy(), tbl.column("askSize").to_numpy())
            out = pa.table(cols)
        else:
            side = np.where(tbl.column("side").to_numpy(zero_copy_only=False) == "Buy", 1, -1).astype(np.int8)
            df = pd.DataFrame({"ts": ts, "price": tbl.column("price").to_numpy(), "size": tbl.column("size").to_numpy(),
                               "side": side, "id": tbl.column("trdMatchID").to_numpy(zero_copy_only=False)})
            st = {"rows": int(len(df)), "nonmonotonic_steps": int((np.diff(df.ts.to_numpy()) < 0).sum()) if len(df) > 1 else 0,
                  "duplicate_trdMatchID": int(df.id.duplicated().sum())}
            df = df.drop_duplicates("id").sort_values("ts", kind="stable")
            m = df[df.id.isin(ledger_ids.keys())]
            st["ledger_fills"] = len(ledger_ids)
            st["ledger_joined"] = int(len(m))
            st["ledger_price_equal"] = int((m.price.to_numpy() == np.array([ledger_ids[i] for i in m.id])).sum())
            st["ledger_missing_ids"] = sorted(set(ledger_ids) - set(m.id))[:5]
            out = pa.Table.from_pandas(df.drop(columns="id"), preserve_index=False)
        path = MARKET / kind / f"{d}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(out, path, compression="zstd")
        raw.unlink()
        r.update(st)
        r["parquet_bytes"] = path.stat().st_size
        r["seconds"] = round(time.time() - t0, 1)
        rec[kind] = r
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(rec))
    return rec


def ledger_ids_by_day() -> dict[str, dict[str, float]]:
    ex = pd.read_parquet("data/research/expert_execution/aoa_normalized/executions.parquet",
                         columns=["trdmatchid", "transact_ts", "symbol", "exectype", "lastpx"])
    ex = ex[(ex.symbol == SYMBOL) & (ex.exectype == "Trade")]
    out: dict[str, dict[str, float]] = {}
    for d, g in ex.groupby(ex.transact_ts.dt.strftime("%Y%m%d")):
        out[d] = dict(zip(g.trdmatchid, g.lastpx))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--first", default=FIRST_DAY.strftime("%Y%m%d"))
    ap.add_argument("--last", default=LAST_DAY.strftime("%Y%m%d"))
    a = ap.parse_args()
    listing = {k: list_archive(k) for k in ("quote", "trade")}
    (E1 / "archive_listing.json").write_text(json.dumps(listing))
    ids = ledger_ids_by_day()
    todo = [d for d in days() if a.first <= d <= a.last]
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(a.workers) as pool:
        futs = {pool.submit(process_day, d, listing, ids.get(d, {})): d for d in todo}
        for f in as_completed(futs):
            d = futs[f]
            try:
                f.result()
                done += 1
            except Exception as e:  # noqa: BLE001 (report and continue; rerun resumes)
                print(f"FAIL {d}: {e!r}", flush=True)
            if done % 25 == 0:
                print(f"{done}/{len(todo)} days, {time.time() - t0:.0f}s", flush=True)
    print(f"done {done}/{len(todo)} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()


def summarize_manifest() -> dict:
    """Aggregate per-day manifests into e1/market_manifest.json."""
    recs = [json.loads(p.read_text()) for p in sorted((MARKET / "manifest").glob("*.json"))]
    out = {"archive": ARCHIVE, "symbol": SYMBOL, "days": len(recs),
           "first": recs[0]["date"] if recs else None, "last": recs[-1]["date"] if recs else None}
    for kind in ("quote", "trade"):
        rs = [r[kind] for r in recs if not r[kind].get("missing")]
        out[kind] = {"files": len(rs), "missing_days": [r["date"] for r in recs if r[kind].get("missing")],
                     "raw_bytes": sum(r["bytes"] for r in rs), "parquet_bytes": sum(r["parquet_bytes"] for r in rs),
                     "rows_all_symbols": sum(r["rows_all_symbols"] for r in rs),
                     "rows_xbtusd": sum(r["rows"] for r in rs),
                     "nonmonotonic_steps": sum(r["nonmonotonic_steps"] for r in rs),
                     "download_parse_seconds": round(sum(r["seconds"] for r in rs), 1)}
        if kind == "quote":
            out[kind].update({k: sum(r[k] for r in rs) for k in
                              ("exact_duplicates", "same_ts_superseded", "kept", "crossed_or_locked", "nonpositive", "gaps_over_60s")})
            out[kind]["max_gap_s"] = max(r["max_gap_s"] or 0 for r in rs)
            out[kind]["days_with_gap_over_60s"] = [r_["date"] for r_ in recs if r_["quote"].get("gaps_over_60s")]
        else:
            out[kind].update({k: sum(r[k] for r in rs) for k in
                              ("duplicate_trdMatchID", "ledger_fills", "ledger_joined", "ledger_price_equal")})
            out[kind]["days_with_unjoined_ledger_fills"] = [r_["date"] for r_ in recs
                                                            if r_["trade"]["ledger_joined"] != r_["trade"]["ledger_fills"]]
    out["files"] = {r["date"]: {k: {"bytes": r[k]["bytes"], "etag": r[k]["etag"], "sha256": r[k]["sha256"]}
                                for k in ("quote", "trade") if not r[k].get("missing")} for r in recs}
    (E1 / "market_manifest.json").write_text(json.dumps(out, indent=1))
    return out
