"""Local, read-only replay and memory probe; never creates a paper launch."""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import tempfile
import threading
import time
from datetime import date
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--loader', choices=['frozen', 'lean'], default='lean')
    ap.add_argument('--runtime', default='data/runtime')
    ap.add_argument('--launch', default='2025-09-30')
    ap.add_argument('--as-of', default='2026-10-12')
    ap.add_argument('--window', action='store_true')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    begin = time.monotonic()
    peaks = {'VmRSS': 0, 'RssAnon': 0}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            for line in Path('/proc/self/status').read_text().splitlines():
                key, _, value = line.partition(':')
                if key in peaks:
                    peaks[key] = max(peaks[key], int(value.split()[0]))
            stop.wait(.02)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    from app.market.calendar import MarketCalendar
    from app.strategies.i_paper import view as V, lean as LN, engine as EN, store as ST
    cal = MarketCalendar()
    with tempfile.TemporaryDirectory(prefix='i-prep-replay-') as tmp:
        work = Path(tmp)
        vw = V.build_view(V.Stores(Path(a.runtime).resolve()), date.fromisoformat(a.as_of), work/'view', cal)
        if a.loader == 'frozen':
            m = V.load_v21(vw, work/'scratch')
        else:
            m = LN.load(vw, LN.start_for(vw, a.launch) if a.window else None)
        result = EN.compute(m, cal, date.fromisoformat(a.launch), date.fromisoformat(a.as_of),
                            registry={}, ranking_files=vw.ranking_files, evidence_files=vw.evidence_files)
        events = result['events']
        def digest(value):
            return hashlib.sha256(ST.canon(value).encode()).hexdigest()
        signals = [e for e in events if e['type'] == 'SIGNAL']
        candidates = {s: m.m1.get_rank(s, EN.FACTOR) for s in result['signals']}
        candidates = {s: {k: v for k, v in r.items() if k != 'snap'} for s, r in candidates.items()}
        report = dict(loader=a.loader, launch=a.launch, as_of=a.as_of, window=a.window,
                      snapshot_availability='historical on-time assumption; experiment only',
                      input_manifest=vw.manifest, shape=list(m.C.shape),
                      events=len(events), event_sha256=hashlib.sha256(''.join(ST.canon(e)+'\n' for e in events).encode()).hexdigest(),
                      candidate_digest=digest(candidates), final_state_digest=digest(result.get('latest')),
                      trade_rebalance_digest=digest([e for e in events if e['type'] in ('TRADE','EXEC')]),
                      ranking_digest=digest(signals), continuity_digest=digest({k:result.get(k) for k in ('linked','unresolved','v21_continuity_differences')}),
                      final_state=result.get('latest'), signals=signals, refused=result['refused'])
    stop.set(); thread.join()
    report.update(rss_peak_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  anon_peak_sampled_kib=peaks['RssAnon'], wall_seconds=round(time.monotonic()-begin,3))
    Path(a.out).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('input_manifest','final_state','signals')}))


if __name__ == '__main__':
    main()
