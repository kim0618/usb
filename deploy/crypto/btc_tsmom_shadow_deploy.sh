#!/usr/bin/env bash
# BTC TSMOM 앙상블 섀도우 운영 설치. I paper 배포(docs/operations/i_paper_deploy_prep_v2) 방식과 같다:
#   release tgz(git archive <commit>) → /root/usb_runtime/releases, 백업·증거 /root/usb_runtime/btc_tsmom_deploy_<ts>/,
#   코드 3파일을 /root/usb/backend 에 추가(기존 파일 덮어쓰기 없음), unit 2개 설치(헤더에 commit 기록), timer enable, 1회 수동 실행.
# 하지 않는 것: 주문, 인증키, 기존 service restart, git pull, 삭제.
# 되돌리기: ssh traderj 'systemctl disable --now usb-btc-tsmom-shadow.timer; rm -f /etc/systemd/system/usb-btc-tsmom-shadow.{service,timer}; systemctl daemon-reload;
#            rm -rf /root/usb/backend/app/crypto/tsmom /root/usb/backend/app/dev/run_btc_tsmom_shadow.py'   (store는 남김)
set -euo pipefail
cd "$(dirname "$0")/../.."
COMMIT="${1:?usage: $0 <commit>}"
TS="$(date +%Y%m%d-%H%M%S)"
REL="btc-tsmom-shadow-${COMMIT}.tgz"
FILES="backend/app/crypto/tsmom/__init__.py backend/app/crypto/tsmom/signal.py backend/app/dev/run_btc_tsmom_shadow.py"
UNITS="docs/operations/btc_tsmom_shadow/systemd"

# 1. 로컬: release tgz (커밋 기준, 작업트리 아님) + 매니페스트
TMP="$(mktemp -d)"
git archive "$COMMIT" $FILES | tar -x -C "$TMP"
( cd "$TMP" && find . -type f ! -name MANIFEST.sha256 | sort | xargs sha256sum > MANIFEST.sha256 && tar -czf "/tmp/$REL" . )
REL_SHA="$(sha256sum "/tmp/$REL" | cut -c1-64)"
echo "release $REL sha256 $REL_SHA"
sed "s|^# DRAFT - not installed.|# Installed $TS from $COMMIT $UNITS.|" "$UNITS/usb-btc-tsmom-shadow.service" > "$TMP/usb-btc-tsmom-shadow.service"
sed "s|^# DRAFT - not installed.|# Installed $TS from $COMMIT $UNITS.|" "$UNITS/usb-btc-tsmom-shadow.timer" > "$TMP/usb-btc-tsmom-shadow.timer"

# 2. 서버: 사전 상태 + 충돌 확인(파일이 이미 있으면 중단)
ssh traderj "set -e
mkdir -p /root/usb_runtime/releases /root/usb_runtime/btc_tsmom_deploy_$TS /root/usb_runtime/btc_tsmom_shadow/store
for f in $FILES; do [ -e /root/usb/\$f ] && { echo \"ABORT: /root/usb/\$f already exists\"; exit 2; }; done
for u in usb-btc-tsmom-shadow.service usb-btc-tsmom-shadow.timer; do [ -e /etc/systemd/system/\$u ] && { echo \"ABORT: \$u already installed\"; exit 2; }; done
{ date; free -m; systemctl list-timers --no-pager | grep -E 'usb-'; ps -eo pid,comm,args | grep -E 'uvicorn|next-server' | grep -v grep; } > /root/usb_runtime/btc_tsmom_deploy_$TS/pre.txt
echo pre-check ok"

# 3. 업로드 + 설치
scp -q "/tmp/$REL" "traderj:/root/usb_runtime/releases/$REL"
scp -q "$TMP/usb-btc-tsmom-shadow.service" "$TMP/usb-btc-tsmom-shadow.timer" "traderj:/root/usb_runtime/btc_tsmom_deploy_$TS/"
ssh traderj "set -e
cd /root/usb_runtime/releases
echo \"$REL_SHA  $REL\" | sha256sum -c -
mkdir -p /root/usb_runtime/btc_tsmom_deploy_$TS/extract && tar -xzf $REL -C /root/usb_runtime/btc_tsmom_deploy_$TS/extract
( cd /root/usb_runtime/btc_tsmom_deploy_$TS/extract && sha256sum -c MANIFEST.sha256 )
cp -a /root/usb_runtime/btc_tsmom_deploy_$TS/extract/backend/app/crypto/tsmom /root/usb/backend/app/crypto/tsmom
cp -a /root/usb_runtime/btc_tsmom_deploy_$TS/extract/backend/app/dev/run_btc_tsmom_shadow.py /root/usb/backend/app/dev/
chown -R root:root /root/usb/backend/app/crypto/tsmom /root/usb/backend/app/dev/run_btc_tsmom_shadow.py
cp /root/usb_runtime/btc_tsmom_deploy_$TS/usb-btc-tsmom-shadow.service /root/usb_runtime/btc_tsmom_deploy_$TS/usb-btc-tsmom-shadow.timer /etc/systemd/system/
( cd /root/usb && sha256sum $FILES /etc/systemd/system/usb-btc-tsmom-shadow.service /etc/systemd/system/usb-btc-tsmom-shadow.timer ) > /root/usb_runtime/btc_tsmom_deploy_$TS/SHA256SUMS
systemctl daemon-reload
systemctl enable --now usb-btc-tsmom-shadow.timer
echo '== first run (manual)'
systemctl start usb-btc-tsmom-shadow.service
journalctl -u usb-btc-tsmom-shadow.service --no-pager -n 2 -o cat
echo '== timer'
systemctl list-timers --no-pager | grep tsmom
echo '== store'
wc -l /root/usb_runtime/btc_tsmom_shadow/store/ledger.jsonl
echo '== post'
{ date; free -m; ps -eo pid,comm,args | grep -E 'uvicorn|next-server' | grep -v grep; } > /root/usb_runtime/btc_tsmom_deploy_$TS/post.txt
diff <(grep -E 'uvicorn|next' /root/usb_runtime/btc_tsmom_deploy_$TS/pre.txt | awk '{print \$1,\$2}') <(grep -E 'uvicorn|next' /root/usb_runtime/btc_tsmom_deploy_$TS/post.txt | awk '{print \$1,\$2}') && echo 'existing service PIDs unchanged'
free -m | sed -n 2p"
rm -rf "$TMP" "/tmp/$REL"
echo "evidence: traderj:/root/usb_runtime/btc_tsmom_deploy_$TS/"
