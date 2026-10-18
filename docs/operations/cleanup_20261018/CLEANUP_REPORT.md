# HOST DISK EMERGENCY CLEANUP 2026-10-18

결과: **PASS** (삭제 12:47:27 KST, 증가 측정 12:49~13:19)

## 왜
2026-10-18 crypto 배포 중 trader-j 디스크 99%, 여유 117,112,832 bytes. 원인: 이전 frontend 배포 스크립트가
Next `cache`(~116MB)를 롤백 디렉터리에서 새 빌드로 복사해, 롤백 디렉터리마다 ~121MB가 쌓였다.
같은 날 배포(`docs/crypto/marker_leverage_production_v1/`)부터 cache는 이동(mv)으로 바꿨다.

## 무엇을
- 보존: 서빙 빌드 `/root/usb/frontend/.next-build` (BUILD_ID c4VYa7VRAWu0aFBN3fAvV; `usb-frontend`
  WorkingDirectory `/root/usb/frontend`, `npm start` = `NEXT_DIST_DIR=.next-build next start`), 최신 롤백
  `.next-build.pre-marker-leverage-20261018-121704` (PrBzRXD3I4vTsgKCutS24, 배포 manifest와 sha 일치).
- 삭제: 그보다 오래된 frontend 롤백·실패 빌드 9개, 646,938,624 bytes. 목록·바이트·BUILD_ID·파일 수·목록 sha·사유는
  `DISK_CLEANUP_DELETE_MANIFEST_20261018-124727.txt` (서버 원본 `/root/usb_runtime/` 같은 이름). `-124703`은 같은 점검의 check 모드 기록.
- 점검(후보마다, `audit.sh`·`cleanup.sh`): systemd, nginx, symlink, 열린 fd, mmap, 프로세스 cwd/root, config/cron,
  운영 스크립트 참조 전부 0. 대조군: 같은 방법이 열린 fd 60개와 `/root/usb/frontend` 아래 mmap 1개를 검출.
- 운영 데이터(PAPER 원장, Q123, context/wall/flow, H/A/E runtime)·git 파일 삭제 0. 서비스 재시작 0.

## 결과
| | bytes |
|---|---|
| 삭제 전 여유 | 117,112,832 (99%) |
| 삭제 직후 여유 | 764,051,456 (93%) |
| 30분 뒤 여유 | 778,678,272 |
| 확보 | 646,938,624 |

전후(`health_pre.txt`, `health_post.txt`): frontend/crypto/backend health 200, 6개 서비스 PID·NRestarts 불변,
PAPER 원장 digest 불변, Q123 RUNNING, Flow COMPLETE.

## 증가 측정 (`growth.sh`, `growth.txt`)
5분 간격 7회 df + 가벼운 경로, 무거운 `crypto_context`는 처음·끝 2회(depth 2).
- 30분 순변화 +16MB: retention이 `crypto_context/journal`(-13.3MB)과 `crypto_paper/run`(-4.8MB)을 주기적으로 지움.
  정리 사이 구간 소비 250~630KB/분.
- 꾸준한 순증가: `btc_context_ui` +2.4MB(~79KB/분), `/var/log/nginx` +1.5MB(~50KB/분). 이 둘이 정리되는지는 미확인.
- 남은 위험: LOW. 하루 단위 재측정 권장.
- 주의: `/root/usb_runtime` 전체 depth-1 du는 43초 걸리고 IO PSI를 97까지 올렸다. 주기 측정에 쓰지 않는다.
