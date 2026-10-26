import os,json,hashlib,subprocess,time
from pathlib import Path
out={'time':time.time(),'psi':{n:Path('/proc/pressure/'+n).read_text() for n in ('memory','io')}}
if float(out['psi']['memory'].split('avg10=')[1].split()[0])>10:
 print(json.dumps(out));raise SystemExit()
base=Path('/root/usb_runtime/crypto_paper/src/backend/app/crypto')
names=['paper/segments.py','paper/ledger.py','terminal/api.py','terminal/server.py','terminal/session.py','terminal/hot_routes.py','terminal/trade_candles.py','hot_coins/slot.py','hot_coins/market.py','symbols.py']
out['sources']={}
for n in names:
 p=base/n
 if p.exists():
  b=p.read_bytes();out['sources'][n]={'sha256':hashlib.sha256(b).hexdigest(),'text':b.decode()}
units=['usb-crypto-paper','usb-crypto-context','usb-crypto-q123-forward','usb-btc-context-ui','usb-btc-context-structure','usb-btc-context-flow','usb-crypto-manual-research','usb-crypto-feedlog','multipathd','rpcbind','networkd-dispatcher','usb-a-snapshot-consumer','usb-ae-producer','usb-grouped-daily','usb-h-forward']
props=['MainPID','ActiveState','MemoryCurrent','MemoryPeak','MemorySwapCurrent','CPUUsageNSec','NRestarts','ExecMainStartTimestamp','FragmentPath','Requires','RequiredBy','Wants','WantedBy']
out['units']={}
for u in units:
 s=subprocess.check_output(['systemctl','show',u]+['-p'+p for p in props],text=True)
 d=dict(x.split('=',1) for x in s.splitlines() if '=' in x);out['units'][u]=d
 pid=int(d.get('MainPID') or 0)
 if pid:
  for f in ('io','smaps_rollup','stat','cgroup'):
   try:d[f]=Path(f'/proc/{pid}/{f}').read_text()
   except OSError:pass
  if u=='usb-crypto-paper':
   d['cmdline']=Path(f'/proc/{pid}/cmdline').read_text().replace('\0',' ')
   d['path_env']=[x for x in Path(f'/proc/{pid}/environ').read_text().split('\0') if x.startswith(('PYTHONPATH=','CRYPTO_PAPER_ROOT=','CRYPTO_PAPER_CONFIG='))]
   d['fds']={p.name:os.readlink(p) for p in Path(f'/proc/{pid}/fd').iterdir() if p.exists()}
# units are reviewed locally; no EnvironmentFile contents are included
out['unit_text']={u:subprocess.run(['systemctl','cat',u],capture_output=True,text=True).stdout for u in ['usb-crypto-paper','usb-crypto-manual-research','usb-btc-context-ui','usb-btc-context-structure','usb-btc-context-flow','usb-grouped-daily.timer','usb-h-forward.timer','usb-ae-producer']}
out['paper_entries']=[str(p) for p in Path('/root/usb_runtime/crypto_paper').iterdir()]
out['manual_entries']=[str(p) for p in Path('/root/usb_runtime/manual_trade_research/store').iterdir()]
for k,cmd in {'listeners':['ss','-lntup'],'mounts':['cat','/proc/mounts'],'blocks':['lsblk','-o','NAME,TYPE,FSTYPE,MOUNTPOINTS'],'multipath':['multipath','-ll']}.items():
 out[k]=subprocess.run(cmd,capture_output=True,text=True).stdout
print(json.dumps(out))
