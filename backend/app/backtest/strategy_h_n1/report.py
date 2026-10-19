"""Funnel, exclusion and result tables for the H-N1 census (reads the three phase artifacts)."""

from __future__ import annotations

from collections import Counter
import gzip
import json

from app.backtest.strategy_h_n1.common import OUT, file_sha
from app.backtest.strategy_h_n1.run import CUTOFF, EVENTS, OUTCOMES, SUMMARY

FUNNEL = OUT / "h_n1_funnel_v1.json"


def funnel(events: list[dict], out_rows: list[dict], q33: float, q67: float, branch: str) -> dict:
    """branch PRIMARY follows F+, CONTRAST follows F-."""
    by_acc = {(r["cik"], r["accession"]): r for r in out_rows}
    res = {}
    for sp in ("DEV", "HOLDOUT"):
        pool = [e for e in events if e.get("split") == sp
                or (sp == "HOLDOUT" and e.get("exclusion") == "PIT_NO_SIGNAL_SESSION")]
        steps = [("All eligible 10-Q (signal >= snapshot)", lambda e: True),
                 ("PIT valid", lambda e: e.get("exclusion") != "PIT_NO_SIGNAL_SESSION"),
                 ("universe valid", lambda e: e["universe_member"] and e["ticker"] is not None),
                 ("price >= $5", lambda e: e["price_ok"]),
                 ("ADV >= $5M", lambda e: e["adv_ok"]),
                 ("fundamental valid", lambda e: e["fundamental_valid"]),
                 ("F+" if branch == "PRIMARY" else "F-",
                  (lambda e: e["f_plus"]) if branch == "PRIMARY" else (lambda e: e["f_minus"])),
                 ("reaction available", lambda e: e["reaction_available"]),
                 ("WEAK", lambda e: e["reaction"] <= q33),
                 ("entry available", lambda e: e["entry_present"]),
                 ("5D valid", lambda e: "5" in by_acc.get((e["cik"], e["accession"]), {}).get("out", {})),
                 ("21D valid", lambda e: "21" in by_acc.get((e["cik"], e["accession"]), {}).get("out", {})),
                 ("63D valid", lambda e: "63" in by_acc.get((e["cik"], e["accession"]), {}).get("out", {}))]
        cur, n0, table = pool, len(pool), []
        for name, ok in steps:
            nxt = [e for e in cur if ok(e)]
            table.append({"step": name, "input": len(cur), "remaining": len(nxt), "excluded": len(cur) - len(nxt),
                          "step_excl_pct": (len(cur) - len(nxt)) / len(cur) if cur else 0.0,
                          "cum_retention_pct": len(nxt) / n0 if n0 else 0.0})
            cur = nxt
        res[sp] = table
    return res


def main():
    ev = json.loads(gzip.open(EVENTS, "rt").read())["events"]
    cut = json.loads(CUTOFF.read_text())
    rows = json.loads(gzip.open(OUTCOMES, "rt").read())["rows"]
    out = {"primary": funnel(ev, [r for r in rows if r["cohort"] == "PRIMARY"], cut["q33"], cut["q67"], "PRIMARY"),
           "contrast": funnel(ev, [r for r in rows if r["cohort"] == "CONTRAST"], cut["q33"], cut["q67"], "CONTRAST")}
    out["exclusions"] = {sp: dict(Counter(e.get("exclusion") or "NONE" for e in ev
                                          if (e.get("split") or "NO_SIGNAL") == sp).most_common())
                         for sp in ("PRE_SNAPSHOT", "DEV", "HOLDOUT", "NO_SIGNAL")}
    out["fund_status"] = {sp: dict(Counter(e.get("fund_status") for e in ev if e.get("split") == sp
                                           and e.get("universe_member")).most_common()) for sp in ("DEV", "HOLDOUT")}
    elig = [e for e in ev if e.get("exclusion") is None]
    out["buckets"] = {sp: dict(Counter((("F+" if e["f_plus"] else "F-"),
                                         "WEAK" if e["reaction"] <= cut["q33"] else
                                         "MIDDLE" if e["reaction"] <= cut["q67"] else "STRONG")
                                        for e in elig if e["split"] == sp)) for sp in ("DEV", "HOLDOUT")}
    out["buckets"] = {sp: {f"{a}|{b}": n for (a, b), n in d.items()} for sp, d in out["buckets"].items()}
    out["anchor"] = {sp: {"8k_anchor": sum(1 for e in elig if e["split"] == sp and not e["anchor_is_10q"]),
                          "10q_anchor": sum(1 for e in elig if e["split"] == sp and e["anchor_is_10q"]),
                          "submissions_truncated": sum(1 for e in elig if e["split"] == sp and e["submissions_truncated"])}
                     for sp in ("DEV", "HOLDOUT")}
    FUNNEL.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    print("funnel sha", file_sha(FUNNEL))


if __name__ == "__main__":
    main()
