# Strategy D-AGGRESSIVE Reuse Matrix (MARKET_STRUCTURE_ANALOG_TAIL_V1)

작성 2026-09-21. 단계 D0. 판정 근거는 `backend/app/backtest/strategy_d_v2/`, `strategy_d_analog/` 실제 코드와
V2-A D1~D4 결과 문서, 이 머신의 run store(`data/runtime/strategy_d_v2/runs`)다. **이 문서는 코드를 만들지
않으며 V1·V2-A 코드를 수정하지 않는다.**

```text
REUSE       그대로 쓴다. 인자만 바뀐다
EXTEND      구조는 그대로, 새 열/새 인자를 추가한다 (D-AGG 패키지 안에서)
NEW         새로 만든다
DO_NOT_USE  D-AGG 설계상 쓰지 않는다
```

---

## 1. 패키지 위치: V2-A 패키지에 파일을 넣지 않는다

D-AGG 코드는 **새 패키지 `backend/app/backtest/strategy_d_agg/`**에 둔다. V2-A 패키지는 읽기 전용으로
import만 한다.

이유(실측): V2-A `identity.code_digest`는 패키지의 `*.py` 전체를 해싱한다. V2-A D4 §11.2에서 확인했듯
패키지에 파일이 하나 늘면 **같은 D1을 다시 돌려도 run id가 달라진다.** D-AGG 모듈을 `strategy_d_v2/`에
넣으면 V2-A 부모 체인의 재현성을 D-AGG가 오염시킨다. 새 패키지는 자기 `code_digest`를 가지며 V2-A run
id에 영향을 주지 않는다.

---

## 2. 자산별 판정

| 자산 | 위치 | 판정 | 근거 |
| --- | --- | --- | --- |
| freeze / grid / read-set digest | `strategy_d_analog/source.py` | **REUSE** | V2-A D1~D4가 쓴 경로 그대로. PRE/POST read-set 불변 검사 포함 |
| freeze 바인딩 | `strategy_d_v2/d1.assert_freeze_binding` | **REUSE** | 세 digest(freeze/source/grid) 일치 검사 |
| universe 적격 행렬 | `strategy_d_v2/d1.eligibility_matrix` | **REUSE** | V2-A D4도 패널에서 재도출한다. comparator 모집단이 곧 이 행렬 |
| rules 로더 / checksum | `strategy_d_v2/config.py`, `strategy_c_selection.rules.canonical_checksum` | **REUSE (V2-A 규칙 검증용)** + **NEW (D-AGG 규칙 로더)** | V2-A canonical `2b060ceb…` 일치 검사는 그대로, D-AGG 규칙은 자기 로더 |
| 10좌표 / 순위 / encoder | `structure_features.py`, `structure_encoder.py` | **REUSE (rv_20만)** | 신호는 D3 산출물에서 읽는다. 좌표는 secondary X-6/X-7의 `rv_20` 한 열만 재계산 |
| library / neighbor / similarity | `library.py`, `strategy_d_analog/neighbor_search.py`, `similarity.py` | **DO_NOT_USE (재실행 없음)** | 이웃은 D2 산출물, A(q)는 D3 산출물. D-AGG는 이웃을 다시 찾지 않는다 |
| **A(q)** | V2-A D3 `signal_rows.parquet` 의 `analog_signal_A` 열 | **REUSE** | 새 신호 금지(규칙 `signal.new_signal = none`) |
| D3 query 집합 / 표본 순서 | `signal_rows` 의 `query_date_idx`, `query_ticker_col`, `query_ticker`, `sample_rank`, `signal_status` | **REUSE** | setup 선택과 tie-break에 쓴다 |
| D3 부모 로더 / 결합 | `strategy_d_v2/d4.load_parent_d3`, `join_artifacts` | **EXTEND** | 구조는 그대로. 바인딩을 **A 관련 열 digest**로 바꾼 D-AGG 버전(§3) |
| `b0` 열 | `signal_rows.b0` | **REUSE (secondary X-5만)** | 판정 비사용. 머신 간 2 ULP 차이는 X-5 기술통계에만 영향 |
| query 라벨 (`excess_return_5`) | D3 `evaluation_labels.parquet` | **REUSE** | X-8 기술통계 |
| 라벨 유효성 + 20D CA 확장 | `strategy_d_analog/label_extension.py` | **REUSE** | h=5 유효성 정의가 V2-A와 동일 |
| **MFE/MAE** | `strategy_d_v2/evaluation_labels.forward_extremes` -> `strategy_d_analog/labels.compute_extremes` | **REUSE** | 이미 `(T, N)` 전체 행렬을 만든다. 유니버스 comparator에 필요한 전 종목 값이 여기서 나온다. 공식 `max(H(D+1..D+h))/O(D+1) - 1` 이 선언과 문자 그대로 일치 |
| `close_return_5` | `labels.compute_labels` | **REUSE** | X-8 |
| moving block bootstrap | `strategy_d_analog/resample.block_indices`, `draw_digest`, `block_partition` | **REUSE** | 인자 명시 전달(`seed=20260921, horizon=5, block_length=20, replicates=10000`). 모듈 상수 `SEED` 등은 읽지 않는다 |
| percentile interval | `resample.interval` | **DO_NOT_USE** | 날짜 시계열의 **평균**용이다. TL은 두 합의 **비율**이라 같은 draw 위에서 분자·분모를 따로 합하는 함수가 필요하다(NEW) |
| IC / 분위 산술 | `strategy_d_analog/metrics.py` | **REUSE (secondary만)** | X-10 분위 프로파일. 게이트는 IC를 쓰지 않는다 |
| identity / run id 레시피 | `strategy_d_v2/identity.py` | **EXTEND (복제)** | 레시피(정규 JSON, float 거부, 경로·시각 제외)는 그대로, `STRATEGY_ID`·`PACKAGE_DIR`·prefix만 D-AGG 값. 모듈 자체는 V2-A 패키지를 가리키므로 새 패키지에 같은 구조로 둔다 |
| artifact / COMPLETE / 부모 PRE-POST sha | `d3.py`/`d4.py` 의 START GATE 패턴 | **EXTEND** | 부모 목록에 D3(+D1/D2 파일) 추가 |
| PIT 헬퍼 | `strategy_d_v2/pit.py`, `strategy_d_analog/pit_audit.py` | **REUSE** | truncate / mutation 패턴을 MFE/MAE 감사(PA-3)에 적용 |
| V2-A gate / verdict | `strategy_d_v2/d4.screening_gate`, `decide` | **DO_NOT_USE** | 조건이 전혀 다르다. V2-A 판정 코드를 호출하면 두 연구가 섞인다 |
| V2-A D4 결과 (per-date IC, verdict) | `dv2a4-a111d4b264b5` | **DO_NOT_USE** | D-AGG의 입력이 아니다. 읽는 것은 D3까지 |
| 경로 인코더 / V1 표현 A·B | `strategy_d_analog/encoder.py` | **DO_NOT_USE** | V2-A에서도 비사용 |
| A/B/C/E Alpha, adapter, driver, SimBroker, portfolio | - | **DO_NOT_USE** | 스크리닝은 체결·비용·사이징을 모사하지 않는다. 백테스터는 PASS 이후 별도 선언 |

---

## 3. 부모 바인딩 규칙 (EXTEND의 핵심)

```text
parent D3        rules canonical 2b060ceb…fe229 을 묶는 COMPLETE D3 run
                 이 머신: dv2a3-850a238d9e7a  (D2 dv2a2-bdfb160b8e58, D1 dv2a1-7cfc565cc548)
binding digest   signal_rows 의 [query_date_idx, query_ticker_col, query_ticker, sample_rank,
                 analog_signal_A, signal_status] 만 (b0, b0_strong, distance 열 제외)
labels digest    evaluation_labels 전체 (V2-A D4 §11 에서 머신 간 일치 확인됨)
```

V2-A D4는 `signal_rows` 전체 digest를 바인딩했고, 그 digest는 `b0` 때문에 집 PC(`d564fab8…`)와 회사
PC(`0346ebaf…`)가 다르다. D-AGG가 같은 방식을 쓰면 다른 머신에서 부모를 재생성했을 때 바인딩이 깨진다.
A 열은 행렬곱을 타지 않으므로(neighbor excess의 중앙값) A 전용 digest는 머신 독립이어야 하며, **D-AGG-1이
이것을 실측으로 확인한다**(두 머신 모두 가능하면 대조, 불가하면 같은 머신 2회 + 계산 경로 검토).

---

## 4. NEW

| 자산 | 내용 | 예상 규모 |
| --- | --- | --- |
| `strategy_d_agg/config.py` | D-AGG 규칙 로더, canonical 검증, 상수는 JSON에서만 | 약 100~140줄 |
| `strategy_d_agg/identity.py` | V2-A 레시피 복제, prefix `dagg1`/`dagg2` | 약 90줄 |
| `strategy_d_agg/setup.py` | 날짜별 상위 10% 선택 (라벨 import 금지, PA-2) | 약 60~80줄 |
| `strategy_d_agg/excursions.py` | 사건 지표 UP10/DN10, 날짜별 `p_up`/`p_dn`/중앙값, 유니버스 집계 | 약 120~160줄 |
| `strategy_d_agg/ratios.py` | 날짜 층화 비율 TL/DL/NTL/AG, 같은 draw의 비율 bootstrap, 블록별 값, leave-out | 약 150~200줄 |
| `strategy_d_agg/gate.py` | H1~H7, T1~T6, 판정표. 파일·app 모듈을 import하지 않는 순수 함수 | 약 100~130줄 |
| `strategy_d_agg/d1.py` | D-AGG-1 Data/PIT + MFE/MAE 검증 | 약 300~400줄 |
| `strategy_d_agg/d2.py` | D-AGG-2 채점 + secondary | 약 400~600줄 |
| 테스트 | 폐형식 비율, 결정성, PIT mutation, 방화벽, 판정표 경계 | 약 400~500줄 |

```text
NEW 합계 추정: 약 1,700~2,300줄 (테스트 포함)
V2-A 코드 수정: 0 줄
```

---

## 5. 방화벽

V2-A가 세운 규칙을 그대로 이어받고 두 개를 추가한다.

```text
유지  신호를 만드는 코드는 라벨을 볼 수 없다 (V2-A D3 AST 테스트)
유지  labels 단일 importer: 미래 봉 -> 숫자 경로는 evaluation_labels 하나 (D-AGG는 forward_extremes 호출)
추가  strategy_d_agg.setup 은 labels / evaluation_labels / excursions 를 import하지 않는다
추가  strategy_d_agg 는 strategy_d_v2.d4 의 gate/decide 와 V1 resample 상수(SEED, BLOCK_LENGTH, REPLICATES)를 읽지 않는다
```
