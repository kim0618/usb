# US-B STRATEGY A · C0/C1 EDGE-FIRST CENSUS · PREREGISTRATION V1

작성 2026-10-19 (KST). 이 문서는 C0 결과 열람 전에 동결한다. SHA256은 `PREREGISTRATION_C0_C1.sha256`에 기록한다.

## 0. 범위와 금지

- A Paper는 그대로 둔다. 운영 변경 0, commit 0, push 0, deploy 0, 실주문 0.
- A Paper 결과는 이 연구의 규칙 설정·변경에 쓰지 않는다.
- Human/GPT 판단은 census에서 제외한다(후보 선정·필터·평가 어디에도 개입하지 않음).
- Family 메뉴는 F1/F2/F3에서 **CLOSED**. 결과 열람 후 family 추가·정의 변경·게이트 변경 금지.
- 신규 데이터 수집 금지. 기존 캐시만 쓴다.
- 동결 후 허용되는 개정은 단 하나: C1 실행 직전 측정한 minute-session 실측 수(§6.1)를 "사실 기록"으로 덧붙이는 것. 그 외 수정은 새 버전(V2)이며 이 V1 결과와 섞지 않는다.

## 1. 데이터 (C0)

| 항목 | 출처 | 비고 |
|---|---|---|
| 일봉 OHLCV | `data/runtime/strategy_c/raw/grouped/*.json.gz` (Massive grouped daily, unadjusted) | 520세션, 2024-09-16 ~ 2026-10-12 |
| 티커→CIK, 보통주 여부 | `data/runtime/strategy_c/raw/tickers/CS_*.json.gz` (분기 스냅샷 9개, type=CS) | |
| 분할 | `data/runtime/strategy_c/raw/splits/*.json.gz` | |
| 8-K, SIC | SEC submissions 캐시 합집합: `strategy_h_v2/d1_1`, `strategy_h/h_pv2c`, `strategy_h/h0_5` 의 `sec_raw/submissions` | `filings.recent`만 사용(추가 files 미캐시) |
| 발행주식수 | 같은 세 캐시의 `sec_raw/companyfacts`, `dei:EntityCommonStockSharesOutstanding` | 시총 PIT 계산용 |

`acceptanceDateTime`은 **UTC**로 읽는다(근거: C-E0 공식 실행 `strategy_c/e0/runs/ce01-234478e8f7ff27570e13` timezone audit 판정 UTC). C0 산출물에 Item 2.02 접수시각의 ET 시각 분포를 sanity로 함께 출력한다(이 분포는 판정을 바꾸지 않는다. 분포가 UTC 해석과 명백히 모순되면 C0를 INVALID로 보고하고 STOP).

## 2. 세션·PIT 공통 규칙

- 세션 grid = grouped daily 파일이 있는 날짜(XNYS). D+1 = grid상 다음 세션. D+1이 grid 안에 있어야 하므로 D ≤ 2026-10-09.
- 보통주 판정·CIK: as_of ≤ D 인 가장 최근 CS 스냅샷. D가 첫 스냅샷(2024-10-01) 이전이면 첫 스냅샷을 쓴다(최대 11세션 look-ahead, 기록만).
- **COMMON** (모든 family, D 종가 기준):
  - close_D ≥ $5
  - market cap_D = shares_PIT × close_D ≥ $300M
  - shares_PIT = `filed` ≤ D 인 가장 최근 `dei:EntityCommonStockSharesOutstanding` 보고(같은 accn·같은 end의 여러 class 값은 합산). 가장 최근 보고의 filed가 D보다 400일 넘게 이전이면 STALE → 결측. 결측이면 후보 제외, `MCAP_MISSING`으로 집계.
- **분할 오염 제외**: execution_date가 [D−1, D+1] 안에 있는 분할이 있으면 모든 family에서 제외. F1은 [D−20세션, D+1], F3는 [D−252세션, D+1]로 창을 넓힌다. 제외 건수는 `SPLIT_EXCLUDED`로 집계.
- 같은 심볼이 여러 family에 동시에 걸리면 family별로 각각 센다. family 내부에서 연속일 재등장은 각각 센다(repeat ratio가 이를 드러냄).

## 3. Family 정의 (CLOSED)

### F1 · 거래량 동반 급등
- prev close = grid상 직전 세션 종가(직전 세션 바가 없으면 평가 불가).
- close_D / prev_close − 1 ≥ +5.00%
- avg20 = 직전 20개 grid 세션 중 바가 있는 날의 거래량 평균(최소 15일 존재 필요, 미달이면 평가 불가)
- volume_D ≥ 2.0 × avg20
- 평가 가능 첫 D = grid[20] = 2024-10-14.

### F2 · 장후 실적 8-K
- form == `8-K` (8-K/A 제외), `items`에 `2.02` 포함.
- acceptance(ET) ≥ D 16:00:00 ET 이고 < D+1 09:15:00 ET. 주말·휴일은 D(금요일 등 직전 세션)와 D+1(다음 세션) 사이에 자연 포함.
- 장전 발표(D+1 00:00~09:15 사이가 아닌, D+1 09:15 이후 또는 D 16:00 이전)는 제외. 즉 위 창 밖은 전부 제외.
- CIK → D 시점 CS 스냅샷의 티커(복수 class면 각각 후보, 기록).
- 커버리지: CIK의 `recent`에서 가장 오래된 filingDate보다 이전 D는 해당 CIK에 대해 NOT_COVERED. submissions가 없는 CIK도 NOT_COVERED.
- 평가 가능 첫 D = grid[0] = 2024-09-16.

### F3 · 52주 신고가 돌파
- close_D ≥ max(high) over 직전 252개 grid 세션.
- 그 252세션 중 바가 227개 이상 있어야 평가(미달은 결측).
- 평가 가능 첫 D = grid[252] = 2025-09-18. 그 이전은 **NOT_AVAILABLE**(YEAR_A = NOT_AVAILABLE).
- 가격 ≥ $5, 시총 ≥ $300M (COMMON과 동일).

## 4. 기간 창

- YEAR_A: D ∈ [2024-09-16, 2025-09-17]
- YEAR_B: D ∈ [2025-09-18, 2026-10-09]
- F1/F2는 두 창 모두 보고. F3는 YEAR_B(=available period)만, YEAR_A = NOT_AVAILABLE.

## 5. C0 산출

family별, 전체 + YEAR_A/YEAR_B:
candidate sessions, candidate count, unique symbols, repeat ratio(= candidates / unique symbols), median candidates/session(후보가 1건 이상인 세션 기준, 그리고 전체 평가 가능 세션 기준 둘 다), top5 symbol share(최빈 5심볼의 후보 수 / 전체), 최대 단일 세션 점유율, SIC 2자리 상위 3개 점유율과 UNKNOWN 비율(submissions 캐시에 있는 CIK만, 정보용).

D+1 분포(grouped daily D+1 바 기준, gross, 비용 없음, 서술용):
- gap = O₁/C₀ − 1
- open-to-close = C₁/O₁ − 1
- close-to-close = C₁/C₀ − 1
- high excursion = H₁/O₁ − 1
- low excursion = L₁/O₁ − 1
각 n, mean, median, p10, p25, p75, p90, share>0. D+1 바가 없으면 `NO_D1_BAR`로 집계하고 분포에서 제외.

F2 추가: COMMON 통과 심볼-세션 중 SEC 커버리지 비율, Item 2.02 접수시각 ET 분포.

**D+1 분포는 게이트가 아니다.** C0 판정에 쓰지 않는다.

### 5.1 C0 STRUCTURAL GATES (동결)

| id | 조건 | 적용 |
|---|---|---|
| S1 | candidate count(평가 가능 전 기간, D+1 바 있는 것) ≥ 100 | 전 family |
| S2 | unique symbols ≥ 30 | 전 family |
| S3 | top5 symbol share ≤ 25% | 전 family |
| S4 | candidate sessions ≥ 50 | 전 family |
| S5 | 최대 단일 세션 점유율 ≤ 10% | 전 family |
| S6 | YEAR_A ≥ 30 그리고 YEAR_B ≥ 30 candidates | F1/F2 (F3는 미적용, 상태 AVAILABLE_PERIOD_ONLY로 표기) |
| S7 | SEC 커버리지 ≥ 50% | F2만. 미달이면 `C0_DATA_LIMITED` = 구조 실패(신규 수집 금지이므로 CLOSED) |

하나라도 실패 → `C0_STRUCTURAL_FAIL` → CLOSED. 전부 통과 → `C0_PASS` → C1 대상.

## 6. C1 사전등록 (실행은 이번 범위 아님)

### 6.1 minute 데이터 (C1 실행 전 사실 기록)
C1 실행 직전에 Drive를 remount하고, 실제로 읽을 수 있는 **market-wide minute session 수**를 측정해 아래에 기록한다. 이 숫자는 규칙이 아니라 사실이며, C0-pass 후보 중 minute 데이터가 있는 건수를 결정한다.

- MEASURED_MINUTE_SESSIONS = `PENDING (C1 실행 전 기록)`
- MEASURED_AT = `PENDING`
- 후보 중 minute 커버 건수 = `PENDING`

### 6.2 거래 규칙 (동결)
- Long only, 후보 1건 = 거래 1건, 포트폴리오 상한 없음(census), family별 독립.
- 진입: D+1 정규장 09:31 1분봉 시가(execution_v0 `fill_delay_bars=1`과 같은 의미). 09:31 바가 없으면 그 이후 첫 바 시가, 09:45까지 없으면 NO_FILL(거래 아님, 집계).
- 손절: entry − 1.0 × ATR14(D). ATR14 = grouped daily 기준 Wilder ATR(직전 14세션 true range, D 포함).
- R 거리 = 1.0 × ATR14(D).
- 청산: 손절 터치(바 low ≤ stop) 시 min(stop, 그 바 시가)에 체결(WORST_CASE). 터치 없으면 D+1 15:59 바 종가(시간 청산). 오버나이트 없음.
- R_gross = (exit − entry) / R거리.

### 6.3 비용 층
1. GROSS: 0
2. FEE_ONLY: commission 10bp/leg (execution_v0 commission_bps), 왕복 20bp
3. EXECUTION_V0_NET: 25bp/leg (spread 10 + slippage 5 + commission 10), 왕복 50bp

비용(R) = (bps_leg × entry + bps_leg × exit) / 10⁴ / R거리.

### 6.4 표본
- n ≥ 100: STRONG_SAMPLE
- 50 ≤ n < 100: EVALUABLE
- n < 50: INSUFFICIENT (다른 판정보다 먼저 적용)

### 6.5 게이트 (EXECUTION_V0_NET 기준)
PRIMARY: Net PF ≥ 1.20, Net Total R > 0, Net Avg R > 0.
ROBUSTNESS: D 순으로 정렬한 거래의 앞 절반 Total R > 0, 뒤 절반 Total R > 0, Top5 거래 R 합 / Net Total R < 50%.

### 6.6 판정 트리
```
C0 structural fail            → CLOSED
C0 pass                        → C1
  n < 50                       → INSUFFICIENT
  GROSS Total R ≤ 0            → GROSS_FAIL / CLOSED
  GROSS > 0, NET Total R ≤ 0   → COST_BOUND / CLOSED
  PRIMARY + ROBUSTNESS 통과    → RANKING_RESEARCH
  NET > 0 이나 PRIMARY 또는 ROBUSTNESS 실패 → NET_FRAGILE / CLOSED
```
NET_FRAGILE은 원 지시서 트리에 없던 칸을 결과 전에 채운 것이다(구제 금지).
