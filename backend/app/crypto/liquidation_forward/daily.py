"""Daily finalize (contract section 6-7) and a rolling quality report.

    LIQFWD_ROOT=... python -m app.crypto.liquidation_forward.daily finalize [--date YYYY-MM-DD]   (default: yesterday UTC)
    LIQFWD_ROOT=... python -m app.crypto.liquidation_forward.daily report --hours 1

Runs as a separate short-lived process (systemd timer) so the collector never loads pyarrow.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import COLLECTOR_VERSION, EXCHANGES, SCHEMA_VERSION

HB_GAP_MS = 150_000
LATE_MS = 300_000
OKX_REST_WINDOW_MS = 20 * 3_600_000
SOURCES = {"bybit": ("bybit_ws",), "binance": ("binance_ws",), "okx": ("okx_rest", "okx_ws")}


def day_bounds(date: str) -> tuple[int, int]:
    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000), int((d + timedelta(days=1)).timestamp() * 1000)


def _read_jsonl(p: Path) -> list[dict]:
    op = gzip.open if p.suffix == ".gz" else open
    out = []
    with op(p, "rt") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def norm_rows(root: Path, exchange: str, t0: int, t1: int, include_partial: bool = False) -> list[dict]:
    rows = []
    for date in {datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for t in (t0 - 3_600_000, t0, t1 - 1, t1 + 3_600_000)}:
        d = root / "norm" / f"exchange={exchange}" / f"date={date}"
        if not d.exists():
            continue
        pats = ["*.jsonl.gz"] + (["*.jsonl.partial"] if include_partial else [])
        for pat in pats:
            for p in sorted(d.glob(pat)):
                rows += [r for r in _read_jsonl(p) if t0 <= r["event_ts_ms"] < t1]
    return rows


def heartbeats(root: Path, t0: int, t1: int) -> list[dict]:
    out = []
    for p in sorted(list((root / "runtime").glob("heartbeat-*.jsonl")) + list((root / "runtime").glob("heartbeat-*.jsonl.gz"))):
        out += [h for h in _read_jsonl(p) if t0 - HB_GAP_MS <= h["ms"] < t1 + HB_GAP_MS]
    return sorted(out, key=lambda h: h["ms"])


def up_intervals(hbs: list[dict], key: str, t0: int, t1: int) -> list[list[int]]:
    """An interval between consecutive heartbeats (<= 150 s apart, same collector session) counts as UP if
    both ends report UP. A restart therefore always shows as DOWN from the last heartbeat of the old
    session to the first heartbeat of the new one (conservative: the real outage may be shorter)."""
    iv = []
    for a, b in zip(hbs, hbs[1:]):
        same_session = a.get("session_start_ms") == b.get("session_start_ms")
        if same_session and b["ms"] - a["ms"] <= HB_GAP_MS and a["up"].get(key) and b["up"].get(key):
            s, e = max(a["ms"], t0), min(b["ms"], t1)
            if s < e:
                if iv and iv[-1][1] == s:
                    iv[-1][1] = e
                else:
                    iv.append([s, e])
    return iv


def complement(iv: list[list[int]], t0: int, t1: int) -> list[list[int]]:
    out, cur = [], t0
    for s, e in iv:
        if s > cur:
            out.append([cur, s])
        cur = max(cur, e)
    if cur < t1:
        out.append([cur, t1])
    return out


def counter_deltas(hbs: list[dict], t0: int) -> dict:
    """Per-stream counters are cumulative per session; sum the in-window increase of every session."""
    by_sess: dict[int, list[dict]] = {}
    for h in hbs:
        by_sess.setdefault(h.get("session_start_ms", 0), []).append(h)
    tot: dict[str, dict[str, int]] = {}
    for sess, hs in by_sess.items():
        before = [h for h in hs if h["ms"] < t0]
        inside = [h for h in hs if h["ms"] >= t0]
        if not inside:
            continue
        base = before[-1]["stats"] if before else None
        last = inside[-1]["stats"]
        for stream, c in last.items():
            t = tot.setdefault(stream, {})
            for k, v in c.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)) or k in ("health_ms", "last_msg_ms", "last_pages"):
                    continue
                t[k] = t.get(k, 0) + v - ((base or {}).get(stream, {}).get(k, 0))
    return tot


def _stats(rows: list[dict], session_starts: list[int] | None = None) -> dict:
    if not rows:
        return {"rows": 0}
    st = sorted(session_starts or [])
    ev = np.array([r["event_ts_ms"] for r in rows], dtype=np.int64)
    rc = np.array([r["receive_ts_ms"] for r in rows], dtype=np.int64)
    delay = rc - ev
    side = [r["liquidated_side"] for r in rows]
    notional = np.array([r["notional_usd"] if r["notional_usd"] is not None else np.nan for r in rows])
    late = int(((delay > LATE_MS) | ((ev // 86_400_000) != (rc // 86_400_000))).sum())
    keys = [r["dedupe_key"] for r in rows]
    return {"rows": len(rows), "first_event_ms": int(ev.min()), "last_event_ms": int(ev.max()),
            "long_count": side.count("LONG"), "short_count": side.count("SHORT"), "null_side": side.count(None),
            "notional_usd": float(np.nansum(notional)),
            "long_notional_usd": float(np.nansum([n for n, s in zip(notional, side) if s == "LONG"])),
            "short_notional_usd": float(np.nansum([n for n, s in zip(notional, side) if s == "SHORT"])),
            "null_notional": int(np.isnan(notional).sum()),
            "delay_ms": {"p50": float(np.percentile(delay, 50)), "p95": float(np.percentile(delay, 95)),
                         "max": int(delay.max()), "min": int(delay.min()), "negative": int((delay < 0).sum())},
            "late_events": late, "duplicate_keys_in_window": len(keys) - len(set(keys)),
            **_live_split(ev, rc, st)}


def _live_split(ev: np.ndarray, rc: np.ndarray, starts: list[int]) -> dict:
    """Rows whose event predates the session that received them are backfill (OKX REST rolling window),
    not latency. Report them apart so delay percentiles describe live collection only."""
    if not starts:
        return {"pre_session_rows": None}
    s = np.array(starts, dtype=np.int64)
    k = np.searchsorted(s, rc, side="right") - 1
    sess = np.where(k >= 0, s[np.clip(k, 0, None)], np.iinfo(np.int64).min)
    pre = ev < sess
    live = rc[~pre] - ev[~pre]
    out = {"pre_session_rows": int(pre.sum())}
    if len(live):
        out["delay_ms_live"] = {"p50": float(np.percentile(live, 50)), "p95": float(np.percentile(live, 95)), "max": int(live.max())}
    return out


def session_starts(root: Path) -> list[int]:
    p = root / "runtime" / "sessions.jsonl"
    return [r["ms"] for r in _read_jsonl(p) if r.get("event") == "start"] if p.exists() else []


def summarize(root: Path, t0: int, t1: int, include_partial: bool) -> dict:
    hbs = heartbeats(root, t0, t1)
    starts = session_starts(root)
    out = {"window_ms": [t0, t1], "collector_version": COLLECTOR_VERSION, "schema_version": SCHEMA_VERSION,
           "heartbeats": len(hbs), "exchanges": {}, "counters": counter_deltas(hbs, t0)}
    collector_up = up_intervals([dict(h, up=dict(h["up"], _collector=True)) for h in hbs], "_collector", t0, t1)
    out["collector_up"] = collector_up
    out["collector_down"] = complement(collector_up, t0, t1)
    for ex in EXCHANGES:
        e = {}
        for src in SOURCES[ex]:
            rows = [r for r in norm_rows(root, ex, t0, t1, include_partial) if r["source"] == src]
            e[src] = _stats(rows, starts)
            up_key = {"okx_rest": "okx", "okx_ws": "okx_ws"}.get(src, ex)
            iv = up_intervals(hbs, up_key, t0, t1)
            e[src]["up_intervals"] = iv
            e[src]["down_intervals"] = complement(iv, t0, t1)
            e[src]["up_share"] = sum(b - a for a, b in iv) / (t1 - t0)
        if ex == "okx":
            rest = {r["dedupe_key"] for r in norm_rows(root, ex, t0, t1, include_partial) if r["source"] == "okx_rest"}
            ws = {r["dedupe_key"] for r in norm_rows(root, ex, t0, t1, include_partial) if r["source"] == "okx_ws"}
            e["ws_audit"] = {"ws_rows": len(ws), "rest_rows": len(rest), "ws_in_rest": len(ws & rest), "ws_only": len(ws - rest),
                             "ws_share_of_rest": len(ws & rest) / len(rest) if rest else None}
            # REST windows roll ~21 h: a DOWN stretch shorter than 20 h followed by a successful poll is recoverable
            e["okx_rest"]["down_recoverable_by_rest_window"] = [iv for iv in e["okx_rest"]["down_intervals"]
                                                                if iv[1] - iv[0] <= OKX_REST_WINDOW_MS and iv[1] < t1]
        out["exchanges"][ex] = e
    last = hbs[-1] if hbs else {}
    out["last_heartbeat"] = {k: last.get(k) for k in ("ms", "disk_free", "disk_guard", "rss_kb", "ct_val", "up")}
    return out


def compress_heartbeat(root: Path, date: str) -> dict | None:
    """A finished day's heartbeat file (~2 MB/day plain) is gzipped once the day is finalized."""
    src = root / "runtime" / f"heartbeat-{date}.jsonl"
    if not src.exists() or date >= datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        return None
    dst = src.with_name(src.name + ".gz")
    tmp = dst.with_name(dst.name + ".partial")
    with open(src, "rb") as f, gzip.open(tmp, "wb") as g:
        g.write(f.read())
    os.replace(tmp, dst)
    from .store import sha256_file
    info = {"file": str(dst), "plain_bytes": src.stat().st_size, "bytes": dst.stat().st_size, "sha256": sha256_file(dst)}
    src.unlink()
    return info


def finalize(root: Path, date: str) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    t0, t1 = day_bounds(date)
    summ = summarize(root, t0, t1, include_partial=False)
    files = []
    for ex in EXCHANGES:
        rows = sorted(norm_rows(root, ex, t0, t1), key=lambda r: (r["event_ts_ms"], r["receive_ts_ms"]))
        if not rows:
            continue
        out = root / "normalized" / f"exchange={ex}" / f"date={date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name("part.parquet.partial")
        pq.write_table(pa.Table.from_pylist(rows), tmp, compression="zstd")
        os.replace(tmp, out)
        from .store import sha256_file
        files.append({"exchange": ex, "file": str(out), "rows": len(rows), "bytes": out.stat().st_size, "sha256": sha256_file(out)})
    seg = [json.loads(l) for l in open(root / "manifest" / "segments.jsonl")] if (root / "manifest" / "segments.jsonl").exists() else []
    seg = [s for s in seg if f"date={date}" in s["file"]]
    sessions = [json.loads(l) for l in open(root / "runtime" / "sessions.jsonl")] if (root / "runtime" / "sessions.jsonl").exists() else []
    summ.update({"date": date, "parquet": files, "segments": seg,
                 "sessions_in_day": [s for s in sessions if t0 <= s["ms"] < t1 and s["event"] in ("start", "stop", "disk_guard", "start_marker")]})
    summ["heartbeat_archive"] = compress_heartbeat(root, date)
    p = root / "manifest" / "daily" / f"date={date}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".partial")
    tmp.write_text(json.dumps(summ, indent=1))
    os.replace(tmp, p)
    return summ


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["finalize", "report"])
    ap.add_argument("--date")
    ap.add_argument("--hours", type=float, default=1.0)
    a = ap.parse_args()
    root = Path(os.environ.get("LIQFWD_ROOT", "data/research/crypto/liquidation_forward"))
    if a.cmd == "finalize":
        date = a.date or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        r = finalize(root, date)
        print(json.dumps({"date": date, "parquet": r["parquet"]}, indent=1))
    else:
        t1 = int(datetime.now(timezone.utc).timestamp() * 1000)
        print(json.dumps(summarize(root, t1 - int(a.hours * 3_600_000), t1, include_partial=True), indent=1))


if __name__ == "__main__":
    main()
