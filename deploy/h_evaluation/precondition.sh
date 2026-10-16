#!/bin/bash
# ExecCondition of usb-h-evaluation (P2.1 activation, 2026-10-16). Exit 0 = run, 1 = skip today's trigger
# (systemd records a skipped condition, not a failure). Starts the evaluation only when H's daily update
# succeeded today, no E/A batch is running, it is inside the window that ends before E paper (03:45 ET),
# and no evaluation was attempted yet today. It never stops or changes another unit.
set -u
skip(){ echo "h-evaluation precondition: SKIP $*"; exit 1; }
TODAY=$(TZ=America/New_York date +%F)
NOW=$(TZ=America/New_York date +%H%M)
[ "$NOW" -ge 0130 ] && [ "$NOW" -le 0245 ] || skip "outside 01:30-02:45 ET (now $NOW ET)"
ts=$(systemctl show -p ExecMainExitTimestamp --value usb-h-forward.service)
res=$(systemctl show -p Result --value usb-h-forward.service)
day=""; [ -n "$ts" ] && day=$(TZ=America/New_York date -d "$ts" +%F 2>/dev/null)
[ "$day" = "$TODAY" ] && [ "$res" = success ] || skip "usb-h-forward not successful today (exit='$ts' result=$res)"
for u in usb-e-universe usb-e-rvol-once usb-grouped-daily usb-splits-refresh usb-e-paper; do
  s=$(systemctl is-active $u.service)
  [ "$s" = active ] || [ "$s" = activating ] || [ "$s" = reloading ] && skip "$u is $s"
done
MARK=/root/usb/data/runtime/strategy_h_v2/d7/auto/evaluation_attempt_day
[ "$(cat $MARK 2>/dev/null)" = "$TODAY" ] && skip "already attempted today ($TODAY ET)"
echo "h-evaluation precondition: RUN ($TODAY ET $NOW)"
exit 0
