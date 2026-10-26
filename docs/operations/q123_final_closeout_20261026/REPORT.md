# US-B CRYPTO: Q123 FINAL OPERATIONAL REMOVAL V1

작업일 **2026-10-26 KST**, 운영 서버 trader-j. 증거 원본과 롤백 절차는 로컬 아카이브 `~/usb_archive/2026-10/crypto/q123-final-closeout-20261026/`(`ROLLBACK.md`, `SHA256SUMS`)에 있다. 이 문서는 요약이다.

**Q123 overall = RESEARCH CLOSED / CONTINUATION STOPPED.**

**Q123 OPERATIONAL REMOVAL = PARTIAL.** 운영 서비스 종료는 성공했다. Q123과 context collector 모두 STOPPED / DISABLED, 프로세스 RSS 0, 신규 context 쓰기 0이다. PARTIAL은 데이터 retention 정리가 덜 끝났기 때문이며, 사유는 두 가지다.

1. 서버 journal에 남은 485파일 약 373MB는 48시간 HOT 기간이다. 현행 retention 계약상 그 뒤에야 manifest와 receipt를 받을 수 있어 10/28 이후 정리한다. 그 중 context/wall_r0 약 60MB(seg 353~372)는 V1.2 `Q123_MARGIN` 때문에 계속 남는다(4절).
2. Q123 runtime 일부(checkpoints, status, store)는 retention Q123 guard의 입력이라 서버에 유지한다.

## 1. 연구 결과 (사전등록 분석 1회, 10/26 08:50 store)

계약 `CONTRACT_Q123_FORWARD_V1.md` SHA256 `9d855ad7…`, analyzer `analyze.py` `4fcaa2da…`, release `q123-forward-20cba2c`(MANIFEST 64 OK). 판정 규칙은 부모 계약 `CONTRACT_MC_R0.md` 12~14절.

| 질문 | 최종 60분 N | 분석 시점 N | 판정 | 60분 net 평균 / 95% CI | 양수 비율 |
|---|---:|---:|---|---|---|
| Q1 저항 거부 SHORT | 36 | 36 | **Q1 = NO_CONTEXT_EDGE** | −14.63bp / [−23.88, −4.98] | 10/36 |
| Q2 저항 돌파 LONG | 21 | 20 | **Q2 = INSUFFICIENT** (NOT_MEASURABLE) | 판정 제외 | |
| Q3A 지지 유지 LONG | 37 | 37 | **Q3A = NO_CONTEXT_EDGE** | −13.39bp / [−20.02, −7.11] | 12/37 |
| Q3B 지지 붕괴 SHORT | 16 | 15 | **Q3B = INSUFFICIENT** (NOT_MEASURABLE) | 판정 제외 | |

NO_CONTEXT_EDGE는 부모 계약 14절의 A 판정이다.

* 4질문 × {5, 15, 60분} 격자에서 Holm 유의 칸 0, 자체 net 양수 칸 0. lean 방향이 맞는 칸(Q1 5분 p 0.127, Q3A 5분 p 0.616, Q3A 15분 p 0.415)도 B 조건을 채우지 못했다.
* Q2/Q3B는 측정하지 못한 것이다. 실패가 아니다. 이 문서는 Q123 전체 실패를 주장하지 않는다.
* 최종 N은 정지 후 store 기준이다. 분석 뒤 Q2와 Q3B에서 마지막 60분 outcome이 하나씩 완결됐다. 둘 다 30 미만이라 판정은 그대로이고, 분석은 다시 돌리지 않았다.
* 정지 근거는 운영 감사(`PRODUCTION_RESOURCE_OWNERSHIP_AUDIT_V1.md` 10절)의 STOP_CANDIDATE와 사용자 결정이다. 사전등록 계약에는 종료 규칙이 없다(10/23 readiness 보고서).

**Q123 research = CLOSED.**

## 2. 운영 조치

| 순서 | 조치 | 결과 |
|---|---|---|
| 0 | preflight | PASS, NO_RECEIPT 0, 여유 1,144MB (같은 날 아침 ctx archive catch-up 후) |
| 1 | 활성 consumer 감사 (unit, timer, cron, nginx, 8013 연결, `/proc` fd·cmdline, 소스 grep) | collector를 읽는 활성 프로세스는 Q123 하나. paper·backend·frontend·LIVE·A/E/H/I 참조 0. BTC Context UI·structure·flow·manual-research는 이미 disabled |
| 2 | usb-crypto-context: disable 후 SIGTERM만 전송 (unit stop timeout 30초가 종료 단계 상한 3×15초보다 짧아 SIGKILL 위험을 피함) | 16초 만에 종료, END 기록(stop_reason signal), exit 0, NRestarts 0, `.open` 0, seg 372까지 seal |
| 3 | Q123이 세션 마지막 샘플(09:43:00)까지 읽은 것을 확인 | WAITING_FOR_DATA |
| 4 | usb-crypto-q123-forward: disable 후 SIGTERM | 15초 만에 종료, exit 0, NRestarts 0, 최종 checkpoint seg 372 |
| 5 | 최종 증거 아카이브 | 서버 q123_forward 164파일 sha256 전부 일치, JSON/JSONL/gzip 손상 0, 08:50 store가 최종 store의 prefix |
| 6 | retention dry-run (정지 후) | q123 OK, watermark 364, eligible 0 = receipt_verified 0, 남은 485파일 373MB 전부 YOUNG |
| 7 | 삭제 (5중 증명) | `q123_forward/runtime/klines.jsonl` 1파일 9,544,826B만. 목록은 아카이브 `ops/deletion_manifest.json` |

collector를 Q123보다 먼저 멈췄다. Q123 store와 checkpoint가 collector의 마지막 기록까지 포함하게 하려는 순서다.

**남긴 것과 이유.** Q123 checkpoints, status, store는 retention의 Q123 guard가 읽는다(`CTX_V1_Q123_RUNTIME`, `CTX_V1_Q123_STORE`). 지우면 `Q123_NO_RUNTIME`이 되어 context/wall_r0가 영구히 지워지지 않는다. release, config, unit 파일은 롤백용이고 크기가 작다. journal은 retention만 지운다. writer lock은 손대지 않았다.

## 3. 자원 (before 09:41~09:42 4표본, after 09:47~09:59 13표본)

| 항목 | before | after |
|---|---|---|
| MemAvailable | 279~304MB (평균 약 295) | 510~523MB (평균 약 516) |
| swap 사용 | 522MB | 387MB |
| memory PSI some avg10 | 0.00 | 0.00 |
| IO PSI some avg300 | 1.8~2.0 | 2.1~6.6 (정지 직후 아카이브 rsync 영향 후 2.1로 하락) |
| 디스크 여유 | 1,115MB | 1,167MB |
| Q123 RSS / collector RSS | 132MB / 99MB | 0 / 0 (프로세스 0) |
| journal 파일 수 / 최신 기록 | 계속 증가 | 857 고정 / 09:43:22 고정 |
| Q123 store | 계속 증가 | 8,078,340B 고정 |

MemAvailable 증가는 짧은 창의 관찰값이다. 장기 평균을 보장하지 않는다.

## 4. 남은 일

* **10/28 이후 ctx archive pull 1회.** retention timer(04:40 KST, 유지)가 HOT이 끝난 10/24~10/26 파일의 manifest를 만든다. `/usb-server-preflight` 안내가 뜨면 기존 절차(pull, 독립 검증, receipt, dry-run, apply)로 정리한다.
* **영구 잔류분.** 세션 31cc8177은 writer lock이 계속 가리켜 V1.2에서 ACTIVE다. watermark 364 기준 context/wall_r0 seg 353~372(약 20시간치)는 `Q123_MARGIN`으로 남는다. 지우려면 retired session finalization을 다루는 새 retention 계약과 구현이 필요하다(10/23 readiness 보고서 P절 D·E). 별도 승인 사항이다.
* collector 정지로 journal 신규 성장은 0이다.

## 5. 회귀

| 항목 | 결과 |
|---|---|
| PAPER | active, PID 2846976 그대로, NRestarts 0, `/health` ok |
| PAPER ledger | 332줄, sha256 `63a33364…` 작업 전후 동일 (positions 변화 없음) |
| LIVE / HOT manual | paper 서버 내부 기능, 프로세스 그대로. 서명 조회는 하지 않음 |
| 주문 생성 | 0 |
| backend / frontend | PID 그대로, NRestarts 0 |
| C1 | 10/15 제거 상태 그대로 |
| liqfwd | disabled 그대로 |
| A/E/H/I | unit·timer 변경 없음 |

## 6. 최종 상태

| 항목 | 값 |
|---|---|
| Q123 service | STOPPED / DISABLED |
| Q123 runtime | KEPT (retention guard 입력), kline 캐시만 삭제 |
| context collector | STOPPED / DISABLED |
| context runtime | KEPT (journal 373MB는 retention 대기) |
| new context writes | 0 |
| NO_RECEIPT | 0 (10/24 이후 파일은 아직 manifest 전) |
| final evidence archived | YES |
| rollback | READY (`ROLLBACK.md`) |
| service restart | 0, SIGKILL 0, 직접 rm은 검증된 1파일 |
| commit / push | 없음 |
