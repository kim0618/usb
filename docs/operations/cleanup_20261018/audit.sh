#!/bin/bash
set -u
[ "$(hostname)" = trader-j ] || exit 9
F=/root/usb/frontend
CANDS=".next-build.failed-crypto-manual-ux-20261016-113758 .next-build.pre-c1-remove-20261015-122146 .next-build.pre-crypto-manual-ux-20261016-113758 .next-build.pre-exfix-20261013-133453 .next-build.pre-flow-20261012-143915 .next-build.pre-hauto-20261014-1354 .next-build.pre-hot-live-manual-20261017-204005 .next-build.pre-polling-20261013-151541 .next-build.pre-same-day-20261016-111531"
# Process-side views, collected once.
PROCS=$(for p in /proc/[0-9]*; do pid=${p#/proc/}; c=$(readlink $p/cwd 2>/dev/null); r=$(readlink $p/root 2>/dev/null); echo "$pid cwd=$c root=$r"; done)
FDS=$(for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | awk '{print $NF}'; done | sort -u)
MAPS=$(cat /proc/[0-9]*/maps 2>/dev/null | awk '{print $6}' | sort -u)
LINKS=$(find $F /root/usb_runtime /etc/systemd/system /etc/nginx -maxdepth 2 -type l -printf '%p -> %l\n' 2>/dev/null)
for d in $CANDS; do
  abs=$F/$d
  sd=$(grep -rlF "$d" /etc/systemd/system /lib/systemd/system 2>/dev/null | wc -l)
  ng=$(grep -rlF "$d" /etc/nginx 2>/dev/null | wc -l)
  ln=$(echo "$LINKS" | grep -cF "$d")
  fd=$(echo "$FDS" | grep -cF "$abs")
  mp=$(echo "$MAPS" | grep -cF "$abs")
  cw=$(echo "$PROCS" | grep -cF "$abs")
  cfg=$(grep -lF "$d" $F/package.json $F/next.config.* $F/.env* /var/spool/cron/crontabs/* /etc/crontab /etc/cron.d/* 2>/dev/null | wc -l)
  scr=$(grep -lF "$d" /root/usb_runtime/*.sh 2>/dev/null | wc -l)
  bid=$(cat $abs/BUILD_ID 2>/dev/null)
  printf "%s\tBUILD_ID=%s\tsystemd=%s nginx=%s symlink=%s fd=%s mmap=%s cwd/root=%s config/cron=%s runtime_scripts=%s\n" "$d" "$bid" $sd $ng $ln $fd $mp $cw $cfg $scr
done
echo "== control: the serving build is seen by the same checks"
abs=$F/.next-build
echo "serving fd=$(echo "$FDS" | grep -cE "^$abs(/|$)") mmap=$(echo "$MAPS" | grep -cE "^$abs/") cwd(frontend)=$(echo "$PROCS" | grep -cF "cwd=$F ") config(NEXT_DIST_DIR=.next-build in package.json)=$(grep -c 'NEXT_DIST_DIR=.next-build ' $F/package.json)"
echo "== symlinks under known paths"; echo "$LINKS" | head -20
