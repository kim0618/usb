# Strategy D PIT Contract V1 (D0)

작성 2026-09-17. 대상: 앞으로 만들 `backend/app/backtest/strategy_d_analog/` (D0 시점 코드 0).
규칙 정본: `d_analog_rules_v1.json` (canonical sha256 `680bf113253fc434102f46a4166ac38b23dfbb4ba7591a7d88c3430c058c0cd3`,
2026-09-17 17:25 KST 선언, D 데이터 읽기 전).

D는 C보다 누수 경로가 하나 더 많다. C는 "D일 feature가 미래를 읽는가"만 막으면 됐지만, D는 **다른 종목의 과거
창과 그 창의 label**을 신호로 쓰므로 이웃의 label이 query 시점에 확정돼 있었는지, 이웃이 query와 같은 시기의
동반 움직임을 가져오지 않는지까지 막아야 한다.

## 1. 시간 정의

| 항목 | 정의 |
| --- | --- |
| `D` | query 종료 세션. 경로 벡터가 읽는 마지막 세션 |
| `as_of` | D 정규장 종료(XNYS, 조기폐장 포함) |
| `available_at` | D+1 04:00 ET. Massive Basic은 당일 T 일봉을 주지 않고, 일봉 volume은 저녁까지 채워지는 중이라 보수적으로 둠(C와 같음) |
| query label 시작 | `P0 = O(D+1)` |
| query label 창 | D+1..D+h. D 자신은 포함하지 않음 |
| 이웃 종료 세션 | `d` (라이브러리 창의 마지막 세션) |
| 이웃 label 창 | d+1..d+h |
| 이웃 사용 조건 | `d + h <= D - W` (§4) |

세션 오프셋은 모두 C raw 캐시의 XNYS 세션 grid index다. 달력일을 쓰지 않는다.

## 2. 가격 기준

`P(t) = raw close(t) / F(t)`, `F(t)` = 실행일 `<= t`인 분할의 `split_from / split_to` 곱 (C `Panel.split_arrays`).

- 경로 벡터는 창 안 비율로만 쓰인다. 표현 A는 z 정규화(스케일 불변), 표현 B는 `ln(P(D-W+i)/P(D-W))`.
  둘 다 `F`의 공통 스케일이 상쇄되므로, D 이후 분할이 D 행 벡터에 들어갈 경로가 없다.
- `adjusted=true` 가격은 금지다(미래 분할이 과거 가격을 바꿈).
- label은 D 이후 행의 `F`까지 읽는다(경제적 가격 조정). label 코드 경로는 인코더와 분리한다(§8).

## 3. Query 벡터 계약

| 입력 | as_of | available_at | lookback | PIT risk | status |
| --- | --- | --- | --- | --- | --- |
| 표현 A `z_i` | D close | D+1 04:00 ET | D-W..D (W+1점) | 미래 분할, 미래 bar | INCLUDE |
| 표현 B `c_i` | D close | D+1 04:00 ET | D-W..D (W점) | 동일 | INCLUDE |
| universe 소속 | 최신 스냅샷 as_of <= D | 스냅샷 as_of | - | 스냅샷 사이 신규 상장 누락(보수), `type` ASSUMED_STATIC | INCLUDE (PARTIAL PIT) |
| close >= $3 | D close | D+1 04:00 ET | D | 없음 | INCLUDE |
| ADV20 >= $5M | D-1 close | D+1 04:00 ET | D-20..D-1 | 없음 | INCLUDE |
| 61봉 연속 | D | D+1 04:00 ET | D-60..D | 이후 상폐로 과거 봉이 사라지면 안 됨 | INCLUDE |
| split 제외 | D | D+1 04:00 ET | 실행일 in (D-60, D] | 발표일 없음, 실행일만 | INCLUDE |
| ca_suspect 제외 | D close | D+1 04:00 ET | P(t)/P(t-1), t in D-59..D | 미기록 분할 | INCLUDE |
| `composite_figi` | 스냅샷 as_of <= D | 스냅샷 as_of | - | FIGI null이면 ticker만 비교 | INCLUDE (same-symbol 규칙 전용) |
| OHLC 경로, volume 경로, 변동성 정규화 | - | - | - | - | **EXCLUDE (D5 후보)** |
| Market cap, Sector, News, Earnings | - | - | - | PIT 불확실 또는 범위 밖 | **EXCLUDE** |

Query 표본: D의 적격 종목을 `sha256('Q|20260917|D|ticker')` 순서로 정렬해 앞 300개. label을 보기 전에 뽑고,
label이 무효인 query는 그 뒤에 빼서 센다. 표본 선택이 미래 생존 여부에 의존하지 않게 하기 위해서다.

## 4. Library와 Neighbor 계약

### 4.1 라이브러리 창의 자격 (창 종료 세션 d 기준)

1. §3의 universe 규칙을 **d 시점 값으로** 전부 충족(스냅샷 as_of <= d, close(d), ADV20(d-20..d-1), 61봉 연속, 분할/CA 제외).
2. `d % 5 == 0` (stride 5).
3. horizon h label이 유효: d+1 봉 존재, **d+h 세션 봉 존재**, label CA 의심 아님(§5).
4. 벡터가 정의됨(A: std > 0, B: 모두 유한).

### 4.2 Query q=(s, D)에 대한 이웃 정책

| 규칙 | 정의 | 막는 것 |
| --- | --- | --- |
| Forward-label embargo | `d + h <= D` | 이웃 label이 query 시점에 확정되지 않은 look-ahead |
| Overlapping-window rule | `d + h <= D - W` (이웃의 창과 결과 전체가 query 창 시작 전에 끝남) | query와 같은 시기의 동반 움직임(섹터 co-movement)이 "과거 analog"로 들어오는 것 |
| 실제 적용 | `d + h <= D - W` (두 규칙의 교집합이 곧 이 식) | - |
| Same symbol | 이웃 ticker == s 이면 전 기간 제외 | 같은 종목의 인접 창이 Top-K 독점 |
| Same FIGI | 이웃 `composite_figi`(d 스냅샷) == query `composite_figi`(D 스냅샷), 둘 다 non-null이면 제외 | 티커 변경한 같은 회사 |
| Ticker cap | 이웃 ticker당 최대 1창 | 다른 종목의 인접 창(stride 5 간격) 중복 |
| Date cap | 같은 종료일 d에서 최대 5창 | 시장 전체가 같은 모양을 그린 하루가 Top-K 독점 |
| Top-K | 50. 정책 적용 후 50 미만이면 `INSUFFICIENT_NEIGHBORS`로 query 제외, 비율 보고 | 얇은 라이브러리에서 나온 신호 |
| 선택 순서 | 유사도 순(A rho 내림차순, B 거리 오름차순), 동률은 d 오름차순, ticker 오름차순. cap을 깨는 후보는 건너뜀 | 비결정성 |

### 4.3 왜 엄격 embargo인가

`d + h <= D`만 쓰면 look-ahead는 없다. 그러나 W=60이면 이웃의 결과 구간(d+1..d+h)이 query 창 안에 있을 수 있고,
그 구간에서 같은 업종 종목이 함께 움직였다면 "비슷한 과거 경로"가 아니라 "지금 같이 움직이는 종목"의 정보가 된다.
이는 다른 가설(동시대 co-movement)이라 V1에서 막는다. 대가로 W=60, h=20 조합은 query 창보다 80세션 이전의
이웃만 쓴다. 평가 시작 index 260은 이 조합에서도 라이브러리 종료 세션 폭 120 이상을 보장하도록 잡았다.

### 4.4 생존편향

- 이후 상폐, 티커 변경 종목의 과거 창은 d 시점에 존재했으므로 라이브러리에 **남아야 한다**. 현재 상장 목록을 소급하지 않는다.
- 이웃 label 유효성은 d+1..d+h 안의 봉만으로 정해지고 `d + h <= D - W`라 query 시점에 이미 알려진 사실이다.
- **C `DISAPPEARED`를 이웃 유효성에 쓰지 않는다.** C 정의는 "D+h 이후 데이터셋 끝까지 봉이 하나라도 있는가"라
  D+h 너머를 읽는다. 이웃에 쓰면 "그 종목이 query 날짜 이후에 거래를 재개했는가"가 신호에 들어간다.
  17:21 KST 규칙 초안에 이 경로가 있었고, 결과와 데이터를 보기 전인 17:25 KST에 "d+h 세션 봉 필수"로 바꿔 다시 선언했다.

## 5. Label 계약

- `close_return_h = P(D+h) / P0 - 1`, [-1, 1] clip. D+h 봉이 있으면 C `LabelSet.close_return[h]`와 같은 값이다.
- `excess_return_h = close_return_h - median(같은 날짜의 label 유효 적격 전 종목 close_return_h)`. 중앙값은 query 표본이 아니라
  전체 적격 universe로 계산한다. 이웃은 자기 종료일 d의 중앙값을 뺀다.
- `mfe_h`, `mae_h`: D+1..D+h의 H, L 극값 / P0 - 1 (secondary 전용).
- 무효: `NO_ENTRY_BAR`(D+1 봉 없음), `MISSING_HORIZON_BAR`(D+h 세션 봉 없음), label CA 의심. 이웃과 query에 같은 규칙.
  C `DISAPPEARED`는 쓰지 않는다(§4.4).
- label CA 의심 창:
  - h <= 10: C `LabelSet.label_ca_suspect` 그대로(D..D+10 검사).
  - h = 20: 위 + D+11..D+20에서 `close(t)/close(t-1)` 또는 `open(t)/close(t-1)`이 3.0 이상 또는 1/3 이하.
  - 이유: C `LABEL_CA_HORIZON = 10`. 합성 panel에서 D+15 10배 점프가 유효 20D label(+900%)로 통과함을 실측(`D_REUSE_MATRIX_V1.md` §2.1).
  - 이웃에 쓸 때 C 창 끝 d+10은 W >= 20이라 항상 `D - W - h + 10 < D`다. CA 창이 query 날짜를 넘지 않는다.
- label 계산 순서: 전체 panel에서 한 번 계산하되, 이웃 label을 읽는 모든 경로는 `d + h <= D - W`를 런타임에 검사하는
  view를 통과한다. 위반 시 `PointInTimeViolation` 성격의 예외로 실행을 멈춘다(조용히 거르지 않음).

## 6. Baseline PIT

| 기준선 | 입력 | PIT 조건 |
| --- | --- | --- |
| N1 | query와 **같은** 라이브러리와 정책, 변동성 5분위 | 5분위는 각 창 종료일의 적격 universe 안 백분위(같은 날짜 횡단면만 사용). 무작위 순서는 해시라 결과와 무관 |
| N1b | 실제 이웃의 종료일 d, 같은 변동성 5분위 | 동일 |
| N2 feature 5개 | `return_W`, `return_1d`, `return_5d`, `realized_vol_W`(D-W+1..D 로그수익률 ddof1), `distance_to_W_high`(H(D-W+1..D)) | 모두 D 이하 행. 날짜별 백분위 표준화(같은 날짜 횡단면) |
| N2a | N2 feature 공간 kNN, D와 같은 라이브러리, 정책, K, cap | 동일 |
| N2b | 날짜별 rank(S) ~ N2 feature 5개 OLS 잔차 | 회귀에 label을 쓰지 않음. 같은 날짜 feature만 |

기준선은 D와 **같은 query 표본, 같은 평가일, 같은 label**로 계산한다. 기준선만 다른 표본을 쓰면 비교가 무효다.

## 7. Mutation tests (D2~D4에서 구현, D0는 목록 동결)

| # | 테스트 | 합성 fixture 기대 | 실데이터 감사 기대 |
| --- | --- | --- | --- |
| 1 | 절단 재실행: `truncate(panel, D)`로 D의 S(q), sigma(q), 이웃 목록 재계산 | 전체 실행과 bit 단위 동일 | 불일치 셀 0 (12개 감사일) |
| 2 | D 이후 모든 bar 변조(고가 x100, 종가/시가/거래량 난수배) | query 벡터, 이웃 목록, S(q) 불변 | 0 |
| 3 | D+1..D+5 가짜 분할 주입(200종목) | 벡터, 이웃, S(q) 불변. query label만 경제적으로 조정 | 0 |
| 4 | 마지막 세션에 봉이 없는(이후 사라진) 종목 삭제 | 다른 종목의 **query 벡터**는 불변. 이웃 목록은 **바뀌어야 함**(사라진 종목의 과거 창이 라이브러리에 있었다는 생존편향 양성 대조) | 벡터 불일치 0, 이웃 변화 감지 >= 1 |
| 5 | Embargo 경계: `d + h = D - W` 인 planted 동일 경로 창은 선택 가능, `d + h = D - W + 1` 인 창은 선택 불가 | 경계 정확 | 런타임 view 위반 0 |
| 6 | Embargo 양성 대조: 규칙을 `d <= D`로 느슨하게 한 복제 설정에서 query 미래와 같은 label을 가진 planted 창 | 느슨한 설정에서만 선택됨, 선언 설정에서는 선택 안 됨 | - |
| 7 | Same symbol: query 종목의 과거 동일 경로 창, 같은 FIGI 다른 ticker 창 | 절대 선택 안 됨 | 이웃 중 same-symbol/FIGI 0 |
| 8 | Cap: 한 종목의 인접 창 여러 개, 한 날짜의 동일 경로 창 10개 | 종목당 1, 날짜당 5 | 위반 0 |
| 9 | 과거 영향 양성 대조: query 창 안 종가 하나를 x2 | 표현 A/B 벡터가 바뀜 | 변화 감지 |
| 10 | 동일 입력 2회 | query 결과표 digest 동일 | 동일 |
| 11 | 규칙 파일 변경 | `config.load_rules()` 거부 | - |
| 12 | 20D label CA 확장 | D+15 10배 점프 -> 20D label 무효, 1/3/5/10D label은 C와 동일 | - |
| 13 | 경로 분리(AST) | `encoder.py`, `similarity.py`, `universe.py`가 `labels`, `signal`을 import하지 않음 | - |
| 14 | Import 경계(AST) | `D_REUSE_MATRIX_V1.md` §4 금지 prefix 0 | - |
| 15 | Query 표본이 label과 무관 | label을 전부 NaN으로 바꿔도 날짜별 표본 ticker 목록 동일 | - |
| 16 | 이웃 유효성의 미래 재개 누수 | d+h..D 사이 봉이 하나도 없는 라이브러리 종목에 D 이후 봉을 **삽입**해도 그 창의 유효성, 이웃 목록, S(q) 불변 (#2는 기존 봉 변조라 이 경로를 못 잡음) | 불일치 0 |

PIT violation = 실데이터 감사 불일치 합 + (결정성 실패 1) + (양성 대조 미감지: #4 이웃 변화, #9 각 1).
GATE-D-ALPHA 조건 10은 이 값이 0일 때만 통과한다.

## 8. 코드 경로 분리 규칙

```text
universe.py, encoder.py, similarity.py   : D 이하 행만. labels/signal import 금지
neighbor_search.py                       : label은 "유효 여부 mask"만, embargo view 통과분만
signal.py, baselines.py                  : 이웃 label 값 (embargo view 통과분만)
evaluate.py                              : query label (평가 전용, 신호 계산에 역류 금지)
label_extension.py / C labels.py         : D 이후 행을 읽는 유일한 위치
```

## 9. 알려진 한계 (PIT가 아니라 데이터 한계)

- Massive 분할 기록에 발표일이 없다. 실행일 기준으로만 쓰고, 실행일 오기는 CA 제외 규칙으로만 방어한다.
- 스냅샷 `type`, `primary_exchange`는 스냅샷 날짜 기준 값으로 가정한다(ASSUMED_STATIC). 분기 사이 신규 상장은 다음 분기까지 빠진다.
- FIGI가 null이거나 FIGI까지 바뀐 기업 재편은 same-company로 잡지 못한다. 같은 회사의 다른 share class(예: 두 클래스 상장)도 잡지 못한다.
- 합병 대기 종목처럼 경로가 거의 평탄한 창은 표현 A에서 z 정규화가 잡음을 키운다. V1은 std == 0만 제외한다.
- 평가창은 약 10개월 단일 regime이고 롤링 2년 캐시가 정본이다. 캐시를 잃으면 같은 run을 API로 다시 만들 수 없다.
- Massive 일봉 volume에 소수 주식이 있다. D는 volume을 ADV20 필터에만 쓴다.
