"""Validate and package only the approved 203 historical grouped files locally."""
import gzip
import hashlib
import json
import math
import tarfile
from datetime import date
from pathlib import Path

from app.market.calendar import MarketCalendar

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / 'data/runtime/strategy_c/raw/grouped'
DEST = ROOT / 'data/runtime/i_paper_deploy_prep_v2'


def main():
    files = sorted(p for p in SOURCE.glob('*.json.gz') if '2025-10-17' <= p.name[:10] <= '2026-08-10')
    cal = MarketCalendar()
    day = date(2025, 10, 17)
    want = []
    while day <= date(2026, 8, 10):
        if cal.is_trading_day(day):
            want.append(day.isoformat())
        day = cal.next_trading_day(day)
    rows, errors = [], []
    for p in files:
        j = json.loads(gzip.open(p).read())
        rs = j.get('body', {}).get('results')
        seen = set()
        if j.get('session') != p.name[:10] or not isinstance(rs, list) or not rs:
            errors.append([p.name, 'session/body/results'])
            continue
        for row in rs:
            t = row.get('T')
            if not isinstance(t, str) or not t or t in seen:
                errors.append([p.name, 'duplicate/invalid ticker', t])
            seen.add(t)
            for k in ('c', 'v'):
                value = row.get(k)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    errors.append([p.name, 'invalid '+k, t])
        rows.append(dict(name=p.name, bytes=p.stat().st_size, rows=len(rs), sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    have = [p.name[:10] for p in files]
    out = dict(first_session=have[0], last_session=have[-1], files=len(files), bytes=sum(x['bytes'] for x in rows),
               missing_sessions=sorted(set(want)-set(have)), extra_sessions=sorted(set(have)-set(want)),
               duplicate_sessions=len(have)-len(set(have)), schema_errors=errors,
               schema='session=filename; nonempty body.results; unique nonempty T; finite nonnegative numeric c,v', entries=rows)
    assert len(files) == 203 and not any(out[k] for k in ('missing_sessions', 'extra_sessions', 'duplicate_sessions', 'schema_errors')), out
    (HERE/'price_history_manifest.json').write_text(json.dumps(out, indent=2)+'\n')
    (HERE/'PRICE_HISTORY_SHA256SUMS').write_text(''.join(x['sha256']+'  '+x['name']+'\n' for x in rows))
    DEST.mkdir(parents=True, exist_ok=True)
    package = DEST/'price_history_2025-10-17_2026-08-10.tar'
    with tarfile.open(package, 'w') as tar:
        for p in files:
            tar.add(p, arcname='grouped/'+p.name)
        tar.add(HERE/'price_history_manifest.json', arcname='manifest.json')
        tar.add(HERE/'PRICE_HISTORY_SHA256SUMS', arcname='SHA256SUMS')
    out = {k:v for k,v in out.items() if k != 'entries'}
    out.update(package=str(package.relative_to(ROOT)), package_sha256=hashlib.sha256(package.read_bytes()).hexdigest())
    (HERE/'price_history_validation.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps(out))


if __name__ == '__main__':
    main()
