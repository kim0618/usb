"""Strategy I paper store: launch once, run idempotently, never rewrite history.

Layout under the store directory (a new directory of its own; no other strategy's store is touched):

* ``launch.json``   written once by ``launch``: launch signal, capital, frozen code and contract hashes.
* ``events.jsonl``  canonical events. A run recomputes every event from the launch to its as-of
  session; the stored file must be an exact prefix of that list, and only the new tail is written
  (whole-file atomic replace). A stored event the inputs no longer reproduce is an integrity
  failure: nothing is written and the run reports it.
* ``state.json``    last as-of, event count and sha256 of ``events.jsonl``; rewritten atomically.
* ``runs.jsonl``    one line per completed run (input manifest, appended count, pending signals).
* ``run.lock``      ``flock`` held for the whole run; a second concurrent run is refused.

Crash recovery needs no special path: a run that died before replacing ``events.jsonl`` left the old
file intact; one that died after it but before ``state.json`` is detected (state behind events) and
the next run re-verifies the prefix and rewrites the state.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import view as V

FORMAT = "US-B-I-PAPER-V1"


class IntegrityError(RuntimeError):
    pass


class Busy(RuntimeError):
    pass


def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text); fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, path)


@contextmanager
def locked(store: Path):
    store.mkdir(parents=True, exist_ok=True)
    fh = open(store / "run.lock", "a+")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as ex:
            raise Busy("another Strategy I paper run holds the lock") from ex
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN); fh.close()


def launch(store: Path, launch_signal: date, capital: int = 10000, stores: V.Stores | None = None, today: str | None = None) -> dict[str, Any]:
    p = store / "launch.json"
    if p.exists():
        raise IntegrityError("already launched: " + p.read_text())
    store.mkdir(parents=True, exist_ok=True)
    body = {"format": FORMAT, "launch_signal": launch_signal.isoformat(), "capital": capital, "factor": "MOM12_2", "cost_bp": 40,
            "v21_contract_canonical": V.V21_CANONICAL, "code_sha256": V.CODE_SHA, "d17_inputs_sha256": V.D17_SHA,
            "continuity": "US-B-I-PAPER-CONTINUITY-PIT-V1 (continuity.py); unresolved -> V1 price-stop rule, recorded",
            "overlay": "observation only; FORWARD_PIT SIC snapshot required", "orders": "none",
            "created_at": datetime.now(timezone.utc).isoformat()}
    _atomic_write(p, json.dumps(body, indent=1, sort_keys=True) + "\n")
    if stores is not None:
        register_snapshots(store, stores, today)
    return body


def read_events(store: Path) -> list[str]:
    p = store / "events.jsonl"
    if not p.exists(): return []
    text = p.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise IntegrityError("events.jsonl ends with a partial line")
    return [ln for ln in text.split("\n") if ln]


def run(store: Path, as_of: date, compute: Callable[[date, date, Path], dict[str, Any]]) -> dict[str, Any]:
    """compute(launch_signal, as_of, work_dir) -> {'events': [...], 'pending': [...], 'manifest': {...}}

    A failed run leaves events and state untouched and writes ``last_error.json`` (cleared by the next
    successful run), so the read model can tell an input or integrity failure from a quiet day."""
    try:
        rec = _run(store, as_of, compute)
    except Busy:
        raise
    except Exception as ex:
        if (store / "launch.json").exists():
            kind = "INPUT_BLOCKED" if isinstance(ex, V.ViewError) else ("INTEGRITY" if isinstance(ex, IntegrityError) else "ERROR")
            _atomic_write(store / "last_error.json", canon({"at": datetime.now(timezone.utc).isoformat(), "as_of": as_of.isoformat(),
                                                            "kind": kind, "error": str(ex)[:500]}) + "\n")
        raise
    (store / "last_error.json").unlink(missing_ok=True)
    return rec


def _run(store: Path, as_of: date, compute: Callable[[date, date, Path], dict[str, Any]]) -> dict[str, Any]:
    with locked(store):
        lp = store / "launch.json"
        if not lp.exists():
            raise IntegrityError("not launched")
        L = json.loads(lp.read_text())
        for leftover in store.glob("*.tmp.*"):
            leftover.unlink()
        state_p = store / "state.json"
        state = json.loads(state_p.read_text()) if state_p.exists() else None
        if state and date.fromisoformat(state["as_of"]) > as_of:
            raise IntegrityError("as-of %s is behind the stored state %s" % (as_of, state["as_of"]))
        stored = read_events(store)
        work = store / "work" / ("%s_%d" % (as_of.isoformat(), os.getpid()))
        if work.exists(): shutil.rmtree(work)
        work.mkdir(parents=True)
        try:
            out = compute(date.fromisoformat(L["launch_signal"]), as_of, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        new = [canon(e) for e in out["events"]]
        if len(stored) > len(new) or any(a != b for a, b in zip(stored, new)):
            bad = next((j for j, (a, b) in enumerate(zip(stored, new)) if a != b), min(len(stored), len(new)))
            raise IntegrityError("stored event %d is not reproduced by the inputs" % bad)
        appended = len(new) - len(stored)
        text = "".join(ln + "\n" for ln in new)
        if appended:
            _atomic_write(store / "events.jsonl", text)
        sha = hashlib.sha256(text.encode()).hexdigest()
        st = {"format": FORMAT, "as_of": as_of.isoformat(), "events": len(new), "events_sha256": sha, "pending_signals": out.get("pending", []),
              "refused_signals": out.get("refused", []), "latest": out.get("latest"), "completed_at": datetime.now(timezone.utc).isoformat()}
        _atomic_write(state_p, json.dumps(st, sort_keys=True) + "\n")
        rec = {"at": datetime.now(timezone.utc).isoformat(), "as_of": as_of.isoformat(), "appended": appended, "events": len(new),
               "events_sha256": sha, "pending": out.get("pending", []), "refused": out.get("refused", []), "manifest": out.get("manifest", {}),
               "continuity_contract": out.get("continuity_contract"), "v21_continuity_differences": out.get("v21_continuity_differences", [])}
        with open(store / "runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(canon(rec) + "\n"); fh.flush(); os.fsync(fh.fileno())
        return rec


def et_today() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def register_snapshots(store: Path, stores: V.Stores, today: str | None = None) -> dict[str, dict[str, str]]:
    """first_seen registry of every reference snapshot file visible now (continuity contract availability)."""
    from . import continuity as CT
    return CT.update_registry(store / "reference_seen.json", stores.snapshot_files(), today or et_today())


def frozen_compute(stores: V.Stores, sic_store: Path | None = None, store: Path | None = None, today: str | None = None,
                   lean: bool = True):
    """The production compute: register snapshots (first_seen = today ET) -> point-in-time view -> frozen
    V2.1 with the PIT continuity table -> events."""
    from app.market.calendar import MarketCalendar
    from . import engine as EN
    from . import lean as LN

    def _c(launch_signal: date, as_of: date, work: Path) -> dict[str, Any]:
        cal = MarketCalendar()
        reg = register_snapshots(store, stores, today) if store is not None else None
        view = V.build_view(stores, as_of, work / "view", cal, hash_grouped=not lean)
        (work / "scratch").mkdir()
        rk = EN.read_snapshots(view.ranking_files, reg)        # before the panel: off the memory peak
        ev = EN.read_snapshots(view.evidence_files, reg)
        m21 = (LN.load(view, LN.start_for(view, launch_signal.isoformat())) if lean else V.load_v21(view, work / "scratch"))
        if lean:                                                # hashed by the panel from the bytes it parsed
            view.manifest["grouped_hash"] = V.grouped_hash(view.grouped_hash_files, m21.m1.grouped_sha256)
        res = EN.compute(m21, cal, launch_signal, as_of, sic_store, registry=reg, ranking_files=rk, evidence_files=ev)
        res["manifest"] = dict(view.manifest, reference_registry_sha256=hashlib.sha256(canon(reg or {}).encode()).hexdigest(),
                               loader=m21.__dict__.get("_lean_manifest", "FROZEN_IMPORT"))
        return res
    return _c
