# Strategy D V2-A D3 Results (Structure Analog Signal, GATE-D-V2A-3)

실행 2026-09-20 (집 PC). 단계 **D-V2A-3**. 부모: D1 `dv2a1-cec5cc9bc674`, D2 `dv2a2-bf9ceb93dd23`.
선언 문서: `D_V2A_SCREENING_CONTRACT_V1.md`, `d_v2a_rules_v1.json`(canonical `2b060ceb3474…fe229`).

```text
GATE-D-V2A-3 = PASS

이번 단계가 계산한 것: 이웃 3,315,000건의 excess_return_5 결합, A(q) = 상위 50 median,
                      B0/B0-strong 승계, query 평가 라벨(별도 아티팩트), 미래변조 감사
이번 단계가 계산하지 않은 것: IC(A), IC(B0), delta IC, 분위, bootstrap, 시간블록,
                              PASS/BORDERLINE/FAIL, 장기데이터 구매 판단
V1 변경 0 · A/B/C/E 변경 0 · 커밋 0 · 푸시 0 · 배포 0
```

---

## 1. START GATE

| # | 확인 | 결과 |
| --- | --- | --- |
| 1 | rules checksum | `2b060ceb3474…fe229` **MATCH** (`.sha256`에서 읽음) |
| 2 | D1 parent COMPLETE / identity | PASS, `cec5cc9bc674eea0…33c0` |
| 3 | D2 parent COMPLETE / identity | PASS, `bf9ceb93dd233d08…c96b`, 부모가 위 D1임을 확인 |
| 4 | D2 `neighbors.parquet` digest | `961b79c170f08c23…` **MATCH** (12개 컬럼 재해시) |
| 5 | D2 query manifest | D1 표본 digest와 **MATCH** |
| 6 | D2 feature schema | `0b222cf96cccfed2…` **MATCH** |
| 7 | freeze / grid / read digest | 세 값 모두 선언·D1·D2·실제 로드 **4중 일치** |
| 8 | D1 B0 artifact digest | `b06da81e77782d61…` / strong `546b99ff24108165…` **MATCH** |
| 9 | V1 files/results unchanged | V1 code digest = V1 D4 run 기록값 (§9) |
| 10 | parent 명시 입력 | CLI가 `--parent-d1-run-id`, `--parent-d2-run-id`를 **필수**로 받음 |

최신 run 자동선택·`CURRENT` 추종 경로는 코드에 없다.

---

## 2. 실행 정보

| 항목 | 값 |
| --- | --- |
| HEAD / branch | `4e84ec12a1fa176a19807f0d966f45925623763b` / `main` |
| run id | **`dv2a3-eda7523e6203`** |
| identity digest | `eda7523e62034fddc0602391a22e034078d86be58b77dd3c6e4a67d069721255` |
| code digest (V2-A 패키지) | `98a04ecaf95b3dc1…8bbe` |
| parent chain | D3 -> D2 `bf9ceb93dd233d08…` -> D1 `cec5cc9bc674eea0…` |
| label value contract | `d-label-value-v1` (V1과 공유) |
| signal contract | `d-v2a-analog-signal-median-v1` |
| evaluation contract | `d-v2a-forward-excess-v1` |

---

## 3. 부모 재현 검증

D1은 digest만 발행했으므로 D3도 좌표를 재계산하고 그 위에 무엇을 쌓기 전에 대조한다.
D3가 실제로 쓰는 8종만 요구한다(라이브러리 digest는 D2의 COMPLETE 토큰으로 상속).

```text
raw_feature_matrix · rank_feature_matrix · validity_mask · vector_status
label_validity_primary · query_sample · b0_rows · b0_strong_rows
→ 8/8 일치
```

D2 이웃 아티팩트는 12개 digest 컬럼을 다시 읽어 `column_digest`를 재계산해 대조했고,
행 배치(query 블록 연속, 블록 내부 rank 1..50, 블록 내 query 식별자 불변)와 query 표본 순서가
D1 표본과 같은지도 검사했다. 어긋나면 R2/R11로 정지한다.

---

## 4. Historical label join

```text
h = 5 (primary only). 다른 horizon은 만들지 않았다.
```

| 항목 | 값 |
| --- | --- |
| 이웃 행 | **3,315,000** (66,300 query x 50) |
| label 유효 | **3,315,000** (100.0%) |
| label 무효 | **0** |
| query당 유효 이웃 | 최소 50 / 최대 50 |
| benchmark | 같은 세션의 label 유효 적격 유니버스 median `close_return_5` |

D1/D2가 "라벨이 결정 가능한 창만 라이브러리에 넣는다"는 계약으로 행을 admit했으므로 50/50이
**요구사항**이다. 하나라도 무효면 51위 이웃으로 채우지 않고 `PARENT_CONTRACT_VIOLATION`으로
정지하도록 구현했다(테스트로 고정).

### 4.1 excess 정의가 두 경로에서 같다는 증명

두 경로가 "같은 공식을 각자 구현"하는 구조가 아니다. **행렬이 하나**이고 인덱스 집합이 둘이다.

```text
evaluation_labels.forward_excess(panel, validity, eligible, h=5)
        → (T, N) excess 행렬 하나
            ├─ gather(neighbor_end_idx, neighbor_ticker_col)  → A(q) 입력
            └─ gather(query_date_idx,  query_ticker_col)      → 평가 라벨
```

V1 `labels.compute_labels`(선언 전사본, C `LabelSet`에 대해 테스트로 고정됨)를 그대로 쓴다.
정의가 어긋날 자리가 존재하지 않는다.

---

## 5. Analog signal A(q)

```text
A(q) = median( 수락된 top-50 이웃의 excess_return_5 )
rows 66,300 · 전부 유한 · group size 50
distance weighting: NONE (등가중)
```

`analog_signal.py`는 패키지에서 가장 작은 모듈이고 의도적으로 무지하다. **인자가 (values,
group_size) 둘뿐**이라 거리도, query 식별자도, query의 미래도 도달할 경로가 없다. 저장된 신호가
자기 입력의 median과 일치하는지도 실행 중에 재확인한다.

짝수 50개의 median은 가운데 두 값의 평균(일반적 median 정의, V1 신호와 같은 관례)이며 테스트로
고정했다. mean / 가중 median / trimmed / top-20 경로는 코드에 없다.

---

## 6. B0 승계

| 항목 | 값 |
| --- | --- |
| B0 rows | 66,300, D1 digest **일치** |
| B0-strong rows | 66,300, D1 digest **일치** |
| 역할 | D-V2A-4 comparator 컬럼. 이번 단계에서 **평가하지 않음** |

D3는 B0를 새로 정의하지 않고 D1과 같은 부호·등가중으로 재계산해 digest 동일성을 확인했다.

---

## 7. Query 평가 라벨 (별도 경로·별도 파일)

| 항목 | 값 |
| --- | --- |
| rows | 66,300 |
| valid | **66,162** |
| invalid | **138** |
| 파일 | `evaluation_labels.parquet` (signal과 물리적으로 분리) |

D1이 예상한 66,162와 **정확히 일치**한다. 무효 query는 제거하지 않는다. A(q)도 B0도 정상
생성되고 평가 라벨만 NaN + `query_label_valid = false`로 남으며, 어느 날짜의 유효 집합을 쓸지는
D-V2A-4가 정한다.

signal 아티팩트에는 `excess_return` / `realized` / `label_valid` 계열 컬럼이 존재하지 않는다.

---

## 8. PIT

| 검사 | 결과 |
| --- | --- |
| label embargo `d + 5 <= D - 60` | 3,315,000행 전수 재검사, 최대 초과 **0 세션** |
| violations | **0** |

D2가 이미 이 규칙으로 선택했지만, **결과를 숫자로 바꾸는 단계가 그 시점을 검증하지 않는 유일한
단계가 되어서는 안 되므로** 행 단위로 다시 확인한다.

### 8.1 미래 query 변조 감사 (실데이터)

3개 평가일(index 260 / 333 / 406)에서 query 날짜 이후의 종가·시가 x100, 고가 x100, 저가 x0.01,
거래량 x500으로 치환하고 단계를 통째로 다시 수행했다.

```text
queries 900
A(q)   불변
B0(q)  불변
findings 0

query 평가 라벨: 900건 중 894건이 변함  (나머지 6건은 변조 전후 모두 무효 라벨)
```

앞의 절반만 검사하면 "아무것도 계산하지 않는 파이프라인"도 통과하므로, **라벨이 실제로 움직였는지를
양성대조로 함께 요구**한다.

### 8.2 라벨 창 경계 (합성 테스트)

| 변조 위치 | 요구 | 결과 |
| --- | --- | --- |
| `d+11` 이후 (CA 창 밖) | historical excess·유효성 불변 | PASS |
| `d+1..d+5` (라벨 창 내부) | 변함 (양성대조) | PASS |
| 같은 날짜 다른 종목의 라벨 창 | 벤치마크가 횡단면 median이므로 변함 (양성대조) | PASS |

h<=10의 CA 창이 `d..d+10`이라는 선언 때문에 `d+6..d+10` 변조는 **유효성**을 바꿀 수 있다. 이는
선언된 성질이며, 그래서 "미래에 눈멀었다"는 경계는 `d+5`가 아니라 **`d+10`**로 테스트한다.

---

## 9. 신호 / 평가 경로 분리

```text
neighbors + historical labels ─► analog_signal.py ─► A(q)
D1 rank matrix                ─► b0_composite.py ─► B0(q)
──────────────────────────────────────────────────────────
query future prices           ─► evaluation_labels.py ─► realized excess_return_5
```

AST 테스트로 고정한 것:

* `analog_signal`은 `evaluation_labels`도 V1 `labels`도 import하지 않고, 이름에 `label`이 들어간
  어떤 모듈도 import하지 않는다.
* V1 `labels`(미래 봉을 숫자로 바꾸는 유일한 모듈)를 import하는 V2-A 파일은
  **`evaluation_labels.py` 하나뿐**이다.
* V1 import 화이트리스트에 `labels`가 D3에서 추가됐고, 금지 목록(encoder / signal / evaluation /
  baselines / metrics / resample / gate / library / config / d1~d4)은 그대로다.

---

## 10. V1 / 타 전략 격리

| 검사 | 결과 |
| --- | --- |
| V1 패키지 code digest | `a8bb64aa52320773…` = V1 D4 run 기록값과 **동일** |
| V1 rules / 문서 / run artifacts | 변경 0건 |
| A/B/C/E 파일 | 변경 0건 |
| V1 D4 결과·post-mortem을 runtime input으로 읽음 | 없음 (경로 문자열·수치 하드코딩 테스트로 고정) |

---

## 11. 결정성과 입력 불변

### 11.1 두 번 완주

| 아티팩트 | 동일 |
| --- | --- |
| `identity.json` `parent_d1.json` `parent_d2.json` `status_counts.json` | 동일 |
| `signal_rows.parquet` (4.54MB) | **바이트 동일** |
| `evaluation_labels.parquet` (870KB) | **바이트 동일** |
| `summary.json` | `performance` 블록 외 동일 |
| `COMPLETE.json` | `completed_at` 외 동일 |

run id도 같다(`dv2a3-eda7523e6203`).

```text
signal_rows        digest d564fab886c34c40d50ba9452816f224615b4c6de3c3ecc4538aed311f873c63
evaluation_labels  digest febd1894049974a1c3f4452f17b5a8f40e68abe4b32d1fd203adc77cc85d0fec
```

### 11.2 입력 불변

```text
PRE  read-set digest 7e790a8dcf1db8ce…7d55
POST read-set digest 7e790a8dcf1db8ce…7d55        동일
D1 부모 파일 3종 · D2 부모 파일 9종 sha256        PRE/POST 동일
```

어느 쪽이든 달라지면 `F1 PARENT_MUTATED_DURING_D3`로 정지한다.

---

## 12. 성능

| 항목 | 값 |
| --- | --- |
| 총 소요 | **86.3초** (load 45.6 / 나머지 40.7) |
| peak RSS | **1,753MB** (한도 2,048MB) |
| 입력 | 이웃 3,315,000행 · query 66,300 |
| `signal_rows.parquet` | 4.54MB |
| `evaluation_labels.parquet` | 0.87MB |

D2(380초)보다 훨씬 가볍다. 이웃 행은 D2 아티팩트에서 **필요한 12개 컬럼만** 읽고, 라벨 결합은
`(T, N)` 행렬 한 번의 gather + `(66300, 50)` reshape median으로 처리한다. 3.3M행 DataFrame
복사본은 만들지 않는다.

---

## 13. 신규 코드와 테스트

| 모듈 | 줄수 | 역할 |
| --- | --- | --- |
| `analog_signal.py` | 66 | A(q) median. 인자 2개, label/거리 도달 불가 |
| `evaluation_labels.py` | 96 | excess 행렬(단일 정의) + query 평가 라벨 |
| `d3.py` | 670 | 부모 바인딩, 이웃 검증, 라벨 결합, 미래변조 감사, 아티팩트 |
| 테스트 `test_strategy_d_v2_d3.py` | 330 | 20 test |

```text
V2-A 패키지 3,551줄 / 테스트 1,670줄 (109 test)
```

---

## 14. Regression

| 대상 | 결과 |
| --- | --- |
| `backend/tests/strategy_d_v2` | **109 passed** (features 26 / PIT 29 / D1 10 / D2 24 / D3 20) |
| `backend/tests/strategy_d` (V1) | **167 passed** |
| `backend/tests` 전체 | **1,918 passed / 1 skipped** (491.69s) |

귀속을 구분해 둔다. D2 시점 1,898에서 1,918로 늘어난 20건은 전부 이번 단계의 신규 테스트다.
다른 세션(`strategy_e1` 계열)의 추가분은 이 구간에 없었고, 실패 0건이므로 귀속 분쟁도 없다.

---

## 15. GATE-D-V2A-3

| # | 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| 1 | rules checksum MATCH | PASS | §1 |
| 2 | D1 parent MATCH | PASS | §3 (8/8) |
| 3 | D2 parent MATCH | PASS | §1, §3 |
| 4 | 이웃 validity 50/50 전 query | PASS | §4 |
| 5 | label PIT violations 0 | PASS | §8 |
| 6 | A(q) = 선언된 median | PASS | §5 |
| 7 | B0 digest MATCH | PASS | §6 |
| 8 | signal/evaluation firewall | PASS | §9 |
| 9 | 미래 query 변조 -> 신호 불변 | PASS | §8.1 |
| 10 | historical label mutation 계약 | PASS | §8.2 |
| 11 | 2회 완주 digest MATCH | PASS | §11.1 |
| 12 | PRE/POST inputs immutable | PASS | §11.2 |
| 13 | RSS <= 2GB | PASS | 1,753MB |
| 14 | Alpha metrics calculated 0 | PASS | §16 |
| 15 | V1/A/B/C files changed 0 | PASS | §10 |

```text
GATE-D-V2A-3 = PASS
```

---

## 16. Alpha firewall

```text
IC(A)                      NO
IC(B0)                     NO
delta IC                   NO
Q1~Q5 / Q5-Q1              NO
bootstrap                  NO
time-block performance     NO
PASS/BORDERLINE/FAIL       NO
long-data purchase 판단     NO
```

요청문 §25대로 **A(q) 값 자체의 통계도 보고하지 않는다.** summary에 A의 평균·부호·분포가 없고,
realized와 같은 방향인지, B0보다 좋아 보이는지에 대한 어떤 집계도 없다. 보고하는 것은 행 수,
유한 여부, 상태 카운트, digest, 거리 메타데이터뿐이다(거리는 A(q) 입력이 아님).

---

## 17. 다음 단계

```text
D-V2A-4  2-YEAR SCREENING EVALUATION
  signal_rows.parquet 와 evaluation_labels.parquet 를 처음으로 join
  IC(A) · IC(B0) · delta(t) · paired moving block bootstrap · Q5-Q1 · 4 블록
  S1~S8 게이트 → SCREENING_PASS / BORDERLINE / FAIL
```

D4가 물려받는 값: run `dv2a3-eda7523e6203`, identity `eda7523e62034fdd…1255`,
`signal_rows` digest `d564fab886c34c40…`, `evaluation_labels` digest `febd1894049974a1…`,
query 66,300(평가 유효 66,162), A(q)/B0 각 66,300행.
