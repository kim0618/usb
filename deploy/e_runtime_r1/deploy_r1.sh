#!/bin/bash
# E-RUNTIME-R1 deployment, one step per subcommand, each run only after the user approves it.
#
#   deploy_r1.sh preflight            read-only: server files still equal the pre-R1 git base
#   deploy_r1.sh rvol                 RVOL collector code + store backup + replay of the staged repairs
#   deploy_r1.sh runtime              E worker (no A, V2, gate) + A's own worker + API; E stays decision-only
#   deploy_r1.sh shadow               next session runs the V2 paper into SHADOW_VALIDATION
#   deploy_r1.sh verify <session>     independent recomputation of a shadow session (must PASS)
#   deploy_r1.sh official <session>   V2 official book from the next session (needs a PASS report)
#   deploy_r1.sh rollback             restore every replaced file and the pre-R1 units
#
# Run from the repo root (~/usb) on the main branch with the R1 commit checked out.
set -euo pipefail
BASE=978714d                                   # git HEAD before the R1 commit
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/usb_runtime/backups/e_runtime_r1
EP=/root/usb_runtime/strategy_e_paper/src
RV=/root/usb_runtime/strategy_e_rvol/src
USB=/root/usb
RUN_ROOT=/root/usb_runtime/strategy_e_paper/run
STORE=/root/usb_runtime/strategy_e_rvol/kiwoom_premarket.sqlite3
STAGED_STORE=/root/usb_runtime/strategy_e_rvol_repair_r1/store_staging.sqlite3
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)

# -n: never read the caller's stdin (the release loops feed it); remote_stdin pipes on purpose
remote() { ssh -n traderj "hostname | grep -qx trader-j || { echo 'not trader-j'; exit 9; }; $1"; }
remote_stdin() { ssh traderj "hostname | grep -qx trader-j || { echo 'not trader-j'; exit 9; }; $1"; }
tree_of() { case $1 in EP) echo $EP;; RV) echo $RV;; USB) echo $USB;; esac; }

release() { grep -v '^#' "$HERE/RELEASE_FILES.txt" | awk 'NF>=2 {print $1, $2, ($3=="NEW")?"NEW":"OLD"}' | grep -E "^($1) "; }

preflight() {
  local trees=${1:-"EP|RV|USB"} bad=0
  while read -r tree path kind; do
    local dst; dst=$(tree_of "$tree")/$path
    if [ "$kind" = NEW ]; then
      remote "test ! -e $dst" || { echo "EXISTS (expected new): $tree $path"; bad=1; }
      continue
    fi
    local want have
    want=$(cd "$REPO" && git show "$BASE:$path" | md5sum | cut -c1-32)
    have=$(remote "md5sum $dst | cut -c1-32")
    [ "$want" = "$have" ] && echo "ok   $tree $path" || { echo "DIFF $tree $path (server $have, base $want)"; bad=1; }
  done < <(release "$trees")
  return $bad
}

push_files() {                                  # backup then copy; <trees regex>
  remote "mkdir -p $BACKUP/$STAMP"
  while read -r tree path kind; do
    local dst; dst=$(tree_of "$tree")/$path
    if [ "$kind" = OLD ]; then remote "mkdir -p $BACKUP/$STAMP/$tree/$(dirname "$path") && cp -p $dst $BACKUP/$STAMP/$tree/$path"; fi
    remote "mkdir -p $(dirname "$dst")"
    scp -q "$REPO/$path" "traderj:$dst" < /dev/null
    echo "copied $tree $path"
  done < <(release "$1")
  echo "$BACKUP/$STAMP" | remote_stdin "cat >> $BACKUP/STAMPS"
}

mode() {                                        # <mode> [extra json]
  remote "printf '%s\n' '{\"mode\": \"$1\", \"since\": \"$(date -u +%FT%TZ)\"${2:+, $2}}' > $RUN_ROOT/runtime_mode.json && cat $RUN_ROOT/runtime_mode.json"
}

case ${1:-} in
  preflight) preflight ;;
  rvol)
    preflight "RV"
    remote "while systemctl is-active --quiet usb-e-rvol-once.service; do echo 'waiting for usb-e-rvol-once'; sleep 30; done
            mkdir -p $BACKUP/$STAMP && /root/usb/.venv/bin/python -c \"import sqlite3; s=sqlite3.connect('file:$STORE?mode=ro', uri=True); d=sqlite3.connect('$BACKUP/$STAMP/kiwoom_premarket.sqlite3'); s.backup(d); d.close()\" && echo store backed up"
    push_files "RV"
    remote "cd $RV/backend && PYTHONPATH=\$PWD /root/usb/.venv/bin/python -m app.dev.run_e_rt3_rvol --store $STORE --universe none repair-replay --source $STAGED_STORE
            cd $RV/backend && PYTHONPATH=\$PWD /root/usb/.venv/bin/python -m app.dev.run_e_rt3_rvol --store $STORE --universe none repair-replay --source $STAGED_STORE --apply
            cd $RV/backend && PYTHONPATH=\$PWD /root/usb/.venv/bin/python -m app.dev.run_e_rt3_rvol --store $STORE --universe none --out $BACKUP/$STAMP/E_RVOL_CORRUPTED_ROWS_AFTER_REPAIR.json inventory | head -8"
    ;;
  runtime)
    preflight "EP|USB"
    push_files "EP|USB"
    remote "cd $EP/backend && PYTHONPATH=\$PWD /root/usb/.venv/bin/python -c 'import app.dev.run_e_rt2_dryrun, app.dev.run_a_mover_live, app.dev.verify_e_runtime_session' && echo EP imports ok
            cd $USB/backend && PYTHONPATH=\$PWD /root/usb/.venv/bin/python -c 'import app.api.strategies' && echo USB imports ok"
    scp -q "$HERE/usb-a-mover-live.service" "$HERE/usb-a-mover-live.timer" traderj:/etc/systemd/system/
    remote "mkdir -p /root/usb_runtime/strategy_a_mover_live/run && systemctl daemon-reload && systemctl enable --now usb-a-mover-live.timer && systemctl list-timers usb-a-mover-live.timer usb-e-paper.timer --no-pager"
    mode RUNTIME_PAUSED_FOR_REPAIR
    remote "systemctl restart usb-backend && for i in \$(seq 1 30); do curl -sf 127.0.0.1:8000/health >/dev/null && break; sleep 3; done; curl -s 127.0.0.1:8000/api/v1/strategies/STRATEGY_E_MAX_V1/status | head -c 300; echo"
    ;;
  shadow)
    scp -q "$HERE/usb-e-paper.r1-shadow.conf" traderj:/etc/systemd/system/usb-e-paper.service.d/r1-shadow.conf
    remote "mkdir -p $BACKUP/units && mv /etc/systemd/system/usb-e-paper.service.d/r1-paper-off.conf $BACKUP/units/ 2>/dev/null || true; systemctl daemon-reload; systemctl cat usb-e-paper.service | grep -E '^ExecStart|r1-'"
    mode RUNTIME_SHADOW_VALIDATION
    ;;
  verify)
    S=${2:?session}
    remote "cd $EP/backend && PYTHONPATH=\$PWD /root/usb/.venv/bin/python -m app.dev.verify_e_runtime_session --run-dir $RUN_ROOT/$S --universe /root/usb_runtime/strategy_e_paper/universe --store $STORE --book-root $RUN_ROOT/paper_state_v2/SHADOW_VALIDATION --out $RUN_ROOT/$S/E_RUNTIME_R1_SHADOW_VERIFY.json"
    ;;
  official)
    S=${2:?shadow session that passed}
    remote "python3 -c \"import json,sys; b=json.load(open('$RUN_ROOT/$S/E_RUNTIME_R1_SHADOW_VERIFY.json')); sys.exit(0 if b['verdict']=='E-RUNTIME-R1 SHADOW PASS' else 1)\"" \
      || { echo "no SHADOW PASS report for $S: refusing"; exit 1; }
    scp -q "$HERE/usb-e-paper.r1-official.conf" traderj:/etc/systemd/system/usb-e-paper.service.d/r1-official.conf
    remote "mv /etc/systemd/system/usb-e-paper.service.d/r1-shadow.conf $BACKUP/units/ && systemctl daemon-reload && systemctl cat usb-e-paper.service | grep -E '^ExecStart|r1-'"
    mode OFFICIAL_PAPER_RUNNING "\"shadow_pass_session\": \"$S\""
    ;;
  rollback)
    LAST=$(remote "tail -1 $BACKUP/STAMPS")
    echo "restoring from $LAST"
    remote "cd $LAST && for tree in EP RV USB; do [ -d \$tree ] || continue; case \$tree in EP) d=$EP;; RV) d=$RV;; USB) d=$USB;; esac; (cd \$tree && find . -type f | while read f; do cp -p \"\$f\" \"\$d/\$f\"; echo restored \$tree \$f; done); done"
    remote "systemctl disable --now usb-a-mover-live.timer 2>/dev/null || true; rm -f /etc/systemd/system/usb-a-mover-live.*; rm -f /etc/systemd/system/usb-e-paper.service.d/r1-shadow.conf /etc/systemd/system/usb-e-paper.service.d/r1-official.conf; [ -f $BACKUP/units/r1-paper-off.conf ] && cp -p $BACKUP/units/r1-paper-off.conf /etc/systemd/system/usb-e-paper.service.d/; rm -f $RUN_ROOT/runtime_mode.json; systemctl daemon-reload; systemctl restart usb-backend"
    echo "NOTE: RVOL store repairs are kept (each is logged in kiwoom_repair_log); the pre-repair store copy is in the rvol step's backup dir."
    echo "NOTE: new files (lane_share, run_a_mover_live, verify, labels json) are left in place and unused;"
    echo "      the pre-R1 E worker re-attaches A when it runs. The r1-paper-off drop-in decides --paper."
    ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
