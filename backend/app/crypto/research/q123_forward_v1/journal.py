"""Contract section 7: an append-only research journal, separate from every trading path.

A record is written once. A rerun appends only ids (and outcome horizons) it has not written before,
and refuses when a recomputed record for an id already on disk differs from what is stored: the
detector is deterministic, so a difference is a defect, not an update.
"""
from __future__ import annotations

import json
from pathlib import Path

EPISODES = "episodes.jsonl"
OUTCOMES = "outcomes.jsonl"


class JournalConflict(RuntimeError):
    pass


def canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(path: Path, key) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            out[key(rec)] = line
    return out


def append(root: Path, episodes: list[dict], outcomes: list[dict]) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    ep_key = lambda r: r["event_id"]  # noqa: E731
    oc_key = lambda r: (r["event_id"], r["horizon_min"])  # noqa: E731
    report = {"episodes_new": 0, "episodes_existing": 0, "outcomes_new": 0, "outcomes_existing": 0}
    for name, rows, key, label in ((EPISODES, episodes, ep_key, "episodes"),
                                   (OUTCOMES, [o for o in outcomes if o["status"] == "MEASURED"],
                                    oc_key, "outcomes")):
        path = root / name
        stored = _load(path, key)
        fresh = []
        for rec in rows:
            line = canonical(rec)
            k = key(rec)
            if k in stored:
                if stored[k] != line:
                    raise JournalConflict(f"{name}: recomputed record for {k} differs from disk")
                report[f"{label}_existing"] += 1
                continue
            fresh.append(line)
            stored[k] = line
        if fresh:
            with open(path, "a") as handle:
                handle.write("".join(line + "\n" for line in fresh))
        report[f"{label}_new"] += len(fresh)
    return report
