# Strategy D-AGGRESSIVE D-AGG-2 결과 (Tail Screening)

실행 2026-09-21 (회사 PC). 단계 **D-AGG-2**. 선언 `d_agg_rules_v1.json` canonical `1d2b453a…033a8`,
parent D-AGG-1 `dagg1-e90649a1e15a`, V2-A lineage `dv2a1-7cfc565cc548` / `dv2a2-bdfb160b8e58` /
`dv2a3-850a238d9e7a` / `dv2a4-a111d4b264b5`.

```text
GATE-D-AGG-SCREEN = D_AGG_SCREEN_BORDERLINE     (T4 NTL 1.044 < 1.10, 나머지 H1~H7·T1~T3·T5·T6 충족)
run dagg2-588ada9e677f     phase hard check PASS
V2-A 공식 판정 SCREENING_FAIL 그대로
```

판정 한 줄: **A 상위 10%는 +10% 도달이 같은 날 유니버스보다 1.53배 잦지만, -10% 도달도 1.47배 잦다.** 상방
농축이 하방 농축을 넘는 폭(NTL 1.044)이 사전등록 PASS 수준 1.10에 못 미친다. 하드 조건 NTL >= 1.00, AG >= 1.00은
간신히 통과했다.

---

## 1. 사전 노출 (다시 명시)

D0 선언 시점에 보였던 것: V2-A D4 S-12의 A 5분위별 **평균** MFE_5(0.0683 / 0.0475 / 0.0454 / 0.0470 / 0.0591)와
평균 MAE_5(-0.0605 / -0.0435 / -0.0405 / -0.0421 / -0.0524). 둘 다 U자형이었다. 상위 10% 꼬리 확률,
TL/DL/NTL/AG, 기저율은 D0와 D-AGG-1에서 계산하지 않았고 이 단계에서 처음 계산했다.

D0 개념문 §3의 사전 기대는 "TL은 1을 넘지만 DL도 올라 NTL이 1 근처, H5/H6/T4에서 FAIL"이었다. 실제 결과는
TL > 1, DL도 크게 오르고, NTL은 1.04로 1 근처였다. 방향은 맞았다. 다만 NTL과 AG가 하드 하한 1.00을 근소하게
넘었고 T4 하나만 미달해 FAIL이 아니라 BORDERLINE이 되었다.

## 2. START GATE

| # | 확인 | 결과 |
| --- | --- | --- |
| 1 | rules checksum | MATCH, gate 문구 파싱값이 계약 §9와 일치 |
| 2~3 | D-AGG-1 COMPLETE / identity | PASS, identity digest `e90649a1…` 일치 |
| 4~5 | query / universe excursion digest | `ad89da74…` / `0b48abd7…` 재계산 일치, universe_geometry `875d28da…` 일치 |
| 6 | A binding digest | `4f718bbd…` 재계산 일치 |
| 7 | freeze / grid / read | 일치, read `7e790a8d…7d55` PRE = POST |
| 8 | V2-A 문서·코드·D4 run | D-AGG-1 POST 기록과 동일, 판정 SCREENING_FAIL |
| 9 | D-AGG-1 alpha firewall 기록 | 있음 (`outcome_aggregates_computed = false`) |
| 10 | 다른 strategy_d_agg run 쓰기 | 없음 (run 목록 PRE = POST, D-AGG-1 파일 sha PRE = POST) |

HEAD는 D-AGG-1 이후 `b05bbf6`으로 바뀌었다(다른 세션의 Strategy E-MAX 문서 커밋). D 영역 변경은 0이다.

## 3. Setup / Universe

| | 값 |
| --- | --- |
| 평가 세션 | 221 (thin 세션 탈락 0) |
| 선택 (validity 전) | 6,630 = 세션당 30 × 221, 정확 올림 `ceil(1/10 × 300)` |
| 선택 후 유효 / 무효 | 6,619 / 11 (NO_ENTRY 1, MISSING_HORIZON 5, CA_SUSPECT 5), 유효 비율 0.9983 |
| setup 고유 종목 | 2,422 |
| universe 적격 / 유효 / 무효 | 581,156 / 580,001 / 1,155 |

**H3 결측 편향**: setup 무효 비율 0.00166, universe 0.00199. setup 쪽이 더 낮아서 `<= 0.02`와 `<= 2 x universe`를
모두 충족한다. 결측을 outcome false로 취급하지 않는다(무효 행은 분자와 분모 모두에서 빠진다).

## 4. Primary

세션별 발생률의 세션 평균(221세션 등가중):

| | setup (A 상위 10%) | universe |
| --- | --- | --- |
| UP10 (`MFE_5 >= +10%`) | **0.2046** | 0.1334 |
| DN10 (`MAE_5 <= -10%`) | **0.1685** | 0.1146 |

```text
TL  = Σ p_up_S / Σ p_up_U                                  = 1.5346   95% CI [1.4250, 1.6834]
DL  = Σ p_dn_S / Σ p_dn_U                                  = 1.4702   95% CI [1.3515, 1.6361]
NTL = TL / DL                                              = 1.0438   95% CI [0.9593, 1.1285]
AG  = (Σ med MFE_S / Σ med(-MAE)_S) / (Σ med MFE_U / Σ med(-MAE)_U)
                                                           = 1.0401   95% CI [0.9281, 1.1330]
TEP = mean(p_up_S - p_up_U)                                = +0.0713  95% CI [+0.0570, +0.0918]
```

bootstrap: moving block, 비순환, 블록 20, 10,000 replicate, `default_rng([20260921, 5])`. draw 행렬 하나
(digest `948efefd…`)를 모든 구간에 공유하고, 각 replicate에서 분자와 분모 합을 같은 세션 위에서 계산했다.
pooled TL(행 풀링)은 1.5343으로 층화값과 거의 같다(판정 비사용).

**NTL과 AG의 CI는 1을 포함한다.** 게이트는 둘의 점추정만 읽도록 동결되어 있으므로 판정은 바뀌지 않는다.
그러나 "상방이 하방보다 더 농축된다"는 명제는 이 표본에서 통계적으로 확립되지 않았다.

## 5. 시간 블록 (56 / 55 / 55 / 55)

| 블록 | 세션 | TL | DL | NTL | AG |
| --- | --- | --- | --- | --- | --- |
| 1 | 260..315 | 1.371 | 1.295 | 1.058 | 1.051 |
| 2 | 316..370 | 1.695 | 1.655 | 1.024 | **0.921** |
| 3 | 371..425 | 1.549 | 1.356 | 1.142 | 1.206 |
| 4 | 426..480 | 1.517 | 1.533 | **0.990** | 1.005 |

TL > 1은 4/4 블록이다. NTL >= 1.10인 블록은 3번 하나뿐이고, 블록 4는 NTL < 1, 블록 2는 AG < 1이다.

## 6. 집중도

| 항목 | 값 | 기준 |
| --- | --- | --- |
| 단일 종목 최대 점유율 (setup UP10 중) | 0.0052 | H7 `<= 0.05` |
| 상위 5세션 제거 후 TL | 1.4962 | T5 `>= 1.10` |
| 상위 10종목 제거 후 TL | 1.5024 (min-20 재적용, 탈락 세션 0) | T6 `>= 1.10` |
| 상위 5세션의 양의 초과사건 점유율 | 0.093 | 기술 |
| 상위 10종목의 양의 초과사건 점유율 | 0.054 | 기술 |
| 가장 자주 선택된 종목의 행 수 | 12 / 6,619 | 기술 |

농축은 소수 날짜나 소수 종목의 효과가 아니다. 넓게 퍼져 있다.

## 7. 게이트

| 조건 | 관측 | 기준 | 판정 |
| --- | --- | --- | --- |
| H1 표본 | 221 / 6,619 / 2,422 / 1,355 | >=150 / >=5000 / >=500 / >=250 | PASS |
| H2 PIT | 0 (D-AGG-1 감사) | == 0 | PASS |
| H3 결측 | 0.00166 vs universe 0.00199 | <= 0.02, <= 2x | PASS |
| H4 방향 | TL 1.5346 | > 1.00 | PASS |
| H5 | NTL 1.0438 | >= 1.00 | PASS |
| H6 | AG 1.0401 | >= 1.00 | PASS |
| H7 | 0.0052 | <= 0.05 | PASS |
| T1 | TL 1.5346 | >= 1.25 (floor 1.10) | PASS |
| T2 | CI 하한 1.4250 | > 1.00 (floor > 0.95) | PASS |
| T3 | 4/4 | >= 3 (floor 2) | PASS |
| **T4** | **NTL 1.0438** | **>= 1.10** (floor >= 1.00) | **PASS 수준 미달, floor 충족** |
| T5 | 1.4962 | >= 1.10 (floor > 1.00) | PASS |
| T6 | 1.5024 | >= 1.10 (floor > 1.00) | PASS |

```text
PASS 수준 미달 = [T4] 정확히 1개, floor 미달 없음, INVERSE 아님 (TL CI 상한 1.683)
GATE-D-AGG-SCREEN = D_AGG_SCREEN_BORDERLINE
```

secondary 계산 뒤 저장된 gate 입력으로 판정을 다시 계산했고 결과가 같았다(`secondary_cannot_overturn`).

## 8. 변동성 함정 진단 (secondary, 판정 비사용)

| 선택 | TL | DL | NTL | AG |
| --- | --- | --- | --- | --- |
| **A 상위 10% (primary)** | 1.535 | 1.470 | 1.044 | 1.040 |
| X-6 rv_20 상위 10% (순수 변동성) | 2.702 | 3.255 | 0.830 | 0.872 |
| X-7 rv_20 10분위 구성을 맞춘 유니버스 대비 | **1.079** | 1.026 | 1.052 | - |
| X-5 B0 상위 10% | 0.407 | 0.491 | 0.829 | 0.986 |

- **TL 1.53의 대부분은 변동성 구성에서 나온다.** setup의 세션별 rv_20 분위 구성과 같게 재가중한 유니버스와
  비교하면 TL은 1.08, DL은 1.03으로 떨어진다. 초과 lift 0.53 중 약 0.08만 변동성 구성으로 설명되지 않는다.
- 그렇다고 A가 순수 변동성 선택기는 아니다. rv_20 상위 10%는 NTL 0.83, AG 0.87로 하방이 더 농축되는 반면,
  A 상위 10%는 NTL 1.04, AG 1.04다. 같은 변동성 구성 안에서는 상방 쪽이 약간 더 크다(X-7 NTL 1.05).
- 전형적 변동성 선택기의 모양(TL >> 1, DL >> 1, NTL ~ 1, AG ~ 1)에 가깝다. 게이트는 이 모양을 BORDERLINE으로 두었다.

**A 10분위 프로파일(X-10)은 U자형이다.** 상위 1분위 TL 1.53 / DL 1.47, 하위 10분위 TL 1.94 / DL 2.22, 가운데
분위는 TL 0.67~0.92다. A의 양 극단이 변동성 큰 종목이라는 사전 노출 패턴이 꼬리 확률에서도 그대로 나온다.
하위 분위는 보고만 하며 사용할 수 없다(D0 금지).

**UP10과 DN10 동시 발생**(X 기술): setup 90행(1.36%), universe 4,990행(0.86%).

## 9. Secondary 전수 (판정 비사용)

| 슬롯 | 결과 |
| --- | --- |
| X-1 ±5% | TL 1.285 / DL 1.273 / NTL 1.010 |
| X-1 ±15% | TL 1.602 / DL 1.557 / NTL 1.029 |
| X-2 Top 5% | TL 1.828 / DL 1.815 / NTL 1.007 / AG 1.027 |
| X-2 Top 2% | TL 2.204 / DL 2.149 / NTL 1.026 / AG 1.049 |
| X-3 TEP | +0.0713, CI [+0.0570, +0.0918] |
| X-4 comparator = query 표본 300 | TL 1.545 / DL 1.447 / NTL 1.067 / AG 1.038 |
| X-5 B0 상위 10% | TL 0.407 / DL 0.491 / NTL 0.829 / AG 0.986, setup 대 B0 상위 10% TL 비 3.77 |
| X-6 rv_20 상위 10% | TL 2.702 / DL 3.255 / NTL 0.830 / AG 0.872 |
| X-7 변동성 매칭 유니버스 | TL 1.079 / DL 1.026 / NTL 1.052 |
| X-8 setup excess_return_5 | 평균 +0.39%, 중앙값 +0.19%, 양수 51.4% (universe 평균 +0.12%, 중앙값 0.00%, 50.0%) |
| X-8 setup close_return_5 | 평균 +0.59%, 중앙값 +0.34%, 양수 52.2% (universe +0.33%, +0.15%, 51.3%) |
| X-9 setup MFE p50 / p80 / p90 | 0.0456 / 0.1017 / 0.1470 (universe 0.0351 / 0.0784 / 0.1166) |
| X-9 setup MAE p50 / p20 / p10 | -0.0413 / -0.0907 / -0.1298 (universe -0.0329 / -0.0734 / -0.1070) |
| X-10 A 10분위 | §8 (U자형) |
| X-11 집중도 기술 | §6 |
| X-12 블록별 | §5 |

선택을 좁힐수록(10% → 5% → 2%) TL과 DL이 같이 커지고 NTL은 1 근처에 머문다. 좁힐수록 변동성이 더 강하게
선택된다는 뜻이다. 비대칭은 좁혀도 늘지 않는다.

## 10. 결정성 / 성능

| 항목 | 결과 |
| --- | --- |
| 별도 프로세스 2회 | run id `dagg2-588ada9e677f` 동일. metrics·gate·secondary·setup_rows·session_metrics·draw digest 전부 동일 |
| setup_rows | `c4f19179…` |
| session_metrics | `48d450a7…` |
| gate | `e6cac9c2…` |
| 소요 / RSS | 119.5초(primary + bootstrap 0.23초, secondary 3.2초, 나머지는 패널 로드와 secondary 입력) / 1,041MB |

## 11. 코드와 테스트

추가 모듈: `setup.py`(A만 읽는 선택), `ratios.py`(세션 층화 비율, 공유 draw bootstrap, 블록, leave-out),
`gate.py`(파일을 열지 않는 순수 함수), `scoring.py`(파일 I/O 없는 채점), `d2.py`(검증·로드·기록), 러너
`app/dev/run_strategy_d_agg_d2.py`. `excursions.py`에는 X-8용 `close_and_excess`를 추가했다(미래 봉 단일 모듈
규칙 유지). V2-A 코드는 수정하지 않았다.

주의: `strategy_d_agg`에 파일이 늘어 그 `code_digest`가 바뀌었다. D-AGG-1을 지금 다시 돌리면 run id가
`dagg1-e90649a1e15a`와 달라진다. 데이터 digest는 그대로다(V2-A D4 §11.2와 같은 성질). D-AGG-2는 D-AGG-1을 다시
실행하지 않고 COMPLETE 토큰의 digest에 바인딩한다.

테스트 `test_d_agg_d2.py` 27개. D-AGG 전체 66개, D 회귀(strategy_d_agg + strategy_d_v2 + strategy_d) 377 passed. backend 전체(repo root 실행) **3,302 passed / 1 skipped** (1,010초). `strategy_b` scanner 테스트 2개는 다른 세션이 작업 중인 미커밋 `app/strategy_b/scanner.py` 때문에 수집 단계 ImportError가 나서 제외했다(V2-A D4 때와 같은 원인이며 D 변경과 무관).

D0가 문구로 정하지 않은 구현 규칙(실데이터 계산 전에 고정했고, 게이트 문턱은 아님):

- leave-out 재계산에도 20행 세션 규칙을 다시 적용했다(D0: "from every statistic of this study"). 모든 세션을
  유지하는 변형도 함께 보고했고, 이번에는 탈락 세션이 없어 두 값이 같다.
- 초과사건 동률은 위치 오름차순(세션 index, ticker column)으로 깬다.
- secondary 선택(Top 5%, Top 2%, B0, rv_20)은 primary 평가 세션 위에서 20행 규칙 없이 계산한다. 빈 세션은
  해당 슬롯에서만 빠진다(이번에는 0).

## 12. 다음 (동결 정책)

선언 `on_verdict.BORDERLINE`: *"authorizes a written note (cost, realized effect, the missed condition, what the
backtest would have to show) before any trading-rule work; no backtest without that note"*.

```text
다음 = BORDERLINE 서면 노트 (비용, 실현 효과, 미달 조건 T4, 백테스트가 보여야 할 것)
노트 없이 trading rule / backtester 작업 금지
Virtual Trading / Long Data 구매: 불가
재튜닝 금지 (문턱·분위·threshold·comparator 변경은 새 선언)
```

노트가 정면으로 다뤄야 할 사실: 변동성을 맞추면 TL이 1.08로 줄고, NTL과 AG의 CI가 1을 포함하며, 선택을
좁혀도 비대칭이 늘지 않는다. 백테스트가 의미를 가지려면 같은 변동성 구성의 무작위 선택보다 나은 청산 후
기대값을 보여야 한다. 그 기준 자체가 노트에서 사전에 정해져야 한다.
