# US-B CRYPTO D5 Edge Discovery 결과 V1

작성 2026-09-24. 사전등록 계약 `CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1.md`
(sha256 `720a822f23a523a806d54a8d68dc11108cad9e3168fe44de82e6ce7e1cdd1cad`, 동결 2026-09-24T05:16:14Z)을
그대로 실행한 결과다. 동결 뒤 계약 수정 0회, threshold 이동 0회, 추가 feature 0개.
셀 단위 대장은 `CRYPTO_D5_CANDIDATE_REGISTRY_V1.md`.

---

## 0. 결론

| 항목 | 결과 |
|---|---|
| **SURVIVE** | **0 / 540 주요 셀** |
| WEAK | 29 (전부 `W2_COST_KILLED_GROSS_EDGE`) |
| REJECT | 511 주요 + 172 (1m) |
| REFERENCE_ONLY (1m) | 8 |
| VIP_0 기준 비용 후 양수인 셀 | **0개. 어느 구간에서도, 어느 셀에서도** |
| D6 Score 후보 | **없음** |

**BTCUSDT 1분봉 단순 feature 18개로는 5~30분 horizon에서 taker 비용을 넘는 edge가 없다.**
시간적으로 재현되는 gross 신호는 있다(단기 평균회귀와 perp-index 괴리 회귀). 그러나 세 구간에서 모두
유지되는 크기가 **최대 0.89bp**이고, 가장 컸던 DISCOVERY 값도 **4.02bp**다. VIP_0 왕복 수수료는
**약 11.0bp**다. 비용 대비 한 자릿수 배 모자라다.

이 결론은 "비용 모델을 조금 바꾸면 뒤집히는" 종류가 아니다. 아래 7절 참고.

---

## 1. 실행 기록

| 항목 | 값 |
|---|---|
| 코드 | `backend/app/crypto/research/{dataset,features,study,microcost,registry}.py` |
| PIT 테스트 | `backend/tests/crypto/test_d5_research_pit.py` 5개 통과(미래 bar 변조 불변, t+1 시가 진입, funding 보유 규칙, cutoff 과거 전용, OI 5분 지연) |
| 실행 | `PYTHONPATH=backend .venv/bin/python -m app.crypto.research.study` 87초 |
| 계약 해시 검사 | 실행 시 재계산, 동결값과 일치 |
| 전 셀 결과 | `data/runtime/crypto/d5/cells_v1.json` (720셀, 필터 없음) |
| 로그 | `data/runtime/crypto/_d5_logs/study_v1.log` |

| 구간 | 표본 시작 | 끝(미포함) | 표본 bar (embargo 후) |
|---|---|---|---|
| DISCOVERY | 2021-03-04 | 2024-07-01 | 1,749,569 |
| VALIDATION | 2024-07-01 | 2025-10-01 | 658,049 |
| HOLDOUT | 2025-10-01 | 2026-09-23 | 514,049 |

변동성 regime cutoff(DISCOVERY에서 계산, 1m 로그수익 24시간 표준편차): LOW < 5.49e-4, HIGH > 8.62e-4.

**무조건 기준선** (bucket 무관 전체 bar의 LONG gross 평균):

| horizon | DISCOVERY | VALIDATION | HOLDOUT |
|---|---|---|---|
| 5m | +0.026bp | +0.056bp | -0.017bp |
| 15m | +0.075bp | +0.168bp | -0.053bp |
| 30m | +0.148bp | +0.336bp | -0.106bp |

drift는 0.34bp 이하라 아래 셀 결과를 설명하지 못한다. HOLDOUT은 약세 구간이라 기준선 LONG이 음수다.

---

## 2. 살아남은 Edge

**없음.** SURVIVE 조건 S1(DISCOVERY net 95% CI 하한 > 0)을 통과한 셀부터 0개다. net 평균이 양수인 셀이
DISCOVERY 0, VALIDATION 0, HOLDOUT 0이다. 주요 셀 중 가장 나은 net 평균은 **-7.05bp**
(E3-B1-30m-LONG, DISCOVERY).

사전 기록한 다중검정 규모(귀무가설에서 S1 우연 통과 약 13셀)와 비교해도, 실제 S1 통과가 0이므로
"우연히 살아남은 후보를 걸러야 하는" 상황 자체가 생기지 않았다.

---

## 3. 비용에 죽은 Edge (WEAK 29)

세 구간 모두 같은 방향의 gross가 있고, DISCOVERY gross 95% CI와 VALIDATION gross 90% CI 하한이 0보다 큰
셀들이다. 두 계열뿐이다.

### 3.1 단기 평균회귀 (23셀)

직전 1~60분 수익률·주문흐름이 하위 bucket(B1·B2)이면 LONG, 상위(B4)면 SHORT.

| 셀 | DISCOVERY gross | VALIDATION | HOLDOUT | 세 구간 최소 |
|---|---|---|---|---|
| B3 `mom_ret_15` B1 30m LONG | +1.81 | +1.13 | +0.79 | +0.79 |
| B2 `mom_ret_5` B1 30m LONG | +1.93 | +1.11 | +0.66 | +0.66 |
| A1 `trend_ret_60` B1 30m LONG | +1.17 | +1.37 | +0.63 | +0.63 |
| B2 `mom_ret_5` B1 15m LONG | +1.19 | +0.86 | +0.60 | +0.60 |
| B1 `mom_ret_1` B1 30m LONG | +1.26 | +1.35 | +0.49 | +0.49 |
| C3 `vol_signed_flow_15` B1 30m LONG | +1.17 | +0.85 | +0.30 | +0.30 |
| B3 `mom_ret_15` B4 15m SHORT | +0.37 | +0.26 | +0.48 | +0.26 |

(단위 bp, 전체 목록은 대장 3절)

이 계열의 중앙값은 평균보다 크다(B3-B1-30m-LONG: 중앙 +4.45bp / 평균 +1.81bp, DISCOVERY). 대부분
작게 되돌리고 가끔 크게 더 밀리는 분포다. 중앙값 기준으로도 11bp에 못 미친다(모든 주요 셀 중 최대
중앙 gross 5.15bp, 최대 중앙 net -5.96bp). net 승률은 최대 43.1%.

### 3.2 perp-index 괴리 회귀 (6셀)

E3 `fund_premium` = `ln(perp 체결가 / index)`. perp가 index보다 싸면(B1) LONG, 비싸면(B5) SHORT.

| 셀 | DISCOVERY | VALIDATION | HOLDOUT |
|---|---|---|---|
| E3 B1 30m LONG | +4.02 | +1.41 | +0.89 |
| E3 B1 15m LONG | +2.51 | +0.85 | +0.64 |
| E3 B5 15m SHORT | +2.08 | +0.42 | +0.32 |

연도별로 보면 **edge가 해마다 줄었다**(E3-B1-30m-LONG gross: 2021 +6.48 / 2022 +5.03 / 2023 +3.48 /
2024 +1.53 / 2025 +1.46 / 2026 +0.68bp). 시장이 효율화되며 사라지는 중인 신호다. E3-B5-30m-SHORT는
DISCOVERY +3.26bp였지만 VALIDATION 90% CI가 0을 걸쳐 REJECT됐다.

---

## 4. 죽은 Edge (DISCOVERY에서만 보인 것)

DISCOVERY gross 상위에 있었으나 뒤 구간에서 사라진 셀(대장 5절 15개):

| 셀 | DISCOVERY | VALIDATION | HOLDOUT | 판정 |
|---|---|---|---|---|
| E1 `fund_last` B1 30m LONG | +1.65 | -0.19 | -0.23 | 부호 반전 |
| E3 `fund_premium` B4 30m SHORT | +1.66 | -0.29 | +0.30 | VALIDATION 반전 |
| F1 `vola_rv_60` B5 30m LONG | +1.39 | +1.87 | -0.12 | HOLDOUT 반전 |
| E2 `fund_sum_3` B1 30m LONG | +1.22 | +0.47 | -0.46 | HOLDOUT 반전 |
| A2 `trend_ret_240` B1 30m LONG | +1.03 | +1.00 | -0.53 | HOLDOUT 반전 |
| D1 `oi_chg_60` B1 30m LONG | +0.91 | +1.52 | -0.06 | HOLDOUT 반전 |

**funding 레벨(E1·E2)과 OI(D1~D3) 계열은 한 셀도 WEAK에 들지 못했다.** OI 계열의 세 구간 최소 gross
최댓값은 0.26bp로 6개 그룹 중 가장 약하다. 변동성·거래량 상위 bucket의 LONG(F1·F3·C1·C2 B5)은 평균은
양수지만 CI가 넓어 판정 조건을 못 채웠다.

그룹별 "세 구간 모두 유지된 gross"의 최댓값: A 추세 0.63 / B 모멘텀 0.79 / C 거래량 0.86 / D OI 0.26 /
E funding 0.89 / F 변동성 0.61 (bp).

---

## 5. LONG vs SHORT

| | 세 구간 gross 모두 양수 | WEAK |
|---|---|---|
| LONG | 80셀 | 25 |
| SHORT | 28셀 | 4 |

비대칭이 크다. **급락 뒤 되돌림(LONG)이 급등 뒤 되돌림(SHORT)보다 강하고 오래 간다.** drift 때문이
아니라는 근거: HOLDOUT은 -24.5% 약세장이고 기준선 LONG이 음수(-0.106bp@30m)인데도 평균회귀 LONG
셀들의 HOLDOUT gross는 양수로 남았다. SHORT 쪽에서 살아남은 것은 E3 B5(괴리 회귀)와 B2·B3 B4
(작은 상승 뒤 되돌림)뿐이고 세 구간 최소 gross가 0.32bp 이하다.

---

## 6. Horizon

| horizon | WEAK | WEAK 셀의 세 구간 최소 gross 평균 |
|---|---|---|
| 5m | 10 | +0.19bp |
| 15m | 11 | +0.34bp |
| 30m | 8 | +0.55bp |
| 1m (참고) | 8 | +0.06bp 안팎 |

**가장 유효한 horizon은 30m.** 같은 신호도 horizon이 길수록 gross가 커진다(평균회귀가 30분 안에 다
끝나지 않음). 그래도 30m 최대가 0.89bp다. horizon별 gross 증가 속도(5m→30m 약 3배)로는 11bp에
도달하려면 이 계약의 범위를 훨씬 넘는 보유시간이 필요하다. 그것은 이번 결과로 말할 수 없다(검증 안 함).

---

## 7. 비용 민감도

**수수료가 전부다.** VIP_0 왕복 약 11.0bp가 net과 gross 차이의 거의 전부이고, spread·충격·funding은
합쳐도 BASE에서 0.01bp, STRESS에서 2.5bp다.

| 비용 가정 | 왕복 taker | 가장 강한 셀(E3-B1-30m-LONG) 세 구간 최소 gross 0.89bp 대비 |
|---|---|---|
| ZERO | 0 | +0.89 (상한선) |
| VIP_0 (가정 등급) | 11.0bp | -10.1 |
| VIP_1 (공표요율, 미도달) | 8.0bp | -7.1 |
| SUPREME_VIP (공표요율, 미도달) | 6.0bp | -5.1 |

공표된 가장 싼 taker 등급(SUPREME 0.030%)에서도 필요한 gross의 1/7에 못 미친다. **taker 체결로는
등급과 무관하게 불가능하다.** DISCOVERY 최댓값 4.02bp도 SUPREME 왕복 6bp보다 작다.

유일한 이론적 경로는 maker 체결(SUPREME maker 0.0000%)이다. 그러나 대기 주문이 언제 체결되는지는
과거 1분봉으로 알 수 없고, 평균회귀 신호에 maker로 들어가면 체결되는 주문이 바로 불리한 쪽(더 밀리는 쪽)에
몰린다(adverse selection). 이번 D5는 이 경로를 검증하지 않았다. 검증하려면 큐 위치를 볼 수 있는 실시간
호가·체결 축적이 필요하다.

---

## 8. Spread / Depth 민감도

| 시나리오 | 왕복 추가 비용 | 효과 |
|---|---|---|
| BASE (실측 p50) | 0.0118bp | 무시 가능 |
| STRESS (실측 최대) | 2.537bp | 모든 셀 net을 약 2.5bp 더 낮춤 |

WEAK 29셀의 VALIDATION+HOLDOUT 합산 STRESS net은 전부 -12.35 ~ -13.42bp다. spread/depth는 결론에 영향이
없다. 실측 근거는 23시간 top-5 테이프(8절 계약)라 **2021~2026 급변 구간의 실제 spread는 UNKNOWN**이며,
그 구간 비용은 STRESS보다 나빴을 가능성이 높다. 방향은 결론을 더 강하게 만드는 쪽이다.

Q_ref 0.1 BTC에서 top-5 깊이가 모자라 체결 자체가 불가능한 스냅샷이 매수 2.85% / 매도 3.55%였다
(1.0 BTC면 18~20%). 비용으로 환산하지 않았다.

---

## 9. Regime / 시간대 민감도

WEAK 셀은 net 기준으로 모든 regime 라벨에서 음수라(robustness `net T0/3 V0/3 S0/4 Y0/6`) S7은 판정할
의미가 없다. 아래는 **gross** 분해다(세 구간 합산, 판정에 쓰지 않음).

| 셀 | 추세 BULL / BEAR / SIDEWAYS | 변동성 HIGH / MID / LOW | UTC 세션 00 / 06 / 12 / 18 | 24시간 중 양수 |
|---|---|---|---|---|
| E3-B1-30m-LONG | +3.28 / +5.09 / +1.83 | +5.44 / +2.36 / +1.47 | +2.11 / +3.67 / +3.35 / +2.97 | 23 |
| B3-B1-30m-LONG | +2.60 / +3.01 / +0.05 | +3.64 / +0.15 / +0.50 | +0.70 / +1.88 / +1.09 / +2.32 | 16 |
| B2-B1-30m-LONG | +2.08 / +3.14 / +0.32 | +3.74 / +0.24 / +0.37 | +0.72 / +1.99 / +1.13 / +2.33 | 19 |
| A1-B1-30m-LONG | +2.89 / +2.27 / -0.32 | +3.51 / -0.57 / +0.46 | -0.42 / +1.22 / +1.00 / +2.46 | 16 |
| E3-B5-15m-SHORT | +0.98 / +3.36 / +1.40 | +2.54 / +1.50 / +0.66 | +1.51 / +0.58 / +2.00 / +1.52 | 23 |

- **평균회귀는 고변동성·추세장에 몰려 있다.** SIDEWAYS와 MID/LOW 변동성에서는 거의 0이다.
- 괴리 회귀(E3)는 regime 전반에 퍼져 있지만 해마다 줄고 있다(3.2절).
- 시간대: 평균회귀는 UTC 18-24 세션이 가장 강하고 00-06이 가장 약하다. 특정 시간 하나에 의존하지 않는다.
- 판정에 쓰지 않은 참고: N 1만 이상 regime 조각 중 가장 큰 gross는 E3-B1-30m-LONG의 2021년 +6.48bp다.
  **어떤 regime 조각도 VIP_0 비용을 넘지 않는다**(해당 조각 net -4.71bp). regime 필터를 붙여도
  이 feature들로는 비용을 넘을 수 없다는 뜻이다. 이것을 근거로 regime 필터를 새로 고르지 않는다.

---

## 10. Microstructure (EXPLORATORY, 판정 미사용)

계약 12절대로 historical 연구와 분리했다. 입력은 23.1시간 top-5 테이프 75,640 스냅샷.
결과 파일 `data/runtime/crypto/d5/microstructure_exploratory_v1.json`.

top-5 호가 불균형 `(bidQty-askQty)/(bidQty+askQty)` 5분위별 이후 mid 변화:

| forward | Q1 (매도 우위) | Q2 | Q3 | Q4 | Q5 (매수 우위) |
|---|---|---|---|---|---|
| 10초 | -0.553bp (상승 28.2%) | -0.284 | -0.036 | +0.152 | +0.443bp (상승 56.9%) |
| 60초 | -0.959bp | -0.497 | -0.197 | -0.090 | +0.219bp |
| 300초 | -2.070bp | -1.698 | -1.283 | -1.228 | -0.718bp |

불균형은 10초 mid 방향을 **단조롭게** 가리킨다. 그러나 Q5-Q1 차이가 10초 약 1.0bp, 60초 약 1.2bp로
수수료의 1/10이다. 300초 열은 테이프 기간 BTC가 하락해 전 분위가 음수다(drift). 하루치라 장기 근거가
될 수 없고, taker로는 이 역시 비용을 못 넘는다.

---

## 11. D6 Score에 넣을 후보

**없음.** SURVIVE가 0이므로 계약상 D6 입력 후보가 없다. WEAK(비용에 죽은 gross)를 Score에 넣으면
"비용 후 음수인 신호의 가중합"이 되므로 넣지 않는다.

이 결과가 D6 방향에 주는 제약(판단은 사용자 몫, 여기서는 사실만):
1. 1분봉 단순 feature + 5~30m + taker 조합은 이 데이터에서 닫혔다. 같은 조합에서 feature를 더 늘리는 것은
   다중검정만 키운다.
2. 비용을 넘으려면 gross가 한 자릿수 배 커져야 한다. 후보는 (a) 훨씬 긴 보유시간(수 시간~일),
   (b) maker 체결(큐 데이터 필요), (c) 다른 정보원. 셋 다 V1 계약 범위 밖이며 새 사전등록이 필요하다.
3. 평균회귀·괴리 회귀 신호는 "진입 타이밍 필터"로서의 가치가 남아 있을 수 있다(예: 다른 이유로 이미
   진입하기로 했을 때 급락 직후를 고르기). 이것도 검증 안 됨.

---

## 12. 한계

| # | 내용 |
|---|---|
| L1 | 표본은 bar마다 1개라 겹친다. 조건부 기대값이지 실행 가능한 거래 곡선이 아니다. 7일 block bootstrap으로 SE만 보정했다 |
| L2 | 진입가 proxy는 다음 bar 시가(체결가). 판단 후 주문 지연은 모델링 안 함. 방향상 결과를 더 나쁘게 만드는 요인 |
| L3 | historical spread/depth 없음. 급변 구간 비용 UNKNOWN(8절) |
| L4 | OI stamp 의미 미확정이라 5분 늦게 사용(보수적). OI 계열이 약한 이유 중 일부일 수 있다 |
| L5 | 실시간 근거는 23시간 1개 테이프. 운영 서버 테이프 직접 읽기는 이번 세션에서 권한 거부되어 시도하지 않았고, 이전 세션이 받아 둔 로컬 사본을 썼다 |
| L6 | 540셀은 서로 상관이 크다(B1·B2·B3·A1·C3는 모두 "최근 하락"의 변형). WEAK 29셀은 독립 신호 29개가 아니라 2개 계열이다 |

---

## 13. 산출물

| 파일 | 상태 |
|---|---|
| `docs/crypto/CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1.md` | 동결 |
| `docs/crypto/CRYPTO_D5_EDGE_RESULTS_V1.md` | 이 문서 |
| `docs/crypto/CRYPTO_D5_CANDIDATE_REGISTRY_V1.md` | 생성물(registry.py) |
| `backend/app/crypto/research/` 6파일 | 신규 |
| `backend/tests/crypto/test_d5_research_pit.py` | 신규, 5 passed |
| `data/runtime/crypto/d5/` | grid 캐시, 동결 기록, 실시간 비용 측정, 셀 결과, 미시구조 탐색 (gitignore) |

운영 서버·soak 서비스·운영 paper 계좌 변경 0건. 주문 0건. commit/push 0건.
Score / Probability / AUTO 구현 0줄.
