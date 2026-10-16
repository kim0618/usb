#!/bin/bash
# usb-h-evaluation-guard (P2.1 activation, 2026-10-16): runs beside usb-h-evaluation, outside its cgroup,
# and stops only that unit when the 1 GB host is in trouble for 30 s in a row: crypto terminal or backend
# health > 5 s, memory PSI full avg10 > 50 %, MemAvailable < 100 MB, or swap traffic > 10 MB per 5 s;
# immediately on a new OOM kill; and at 03:35 ET (E paper starts 03:45 ET). It never stops anything else.
# Every 5 s sample goes to auto/guard/<start>.csv (last 30 kept); the summary goes to the journal.
set -u
U=usb-h-evaluation.service; CG=/sys/fs/cgroup/system.slice/$U
DIR=/root/usb/data/runtime/strategy_h_v2/d7/auto/guard; mkdir -p $DIR
OUT=$DIR/$(date -u +%Y%m%dT%H%M%SZ).csv
log(){ echo "h-guard: $*"; }
active(){ s=$(systemctl is-active $U); [ "$s" = active ] || [ "$s" = activating ]; }
for i in $(seq 12); do active && break; sleep 5; done
active || { log "unit not running, nothing to guard"; exit 0; }
echo "ts,mem_avail_kb,swap_free_kb,pswpin,pswpout,psi_mem_full10,psi_io_full10,crypto_s,backend_s,h_cgroup_bytes,h_rss_kb,bad_s" > $OUT
v(){ awk -v k="$1" '$1==k{print $2}' "$2"; }
t0=$SECONDS; bad_since=-1; stopped=""; oom0=$(v oom_kill /proc/vmstat)
si0=$(v pswpin /proc/vmstat); so0=$(v pswpout /proc/vmstat); psi=$(v pswpin /proc/vmstat); pso=$(v pswpout /proc/vmstat)
ma_min=99999999; pm_max=0; pi_max=0; c_max=0; b_max=0; cg_max=0; n=0; c1=0; c5=0; ct=0
declare -A seen
while active; do
  ma=$(v MemAvailable: /proc/meminfo); sf=$(v SwapFree: /proc/meminfo)
  si=$(v pswpin /proc/vmstat); so=$(v pswpout /proc/vmstat); oom=$(v oom_kill /proc/vmstat)
  pm=$(awk -F'[= ]' '/^full/{print $3}' /proc/pressure/memory); pi=$(awk -F'[= ]' '/^full/{print $3}' /proc/pressure/io)
  c=$(curl -s -m 10 -o /dev/null -w "%{time_total}" http://127.0.0.1:8100/health || echo 10)
  b=$(curl -s -m 10 -o /dev/null -w "%{time_total}" http://127.0.0.1:8000/health || echo 10)
  cg=$(cat $CG/memory.current 2>/dev/null || echo 0); rss=0
  for p in $(cat $CG/cgroup.procs 2>/dev/null); do
    grep -q python /proc/$p/comm 2>/dev/null || continue
    rss=$(awk '/VmRSS/{print $2}' /proc/$p/status 2>/dev/null)
    if [ -z "${seen[$p]:-}" ] && [ "$(grep -c pandas /proc/$p/maps 2>/dev/null)" -gt 0 ]; then
      seen[$p]=1; log "pid $p $(tr '\0' ' ' < /proc/$p/cmdline | cut -c30-110) pyarrow_maps=$(grep -c pyarrow /proc/$p/maps 2>/dev/null)"
    fi
  done
  swap_pages=$(( si - psi + so - pso )); psi=$si; pso=$so
  reason=""
  awk "BEGIN{exit !($c>5)}" && reason="$reason crypto=${c}s"
  awk "BEGIN{exit !($b>5)}" && reason="$reason backend=${b}s"
  awk "BEGIN{exit !($pm>50)}" && reason="$reason mem_psi_full=$pm"
  [ "$ma" -lt 102400 ] && reason="$reason mem_avail=${ma}kB"
  [ "$swap_pages" -gt 2560 ] && reason="$reason swap_pages=$swap_pages"
  if [ -n "$reason" ]; then [ $bad_since -lt 0 ] && bad_since=$SECONDS; else bad_since=-1; fi
  bad=$([ $bad_since -ge 0 ] && echo $((SECONDS-bad_since)) || echo 0)
  echo "$(date -u +%FT%TZ),$ma,$sf,$si,$so,$pm,$pi,$c,$b,$cg,$rss,$bad" >> $OUT
  n=$((n+1)); [ "$ma" -lt "$ma_min" ] && ma_min=$ma; [ "$cg" -gt "$cg_max" ] && cg_max=$cg
  awk "BEGIN{exit !($pm>$pm_max)}" && pm_max=$pm; awk "BEGIN{exit !($pi>$pi_max)}" && pi_max=$pi
  awk "BEGIN{exit !($c>$c_max)}" && c_max=$c; awk "BEGIN{exit !($b>$b_max)}" && b_max=$b
  awk "BEGIN{exit !($c>1)}" && c1=$((c1+1)); awk "BEGIN{exit !($c>5)}" && c5=$((c5+1)); awk "BEGIN{exit !($c>=10)}" && ct=$((ct+1))
  if [ -z "$stopped" ]; then
    if [ "$oom" -gt "$oom0" ]; then stopped="oom_kill $oom0->$oom"
    elif [ "$bad" -ge 30 ]; then stopped="sustained:$reason"
    elif [ "$(TZ=America/New_York date +%H%M)" -ge 0335 ] && [ "$(TZ=America/New_York date +%H%M)" -lt 1200 ]; then stopped="deadline 03:35 ET"
    fi
    [ -n "$stopped" ] && { log "STOP usb-h-evaluation: $stopped"; systemctl stop --no-block $U; }
  fi
  sleep 5
done
log "summary secs=$((SECONDS-t0)) samples=$n stopped=${stopped:-no} mem_avail_min_kb=$ma_min swap_free_delta_kb=$(( $(v SwapFree: /proc/meminfo) - $(head -2 $OUT | tail -1 | cut -d, -f3) )) swap_in_out_pages=$(( $(v pswpin /proc/vmstat)-si0 ))/$(( $(v pswpout /proc/vmstat)-so0 )) mem_psi_full_max=$pm_max io_psi_full_max=$pi_max crypto_max_s=$c_max crypto_gt1=$c1 crypto_gt5=$c5 crypto_timeouts=$ct backend_max_s=$b_max h_cgroup_max_bytes=$cg_max csv=$OUT"
ls -1t $DIR/*.csv 2>/dev/null | tail -n +31 | xargs -r rm -f
