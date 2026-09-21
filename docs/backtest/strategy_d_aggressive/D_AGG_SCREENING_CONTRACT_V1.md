# Strategy D-AGGRESSIVE Screening Contract (MARKET_STRUCTURE_ANALOG_TAIL_V1)

작성 2026-09-21. 단계 **D0 사전등록**. 이 문서와 `d_agg_rules_v1.json`이 선언의 본문이며, JSON의 canonical
checksum이 연구 동일성의 기준이다. 개념 근거는 `D_AGG_CONCEPT_V1.md`, 인프라 판정은 `D_AGG_REUSE_MATRIX_V1.md`.

```text
strategy_id      MARKET_STRUCTURE_ANALOG_TAIL_V1   (alias D-AGGRESSIVE)
research_family  HISTORICAL_ANALOG_TAIL_CAPTURE
rule_id          d-agg-rules-v1
study_class      SCREENING  (trading rule / backtest 설계 단계로 보낼지 판단, 알파·매매·구매 승인 아님)
gate_name        GATE-D-AGG-SCREEN
rules_canonical_checksum = 1d2b453aed74a7fc76384659b9389ba6b9c7b55e114f7b4a18184e441cc033a8
declared_before_results = true  (범위와 사전 노출은 §2)
선언 시점 데이터 읽기 0, API 호출 0, 코드 0, run 0, 꼬리 통계 계산 0
```

```text
D V2-A 공식 판정: SCREENING_FAIL (S6, S7)  - 이 선언으로 변경되지 않으며 변경될 수 없다
```

---

## 1. 동결 정책

규칙 JSON의 어떤 값이든 바뀌면 canonical checksum이 바뀌고 **다른 연구가 된다.** 새 값은
`d_agg_rules_v2.json`과 새 선언으로 간다. 결과 이후 변경 금지: 신호, setup 분위, 사건 문턱(상·하방), horizon,
MFE/MAE 정의, comparator, primary metric, 게이트 조건·문턱, 판정표, 블록 규칙, 부트스트랩 파라미터와 seed,
집중도 정의, 평가 세션 집합.

---

## 2. 사전 노출 공개

선언 시점에 보였던 것(전문은 JSON `prior_exposure`, 해석은 개념문 §3):

```text
V2-A D4 S-12  A(q) 5분위별 평균 MFE_5  0.0683 / 0.0475 / 0.0454 / 0.0470 / 0.0591
              A(q) 5분위별 평균 MAE_5 -0.0605 / -0.0435 / -0.0405 / -0.0421 / -0.0524
V2-A D4       mean IC(A) +0.0172, Q5-Q1 excess +0.0021, 라벨 무효 138 / 66,300
Strategy C    GATE-C2 MFE lift 1.36 = 변동성, V2B = VOLATILITY
```

계산된 적 없는 것: 꼬리 발생률, 상위 10% 통계, 문턱별 비교, 유니버스 기저율. **이 선언의 어떤 문턱도 후보
값을 데이터 위에서 비교해 고르지 않았다.** 사전 노출은 변동성 탈락 조건(H5, H6, T4)을 필수로 넣는 방향으로만
작용했다.

금지: Q1이 크다는 이유로 하위 10%나 양쪽 꼬리로 setup을 바꾸는 것, D-AGG-1 기저율을 본 뒤 문턱·분위·
horizon·comparator를 다시 고르는 것.

---

## 3. 데이터와 신호

| 항목 | 값 |
| --- | --- |
| dataset | `USB-HIST-V1` / `STRATEGY_C_RAW_FREEZE_V1`, `FROZEN`, 새 수집 금지 |
| 범위 | 2024-09-17 ~ 2026-09-16, XNYS 501세션 |
| digest | freeze `9ebd6c29…aafe`, source `adc4f191…2c02`, grid `830744f6…66c9` (V2-A와 동일, D-AGG-1이 재계산) |
| 데이터 지위 | DEVELOPMENT / SCREENING 영구. 이 창을 읽는 **네 번째** 연구 |
| 신호 | `A(q)` = V2-A D3 `signal_rows.analog_signal_A`, 새 신호 0 |
| 신호 동결 입력 | V2-A 유니버스, 10좌표, 순위 스케일링, 등가중 Euclidean, K=50, stride 5, cap 1/5, embargo `d + h <= D - 60`, 동일 ticker·FIGI 제외, h=5 |
| 부모 | rules canonical `2b060ceb…fe229`를 묶는 COMPLETE D3 run (이 머신 `dv2a3-850a238d9e7a`) |
| 바인딩 digest | `signal_rows`의 `query_date_idx, query_ticker_col, query_ticker, sample_rank, analog_signal_A, signal_status`만. `b0` 계열 제외(머신 간 2 ULP, V2-A D4 §11) |
| 평가 세션 | V2-A와 동일 221세션 (index 260..480), 세션당 query 300 |

---

## 4. Primary Setup

```text
HIGH_A_TOP_DECILE
세션 t 마다
  모집단  signal_status == OK 인 query (D 시점에 확정)
  정렬    A(q) 내림차순, 동률은 sample_rank 오름차순
  선택    앞 ceil(0.10 x n_ok(t)) 행          -> 약 30행 / 세션, 약 6,630행 / 221세션
  그 뒤   라벨 무효 행 제거, 계수
세션 탈락  라벨 유효 setup 행이 20 미만인 세션은 두 집단 모두에서 전 통계 제외, 계수
```

선택은 **라벨 유효성을 보기 전에** 한다. 분위는 **세션 안에서** 자른다. Top 5% / Top 2%는 secondary.

---

## 5. MFE / MAE / 사건

```text
available_at     D+1 04:00 ET
entry_reference  P0 = O(D+1)
window           D+1 .. D+5  (양끝 포함, D 불포함)
MFE_5            max( H(D+1), ..., H(D+5) ) / P0 - 1        >= 0 항상
MAE_5            min( L(D+1), ..., L(D+5) ) / P0 - 1        <= 0 항상
가격 기준        O, H, L = raw / F(t), F(t) = execution_date <= t 인 분할의 곱  (V2-A 동일)
validity         D+1 봉, D+5 세션 봉 존재, label CA 의심 아님  (V2-A 동일)
clip             MFE/MAE 없음 (게이트 통계는 지표·중앙값뿐)
구현             strategy_d_v2.evaluation_labels.forward_extremes(h=5) -> labels.compute_extremes
기록(비게이트)   close_return_5 = P(D+5)/P0 - 1 ([-1,1] clip), excess_return_5 (V2-A 정의)
```

진입 기준은 V2-A 라벨의 `available_at`과 같다. 신호는 D 종가까지의 정보로 만들어지고 매수는 D+1 시가다.
미래 정보가 진입가에 들어가지 않는다.

| 사건 | 정의 | 역할 |
| --- | --- | --- |
| **UP10** | `MFE_5 >= +0.10` | **primary tail** |
| **DN10** | `MAE_5 <= -0.10` | **primary downside** (상방의 거울상) |
| UP5, UP15 | `MFE_5 >= 0.05`, `>= 0.15` | secondary |
| DN5, DN15 | `MAE_5 <= -0.05`, `<= -0.15` | secondary |

MFE는 창이 닿은 최고가이며 청산 가능 가격이 아니다. 포착률은 trading rule 단계에서 다룬다.

---

## 6. Comparator와 지표

### 6.1 Comparator

**SAME_DATE_ELIGIBLE_UNIVERSE**: 세션 t의 라벨 유효 적격 종목 전체(V2-A 유니버스 규칙, `excess_return_5`
중앙값과 같은 모집단, 약 2,600종목). setup과의 약 1% 중복은 보정하지 않는다(lift를 1 쪽으로 당기는 보수적
편향).

### 6.2 세션별 양

```text
p_up_S(t), p_up_U(t)   setup / 유니버스의 라벨 유효 행 중 UP10 비율
p_dn_S(t), p_dn_U(t)   setup / 유니버스의 라벨 유효 행 중 DN10 비율
m_up_G(t)              집단 G의 median MFE_5
m_dn_G(t)              집단 G의 median(-MAE_5)
```

### 6.3 지표

```text
PRIMARY
  TL  = sum_t p_up_S(t) / sum_t p_up_U(t)          세션 층화 상방 꼬리 lift, 세션 등가중

REQUIRED
  DL  = sum_t p_dn_S(t) / sum_t p_dn_U(t)          하방 꼬리 lift
  NTL = TL / DL                                    net tail lift
  AG  = R_S / R_U,  R_G = sum_t m_up_G(t) / sum_t m_dn_G(t)
                                                   중앙값 payoff 비대칭 이득 (척도 불변)

DESCRIPTIVE
  TEP = mean_t ( p_up_S(t) - p_up_U(t) )           꼬리 초과확률
  기저율 = mean_t p_up_U(t), mean_t p_dn_U(t)       D-AGG-1 데이터 검증용
```

비율은 전부 **세션 합을 먼저 만든 뒤** 나눈다. 세션별 비율은 만들지 않는다(분모가 0 근처가 될 수 있는
세션을 피한다). TL 분모는 약 221세션에 걸친 0.1 규모 비율의 합이라 0 근처가 될 수 없고, 0이면 데이터
무결성 HARD FAIL이다.

---

## 7. 통계

```text
단위           평가 세션
bootstrap      moving block, 비순환, 블록 20세션, 10,000 replicate, percentile, 95% 양측
draw           strategy_d_analog.resample.block_indices(T, horizon=5, block_length=20,
                                                         replicates=10000, seed=20260921)
               = numpy default_rng([20260921, 5]), 시작점 [0, T-20] 균등, ceil(T/20) 블록 이어붙여 T로 절단
draw 공유      한 draw 행렬이 이 연구의 모든 구간에 쓰인다.
               각 replicate에서 비율의 분자 합과 분모 합을 같은 재표집 세션 위에서 계산한다
seed           20260921 은 이 연구 고유 seed. V1 20260917, V2-A 20260920 은 재표집에 쓰지 않는다
               (query 표본 해시의 20260917 은 표본을 상속하므로 그대로)
시간 블록      resample.block_partition(T, 4), 연속 근등분 (T = 221 이면 56/55/55/55)
primary 검정   1개 (setup 1 x 문턱 1 x 지표 1), 다중성 보정 없음
```

V2-A D4가 겪은 선언 모호점 두 가지(seed spawn 방식, 분위를 세션 안/풀에서 자르는지)를 이 선언은 문자로
닫았다: spawn은 `[20260921, 5]` 하나, 분위는 세션 안.

---

## 8. 집중도

```text
e_t   = k_up_S(t) - n_S(t) x p_up_U(t)                     세션 t 의 setup 초과 UP10 수
e_i   = sum_{ticker i 의 setup 행} ( 1[UP10] - p_up_U(t) )   종목 i 의 초과 UP10 수

leave_top5_sessions_TL   e_t 상위 5세션을 두 집단 모두에서 제거하고 TL 재계산
leave_top10_tickers_TL   e_i 상위 10종목의 모든 행을 두 집단 모두에서, 모든 세션에서 제거하고 TL 재계산
single_ticker_share      max_i ( 종목 i 의 setup UP10 수 ) / ( setup UP10 총수 )
```

기술통계: 상위 5세션의 양의 `e_t` 점유율, 상위 10종목의 양의 `e_i` 점유율, setup 고유 종목 수, 가장 자주
선택된 종목의 행 수. **섹터/테마는 `NOT_AVAILABLE`**: 저장소에 PIT 종목->섹터 매핑이 없다(V2-A가 섹터 좌표를
뺀 이유와 같음). 한계로 보고하며 게이트가 아니다.

---

## 9. GATE-D-AGG-SCREEN

### 9.1 하드 조건 (하나라도 미달이면 FAIL)

| 조건 | 내용 | 문턱 |
| --- | --- | --- |
| **H1** 표본 | 평가 세션 / 라벨 유효 setup 행 / setup 고유 종목 / setup UP10 사건 | `>= 150` / `>= 5,000` / `>= 500` / `>= 250` |
| **H2** PIT | violation | `== 0` (미달 시 다른 조건을 보지 않고 FAIL) |
| **H3** 결측 편향 | setup 라벨 무효 비율 | `<= 0.02` 그리고 `<= 2 x` 유니버스 라벨 무효 비율 |
| **H4** 방향 | TL | `> 1.00` |
| **H5** 순수 변동성 아님 (꼬리) | NTL | `>= 1.00` |
| **H6** 순수 변동성 아님 (중앙값) | AG | `>= 1.00` |
| **H7** 단일 종목 | single_ticker_share | `<= 0.05` |

### 9.2 단계 조건 (PASS 수준 / BORDERLINE 하한)

| 조건 | 내용 | PASS 수준 | 하한 (floor) |
| --- | --- | --- | --- |
| **T1** 꼬리 lift | TL | `>= 1.25` | `>= 1.10` |
| **T2** 꼬리 lift 구간 | TL 95% CI 하한 | `> 1.00` | `> 0.95` |
| **T3** 블록 안정성 | TL > 1 인 블록 수 (4개 중) | `>= 3` | `>= 2` |
| **T4** net tail lift | NTL | `>= 1.10` | `>= 1.00` (= H5) |
| **T5** 세션 집중 | leave_top5_sessions_TL | `>= 1.10` | `> 1.00` |
| **T6** 종목 집중 | leave_top10_tickers_TL | `>= 1.10` | `> 1.00` |

### 9.3 판정

```text
D_AGG_SCREEN_PASS        H1~H7 전부 + T1~T6 전부 PASS 수준
D_AGG_SCREEN_BORDERLINE  H1~H7 전부 + T1~T6 전부 하한 이상 + PASS 수준 미달이 정확히 하나
D_AGG_SCREEN_FAIL        그 외 전부
```

INVERSE: TL 95% CI **상한** `< 1.00`이면 `INVERSE_EFFECT`로 보고하며 FAIL이다. A 하위 쪽을 쓰려면 새 선언이
필요하다.

`secondary_cannot_overturn`: 어떤 secondary·기술통계도 판정을 어느 방향으로도 바꾸지 않는다.

### 9.4 문턱 근거

- **T1 1.25**: trading rule 단계를 열 가치가 있다고 선언하는 최소 농축(기대 +10% 사건 4개당 1개 추가). §10의
  잡음 모형에서 참 TL = 1.25일 때 구속력 있는 조건은 구간이 아니라 점추정이다.
- **H5, H6, T4**: 합성 순수 변동성 선택(드리프트 0)이 TL 1.55~3.31을 내면서 NTL 0.90~0.97, AG 0.985~0.991을
  냈다(개념문 §4.4). **TL 하나로는 변동성이 통과한다.** 비대칭 조건은 이것을 막기 위해 있다.
- **T5, T6 1.10**: 세션의 약 2%와 가장 강한 10종목을 빼도 농축이 남아야 한다.
- **T3 3/4 vs "2/4 + 강한 aggregate"**: 3/4를 PASS로 고정했다. 귀무에서 3/4 이상일 확률은 0.3125로 단독으로는
  약하지만 T1·T2와 결합되고, 참 TL 1.25에서는 0.95~1.00이라 공격형이라도 3/4를 요구할 비용이 거의 없다.
  2/4는 BORDERLINE 하한으로만 허용한다.
- 문턱은 결과를 보고 조정하지 않는다.

---

## 10. 검정력 / 데이터 충분성 (기저율을 보지 않고)

기저율은 A에 조건부인 정보가 아니지만 **문턱 선택에 영향을 줄 수 있으므로 D0에서 계산하지 않는다.** D-AGG-1이
데이터 검증 산출물로 처음 계산하며, 계산 후 어떤 선언값도 다시 열지 않는다.

가정: `p_up_U` in {0.06, 0.10, 0.15, 0.20}, setup 6,630행, 설계효과 `DEFF` in {1, 2, 4}(세션 내 상관 + 지속적으로
선택되는 종목의 5세션 창 중첩). 유니버스 쪽 분산은 무시(약 57만 행).

```text
SE(log TL) ~= sqrt( DEFF x (1 - p) / (p x 6630) )
```

| `p_up_U` | DEFF | SE(log TL) | CI>1을 80%로 얻는 TL | 참 TL 1.25의 CI 하한 | 귀무 P(TL >= 1.10) | 귀무 P(TL >= 1.25) |
| --- | --- | --- | --- | --- | --- | --- |
| 0.06 | 1 / 2 / 4 | 0.049 / 0.069 / 0.097 | 1.15 / 1.21 / 1.31 | 1.15 / 1.11 / 1.06 | 0.025 / 0.083 / 0.164 | 0.000 / 0.001 / 0.011 |
| 0.10 | 1 / 2 / 4 | 0.037 / 0.052 / 0.074 | 1.11 / 1.16 / 1.23 | 1.17 / 1.14 / 1.10 | 0.005 / 0.034 / 0.098 | 0.000 / 0.000 / 0.001 |
| 0.15 | 1 / 2 / 4 | 0.029 / 0.041 / 0.059 | 1.09 / 1.12 / 1.18 | 1.19 / 1.16 / 1.13 | 0.001 / 0.011 / 0.052 | 0.000 |
| 0.20 | 1 / 2 / 4 | 0.025 / 0.035 / 0.049 | 1.07 / 1.10 / 1.15 | 1.20 / 1.18 / 1.15 | 0.000 / 0.003 / 0.026 | 0.000 |

블록 조건: 참 TL 1.25에서 `P(>= 3/4 블록 TL > 1)` = 0.95~1.00, 귀무에서 0.3125.

읽는 법: **표집 잡음은 주된 위험이 아니다.** 6,630행이면 TL 1.25 수준은 거의 모든 가정에서 구간이 1을
배제한다. 주된 위험은 변동성 교란이고, 그것은 표본을 늘려서가 아니라 H5·H6·T4로 다룬다. 가장 나쁜 가정
(`p = 0.06, DEFF = 4`)에서 귀무가 T1 하한(1.10)을 넘을 확률이 0.16이라 BORDERLINE 오판의 여지가 있고, 그
경우에도 T2·T3·T4가 함께 걸러야 BORDERLINE이 된다.

| 항목 | 값 | H1 문턱 |
| --- | --- | --- |
| 평가 세션 | 221 (V2-A 실측, 탈락 0) | `>= 150` |
| setup 행 | 약 6,630 (V2-A 라벨 무효율 0.2% 적용 시 약 6,617) | `>= 5,000` |
| setup 고유 종목 | 미지 (지속 선택 때문에 행 수보다 훨씬 적을 수 있음) | `>= 500` |
| setup UP10 사건 | 약 400~1,300 (가정 p 0.06~0.20에 TL 1 적용) | `>= 250` |

setup 고유 종목 수는 사전에 알 수 없다. V2-A query 고유 종목이 3,323이므로 500 미만이면 A가 소수 종목에
고착된 것이며, 그 자체가 FAIL 사유다(H1).

---

## 11. Secondary (판정 비사용, 부호 무관 전수)

| 슬롯 | 내용 |
| --- | --- |
| X-1 | UP5/UP15, DN5/DN15에서의 TL, DL |
| X-2 | Top 5%, Top 2% setup의 TL, DL, NTL, AG |
| X-3 | TEP와 bootstrap 구간 |
| X-4 | comparator를 세션 query 표본 300으로 바꾼 전 primary 통계 |
| X-5 | B0 상위 10%의 TL/DL/NTL/AG, setup 대 B0 상위 10%의 TL 비 |
| X-6 | **rv_20 상위 10%**의 TL/DL/NTL/AG (순수 변동성 기준선) |
| X-7 | setup의 세션별 rv_20 10분위 구성으로 재가중한 유니버스 대비 TL, DL |
| X-8 | setup·유니버스의 `close_return_5`, `excess_return_5` 평균·중앙값·양수 비율 |
| X-9 | `MFE_5`, `MAE_5` 분위수 p10..p90, setup·유니버스 |
| X-10 | A(q) 10분위 전체의 `p_up`, `p_dn`, median MFE, median MAE (하위 분위는 보고만, 사용 불가) |
| X-11 | §8 집중도 기술통계 |
| X-12 | 블록별 TL, DL, NTL, AG |

X-6과 X-7은 "A가 변동성 이상의 무엇을 하는가"에 가장 직접 답하므로 결과 문서 본문에 반드시 싣는다. 판정은
바꾸지 않는다.

---

## 12. PIT 계약

`docs/backtest/strategy_d/D_PIT_CONTRACT_V1.md`와 V2-A delta P-1~P-4를 **그대로 상속**한다. 추가:

| # | 조항 |
| --- | --- |
| PA-1 | setup 선택은 세션 t의 `A(q)`, `signal_status`, `sample_rank`만 읽는다. 전부 PIT 감사를 통과한 V2-A D3 산출물이 D 종가 시점에 고정한 값이다 |
| PA-2 | setup 선택은 라벨 유효성보다 먼저다. 선택 모듈은 라벨 모듈을 import할 수 없다(AST 테스트) |
| PA-3 | 세션 D의 `MFE_5`/`MAE_5`는 D+1..D+5 봉만 읽는다. D+5 이후 절단, D+5 이후 또는 D+1 이전 봉 변조에 대해 비트 동일해야 한다(단 `O(D+1)` 변조는 설계상 P0를 바꾼다) |
| PA-4 | 세션 t의 유니버스 comparator는 V2-A D1과 같은 t 시점 적격 마스크를 쓴다. 미래 적격성 없음 |
| PA-5 | V2-A 평가 grid(index 260..480) 밖 세션은 어떤 통계에도 들어가지 않는다 |

PIT violation이 1건이라도 있으면 H2 실패로 즉시 FAIL이다.

---

## 13. 판정 이후

| 판정 | 허용되는 다음 행동 |
| --- | --- |
| **PASS** | D-AGG trading rule 명세와 historical backtest **설계 선언**. Virtual Trading·Long Data 구매 불가 |
| **BORDERLINE** | 구매도 백테스트도 아닌 **서면 노트**(비용, 실현 효과, 미달 조건, 백테스트가 보여야 할 것)가 먼저. 노트 없이 규칙 작업 금지 |
| **FAIL** | 이 신호·setup·문턱·창의 D-AGG 종결. D-AGG를 근거로 Long Data 논의를 다시 열지 않는다 |

어떤 판정도 V2-A 판정을 바꾸지 않는다. US-B 운영 순서:

```text
2년 aggressive screening PASS
  -> trading rule / backtester 설계 선언
  -> historical backtest
  -> Virtual Trading
  -> forward 성과 확인
  -> 그 이후 Long Data 구매 검토
```

---

## 14. 다음 단계

### D-AGG-1 Data/PIT + MFE/MAE validation (게이트 판정 없음)

1. D-AGG rules canonical `1d2b453a…033a8` 재계산 일치, V2-A rules `2b060ceb…fe229` 일치
2. freeze/source/grid digest 재계산 일치, read-set PRE/POST 불변(동시 writer 확인 규칙은 9/18 좁힌 규칙 그대로)
3. V2-A D3 부모 COMPLETE, A 전용 바인딩 digest 산출·고정, `evaluation_labels` digest 일치
4. `compute_extremes(h=5)` 가 선언 공식과 일치하는지 합성 패널 폐형식 테스트, `MFE >= 0`·`MAE <= 0` 전수 확인
5. PA-3 truncate / mutation 감사, PA-2 방화벽 AST 테스트
6. setup 선택 결정성(2회 비트 동일), 세션별 `n_ok`, setup 행 수, 라벨 무효 계수
7. 유니버스 행 수, 라벨 무효 비율, **기저율 `mean p_up_U`, `mean p_dn_U` 최초 계산**(기록만, 선언값 재개봉 금지)
8. setup 쪽 꼬리 통계는 **계산하지 않는다**(D-AGG-2의 몫)

### D-AGG-2 Screening 채점

TL/DL/NTL/AG, bootstrap, 블록, 집중도, H1~H7, T1~T6, 판정, secondary X-1~X-12.

---

## 15. D0 GATE 체크리스트

| # | 조건 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | Stable D와 목적이 명확히 다름 | **충족** | 개념문 §1 표, JSON `relation_to_v2a` |
| 2 | Strategy ID 분리 | **충족** | `MARKET_STRUCTURE_ANALOG_TAIL_V1`, rule `d-agg-rules-v1`, 패키지 `strategy_d_agg`(Reuse §1) |
| 3 | primary setup 고정 | **충족** | §4, 세션 내 상위 10% |
| 4 | primary tail 고정 | **충족** | §5, `MFE_5 >= +0.10` |
| 5 | MFE 정의 고정 | **충족** | §5 |
| 6 | MAE 정의 고정 | **충족** | §5 |
| 7 | comparator 고정 | **충족** | §6.1, 같은 날 적격 유니버스 |
| 8 | primary metric 고정 | **충족** | §6.3, 세션 층화 TL |
| 9 | PASS/BORDERLINE/FAIL 수치 기준 | **충족** | §9 |
| 10 | concentration 조건 | **충족** | §8, H7, T5, T6 |
| 11 | block stability 조건 | **충족** | T3, §7 블록 규칙 |
| 12 | bootstrap 계약 | **충족** | §7, seed·spawn·draw 공유까지 문자 고정 |
| 13 | secondary 판정 비사용 | **충족** | §11, `secondary_cannot_overturn` |
| 14 | PIT contract | **충족** | §12, 상속 + PA-1~PA-5 |
| 15 | 재사용 자산 확정 | **충족** | `D_AGG_REUSE_MATRIX_V1.md` |
| 16 | 다음 단계 정의 | **충족** | §14 |

```text
GATE-D-AGG-D0 = PASS
```

---

## 16. 이번 단계에서 하지 않은 것

```text
코드 0 · 데이터 읽기 0 · API 호출 0 · 꼬리 통계 계산 0 · run 0
V2-A 규칙·결과·산출물·코드·문서 변경 0
Backtester 0 · Virtual Trading 0 · Long Data 구매 0
커밋 0 · 푸시 0 · 배포 0
```

run store에서 읽은 것은 D3 parquet의 **스키마(열 이름)**뿐이다. 값은 읽지 않았다. 개념문 §4.4의 합성
시뮬레이션과 §10의 검정력 표는 리포 밖 scratchpad에서 1회 산출했고 리포에 파일을 남기지 않았다.
