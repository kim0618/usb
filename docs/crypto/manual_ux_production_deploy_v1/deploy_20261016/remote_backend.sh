#!/bin/bash
# Crypto manual UX + HALF CLOSE backend apply. Path-specific, usb-crypto-paper only.
set -uo pipefail
[ "$(hostname)" = trader-j ] || { echo WRONG_HOST; exit 9; }
TS=$1; MODE=${2:-check}
SRC=/root/usb_runtime/crypto_paper/src
STAGE=/root/usb_runtime/crypto_manual_ux_stage_$TS
BK=/root/usb_runtime/crypto_manual_ux_pre_$TS
FILES="backend/app/crypto/live/arm.py backend/app/crypto/live/orders.py backend/app/crypto/paper/config.py backend/app/crypto/paper/engine.py backend/app/crypto/paper/sizing.py backend/app/crypto/terminal/api.py backend/app/crypto/terminal/live_routes.py backend/app/crypto/terminal/server.py"
NEWF=backend/app/crypto/terminal/executions.py
PY=/root/usb/.venv/bin/python
cd $STAGE || exit 2
sha256sum -c --quiet /root/usb_runtime/crypto_manual_ux_stage_manifest_$TS.txt || { echo "STOP: stage sha mismatch"; exit 3; }
# base check: live files must be the 52f8b93^ versions
while read -r f h; do [ "$(sha256sum $SRC/$f | cut -c1-12)" = "$h" ] || { echo "STOP: live $f is not base"; exit 4; }; done <<'B'
backend/app/crypto/live/arm.py f63637dcd96f
backend/app/crypto/live/orders.py 70384cad604f
backend/app/crypto/paper/config.py 2fc806140e4f
backend/app/crypto/paper/engine.py f0455940a1d7
backend/app/crypto/paper/sizing.py f5d320d96021
backend/app/crypto/terminal/api.py 699bc056e0b6
backend/app/crypto/terminal/live_routes.py 4d350c9647d6
backend/app/crypto/terminal/server.py 65b12dea8fae
B
[ ! -e $SRC/$NEWF ] || { echo "STOP: executions.py already present"; exit 4; }
echo "base check PASS"
# import check on a throwaway copy of src with the new files overlaid
IC=/root/usb_runtime/crypto_manual_ux_importcheck_$TS; rm -rf $IC; cp -a $SRC/backend $IC
for f in $FILES $NEWF; do cp ${f} $IC/${f#backend/}; done
(cd $IC && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$IC $PY -c "
import py_compile,sys
for f in sys.argv[1:]: py_compile.compile(f, doraise=True)
import app.crypto.live.arm, app.crypto.live.orders, app.crypto.paper.config, app.crypto.paper.engine, app.crypto.paper.sizing
import app.crypto.terminal.executions, app.crypto.terminal.api, app.crypto.terminal.live_routes, app.crypto.terminal.server
import app.crypto.paper.config as c
print('import PASS; modules with c1:', [m for m in sys.modules if 'c1' in m.split('.')])
print('depth constants:', {k:getattr(c,k) for k in dir(c) if 'DEPTH' in k})
" $(for f in $FILES $NEWF; do echo ${f#backend/}; done)) || { echo "STOP: import check FAIL"; rm -rf $IC; exit 5; }
rm -rf $IC
[ "$MODE" = apply ] || { echo "check only"; exit 0; }
# backup
mkdir -p $BK
for f in $FILES; do mkdir -p $BK/$(dirname $f); cp -a $SRC/$f $BK/$f; done
systemctl cat usb-crypto-paper > $BK/unit_cat_before.txt
(cd $BK && find backend -type f | sort | xargs sha256sum) > $BK/MANIFEST.txt
(cd $SRC && sha256sum -c --quiet $BK/MANIFEST.txt) || { echo "STOP: backup verify FAIL"; exit 6; }
echo "backup PASS $BK ($(wc -l < $BK/MANIFEST.txt) files)"
# apply
for f in $FILES $NEWF; do cp $f $SRC/$f.manualux.new && mv $SRC/$f.manualux.new $SRC/$f; done
(cd $SRC && for f in $FILES $NEWF; do sha256sum $f; done) | diff <(cd $STAGE && for f in $FILES $NEWF; do sha256sum $f; done) - && echo "apply PASS"
PID0=$(systemctl show usb-crypto-paper -p MainPID --value)
echo "restart usb-crypto-paper (pid $PID0) at $(date +%T)"
systemctl restart usb-crypto-paper
ok=0; for i in $(seq 1 150); do c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8100/api/crypto/state || true); [ "$c" = 200 ] && { ok=1; break; }; sleep 1; done
PID1=$(systemctl show usb-crypto-paper -p MainPID --value)
echo "health=$ok after ${i}s pid $PID0 -> $PID1 NRestarts=$(systemctl show usb-crypto-paper -p NRestarts --value)"
if [ $ok != 1 ]; then
  echo "HEALTH FAIL -> ROLLBACK"; journalctl -u usb-crypto-paper --since "-3min" --no-pager | tail -30
  for f in $FILES; do cp -a $BK/$f $SRC/$f; done; rm -f $SRC/$NEWF; systemctl restart usb-crypto-paper; exit 7
fi
journalctl -u usb-crypto-paper --since "-3min" --no-pager | grep -ciE 'traceback|error' | sed 's/^/journal traceback|error lines: /'
