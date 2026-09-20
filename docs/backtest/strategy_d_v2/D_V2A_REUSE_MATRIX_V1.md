# Strategy D V2-A Reuse Matrix (MARKET_STRUCTURE_ANALOG_V2)

작성 2026-09-20. 단계 D0. 판정 근거는 `backend/app/backtest/strategy_d_analog/` 실제 코드와
D1~D4 결과 문서다. **이 문서는 코드를 만들지 않으며 V1 코드를 수정하지 않는다.**

판정 기호:

```text
REUSE       그대로 쓴다. 인자만 바뀐다
EXTEND      구조는 그대로, 새 열/새 인자를 추가한다
NEW         새로 만든다
DO_NOT_USE  V2-A 설계상 쓰지 않는다
```

---

## 1. 모듈별 판정

| 자산 | 위치 | 줄수 | 판정 | 근거 |
| --- | --- | --- | --- | --- |
| source / freeze / grid (G1~G8) | `source.py` | 374 | **REUSE** | 510파일 sha256, grid digest, read-set PRE/POST 불변 검사가 전략 중립. 지수 context column 확장은 §3으로 **불필요해졌다** |
| identity / code_digest / run id | `identity.py` | 117 | **REUSE** | stdlib + D models만 import. 새 strategy_id/rules checksum만 주입 |
| universe 적격 규칙 | `universe.py` | 89 | **REUSE** | 값까지 동일(min_close 3.0, ADV20 5M, seasoning 60, split/CA 창). 신호와 무관 |
| query sampling (300/date) | `sampling.py` | 45 | **REUSE** | 라벨 보기 전 추출. **같은 seed `20260917` 유지**가 표본 쇼핑 방지이자 V1 대비 비교 가능성 |
| label 값 + 20D CA 확장 | `labels.py`, `label_extension.py` | 175 + 120 | **REUSE** | `excess_return_h` 정의를 V1과 공유(요청문 §26). C `LabelSet` 동일성 테스트 고정 |
| PIT audit (truncate/변조/embargo) | `pit.py`, `pit_audit.py` | 128 + 165 | **REUSE** | 12일 50,400 query bit 동일 검사. 좌표계와 무관 |
| library 구성 (stride/cap/FIGI) | `library.py` | 169 | **EXTEND** | 구조 동일. `library_meta`에 좌표 유한성 플래그 열 추가 |
| neighbor 선택 (greedy cap, tie-break) | `neighbor_search.py` | 167 | **REUSE** | `rank_score` 배열만 받는 **거리 불가지론** 설계. full-sort 동치 테스트 포함 |
| similarity 커널 | `similarity.py` | 73 | **REUSE** | Euclidean 청크 gemm 경로를 10차원 순위 벡터에 그대로 적용. Pearson 경로는 미사용 |
| 백분위 표준화 | `features.percentile_rank` | (137 중) | **REUSE** | `(평균순위 - 1)/(n - 1)`, 동점 평균순위. V2-A 스케일링 정의와 **문자 그대로 일치** |
| N2 피처 5종 | `features.build` | (137 중) | **EXTEND** | 새 좌표 10개를 같은 표준화 경로에 얹는다. 기존 5종은 secondary B2 비교용으로 유지 |
| signal / evaluation 분리 (Alpha firewall) | `signal.py`, `evaluation.py` | 153 + 60 | **REUSE** | D3의 query future firewall AST 테스트가 이 연구선의 핵심 자산 |
| IC / 분위 / 블록 통계 | `metrics.py` | 147 | **REUSE** | 폐형식 대조 테스트 포함 |
| block bootstrap / paired / digest | `resample.py` | 107 | **REUSE** | 블록 길이·replicate·seed를 인자로 받음. paired 경로가 그대로 delta_IC에 쓰인다 |
| gate 판정 | `gate.py` | 144 | **REUSE (규칙만 교체)** | `app.*`/`json`/`pathlib`/`pyarrow`를 import하지 않는 **파일을 열 수 없는 gate**. 조건 목록 교체로 새 gate가 된다 |
| artifacts / COMPLETE / 부모 바인딩 | `artifacts.py`, D2~D4 START GATE | 138 | **REUSE** | 부모 digest 재계산 거부 경로 포함 |
| 실행 CLI / 조합 루프 | `d1.py` ~ `d4.py` | 2,109 | **EXTEND** | 조합·검정 정의가 D0 JSON에서 오므로 규칙 교체로 대응. 14검정 루프가 1 primary + 13 secondary 루프로 축소 |
| N1 draw / N2a kNN / 직교화 | `baselines.py` | 187 | **REUSE (secondary로 강등)** | N1/N2a/N2b는 V2-A에서 **판정 비사용 기술통계**. 선언된 편차 `d-n1-draw-v1-prefix-seeded` 그대로 승계 |
| **경로 인코더 (표현 A/B)** | `encoder.py` | 111 | **DO_NOT_USE** | V2-A는 raw shape를 신호 입력에서 제거한다(개념문 §1). joint distance 금지 |
| A/B/C Alpha 모듈 전체 | `strategy/engine.py`, `strategy_b/*`, `strategy_c_selection/features.py`, `rules.py` | - | **DO_NOT_USE** | V1 D0 Reuse Matrix 판정 그대로 유효 |
| adapter / driver / SimBroker / portfolio | - | - | **DO_NOT_USE** | 스크리닝은 체결·비용을 모사하지 않는다 |

---

## 2. NEW (새로 만들 것)

| 자산 | 내용 | 예상 규모 |
| --- | --- | --- |
| `structure_features.py` | 좌표 10개 계산 + 유한성 마스크. 기존 `percentile_rank` 호출 | 약 180~220줄 |
| `structure_encoder.py` | `(n, 10)` 순위 벡터 조립. `similarity.score_block` 입력 규격에 맞춤 | 약 60~80줄 |
| `b0_composite.py` | 부호 선언 등가중 순위 합성지표 B0, B0-strong | 약 60~80줄 |
| `gate_v2a.py` 조건 테이블 | 스크리닝 8조건. `gate.py` 엔진 재사용 | 약 80~100줄 |

```text
NEW 합계 추정: 약 380~480줄
폐기 대상: encoder.py 111줄 (V1 파일은 그대로 두고 V2-A가 참조하지 않는 방식)
V1 인프라 5,284줄 중 재작성 대상 없음
```

**V2-A의 비용은 코드가 아니라 선언·검정·문서다.** V1 실측 비용은 D2 36.7분, D3 4.9분,
D4 63.5분, 테스트 167개였다. V2-A는 검정 수가 14 -> 1 primary로 줄어 D4 실행 시간이 감소하고,
좌표 계산이 늘어 D2가 소폭 증가할 것으로 본다(사전 추정이며 측정값이 아니다).

---

## 3. 이번 D0에서 사라진 EXTEND

이전 연구설계 문서는 `source.py`에 SPY/RSP/QQQ/IWM context column을 싣는 EXTEND를 상대강도
좌표의 전제로 잡았다. 개념문 §3.4의 순위 불변 항등식으로 **그 좌표 자체가 불필요**해졌으므로,
이 EXTEND는 V2-A critical path에서 제거한다. 패널은 V1과 동일하게 CS만 남긴다.

---

## 4. 승계되는 선언 편차

V1 D4 §11의 N1 draw 편차(`d-n1-draw-v1-prefix-seeded`)는 V2-A에서도 그대로 승계한다. D0 원문의
후보별 해시 순서는 단일 코어 86시간이며 실행 불가하다. V2-A에서 N1 계열은 secondary이므로
판정에 영향이 없고, 규칙 JSON에 편차 식별자를 명시한다.
