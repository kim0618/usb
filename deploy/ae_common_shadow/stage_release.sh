#!/bin/bash
# A/E common producer, one-session shadow cutover. One step per subcommand, each run only after the
# user approves it. Nothing here restarts usb-backend, touches a paper book or replaces a file.
#
#   stage_release.sh preflight              read-only: targets absent, deployed A code = tested code,
#                                           disk, RVOL store, e-paper drop-ins, no producer running
#   stage_release.sh stage <commit>         producer tree from `git archive <commit>`, 3 new EP files,
#                                           units installed (timers enabled, every unit gated by
#                                           ACTIVE_SESSION, so nothing runs until `arm`)
#   stage_release.sh arm <YYYY-MM-DD>       that ET session only: e-paper skips, producer + consumer run
#   stage_release.sh disarm                 remove ACTIVE_SESSION: e-paper runs again from the next start
#   stage_release.sh verify <YYYY-MM-DD>    read-only live gate -> run/<session>/live_gate.json
#   stage_release.sh rollback               disarm, remove the units, the drop-in and the 3 EP files
#
# Run from the repo root on a clean checkout of the release commit (AGENTS.md 14: clean source only).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
EP=/root/usb_runtime/strategy_e_paper/src
ROOT=/root/usb_runtime/ae_common_producer
PROD=$ROOT/src
UNITS=/etc/systemd/system
STORE=/root/usb_runtime/strategy_e_rvol/kiwoom_premarket.sqlite3
PAPER_DB=/root/usb/data/runtime/usb_paper_trading.sqlite3
PROD_DOCS="docs/backtest/strategy_e_candidate docs/backtest/strategy_e_max"

remote() { ssh -n traderj "hostname | grep -qx trader-j || { echo 'not trader-j'; exit 9; }; $1"; }
release() { grep -v '^#' "$HERE/RELEASE_FILES.txt" | awk 'NF>=3' | grep -E "^($1) "; }
target_of() {                                   # tree path [unit-name]
  case $1 in EP) echo "$EP/$2";; UNIT) echo "$UNITS/$3";; esac
}

preflight() {
  local bad=0
  while read -r tree path _ name; do
    remote "test ! -e $(target_of "$tree" "$path" "$name")" || { echo "EXISTS (expected new): $tree $path"; bad=1; }
  done < <(release "EP|UNIT")
  while read -r want path; do
    have=$(remote "md5sum $EP/$path | cut -c1-32")
    [ "$want" = "$have" ] && echo "ok   tested A code $path" || { echo "DIFF $path (server $have, tested $want)"; bad=1; }
  done < "$HERE/EP_A_CODE_TESTED.md5"
  remote "test -f $STORE" || { echo "RVOL store missing"; bad=1; }
  remote "test -f $UNITS/usb-e-paper.service.d/r1-paper-off.conf" || { echo "E paper is not OFF (r1-paper-off.conf)"; bad=1; }
  remote "! systemctl is-active --quiet usb-ae-producer.service" || { echo "producer running"; bad=1; }
  free=$(remote "df --output=avail -m / | tail -1 | tr -d ' '")
  [ "$free" -ge 150 ] && echo "ok   disk ${free}MB free" || { echo "DISK ${free}MB free (< 150)"; bad=1; }
  return $bad
}

stage() {
  local commit=${1:?release commit}
  git -C "$REPO" diff --quiet "$commit" -- backend/app deploy/ae_common_shadow $PROD_DOCS || { echo "working tree differs from $commit"; exit 2; }
  preflight
  remote "mkdir -p $PROD $ROOT/snapshots $ROOT/run $ROOT/a_consumer"
  # E's decision reads its frozen rules from <tree>/docs/backtest (strategy_e_v1_1.decision.load_rules,
  # strategy_e_max.v1.load_rules): without them the shadow decision fails, so they ship with the code
  git -C "$REPO" archive "$commit" backend/app $PROD_DOCS | ssh traderj "tar -x -C $PROD"
  remote "cd $PROD/backend && PYTHONPATH=$PROD/backend /root/usb/.venv/bin/python -c \
    'from app.strategy_e_v1_1 import decision as D; from app.strategy_e_max import v1; D.load_rules(); v1.load_rules(); import app.ae_common.producer, app.dev.run_ae_common_producer; print(\"producer tree imports and E rules: ok\")'"
  while read -r tree path _ name; do
    dst=$(target_of "$tree" "$path" "$name")
    remote "mkdir -p $(dirname "$dst")"
    scp -q "$REPO/$path" "traderj:$dst" < /dev/null
    echo "copied $tree $path -> $dst"
  done < <(release "EP|UNIT")
  remote "systemctl daemon-reload && systemctl enable usb-ae-producer.timer usb-a-snapshot-consumer.timer"
  echo "$commit" | ssh traderj "cat > $ROOT/RELEASE_COMMIT"
  echo "staged $commit; not armed (no ACTIVE_SESSION): usb-e-paper is unchanged"
}

arm() {
  local session=${1:?YYYY-MM-DD}
  remote "test -f $PROD/backend/app/dev/run_ae_common_producer.py && test -f $EP/backend/app/ae_common/a_consumer.py"
  echo "$session" | ssh traderj "cat > $ROOT/ACTIVE_SESSION"
  remote "systemctl daemon-reload; systemctl list-timers --all --no-pager | grep -E 'usb-e-paper|usb-ae-producer|usb-a-snapshot'"
  echo "armed for $session: usb-e-paper skips that morning only"
}

disarm() { remote "rm -f $ROOT/ACTIVE_SESSION"; echo "disarmed: usb-e-paper runs from its next start"; }

verify() {
  local session=${1:?YYYY-MM-DD}
  remote "cd $PROD/backend && PYTHONPATH=$PROD/backend ionice -c3 nice -n19 /root/usb/.venv/bin/python -m app.dev.verify_ae_shadow \
    --session $session --producer-out $ROOT/run --consumer-out $ROOT/a_consumer --snapshots $ROOT/snapshots \
    --database $PAPER_DB"
}

rollback() {
  disarm
  remote "systemctl disable --now usb-ae-producer.timer usb-a-snapshot-consumer.timer || true"
  while read -r tree path _ name; do
    remote "rm -f $(target_of "$tree" "$path" "$name")"
    echo "removed $tree $path"
  done < <(release "EP|UNIT")
  remote "rm -rf $EP/backend/app/ae_common/__pycache__; rmdir $EP/backend/app/ae_common 2>/dev/null || true; systemctl daemon-reload"
  echo "rolled back; $ROOT (tree, snapshots, run records) kept as evidence"
}

case ${1:-} in
  preflight) preflight ;;
  stage) stage "${2:-}" ;;
  arm) arm "${2:-}" ;;
  disarm) disarm ;;
  verify) verify "${2:-}" ;;
  rollback) rollback ;;
  *) sed -n '2,16p' "$0"; exit 1 ;;
esac
