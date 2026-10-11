"""Context Collector journal, read as of past instants (S3). The journal's context segments are
1 s rows; the newest row with receive_ms <= T is the collector's state as known at T. Read only:
gzip segments and the open segment are opened read-only, a torn last line is skipped."""
from __future__ import annotations

import glob
import gzip
import json
import re
from pathlib import Path

STALE_MS = 5_000
_RECV = re.compile(rb'"receive_ms":\s*(\d+)')


def _segments(root: Path) -> list[Path]:
    files = sorted(glob.glob(str(root / "context" / "context-*")))
    return [Path(f) for f in files if f.endswith(".gz") or f.endswith(".open") or f.endswith(".jsonl")]


def _open(path: Path):
    return gzip.open(path, "rb") if path.name.endswith(".gz") else open(path, "rb")


def _first_ms(path: Path) -> int | None:
    try:
        with _open(path) as fh:
            for line in fh:
                m = _RECV.search(line)
                if m:
                    return int(m.group(1))
    except (OSError, EOFError):
        return None
    return None


class Journal:
    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._segs: list[tuple[int, Path]] | None = None

    def segments(self) -> list[tuple[int, Path]]:
        if self._segs is None:
            out = []
            if self.root is not None and (self.root / "context").exists():
                for p in _segments(self.root):
                    f = _first_ms(p)
                    if f is not None:
                        out.append((f, p))
            out.sort(key=lambda x: (x[0], x[1].name))
            self._segs = out
        return self._segs

    def rows_at(self, times: list[int]) -> dict[int, dict | None]:
        """{T: newest payload row with receive_ms <= T, or None}. One pass over the segments that
        can hold an answer."""
        segs = self.segments()
        out: dict[int, dict | None] = {t: None for t in times}
        if not segs or not times:
            return out
        wanted = sorted(set(times))
        # The segment for T is the last one starting at or before T; T before the first segment
        # has no answer.
        by_seg: dict[int, list[int]] = {}
        for t in wanted:
            idx = None
            for i, (first, _) in enumerate(segs):
                if first <= t:
                    idx = i
                else:
                    break
            if idx is not None:
                by_seg.setdefault(idx, []).append(t)
        for idx, ts in sorted(by_seg.items()):
            ts = sorted(ts)
            k = 0
            best: tuple[int, bytes] | None = None
            try:
                with _open(segs[idx][1]) as fh:
                    for line in fh:
                        m = _RECV.search(line)
                        if not m:
                            continue
                        r = int(m.group(1))
                        while k < len(ts) and r > ts[k]:
                            out[ts[k]] = _parse(best)
                            k += 1
                        if k >= len(ts):
                            break
                        best = (r, line)
            except (OSError, EOFError):
                pass
            while k < len(ts):
                out[ts[k]] = _parse(best)
                k += 1
        return out


def _parse(best: tuple[int, bytes] | None) -> dict | None:
    if best is None:
        return None
    try:
        return json.loads(best[1])
    except ValueError:
        return None


def collector_fields(row: dict | None, as_of_ms: int) -> tuple[dict, str, str | None]:
    """(fields, status, reason). Values are the collector's own strings, unchanged."""
    if row is None:
        return {}, "NOT_AVAILABLE", "NO_JOURNAL_ROW_AT_OR_BEFORE"
    p = row.get("payload") or {}
    recv = int(row.get("receive_ms") or 0)
    age = as_of_ms - recv
    book = p.get("book") or {}
    liq = p.get("liquidity") or {}
    flow = p.get("flow") or {}
    sides = {}
    for side in ("ASK", "BID"):
        s = liq.get(side) or {}
        n = s.get("nearest") or {}
        sides[side] = {"wall_state": s.get("wall_state"), "price": n.get("price"),
                       "distance_bps": n.get("distance_bps"), "notional_usdt": n.get("notional_usdt"),
                       "persistence_ms": n.get("persistence_ms")}
    windows = {}
    for w in ("5s", "15s", "60s"):
        x = (flow.get("windows") or {}).get(w) or {}
        windows[w] = {"buy_usdt": x.get("buy_usdt"), "sell_usdt": x.get("sell_usdt"),
                      "state": x.get("state")}
    fields = {"row_ms": recv, "age_ms": age, "session_id": row.get("session_id"),
              "collector_state": (p.get("collector") or {}).get("state"),
              "book": {k: book.get(k) for k in ("best_bid", "best_ask", "mid", "spread_bps", "state",
                                                "generation")},
              "liquidity": sides, "flow": windows, "flow_state": flow.get("state")}
    if age > STALE_MS:
        return fields, "STALE", f"ROW_AGE_MS_{age}"
    return fields, "OK", None
