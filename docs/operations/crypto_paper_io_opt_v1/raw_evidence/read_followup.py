import json,subprocess,hashlib
from pathlib import Path
out={'psi':Path('/proc/pressure/memory').read_text()}
if float(out['psi'].split('avg10=')[1].split()[0])>10:print(json.dumps(out));raise SystemExit()
out['a_consumer']={k:subprocess.run(['systemctl',cmd,'usb-a-snapshot-consumer'+suffix],capture_output=True,text=True).stdout.strip() for k,cmd,suffix in [('enabled','is-enabled','.timer'),('active','is-active','.service')]}
out['hold_a']=Path('/root/usb_runtime/ae_common_producer/HOLD_A').exists()
out['ae']={}
for day in ('2026-10-19','2026-10-20','2026-10-21'):
 p=Path('/root/usb_runtime/ae_common_producer/run')/day
 row={'files':[x.name for x in p.iterdir()]}
 for f in p.glob('*.json'):
  if f.stat().st_size>2_000_000:continue
  d=json.loads(f.read_text())
  if isinstance(d,dict):
   row[f.name]={'keys':list(d)}
   for k in ('rolling','a_sweep','seal_0915','resources','timing','phases'):
    if k in d:row[f.name][k]=d[k]
 s=Path('/root/usb_runtime/ae_common_producer/snapshots')/day
 row['snapshot_sizes']={x.name:x.stat().st_size for x in s.iterdir() if x.is_file()}
 out['ae'][day]=row
# No fstab contents exported, only filesystem type and NFS occurrence count
fstab=Path('/etc/fstab').read_text();out['fstab_nfs_entries']=sum(1 for l in fstab.splitlines() if not l.lstrip().startswith('#') and any(x in ('nfs','nfs4') for x in l.split()))
out['rpcinfo']=subprocess.run(['rpcinfo','-p'],capture_output=True,text=True).stdout
out['dispatcher_hook_names']={str(p):[x.name for x in p.iterdir()] for p in (Path('/etc/networkd-dispatcher'),Path('/usr/lib/networkd-dispatcher')) if p.exists()}
out['source_hashes']={}
for tree,rel in [('btc_context_ui','crypto/btc_context_ui_v1/api.py'),('btc_context_ui','crypto/btc_context_ui_v1/entry_context.py'),('manual_trade_research','crypto/manual_snapshot_v1/recorder.py'),('ae_common_producer','ae_common/producer.py')]:
 p=Path('/root/usb_runtime')/tree/'src/backend/app'/rel
 if p.exists():out['source_hashes'][tree+'/'+rel]=hashlib.sha256(p.read_bytes()).hexdigest()
m=Path('/root/usb_runtime/manual_trade_research/store/cursor.json');d=json.loads(m.read_text());out['manual_extra']={k:d.get(k) for k in ('market_requests','deferred_snapshots','s1_last_calculated_ms','waiting_for_s1_classification')};out['manual_open_episode_present']=d.get('open_episode') is not None
print(json.dumps(out))
