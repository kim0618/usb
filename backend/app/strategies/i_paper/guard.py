"""RAM/PSI start-time guard V3 (pre-start decision only; no swap credit, no strategy imports).

Evidence base (controlled one-shot runs 2026-10-28 on the 1 GB host): RUN#1 and RUN#2 both completed with
MemAvailable min 260-263 MiB, so the old 450 MiB fixed gate is not supported; the clean pre-start
baseline was MemAvailable 353-389 MiB, memory PSI full 0, IO PSI full avg10 up to 14.3.

Start conditions (all must hold):
  * MemAvailable >= 350 MiB (RAM only, free swap is never added);
  * memory PSI some >= 2 / full >= 1 (avg10 and avg60) blocks; IO PSI full >= 15 (avg10 and avg60) blocks;
    IO PSI some is validated and logged only (it sat at 10+ on a clean host while I was not running);
  * no heavy US-B unit and no E unit is active, and not inside the E window (03:30-16:30 ET weekdays);
  * SwapFree >= 512 MiB as a separate floor (idle daemon pages need somewhere to go).

Run-time protection is NOT part of this module: the abort rules (MemAvailable < 220 now or < 240 for 3 s,
memory PSI full >= 80 now or >= 50 for 5 s, IO PSI full >= 70 for 10 s, backend/crypto health failure,
critical D-state >= 10 s, OOM) belong to a separate supervisor, never to a background thread in here.

Exit contract. Native: 0 RUN, 10 normal resource skip, 20 internal error, 21 invalid environment,
22 source/log read/write failure. With --systemd-condition (ExecCondition semantics: 0 start, 1..254 skip
silently, >=255 unit failure): RUN 0, normal skip 75 (never Python's default 1/2, so a crashed interpreter or
import error that exits 1 cannot be mistaken for a verdict), every error 255. Every verdict prints one JSON
line first (decision RUN/SKIP/ERROR, native exit_code), so a journal reader can tell skip from error.
CLI requires an explicit MemAvailable threshold; no runtime/timer is installed here.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
# Server unit names (systemctl list-unit-files "usb-*", 2026-10-28). usb-crypto-feedlog is a sub-second
# per-minute oneshot (MemoryMax 80M) and is deliberately not heavy: listing it would skip at random.
HEAVY_UNITS = ("usb-e-rvol.service", "usb-e-rvol-once.service", "usb-grouped-daily.service",
               "usb-splits-refresh.service", "usb-h-forward.service", "usb-e-universe.service",
               "usb-a-snapshot-consumer.service", "usb-h-evaluation.service", "usb-h-evaluation-guard.service",
               "usb-morning-scan.service", "usb-crypto-context-retention.service")
E_UNITS = ("usb-ae-producer.service", "usb-e-paper.service", "usb-e-rt2.service")
E_WINDOW = (time(3, 30), time(16, 30))
RUN, NORMAL_RESOURCE_SKIP, GUARD_INTERNAL_ERROR, INVALID_ENVIRONMENT, SOURCE_READ_ERROR = 0, 10, 20, 21, 22
SYSTEMD_NORMAL_SKIP, SYSTEMD_GUARD_ERROR = 75, 255
PROC = Path("/proc")   # tests point this at a fake /proc


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Policy:
    min_available_mib: float = 350
    min_swap_free_mib: float = 512
    memory_some: float = 2
    memory_full: float = 1
    io_full: float = 15

    def validate(self) -> None:
        values = asdict(self)
        if any(not math.isfinite(v) or v <= 0 for v in values.values()):
            raise ValueError("thresholds must be finite and positive")
        if any(v > 100 for k, v in values.items() if not k.endswith("_mib")):
            raise ValueError("PSI thresholds must be <= 100 percent")


def _is_active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "show", unit, "--property=ActiveState", "--value"],
                       capture_output=True, text=True, check=False, timeout=5)
    state = r.stdout.strip()
    if r.returncode or state not in {"active", "activating", "deactivating", "reloading", "refreshing", "inactive", "failed"}:
        raise SourceError("cannot read unit state: " + unit)
    return state not in {"inactive", "failed"}


def _meminfo() -> dict[str, float]:
    out = {}
    for line in (PROC / "meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        if k in ("MemAvailable", "SwapFree", "SwapTotal"):
            parts = v.split()
            if len(parts) != 2 or parts[1] != "kB":
                raise SourceError("invalid meminfo units")
            out[k] = int(parts[0]) / 1024
    return out


def _pressure() -> dict[str, dict[str, dict[str, float]]]:
    out = {}
    for resource in ("memory", "io"):
        out[resource] = {}
        for line in (PROC / "pressure" / resource).read_text().splitlines():
            fields = line.split()
            out[resource][fields[0]] = {k: float(v) for k, v in (x.split("=") for x in fields[1:])}
    return out


def decide(now: datetime, is_active: Callable[[str], bool] = _is_active,
           meminfo: Callable[[], dict] = _meminfo, pressure: Callable[[], dict] = _pressure,
           policy: Policy = Policy()) -> dict:
    policy.validate()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware now required")
    limits = {("memory", "some"): policy.memory_some, ("memory", "full"): policy.memory_full,
              ("io", "full"): policy.io_full}      # io some is validated and logged, never a limit
    reasons = []
    busy = [u for u in HEAVY_UNITS if is_active(u)]
    if busy:
        reasons.append("HEAVY_UNIT_ACTIVE:" + ",".join(busy))
    e_busy = [u for u in E_UNITS if is_active(u)]
    if e_busy:
        reasons.append("E_UNIT_ACTIVE:" + ",".join(e_busy))
    et = now.astimezone(ET)
    if et.weekday() < 5 and E_WINDOW[0] <= et.time() < E_WINDOW[1]:
        reasons.append("E_ACTIVE_WINDOW:%s" % et.strftime("%a %H:%M ET"))
    try:
        m, p = meminfo(), pressure()
        available, swap_total, swap_free = m["MemAvailable"], m["SwapTotal"], m["SwapFree"]
        if any(not math.isfinite(v) or v < 0 for v in (available, swap_total, swap_free)):
            raise ValueError()
        for resource in ("memory", "io"):
            for kind in ("some", "full"):
                limit = limits.get((resource, kind))
                for avg in ("avg10", "avg60", "avg300"):
                    value = p[resource][kind][avg]
                    if not math.isfinite(value) or not 0 <= value <= 100:
                        raise ValueError()
                    # avg300 is logged; avg10 catches bursts, avg60 blocks early recovery.
                    if limit is not None and avg != "avg300" and value >= limit:
                        reasons.append("%s_PSI_%s_%s:%.2f" % (resource.upper(), kind, avg, value))
    except (KeyError, TypeError, ValueError) as ex:
        raise SourceError("missing or invalid MemAvailable/swap/PSI data") from ex
    if available < policy.min_available_mib:
        reasons.append("MEM_AVAILABLE_MIB:%.2f" % available)
    if swap_total > 0 and swap_free < policy.min_swap_free_mib:
        reasons.append("SWAP_FREE_MIB:%.2f" % swap_free)
    code = NORMAL_RESOURCE_SKIP if reasons else RUN
    return {"at": now.astimezone(timezone.utc).isoformat(), "et": et.isoformat(),
            "decision": "SKIP" if reasons else "RUN", "exit_code": code, "reasons": reasons,
            "pressure": p, "policy": asdict(policy), "mem_available_mib": available,
            "swap_free_mib": swap_free, "swap_used_mib": swap_total - swap_free, "swap_credit_mib": 0, "guard": "V3"}


def condition_exit(code: int) -> int:
    return RUN if code == RUN else (SYSTEMD_NORMAL_SKIP if code == NORMAL_RESOURCE_SKIP else SYSTEMD_GUARD_ERROR)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--min-available-mib", type=float, required=True)
    ap.add_argument("--min-swap-free-mib", type=float, default=Policy.min_swap_free_mib)
    ap.add_argument("--systemd-condition", action="store_true")
    args = None
    try:
        args = ap.parse_args(argv)
        policy = Policy(min_available_mib=args.min_available_mib, min_swap_free_mib=args.min_swap_free_mib)
        policy.validate()
        rec = decide(datetime.now(timezone.utc), policy=policy)
        # A missing store or unwritable journal is an error, never a normal skip.
        with (Path(args.store) / "guard.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        code = rec["exit_code"]
    except SystemExit as ex:
        if ex.code == 0:
            return 0
        code, rec = INVALID_ENVIRONMENT, {"decision": "ERROR", "error": "INVALID_ARGUMENTS"}
    except (SourceError, OSError, subprocess.SubprocessError):
        code, rec = SOURCE_READ_ERROR, {"decision": "ERROR", "error": "SOURCE_OR_LOG_UNAVAILABLE"}
    except ValueError:
        code, rec = INVALID_ENVIRONMENT, {"decision": "ERROR", "error": "INVALID_ENVIRONMENT"}
    except Exception as ex:
        code, rec = GUARD_INTERNAL_ERROR, {"decision": "ERROR", "error": type(ex).__name__}
    rec["exit_code"] = code
    print(json.dumps(rec, sort_keys=True), flush=True)
    # Detect the adapter flag even if argparse failed on a missing required argument.
    import sys
    adapter = args.systemd_condition if args is not None else "--systemd-condition" in (argv if argv is not None else sys.argv[1:])
    return condition_exit(code) if adapter else code


def _entry() -> int:
    import sys
    try:
        return main()
    except BaseException as ex:      # main() handles Exception itself; this is KeyboardInterrupt and friends
        print(json.dumps({"decision": "ERROR", "error": "UNHANDLED_" + type(ex).__name__,
                          "exit_code": GUARD_INTERNAL_ERROR}, sort_keys=True), flush=True)
        return SYSTEMD_GUARD_ERROR if "--systemd-condition" in sys.argv[1:] else GUARD_INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(_entry())
