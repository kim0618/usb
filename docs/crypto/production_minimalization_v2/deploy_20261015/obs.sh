#!/bin/bash
# Read-only observation sample for the C1 removal deploy.
[ "$(hostname)" = trader-j ] || { echo WRONG; exit 1; }
U=usb-crypto-paper; CG=/sys/fs/cgroup/system.slice/$U.service
pid=$(systemctl show -p MainPID --value $U)
t0=$(awk '{print $14+$15}' /proc/$pid/stat); sleep 60; t1=$(awk '{print $14+$15}' /proc/$pid/stat)
echo "$(date +%T) paper pid=$pid NRestarts=$(systemctl show -p NRestarts --value $U) $(awk '/VmRSS/{print "rss="$2"k"}' /proc/$pid/status) $(awk '/^Pss:/{p=$2}/^Swap:/{s=$2}END{print "pss="p"k swap="s"k"}' /proc/$pid/smaps_rollup) cpu60s=$(echo "scale=1; ($t1-$t0)/60" | bc)% threads=$(ls /proc/$pid/task | wc -l) memcur=$(( $(cat $CG/memory.current)/1024 ))k $(grep max $CG/memory.events | tr '\n' ' ') tcp_estab=$(ss -tnp state established 2>/dev/null | grep -c "pid=$pid,")"
grep -E 'MemAvailable|SwapFree' /proc/meminfo | tr -s ' ' | tr '\n' ' '; echo
echo "mem $(head -1 /proc/pressure/memory)"; echo "mem $(sed -n 2p /proc/pressure/memory)"
echo "io  $(head -1 /proc/pressure/io)"; echo "io  $(sed -n 2p /proc/pressure/io)"
for u in usb-crypto-context usb-crypto-q123-forward usb-btc-context-ui usb-frontend; do echo "$u $(systemctl show -p MainPID,NRestarts,ActiveState --value $u | tr '\n' ' ')"; done
ls /root/usb_runtime/crypto_paper/data/runtime/crypto/c1 2>&1 | head -1
journalctl -u $U --since "12:23" --no-pager | grep -ciE 'c1|traceback'
python3 - <<'PY'
import json,time
q=json.load(open("/root/usb_runtime/q123_forward/runtime/status.json"))
print("q123", q["state"], "lag_ms", q["lag_ms"], "samples_read", q["counters"]["samples_read"], "store", q["store_counts"])
f=json.load(open("/root/usb_runtime/btc_context_ui/flow/flow_horizons.json"))
print("flow age_s", round(time.time()-f["written_ms"]/1000), {k:v["quality"] for k,v in f["horizons"].items()})
PY
journalctl -u usb-btc-context-structure -u usb-btc-context-flow -u usb-crypto-manual-research --since "12:15" --no-pager | grep -E 'Failed|timeout|Killed' | wc -l
journalctl -u usb-crypto-manual-research --since "-3min" --no-pager | grep -E 'Finished|Deactivated' | tail -1 | cut -c1-110
tail -1 /root/usb_runtime/manual_trade_research/logs/recorder.log | cut -c1-160
