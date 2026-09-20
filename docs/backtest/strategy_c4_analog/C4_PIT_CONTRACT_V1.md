# C-4 PIT 계약

작성 2026-09-20. C `C_PIT_CONTRACT_V1.md`, C-E0 `c_e0_rules_v1.json > pit`, EQM `pit_audit`,
D `D_PIT_CONTRACT_V1.md`를 상속하고 아날로그 탐색에만 있는 규칙을 추가한다.

## 1. 좌표는 D일 이전만 읽는다

| 계층 | 읽는 범위 | 강제 방법 |
| --- | --- | --- |
| Layer A 시장행동 | D-20..D (52주 좌표는 F0에서 제외) | C의 `features.compute`를 그대로 호출, C의 PIT 감사 대상 |
| Layer B 이벤트 | `W_PRIMARY = [open(D-2), close(D))`, age는 [D-20, D] | acceptance UTC, `effective_time < close(D)` |
| Layer C 품질 | 그 accession 자신의 fact만 | `accn` 일치 검사 |
| Layer D OBV/VWAP | 그리드 첫 세션..D | 누적합은 D 이후를 포함할 수 없다 |
| Layer E 시장맥락 | SPY D-14..D, 순위는 d < D | expanding rank |

가격은 전부 `P(t) = raw(t)/F(t)`이고 `F(t)`는 t 이하에 집행된 분할만 반영한다. `adjusted=true`는 금지다.

## 2. 정규화가 미래를 보지 못한다

- 종목 수준 연속 좌표: **그날 base-eligible 단면 안에서의 백분위** `(평균순위-1)/(n-1)`. 다른 날짜를 구조적으로 볼 수 없다.
- 날짜 수준 좌표(SPY 3개)와 희소 좌표(`revenue_yoy`): **d < D 인 행만 모은 분포에서의 백분위**.
- 전체 표본 평균·표준편차·분위수는 어떤 좌표에도 쓰지 않는다.
- 결측은 선언된 중립값 0.5를 받고, 결측 사실은 그 계층의 지시자 차원(`event_type_UNKNOWN`,
  `quality_observable_flag`)이 싣는다. **UNKNOWN을 NO_EVENT나 0으로 바꾸지 않는다.**

## 3. 이벤트 시각

EDGAR `acceptanceDateTime`은 UTC다(C-E0가 40/40 대조로 확정). `effective_time >= close(D)`인 제출물은
어떤 좌표에도 들어가지 않는다. CIK는 `as_of <= D` 스냅샷에서만 오고, 다음 스냅샷과 CIK가 다르면 그 구간 전체가
UNKNOWN_MAPPING이다. UNKNOWN_MAPPING, UNKNOWN_COVERAGE, UNKNOWN_PIT, UNKNOWN_FORM, EXCLUDED_MA_TARGET은
C-E0의 우선순위 그대로 UNKNOWN 범주가 된다.

## 4. 아날로그는 과거에서만 온다

```text
analog_date <= query_date_idx - 20
```

Library 행렬은 세션 오름차순으로 보관하므로 "허용되는 아날로그 전체"는 그 행렬의 **접두부**다. 미래 아날로그는
걸러지는 것이 아니라 도달 불가능하다. 20세션은 최대 라벨 horizon 10과 최대 피처 lookback 20을 모두 덮는다.
따라서 아날로그의 라벨 창(d+1..d+10)은 query의 피처 창에도, query의 라벨 창에도 닿지 않는다.

## 5. 동일 기업과 집중

같은 ticker는 전 기간 제외한다. `composite_figi`가 양쪽 모두 non-null이고 같으면 다른 ticker여도 제외한다.
ticker당 1건, FIGI당 1건, analog_date당 5건이 상한이다.

## 6. 라벨

C의 정의를 그대로 쓴다. `P0 = open(D+1)/F(D+1)`, 창 D+1..D+h, `close_return_h`는 [-1,1] clip,
MFE/MAE는 clip 없음, 유효성은 진입봉·horizon봉·라벨 CA 의심. 초과수익은 **같은 세션의 C-M 매칭 셀**
(price_bucket, atr_bucket, adv20_bucket) 평균을 뺀 값이고, 셀 구성원이 5 미만이면 같은 세션 전체 평균을 쓰며
`cell_matched=0`으로 기록한다. Query 행과 Library 행에 **같은 정의**를 적용한다.

라벨 코드는 좌표 코드와 모듈이 분리되어 있고, `features.py`가 라벨 모듈을 import하지 않는다는 것을 AST 테스트가
강제한다.

## 7. 감사 (수익률을 보기 전에 실행)

| 코드 | 내용 | 통과 기준 |
| --- | --- | --- |
| E1 | 선택된 아날로그 중 query 날짜보다 미래 | 0 |
| E2 | embargo 위반 | 0 |
| E3 | 동일 ticker/FIGI, cap 초과 | 0 |
| E4 | analog_date cap 초과 | 0 |
| E5 | D에서 잘라낸 세계로 다시 계산한 row D가 원본과 불일치한 셀 | 0 |
| E6 | 아날로그 라벨 창이 query D+1 이상에 닿음 | 0 |
| E7 | 정규화·라이브러리 digest 2회 동일 | 동일 |
| E8 | 동결 C-M0 후보 집합 재현 | 완전 일치 |

E5는 두 층에서 본다. 원시층은 `truncate(panel, D)`로 C 피처·이벤트 테이블·OBV·VWAP를 다시 만들어 row D를 비교하고,
정규화층은 원시 배열을 [0..D]로 잘라 다시 정규화해 row D를 비교한다. 둘 다 셀 단위 완전 일치를 요구한다.

하나라도 실패하면 **수익률 분석을 실행하지 않는다.** 수익률을 본 뒤 PIT 위반이 발견되면 이 선언의 판정은 FAIL이고,
재시도는 새 선언으로만 가능하다.

## 8. 동결 자산 불변

C-M, C-E0, EQM-V0, D의 규칙 JSON·코드·run 디렉터리·저장소를 쓰지 않는다. C-4가 새로 받은 SEC 제출물과 XBRL은
`data/runtime/strategy_c4/{sec,xbrl}`에 따로 쌓고, 판독기는 동결 저장소를 먼저, C-4 저장소를 나중에 본다.
따라서 동결 저장소의 digest는 종료된 연구가 기록한 값 그대로 남는다.
