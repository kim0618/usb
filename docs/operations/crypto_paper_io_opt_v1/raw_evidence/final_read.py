from pathlib import Path
import json,hashlib,os,subprocess
out={'psi':Path('/proc/pressure/memory').read_text(),'hashes':{}}
if float(out['psi'].split('avg10=')[1].split()[0])>10:print(json.dumps(out));raise SystemExit()
root=Path('/root/usb_runtime/crypto_paper/src/backend/app/crypto');size=0
for base,dirs,files in os.walk(root):
 dirs[:]=[d for d in dirs if d!='__pycache__']
 for name in files:
  if not name.endswith('.py'):continue
  p=Path(base)/name;size+=p.stat().st_size
  if size>10_000_000:raise RuntimeError('bounded hash read limit')
  out['hashes'][str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
out['source_bytes_hashed']=size
out['units']={}
for u in ('usb-crypto-paper','usb-crypto-context','usb-crypto-q123-forward','usb-btc-context-ui','usb-i-paper'):
 s=subprocess.check_output(['systemctl','show',u,'-pMainPID','-pNRestarts','-pActiveState'],text=True)
 out['units'][u]=dict(l.split('=',1) for l in s.splitlines() if '=' in l)
out['timers']={u:subprocess.run(['systemctl','is-enabled',u+'.timer'],capture_output=True,text=True).stdout.strip() for u in ('usb-btc-context-structure','usb-btc-context-flow','usb-crypto-manual-research','usb-crypto-feedlog','usb-i-paper')}
print(json.dumps(out))
