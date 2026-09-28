"""Frozen H-PV2C disjoint-universe selection and confirmation gates."""
from __future__ import annotations
from collections import Counter,defaultdict
import hashlib
PV2_DECISIONS=('2024-10-31','2024-11-29','2024-12-31','2025-01-31','2025-02-28','2025-03-31','2025-04-30','2025-05-30','2025-06-30','2025-07-31','2025-08-29','2025-09-30','2025-10-31','2025-11-28','2025-12-31','2026-01-30','2026-02-27','2026-03-31','2026-04-30','2026-05-29')
QUOTAS={'XNYS':60,'XNAS':55,'XASE':5};SEED='STRATEGY_H_PV2C_CONFIRMATION_V1'
def stable_id(r):return r.get('share_class_figi') or r.get('composite_figi')
def select_confirmation(rows,bar_counts,decision_symbols,development_ciks,development_ids):
 eligible=[r for r in rows if r.get('type')=='CS' and r.get('market')=='stocks' and r.get('locale')=='us' and r.get('primary_exchange') in QUOTAS and r.get('cik') and stable_id(r)]
 by=defaultdict(list)
 for r in eligible:by[r['cik']].append(r)
 candidates=[r for r in eligible if len(by[r['cik']])==1 and r['cik'] not in development_ciks and stable_id(r) not in development_ids and bar_counts.get(r['ticker'],0)>=451 and r['ticker'] in decision_symbols]
 selected=[]
 for exchange,quota in QUOTAS.items():
  pool=[r for r in candidates if r['primary_exchange']==exchange]
  pool.sort(key=lambda r:hashlib.sha256(f"{SEED}|{exchange}|{r['cik']}|{stable_id(r)}|{r['ticker']}".encode()).hexdigest())
  if len(pool)<quota:raise ValueError(f'INSUFFICIENT UNIVERSE {exchange}: {len(pool)} < {quota}')
  selected+=pool[:quota]
 assert_no_leakage(selected,development_ciks,development_ids)
 return selected
def assert_no_leakage(rows,development_ciks,development_ids):
 for r in rows:
  if r['cik'] in development_ciks or stable_id(r) in development_ids:raise ValueError('development identity leakage')
 if len({r['cik'] for r in rows})!=len(rows):raise ValueError('confirmation CIK duplicated')
def confirmation_verdict(gates):
 if all(gates[f'C{i}']=='PASS' for i in range(1,9)):return 'CONFIRMED'
 if gates['C8']=='PASS' and sum(gates[f'C{i}']=='PASS' for i in range(1,5))<=1:return 'NOT CONFIRMED'
 return 'INCONCLUSIVE'
