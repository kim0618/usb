# Strategy D D2 Test Vectors V1

작성 2026-09-18. `D_D2_IMPLEMENTATION_DESIGN_V1.md`의 짝 문서다. **테스트 코드는 아직 없다.** D2 착수 때 `backend/tests/strategy_d/`에 이 표 그대로 옮긴다.

- 규칙 정본: `d_analog_rules_v1.json` canonical `680bf113...0cd3`
- 모든 fixture는 합성 데이터다(API 0, 캐시 0). 수치 기대값은 2026-09-18 numpy로 따로 계산했다(D 코드 없음).
- 공통 표기: `W=20`이면 창은 종가 21개 `P_0..P_20`, `i = 0..20`.
- 허용 오차: A `1e-12`, B `1e-9` (같은 벡터의 d는 `1e-6`), 그 외는 정확히 같음.

## 1. Encoder (V1~V7)

| # | 이름 | fixture | 기대 결과 |
| --- | --- | --- | --- |
| V1 | 선형 상승 | `P_i = 10 + i` | A: `std = 6.0553007081949835`, `z_0 = -1.651445647689541`, `z_20 = +1.651445647689541`, `sum z = 0`, `sum z^2 = 21`. B: 길이 20, `c_20 = ln 3 = 1.0986122886681098`, `c_1 = ln 1.1` |
| V2 | 선형 하락 | `P_i = 30 - i` | A: V1의 z를 뒤집은 것(`z_i = -z_i(V1)`), `rho(V1, V2) = -1.0`. B: `c_20 = -1.0986122886681098`, `d(V1, V2) = 5.834719216810601` |
| V3 | flat | `P_i = 10` (그리고 `10.07` x 61, `7.77` x 21) | A: **`VECTOR_UNDEFINED`** (`ptp == 0`). 참고: `np.std(ddof=0)`은 10.07 x 61에서 `5.3e-15`, 7.77 x 21에서 `2.7e-15`로 0이 아니다. std 비교로 구현하면 이 테스트가 실패해야 정상이다. B: **정의됨**, 영벡터 |
| V4 | 같은 모양, 다른 가격 | `P' = 100 x P(V1)` | A: `max abs(z' - z) <= 1e-12` (실측 2.2e-16), `rho = 1`. B: `d <= 1e-12` (실측 0) |
| V4b | query와 라이브러리 경로 일치 | 같은 (ticker, e)를 query로 한 번, 라이브러리 행으로 한 번 encode | 두 벡터가 **bit 단위로 같음** |
| V5 | 같은 방향, 다른 진폭 | `P_a = 10 + i` (+200%), `P_b = 10 + 0.1 i` (+20%) | A: `rho = 1.0` (구분 못 함). B: `c_20(b) = ln 1.2 = 0.1823215567939546`, `d = 2.805256433161798` |
| V5b | 평행 이동 | `P' = 3 P(V1) + 7` | A: `rho = 1.0`. B: `d > 0` (A와 B가 다른 정보라는 확인) |
| V6 | 분할 포함 | raw close: `i < 10`이면 100, `i >= 10`이면 50. 세션 `e-10`에 2:1 분할(`split_from=1, split_to=2`) | universe: `SPLIT_WINDOW`로 제외(실행일이 `(e-60, e]` 안). encoder만 직접 호출하면 `P = close/F`가 100으로 일정 -> A `VECTOR_UNDEFINED`, B 영벡터(-50% 점프가 없음을 확인). 분할 실행일을 `e+3`으로 옮기면 e 시점 벡터는 분할이 없을 때와 bit 단위로 같음 |
| V7 | 결측 봉 | V1에서 세션 `e-5` close = NaN | universe: `NO_HISTORY`. encoder에 직접 넣으면 **hard fail**(보간, ffill, bfill 없음). 세션 `e-60`(창 밖, 61봉 조건 안)만 NaN이어도 `NO_HISTORY` |

## 2. Similarity (V8~V12)

| # | 이름 | fixture | 기대 결과 |
| --- | --- | --- | --- |
| V8 | 같은 벡터 | q = l = V1 벡터 | A: `abs(rho - 1) <= 1e-12`, `rank_score = rho`. B: `d <= 1e-6`, `rank_score = -d`. 전개식이 음수로 나오면 0으로 자름(`d2 >= 0`) |
| V9 | 반대 벡터 | A: `z` vs `-z` / B: `c` vs `-c` (c = V1 B 벡터, `||c|| = 3.3017118542166797`) | A: `rho = -1.0`. B: `d = 2||c|| = 6.603423708433359` |
| V10 | 진폭만 다름 | B: `c` vs `2c` / 영벡터 vs `c` | B: `d = ||c|| = 3.3017118542166797` (두 경우 모두) |
| V11 | 상수 벡터 | A 영벡터 또는 분산 0 벡터를 `score_block`에 직접 전달 | **hard fail** (정의된 벡터만 들어와야 함). B 영벡터는 정상 계산 (V10) |
| V12 | 동률 | 같은 벡터 3개: `(end 100, "BBB")`, `(end 100, "AAA")`, `(end 95, "ZZZ")`, 라이브러리 입력 순서는 섞어서 | 순서 `(95, ZZZ) -> (100, AAA) -> (100, BBB)`. `Panel.tickers` 정렬 순서 = 문자열 순서 가정도 여기서 확인 |

## 3. Neighbor (V13~V20)

기본 설정: `W=20, h=5, D=300`, `K=50`. 적격 라이브러리는 서로 다른 ticker 200개 x 종료일 여러 개를 난수 경로로 채우고, 검사할 행만 심는다.

| # | 이름 | fixture | 기대 결과 |
| --- | --- | --- | --- |
| V13 | same ticker 제외 | query ticker `QQQX`의 과거 창(종료 200)을 query와 같은 벡터로 심음 | 선택 안 됨. 1위는 그다음으로 비슷한 다른 ticker. `candidates_after_same_symbol = cut - (QQQX 행 수)` |
| V14 | same FIGI 제외 | (a) 이웃 `OLDT`(d 스냅샷 FIGI `BBG000X`), query `NEWT`(D 스냅샷 FIGI `BBG000X`), 같은 벡터 / (b) 한쪽 FIGI null | (a) 선택 안 됨. (b) 선택됨 (둘 다 non-null일 때만 제외, D0 알려진 한계) |
| V15 | embargo 경계 | 같은 벡터 창을 `d = 275`에 심음 (`d + h = 280 = D - W`) | **선택됨** (rank 1). R6/R7 통과 |
| V16 | embargo +1 위반 | V15와 같은 행, query 날짜만 `D = 299` (`d + h = 280 > 279 = D - W`) | 후보에 없음(prefix cut). `EmbargoView`로 이 행의 유효성을 직접 요청하면 `PointInTimeViolation` |
| V17 | ticker 집중 | ticker `CONC`의 창 10개(종료 100, 105, ..., 145)를 최상위 10개 score로 심음 | `CONC`는 1개만 채택(가장 높은 score 창). 나머지 49개는 다른 ticker |
| V17b | cap 순서 의존 | 종료 150에 score 최상위 5개(ticker A1..A5), 그다음이 `X`의 종료 150 창, 그다음이 `X`의 종료 155 창 | `X`의 150 창은 date cap으로 건너뛰고 **155 창이 채택**됨. "ticker별 최선 창만 미리 남기기"로 구현하면 실패해야 함 |
| V18 | 날짜 집중 | 종료 150에 같은 score 창 10개(`T00..T09`) | `T00..T04` 채택, `T05..T09` 건너뜀 (ticker 오름차순 동률 처리) |
| V19 | K보다 적은 적격 | 정책 적용 후 후보 30개 / 후보 60개가 전부 한 ticker | 둘 다 `INSUFFICIENT_NEIGHBORS`, `accepted_count` = 30 / 1, Top-M이 `live`까지 확장된 뒤 멈춤 |
| V20 | 결정적 순서 | 라이브러리 입력 행 순서 셔플, 2회 실행, `M0 = 50`과 `M0 = 전체` 비교(난수 fixture seed 5개) | 이웃 목록과 content digest 모두 동일 |

## 4. PIT (V21~V25)

기본: 합성 panel 40종목 x 400세션, 분할 몇 개 포함, query 날짜 `D = 300`.

| # | 이름 | 변조 | 기대 결과 |
| --- | --- | --- | --- |
| V21 | 미래 봉 변조 | `D+1..` 모든 행: high x100, close/open x U(0.2, 5), volume x U(0.1, 10) | D의 query 벡터, 후보 cut, 이웃 목록, `metric_value` **bit 단위 동일** |
| V22 | 미래 분할 | 적격 종목 200개(합성은 전 종목)에 실행일 `D+1..D+5` 가짜 분할 주입 | V21과 같음. universe mask(D 행) 동일 |
| V23 | 미래 재개 (D0 PIT #16) | (a) 라이브러리 종목 `GAPX`: `d+h`에는 봉이 있고 `d+h+1..D`에는 없음. D 뒤에 봉 삽입 / (b) `d+h` 봉이 없는 종목에 D 뒤 봉 삽입 | (a) 그 창의 `valid_h`, 이웃 목록 불변. (b) 여전히 `MISSING_HORIZON_BAR`로 무효, 이웃 목록 불변 |
| V24 | raw digest 불일치 | freeze 행 하나의 sha256을 한 글자 바꾸거나 raw 파일 1바이트 변경 | `load_daily_history`가 `DatasetDigestMismatch`로 **즉시 중단**, 산출물 파일 0 |
| V25 | 규칙 checksum 불일치 | 규칙 JSON 사본의 `top_k`를 51로 | `config.load_rules`가 `RulesChanged`. D2 산출물을 다른 checksum으로 D3 loader에 넘기면 거부 |

## 5. D0 PIT mutation 목록(16개) 중 D2 담당 대응

| D0 PIT # | 내용 | D2 테스트 |
| --- | --- | --- |
| 1 | 절단 재실행 bit 동일 | V21과 같은 fixture로 `truncate(panel, D)` 재실행 비교 (추가 케이스 T1) |
| 2 | 미래 bar 변조 | V21 |
| 3 | 미래 분할 | V22 |
| 4 | 이후 사라진 종목 삭제 (벡터 불변, 이웃은 바뀌어야 함) | 추가 케이스 T4: 마지막 세션 봉이 없는 종목 열 삭제 -> 남은 종목 벡터 동일, 이웃 목록 변화 >= 1 |
| 5 | embargo 경계 | V15, V16 |
| 6 | embargo 양성 대조 | 추가 케이스 T6: 규칙 사본에서 embargo를 `d <= D`로 느슨하게 한 설정(테스트 전용 경로)에서만 planted 창이 선택됨 |
| 7 | same symbol / FIGI | V13, V14 |
| 8 | cap | V17, V17b, V18 |
| 9 | 과거 영향 양성 대조 | 추가 케이스 T9: 창 안 종가 하나 x2 -> A, B 벡터 변화 감지 |
| 10 | 결정성 | V20 |
| 11 | 규칙 변경 거부 | V25 |
| 12 | 20D label CA 확장 | 추가 케이스 T12: D+15 10배 점프 -> `valid_20 = False`, h in {1,3,5,10}의 CA mask는 C `LabelSet.label_ca_suspect`와 같음 |
| 13 | 경로 분리 (AST) | 추가 케이스 T13: `encoder`, `similarity`, `universe`, `sampling`이 `label_extension`, `signal`을 import하지 않음 |
| 14 | import 경계 (AST) | 추가 케이스 T14: D0 Reuse Matrix §4 금지 prefix 0 + `engine.identity` 전이 로드 스냅샷 |
| 15 | query 표본이 label과 무관 | 추가 케이스 T15: label 배열을 전부 NaN으로 바꿔도 날짜별 표본 동일 |
| 16 | 미래 재개 누수 | V23 |

추가 케이스 T1, T4, T6, T9, T12~T15는 25개 목록과 별도로 D2 테스트에 들어간다. D2 PASS 조건 P1은 V1~V25, P2는 이 대응표 전체다.

## 6. 참고: 재현 스크립트

기대값 계산은 아래 식만 썼다(저장소 코드 import 없음).

```python
zA = lambda P: (P - P.mean()) / P.std(ddof=0)
cB = lambda P: np.log(P[1:] / P[0])
rho = lambda a, b: zA(a) @ zA(b) / len(a)
dB = lambda a, b: np.linalg.norm(cB(a) - cB(b))
```
