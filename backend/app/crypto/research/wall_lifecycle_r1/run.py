"""Run `wall-lifecycle.r1` and `entry-advisor.r0` on a copied journal and the public series.

    PYTHONPATH=. python -m app.crypto.research.wall_lifecycle_r1.run \
        --journal DIR --binance DIR --out DIR [--session 31cc8177] [--cut-ms MS]

Writes `results.json` (every cell), `levels.jsonl` (one row per level episode with its outcomes),
`advisor_events.jsonl` and `counters.json`. Deterministic: the same inputs give byte-equal files.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from . import advisor as A
from . import contract as K
from . import study as S
from .outcomes import Series
from .replay import replay

REPO_ROOT = Path(__file__).resolve().parents[5]


def load_binance(directory: Path) -> dict[str, Any]:
    out = {}
    for name in ("klines_1m", "oi_5m", "funding", "premium_1m"):
        path = directory / f"{name}.json"
        out[name] = json.loads(path.read_text()) if path.exists() else []
    return out


def run(journal: Path, binance: Path, out: Path, *, session8: str | None = None,
        cut_ms: int | None = None) -> dict[str, Any]:
    t0 = time.time()
    rep = replay(journal, session8, cut_ms=cut_ms)
    bn = load_binance(binance)
    cut = cut_ms or (rep.path.sample_ms[-1] if rep.path.sample_ms else 0)
    series = Series.build(rep.path, [r for r in bn["klines_1m"] if int(r[6]) < cut],
                          [r for r in bn["oi_5m"] if int(r["timestamp"]) + 300_000 <= cut],
                          [r for r in bn["funding"] if int(r["fundingTime"]) <= cut],
                          [r for r in bn["premium_1m"] if int(r[6]) < cut])
    rows = S.annotate(rep, series)
    q1 = S.q1_persistence(rows)
    q2 = S.q2_notional(rep, series, rows)
    q3 = S.q3_end_class(rows)
    q4 = S.q4_flow(rows)
    q5 = S.q5_next_wall(rep, series)
    q6 = S.q6_cluster(rows)
    deriv = S.derivatives_by_class(rows)
    events = A.build_events(rep, series)
    adv = A.evaluate(events, rep, series)
    coverage = {
        "session_id": rep.session_id, "samples": len(rep.path.sample_ms),
        "first_sample_ms": rep.path.sample_ms[0] if rep.path.sample_ms else None,
        "last_sample_ms": rep.path.sample_ms[-1] if rep.path.sample_ms else None,
        "hours": ((rep.path.sample_ms[-1] - rep.path.sample_ms[0]) / 3_600_000) if rep.path.sample_ms else 0,
        "gaps_over_2_5s": rep.counters.get("gaps", 0),
        "klines_1m": len(series.kline_close_ms), "oi_buckets": len(series.oi_t),
        "funding_rows": len(series.funding_t), "premium_bars": len(series.basis_t),
        "cut_ms": cut_ms,
    }
    counts = {
        "levels_total": len(rep.levels),
        "levels_display_qualified": sum(1 for r in rows if r["display_qualified"]),
        "levels_touched": sum(1 for r in rows if r["t_touch"] is not None),
        "levels_display_touched": sum(1 for r in rows if r["display_qualified"] and r["t_touch"] is not None),
        "levels_approached": sum(1 for r in rows if r["t_approach"] is not None),
        "touches_total": sum(r["touches"] for r in rows),
        "v2_bins": len(rep.bins),
        "v2_bins_touched": sum(1 for b in rep.bins if b.t_touch is not None),
        "v2_bins_with_collector_end": sum(1 for b in rep.bins if b.collector_end is not None),
        "by_side": {side: {"levels": sum(1 for r in rows if r["side"] == side),
                           "display_qualified": sum(1 for r in rows if r["side"] == side and r["display_qualified"]),
                           "touched": sum(1 for r in rows if r["side"] == side and r["t_touch"] is not None)}
                    for side in ("ASK", "BID")},
        "replay_counters": rep.counters,
    }
    results = {
        "version": K.VERSION, "advisor_version": K.ADVISOR_VERSION,
        "contract": K.identity(REPO_ROOT), "coverage": coverage, "counts": counts,
        "q1_persistence": q1, "q2_notional": q2, "q3_end_class": q3, "q4_flow": q4,
        "q5_next_wall": q5, "q6_cluster": q6, "derivatives_by_class": deriv,
        "wall_verdict": S.wall_verdict(q1, q3, q4, q6),
        "advisor": adv,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, sort_keys=True, default=_default) + "\n")
    with open(out / "levels.jsonl", "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=_default) + "\n")
    with open(out / "advisor_events.jsonl", "w") as handle:
        for ev in events:
            handle.write(json.dumps(ev, sort_keys=True, default=_default) + "\n")
    (out / "counters.json").write_text(json.dumps(rep.counters, indent=1, sort_keys=True) + "\n")
    # timing lives outside results.json so two runs on the same input are byte-equal
    (out / "timing.json").write_text(json.dumps({"elapsed_s": round(time.time() - t0, 1)}) + "\n")
    results["elapsed_s"] = round(time.time() - t0, 1)
    return results


def _default(value: Any):
    if isinstance(value, float) and (value != value):
        return None
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(type(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.research.wall_lifecycle_r1.run")
    parser.add_argument("--journal", required=True)
    parser.add_argument("--binance", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--session", default=None)
    parser.add_argument("--cut-ms", type=int, default=None)
    args = parser.parse_args(argv)
    results = run(Path(args.journal), Path(args.binance), Path(args.out), session8=args.session,
                  cut_ms=args.cut_ms)
    print(json.dumps({"wall_verdict": results["wall_verdict"], "advisor_verdict": results["advisor"]["verdict"],
                      "levels": results["counts"]["levels_total"], "events": results["advisor"]["events"],
                      "elapsed_s": results["elapsed_s"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
