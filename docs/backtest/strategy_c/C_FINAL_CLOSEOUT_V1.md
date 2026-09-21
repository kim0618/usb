# Strategy C 최종 종료 (C-ATTACK 이후)

작성 2026-09-21. 이 문서는 `C_FAMILY_CLOSEOUT_V1.md`(2026-09-20, commit `4e84ec12`)가 적은 최종 상태를 **대체(supersede)**한다.
그 문서는 방향성 계열(C-1~C-4)의 종료 기록으로 바이트 그대로 보존한다. 이 문서는 그 뒤에 새 연구 ID로 수행한
C-ATTACK-V0의 실패를 더해 Strategy C 전체를 닫는다.

```text
STRATEGY C = FULLY CLOSED

Stable / Directional      = FAIL   (C-1 / C-M,   GATE-C2)
Event Confirmation        = FAIL   (C-2 / C-E0,  GATE-CE0)
Event Quality             = FAIL   (C-3 / EQM-V0, GATE-EQM-V0)
Contextual Analog         = FAIL   (C-4 / C4-V0, C4_INTERNAL_SCREEN)
Attack Right-Tail Harvest = FAIL   (C-ATTACK-V0, GATE-C-ATTACK-V0)

LONG-SIDE RESEARCH = CLOSED
```

다섯 단계 모두 사전등록한 절차대로 검증했고, PIT 위반은 0이었으며, 표본도 충분했다. 실패는 배관 문제가 아니라
가설에 대한 답이다.

## 1. 흐름

```text
C-1  Momentum + Volume
     -> MFE lift (10D +15% Lift 1.358)
     -> 10D 종가 초과 -0.06%p, CI가 0을 포함: 방향성 알파 없음
     -> FAIL

C-2  + Event Presence (SEC 8-K / 10-Q / 10-K)
     -> EM 5D 초과 -0.256%p, M_ONLY +0.319%p: 가설과 부호가 반대
     -> FAIL

C-3  + Event Quality (매출 YoY)
     -> EQ2 +0.265%p가 M_ONLY +0.319%p를 넘지 못함
     -> FAIL

C-4  + Context / Analog
     -> F3 IC +0.022, F0 +0.060, F3 - F0 = -0.038 [-0.069, -0.007]
     -> 맥락을 더할수록 나빠짐
     -> FAIL

C-ATTACK  Right-tail harvesting, M_ONLY, TP +15% / SL -7% / 10D
     -> Net -0.260%, PF 0.940
     -> candidate - control = -0.122%p [-0.711, +0.397]
     -> 후보가 통제를 이기지 못함
     -> FAIL
```

## 2. C-ATTACK-V0 요약

상세는 `docs/backtest/strategy_c_attack/C_ATTACK_RESULTS_V1.md`에 있다.

| 항목 | 값 |
| --- | --- |
| 선언 | `c_attack_rules_v1.json` canonical sha256 `0e3b4aa52ea757b625dc2b0bb7292eae176e34ab9934a829c0c1ecdb9e252785` (2026-09-21 14:49:48 KST, 시뮬레이션 0건 상태에서 동결) |
| run | `catk0-55aacfcc4f4470a6a79e` |
| Primary | C-M0 + M_ONLY, D+1 시가 진입, TP +15% / SL -7%, 최대 10거래일, 같은 봉은 SL 우선, SL 갭 관통 시 실제 시가 청산 |
| 비용 | `C_ATTACK_COST_V1` 가격대별 왕복 25~100bp (stop형 청산 +20bp 포함) |

| Cohort | N | TP First | SL First | Neither | Ambiguous | Net Exp | PF | MDD |
| --- | --: | --: | --: | --: | --: | --: | --: | --: |
| M_ONLY 후보 | 3,542 | 21.46% | 51.44% | 26.34% | 0.76% | **-0.260%** | **0.940** | 9.42% |
| 매칭 통제 (셀 가중) | 3,359 | 16.81% | 43.35% | - | 0.11% | -0.043% | - | - |
| 매칭 통제 (풀링) | 108,021 | 12.47% | 37.42% | 50.06% | 0.05% | -0.044% | 0.987 | 6.97% |

- Gate 통계: candidate - control = **-0.122%p**, 95% CI **[-0.711, +0.397]**.
- 갭 SL 관통 173건(4.9%), 평균 gross -9.9%. 명목 SL보다 큰 손실이 결과에 반영됐다.
- 4개 시간 구간 중 통제 대비 우위는 2개다. 순이익 양수 구간은 마지막 1개뿐이고, 그 구간을 빼면 -0.59%다.
- 이익 상위 5종목을 빼면 -0.39%다.
- 실패 조건: H1 순기대값, H2 점추정, H2 CI 하한, PF > 1.10, 시간 안정성, 집중도.
- 통과 조건: 표본, PIT, MDD, 갭 처리, 보수적 모호성 처리.
- 같은 봉 모호성이 0.76%이고 낙관 순서에서도 같은 조건이 실패하므로, 분봉으로 순서를 정밀화해도 판정은 바뀌지 않는다.

**진단용 pair E(TP +20% / SL -10%)는 승격하지 않는다.** net +0.11%, PF 1.02가 나왔지만 Primary가 아니다. 통제 대비
+0.093%p [-0.668, +0.669]로 우위도 없고, 상위 10건이 이익의 130%를 차지한다.

## 3. M_ONLY 출처 재현

C-E0 원본 PC에 접근할 수 없어, 사용자 결정으로 SEC 저장소를 기존 fetcher로 다시 받아 C-E0 status를 결정적으로
재현했다.

| 항목 | 값 |
| --- | --- |
| HTTP | 2,384건 전부 200, 재시도 0 |
| CIK / 페이지 / 행 | 2,339 / 2,344 / 1,770,876 (원본과 같음) |
| status | M_ONLY 3,552 / EM 2,550 / EM_NEGATIVE_RISK 222 / EXCLUDED_MA_TARGET 58 / UNKNOWN_FORM 292 / UNKNOWN_MAPPING 6 / UNKNOWN_PIT 0 / UNKNOWN_COVERAGE 0 (공표값과 같음) |
| identity | (signal_date, ticker) 집합이 동결 C-M0 6,680행과 같고, C-E0 문서 수치 65개가 모두 일치 |
| canonical status digest | `b46ed8627b352e5e2ee23c32512cb4df1619914967bbcb85961ec76073af327d` |
| 판정 | `C_ATTACK_SOURCE_GATE = PASS` |

재현 run id는 `ce01-f7a521d7f50ffaffd0b0`이다. C-E0 run id에는 SEC 저장소 digest가 들어가므로 재수집 저장소로는
원본 id `ce01-234478e8f7ff27570e13`이 나올 수 없다.

## 4. SEC 타임스탬프 데이터 계약 경고

C-E0는 `acceptanceDateTime`을 UTC로 확정했다(원 감사 40/40). 재수집 저장소로 다시 감사하니:

```text
38 / 40 = UTC 해석과 일치
 2 / 40 = ET 벽시계 시각이 찍힌 예외
          0001104659-24-116299 (2024-11-12 06:55)
          0000950170-25-105472 (2025-08-08 07:35)
```

9/18 이후 공시가 표본 풀에 추가돼, 같은 seed로도 다른 표본이 뽑혔다. 판정이 갈리면 C-E0 코드는 ET로 떨어지고,
그 경우 status는 EM 2,524 / M_ONLY 3,579로 공표값과 어긋난다. C-ATTACK은 동결 C-E0 결론을 재현하기 위해 UTC를
유지했다. `strategy_c_e0` 코드는 바꾸지 않았고, 감사 판정만 래퍼에서 고정했다. 재현 결과는 공표값과 완전히 일치한다.

**이것은 C 결과를 바꾸는 사유가 아니다.** 향후 SEC 연구를 위한 경고로만 남긴다.

```text
Do not assume every acceptanceDateTime is uniformly UTC.
EDGAR를 쓰는 새 연구는 ET 스탬프 예외를 검출하는 절차를 선언에 넣어야 한다.
```

## 5. 최종 해석

> C 후보는 큰 상방 움직임이 생길 확률을 일부 높였다. 그러나 하방 Tail과 손절이 먼저 닿을 확률은 그보다 더 크게
> 높였다. 종가 방향성, Event confirmation, Event quality, contextual analog, 비대칭 공격형 exit 가운데 어느 방식에서도
> 반복 가능한 Long-side alpha를 확인하지 못했다.

C-ATTACK의 수치가 이를 직접 보여준다. TP 선행은 통제보다 +4.7%p, SL 선행은 +8.1%p다. 비용 전 gross도 후보(+0.23%)가
통제(+0.43%)보다 낮다.

## 6. 재개 금지

다음 방식으로 Strategy C를 다시 열지 않는다.

```text
- momentum threshold tuning
- RVOL tuning
- event-window tuning
- event-class cherry-picking
- quality threshold tuning
- K tuning
- distance tuning
- feature weighting
- F0-only reuse
- TP/SL grid tuning
- holding-period tuning
- diagnostic pair promotion (pair A/B/D/E 포함)
- M_ONLY 재선별, 기간·종목 제외, 분봉 정밀화로 판정 뒤집기
```

같은 가설을 결과를 본 뒤 재최적화해서 Strategy C를 살리지 않는다.

## 7. 재사용 허용 자산

Strategy C는 폐기하지만, 아래 인프라와 데이터는 다른 전략에서 재사용할 수 있다.

| 자산 | 위치 |
| --- | --- |
| SEC submissions store | `data/runtime/strategy_c/e0/raw/` (로컬, 재수집본) |
| XBRL store | `data/runtime/strategy_eqm/v0/raw` 등 (원본 PC) |
| PIT 유틸, 이벤트 taxonomy 인프라 | `backend/app/backtest/strategy_c_e0/`, `strategy_c_selection/` |
| 매칭 통제군, block bootstrap | `strategy_c_selection/evaluate.py`, `strategy_c_e0/stats.py` |
| TP/SL first-hit 시뮬레이터, 갭 반영 체결, 비용모델 | `backend/app/backtest/strategy_c_attack/` |
| 연구 산출물 게시 절차 | Drive `1_US-B/research_artifacts/` + `checksums.sha256` + `run_identity.json` |

## 8. 변동성 관찰의 처리

```text
C-M detects volatility expansion characteristics.
```

이 관찰은 보존한다. 그러나 **Strategy C Long을 재개하는 근거로 쓰지 않는다.** 변동성 연구를 하려면
`VOLATILITY_EXPANSION_V0`처럼 **새 Strategy ID, 새 가설, 새 사전등록**으로 완전히 독립적으로 시작해야 한다.
예를 들어 변동성 중립 구조나 옵션 전략이 여기에 해당한다. C-V2B가 이미 적었듯 D일 True Range를 통제하면 변동성
초과의 약 90%가 사라지므로, 그 연구는 이 통제를 사전등록 조건에 넣어야 한다.

## 9. 보존 자산

| 대상 | 위치 |
| --- | --- |
| C-ATTACK 선언·문서 | `docs/backtest/strategy_c_attack/` |
| C-ATTACK run | `data/runtime/strategy_c_attack/runs/catk0-55aacfcc4f4470a6a79e/` (로컬) |
| C-E0 재현본 | 로컬 `data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13/`, Drive `1_US-B/research_artifacts/strategy_c/ce01-234478e8f7ff27570e13/` |
| 이전 단계 | `C_FAMILY_CLOSEOUT_V1.md` §7 |

Drive 산출물 7개(candidate_status.parquet, summary.json, run_identity.json, manifest.json, rules.json, taxonomy.json,
checksums.sha256)는 `sha256sum -c` 전부 OK다. `data/runtime`과 Drive 파일은 Git에 넣지 않는다.

## 10. 이 종료의 의미

> Momentum, Event Presence, Event Quality, Contextual Analog, Aggressive Right-Tail Harvest까지 검증했으나 안정형과
> 공격형 어느 쪽에서도 반복 가능한 Long-side alpha를 확인하지 못했다.

Strategy C는 최종 폐기한다. 기존 데이터와 연구 인프라만 재사용 자산으로 보존한다.
