# Strategy D-AGGRESSIVE D-AGG-4 결과 (Backtest Evaluation, 최종 개발 데이터 게이트)

실행 2026-09-21 (회사 PC). 계약 `d_agg_trading_rules_v1.json` canonical `4b35d3446799…3e3871`(`.sha256` 전체 값을
읽어 검증), 규칙 `D_AGG_BRACKET_10_10_H5_V1`, control `D_AGG_RV20_DECILE_REWEIGHT_V1`.

```text
GATE-D-AGG-BACKTEST = D_AGG_BACKTEST_FAIL        (P4 미달: Delta 95% CI 하한 -0.077% <= 0)
run dagg4-ceaeaf3b9e30
→ Strategy D research CLOSED  (D V1 FAIL, D V2-A SCREENING_FAIL, D-AGG FAIL)
```

판정 한 줄: **D-AGG setup은 비용 반영 후 거래당 +0.30%를 벌었지만, 같은 날 같은 변동성 구성의 control도 +0.19%를 벌었다.
차이 +0.11%는 구간이 0을 포함하고, 사전등록 MDE 0.25%의 절반에도 못 미친다.**

---

## 1. START GATE

| # | 확인 | 결과 |
| --- | --- | --- |
| 1 | trading rules checksum | `.sha256`의 64자 값과 재계산 일치 |
| 2 | D0 rules checksum | `1d2b453a…033a8` 일치 |
| 3 | D-AGG-1 | `dagg1-e90649a1e15a` COMPLETE·identity·artifact digest 일치 |
| 4 | D-AGG-2 | `dagg2-588ada9e677f` BORDERLINE, gate digest 재계산 일치, parent = D-AGG-1 |
| 5 | Borderline Decision | `DECISION: PROCEED_LIMITED` |
| 6 | D3 MDE | `MDE GATE = MDE_PASS` |
| 7 | freeze / grid / read | 일치, read `7e790a8d…7d55` PRE = POST |
| 8 | A binding | `4f718bbd…` 일치 |
| 9 | V2-A | 문서·코드·D4 run이 D-AGG-1 기록과 동일 |
| 10 | D-AGG 문서 11개 | 실행 PRE = POST sha 동일, D0 파일은 D-AGG-2 identity 기록과 동일 |
| 11 | 다른 strategy_d_agg 쓰기 | 없음 (run 목록, D-AGG-1/2 파일 sha PRE = POST) |

HEAD `cf5a6cb`(main). 다른 세션의 E-MAX·C 커밋만 있고 D 영역 변경은 0이다.

## 2. 잡음 재현 (게이트 평가 전)

| 항목 | D3 기록 (6자리 표기) | D4 백테스터 | 절대 오차 | 허용 5e-7 |
| --- | --- | --- | --- | --- |
| 유지 세션 | 221 | 221 | 0 | 일치 |
| 유효 setup 거래 | 6,619 | 6,619 | 0 | 일치 |
| sd_delta | 0.014155 | 0.014155498584065552 | 4.99e-7 | 통과 |
| SE_block | 0.000886 | 0.0008862175726317653 | 2.18e-7 | 통과 |

허용오차는 D3 계약에 없어서, D4 실행 전에 "표기 정밀도의 반 단위(5e-7), 세션 수와 거래 수는 정확히"로 선언했다
(`evaluation4.NOISE_TOLERANCE`). sd_delta 오차가 허용치에 거의 붙어 있어 D3 봉인 추정기를 전체 정밀도로 다시 출력했다
(분산 항목만). 결과는 `0.014155498584065552`와 `0.0008862175726317653`으로 **D4와 비트 단위로 같다.** 오차는 D3의
6자리 반올림 표기에서 온 것이고, 추정기와 백테스터는 같은 규칙을 구현했다.

## 3. 매매 규칙 (동결값 그대로)

진입 `O(D+1)/F(D+1)`(진입가 자체는 TP/SL 판정 없음), TP `+10%`, SL `-10%`, 도달 판정 허용치 1e-12, D+2..D+5 시가가 레벨을
넘으면 시가로 체결, same-bar는 SL 먼저, 봉 없는 중간 세션은 건너뜀, D+5 종가 시간청산, `COST_10BP` 왕복 총액 차감
(`net = gross - 0.0010`), NO_TRADE는 D-AGG-1 무효 행.

## 4. 표본

| | 값 |
| --- | --- |
| 세션 | 221 (탈락 0, 빈 bucket 0) |
| setup 선택 / 유효 거래 / NO_TRADE | 6,630 / 6,619 / 11 (0.166%) |
| control 행 / 유효 | 574,526 / 573,382 (NO_TRADE 0.199%) |
| 세션당 control (중앙값) | 2,583 |

## 5. Primary 결과

| 통계 | 값 |
| --- | --- |
| Mean setup net (세션 등가중) | **+0.3037% / trade** |
| Mean control net | +0.1910% / trade |
| **Mean Delta** | **+0.1126% / trade** |
| **Delta 95% CI** | **[-0.0774%, +0.2663%]** |
| setup gross (풀링) | +0.4055% |
| setup net 평균 / 중앙값 (풀링) | +0.3055% / +0.3109% |

bootstrap: moving block 20, 10,000회, seed 2026092103, draw digest `df5733c0…`.

## 6. 게이트

| 조건 | 관측 | 기준 | 판정 |
| --- | --- | --- | --- |
| P1 유효성 | PIT 0, 세션 221 / 탈락 0, 거래 6,619, NO_TRADE 0.166% vs control 0.199% | 각 기준 | PASS |
| P2 절대 수익 | +0.3037% | > 0 | PASS |
| P3 우위 점추정 | +0.1126% | > 0 | PASS |
| **P4 우위 구간** | **CI 하한 -0.0774%** | **> 0** | **FAIL** |
| P5 시간 블록 | 3/4 (B2 음수) | >= 3 | PASS |
| P6 leave-out | 상위 5세션 제거 +0.0464%, 상위 10종목 제거 +0.0342% | 둘 다 > 0 | PASS |
| P7 집중도 | 0.85% | <= 5% | PASS |

```text
GATE-D-AGG-BACKTEST = D_AGG_BACKTEST_FAIL   (failed ['P4'])
BORDERLINE 없음 (1회용 결정), INCONCLUSIVE 사유 없음 (기술 문제 없음)
secondary 계산 뒤 판정을 다시 계산했고 결과가 같았다
```

## 7. 시간 블록

| 블록 | 세션 | setup net | control net | Delta |
| --- | --- | --- | --- | --- |
| B1 | 260..315 | +0.128% | +0.067% | +0.061% |
| B2 | 316..370 | -0.371% | -0.291% | **-0.080%** |
| B3 | 371..425 | +0.993% | +0.778% | +0.215% |
| B4 | 426..480 | +0.468% | +0.212% | +0.256% |

setup과 control이 블록마다 같이 움직인다. 수익의 대부분은 두 집단이 공유하는 시장·변동성 요인이다.

## 8. Leave-out과 집중도

상위 5세션(276, 385, 441, 457, 477)을 빼면 Delta는 +0.046%, 상위 10종목을 빼면 +0.034%다. 둘 다 양수라 P6는 통과했다.
그러나 원래 +0.113%의 60~70%가 세션 5개나 종목 10개에서 나온다. 단일 종목 최대 점유율은 양의 기여 중 0.85%(양의 기여
종목 1,265개)다.

## 9. Secondary (판정 비사용)

| 슬롯 | Mean setup net | Mean control net | Mean Delta | Delta 95% CI |
| --- | --- | --- | --- | --- |
| **Primary** | +0.304% | +0.191% | +0.113% | [-0.077%, +0.266%] |
| S1 시간청산만 | +0.514% | +0.314% | +0.200% | [-0.017%, +0.419%] |
| S2 same-bar TP 먼저 | +0.316% | +0.207% | +0.109% | [-0.080%, +0.260%] |
| S3 비용 2배 | +0.204% | +0.091% | +0.113% | [-0.077%, +0.266%] |

- S3의 Delta가 primary와 같다. 거래당 고정비용이 Delta에서 상쇄된다는 계약 문장이 그대로 확인된다.
- S2의 차이는 작다. same-bar 동시 도달이 setup 거래의 0.06%뿐이다.
- S1(bracket 없음)의 Delta가 더 크지만 구간은 여전히 0을 포함한다. 결과를 보고 규칙을 바꾸는 것은 금지이며, S1도 P4를 넘지 못한다.

## 10. 기술 통계 (판정 비사용)

| 항목 | setup | control |
| --- | --- | --- |
| 거래 수 | 6,619 | 573,382 |
| 승률 (net > 0) | 52.0% | - |
| profit factor | 1.111 | - |
| TP / SL | 17.4% / 14.2% | 11.1% / 9.7% |
| 갭 TP / 갭 SL | 2.5% / 1.9% | 1.7% / 1.2% |
| same-bar SL | 0.06% | 0.05% |
| 시간청산 | 64.0% | 76.2% |

연구용 누적 곡선(세션 setup 평균 net의 단순 누적합)의 최종값은 0.671, 최대 낙폭은 0.591이다. 겹치는 5세션 코호트를
그냥 더한 값이라 **실제 포트폴리오 수익률이나 낙폭이 아니다.** 포지션 사이징 전략이 아니므로 연구 참고용으로만 기록한다.

setup은 레벨 청산(TP+SL+갭 36%)이 control(24%)보다 잦다. D-AGG-2에서 본 "큰 움직임 농축"이 체결 단계에서도 그대로
나타난다. 그러나 순기대값 차이로는 이어지지 않았다.

## 11. PIT

12개 날짜(260..480, 20세션 간격)에서 violations **0**.

| 감사 | 결과 |
| --- | --- |
| 청산 이후 봉 변조 (행마다 자기 청산 세션 이후) | 불변 |
| D+5 이후 전체 변조 | 불변 |
| 신호일 D 봉 변조 | 불변 |
| TP 양성대조 (TIME 행 H(D+3)=1.2 P0) | 날짜당 1,756~2,248행 모두 D+3 TP로 전환 |
| SL 양성대조 (L(D+3)=0.8 P0) | 모두 D+3 SL로 전환 |
| 진입가 양성대조 (O(D+1) x 1.05) | 2,053~2,418행 수익 이동 |
| rv_20 as-of | 독립 재계산값이 V2-A 좌표와 일치(상대오차 1e-9 이내), D 이후 변조로 불변, bucket 불변, 좌표 대비 bucket 불일치 0 |

control bucket은 validity를 보기 전의 as-of 모집단으로 만들었다(`control.buckets`는 validity 인자가 없다. 테스트로 고정).
신호는 D3 A(q)를 digest로 바인딩했고, 선택은 A만의 함수다(D-AGG-2 테스트).

## 12. 결정성 / 성능

| 항목 | 결과 |
| --- | --- |
| 별도 프로세스 2회 | run id `dagg4-ceaeaf3b9e30` 동일, 전 digest·metrics·gate·secondary·blocks·leave-out·PIT·잡음 재현 동일 |
| trade table | `d0796efc…` |
| session delta | `609cf83d…` |
| 전체 행 거래 | `b055565b…` |
| gate | `9b58cd15…` |
| 소요 / RSS | 131.5초 / 1,188MB (한도 2,048MB) |

## 13. 코드와 테스트

새 모듈: `backtest.py`(bracket 체결, 벡터화), `control.py`(rv_20 분위·가중·Delta·기여도), `evaluation4.py`(잡음 재현, 순수 함수
게이트), `pit4.py`(체결 PIT 감사), `d4.py`(검증·로드·기록), 러너 `app/dev/run_strategy_d_agg_d4.py`. `identity.RUN_PREFIX`에
`D4: dagg4`를 추가했다. V2-A와 A/B/C/E 코드 변경은 0이고, production broker와 portfolio는 쓰지 않았다.

실행 중 결함 1건: 첫 실행이 PIT 단계에서 `OverflowError`(numpy 2의 int8 승격 규칙: `260 + int8`)로 멈췄다. 잡음 재현 직후,
어떤 위치 통계도 만들기 전이었고 artifact도 쓰지 않았다. 캐스팅을 고친 뒤 날짜 index 260으로 회귀 테스트를 추가했다.
합성 테스트가 index 70만 써서 놓친 결함이다.

테스트 `test_d_agg_d4.py` 46개. D-AGG 전체 112개, D 회귀(strategy_d_agg + strategy_d_v2 + strategy_d) 423 passed. backend 전체(repo root 실행) 3,425 passed / 1 skipped / 1 failed. 실패 1건은 다른 세션의 미추적 파일 `test_massive_year_feasibility.py`의 시간 의존 rate limiter 테스트다(부하 중 sleep 합계가 1.2초 어긋남). 단독 실행하면 50 passed이고 D 변경과 무관하다. `strategy_b` scanner 테스트 2개는 기존과 같은 이유(다른 세션의 미커밋 `scanner.py`)로 제외했다.

## 14. 해석과 종결

```text
확인된 것      A(q) 상위 10%는 큰 움직임을 농축한다 (D-AGG-2 TL 1.53).
              실제 bracket 체결에서도 레벨 청산이 더 잦고, 비용 후 절대 수익이 양수다 (+0.30%/trade).
확인되지 않은 것  그 수익이 같은 날 같은 변동성 구성의 종목을 사는 것보다 낫다는 것.
              Delta +0.11%는 구간이 0을 포함하고 MDE(0.25%)보다 작다.
```

D-AGG-3 계약과 요청의 동결 정책대로:

```text
D_AGG_BACKTEST_FAIL → STRATEGY D RESEARCH CLOSED
  D V1 (Raw Path Analog)             CLOSED / FAIL
  D V2-A (Market Structure Analog)   SCREENING_FAIL
  D-AGGRESSIVE (Tail Capture)        BACKTEST_FAIL
현재 2년 데이터에서 D 추가 연구 금지 (V2-B / V3 파라미터 탐색 포함)
Virtual Trading: NOT AUTHORIZED   Long Data Purchase: NOT AUTHORIZED
```

닫는 범위: 이 신호(V2-A A(q)), 이 setup, 이 매매 규칙, 이 control, 이 2년 창에서 "변동성 매칭 대비 순기대값 우위"
가설. 이 창은 다섯 번 읽혔으므로 같은 창에서의 재선언은 증거력을 가질 수 없다.
