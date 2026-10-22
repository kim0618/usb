# US-B Strategy I: PAPER DEPLOY PREP IMPLEMENTATION V2 결과

작성 2026-10-22 KST. Codex 세션(2026-10-21 17:23 KST)이 6단계 직후 사용량 한도로 멈춰서 Claude가 7단계부터 이어받았다.
운영 배포·서비스 restart/stop·I Paper launch는 하지 않았다. 서버에서는 읽기만 했다(조회, rsync pull, 기존 ctx archive pull 계약).

## 판정

**I PAPER DEPLOYMENT READINESS = READY_FOR_PRODUCTION_APPROVAL** (운영 반영은 사용자 승인 대기)

| 항목 | 판정 | 근거 |
|---|---|---|
| CODE | PASS | I 테스트 전체 통과(`tests_final.txt`), 기준 462 events `c5797443…` 재현, frozen 대비 행렬·trade·NAV 바이트 동일 |
| DATA | PASS | 203파일 2025-10-17~2026-08-10, 누락·중복·스키마 오류 0, 서버 50파일과 겹치는 44파일 sha 동일, 서버+history 병합 view가 실험 입력과 파일 단위로 동일 |
| SNAPSHOT | PASS(조건) | forward launch에는 서버의 CS_2026-10-01 하나로 충분(E1·E2 완전 일치). 다음 분기 collector는 설계만 있음 → 2027-01-29 신호 전까지 구현 필요 |
| MEMORY | PASS | 운영 경로 피크 235~240MiB(launch), +12M 약 355MiB. ET 02:15 시각 서버 available 170~370MiB + swap 여유 1.4GiB |
| TIMER | PASS | oneshot + timer 초안, ET 02:15 Tue..Sat, guard 테스트 통과 |
| DISK | PASS(주의) | 서버 / 여유 1.1G(90%). 업로드 약 78MB + store 수 MB |
| ROLLBACK | PASS | 아래 절차. 다른 전략 디렉터리를 건드리지 않는 배치라 되돌리기가 국소적 |

## 1~6단계 (Codex 수행분, 확인만)

- 커밋 `54eb67f feat(i-paper): package frozen runtime and parity-verified lean loader` (미push, `84df43f`와 함께 origin보다 2개 앞섬)
- frozen 패키지 `backend/app/strategies/i_paper/frozen/` 약 170KB, manifest sha `4530fc7c…` 실행 전 검증
- ctx archive pull(Retention V1.2 기존 계약): 20261016-002 3파일, 20261017-001 72파일 신규 검증·receipt, 나머지 ALREADY_VERIFIED, 오류 0 (`archive_pull.json`)

## 7. 최소 price history 패키지

- `data/runtime/i_paper_deploy_prep_v2/price_history_2025-10-17_2026-08-10.tar` sha256 `5db96715…`, 203파일 77,641,065 bytes
- 서버 grouped 50파일(2026-08-11~10-20) pull 후 sha 전부 일치, 연속성 끊김 없음(병합 253세션 XNYS 달력 완전)
- **배치 결정**: 서버 `strategy_c/raw/grouped`에 넣지 않는다. H(h-forward 매일 실행)가 그 디렉터리를 읽고, 겹치는 세션에서 그쪽 파일을 우선하기 때문에 H의 입력이 바뀐다. 대신 I 전용 `/root/usb_runtime/strategy_i_paper/history/grouped`에 두고 view가 병합한다(`Stores.history`, 같은 세션이 양쪽에 있으면 sha가 같아야 하고 다르면 refuse).

## 8. Snapshot parity

| 실험 | 조건 | 결과 |
|---|---|---|
| E1 | 최소 history, A=서버 CS_2026-10-01만 vs B=로컬 11개, 2026-10-20 랭킹 | snapshot·eligible 2540·ranked 2398·order·rank sha 전부 동일 |
| E2 | 전체 history, 시점 이동 proxy A=CS_2026-07-01만 vs B=전체, launch 2026-07-31 | 94 events, event·candidate·final·ranking·continuity·trade digest 전부 동일 |
| E3 | 요청 문구 그대로 A=CS_2026-10-01만 vs B=전체, launch 2025-09-30 | 다름: A는 2026-10-01 이전 신호 13개 전부 refuse(0 events), B 468 events |

결론: **forward launch(2026-10-30~)에는 historical snapshot 업로드 불필요.** 필요한 범위는 과거 구간을 재생할 때만 해당하며, 그 경우 첫 신호 시점에 유효한 snapshot부터 전부 필요하다. 운영 registry는 새 파일을 업로드한 날부터만 available로 보므로, 올려도 과거 결정에는 영향이 없다. threshold·로직은 바꾸지 않았다.
부수 관찰: snapshot이 적으면 `all_cs` 합집합 컬럼이 줄어 RSS가 약 100MB 낮다(결과는 동일).

## 9. 분기 snapshot collector 설계 (미구현, timer 비활성)

- source: Massive `/v3/reference/tickers` CS, 기존 `raw_fetch.fetch_tickers` 그대로 사용(CS_2026-10-01을 만든 함수, `strategy-c-selection-raw-v1` 포맷 그대로 호환)
- date: 분기 첫 XNYS 세션(1·4·7·10월). 파일 `CS_<as_of>.json.gz`, as_of = 그 세션
- 필드: ticker, cik(issuer identity), composite_figi·share_class_figi, market, primary_exchange, type(security type), active, delisted_utc, last_updated_utc (기존 TICKER_FIELDS)
- 실행: 매일 01:30 ET oneshot. "as_of ≤ 오늘인 분기 시작 세션 중 파일이 없는 것"이 있을 때만 호출(약 6회), 있으면 즉시 종료 → 중복 실행 안전
- 쓰기: I 전용 staging에 받기 → 검증(행 1개 이상, ticker 중복 0, as_of 일치, 직전 snapshot 대비 행 수 80% 이상, secret-like 없음) → sha256 sidecar → `os.replace`로 `strategy_c/raw/tickers/`에 원자적 반영
- 실패 시: staging 삭제, 기존 snapshot 유지, 비정상 종료로 unit failure 표시. 기존 파일은 절대 덮어쓰지 않음(cached)
- 유의: H refresh와 같은 Massive 키 5/분 limiter를 쓰므로 grouped(00:40)·splits(00:55)·h-forward(01:10)와 시각을 분리

## 10. 2차 메모리 최적화 (결과 불변만, 하나씩)

검증 기준: pytest 16개(462 events `c5797443…`, trade·NAV sha, 행렬 동일), E2_B·E3_B·E2_A digest 7종 동일, 운영 구성(E1_A) 피크.

| 후보 | 내용 | digest | 운영 구성 피크 |
|---|---|---|---|
| 기준 | | | 301MiB |
| A | snapshot 행을 `primary_exchange`·`cik`만 보관(두 키 항상 존재 → `snap.get(tk) or …` 분기 동일) | 동일, 적용 | 298 (snapshot 11개 구성은 −35MiB) |
| B | dense `adj` 제거 → split 종목만 배율 배열(`SplitFactors`), CA는 split 열만 곱함 | 동일, 적용 | 279 |
| C | DV를 위치 233(=252−19) 이후 행만 보관(`WindowFrame`) | 동일, 적용 | 252 |
| D | column-set cache | 미적용 | 메모리 이득 없음(속도 항목), 랭킹 함수 본문 교체가 필요해 위험 대비 이득 없음 |

교체한 frozen 줄은 V1 21-25·28-40·45-53이며, 각 줄 범위의 sha를 `REPLACED_SHA`에 고정해 실행 전에 검증한다. float32·universe 축소·incremental semantics 변경은 없다.

## 11. Memory profile (최종 코드, 운영 경로 = window loader)

| 구성 | 행×열 | snapshot | events | RSS 피크 | anon | wall |
|---|---|---|---|---|---|---|
| launch | 253×5320 | 1 | 0 | 238MiB | 168MiB | 13s |
| +1M | 275×5477 | 2 | 37 | 260 | 190 | 23s |
| +3M | 317×5477 | 2 | 94 | 272 | 202 | 22s |
| +6M | 380×5625 | 3 | 203 | 299 | 228 | 23s |
| +12M | 503×5908 | 5 | 440 | 353 | 283 | 20s |
| full(전체 history, window 없음) | 525×6504 | 11 | 468 | 404 | 333 | 26s |

projection은 실데이터를 시점만 앞당긴 proxy다(같은 행 수, 그 시점까지 쌓였을 분기 snapshot 수). 증가율은 연 약 +115MiB. 실제 서버는 100 IOPS 디스크·swap 사용 중이라 wall은 로컬보다 길 것으로 본다. 운영 배치 그대로의 E2E(`run --launch 2026-10-30` → `--as-of 2026-10-20`)는 피크 235MiB, 8초, 재실행 시 appended 0(멱등).

## 12~14. Oneshot·timer·guard·cgroup (초안, 미설치)

- `systemd/usb-i-paper.service`, `systemd/usb-i-paper.timer`: `Tue..Sat 02:15 America/New_York`, Persistent
- 실행: `run --as-of auto`(ET 기준 직전 XNYS 세션) `--history <I 전용>`
- guard(`app.strategies.i_paper.guard`, ExecCondition): skip 조건은 e-rvol·e-rvol-once·grouped-daily·splits-refresh·h-forward 중 active, E unit(ae-producer·e-paper·e-rt2) active, 평일 ET 03:30~16:30, PSI memory some avg10 ≥ 10, MemAvailable+SwapFree < 600MiB. 결정은 journal과 `store/guard.jsonl`에 남음. skip된 날은 다음 실행이 따라잡음(매 실행이 launch부터 재계산하고 prefix 검증)
- cgroup: MemoryHigh=450M, MemoryMax=700M(실측 피크 바로 위로 잡지 않음), Nice=19, IOSchedulingClass=idle, CPUWeight=20, TimeoutStartSec=30min
- ET 기준: e-universe 00:20, grouped 00:40, splits 00:55, h-forward 01:10, **I 02:15**, ae-producer 03:45. 모두 America/New_York라 DST에도 간격 유지

## 운영 반영 절차 (사용자 승인 후)

1. 커밋·push(현재 I 변경분 미커밋) → 서버 `/root/usb` pull
2. 서버 preflight(디스크, ctx receipt)
3. history 업로드: tar 전송 → `/root/usb_runtime/strategy_i_paper/history/grouped` 압축 해제 → `sha256sum -c`
4. `run --launch 2026-10-30` 1회(launch.json 생성)
5. unit·timer 설치 → `systemctl start usb-i-paper.service` 1회 수동 확인(MemoryPeak 기록) → timer enable

## Rollback

- `systemctl disable --now usb-i-paper.timer`, unit 파일 2개 삭제, `daemon-reload`
- store는 지우지 않고 `strategy_i_paper/store` → `store.rolledback_<ts>`로 이동(증거 보존)
- history 디렉터리는 I 전용이라 삭제해도 다른 전략 영향 없음
- 코드: I 패키지만 바뀌었고 다른 전략 파일은 수정하지 않음 → 해당 커밋 revert

## 남은 일

- 분기 snapshot collector 구현(2027-01-29 신호 전)
- overlay용 FORWARD_PIT SIC snapshot collector 없음 → overlay는 NOT_EVALUATED 기록만(장부 영향 없음, 기존 설계대로)
- 서버 첫 수동 실행에서 실제 MemoryPeak·wall 측정 후 MemoryHigh 재검토
