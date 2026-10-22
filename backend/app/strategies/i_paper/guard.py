"""Pre-run overlap guard for the Strategy I paper oneshot (systemd ``ExecCondition``).

Exit 0 = run. Exit 1 = skip this firing (systemd treats 1..254 from ExecCondition as "condition failed",
not as a unit failure). A skipped day is safe: every run recomputes the book from the launch signal and
appends only the events not yet stored, so the next firing catches up.

Skip when any of these holds:

* a heavy unit is active: E RVOL (timer and once), grouped daily, splits refresh, H forward;
* E is in its active window: an E unit is active, or ET wall time is inside E_WINDOW on a weekday
  (the A/E common producer starts 03:45 ET Mon..Fri; the window is kept wide on purpose);
* host memory is already under pressure: PSI memory ``some avg10`` >= PSI_SKIP, or MemAvailable +
  SwapFree below HEADROOM_MIB (the run peaks near 240 MiB at launch, about 355 MiB a year later).

Every decision is printed as one JSON line (journal) and appended to ``<store>/guard.jsonl``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HEAVY_UNITS = ("usb-e-rvol.service", "usb-e-rvol-once.service", "usb-grouped-daily.service",
               "usb-splits-refresh.service", "usb-h-forward.service")
E_UNITS = ("usb-ae-producer.service", "usb-e-paper.service", "usb-e-rt2.service")
E_WINDOW = (time(3, 30), time(16, 30))       # ET, Mon..Fri
PSI_SKIP = 10.0
HEADROOM_MIB = 600


def _is_active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False)
    return r.returncode == 0


def _meminfo() -> dict[str, int]:
    out = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        out[k] = int(v.split()[0]) // 1024
    return out


def _psi_some_avg10() -> float:
    for line in Path("/proc/pressure/memory").read_text().splitlines():
        if line.startswith("some"):
            return float(line.split()[1].split("=")[1])
    return 0.0


def decide(now: datetime, is_active: Callable[[str], bool] = _is_active, meminfo: Callable[[], dict[str, int]] = _meminfo,
           psi: Callable[[], float] = _psi_some_avg10) -> dict:
    reasons = []
    busy = [u for u in HEAVY_UNITS if is_active(u)]
    if busy: reasons.append("HEAVY_UNIT_ACTIVE:" + ",".join(busy))
    e_busy = [u for u in E_UNITS if is_active(u)]
    if e_busy: reasons.append("E_UNIT_ACTIVE:" + ",".join(e_busy))
    et = now.astimezone(ET)
    if et.weekday() < 5 and E_WINDOW[0] <= et.time() < E_WINDOW[1]:
        reasons.append("E_ACTIVE_WINDOW:%s" % et.strftime("%a %H:%M ET"))
    p = psi()
    if p >= PSI_SKIP: reasons.append("MEMORY_PSI:%.2f" % p)
    m = meminfo()
    head = m.get("MemAvailable", 0) + m.get("SwapFree", 0)
    if head < HEADROOM_MIB: reasons.append("MEMORY_HEADROOM_MIB:%d" % head)
    return {"at": now.astimezone(timezone.utc).isoformat(), "et": et.isoformat(), "decision": "SKIP" if reasons else "RUN",
            "reasons": reasons, "psi_some_avg10": p, "mem_available_mib": m.get("MemAvailable"), "swap_free_mib": m.get("SwapFree")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    a = ap.parse_args(argv)
    rec = decide(datetime.now(timezone.utc))
    line = json.dumps(rec, sort_keys=True)
    print(line, flush=True)
    store = Path(a.store)
    if store.is_dir():
        with open(store / "guard.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0 if rec["decision"] == "RUN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
