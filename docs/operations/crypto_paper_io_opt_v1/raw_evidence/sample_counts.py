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
  s=p.stat();r=tailrow(p);out['tapes'][p.parent.name]={'bytes':s.st_size,'mtime':s.st_mtime,'last_seq':r.get('seq'),'last_kind':r.get('kind'),'last_ts_ms':r.get('payload',{}).get('ts_ms')}
 pid=int(subprocess.check_output(['systemctl','show','usb-crypto-paper','-pMainPID','--value']))
 out['pid']=pid;out['io']=dict(x.split(': ') for x in Path(f'/proc/{pid}/io').read_text().splitlines());out['cpu_ticks']=Path(f'/proc/{pid}/stat').read_text().split()[13:15]
 out['available_kb']=next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
 out['psi']={n:Path('/proc/pressure/'+n).read_text() for n in ('memory','io')};return out
out={'before':sample()}
time.sleep(15)
out['after']=sample();print(json.dumps(out))
