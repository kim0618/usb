"""Offline / batch runner. Reads collector journals and a kline file, writes the research journal.

Read only towards everything it reads. Writes only under `--out`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import CONTRACT_SHA256, VERSION
from . import contract as K
from . import detect as D
from . import frames as F
from . import journal as J
from . import outcome as O
from . import stats as S


def compute(klines: Path, roots: list[Path], *, cut_ms: int | None = None) -> dict:
    """Everything known at `cut_ms` (or at the end of the inputs): records, outcomes, summary."""
    rows = F.load_kline_rows(klines, closed_by_ms=cut_ms)
    mf = F.minute_frame(rows)
    sx = F.second_frame(roots, cut_ms=cut_ms)
    records, census = D.detect(mf, sx, decision_cut_ms=cut_ms)
    index_of = {int(ms): i for i, ms in enumerate(sx.frame.ms)}
    outcomes = []
    for rec in records:
        if rec["validity"] != "VALID":
            continue
        j = index_of[rec["decision_ms"]]
        for h in K.HORIZONS_MIN:
            o = O.outcome(sx.frame, rec["lean"], rec["level_price"], rec["side"], h,
                          decision_index=j)
            o["event_id"] = rec["event_id"]
            o["question"] = rec["question"]
            o["fires"] = rec["fires"]
            outcomes.append(o)
    layer = {"samples": len(sx.frame.ms),
             "sessions": len(set(sx.session_id)),
             "first_sample_ms": int(sx.frame.ms[0]) if len(sx.frame.ms) else None,
             "last_sample_ms": int(sx.frame.ms[-1]) if len(sx.frame.ms) else None,
             "liquidity_states": _count(sx.liquidity_state), "flow_states": _count(sx.flow_state),
             "r0_ok_share": round(sum(s.ok for s in sx.frame.samples) / max(1, len(sx.frame.samples)), 4),
             "minutes_published": len(mf.rows)}
    return {"version": VERSION, "contract_sha256": CONTRACT_SHA256, "cut_ms": cut_ms,
            "records": records, "outcomes": outcomes, "census": census, "layers": layer,
            "summary": S.summarise(records, outcomes)}


def _count(values) -> dict:
    out: dict = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.crypto.research.q123_forward_v1")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", required=True)
    f.add_argument("--end-ms", type=int, required=True)
    r = sub.add_parser("run")
    r.add_argument("--klines", required=True)
    r.add_argument("--root", action="append", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--cut-ms", type=int)
    a = p.parse_args(argv)
    if a.cmd == "fetch":
        print(F.fetch_klines(Path(a.out), a.end_ms))
        return 0
    res = compute(Path(a.klines), [Path(x) for x in a.root], cut_ms=a.cut_ms)
    out = Path(a.out)
    rep = J.append(out, res["records"], res["outcomes"])
    (out / "summary.json").write_text(json.dumps(
        {k: v for k, v in res.items() if k not in ("records", "outcomes")}, indent=2,
        sort_keys=True, default=str) + "\n")
    print(json.dumps({"journal": rep, "layers": res["layers"]}, sort_keys=True))
    return 0
