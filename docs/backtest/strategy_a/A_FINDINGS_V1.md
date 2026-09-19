# Strategy A Findings V1 (9/18 Gap 스윕 재분석)

작성 2026-09-19, 집 PC(`DESKTOP-CR3T63A`). **새 백테스트 실행 0건.** 2026-09-18 회사 PC(`DESKTOP-C4EV6UM`)가
남긴 드라이브 아티팩트를 읽어 재집계한 기록이다. 코드, 설정, 규칙, 결과 파일은 아무것도 바꾸지 않았다.

- 근거 리포트: `backtest/reports/gap_tightening_v2_02f095bdd5e9a4aba949/` (experiment_version `gap-tightening-v2`)
- 근거 원자료: `backtest/results/<run_id>/trades.parquet` 4건 (드라이브 `1_US-B`)
- 작성 시점 저장소: `main` HEAD `a2b91ee`. 다른 세션의 미커밋 변경이 있었다(`strategy_d_analog/` D 구현,
  `docs/backtest/strategy_b/`, B 분봉 수집 프로세스 실행 중). 이 문서는 이 파일 외에 아무것도 바꾸지 않았다.
- 이 문서는 사전등록이 아니다. 8절의 실험 후보는 규칙 선언이 아니며, 실행 전에 별도 사전등록 문서가 필요하다.

## 판정

> **GATE 없음. gap_min 단일 변수 탐색은 여기서 중단한다.**

4개 런 전부에서 청산사유별 R 구조가 동일하다. gap 임계값은 표본 수만 줄이고 트레이드 하나의
손익 기하구조를 바꾸지 않는다. 3.0%가 플러스로 넘어간 것은 성능 개선이 아니라 표본 효과다(3절).

동시에, 지금 수치는 프로덕션 A의 성능이 아니다. A의 오버나이트 레그가 백테스트에서 한 번도
작동한 적이 없다(6절). 이 한계를 풀지 않으면 A 백테스트는 "전량 당일청산" 축소판만 측정한다.

## 1. 근거 실행 사실

| 항목 | 값 |
| --- | --- |
| batch | `batch-20260918-001`, `FAST_RESEARCH_MODE`, workers 1, host `DESKTOP-C4EV6UM` |
| 요청 구간 / 유효 진입 구간 | 2025-09-16..2026-09-15 / 2025-10-13..2026-09-14, **231세션** |
| universe | `research-universe-v2`, 선정 `ru2-pit-adv-sector-cap-dataeligible-v2`, 선언 29종목 / 평가가능 29종목 |
| universe checksum | `6a54d9a9…` / metadata `addc19f9…` |
| 초기 자본 | USD 10,000 (`ASSUMED_RESEARCH`, canonical 계약 없음) |
| 공통 파라미터 | gap_max 0.15, 프리마켓 거래량 V1 문턱 0.05, opening_range 15분, 진입 데드라인 10:30 ET |
| 트레이드당 리스크 | 약 USD 48.5~50.0 (자본의 0.5%) |
| engine_code_fingerprint | `f17f135d…` |
| authority | `RESEARCH_ONLY`. candidate/approval/rank = `ASSUMED`, overnight = `UNKNOWN_CLOSE`/`UNKNOWN`, trailing = `UNKNOWN_DEFAULT`/`UNKNOWN` |
| 검증 강도 | `replays_per_run=1`, baseline_recompute·determinism_replay·differential_suite·mutation_tests·full_pytest 전부 false |

`FAST_RESEARCH_MODE`이므로 결정성 재현이 확인되지 않은 결과다.

## 2. gap 스윕 결과 (사실, 리포트 3절 그대로)

| 지표 | gap 2.0% (기준) | 2.5% | 3.0% | 4.0% |
| --- | --: | --: | --: | --: |
| run_id | `csb1-e9f8c222…` | `exp1-0a9f647a…` | `exp1-b77c8391…` | `exp1-78cccb7c…` |
| 체결·청산 트레이드 | 37 | 33 | 24 | 17 |
| 승 / 패 | 15 / 22 | 13 / 20 | 13 / 11 | 9 / 8 |
| 승률 | 0.4054 | 0.3939 | 0.5417 | 0.5294 |
| 총수익률 | -2.719% | -2.550% | **+0.121%** | **+0.027%** |
| net PnL (USD) | -271.91 | -255.05 | +12.13 | +2.68 |
| 평균 R | -0.1482 | -0.1558 | +0.0108 | +0.0039 |
| Profit Factor | 0.5255 | 0.5003 | 1.0488 | 1.0149 |
| 최대 낙폭 | 2.938% | 2.889% | 0.993% | 0.923% |
| 거래 종목 수 | 18 | 18 | 16 | 13 |

## 3. 3.0%가 플러스인 이유 (사실 + 해석)

사실. 기준 대비 제거된 트레이드의 승패 구성이다(리포트 6절).

| 변형 | 제거 | 승 | 패 | 제거분 합계 R |
| --- | --: | --: | --: | --: |
| 2.5% | 4 | 2 | 2 | -0.342 |
| 3.0% | 13 | 2 | **11** | -5.742 |
| 4.0% | 20 | 6 | 14 | -5.549 |

세 변형 모두 신규 진입은 0건이다("Trades only in ...: none").

해석. 3.0%는 기준의 손실 22건 중 11건을 잘라내고 승리 15건 중 2건만 잃은 결과다. 규칙이 좋아진 것이
아니라, 같은 모집단에서 손실이 많이 걸린 구간을 표본에서 제외했다. 남은 표본은 24건이고 순손익은
+USD 12.13, 즉 자본의 0.12%다. 이 크기의 표본과 효과로는 어떤 판정도 할 수 없다.

## 4. 청산사유별 R 구조 (사실, 재집계)

`trades.parquet`을 직접 집계했다. 승 판정은 `realized_r > 0`. 각 런의 승 합계가 리포트의
`winning_trades`(15 / 13 / 13 / 9)와 일치해 교차검증된다.

gap 2.0% 기준:

| 청산사유 | 건수 | 승 | 평균 R | 합계 R | net PnL | MFE 중위 | MAE 중위 |
| --- | --: | --: | --: | --: | --: | --: | --: |
| INITIAL_STOP | 9 | 0 | -0.983 | -8.848 | -435.47 | 0.08R | -1.07R |
| OVERNIGHT_REJECTED | 23 | 10 | -0.013 | -0.297 | -15.02 | 0.50R | -0.52R |
| TRAILING_STOP | 5 | 5 | +0.733 | +3.663 | +178.59 | 1.06R | -0.12R |

4개 런 전부 같은 형태다. 평균 R은 INITIAL_STOP -0.98 ± 0.01, TRAILING_STOP +0.73~0.79,
OVERNIGHT_REJECTED -0.013~+0.078 범위에 머문다. 즉 **gap 값이 바꾸는 것은 세 버킷의 개수 비율뿐이다.**

해석. 익절 +0.73R, 손절 -0.98R이면 손익분기 승률은 약 57%다. 실제 트레일링 도달은 gap 2.0%에서
5/37(13.5%)이고, 나머지 62%(23/37)는 ~0R로 마감한다. 승률을 57% 위로 올리는 것보다 R 비대칭을
바꾸는 쪽이 구조적으로 더 큰 레버다.

## 5. 관측된 상방의 경계 (사실 - 구분해서 읽어야 함)

`max_favorable_excursion`과 `max_adverse_excursion`은 R이 아니라 **진입가 기준 가격 거리**로
저장돼 있다. R 환산은 `MFE / (entry_price - initial_stop)`이다. 이 변환을 빠뜨리면 음수 -45R 같은
무의미한 값이 나온다.

관측 완전성이 버킷마다 다르므로 나누어 서술한다.

- **종일 보유 23건**(OVERNIGHT_REJECTED, 15:51 ET 강제청산). 장중 전 구간이 관측됐다.
  MFE 최소 0.08R, 중위 0.50R, 평균 0.55R, **최대 0.98R. 1R에 도달한 건이 0건이다.**
- **트레일링 청산 5건**. MFE 1.018 / 1.050 / 1.057 / 1.099 / 1.184R. 청산 시점까지만 관측되므로
  더 갔을지는 이 데이터로 알 수 없다. 전체 최대는 `2026-05-26-MU`의 1.184R이며 4개 런 모두 동일하다.
- 37건 중 MFE 1.5R 초과는 0건이다.

해석. 관측된 상방이 1R 부근에서 끊긴다. 그 경계가 **시장이 거기까지만 갔기 때문인지, 트레일링
규칙이 거기서 잘라냈기 때문인지 이 데이터만으로는 구분할 수 없다.** 다만 잘리지 않고 종일 보유된
23건이 한 건도 1R에 닿지 못했다는 사실은 후자만으로는 설명되지 않는다. 8절의 첫 실험이 이 둘을
분리하도록 설계돼야 한다.

## 6. 오버나이트 레그는 한 번도 검증되지 않았다 (사실)

- 37건 전부 `sessions_held = 1`, `day2_reached = False`다. 4개 런 모두 같다.
- 62%(23/37)가 `OVERNIGHT_REJECTED`로 15:51 ET에 청산된다.
- 그런데 프로덕션 설정은 오버나이트를 허용한다. `backend/app/strategy/config.py:36` `overnight_enabled = True`,
  `:37` `overnight_max_positions = 1`, `:40` `overnight_max_stop_distance_r = 2`.
- 차단 지점은 `backend/app/strategy/engine.py:253`이다. 게이트가
  `overnight_suitability ∈ {MEDIUM, HIGH}`를 요구한다.
- 이 필드의 출처는 리서치(GPT) 산출물이다(`backend/app/research/domain.py:85`,
  `backend/app/services/entry_management_runtime.py:173`). 과거 날짜에는 존재하지 않으므로
  `backend/app/strategy/lifecycle.py:98`의 기본값 `UNKNOWN`으로 떨어지고 게이트가 닫힌다.
- 리포트 authority의 `overnight_mode: UNKNOWN_CLOSE`, `overnight_source: UNKNOWN`이 그 기록이다.
  `trailing_mode: UNKNOWN_DEFAULT`, `rank_source: ASSUMED`, `candidate_source: ASSUMED`도 같은 성질이다.

따라서 2절의 수치는 프로덕션 A가 아니라 **"갭 + 프리마켓 거래량 + 오프닝레인지 돌파, 전량 당일청산,
후보는 GPT 랭크 대신 research universe 29종목"** 규칙의 성능이다. A의 수익 설계인 오버나이트 연속
구간은 측정된 적이 없다. A 백테스트 결과를 프로덕션 A의 기대성능으로 인용하면 안 된다.

## 7. 진입 깔때기 (사실, gap 2.0% 기준)

| 단계 | 건수 | 비고 |
| --- | --: | --- |
| 승인 후보 / 프리마켓 평가 | 1,848 | 231세션 x 8 |
| 갭 미달 / 갭 과대 | 1,586 / 5 | |
| 프리마켓 거래량 미달 | 90 | |
| 프리마켓 통과 = 오프닝레인지 완료 | 167 | |
| **진입 데드라인(10:30 ET) 소멸** | **110** | 통과분의 65.9% |
| 신호 | 57 | |
| 진입가 상한 초과 | 20 | 신호의 35.1% |
| 체결 = 진입 = 청산 | 37 | |
| max3 / 3R / capacity 거부 | 0 / 0 / 0 | 리스크 한도는 한 번도 작동하지 않았다 |

해석. 프리마켓을 통과한 167건 중 진입까지 간 것은 37건(22%)이다. 손실 대부분은 데드라인과 가격 상한
두 게이트에서 발생하는 기회 손실이며, 이 두 값(10:30, 진입가 상한)은 지금까지 단일 변수로 검증된 적이 없다.

## 8. 반증 가능한 다음 실험 후보 (해석, 사전등록 아님)

우선순위 순이다. 실행 전 각각 사전등록 문서와 규칙 checksum 선언이 필요하다.

1. **상방 경계의 원인 분리.** 트레일링만 단일 변수로 느슨하게 하고(또는 목표 R 상향) 같은 231세션을
   재실행한다. MFE 분포가 1R 위로 늘어나면 경계는 규칙 탓, 그대로면 시장 탓이다. 5절이 이 실험 없이는
   결론에 도달할 수 없다.
2. **오버나이트 적합성의 PIT 프록시.** GPT 없이 과거 날짜에 계산 가능한 규칙으로
   `overnight_suitability`를 대체한다. C의 PIT 계약(`docs/backtest/strategy_c/C_PIT_CONTRACT_V1.md`)을
   따르고, 프록시가 본 데이터가 진입 시점 이전인지 감사해야 한다. 이게 되면 A의 오버나이트 레그가
   처음으로 측정된다. 안 되면 "A는 백테스트로 검증 불가한 구성요소를 포함한다"를 명시적으로 기록한다.
3. **진입 데드라인 민감도.** 10:30을 단일 변수로 옮긴다. 기존 `deadline_sensitivity_v1_9a95c655…`
   리포트가 있으므로 먼저 그 결과를 읽고 중복을 피한다.
4. **표본 확대.** 29종목 231세션은 어떤 결론에도 부족하다. 구간 또는 유니버스를 늘리기 전에는
   위 실험들의 결과를 성능 판정으로 읽지 않는다.

gap_min 추가 스윕은 이 목록에 없다. 4절이 이유다.

## 9. 재현 절차

A 엔진 코드 없이 결과만 재집계하는 절차다. 드라이브만 마운트돼 있으면 어느 PC에서도 된다.

```python
# PYTHONPATH 불필요. pyarrow + pandas만 사용
import pandas as pd, pathlib
W = pathlib.Path("/mnt/g/내 드라이브/1_US-B")   # PC마다 drive letter 다름
t = pd.read_parquet(W / "backtest/results/csb1-e9f8c2225f4f758c52a2/trades.parquet")
for c in ["entry_price", "initial_stop", "max_favorable_excursion", "realized_r", "net_pnl"]:
    t[c] = pd.to_numeric(t[c], errors="coerce").astype(float)
t["mfeR"] = t.max_favorable_excursion / (t.entry_price - t.initial_stop)
print(t.groupby("exit_reason")[["realized_r", "mfeR"]].agg(["size", "mean", "median"]))
```

`Decimal`이 문자열로 저장된 열이 있어 `holding_minutes` 등은 `to_numeric` 없이 집계하면 실패한다.

## 10. 한계

- 새 실행이 없다. 4~7절은 9/18 결과의 재집계이며, 그 결과 자체가 `FAST_RESEARCH_MODE`(단일 replay,
  결정성 미확인)에서 나왔다.
- 표본 24~37 트레이드, 종목 13~18개, 231세션이다. 성능 판정 근거로 쓸 수 없다.
- 5절의 트레일링 청산 5건은 관측이 청산 시점에서 절단돼 있다.
- 작성 시점 기준 집 PC에는 A 엔진 의존 모듈 약 30개가 없어(커밋 `33d329f` 본문 참조) 재실행이
  불가능했다. 회사 PC에서 `baseline/`, `portfolio/`, `basis/`, `replay/`, `research/` 미커밋 파일을
  push하면 재실행 가능하다.
- `33d329f`의 `--historical-snapshot` 바인딩은 실데이터 스모크와 parity 미실행 상태다. 8절 실험을
  스냅샷 바인딩으로 돌리려면 그 검증이 먼저다.
