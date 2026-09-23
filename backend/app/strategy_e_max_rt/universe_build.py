"""The canonical D-1 eligible universe for a forward session, buildable on a server without Drive.

The rule is not reimplemented here: eligibility is ``strategy_e_max_forward.forward_daily`` over the
frozen daily base, exactly as the local build does. What this module adds is a way to carry the part
of the frozen base a forward session actually reads:

* ``export_base_slice`` writes the last ``sessions`` rows of the frozen panel (close, volume), the
  ticker column space, the split list, the most recent membership snapshots and the allowed
  exchanges. The frozen raw store itself (170 MB of grouped daily, and the memory to parse it) then
  stays on the workstation;
* ``load_base_slice`` rebuilds a ``DailyBase`` from that file, so the server calls the same
  ``eligible_universe`` with the same inputs.

The slice is only ever a *carrier*: a build from it must produce the same symbols and the same digest
as a build from the full panel, and the CLI has a ``verify`` mode that asserts exactly that.

The artifact records ``target_session`` and ``asof_session``. A consumer that needs D-1 identity
checks ``asof_session == calendar.previous_trading_day(target_session)`` and refuses a stale file
rather than deciding on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_c_selection.panel import SplitEvent
from app.market.calendar import MarketCalendar
from app.strategy_e_max_forward import forward_daily as FD, storage as ST

ARTIFACT_FORMAT = "e-canonical-universe-v2"
SLICE_FORMAT = "e-universe-base-slice-v1"
SOURCE = "MASSIVE"
RULE = "E0 daily eligibility via app.strategy_e_v1_1.universe.daily_eligibility (forward_daily)"
#: Fields the digest covers. ``generated_at`` is deliberately outside it: the same inputs on two
#: machines must produce the same digest.
DIGEST_FIELDS = ("format", "target_session", "asof_session", "rules_version", "inputs", "symbols",
                 "close_d_minus_1", "dollar_volume_d_minus_1", "spy_close_d_minus_1")


def rules_version() -> str:
    from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
    return f"e0-daily-eligibility/{load_e0_rules().snapshot_id}"


# -- the base slice --------------------------------------------------------------------------------

def export_base_slice(root: Path, out: Path, *, sessions: int = 60, snapshots: int = 3,
                      base: FD.DailyBase | None = None) -> dict[str, Any]:
    """Write the tail of the frozen daily base that forward sessions read."""
    base = base if base is not None else FD.load_base(root)
    keep = base.sessions[-sessions:]
    start = len(base.sessions) - len(keep)
    kept_snapshots = sorted(base.snapshots)[-snapshots:]
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        close=base.close[start:].astype(np.float64),
        volume=base.volume[start:].astype(np.float64),
        meta=np.array([json.dumps({
            "format": SLICE_FORMAT,
            "rules_version": rules_version(),
            "sessions": [d.isoformat() for d in keep],
            "tickers": list(base.tickers),
            "allowed_exchanges": sorted(base.allowed_exchanges),
            "splits": [[e.ticker, e.execution_date.isoformat(), e.split_from, e.split_to]
                       for e in base.splits],
            "snapshots": {d.isoformat(): sorted(base.snapshots[d]) for d in kept_snapshots},
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })], dtype=object),
        allow_pickle=True)
    return {"sessions": len(keep), "first_session": keep[0].isoformat(), "last_session": keep[-1].isoformat(),
            "tickers": len(base.tickers), "snapshots": [d.isoformat() for d in kept_snapshots],
            "splits": len(base.splits), "bytes": out.stat().st_size, "sha256": ST.sha256_file(out)}


def load_base_slice(path: Path) -> FD.DailyBase:
    payload = np.load(path, allow_pickle=True)
    meta = json.loads(payload["meta"][0])
    if meta["format"] != SLICE_FORMAT:
        raise ValueError(f"{path} is not a {SLICE_FORMAT} file")
    return FD.DailyBase(
        sessions=[date.fromisoformat(d) for d in meta["sessions"]],
        tickers=tuple(meta["tickers"]),
        close=payload["close"],
        volume=payload["volume"],
        splits=tuple(SplitEvent(t, date.fromisoformat(d), float(a), float(b))
                     for t, d, a, b in meta["splits"]),
        snapshots={date.fromisoformat(d): frozenset(v) for d, v in meta["snapshots"].items()},
        allowed_exchanges=frozenset(meta["allowed_exchanges"]))


# -- the artifact ----------------------------------------------------------------------------------

def digest_of(payload: dict[str, Any]) -> str:
    body = {k: payload[k] for k in DIGEST_FIELDS if k in payload}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def build(session: date, *, base: FD.DailyBase, root: Path,
          calendar: MarketCalendar | None = None) -> dict[str, Any]:
    """The canonical universe artifact for ``session``; raises when a required input is missing."""
    cal = calendar or MarketCalendar("America/New_York")
    previous = cal.previous_trading_day(session)
    splits_path = ST.splits_asof_path(root, previous)
    if not splits_path.exists():
        raise FileNotFoundError(f"splits_asof {previous} missing at {splits_path}")
    body = json.loads(gzip.decompress(splits_path.read_bytes()))
    events = tuple(SplitEvent(r["ticker"], date.fromisoformat(r["execution_date"]),
                              float(r["split_from"]), float(r["split_to"]))
                   for r in body["results"] if r["split_from"] != r["split_to"])
    universe, missing = FD.eligible_universe(base, root, session, cal, forward_splits=events)
    if universe is None:
        raise RuntimeError(f"inputs missing: {missing}")
    grouped_path = ST.grouped_path(root, previous)
    grouped = json.loads(gzip.decompress(grouped_path.read_bytes()))["body"]["results"]
    rows = {r["T"]: r for r in grouped}
    payload: dict[str, Any] = {
        "format": ARTIFACT_FORMAT,
        "target_session": session.isoformat(),
        "asof_session": previous.isoformat(),
        "source": SOURCE,
        "rules_version": rules_version(),
        "rule": RULE,
        "inputs": {"grouped_daily_d_minus_1_sha256": ST.sha256_file(grouped_path),
                   "splits_asof_d_minus_1_sha256": ST.sha256_file(splits_path),
                   "note": "split executions on the session itself are not in this list (provisional)"},
        "symbols": list(universe),
        "symbol_count": len(universe),
        "close_d_minus_1": {s: float(rows[s]["c"]) for s in universe},
        "dollar_volume_d_minus_1": {s: float(rows[s]["c"]) * float(rows[s]["v"]) for s in universe},
        "spy_close_d_minus_1": float(rows["SPY"]["c"]),
        # kept so an existing reader keeps working unchanged
        "session": session.isoformat(),
        "d_minus_1": previous.isoformat(),
    }
    payload["digest"] = digest_of(payload)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return payload


def write(payload: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return out


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def d_minus_1_identity(payload: dict[str, Any], session: date,
                       calendar: MarketCalendar | None = None) -> tuple[bool, str]:
    """Is this artifact really the D-1 universe of ``session``? A stale file is never reused."""
    cal = calendar or MarketCalendar("America/New_York")
    target = payload.get("target_session") or payload.get("session")
    asof = payload.get("asof_session") or payload.get("d_minus_1")
    if target != session.isoformat():
        return False, f"artifact targets {target}, not {session.isoformat()}"
    expected = cal.previous_trading_day(session).isoformat()
    if asof != expected:
        return False, f"artifact is as-of {asof}, but D-1 of {session.isoformat()} is {expected}"
    if payload.get("format") == ARTIFACT_FORMAT and payload.get("digest") != digest_of(payload):
        return False, "artifact digest does not match its own content"
    if payload.get("format") != ARTIFACT_FORMAT:
        return True, f"ok (pre-v2 artifact {payload.get('format')}: digest not re-derivable)"
    if not payload.get("symbols"):
        return False, "artifact carries no symbols"
    return True, "ok"


def identical(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, list[str]]:
    """Two builds of the same session, from a full panel and from a slice, must agree exactly."""
    problems = []
    for key in DIGEST_FIELDS:
        if a.get(key) != b.get(key):
            problems.append(key)
    if a.get("digest") != b.get("digest"):
        problems.append("digest")
    return not problems, problems


def sequence_summary(symbols: Sequence[str]) -> str:
    return f"{len(symbols)} symbols {symbols[0]}..{symbols[-1]}" if symbols else "0 symbols"
