#!/bin/bash
# HOT LIVE manual unlock: backend = 043cee1 live_routes.py only.
set -uo pipefail
[ "$(hostname)" = trader-j ] || { echo WRONG_HOST; exit 9; }
TS=$1; MODE=${2:-check}
SRC=/root/usb_runtime/crypto_paper/src
STAGE=/root/usb_runtime/hot_live_manual_stage_$TS
BK=/root/usb_runtime/hot_live_manual_pre_$TS
PY=/root/usb/.venv/bin/python
F=backend/app/crypto/terminal/live_routes.py
BASE=62ba10c3531122e7466ca47b57b8655d0b9a6b3c534eeee94006ff94f28b8ebe
cd $STAGE || exit 2
sha256sum -c --quiet /root/usb_runtime/hot_live_manual_manifest_$TS.txt || { echo "STOP: stage sha"; exit 3; }
[ "$(find . -type f | wc -l)" = 1 ] || { echo "STOP: stage has extra files"; exit 3; }
[ "$(sha256sum $SRC/$F | cut -d' ' -f1)" = "$BASE" ] || { echo "STOP: live $F is not 198172e/043cee1^"; exit 4; }
echo "base check PASS"
IC=/root/usb_runtime/hot_live_manual_importcheck_$TS; rm -rf $IC; cp -a $SRC/backend $IC; cp $F $IC/${F#backend/}
(cd $IC && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$IC $PY -c "
import sys, py_compile
py_compile.compile('app/crypto/terminal/live_routes.py', cfile='/tmp/hl_compile_check.pyc', doraise=True)
import app.crypto.terminal.live_routes as lr, app.crypto.terminal.api, app.crypto.terminal.server
src = open(lr.__file__).read()
assert 'BINANCE_LIVE_HOT_ORDERS' not in src and 'HOT_LIVE_ORDERS_DISABLED' not in src
assert not hasattr(lr, '_hot_live_refusal') and not hasattr(lr, '_symbol_gates')
assert 'preflight_open' in src and 'router.submit' in src and 'AUTO_SINGLE_SYMBOL' in src
print('compile+import PASS; c1 modules', [m for m in sys.modules if 'c1' in m.split('.')])
") || { echo "STOP: import FAIL"; rm -rf $IC; exit 5; }
rm -rf $IC /tmp/hl_compile_check.pyc
[ "$MODE" = apply ] || { echo "check only"; exit 0; }
mkdir -p $BK/$(dirname $F); cp -a $SRC/$F $BK/$F
(cd $BK && sha256sum $F) > $BK/MANIFEST.txt
(cd $SRC && sha256sum -c --quiet $BK/MANIFEST.txt) || { echo "STOP: backup verify"; exit 6; }
echo "backup PASS $BK"
cp $F $SRC/$F.hl.new && mv $SRC/$F.hl.new $SRC/$F
(cd $SRC && sha256sum -c --quiet /root/usb_runtime/hot_live_manual_manifest_$TS.txt) && echo "apply PASS"
PID0=$(systemctl show usb-crypto-paper -p MainPID --value)
echo "restart usb-crypto-paper (pid $PID0) at $(date +%T)"
systemctl restart usb-crypto-paper
ok=0; for i in $(seq 1 150); do c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8100/api/crypto/state || true); [ "$c" = 200 ] && { ok=1; break; }; sleep 1; done
echo "health=$ok after ${i}s pid $PID0 -> $(systemctl show usb-crypto-paper -p MainPID --value) NRestarts=$(systemctl show usb-crypto-paper -p NRestarts --value)"
if [ $ok != 1 ]; then echo "HEALTH FAIL -> ROLLBACK"; cp -a $BK/$F $SRC/$F; systemctl restart usb-crypto-paper; exit 7; fi
journalctl -u usb-crypto-paper --since "-3min" --no-pager | grep -ciE 'traceback' | sed 's/^/tracebacks: /'
