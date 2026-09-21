# Strategy D V2-A D-V2A-4 결과 (SCREENING 판정)

실행 2026-09-21 (회사 PC). 단계 **D-V2A-4**. 부모: D1 `dv2a1-7cfc565cc548`, D2 `dv2a2-bdfb160b8e58`,
D3 `dv2a3-850a238d9e7a`. 선언은 `D_V2A_SCREENING_CONTRACT_V1.md` + `d_v2a_rules_v1.json`
(canonical `2b060ceb3474…fe229`), 2026-09-20 동결분 그대로다.

```text
GATE-D-V2A-SCREEN = SCREENING_FAIL     (S6, S7 미달)
단계 hard check    = PASS              (13/13)
LONG_DATA_PURCHASE_CANDIDATE = NO

이번 단계가 계산한 것: signal x evaluation 결합, 날짜별 IC(A)·IC(B0)·delta,
                      moving block bootstrap, 4시간블록, A(q) 5분위, S1~S8, secondary 9칸
이번 단계가 하지 않은 것: backtester, adapter, 체결모사, 포지션 사이징, 실거래
V1 변경 0 · A/B/C/E 변경 0 · 배포 0
```

판정 한 줄: **A(q)는 이 창에서 0보다 유의하게 크지만(IC +0.0172, 95% CI 전부 양수),
"같은 좌표의 등가중 합성지표를 넘는가"라는 이 연구의 질문은 2년 데이터로 답이 나오지 않는다.**

---

## 1. START GATE

| # | 확인 | 결과 |
| --- | --- | --- |
| 1 | rules checksum | `2b060ceb3474…fe229` **MATCH** (`.sha256`에서 읽음) |
| 2 | D3 parent COMPLETE / identity | PASS, 부모가 위 D2임을 확인 |
| 3 | `signal_rows` digest 재계산 | `0346ebaf0d873ccb…` = D3 COMPLETE 토큰 |
| 4 | `evaluation_labels` digest 재계산 | `febd1894049974a1…` = D3 COMPLETE 토큰 |
| 5 | 아티팩트 identity 블록 | 두 parquet 모두 rules checksum 일치 |
| 6 | 부모 파일 PRE/POST sha256 | D1 3개 · D2 9개 · D3 8개 전부 불변 |
| 7 | read set PRE/POST | `7e790a8dcf1db8ce…7d55` 불변 (동시 writer 있음, §10) |

---

## 2. 결합

두 아티팩트는 **머지하지 않는다.** D3가 같은 배열에서 같은 순서로 썼으므로 결합은 항등식이고,
`(query_date_idx, sample_rank, query_ticker_col)` 세 컬럼의 원소별 일치를 요구한다. 머지였다면
재정렬이나 행 누락을 조용히 수습했을 것이다.

| 항목 | 값 |
| --- | --- |
| 행 | 66,300 (양쪽 동일) |
| 키 일치 | 3컬럼 전수 일치 |
| `signal_status` | OK 66,300 / VECTOR_UNDEFINED 0 / INSUFFICIENT_NEIGHBORS 0 |
| 라벨 무효 query | 138 |
| 채점 대상 query | **66,162** |

---

## 3. 표본 (S1)

| 항목 | 값 | 문턱 | 판정 |
| --- | --- | --- | --- |
| 평가일 | **221** | `>= 150` | 충족 |
| 유효 query | **66,162** | `>= 20,000` | 충족 |
| 고유 query ticker | **3,323** | `>= 1,000` | 충족 |
| `INSUFFICIENT_NEIGHBORS` 비율 | **0.0000** | `<= 0.05` | 충족 |
| `VECTOR_UNDEFINED` 비율 | **0.0000** | `<= 0.05` | 충족 |

날짜당 유효 query 최소 296 / 중앙값 300. `min_valid_queries_per_date`(100) 미달로 탈락한 날짜 0건,
IC가 정의되지 않아 탈락한 날짜 0건이다. 선언 §9가 예상한 221일·66,100 근방과 일치한다.

날짜를 버릴 때는 **통째로** 버린다. A에서만 버리고 B0에서 남기면 delta가 서로 다른 날짜 집합 위에
놓이기 때문이고, 그런 날짜가 0건이라 이번에는 문제가 되지 않았다.

---

## 4. Primary

```text
IC_A(t)  = Spearman( A(q),  excess_return_5 )   그 날짜의 유효 query 전체
IC_B0(t) = Spearman( B0(q), excess_return_5 )   같은 query 집합
delta(t) = IC_A(t) - IC_B0(t)
primary  = mean_t delta(t)                      날짜 등가중, 221일
```

| 통계량 | 값 |
| --- | --- |
| `mean IC(A)` | **+0.017156** |
| `mean IC(B0)` | **+0.004591** |
| `mean delta` | **+0.012566** |
| `median delta` | +0.016536 |
| delta > 0 인 날짜 비율 | 0.548 |
| IC(A) > 0 인 날짜 비율 | 0.593 |
| delta 95% percentile CI | **[-0.018077, +0.040589]** |
| IC(A) 95% percentile CI | [+0.005266, +0.029902] |
| IC(B0) 95% percentile CI | [-0.023838, +0.038308] |

bootstrap은 선언대로 블록 20세션 · 10,000 replicate · seed 20260920 · percentile · paired(같은 draw).
draw digest `441944514d9e96a8…`.

**seed 해석의 공개.** 선언은 seed를 하나 지정하고 spawn 방식을 말하지 않는다. V1 헬퍼는
`default_rng([seed, horizon])`로 뽑으므로 두 해석이 가능하고, S6이 CI 하한을 읽는 조건이라 선택이
공짜가 아니다. 그래서 **게이트는 선언 문자 그대로의 seed로 뽑은 구간을 읽고**, horizon spawn 변형도
같이 뽑아 나란히 적었다.

```text
게이트(문자 그대로)  delta CI 하한 -0.018077
horizon spawn 변형   delta CI 하한 -0.018198
두 해석 모두 S6 미달 → 해석 선택이 판정을 바꾸지 않았다
```

---

## 5. GATE-D-V2A-SCREEN

| 조건 | 내용 | 관측 | 문턱 | 판정 |
| --- | --- | --- | --- | --- |
| **S1** 표본 | §3 다섯 항목 | 전부 충족 | - | **PASS** |
| **S2** PIT | violation | **0** | `== 0` | **PASS** |
| **S3** 수준 | `mean IC(A)` | +0.017156 | `> 0` | **PASS** |
| **S4** 부호 가드 | `mean IC(A)` vs `abs(mean IC(B0))` | 0.017156 vs 0.004591 | `>` | **PASS** |
| **S5** 우위 점추정 | `mean delta` | +0.012566 | `>= +0.0030` | **PASS** |
| **S6** 우위 구간 | delta CI 하한 | **-0.018077** | `> -0.0025` | **FAIL** |
| **S7** 구간 안정성 | delta>0 블록 / IC(A)>0 블록 | **2** / 3 | 각 `>= 3` | **FAIL** |
| **S8** 경제적 크기 | A(q) 5분위 Q5-Q1 | +0.002089 | `> 0` | **PASS** |

```text
SCREENING_PASS        S1~S8 전부          -> 아님 (S6, S7 미달)
SCREENING_BORDERLINE  S5/S6/S7 중 정확히 하나만 미달 -> 아님 (둘 미달)
SCREENING_FAIL        그 외                -> 해당

GATE-D-V2A-SCREEN = SCREENING_FAIL
INVERSE_EFFECT = 없음 (IC(A)·delta 모두 유의하게 음수가 아님)
```

선언 §7.3의 "둘 이상 미달이면 FAIL"에 그대로 걸린다. BORDERLINE은 하나까지만 허용된다.

### 5.1 시간블록 (S7 상세)

평가일 221일을 연속 4블록(56/55/55/55)으로 나눈다.

| 블록 | `delta` | `IC(A)` | `IC(B0)` |
| --- | --- | --- | --- |
| 1 | -0.00582 | +0.02601 | +0.03183 |
| 2 | -0.01189 | -0.00657 | +0.00532 |
| 3 | **+0.06388** | +0.02074 | -0.04314 |
| 4 | +0.00443 | +0.02829 | +0.02386 |

`mean delta` +0.01257의 대부분이 **블록 3 하나**에서 나온다. 그 블록에서 A가 특별히 좋았기
때문이 아니라(IC(A) +0.021로 평범) **B0가 -0.043으로 무너졌기** 때문이다. IC(A)는 4블록 중 3개에서
양수라 그쪽 조건은 충족했지만, delta는 2개뿐이라 S7이 미달이다.

### 5.2 A(q) 5분위 (S8)

| 분위 | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- |
| 일내 평균 실현 `excess_return_5` | +0.00020 | +0.00022 | +0.00126 | +0.00126 | +0.00229 |

```text
Q5 - Q1  일내 컷 +0.002089   (게이트가 읽는 값)
Q5 - Q1  풀드 컷 +0.002148   (문언의 다른 해석)
단조 위반 1 · basket trend +0.90
```

선언 §7.2의 S8은 분위를 **날짜 안에서** 자르는지 풀에서 자르는지 말하지 않는다. 이 연구는 IC도 집계도
bootstrap 단위도 전부 날짜이고 V1 분위 코드도 날짜 안에서 자르므로 그쪽을 게이트로 읽고, 다른 해석도
같이 실었다. 두 값의 부호와 크기가 사실상 같아 해석 선택이 S8을 바꾸지 않는다.

같은 계산을 B0로 하면 `Q5 - Q1`이 **-0.00114**(단조 위반 3, trend -0.40)다. S8은 A에만 걸린 조건이라
판정에 쓰이지 않지만, B0의 분위가 뒤집혀 있다는 사실은 §7의 부호 진단과 같은 이야기를 한다.

---

## 6. 의무 공개 (선언 §8.3)

| 항목 | 값 |
| --- | --- |
| 실현 `rho = corr(IC_A(t), IC_B0(t))` | **+0.3429** |
| `sd(IC_A)` | 0.0692 |
| `sd(IC_B0)` | **0.1424** |
| `sd(delta)` | **0.1353** |
| pairing 판정 | **`sd(delta)` > `sd(IC_A)` → 이 조합에서 pairing은 분산을 줄이지 못했다** |
| 실현 SE (bootstrap) | **0.01498** |
| 실현 SE (iid) | 0.00910 |
| §8.1 예측 SE (실현 rho 대입) | 0.00636 |

선언 §6.5는 B1을 primary에서 내린 이유로 "pairing이 분산을 줄이지 못하는 조합"을 들며 **B0는 같은
좌표에서 나오므로 그 문제가 완화될 것으로 기대**한다고 적었다. 완화되지 않았다. 실현 rho가 0.34에
그쳤고, `sd(IC_B0)`가 `sd(IC_A)`의 2.06배라 차분이 A 단독보다 더 시끄럽다.

그래서 실현 SE가 예측의 **2.4배**다. 선언 §8.1 표에서 rho 0.34는 `SE(delta)` 약 0.0064 · `delta80`
약 0.0075를 뜻했는데, 실제로는 SE 0.0150이었다. 이 검정력에서 **+0.0126의 점추정으로는 어떤 구간
조건도 통과할 수 없다.** 문턱은 rho에 의존하지 않게 설계됐으므로 이 공개는 판정을 바꾸지 않고,
FAIL의 해석 범위(§9)에만 반영된다.

---

## 7. Secondary (판정 비사용, 부호 무관 전수)

### 7.1 S-6 좌표별 단변량 IC - B0 부호 3개가 틀렸다

| 좌표 | 단변량 IC | 선언 부호 | prior | 일치 |
| --- | --- | --- | --- | --- |
| `return_20` | -0.02244 | -1 | STRONG | O |
| `rvol_today` | **+0.01862** | -1 | WEAK | **X** |
| `rv_20` | -0.01633 | -1 | STRONG | O |
| `tr_today_ratio` | **+0.01118** | -1 | WEAK | **X** |
| `atr_ratio_20_60` | -0.00838 | -1 | WEAK | O |
| `dollar_volume_ratio_20_60` | -0.00783 | -1 | WEAK | O |
| `return_5` | -0.00693 | -1 | STRONG | O |
| `return_60` | **-0.00323** | +1 | **STRONG** | **X** |
| `dist_to_20d_high` | +0.00339 | +1 | STRONG | O |
| `position_in_60d_range` | +0.00058 | +1 | STRONG | O |

**10개 중 7개 일치.** 선언 §6.4가 미리 지목한 구멍이 실제로 열렸다. 틀린 셋 중 둘은 WEAK 사전이었고,
하나(`return_60` 중기 모멘텀)는 STRONG이었다. 부호가 틀린 좌표가 B0를 갉아먹었고, 그만큼 `mean delta`가
부풀었다. 선언은 이 구멍을 S4 부호 가드로 막았고 S4는 통과했지만(A가 B0의 절대값보다 크다), **delta의
크기 자체는 B0의 손상분을 포함한다**는 점을 판정 해석에 반영해야 한다.

### 7.2 S-7 B0-strong - 여섯 좌표 합성이 아날로그보다 낫다

```text
mean IC(B0-strong)      +0.020139
mean IC(A)              +0.017156
delta vs B0-strong      -0.002982      (A가 낮다)
```

STRONG 사전 6좌표만의 등가중 합성지표가 **A(q)보다 높은 IC**를 낸다. 선언은 B0-strong을 secondary로만
쓰라고 못박았으므로 판정에 넣지 않는다. 그러나 이 수치는 §7.1과 합쳐 하나의 이야기를 만든다:
primary가 잡은 +0.0126의 우위는 상당 부분 **B0의 부호 오류에 대한 우위**이지, 좌표 정보에 대한 우위가
아니다. 부호를 제대로 준 합성지표에는 오히려 뒤진다.

### 7.3 나머지 칸

| 슬롯 | 결과 |
| --- | --- |
| S-5 변동성 중립화 응답 (`excess_return_5 / rv_20`) | `IC(A)` +0.01239, `IC(B0)` -0.00416, delta **+0.01655** |
| S-11 B3 직교화 잔차 | A를 좌표 10개로 회귀했을 때 `R^2` **0.064**, 잔차 IC **+0.01134** |
| S-12 이웃 평균거리 5분위별 `IC(A)` | +0.0169 / +0.0177 / +0.0017 / +0.0008 / **+0.0438** |
| S-12 A 5분위별 MFE | +0.0683 / +0.0475 / +0.0454 / +0.0470 / +0.0591 |
| S-12 A 5분위별 MAE | -0.0605 / -0.0435 / -0.0405 / -0.0421 / -0.0524 |
| S-13 `rv_20` 5분위별 `IC(A)` | -0.0009 / +0.0089 / +0.0038 / +0.0127 / +0.0209 |
| S-13 시장 롤링변동성 3분위 `IC(A)` | +0.0124 (74일) / +0.0211 (73일) / +0.0141 (74일) |
| S-13 4시간블록 | §5.1 |

S-11은 이번 실행에서 아날로그 쪽에 가장 유리한 수치다. A(q)는 자기 좌표들의 선형결합으로 6.4%밖에
설명되지 않고, 잔차만으로도 IC +0.0113이 남는다. **아날로그 기계가 만드는 정보의 대부분은 좌표의 선형
재표현이 아니다.** 그럼에도 판정은 FAIL인데, 이 연구가 던진 질문이 "정보가 다른가"가 아니라 "등가중
합성지표를 **넘는가**"이고 그 비교의 분산이 감당이 안 되기 때문이다. secondary는 어느 방향으로도 판정을
뒤집지 않는다(선언 `secondary_cannot_overturn`).

S-12 거리 분위는 단조가 아니다. 가장 먼 5분위에서 IC가 가장 높다(+0.0438). 거리는 A(q)의 입력이 아니며
(등가중 median), 이 수치는 "가까운 이웃일수록 좋다"는 직관에 근거가 없음을 보여준다.

### 7.4 이번 단계가 채우지 못한 칸

| 슬롯 | 사유 |
| --- | --- |
| S-1~S-4 (horizon 1/3/10/20) | embargo `d + h <= D - 60`가 h에 의존해 **horizon마다 별도 Top-K 탐색**(각 D2 규모, 약 380초) |
| S-8 family ablation | 9좌표 탐색 4회 |
| S-9 B1 랜덤 아날로그 | V1 N1 draw 20 replicate |
| S-10 B2 5좌표 kNN | 다른 좌표계 탐색 |
| S-13 중 V1 `S(q)` 상관 | V1 W60_H10_B 아티팩트가 이 머신 run store에 없음(V1 run은 다른 머신 산출) |

전부 **판정 비입력**이다. 채우면 약 1시간의 탐색 비용이 들고 FAIL을 바꿀 수 없으므로 이번 단계에서
실행하지 않았다. 필요하면 별도 단계로 돌린다.

---

## 8. 재현성 / 무결성

| 항목 | 결과 |
| --- | --- |
| 날짜별 IC 2회 계산 digest | `e8070df8600ab7dd…` **동일** |
| bootstrap draw digest 2회 | `441944514d9e96a8…` **동일**, CI 동일 |
| 부모 파일 PRE/POST | D1·D2·D3 전부 불변 |
| read set PRE/POST | 불변 |
| D1 digest 8종 재계산 | 전부 일치 |
| 아티팩트 B0 재도출 | D3 `b0` 컬럼 = 이 실행이 좌표에서 만든 B0 (전수 일치) |
| 아티팩트 라벨 재도출 | D3 `evaluation_labels` = 이 실행이 패널에서 만든 라벨 (전수 일치) |
| peak RSS | 1,290MB (한도 2,048MB) |
| 총 소요 | 83.5초 |
| hard check | **13/13 PASS** |

---

## 9. FAIL이 닫는 범위

선언 §13 `on_FAIL` 원문: *"Strategy D research CLOSED; long data purchase is not justified by D.
The closure is scoped to this coordinate system, this metric, K=50 and this data window."*

```text
LONG_DATA_PURCHASE_CANDIDATE = NO
Backtester · adapter · 체결모사 · 포지션 사이징 · 실거래 : 전부 금지 (PASS였어도 금지)
```

닫는 것: 구조 10좌표 + 등가중 Euclidean + K=50 + 이 2년 창에서, 아날로그 기계가 **같은 좌표의 등가중
합성지표를 넘는다**는 가설.

닫지 않는 것:

- Historical Analog 아이디어 전체. §6이 보여준 대로 이 창의 검정력은 예측의 1/2.4였고, `delta80`이
  0.0075에서 0.018 수준으로 밀렸다. 진짜 +0.008짜리 우위가 있어도 이 데이터는 FAIL을 낸다.
- **A(q) 자체의 유효성.** `mean IC(A)` +0.0172의 95% CI가 [+0.0053, +0.0299]로 전부 양수다. 이 값은
  V1 계열에서 측정된 아날로그 IC 총량(최대 +0.0177)과 같은 자리다. 이 연구가 FAIL인 이유는 A가 0이어서가
  아니라 **비교 대상과의 차이를 이 표본으로 분해할 수 없어서**다.
- 다른 전략을 근거로 한 장기 데이터 구매.

### 9.1 이 결과가 다음 판단에 남기는 것

1. **비교 설계가 병목이다.** paired 설계는 두 계열의 상관이 높을 때만 분산을 줄인다. B1(V1)에서 실패했고
   B0(V2-A)에서도 실패했다. 다음 선언이 또 "X 대비 우위"를 primary로 잡는다면, 비교 대상의 날짜별 분산을
   **선언 전에** 추정해 두어야 한다. `sd(IC_B0)` 0.142는 사후에 알 수 있는 값이 아니었지만, B0의 부호
   신뢰도(WEAK 4개)로부터 예상은 가능했다.
2. **부호 사전이 baseline을 만든다.** 10개 중 3개가 틀렸고, 그중 하나는 STRONG이었다. baseline을
   합성지표로 잡는 설계는 사전 부호의 품질에 판정이 종속된다.
3. **A의 정보는 좌표의 선형 재표현이 아니다**(S-11, `R^2` 0.064). 이 방향은 이 데이터에서 닫히지 않았다.

---

## 10. 동시 writer / 공통 저장소

`historical_v2 fetch --only b_minute`(PID 8236)가 D1~D4 전 구간에서 실행 중이었다. 2026-09-18
사용자 지시대로 **좁힌 규칙**을 적용했다.

| 항목 | 확인 |
| --- | --- |
| writer namespace | `market_data/raw/massive/minute/` 만 |
| D 읽기 집합 | freeze `files` 목록 (grouped daily OK 501 + reference + splits) |
| 교집합 | 없음. `read_set_digest`는 minute을 포함하지 않는다(`source.py` 정의) |
| PRE/POST read digest | D1·D3·D4 전부 `7e790a8dcf1db8ce…7d55` 불변 |
| snapshot | `USB-HIST-V1` FROZEN, `e2a8e5d67ce3b864…` |

---

## 11. 교차머신 재현성 - 측정된 것과 갈린 것

D1~D3 run은 9/20 집 PC에서 만들어졌고 run store는 repo에 없다(`data/runtime`은 git 제외). 이 머신에서
D1~D3을 다시 돌려 부모를 만들었고, 그 과정에서 **데이터 경로는 비트 단위로 재현되지만 한 곳만 갈린다**는
사실이 드러났다.

| 대상 | 집 PC | 회사 PC | 결과 |
| --- | --- | --- | --- |
| `grid_digest` | `830744f64a2d…` | 동일 | MATCH |
| `freeze_digest` / `source_digest` | `9ebd6c29…` / `adc4f191…` | 동일 | MATCH |
| `d_read_digest` | `7e790a8d…7d55` | 동일 | MATCH |
| `raw_feature_matrix` | `75f6810864c2a9e4…` | 동일 | MATCH |
| `rank_feature_matrix` | `2749496384c2d675…` | 동일 | MATCH |
| D2 `neighbors` | `961b79c170f08c23…` | 동일 | MATCH |
| `evaluation_labels` | `febd1894049974a1…` | 동일 | MATCH |
| **D1 `b0_rows`** | `b06da81e7778…` | `2f8f8d6678bf…` | **DIFFER** |
| **D3 `signal_rows`** | `d564fab886c3…` | `0346ebaf0d87…` | **DIFFER** (b0 컬럼 때문) |
| run id | `dv2a1-cec5cc9bc674` 등 | `dv2a1-7cfc565cc548` 등 | DIFFER (§11.2) |

### 11.1 원인과 크기

`B0 = rank_matrix @ weights` 하나만 BLAS를 탄다. 입력(rank matrix)은 비트 동일한데 곱의 합산 순서가
구현마다 달라 마지막 비트가 갈린다. 실측:

```text
같은 rank 행을 66,300행 곱으로 계산 vs 300행 곱으로 계산
date 333 에서 300행 중 1행이 2 ULP (2.776e-17) 차이
date 260, 406 은 차이 0
합성 난수로는 재현되지 않음 (값 의존)
```

**판정에 미치는 영향을 측정했다.** B0 66,300개 중 52,937개를 최대 2 ULP(최대 2.2e-16) 흔든 뒤 게이트를
다시 계산했다.

| 통계량 | 실행값 | 섭동 후 | 변화 |
| --- | --- | --- | --- |
| `mean IC(B0)` | 0.004590584 | 0.004593826 | +3.2e-06 |
| `mean delta` | 0.012565697 | 0.012562456 | -3.2e-06 |
| delta CI 하한 | -0.018076730 | -0.018080105 | -3.4e-06 |
| S5 / S6 / S7 | PASS / FAIL / FAIL | PASS / FAIL / FAIL | **변화 없음** |

가장 가까운 문턱까지의 여유가 0.0096(S5)인데 섭동 효과는 3e-06이다. **머신 간 B0 차이는 이 판정과
무관하다.** 다만 "byte identical"이라는 표현은 앞으로 **같은 머신 안에서만** 참이다.

### 11.2 run id가 달라지는 이유

`identity.code_digest`는 패키지의 `*.py` 전체를 해싱한다. 집 PC의 D1은 `d2.py`·`d3.py`가 없던 시점에
만들어졌으므로, 그 파일들이 생긴 뒤 같은 D1을 다시 돌리면 코드 digest가 달라지고 run id도 달라진다.
데이터 digest는 그대로이므로 과학적 동일성은 유지되지만, **부모 run id는 패키지가 자라면 재현 불가**라는
성질을 기록해 둔다.

---

## 12. D3 감사 수정 (이 단계에서 발견)

이 머신에서 D3를 처음 돌렸을 때 `future_query_mutation`이 **FAIL**했다. date 333에서 `b0_changed`,
즉 "미래를 변조했더니 D 시점 좌표로만 만드는 B0가 움직였다"는 보고였다. 누출이 아니었다.

원인은 §11.1 그대로다. 감사는 **66,300행 곱으로 만든 아티팩트 값**을 참조로 쓰고 **300행 곱으로 재계산한
값**과 `np.array_equal`로 비교하고 있었다. 두 값 모두 옳은데 마지막 비트가 다르다. 집 PC의 BLAS는 그 행을
같게 계산해서 통과했을 뿐이고, **감사 결과가 머신에 의존**하고 있었다.

수정(`d3.py`):

1. 참조를 **감사와 같은 경로·같은 shape**로 만든다(`audit_baseline`, 좌표가 메모리에 있을 때 채집).
2. 미래가 실제로 오염시킬 수 있는 대상인 **좌표 행렬을 비트 단위로 비교**한다(`coordinates_changed` 신설).
   B0는 좌표의 결정적 함수라 좌표가 같으면 누출 정보를 더하지 않는다.
3. 아티팩트 값과 감사 참조의 ULP 격차를 숨기지 않고 `b0_artifact_max_ulp`로 **보고**한다.

재실행 결과 `dv2a3-850a238d9e7a` **PASS**, findings 0, `b0_artifact_max_ulp` 2, 양성대조(라벨 894/900
이동)는 집 PC 기록과 동일하다. 규칙 JSON·좌표·K·embargo는 건드리지 않았다.

회귀 테스트 3개를 추가했다. 기존 테스트가 이 결함을 놓친 이유는 **양쪽을 같은 크기로 계산**했기
때문이고, 새 테스트는 참조가 감사 경로에서 나오는지, 좌표 교환처럼 B0가 못 보는 변화를 감사가 잡는지를
고정한다.

---

## 13. 신규 코드와 테스트

| 모듈 | 줄수 | 역할 |
| --- | --- | --- |
| `d4.py` | 1,178 | 결합, 날짜별 IC, bootstrap, S1~S8, secondary 9칸, 아티팩트 |
| `evaluation_labels.forward_extremes` | +11 | MFE/MAE (S-12). `labels` 단일 importer 규칙 유지 |
| `config.py` | +45 | `bootstrap` / `delta_ci_low_threshold` / `block_stability_minimum` / `time_blocks` |
| `d3.py` | 수정 | §12 |
| `run_strategy_d_v2a_d4.py` | 63 | 러너. **FAIL은 결과이므로 exit 0** (hard check 실패만 non-zero) |
| 테스트 `test_strategy_d_v2_d4.py` | 359 | 30 test |
| 테스트 `test_strategy_d_v2_d3.py` | +47 | 3 test (§12) |
| 테스트 `test_strategy_d_v2_pit.py` | +24 | 2 test (방화벽 §14) |

---

## 14. 방화벽 갱신

`metrics`·`resample`(V1)은 지금까지 **금지** 목록이었다. D-V2A-4가 채점 단계이므로 두 모듈을 허용으로
옮겼다. 근거는 두 모듈이 **채점의 산술**이지 V1의 판정이 아니라는 것이다(Spearman, 등개수 바스켓,
연속 블록, 호출자가 길이·replicate·seed를 주는 moving block bootstrap). `gate`·`evaluation`·`signal`·
`baselines`·`encoder`는 금지에 남는다.

추가를 정직하게 묶는 테스트 2개:

```text
test_only_the_scoring_phase_loads_the_scoring_arithmetic
    d4.py 외 어떤 파일도 metrics/resample을 import하지 않는다
    (IC를 계산할 수 있는 모듈은 IC를 볼 수 있는 모듈이고, D1~D3의 방화벽은 그것이 불가능하다는 데 있다)

test_the_scoring_phase_brings_its_own_numbers
    d4.py는 resample.SEED / BLOCK_LENGTH / REPLICATES / BONFERRONI_ALPHA 등 V1 상수를 읽지 않는다
    V1 seed 20260917 문자열 부재, bonferroni 부재, block_length=/replicates=/seed= 명시 전달
```

---

## 15. Regression

| 대상 | 결과 |
| --- | --- |
| `backend/tests/strategy_d_v2` | **144 passed** (features 26 / PIT 31 / D1 10 / D2 24 / D3 23 / D4 30) |
| `backend/tests/strategy_d` (V1) | 167 passed (변경 없음) |
| `backend/tests` 전체 | **2,761 passed / 1 skipped** (966초) |

전체 실행에서 `strategy_b/test_strategy_b_scanner.py`와 `test_strategy_b_historical_scanner.py` 2개는
수집 단계에서 ImportError(`OBSERVATION_ONLY` 미정의)라 제외했다. **다른 세션이 작업 중인 미커밋
`app/strategy_b/scanner.py` 때문이며 이 단계의 변경과 무관하다**(D 변경은 strategy_b를 건드리지 않는다).

---

## 16. 다음 단계

```text
선언 §13 on_FAIL  ->  Strategy D 연구 종결. D를 근거로 한 장기 데이터 구매는 정당화되지 않는다.

선택지 (사용자 판단 사항, 이 문서가 결정하지 않는다)
  A. 종결 확정. 미완 secondary 7칸은 채우지 않는다
  B. 미완 secondary(S-1~S-4, S-8, S-9, S-10)를 채워 FAIL의 진단 가치를 올린다 (약 1시간 탐색)
  C. V2-B(VOLATILITY 계열) 등 다른 선언으로 이동
```

B를 고르더라도 판정은 바뀌지 않는다(`secondary_cannot_overturn`). 우위 가설을 다시 세우려면 §9.1의
비교 설계 문제를 먼저 해결한 **새 선언**이 필요하고, 그것은 이 문서의 범위가 아니다.
