"""One symbol's Common Raw minute bars inside the F0 primary window, with integrity counters.

The Drive ledgers are the authority. A page is read from the local mirror only when the mirror
file's sha256 equals the Drive ledger's ``file_sha256``; otherwise the Drive file is read and
checked the same way. Every page read goes into the read-set.

Nothing here selects or transforms bars beyond: keep bars whose ET date is a primary session,
turn the bar start into (session index, minute of day), drop identical duplicate timestamps and
count them. Critical integrity problems are counted, never repaired.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import gzip
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
DRIVE_WORKSPACE = Path("/mnt/g/내 드라이브/1_US-B")
MIRROR_WORKSPACE = Path(__file__).resolve().parents[4] / "data/runtime/strategy_b_e0/mirror"
MINUTE_DIR = "market_data/raw/massive/minute"
F0_WINDOW_MINUTES = (0, 24 * 60)


@dataclass
class Tape:
    symbol: str
    day: np.ndarray      # int16 session index into the primary sessions
    minute: np.ndarray   # int16 minute of the ET day of the bar start
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    vw: np.ndarray
    integrity: dict = field(default_factory=dict)
    read_set: list = field(default_factory=list)   # (file, file_sha256, source)


class DayClock:
    """UTC ms -> (session index, minute) for the primary sessions, DST-exact per calendar day."""

    def __init__(self, sessions: Sequence[str]):
        self.sessions = [date.fromisoformat(s) for s in sessions]
        first, last = self.sessions[0] - timedelta(days=1), self.sessions[-1] + timedelta(days=1)
        days, starts = [], []
        d = first
        while d <= last + timedelta(days=1):
            days.append(d)
            starts.append(int(datetime.combine(d, time(0), tzinfo=ET).timestamp() * 1000))
            d += timedelta(days=1)
        self.days = days
        self.starts = np.array(starts, dtype=np.int64)
        index = {s: i for i, s in enumerate(self.sessions)}
        self.day_to_session = np.array([index.get(x, -1) for x in days], dtype=np.int32)

    def convert(self, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(session index or -1, minute of day, inside calendar range)."""
        pos = np.searchsorted(self.starts, t, side="right") - 1
        inside = (pos >= 0) & (pos < len(self.days) - 1)
        pos = np.clip(pos, 0, len(self.days) - 2)
        length = self.starts[pos + 1] - self.starts[pos]
        offset = t - self.starts[pos]
        # a DST day is 23 h or 25 h long; minute-of-day is only defined on the wall clock
        if np.any(inside & (length != 86_400_000)):
            raise RuntimeError("a DST transition day lies inside the F0 window")
        minute = (offset // 60_000).astype(np.int32)
        return np.where(inside, self.day_to_session[pos], -1), minute, inside


def _ledgers(folder: Path, first: str, last: str) -> list[dict]:
    out = []
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.request.json")):
        ledger = json.loads(path.read_text(encoding="utf-8"))
        if ledger.get("status") != "COMPLETE":
            continue
        if str(ledger["end"]) < first or str(ledger["start"]) > last:
            continue
        out.append(ledger)
    return out


def load(symbol: str, sessions: Sequence[str], clock: DayClock, *,
         drive: Path = DRIVE_WORKSPACE, mirror: Path = MIRROR_WORKSPACE) -> Tape | None:
    first, last = sessions[0], sessions[-1]
    drive_dir = drive / MINUTE_DIR / symbol
    mirror_dir = mirror / MINUTE_DIR / symbol
    integrity = {"sha_mismatch": 0, "dup_identical": 0, "dup_conflict": 0, "non_minute_ts": 0,
                 "invalid_ohlc_used": 0, "nonpositive_price_used": 0, "null_field_used": 0,
                 "after_2000": 0, "pages": 0}
    read_set = []
    cols = {k: [] for k in ("t", "o", "h", "l", "c", "v", "vw")}
    for ledger in _ledgers(drive_dir, first, last):
        for page in ledger["pages"]:
            name, expected = page["file"], page["file_sha256"]
            body, source = None, None
            candidate = mirror_dir / name
            if candidate.is_file():
                raw = candidate.read_bytes()
                if hashlib.sha256(raw).hexdigest() == expected:
                    body, source = raw, "mirror"
            if body is None:
                raw = (drive_dir / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != expected:
                    integrity["sha_mismatch"] += 1
                    continue
                body, source = raw, "drive"
            read_set.append((f"{symbol}/{name}", expected, source))
            integrity["pages"] += 1
            for r in json.loads(gzip.decompress(body)).get("results") or ():
                cols["t"].append(r["t"])
                for k in ("o", "h", "l", "c", "v", "vw"):
                    x = r.get(k)
                    cols[k].append(np.nan if x is None else float(x))
    if not cols["t"]:
        return None
    t = np.array(cols["t"], dtype=np.int64)
    arrays = {k: np.array(cols[k], dtype=np.float64) for k in ("o", "h", "l", "c", "v", "vw")}
    integrity["non_minute_ts"] = int(np.count_nonzero(t % 60_000))
    day, minute, _ = clock.convert(t)
    keep = day >= 0
    t, day, minute = t[keep], day[keep], minute[keep]
    arrays = {k: v[keep] for k, v in arrays.items()}
    # duplicates (overlapping ledgers or page boundaries): identical ones are dropped and counted
    order = np.lexsort((t,))
    t, day, minute = t[order], day[order], minute[order]
    arrays = {k: v[order] for k, v in arrays.items()}
    if t.size > 1:
        same = np.flatnonzero(t[1:] == t[:-1]) + 1
        if same.size:
            stacked = np.stack([arrays[k] for k in ("o", "h", "l", "c", "v", "vw")], axis=1)
            equal = np.all((stacked[same] == stacked[same - 1])
                           | (np.isnan(stacked[same]) & np.isnan(stacked[same - 1])), axis=1)
            integrity["dup_identical"] = int(equal.sum())
            integrity["dup_conflict"] = int((~equal).sum())
            drop = np.zeros(t.size, dtype=bool)
            drop[same] = True
            t, day, minute = t[~drop], day[~drop], minute[~drop]
            arrays = {k: v[~drop] for k, v in arrays.items()}
    integrity["after_2000"] = int(np.count_nonzero(minute >= 20 * 60))
    used = (minute >= 9 * 60 + 30) & (minute < 20 * 60)
    o, h, l, c = arrays["o"], arrays["h"], arrays["l"], arrays["c"]
    null = ~(np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & np.isfinite(arrays["v"]))
    with np.errstate(invalid="ignore"):
        bad = (h < np.maximum(np.maximum(o, c), l)) | (l > np.minimum(np.minimum(o, c), h))
        nonpos = np.minimum(np.minimum(o, h), np.minimum(l, c)) <= 0
    integrity["null_field_used"] = int(np.count_nonzero(used & null))
    integrity["invalid_ohlc_used"] = int(np.count_nonzero(used & bad & ~null))
    integrity["nonpositive_price_used"] = int(np.count_nonzero(used & nonpos & ~null))
    return Tape(symbol, day.astype(np.int16), minute.astype(np.int16), arrays["o"], arrays["h"],
                arrays["l"], arrays["c"], arrays["v"], arrays["vw"], integrity, read_set)


def subset(tape: Tape, keep: np.ndarray) -> Tape:
    return Tape(tape.symbol, tape.day[keep], tape.minute[keep], tape.o[keep], tape.h[keep],
                tape.l[keep], tape.c[keep], tape.v[keep], tape.vw[keep], tape.integrity, tape.read_set)
