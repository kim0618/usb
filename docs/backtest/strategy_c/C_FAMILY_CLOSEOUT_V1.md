# Strategy C 계열 최종 종료

작성 2026-09-20. 이 문서는 C-1부터 C-4까지의 종결 기록이다. 판정을 바꾸거나 되살리기 위한 문서가 아니다.

`C_STATUS_CLOSED_V1.md`(2026-09-19, C-1·C-2 종료 기록)는 **바이트 그대로 보존한다.** 그 문서 §6 "다음"이
당시 기준으로 "활성 라인은 Strategy D"라고 적고 있으나, 그 이후 EQM-V0와 C-4가 추가로 수행되어 모두 실패했고
Strategy D도 같은 날 별도로 종료됐다. 그 §6은 이 문서가 대체한다.

```text
STRATEGY C FAMILY
STATUS = CLOSED

C-1  C-M                          GATE-C2           = FAIL
C-2  C-E0                         GATE-CE0          = FAIL   (H1 FAIL, H2 FAIL)
C-3  EVENT_QUALITY_MOMENTUM_V0    GATE-EQM-V0       = FAIL   (H1 FAIL, H2 FAIL, H3 FAIL)
C-4  CONTEXTUAL_ANALOG_MOMENTUM_V0  C4_INTERNAL_SCREEN = FAIL

DIRECTIONAL EARLY MOMENTUM THESIS = REJECTED
```

"실패작이었다"는 뜻이 아니다. **사전등록된 절차로 네 단계를 충분히 검증했고, 결과가 실패였으므로 연구 프로토콜에
따라 정상 종료했다**는 뜻이다. 실패 결과와 재현 자산은 삭제하지 않고 보존한다.

---

## 1. 네 단계와 각각이 무엇을 반증했는가

| 단계 | 가설 | 판정 | 실패한 지점 |
| --- | --- | --- | --- |
| **C-1 / C-M** | 급등 + 거래량 급증 후보에 전방 방향성 알파가 있다 | `GATE-C2 = FAIL` | 9개 조건 중 조건 3만 실패. MFE Lift는 존재(10D +15% Lift 1.358, CI 하한 1.232)하나 10D 종가 초과가 -0.06%p이고 CI가 0을 포함 |
| **C-2 / C-E0** | 기업 이벤트가 동반되면 방향성이 개선된다 | `GATE-CE0 = FAIL`, H1·H2 FAIL | 조건 1·2·3·4·6 실패. EM 5D 종가 초과 **-0.256%p**, M_ONLY **+0.319%p**로 **부호가 가설과 반대** |
| **C-3 / EQM-V0** | 이벤트의 규모(매출 YoY)가 크면 방향성이 개선된다 | `GATE-EQM-V0 = FAIL`, H1·H2·H3 FAIL | 조건 2·4·8 실패. EQ2 +0.265%p가 M_ONLY +0.319%p를 넘지 못함(차이 -0.054%p) |
| **C-4 / C4-V0** | 유사했던 과거 상황의 실제 결과 분포가 방향을 선별한다 | `C4_INTERNAL_SCREEN = FAIL` | C2·C3·C4·C10 실패. 맥락을 더할수록 IC가 단조 감소하고 F3가 F0보다 **유의하게 나쁨** |

각 단계의 PIT 감사는 모두 위반 0이었다. 표본 부족도 아니었다. C-2는 사전검사 P1~P8 전부 통과,
C-4는 커버리지 게이트와 엔지니어링 게이트 E1~E9를 전부 통과한 뒤에야 수익률을 읽었다.

즉 **네 번의 실패는 배관 문제가 아니라 가설에 대한 답이다.**

## 2. C-4 핵심 수치

run `c4scr1-a449a688adc8e079acf9`, 선언 `c4f1d243…`, 라이브러리 1,102,275행, 쿼리 9,590행, Top-50 확보 100%.

| Family | 차원 | Mean daily IC | 95% CI |
| --- | --: | --: | --- |
| F0 Market | 11 | **+0.06031** | [+0.03469, +0.08652] |
| F1 +Event | 22 | +0.03731 | [+0.00384, +0.06967] |
| F2 +Quality | 26 | +0.03688 | [+0.00441, +0.06877] |
| F3 Full Context (Primary) | 37 | **+0.02239** | [-0.00748, +0.05134] |
| **F3 − F0** | | **-0.03793** | **[-0.06927, -0.00710]** |

```text
Context를 추가할수록 IC 감소.
F3는 F0보다 유의하게 나쁨.
```

방향성 알파는 없다.

```text
F3  Q5-Q1 close excess = +0.0027   95% CI [-0.0061, +0.0118]  (0 포함)
F3  Q5    close excess = -0.0044   (음수)
```

## 3. 보존하는 관찰 (Primary evidence로 승격하지 않는다)

C를 살리는 근거로 쓰지 않는다. 연구 기록으로만 남긴다.

```text
C-1:
Directional alpha 없음
Volatility expansion observed
(MFE Lift가 뉴스 없는 M_ONLY 부분집합에 몰려 있었다)

C-4:
Analog predictor also sorts volatility characteristics
rather than robust directional return
```

C-4 분위표가 그것을 직접 보여준다. 예측값이 가장 낮은 Q1이 MFE도 가장 크고(+0.1237) MAE도 가장 깊다(-0.1040).
Q5는 양쪽 다 작다(+0.0778 / -0.0702). D일 True Range와 ATR 확장을 좌표에 넣어 흡수하려 했으나 흡수되지 않았다.

또 하나, **post-hoc technical observation으로만** 남긴다.

```text
return_5d IC  ≈ -0.06695   (유의)
F0 IC         ≈ +0.06031
```

크기가 비슷하고 부호가 반대다. F0 아날로그 예측값이 후보 풀 안의 단기 반전을 재포장한 값일 가능성을 시사한다.
**이것은 사전등록된 검정이 아니며 어떤 판정의 근거도 아니다.** 그리고 그 반전조차 종가 분위 스프레드로는
유의하게 바뀌지 않았다(C3 FAIL).

## 4. C-4와 Strategy D의 관계

```text
C-4 F0 resembles Strategy D N2a baseline,
but C-4 is not Strategy D.
```

C-4의 F0(시장 좌표만)는 D의 N2a baseline과 구조가 유사하다. 그러나 C-4는 상황(Situation) 아날로그이고
D는 차트(Chart) 아날로그로, 매칭 대상·쿼리 모집단·정보원이 다르다. Strategy D의 상태와 산출물은
**이 종료 작업에서 수정하지 않는다.** D의 종료 처리는 별도 세션과 별도 commit에서 한다.

## 5. 재개 금지

```text
Reopening the Strategy C family by:
- threshold tuning
- event-window tuning
- horizon tuning
- K tuning
- distance-function tuning
- feature weighting or dimensionality reduction
- feature-family reconstruction
- post-hoc event-class selection
- F0-only reuse as a new strategy
- building a trading backtester on any C-family hypothesis

is prohibited under the frozen research protocol.
```

한국어로 같은 의미를 다시 적는다. **Strategy C 계열 방향성 연구는 종료되었다.** 임계값 재조정, event window 변경,
horizon 변경, K 변경, 거리함수 교체, feature 가중치 도입이나 차원 축소, feature family 재구성, 이벤트 클래스 사후 선별,
**F0만 떼어 새 전략으로 만드는 것**, 그리고 C 계열 가설 위에 트레이딩 백테스터를 만드는 것은 동결된 연구 프로토콜상
금지된다.

특히 F0-only 재사용을 명시적으로 금지하는 이유는 두 가지다. 첫째, Primary family는 결과를 보기 전에 F3로 고정했으므로
F0로 갈아타는 것은 사후 선택이다. 둘째, F0 구조는 Strategy D의 N2a baseline과 거의 같고, D는 같은 날 별도로 종료됐다.

다른 가설은 **새 연구 ID와 새 사전등록**으로만 시작한다.

## 6. 동결 자산과 checksum

이 종료 시점에 전부 재검증했고 모두 일치한다.

| 선언 | canonical sha256 |
| --- | --- |
| `strategy_c/c_m_selection_rules_v1.json` | `c769aea5f25bcc46cfdc40a7d74fe325b5059f630714c007d1285cb2d9865d54` |
| `strategy_c/v2/c_v2ab_rules_v1.json` | `2226600f80cacb24…` |
| `strategy_c/v2/c_e0_rules_v1.json` | `48fc34cb06dd88b1bf3d6c5083366cf768fd269f355363a6d83789f2d0f62c71` |
| `strategy_c/v2/c_e0_event_taxonomy_v1.json` | `6ee5a16a9828d6b1d04d76f005117629fd84341399eeadb243c6b41567a45c6b` |
| `strategy_eqm_v0/eqm_v0_rules_v1.json` | `0cd09ba2bd2e03d083a46696d26af55bfb7f0b46c765e040f6d5b87d48d4840c` |
| `strategy_c4_analog/c4_internal_rules_v1.json` | `c4f1d243f17c21cf9954536d0874640c1743d03c5afa666b247f8765099dddf8` |

checksum 방식은 네 단계 모두 같다: `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`의 sha256.

### 동결 저장소가 변하지 않았다는 증거

C-4는 라이브러리 전체를 덮기 위해 SEC 제출물 1,534 CIK와 companyfacts 2,552 문서를 새로 받았다. 그 바이트는
`data/runtime/strategy_c4/{sec,xbrl}`에만 쌓았고 판독기가 동결 저장소를 먼저, C-4 저장소를 나중에 본다.
C-4 run이 기록한 동결 저장소 digest가 종료된 두 연구가 기록한 값과 같다.

```text
frozen_sec   bf4d744c73cf…   = C-E0 run-record digest
frozen_xbrl  fcdd2e5f6c65…   = EQM-V0 store digest
```

## 7. 보존 자산

`data/runtime`은 gitignore 대상이므로 원시 데이터와 run 산출물은 저장소에 넣지 않는다. 로컬에서 삭제하지 않는다.

| 대상 | 위치 |
| --- | --- |
| C-M 결과 | `data/runtime/strategy_c/runs/cmsel1-855b6a0ce64e3698fc74/` |
| C-E0 이벤트 저장소·결과 | `data/runtime/strategy_c/e0/{raw,runs/ce01-234478e8f7ff27570e13}` |
| EQM XBRL 저장소·결과 | `data/runtime/strategy_eqm/v0/{raw,features,runs/eqm0-ebdad0d225cca00f8fdc}` |
| C-4 증분 저장소 | `data/runtime/strategy_c4/sec` (`5afe4f4e…`), `data/runtime/strategy_c4/xbrl` (`927a011b…`) |
| C-4 run | `data/runtime/strategy_c4/runs/c4scr1-a449a688adc8e079acf9/` |
| C-4 커버리지 감사 | `data/runtime/strategy_c4/coverage/` |

문서·선언·manifest·digest는 저장소에 남는다. 코드는 `backend/app/backtest/strategy_c_selection/`, `strategy_c_v2/`,
`strategy_c_e0/`, `strategy_eqm_v0/`, `strategy_c4_analog/`에 있고 테스트는 `backend/tests/strategy_c/`,
`strategy_eqm_v0/`, `strategy_c4_analog/`에 있다.

## 8. 이 종료의 의미

> C 계열에서 Momentum, Event Presence, Event Quality, Contextual Historical Analog까지 단계적으로 검증했으나
> 반복 가능한 방향성 Alpha를 확인하지 못했고, 사전등록된 종료 규칙에 따라 연구를 정상 종료했다.

이 문서는 C 계열을 다시 열려는 시도가 있을 때 먼저 읽는 기준 문서다.
