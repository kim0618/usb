# Strategy D D1 Results V1 (Data / PIT Feasibility)

실행 2026-09-19 (집 PC). 판정 **D1 PASS**.

- run id `dpit1-2ec7d609bf56`, identity digest `2ec7d609bf56693b228abfeddf21597d1ef007966537516348e15fb4a0e8ca84`
- 산출물 `data/runtime/strategy_d/runs/dpit1-2ec7d609bf56/` (로컬, git-ignored): `run_identity.json`, `d1_report.json`, `run_context.json`, `COMPLETE.json`
- 규칙 정본 `d_analog_rules_v1.json`, canonical sha256 `680bf113…0cd3` **MATCH** (D0 이후 변경 0)
- 규칙 파일 sha256 `b6309ae4…52fd5` (D1 Pre-flight §1 기록값과 동일)
- Pre-flight 계약 sha256 `aa481281cd7e9d0585637d13a189fbc833a460fdd86d1d45905451af352d29b0` (§13 P4)
- 새 코드: `backend/app/backtest/strategy_d_analog/` (`config`, `models`, `identity`, `source`, `universe`, `d1`), CLI `app.dev.run_strategy_d_d1`, 테스트 `backend/tests/strategy_d/` 33개
- A/B/C 코드 변경 0. D0/D2 설계/Pre-flight 문서 변경 0.

> D1은 forward label을 한 번도 읽지 않는다. 이 단계에서 연구 결과(IC, 신호)가 보일 수 있는 경로가 없다.

---

## 1. 실행 전제 확인 (Pre-flight §13)

| # | 조건 | 결과 |
| --- | --- | --- |
| P1 | 공통 snapshot `FROZEN` + freeze grouped 파일이 공통 경로에서 sha256 일치 | **OK.** `USB-HIST-V1` status `FROZEN`, 읽은 510개 파일 전부 freeze 행 sha256 일치(G6). 공통 경로 501/501, C 로컬 fallback 0 |
| P2 | 공통화 수집/승격 프로세스 종료, 파일 쓰기 경합 없음 | **OK.** 이 PC에 collector 프로세스 없음. B 분봉 수집(`market_data/raw/massive/minute/`)은 미완이지만 D는 `grouped_daily`, `reference_tickers`, `splits`만 읽어 경로가 겹치지 않는다 |
| P3 | D0 checksum 재계산 MATCH | **OK** (`680bf113…`) |
| P4 | Pre-flight 문서 sha256을 run identity에 기록 | **OK** (`preflight_contract_sha256`) |

입력 경로는 이 PC의 Google Drive 마운트 `/mnt/g/내 드라이브/1_US-B`이며, `snapshot_id`는 CLI 인자로 명시했다. `CURRENT_HISTORICAL_SNAPSHOT.json` 포인터는 `run_context.json`에 기록만 하고 따라가지 않았다(Pre-flight §8.3).

---

## 2. B2 해소: Canonical Session Grid 확정

| 값 | 결과 |
| --- | --- |
| `grid_start` | **2024-09-17** (grid index 0) |
| `grid_end` | **2026-09-16** |
| `grid_count` (N) | **501** |
| `grid_digest` | `830744f64a2d6b8378609ad796313b03575fefb146068f2def602867e80166c9` |

G1~G8 (하나라도 실패하면 HARD FAIL R3, 전부 통과):

| # | 검사 | 결과 |
| --- | --- | --- |
| G1 | 모든 grid 날짜가 XNYS 거래일 | PASS (501/501) |
| G2 | 엄격 오름차순, 중복 없음 | PASS |
| G3 | 연속한 두 grid 날짜 사이 XNYS 세션 결손 0 | PASS |
| G4 | `status != OK` 세션이 grid 첫날보다 앞에만 존재 | PASS (`2024-09-16` 403 1건, grid 밖) |
| G5 | 파일 내 `session` == freeze 행 `session_date` == 파일명 | PASS (로딩 중 501회 검사) |
| G6 | 파일 sha256 == freeze 행 sha256 | PASS (grouped 501 + tickers 8 + splits 1 = 510회) |
| G7 | grid가 snapshot `start_date..end_date` 안 | PASS |
| G8 | `grid_count` == freeze `grouped_usable_sessions`, 범위 == `usable_range` | PASS (501, 2024-09-17..2026-09-16) |

D2 설계 §1이 freeze 메타데이터에서 옮겨 적었던 참고값(501세션, index 0 = 2024-09-17)과 같은 값이 **G1~G8 통과 뒤 확정값**이 되었다. Pre-flight §2.5대로 D1~D4 V1은 이 `(freeze_digest, grid_digest)` 하나에 묶인다.

---

## 3. Data Freeze Identity (Pre-flight §9.1)

| 필드 | 값 |
| --- | --- |
| `snapshot_id` | `USB-HIST-V1` |
| `snapshot_sha256` | `e2a8e5d67ce3b864…07be7` |
| `freeze_id` | `STRATEGY_C_RAW_FREEZE_V1` |
| `freeze_digest` | `9ebd6c29c66728ac…eafe` |
| `source_digest` (`c_raw_digest`) | `adc4f1919dd83577…32c02` |
| `d_read_digest` | `7e790a8dcf1db8ce…7d55` (D가 실제로 읽은 510개 파일) |
| `daily_authority` | `MASSIVE_GROUPED_DAILY` (단일 authority, `per_symbol_daily` 미사용) |
| `first/last/count` | 2024-09-17 / 2026-09-16 / 501 |
| `grid_digest` | `830744f6…66c9` |

`c_raw_freeze.json`은 자기 `files` 행으로 `freeze_digest`를 재계산해 일치를 확인했고, snapshot이 담은 `c_raw_freeze.freeze_digest`와도 일치한다.

---

## 4. 데이터셋 규모

| 항목 | 값 | 비고 |
| --- | --- | --- |
| Panel 티커 | 6,340 | 8개 분기 CS 스냅샷(거래소 5개 필터 후)의 합집합 |
| grouped 행 | 5,760,100 읽음 / 2,576,207 채택 | 나머지는 CS 스냅샷 밖 심볼(ETF, 워런트 등)이거나 OHLCV 결측 행 |
| 분기 스냅샷 | 8 (2024-10-01 ~ 2026-07-01) | |
| 분할 기록 | freeze 3,328행 -> 3,316 이벤트 | 같은 `(ticker, execution_date)` 중복 12건 제거, 비율 이상 0건 |
| 스냅샷 없는 앞쪽 날짜 | **10** (2024-09-17 ~ 2024-09-30) | 첫 스냅샷 `as_of = 2024-10-01`. 정상 제외(`NOT_MEMBER`)이며 라이브러리 최소 index 60보다 앞이라 영향 없음 |
| 읽은 위치 | 공통 501 / C 로컬 0 | |

C 패널이 보고한 6,341과 1 차이가 나는 이유는 C가 벤치마크 `SPY`를 패널에 따로 넣기 때문이다. `SPY`는 CS 스냅샷에 없고(ETF), D 규칙에 벤치마크가 없어 D 패널에는 들어가지 않는다.

---

## 5. B8 해소: grouped 중복 티커 행

**0건.** 501세션 5,760,100행에서 같은 세션에 같은 `T`가 두 번 나온 사례가 없다.

D 로더는 C `load_panel`처럼 조용히 덮어쓰지 않고 중복을 만나면 F3로 멈춘다(기본값). 측정 결과가 0이므로 D1은 BLOCKED가 아니며, 이 동작을 그대로 둔다. 측정용 `--allow-duplicate-rows` 경로는 남겨두되 D2~D4 실행에는 쓰지 않는다.

---

## 6. Universe 적격 규모 (query 날짜)

평가 구간은 D0 그대로 `[eval_start_idx, eval_end_idx] = [260, 480]`, **221일** (index 260 = 2025-10-01, index 480 = 2026-08-18).

| 항목 | 값 |
| --- | --- |
| 적격 ticker-date | **581,156** |
| 날짜당 적격 종목 | 최소 2,551 / 중앙값 2,619 / 평균 2,630 / 최대 2,756 |
| 적격 종목 < 300인 날짜 | **0** |
| 적격 종목 < 100(`min_valid_queries_per_date`)인 날짜 | **0** |

제외 사유 분포(첫 일치 우선, 합계 = 221일 x 6,340종목 = 1,401,140):

| 사유 | 수 | 비중 |
| --- | --- | --- |
| ELIGIBLE | 581,156 | 41.5% |
| LOW_ADV | 241,291 | 17.2% |
| NOT_MEMBER | 240,778 | 17.2% |
| LOW_PRICE | 222,724 | 15.9% |
| NO_HISTORY | 108,777 | 7.8% |
| SPLIT_WINDOW | 5,067 | 0.36% |
| CA_SUSPECT | 1,347 | 0.10% |

`NOT_MEMBER`는 상장 전/상폐 후와 거래소 밖을 모두 포함한다. 사유는 D2 설계 §6.2 표 순서로 하나만 부여하므로 위 분포는 겹치지 않는 분할이다.

---

## 7. Query 표본 feasibility

| 항목 | 값 | D0 GATE 조건 9 대비 |
| --- | --- | --- |
| 표본 window | 221일 x 300 = **66,300** | valid queries >= 20,000: label 유효성으로 최대 70%가 빠져도 충족 |
| 고유 표본 ticker | **3,331** | unique query tickers >= 1,000: 충족 |
| 평가일 수 | **221** | evaluable dates >= 150: 충족 |

표본 해시는 `Q|20260917|{D}|{ticker}` (Pre-flight §5.2) 그대로이며, 같은 입력에서 같은 300개가 나오는 것을 테스트로 고정했다. 조건 9의 실제 판정은 label 유효성이 붙는 D4 몫이고, D1은 상한만 기록한다.

---

## 8. Historical Pattern Library 규모

stride anchor 0, `d % 5 == 0`, 최소 `d = 60` (61봉 조건). 평가 상한 index 480까지 **85개 stride 날짜**.

| 항목 | 값 |
| --- | --- |
| 라이브러리 창 합계 | **216,243** |
| stride 날짜당 창 | 최소 2,361 / 중앙값 2,575 / 평균 2,544 / 최대 2,752 |

embargo(`d + h <= D - W`)를 적용했을 때 query 한 건이 실제로 보는 후보 수(라벨 유효성 적용 전 상한):

| 검정 | query index 260 (가장 이른 평가일) | query index 480 (마지막 평가일) |
| --- | --- | --- |
| W20 / h1 | 87,762 (36 stride 날짜) | 202,940 (80) |
| W40 / h10 | 75,281 (31) | 189,312 (75) |
| W60 / h20 | **60,295 (25 stride 날짜 = 125세션 span)** | 173,369 (69) |

가장 빡빡한 W60/h20 조합도 첫 평가일에 stride 날짜 25개(125세션)를 확보한다. D0 `min_library_span_sessions = 120`이 `eval_start_idx = 260`을 통해 의도한 바와 일치한다. Top-K = 50과 집중도 cap(종목 1, 날짜 5)을 감안해도 후보가 모자랄 구조적 이유는 없다. `INSUFFICIENT_NEIGHBORS` 실제 비율은 D2가 측정한다.

---

## 9. Stable Identity (FIGI) 결측률

| 항목 | 값 |
| --- | --- |
| 적격 ticker-date | 581,156 |
| `composite_figi` null | 53,422 |
| **null 비율** | **9.19%** |

D0 `figi_rule`대로 FIGI가 null이면 same-symbol 비교에서 **제외가 아니라 비교 생략**이다. ticker 문자열 일치 제외는 항상 적용되므로, FIGI null의 효과는 "종목명이 바뀐 같은 회사를 못 잡는" 알려진 한계(D0 `ticker_reuse_handling`)에 9.19%만큼의 노출이 남는다는 것이다. 규칙 변경 사유가 아니며 D2 이웃 산출물에 `figi_code = -1`로 기록된다.

---

## 10. 실행 비용 (B7)

| 항목 | 값 |
| --- | --- |
| D1 실행 시간 | 33.3초 (데이터 로드 29.5초 포함) |
| 이 PC 실측 행렬곱 속도 | 16.75 GFLOP/s (300 x 61 @ 61 x 20,000) |
| D2 정확 검색 총 연산 | 약 9,100 GFLOP (14검정 합, 라벨 유효성 적용 전 상한) |
| D2 예상 검색 시간 | **약 9분** (543초) |
| 라이브러리 행렬 메모리 | W20 36 MB / W40 71 MB / W60 106 MB (float64) |
| 가용 메모리 | 6.5 GB (실행 시점), swap 증가 없음 |

D2 RSS 예산 2.0 GB에 대해 여유가 크다. D0 §10이 추정한 검정당 약 10^12 flop 수준과 자릿수가 맞고(실측 합계 9.1 x 10^12), **계산이 버겁다는 이유로 규칙(K, stride, 표본 수, 정확 검색)을 바꿀 필요가 전혀 없다.**

---

## 11. D1이 내린 결정

### 11.1 B3: 20D label CA 확장 -> **Temporary** (`label_extension.py`)

D_REUSE_MATRIX §2.1의 두 안 중 Temporary를 택한다.

- 근거: C-M V1은 `GATE-C2 = FAIL`로 판정이 동결됐지만, C-V2A/B run과 C-V2C 설계가 진행 중이고 `strategy_c_selection/labels.py`는 그 run들의 `code_digest`에 들어간다. 지금 C를 고치면 C V2 재현성이 깨진다.
- 계약: D 패키지가 C `LabelSet`의 창(D..D+10) 결과와 **일치**하고 D+11..D+20에만 같은 비율 규칙을 추가한다는 것을 D 테스트로 고정한다(구현은 D3).
- Final(공통 `ca_horizon` 인자화)은 C V2까지 끝난 뒤 별도 작업으로 남긴다. 어느 쪽이든 D 규칙 JSON의 `labels.label_ca_suspect` 정의는 바뀌지 않는다.

### 11.2 B5: C 로컬 vs 공통 경로 Panel 동일성

이 PC에는 C 로컬 캐시(`data/runtime/strategy_c/raw`)가 없다(C 수집은 회사 PC에서 수행). 두 경로를 같은 로더로 읽어 배열을 비교하는 실측은 이 호스트에서 할 수 없다.

대신 **바이트 동일성으로 대체한다**: D는 두 위치를 구분하지 않는 단일 로더(`source.py`)를 쓰고, 읽은 파일은 freeze 행 sha256과 일치해야 한다(G6, 510/510 통과). freeze 행의 sha256은 C 로컬 파일에서 계산된 값이므로, 공통 파일이 그 값과 같다는 것은 곧 C 로컬 파일과 바이트가 같다는 뜻이고, 같은 바이트에 같은 함수를 적용한 배열은 같다. C `load_panel`과의 비교는 애초에 목적이 아니다(D는 중복 행 처리와 배치 레이아웃이 달라 의도적으로 자체 로더를 쓴다).

B5는 이 근거로 **해소**로 기록한다. C 로컬 캐시가 있는 PC에서 확인하고 싶다면 `--c-raw-root`로 같은 run을 한 번 더 돌리면 `d_read_digest`가 같아야 한다.

### 11.3 `strategy_c_selection.rules` import 예외 (D_REUSE_MATRIX §4의 D1 결정 사항)

**예외를 허용하지 않는다.** D `config.py`가 canonical checksum recipe(2줄)를 자체 구현하고, D 테스트가 C `canonical_checksum`과 같은 값을 내는지 고정한다(`test_checksum_recipe_equals_the_c_implementation`). 근거는 §11.1과 같다: C가 움직이는 동안 D identity를 C 모듈에 묶지 않는다. 비용은 2줄이고, 드리프트는 테스트가 막는다.

### 11.4 B6: run identity import 격리 (Pre-flight §7 확인)

D 전용 `identity.py`(stdlib + D `models`만 import)로 구현했다. D 패키지 전체를 import한 새 프로세스에서 `app.strategy*`, `app.strategy_b*`, `app.services*`, `app.risk*`, `app.broker*`, `app.backtest.engine.driver`, `app.backtest.engine.identity`가 **0개** 로드되는 것을 테스트로 고정했다.

### 11.5 close 기준 해석

D2 설계 §6.2의 기록대로 `min_close`는 **raw close(e)** 로 해석했다(C `features.hard_filter`와 같음). `P = close/F`는 grid 시작점부터 누적된 값이라 경제적 가격이 아니다. D1 보고서 명시 요구사항을 이 문서로 충족한다.

---

## 12. 테스트

`backend/tests/strategy_d/` 33개 전부 통과 (합성 데이터만 사용, 네트워크/Drive 접근 0).

| 묶음 | 내용 |
| --- | --- |
| 데이터 계약 | 스냅샷 행이 `type=CS`, `active=true`가 아니면 패널에 들어오지 않음 |
| 규칙/identity | 선언 checksum 일치, 규칙 수정 거부, C recipe 동치, query/N1 해시 벡터, 표본 결정성, identity에서 시각/호스트 배제, float payload 거부, code digest |
| import 경계 | AST 금지 prefix, `identity.py` stdlib 한정, `source.py` 외 저장소 import 금지, 전이 로드 0 |
| grid/freeze | G1~G8 통과, 세션 결손(G3), grid 내부 unusable(G4), 세션 라벨 불일치(G5), sha256 불일치(G6/R2), 다른 dataset 거부(R2), 미동결 snapshot 거부, 중복 행 F3 + 측정 모드 |
| universe | 사유별 witness, 분할 창, 결측 봉, 스냅샷 밖 종목, stride anchor |
| PIT | as-of view 밖 접근 차단, 미래 봉 변조 무영향(D0 PIT #2), 미래 분할 주입 무영향(#3), 절단 재실행 bit 동일(#1) |
| D1 run | end-to-end PASS, COMPLETE 기록, 같은 입력 -> 같은 identity, 짧은 dataset F4 |

실데이터 재현: 같은 입력으로 D1을 두 번 실행해 `d1_report.json`이 timing과 기계 벤치마크를 빼고 **완전히 동일**함을 확인했다(identity digest도 동일).

---

## 13. 판정과 다음 단계

**D1 PASS.** Pre-flight §13의 P1~P4가 모두 충족됐고, G1~G8 통과, B8 = 0, 적격 규모/표본/라이브러리가 D0 설계 전제를 모두 넘는다. HARD FAIL(R1~R12, F1~F5) 발생 0건.

D2가 이제 `expected = FreezeIdentity(dpit1-2ec7d609bf56의 data 블록)`으로 데이터를 받고 다음을 구현한다(D2 설계 §25, Pre-flight §12 우선순위 적용):

```text
encoder -> similarity -> library -> neighbor_search -> label_extension(B3 Temporary) -> pit_audit -> artifacts
```

D2 착수 시 이 문서가 고정하는 값: grid(2024-09-17..2026-09-16, N=501, digest `830744f6…`), 평가 구간 [260, 480], 라이브러리 stride 날짜 85개(index 60 = 2024-12-11 ~ index 480).

**D1은 GATE-D-ALPHA와 무관하다.** 데이터가 규칙대로 존재한다는 것뿐이며, 신호가 있는지에 대해 아무것도 말하지 않는다.

---

## 부록: 재현

```bash
cd ~/usb && PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_d_d1 --snapshot-id USB-HIST-V1
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/strategy_d -q
```

`--workspace-root`를 주지 않으면 마운트된 Google Drive에서 `1_US-B`를 찾는다(회사 PC `/mnt/h`, 집 PC `/mnt/g`). 같은 데이터로 실행한 이전 run id 두 개(`dpit1-e9807b13756c`, `dpit1-5685be8b4171`)는 산출물을 대체하고 지웠다. 차이는 코드뿐이고(리포트에 날짜별 원자료 배열 `eligible_by_date_idx`/`windows_by_end_idx` 추가, 스냅샷 `active=true` 필터 보완) **측정값은 세 run이 모두 동일**하다. `code_digest`가 바뀌면 run id가 바뀌는 것이 identity 계약의 의도된 동작이다.
