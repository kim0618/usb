# Strategy D V2-A D1 Results (Data / PIT Pre-validation, GATE-D-V2A-1)

실행 2026-09-20 (집 PC). 단계 **D-V2A-1**. 선언 문서: `D_V2A_SCREENING_CONTRACT_V1.md`,
`d_v2a_rules_v1.json`(canonical `2b060ceb3474…fe229`).

```text
GATE-D-V2A-1 = PASS

이번 단계가 계산한 것: 좌표 10개, 횡단면 rank, B0 값, universe/library/label 적격성,
                      PIT mutation, 결정성, 표본·검정력 재계산
이번 단계가 계산하지 않은 것: Neighbor Search, Top-K, A(q), IC, delta IC, 분위, bootstrap,
                              PASS/BORDERLINE/FAIL 스크리닝 판정
V1 변경 0 · 커밋 0 · 푸시 0 · 배포 0
```

---

## 1. Repository / Rules / Freeze

| 항목 | 값 |
| --- | --- |
| HEAD | `4e84ec12a1fa176a19807f0d966f45925623763b` (branch `main`) |
| 작업 트리 | V2-A 신규 파일만 추가. V1 경로 수정 0건 (§9) |
| rules canonical checksum | `2b060ceb34748c3d933d01aee2cf7669ea48bf72a95ae811eba978a3aa3fe229` **MATCH** |
| rules 파일 sha256 | `7ddcb93948339ed7bc206db50240f9fedd53a4faf13f6b7f2179edd6cbfb4440` |
| 계약문 sha256 | `d6b956f7ba2d3180853f280a5f0decd0002cc3941e3c44103c303daf6ec80287` |
| snapshot / freeze | `USB-HIST-V1` / `STRATEGY_C_RAW_FREEZE_V1`, status `FROZEN` |
| grid | 2024-09-17 ~ 2026-09-16, **501세션**, digest `830744f64a2d…66c9` **MATCH** |
| `freeze_digest` | `9ebd6c29c66728ac…eafe` **MATCH** |
| `source_digest` | `adc4f1919dd83577…32c02` **MATCH** |
| `d_read_digest` | `7e790a8dcf1db8ce…7d55` (V1 D1이 기록한 값과 동일) |
| run id / identity | `dv2a1-cec5cc9bc674` / `cec5cc9bc674eea0…33c0` |
| code digest (V2-A 패키지) | `dc053e09dc6451e9…0607` |

D0 산출물 5종(`D_V2A_CONCEPT_V1.md`, `D_V2A_REUSE_MATRIX_V1.md`, `D_V2A_SCREENING_CONTRACT_V1.md`,
`d_v2a_rules_v1.json`, `d_v2a_rules_v1.sha256`)이 모두 존재하고, 다른 세션이 `docs/backtest/strategy_d_v2`
또는 V1 경로를 수정한 흔적은 없다.

**digest의 출처를 명시한다.** 요청문 §2는 V1 D1 artifact에서 네 digest를 읽으라고 했으나, 구현은
`d_v2a_rules_v1.json` §data에 적힌 값을 권위로 쓴다. 그 값들은 D0 선언 시점에 checksum 안에
동결됐고, artifact 파일은 언제든 덮어쓸 수 있기 때문이다. 대신 로드된 저장소의 실제 digest를 선언값과
대조해 하나라도 다르면 R2로 멈춘다(`assert_freeze_binding`). `d_read_digest`는 선언에 없는 값이므로
로드 시 재계산하고, V1 D1이 기록한 `7e790a8dcf1db8ce…7d55`와 같은지 이 문서에서 대조했다.

checksum은 코드에 상수로 박지 않고 `d_v2a_rules_v1.sha256`에서 읽는다. 패키지 어느 파일에도
canonical digest 문자열이 존재하지 않음을 테스트로 고정했다(`test_rules_checksum_is_read_from_the_declaration_file`).

CURRENT 포인터는 `run_context.json`에 기록만 하고 따라가지 않았다.

---

## 2. 좌표 10개 실측

| # | 좌표 | lookback | 정의 창 | 미정의 처리 |
| --- | --- | --- | --- | --- |
| 1 | `return_5` | 5 | `P(D)/P(D-5) - 1` | 비유한 시 제외 |
| 2 | `return_20` | 20 | `P(D)/P(D-20) - 1` | 동일 |
| 3 | `return_60` | 60 | `P(D)/P(D-60) - 1` | 동일 |
| 4 | `dist_to_20d_high` | 20 | `P(D)/max(H(D-19..D)) - 1` | `max(H) <= 0` |
| 5 | `position_in_60d_range` | 60 | `(P - min L)/(max H - min L)`, 창 `D-59..D` | `max == min` |
| 6 | `rv_20` | 20 | `std_ddof1(ln P(t)/P(t-1))`, `t in D-19..D` | 비유한 |
| 7 | `atr_ratio_20_60` | 60 | `ATR_20/ATR_60`, `TR = max(H-L, |H-P(t-1)|, |L-P(t-1)|)` | `ATR_60 == 0` |
| 8 | `tr_today_ratio` | 20 | `TR(D)/ATR_20` | `ATR_20 == 0` |
| 9 | `rvol_today` | 20 | `V(D)/mean V(D-20..D-1)` | 분모 0 |
| 10 | `dollar_volume_ratio_20_60` | 60 | `mean DV(D-19..D)/mean DV(D-59..D)` | 분모 0 |

**최대 lookback 60세션**이 코드에서 확인됐다. 선언의 `max_lookback_sessions`와 좌표별 lookback의
최댓값이 일치하지 않으면 `load_rules`가 R1으로 멈춘다.

경계 실측(테스트 `test_return_60_reads_exactly_sixty_sessions_back`): D-60 종가를 바꾸면
`return_60`이 움직이고, D-61을 바꾸면 움직이지 않는다.

`TR`은 `np.maximum`으로 계산한다. `np.fmax`는 이전 종가가 없을 때 `H-L`로 조용히 되돌아가므로
결측이 숨는다. 결측은 전파돼 창을 미정의로 만들어야 한다.

### 2.1 벡터 유효성

```text
평가·라이브러리 구간(index 0..480) 적격 ticker-date   1,070,806
그중 10좌표 전부 유효한 벡터                          1,070,806   (100.0%)
```

| 상태 | 수 |
| --- | --- |
| OK | 1,070,806 |
| NOT_ELIGIBLE | 2,105,534 |
| INSUFFICIENT_HISTORY | 0 |
| INCOMPLETE_BAR | 0 |
| ZERO_DENOMINATOR | 0 |
| NON_FINITE | 0 |

적격 창 중 좌표가 하나라도 빠진 경우가 **0건**이다. 이유는 구조적이다. 유니버스 규칙이 이미
`D-60..D` 61개 연속 봉을 요구하므로 60세션 이내 좌표는 전부 계산되고, 분모 보호 조건(60일 레인지
0, ATR 0 등)은 유동 종목에서 발생하지 않는다.

분모 가드 발동 수는 패널 전체와 적격 창을 나눠 보고한다. 패널 전체 수치(예 `atr_ratio_20_60`
1,073,240)는 **봉이 아예 없는 칸**이 대부분이고, 연구가 실제로 잃은 창은 적격 기준 **전 좌표 0건**이다.

### 2.2 rank scaling과 선언 해석

```text
scaling   (평균순위 - 1)/(n - 1), 동점은 평균순위, NaN은 순위에 불참
모집단    그 날짜의 적격 유니버스 중 10좌표를 모두 갖춘 이름
```

선언문은 모집단을 "그 날짜의 전체 적격 유니버스"로, 별도 조항에서 "좌표가 하나라도 비유한이면
query와 library 양쪽에서 제외"로 적는다. 구현은 두 조항이 동시에 성립하는 읽기를 택했고, 이 선택을
**D1 선언 해석**으로 기록한다. 대안(좌표별로 적격 이름 전체에 대해 순위)도 함께 계산해 비교했다.

```text
두 프레임의 좌표별 최대 차이: 10좌표 전부 0.0
```

적격 == 벡터 유효가 모든 날짜에서 성립하므로 두 프레임은 **이 데이터셋에서 수치적으로 동일**하다.
해석 선택이 결과에 미치는 영향은 0이며, 주장 대신 측정으로 남긴다.

동점 처리와 순위 모집단은 테스트로 고정했다(동일 경로 두 종목이 같은 percentile을 받고, ticker
순서가 percentile을 바꾸지 않는다).

### 2.3 rank invariance (상대강도 좌표 제외 근거)

```text
rank(return_h - 날짜상수) == rank(return_h)
```

D0 §3.4가 좌표에서 시장 상대강도를 뺀 근거를 테스트로 고정했다
(`test_rank_invariance_to_a_market_wide_constant`, 4개 날짜 x 3개 추세 좌표). 이 계약이 깨지면
중복 좌표가 벡터에 들어올 수 있으므로 주석이 아니라 테스트로 유지한다.

---

## 3. Universe / Query 표본

| 항목 | 값 | 대조 |
| --- | --- | --- |
| 평가 구간 | index [260, 480] = **221일** (2025-10-01 ~ 2026-08-18) | D0 예상 221 **일치** |
| 적격 ticker-date (평가일) | **581,156** | V1 D1 581,156 **일치** |
| 날짜당 적격 종목 | 최소 2,551 / 중앙값 2,619 / 평균 2,630 / 최대 2,756 | V1 D1과 동일 |
| 날짜당 벡터 유효 종목 | 최소 2,551 / 중앙값 2,619 / 최대 2,756 | 적격과 동일 |
| query 표본 | 221 x 300 = **66,300** | D0 예상 66,300 **일치** |
| 고유 query ticker | **3,331** | D0 예상 3,331 **일치** |
| 벡터 유효 query | **66,300** (미정의 0, share 0.0) | - |
| h=5 label 유효 query | **66,162** | D0 예상 약 66,100 **일치** |
| 적격 300 미만 날짜 | 0 | - |
| `min_valid_queries_per_date`(100) 미만 날짜 | 0 | - |

제외 사유 분포(평가일, 첫 일치 우선): ELIGIBLE 581,156 / LOW_ADV 241,291 / NOT_MEMBER 240,778 /
LOW_PRICE 222,724 / NO_HISTORY 108,777 / SPLIT_WINDOW 5,067 / CA_SUSPECT 1,347.
**V1 D1의 분포와 완전히 같다.** 유니버스 규칙을 바꾸지 않았고 좌표 추가로 탈락한 행이 0건이므로
`INVALID_FEATURE` / `ZERO_DENOMINATOR` 항목은 전부 0이다.

query 표본 해시는 V1과 같은 `Q|20260917|{D}|{ticker}`이고, 표본은 label을 보기 전에 뽑힌다.
seed를 바꾸지 않았으므로 **V1과 같은 66,300개 query**를 본다.

---

## 4. Historical Library 적격성 (Top-K 탐색 없음)

```text
h = 5, L = 60, stride 5, embargo d + 5 <= D - 60
```

| 항목 | 값 |
| --- | --- |
| stride 날짜 (grid 전체 열거) | **85** (index 60..480) |
| stride 날짜 (h=5 embargo 상한까지) | **72** (index 60..415) |
| 마지막 사용 가능 end index | 415 = `eval_end(480) - 60 - 5` |
| library row (적격 & 벡터 & label 유효) | **180,947** |
| stride 날짜당 row | 최소 2,357 / 중앙값 2,519 / 최대 2,654 |
| query 날짜에서 보이는 row | **최소 67,604** / 중앙값 123,559 / 최대 180,947 |
| `top_k` = 50 대비 | 최소값이 50의 1,352배. 구조적 여유 |
| 제외 | NOT_ELIGIBLE 275,210 / VECTOR_UNDEFINED 0 / LABEL_INVALID 323 |

**D0 예상 "stride 날짜 약 85"와의 차이를 설명한다.** 85는 grid 전체에 대한 stride 열거값이고(V1 D1이
같은 방식으로 기록했다), h=5의 embargo가 `d <= D - 65`를 요구하므로 마지막 평가일 480에서도
415까지만 라이브러리가 된다. 따라서 **실사용 가능한 stride 날짜는 72**다. 버그가 아니라 embargo
상한의 정의이며, 두 수를 모두 보고한다.

Top-K 선택·거리 계산은 이 단계에서 실행하지 않았다. `EmbargoView.assert_candidates`로 prefix 경계만
검사했다.

---

## 5. B0 (값 생성만, 평가 없음)

```text
B0(q) = (1/10) * sum_i s_i * rank_i(q)
rows  = 66,300 (벡터 유효 query 전부)
min / median / max = -0.61682 / -0.197243 / 0.23086
```

| 좌표 | 부호 | prior |
| --- | --- | --- |
| `return_5` `return_20` | -1 | STRONG |
| `return_60` `dist_to_20d_high` `position_in_60d_range` | +1 | STRONG |
| `rv_20` | -1 | STRONG |
| `atr_ratio_20_60` `tr_today_ratio` `rvol_today` `dollar_volume_ratio_20_60` | -1 | WEAK |

중앙값이 -0.197인 것은 부호 합이 `-4`이고 순위 평균이 0.5이기 때문이다(`-4/10 x 0.5 = -0.2`).
B0는 Spearman 순위로만 쓰이므로 수준은 판정과 무관하다.

`B0-strong`(STRONG 6좌표) 66,300행도 함께 생성했다. secondary 진단용이며 가중치에 관여하지 않는다.

### 5.1 B0 Alpha firewall

```text
IC(B0, future return)   계산하지 않음
B0 분위별 수익          계산하지 않음
B0 hit rate             계산하지 않음
B0 vs Analog            계산하지 않음
```

`b0_composite`는 rank 행렬 외의 인자를 받지 않는다. 모듈이 label 계열 모듈을 import하지 않음을
AST 테스트로 고정했고, 아티팩트 스키마는 `pit.assert_no_label_values`가 키 이름 수준에서 막는다.

---

## 6. Label / Embargo

이 단계는 **label 유효성만** 계산했다. 값(`close_return`, `excess_return`, MFE/MAE)은 계산하지
않았고, 값을 만드는 V1 `labels.py`는 V2-A 패키지가 import하지 않는다(화이트리스트 테스트).

| h | valid | no_entry_bar | missing_horizon_bar | label_ca_suspect |
| --- | --- | --- | --- | --- |
| 1 | 2,565,392 | 605,255 | 605,255 | 5,693 |
| 3 | 2,534,265 | 605,255 | 615,518 | 5,693 |
| **5** | **2,521,241** | 605,255 | 625,764 | 5,693 |
| 10 | 2,489,943 | 605,255 | 651,424 | 5,693 |
| 20 | 2,422,797 | 605,255 | 702,779 | 11,496 |

embargo 실측 계약:

```text
limit_idx = D - L - h = D - 65
query 300 기준 235까지 허용, 236은 PointInTimeViolation
```

h<=10의 label CA 창은 V1 계약대로 `D..D+10`이다. embargo와 겹치지 않음을 산술로 고정했다:
이웃의 CA 창 끝 `d + 10 <= (D - 65) + 10 = D - 55 < D`. 테스트
`test_embargo_keeps_the_ca_window_of_a_neighbour_before_the_query`가 이 부등식을 지킨다.

---

## 7. PIT

| 검사 | 방법 | 결과 |
| --- | --- | --- |
| Future price mutation | D 이후 close/high/low를 x100, x0.01로 변경 | D행 10좌표 **불변** |
| 동일일 price (양성대조) | D행 종가 x1.10 | 6개 이상 좌표가 **변함** |
| Future volume mutation | D 이후 volume x500 | D행 10좌표 **불변** |
| 동일일 volume (양성대조) | D행 volume x4 | `rvol_today`·거래대금비 **변함** |
| Future split mutation | D+1에 1:10 분할 추가 | D행 10좌표 **불변** |
| 과거 split (양성대조) | D-10에 1:4 분할 | `return_20` **변함** |
| Future reference mutation | D 이후 스냅샷 추가/축소 | D의 membership **불변** |
| Rank leakage (미래) | 다른 종목의 **D 이후** 값 x50 | D의 10좌표 rank **불변** |
| Rank leakage (동일일, 양성대조) | 다른 종목의 **D 당일** 값 x1.5 | D의 rank **변함** |
| Universe mutation | D 이후 상장되는 종목 추가 | D의 rank 프레임 **불변** |
| Incomplete current bar | D의 O/H/L/C/V 중 하나를 NaN | 벡터 **미생성**(INCOMPLETE_BAR) |
| Split window | `(D-60, D]` 내부 분할 | 창 **제외**(SPLIT_WINDOW). 억지 보정 없음 |
| Truncation (실데이터) | 12개 평가일에서 패널을 그 날짜로 절단해 재계산 | 120개 좌표 행 **bit 동일**, 불일치 0 |

절단 감사 날짜: index 260, 278, 296, 314, 332, 350, 368, 386, 404, 422, 440, 458
(2025-10-01 ~ 2026-07-17). 절단은 이후 봉·이후 분할·이후 스냅샷을 모두 제거하므로, 어느 좌표가
앞을 봤다면 값이 달라진다. `equal_nan=True` 비교로 **오차 허용 없이** 일치했다.

거래량 창 보호: `rvol_today`(D-20..D)와 `dollar_volume_ratio_20_60`(D-59..D)이 모두
`(D-60, D]` 분할 보호창 안에 있다. `pit.assert_volume_window_protected(61, 60)`은 HardFail이다.
따라서 `V x F` 변환 선언이 필요 없고, 원시 거래량을 그대로 쓴다.

```text
PIT violations: 0
```

---

## 8. 결정성 / 입력 불변

### 8.1 같은 프로세스 안에서 2회 계산

| 대상 | 일치 |
| --- | --- |
| raw feature matrix (10좌표) | 일치 |
| validity mask | 일치 |
| vector status | 일치 |
| rank feature matrix (10좌표) | 일치 |
| B0 rows | 일치 |

digest(일부):

```text
raw_feature_matrix   75f6810864c2a9e4…
rank_feature_matrix  2749496384c2d675…
validity_mask        c1e02937ab66bc39…
b0_rows              b06da81e77782d61…
query_sample         02b74f44b1640…:0175d3d093aee…
library_eligibility  43c22b259f37c5e2…:2b0932ccd8759…
```

### 8.2 독립 실행 2회

같은 freeze / rules / grid / code로 CLI를 두 번 실행했다.

```text
run 1  run_id dv2a1-cec5cc9bc674   total 94.6s
run 2  run_id dv2a1-cec5cc9bc674   total 95.3s
d1_report.json: timing 블록을 제외하고 완전히 동일
```

`data/runtime/strategy_d_v2/runs/`에는 같은 단계의 이전 run 두 개(`dv2a1-101a8e2c2cfd`,
`dv2a1-57a9384f307e`)가 남아 있다. 둘 다 PASS였으나 **코드 digest가 다른 중간본**이므로
authoritative run은 `dv2a1-cec5cc9bc674` 하나다. D-V2A-2는 이 run id와 identity digest를 부모로
바인딩한다.

run id가 같다는 것 자체가 identity에서 시각·호스트·경로가 빠져 있다는 확인이다.

### 8.3 입력 불변

```text
PRE  read-set digest  7e790a8dcf1db8cee336aa281f5b9b7b8481402bbc1d828f27d19cccb0dd7d55
POST read-set digest  7e790a8dcf1db8cee336aa281f5b9b7b8481402bbc1d828f27d19cccb0dd7d55
identical = true
```

다르면 `F1 INPUT_MUTATED`로 즉시 정지한다. 이 검사의 민감도도 테스트로 고정했다(합성 저장소의
파일 1바이트를 바꾸면 digest가 달라진다).

---

## 9. V1 격리

| 검사 | 결과 |
| --- | --- |
| V1 패키지 code digest | `a8bb64aa52320773…` = V1 마지막 run(`deval1-99574380a3d0`)이 기록한 값과 **동일** |
| V1 rules 파일 sha256 | `b6309ae40aec9a48…` = V1 D1 기록값과 **동일** |
| V1 Pre-flight 문서 sha256 | `aa481281cd7e9d05…` = V1 D1 기록값과 **동일** |
| V1 run artifacts | 수정 0건. V2-A는 `data/runtime/strategy_d_v2/runs/`에만 쓴다 |
| V1 결과 하드코딩 | 없음. `HISTORICAL_ANALOG_V1`, `W40_H10`, `0.0177`, V1 checksum 문자열이 패키지에 존재하지 않음을 테스트로 고정 |
| V1 artifact 경로 참조 | 없음. `data/runtime/strategy_d/`, `dpit1-`, `deval1-` 등이 코드에 없음 |

V1에서 import하는 모듈은 **화이트리스트**다.

```text
허용  models  source  universe  identity  label_extension  features(percentile_rank)  pit
금지  encoder  similarity  neighbor_search  library  signal  evaluation  baselines
      metrics  resample  gate  labels  config  d1~d4
```

`labels`(값 생성)가 금지 목록에 있다는 점이 이 단계의 alpha firewall이다. A/B/C 알파 모듈과
adapter/SimBroker 계열도 import 금지이며, 전부 AST 테스트로 강제된다.

---

## 10. 표본 Gate (S1) 판정

선언문에서 읽은 문턱과 실측:

| 조건 | 실측 | 문턱 | 판정 |
| --- | --- | --- | --- |
| evaluable dates | **221** | `>= 150` | PASS |
| valid queries | **66,162** | `>= 20,000` | PASS |
| unique query tickers | **3,331** | `>= 1,000` | PASS |
| VECTOR_UNDEFINED share | **0.0** | `<= 0.05` | PASS |
| INSUFFICIENT_NEIGHBORS share | 측정 불가 | `<= 0.05` | **D-V2A-2로 이월** |

마지막 항목은 이웃을 실제로 찾아야 나오는 값이므로 D1의 판정 입력이 아니다. 나머지 네 항목이
전부 충족되므로 **현재 평가일 수는 primary screening에 충분하다**(221 >= 150). 150 미만이 아니므로
D-V2A-1 FAIL/INCONCLUSIVE 계약은 발동하지 않는다.

---

## 11. MDE 재검증 (실측 날짜 수 기준)

입력은 선언에 동결된 잡음 상수(V1이 측정한 날짜별 IC 표준편차 0.066, SE 팽창 1.25)와 **실측
평가일 221**뿐이다. 알파 결과는 쓰지 않았다.

```text
SE(delta) = 0.066 * sqrt(2(1 - rho)) / sqrt(221) * 1.25
```

| `rho` | `sd(delta)` | `SE(delta)` | MDE(유의) | `delta80`(S5 기준) | 선언 대비 차이 |
| --- | --- | --- | --- | --- | --- |
| 0.0 | 0.093338 | 0.007848 | **0.015382** | **0.009605** | MDE -1.8e-05 |
| 0.5 | 0.066000 | 0.005550 | **0.010877** | **0.007671** | MDE -2.3e-05 |
| 0.8 | 0.041742 | 0.003510 | **0.006879** | **0.005954** | MDE -2.1e-05 |
| 0.9 | 0.029516 | 0.002482 | **0.004864** | **0.005089** | MDE -3.6e-05 |

```text
MDE range     0.004864 ~ 0.015382
delta80 range 0.005089 ~ 0.009605
```

평가일이 D0 가정과 같은 221이므로 선언 표와의 차이는 **반올림 수준(최대 4.6e-05)**이다.
D0 §8의 결론은 그대로 유지된다: 이 데이터가 탐지할 수 있는 것은 **큰 우위뿐**이고, 작지만 진짜인
우위는 BORDERLINE 또는 FAIL로 떨어진다.

---

## 12. 신규 코드와 테스트

| 모듈 | 줄수 | 역할 |
| --- | --- | --- |
| `config.py` | 347 | 선언 로딩·검증(좌표·부호·문턱·검정력 상수) |
| `structure_features.py` | 270 | 좌표 10개 + 상태 + rank 프레임 |
| `structure_encoder.py` | 72 | 벡터 조립, 등가중 Euclidean(픽스처용) |
| `b0_composite.py` | 68 | B0 / B0-strong |
| `pit.py` | 92 | embargo, 창 보호, 아티팩트 firewall |
| `identity.py` | 95 | V2-A run identity |
| `models.py` | 83 | 상태·사유 어휘 |
| `d1.py` | 543 | D-V2A-1 실행기 |
| 합계 | **1,576** | |
| 테스트 | **875** (65 test) | features 26 / pit 29 / d1 10 |

D0 Reuse Matrix는 신규를 "약 380~480줄"로 추정했다. 실제 1,576줄인 이유는 추정이 좌표·B0·gate
조건표만 셌고, **선언 로더(347)와 D1 실행기(539)를 EXTEND로 분류**했기 때문이다. V1의 `config.py`와
`d1.py`는 V1 규칙 스키마와 14검정 구조에 묶여 있어 재사용보다 V2-A 전용 작성이 옳았다. 좌표·인코더·
B0만 세면 410줄로 추정 범위 안이다. 추정 오차의 원인을 기록하고, Reuse Matrix는 수정하지 않는다.

---

## 13. Regression

| 대상 | 결과 |
| --- | --- |
| `backend/tests/strategy_d_v2` (신규) | **65 passed** |
| `backend/tests/strategy_d` (V1) | **167 passed** (V2 포함 시 232) |
| `backend/tests` 전체 | **1,841 passed / 1 skipped** (955.68s) |

귀속을 구분해 둔다. 작업 중 **다른 세션이 같은 리포에 `strategy_e1_premarket`,
`strategy_e1_h5_confirm` 및 그 테스트를 추가**했고(작업 시작 시점 git status에는 없던 경로),
전체 스위트 수가 1,787 -> 1,841로 늘어난 것은 그 때문이다. 실패 0건이므로 귀속 분쟁은 없다.
D/V2-A 경로와는 겹치지 않으며, V1 패키지 code digest는 V1 마지막 run이 기록한 값과 여전히 같다.

---

## 14. GATE-D-V2A-1

| # | 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| 1 | V2-A checksum MATCH | PASS | §1 |
| 2 | freeze / grid MATCH | PASS | §1 |
| 3 | 10 feature formula tests | PASS | 26 test, 참조 구현 대조 |
| 4 | feature future leakage 0 | PASS | §7 |
| 5 | volume future leakage 0 | PASS | §7 |
| 6 | split / reference PIT | PASS | §7 |
| 7 | rank universe PIT | PASS | §7 |
| 8 | B0 deterministic | PASS | §8.1 |
| 9 | label validity PIT | PASS | §6 |
| 10 | library eligibility deterministic | PASS | §8.1 digest |
| 11 | query sample deterministic | PASS | §8.1 digest |
| 12 | PRE/POST read-set digest 동일 | PASS | §8.3 |
| 13 | evaluation sessions >= 문턱 | PASS | 221 >= 150 |
| 14 | MDE가 D0 가정과 일치 | PASS | §11, 차이 <= 4.6e-05 |
| 15 | V1 files / results unchanged | PASS | §9 |
| 16 | Alpha metric 계산 0 | PASS | §5.1, §15 |

```text
GATE-D-V2A-1 = PASS
```

---

## 15. Alpha firewall 최종 확인

```text
IC calculated        NO
Q5/Q1                NO
Analog search        NO
Neighbor selection   NO
Label values read    NO
Bootstrap            NO
Screening verdict    NO
```

아티팩트(`d1_report.json`, sha256 `73dfdf28a253385c…`)에 `close_return` / `excess_return` /
`mfe` / `mae` 문자열이 존재하지 않음을 실행 중 가드와 테스트 양쪽에서 확인했다.

---

## 16. 다음 단계

```text
D-V2A-2  STRUCTURE ANALOG ENGINE
  structure vector -> 등가중 Euclidean -> 적격 library -> top-50
  여전히 Alpha는 보지 않는다 (IC / 분위 / gate 없음)
```

이 단계가 넘기는 값: run `dv2a1-cec5cc9bc674`, identity `cec5cc9bc674eea0…33c0`,
freeze/grid digest, library 적격 행 180,947(h=5), query 표본 66,300, 벡터 digest 6종.
