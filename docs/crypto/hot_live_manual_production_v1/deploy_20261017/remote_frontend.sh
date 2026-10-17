#!/bin/bash
set -uo pipefail
TS=$1; NEW_ID=$2; OLD_ID=$3
[ "$(hostname)" = trader-j ] || { echo WRONG_HOST; exit 9; }
cd /root/usb/frontend
[ "$(cat .next-build/BUILD_ID)" = "$OLD_ID" ] || { echo "STOP: served build changed"; exit 3; }
pgrep -f "next build" >/dev/null && { echo "STOP: another next build running"; exit 3; }
snap(){ for s in usb-frontend usb-backend usb-crypto-paper usb-crypto-context usb-crypto-q123-forward usb-btc-context-ui nginx; do printf "%s pid=%s nr=%s %s\n" $s "$(systemctl show $s -p MainPID --value)" "$(systemctl show $s -p NRestarts --value)" "$(systemctl show $s -p ActiveState --value)"; done; }
snap > /tmp/hlm_svc_before
ST=/root/usb_runtime/hot_live_manual_fe_stage_$TS; rm -rf $ST && mkdir -p $ST
echo "$4  /root/usb_runtime/next-build-hot-live-$TS.tgz" | sha256sum -c --quiet || { echo "STOP: tgz sha"; exit 4; }
mkdir -p /root/usb_runtime/hot_live_manual_pre_$TS
tar -xzf /root/usb_runtime/next-build-hot-live-$TS.tgz -C $ST
[ "$(cat $ST/.next-build/BUILD_ID)" = "$NEW_ID" ] || { echo "STOP: staged id mismatch"; exit 4; }
# rollback copy manifest of the current build
(cd .next-build && find . -path ./cache -prune -o -type f -print | sort | xargs sha256sum) > /root/usb_runtime/hot_live_manual_pre_$TS/FRONTEND_BUILD_MANIFEST_$OLD_ID.txt
echo "stop/swap/start at $(date +%T)"
systemctl stop usb-frontend
mv .next-build .next-build.pre-hot-live-manual-$TS
mv $ST/.next-build .next-build
cp -a .next-build.pre-hot-live-manual-$TS/cache .next-build/cache 2>/dev/null || true
(cd .next-build.pre-hot-live-manual-$TS && find . -path ./cache -prune -o -type f -print | sort | xargs sha256sum) | diff -q - /root/usb_runtime/hot_live_manual_pre_$TS/FRONTEND_BUILD_MANIFEST_$OLD_ID.txt && echo "rollback build intact"
systemctl start usb-frontend
ok=0; for i in $(seq 1 120); do c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3000/crypto-paper || true); [ "$c" = 200 ] && { ok=1; break; }; sleep 1; done
if [ $ok != 1 ]; then
  echo "SMOKE FAIL -> ROLLBACK"; systemctl stop usb-frontend; mv .next-build .next-build.failed-hot-live-manual-$TS-$(date +%H%M%S)
  mv .next-build.pre-hot-live-manual-$TS .next-build; systemctl start usb-frontend; exit 5
fi
echo "BUILD_ID now $(cat .next-build/BUILD_ID) after ${i}s (rollback: .next-build.pre-hot-live-manual-$TS)"
for p in /crypto-paper /trading /adoption /trading-h /; do printf "%s %s\n" $p "$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 http://127.0.0.1:3000$p)"; done
snap > /tmp/hlm_svc_after; echo "-- service diff (only usb-frontend may change):"; diff /tmp/hlm_svc_before /tmp/hlm_svc_after || true
