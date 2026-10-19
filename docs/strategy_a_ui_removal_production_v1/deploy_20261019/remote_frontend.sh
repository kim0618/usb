#!/bin/bash
# Frontend swap for 4041cf6 (Strategy A UI removal) on the c4VYa7 reconstruction.
# Same procedure as docs/crypto/marker_leverage_production_v1/deploy_20261018/remote_frontend.sh:
# the Next cache is MOVED to the new build; the rollback build keeps everything but it.
set -uo pipefail
TS=$1; NEW_ID=$2; OLD_ID=$3; TGZ_SHA=$4
[ "$(hostname)" = trader-j ] || { echo WRONG_HOST; exit 9; }
cd /root/usb/frontend
[ "$(cat .next-build/BUILD_ID)" = "$OLD_ID" ] || { echo "STOP: served build changed"; exit 3; }
ps -eo args | grep -q "[n]ext build" && { echo "STOP: another next build running"; exit 3; }
FREE=$(df -B1K / | awk 'NR==2{print $4}'); [ "$FREE" -gt 300000 ] || { echo "STOP: only ${FREE}KB free"; exit 3; }
snap(){ for s in usb-frontend usb-backend usb-crypto-paper usb-crypto-context usb-crypto-q123-forward usb-btc-context-ui usb-e-paper usb-ae-producer nginx; do printf "%s pid=%s nr=%s %s\n" $s "$(systemctl show $s -p MainPID --value)" "$(systemctl show $s -p NRestarts --value)" "$(systemctl show $s -p ActiveState --value)"; done; }
snap > /tmp/aui_svc_before; cat /tmp/aui_svc_before
ST=/root/usb_runtime/a_ui_removal_fe_stage_$TS; rm -rf $ST && mkdir -p $ST
echo "$TGZ_SHA  /root/usb_runtime/next-build-a-ui-removal-$TS.tgz" | sha256sum -c --quiet || { echo "STOP: tgz sha"; exit 4; }
tar -xzf /root/usb_runtime/next-build-a-ui-removal-$TS.tgz -C $ST
[ "$(cat $ST/.next-build/BUILD_ID)" = "$NEW_ID" ] || { echo "STOP: staged id mismatch"; exit 4; }
mkdir -p /root/usb_runtime/a_ui_removal_pre_$TS
(cd .next-build && find . -path ./cache -prune -o -type f -print | sort | xargs sha256sum) > /root/usb_runtime/a_ui_removal_pre_$TS/FRONTEND_BUILD_MANIFEST_$OLD_ID.txt
echo "stop/swap/start at $(date +%T) free=${FREE}KB"
systemctl stop usb-frontend
mv .next-build .next-build.pre-a-ui-removal-$TS
mv $ST/.next-build .next-build
[ -d .next-build.pre-a-ui-removal-$TS/cache ] && mv .next-build.pre-a-ui-removal-$TS/cache .next-build/cache
(cd .next-build.pre-a-ui-removal-$TS && find . -path ./cache -prune -o -type f -print | sort | xargs sha256sum) | diff -q - /root/usb_runtime/a_ui_removal_pre_$TS/FRONTEND_BUILD_MANIFEST_$OLD_ID.txt && echo "rollback build intact (cache moved to the new build)"
systemctl start usb-frontend
ok=0; for i in $(seq 1 120); do c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3000/crypto-paper || true); [ "$c" = 200 ] && { ok=1; break; }; sleep 1; done
if [ $ok != 1 ]; then
  echo "SMOKE FAIL -> ROLLBACK"; systemctl stop usb-frontend
  [ -d .next-build/cache ] && mv .next-build/cache .next-build.pre-a-ui-removal-$TS/cache
  mv .next-build .next-build.failed-a-ui-removal-$TS-$(date +%H%M%S)
  mv .next-build.pre-a-ui-removal-$TS .next-build; systemctl start usb-frontend; exit 5
fi
rmdir $ST 2>/dev/null
echo "BUILD_ID now $(cat .next-build/BUILD_ID) after ${i}s (rollback: .next-build.pre-a-ui-removal-$TS)"
for p in /crypto-paper /dashboard /strategy-e /trading-h /strategy-h /strategy-compare /daily /strategy-history /runtime /settings / /trading /adoption /candidates /research /shadow; do printf "%s %s\n" $p "$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 http://127.0.0.1:3000$p)"; done
snap > /tmp/aui_svc_after; echo "-- service diff (only usb-frontend may change):"; diff /tmp/aui_svc_before /tmp/aui_svc_after || true
echo "free after: $(df -B1K / | awk 'NR==2{print $4}')KB"
