"""Memory profile of the final lean loader on the production compute path (window loader, local only).

launch   : minimal history 2025-10-17..2026-10-20 + the server's CS_2026-10-01 (E1_A), launch 2026-10-30.
full     : all local history 2024-09-16..2026-10-20 + every snapshot (E3_B), launch 2025-09-30, no window.
+N months: real data, time-shifted. Launch L is N months before the production launch; the kept rows are
           the window loader's (260 before L .. 2026-10-20) and the snapshots are the quarterly ones from
           the one in force at L on, i.e. what the server will have accumulated N months after launch.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
EXP = REPO / "data/runtime/i_paper_deploy_prep_v2/exp"
LOCAL = REPO / "data/runtime"
SRV = REPO / "data/runtime/i_paper_deploy_prep_v2/server"
RUNS = [("launch", "E1_A", "2026-10-30", True, None), ("full", "E3_B", "2025-09-30", False, None),
        ("+1M", "P1M", "2026-09-30", True, "2026-07-01"), ("+3M", "P3M", "2026-07-31", True, "2026-07-01"),
        ("+6M", "P6M", "2026-04-30", True, "2026-04-01"), ("+12M", "P12M", "2025-10-31", True, "2025-10-01")]


def proxy(name: str, first_snapshot: str) -> Path:
    root = EXP / name
    if root.exists(): return root
    srv = {p.name: p for p in (SRV / "strategy_c/raw/grouped").glob("*.json.gz")}
    loc = {p.name: p for p in (LOCAL / "strategy_c/raw/grouped").glob("*.json.gz")}
    allg = dict(loc, **srv)
    for sub, files in (("strategy_c/raw/grouped", [allg[n] for n in sorted(allg)]),
                       ("strategy_c/raw/tickers", [p for p in sorted((LOCAL / "strategy_c/raw/tickers").glob("CS_*.json.gz")) if p.name[3:13] >= first_snapshot]),
                       ("strategy_c/raw/splits", sorted((SRV / "strategy_c/raw/splits").glob("*.json.gz"))),
                       ("strategy_h_v2/d7/auto/reference", sorted((SRV / "strategy_h_v2/d7/auto/reference").glob("*.json.gz")))):
        (root / sub).mkdir(parents=True)
        for p in files: os.symlink(p.resolve(), root / sub / p.name)
    (root / "research_universe_u1/reference/tickers").mkdir(parents=True)
    return root


def main():
    env = dict(os.environ, PYTHONPATH=str(REPO / "backend"))
    rows = []
    for label, arm, launch, window, first in RUNS:
        rt = proxy(arm, first) if first else EXP / arm
        out = OUT / ("memory_%s.json" % arm)
        cmd = [str(REPO / ".venv/bin/python"), str(OUT / "profile.py"), "--runtime", str(rt), "--launch", launch,
               "--as-of", "2026-10-20", "--out", str(out)] + (["--window"] if window else [])
        subprocess.run(cmd, check=True, env=env, cwd=REPO, stdout=subprocess.DEVNULL)
        r = json.loads(out.read_text())
        rows.append(dict(label=label, arm=arm, launch=launch, window=window, shape=r["shape"], events=r["events"],
                         snapshots=len(r["input_manifest"]["tickers"]), rss_peak_mib=round(r["rss_peak_kib"] / 1024, 1),
                         anon_peak_mib=round(r["anon_peak_sampled_kib"] / 1024, 1), wall_s=r["wall_seconds"]))
        print(json.dumps(rows[-1]))
    (OUT / "memory_profile_result.json").write_text(json.dumps(rows, indent=1) + "\n")


if __name__ == "__main__":
    main()
