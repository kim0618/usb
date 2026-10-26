---
description: Run before US-B production server operations (deploy, build, restart, file swap, runtime/log/Q123 checks, retention or cleanup on traderj) to check disk free space and crypto context archive/receipt freshness. Reminds the user when the manual ctx archive pull is due. Never performs archive pull, receipt creation, retention apply, deletion, or restart without explicit approval. Not for local-only code, research, docs or prompt work.
allowed-tools: Bash, Read
---

# US-B 서버 작업 전 점검 (usb-server-preflight)

운영 서버(traderj)를 **실제로 확인하거나 바꾸는 작업**을 시작하기 전에 디스크 여유와 crypto context archive 상태를 한 번 본다. 하는 일은 **확인과 안내뿐**이다.

| 원칙 | 값 |
|---|---|
| ctx archive pull | **MANUAL** (사용자 승인 후에만) |
| reminder | AUTOMATIC (이 스킬) |
| retention apply | 사용자 승인 필요 |
| rm / truncate / logrotate 강제 | 절대 안 함 |
| service restart / 주문 / 레버리지 / git push | 절대 안 함 |
| 광역 scan (`find /`, `du /`, 트리 du/find) | 절대 안 함 |

## 언제 쓰나

쓴다: crypto 운영 배포, H/A/E 운영 배포, Q123 운영 체크, 서버 상태 확인, 운영 frontend/backend 빌드, service restart가 들어가는 작업, 운영 로그·런타임 점검, retention/cleanup 작업, 운영 서버 파일 교체.

안 쓴다: 코드 설명, 로컬 전용 연구·백테스트, 문서·프롬프트 작성, 로컬 파일만 읽는 분석, 운영 서버와 무관한 질문.

한 세션에서 같은 서버 작업 흐름이면 처음 한 번만 돌린다. 몇 시간 뒤 다른 운영 작업을 시작하면 다시 돌린다.

## 1. 실행 (읽기 전용, ssh 1회, 약 15초)

```bash
cd ~/usb && python3 deploy/crypto/server_preflight.py
```

이 스크립트가 읽는 것은 다음뿐이다.

- 서버: `shutil.disk_usage("/")`(df와 같은 값), `crypto_context/archive/{manifests,receipts}/*.json`(파일당 수 KB), `logs/retention.log`의 마지막 줄, `/proc/pressure/io`
- 로컬: `~/usb_archive/crypto_context/receipts/*.json`의 `verified_at`

판정 규칙은 스크립트의 `classify()`가 정본이다. 규칙을 바꾸려면 스크립트와 이 문서의 아래 표를 함께 고치고 `--selftest`를 다시 돌린다.

## 2. 판정 기준

| 입력 | RECOMMENDED (WARNING) | STRONGLY_RECOMMENDED (CRITICAL) |
|---|---|---|
| 마지막 성공 pull 이후 (context collection ACTIVE 또는 UNKNOWN일 때만) | ≥ 48시간 | ≥ 72시간 |
| context collection 상태 UNKNOWN | WARNING | |
| NO_RECEIPT | ≥ 150MB | (자체로는 WARNING까지) |
| 디스크 여유 | < 800MB | < 500MB |
| 값을 못 읽음 | 해당 항목 unknown → WARNING | |

- 셋 다 아니면 PASS다. **PASS면 archive 얘기를 꺼내지 않고** 원래 작업을 계속한다.
- context collection 상태(`ctx_state()`): collector와 Q123은 2026-10-26에 퇴역했다. 퇴역 뒤에는 pull할 일이 다시 생기지 않으므로 pull 경과 시간으로 경보하지 않는다.
  - **ACTIVE**: unit이 enabled이거나 실행 중, writer 프로세스(`context_collector_v1 collect`, `q123_forward_v1`)가 있음, 또는 journal 쓰기가 24시간 이내. 이 중 하나라도 해당하면 ACTIVE이고 기존 pull 규칙을 그대로 적용한다.
  - **RETIRED**: 모든 항목을 실제로 읽었고 전부 조용할 때만이다. `usb-crypto-context.service`는 loaded·disabled·inactive여야 한다. `usb-crypto-q123-forward.service`는 그와 같거나 not-found(제거)여도 된다. writer 0, journal(심볼릭 링크 제외) 마지막 쓰기 24시간 이상이어야 한다. 이때 pull 경과 시간만 판정에서 빠지고, 디스크·NO_RECEIPT 규칙은 그대로다. archive reminder는 NO_RECEIPT로만 정해진다(디스크 부족만으로는 NONE, pull로 디스크가 늘지 않으니까).
  - **UNKNOWN**: 하나라도 읽지 못했을 때다(collector unit not-found 포함). 파일이 없다고 RETIRED로 보지 않는다. WARNING을 내고 pull 규칙도 유지한다.
- collector unit 파일을 나중에 지우면 상태가 UNKNOWN이 된다. 그 정리를 할 때 이 규칙도 같이 고친다.
- NO_RECEIPT는 "현재 receipt가 없는 manifest의 합"과 "마지막 retention 로그의 NO_RECEIPT" 중 큰 값이다. 아직 manifest에 안 들어간 48시간 이내 파일(YOUNG, NO_MANIFEST)은 세지 않는다.
- 마지막 pull은 서버 receipt와 로컬 receipt의 `verified_at` 중 최신값이다.
- IO PSI avg10 ≥ 60이면 출력에 `HIGH: defer any pull`이 붙는다. 이 값은 판정에 들어가지 않고, pull 시점을 미루라는 안내에만 쓴다.

## 3. 사용자에게 말하는 방식

원래 하려던 작업을 막지 않는다. 결정은 항상 사용자에게 둔다.

- **PASS**: 아무 말도 하지 않는다(내부 보고만). 원래 작업 preflight를 이어 간다.
- **WARNING**: 한두 줄로 묻는다.
  > 배포 전에 하나만 확인할 게 있어요. ctx archive pull이 54시간 안 돌았고 NO_RECEIPT 165MB, 여유 720MB예요. archive부터 정리할까요, 이번 작업 먼저 할까요?
- **CRITICAL**: 정리를 먼저 하라고 권한다. 그래도 승인 전에는 실행하지 않는다.
  > 지금 여유가 480MB라 일반 배포나 빌드보다 ctx archive 정리를 먼저 하는 게 좋아요. 진행할까요?
- 서버를 못 읽었으면(`server read error`) 그 사실만 알리고 원래 작업 판단은 사용자에게 맡긴다.

AskUserQuestion 도구는 쓰지 않는다. 텍스트로 묻는다.

## 4. 내부 보고 형식

스크립트 출력이 그대로 이 형식이다.

```
US-B SERVER MAINTENANCE PREFLIGHT
disk free = ?
disk use = ?%
last ctx archive pull = ?
hours since pull = ?            (RETIRED면 "not checked: context retired")
context collection = ACTIVE / RETIRED / UNKNOWN (근거)
NO_RECEIPT files = ?
NO_RECEIPT bytes = ?
pending manifests = ?
retention status = ?
io psi avg10 = ?
archive reminder = NONE / RECOMMENDED / STRONGLY_RECOMMENDED
verdict = PASS / WARNING / CRITICAL
operational action automatically taken = 0
```

## 5. 사용자가 archive 정리를 승인했을 때 (정본 절차만)

계약은 `docs/crypto/context_collector_v1/RETENTION_CTX_V1_2.md`, 절차는 같은 폴더의 `RETENTION_CTX_V1_2_RUNBOOK.md`다. 새 archive 규칙을 만들지 않는다.

1. IO PSI를 본다. avg10이 높으면(대략 60 이상) 가라앉을 때까지 기다린다.
2. 서버 manifest를 받아 receipt가 없는 파일 목록을 만든다. manifest는 서버 retention이 만든다. 직접 만들지 않는다.
3. pull: `archive.pull()`(= `deploy/crypto/ctx-archive-pull.sh`)를 쓴다. 이 서버에서는 rsync를 저우선순위로 감싼다.
   ```bash
   # 래퍼 예: exec rsync --bwlimit=4000 --rsync-path="ionice -c3 nice -n19 rsync" "$@"
   PYTHONPATH=backend python3 -c "from pathlib import Path; from app.crypto.context_collector_v1 import archive as A; \
   print(A.pull(host='traderj', remote_root='/root/usb_runtime/crypto_context/journal', \
   remote_archive='/root/usb_runtime/crypto_context/archive', dest=Path('~/usb_archive/crypto_context'), \
   rsync='<래퍼 경로>', upload=False))"
   ```
   처음에는 `upload=False`로 받기와 검증만 한다.
4. 로컬에서 따로 검증한다: 파일 수, 크기, sha256, gzip 무결성을 manifest와 대조한다.
5. 같은 호출을 `upload=True`로 다시 돌려 receipt를 올린다. 서버와 로컬 receipt의 sha256이 같은지 확인한다.
6. dry-run: 서버에서 unit Environment와 `config/context.env`를 읽은 뒤 `retain --no-manifest`를 `--apply` 없이 실행한다. **`prune_eligible == receipt_verified`**이고 `q123.state == OK`인지 확인한다.
7. apply: runbook 3절대로 `systemctl start usb-crypto-context-retention.service`(retention oneshot, 서비스 재시작이 아님). 삭제는 retention 도구만 한다.
8. apply가 새 manifest를 만들면 `keep_reasons`에 NO_RECEIPT가 다시 생긴다. 승인 범위 안이면 2~7을 한 번 더 한다.
9. post-check: `df -B1 /`, journal 합계, NO_RECEIPT, collector·Q123·btc-context-ui·paper의 PID와 NRestarts, structure/flow 파일 갱신 시각, paper ledger sha256.

source journal을 직접 지우지 않는다.

## 6. 헷갈리지 말 것

ctx archive pull은 Q123도, Wall/Flow 계산기도, collector도, 주문 기능도, 매매 전략도 아니다. crypto_context journal을 로컬 COLD(`~/usb_archive/crypto_context`)로 안전하게 복사해서, receipt 기반 retention이 서버 원본을 지울 수 있게 해 주는 백업과 보관 절차다.

## 7. 참고 배경 (판정 기준 아님)

아래 수치는 참고 맥락이다. 판정에 하드코딩하지 않는다.

- 2026-10-19: 10/15 이후 pull이 없어서 NO_RECEIPT가 약 299MB(288파일) 쌓였다. 정본 pull → receipt → retention으로 357파일 371MB를 회수했고, 여유가 약 635MB에서 1.02GB가 됐다. 런타임 데이터 손실 0, 서비스 재시작 0. 이 사고가 이 reminder가 생긴 이유다.
- 당시 증가 속도: crypto_context 원천 kind 하루 약 55~75MB(receipt가 있어야 지워짐), crypto_paper/run 하루 약 11~14MB(retention 없음, 이 스킬은 손대지 않음), btc_context_ui와 Q123은 소량, nginx는 logrotate 정상, frontend는 현재 빌드와 최신 rollback만 유지.
- 서버 IO PSI 기준선이 원래 높다(avg300 약 20). 트리 du/find 한 번에 avg10이 90대로 튄 적이 있다.

## 8. 자체 시험

```bash
cd ~/usb && python3 deploy/crypto/server_preflight.py --selftest
```

다음 세 묶음, 모두 39개를 검증한다.
- 시나리오 T1~T5, 경계값(48h, 72h, 150MB, 800MB, 500MB), 값 unknown: 12개
- context 상태별 판정 C1~C9: 12개
- 상태 판정 자체 S_*: 15개(enabled, 실행 중, failed, writer, 24h 경계, 읽기 실패, Q123 제거 허용, collector 제거는 UNKNOWN 등)
