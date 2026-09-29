# US-B CRYPTO D6-C Historical Replay 사전등록 계약 V1

작성 2026-09-28. **이 문서는 계약이다.** 성과 숫자를 하나라도 보기 전에 동결한다.
동결 기록 `data/runtime/crypto/d6/d6c_freeze_v1.json`. 실행기는 시작할 때 아래 세 해시를 모두
재계산하고 하나라도 다르면 실행을 거부한다.

| 문서 | sha256 |
|---|---|
| D6-A 계약 `CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md` | `1a6bcaa51d9302850d14827618bcc5d32709f768f324f425e401fac8a622cc0d` |
| I1 해석 `CRYPTO_D6_EXECUTION_INTERPRETATION_I1_V1.md` | `03f076c879984829b4234357abb464c55cd435c491228832741b5b0958f72fc6` |
| 이 문서 | 동결 시 기록 |

---

## 0. 범위와 금지

| # | 규칙 |
|---|---|
| Z1 | **FDN-V1을 있는 그대로 평가한다.** score weight·threshold·feature·hard filter·stop 승수·horizon·leverage·위험예산·수수료 변경 0 |
| Z2 | 결과를 본 뒤 파라미터 조정 0, best fold 선택 0, best year 선택 0 |
| Z3 | LONG only 유지. SHORT 추가 0 |
| Z4 | 입력은 D2 정본 5종(kline·mark·index·OI·funding)뿐. 청산 forward·AOA·D5.2 외부 데이터·미래 데이터 0 |
| Z5 | **feature 재구현 0.** D6-B 엔진(`app.crypto.research.d6`)을 그대로 호출한다 |
| Z6 | **별도 백테스터 작성 0.** 체결·수수료·funding 회계는 실제 Paper 엔진이 한다(검증계획 C2) |
| Z7 | 운영 서버·운영 Paper 계좌·AUTO UI·manual UI·liqfwd 접근 0. 배포 0 |
| Z8 | commit/push 0 |
| Z9 | 초기 metric이 나빠도 **사전등록 분석을 끝까지 수행한다**. 중도 중단 0 |

---

## 1. 연구 창과 fold

| 항목 | 값 |
|---|---|
| 입력 시작 | 2021-02-01T00:00:00Z |
| 표본 시작 | **2021-03-04T00:00:00Z** (30일 warm-up 후) |
| 끝 | **2026-09-23T00:00:00Z 미포함** |
| OOS | **2022-01-01 ~ 2026-09-23** (F1~F9 test 합) |

fold는 **D5.1 계약 2절 표를 글자 그대로 재사용한다**(F1~F9, test 6개월, F9만 2026-01-01 ~ 09-23).
D6용 새 fold를 만들지 않는다. 결과를 보기 전에 이렇게 정했다.

| fold | test 시작 | test 끝(미포함) |
|---|---|---|
| F1 | 2022-01-01 | 2022-07-01 |
| F2 | 2022-07-01 | 2023-01-01 |
| F3 | 2023-01-01 | 2023-07-01 |
| F4 | 2023-07-01 | 2024-01-01 |
| F5 | 2024-01-01 | 2024-07-01 |
| F6 | 2024-07-01 | 2025-01-01 |
| F7 | 2025-01-01 | 2025-07-01 |
| F8 | 2025-07-01 | 2026-01-01 |
| F9 | 2026-01-01 | 2026-09-23 |

**train 구간은 판정에 쓰지 않는다.** FDN-V1에는 적합할 파라미터가 없으므로(전부 이 계약에 상수로
동결) fold는 사실상 시간 분할 보고 단위다. 이것을 "학습/검증"이라고 부르지 않는다.

거래 귀속: **진입 bar의 시각**이 속한 fold로 귀속한다. fold 경계를 가로지르는 보유는 그대로 둔다
(진입 fold에 귀속). 잘라내지 않는다.

**untouched holdout 아님.** 2021~2026은 D5·D5.1·D5.2가 이미 전수 탐색한 구간이고 FDN-V1은 그 결과를
보고 설계됐다. **historical 결과는 confirmation evidence이지 true OOS가 아니다.**
TRUE OOS는 D6-D forward shadow부터다.

---

## 2. 실행 의미론 (I1)

I1 해석 기록을 그대로 적용한다.

### 2.1 Decision at `t`
`t`까지의 bar만 사용. `DEC.decide()`를 `entry_reference_price=None`으로 호출한다.
신호 판정은 **filter_pass + score ≥ 50 + 필수조건(M1·M2) + separation**으로만 읽고,
사이징 실현가능성은 신호에 영향을 주지 않는다.

`target_notional = equity x 0.005 / stop_distance`, `equity x 1.0` 상한.
`stop_distance = clip(2.5 x f_rv24h x sqrt(240), 0.01, 0.10)`.

### 2.2 Execution at `t+1`
| 항목 | 값 |
|---|---|
| 기준가 | `open[t+1]` |
| 수량 | `floor_to_step(target_notional / open[t+1], 0.001)` |
| 주문 | MARKET taker, 엔진이 합성 호가 소모 |
| 손절가 | `actual_fill_price x (1 - stop_distance)` |

### 2.3 EXECUTION_REJECT
`EXECUTION_REJECT_QTY_BELOW_MIN` / `_NOTIONAL_BELOW_MIN` / `_ENGINE`.
거래로 세지 않고 건수를 별도 보고한다. 쿨다운을 시작하지 않는다.

`EXECUTION_REJECT_SAFE_MAX`는 **과거 호가가 없어 평가하지 않는다**(4절).

### 2.4 equity
사이징에 쓰는 `equity`는 **실행 시점 엔진 계좌의 equity**다(복리). 초기 자본 고정이 아니다.
결과를 보기 전에 이렇게 정했다.

---

## 3. 계좌 모델

| 항목 | 값 |
|---|---|
| 계좌 | **D6-C 전용 historical AUTO 계좌.** manual 계좌와 완전 독립(별도 run_id, 메모리 전용 원장) |
| 초기 자본 | **10,000,000 KRW**, 환율 **1,344.00 KRW/USDT** (D4 `run_config.json` 기록값) → **7,440.47 USDT** |
| 성과 정본 통화 | **USDT.** KRW는 표시용이며 성과 계산에 쓰지 않는다 |
| 레버리지 | **1x 고정.** 변경 0 |
| 동시 포지션 | 1개 |
| 위험한도표 | `data/runtime/crypto/BTCUSDT/reference/risk_limit_1790128376.json` |

운영 Paper 계좌·run 디렉터리 접근 0. 엔진은 메모리 전용으로 띄운다.

---

## 4. 체결 모델 (합성 호가)

**과거 호가는 존재하지 않는다**(D1). D4 `paper/historical.py`의 `SyntheticBook`을 그대로 쓴다.

| 항목 | 값 |
|---|---|
| spread | **0.10 USDT** (= 1 tick. D2 실측 구간에서 관측된 spread) |
| 깊이 | **5 BTC / 측**, 평탄 |
| tick | 0.10 |
| bar 내부 경로 | 가정. 5절 |

| # | 규칙 |
|---|---|
| F1 | 결과 문서에서 이것을 **실제 과거 호가라고 표현하지 않는다.** 체결가는 모델 출력이다 |
| F2 | 모든 체결 기록에 `book_synthetic: true` 표시 |
| F3 | **깊이 영향 측정**: 최대 주문 수량과 5 BTC의 비를 보고한다. 비가 작으면 깊이 가정이 결과를 좌우하지 않는다는 근거가 된다 |
| F4 | **SAFE MAX는 실제 안전성 증거로 쓰지 않는다.** 과거 호가가 없으므로 `EXECUTION_REJECT_SAFE_MAX`는 `NOT_EVALUATED` |
| F5 | spread 0.10은 왕복 약 **0.01bp**(BTC 100,000 기준)로 D5 실측 BASE(0.0118bp)와 같은 자릿수다. 둘을 이중으로 차감하지 않는다 |

---

## 5. Intrabar 모호성 (보수적 규칙 동결)

1분 bar는 자기 극값의 순서를 말해주지 않는다. **adverse-first**를 전 구간에 적용한다.

| 상황 | 규칙 |
|---|---|
| 보유 중 bar에서 손절가가 닿음 | **손절 발생.** 판정은 그 bar의 **mark low** 기준 |
| 같은 bar에서 손절가도 닿고 4h도 만료 | **손절이 이긴다** (D6-A `same_bar_rule`) |
| 손절 체결가 | **`min(stop_price, 그 bar의 open)`**. bar가 손절가 아래에서 열리면 갭으로 통과한 것이므로 open에 체결 |
| 유리한 움직임 | 익절 규칙이 없으므로 사용하지 않는다 |
| best-case 선택 | **금지** |

`min(stop_price, bar_open)`을 택한 이유: bar low에 체결시키는 것은 비현실적으로 나쁘고,
항상 `stop_price`에 체결시키는 것은 갭을 무시해 낙관적이다. 갭만 벌하는 것이 보수적이면서 현실적이다.

**모호 bar 수를 별도로 센다**: 같은 bar에서 손절 조건과 시간 만료가 동시에 성립한 건수,
그리고 갭으로 손절가 아래에서 열린 건수.

---

## 6. 진입 / 청산 / 재진입

| 항목 | 규칙 | 출처 |
|---|---|---|
| 진입 | bar t 결정 → `open[t+1]` 체결 | D6-A §6.1 |
| 시간 청산 | 진입 후 **정확히 240분**. 체결 기준가 = 그 bar의 open | D6-A §6.2 X1 |
| 손절 | mark low ≤ stop_price | D6-A §6.2 X2 |
| 최대 보유 | 240분 | D6-A §6.2 X3 |
| **중복 신호** | 포지션 보유 중 새 LONG 신호는 **무시**. H9(POSITION_OPEN)가 이미 그렇게 정하고 있다 | D6-A H9 |
| **ADD / 물타기** | **금지**. FDN-V1은 평균단가 전략이 아니다 | D6-A §6.2 `other_exits_allowed: false`, AOA closeout |
| **재진입** | 청산 후 **60분 쿨다운**(H8) 이후 가능 | D6-A H8 |
| 그 외 청산 규칙 | **없음.** score reversal·trailing·부분청산·익절 0 | D6-A §6.2 |

### 데이터 결손 / 창 끝

| 상황 | 규칙 |
|---|---|
| 240분 청산 bar가 창 끝을 넘음 | **그 거래를 결과에서 제외한다.** 미청산 포지션을 종가로 평가하지 않는다. 건수를 보고 |
| 보유 중 bar 결손 | D2 grid는 결손 0(D2 검증). 그래도 발생하면 그 거래를 제외하고 건수를 보고 |
| feature NaN | H4가 이미 진입을 막는다 |

---

## 7. 비용

**PRIMARY는 VIP0이다.**

| 항목 | 값 | 출처 |
|---|---|---|
| taker | **0.055%** (0.00055) | `fee_source_verification_v1.json`, `app.crypto.paper.fees.load_scenarios`로 읽음 |
| maker | 사용 0 | D6-A Z4, AOA E1 |
| 진입 | taker | |
| 청산 | taker (시간 청산·손절 모두) | |
| funding | **실제 D2 funding rate**, 8h UTC 그리드, 보유 중 교차한 정산만. 엔진이 처리 | D3 계약 FN1~FN8 |
| 환율 | 표시용만 | |

### 비용 시나리오 (사후 조정, 엔진 1회 실행)

엔진 결과가 `VIP0_BASE`다(taker 양면 + 합성 spread + funding). 나머지는 거래 원장 위에서 계산한다.

| 시나리오 | 정의 |
|---|---|
| `ZERO` | 기준가 기준 총수익. 수수료·funding·spread 0 |
| **`VIP0_BASE`** | **엔진 결과 그대로. 주 판정 시나리오** |
| `VIP0_STRESS` | `VIP0_BASE` - (D5 STRESS **2.537159730588128e-04** - D5 BASE **1.1841571614073798e-06**) x 진입 명목 |
| `TAKER_PLUS_20PCT` | `VIP0_BASE` - 0.2 x 0.00055 x (진입 명목 + 청산 명목) |

STRESS 값은 D5 계약 8절의 실측 최대(23.1시간 top-5 테이프)를 그대로 쓴다. 새로 정하지 않았다.

**Maker 시나리오 금지.** AOA E1(`BYBIT_CF_NEGATIVE`)이 maker를 구제 시나리오로 쓰는 것을 막았다.

---

## 8. Arm (동결, 10개)

| arm | 내용 | 판정 |
|---|---|---|
| **A-MAIN** | 계약 그대로: score ≥ 50, 4h, k=2.5, 1x, 0.5% | **이 arm으로만 판정한다** |
| A-NOSTOP | 손절 없음, 나머지 동일 | G11 sanity 전용. **판정 불가** |
| A-SENS-1 | score threshold 37 | G8 |
| A-SENS-2 | score threshold 62 | G8 |
| A-SENS-3 | 손절 k = 2.0 | G8 |
| A-SENS-4 | 손절 k = 3.0 | G8 |
| A-SENS-5 | OI 창 30분 | G8 |
| A-SENS-6 | OI 창 120분 | G8 |
| A-REF-2h | 보유 2h | **보고 전용. 판정 금지** |
| A-REF-8h | 보유 8h | **보고 전용. 판정 금지** |

| # | 규칙 |
|---|---|
| E1 | arm은 이 10개뿐. 추가 0 |
| E2 | A-REF가 A-MAIN보다 좋아도 **후보를 바꾸지 않는다** |
| E3 | A-SENS는 **G8 통과 여부만** 본다. 더 좋은 SENS 값을 새 기본값으로 삼지 않는다. 그것은 미래 V2 가설일 뿐이다 |
| E4 | 전 arm 결과를 필터 없이 JSON으로 남긴다 |

A-SENS-5/6은 `f_oi1h`의 lag만 30/120분으로 바꾼다. 같은 공식·같은 bucket 규칙을 쓰며,
lag 60에서 D6-B의 `f_oi1h`와 일치함을 테스트로 고정한다.

---

## 9. 지표 정의

### 거래 단위

| 지표 | 정의 |
|---|---|
| `gross_bp` | `(exit_reference_price / entry_reference_price - 1) x 10^4`. 비용 0 |
| `net_bp` | `엔진 실현 net_pnl(해당 거래) / 진입 명목 x 10^4`. 수수료·funding·spread 포함 |
| 진입 명목 | `체결 수량 x 실제 체결가` |
| 보유 시간 | 진입 체결 ~ 청산 체결 (분) |

### 집계

| 지표 | 정의 |
|---|---|
| trades / wins / losses | `net_bp > 0` 이 win |
| win rate | wins / trades |
| gross pnl / net pnl | USDT 합 |
| total fees / funding | USDT 합 (엔진 원장) |
| **PF** | `양수 net 합 / |음수 net 합|`. 분모 0이면 `INF` |
| payoff ratio | 평균 이익 / |평균 손실| |
| average / median trade | `net_bp` |
| expectancy | `net_bp` 평균 (= average trade) |
| **MDD** | **거래 종료 시점 equity 곡선의 최대 낙폭 비율.** bar 단위 미실현 낙폭이 아니다 |
| return | `(최종 equity / 초기 equity - 1)` |
| Sharpe | **사용하지 않는다.** D6-A 게이트에 없고 여기서 새로 만들지 않는다 |
| average hold | 분 |
| stop exits / time exits | 건수 |
| rejected executions | 건수 (거래 아님) |
| `edge_to_cost_ratio` | `gross_bp 평균 / (gross_bp 평균 - net_bp 평균)` (D5.1 정의 그대로) |

### CI

**7 UTC일 moving block bootstrap, 1,000회, seed 20260928** (D6-A 계약 `validation.bootstrap` 그대로).
일별 (net_bp 합, 거래 수)를 블록 재표집해 비율 추정량의 2.5/97.5 분위를 쓴다. D5·D5.1·D5.2와 같은 방식.

---

## 10. 분해 보고 (평균만 보고하지 않는다)

| 축 | 보고 항목 |
|---|---|
| fold F1~F9 | trades, net, PF, MDD, win rate, expectancy, **양수 fold 수 명시** |
| 연도 2022~2026 | trades, net, PF, MDD |
| 변동성 HIGH / MID | trades, net (LOW는 H5가 막아 존재하지 않음) |
| 추세 BULL / BEAR / SIDEWAYS | trades, net |
| UTC 세션 00-06 / 06-12 / 12-18 / 18-24 | trades, net |

regime 라벨은 **D5 계약 10절 정의 그대로**다: 추세 = 직전 7일 로그수익 ±5%,
변동성 = D5.1 동결 cutoff, 세션 = 진입 bar 시각 `hour // 6`. **새 regime 탐색 0.**

---

## 11. 집중도

| 항목 |
|---|
| top 1 거래 기여 (net 합 대비 %) |
| top 5 거래 기여 |
| top 1% 거래 기여 |
| top month 기여 |
| top year 기여 |
| **상위 거래 제거 민감도**: top 1 제거 후 net, top 5 제거 후 net |

종목은 BTCUSDT 1종이므로 top symbol은 해당 없음.

**부호 규약**: 기여도는 `해당 net 합 / 전체 net 합`이다. 전체 net 합이 0 이하면 비율이 의미를 잃으므로
`NOT_MEANINGFUL`로 적고 절대값만 보고한다. 결과를 본 뒤 규약을 바꾸지 않는다.

---

## 12. 판정 게이트 G1~G11

**D6-A 계약 13절을 그대로 읽어 구현한다. 새 게이트 0.**
각 게이트는 `PASS` / `FAIL` / `NOT_EVALUABLE`을 낸다. **`NOT_EVALUABLE`을 PASS로 세지 않는다.**

| id | 규칙 (D6-A 13절) |
|---|---|
| G1 | OOS VIP0_BASE net 평균 > 0 **이고** 95% CI 하한 > 0 |
| G2 | edge/cost ratio ≥ 1.2 |
| G3 | 판정 가능 fold 중 gross > 0 비율 ≥ 2/3, net > 0 비율 ≥ 1/2 |
| G4 | OOS 거래 ≥ 200 **이고** 판정 가능 fold ≥ 6 (fold당 거래 ≥ 20 이고 days ≥ 20) |
| G5 | leave-one-year-out net > 0 **이고** 단일 연도가 총 net 합의 40%를 넘지 않음 |
| G6 | 단일 거래가 총 net 합의 10%를 넘지 않음 |
| G7 | VIP0_STRESS net > 0 **이고** taker +20%에서도 net > 0 |
| G8 | 6개 민감도 arm 전부에서 net > 0 |
| G9 | 추세 3라벨 중 2개 이상 net > 0, **변동성 HIGH·MID 둘 다** net > 0, 세션 4개 중 3개 이상 net > 0 |
| G10 | PF > 1.3 **이고** MDD < 15% |
| G11 | NO_STOP arm 부호가 연구 추정과 정합 (**PASS 조건 아님**, sanity check) |

**최종 PASS는 G1~G10 전부 PASS일 때만이다.** G11은 참고다.

`NOT_EVALUABLE` 처리:
- G5·G6의 기여도는 총 net 합 ≤ 0이면 `NOT_MEANINGFUL`이지만, 그 경우 leave-one-year-out 조건이
  이미 실패하므로 **G5는 FAIL**이다. G6은 비율이 의미를 잃으므로 `NOT_EVALUABLE`이고 **PASS로 세지 않는다**.
- 판정 가능 fold가 0이면 G3은 `NOT_EVALUABLE`.

---

## 13. 최종 판정

| 판정 | 조건 |
|---|---|
| **PASS** | G1~G10 전부 PASS |
| **FAIL** | 하나라도 FAIL |
| **INCONCLUSIVE** | FAIL은 없으나 `NOT_EVALUABLE`이 있어 PASS를 선언할 수 없음 |

| # | 규칙 |
|---|---|
| V1 | **게이트 숫자를 결과를 본 뒤 옮기지 않는다** |
| V2 | FAIL이면 **FDN-V1 CLOSED.** 조건을 바꿔 재시도하지 않는다. V2 사전등록은 사용자 결정 |
| V3 | INCONCLUSIVE면 **무엇이 부족한지 정확히 명시**하고 threshold를 바꾸지 않는다 |
| V4 | PASS일 때만 **D6-D = AUTHORIZED**. 그 외 `NOT AUTHORIZED`이고 shadow 구현 금지 |
| V5 | 판정은 **A-MAIN 기본 설정**으로만 한다. 주변 파라미터에서 더 좋은 값을 찾아도 FDN-V1 결과를 바꾸지 않는다 |

---

## 14. 산출물

| 파일 |
|---|
| `docs/crypto/CRYPTO_D6_C_BACKTEST_CONTRACT_V1.md` (이 문서) |
| `docs/crypto/CRYPTO_D6_C_BACKTEST_RESULTS_V1.md` |
| `docs/crypto/CRYPTO_D6_C_GATE_REPORT_V1.md` |
| `data/research/crypto/d6/d6c_contract_v1.json` |
| `data/research/crypto/d6/d6c_results_v1.json` |
| `data/research/crypto/d6/d6c_trades_v1.parquet` |
| `data/research/crypto/d6/d6c_fold_metrics_v1.parquet` |
| 코드 `backend/app/crypto/research/d6c/` |
| 테스트 `backend/tests/crypto/test_d6c_*.py` |

D6-B 엔진(`app/crypto/research/d6/`) 수정 0. Paper 엔진 수정 0. 배포 0. commit/push 0.
