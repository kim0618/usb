# Strategy D Final Closeout (V1 / V2-A / D-AGGRESSIVE)

작성 2026-09-21. 이 문서는 새 판정을 내리지 않는다. 이미 동결된 판정을 한곳에 모으고, 종결의 이유와 범위, 재개 조건을 고정한다.
D0~D4 문서와 run artifact는 수정하지 않는다.

```text
STRATEGY D FINAL STATUS

D V1           (HISTORICAL_ANALOG_V1, raw path analog)           FAIL / CLOSED
D V2-A         (MARKET_STRUCTURE_ANALOG_V2)                      SCREENING_FAIL
D-AGGRESSIVE   (MARKET_STRUCTURE_ANALOG_TAIL_V1)                 BACKTEST_FAIL

FINAL: STRATEGY D RESEARCH CLOSED
```

---

## 1. D-AGGRESSIVE lineage

| 단계 | 판정 | 근거 |
| --- | --- | --- |
| D0 사전등록 | PASS | `D_AGG_SCREENING_CONTRACT_V1.md`, `d_agg_rules_v1.json` canonical `1d2b453aed74…033a8` |
| D1 Data/PIT + MFE/MAE | PASS | run `dagg1-e90649a1e15a`, `D_AGG_D1_RESULTS_V1.md` |
| D2 Tail Screening | BORDERLINE | run `dagg2-588ada9e677f`, `D_AGG_D2_RESULTS_V1.md` |
| Borderline Decision | PROCEED_LIMITED (VOI MEDIUM, 1회용) | `D_AGG_BORDERLINE_DECISION_V1.md` |
| D3 Trading / Control 사전등록 | MDE PASS, 백테스터 허용 | `D_AGG_TRADING_CONTROL_CONTRACT_V1.md`, `d_agg_trading_rules_v1.json` canonical `4b35d3446799…3e3871` |
| D4 Backtest | **BACKTEST_FAIL** | run `dagg4-ceaeaf3b9e30`, `D_AGG_D4_RESULTS_V1.md` |

D2 핵심: TL 1.5346 (CI [1.4250, 1.6834]), DL 1.4702, NTL 1.0438, AG 1.0401, TL 양수 블록 4/4. PASS 수준 미달은 T4 하나(NTL 1.0438 < 1.10)였다.

D3 핵심: MDE80 0.248%/거래 (한도 0.50%). 규칙은 A(q) 세션 상위 10%, D+1 시가 진입, TP +10% / SL -10%, D+1..D+5, D+5 종가
시간청산, same-bar SL 먼저, COST_10BP. control은 같은 날 rv_20 10분위 전수 재가중(setup 제외, 무작위 추출 없음)이다.

D4 핵심: setup net +0.304%, control net +0.191%, **Delta +0.113%, 95% CI [-0.077%, +0.266%]**. 블록 Delta는 B1 +0.061% /
B2 -0.080% / B3 +0.215% / B4 +0.256%(3/4 양수). leave-out은 상위 5세션 제거 +0.046%, 상위 10종목 제거 +0.034%다.
게이트는 P1 PASS, P2 PASS, P3 PASS, **P4 FAIL**, P5 PASS, P6 PASS, P7 PASS였다.

## 2. 확인된 것과 확인되지 않은 것

**확인된 것.** D 아날로그 신호는 향후 큰 움직임(고변동성, 꼬리 사건)이 날 종목을 농축한다. D2에서 UP10 lift는 약 1.53배,
DN10 lift는 약 1.47배였다. D4에서도 setup의 레벨 청산(TP·SL·갭) 비율이 36%로 control 24%보다 높았다. 큰 움직임을 찾는
능력 자체는 확인되었다.

**확인되지 않은 것.**
- 상방 꼬리가 하방 꼬리보다 충분히 우월하다는 것. NTL의 CI가 1을 포함했다.
- 같은 변동성의 종목과 비교했을 때 독립적인 long payoff 우위가 있다는 것. D4 Delta +0.113%는 이 2년 데이터에서 0과 통계적으로
  구분되지 않았다.

## 3. FAIL의 이유 (공격형이라는 이유로 뒤집지 않는다)

D4 FAIL의 이유는 다음이 아니다.

```text
MDD가 커서                X
승률이 낮아서             X
위험성이 커서             X
```

게이트에는 MDD, 승률, 위험 조건이 없었다. 실제 이유는 하나다.

> **동일한 변동성 위험을 가진 control 대비 추가 수익 우위가 충분히 입증되지 않았다** (P4: Delta 95% CI 하한 > 0 미충족).

setup의 절대 수익(+0.304%)은 양수였고, 공격형 목적(큰 움직임 포착)도 달성했다. 그러나 그 수익은 같은 변동성의 아무 종목을 사서
얻는 수익과 구별되지 않았다. 공격형 전략의 목적을 그대로 유지해도 결론은 FAIL이다.

## 4. 구현 결함 기록 (결과 개선이 아님)

D4 첫 실행은 PIT 단계에서 numpy 2의 int8 승격 규칙 때문에 `OverflowError`(`260 + int8`)로 멈췄다. 잡음 재현 직후였고, 어떤
위치 통계(평균, Delta, 적중률)도 만들기 전이었으며, artifact도 쓰지 않았다. 캐스팅을 고치고 날짜 index 260 회귀 테스트를
추가했다(`test_d_agg_d4.py`의 `test_pit_audit_synthetic_has_witnesses_and_no_findings[300-260]`). 성과를 본 뒤 성과를 바꾼
수정이 아니다.

## 5. 추가 연구 금지 범위 (현재 2년 development dataset)

```text
D V2-B, D V3
Top 5%, Top 2% (또는 다른 setup 분위)
TP +15% / +20%, SL -5% / -7%, 보유 10D 등 매매 규칙 변경
새 distance, 새 K, 새 feature, 새 volatility filter
잘 나온 블록·세션·종목만 사용
secondary(S1 시간청산 등)를 primary로 승격
```

이 모두는 현재 결과를 본 뒤의 추가 최적화다. USB-HIST-V1 창은 D V1, C-4, D V2-A, D-AGG가 읽었고, D-AGG 안에서만 다섯 단계에
걸쳐 읽혔다. 이 창에서 다시 선언해도 증거력을 가질 수 없다.

## 6. 권한

```text
Virtual Trading                  NOT AUTHORIZED
Long Historical Data Purchase    NOT AUTHORIZED (D를 근거로 삼지 않음)
Current 2Y further D research    NOT AUTHORIZED
```

## 7. 재개 조건

Strategy D를 영구 삭제하지는 않는다. 다만 현재 연구선은 CLOSED다. 재개하려면 다음 중 하나의 **새 정보**가 있어야 한다.

- USB-HIST-V1이 보지 않은 충분한 forward data
- 새로운 장기 historical dataset (개발·검증·locked OOS를 물리적으로 분리)
- 현재 D와 본질적으로 다른 사전 가설 (새 선언, 새 checksum)

기존 2년 데이터에서 파라미터를 조정해 재개하는 것은 금지한다.

```text
NEXT: No immediate Strategy D work.
      Do not reopen using current 2-year dataset parameter tuning.
      Reopen only with genuinely new data or a fundamentally new pre-registered hypothesis.
```
