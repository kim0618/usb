"""Host and process resource samples for the shadow gate (RSS, swap, iowait, PSI), from /proc.

A daemon thread samples every ``interval`` seconds; :meth:`Sampler.dense` shortens the interval
around the 09:15 - 09:31 window, where A's sweep, both seals and E's finalization meet. Missing
/proc files (a test host) leave the field ``None``; sampling never raises into the producer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import resource
import threading
from typing import Any


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[name] = int(parts[0])                       # kB
    return out


def process_rss_kb() -> int | None:
    for line in (_read("/proc/self/status") or "").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return None


def cpu_times() -> tuple[int, int] | None:
    """(iowait, total) jiffies from the aggregate cpu line."""
    first = (_read("/proc/stat") or "").splitlines()[:1]
    if not first or not first[0].startswith("cpu "):
        return None
    values = [int(v) for v in first[0].split()[1:]]
    return (values[4] if len(values) > 4 else 0), sum(values)


def psi(kind: str) -> dict[str, float] | None:
    text = _read(f"/proc/pressure/{kind}")
    if text is None:
        return None
    out: dict[str, float] = {}
    for line in text.splitlines():
        head, *pairs = line.split()
        for pair in pairs:
            key, _, value = pair.partition("=")
            if key == "avg10":
                out[head] = float(value)
    return out


@dataclass
class Sampler:
    clock: Callable[[], datetime]
    interval: float = 30.0
    samples: list[dict[str, Any]] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _last_cpu: tuple[int, int] | None = None
    _thread: threading.Thread | None = None

    def sample(self) -> dict[str, Any]:
        mem = meminfo()
        cpu = cpu_times()
        iowait = None
        if cpu and self._last_cpu and cpu[1] > self._last_cpu[1]:
            iowait = round(100.0 * (cpu[0] - self._last_cpu[0]) / (cpu[1] - self._last_cpu[1]), 2)
        self._last_cpu = cpu
        swap = (mem["SwapTotal"] - mem["SwapFree"]) // 1024 if "SwapTotal" in mem and "SwapFree" in mem else None
        row = {"at": self.clock().isoformat(), "rss_mb": (process_rss_kb() or 0) // 1024 or None,
               "mem_available_mb": mem["MemAvailable"] // 1024 if "MemAvailable" in mem else None,
               "swap_used_mb": swap, "iowait_pct": iowait, "psi_memory": psi("memory"), "psi_io": psi("io")}
        self.samples.append(row)
        return row

    def start(self) -> None:
        self.sample()

        def loop() -> None:
            while not self._stop.wait(self.interval):
                try:
                    self.sample()
                except Exception:                            # pragma: no cover - evidence only
                    pass
        self._thread = threading.Thread(target=loop, daemon=True, name="resource-sampler")
        self._thread.start()

    def dense(self, interval: float) -> None:
        self.interval = interval

    def stop(self) -> None:
        self._stop.set()
        self.sample()

    def summary(self) -> dict[str, Any]:
        def values(key: str) -> list[float]:
            return [s[key] for s in self.samples if s.get(key) is not None]
        rss, avail, swap, iowait = values("rss_mb"), values("mem_available_mb"), values("swap_used_mb"), values("iowait_pct")
        psi_io = [s["psi_io"].get("full", 0.0) for s in self.samples if s.get("psi_io")]
        psi_mem = [s["psi_memory"].get("some", 0.0) for s in self.samples if s.get("psi_memory")]
        return {"samples": len(self.samples),
                "process_rss_peak_mb": max(rss, default=None),
                "process_maxrss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                "host_mem_available_min_mb": min(avail, default=None),
                "swap_used_start_mb": swap[0] if swap else None, "swap_used_peak_mb": max(swap, default=None),
                "swap_delta_mb": (max(swap) - swap[0]) if swap else None,
                "iowait_peak_pct": max(iowait, default=None),
                "psi_io_full_avg10_peak": max(psi_io, default=None),
                "psi_memory_some_avg10_peak": max(psi_mem, default=None)}
