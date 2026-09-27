# F0 REGULAR -> AFTER-HOURS RESULTS V1

- 공식 판정: **FAIL**
- run `f0-3a7971269e2a` (identity `3a797126…aef64`), 2026-09-27 08:44 UTC, 소요 367초
- rules canonical `3b980aa46f27f1a138478c87cfcd7c510ef9e710044e1e76ff6374e2a0118fd0` (수정 없음)
- code digest `78e5dbd8…`, minute read-set `b51ba28f…`(3,142 페이지 = mirror 3,136 + drive 6, 전 페이지 sha256이 Drive ledger와 일치), daily read-set `c1258af0…`
- git HEAD `cfb29c1` (dirty: 다른 세션의 미커밋 변경), Python 3.12.3, numpy 2.5.2
- 산출물: `data/runtime/strategy_f_candidate/runs/f0-3a7971269e2a/`

아래 수치는 모두 산출물 JSON에서 그대로 옮긴 것이다. 결과를 본 뒤 바꾼 규칙은 없다.

## 1. PIT 감사 (성과 계산 전에 실행)

| 감사 | 결과 |
|---|---|
| PIT-1 feature cutoff poison | PASS. 265,698행 검사, feature·mask·유효상태 차이 0 |
| PIT-2 daily/reference poison | PASS. 12세션 30,590행, universe·prev_close·feature·mask 차이 0 |
| PIT-3 synthetic leak | PASS. 심은 16:05 누수를 265,698/265,698행에서 탐지 |

## 2. Coverage와 체결 해석

- 적격 symbol-session 265,959개가 아래 단계를 거쳐 baseline 16,077개가 된다.
  - 정규장 봉 있음 265,534
  - 15:59 봉 있음 265,303
  - 16:05 정각 봉 있음 23,182 (8.7%)
  - 17:00 청산 resolved **16,077** (NO_TRADE 242,121, UNRESOLVED_EXIT 7,105)
- baseline은 1,224종목·104세션이고, 세션당 99~253개다.
- 청산 봉 나이: 중앙값 60초, p95 420초. 목표 봉(16:59) 정각 사용률 48.0%, 10분 lookback 사용률 52.0%.
- 기준가 P(t)가 정각 봉(나이 0)인 비율: 15:00 83.3%, 15:30 88.0%, 15:45 91.5%.
- 분봉이 없는 적격 종목 25개(CON 포함), VWAP_UNAVAILABLE 0.
- 무결성
  - critical 항목 전부 0.
  - 동일값 중복 1,121개는 제거하고 개수만 기록했다.
  - 20:00 이후 봉 13개는 무시했다.
  - baseline 중 TICKER_SUSPECT는 21행이다.

## 3. Baseline (16:05 open → 16:50~16:59 마지막 close, gross)

| 항목 | 값 |
|---|---|
| mean / median | +0.051% / +0.013% |
| win rate / std | 51.0% / 2.62% |
| P5 / P25 / P75 / P95 | -1.47% / -0.26% / +0.32% / +1.78% |
| P(r ≥ +0.5 / 1 / 2 / 3 / 5%) | 18.0 / 9.0 / 4.2 / 2.5 / 1.5% |
| P(r ≤ -0.5 / 1 / 2 / 3%) | 15.4 / 7.6 / 3.5 / 2.4% |
| MFE mean / median | 0.91% / 0.35% |
| MAE mean / median | -0.87% / -0.34% |
| P(MFE ≥ 2 / 3 / 5%) | 8.8 / **5.30** / 2.9% |

## 4. H1~H5 요약

| | H1 | H2 | H3 | H4 | H5 |
|---|---|---|---|---|---|
| N / 종목 / 세션 | 2,102 / 563 / 103 | 1,621 / 491 / 103 | 658 / 381 / 93 | 1,443 / 444 / 83 | 147 / 123 / 62 |
| mean (gross) | +0.026% | +0.179% | -0.038% | +0.097% | +0.023% |
| mean lift | -0.025%p | +0.128%p | -0.089%p | +0.046%p | -0.028%p |
| median | -0.048% | -0.082% | -0.066% | -0.060% | -0.111% |
| P(MFE≥3%) H / base | 6.23 / 5.30% | 9.25 / 5.30% | 11.70 / 5.30% | 7.97 / 5.30% | 11.56 / 5.30% |
| tail lift (절대 / 배수) | +0.93%p / 1.18x | +3.95%p / 1.75x | +6.40%p / 2.21x | +2.67%p / 1.50x | +6.27%p / 2.18x |
| tail lift 99% CI | [-0.60, +2.46]%p | [+1.53, +7.45]%p | [+3.38, +9.68]%p | [+0.81, +4.72]%p | [+0.12, +14.00]%p |
| mean lift 99% CI | [-0.23, +0.20]%p | [-0.11, +0.42]%p | [-0.50, +0.33]%p | [-0.16, +0.25]%p | [-0.63, +0.58]%p |
| P(r≤-2%) H (base 3.49%) | 4.71% | 5.55% | 8.36% | 4.78% | 9.52% |
| top1% 제거 후 mean lift | -0.21%p | -0.12%p | -0.25%p | -0.14%p | -0.25%p |
| top1 / top5 비중 (50bp net) | 20.6 / 40.9% | 16.4 / 33.9% | 6.9 / 26.4% | 8.4 / 28.0% | 29.2 / 70.4% |
| leave-top-10 mean lift | -0.16%p | -0.06%p | -0.30%p | -0.10%p | -0.50%p |
| 양수 블록 (mean / tail) | 2 / 4 | 3 / 4 | 2 / 4 | 1 / 3 | 3 / 3 |
| break-even (bp) | 2.6 | 17.9 | -3.8 | 9.7 | 2.3 |

## 5. Gate matrix

| Gate | H1 | H2 | H3 | H4 | H5 |
|---|---|---|---|---|---|
| Sample | PASS | PASS | PASS | PASS | INSUFFICIENT_SAMPLE |
| Statistical | FAIL | PASS | PASS | PASS | PASS |
| Tail | FAIL | PASS | PASS | PASS | PASS |
| Mean/Median | FAIL | PASS | FAIL | PASS | FAIL |
| Downside | PASS | FAIL | FAIL | PASS | FAIL |
| Extreme | FAIL | FAIL | FAIL | FAIL | FAIL |
| Concentration | FAIL | FAIL | FAIL | FAIL | FAIL |
| Time Consistency | FAIL | PASS | FAIL | FAIL | PASS |
| Cost | FAIL | FAIL | FAIL | FAIL | FAIL |
| PIT | PASS | PASS | PASS | PASS | PASS |
| **H 판정** | H_FAIL | H_FAIL | H_FAIL | H_FAIL | INSUFFICIENT_SAMPLE |

## 6. 판정: FAIL

**H_PASS가 하나도 없다.** INCONCLUSIVE 조건도 하나도 해당하지 않는다.
- sample을 충족한 H가 4개다.
- UNDERPOWERED인 H가 없다. H1의 Statistical은 99% CI 반폭이 1.53%p로 0.02 미만이라 FAIL이다.
- resolved 세션이 104개다.
- critical 무결성 실패가 없다.

**결정 gate**
- **Cost gate는 5개 H 모두 FAIL이다.** break-even은 최고가 H2의 17.9bp로, 기준 50bp에 한참 못 미친다.
- **Extreme gate도 5개 H 모두 FAIL이다.** 상위 1% 이익 거래를 빼면 mean lift가 전부 음수가 된다.
- 가장 근접한 H2는 Sample·Statistical·Tail·Mean/Median·Time·PIT를 통과했다. 그러나 다음 4개에서 탈락했다.
  - Downside: P(r≤-2%)가 +2.06%p, 1.59배 악화
  - Extreme
  - Concentration: top1 16.4% > 15%, leave-top-10 mean lift 음수
  - Cost

**해석 (판정과 별개)**
- H2·H3·H4는 장후 큰 상승 excursion(MFE≥3%) 확률을 통계적으로 유의하게 올린다(99% CI 하한 > 0).
- 그러나 같은 H들이 큰 하락 확률도 함께 올린다. 평균 초과수익은 소수의 극단 거래가 만들고, 그것을 빼면 사라진다.
- 즉 이 feature들은 방향이 아니라 **장후 변동성을 선택**한다. 이 해석은 판정을 바꾸지 않는다.

## 7. 진단 (판정 불변)

**Secondary horizon** (16:05 진입, 판정 입력 아님)
- 16:30 / 18:00 / 20:00에서 mean lift는 horizon이 길수록 커진다. 예: H2 +0.13 / +0.20 / +0.28%p.
- 그래도 모든 H의 gross 평균이 50bp 비용 가정에 못 미친다. 20:00 H5가 +0.66%로 최대지만 표본이 138개다.
- 16:15 진입 진단도 17:00 primary와 같은 양상이다.

**Matched control** (같은 세션 CEM)
- 매칭률 84~92%.
- 매칭 후 tail 차이: H2 +3.07%p, H3 +4.42%p, H4 +3.00%p.
- 매칭 후 mean 차이: H2 +0.16%p, H4 +0.22%p.
- 세션 demean 결과도 방향이 같다.

**Event** (SEC 8-K item 2.02, baseline 기준)
- EVENT 1,216 / NON_EVENT 13,218 / UNKNOWN 1,643.
- EVENT 행의 P(MFE≥3%)는 34.0%, NON_EVENT는 3.0%다. 큰 장후 움직임은 대부분 실적 공시 세션에서 나온다.
- UNKNOWN은 NON_EVENT에 합치지 않았다. 뉴스 데이터와 실적 캘린더는 없다.

**16:00 경매**
- baseline에서 16:00 open은 c1559와 중앙값이 같다(차이 0.000%).
- H군은 15:50~15:59 사이 상승(중앙값 +0.17~0.59%) 뒤, 16:00 close와 16:00~16:04에서 소폭 되돌린다.

**단일 feature** (Q1~Q5)
- 평균 수익은 모든 feature에서 비단조다.
- P(MFE≥3%)는 regular_RVOL에서 증가 단조이고, 거래대금 계열에서 감소 단조다.
- 이 결과로 threshold를 탐색하거나 바꾸지 않았다.

## 8. 한계

- **SPY 공백:** SPY의 Common Raw에 2026-08-06~09-03(21세션) 공백이 있다. 해당 세션의 `relative_strength_vs_SPY`는 NaN이고(결측률 20.3%), 그 기간에는 H4가 성립할 수 없다. 이 데이터는 legacy parquet에만 있는데, 규칙상 legacy를 쓰지 않는다. readiness 감사에서 SPY 커버리지를 따로 확인하지 않아 놓친 항목이다.
- **RVOL 초기 구간:** 초기 5세션은 RVOL이 NaN이다. 세션 6~20은 분모가 20세션 미만이다. history 5~9 / 10~19 / 20 = 12,371 / 24,957 / 215,556행.
- **체결 가능성:** 16:05 정각 봉 조건 때문에 적격 행의 91%가 NO_TRADE다. baseline은 장후 유동성이 있는 종목 쪽으로 치우친다.
- **비용:** 호가·스프레드가 없어 비용은 전부 COST STRESS ASSUMPTION이다.
- **이벤트:** SEC 커버리지가 부분적이고 뉴스는 없다.
- **표본 기간:** 104세션, 단일 국면이다.
- **PIT:** list_date 없음, 티커 재사용 미감지.

## 9. 재현성

동일 입력으로 재실행한 결과는 `runs/f0-3a7971269e2a/repeat-2/reproduction.json`에 있다(manifest를 뺀 산출물 sha256 비교).
