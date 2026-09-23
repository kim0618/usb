# Strategy B 최종 종료 (HOD_BREAKOUT)

작성 2026-09-23. 사용자 결정으로 Strategy B 연구 트랙 전체를 닫는다.
이 문서는 기록이고, 앞선 동결 계약·결과·probe 산출물은 한 글자도 고치지 않는다.

```text
STRATEGY B = NO_GO / CLOSED

B-E0  HOD_BREAKOUT (authoritative, 84세션)     = FAIL
B-E0  DIAG_ZERO_COST (비용 0 진단)             = 비용이 원인이 아님
B-E0  CF_SIGNAL_BAR (신호봉 체결 반사실)        = 진입 지연이 원인이 아님
B-E1-A H1 volume_acceleration >= 1.0 (동결)     = 독립 검증 미실행 (취소)
B-E1-A discovery diagnostic (in-sample)         = WEAK_IMPROVEMENT
FIRST_PULLBACK                                  = NOT_PURSUED / CLOSED_WITH_STRATEGY_B

RESEARCH = CLOSED, DEVELOPMENT = WILL_NOT_START
```

## 1. 최종 상태

| 항목 | 상태 |
| --- | --- |
| 전략 판정 | `NO_GO` |
| 연구 | `CLOSED` |
| 개발 | `NOT_STARTED / WILL_NOT_START` |
| 페이퍼 트레이딩 | `NOT_STARTED` |
| 실거래 | `NOT_STARTED` |
| 독립 검증 | `NOT_STARTED / CANCELLED` |
| 추가 historical 수집 | `NOT_STARTED` |

## 2. B-E0 최종 결과 (변경 없음)

계약 `b_e0_contract_v1.json` FROZEN `6e3d69af...`, run `be0-995dec075c1f4aea610a`, 2026-05-18 ~ 09-16, 84세션.

| 지표 | 값 |
| --- | --- |
| 수익률 | -25.72% (7,428.92 → 5,517.90) |
| 평균 net R | -0.354, 95% CI [-0.484, -0.220] |
| PF | 0.446 |
| 승률 | 31.5% |
| MDD | 25.7% |
| 거래 / 거래 세션 | 219 / 77 |
| 양수인 달 | 0 / 5 |
| lift | LIFT_FAIL |
| 판정 | **FAIL** |

## 3. 왜 닫는가

1. **B-E0가 명확히 FAIL이다.** 표본 게이트(30거래·20세션)를 넘긴 상태에서 CI 상한이 0 아래였다.
2. **비용이 원인이 아니다.** `DIAG_ZERO_COST`: 수익률 -10.05%, 평균 R -0.064, PF 0.753, CI [-0.203, +0.079]. 비용을 전부 없애도 기대값이 0 이하다.
3. **진입 지연이 원인이 아니다.** `CF_SIGNAL_BAR`: 평균 R -0.332로 BASE 대비 +0.02R. 같은 신호 215건에서 진입가는 중앙값 0.05% 싸졌을 뿐이다.
4. **신호 경로에 방향성이 거의 없다.** 382개 신호에서 30분 MFE 중앙 +1.84% vs MAE 중앙 -1.85%, 30분 수익률 중앙 -0.09%, ±1% 선도달이 183 대 185다.
5. **H1은 손실을 줄였지만 부호를 바꾸지 못했다.** 아래 4절.
6. **가장 유리한 조건에서도 PF < 1, 평균 R < 0이다.** 가설을 찾은 표본에서 실행한 결과가 그렇다.
7. **추가 데이터의 기대 정보가치가 비용에 못 미친다.** 아래 5절.

최종 원인 분류: **MIXED (주원인 SIGNAL_EDGE_WEAK + 부분집합 후보 + 일부 EXIT_CAPTURE)**.

## 4. B-E1-A (동결 유지, 독립 검증 취소)

계약 `b_e1a_contract_v1.json` FROZEN `b919fc54a65e61d50adf0a56d1c7c7430209339ed5f3ec7c5b0761a7e3505418`.
가설 H1: 최근 5분 실제 거래량 ÷ 직전 5분 ≥ 1.0, 각 candidate episode의 첫 E0 ENTRY_SIGNALLED 기회에서 1회만 판정.

discovery diagnostic (run `be0-4d0d4c3cf92c55c45a8a`, 같은 표본이라 판정 아님):

| 지표 | B-E0 | B-E1-A |
| --- | --- | --- |
| 수익률 | -25.72% | -3.03% |
| 평균 R | -0.354 | -0.104 (CI [-0.325, +0.112]) |
| PF | 0.446 | 0.815 |
| 승률 | 31.5% | 46.2% |
| MDD | 25.7% | 4.1% |
| 거래 | 219 | 91 |
| 양수인 달 | 0/5 | 1/5 |

H1 필터: 첫 신호 기회 388건 중 통과 138 / 탈락 250 (통과율 35.6%).

개선은 컸지만 수익률·평균 R·PF가 모두 기준 아래이므로 **WEAK_IMPROVEMENT**로 확정한다.

## 5. 독립 검증 취소 근거

예정 구간 2026-01-02 ~ 2026-05-15(93세션), 워밍업 2025-12-03 ~. 상태 `DATASET_NOT_COLLECTED`.

2026-09-23 실측(Drive ledger 전수 스캔, 3,884종목):

| 구간 | 보유 |
| --- | --- |
| 2026-04-20 ~ 05-15 | 3,372 / 3,882 |
| 2026-03-20 ~ 04-17 (1개월 screening의 워밍업) | 0 / 3,882 |
| 2025-12-03 ~ 2026-05-15 (동결 창 전체) | 0 / 3,882 |

Massive 요금제가 분당 5콜(12초 간격, 수집기 13초)이고 비용은 종목당 최소 1콜이라 **기간이 아니라 종목 수가 비용을 정한다**.
1개월만 받아도 약 3,900요청·약 14시간이고, 동결 창 전체(93세션)도 약 4,000~4,300요청·약 14~16시간이다. 짧게 끊는 이점이 없다.

결정: `INDEPENDENT_VALIDATION_CANCELLED_BY_RESEARCH_CLOSEOUT`.
동결 계약은 삭제·수정하지 않고 연구 이력으로 보존한다.

## 6. 문제가 아니었던 것

- 백테스터: A/B 체결 차분 PASS(최대 절대오차 4e-13), 두 번 실행 바이트 동일.
- 데이터 커버리지: 285,175 / 285,175 쌍, 품질 제외 2쌍.
- 메모리: 전체 세션 peak 12.7GB(추정) → 3.68GB로 줄여 10GB WSL에서 실행.
- Kiwoom 실시간 스캐너 속도: 정규장 순위 갱신 60초 이하, 아래 7절.
- NEXT_BAR_OPEN 지연: +0.02R.
- 수수료 단독: 비용 0에서도 음수.

닫는 이유는 `STRATEGY EDGE INSUFFICIENT`이지 `KIWOOM IMPLEMENTATION BLOCKER`가 아니다.

## 7. Kiwoom 실시간 가능성 (기록 보존)

2026-09-22 22:35~22:54 KST(09:35~09:54 ET) 서버 READ-ONLY probe, snapshot 20/20, 오류 0, 주문 0콜, WebSocket 미사용.

- 정규장 순위 갱신: 19개 구간 전부 변화, `observed ranking refresh <= 60 s`
- 합집합 평균 크기: top50 79.8 / top100 155.2 / top200 312.5
- 장초반 신규 편입: top200 64종목, top50 8종목. 예: `CWD` 09:39 ET 172위 등장 → 27위, 거래량 52.9만 → 603만, +34.9%
- 값 의미 확정: `acc_trde_qty`는 04:00부터의 당일 누적, `trde_prica`는 천 달러 단위 같은 누적
- 판정: `FAST_ENOUGH_FOR_STAGE1`, 사전검증 전체는 `PASS_WITH_LIMITATIONS`

남은 실시간 blocker(구독 한도 단위, Kiwoom/Massive 거래량 0.70배, VWAP parity, RVOL 기준선, 주문 경로, 재연결 복구, 전략 공용 WS gateway)는 해소하지 않은 채 남는다. Strategy B를 위해 더 풀지 않는다.

## 8. 남기는 자산

삭제하지 않는다. 다른 전략·셋업 연구에서 재사용할 수 있다.

- 순수 레이어(`app/strategy_b`): PIT 안전 feature, sparse·split 처리, RVOL, volume acceleration, HOD/LOD, VWAP, SessionTape, Candidate FSM
- 과거 어댑터(`app/backtest/strategy_b_e0`): SessionSource, DatasetFacts, mirror·session cache, preflight, universe artifact, gate·lift·metrics, 실행 차분, freeze·code identity, 메모리 최적화
- 러너·CLI(`app/dev/run_strategy_b_e0.py`)와 테스트(`tests/strategy_b/`)
- Kiwoom FE/FT 실시간 probe, broad scanner probe와 순위 turnover 분석
- 실시간 구조 연구 문서

자동으로 Strategy B를 재개하지 않는다.

## 9. FIRST_PULLBACK

authoritative 검증까지 가지 않았다. 상태 `NOT_PURSUED / CLOSED_WITH_STRATEGY_B`.
다시 하려면 B-HOD의 E2/E3로 잇지 말고, 새 strategy/research track과 새 사전등록(D0)부터 시작한다.

## 10. 보호 대상 (수정 금지)

- `b_e0_contract_v1.json` / `.sha256` FROZEN `6e3d69af...`
- `b_e1a_contract_v1.json` / `.sha256` FROZEN `b919fc54...`
- authoritative run `be0-995dec075c1f4aea610a`, 진단 run `be0-766076888a43fc695ee1`·`be0-aa1fc6f27988b957ec62`·`be0-4d0d4c3cf92c55c45a8a`
- Kiwoom probe 원자료, 체크섬 이력

이 종료 기록은 위 파일을 고치지 않고 별도 문서·레코드로 남긴다.

## 11. 산출물 위치

- 문서: 이 파일, `B_E0_RESULTS_V1.md`, `B_E1A_PREREGISTRATION_V1.md`, `B_E1_RESEARCH_PLAN_DRAFT.md`, `B_KIWOOM_BROAD_SCANNER_PREVALIDATION.md`
- 종료 레코드: `docs/backtest/strategy_b/b_strategy_closeout_v1.json`
- run 산출물·freeze ledger·probe 원자료는 `data/runtime/` 아래에 있고 git 추적 대상이 아니다(로컬·Drive 보관).

## 12. 최종 결정

```text
NO FURTHER B-HOD RESEARCH
NO B-E1-A INDEPENDENT COLLECTION
NO STRATEGY B DEVELOPMENT
NO PAPER TRADING
NO LIVE TRADING
```
