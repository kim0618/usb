#!/usr/bin/env python3
"""US-B server maintenance preflight: disk free space and crypto context archive freshness.

Read only. One ssh call reads `df`, the archive manifest/receipt listings, the last line of the
context retention log and the IO PSI; the local COLD receipts give the last successful pull. It
never pulls, writes a receipt, runs retention, deletes, truncates or restarts anything.

    python3 deploy/crypto/server_preflight.py            # check the server, print the report
    python3 deploy/crypto/server_preflight.py --json     # same, as JSON
    python3 deploy/crypto/server_preflight.py --selftest # verdict rules on fixed scenarios

Policy (skill `usb-server-preflight`): archive pull is MANUAL; this only reminds.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HOST = os.environ.get("CTX_ARCHIVE_HOST", "traderj")
LOCAL_COLD = Path(os.environ.get("CTX_ARCHIVE_DEST", "~/usb_archive/crypto_context")).expanduser()

MB = 1_000_000
PULL_REMIND_H = 48
PULL_STRONG_H = 72
NO_RECEIPT_WARN_BYTES = 150 * MB
DISK_WARN_BYTES = 800 * MB
DISK_CRITICAL_BYTES = 500 * MB
IO_PSI_HIGH_AVG10 = 60.0

KST = timezone(timedelta(hours=9))

# Runs on the server. Prints one JSON object. Reads small files only (manifests are ~17KB each).
REMOTE = r'''
[ "$(hostname)" = "trader-j" ] || { echo '{"error":"WRONG_HOST"}'; exit 0; }
python3 - <<'PY'
import json, os, shutil, glob
A = "/root/usb_runtime/crypto_context/archive"
L = "/root/usb_runtime/crypto_context/logs/retention.log"
u = shutil.disk_usage("/")
out = {"total": u.total, "used": u.used, "free": u.free, "manifests": {}, "receipts": {}}
for p in glob.glob(A + "/manifests/*.json"):
    try:
        m = json.load(open(p))
        out["manifests"][m["manifest_id"]] = {"files": m.get("file_count"), "bytes": m.get("total_bytes")}
    except Exception as e:
        out["manifests"][os.path.basename(p)[:-5]] = {"error": type(e).__name__}
for p in glob.glob(A + "/receipts/*.json"):
    try:
        r = json.load(open(p))
        out["receipts"][r["manifest_id"]] = {"result": r.get("result"), "verified_at": r.get("verified_at")}
    except Exception as e:
        out["receipts"][os.path.basename(p)[:-5]] = {"error": type(e).__name__}
try:
    with open(L, "rb") as f:
        f.seek(0, 2); f.seek(max(0, f.tell() - 65536))
        last = [x for x in f.read().decode(errors="replace").splitlines() if x.startswith("{")][-1]
    d = json.loads(last)
    out["retention"] = {k: d.get(k) for k in ("event", "now_utc", "applied", "health", "deleted",
                        "deleted_expired", "failed", "keep_reasons", "prune_eligible")}
    out["retention"]["q123_state"] = (d.get("q123") or {}).get("state")
except Exception as e:
    out["retention"] = {"error": type(e).__name__}
try:
    out["io_psi"] = open("/proc/pressure/io").readline().strip()
except Exception:
    out["io_psi"] = None
print(json.dumps(out))
PY
'''


def _parse_ts(value) -> float | None:
    """Receipt timestamps are ISO strings (archive.utc_iso) or epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify(*, free_bytes: int | None, hours_since_pull: float | None,
             no_receipt_bytes: int | None) -> dict:
    """The verdict. Unknown inputs never pass silently: they count as RECOMMENDED."""
    reasons = []
    strong = False
    remind = False
    if free_bytes is None:
        remind = True
        reasons.append("disk free unknown")
    elif free_bytes < DISK_CRITICAL_BYTES:
        strong = True
        reasons.append(f"free {free_bytes / MB:.1f}MB < {DISK_CRITICAL_BYTES // MB}MB")
    elif free_bytes < DISK_WARN_BYTES:
        remind = True
        reasons.append(f"free {free_bytes / MB:.1f}MB < {DISK_WARN_BYTES // MB}MB")
    if hours_since_pull is None:
        remind = True
        reasons.append("last pull unknown")
    elif hours_since_pull >= PULL_STRONG_H:
        strong = True
        reasons.append(f"last pull {hours_since_pull:.0f}h >= {PULL_STRONG_H}h")
    elif hours_since_pull >= PULL_REMIND_H:
        remind = True
        reasons.append(f"last pull {hours_since_pull:.0f}h >= {PULL_REMIND_H}h")
    if no_receipt_bytes is None:
        remind = True
        reasons.append("NO_RECEIPT unknown")
    elif no_receipt_bytes >= NO_RECEIPT_WARN_BYTES:
        remind = True
        reasons.append(f"NO_RECEIPT {no_receipt_bytes / MB:.0f}MB >= {NO_RECEIPT_WARN_BYTES // MB}MB")
    if strong:
        return {"verdict": "CRITICAL", "reminder": "STRONGLY_RECOMMENDED", "reasons": reasons}
    if remind:
        return {"verdict": "WARNING", "reminder": "RECOMMENDED", "reasons": reasons}
    return {"verdict": "PASS", "reminder": "NONE", "reasons": reasons}


def last_local_pull(cold: Path) -> float | None:
    """Newest `verified_at` of an OK receipt in the local COLD copy (the pull writes it)."""
    best = None
    for path in (cold / "receipts").glob("*.json"):
        try:
            r = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if r.get("result") != "OK":
            continue
        t = _parse_ts(r.get("verified_at")) or path.stat().st_mtime
        best = t if best is None or t > best else best
    return best


def gather(host: str = HOST, cold: Path = LOCAL_COLD, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rep: dict = {"host": host, "checked_at": datetime.fromtimestamp(now, KST).isoformat(timespec="seconds")}
    try:
        got = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, "bash -s"],
                             input=REMOTE, capture_output=True, text=True, timeout=60)
        srv = json.loads(got.stdout.strip().splitlines()[-1]) if got.stdout.strip() else {"error": "NO_OUTPUT"}
    except (subprocess.SubprocessError, ValueError, IndexError, OSError) as e:
        srv = {"error": type(e).__name__}
    rep["server_error"] = srv.get("error")
    free = srv.get("free")
    rep["disk_free_bytes"] = free
    # df Use%: used / (used + available); reserved blocks are in neither.
    rep["disk_use_pct"] = (round(100 * srv["used"] / (srv["used"] + srv["free"]), 1)
                           if srv.get("used") is not None and free is not None else None)

    manifests = srv.get("manifests", {})
    receipts = srv.get("receipts", {})
    pending = {mid: m for mid, m in manifests.items()
               if (receipts.get(mid) or {}).get("result") != "OK" and "error" not in m}
    rep["pending_manifests"] = sorted(pending)
    pend_bytes = sum(m.get("bytes") or 0 for m in pending.values()) if not srv.get("error") else None
    pend_files = sum(m.get("files") or 0 for m in pending.values()) if not srv.get("error") else None

    ret = srv.get("retention") or {}
    nr = ((ret.get("keep_reasons") or {}).get("NO_RECEIPT") or {})
    rep["retention"] = {
        "last_run_utc": ret.get("now_utc"), "applied": ret.get("applied"), "health": ret.get("health"),
        "q123_state": ret.get("q123_state"), "failed": len(ret.get("failed") or []),
        "deleted": ret.get("deleted"), "keep_reasons": ret.get("keep_reasons"), "error": ret.get("error")}
    # The log is as of the last 04:40 run; the manifest listing is current. Use the larger.
    candidates = [x for x in (pend_bytes, nr.get("bytes")) if x is not None]
    rep["no_receipt_bytes"] = max(candidates) if candidates else None
    rep["no_receipt_files"] = max([x for x in (pend_files, nr.get("files")) if x is not None], default=None)

    srv_pull = max((_parse_ts(r.get("verified_at")) or 0 for r in receipts.values()
                    if r.get("result") == "OK"), default=None) or None
    loc_pull = last_local_pull(cold)
    pulls = [t for t in (srv_pull, loc_pull) if t]
    last = max(pulls) if pulls else None
    rep["last_pull"] = datetime.fromtimestamp(last, KST).isoformat(timespec="minutes") if last else None
    rep["hours_since_pull"] = round((now - last) / 3600, 1) if last else None

    psi = srv.get("io_psi") or ""
    try:
        avg10 = float(psi.split("avg10=")[1].split()[0])
    except (IndexError, ValueError):
        avg10 = None
    rep["io_psi_avg10"] = avg10
    rep["io_pressure_high"] = avg10 is not None and avg10 >= IO_PSI_HIGH_AVG10

    rep.update(classify(free_bytes=free, hours_since_pull=rep["hours_since_pull"],
                        no_receipt_bytes=rep["no_receipt_bytes"]))
    rep["operational_action_taken"] = 0
    return rep


def render(r: dict) -> str:
    mb = lambda b: "?" if b is None else f"{b / MB:,.0f}MB"
    ret = r["retention"]
    kr = ret.get("keep_reasons") or {}
    kr_s = ", ".join(f"{k} {v.get('files')}/{mb(v.get('bytes'))}" for k, v in sorted(kr.items())) or "?"
    lines = [
        "US-B SERVER MAINTENANCE PREFLIGHT",
        f"disk free = {mb(r['disk_free_bytes'])}",
        f"disk use = {r['disk_use_pct']}%",
        f"last ctx archive pull = {r['last_pull']}",
        f"hours since pull = {r['hours_since_pull']}",
        f"NO_RECEIPT files = {r['no_receipt_files']}",
        f"NO_RECEIPT bytes = {mb(r['no_receipt_bytes'])}",
        f"pending manifests = {', '.join(r['pending_manifests']) or 'none'}",
        f"retention status = last {ret.get('last_run_utc')} health {ret.get('health')} "
        f"q123 {ret.get('q123_state')} failed {ret.get('failed')} | {kr_s}",
        f"io psi avg10 = {r['io_psi_avg10']}" + (" (HIGH: defer any pull)" if r["io_pressure_high"] else ""),
        f"archive reminder = {r['reminder']}",
        f"verdict = {r['verdict']}" + (f" ({'; '.join(r['reasons'])})" if r["reasons"] else ""),
        "operational action automatically taken = 0",
    ]
    if r.get("server_error"):
        lines.insert(1, f"server read error = {r['server_error']}")
    return "\n".join(lines)


SCENARIOS = [
    # (name, free MB, hours since pull, NO_RECEIPT MB, expected verdict, expected reminder)
    ("T1", 1200, 12, 30, "PASS", "NONE"),
    ("T2", 900, 55, 120, "WARNING", "RECOMMENDED"),
    ("T3", 700, 24, 170, "WARNING", "RECOMMENDED"),
    ("T4", 480, 30, 90, "CRITICAL", "STRONGLY_RECOMMENDED"),
    ("T5", 900, 80, 200, "CRITICAL", "STRONGLY_RECOMMENDED"),
    ("B48", 1200, 48, 0, "WARNING", "RECOMMENDED"),        # boundary: >= 48h
    ("B72", 1200, 72, 0, "CRITICAL", "STRONGLY_RECOMMENDED"),
    ("B150", 1200, 1, 150, "WARNING", "RECOMMENDED"),
    ("B800", 799.999999, 1, 0, "WARNING", "RECOMMENDED"),
    ("B800ok", 800, 1, 0, "PASS", "NONE"),
    ("B500", 499.999999, 1, 0, "CRITICAL", "STRONGLY_RECOMMENDED"),
    ("UNKNOWN", None, None, None, "WARNING", "RECOMMENDED"),
]


def selftest() -> int:
    bad = 0
    for name, free, h, nr, verdict, reminder in SCENARIOS:
        got = classify(free_bytes=None if free is None else int(free * MB), hours_since_pull=h,
                       no_receipt_bytes=None if nr is None else int(nr * MB))
        ok = got["verdict"] == verdict and got["reminder"] == reminder
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: {got['verdict']}/{got['reminder']} {got['reasons']}")
    print(f"{len(SCENARIOS) - bad}/{len(SCENARIOS)} passed")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    r = gather()
    print(json.dumps(r, indent=1, ensure_ascii=False) if a.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
