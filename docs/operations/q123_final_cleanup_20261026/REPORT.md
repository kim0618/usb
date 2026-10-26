# US-B CRYPTO: Q123 FINAL RETIRED RUNTIME CLEANUP V1

작업일 **2026-10-26 KST 11:30~11:42**, 운영 서버 trader-j. 같은 날 앞선 운영 종료 보고서는 `docs/operations/q123_final_closeout_20261026/REPORT.md`이고, 이 문서는 그 보고서를 고치지 않고 이어 쓰는 별도 기록이다. 증거 원본은 로컬 `~/usb_archive/2026-10/crypto/q123-final-cleanup-20261026/`(`MANIFEST.json` sha256 `e0afaff1…`, `SHA256SUMS` 17)에 있다.

**Q123 FINAL RETIRED RUNTIME CLEANUP = PASS.**

| 구분 | 상태 |
|---|---|
| Q123 research | **CLOSED** |
| Q123 production runtime | **REMOVED** |
| 연구 증거 | **PRESERVED** |

연구 판정(closeout 보고서 1절, 사전등록 분석 1회)은 바뀌지 않았다.

| 질문 | 판정 | N |
|---|---|---|
| Q1 | NO_CONTEXT_EDGE | 36 |
| Q2 | INSUFFICIENT (NOT_MEASURABLE) | 분석 시점 20 / 최종 21 |
| Q3A | NO_CONTEXT_EDGE | 37 |
| Q3B | INSUFFICIENT (NOT_MEASURABLE) | 분석 시점 15 / 최종 16 |

Q2와 Q3B는 측정하지 못한 것이지 실패가 아니다.

## 1. 왜 48시간을 기다리지 않았나

closeout 시점의 계획은 10/28 이후 기존 pull, receipt, retention 절차로 HOT 파일을 정리하는 것이었다. 기다려도 두 가지는 해결되지 않는다.

* 세션 31cc8177은 `.writer.lock`이 계속 가리키므로 V1.2 계약 4절상 ACTIVE다(writer가 죽어도 ACTIVE). 그래서 context/wall_r0 seg 353~372(40파일 42,067,711B)는 `Q123_MARGIN`으로 영구히 남는다. 세션을 CLOSED로 본다 해도 Q123 최종 cursor가 `done=false`라 `Q123_NOT_DONE`에 걸린다.
* retention에는 종료 세션을 retire 또는 finalize하는 공식 명령이 없다(retention.py, archive.py, q123_forward_v1, 계약 문서 확인).

즉 기존 범용 retention만으로는 이 파일들을 지울 수 없었다. writer가 없으므로 기다리는 동안 새로 생기는 정보도 없다. 그래서 retention 코드, 계약, `context.env`, writer lock은 그대로 두고, 별도 final-cleanup manifest와 5중 증명을 붙인 retired cleanup으로 지웠다.

closeout 보고서 4절의 **10/28 이후 ctx archive pull 1회 일정은 취소한다.** 대상 파일이 이미 COLD로 옮겨졌고 서버에서 지워졌다.

## 2. 신규 writer가 없다는 증거

| 항목 | 값 |
|---|---|
| retired session | `31cc8177-b7cc-407d-aceb-ffb27b8ac94a`, START 10/10 21:47 |
| collector END 기록 | `session-20261026-31cc8177-00372.jsonl`, reason signal, ended 10/26 09:43:21, records 141,581,758 |
| 최종 segment | 372 (context 09:43:22, wall_r0·wall_v2 09:43:24, telemetry 09:43:21, storage_stats 09:43:24) |
| Q123 최종 checkpoint | `ckpt-001792975380097.json`, sample 09:43:00, cursor context [372, 11020409]·wall_r0 [372, 17908717], done false, pending 11, status WAITING_FOR_DATA |
| unit | usb-crypto-context, usb-crypto-q123-forward, usb-btc-context-ui 전부 disabled/inactive. structure, flow, manual-research는 static/inactive이고 timer 없음 |
| 프로세스 | cmdline, cwd, open fd, mmap 참조 0 (`/proc` 전수, 삭제 직전 재확인) |
| cron, nginx, 포트 8013 리슨 | 0, 0, 0 |
| 운영 소스·env 참조 | `/root/usb/backend/app`, `deploy`, paper env에서 journal·q123 경로와 8013 참조 0 |
| `.open`/`.tmp` | 0, journal 파일 수 857 고정, 최신 기록 09:43 고정 |

남은 참조는 retention(정적 oneshot과 04:40 timer)의 `CTX_V1_Q123_RUNTIME`, `CTX_V1_Q123_STORE` 설정뿐이다. 이것은 실행 consumer가 아니라 retention 전용 참조다.

## 3. 아카이브 검증

| 대상 | 아카이브 위치 | 결과 |
|---|---|---|
| journal 857파일 373,402,119B (삭제 후보 485 + session 372) | `~/usb_archive/crypto_context/journal/<kind>/` | 서버 sha256과 857/857 일치, 크기 일치, gzip 147/147 정상, 비압축 jsonl 710개 파싱 오류 0·잘린 줄 0 |
| COLD 연속성 | 같은 위치 | context, wall_r0, wall_v2 모두 seg 1~372 연속 |
| Q123 runtime·store 57파일 | `~/usb_archive/2026-10/crypto/q123-final-closeout-20261026/server/q123_forward/` | 서버 파일과 closeout 기록 sha 57/57 일치, 아카이브 사본 재검증 OK, closeout `SHA256SUMS` OK |

pull은 기존 저부하 방식(`rsync --bwlimit=4000 --rsync-path="ionice -c3 nice -n19 rsync"`, `--files-from` 명시 목록)으로 했다. 로컬에 같은 이름 파일은 0이었다. 이 파일들은 retention manifest가 없으므로 `archive/manifests`나 receipt에는 넣지 않았다. 삭제 근거는 위 `MANIFEST.json`이다.

## 4. 삭제 (5중 증명)

삭제 직전 서버에서 다시 확인했다. 프로세스 참조 0, systemd active 0, cron 0, 포트 0, open 파일 0. 파일마다 일반 파일, 링크 수 1, 크기와 mtime이 목록과 같은지 확인했다. Q123 파일은 sha256도 다시 확인했다. 경로가 Q123/세션 31cc8177 패턴이 아니면 전체를 멈추게 했다. 삭제는 명시 목록의 `os.unlink`만 썼고 와일드카드나 디렉터리 삭제는 없다.

| 대상 | files | bytes | 5-proof | 결과 |
|---|---:|---:|---|---|
| journal/context seg 324~372 | 49 | 58,184,204 | PASS | 삭제 |
| journal/wall_r0 seg 324~372 | 49 | 43,749,771 | PASS | 삭제 |
| journal/wall_v2 seg 324~372 | 49 | 21,026,908 | PASS | 삭제 |
| journal/telemetry seg 204~372 | 169 | 181,731,738 | PASS | 삭제 |
| journal/storage_stats seg 204~372 | 169 | 68,706,431 | PASS | 삭제 |
| q123_forward runtime (checkpoint 51, status) + store 5 | 57 | 8,950,430 | PASS | 삭제 |
| **합계** | **542** | **382,349,482** | 542/542 | 실패 0 |

그 중 `Q123_MARGIN` 잔류분(context·wall_r0 seg 353~372)은 40파일 42,067,711B다.

| 묶음 | before | after |
|---|---|---|
| 48h HOT journal (5개 kind) | 485파일 373,399,052B | 0 |
| Q123_MARGIN (위 HOT의 부분집합) | 40파일 42,067,711B | 0 |
| Q123 runtime/store | 57파일 8,950,430B | 0 |
| 합계 | 542파일 382,349,482B | 0 |

남긴 것:

* journal/session 372파일 3,067B: 계약상 PINNED(ALWAYS_KEPT)이고 세션 식별 기록이다. COLD에도 사본이 있다.
* `.writer.lock`과 kind 디렉터리: retention `check_root`가 요구한다. lock을 지우면 retention이 REFUSED로 끝난다.
* q123_forward releases, config, logs, src 링크와 unit 파일 약 1.12MB: 롤백용 코드와 설정이다. closeout 아카이브에도 있다.
* 빈 디렉터리 `q123_forward/runtime/checkpoints`, `q123_forward/store`.

## 5. retention 사후 상태

정본 절차대로 unit 환경과 `context.env`를 읽고 `retain --no-manifest`를 `--apply` 없이 실행했다.

| 항목 | 값 |
|---|---|
| exit code | 0 |
| failed | 0 |
| keep reasons | ALWAYS_KEPT 372 / 3,067B 뿐 |
| HOT, archive candidate, prune eligible, expired | 전부 0 |
| NO_RECEIPT | 0 |
| Q123 관련 keep reason | 0 |
| health | OK |
| q123 guard state | `NO_CHECKPOINT` |
| session 31cc8177 | ACTIVE (WRITER_LOCK_NAMES_IT), end_record true |

`NO_CHECKPOINT`는 오류가 아니라 fail-closed 정보값이다. context/wall_r0 후보가 0이므로 아무것도 막지 않는다. retention timer(04:40)는 그대로 두었다. 지금은 할 일이 없지만 telemetry 만료 규칙과 다른 데이터에는 해가 없다. 다음 timer 실행부터 `retention.log`와 preflight의 retention 줄에 `q123 NO_CHECKPOINT`, `ALWAYS_KEPT 372`가 보이는 것이 정상이다.

## 6. 자원

| 항목 | before (11:33) | after (11:40~11:41, 3표본) |
|---|---|---|
| 디스크 여유 | 1,163,362,304B | 1,547,431,936B (+384MB) |
| MemAvailable | 306MB | 391~417MB |
| swap 사용 | 179MB | 237MB |
| memory PSI some avg10 | 0.00 | 0.00 |
| IO PSI some avg10 / avg300 | 1.43 / 10.87 | 2.38~18.11 / 20.3~23.5 |

메모리와 swap 변화는 이번 삭제 때문이 아니다. 멈춘 프로세스가 없고, 파일 삭제는 RSS를 바꾸지 않는다. 서버 sha 계산(373MB 읽기)과 pull 동안 IO PSI avg10이 최대 98까지 올랐고, page cache 변동이 섞였다. pull은 avg10이 11로 내려간 뒤 시작했다.

## 7. 회귀

| 항목 | 결과 |
|---|---|
| PAPER | usb-crypto-paper active, PID 2846976 그대로, NRestarts 0, `/health` ok |
| PAPER ledger | 28개 ledger 658줄, 결합 sha `5895c6ca…` 전후 동일. main 332줄, mtime 09:00:00 (closeout 때와 같음) |
| LIVE / HOT manual | paper 서버 내부 기능, 프로세스 그대로. 서명 조회는 하지 않음 |
| 주문 생성 / 포지션 변화 | 0 / 없음 |
| backend / frontend | PID 그대로, NRestarts 0 |
| A/E/H/I | timer 목록과 enabled 상태 전후 동일. usb-i-paper.service failed는 10/22 10:08부터 이어진 기존 상태(timer disabled) |
| service restart | 0 |

## 8. 롤백 현실

| 항목 | 값 |
|---|---|
| rollback source | 아카이브 최종 증거(closeout 폴더의 q123_forward runtime/store, `ROLLBACK.md`) + COLD journal + git source + 서버에 남긴 release/config/unit |
| reconstruction required | **YES (RECONSTRUCTION REQUIRED)** |

`start`만으로 바로 복구되는 상태가 아니다. collector나 Q123을 다시 `start`해도 예전 상태로 돌아가지 않는다. Q123을 재개하려면 closeout 아카이브에서 runtime과 store를 복원하고, 세션 31cc8177의 context/wall_r0를 COLD에서 서버 journal로 다시 올려야 한다. collector만 다시 켜면 새 세션이 시작된다. 그때 retention Q123 guard가 `NO_CHECKPOINT`이므로 새 context/wall_r0는 지워지지 않고 쌓인다. 따라서 Q123 runtime을 복원하거나 retention 설정을 따로 정리하는 결정이 먼저 필요하다. Q123 연구는 CLOSED라서 재개 편의보다 깨끗한 제거를 택했다.

## 9. 최종 상태

| 항목 | 값 |
|---|---|
| Q123 research | CLOSED |
| Q123 production runtime | REMOVED |
| Q123 service | STOPPED / DISABLED, 프로세스 0 |
| context collector | STOPPED / DISABLED, 프로세스 0 |
| active consumers | 0 |
| research evidence | PRESERVED (closeout 폴더, COLD journal seg 1~372) |
| COLD archive | PASS (857/857 sha·크기, gzip 147/147, jsonl 오류 0, seg 1~372 연속) |
| retention error / NO_RECEIPT | 0 / 0 |
| q123 guard | NO_CHECKPOINT (정상 closeout 상태, `context.env`가 삭제된 runtime 경로를 계속 가리킴) |
| PAPER / LIVE / A / E / H / I | 변경 없음 |
| 서버 journal | session 372파일 3,067B와 lock만 남음 |
| 서버 Q123 데이터 | 0 (코드·설정 약 1.12MB만 남음) |
| 서버에 Q123 때문에 남겨야 하는 데이터 | 없음 |
| commit / push | 없음 |
