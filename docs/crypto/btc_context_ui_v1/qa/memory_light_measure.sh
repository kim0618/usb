#!/usr/bin/env bash
# Memory-light architecture measurement: worker rebuild, API cold/first/30min, worker steps each minute.
set -u
S=$1; MIN=${2:-30}; cd ~/usb/backend
PY=~/usb/.venv/bin/python; export PYTHONPATH=$HOME/usb/backend PYTHONDONTWRITEBYTECODE=1
tv() { /usr/bin/time -f '{"max_rss_kb":%M,"elapsed_s":%e,"user_s":%U,"sys_s":%S}' -o "$1" "${@:2}"; }
tv $S/rebuild.time $PY -m app.crypto.btc_context_ui_v1.structure_worker --state-dir $S/state > $S/rebuild.out 2>&1
echo "rebuild $(cat $S/rebuild.out) $(cat $S/rebuild.time) ckpt_bytes=$(stat -c %s $S/state/structure_engine.pickle) snap_bytes=$(stat -c %s $S/state/structure_snapshot.json)" > $S/log.txt
$PY -m app.crypto.btc_context_ui_v1 --root $S/ctxroot --structure-snapshot $S/state/structure_snapshot.json --port 18014 > $S/api.log 2>&1 &
API=$!; echo $API > $S/api.pid
sleep 4
rss() { awk '/VmRSS|VmHWM/{printf "%s=%s ", $1, $2}' /proc/$API/status; }
echo "api_cold $(rss)" >> $S/log.txt
curl -s localhost:18014/snapshot > $S/first.json; sleep 3; curl -s localhost:18014/snapshot > $S/first2.json
echo "api_after_first_request $(rss)" >> $S/log.txt
end=$(( $(date +%s) + MIN*60 )); last_min=-1; i=0
while [ $(date +%s) -lt $end ]; do
  curl -s -o /dev/null localhost:18014/snapshot
  m=$(( $(date +%s) / 60 ))
  if [ $m -ne $last_min ] && [ $(( $(date +%s) % 60 )) -ge 3 ]; then
    last_min=$m; i=$((i+1))
    tv $S/step.time $PY -m app.crypto.btc_context_ui_v1.structure_worker --state-dir $S/state > $S/step.out 2>&1
    echo "min=$i api $(rss) worker $(cat $S/step.out) $(cat $S/step.time)" >> $S/log.txt
  fi
  sleep 5
done
curl -s localhost:18014/snapshot > $S/last.json
echo "api_end $(rss)" >> $S/log.txt
kill $API
