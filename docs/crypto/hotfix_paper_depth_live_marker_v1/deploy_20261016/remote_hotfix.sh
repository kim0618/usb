#!/bin/bash
set -uo pipefail
[ "$(hostname)" = trader-j ] || { echo WRONG_HOST; exit 9; }
TS=$1; MODE=${2:-check}
SRC=/root/usb_runtime/crypto_paper/src
STAGE=/root/usb_runtime/crypto_hotfix_stage_$TS
BK=/root/usb_runtime/crypto_hotfix_pre_$TS
PY=/root/usb/.venv/bin/python
cd $STAGE || exit 2
sha256sum -c --quiet /root/usb_runtime/crypto_hotfix_manifest_$TS.txt || { echo "STOP: stage sha"; exit 3; }
FILES=$(find backend -type f | sort)
while read -r f h; do [ "$(sha256sum $SRC/$f | cut -c1-12)" = "$h" ] || { echo "STOP: live $f not base"; exit 4; }; done <<'B'
backend/app/crypto/hot_coins/market.py f363f62c441c
backend/app/crypto/paper/book.py 33d9999b4369
backend/app/crypto/paper/engine.py 6f365178be50
backend/app/crypto/paper/trade_preview.py 633c3a8d7a1e
backend/app/crypto/terminal/feed.py 380aa656eb6b
backend/app/crypto/terminal/session.py 3e88a987f0e1
backend/app/crypto/terminal/executions.py 2f5185ffd770
backend/app/crypto/terminal/live_routes.py cd0f58b0cb96
B
echo "base check PASS"
IC=/root/usb_runtime/crypto_hotfix_importcheck_$TS; rm -rf $IC; cp -a $SRC/backend $IC
for f in $FILES; do cp $f $IC/${f#backend/}; done
(cd $IC && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$IC $PY -c "
import sys
import app.crypto.paper.book as b, app.crypto.paper.engine as e, app.crypto.paper.trade_preview, app.crypto.terminal.feed, app.crypto.terminal.session, app.crypto.hot_coins.market
import app.crypto.terminal.executions, app.crypto.terminal.live_routes, app.crypto.terminal.api, app.crypto.terminal.server
print('import PASS; c1 modules', [m for m in sys.modules if 'c1' in m.split('.')], 'SIZING', b.SIZING_BOOK_DEPTH, 'TAPE', e.TAPE_BOOK_DEPTH)
") || { echo "STOP: import FAIL"; rm -rf $IC; exit 5; }
rm -rf $IC
[ "$MODE" = apply ] || { echo "check only"; exit 0; }
mkdir -p $BK
for f in $FILES; do mkdir -p $BK/$(dirname $f); cp -a $SRC/$f $BK/$f; done
(cd $BK && find backend -type f | sort | xargs sha256sum) > $BK/MANIFEST.txt
(cd $SRC && sha256sum -c --quiet $BK/MANIFEST.txt) || { echo "STOP: backup verify"; exit 6; }
echo "backup PASS $BK ($(wc -l < $BK/MANIFEST.txt) files)"
for f in $FILES; do cp $f $SRC/$f.hf.new && mv $SRC/$f.hf.new $SRC/$f; done
(cd $SRC && sha256sum -c --quiet /root/usb_runtime/crypto_hotfix_manifest_$TS.txt) && echo "apply PASS"
PID0=$(systemctl show usb-crypto-paper -p MainPID --value)
echo "restart usb-crypto-paper (pid $PID0) at $(date +%T)"
systemctl restart usb-crypto-paper
ok=0; for i in $(seq 1 150); do c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8100/api/crypto/state || true); [ "$c" = 200 ] && { ok=1; break; }; sleep 1; done
echo "health=$ok after ${i}s pid $PID0 -> $(systemctl show usb-crypto-paper -p MainPID --value) NRestarts=$(systemctl show usb-crypto-paper -p NRestarts --value)"
if [ $ok != 1 ]; then echo "HEALTH FAIL -> ROLLBACK"; for f in $FILES; do cp -a $BK/$f $SRC/$f; done; systemctl restart usb-crypto-paper; exit 7; fi
journalctl -u usb-crypto-paper --since "-3min" --no-pager | grep -ciE 'traceback' | sed 's/^/tracebacks: /'
