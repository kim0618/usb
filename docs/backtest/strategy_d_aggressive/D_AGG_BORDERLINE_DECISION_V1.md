# Strategy D-AGGRESSIVE BORDERLINE Decision Note (MARKET_STRUCTURE_ANALOG_TAIL_V1)

작성 2026-09-21. 이 문서는 D0 선언 `on_verdict.BORDERLINE`이 trading rule 작업 전에 요구하는 서면 노트다.

> *"authorizes a written note (cost, realized effect, the missed condition, what the backtest would
> have to show) before any trading-rule work; no backtest without that note"*

이 노트는 판정을 바꾸지 않는다. 코드도, trading rule도, backtester도 만들지 않는다. 결정하는 것은 하나다:
제한된 다음 단계(D-AGG-3 사전등록)로 갈 정보가치가 있는가.

```text
GATE-D-AGG-SCREEN = D_AGG_SCREEN_BORDERLINE      (변경 불가: PASS로도 FAIL로도 바꾸지 않는다)
하드 조건 H1~H7    전부 PASS
PASS 수준 미달      T4 하나: NTL 1.0438 < 1.10
BORDERLINE 하한     NTL >= 1.00 충족
근거 run            dagg2-588ada9e677f (D-AGG-1 dagg1-e90649a1e15a, rules 1d2b453a…033a8)
```

---

## 1. 실현 효과 (노트 필수 항목 "realized effect")

| 항목 | 값 |
| --- | --- |
| Setup UP10 (세션 평균) | 20.46% |
| Universe UP10 | 13.34% |
| Setup DN10 | 16.85% |
| Universe DN10 | 11.46% |
| TL | 1.5346, 95% CI [1.4250, 1.6834] |
| DL | 1.4702, 95% CI [1.352, 1.636] |
| NTL | 1.0438, 95% CI [0.959, 1.128] |
| AG | 1.0401, 95% CI [0.928, 1.133] |

| 블록 | TL | NTL |
| --- | --- | --- |
| B1 | 1.371 | 1.058 |
| B2 | 1.695 | 1.024 |
| B3 | 1.549 | 1.142 |
| B4 | 1.517 | 0.990 |

TL > 1인 블록은 4/4다. 집중도: 단일 종목 최대 0.52%, 상위 5세션을 빼도 TL 1.4962, 상위 10종목을 빼도 TL 1.5024.

## 2. 미달 조건 (노트 필수 항목 "the missed condition")

T4는 "상방 꼬리의 농축이 하방 꼬리의 농축을 10% 이상 넘는가"를 묻는다. 관측값은 4.4%였다. 하드 하한(H5
NTL >= 1.00, H6 AG >= 1.00)은 각각 0.044와 0.040 차이로 통과했다. 두 값 모두 bootstrap CI가 1을 포함하므로,
하드 하한 통과도 이 표본에서 통계적으로 견고한 사실이 아니다.

---

## 3. 긍정적 증거 (D-AGG를 즉시 종료하지 않는 근거)

- **A. 강한 상방 꼬리 농축.** TL 1.535. 같은 날 적격 universe 전체보다 +10% excursion이 약 53% 더 잦고, CI
  하한 1.425도 1에서 멀다.
- **B. 기간 안정성.** TL이 4개 연속 블록 모두에서 1.37~1.70이다. 한두 시기의 현상이 아니다.
- **C. 집중도 문제 없음.** 상위 날짜와 상위 종목을 제거해도 TL이 약 1.50으로 유지된다. 소수의 대박 사건이
  만든 결과가 아니다.
- **D. 데이터 무결성.** PIT violation 0, 결측 편향 게이트 PASS(setup 무효율이 universe보다 낮음), 2회 실행 결정성
  일치.

## 4. 부정적 증거 (정면 기록)

- **A. 상방 비대칭이 약하다.** TL 1.535, DL 1.470, NTL 1.044. 상방 꼬리가 늘어난 만큼 하방 꼬리도 거의 같이
  늘었다. 현재 A(q)는 "상방 급등 후보"보다 **"큰 움직임 후보"**를 찾고 있을 가능성이 높다.
- **B. NTL과 AG의 우위가 통계적으로 확립되지 않았다.** NTL CI [0.959, 1.128], AG CI [0.928, 1.133]. 이 2년
  데이터로 "상방이 하방보다 더 농축된다"고 쓰지 않는다.
- **C. 변동성 구성이 lift 대부분을 설명한다(X-7, secondary).** setup의 세션별 rv_20 분위 구성에 맞춰 재가중한
  universe와 비교하면 TL 1.079, DL 1.026, NTL 1.052다. 원래 TL 1.535에서 초과분 0.535 중 약 0.08만 남는다.
- **D. 선택을 강화해도 비대칭이 늘지 않는다.** NTL은 Top 10% 1.044, Top 5% 1.007, Top 2% 1.026이다. 선택을
  좁히면 TL과 DL이 함께 커질 뿐이다(Top 2% TL 2.20 / DL 2.15). **Top 5%나 Top 2%로 재튜닝하는 것은 금지한다.**
- **E. A의 양 극단이 모두 변동성 종목이다(X-10).** 10분위 프로파일이 U자형이다. 최하위 분위가 TL 1.94 / DL 2.22로
  최상위보다 꼬리가 더 두껍다. D0 사전 노출(분위 평균 MFE/MAE의 U자형)과 같은 구조다.

## 5. 현재 연구 해석

```text
확인된 것      A(q) 상위 10%는 향후 5세션 안에 큰 가격 excursion이 날 종목을 농축한다 (양방향).
확인되지 않은 것  A(q)가 고변동성 종목을 고르는 것 이상으로, 상방 payoff를 하방 payoff보다
               유리하게 농축한다.
```

따라서 현재 D-AGG는 **Tail / Volatility Event Detector 후보**로만 부를 수 있다. **Profitable Long Trading
Strategy라고 부르지 않는다.**

다만 결론이 한쪽으로만 기울지는 않는다. 두 가지 사실이 판단을 열어 둔다.

1. 변동성 구성을 맞춘 비교(X-7)에서도 TL 1.079가 DL 1.026보다 크다. 같은 변동성 안에서 상방이 약간 더 두껍다는
   방향이다. 크기는 작고 CI는 계산하지 않았다(secondary).
2. V2-A D4에서 같은 신호 A(q)의 `excess_return_5` IC가 +0.0172, 95% CI [+0.0053, +0.0299]로 0보다 유의하게
   컸다. D-AGG-2 X-8에서도 setup의 excess_return_5 평균은 +0.39%(universe +0.12%), 중앙값은 +0.19%(universe
   0.00%)였다. 방향성 정보가 조금 있다는 것은 이미 측정되어 있다. 이것이 변동성 매칭 후에도 남는지가
   측정되지 않았을 뿐이다.

---

## 6. 다음 단계의 목적과 primary question

다음 단계가 있다면 알파를 다시 탐색하는 단계가 아니다. 목적은 하나다:

> **비용을 반영한 뒤, D-AGG setup의 거래당 net expectancy가 같은 날짜·같은 변동성 구성의 control보다
> 유의미하게 높은가?**

```text
Delta = Net expectancy(D-AGG setup) - Net expectancy(volatility-matched control)
```

"Strategy PF > 1"이나 "universe 전체보다 낫다"는 PASS 근거가 될 수 없다. 이 두 비교는 변동성 프리미엄을
걸러내지 못한다. D-AGG-2가 보여준 핵심 불확실성이 바로 변동성 교란이므로, control이 이 교란을 제거해야 한다.

**비용과 Delta의 관계.** setup과 control에 같은 체결 규칙과 같은 비용 모델을 적용하면, 거래당 고정비용은 Delta에서
상쇄된다. 따라서 비용 모델은 Delta가 아니라 **절대 net expectancy > 0 조건**을 좌우한다. 둘 다 게이트에 있어야
한다. 변동성 매칭 덕분에 두 집단의 가격대와 변동성 구성이 비슷하므로, 변동성에 비례하는 비용도 대부분
상쇄된다.

## 7. 백테스트가 보여야 할 것 (노트 필수 항목 "what the backtest would have to show")

D-AGG-3에서 수치를 동결하되, 다음 구조를 벗어나지 않는다.

1. **Delta > 0, 구간으로.** 세션 단위 moving block bootstrap(기존 인프라, 블록 20, 10,000회, 새 seed 1개 선언)의
   95% CI 하한이 0보다 크다.
2. **절대 수익성.** 비용 반영 후 setup의 net expectancy가 0보다 크다. 비교 우위만 있고 손실이 나는 경우는
   PASS가 아니다.
3. **시간 안정성.** 4개 연속 블록 중 3개 이상에서 Delta > 0.
4. **집중도.** 상위 5세션, 상위 10종목을 제거해도 Delta > 0.
5. **체결 현실성.** same-bar 모호성은 보수적으로 처리(아래 §9)하고, 그 처리를 낙관적으로 바꾼 변형은 secondary로만 둔다.

그리고 PASS가 의미하는 범위를 미리 제한한다. 이 창(USB-HIST-V1)을 읽는 다섯 번째 단계이므로 결과는 개발
데이터 위의 증거다. PASS는 Virtual Trading 설계 선언을 허용할 뿐이고, 장기 데이터 구매 근거가 아니다.

## 8. Volatility-matched control (D-AGG-3에서 동결할 권고안)

**Primary: 전수 재가중 control (무작위 추출 없음).**

```text
세션 D마다
  모집단     as-of-D 적격 universe 전체 (D-AGG-1 universe 행, 미래 validity를 보기 전)
  분위 경계  그 모집단의 rv_20 10분위 (V2-A 좌표 rv_20, D 이전 정보만)
  가중치     setup 행의 분위 구성 w_k(D)
  control    분위 k의 모든 적격 종목에 같은 trading rule을 적용한 net return 평균을 w_k로 가중
  제외       그날 setup에 뽑힌 종목은 control 모집단에서 뺀다
```

무작위 draw 대신 전수 가중을 권고하는 이유는 두 가지다. seed와 draw 규칙이라는 자유도가 없고, 추출 잡음이
없어 Delta의 분산이 가장 작다. 무작위로 매칭한 control(분위별 k개, 해시 순서로 결정적 추출)은 secondary로 둔다.

**X-7과 다른 점 하나를 바로잡는다.** X-7은 분위 경계를 *유효 excursion 행*으로 만들었다. 즉 미래 validity를 본
뒤 경계를 정했다. 무효 행이 0.2%라 X-7 수치에 대한 영향은 무시할 만하지만, control 계약은 PIT상 깨끗해야
하므로 경계를 **validity 이전의 as-of 적격 모집단**에서 만든다. setup과 control의 무효 행은 같은 규칙으로 처리하고
계수한다.

**매칭 변수는 rv_20 하나로 고정한다.** 결과를 보고 ATR, tr_today_ratio, rvol을 추가하거나 바꾸지 않는다. 2변수
매칭(rv_20 x tr_today_ratio 5x5)은 secondary로만 사전등록할 수 있다.

## 9. Trading rule 자유도 (D-AGG-3에서 primary 1개만 동결)

새 숫자를 만들지 않는 것이 원칙이다. D0가 이미 동결한 기하를 그대로 쓰는 안을 권고한다.

| 항목 | 권고 primary | 근거 |
| --- | --- | --- |
| 진입 | D+1 시가 (`P0 = O(D+1)`) | D0/D-AGG-1 진입 기준과 동일, D 종가 이후 신호 |
| TP | `+10%` (UP10 문턱) | D0 동결값 재사용, 새 파라미터 0 |
| SL | `-10%` (DN10 문턱) | 같음 |
| 최대 보유 | D+5 종가 청산 | D0 horizon h=5 |
| 갭 | 시가가 TP/SL을 넘어 열리면 그 시가로 체결 | 보수적이며 결정적 |
| same-bar TP·SL 동시 | **SL 먼저**로 처리 | 일봉으로 순서를 알 수 없음, 보수적 |
| 비용 | 편도 고정 bp, US-B 기존 관례(E 계열 10bp)와 같은 틀 | setup과 control에 동일 적용 |
| 상장폐지/창 안 결측 | D-AGG-1 무효 규칙 그대로 (거래 제외, 계수) | 새 청산 규칙을 만들지 않음 |

secondary로만 둘 수 있는 것: 시간청산만(TP/SL 없음, `close_return_5`와 같음), same-bar TP 먼저, 비용 2배. 결과를
보고 primary를 바꾸지 않는다. grid search는 하지 않는다.

**D-AGG-2 결과가 이 선택에 주는 경고.** 대칭 bracket에서는 NTL이 1 근처이고 동시 발생을 SL로 처리하므로, setup의
bracket 성과가 control과 크게 다르지 않을 사전 가능성이 높다. 이 경고를 이유로 규칙을 바꾸지 않는다. 그렇게
하는 것이 바로 사후최적화다.

## 10. 금지 (현재 2년 결과를 본 뒤의 사후최적화)

```text
Top 5% / Top 2%로 변경              UP15를 primary로 변경
A(q) 문턱 최적화                    변동성 필터를 추가하고 가장 좋은 구간 선택
특정 블록 제거 (B2, B4 포함)         특정 ticker / theme 제거
NTL을 높이는 feature 추가            A 하위 분위 사용 (D0 금지 유지)
TP/SL/보유기간 grid에서 최선 선택      control 정의를 결과 보고 변경
```

## 11. 비용 (노트 필수 항목 "cost")

| 항목 | 추정 | 근거 |
| --- | --- | --- |
| D-AGG-3 사전등록 | 문서 1~2개, 코드 0 | D0와 같은 형식 |
| 백테스트 구현 | 작다. 일봉 event 시뮬레이터 1개(약 300~500줄) + control 가중 + 게이트 재사용 | 진입가, 창, validity, universe 행, rv_20 경로, bootstrap, 블록, leave-out이 D-AGG-1/2에 이미 있다. A 엔진 backtester나 SimBroker를 쓰지 않는다 |
| 실행 | 분 단위 | D-AGG-2가 120초 (6,630 setup + 약 58만 universe 행) |
| 데이터 | 0 | 동결 창 재사용. 구매·수집 없음 |
| 기회비용 | 이 창을 다섯 번째로 읽음 | 결과의 증거력은 개발 데이터 수준으로 제한 |

## 12. 검정력 (정보가치를 깎는 요인)

D-AGG-3이 정식으로 계산하되, 사전 추정은 이렇다(가정이며 측정값이 아니다).

```text
setup 거래 수         약 6,600 (세션당 30, 221세션)
거래당 수익 sd        상위 분위 변동성 기준 5세션 약 6~10% (±10% bracket이면 줄어듦)
control              전수 가중이라 추출 잡음 거의 없음, Delta 분산은 대부분 setup 쪽
세션 내 상관          같은 날 30종목, 설계효과 1.5~3
SE(Delta)            약 0.10% ~ 0.25% / 거래
80% 검정력 MDE        약 0.3% ~ 0.7% / 거래
```

기대 효과 크기는 이 MDE와 같은 자리에 있다. 변동성 매칭 전 excess_return_5 격차가 +0.27%(X-8, setup 평균 -
universe 평균)이고, 매칭 후에는 더 작을 것이다. **백테스트가 "우위 없음을 확인"이 아니라 "판단 불가"로 끝날
가능성이 상당하다.** 그래서 D-AGG-3에 착수 중단 조건을 넣는다(§14).

---

## 13. 정보가치와 결정

**VALUE OF INFORMATION: MEDIUM**

높이는 요인:
- 핵심 불확실성(변동성 교란)을 control test 하나로 직접 겨냥한다. 다른 연구로 돌아가지 않아도 답의 형태가 정해져 있다.
- 비용이 낮다. 새 데이터가 없고, 인프라 대부분을 재사용한다.
- 작지만 양의 방향 신호가 이미 두 개 있다(X-7 매칭 후 TL 1.08 > DL 1.03, V2-A A의 유의한 양의 IC).
- 결과가 어느 쪽이든 D 연구선을 닫거나 좁히는 데 쓸 수 있다. 매칭 후에도 Delta가 0 근처면 "A는 변동성
  탐지기"로 확정되어 D 연구 종료 근거가 된다.

낮추는 요인:
- 기대 효과가 MDE와 같은 크기라 판단 불가로 끝날 수 있다.
- 개발 데이터를 다섯 번째로 읽는다. PASS여도 증거력이 제한된다.
- 사전 정보(NTL ~ 1, 좁힐수록 변동성만 강해짐)가 우위 쪽으로 기울어 있지 않다.

**DECISION: PROCEED_LIMITED**

조건:
- 다음 단계는 **문서 사전등록만**이다(D-AGG-3). 코드와 backtester는 D-AGG-3 게이트가 PASS한 뒤에만 허용한다.
- D-AGG-3이 착수 중단 조건(§14)에 걸리면 백테스트를 구현하지 않고 D-AGG를 종료한다.
- 이 결정은 한 번만 쓴다. D-AGG-3 이후의 백테스트가 BORDERLINE이면 추가 연장 없이 동결 정책을 따른다.
  재선언으로 같은 창을 계속 읽지 않는다.

STOP을 택하지 않은 이유: STOP의 근거인 "단순 변동성 선택기일 가능성이 매우 높다"는 X-7(판정 비사용 secondary,
CI 없음)에 크게 기대고 있다. 그 가설을 공식적으로 검증하는 비용이 낮고, 검증 결과가 어느 쪽이든 D 연구선의
종료 여부를 결정할 수 있다.

## 14. D-AGG-3 착수 조건과 중단 조건

D-AGG-3 TRADING / CONTROL PRE-REGISTRATION에서 결과를 보기 전에 동결할 것:

```text
entry / exit / TP / SL / max holding / gap / same-bar ambiguity / cost / delisting
volatility-matched control (분위 경계, 가중, setup 제외, 무효 처리)
primary = 세션 층화 Delta net expectancy, bootstrap 계약, 블록, leave-out
PASS / BORDERLINE / FAIL 수치 기준, secondary 목록(판정 비사용)
```

**착수 중단 조건(DO_NOT_START).** 하나라도 해당하면 백테스트를 구현하지 않고 D-AGG를 종료한다.

1. 결과를 보지 않은 잡음 입력만으로 계산한 80% 검정력 MDE가 거래당 0.50%를 넘는다. 잡음 입력은 세션 간 분산,
   세션 내 상관, bracket 적용 후 sd의 분포 모수이며, setup과 control의 수익 차이는 포함하지 않는다.
2. control 정의를 as-of 적격 모집단만으로 PIT-clean하게 구성할 수 없다.
3. primary 규칙에 새 수치 파라미터가 하나라도 필요하다. D0 기하와 US-B 비용 관례로 채울 수 없는 경우다.

잡음 입력을 추정하려면 setup 수익의 분산을 읽어야 한다. 이 값은 Delta의 부호를 알려 주지 않지만 결과 데이터의 일부다.
D-AGG-3은 어떤 통계를 어떤 순서로 읽는지를 먼저 적고, 수익 차이나 평균을 산출하는 코드를 그 단계에 두지 않는다.

## 15. 공식 기록

```text
D V1                  CLOSED / FAIL
D V2-A                SCREENING_FAIL (WATCHLIST / FORWARD VALIDATION CANDIDATE)
D-AGGRESSIVE D2       D_AGG_SCREEN_BORDERLINE          <- 변경 없음
BORDERLINE 노트        DECISION = PROCEED_LIMITED, VALUE OF INFORMATION = MEDIUM
다음                  D-AGG-3 TRADING / CONTROL PRE-REGISTRATION (문서만)
Backtester / Virtual Trading / Long Data Purchase = NOT AUTHORIZED
```

이 노트는 D0, D-AGG-1, D-AGG-2 문서를 수정하지 않는다. 코드 0, 데이터 읽기 0, 새 통계 계산 0.
