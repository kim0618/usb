#!/bin/bash
set -uo pipefail
[ "$(hostname)" = trader-j ] || { echo WRONG_HOST; exit 9; }
MODE=${1:-check}
F=/root/usb/frontend
KEEP_CURRENT=$F/.next-build
KEEP_ROLLBACK=$F/.next-build.pre-marker-leverage-20261018-121704
CANDS=".next-build.failed-crypto-manual-ux-20261016-113758 .next-build.pre-c1-remove-20261015-122146 .next-build.pre-crypto-manual-ux-20261016-113758 .next-build.pre-exfix-20261013-133453 .next-build.pre-flow-20261012-143915 .next-build.pre-hauto-20261014-1354 .next-build.pre-hot-live-manual-20261017-204005 .next-build.pre-polling-20261013-151541 .next-build.pre-same-day-20261016-111531"
[ "$(cat $KEEP_CURRENT/BUILD_ID)" = c4VYa7VRAWu0aFBN3fAvV ] || { echo "STOP: serving build changed"; exit 3; }
[ "$(cat $KEEP_ROLLBACK/BUILD_ID)" = PrBzRXD3I4vTsgKCutS24 ] || { echo "STOP: rollback build changed"; exit 3; }
FDS=$(for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | awk '{print $NF}'; done | sort -u)
MAPS=$(cat /proc/[0-9]*/maps 2>/dev/null | awk '{print $6}' | sort -u)
PROCS=$(for p in /proc/[0-9]*; do echo "$(readlink $p/cwd 2>/dev/null) $(readlink $p/root 2>/dev/null)"; done)
TS=$(date +%Y%m%d-%H%M%S)
MAN=/root/usb_runtime/DISK_CLEANUP_DELETE_MANIFEST_$TS.txt
{
echo "# DISK CLEANUP DELETE MANIFEST $TS (mode=$MODE)"
echo "# DELETE_MANIFEST_BEFORE df: $(df -B1 / | tail -1)"
echo "# keep current : $KEEP_CURRENT BUILD_ID=$(cat $KEEP_CURRENT/BUILD_ID) (served: usb-frontend WorkingDirectory=$F, npm start NEXT_DIST_DIR=.next-build)"
echo "# keep rollback: $KEEP_ROLLBACK BUILD_ID=$(cat $KEEP_ROLLBACK/BUILD_ID) (latest valid rollback, made 2026-10-18 12:24)"
echo "# checks per path: systemd, nginx, symlink, open fd, mmap, process cwd/root, config/cron, runtime scripts (controls: 60 open fds and a /root/usb/frontend mmap detected by the same method)"
printf "#path\tbytes\tBUILD_ID\tfiles\tmtime\tlisting_sha256\trefs\treason\n"
} > $MAN
fail=0
for d in $CANDS; do
  abs=$F/$d
  [ -d "$abs" ] || { echo "MISSING $abs" >> $MAN; continue; }
  case "$abs" in "$KEEP_CURRENT"|"$KEEP_ROLLBACK") echo "STOP: keep path in candidates"; exit 4;; esac
  refs=$(( $(grep -rlF "$d" /etc/systemd/system /lib/systemd/system /etc/nginx 2>/dev/null | wc -l) \
          + $(find $F /root/usb_runtime /etc/systemd/system /etc/nginx -maxdepth 2 -type l -printf '%l\n' 2>/dev/null | grep -cF "$d") \
          + $(echo "$FDS" | grep -cF "$abs") + $(echo "$MAPS" | grep -cF "$abs") + $(echo "$PROCS" | grep -cF "$abs") \
          + $(grep -lF "$d" $F/package.json $F/next.config.* $F/.env* /var/spool/cron/crontabs/* /etc/crontab /etc/cron.d/* /root/usb_runtime/*.sh 2>/dev/null | wc -l) ))
  bytes=$(du -sb "$abs" | cut -f1)
  files=$(find "$abs" -type f | wc -l)
  lsha=$(cd "$abs" && find . -type f -printf '%P %s\n' | sort | sha256sum | cut -c1-16)
  reason="older frontend rollback/build (retention: keep current + latest rollback)"
  case "$d" in *failed*) reason="failed build, referenced by nothing (retention)";; esac
  printf "%s\t%s\t%s\t%s\t%s\t%s\trefs=%s\t%s\n" "$abs" "$bytes" "$(cat $abs/BUILD_ID 2>/dev/null)" "$files" "$(stat -c %y "$abs" | cut -c1-19)" "$lsha" "$refs" "$reason" >> $MAN
  [ "$refs" = 0 ] || fail=1
done
cat $MAN
[ $fail = 0 ] || { echo "STOP: a candidate has a reference"; exit 5; }
[ "$MODE" = delete ] || { echo "check only (manifest kept at $MAN)"; exit 0; }
grep -v '^#' $MAN | cut -f1 | while read -r abs; do
  case "$abs" in $F/.next-build.*) ;; *) echo "STOP: unexpected path $abs"; exit 6;; esac
  [ "$abs" = "$KEEP_ROLLBACK" ] && { echo "STOP: rollback"; exit 6; }
  rm -rf -- "$abs" && echo "deleted $abs"
done
echo "# DELETE_MANIFEST_AFTER df: $(df -B1 / | tail -1)" >> $MAN
for d in $CANDS; do [ -e "$F/$d" ] && echo "STILL PRESENT $F/$d"; done
echo "kept: $(cat $KEEP_CURRENT/BUILD_ID) $(cat $KEEP_ROLLBACK/BUILD_ID)"; ls -d $F/.next-build*
tail -1 $MAN
