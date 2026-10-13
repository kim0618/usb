# US-B CRYPTO C1 Exit Research E1 - Executable 4H Baseline 사전등록 계약 V1

작성 2026-10-02. 이 문서의 sha256을 `data/research/crypto/c1_exit/e1_freeze_v1.json`에 기록한 뒤에만 수익률을 계산한다.
`c1_exit.e1.run()`은 실행 시 hash를 다시 계산하고 다르면 거부한다. **결과를 본 뒤 이 문서를 수정하지 않는다.**

## 0. 질문과 범위

유일한 질문: **C1 Entry를 실제 거래 단위로 재구성했을 때 고정 4H 청산이 현실적 비용 후 양의 기대값을 보이는가.**

| # | 규칙 |
|---|---|
| Z1 | **Exit 후보 0개.** B1·B3·B4·C1x·C2x·C3x·D1·D2 실행 금지. stop 실행 없음 |
| Z2 | C1 정의·threshold·방향 변경 금지. LONG only (SHORT은 E0에서 제외) |
| Z3 | 운영 C1 engine, LIVE terminal, paper 운영 계좌, 서버, 주문, API key 접근 0 |
| Z4 | commit/push 금지. 연구 경계 `docs/crypto/c1_exit/`, `backend/app/crypto/research/c1_exit/`, `data/research/crypto/c1_exit/` |
| Z5 | 이 데이터는 D5·D5.1·D5.2·D6가 이미 사용했다. 어떤 결과도 "새 out-of-sample confirmation"이라고 부르지 않는다 |

## 1. C1 Entry (D5.2 계약 sha256 `c49cd0e5…` 4절, 코드 `derivatives_flow.run()` 그대로)

C1[t] = (S1 bucket = B1) AND (Bybit OI 1h 로그변화 < 0) AND (24h 변동성 regime = HIGH).
- S1 = ln(Bybit perp last / Binance spot), 5m 격자, bar t는 `floor((ts+60s)/5m)*5m`에 끝난 5m bar까지만 본다.
- bucket: UTC 날짜 D의 cutoff = [D-30일, D)의 10/30/70/90 분위 (`features.bucketize`).
- OI 1h = ln(oi[t] / oi[t-60]), `dataset`의 oi는 기록 시각 + 5분에 가용.
- HIGH = 24h 1m 로그수익 std > D5.2 F1 train 70분위 cutoff (`regime_labels`, D5.2 기록값 0.0010924950359531157).
- 입력 결측이면 비교가 False → C1 False. 함수는 `derivatives_flow`의 모듈을 import만 하고 수정하지 않는다.

**실행 전 parity 검사(수익률 미사용)**: OOS에서 D5.2 run과 같은 마스크(240m exit가 fold 경계 안)로 센 C1 TRUE bar 수가 **18,498**이고 vol cutoff가 D5.2 기록과 같아야 한다. 다르면 실행 중단.

## 2. Signal event / episode / trade

운영 C1 Signal Engine의 lifecycle/dedupe 계약은 2026-10-02 기준 repository(작업트리와 origin/main 모두)에 **없다**. 따라서 provisional 정의:

| 단위 | 정의 |
|---|---|
| raw TRUE bar | C1[t] = True인 1m bar |
| state episode | C1[t-1] = False 이고 C1[t] = True인 t (FALSE→TRUE 전환 1건 = 1 event). TRUE가 이어지는 동안 추가 event 없음, FALSE가 되면 re-arm |
| E1 executable trade | episode 시작 bar t에서 결정, entry = open[t+1]. entry 시각이 OOS [2022-01-01, 2026-09-23)이고 exit bar가 데이터 끝 안에 있는 episode **전부** |

- **겹침 허용**: 이전 trade 보유 중 새 episode가 와도 독립 1단위 trade로 센다. signal universe를 4H 보유 규칙이 정하지 않게 하기 위해서다. 겹침은 8절 cluster bootstrap으로 처리한다.
- **참고(판정 불가)**: single-position 변형(보유 중 시작된 episode 건너뜀)의 trade 수와 net mean만 보고한다.
- D6의 455건(4h+60m cooldown)은 QC 참고값이며 정본이 아니다.
- 운영 lifecycle 계약이 나중에 다르게 동결되면 그 계약으로 E1을 재계산하고 이 결과는 provisional로 남긴다.

## 3. Benchmark 4H

entry = open[t+1], exit = open[t+1+240] (D5.2 2절과 동일, 정확히 240분 뒤 bar의 시가). 4H는 benchmark이며 최적 exit라고 해석하지 않는다.
참고 horizon: **30m, 1h, 2h, 8h** (같은 episode, 같은 회계). 6H 없음. 참고 결과로 benchmark를 교체하지 않는다.

## 4. 비용 회계 (D6-C 계약 재사용, `d6c.execution`의 엔진·호가·funding 함수 import)

- trade마다 새 in-memory `PaperEngine`(D6-C `build_engine`, 1x, VIP_0 taker 0.055%, maker 미사용). 고정 목표 명목 **1,000 USDT**(수량 0.001 BTC step 내림). bp 결과는 크기와 무관.
- 체결: D6-C `SyntheticBook`(spread 0.10 USDT, depth 5 BTC, tick 0.10). 진입·청산 모두 taker market.
- funding: D2 실제 rate, 보유 중 지난 8h 정산만 (D6-C와 같은 `boundary <= exit_ts` 규칙).
- trade별 저장 (bp = USDT / entry notional x 1e4):
  - `gross_bp` = (exit 기준가 / entry 기준가 - 1) x 1e4  (D6-C ZERO)
  - `spread_bp` = gross_bp - 엔진 realized gross bp
  - `fee_bp`, `funding_bp` = 엔진 누적 카운터 차이
  - `total_cost_bp` = gross_bp - net_bp
  - `net_bp` (BASE) = (realized gross - fees - funding) / entry notional
  - `net_stress_bp` = net_bp - (0.0002537159730588128 - 0.0000011841571614073798) x 1e4 (D6-C VIP0_STRESS)
- 회계 항등식 위반(flat-to-flat equity 변화 ≠ net)은 trade마다 검사, 1건이라도 있으면 보고.

## 5. Trade path dataset

`data/research/crypto/c1_exit/e1_trades_v1.parquet`. trade별: signal_id, signal(decision) time, entry time/price, exit time/price, gross/spread/fee/funding/total_cost/net/net_stress (4H), MFE_4H, MAE_4H(엔트리 기준가 대비 last 1m high/low, 보유 bar = entry bar ~ exit bar 직전), MFE_time, MAE_time(진입 후 분), fold, UTC 날짜, vol regime, feature snapshot(S1, S1의 그날 10% cutoff, OI 1h 변화, rv24h), episode 길이(분, **사후 정보, 기술통계 전용**), 참고 horizon별 gross/net/net_stress.
MFE/MAE는 경로 극값 기록일 뿐 stop 시뮬레이션이 아니다.

## 6. Primary metrics (BASE·STRESS 각각, 4H)

trades, gross mean, net mean, net median, win rate(net > 0), PF(양수 합 / |음수 합|), total net(bp 합), average win, average loss, P5, P10, worst, MFE mean/median, MAE mean/median, 그리고 8절 CI.

## 7. Robustness

BASE net 기준 내림차순 top 1, top 3, top ceil(1%), top ceil(5%) 제거 후 net mean과 부호. Fold F1~F9 (D5.1 경계, entry 시각 귀속): trades, net mean, median, sign, **positive fold 수 / 9**. trade < 20인 fold는 `SMALL` 표시(판정 분모에서 빼지 않고 표시만).

## 8. Statistical dependence (실행 전 동결)

- **Primary CI (gate)**: D6-C `metrics.bootstrap_ci`와 같은 방식. trade가 있는 UTC 날짜를 시간순으로 놓고 **연속 7일 블록**을 복원추출, 통계량 = 추출된 날짜들의 net 합 / trade 수 합. **10,000회, seed 20261002**, 95% percentile.
- 보조 1: calendar-day cluster bootstrap (trade 있는 날짜를 iid 복원추출, 같은 통계량, 10,000회, seed 20261003).
- 보조 2: iid trade bootstrap (참고용, 10,000회, seed 20261004). **유일한 CI로 쓰지 않는다.**

## 9. E1 Gate (primary = BASE 4H net, 8절 primary CI)

| 상태 | 조건 | 조치 |
|---|---|---|
| **E1_NEGATIVE** | CI upper ≤ 0 | E2 Exit 최적화 금지. tail/MFE/MAE 진단까지만 |
| **E1_INCONCLUSIVE** | CI lower ≤ 0 < CI upper | E2는 exploratory only. 어떤 exit도 "수익 전략 확정" 결론 금지, forward 필수 |
| **E1_POSITIVE_BASELINE** | CI lower > 0 | E2 정상 진행 가능 (재사용 데이터 한계 명시) |

E2 Authorization: NEGATIVE → NO, INCONCLUSIVE → EXPLORATORY_ONLY, POSITIVE → YES.

## 10. Secondary warnings (primary status 불변)

W1 STRESS net mean ≤ 0, W2 top 3 제거 후 부호 반전, W3 top 1% 제거 후 부호 반전, W4 positive folds < 6/9, W5 BASE median ≤ 0, W6 PF ≤ 1. 개수와 내용 보고.

## 11. Lookahead audit (자동)

1. **truncation test**: 원시 입력을 시각 T_cut 이후 NaN으로 지운 뒤 C1을 다시 계산해, T_cut 이전 bar의 C1이 전체 데이터 계산과 **완전히 같아야** 한다. T_cut 2개(2023-05-17 13:07 UTC, 2025-11-03 06:41 UTC), 비교 구간 = T_cut 이전 7일.
2. bucket cutoff가 [D-30, D)만 쓰는지(코드 확인), OI +5m(`dataset.OI_DELAY_MS`), 24h vol 창이 t까지인지, entry = t+1 open, exit = t+1+h open, funding이 보유 구간 정산만인지, fold 귀속이 entry 시각인지 보고.
3. 위반 1건이라도 있으면 결과를 판정에 쓰지 않는다.

## 12. 산출물

계약 본 문서, 결과 `docs/crypto/c1_exit/C1_EXIT_E1_RESULTS_V1.md`, 코드 `backend/app/crypto/research/c1_exit/e1.py`, 데이터 `data/research/crypto/c1_exit/` (e1_freeze_v1.json, e1_results_v1.json, e1_trades_v1.parquet). **E2는 실행하지 않는다.**
