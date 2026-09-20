# Strategy D D4 Results V1 (Alpha Pre-validation, GATE-D-ALPHA)

실행 2026-09-20 (집 PC). 판정 **GATE-D-ALPHA = FAIL**.

- run id `deval1-99574380a3d0`
- parent D3 `dsig1-eaeb6df9dea6`, parent D2 `dneigh1-fe0362523739`
- 산출물 `data/runtime/strategy_d/runs/deval1-99574380a3d0/` (로컬, git-ignored)
- 규칙 정본 `d_analog_rules_v1.json`, canonical sha256 `680bf113…0cd3` **MATCH**
- 새 코드: `strategy_d_analog/`에 `features`, `metrics`, `resample`, `baselines`, `gate`, `d4`,
  CLI `app.dev.run_strategy_d_alpha`, 테스트 `test_strategy_d_d4.py` 45개
- A/B/C 코드 변경 0. D0~D3 문서 변경 0. 커밋/푸시/배포 0. Backtester 0.

> **판정 의미.** D0 `pass_fail_policy.pass_meaning`이 정의한 대로, PASS는 "이 데이터 구간에서
> 아날로그 신호가 랜덤 아날로그와 단순 가격 피처를 넘는 정보를 담는다"는 뜻이었다. FAIL은 그
> 근거가 사전등록 기준에서 확인되지 않았다는 뜻이다. 수익성에 대한 진술이 아니다.

---

## 1. START GATE (10/10)

| # | 조건 | 결과 |
| --- | --- | --- |
| 1 | D0 checksum MATCH | **OK** `680bf113…0cd3` |
| 2 | D1 freeze/grid MATCH | **OK** D2 `data` == D3 `data` |
| 3 | D2 COMPLETE 검증 | **OK** verdict PASS, identity == COMPLETE token |
| 4 | D3 COMPLETE 검증 | **OK** verdict PASS |
| 5 | D3 `signal_rows` digest MATCH | **OK** 디스크에서 열 digest 재계산 |
| 6 | D3 `evaluation_labels` digest MATCH | **OK** 동일 |
| 7 | D3 parent binding MATCH | **OK** `parent_d2_identity` == D2 identity |
| 8 | 다른 D 작업의 artifact 수정 없음 | **OK** `d_identity` 스탬프 1종, 실행 중 D 프로세스 없음 |
| 9 | signal / evaluation 분리 | **OK** signal에 future 열 0, evaluation에 S/sigma 0 |
| 10 | D3 query future firewall PASS | **OK** D3 테스트 스위트 |

실행 중에도 `read_parent_tables`가 두 테이블의 digest를, `read_library`가 `library_meta`의
digest를 다시 대조한다. 부모가 한 글자라도 움직이면 D4는 평가 전에 멈춘다.

---

## 2. D4가 읽은 것 (§2 해석)

요청문 §2는 D4를 "순수 Evaluation Layer"로 규정하고 D2 Library 재호출을 금지한다. 그런데 D0
`baseline_N1.library`는 "the same library the query sees", `baseline_N2.N2a_feature_knn`은
"same library, policies, top_k, caps and signal as D"로 정의돼 있어 라이브러리 없이는 존재할 수
없다. 두 문서가 충돌하므로 우선순위(D0 규칙 JSON 우선)에 따라 다음과 같이 해석했다.

| 항목 | D4에서 | 근거 |
| --- | --- | --- |
| D의 `S(q)`, `sigma(q)` | **D3 산출물에서 그대로 읽음.** 재계산 경로 없음 | §2, §5 |
| Pattern Encoder, 경로 similarity | **호출 0.** AST 테스트가 import를 금지 | §2 |
| Top-K 재검색(D용) | **없음** | §0, §2 |
| `library_meta_W*.parquet` (신원·유효성 플래그만, 벡터 없음) | 읽음 | N1/N2a 정의상 필수 |
| frozen panel | 읽음 | rv 분위·N2 피처·MFE/MAE |
| `neighbor_search.select`, `similarity.score_block` | N2a에서 재사용 | N2a가 D와 **거리 외에는 동일**해야 공정 |

N2a가 배포 모듈의 `select`를 그대로 쓰는 것이 핵심이다. 자체 사본을 쓰면 D와 N2a가 거리 말고도
달라질 수 있고, 그 차이는 아무도 볼 수 없다.

---

## 3. 평가 표본

| 항목 | 값 | D0 조건 9 |
| --- | --- | --- |
| 평가 가능 날짜 | **221** (전 검정) | >= 150 **충족** |
| 유효 query | 65,848 ~ 66,255 (h별) | >= 20,000 **충족** |
| 고유 query ticker | 3,314 ~ 3,327 | >= 1,000 **충족** |
| `INSUFFICIENT_NEIGHBORS` 비율 | **0.0** | <= 0.05 **충족** |
| PIT violation | **0** | == 0 **충족** |

`min_valid_queries_per_date = 100`을 "평가 가능 날짜"의 정의로 해석했다. D0에서 이 상수가 쓰이는
곳이 여기뿐이고, 100개 미만 횡단면에서 순위상관은 의미가 없다.

N1 draw가 top_k를 못 채운 경우 **0건**, N2a가 못 채운 경우 **0건**, N2 피처 결측 query **0건**.

---

## 4. 14검정 결과 (전부 출력)

`Mean IC`는 D0 primary metric(날짜별 Spearman IC의 등가중 평균), `IC CI low`는 Bonferroni
수준(1 - 0.05/14 = 0.99643) percentile 하한, `Blk`는 IC > 0인 시간 블록 수다.

| W | h | Rep | N valid | Mean IC | IC CI low | Q5-Q1 | N1 delta | N2a delta | N2b | Blk | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20 | 1 | A | 66,255 | -0.003905 | -0.017773 | -0.000167 | -0.006905 | -0.010116 | -0.001690 | 2/4 | FAIL |
| 20 | 1 | B | 66,255 | +0.007436 | -0.006305 | +0.000356 | +0.004436 | +0.001225 | +0.005761 | 4/4 | FAIL |
| 20 | 3 | A | 66,202 | -0.000780 | -0.018571 | +0.000681 | -0.002886 | -0.005292 | -0.001051 | 2/4 | FAIL |
| 20 | 3 | B | 66,202 | +0.001745 | -0.016067 | +0.000621 | -0.000362 | -0.002767 | +0.001930 | 3/4 | FAIL |
| 20 | 5 | A | 66,162 | +0.001022 | -0.009311 | +0.001539 | -0.003024 | -0.005868 | +0.000356 | 3/4 | FAIL |
| 20 | 5 | B | 66,162 | +0.009875 | -0.002250 | +0.002398 | +0.005830 | +0.002985 | +0.008348 | 3/4 | FAIL |
| 40 | 5 | A | 66,162 | +0.003863 | -0.008913 | +0.000275 | -0.001573 | +0.000057 | +0.000941 | 2/4 | FAIL |
| 40 | 5 | B | 66,162 | +0.007328 | -0.009233 | +0.000714 | +0.001892 | +0.003522 | +0.000828 | 2/4 | FAIL |
| 40 | 10 | A | 66,062 | +0.011691 | -0.001146 | +0.003002 | +0.003507 | +0.001311 | +0.008013 | 4/4 | FAIL |
| 40 | 10 | B | 66,062 | **+0.017728** | -0.004073 | +0.004051 | +0.009544 | +0.007348 | +0.012085 | 3/4 | FAIL |
| 60 | 10 | A | 66,062 | +0.003391 | -0.013044 | +0.002192 | -0.003442 | -0.009213 | +0.000411 | 2/4 | FAIL |
| 60 | 10 | B | 66,062 | +0.011654 | -0.016896 | +0.003700 | +0.004822 | -0.000950 | +0.005830 | 3/4 | FAIL |
| 60 | 20 | A | 65,848 | -0.003232 | -0.021478 | -0.002087 | -0.011583 | -0.015789 | -0.002059 | 2/4 | FAIL |
| 60 | 20 | B | 65,848 | +0.010924 | -0.008525 | +0.002368 | +0.002573 | -0.001633 | +0.011999 | 3/4 | FAIL |

어떤 검정도 결과가 나쁘다는 이유로 표에서 빼지 않았다.

### 4.1 조건별 실패 분포

| 조건 | 실패 검정 수 |
| --- | --- |
| 1 IC point > 0 | 3 / 14 |
| **2 IC CI low > 0 (Bonferroni)** | **14 / 14** |
| **3 IC - IC_N1 CI low > 0** | **14 / 14** |
| **4 N2b residual IC CI low > 0** | **14 / 14** |
| 5 IC - IC_N2a point > 0 | 8 / 14 |
| 6 Q5 - Q1 > 0 | 2 / 14 |
| 7 time blocks (>= 3/4) | 6 / 14 |
| 8 horizon consistency | 4 / 14 |
| 9 sample | 0 / 14 |
| 10 PIT violations | 0 / 14 |

**결정적인 것은 조건 2, 3, 4가 14검정 전부에서 실패했다는 점이다.** 표본과 PIT는 완벽하게
충족했고, 실패는 오직 신뢰구간이 0을 포함한다는 데서 왔다. 가장 강한 W40_H10_B조차 IC 점추정
+0.0177에 Bonferroni 하한 -0.0041로, 0을 넘지 못한다.

### 4.2 INCONCLUSIVE가 아닌 이유

D0 `decision.INCONCLUSIVE`는 "no test passes, and some test meets 1, 2, 3 and 4 but fails only
7, 8 or 9"다. 조건 2·3·4가 **모든** 검정에서 실패했으므로 이 경로에 해당하는 검정이 하나도 없다.
평가 가능 날짜도 221 >= 150이라 두 번째 INCONCLUSIVE 경로(데이터 깊이 부족)에도 해당하지 않는다.

특히 W60/h20은 데이터 깊이 한계로 INCONCLUSIVE가 예상됐던 조합이지만, 실제로는 221개 평가일과
65,848개 유효 query를 확보해 조건 9를 충족했다. 표본이 모자라서가 아니라 **신호가 기준에 못
미쳐서** FAIL이다.

### 4.3 INVERSE_EFFECT

**0건.** IC 신뢰구간이 전부 0 아래인 검정은 없다. 음의 IC 점추정이 3건(W20_H1_A, W20_H3_A,
W60_H20_A) 있지만 구간이 0을 포함하므로 D0 정의상 역효과가 아니다.

---

## 5. 기준선

### 5.1 N1 (Random Analog)

같은 라이브러리, 같은 embargo, 같은 cap, 같은 top_k, 같은 median 신호에 **유사도만 랜덤 추출로**
바꾼 기준선이다. 질문은 "패턴 매칭이 동전 던지기보다 나은가"다.

`IC - IC_N1`의 Bonferroni 하한이 **14검정 전부 0 이하**다. 점추정으로도 6개 검정에서 음수다.
Representation B의 긴 horizon(W40_H10_B +0.0095, W60_H10_B +0.0048)이 가장 큰 양의 차이지만
구간은 0을 포함한다.

### 5.2 N2a (Feature kNN)

같은 파이프라인에 경로 대신 5개 표준화 피처의 Euclidean 거리를 쓴 기준선이다.
`IC - IC_N2a` 점추정이 **8검정에서 음수**다. 즉 절반 이상의 검정에서 단순 가격 피처 kNN이
패턴 경로 매칭보다 나았다.

### 5.3 N2b (Orthogonalized)

`rank(S(q))`를 5개 피처에 OLS로 회귀한 잔차의 IC다. 점추정은 11검정에서 양수이나 Bonferroni
하한은 **14검정 전부 0 이하**다.

### 5.4 N1b

**계산하지 않았다.** D0가 "reported, never a gate input"으로 규정한 descriptive 변형이고,
D가 채택한 실제 이웃 신원이 필요해 D2 neighbor 테이블(46.4M행 x 14)을 다시 읽어야 한다.
요청문 §2가 D4 입력에서 제외한 자료이며 gate에 영향이 없어 생략했다.

---

## 6. sigma(q) 신뢰도 가설

D0 secondary는 sigma **3분위**(terciles, descriptive)를 요구한다. 가설은 "similarity confidence가
높을수록 S(q)의 예측력이 강해진다"였다.

| TestId | T1 (low sigma) | T2 | T3 (high sigma) |
| --- | --- | --- | --- |
| W20_H1_B | +0.011174 | +0.002477 | +0.007929 |
| W20_H5_B | +0.015980 | +0.005956 | +0.003297 |
| W40_H10_B | +0.023355 | +0.010289 | +0.004181 |
| W60_H10_B | +0.020306 | +0.000984 | +0.006584 |
| W60_H20_B | +0.001088 | +0.012779 | +0.018490 |
| W40_H5_B | +0.014746 | -0.002217 | -0.003572 |

**가설과 반대 방향이 더 흔하다.** 표현 B의 여러 검정에서 오히려 **낮은** sigma 구간의 IC가 가장
높다. 단조 증가를 보이는 것은 W60_H20_B 하나뿐이다. sigma는 V1에서 descriptive이고 gate 입력도
필터도 아니므로(D0 `signal.similarity_confidence.role`) 이 관찰로 아무것도 바꾸지 않는다.

---

## 7. Quintile / MFE / MAE

날짜 **안에서** S(q)를 5분위로 나눈 뒤 바스켓 평균을 내고, 날짜별 값을 등가중 평균했다. 전 기간을
한꺼번에 percentile로 나누지 않았다.

W40_H10_B (IC가 가장 높은 검정):

| | Q1 | Q2 | Q3 | Q4 | Q5 |
| --- | --- | --- | --- | --- | --- |
| realized excess | +0.00068 | +0.00225 | +0.00343 | +0.00279 | +0.00474 |
| MFE | +0.08889 | +0.07315 | +0.07182 | +0.07351 | +0.08785 |
| MAE | -0.07639 | -0.06254 | -0.05950 | -0.06255 | -0.07301 |

Q5-Q1 = +0.00406으로 조건 6은 충족한다. 다만 단조는 아니고(Q3 > Q4), MFE/MAE는 Q1과 Q5 양끝에서
함께 커지는 U자다. 이는 S(q)의 양끝이 **변동성이 큰 종목**을 고르고 있다는 뜻이며, 방향성
정보와는 별개다.

**MFE/MAE 계산 여부: 부분 YES.** query 실현 MFE/MAE는 계산했다(D0 secondary "query realized
mfe_h / mae_h by S(q) quintile"). 이웃 median MFE/MAE의 IC는 계산하지 않았다 - D2 neighbor
테이블 재독이 필요하고(§5.4와 같은 이유) gate 조건이 아니다.

---

## 8. 시간 블록 안정성

221개 평가일을 4개 연속 블록(56/55/55/55)으로 나눴다. 경계는 결과를 보기 전에 D0
`time_block_rule`로 정해졌고 바꾸지 않았다.

IC > 0인 블록 수: 4/4가 2검정, 3/4가 6검정, 2/4가 6검정. 조건 7(>= 3/4)을 6검정이 실패했다.

W40_H10_B의 블록별 IC: `[+0.0297, +0.0157, +0.0297, -0.0044]` - 마지막 블록에서 부호가 뒤집힌다.

---

## 9. 집중도

D0에는 집중도 gate 조건이 **없다.** 따라서 아래는 진단이며 판정을 바꿀 수 없다.

W40_H10_B에서 상위 3개 날짜가 |IC| 합계에서 차지하는 비중은 각각 1.36%, 1.35%, 1.24%다. 특정
소수 날짜가 결과를 지배하지 않는다. 즉 이 FAIL은 "몇 날이 망쳤다"가 아니라 신호가 고르게 약하다.

---

## 10. 다중검정과 부트스트랩

| 항목 | 값 (D0 동결) |
| --- | --- |
| 부트스트랩 | moving block, 블록 길이 20세션, 10,000 replicate, seed 20260917 |
| 구간 | percentile |
| 다중검정 | Bonferroni over 14, 양측 1 - 0.05/14 = **0.99643** |
| paired | N1·N2a 차이는 **같은 draw**에서 날짜별 대응차로 |

horizon별로 draw 행렬을 하나씩 만들어(같은 horizon의 검정은 같은 draw 공유) 대응차가 실제로
대응되게 했다. draw digest는 `summary.json`에 기록된다. 95% 구간도 함께 보고하지만 gate 판정은
Bonferroni 수준만 쓴다. **보정하지 않은 p로 통과시킨 검정은 없다.**

---

## 11. D0 계약의 실행 불가 항목 (DECLARED DEVIATION)

D0 `baseline_N1.draw`는 후보마다
`sha256('N1|seed|replicate|query_date|query_ticker|lib_date|lib_ticker')`를 계산해 정렬하라고
규정한다. 이 기계에서 실측한 결과:

```text
sha256 처리율        1.81 M hash/s (단일 코어, midstate 재사용 포함)
필요 해시 수         30,000 후보 x 66,300 query x 20 replicate x 14 검정 = 5.57e11
단일 코어 소요       86 시간
12코어 완전병렬 가정  7 시간
```

선언된 연구 중 **유일하게 실행할 수 없는 부분**이다. 구현은 D0의 키 **접두부**(후보 앞까지)를
sha256해 PCG64를 seed하고, 후보 순열의 균일 랜덤 앞부분을 뽑는 방식으로 대체했다(22분).

| 보존된 것 | 달라진 것 |
| --- | --- |
| 같은 후보 풀(같은 라이브러리·embargo·rv 분위·same-symbol 제외) | 특정 순열 |
| 무복원 균일 추출 | |
| (replicate, query) 결정성, 전역 난수 미사용 | |
| 같은 cap(종목 1, 날짜 5), 같은 top_k | |
| D0 seed 리터럴 `20260917`, replicate 0부터 | |

추출 집합과 그 순서의 **분포는 동일하다.** run identity에
`n1_draw_contract = d-n1-draw-v1-prefix-seeded`로 기록했고, 테스트가 이 편차 표기를 강제한다.

**이 편차가 판정에 미친 영향:** 조건 3(`delta_ic_vs_N1`)이 gate 조건이므로 원칙적으로 PASS는
N1에 의존한다. 그러나 이번 결과에서 **조건 2와 4도 14검정 전부 실패**했고 이 둘은 N1과 무관하다.
따라서 N1을 D0 원문대로 구현했더라도 **판정은 FAIL로 동일하다.**

---

## 12. Alpha Firewall (D3 이하)

D4는 IC를 계산하는 첫 단계다. 그 이전 단계가 여전히 눈감고 있음을 테스트로 고정한다.

- D2 모듈 14개: `close_return`, `excess_return`, `forward_return`, `compute_labels`, `mfe`,
  `mae`, `spearman`, `quintile`, `win_rate`, `baseline_n1` 식별자 0건 + label 값 모듈 import 0건
- D0~D3 모듈 18개: `spearman`, `pearson_ic`, `quintile`, `bootstrap`, `bonferroni`,
  `baseline_n1/n2`, `pass_fail`, `ic_point` 식별자 0건
- `gate.py`는 `app.*`, `json`, `pathlib`, `pyarrow`를 import하지 않는다 - **파일을 열 수 없는
  gate는 특정 파일에 맞춰 조정될 수 없다.**

---

## 13. 테스트

`test_strategy_d_d4.py` **45개**, D 전체 **167개** 통과.

| 묶음 | 내용 |
| --- | --- |
| 통계 | Spearman을 `1 - 6*sum(d^2)/(n(n^2-1))` 폐형식과 대조, 동률 평균순위, 평탄면 NaN |
| 분위 | 날짜 내부 등개수 분할, 단조 위반 수, 3개 미만이면 NaN |
| 블록 | 221일 -> 56/55/55/55, 연속·시간순 |
| 부트스트랩 | 블록 길이 20·10,000·seed·percentile이 D0 선언문과 일치, 블록 연속성, 재현성, Bonferroni가 95%보다 넓음, paired가 같은 draw |
| 피처 | 5개 공식 각각을 손계산과 대조, 백분위 표준화, rv 분위 경계 0.2/0.4/0.6/0.8 |
| N1 | 결정성, cap 준수, 자기 종목·FIGI 배제, replicate별 상이, D0 seed 리터럴 |
| N2b | 피처 선형결합은 잔차 0, 잔차가 5개 피처 전부와 직교 |
| **Gate** | 10개 조건이 D0 선언 목록과 **키까지 일치**, 각 조건 단독 실패 13케이스, 경계값(0.0은 "> 0" 아님, 149 != 150), NaN은 어떤 조건도 만족 못함, soft 조건만 실패 시 INCONCLUSIVE, 유의한 음수 IC는 INVERSE_EFFECT, 전략 판정 4경로 |
| 비선언 조건 | gate 조건 집합에 monotonic/concentration/sharpe/drawdown 없음 |
| 체인 | D2 -> D3 -> D4 전체를 합성 데이터로 실행, 부모 변조 거부, 산출물·스탬프, 2회 실행 digest 동일, N1 재현성 |

---

## 14. 성능

| 항목 | 값 |
| --- | --- |
| 실행 시간 | **3,807.7초 (63.5분)** |
| peak RSS | **2,449.9 MB** |
| 조합당 | 490 ~ 575초 (7조합) |
| 읽은 D3 행 | signal 928,200 + evaluation 331,500 |
| N1 draw | 7조합 x 221일 x ~300 query x 20 replicate |

N1·N2a는 (W, h)에만 의존하고 representation에는 의존하지 않으므로 7회만 계산해 A/B가 공유한다.
이 공유는 가정이 아니라 **검사**다: 두 representation의 라이브러리 행이 다르면 `HardFail`로 멈춘다.

D0에 D4 RSS 조건은 없다. 2 GB는 D2 설계 목표였고 여기서는 넘겼다(2.45 GB).

---

## 15. GATE-D-ALPHA 판정

```text
PASS         0 / 14
INCONCLUSIVE 0 / 14
FAIL        14 / 14
INVERSE_EFFECT 0 / 14

STRATEGY D PREVALIDATION = FAIL
```

D0 `decision`: "PASS: at least one of the 14 tests meets all 10 conditions" - 0개.
"INCONCLUSIVE: ... some test meets 1, 2, 3 and 4 but fails only 7, 8 or 9" - 조건 2·3·4가 전
검정에서 실패해 해당 없음. "or the data yields fewer than 150 evaluable dates" - 221일로 해당
없음. 따라서 **FAIL**.

### 15.1 D0가 지시하는 후속

D0 `pass_fail_policy.on_FAIL`:

> "Strategy D research ends; no backtester, adapter or trading spec"

따라서:

```text
Strategy D V1 종료
D5 Feature Ablation      진행 안 함
D6 Long Historical OOS   진행 안 함
Backtester               금지
Trading Rule Spec        금지
```

---

## 16. 결과 해석의 한계

말할 수 있는 것:

- 사전등록된 기준에서 **Pattern Alpha의 근거가 확인되지 않았다.**
- 14개 (W, h, rep) 중 gate를 통과한 것은 없다.
- 랜덤 아날로그(N1) 대비 우위가 Bonferroni 수준에서 확인되지 않았고, 절반 이상의 검정에서는
  단순 가격 피처 kNN(N2a)이 점추정으로 더 나았다.
- 신호가 남아 있을 가능성이 가장 큰 영역은 표현 B의 중간 horizon(W40/h10)이지만, 그것도 구간이
  0을 포함한다.
- 기간별로는 마지막 블록에서 부호가 뒤집히는 검정이 많다.

말할 수 없는 것:

- 수익이 난다 / 실거래 가능하다 - **근거 없음.**
- 백테스터 없이 기대수익을 추정하는 것 - **하지 않았고 할 수 없다.**
- "조건을 조금만 완화하면 통과한다" - 사전등록 위반이며 이 문서의 목적에 반한다.

---

## 17. D V2 RESEARCH IDEA (V1에 반영 안 함)

결과를 보고 떠오른 것은 여기에만 적고 V1에는 넣지 않는다. 실행하려면 **새 규칙 버전 선언**이
필요하다.

1. **표현 A와 B의 비대칭.** 14검정 중 IC 점추정 음수 3건이 전부 A(z정규화 경로)다. B(누적
   로그수익, 진폭 보존)가 일관되게 높다. 진폭이 모양보다 중요할 수 있다.
2. **horizon 의존성.** h=1이 가장 약하고 h=10 부근이 가장 강하다. 짧은 horizon에서는 미시구조
   잡음이 지배할 수 있다.
3. **sigma 가설의 반전.** 낮은 sigma에서 IC가 더 높은 검정이 여럿이다. V1의 신뢰도 정의가
   측정하려던 것과 반대를 재고 있을 가능성.
4. **MFE/MAE의 U자.** S(q) 양끝이 변동성 큰 종목을 고른다. 신호가 방향이 아니라 변동성을
   집어내고 있을 가능성 - 변동성 중립화가 필요할 수 있다.

**이 중 어느 것도 V1 판정을 바꾸지 않는다.**

---

## 18. 결정성

같은 parent D3·D2, 같은 code, 같은 rules, 같은 freeze로 2회 실행해 대조했다. **6개 content
digest가 전부 일치**했고 identity digest와 run id(`deval1-99574380a3d0`)까지 같았다. 두 run이
같은 디렉터리를 가리키므로 run 2는 `--runs-dir`로 분리해 실행했다.

| 산출물 | digest | 2회차 |
| --- | --- | --- |
| `testid_results` | `8913842fc9dfd480c2bdcd417ed19c3c…` | 일치 |
| `ic_daily` | `90bb62b91ea3b15e9d02b58cf381ec02…` | 일치 |
| `quintile_daily` | `1a360be8311b071da33bfdb19762f61f…` | 일치 |
| `sigma_daily` | `d5a29e608ac18b2cf1ca1162ea1bc05c…` | 일치 |
| `bootstrap` | `74a36be2a4fd4e4e4ea63514ec75edc3…` | 일치 |
| `gate_results` | `e7c8851e9d422d4ce384ab9c9ec09bdf…` | 일치 |

부트스트랩 draw digest(horizon별 5개), gate 판정, 14개 verdict 전체, 표본 집계도 모두 동일했다.
달라진 값은 `elapsed_seconds`(3,807.7 / 3,882.0)와 `peak_rss_mb`(2,449.9 / 2,442.2)뿐이며 둘 다
설계상 결정적 digest에서 제외된 runtime 값이다.

N1 replicate는 (replicate, query) 파생 seed라 전역 난수 상태에 의존하지 않는다. 20 replicate x
7조합 x 221일 x ~300 query = 928만 회의 난수 추출이 두 실행에서 같은 결과를 냈다는 것이 그
증거다.

---

## 부록: 재현

```bash
cd ~/usb
OPENBLAS_NUM_THREADS=8 PYTHONPATH=backend .venv/bin/python -u \
  -m app.dev.run_strategy_d_alpha --snapshot-id USB-HIST-V1 \
  --d3-run dsig1-eaeb6df9dea6 --d2-run dneigh1-fe0362523739
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/strategy_d -q
```

`--combination-limit N`은 앞 N개 (W, h) 조합만 도는 smoke 옵션이며, 평가 조합이 run identity에
들어가므로 전체 실행과 run id가 겹치지 않는다.
