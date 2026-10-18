#!/bin/bash
[ "$(hostname)" = trader-j ] || exit 9
LIGHT="/root/usb_runtime/crypto_paper/run /root/usb_runtime/crypto_paper/data /root/usb_runtime/q123_forward /root/usb_runtime/btc_context_ui /root/usb_runtime/manual_trade_research /root/usb/data/runtime /var/log/journal /var/log/nginx /var/log/sysstat /root/usb/frontend/.next-build/cache"
heavy() { ionice -c3 nice -n19 du -k --max-depth=2 /root/usb_runtime/crypto_context 2>/dev/null | sort -k2; }
light() { for p in $LIGHT; do printf "%s %s\n" "$(ionice -c3 nice -n19 du -sk $p 2>/dev/null | cut -f1)" "$p"; done; }
echo "== heavy@start $(date +%T)"; heavy > /tmp/g_heavy0; cat /tmp/g_heavy0 | sort -rn | head -12
for n in 0 1 2 3 4 5 6; do
  echo "== sample $n $(date +%T) avail_bytes=$(df -B1 / | awk 'NR==2{print $4}') io[$(head -1 /proc/pressure/io | cut -d' ' -f2-3)] mem[$(head -1 /proc/pressure/memory | cut -d' ' -f2)]"
  light > /tmp/g_light$n; cat /tmp/g_light$n
  [ $n -lt 6 ] && sleep 300
done
echo "== heavy@end $(date +%T)"; heavy > /tmp/g_heavy1
echo "== light delta KB (sample0 -> sample6)"; join -1 2 -2 2 <(sort -k2 /tmp/g_light0) <(sort -k2 /tmp/g_light6) | awk '{printf "%+d KB %s\n", $3-$2, $1}' | sort -rn
echo "== crypto_context delta KB (start -> end, depth 2, nonzero)"; join -1 2 -2 2 <(sort -k2 /tmp/g_heavy0) <(sort -k2 /tmp/g_heavy1) | awk '$3!=$2{printf "%+d KB %s\n", $3-$2, $1}' | sort -rn | head -15
rm -f /tmp/g_heavy0 /tmp/g_heavy1 /tmp/g_light?
