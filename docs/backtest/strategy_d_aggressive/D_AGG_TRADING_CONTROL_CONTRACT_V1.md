# Strategy D-AGGRESSIVE D-AGG-3 Trading / Control Contract (MARKET_STRUCTURE_ANALOG_TAIL_V1)

작성 2026-09-21. 단계 **D-AGG-3 사전등록**. 계약 본문은 `d_agg_trading_rules_v1.json`이다(canonical
`4b35d3446799…3e3871`). 그 checksum은 2026-09-21T07:26:51Z에 기록했고, **MDE 잡음 추정기는 그 뒤에
실행했다.** 이 문서는 JSON을 풀어 쓰고, 착수 게이트(MDE) 결과를 기록한다.

```text
D V2-A                    SCREENING_FAIL                  (변경 없음)
D-AGG Tail Screening       D_AGG_SCREEN_BORDERLINE         (변경 없음)
BORDERLINE Decision        PROCEED_LIMITED (1회용)
D-AGG-3 MDE Gate           MDE_PASS   MDE80 = 0.248% / trade <= 0.50%
BACKTESTER IMPLEMENTATION  AUTHORIZED (이 계약에 한해, 다음 별도 단계)
코드 0 (저장소) · 거래 성과 계산 0 · 평균/Delta/PF/승률/TP·SL 비율 열람 0
```

---

## 1. START GATE

| 확인 | 결과 |
| --- | --- |
| D-AGG rules checksum | `1d2b453a…033a8` MATCH |
| D-AGG-1 parent | `dagg1-e90649a1e15a`: COMPLETE, identity, query/universe/geometry digest 전부 일치 |
| D-AGG-2 result | `dagg2-588ada9e677f`: COMPLETE `D_AGG_SCREEN_BORDERLINE`, gate digest `e6cac9c2…` 재계산 일치 |
| BORDERLINE Decision 문서 | 있음 |
| freeze / grid / read digest | 일치. read `7e790a8d…7d55`, 추정기 실행 PRE = POST |
| V2-A 문서·코드·D4 run | D-AGG-1 POST 기록과 동일 |
| D0 규칙 파일·계약 문서 | D-AGG-2 identity에 기록된 sha와 동일 |
| A binding | `4f718bbd…` 일치 |

HEAD는 `cf5a6cb`다. D-AGG-2 이후 다른 세션이 Strategy E-MAX와 C 종결을 커밋했다. D 영역 변경은 0이다.

## 2. 순서 (지킨 그대로)

```text
1. 규칙·control·잡음 추정 절차·MDE 한도·백테스트 게이트를 JSON으로 동결 → checksum 기록 (07:26:51Z)
2. 봉인된 잡음 추정기 실행 (scratchpad, 저장소 밖) → 허용된 분산 항목만 출력
3. MDE 게이트 판정
4. 이 문서 작성 (JSON은 1 이후 변경 없음)
```

MDE는 동결된 estimand(이 규칙, 이 control) 위에서 계산해야 의미가 있다. 그래서 규칙을 먼저 동결했다. BORDERLINE
Decision §14가 잡음 입력으로 "bracket 적용 후 sd"를 명시했으므로 추정기는 거래 수익을 내부에서 만든다. 그러나
위치 통계(평균, 중앙값, Delta, 적중률, 양수 비율)는 하나도 출력하지 않는다. 추정기는 출력 dict가 whitelist를
벗어나면 중단한다. 스크립트 sha256은 `2a136987ad4c077c6ba10b827d3c7fcc288fa19e82ee3bd645895bef5f65d5fb`이다.

## 3. 사전 노출

D-AGG-2 X-8에서 setup과 universe 전체의 `close_return_5`, `excess_return_5` 평균과 중앙값이 이미 공개되었다(시간청산,
TP/SL 없음, 변동성 매칭 없음). X-7(변동성 매칭 꼬리 확률)과 X-9(MFE/MAE 분위수)도 공개되었다. **이 규칙과 이
control의 거래 수익은 계산된 적이 없다.** 이 사전 노출은 규칙 선택에 쓰지 않았다. 규칙은 BORDERLINE Decision
권고(D0 문턱 재사용)를 그대로 옮긴 것이다.

---

## 4. MDE 착수 게이트

### 4.1 잡음 입력 (허용 항목만)

| 항목 | 값 |
| --- | --- |
| 유지 세션 / 탈락 세션 | 221 / 0 |
| 유효 setup 거래 | 6,619 (세션당 최소 29, 중앙값 30) |
| 세션당 control 행 (중앙값) | 2,583 |
| 거래당 setup net 수익 sd (전체, 평균 제거) | 0.0724 |
| 거래당 setup net 수익 sd (세션 내, 세션 평균 제거) | 0.0674 |
| 세션 Delta(t) sd (평균 제거) | 0.01416 |
| Delta(t) lag-1 자기상관 | -0.077 |
| SE_iid = sd / sqrt(221) | 0.000952 |
| **SE_block** (moving block 20, 10,000회, seed 2026092100) | **0.000886** |
| SE 팽창 (block / iid) | 0.93 |

해석: control은 세션당 약 2,600행의 전수 가중이라 잡음이 거의 없다. Delta(t)의 분산은 대부분 세션당 30개 setup의
표집 잡음이다(0.067 / sqrt(30) = 0.0123으로 관측 0.0142와 비슷하다). 변동성 구성을 맞춘 차분이라 시장 공통
충격이 대부분 상쇄되고, 세션 간 자기상관이 거의 없어 block SE가 iid SE보다 약간 작다. BORDERLINE Decision이 사전에
가정한 설계효과 1.5~3보다 실제 의존성이 훨씬 약하다.

### 4.2 판정

```text
MDE80 = (1.959964 + 0.841621) x SE_block = 2.801585 x 0.000886 = 0.002483  (0.248% / trade)
한도   0.0050 (0.50% / trade), 0.50% 정확히는 통과 (Decision: "0.50%를 넘으면" 중단)
MDE GATE = MDE_PASS
```

BORDERLINE Decision §12의 사전 추정(0.3~0.7%)보다 작다. 이 백테스트는 거래당 약 0.25% 이상의 참 Delta를 80%
확률로 탐지할 수 있다.

**이 숫자가 알려 주지 않는 것.** MDE_PASS는 "검정할 가치가 있다"는 뜻이지 "우위가 있다"는 뜻이 아니다. 기대 효과
크기는 여전히 알 수 없다. 사전 노출된 시간청산 격차(universe 전체 대비 +0.27%, 변동성 매칭 전)가 MDE와 같은
크기라는 점만 알 수 있다.

---

## 5. Primary Trading Rule (`D_AGG_BRACKET_10_10_H5_V1`)

| 항목 | 동결값 |
| --- | --- |
| Signal | V2-A D3 `analog_signal_A`, 세션 내 상위 10% (D0 setup, validity 확인 전 선택) |
| 가격 기준 | `x(t)/F(t)`, F = t 이전(포함) 실행 분할의 누적곱 (D-AGG-1 기준) |
| Entry | `P0 = O(D+1)/F(D+1)`. D 종가 이후 첫 가격, D 진입 없음 |
| Entry gap | 진입 체결가 자체에는 TP/SL 판정 없음. 진입 세션의 고가·저가는 진입 후 판정 |
| TP | `1.10 x P0` (D0 UP10) |
| SL | `0.90 x P0` (D0 DN10) |
| 도달 판정 | `price/P0 - 1 >= +0.10 - 1e-12` (TP), `<= -0.10 + 1e-12` (SL). D-AGG-1 허용치 |
| D+1 | 장중: 고가 TP·저가 SL 동시 도달 → SL, 저가만 → SL, 고가만 → TP |
| D+2..D+5 | 순서대로. 시가가 SL 도달 → 그 시가로 청산, 시가가 TP 도달 → 그 시가로 청산, 아니면 장중 규칙 |
| Same-bar | **SL FIRST** (보수적) |
| Gap exit | 레벨을 넘어 열리면 레벨이 아니라 **시가로 체결**. 갭 하락은 SL보다 나쁘고 갭 상승은 TP보다 좋다 |
| Time exit | D+5까지 미도달 → `C(D+5)/F(D+5)` |
| Max hold | D+1..D+5 (5세션) |
| Gross | `exit_price / P0 - 1` |
| Cost | **COST_10BP**: `net = gross - 0.0010` (`STRATEGY_E_COST_RULES_V1` 왕복 총액 차감, E-D6 primary) |
| Missing entry | NO_TRADE (NO_ENTRY_BAR) |
| Missing middle bar | 그 세션은 거래 불가로 건너뛰고 거래를 계속한다. D+6으로 대체하지 않는다 (VALID_GAP) |
| Missing final bar | NO_TRADE (MISSING_HORIZON_BAR). 체결가를 만들지 않는다 |
| Delisting / halt | 창 안에서 구별 불가, missing final bar와 같이 처리 |
| CA-suspect | NO_TRADE (LABEL_CA_SUSPECT) |

**비용 선정 근거.** 저장소에 D 전용 비용 계약은 없다. 가장 가까운 검증된 주식 체결 비용 계약은
`docs/backtest/strategy_e_candidate/strategy_e_cost_rules_v1.json`이다. 이 계약은 호가 스프레드를 관측할 수 없음을
명시하고, 일반 마찰 스트레스를 왕복 총액으로 한 번 차감한다. E-D6가 그중 10bp를 primary로 동결했다. 같은 계약의
한계 조항("저가 종목에는 5~20bp가 과소일 수 있으나, 증거 기반 새 선언 없이 가격 배수 금지")도 그대로 승계한다.
D-AGG setup은 고변동성 쪽으로 기울어 있어 이 과소 추정이 실재할 수 있다. setup과 control에 같은 비용을 적용하므로
비용은 Delta에서 상쇄되고, 절대 수익 조건(P2)에만 영향을 준다.

**알려진 편향.** 창 안 상장폐지는 D+5 봉이 없어 NO_TRADE가 된다. 상폐 손실 거래가 빠지는 편향이지만 두 집단에 같은
규칙이 적용되며, NO_TRADE 수를 보고한다(P1이 setup 쪽 과다를 막는다).

## 6. Volatility-Matched Control (`D_AGG_RV20_DECILE_REWEIGHT_V1`)

```text
세션 D마다
 1. 모집단    as-of-D 적격 universe 전체 (D-AGG-1 universe 행), 미래 validity를 보기 전
 2. 분위 경계  numpy.quantile(모집단 rv_20, [0.1 .. 0.9]) 기본 linear, bucket = searchsorted(side='right') 0..9
              (setup 종목 포함한 전체로 경계를 만들고, validity는 보지 않는다)
 3. setup 제외 그날 setup 종목의 행을 control에서 뺀다 (경계를 만든 뒤)
 4. 가중치    w_j(t) = bucket j의 유효 setup 거래 수 / 유효 setup 거래 수
 5. control   E_C(t) = Σ_j w_j(t) x (bucket j의 유효 non-setup control 행 net 수익 평균)
 6. 빈 bucket w_j(t) > 0인데 bucket j에 유효 control 행이 없으면 그 세션을 제외하고 센다 (이웃 bucket 차용 없음)
```

matching 변수는 rv_20 하나다(V2-A 좌표 `std_ddof1(ln P(t)/P(t-1))`, t = D-19..D). 무작위 추출은 하지 않는다. seed
자유도와 추출 잡음이 없다. secondary 무작위 control도 만들지 않는다. D-AGG-2 X-7은 분위 경계를 유효 행으로 만들었는데,
이 계약은 그 흠을 고쳐 as-of 모집단으로 경계를 만든다. 잡음 추정 결과, 221세션 모두에서 빈 bucket 없이 control이
구성되었다.

## 7. Estimand와 통계

```text
E_S(t)      세션 t 유효 setup 거래 net 수익 평균
Delta(t)    E_S(t) - E_C(t)
PRIMARY     Mean Delta = 유지 세션 등가중 평균
ABSOLUTE    Mean setup net expectancy = E_S(t)의 세션 등가중 평균
bootstrap   moving block, 비순환, 블록 20, 10,000회, percentile 95% 양측
            draws = block_indices(T, horizon=5, block_length=20, replicates=10000, seed=2026092103)
            (MDE 추정 seed 2026092100과 분리)
시간 블록   block_partition(T, 4) → 56/55/55/55
기여도      세션: Delta(t) / 종목: c_i = Σ (net - E_C(t)) / n_S(t), 동률은 ticker column 오름차순
leave-out   Delta 상위 5세션 제거 후 재계산 / c_i 상위 10종목을 setup과 control 양쪽 모든 세션에서 제거 후 재계산
            (20거래 규칙 재적용)
```

## 8. Backtest Gate (`GATE-D-AGG-BACKTEST`)

| 조건 | 내용 |
| --- | --- |
| **P1** 유효성 | 체결 PIT 감사 violation 0; 유지 세션 >= 150, 탈락 세션 <= 5; 유효 setup 거래 >= 5,000; setup NO_TRADE 비율 <= 0.02 그리고 <= 2 x control NO_TRADE 비율 |
| **P2** 절대 수익 | Mean setup net expectancy (COST_10BP) > 0 |
| **P3** 우위 점추정 | Mean Delta > 0 |
| **P4** 우위 구간 | Mean Delta의 95% bootstrap CI 하한 > 0 |
| **P5** 시간 안정성 | 4블록 중 3개 이상에서 블록 Delta 평균 > 0 |
| **P6** leave-out | 상위 5세션 제거 후 Mean Delta > 0 그리고 상위 10종목 제거 후 Mean Delta > 0 |
| **P7** 집중도 | 양의 종목 기여 합계 중 단일 종목 최대 점유율 <= 0.05 (D0 H7 값 재사용) |

```text
D_AGG_BACKTEST_PASS                   P1~P7 전부
D_AGG_BACKTEST_FAIL                   P2~P7 중 하나라도 미달, 또는 P1이 PIT/표본 조건으로 미달
D_AGG_BACKTEST_INCONCLUSIVE_TECHNICAL 데이터 손상·실행 실패로 통계를 계산할 수 없을 때만
BORDERLINE                            없음 (Decision의 1회용 원칙). 통계적으로 애매하면 FAIL
secondary_cannot_overturn             TRUE
```

P2는 점추정만 요구한다. BORDERLINE Decision §7이 절대 수익을 "0보다 크다"로 적었고 CI를 요구하지 않았다. 사후 완화도
사후 강화도 하지 않는다. profit factor는 Decision에 문턱이 없으므로 게이트에 넣지 않고 보고만 한다.

## 9. Secondary (판정 비사용)

| 슬롯 | 내용 |
| --- | --- |
| S1 | 시간청산만 (같은 진입, TP/SL 없음, C(D+5)) |
| S2 | same-bar TP FIRST |
| S3 | 비용 2배 (COST_20BP) |
| 기술 | profit factor, 승률, TP/SL/시간청산/갭 청산 비율, 블록별 setup·control 기대값, 세션당 거래 수 |

## 10. 금지

```text
TP ≠ +10%, SL ≠ -10%, 보유 ≠ 5세션     Top 5% / Top 2%                  변동성·가격 필터
선언된 leave-out 외의 블록·세션·종목·테마 제외      primary control에 두 번째 매칭 변수
결과 후 비용 변경                       grid search                       MDE 게이트 전 위치 통계 열람 (지켰음)
MDE를 보고 한도 변경 (하지 않았음)
```

## 11. 권한과 다음 단계

```text
BACKTESTER IMPLEMENTATION   AUTHORIZED (d_agg_trading_rules_v1.json canonical 4b35d344…에 한해, 다음 별도 단계)
VIRTUAL TRADING             NOT AUTHORIZED (D_AGG_BACKTEST_PASS 전)
LONG DATA PURCHASE          NOT AUTHORIZED
```

다음 단계(D-AGG-4 BACKTEST)가 지켜야 할 것:

1. `strategy_d_agg` 패키지에서 구현하고 V2-A 코드는 수정하지 않는다. D-AGG-1 excursion artifact와 D-AGG-2 setup 선택에
   digest로 바인딩한다.
2. **잡음 재현 검사.** 백테스터가 산출한 거래 수익으로 §4.1의 잡음 항목(유지 세션 221, 유효 거래 6,619, sd_delta
   0.014155, SE_block 0.000886)을 먼저 재현해야 한다. 재현되지 않으면 추정기와 백테스터가 다른 규칙을 구현한
   것이므로 게이트를 읽지 않고 멈춘다.
3. 체결 PIT 감사(D+6 이후 변조 불변, D+1..D+5 고가·저가·시가 양성대조, 신호 방화벽)를 D-AGG-1 방식으로 수행한다.
4. 게이트 판정 후에만 secondary를 계산한다.

이 단계는 D-AGG의 마지막 연장이다. D-AGG-4가 FAIL이면 Strategy D 연구선을 종료한다(D V1 FAIL, D V2-A SCREENING_FAIL,
D-AGG FAIL). PASS여도 이 창(개발 데이터)을 다섯 번째로 읽은 결과이므로, 다음 단계는 Virtual Trading 설계 선언까지만
열린다.
