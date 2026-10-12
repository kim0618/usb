#!/bin/bash
# one-shot reads of the production collector latest file, every 60s, 24 times (~24 min). write 0.
cd "$(dirname "$0")"
for i in $(seq 1 24); do
  ts=$(date +%s)
  timeout 40 ssh traderj 'echo T=$(date -u +%s%3N); cat /run/usb-crypto-context/ctx-state-*/context_latest.json' > "series/snap_${ts}.json.raw" 2>/dev/null
  sleep 60
done
echo DONE > series/DONE
