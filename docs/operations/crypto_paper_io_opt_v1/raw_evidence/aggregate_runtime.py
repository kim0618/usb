import json,subprocess,time
from pathlib import Path
root=Path('/root/usb_runtime/crypto_paper/run')
def tailrow(p):
 try:
  with p.open('rb') as f:
   f.seek(0,2);f.seek(max(0,f.tell()-32000));rows=f.read().decode(errors='replace').splitlines()
  for row in reversed(rows):
   try:return json.loads(row)
   except ValueError:pass
 except OSError:pass
 return {}
def sample():
 out={'ts':time.time(),'tapes':{}}
 for p in root.glob('*/input.jsonl'):
  s=p.stat();r=tailrow(p);out['tapes'][p.parent.name]={'bytes':s.st_size,'mtime':s.st_mtime,'last_kind':r.get('kind'),'last_ts_ms':r.get('payload',{}).get('ts_ms')}
 pid=int(subprocess.check_output(['systemctl','show','usb-crypto-paper','-pMainPID','--value']))
 out['pid']=pid;out['io']=dict(x.split(': ') for x in Path(f'/proc/{pid}/io').read_text().splitlines());out['cpu_ticks']=Path(f'/proc/{pid}/stat').read_text().split()[13:15]
 out['available_kb']=next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
 out['psi']={n:Path('/proc/pressure/'+n).read_text() for n in ('memory','io')};return out
out={'before':sample(),'runs':{}}
slot=root/'hot_slot.json'
if slot.exists():
 d=json.loads(slot.read_text());out['hot']={k:d.get(k) for k in ('symbol','selected_at_ms','scanned_at_ms')}
for p in root.glob('*/run_config.json'):
 d=json.loads(p.read_text());run=p.parent
 row={'symbol':d.get('instrument',{}).get('symbol'),'created_mtime':p.stat().st_mtime}
 row['segments']=len(list((run/'segments').glob('*.manifest.json')))
 # Only latest checkpoint state: no price, size, balance, order id, or raw tape/ledger
 checkpoints=sorted((run/'checkpoints').glob('*.checkpoint.json'))
 if checkpoints:
  c=json.loads(checkpoints[-1].read_text());pos=c.get('position',{});row['checkpoint_flat']=str(pos.get('signed_qty')) in ('0','0.0','0.00','0.000');row['checkpoint_keys']=list(c)
 lr=tailrow(run/'ledger.jsonl');row['last_ledger_event_type']=lr.get('event_type');row['last_ledger_ts_ms']=lr.get('ts_ms')
 # First tape record is bounded and reduced to time only
 with (run/'input.jsonl').open() as f:
  try:first=json.loads(f.readline());row['active_first_ts_ms']=first.get('payload',{}).get('ts_ms')
  except ValueError:pass
 out['runs'][run.name]=row
m=Path('/root/usb_runtime/manual_trade_research')
d=json.loads((m/'store/cursor.json').read_text());out['manual_cursor']={k:d.get(k) for k in ('status','last_run_ms','written','s1_fills','s1_error','trigger','market_status','planned_total','events','episodes','snapshots','anomalies')}
for n in ('events','episodes','snapshots'):
 p=m/f'store/{n}.jsonl';d=tailrow(p);out.setdefault('manual_files',{})[n]={'bytes':p.stat().st_size,'mtime':p.stat().st_mtime,**{k:d.get(k) for k in ('recorded_at_ms','as_of_ms','last_fill_ms','closed','status')}}
for name,keys in [('/root/usb_runtime/manual_trade_research/config/manual_trade_research.env',['MTR_TRIGGER_URL']),('/root/usb_runtime/btc_context_ui/config/btc_context_ui.env',['BTC_CONTEXT_MANUAL_STORE','BTC_CONTEXT_ENTRY_ANALYSIS_RESULTS'])]:
 lines=Path(name).read_text().splitlines()
 for key in keys:
  v=next((l.split('=',1)[1] for l in lines if l.startswith(key+'=')),None);out.setdefault('config_presence',{})[key]={'present':v is not None,'nonempty':bool(v)}
p=Path('/root/usb_runtime/crypto_paper/data/runtime/crypto/live/manual_performance.json')
if p.exists():
 d=json.loads(p.read_text());out['s1_summary']={'mtime':p.stat().st_mtime,'keys':list(d)}
 for k,v in d.items():
  if isinstance(v,list):out['s1_summary'][k+'_count']=len(v)
  elif isinstance(v,(int,float)) and ('time' in k or 'ms' in k):out['s1_summary'][k]=v
for base in ('/root/usb_runtime/ae_common_producer/run','/root/usb_runtime/ae_common_producer/snapshots'):
 p=Path(base);out.setdefault('ae_entries',{})[base]=[x.name for x in p.iterdir()] if p.exists() else []
remaining=15-(time.time()-out['before']['ts'])
if remaining>0:time.sleep(remaining)
out['after']=sample();print(json.dumps(out))
