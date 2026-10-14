# US-B CRYPTO D2 Data Foundation 실행 보고 V1

작성 2026-09-23. **이 문서는 증거다.** Bybit Mainnet PUBLIC API만 사용했고 계정·KYC·API key·
private endpoint·주문 endpoint는 한 번도 쓰지 않았다. Edge/Score/Probability/AUTO/UI/Paper Engine은
구현하지 않았다. commit·push하지 않았다.

> **실행 연속성 기록**: D2 작업은 2026-09-23 01:26 UTC에 다른 에이전트 세션에서 시작됐고, mark/index
> 재수집과 realtime smoke가 진행 중이던 01:48 UTC에 그 세션이 사용량 한도로 중단됐다. 수집 프로세스는
> 살아남았고, 이 세션이 01:50 UTC부터 같은 작업 트리에서 이어받았다. 아래 값은 전부 이 저장소의
> 실제 산출물에서 다시 읽은 것이다.

---

## A. Git preflight

| 항목 | 값 |
|---|---|
| branch | `main` |
| D2 시작 시 HEAD | `9984260` (Strategy E-MAX E-RT2.1 + E-RT3) |
| D2 종료 시 HEAD | `a774047` |
| 작업 중 HEAD 이동 | **5회** |

HEAD 이동 내역과 영향 판정:

| 순서 | 커밋 | 제목 | crypto 영향 |
|---|---|---|---|
| 1 | `fced561` | Strategy E-MAX E-RT3 RVOL 상시 수집 워커(serve) | 없음 |
| 2 | `c68343c` | Strategy E-MAX V1 provisional paper 런타임 | 없음 |
| 3 | `451afa0` | E-MAX paper 세션 기록 실패 방어 | 없음 |
| 4 | `f308ce7` | 다중 전략 운영 UI(A + E-MAX V1) + E 유니버스 서버 빌드 | 없음 |
| 5 | `a774047` | E UI 마감 | 없음 |

다섯 커밋이 건드린 파일은 `backend/app/strategy_e_max_rt/*`, `backend/app/dev/run_e_rt*.py`,
`backend/tests/strategy_e_max/*`, `frontend/*` 뿐이다. **crypto 모듈·문서·데이터와 겹치는 파일은
0개**이므로 이 보고의 판정은 전부 유효하다.

> 이 저장소는 다른 세션이 동시에 작업 중이다. 회귀 테스트를 돌린 시점의 HEAD는 `451afa0`이었고,
> 보고서 작성 완료 시점은 `a774047`이다. 그 사이 두 커밋은 frontend와 `strategy_e_max_rt`만
> 건드렸으므로 `backend/tests` 결과를 무효화하지 않는다.

D2가 만든 것은 전부 untracked이며 기존 dirty 파일은 하나도 건드리지 않았다.

```
?? backend/app/crypto/
?? backend/app/dev/collect_crypto_d2.py
?? backend/tests/crypto/
?? docs/crypto/
```

`data/runtime/`은 gitignore 대상이라 git status에 나타나지 않는다.

### D1 재검증

D1 산출물(`data/runtime/crypto_preflight/` 15개)과 `docs/crypto/*.md` 7편을 읽고 D1 판정을 뒤집는
새 사실이 있는지 확인했다. **D1 판정을 바꿀 사실은 없었다.** D2가 발견한 것은 D1의 오류가 아니라
**D2 구현의 상수 오타**였다(아래 E.4).

---

## B. Risk limit findings

전문은 `docs/crypto/CRYPTO_RISK_LIMIT_FINDINGS_V1.md`.

| 항목 | 결과 |
|---|---|
| tier 수 | **35** (riskId 1 ~ 35) |
| notional 범위 | `(0, 300,000]` ~ `(800,000,000, 1,200,000,000]` |
| maxLeverage | 150.00 -> 1.00, 단조 비증가 |
| IM / MM | 0.0066 / 0.0033 -> 1 / 0.6, 단조 비감소 |
| tier ordering | PASS |
| margin monotonicity | PASS |
| max leverage consistency | PASS. D1 `leverageFilter.maxLeverage = 150.00`과 일치 |
| 경계 해석 | `(직전 riskLimitValue, 현재 riskLimitValue]` |
| 원시 응답 sha256 | `cf4b90de8d2f2895a6984795299e1e13eac1f3d0f556b35ce14233118bf35818` |

발견 2건:

1. `mmDeduction`이 tier 1에서 **빈 문자열**로 온다. 숫자 변환을 강제하면 깨진다. 파서는 `None`으로 보존.
2. `IM x maxLeverage`가 정확히 1이 아니다. tier 1에서 편차 **0.01**(0.0066 x 150 = 0.99).
   어느 쪽이 체결 판정의 authority인지는 공개 endpoint로 결정할 수 없어 **UNKNOWN**이다.

Paper liquidation engine용 schema contract는 문서 4절에 계약으로만 적었고 **엔진은 구현하지 않았다.**

---

## C. 동결된 정책

전문은 `docs/crypto/CRYPTO_D2_POLICY_FREEZE_V1.md`.

| 정책 | 핵심 | 상태 |
|---|---|---|
| P1 Virtual KRW | run 시작 시 FX 1회 고정, run 중 변동은 전략 PnL 미반영, **D2는 환율 숫자 0개 하드코딩** | 계약만 정의 |
| P2 publicTrade | raw 영구 보존, primary identity = `execId`, **`seq`는 unique key 금지** | 구현 완료 |
| P3 orderbook | depth50 raw delta 장기보존 금지, local book 유지, **1초 feature만 영구 보존**, gap/reconnect 시 book reset | 구현 완료 |
| P4 REST rate limit | 8 req/s limiter, 지수 backoff + jitter, 429·retry telemetry를 manifest에 기록, **극한 stress test 금지** | 구현 완료 |
| P5 저장 계층 | immutable raw / derived / manifest / quarantine 구분, dataset manifest와 stream manifest 분리 | 구현 완료 |
| P6 tickers 병합 | snapshot 교체 + delta 병합, 병합 상태만 저장, snapshot 없는 delta는 저장 금지, reconnect 시 state 폐기 | 구현 완료 |

P6는 D2 실행 중 발견한 결함에서 나온 정책이다. 상세는 F절.

---

## D. Historical collector

`backend/app/crypto/` (8 모듈, 685 LOC). 기존 equity writer lock에 **의존하지 않는다.**

| 요구 | 구현 | 위치 |
|---|---|---|
| start/end explicit | `--start`/`--end` 필수, `--start` 생략 시 D1 실측 earliest | `historical.py::collect_series` |
| pagination/cursor | 창 단위 조회 후 `nextPageCursor` 소진, 반복 cursor 감지 시 실패 | `historical.py::iter_window` |
| coverage 검증 | 창 밖 timestamp 반환 시 `CoverageError` | `collect_series` |
| silent truncation 감지 | 반환 first timestamp > 요청/실측 earliest + 1 interval이면 실패 | `collect_series` |
| retry/backoff | 지수 + jitter, 상한 30초, 5회 | `rest.py::BybitPublicClient.get` |
| rate limiter | 8 req/s 토큰 간격 | `rest.py::RateLimiter` |
| UTC only | 입력은 tz 필수, 저장은 epoch ms | `dev/collect_crypto_d2.py::utc_ms` |
| Decimal-safe | 문자열 보존, float 경유 금지, 비유한값 거부 | `models.py::decimal_string` |
| deterministic schema | 데이터셋별 고정 키, 정렬된 JSON 직렬화 | `models.py::normalize_row`, `storage.py::canonical_line` |
| atomic finalize | `.partial` -> `os.replace` | `storage.py::JsonlDatasetWriter.finalize` |
| sha256 | 최종 파일 전체 해시 | `storage.py::sha256_file` |
| manifest | crypto 전용 `manifest/` | `storage.py::write_manifest` |
| restart/resume | `.partial`을 읽어 본 timestamp 복원 후 이어서 | `JsonlDatasetWriter.resume_state` |
| duplicate-safe | timestamp 집합 기반, 중복은 버리고 카운트 | `collect_series` |
| no silent overwrite | 최종 파일/manifest가 이미 있으면 `FileExistsError` | `storage.py` |

OI 전용: `intervalTime=5min`으로 200행 창(= 1000분) 단위 bounded pagination + cursor follow +
first/last timestamp 검증. **요청 start보다 반환 first timestamp가 늦으면 실패**시킨다.

---

## E. 전체 이력 수집 결과

수집 기준 end: `2026-09-23T01:33:59.999Z`. 5개 데이터셋 전부 **status=COMPLETE**.

| 데이터셋 | rows | first (UTC) | last (UTC) | missing | duplicates | requests | retries | 429 |
|---|---|---|---|---|---|---|---|---|
| `kline_1m` | 3,416,578 | 2020-03-25T10:36:00Z | 2026-09-23T01:33:00Z | **0** | 0 | 2,750 | 4 | 0 |
| `funding` | 7,118 | 2020-03-25T16:00:00Z | 2026-09-23T00:00:00Z | 0 | 0 | 36 | 0 | 0 |
| `open_interest_5m` | 645,329 | 2020-08-04T07:50:00Z | 2026-09-23T01:30:00Z | **4** | 0 | 6,452 | 0 | 0 |
| `mark_1m` | 3,416,693 | 2020-03-25T08:41:00Z | 2026-09-23T01:33:00Z | **0** | 0 | 3,417 | 0 | 0 |
| `index_1m` | 3,416,737 | 2020-03-25T07:57:00Z | 2026-09-23T01:33:00Z | **0** | 0 | 3,417 | 0 | 0 |

sha256:

| 데이터셋 | sha256 |
|---|---|
| `kline_1m` | `994beac8a84bc770c51b8644567a1807ca1625e9696b1a40874f372f3f1dc628` |
| `funding` | `6a6c56f0000ec671dd21e5a3afa8ad5981b14ea84f3e9e9b186112d0f7f3c508` |
| `open_interest_5m` | `1409f63c5a8769e0e4e987af49ec3cd72b0963f36d02fbdf306ef73a8f29d403` |
| `mark_1m` | `c930e017bcf4700e158d34a503557855253a4f229ad12b09b78fce173fcf0104` |
| `index_1m` | `b9750cd436c2a5d82f2ca3b9da8bada82be712fe4cc9f31b84ff07941ea2734e` |

> **telemetry 해석 주의**: manifest의 `requests`/`retries`/`rate_limits`는 **최종 프로세스 1회분**이다.
> `kline_1m`은 provider transient error(`retCode=10016`) 이후 `.partial`에서 resume했으므로
> 2,750은 재개 후 요청 수이며 총 요청 수가 아니다(창 수 기준 총량은 약 3,420). 누적 telemetry는
> D3에서 manifest에 합산 기록해야 한다.

### E.1 행 수 교차검증

세 1분 시계열의 행 수 차이가 시작 시각 차이와 **정확히** 일치한다.

- `index_1m` - `kline_1m` = 3,416,737 - 3,416,578 = **159** = 07:57 ~ 10:36 사이 159분
- `mark_1m` - `kline_1m` = 3,416,693 - 3,416,578 = **115** = 08:41 ~ 10:36 사이 115분

세 시계열 모두 내부 1분 간격 위반 0건, invalid OHLC 0건이다. 독립적으로 수집된 세 파일이
서로를 검증한다.

### E.2 Kline: launchTime vs actual earliest

| 항목 | 값 |
|---|---|
| instrument `launchTime` (D1) | 2020-03-15T00:00:00Z |
| 1m kline actual earliest | **2020-03-25T10:36:00Z** |
| 차이 | **10.44일** |

launchTime을 그대로 수집 시작점으로 쓰면 존재하지 않는 구간을 요청하게 된다. 수집기는 D1에서
실측한 earliest를 `KNOWN_EARLIEST_MS` 상수로 들고 있다.

### E.3 Funding: 현재 cap 밖 과거 값

2건이 현재 `fundingCap = 0.00333`을 넘는다.

| 시각 | funding rate |
|---|---|
| 2020-04-03T08:00:00Z | **-0.00375** |
| 2021-01-08T08:00:00Z | **+0.00375** |

D1 R3의 확인이다. **오류로 처리하지 않고 source truth로 보존했다.** 당시 cap을 알 수 없으므로
과거 데이터를 현재 cap으로 검증하면 오탐이 난다. 8시간 간격 위반은 7,118건 중 **0건**이다.

### E.4 Mark/Index 경계 상수 오타와 quarantine

수집 중 D1 문서를 재대조하다가 **D2 구현의 epoch 상수가 틀린 것**을 발견했다. D1의 실측값은
mark `2020-03-25T08:41:00Z`, index `2020-03-25T07:57:00Z`인데 초기 상수가 각각 어긋나 있었고,
그 결과 index가 앞쪽 44분을 건너뛴 채 수집됐다.

조치: 잘못 만들어진 산출물을 **삭제하지 않고** `quarantine/d2_boundary_constant_error/`로 옮겨
증거로 보존한 뒤, 교정된 경계로 mark/index를 **전량 재수집**했다. 위 E 표의 값은 재수집분이다.
kline/funding/OI는 영향받지 않았다.

quarantine 보존물(총 664 MiB): 잘못된 index final + 그 manifest + 잘못된 mark partial + README.

> 이 사건은 D1 판정이 틀렸다는 증거가 아니라, **문서의 실측값을 코드 상수로 옮길 때 대조가
> 필요하다**는 증거다. 지금은 `KNOWN_EARLIEST_MS`의 mark/index 값이 테스트로 고정돼 있다.

### E.5 OI 결손 1건

| 구간 | 결손 |
|---|---|
| 2021-02-08T13:15:00Z -> 2021-02-08T13:40:00Z | 5분 포인트 **4개** |

전체 645,329행 중 4개다. cursor 절삭이 아니라 provider 쪽 공백이다(전후 구간 연속, 중복 0,
zero-OI 0). 수집기의 truncation 검사는 시작점 기준이며 이 구간은 통과한다.

---

## F. Realtime collector

4개 PUBLIC topic을 구독한다: `publicTrade.BTCUSDT`, `orderbook.50.BTCUSDT`, `kline.1.BTCUSDT`,
`tickers.BTCUSDT`. ping/pong은 `websockets`의 `ping_interval=20` / `ping_timeout=20`에 위임한다.

### F.1 수행한 런 3회

| 런 | 시각(KST) | 길이 | 코드 | 종료 | 비고 |
|---|---|---|---|---|---|
| smoke 1 | 10:46 ~ 10:49 | 180초 | 초기 | duration 만료 | ticker 결함을 드러낸 런 |
| smoke 2 | 10:52 ~ 11:16 | **24분 14초** | 초기 | **SIGTERM** | clean shutdown 검증. 76,270 메시지 |
| **gate** | **11:31 ~ 12:00** | **30분(1,800초)** | **최종** | duration 만료 | **아래 수치의 출처** |

smoke 2는 SIGTERM으로 끊었을 때 writer 4개가 전부 정상 finalize되는지 보려고 일부러 중간에 종료했다.
그 사이 발견한 결함 때문에 코드가 바뀌었으므로, **게이트 판정은 최종 코드로 돌린 gate 런만 사용한다.**

중단된 런이 1회 더 있다. 11:16 ~ 11:29 세션은 제어하던 에이전트 세션이 끝나면서 12.7분 만에
죽었고 finalize되지 못했다. 삭제하지 않고 `quarantine/d2_interrupted_session_1790129795/`에
증거로 남겼다(8.5 MiB, manifest 없음).

### F.2 gate 런 telemetry (1,800초, 최종 코드)

```
RealtimeTelemetry(reconnects=0, malformed=0, book_gaps=0, duplicate_trades=0,
                  book_resyncs=0, ticker_deltas_without_snapshot=0, messages=72995)
```

| 항목 | 값 |
|---|---|
| 총 메시지 | 72,995 (약 40.6 msg/s) |
| reconnects | **0** |
| malformed | **0** |
| book_gaps / book_resyncs | **0 / 0** |
| duplicate trades | **0** |
| snapshot 없는 ticker delta | **0** |

session_id `1790130665`. 산출물 4종과 stream manifest 4종이 같은 session_id를 공유한다.

### F.3 산출물 검증

| 스트림 | 행 | manifest 일치 | checksum 재대조 | 관측 span |
|---|---|---|---|---|
| `realtime/public_trade` | 17,939 | 일치 | **MATCH** | 1,739.8초 |
| `features/orderbook_1s` | 1,742 | 일치 | **MATCH** | 1,742.1초 |
| `features/finalized_kline` | 29 | 일치 | **MATCH** | 1,680.0초 |
| `features/ticker_state` | 10,159 | 일치 | **MATCH** | 1,743.6초 |

**publicTrade**: 17,939행, unique `execId` 17,939개, 중복 저장 0.
lag(receive - message) min 36ms / median 581ms / max 1,961ms.

**orderbook 1s feature**: 1,742행, 같은 초 중복 0, 커버리지 **1,742 / 1,743초**.
불변식 위반 0건: 교차호가 0, `microprice`가 best bid/ask 밖 0, `imbalance_20`이 [-1, 1] 밖 0.
관측된 spread는 전 구간 **0.10 = 정확히 1 tick** 하나뿐이다(D1의 측정과 일치).
lag min 37ms / median 560ms / p95 1,041ms / max 1,926ms.

결손 1초는 `2026-09-23T02:43:46Z`다. 앞뒤 행이 각각 `02:43:45.188`과 `02:43:47.188`로 정확히
2,000ms 떨어져 있고 book_gap도 reconnect도 0이므로, 우리 쪽 누락이 아니라 **그 초에 provider가
depth 메시지를 한 건도 보내지 않은 것**이다.

**finalized kline**: 29바 전부 `confirm=true`. `confirm=false` 저장 0, 중복 start 0,
1분 간격 위반 0.

**ticker state**: 10,159행(snapshot 1 + delta 10,158). 필수 7개 필드
(`markPrice`, `indexPrice`, `fundingRate`, `nextFundingTime`, `openInterest`, `volume24h`,
`turnover24h`) 전부 **10,159 / 10,159 non-null**.

### F.4 D2가 고친 realtime 결함 4건

전부 이번 실행 중 실측으로 드러난 것이다.

| # | 결함 | 증거 | 조치 |
|---|---|---|---|
| 1 | **ticker delta 병합 누락** | 1차 smoke의 ticker 1,371행 중 `markPrice`가 있는 행이 **134개뿐**. 나머지는 null | snapshot 교체 + delta 병합. `crypto.ticker.v2`. gate 런에서 10,159/10,159 non-null |
| 2 | **orderbook gap 후 재구독 없음** | Bybit는 새 subscribe에만 snapshot을 보낸다. gap 후 가만히 있으면 feature 스트림이 **조용히 영구 정지** | gap 시 orderbook topic만 unsubscribe -> subscribe. `book_resyncs` telemetry 추가 |
| 3 | **realtime 산출물에 manifest 없음** | provider·checksum·수집기 버전 추적 불가. G절 요구 위반 | `write_stream_manifest` 추가. 세션당 4개 |
| 4 | **세션 파일 4개의 접미사가 갈릴 수 있음** | `int(time.time())`를 writer마다 따로 호출. 초 경계를 넘으면 한 세션의 파일을 묶을 수 없다 | `session_id` 1회 계산 후 공유 |

realtime 밖에서 고친 것 2건:

| # | 결함 | 조치 |
|---|---|---|
| 5 | `.partial`의 마지막 줄이 하드 크래시로 잘렸을 때, resume 후 append가 그 줄에 **이어붙어 JSONL을 깨뜨림** | `resume_state`가 마지막 완전한 줄까지 truncate |
| 6 | `quality.analyze_kline`이 340만 행을 전부 메모리에 적재(이 머신 RAM 9 GiB, **OOM 위험**) | 월 단위 스트리밍 집계로 재작성. 전체 분석 17.7초 |

### F.5 검증하지 못한 것

- **실제 재연결은 관측되지 않았다.** gate 런 30분과 smoke 2의 24분 동안 `reconnects=0`이었다.
  재연결·재구독 경로는 **단위 테스트에서만** 검증됐다(`test_reconnect_resubscribes_and_resets_local_book`).
  실제 연결 단절 하에서의 동작은 **UNKNOWN**이다.
- **book_gap도 관측되지 않았다.** 따라서 실제 gap 상황에서의 resync 왕복도 단위 테스트 증거뿐이다.
- 수 시간 이상 연속 운용은 하지 않았다(D1 R10 그대로 미해소).

---

## G. 저장 계층과 용량

```
data/runtime/crypto/BTCUSDT/
  historical/{kline_1m,funding,open_interest_5m,mark_1m,index_1m}/   immutable raw
  realtime/public_trade/                                            immutable raw
  features/{orderbook_1s,finalized_kline,ticker_state}/             derived
  manifest/                                                          출처 증거
  quality/                                                           품질 리포트
  reference/                                                         risk-limit 원시 응답
  quarantine/                                                        결함 산출물 (사용 금지)
```

| 계층 | 용량 |
|---|---|
| historical (5종 전체 이력) | **1.2 GiB** |
| realtime (raw trade, 런 3회분) | 16 MiB |
| features (orderbook 1s / kline / ticker, 런 3회분) | 11 MiB |
| manifest | 40 KiB |
| quality | 44 KiB |
| reference | 12 KiB |
| quarantine | **673 MiB** (삭제 가능, 사용자 확인 대기) |
| 합계 | **1.9 GiB** |

quarantine 내역: `d2_boundary_constant_error` 664 MiB(E.4), `d2_interrupted_session_1790129795`
8.5 MiB(F.1).

데이터셋별: `kline_1m` 454 MiB, `index_1m` 335 MiB, `mark_1m` 330 MiB,
`open_interest_5m` 40 MiB, `funding` 404 KiB.

모든 영구 파일은 manifest로 `provider`, `market`, `symbol`, `dataset`, `schema_version`,
`collector_version`, `requested_start/end_ms`, `actual_start/end_ms`, `generated_at`,
`relative_path`, `row_count`, `checksum`, `status`, `requests`, `retries`, `rate_limits`를
추적할 수 있다.

---

## H. 데이터 품질과 연구 구간 후보

전체 리포트: `data/runtime/crypto/BTCUSDT/quality/kline_1m_monthly.json` (79개월 전수)

| 연도 | 월수 | bars_expected | bars_actual | missing | duplicate | invalid OHLC | zero-volume | 월 최대 zero-vol 비율 |
|---|---|---|---|---|---|---|---|---|
| 2020 | 10 | 405,444 | 405,444 | 0 | 0 | 0 | 28,505 | 0.164919 |
| 2021 | 12 | 525,600 | 525,600 | 0 | 0 | 0 | 52 | 0.000538 |
| 2022 | 12 | 525,600 | 525,600 | 0 | 0 | 0 | 39 | 0.000903 |
| 2023 | 12 | 525,600 | 525,600 | 0 | 0 | 0 | 1 | 0.000023 |
| 2024 | 12 | 527,040 | 527,040 | 0 | 0 | 0 | 0 | 0 |
| 2025 | 12 | 525,600 | 525,600 | 0 | 0 | 0 | 0 | 0 |
| 2026 | 9 | 381,694 | 381,694 | 0 | 0 | 0 | 0 | 0 |

**79개월 전체에서 missing 0, duplicate 0, invalid OHLC 0이다.** 품질을 가르는 유일한 축은
zero-volume이다.

2020년 월별 zero-volume 비율은 3.6% ~ 16.5%로 높고, 2020-11에 1.4%로 떨어진 뒤
2021-01에 **0.054%**로 급감한다. 2021-01 이후 zero-volume이 남은 달은 6개뿐이다.

| 월 | zero-volume | 비율 |
|---|---|---|
| 2021-01 | 24 | 0.000538 |
| 2021-04 | 15 | 0.000347 |
| 2021-05 | 7 | 0.000157 |
| 2021-06 | 6 | 0.000139 |
| 2022-04 | 39 | 0.000903 |
| 2023-09 | 1 | 0.000023 |

2023-10 이후는 전 구간 zero-volume **0**이다.

### H.1 연구 구간 후보 (기계적 산출, 자동 확정 아님)

| 규칙 | 후보 |
|---|---|
| 엄격: zero-volume이 **정확히 0**인 최초의 완전한 달 | **2021-02** |
| 지속: zero-volume 비율 <= 0.001인 완전한 달이 3개월 연속 시작되는 달 | **2021-01** |

두 규칙 모두 missing/duplicate/invalid OHLC가 0인 완전한 달만 후보로 본다.

**확정하지 않는다.** 근거와 함께 권고만 제출한다. 선택은 연구 설계의 몫이다.

### H.2 데이터셋 공통 overlap window

| 항목 | 값 |
|---|---|
| 공통 시작 | **2020-08-04T07:50:00Z** (제약: `open_interest_5m`) |
| 공통 끝 | **2026-09-23T00:00:00Z** (제약: `funding`) |

산출물: `data/runtime/crypto/BTCUSDT/quality/overlap_window.json`

공통 시작은 OI가 결정하고(D1 Q4 확인), 공통 끝은 8시간 주기인 funding이 결정한다.
H.1의 후보(2021-01 또는 2021-02)는 이 overlap window **안에 있다.** 즉 OI 제약이
연구 구간 선택을 추가로 제한하지 않는다.

---

## I. 테스트

### I.1 신규 테스트 (`backend/tests/crypto/test_crypto_d2.py`, 13개 전부 통과)

| 요구 항목 | 테스트 | 무엇을 실제로 잡는가 |
|---|---|---|
| pagination | `test_cursor_pagination_duplicate_safe_manifest_and_checksum` | cursor 소진, 중복 timestamp 제거, manifest row_count와 sha256 일치 |
| OI truncation detection | `test_oi_silent_truncation_detected` | 요청 start보다 늦은 first timestamp를 조용히 받아들이지 않고 `CoverageError` |
| resume | `test_resume_and_no_silent_overwrite` | `.partial`에서 본 timestamp 복원, 최종 파일 존재 시 덮어쓰기 거부 |
| resume (torn write) | `test_resume_truncates_a_torn_trailing_line` | 크래시로 잘린 마지막 줄을 잘라내고 재개. 이어붙어 JSONL이 깨지지 않는다 |
| duplicate detection | 위 pagination 테스트 + `test_trade_execid_kline_confirm_and_one_second_feature` | REST 중복 timestamp, WS 중복 `execId` |
| manifest/checksum | pagination 테스트 + `test_reconnect_resubscribes_and_resets_local_book` | dataset manifest와 stream manifest 양쪽 |
| Decimal parsing | `test_decimal_parsing_is_exact_and_rejects_non_finite` | 18자리 소수 무손실, `NaN` 거부, D1 경계 상수 고정 |
| orderbook snapshot/delta | `test_orderbook_snapshot_delta_features_and_gap` | snapshot 후 delta 적용, mid/microprice 실제 값 대조 |
| gap/resync | 위 테스트 + reconnect 테스트 | `u` 불연속 시 book 폐기, **unsubscribe -> subscribe 재구독 프레임 전송 확인** |
| trade execId uniqueness | `test_trade_execid_kline_confirm_and_one_second_feature` | 같은 `execId` 2건 중 1건만 저장 |
| kline confirm behavior | 같은 테스트 | `confirm=false` 바는 저장되지 않는다 |
| 1s feature aggregation | 같은 테스트 | 같은 초의 두 번째 갱신은 새 행을 만들지 않는다 |
| reconnect | `test_reconnect_resubscribes_and_resets_local_book` | 연결이 끊긴 뒤 재구독, book reset, 재연결 직후 delta 거부 |
| risk-limit parser | `test_risk_tier_parser_and_monotonicity` | 경계 파생, MM 역전 시 실패 |
| rate limit telemetry | `test_retry_and_rate_limit_telemetry` | 429 -> retry 1회 -> 성공, telemetry 카운터 |
| ticker 병합 | `test_ticker_delta_merges_into_snapshot_state` | delta가 snapshot 상태에 병합되고, snapshot 없는 delta는 저장되지 않는다 |
| quality 스트리밍 | `test_quality_report_streams_months_and_flags_zero_volume` | 월별 집계, zero-volume/invalid OHLC 카운트, overlap window 제약 데이터셋 |
| 세션 추적성 | `test_realtime_session_id_is_shared_by_every_writer` | 한 세션의 파일 4개가 같은 `session_id` |

### I.2 기존 테스트 회귀

```
3788 passed, 23 skipped, 16 warnings in 859.55s (0:14:19)
```

- 실행: `PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests -p no:randomly`
  `--ignore=backend/tests/strategy_b/test_strategy_b_scanner.py`
  `--ignore=backend/tests/test_strategy_b_historical_scanner.py`
- **실패 0건.** crypto 추가·수정으로 인한 회귀는 없다.
- 제외한 2개 파일은 **D2 이전부터 collection error** 상태인 untracked Strategy B 테스트다.
  `--ignore` 없이 돌리면 이 2건이 먼저 수집을 중단시킨다. D2가 만든 문제가 아니며 건드리지 않았다.
- 사용자 미커밋 작업(수정 17 / untracked 다수)은 **한 줄도 수정하지 않았다.** D2가 만든 파일만 추가했다.
- 회귀 범위: `backend/tests` 전체(위 2개 제외). 프런트엔드 테스트는 이번 범위 밖이다.

---

## J. 위험과 UNKNOWN

| # | 항목 | 상태 |
|---|---|---|
| D2-R1 | `IM x maxLeverage != 1`. 체결 판정 authority | **UNKNOWN** |
| D2-R2 | `mmDeduction`을 쓰는 MM 공식 | **UNKNOWN** |
| D2-R3 | risk tier 변경 이력 | **UNKNOWN**. 현재 스냅샷만 획득 가능 |
| D2-R4 | REST rate limit 실제 한도 | **UNKNOWN**. 의도적으로 시험하지 않음(P4) |
| D2-R5 | WS `lag_ms`는 네트워크 지연과 로컬 시계 오차의 **분해되지 않은 합** | 확정(D1 R4) |
| D2-R6 | orderbook raw를 버리는 결정은 되돌릴 수 없다 | 명시적 수용(P3) |
| D2-R7 | 과거 개별 체결 이력은 공개 REST에 없다 | 확정. 지금부터 모으는 수밖에 없다 |
| D2-R8 | 1초 feature가 신호에 충분한지 | **UNKNOWN**. 신호 설계 후에만 답 가능 |
| D2-R9 | `tickers`는 현재 append-only 스냅샷이며 delta 병합 재동기 로직이 없다 | 확정. D3 범위 |
| D2-R10 | 과거 구간에 현재 risk tier를 적용하는 backtest | 명시적 수용. 결과에 기재 필요 |
| D2-R11 | manifest telemetry가 **resume 후 프로세스 1회분**만 기록한다 | 확정. D3에서 누적 합산 |
| D2-R12 | realtime `execId` 중복 판정 집합이 무한 증가한다(장기 24/7 운용 시 메모리) | 확정. 45분 런에서는 무해. D3 supervisor 범위 |

---

## K. D2 판정

| # | 조건 | 판정 | 근거 |
|---|---|---|---|
| 1 | risk-limit tiers 확보 | **PASS** | 35 tier 전수, ordering·monotonicity·D1 일치 검증 |
| 2 | historical full collection 성공 | **PASS** | 5종 전부 status=COMPLETE, 10,902,455행 |
| 3 | manifest/checksum 재현 가능 | **PASS** | 5종 재계산 sha256 전부 MATCH, row_count 전부 일치 |
| 4 | realtime 최소 장시간 smoke run 성공 | **PASS** | 최종 코드 30분 무중단, 72,995 메시지, 이상 telemetry 전부 0 |
| 5 | reconnect/resync 테스트 PASS | **PASS (단위 한정)** | 단위 테스트 통과. **실제 재연결·gap은 관측되지 않음**(F.5) |
| 6 | 월별 데이터 품질 리포트 생성 | **PASS** | 79개월 전수, `quality/kline_1m_monthly.json` |
| 7 | research-window 후보 생성 | **PASS** | 2021-02(엄격) / 2021-01(지속). 자동 확정하지 않음 |
| 8 | account/API key/private endpoint/실주문 사용 0 | **PASS** | PUBLIC endpoint만 사용. 인증 0회 |
| 9 | 기존 A~E 회귀 없음 | **PASS** | 3,788 passed / 23 skipped / **실패 0** |
| 10 | Edge/Paper/UI 구현 0 | **PASS** | crypto 모듈은 저장소의 다른 `app.*`을 하나도 import하지 않는다. 역참조도 0 |

## **D2 = PASS**

10개 조건을 전부 충족한다. 다만 조건 5는 **단위 테스트 수준의 PASS**다. 30분과 24분 연속 운용에서
재연결이 한 번도 일어나지 않아, 실제 단절 상황의 복구 경로는 실측되지 않았다. 이 한계를 D3의
장기 운용 supervisor에서 해소한다.

commit·push하지 않았다. 산출물은 전부 untracked이거나 gitignore 대상이다.

---

## L. D3에서 구현할 정확한 범위

D3는 **데이터 기반 위에 연구 계층을 올리는 단계**다. 아래를 넘지 않는다.

**포함**

1. `tickers` delta 병합과 주기적 REST 재동기(D2-R9 해소). 현재는 스냅샷 append뿐이다.
2. 장기 연속 운용 collector supervisor: 재시작, 일자별 파일 롤오버, 디스크 예산 감시,
   reconnect/malformed/gap telemetry의 영속 기록.
3. historical + realtime을 하나의 시간축으로 잇는 **read-only loader**. PIT 위반 검사 포함.
4. 연구 구간 **확정 절차**: H.1 후보 중 하나를 사용자 결정으로 고정하고 사전등록.
5. mark/index/funding/OI를 kline 시간축에 정렬하는 join 계약과 그 결손 정책.
6. risk tier 스냅샷을 run parameter로 고정하는 메커니즘(계약은 B에 이미 동결).

**제외 (D3에서도 금지)**

- Edge / Score / Probability 산출
- AUTO 실행, 주문 경로, Paper execution engine
- UI
- 계정·API key·private endpoint
- 전략 파라미터 최적화

**선결 조건**

- H.1 연구 구간 후보 중 하나를 사용자가 선택해야 D3.4를 시작할 수 있다.
- quarantine 664 MiB 삭제 여부를 사용자가 결정해야 한다.
