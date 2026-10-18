#!/bin/bash
# A/E common producer: A official via snapshot_0915, E official paper via snapshot_0924, from a first
# paper canary session on. One step per subcommand, each run only after the user approves it. Nothing
# here restarts usb-backend, deletes a book or replaces an A/E operations-tree file.
#
#   stage_release.sh preflight              read-only: targets absent, deployed A code = tested code,
#                                           disk, RVOL store, E V2 official book not yet written
#   stage_release.sh stage <commit>         first install: producer tree (git archive), 3 new EP files,
#                                           units (timers enabled; every unit gated by ACTIVE_FROM)
#   stage_release.sh update <commit>        an installed release: back up, replace the producer tree and
#                                           units, check the EP files, re-run every post-install check
#   stage_release.sh arm <YYYY-MM-DD>       ACTIVE_FROM: from that ET session on the producer and the A
#                                           consumer run and usb-e-paper is skipped (no other change)
#   stage_release.sh hold-e | hold-a        one strategy fails closed from its next run; the other runs
#   stage_release.sh release-e | release-a  lift that hold
#   stage_release.sh disarm                 remove ACTIVE_FROM: usb-e-paper runs again from its next start
#   stage_release.sh verify <YYYY-MM-DD>    read-only audit of that session -> run/<session>/live_gate.json
#   stage_release.sh rollback               disarm, remove the units, the drop-in and the 3 EP files
#                                           (producer tree, snapshots, run records and every book kept)
#
# Run from the repo root on a checkout of the release commit (AGENTS.md 14: clean source only).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
EP=/root/usb_runtime/strategy_e_paper/src
ROOT=/root/usb_runtime/ae_common_producer
PROD=$ROOT/src
UNITS=/etc/systemd/system
STORE=/root/usb_runtime/strategy_e_rvol/kiwoom_premarket.sqlite3
PAPER_DB=/root/usb/data/runtime/usb_paper_trading.sqlite3
E_RUN=/root/usb_runtime/strategy_e_paper/run
E_BOOK=$E_RUN/paper_state_v2/OFFICIAL_KIWOOM_PAPER
PROD_DOCS="docs/backtest/strategy_e_candidate docs/backtest/strategy_e_max"
STAMP=$(date +%Y%m%d_%H%M%S)

# the host is checked by name on every call; target and key can be overridden for another workstation
TARGET=${AE_SSH_TARGET:-root@223.130.151.202}
KEY=${AE_SSH_KEY:-$HOME/.ssh/trader-j.pem}
SSH=(ssh -i "$KEY" -o BatchMode=yes "$TARGET")
remote() { "${SSH[@]}" -n "hostname | grep -qx trader-j || { echo 'not trader-j'; exit 9; }; $1"; }
push() { scp -q -i "$KEY" -o BatchMode=yes "$1" "$TARGET:$2" < /dev/null; }
release() { grep -v '^#' "$HERE/RELEASE_FILES.txt" | awk 'NF>=3' | grep -E "^($1) "; }
target_of() {                                   # tree path [unit-name]
  case $1 in EP) echo "$EP/$2";; UNIT) echo "$UNITS/$3";; esac
}
UNITS_LIST="usb-ae-producer.service usb-ae-producer.timer usb-a-snapshot-consumer.service usb-a-snapshot-consumer.timer"

clean_copies() {                                # the files copied from the working tree equal the commit
  local commit=$1 copied; copied=$(release "EP|UNIT" | awk '{print $2}')
  git -C "$REPO" diff --quiet "$commit" -- $copied || { echo "working tree differs from $commit for a copied file"; exit 2; }
  git -C "$REPO" ls-files --error-unmatch $copied > /dev/null
}

tested_a_code() {
  local bad=0
  while read -r want path; do
    have=$(remote "md5sum $EP/$path | cut -c1-32")
    [ "$want" = "$have" ] && echo "ok   tested A code $path" || { echo "DIFF $path (server $have, tested $want)"; bad=1; }
  done < "$HERE/EP_A_CODE_TESTED.md5"
  return $bad
}

common_checks() {
  local bad=0
  remote "test -f $STORE" || { echo "RVOL store missing"; bad=1; }
  remote "! systemctl is-active --quiet usb-ae-producer.service" || { echo "producer running"; bad=1; }
  free=$(remote "df --output=avail -m / | tail -1 | tr -d ' '")
  [ "$free" -ge 150 ] && echo "ok   disk ${free}MB free" || { echo "DISK ${free}MB free (< 150)"; bad=1; }
  return $bad
}

preflight() {
  local bad=0
  while read -r tree path _ name; do
    remote "test ! -e $(target_of "$tree" "$path" "$name")" || { echo "EXISTS (expected new): $tree $path"; bad=1; }
  done < <(release "EP|UNIT")
  tested_a_code || bad=1
  common_checks || bad=1
  remote "test ! -e $E_BOOK" && echo "ok   E V2 official book not yet written" || { echo "E V2 official book exists"; bad=1; }
  return $bad
}

producer_tree() {                               # <commit>: a fresh tree, then the import + E rules smoke
  remote "mkdir -p $PROD $ROOT/snapshots $ROOT/run $ROOT/a_consumer"
  # E's decision reads its frozen rules from <tree>/docs/backtest: they ship with the code
  git -C "$REPO" archive "$1" backend/app $PROD_DOCS | "${SSH[@]}" "tar -x -C $PROD"
  remote "cd $PROD/backend && PYTHONPATH=$PROD/backend /root/usb/.venv/bin/python -c \
    'from app.strategy_e_v1_1 import decision as D; from app.strategy_e_max import v1; D.load_rules(); v1.load_rules(); import app.ae_common.producer, app.dev.run_ae_common_producer, app.dev.verify_ae_shadow; print(\"producer tree imports and E rules: ok\")'"
}

copy_release() {
  while read -r tree path _ name; do
    dst=$(target_of "$tree" "$path" "$name")
    remote "mkdir -p $(dirname "$dst")"
    push "$REPO/$path" "$dst"
    echo "copied $tree $path -> $dst"
  done < <(release "EP|UNIT")
}

post_install() {                                # <commit>: hashes, EP import, unit syntax
  local commit=$1 bad=0
  while read -r tree path _ name; do
    want=$(git -C "$REPO" show "$commit:$path" | md5sum | cut -c1-32)
    have=$(remote "md5sum $(target_of "$tree" "$path" "$name") | cut -c1-32")
    [ "$want" = "$have" ] && echo "ok   hash $tree $path" || { echo "HASH $tree $path"; bad=1; }
  done < <(release "EP|UNIT")
  remote "cd $EP/backend && PYTHONPATH=. /root/usb/.venv/bin/python -c 'import app.ae_common.a_consumer; print(\"EP consumer import: ok\")'"
  remote "systemctl daemon-reload && systemd-analyze verify $UNITS_LIST 2>&1 | grep -v IPv6 || true"
  echo "$commit" | "${SSH[@]}" "cat > $ROOT/RELEASE_COMMIT"
  return $bad
}

stage() {
  local commit=${1:?release commit}
  clean_copies "$commit"
  preflight
  producer_tree "$commit"
  copy_release
  remote "systemctl daemon-reload && systemctl enable usb-ae-producer.timer usb-a-snapshot-consumer.timer"
  post_install "$commit"
  echo "staged $commit; not armed (no ACTIVE_FROM): usb-e-paper is unchanged"
}

update() {
  local commit=${1:?release commit}
  clean_copies "$commit"
  tested_a_code
  common_checks
  local backup=$ROOT/backups/$STAMP
  remote "mkdir -p $backup/units && mv $PROD $backup/src && for u in $UNITS_LIST; do cp -p $UNITS/\$u $backup/units/ 2>/dev/null || true; done; \
    cp -p $UNITS/usb-e-paper.service.d/ae-shadow-skip.conf $backup/units/ 2>/dev/null || true; \
    cp -p $ROOT/RELEASE_COMMIT $backup/ 2>/dev/null || true"
  echo "backup: $backup"
  producer_tree "$commit"
  copy_release
  post_install "$commit"
  echo "updated to $commit (previous tree and units in $backup)"
}

arm() {
  local first=${1:?YYYY-MM-DD}
  [[ $first =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "first session must be YYYY-MM-DD"; exit 2; }
  remote "test -f $PROD/backend/app/dev/run_ae_common_producer.py && test -f $EP/backend/app/ae_common/a_consumer.py"
  remote "grep -q -- '--paper' $UNITS/usb-ae-producer.service" || { echo "producer unit has no --paper"; exit 2; }
  echo "$first" | "${SSH[@]}" "cat > $ROOT/ACTIVE_FROM && rm -f $ROOT/ACTIVE_SESSION"
  # stage only enables the timers (active from the next boot); arm starts them
  remote "systemctl daemon-reload; systemctl start usb-ae-producer.timer usb-a-snapshot-consumer.timer; systemctl list-timers --all --no-pager | grep -E 'usb-e-paper|usb-ae-producer|usb-a-snapshot'"
  echo "armed from $first: producer + A consumer run on and after it, usb-e-paper is skipped"
}

hold() { remote "touch $ROOT/HOLD_$1"; echo "HOLD_$1 set: strategy $1 fails closed from its next run"; }
unhold() { remote "rm -f $ROOT/HOLD_$1"; echo "HOLD_$1 removed"; }

disarm() {
  remote "rm -f $ROOT/ACTIVE_FROM $ROOT/ACTIVE_SESSION; systemctl stop usb-ae-producer.timer usb-a-snapshot-consumer.timer"
  echo "disarmed: usb-e-paper runs from its next start"
}

verify() {
  local session=${1:?YYYY-MM-DD}
  remote "cd $PROD/backend && PYTHONPATH=$PROD/backend ionice -c3 nice -n19 /root/usb/.venv/bin/python -m app.dev.verify_ae_shadow \
    --session $session --producer-out $ROOT/run --consumer-out $ROOT/a_consumer --snapshots $ROOT/snapshots \
    --database $PAPER_DB --e-book $E_BOOK"
}

rollback() {
  disarm
  remote "systemctl disable --now usb-ae-producer.timer usb-a-snapshot-consumer.timer || true"
  while read -r tree path _ name; do
    remote "rm -f $(target_of "$tree" "$path" "$name")"
    echo "removed $tree $path"
  done < <(release "EP|UNIT")
  remote "rm -rf $EP/backend/app/ae_common/__pycache__; rmdir $EP/backend/app/ae_common 2>/dev/null || true; systemctl daemon-reload"
  echo "rolled back; $ROOT (trees, snapshots, run records) and $E_BOOK kept"
}

case ${1:-} in
  preflight) preflight ;;
  stage) stage "${2:-}" ;;
  update) update "${2:-}" ;;
  arm) arm "${2:-}" ;;
  hold-e) hold E ;;
  hold-a) hold A ;;
  release-e) unhold E ;;
  release-a) unhold A ;;
  disarm) disarm ;;
  verify) verify "${2:-}" ;;
  rollback) rollback ;;
  *) sed -n '2,21p' "$0"; exit 1 ;;
esac
