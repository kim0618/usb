"""V1 MOM12_2 ranking at one session through the packaged lean loader (read-only probe)."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import date
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from app.market.calendar import MarketCalendar
    from app.strategies.i_paper import view as V, lean as LN, engine as EN
    with tempfile.TemporaryDirectory(prefix="i-rank-probe-") as tmp:
        vw = V.build_view(V.Stores(Path(a.runtime).resolve()), date.fromisoformat(a.as_of), Path(tmp) / "view", MarketCalendar())
        m = LN.load(vw)
        R = m.m1.get_rank(a.as_of, EN.FACTOR)
        snap = max(d for d in m.m1.snap_dates if d <= a.as_of)
        rep = dict(as_of=a.as_of, snapshot=snap, snapshots=list(m.m1.snap_dates), shape=list(m.C.shape),
                   pos=m.m1.pos[a.as_of], eligible=R["eligible"], ranked=len(R["order"]),
                   order_sha256=hashlib.sha256("\n".join(R["order"]).encode()).hexdigest(),
                   rank_sha256=hashlib.sha256(json.dumps(R["rank"], sort_keys=True).encode()).hexdigest(),
                   top20=R["order"][:20])
    Path(a.out).write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps({k: v for k, v in rep.items() if k != "top20"}))


if __name__ == "__main__":
    main()
