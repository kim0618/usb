"""H-N1 OUTCOME CENSUS V1 runner: ``python -m app.backtest.strategy_h_n1.run phase1|phase2|phase3``.

Each phase appends a UTC timestamp line to run_log.txt so the order contract-freeze -> event table ->
DEV cutoff -> first forward price read is provable from the log and the artifact digests.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import gzip
import json
import math
import sys

from app.backtest.strategy_h_n1 import cutoff, events, outcomes
from app.backtest.strategy_h_n1.common import (
    BENCH, CONTRACT_CANONICAL, OUT, HN1Stop, file_sha, load_contract, load_panel, load_ticker_maps,
)

EVENTS = OUT / "h_n1_events_phase1.json.gz"
CUTOFF = OUT / "h_n1_dev_reaction_cutoff_v1.json"
OUTCOMES = OUT / "h_n1_outcomes_phase3.json.gz"
SUMMARY = OUT / "h_n1_outcome_summary_v1.json"
LOG = OUT / "run_log.txt"


def _np(o):
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"not serializable: {type(o)}")


def log(msg: str):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def _panel_and_maps():
    maps = load_ticker_maps()
    wanted = {BENCH}
    for cik in maps.universe:
        wanted.update(maps.any_snapshot(cik))
    return load_panel(wanted), maps


def phase1():
    load_contract()
    if EVENTS.exists():
        raise HN1Stop("phase 1 table exists; delete deliberately to rebuild")
    log("PHASE1_START")
    panel, maps = _panel_and_maps()
    ciks = sorted(maps.universe, key=int)
    subs = events.load_submissions(set(ciks))
    log(f"  universe CIKs {len(ciks)}, submissions {len(subs)}, sessions {len(panel.sessions)}")
    ev = events.build_events(panel, maps, subs, ciks, log=log)
    with gzip.open(EVENTS, "wt") as f:
        json.dump({"contract_canonical": CONTRACT_CANONICAL, "events": ev}, f, default=_np)
    log(f"PHASE1_DONE events={len(ev)} sha256={file_sha(EVENTS)}")


def _load_events():
    return json.loads(gzip.open(EVENTS, "rt").read())["events"]


def phase2():
    load_contract()
    ev = _load_events()
    log("PHASE2_START (DEV rows only, no forward price loaded)")
    body = cutoff.freeze(ev, CUTOFF, file_sha(EVENTS))
    log(f"PHASE2_DONE q33={body['q33']:.6f} q67={body['q67']:.6f} n={body['eligible_reaction_n']} "
        f"digest={body['canonical_digest']} sha256={file_sha(CUTOFF)}")


def _iso_week(s: str) -> str:
    y, w, _ = date.fromisoformat(s).isocalendar()
    return f"{y}-W{w:02d}"


def _season(s: str) -> str:
    return f"{s[:4]}Q{(int(s[5:7]) - 1) // 3 + 1}"


def phase3():
    load_contract()
    cut = cutoff.load_frozen(CUTOFF)
    if cut["event_table_sha256"] != file_sha(EVENTS):
        raise HN1Stop("event table changed after the cutoff froze")
    q33, q67 = cut["q33"], cut["q67"]
    ev = _load_events()
    log(f"PHASE3_START cutoff digest {cut['canonical_digest']} (first forward price read follows)")
    for e in ev:
        if e.get("exclusion") is None:
            e["bucket"] = cutoff.bucket(e["reaction"], q33, q67)   # same frozen q for DEV and HOLDOUT
    panel, maps = _panel_and_maps()
    chain = outcomes.Chain(panel, maps)
    rows = []
    for e in ev:
        if e.get("exclusion") is not None or e["bucket"] != "WEAK" or not e["entry_present"]:
            continue
        r = dict(e)
        r["cohort"] = "PRIMARY" if e["f_plus"] else "CONTRAST"
        r["iso_week"], r["season"] = _iso_week(e["signal_session"]), _season(e["signal_session"])
        r["out"] = outcomes.event_outcomes(e, chain)
        rows.append(r)
    log(f"PHASE3_OUTCOMES rows={len(rows)}")
    with gzip.open(OUTCOMES, "wt") as f:
        json.dump({"cutoff_digest": cut["canonical_digest"],
                   "rows": [{**r, "out": {str(k): v for k, v in r["out"].items()}} for r in rows]}, f, default=_np)
    S, SEAS = {}, {}
    for coh in ("PRIMARY", "CONTRAST"):
        for sp in ("DEV", "HOLDOUT"):
            for h in outcomes.ALL_H:
                sel = [r for r in rows if r["cohort"] == coh and r["split"] == sp and h in r["out"]]
                for v in ("obs", "cons"):
                    S[(coh, sp, h, v)] = outcomes.summarize(sel, h, v)
    for h in outcomes.ALL_H:
        sel = [r for r in rows if r["cohort"] == "PRIMARY" and h in r["out"]]
        for v in ("obs", "cons"):
            SEAS[(h, v)] = outcomes.seasons(sel, h, v)
    gates = {(h, v): outcomes.gate_matrix(S, SEAS, h, v) for h in outcomes.PRIMARY_H for v in ("obs", "cons")}
    verdict, why = outcomes.verdict(S, gates)
    overlap = {}
    for sp in ("DEV", "HOLDOUT"):
        sel = [r for r in rows if r["cohort"] == "PRIMARY" and r["split"] == sp and 63 in r["out"]]
        ov = [r for r in sel if r["out"]["overlap63"]]
        cens = [r["out"]["censored63_excess"] for r in sel]
        non = [r["out"][63]["excess_obs"] for r in sel if not r["out"]["overlap63"]]
        overlap[sp] = {"n": len(sel), "overlap_n": len(ov), "overlap_pct": len(ov) / len(sel) if sel else math.nan,
                       "non_overlap_n": len(sel) - len(ov),
                       "censored_mean_0": sum(cens) / len(cens) if cens else math.nan,
                       "non_overlap_mean_0": sum(non) / len(non) if non else math.nan}
    summary = {"contract_canonical": CONTRACT_CANONICAL, "cutoff": cut, "verdict": verdict,
               "final_line": outcomes.final_line(verdict), "why": {str(k): v for k, v in why.items()},
               "stats": {"|".join(map(str, k)): v for k, v in S.items()},
               "seasons": {f"{h}|{v}": s for (h, v), s in SEAS.items()},
               "gates": {f"{h}|{v}": {g: [val, ev_] for g, (val, ev_) in gd.items()} for (h, v), gd in gates.items()},
               "overlap63": overlap, "outcomes_sha256": file_sha(OUTCOMES),
               "events_sha256": file_sha(EVENTS), "cutoff_sha256": file_sha(CUTOFF)}
    SUMMARY.write_text(json.dumps(summary, indent=1, default=str) + "\n")
    log(f"PHASE3_DONE verdict={verdict} summary_sha256={file_sha(SUMMARY)}")


if __name__ == "__main__":
    {"phase1": phase1, "phase2": phase2, "phase3": phase3}[sys.argv[1]]()
