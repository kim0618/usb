# Strategy D V2-A Screening Contract (MARKET_STRUCTURE_ANALOG_V2)

작성 2026-09-20. 단계 **D0 사전등록**. 이 문서와 `d_v2a_rules_v1.json`이 선언의 본문이며,
JSON의 canonical checksum이 연구 동일성의 기준이다. 개념 근거는 `D_V2A_CONCEPT_V1.md`,
인프라 판정은 `D_V2A_REUSE_MATRIX_V1.md`.

```text
strategy_id   MARKET_STRUCTURE_ANALOG_V2
rule_id       d-v2a-rules-v1
study_class   SCREENING  (장기 데이터 구매 판단용, 알파 승인 아님)
gate_name     GATE-D-V2A-SCREEN
rules_canonical_checksum = 2b060ceb34748c3d933d01aee2cf7669ea48bf72a95ae811eba978a3aa3fe229
declared_before_results = true
ai_enabled = false
선언 시점 데이터 읽기 0, API 호출 0, 코드 0, run 0
```

---

## 1. 동결 정책

규칙 JSON의 어떤 값이든 바뀌면 canonical checksum이 바뀌고 **다른 연구가 된다.** 새 값은
`d_v2a_rules_v2.json`과 새 선언으로 간다. 결과를 본 뒤의 수정은 어떤 경우에도 이 선언에
반영하지 않는다.

금지(결과 이후 변경 불가): 좌표 정의·개수, 스케일링, 거리, K, stride, cap, embargo, primary
horizon, primary signal, B0 정의와 부호, 게이트 조건·문턱, 평가 구간, 표본 규칙, 부트스트랩
파라미터.

---

## 2. 데이터 바인딩

새 raw 수집을 하지 않는다. 현재 authoritative dataset을 그대로 쓴다.

| 항목 | 값 |
| --- | --- |
| dataset | `USB-HIST-V1` / `STRATEGY_C_RAW_FREEZE_V1`, status `FROZEN` |
| 기간 / 세션 | 2024-09-17 ~ 2026-09-16, XNYS **501세션** (index 0..500) |
| provider plan | Stocks Basic (rolling 2년, 당일 T bar 없음) |
| source | grouped daily `adjusted=false`, Strategy C raw cache read-only |
| 참조 스냅샷 | 분기 첫 XNYS 세션 기준 `type=CS, active=true`, 날짜 D는 `as_of <= D`의 최신 스냅샷 |
| 분할 | `/v3/reference/splits`, `execution_date <= t`만 `F(t)`에 반영 |
| `freeze_digest` | `9ebd6c29c66728ac8c7f295b5836e7fa8489c24165cc2007d290da2e1c9aeafe` |
| `source_digest` (`c_raw_digest`) | `adc4f1919dd835772262e2820d48c9a04904d5d1c953d8a97386728e08132c02` |
| `grid_digest` | `830744f64a2d6b8378609ad796313b03575fefb146068f2def602867e80166c9` |

가격 기준: `P(t) = raw close(t)/F(t)`, `H(t) = raw high(t)/F(t)`, `L(t) = raw low(t)/F(t)`,
`O(t) = raw open(t)/F(t)`. 거래량 `V(t)`는 **원시 주식 수(미조정)**, 거래대금
`DV(t) = raw close(t) x raw volume(t)`.

D0 단계에서 feature 계산은 하지 않는다. 위 digest는 D-V2A-1이 재계산해 일치를 검사할 대상이다.

---

## 3. Universe (V1과 값까지 동일)

| 항목 | 값 |
| --- | --- |
| 증권 종류 | D 기준 스냅샷의 `CS`, `primary_exchange` in `[XNYS, XNAS, XASE, ARCX, BATS]` |
| 최소 종가 | `P(D) >= 3.0` |
| 최소 ADV20 | `mean(raw close x raw volume)` over `D-20..D-1` `>= 5,000,000` |
| 히스토리 | `D-60..D`의 **61세션 연속 bar** |
| seasoning | 60세션 |
| 분할 창 제외 | `(D-60, D]`에 execution_date를 갖는 분할이 있으면 ticker-date 제외 |
| CA 의심 제외 | `t in D-59..D`에 `P(t)/P(t-1) >= 3.0` 또는 `<= 1/3`이면 제외 |
| 적용 범위 | query 창과 library 창에 **대칭 적용** |

유니버스를 바꾸지 않았으므로 축소 사유·규모를 새로 선언할 필요가 없다. V1 실측 기준 평가
구간의 적격 ticker-date는 581,156건, 날짜당 적격 종목 최소 2,551 / 중앙값 2,619다.
**결과를 보고 유니버스 문턱을 바꾸지 않는다.**

---

## 4. Primary Feature Set (10좌표, 동결)

전 좌표의 lookback <= **60세션**. 표기 `mean X(a..b)`는 세션 인덱스 구간의 산술평균이다.

| # | 이름 | 수식 | 최대 lookback | 미정의 처리 |
| --- | --- | --- | --- | --- |
| 1 | `return_5` | `P(D)/P(D-5) - 1` | 5 | 비유한 시 창 제외 |
| 2 | `return_20` | `P(D)/P(D-20) - 1` | 20 | 동일 |
| 3 | `return_60` | `P(D)/P(D-60) - 1` | 60 | 동일 |
| 4 | `dist_to_20d_high` | `P(D)/max(H(D-19..D)) - 1` | 20 | 분모 `<= 0` 시 제외 |
| 5 | `position_in_60d_range` | `(P(D) - m) / (M - m)`, `M = max(H(D-59..D))`, `m = min(L(D-59..D))` | 60 | `M == m` 시 제외 |
| 6 | `rv_20` | `std_ddof1( ln(P(t)/P(t-1)) )`, `t in D-19..D` | 20 | 비유한 시 제외 |
| 7 | `atr_ratio_20_60` | `ATR_20 / ATR_60`, `ATR_n = mean TR(t)`, `t in D-n+1..D`, `TR(t) = max( H(t)-L(t), abs(H(t)-P(t-1)), abs(L(t)-P(t-1)) )` | 60 | `ATR_60 == 0` 시 제외 |
| 8 | `tr_today_ratio` | `TR(D) / ATR_20` | 20 | `ATR_20 == 0` 시 제외 |
| 9 | `rvol_today` | `V(D) / mean V(D-20..D-1)` | 20 | 분모 `== 0` 시 제외 |
| 10 | `dollar_volume_ratio_20_60` | `mean DV(D-19..D) / mean DV(D-59..D)` | 60 | 분모 `== 0` 시 제외 |

좌표 하나라도 비유한이면 그 (ticker, D) 창은 query에서도 library에서도 제외하고
`VECTOR_UNDEFINED`로 계수한다(V1과 같은 처리).

### 4.1 거래량 좌표의 PIT / 분할 안전성

`V(t)`는 분할 조정을 받지 않으므로 창 안에 분할이 있으면 순진한 비율이 극단값이 된다. 유니버스
규칙이 `(D-60, D]`의 분할을 **query와 library 양쪽에서** 제외하므로, 참조 구간이 그 보호창
안에 있으면 원시 거래량을 변환 없이 쓸 수 있다.

```text
rvol_today                 참조 D-20..D      보호창 내부  안전
dollar_volume_ratio_20_60  참조 D-59..D      보호창 내부  안전
atr_ratio_20_60            P(D-60)을 참조하나 가격은 F(t) 조정본이라 무관
return_60                  P(D-60) 동일 이유로 무관
```

**60세션을 넘는 거래량 좌표는 V2-A에 없다.** 따라서 `V x F` 변환 선언이 필요 없다.

### 4.2 제외한 좌표와 이유

| 제외 | 이유 |
| --- | --- |
| 52주 고점 계열 | 252세션 warmup, 평가일 221 -> 29로 붕괴(§9) |
| 시장 상대강도 (`return_h - market_return_h`) | 횡단면 순위 변환 하에서 `return_h` 좌표와 **수치적으로 동일**(개념문 §3.4). 정보 기여 0 |
| 베타 조정 상대강도 | beta 추정창이 새 자유도. V2-B 후보로만 기록 |
| 섹터 상대강도 | 종목 -> 섹터 PIT 매핑 부재 |
| higher-low / 피벗 계열 | 피벗 정의 파라미터가 3개 이상, 과최적화 표면 |
| raw price path (V1 표현 A/B) | V2-A 신호 입력에서 제거. joint distance 금지 |
| 시장 레짐 | 같은 날짜 모든 query가 같은 값이라 횡단면 순위 기여 0. 층화 보고로만 사용 |

---

## 5. Representation / Similarity / Library

| 항목 | 선언 |
| --- | --- |
| scaling | 날짜별 횡단면 백분위 순위 `(평균순위 - 1)/(n - 1)`, 동점은 평균순위. 기준 모집단은 **그 날짜의 전체 적격 유니버스**(query 표본이 아니다) |
| 벡터 | 위 순위값 10개를 좌표 번호 순으로 이어붙인 `(10,)` 벡터 |
| distance | **등가중 Euclidean** `d = ||v_q - v_n||_2`, 오름차순 |
| 가중치 | 전 좌표 1.0 고정. learned / optimized / Mahalanobis / embedding / metric learning **금지** |
| tie-break | library end session index 오름차순, 그다음 ticker 오름차순 (V1 동일) |
| top_k | **50** |
| library stride | 5세션 (`d % 5 == 0`), expanding |
| min_library_span | 120세션 |
| 종목 cap | library end ticker당 1창 |
| 날짜 cap | library end date당 5창 |
| 선택 | 유사도 순서 greedy, cap 위반 후보는 건너뛰고 top_k까지 |
| 동일 종목 제외 | 전 기간 동일 ticker 제외 + `composite_figi` 일치 제외(양쪽 non-null일 때) |
| embargo | `d + h <= D - 60` (좌표 최대 lookback 60을 V1의 W 자리에 둔다). forward label embargo `d + h <= D`를 함의 |
| 이웃 부족 | top_k 미달 시 query 제외, `INSUFFICIENT_NEIGHBORS`로 계수 |

embargo의 `60`은 V1의 `W` 자리를 좌표 lookback 상한 `L`이 대체한 것이며, 이웃 창과 결과가
query 좌표 창과 겹치지 않게 하는 같은 목적을 수행한다. V1 W=60 조합과 동일한 강도다.

---

## 6. 응답변수 / Horizon / Signal / Baseline

### 6.1 응답변수 (V1과 공유)

```text
labels.available_at      D+1 04:00 ET
reference_price          P0 = O(D+1)
window                   D+1..D+h, D는 절대 포함하지 않는다
close_return_h           P(D+h)/P0 - 1, [-1, 1] clip
excess_return_h          close_return_h - (같은 날짜 label 유효 전체 적격 종목의 median close_return_h)
validity                 D+1 bar 존재, D+h bar 존재, label CA 의심 아님
```

**primary response = `excess_return_5`.** 변동성 중립화 응답변수는 secondary S-5로만 본다
(요청문 §26). V1과 target 정의를 공유하므로 IC 값이 V1과 같은 척도 위에 있다.

### 6.2 Primary horizon = 5세션 (1개)

선택 근거는 개념문 §5(좌표 정의창 20/60세션, 스윙 의도, 라벨 중첩)이며 **V1의 horizon 결과는
근거로 쓰지 않았다.** secondary horizon 1/3/10/20은 기술통계로만 보고한다.

### 6.3 Primary signal

```text
A(q) = median( 수락된 top-50 이웃의 excess_return_5 )
방향   A(q)가 클수록 query의 excess_return_5가 크다
```

median을 mean으로 바꾸지 않는다. mean 버전은 secondary로 보고한다.

### 6.4 Baseline B0 (primary 비교 대상)

같은 10좌표를 쓰되 **이웃 탐색을 하지 않는 등가중 부호 합성지표**다.

```text
B0(q) = (1/10) * sum_i  s_i * r_i(q)
r_i    좌표 i의 날짜별 백분위 순위
s_i    사전 선언 부호 (아래 표), 결과를 보고 바꾸지 않는다
방향   B0(q)가 클수록 excess_return_5가 크다
```

| # | 좌표 | `s_i` | 사전 가설 | prior 강도 |
| --- | --- | --- | --- | --- |
| 1 | `return_5` | **-1** | 주 단위 과잉반응의 되돌림(단기 반전) | STRONG |
| 2 | `return_20` | **-1** | 1개월 반전 | STRONG |
| 3 | `return_60` | **+1** | 중기 모멘텀 | STRONG |
| 4 | `dist_to_20d_high` | **+1** | 고점 근접 = 매물 희박, 앵커링 과소반응 | STRONG |
| 5 | `position_in_60d_range` | **+1** | 구간 상단 = 상대적 강세 | STRONG |
| 6 | `rv_20` | **-1** | 저변동성 이상현상 | STRONG |
| 7 | `atr_ratio_20_60` | **-1** | 변동성 확장은 악화, 수축은 응축 | WEAK |
| 8 | `tr_today_ratio` | **-1** | 당일 충격 이후 할인 | WEAK |
| 9 | `rvol_today` | **-1** | 관심·유동성 수요 충격 이후 되돌림 | WEAK |
| 10 | `dollar_volume_ratio_20_60` | **-1** | 참여도 상승 = 후기 분산 | WEAK |

**WEAK 부호가 4개라는 사실을 숨기지 않는다.** 부호가 틀리면 B0의 IC가 낮아져 primary delta가
부풀 수 있다. 이 구멍은 두 장치로 막는다.

1. 게이트 조건 **S4**(부호 가드): `mean IC(A) > |mean IC(B0)|`. B0가 반대 부호로 크게
   작동하면 delta가 커도 PASS가 되지 않는다.
2. secondary **S-6**: 좌표 10개 각각의 단변량 횡단면 IC를 전수 보고해, 어떤 부호가 틀렸는지
   독자가 직접 확인할 수 있게 한다.

`B0-strong`(STRONG 6좌표만의 등가중 합성)은 **secondary S-7**로만 보고하며 판정에 쓰지 않는다.

### 6.5 Baseline B1 / B2 / B3 (전부 secondary)

| 기준선 | 정의 | 역할 |
| --- | --- | --- |
| B1 | V1 N1과 동일한 랜덤 아날로그(같은 라이브러리, rv 5분위 매칭, 20 replicate) | 기술통계. **판정 비사용** |
| B2 | V1 N2a와 동일한 5좌표 kNN | 좌표 확장의 순효과 기술 |
| B3 | 10좌표에 대한 날짜별 OLS 잔차의 IC (N2b 방식) | 단순 피처 잉여성 기술 |

B1을 primary에서 내린 이유는 완화가 아니라 **paired 분산 구조**다. V1 실측에서 D와 N1의
날짜별 IC가 거의 무상관이라 `sd(IC - IC_N1)`이 13/14 검정에서 `sd(IC)`보다 컸다(중앙값 1.08배).
pairing이 분산을 줄이지 못하는 조합이다. B0는 같은 좌표에서 나오므로 그 문제가 완화될 것으로
기대되며, 실현 상관은 §8.3대로 의무 보고한다. B1 대비 우위가 없으면 그 사실을 결과 문서에
명시한다.

---

## 7. Primary Statistic과 GATE-D-V2A-SCREEN

### 7.1 통계량

```text
날짜 t마다
  IC_A(t)   = Spearman( A(q),  excess_return_5 )   해당 날짜 유효 query 전체
  IC_B0(t)  = Spearman( B0(q), excess_return_5 )   같은 query 집합
  delta(t)  = IC_A(t) - IC_B0(t)

primary   = mean_t delta(t)          (날짜 등가중)
분포추정  = moving block bootstrap, 블록 길이 20세션, 10,000 replicate,
            seed 20260920, percentile CI, paired는 같은 draw
```

primary 가설은 **1개**다. Bonferroni 보정을 적용하지 않으며, 그 이유는 검정 수가 1이기
때문이다(FWER는 동일하게 0.05).

### 7.2 게이트 조건 (수치 동결)

| 조건 | 내용 | 문턱 |
| --- | --- | --- |
| **S1** 표본 | 평가일 / 유효 query / 고유 ticker / `INSUFFICIENT_NEIGHBORS` / `VECTOR_UNDEFINED` | `>= 150` / `>= 20,000` / `>= 1,000` / `<= 0.05` / `<= 0.05` |
| **S2** PIT | PIT violation | `== 0` |
| **S3** 수준 | `mean IC(A)` | `> 0` |
| **S4** 부호 가드 | `mean IC(A)` vs `abs(mean IC(B0))` | `>` |
| **S5** 우위 점추정 | `mean delta` | `>= +0.0030` |
| **S6** 우위 구간 | `delta`의 95% percentile CI 하한 | `> -0.0025` |
| **S7** 구간 안정성 | 4개 연속 시간블록에서 `delta > 0`인 블록 수, **그리고** `IC(A) > 0`인 블록 수 | 각각 `>= 3` |
| **S8** 경제적 크기 | `A(q)` 5분위의 실현 `excess_return_5` Q5 - Q1 | `> 0` |

### 7.3 판정

```text
SCREENING_PASS        S1~S8 전부 충족
SCREENING_BORDERLINE  S1, S2, S3, S4, S8 충족 + mean delta > 0
                      + S5, S6, S7 중 정확히 하나만 미달
SCREENING_FAIL        그 외 전부
```

INVERSE 규칙: `mean IC(A)`나 `mean delta`가 **유의하게 음수**이면 `INVERSE_EFFECT`로 보고하며
어떤 경우에도 PASS가 아니다. 역방향 사용은 새 선언이 필요하다.

BORDERLINE은 남발 수단이 아니다. 점추정이 음수이거나(S3/S5) B0 대비 열위이면(S4) 즉시 FAIL이고,
S5/S6/S7 중 둘 이상이 미달이어도 FAIL이다.

### 7.4 문턱 `+0.0030`과 `-0.0025`의 근거

두 수치는 **V1의 효과크기가 아니라 V1이 측정한 잡음**(날짜별 IC 표준편차 0.066, 자기상관에
의한 SE 팽창 1.25배)에서 운영특성을 역산해 고정했다(§8). 목표 운영특성은

```text
귀무(진짜 우위 = 0)에서 PASS  <= 약 0.10
진짜 우위 +0.008에서 PASS+BORDERLINE >= 약 0.70
```

이었고, `(+0.0030, -0.0025)` 조합이 이를 만족한다(§8.2 표). 결과를 보고 조정하지 않는다.

---

## 8. 검정력 / MDE (요청문 §29)

### 8.1 닫힌 형태

```text
sd(delta)  = sd_IC * sqrt( 2 (1 - rho) )        sd_IC = 0.066 (V1 실측 0.062~0.083의 중앙 부근)
SE(delta)  = sd(delta) / sqrt(221) * 1.25       1.25 = V1 실측 SE 0.00557 / iid SE 0.00444
MDE(유의)  = 1.96 * SE(delta)
delta80    = 0.0030 + 0.8416 * SE(delta)        S5 문턱 기준 80% 검정력 지점
```

`rho`는 `IC_A(t)`와 `IC_B0(t)`의 날짜별 상관이며 **실행 전에는 알 수 없다.**

| `rho` | `sd(delta)` | `SE(delta)` | MDE(유의, 95% 양측) | `delta80` (S5 기준) |
| --- | --- | --- | --- | --- |
| 0.0 | 0.0933 | 0.00785 | 0.0154 | 0.0096 |
| 0.5 | 0.0660 | 0.00555 | 0.0109 | 0.0077 |
| 0.8 | 0.0417 | 0.00351 | 0.0069 | 0.0060 |
| 0.9 | 0.0295 | 0.00248 | 0.0049 | 0.0051 |

읽는 법: **유의성 게이트였다면** 탐지 하한이 0.005~0.015로 이 저장소에서 측정된 아날로그 계열
IC 총량(최대 +0.0177)과 같은 자리에 있어 결론이 나오지 않는다. 스크리닝 게이트는 유의성이
아니라 점추정 + 부호 안정성으로 판정하므로 탐지 하한이 `delta80` 0.005~0.010으로 내려간다.
**그래도 탐지 가능한 것은 큰 우위뿐이며, 작지만 진짜인 우위는 FAIL 또는 BORDERLINE으로 떨어진다.**
이 한계는 게이트의 결함이 아니라 2년 데이터의 성질이고, FAIL의 해석 범위를 §11이 제한한다.

### 8.2 게이트 전체의 근사 운영특성

가정: 날짜별 IC가 정규, `sd_IC = 0.066`, 자기상관 보정 유효표본 141일(= 221 / 1.25^2), 4블록
등분할, Q5-Q1은 `IC(A)`와 강하게 상관. 이 표는 **설계시점 근사이지 측정값이 아니다**
(Monte Carlo 60,000 replicate, `numpy` 난수, 리포 외부 scratchpad에서 산출. 코드 추가 0).

| 진짜 우위 `delta` | `rho` | PASS | BORDERLINE | PASS+BORD |
| --- | --- | --- | --- | --- |
| 0.000 (귀무, B0의 진짜 IC 0.008) | 0.5 / 0.8 / 0.9 | 0.06 / 0.09 / 0.10 | 0.15 / 0.07 / 0.07 | 0.22 / 0.17 / 0.16 |
| 0.004 | 0.5 / 0.8 / 0.9 | 0.21 / 0.42 / 0.60 | 0.26 / 0.14 / 0.12 | 0.47 / 0.56 / 0.72 |
| 0.008 | 0.5 / 0.8 / 0.9 | 0.46 / 0.81 / 0.95 | 0.27 / 0.08 / 0.02 | 0.72 / 0.89 / 0.97 |
| 0.012 | 0.5 / 0.8 / 0.9 | 0.73 / 0.97 / 1.00 | 0.17 / 0.02 / 0.00 | 0.90 / 0.99 / 1.00 |

요약: 귀무에서 잘못 구매 후보로 보낼 확률 약 0.16~0.22, 진짜 우위 +0.008을 놓칠(FAIL) 확률
약 0.03~0.28. 비대칭 비용(잘못된 PASS = 데이터 비용 + 추가 연구 / 잘못된 FAIL = 연구선 종료)을
감안한 의도된 배분이다.

### 8.3 의무 보고 (판정 비변경)

결과 문서는 다음을 반드시 싣는다.

```text
실현 rho = corr( IC_A(t), IC_B0(t) )
실현 sd(IC_A), sd(IC_B0), sd(delta)
sd(delta) > sd(IC_A) 이면: "이 조합에서 pairing은 분산을 줄이지 못했다"를 명시
실현 SE와 §8.1 예측 SE의 대조
```

이 보고는 **판정을 바꾸지 않는다.** 게이트 문턱이 `rho`에 의존하지 않도록 설계한 이유가
여기에 있다. 검정력이 낮게 실현됐다는 사실은 FAIL의 해석 범위(§11)에 반영된다.

---

## 9. 데이터 충분성 사전 계산 (요청문 §28)

```text
L (좌표 최대 lookback)  = 60세션
eval_start_idx          = seasoning(60) + min_library_span(120) + max(L + h)(60 + 20) = 260
eval_end_idx            = N - 1 - max(h) = 501 - 1 - 20 = 480
평가일 수               = 221        (index 260 = 2025-10-01, index 480 = 2026-08-18)
```

`max(h) = 20`은 secondary horizon까지 **모든 검정을 같은 날짜 집합 위에 두기 위한** 것이다.

| 항목 | 값 | S1 문턱 | 판정 |
| --- | --- | --- | --- |
| 평가일 | **221** | `>= 150` | 충족 |
| query 표본 | 221 x 300 = **66,300** | - | - |
| 유효 query (h=5 예상) | 약 **66,100** (V1 실측 h별 65,848 ~ 66,255) | `>= 20,000` | 충족 |
| 고유 query ticker | **3,331** | `>= 1,000` | 충족 |
| library stride 날짜 | **85** (index 60..480, step 5) | - | - |
| `INSUFFICIENT_NEIGHBORS` 전망 | V1 928,200 query에서 **0건** | `<= 0.05` | 구조적 여유 |
| 자기상관 보정 유효 평가일 | 약 **141** (221 / 1.25^2) | - | 검정력 계산 입력 |

좌표 lookback 예산(같은 산식):

| `L` | `eval_start` | 평가일 | `>= 150` |
| --- | --- | --- | --- |
| 60 (V2-A 채택) | 260 | **221** | 충족 |
| 90 | 290 | 191 | 충족 |
| 120 | 320 | 161 | 충족 |
| 130 | 330 | 151 | 경계 |
| 150 | 350 | 131 | 미달 |
| 252 (52주) | 452 | 29 | 붕괴 |

**현재 데이터에서 좌표 lookback 상한은 약 130세션이고, V2-A는 60세션으로 여유를 둔다.**
표본 조건이 사전에 충족되므로 V2-A는 §14의 중단 사유에 해당하지 않는다.

query 표본 규칙은 V1 그대로다: 날짜 D의 적격 ticker를 `sha256('Q|20260917|{D}|{ticker}')`
순으로 정렬해 앞 300개, **라벨 유효성을 보기 전에** 추출한다. seed를 바꾸지 않는 것이
표본 쇼핑 방지이자 V1 대비 비교 가능성이다.

---

## 10. Secondary Diagnostics (13칸, 판정 비사용)

전부 사전 정의하며 **PASS/BORDERLINE/FAIL 판정에 쓰지 않는다.** 결과는 부호와 관계없이 전수
보고한다. secondary 결과로 primary 판정을 뒤집지 않는다.

| 슬롯 | 내용 |
| --- | --- |
| S-1 ~ S-4 | horizon 1 / 3 / 10 / 20의 `IC(A)`와 `delta` |
| S-5 | 변동성 중립화 응답변수 `excess_return_5 / rv_20`에 대한 `IC(A)`, `delta` |
| S-6 | 좌표 10개 각각의 단변량 횡단면 IC (B0 부호 검증용) |
| S-7 | `B0-strong`(STRONG 6좌표) 대비 `delta` |
| S-8 | family 단위 ablation: Trend / Position / Volatility / Volume 각 1개씩 제거한 4변형의 `IC(A)` |
| S-9 | B1 랜덤 아날로그 대비 `delta` |
| S-10 | B2 (V1 5좌표 kNN) 대비 `delta` |
| S-11 | B3 직교화 잔차 IC |
| S-12 | 이웃 평균거리 분위별 IC, `A(q)` 분위별 실현 MFE/MAE |
| S-13 | 층화 기술통계: `rv_20` 분위별 IC, 4개 시간블록별 IC, 시장 롤링 변동성 3분위별 IC, 그리고 **V1 `S(q)`(W60_H10_B 산출물)와 `A(q)`의 날짜 내부 Spearman** |

S-13의 레짐 층화는 **필터가 아니다.** 현재 평가 구간은 4블록 전부 상승(+0.9% / +2.3% / +6.7% /
+5.8%), 연율 변동성 11.6~15.5%, 최대 낙폭 6.5% 이내로 사실상 단일 레짐이므로 층화는 같은
레짐을 쪼개는 것에 가깝다. 레짐 가설은 이 데이터에서 검정 불가이며 그 사실을 결과에 명시한다.

---

## 11. PIT 계약

`docs/backtest/strategy_d/D_PIT_CONTRACT_V1.md`를 **그대로 상속**한다. V2-A가 바꾸는 것은
아래 4개뿐이며, 그 외 조항(정보 가용 시각, truncate 감사, 변조 양성대조, alpha firewall,
read-set 불변, artifact 부모 바인딩)은 문자 그대로 유효하다.

| # | 변경 | 내용 |
| --- | --- | --- |
| P-1 | 신호 입력 | 경로 `P(D-W..D)` -> 구조 좌표 10개. 전부 `<= D` 정보만 사용 |
| P-2 | embargo의 `W` | `d + h <= D - W` -> `d + h <= D - 60` (`L = 60`) |
| P-3 | 좌표 표준화 모집단 | 날짜 D의 적격 유니버스 횡단면. **미래 날짜 정보 미참여**, rolling 추정창 없음 |
| P-4 | 거래량 좌표 | 참조 구간을 `(D-60, D]` 분할 보호창 안으로 제한(§4.1). `V x F` 변환 불필요 |

추가 PIT 요구:

```text
B0(q)는 query의 좌표만으로 계산한다. 라벨, 이웃, 같은 날짜 다른 종목의 실현수익을 읽지 않는다
(횡단면 순위는 좌표값에서만 만들어진다).
delta(t)는 같은 날짜의 같은 query 집합 위에서만 계산한다.
D-V2A-1은 §2의 세 digest 재계산 일치를, D-V2A-3은 truncate 재실행 bit 동일성을 요구한다.
PIT violation이 1건이라도 있으면 S2 실패로 즉시 FAIL이며 다른 조건을 보지 않는다.
```

---

## 12. 데이터 지위 / OOS 설계

```text
USB-HIST-V1 (2024-09-17 ~ 2026-09-16)
  = DEVELOPMENT / SCREENING DATA (영구)
  D V1이 1회, C-4가 1회, V2-A가 3회째로 읽는 창이다
  향후 어떤 연구에서도 locked OOS 또는 holdout으로 승격하지 않는다
```

장기 데이터 확보 이후의 구조는 물리적으로 분리한다.

```text
DEV         미사용 과거 구간. 좌표·규칙 구현과 디버깅
VALIDATION  제한적 연구 선택만 허용
LOCKED OOS  선언 후 단 1회 개봉, 최종 Gate
```

전방 누적(월 약 21세션)은 비용 0의 보조 경로이며, V1/C-4가 보지 않은 평가일 200일 확보에
약 9.5개월이 걸린다. 다만 레짐 문제는 해결되지 않으므로 장기 구매의 대체가 아니라 차선이다.

---

## 13. 장기 데이터 구매 규칙

| 스크리닝 결과 | `LONG_DATA_PURCHASE_CANDIDATE` | 다음 행동 |
| --- | --- | --- |
| **PASS** | **YES** | 8년 이상 / 뚜렷이 다른 변동성 레짐 2개 이상 / dev·validation·locked OOS 물리 분리가 가능한 데이터의 비용 조사 및 구매 검토 |
| **BORDERLINE** | **YES (조건부)** | 구매 전에 비용, 실현 효과크기, 필요 표본(§8.1 역산)을 적은 재평가 노트를 남긴다. 그 노트 없이 구매로 넘어가지 않는다 |
| **FAIL** | **NO** | Strategy D 연구 종료. D를 근거로 한 장기 데이터 구매는 정당화되지 않는다 |

제약:

```text
PASS 또는 BORDERLINE 이어도
  Backtester 진행 금지
  adapter / 체결 모사 / 포지션 사이징 금지
  실거래 승인 금지
2년 스크리닝 결과는 Final OOS가 아니다
장기 데이터 확보 후 D-V2A는 DEV 단계부터 다시 시작한다(이 선언의 재사용 가능, 결과는 재산출)
```

FAIL이 닫는 범위와 닫지 않는 범위:

```text
닫는다   이 좌표계(구조 10좌표) + 등가중 Euclidean + K=50 + 이 데이터 창에서
         아날로그 기계가 동일 좌표 합성지표를 넘는다는 가설
닫지 않는다  Historical Analog 아이디어 전체 (§8의 검정력 한계 때문)
             다른 전략을 근거로 한 장기 데이터 구매
```

---

## 14. D0 GATE 체크리스트

| # | 조건 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | V1과 명확히 다른 가설 | **충족** | 좌표계·비교대상·검정구조 교체, 경로 입력 제거 (개념문 §1) |
| 2 | primary feature set 고정 | **충족** | §4, 10좌표 수식 동결 |
| 3 | feature scaling 고정 | **충족** | §5, 날짜별 횡단면 백분위 순위 |
| 4 | distance 고정 | **충족** | §5, 등가중 Euclidean |
| 5 | K 고정 | **충족** | §5, K = 50 |
| 6 | primary horizon 1개 고정 | **충족** | §6.2, h = 5, 경제적 근거 명시 |
| 7 | primary signal 정의 고정 | **충족** | §6.3, top-50 median |
| 8 | B0 baseline 고정 | **충족** | §6.4, 부호 10개 사전 선언 + 부호 가드 |
| 9 | PASS/BORDERLINE/FAIL 수치 기준 고정 | **충족** | §7.2, §7.3 |
| 10 | secondary 판정 비사용 고정 | **충족** | §10, 13칸 |
| 11 | PIT 계약 정의 | **충족** | §11, V1 상속 + 4개 delta |
| 12 | 평가 표본 사전 계산 | **충족** | §9, 평가일 221 >= 150 |
| 13 | MDE 확인 | **충족** | §8, `delta80` 0.0051 ~ 0.0096 |
| 14 | long-data purchase Gate 정의 | **충족** | §13 |
| 15 | rules checksum 생성 | **충족** | canonical `2b060ceb3474…fe229`, `d_v2a_rules_v1.sha256` |

```text
D0 GATE = PASS
```

중단 조건(사전 선언): 평가일이 150일 미만이거나, 좌표 중 하나라도 `(D-60, D]` 보호창을 벗어난
거래량 참조를 요구하거나, MDE가 `delta80 > 0.02`로 밀리면 V2-A를 착수하지 않는다. 세 조건
모두 §8~§9에서 해당하지 않음을 확인했다.

---

## 15. 이번 단계에서 하지 않은 것

```text
코드 0        structure feature / encoder / neighbor search / signal / evaluation / backtester
run 0         데이터 읽기 0, API 호출 0, feature 계산 0
V1 변경 0     rules / results / artifacts / code / 문서
커밋 0  푸시 0  배포 0
```

§8.2의 Monte Carlo는 리포 밖 scratchpad에서 1회 산출했고 리포에 파일을 남기지 않았다.
§8.1의 닫힌 형태로 같은 수치를 재현할 수 있다.
