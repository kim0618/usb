for s in BTCUSDT SIRENUSDT; do curl -s --max-time 20 "http://127.0.0.1:8100/api/crypto/sizing?symbol=$s" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception as e: print('$s', 'ERR', e); sys.exit()
def walk(x,p=''):
    if isinstance(x,dict):
        for k,v in x.items(): walk(v,p+'.'+k if p else k)
    elif isinstance(x,list):
        for i,v in enumerate(x[:8]): walk(v,p+'['+str(i)+']')
    else:
        if any(t in p.lower() for t in ('qty','label','fraction','feasible','reject','max','depth','symbol','side','available')): print('  ',p,'=',x)
print('== $s'); walk(d)" ; done
du -b /root/usb_runtime/crypto_paper/run/paper-server-001/ 2>/dev/null | tail -1
