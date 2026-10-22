"""Snapshot parity experiment (local, read-only inputs; never creates a paper launch).

Builds symlink runtimes that differ only in the reference snapshots visible to the ranking, then runs
the packaged lean loader + engine on each and compares digests. Grouped history and splits are the same
files in every arm; splits and H evidence are the server's copies.

  E1  minimal history 2025-10-17..2026-10-20: A = server CS_2026-10-01 only, B = all local snapshots.
      Direct V1 ranking at 2026-10-20 (the snapshot every forward signal through 2026-12-31 uses).
  E2  full history, time-shifted proxy of the server set: A = CS_2026-07-01 only, B = all.
      Engine run launch 2026-07-31 .. as-of 2026-10-20 (3 signals, all on CS_2026-07-01 in both arms).
  E3  full history, literal arms: A = CS_2026-10-01 only, B = all; launch 2025-09-30 (frozen baseline).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
LOCAL = REPO / "data/runtime"
SRV = REPO / "data/runtime/i_paper_deploy_prep_v2/server"
EXP = REPO / "data/runtime/i_paper_deploy_prep_v2/exp"


def link_all(dst: Path, files):
    dst.mkdir(parents=True, exist_ok=True)
    for p in files:
        os.symlink(p.resolve(), dst / p.name)


def build(name: str, grouped, snaps_c, snaps_u1):
    root = EXP / name
    if root.exists():
        raise SystemExit("exists: %s" % root)
    link_all(root / "strategy_c/raw/grouped", grouped)
    link_all(root / "strategy_c/raw/tickers", snaps_c)
    link_all(root / "research_universe_u1/reference/tickers", snaps_u1)
    link_all(root / "strategy_c/raw/splits", sorted((SRV / "strategy_c/raw/splits").glob("*.json.gz")))
    link_all(root / "strategy_h_v2/d7/auto/reference", sorted((SRV / "strategy_h_v2/d7/auto/reference").glob("*.json.gz")))
    return root


def grouped(first: str | None):
    srv = {p.name: p for p in (SRV / "strategy_c/raw/grouped").glob("*.json.gz")}
    loc = {p.name: p for p in (LOCAL / "strategy_c/raw/grouped").glob("*.json.gz") if p.name not in srv}
    allf = dict(loc, **srv)
    return [allf[n] for n in sorted(allf) if first is None or n[:10] >= first]


def main():
    local_c = sorted((LOCAL / "strategy_c/raw/tickers").glob("CS_*.json.gz"))
    local_u1 = sorted((LOCAL / "research_universe_u1/reference/tickers").glob("CS_*.json.gz"))
    srv_1001 = [SRV / "strategy_c/raw/tickers/CS_2026-10-01.json.gz"]
    loc_0701 = [p for p in local_c if p.name == "CS_2026-07-01.json.gz"]
    g_min, g_full = grouped("2025-10-17"), grouped(None)
    arms = {
        "E1_A": build("E1_A", g_min, srv_1001, []), "E1_B": build("E1_B", g_min, local_c, local_u1),
        "E2_A": build("E2_A", g_full, loc_0701, []), "E2_B": build("E2_B", g_full, local_c, local_u1),
        "E3_A": build("E3_A", g_full, srv_1001, []), "E3_B": build("E3_B", g_full, local_c, local_u1),
    }
    # the server's CS_2026-10-01 must be the same bytes as the local one, or arm B is not a superset
    same = hashlib.sha256(srv_1001[0].read_bytes()).hexdigest() == hashlib.sha256((LOCAL / "strategy_c/raw/tickers/CS_2026-10-01.json.gz").read_bytes()).hexdigest()
    env = dict(os.environ, PYTHONPATH=str(REPO / "backend"))
    py = str(REPO / ".venv/bin/python")
    res = {"cs_2026_10_01_server_equals_local": same, "grouped_min": [g_min[0].name, g_min[-1].name, len(g_min)],
           "grouped_full": [g_full[0].name, g_full[-1].name, len(g_full)]}
    for arm in ("E1_A", "E1_B"):
        out = OUT / ("parity_%s.json" % arm)
        subprocess.run([py, str(OUT / "rank_probe.py"), "--runtime", str(arms[arm]), "--as-of", "2026-10-20", "--out", str(out)], check=True, env=env, cwd=REPO)
        res[arm] = json.loads(out.read_text())
    for arm, launch in (("E2_A", "2026-07-31"), ("E2_B", "2026-07-31"), ("E3_A", "2025-09-30"), ("E3_B", "2025-09-30")):
        out = OUT / ("parity_%s.json" % arm)
        subprocess.run([py, str(OUT / "profile.py"), "--runtime", str(arms[arm]), "--launch", launch, "--as-of", "2026-10-20", "--out", str(out)], check=True, env=env, cwd=REPO)
        r = json.loads(out.read_text())
        res[arm] = {k: r[k] for k in ("events", "event_sha256", "candidate_digest", "final_state_digest", "ranking_digest",
                                      "continuity_digest", "trade_rebalance_digest", "shape", "refused")}
    keys = ("events", "event_sha256", "candidate_digest", "final_state_digest", "ranking_digest", "continuity_digest", "trade_rebalance_digest")
    res["E1_equal"] = {k: res["E1_A"][k] == res["E1_B"][k] for k in ("snapshot", "eligible", "ranked", "order_sha256", "rank_sha256")}
    res["E2_equal"] = {k: res["E2_A"][k] == res["E2_B"][k] for k in keys}
    res["E3_equal"] = {k: res["E3_A"][k] == res["E3_B"][k] for k in keys}
    (OUT / "snapshot_parity_result.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in res if k.endswith("_equal") or k.startswith("cs_") or k.startswith("grouped")}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
