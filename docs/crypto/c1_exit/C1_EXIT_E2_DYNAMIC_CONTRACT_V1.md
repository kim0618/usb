# US-B CRYPTO C1 Exit Research E2 - Dynamic Exit Exploration 사전등록 계약 V1

작성 2026-10-02. 이 문서의 sha256을 `data/research/crypto/c1_exit/e2_freeze_v1.json`에 기록한 뒤에만 후보 결과를 계산한다.
`c1_exit.e2.run()`은 hash가 다르면 거부한다. **결과를 본 뒤 이 문서를 수정하지 않는다.**
선행: E1 = E1_INCONCLUSIVE (`C1_EXIT_E1_RESULTS_V1.md`, 계약 sha256 `3b2f188f…`) → 이 단계는 **EXPLORATORY_ONLY**.

## 0. 범위와 금지

| # | 규칙 |
|---|---|
| Z1 | Entry는 E1과 동일 (D5.2 C1 LONG, FALSE→TRUE episode, entry = open[t+1]). threshold·feature·universe 변경 0 |
| Z2 | 후보는 E0, B1, B3, B4, C1x, C2x, C3x뿐. **Composite(D1/D2) 금지**, 청산 데이터 금지, 4H benchmark 교체 금지 |
| Z3 | 운영 C1 engine·terminal·paper·live·서버·주문·Auto Exit·배포 0. commit/push 0 |
| Z4 | 어떤 결과도 "확정 전략", "운영 적용 가능", "best exit"로 표현하지 않는다. PROMISING = forward shadow 기록 가치가 있음 |
| Z5 | 데이터는 D5·D5.1·D5.2·D6·E1이 이미 본 구간. out-of-sample confirmation 아님 |

## 1. Lifecycle

2026-10-02 재확인: 운영 C1 Signal Engine lifecycle(OFF/TRIGGERED/ACTIVE/re-arm/dedupe) 계약은 작업트리와 origin/main 어디에도 없다.
→ E1 provisional 정의 유지: event = C1[t-1] False → C1[t] True. OOS event 1,603건(E1과 같아야 하며 다르면 실행 중단).

## 2. 공통 실행 규약

- 기호: e = entry bar (= event bar t + 1), `entry_ref` = open[e], `entry_fill` = 엔진 체결가. 보유는 open[e]부터.
- **평가 시점**: 1m bar k의 종가 뒤(k ≥ e). 그 시점까지 끝난 bar만 본다. 조건이 k에서 참이면 **exit = open[k+1]** (next executable open).
- **5m 평가 bar**: (ts[k] + 1분)이 5분 경계인 k. 외부 feature는 `derivatives_flow.to_1m` 규칙(bar k는 floor((ts+60s)/5m)*5m에 끝난 5m bar)을 그대로 쓴다.
- **max hold 8H**: k ≤ e + 479에서 trigger가 없으면 exit = open[e + 480], `censored_by_max_hold = True`. k = e + 479에서 trigger되면 exit 시각은 같지만 triggered로 기록.
- exit가 한 번 정해지면 그 trade는 끝난다. 이후 정보로 exit를 옮기지 않는다.
- **sigma_4h** (D6 동결 정의, `d6.features.f_rv24h`, `d6.sizing.stop_distance`): sigma_4h = rv24h[t] × √240, rv24h = 직전 1,440개 1m 로그수익률 std(ddof 0). event bar t의 값으로 trade마다 고정. 데이터 전체로 재추정하지 않는다.

## 3. 후보 (동결)

| ID | 범주 | 평가 | trigger 조건 | exit 가격 |
|---|---|---|---|---|
| **E0** | benchmark | - | 고정 4H | open[e+240] |
| R30·R60·R120·R480 | reference only | - | 고정 30m·1h·2h·8h | open[e+h] |
| **B1** | STOP | 1m intrabar (D6-C 그대로) | stop = entry_fill × (1 - clip(2.5 × sigma_4h, 0.01, 0.10)). bar j ∈ [e, e+480)에서 **mark_low[j] ≤ stop**인 첫 j, 없으면 open[e+480] ≤ stop이면 그 bar | **min(stop, open[j])** (adverse-first, gap 포함). 미발동 시 open[e+480] |
| **B3** | PROFIT PROTECTION | 1m 종가 | peak = max(high[e..k]), peak_ret = peak / entry_fill - 1. **peak_ret ≥ 1.0 × sigma_4h** 이후, close[k] / entry_fill - 1 ≤ **(1 - 0.50) × peak_ret** | open[k+1] |
| **B4** | PROFIT PROTECTION | 1m 종가 | peak_ret ≥ 1.0 × sigma_4h 이후, est_net[k] ≤ 0. est_net = close[k]/entry_fill - 1 - 0.00055 × (1 + close[k]/entry_fill) - (진입 뒤 ts[k]+1분까지 지난 funding rate 합) | open[k+1] |
| **C1x** | EDGE | 5m | S1 bucket ≥ B3 (그날 PIT 30분위 cutoff 이상, `features.bucketize` 값 ≥ 2)가 **5m 평가 2회 연속** | open[k+1] |
| **C2x** | EDGE | 5m | Bybit OI 1h 로그변화 > 0 (`dataset` oi, +5분 가용 지연 유지)가 **5m 평가 2회 연속** | open[k+1] |
| **C3x** | EDGE | 5m | rv24h가 유한하고 **≤ D5.2 동결 HIGH cutoff 0.0010924950359531157**인 첫 5m 평가 (확인 1회) | open[k+1] |

- 연속 확인: 조건 거짓 또는 입력 NaN이면 카운트 0으로 리셋.
- B3·B4는 high/low를 종가 시점에 이미 끝난 bar에서만 읽으므로 intrabar 순서 문제가 없다. B1만 intrabar이며 D6-C adverse-first를 쓴다.
- 발동률이 낮아도 threshold를 바꾸지 않는다(C3x는 낮을 것으로 예상, 그 자체를 보고).

### 3.1 Sensitivity sanity arm (판정 보조, winner 선택 금지)

B3-G40, B3-G60 (반납 40% / 60%), B3-A05, B3-A15 (활성 0.5σ / 1.5σ), B4-A05, B4-A15. 주 결과는 기본값(50%, 1.0σ).

## 4. 비용 (E1 = D6-C 그대로)

trade마다 새 `PaperEngine`, 목표 명목 1,000 USDT, SyntheticBook, taker 양쪽, 보유 중 지난 8h funding 정산. exit 기준가가 open이 아닌 경우(B1 stop)는 그 기준가로 청산 호가를 만든다.
저장: gross(기준가), spread, fee, funding, total_cost, net(BASE), net_stress(= BASE - (0.0002537159730588128 - 0.0000011841571614073798) × 1e4 bp).
E1 코드 `Executor.run`에 `exit_ref` 선택 인자를 추가한다(기본값 None = E1 동작 동일).

## 5. 분석 A - Counterfactual (primary for status)

E1 event 1,603건 전부에 각 후보를 적용. trade별 저장: signal_id, candidate_id, entry/exit 시각·가격, triggered_at, trigger_reason, censored_by_max_hold, holding_minutes, gross/cost/net/net_stress, MFE_before_exit, MAE_before_exit, capture, 평가 시점의 S1·bucket·OI 1h 변화·rv24h(trigger 시점 또는 exit 직전 평가).
- MFE/MAE_before_exit: bar [e, exit_bar)의 high/low 최대·최소 / entry_ref - 1 (B1 stop bar 자체는 제외).
- capture(trade) = gross / MFE_before_exit, MFE_before_exit > 0일 때만 (그 외 undefined로 수 보고).
- **profit retention** = Σ gross / Σ MFE_before_exit (MFE_before_exit > 0인 trade만).
- **paired delta** = candidate net - E0 net (trade별, BASE). 보고: mean, median, 7일 블록 CI, day-cluster CI, P(delta > 0) (trade 비율), P_boot(mean delta > 0) (7일 블록 bootstrap 표본 비율).

## 6. 분석 B - Executable one-position

상태기계 FLAT → (event) → OPEN → (후보 exit) → FLAT. event의 결정 bar t가 직전 exit bar 이상(t ≥ exit_bar_prev)일 때만 진입, 그 전 event는 skip(나중에 다시 받지 않음). E0도 같은 기계로 다시 계산.
보고: trades, skipped signals, 평균 보유, exposure(Σ보유분 / OOS 분), net mean, median, total net(bp 합), PF, win, max drawdown(trade 순서대로 누적 bp 합의 고점 대비 최대 하락), P5/P10/worst, fold별 net.

## 7. 통계 (동결)

- 7일 블록 bootstrap: trade가 있는 UTC 날짜를 시간순 정렬, 연속 7일 블록 복원추출, 통계량 = Σ값 / Σtrade수. **10,000회**. D6-C `metrics.bootstrap_ci`와 같은 알고리즘(같은 seed면 같은 결과임을 실행 중 E0에서 검증).
- seed: 후보 net 20261011, paired delta 20261012, day-cluster 20261013.
- iid CI는 쓰지 않는다.

## 8. Tail / winner / robustness

- tail: P5, P10, worst, average loss, MAE_before_exit 평균·P5, **large-loss 빈도 = net ≤ -300bp 비율**.
- winner preservation: E0 net 기준 상위 1%(17건)·5%(81건) cohort에서 E0 gross 합 대비 같은 trade의 후보 gross 합 비율.
- top removal: 후보 net 내림차순 top 1·3·1%·5% 제거 후 mean.
- fold F1~F9 (entry 시각): 후보 net, E0 net, delta, positive candidate folds, positive delta folds.
- 보유: 평균·중앙·P90, censored 비율, trigger 비율.
- 비용: fee·spread·funding·total 평균, BASE vs STRESS.

## 9. Status (분석 A, BASE 기준, 동결)

- **NOT_WORSE**: paired delta 7일 블록 CI 상한 ≥ 0 **그리고** mean delta ≥ -3.0bp.
- 개선 조건(하나 이상):
  - **A paired**: mean delta > 0 그리고 P_boot(mean delta > 0) ≥ 0.80
  - **B tail**: P5(후보) - P5(E0) ≥ +30bp **또는** large-loss 빈도 상대 감소 ≥ 25%
  - **C retention**: profit retention(후보) - profit retention(E0) ≥ +0.05
- guard(전부):
  - G1 후보 net mean, 자기 top 3 제거 후 > 0
  - G2 E0 top 5% cohort gross 보존율 ≥ 50%
  - G3 positive candidate folds ≥ 5/9
  - G4 STRESS net mean > 0
- **PROMISING** = NOT_WORSE ∧ (A ∨ B ∨ C) ∧ G1 ∧ G2 ∧ G3 ∧ G4.
  - B3·B4는 추가로 3.1 sensitivity arm이 전부 NOT_WORSE여야 한다. 하나라도 아니면 INCONCLUSIVE로 내린다.
- **UNPROMISING** = paired delta 7일 블록 CI 상한 < 0, **또는** (mean delta < 0 ∧ B tail 불충족), **또는** (G2 실패 ∧ mean delta < 0).
- **INCONCLUSIVE** = 그 밖.
- E0는 status 대상이 아니다. reference horizon은 status 없이 보고만.

## 10. E3 / forward 권고 규칙

- E3(composite) 권고 YES: PROMISING이 범주가 다른 후보 2개 이상(STOP, PROFIT PROTECTION, EDGE 중)일 때만. 그 밖은 NO.
- forward shadow 기록 권고: PROMISING 후보 전부 + E0. PROMISING이 0이면 E0만 권고.

## 11. Lookahead audit (자동 + 보고)

- B1: mark_low intrabar adverse-first, gap 체결 min(stop, open).
- B3/B4: peak·close·funding 모두 bar k 종가까지 (running 값).
- C1x: bucket cutoff는 [D-30, D) (`bucketize`), 5m 매핑은 `to_1m`.
- C2x: oi는 기록 + 5분 asof.
- C3x: rv24h는 t까지 trailing.
- 실행: 모든 exit는 open[k+1] 또는 B1 intrabar stop.
- 자동 검사: 후보마다 trade 표본 20건에서 trigger 판정을 **exit bar 이후 가격·feature를 NaN으로 지운 배열로 다시 계산**해 trigger bar가 같아야 한다. 불일치 1건이라도 있으면 그 후보는 판정하지 않는다.

## 12. 산출물

계약 본 문서, 결과 `C1_EXIT_E2_RESULTS_V1.md`, 코드 `backend/app/crypto/research/c1_exit/e2.py`, 데이터 `data/research/crypto/c1_exit/` (e2_freeze_v1.json, e2_results_v1.json, e2_counterfactual_v1.parquet, e2_executable_v1.parquet). **결과 보고 후 STOP.**
