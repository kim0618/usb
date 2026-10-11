#!/usr/bin/env bash
# First 24 hours of the Context Collector, Q1/Q2/Q3 counts: events per day, nothing else.
#
#   deploy/crypto_q123_forward/first_24h_summary.sh <work_dir>
#
# Refuses to run before the collector's first 24 hours are over (first production sample
# 2026-10-10T12:47:23Z = 21:47 KST, so not before 2026-10-11 21:47:23 KST).
#
# Read only towards the server: one `rsync` FROM the journal into <work_dir>/journal (no
# --delete, nothing is written on the host), public klines, then the recorder replays the copy
# into a fresh local store and prints the counts. No outcome statistic, no direction.
set -euo pipefail
work=${1:?work_dir}
repo=$(git rev-parse --show-toplevel)
py=${PY:-$HOME/usb/.venv/bin/python}
first_ms=1791636443099                       # first production sample (status / journal)
end_ms=$((first_ms + 86400000))
now_ms=$(($(date +%s) * 1000))
if [ "$now_ms" -lt "$end_ms" ]; then
  echo "REFUSED: the first 24 hours end at $(date -d @$((end_ms / 1000)) '+%F %T %Z'); now $(date '+%F %T %Z')" >&2
  exit 3
fi
mkdir -p "$work"
rsync -a traderj:/root/usb_runtime/crypto_context/journal/ "$work/journal/"
cd "$repo/backend"
klines=$(PYTHONPATH=. "$py" -m app.crypto.research.q123_forward_v1 fetch --out "$work/klines" \
  --end-ms "$now_ms")
rm -rf "$work/store" "$work/runtime"
PYTHONPATH=. "$py" -m app.crypto.research.q123_forward_v1.recorder record \
  --root "$work/journal" --store "$work/store" --runtime "$work/runtime" \
  --klines-file "$klines" --until-idle
PYTHONPATH=. "$py" -m app.crypto.research.q123_forward_v1.recorder summary24h \
  --store "$work/store" --runtime "$work/runtime" --from-ms "$first_ms" --to-ms "$end_ms" \
  | tee "$work/summary24h.json"
