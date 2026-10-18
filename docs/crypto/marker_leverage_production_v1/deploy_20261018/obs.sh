#!/bin/bash
[ "$(hostname)" = trader-j ] || exit 9
for n in $(seq 1 10); do
  t0=$(date +%s%N); curl -s -o /dev/null --max-time 20 http://127.0.0.1:8100/api/crypto/state?symbol=BTCUSDT; t1=$(date +%s%N)
  curl -s -o /dev/null --max-time 20 http://127.0.0.1:8000/api/v1/research/current; t2=$(date +%s%N)
  line="$(date +%T)"
  for u in usb-crypto-paper usb-frontend usb-backend usb-crypto-context usb-crypto-q123-forward; do pid=$(systemctl show -p MainPID --value $u); nr=$(systemctl show -p NRestarts --value $u)
    line="$line | ${u#usb-} pid=$pid nr=$nr $(awk '/VmRSS/{printf "rss=%dM",$2/1024}' /proc/$pid/status) $(awk '/^Pss:/{p=$2}/^Swap:/{s=$2}END{printf "pss=%dM swap=%dM",p/1024,s/1024}' /proc/$pid/smaps_rollup)"; done
  echo "$line"
  echo "   avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)M swapused=$(awk '/SwapTotal/{t=$2}/SwapFree/{f=$2}END{print int((t-f)/1024)}' /proc/meminfo)M mem[$(head -1 /proc/pressure/memory | cut -d' ' -f2-4)] io[$(head -1 /proc/pressure/io | cut -d' ' -f2-4)] lat_crypto=$(( (t1-t0)/1000000 ))ms lat_backend=$(( (t2-t1)/1000000 ))ms disk_free_kb=$(df -B1K / | awk "NR==2{print \$4}") paper_memevents=$(grep -E '^(high|max|oom_kill) ' /sys/fs/cgroup/system.slice/usb-crypto-paper.service/memory.events | tr '\n' ' ')"
  python3 - <<'PY'
import json,time
q=json.load(open("/root/usb_runtime/q123_forward/runtime/status.json"))
f=json.load(open("/root/usb_runtime/btc_context_ui/flow/flow_horizons.json"))
print("   q123", q["state"], "lag_ms", q["lag_ms"], "samples", q["counters"]["samples_read"], "store", sum(q["store_counts"].values()), "kline_err", q["kline_error"], "| flow age", round(time.time()-f["written_ms"]/1000), set(v["quality"] for v in f["horizons"].values()))
PY
  [ $n -lt 10 ] && sleep 60
done
echo "== journal since 12:22"
journalctl -k --since "12:22" --no-pager | grep -ci oom | sed 's/^/kernel oom lines: /'
journalctl -u usb-crypto-paper --since "12:22" --no-pager | grep -ciE 'traceback' | sed 's/^/paper tracebacks: /'
journalctl -u usb-btc-context-flow -u usb-btc-context-structure -u usb-crypto-feedlog -u usb-crypto-manual-research --since "12:22" --no-pager | grep -E 'Failed|timeout|Timeout|Killed' | wc -l | sed 's/^/flow|structure|feedlog|recorder failures: /'
journalctl -u usb-crypto-manual-research --since "-3min" --no-pager | grep -E 'Finished|Deactivated|Succeeded' | tail -1 | cut -c1-120
journalctl -u usb-crypto-feedlog --since "-3min" --no-pager | grep -E 'Finished|Deactivated|Succeeded' | tail -1 | cut -c1-120
tail -1 /root/usb_runtime/manual_trade_research/logs/recorder.log | cut -c1-200
tail -2 /root/usb_runtime/q123_forward/store/quality.jsonl | cut -c1-300
