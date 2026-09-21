# Strategy D-AGGRESSIVE D-AGG-1 결과 (Data/PIT + MFE/MAE Validation)

실행 2026-09-21 (회사 PC). 단계 **D-AGG-1**. 선언 `d_agg_rules_v1.json` canonical `1d2b453a…033a8`.
이 단계는 공격형 성과를 평가하지 않는다. 기하, PIT, 결정성만 검증한다.

```text
GATE-D-AGG-1 = PASS        run dagg1-e90649a1e15a
V2-A 공식 판정 SCREENING_FAIL 그대로, V2-A 코드·문서·결과 변경 0
성과 집계 0 (TL, DL, NTL, AG, UP10/DN10 비율, 상위 분위 성과, bootstrap, 블록, 집중도, 판정 전부 미계산)
```

---

## 1. 선언과의 차이 (한 곳)

D0 계약 §14의 7번은 D-AGG-1에서 유니버스 기저율(`mean p_up_U`, `mean p_dn_U`)을 "기록만" 하도록 적었다.
D-AGG-1 지시는 UP10/DN10 발생률 출력을 금지했으므로 **기저율 계산을 D-AGG-2로 미뤘다.** 선언보다 엄격한
방향이며 게이트 값은 바뀌지 않는다.

---

## 2. Parent

| 항목 | 값 |
| --- | --- |
| lineage | D1 `dv2a1-7cfc565cc548` / D2 `dv2a2-bdfb160b8e58` / D3 `dv2a3-850a238d9e7a` / D4 `dv2a4-a111d4b264b5` (이 머신) |
| A 출처 | V2-A D3 `signal_rows.parquet`, 재계산 없음 |
| binding recipe | `d-agg-a-binding-v1`: `query_date_idx` i64, `query_ticker_col` i64, `query_ticker` utf8, `sample_rank` i64, `analog_signal_A` f64, `signal_status` utf8. D3 행 순서, 문자열 null 금지, NaN은 quiet NaN 하나로 정규화. `b0`/`b0_strong` 제외 |
| A binding digest | `4f718bbd451e8bfcab0fe051118eee0e2e8f9762c4cec98d34518a998653bd5e` |
| query sample | D1 기록 `02b74f44…:0175d3d0…`와 재계산 일치 |
| 이 머신의 V2-A 전체 digest | `signal_rows` `0346ebaf…`, `evaluation_labels` `febd1894…` 일치 (추가 확인) |
| D3 PIT | violations 0, future_query_mutation PASS |

## 3. 데이터

freeze `9ebd6c29…aafe`, grid `830744f6…66c9`, source `adc4f191…2c02`, read digest `7e790a8d…7d55`.
PRE와 POST 모두 같았다. `historical_v2 --only b_minute` 수집기는 dry run 중에는 실행 중이었지만 minute만
쓰고, 정식 두 번 실행 시점에는 이미 종료되어 있었다.

## 4. 계약 (구현이 따른 것)

```text
entry     P0 = O(D+1)/F(D+1). 신호는 D 종가 이후, D 진입 없음
window    grid 세션 D+1..D+5 (양끝 포함). 값은 D와 D+6 이후를 읽지 않는다
MFE_5     max(H(D+1..D+5))/P0 - 1     MAE_5  min(L(D+1..D+5))/P0 - 1
UP10      MFE_5 >= 0.10 - 1e-12       DN10   MAE_5 <= -0.10 + 1e-12    (배타 아님, 선후 판정 없음)
split     x(t)/F(t), F = t 이전(포함) 실행 분할의 누적곱 (V2-A 라벨 기준)
validity  V2-A h=5 그대로: D+1 봉(open>0), D+5 세션 봉, label CA 의심 아님
```

**부동소수점 문턱.** `3.3/3.0 - 1 = 0.09999999999999987`이다. 센트 단위 가격에서는 정확히 ±10%인 창이
흔하다. 유니버스 유효 창 중 문턱 ±1e-12 안에 있는 행이 상방 1,664행, 하방 2,050행이었다. 그래서 1e-12
허용치를 D-AGG-1 구현 계약으로 고정했다(`config.THRESHOLD_EPS`). 실제 가격에서 가장 작은 구별 단위
(1,000달러 주식의 0.0001 tick)는 1e-7이라, 진짜 9.9999% 움직임이 올라갈 수 없다.

**동결 계약에서 나오는 두 가지 사실.** 둘 다 바꾸지 않고 기록한다.

1. **validity는 D+6..D+10을 읽는다.** V2-A label CA 규칙은 h <= 10에서 opens/closes D..D+10을 본다. 값(MFE,
   MAE, 진입가)은 D+1..D+5만 읽지만, 어느 행이 유효한지는 D+10까지의 CA 검사가 정한다. D+6 이후를 뒤섞은
   감사에서 유효 행의 97% 이상이 LABEL_CA_SUSPECT로 바뀌었다(계약 witness). D+11 이후 변조는 아무것도
   바꾸지 않았다.
2. **중간 세션 결측은 유효로 남는다.** D+2..D+4에 봉이 없으면 `VALID_GAP`이 되고 남은 봉으로 MFE/MAE를
   만든다. 대체하지 않으며 D+6으로 채우지도 않는다. query 1행, universe 7행이 여기에 해당한다.

**상장폐지와 halt.** 창 안에서는 둘을 구별할 수 없다. D+5 봉이 없으면 둘 다 `MISSING_HORIZON_BAR`(무효)다.
`missing_subclass`(이후 봉 존재 여부)는 창 밖을 읽는 기술 정보이며 validity에 들어가지 않는다. 매매 청산
규칙은 이 단계에서 만들지 않는다.

## 5. 기하 (상태 수만, 성과 없음)

| | rows | valid | invalid | VALID | VALID_GAP | NO_ENTRY_BAR | MISSING_HORIZON_BAR | LABEL_CA_SUSPECT | BOUNDARY |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| query | 66,300 | 66,162 | 138 | 66,161 | 1 | 22 | 94 | 22 | 0 |
| universe | 581,156 | 580,001 | 1,155 | 579,994 | 7 | 193 | 760 | 202 | 0 |

- query의 signal OK는 66,300/66,300이다. query validity는 V2-A D3 `query_label_valid`와 **비트 단위로 같고**,
  무효 138건은 V2-A D4가 센 값과 같다.
- universe(221세션)의 적격 합계 581,156은 V2-A D1과 같고, 세션별 min/max(2,551/2,756)도 같다. 모든 query는
  자기 날짜의 as-of 적격 종목이다.
- h=5 validity 행렬 digest `5e3e426c…`는 V2-A D1 `label_validity_primary`와 같다.
- MFE/MAE는 V2-A `forward_extremes`(D4 S-12가 쓴 구현)와 유효 창 전체에서 비트 단위로 같다.
- query missing_subclass: RESUMES_LATER 2, NO_LATER_BAR 92.

## 6. PIT (실데이터, 12개 평가일 260..480을 20세션 간격으로, 결과 전에 위치만으로 선정)

| 감사 | 결과 |
| --- | --- |
| slice 동치 (행 [D-5, D+20]만으로 전체 패널의 D 기하 재현) | 12/12 비트 동일 |
| AFTER_WINDOW (D+6 이후 뒤섞기 + D+6 분할 삽입) | 값·진입가·UP10/DN10 불변, validity 변화는 LABEL_CA_SUSPECT뿐 |
| AFTER_CA_WINDOW (D+11 이후 + D+11 분할) | 모든 필드 불변 |
| BEFORE_WINDOW (D-1 이전 전체, H(D)/L(D) 변조) | 모든 필드 불변 |
| HIGH_CONTROL H(D+3)x3 | MFE 상승 witness 날짜당 최소 4,991행, MAE·validity 불변 |
| HORIZON_CONTROL H(D+5)x3 | MFE 상승 witness 최소 5,032행 (D+5 포함 증명) |
| LOW_CONTROL L(D+4)x0.3 | MAE 하락 witness 최소 4,997행, MFE·validity 불변 |
| ENTRY_CONTROL O(D+1)x1.07 | MFE witness 최소 4,773행, MAE witness 최소 5,034행 |
| SIGNAL_FIREWALL (D 이후 전체 변조, D2 이웃과 변조 패널 라벨로 A 재계산) | 12/12 날짜에서 A 비트 동일, 기하 이동 296~300/300 |
| UNIVERSE_AS_OF (D 이후 변조 후 D 적격성 재평가) | 12/12 불변 |
| violations | **0** |

감사 코드의 결함을 하나 고쳤다. 처음에는 ENTRY_CONTROL이 시가만 올려 H < O 봉을 만들었고, excursion 모듈의
무결성 검사(유효 창에서 `MFE >= 0`, `MAE <= 0`)가 이를 잡아 HARD FAIL을 냈다. 원시 데이터는 2,576,207봉 중
O>H, O<L, C>H, C<L, H<L이 모두 0건임을 확인했다. 지금은 시가를 바꿀 때 고가를 함께 올린다.

## 7. 결정성 / 불변성 / 성능

| 항목 | 결과 |
| --- | --- |
| 프로세스 내 2회 계산 | geometry digest 9종 동일 |
| 별도 프로세스 2회 | run id `dagg1-e90649a1e15a` 동일, digest·상태 수·PIT 결과 전부 동일 |
| query_excursions | `ad89da74f0add9d2…` (파일 재독 후 재계산 일치) |
| universe_excursions | `0b48abd7067a90ee…` (재독 일치) |
| universe_geometry | `875d28dad4637257…` |
| read set PRE/POST | `7e790a8d…7d55` 동일 |
| V2-A parent 파일 D1~D3, A digest PRE/POST | 동일 |
| V2-A 문서·코드 digest(`d042ff10…`)·D4 run PRE/POST | 동일 |
| 소요 / peak RSS | 102~107초 / 1,516~1,552MB (한도 2,048MB) |
| artifact | query 2.2MB, universe 10.9MB, per-session 7.8KB, summary 19KB |

## 8. 코드와 테스트

새 패키지 `backend/app/backtest/strategy_d_agg/`: `config` `models` `identity` `parent` `excursions` `universe`
`pit` `d1`, 러너 `app/dev/run_strategy_d_agg_d1.py`. `strategy_d_v2/`에는 파일을 추가하지도 수정하지도
않았다(V2-A code digest 불변). 게이트와 tail metric 모듈은 만들지 않았다.

테스트 `tests/strategy_d_agg/test_d_agg_d1.py`는 **39개**다. 회귀는 `strategy_d_agg` + `strategy_d_v2` +
`strategy_d`에서 **350 passed**. 합성 fixture로 기대값을 고정한 항목: 규칙 checksum과 변조 거부, binding의 b0
무시·A 추적·NaN 정책·열 순서·null 거부, 선언 예제(MFE +10%, MAE -6%, UP10 T, DN10 F), 3.3/3.0 경계(eps 없으면
F), 9.999% 거부, DN10 경계, UP10+DN10 동시, D 제외, D+1·D+5 포함, D+6 제외, 진입가 변조, 창 이후 변조와 CA 창
규칙, 고가·저가 양성대조, 합성 패널 audit_one, 분할 6종(D-3, D+1, D+3 1:3, D+3 역분할 10:1, D+5, D+8), 무기록
가격 점프의 CA 무효, D+1 결측, 중간 결측(VALID_GAP, D+6 미사용), D+5 결측과 subclass 2종, 경계, 미래 무효
종목을 유니버스에 유지, 결정성, V2-A 격리(import와 git 작업트리), excursion 모듈의 신호 비의존, V2-A gate와
V1 resample 상수 미사용, 소스의 집계 패턴 부재, 리포트 키 방화벽.

## 9. Alpha firewall

```text
TL / DL / NTL / AG                  NO
UP10 rate / DN10 rate               NO   (행 단위 불리언만 artifact에 존재)
Top 10% / 5% / 2% outcome           NO   (선택 미적용)
Bootstrap / block / concentration   NO
PASS / BORDERLINE / FAIL            NO
```

리포트는 반환 전에 `assert_no_alpha`로 키를 검사한다. 성과를 시사하는 숫자는 상태 수, 감사 witness 수,
문턱 근접 행 수(발생 여부가 아니라 동률 근처 여부)뿐이다.

## 10. 다음

D-AGG-2 TAIL SCREENING. `dagg1-e90649a1e15a` COMPLETE 토큰의 query/universe digest와 A binding digest에
바인딩하고, D0 동결 게이트로 판정한다. 유니버스 기저율은 그 단계에서 처음 계산한다.
