#!/bin/bash
# MULTI-ENTRY MARKERS + SYMBOL LEVERAGE CAP: backend = 4 files at ebf6898, nothing else.
set -uo pipefail
[ "$(hostname)" = trader-j ] || { echo WRONG_HOST; exit 9; }
TS=$1; MODE=${2:-check}
SRC=/root/usb_runtime/crypto_paper/src
STAGE=/root/usb_runtime/crypto_marker_leverage_stage_$TS
BK=/root/usb_runtime/crypto_marker_leverage_pre_$TS
MAN=/root/usb_runtime/crypto_marker_leverage_manifest_$TS.txt
PY=/root/usb/.venv/bin/python
cd $STAGE || exit 2
sha256sum -c --quiet $MAN || { echo "STOP: stage sha"; exit 3; }
[ "$(find . -type f | wc -l)" = 4 ] || { echo "STOP: stage file count"; exit 3; }
FILES=$(find backend -type f | sort)
while read -r f h; do [ "$(sha256sum $SRC/$f | cut -c1-16)" = "$h" ] || { echo "STOP: live $f not base"; exit 4; }; done <<'B'
backend/app/crypto/terminal/executions.py a0e59a716f805503
backend/app/crypto/live/brackets.py 03b08e67c619d9eb
backend/app/crypto/live/preview.py 7fbfc4b93ec49dae
backend/app/crypto/terminal/live_routes.py 35253d67276afd9e
B
echo "base check PASS"
IC=/root/usb_runtime/crypto_marker_leverage_importcheck_$TS; rm -rf $IC; mkdir -p $IC
# Only the backend app tree, without caches: the disk has ~138MB free.
(cd $SRC/backend && tar --exclude=__pycache__ -cf - app) | tar -xf - -C $IC
for f in $FILES; do cp $f $IC/${f#backend/}; done
(cd $IC && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$IC $PY -c "
import sys, py_compile, tempfile, os
for p in ('app/crypto/terminal/executions.py','app/crypto/live/brackets.py','app/crypto/live/preview.py','app/crypto/terminal/live_routes.py'):
    py_compile.compile(p, cfile=os.path.join(tempfile.gettempdir(), 'mlc_check.pyc'), doraise=True)
import app.crypto.terminal.executions as ex, app.crypto.live.brackets as br, app.crypto.live.preview, app.crypto.live.sizing
import app.crypto.terminal.live_routes, app.crypto.terminal.api, app.crypto.terminal.server
import app.crypto.paper.book as b, app.crypto.paper.engine as e
assert hasattr(ex, '_EntryCounter') and br.LEVERAGE_INVALID_FOR_SYMBOL and br.LEVERAGE_TIER_EXCEEDED
print('compile+import PASS; c1 modules', [m for m in sys.modules if 'c1' in m.split('.')], 'SIZING', b.SIZING_BOOK_DEPTH, 'TAPE', e.TAPE_BOOK_DEPTH)
") || { echo "STOP: import FAIL"; rm -rf $IC /tmp/mlc_check.pyc; exit 5; }
rm -rf $IC /tmp/mlc_check.pyc
[ "$MODE" = apply ] || { echo "check only"; exit 0; }
mkdir -p $BK
for f in $FILES; do mkdir -p $BK/$(dirname $f); cp -a $SRC/$f $BK/$f; done
(cd $BK && find backend -type f | sort | xargs sha256sum) > $BK/MANIFEST.txt
(cd $SRC && sha256sum -c --quiet $BK/MANIFEST.txt) || { echo "STOP: backup verify"; exit 6; }
echo "backup PASS $BK ($(wc -l < $BK/MANIFEST.txt) files)"
for f in $FILES; do cp $f $SRC/$f.mlc.new && mv $SRC/$f.mlc.new $SRC/$f; done
(cd $SRC && sha256sum -c --quiet $MAN) && echo "apply PASS"
PID0=$(systemctl show usb-crypto-paper -p MainPID --value)
echo "restart usb-crypto-paper (pid $PID0) at $(date +%T)"
systemctl restart usb-crypto-paper
ok=0; for i in $(seq 1 150); do c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8100/api/crypto/state || true); [ "$c" = 200 ] && { ok=1; break; }; sleep 1; done
echo "health=$ok after ${i}s pid $PID0 -> $(systemctl show usb-crypto-paper -p MainPID --value) NRestarts=$(systemctl show usb-crypto-paper -p NRestarts --value)"
if [ $ok != 1 ]; then echo "HEALTH FAIL -> ROLLBACK"; for f in $FILES; do cp -a $BK/$f $SRC/$f; done; systemctl restart usb-crypto-paper; exit 7; fi
journalctl -u usb-crypto-paper --since "-3min" --no-pager | grep -ciE 'traceback' | sed 's/^/tracebacks: /'
