# US-B CRYPTO D5.2 Derivatives Flow Edge Study 결과 V1

작성 2026-09-27. 사전등록 `CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md`
(sha256 `c49cd0e577a8b81321c943a8eb6fe20b59635592816191efc30f67454bc8cf81`, 동결 2026-09-27T09:52:37Z)을 그대로 실행했다.
동결 전 계산은 데이터 QC뿐(feature 값 0, 수익률 0). 동결 뒤 계약 변경 0회.
Phase 1: `CRYPTO_D5_2_DERIVATIVES_DATA_PREFLIGHT_V1.md`, `CRYPTO_DERIVATIVES_SOURCE_MATRIX_V1.md`. 셀 대장: `CRYPTO_D5_2_CANDIDATE_REGISTRY_V1.md`.
코드 `backend/app/crypto/research/derivatives_flow.py` (150초, peak 3.8GB), 원본 결과 `data/runtime/crypto/d5_2/cells_v1.json`.

---

## 0. 결론

| 항목 | 결과 |
|---|---|
| 주요 셀 (15m ~ 4h) | **720** |
| SURVIVE | **0** |
| WEAK | 97 (W2 비용에 죽은 gross 96, W1 1) |
| REJECT | 623 (+8h 참고 133) |
| REFERENCE_ONLY (8h) | 11 |
| **Decision gate** | **CASE C**: cross-exchange·spot-perp·기간구조 축(이 데이터, 이 창)은 종료. 청산은 forward 수집으로 |
| D6 진입 | **불가** |

**새 파생시장 데이터도 taker 비용을 넘지 못했다.** 비용 0 기준으로는 720셀 중 360셀이 평균 양수(105셀은 95% CI 하한도 양수)지만,
VIP_0 taker 비용(왕복 약 11bp)을 넣으면 평균 양수는 3셀뿐이고 모두 같은 조합 이벤트(C1)다.

가장 흥미로운 결과는 **C1 `dislocation_event` 4h LONG** (Bybit perp가 현물 대비 하위 10% 할인 + Bybit OI 1시간 감소 + 고변동성):
OOS net **+19.3bp [+4.3, +35.5]**, ratio 2.77, fold 9개 중 8개 gross 양수, 연도 5개 모두 net 양수. 그러나 이벤트가 OOS 179일에 몰린
N_eff 77(기준 200)이고 판정 가능 fold 4개(기준 6)라 **WEAK(W1)**다. 강제 매도 뒤 반등을 가격·OI로 근사한 모양이라, 청산 데이터가 쌓이면 직접 검증할 1순위 가설이다(22절).

---

## 1. Git

`main`. 시작 HEAD `cfb29c1` → 끝 HEAD `8a6ec2d` (다른 세션의 커밋 `research(strategy-f): close F after prevalidation fail`, 이번 작업 파일 미포함). 이번 작업 commit/push 0, 파일 전부 untracked. tracked 수정 23개도 다른 세션 작업이며 건드리지 않았다.

## 2. Source matrix

`CRYPTO_DERIVATIVES_SOURCE_MATRIX_V1.md` 말미의 "추가 (2026-09-27)" 요약표. 요점:

| 계열 | 분류 |
|---|---|
| 청산 3사 | FORWARD_ONLY (장기 무료 없음, Tardis만 PAID) |
| Bybit·Binance·OKX perp·mark·index | LONG_HISTORY (Bybit 1m, Binance 1m, OKX 5m) |
| Binance spot 1m | LONG_HISTORY |
| funding | Bybit·Binance LONG_HISTORY / OKX SHORT_HISTORY(3개월) |
| OI | Bybit 5m·Binance 5m LONG_HISTORY / OKX FORWARD_ONLY(5m) |
| 기간구조 | COIN-M 분기물 LONG_HISTORY / USDT-M 원월물 SHORT_HISTORY |

## 3. Liquidation availability

**과거: 무료 불가.** Bybit·Binance 청산 REST 없음(404), OKX REST 24h 롤링, Binance COIN-M 스냅샷은 2024-10에 중단. 장기 이력은 Tardis(PAID)뿐.
**실시간 (45분 실측)**: Bybit 이벤트 단위(RAW 추정), Binance 초당 1건 집계, **OKX WS는 REST 전량의 33%만 전달**. side 의미는 SHORT 방향만 실측 일치(상승장). Preflight §6.
→ **Q1(청산 cascade 뒤 평균회귀)·Q2(청산 + OI 감소)는 과거 데이터로 답할 수 없다. 이 연구에서 계산하지 않았다.**

## 4. Cross-exchange availability

Bybit(D2 1m)·Binance(vision 1m, CHECKSUM 검증)·OKX(REST 5m, 593,280행 공백 0) perp last·mark·index를 5m UTC 경계에 정렬했다.
timestamp 의미: Bybit·Binance kline = 시작 시각, OKX 캔들 = 시작 시각(→ 종료 경계로 이동), funding = 정산 시각, OI = 기록 + 5분 가용.
funding·OI dispersion은 OKX 장기 이력이 없어 Bybit vs Binance 2거래소로 계산했다(계약 Z4).

## 5. Term structure availability

**USDT-M 분기물은 원월물이 2023년 하반기부터만 있다**(동결 전 QC: 유효 2021 1%, 2022 0.1%, 2023 33%). 그래서 계약 동결 전에 **COIN-M BTCUSD 분기물 + COIN-M index**로
바꿨다(당월물 약 100%, 원월물 연 91~93%). 롤: 만기 7일 초과 최근월 = near, 그다음 = far. 만기 08:00 UTC.

## 6. Historical coverage

연구 창 2021-02-01 ~ 2026-09-23, 5m 경계 593,280개. OOS(F1~F9 test) 안 bucket 부여 비율: 연속 feature 92.2 ~ 100%
(원월물 기반 T2·T3 92.2%), `ts_inverted` 상태 30.4%, C1 이벤트 0.7%.

## 7. Forward-only datasets

3사 청산(Bybit allLiquidation, Binance forceOrder, OKX REST 24h + WS), OKX funding·OI 5m. 계획: `CRYPTO_FORWARD_DATA_PLAN_V1.md`.

## 8. Storage estimates

| 데이터 | 실측 / 추정 |
|---|---|
| 이번 장기 아카이브 (Binance vision 2,659 파일 + COIN-M + OKX 5m 3계열) | **852MB** |
| 청산 메시지만 (하루) | Bybit 0.09MB, OKX 2.0MB(SWAP 전체), Binance 8.1MB(전 심볼 스트림) |
| 청산 + 저빈도 건전성(1분 1회 mark) | 3사 합계 하루 약 2 ~ 11MB (Binance를 BTC 스트림만 받으면 하한 쪽) → 월 0.1 ~ 0.3GB, 연 1 ~ 4GB |
| 건전성 스트림을 원래 빈도로 저장할 경우 | 하루 34 ~ 134MB/거래소 (저장 불필요, 재현성과 무관) |

## 9. Normalized schema

Preflight §8 초안을 유지했다. 이번에 코드로 구현한 부분:
- `deriv_liquidation`: `backend/app/crypto/derivatives/normalize.py` (venue, symbol, event_ts_ms, **liquidated_side LONG/SHORT**, price, qty_base, notional_quote, raw_side, recv_ms, is_aggregated). 모르는 값은 None(0 아님).
- 5m 분석 격자: `derivatives_flow.load_inputs` (venue x last/mark/index, spot, funding, OI, near/far 분기물, COIN-M index). 결측은 NaN, 5m 1개까지만 이월.

## 10. Preregistration hash

`c49cd0e577a8b81321c943a8eb6fe20b59635592816191efc30f67454bc8cf81` (`data/runtime/crypto/d5_2/contract_freeze_v1.json`). `run()`이 실행 시 재계산, 일치.

## 11. Tested features

17개: Cross-exchange X1 ~ X6, Spot-perp S1 ~ S4, 기간구조 T1 ~ T5 (T4 이진), 조합 C1 (이진). 공식은 계약 4절.
bucket은 D5 규칙(과거 30일 10/30/70/90 분위), 비용·통계·판정은 D5.1 규칙 그대로(주요 horizon만 15m ~ 4h).

## 12. Strongest gross edge

| 셀 | gross | 95% CI | net | ratio | 판정 |
|---|---|---|---|---|---|
| C1-EVENT-240m-LONG | **+30.19bp** | [+15.2, +46.6] | +19.28 | 2.77 | WEAK (W1) |
| C1-EVENT-120m-LONG | +18.51 | [+7.9, +29.1] | +7.52 | 1.68 | REJECT (net CI 0 걸침, 표본) |
| C1-EVENT-60m-LONG | +16.27 | [+9.6, +23.8] | +5.27 | 1.48 | REJECT |
| S1-B1-240m-LONG (현물 대비 할인 → LONG) | +8.57 | [+4.6, +13.0] | -2.49 | 0.78 | WEAK (W2) |
| S4-B1-240m-LONG (3사 합성) | +8.18 | [+4.2, +12.4] | -2.94 | 0.74 | WEAK (W2) |
| X1-B1-240m-LONG (Bybit이 타 거래소보다 쌈) | +6.42 | [+2.1, +10.6] | -4.66 | 0.58 | WEAK (W2) |

## 13. Strongest net edge

net 양수는 C1의 60m·120m·240m 세 셀뿐이고 CI 하한까지 양수인 것은 **C1-EVENT-240m-LONG 하나**(+19.28bp [+4.28, +35.48], STRESS +16.75bp).
C1을 빼면 최선의 net은 S1-B1-240m-LONG -2.49bp다.

**C1-240m-LONG이 SURVIVE가 아닌 이유**: S7(N_eff 77 < 200, 판정 가능 fold 4 < 6: F1·F2·F5·F7만 N ≥ 500·days ≥ 20)과 S8(변동성 regime 1/1).
**S8의 변동성 조건은 C1 정의상 통과할 수 없다**(C1이 HIGH 변동성을 조건으로 포함하므로 변동성 3라벨 중 2개 이상 양수가 불가능). 계약 설계의 결함이며,
결과를 본 뒤 판정을 바꾸지 않았다. 그 결함이 없어도 S7에서 떨어진다.

C1-240m-LONG 세부: fold별 gross F1 +35.1 / F2 +8.8 / F3 +68.7 / F4 +37.1 / F5 +35.8 / F6 +22.7 / F7 +25.8 / **F8 -25.5** / F9 +36.9bp.
연도별 net 2022 +14.8 / 2023 +48.3 / 2024 +20.6 / 2025 +8.4 / 2026 +25.9bp. 추세별 표본 BEAR 13,288 / SIDEWAYS 4,021 / BULL 1,189(net 모두 양수).
UTC 00-06시 세션만 net -9.2bp. OOS 이벤트 일수 179일.

## 14. Liquidation results

**계산하지 않음 (FORWARD_ONLY).** 대리 지표 C1(가격 할인 + OI 감소 + 고변동성)이 13절 결과다. 청산 자체의 효과는 forward 데이터로만 판단할 수 있다.

## 15. Cross-exchange basis results (Q3)

| feature | 결과 |
|---|---|
| X1 Bybit vs 타 거래소 | B1(Bybit 할인) LONG 4h W2: gross +6.42, net -4.66. **연도별 gross 2022 +8.9 / 2023 +16.5 / 2024 +9.5 / 2025 -2.2 / 2026 -6.3bp로 소멸.** 수렴 방향은 맞지만 비용 이하 |
| X2 last 분산, X3 mark 분산, X4 basis 분산 | 분산 상위(B4·B5) LONG이 W2: 4h gross +4.0bp 수준, ratio 0.35. 고·중변동성에 몰리고 2025 ~ 2026 소멸. basis와 상관 약 0.1 → 별개 신호지만 변동성 대리변수에 가깝다 |
| X5 funding 차이 | 50셀 전부 REJECT |
| X6 OI 비중 1시간 변화 | 50셀 전부 REJECT |

**Q3 답: cross-exchange 괴리는 방향은 맞지만(할인 거래소 LONG) 크기가 taker 비용보다 작고, 해마다 약해져 최근에는 사라졌다.**

## 16. Spot-perp basis results (Q4)

| feature | 결과 |
|---|---|
| S1 Bybit perp - Binance spot | B1(할인) LONG이 전 horizon W2 (4h gross +8.57, net -2.49, ratio 0.78). **D5.1 G1과 Spearman 0.72, E3와 0.82 → REPACKAGED_BASIS** |
| S4 3사 합성 | S1과 같은 모양 (0.67 / 0.70) |
| S2 24h z-score, S3 1h 변화 | 할인·압축 쪽 LONG W2, 비용 대비 0.2 ~ 0.5 |
| C1 할인 + OI 감소 + 고변동성 | 13절 (W1) |

S1-B1 LONG 연도별 net: 2022 +4.0 / 2023 +6.1 / 2024 -4.1 / 2025 -9.6 / 2026 -9.7bp. **D5.1이 본 basis 신호의 소멸과 같은 궤적이다.**
**Q4 답: 단독 spot-perp 괴리는 D5.1 basis 신호의 재포장이고 비용에 진다. 괴리에 OI 감소와 고변동성이 겹칠 때(C1)만 비용을 넘었지만 표본이 부족하다.**

## 17. Term-structure results (Q5)

기간구조 주요 210셀(T1·T2·T3·T5 각 50 + T4 10) 중 WEAK 6, REJECT 204. T3 기울기·T4 역전은 전부 REJECT. 최선은 T5-B1-240m-LONG(근월 연율 basis 24h 급락 → LONG) gross +4.39, net -6.94bp.
T1 근월 basis 낮을 때(B1) LONG은 fold 방향 4/9로 방향조차 일관되지 않았다.
**Q5 답: 기간구조 압축·역전은 이 창에서 예측력이 없었다.** 역전 상태(T4)는 OOS의 30.4%로 드물지 않았지만 이후 수익과 무관했다.

## 18. LONG vs SHORT

WEAK 97 = LONG 73 / SHORT 24. SHORT 최선은 S1-B5-240m-SHORT(현물 대비 할증 → SHORT) gross +4.43bp, CI 0 걸침, REJECT.
LONG 쪽 우위는 D5·D5.1과 같다(급락·할인 뒤 LONG이 더 강함). 2021 ~ 2026 BTC 상승 drift의 영향을 배제하지 못한다.

## 19. Cost sensitivity

| 시나리오 | 주요 720셀 중 OOS 평균 > 0 | 95% CI 하한 > 0 |
|---|---|---|
| ZERO | 360 | 105 |
| VIP0_FEE | 3 | 1 |
| VIP0_BASE | 3 | 1 |
| VIP0_STRESS | 3 | 1 |

비용 증가 대부분은 수수료다(BASE spread 0.01bp). ZERO에서 CI 하한이 양수인 105셀 가운데 VIP0 비용 뒤에도 평균이 양수인 것은 C1 3셀뿐이고, CI 하한까지 양수인 것은 1셀이다.
Maker 시나리오는 계약상 넣지 않았다(E1 BYBIT_CF_NEGATIVE).

## 20. Walk-forward robustness

C1-240m-LONG은 9개 fold 전부에서 train net 양수라 선택됐고, WF 선택 OOS net +19.28bp. 다른 WEAK 셀은 WF 선택 net이 모두 음수(S4 실패).
basis 계열은 F1 ~ F3(2022 ~ 2023H1) 이후 fold net이 대부분 음수다.

## 21. Year / regime robustness

- 연도: basis·괴리 계열은 2022 ~ 2023 양수, 2024 ~ 2026 음수(소멸). C1은 5개 연도 모두 양수(2025 +8.4bp로 최소).
- 변동성: 분산·basis 계열의 gross는 HIGH·MID에 몰리고 LOW(표본의 60 ~ 70%)에서는 거의 0.
- 추세: C1은 BEAR 표본이 72%. basis 계열도 BEAR에서 가장 강하다.
- 세션: C1은 UTC 00 ~ 06시만 음수.

## 22. SURVIVE / WEAK / REJECT

| 계열 | SURVIVE | WEAK | REJECT | REFERENCE_ONLY |
|---|---|---|---|---|
| Cross-exchange (X1 ~ X6) | 0 | 35 | 322 | 3 |
| Spot-perp (S1 ~ S4) | 0 | 55 | 179 | 6 |
| 기간구조 (T1 ~ T5) | 0 | 6 | 245 | 1 |
| 조합 (C1) | 0 | 1 | 10 | 1 |
| 합계 (8h 포함 864) | **0** | **97** | **756** | **11** |

## 23. D6 진입 가능 여부

**불가. CASE C.** SURVIVE 0이다. C1-240m-LONG은 비용을 넘은 유일한 셀이지만 표본 기준에서 떨어졌고, 사후에 C1을 다시 쪼개거나 기준을 바꾸면
다중검정을 키울 뿐이다(주요 720셀, 귀무에서도 CI 하한 양수가 약 18셀 우연히 나올 규모).

## 24. Forward data plan

`CRYPTO_FORWARD_DATA_PLAN_V1.md`. 요점: 3사 청산 연구 전용 수집(OKX는 REST 24h 폴링 주), 최소 관찰 6개월 또는 하락 이벤트 30회,
첫 사전등록 가설은 **C1과 같은 "할인 + OI 감소 + 고변동성" 순간에 LONG 청산 급증이 있었는가, 그 경우에만 반등이 비용을 넘는가**.

## 25. Tests

`backend/tests/crypto/test_d5_2_derivatives.py` **12 passed**: timestamp 정규화(마이크로초·중복), 5m 종가·결측 NaN·1회 이월, OKX 시작→종료 경계,
OI 가용 지연, 결정 bar의 미래 미참조, basis·연율·기울기·분산 계산, 결측 거래소 NaN, 7일 롤, 청산 side 매핑·거래소 매핑·중복 제거·모르는 값 None,
walk-forward fold 경계(D5.1 동일), 비용 적용(maker 없음), paper/terminal이 연구 모듈을 import하지 않음. crypto 전체 **405 passed**.

## 26. Git status

commit/push 0. 새 파일(untracked): `backend/app/crypto/derivatives/`(`liq_capture`, `archive`, `normalize`, `capture_report`),
`backend/app/crypto/research/derivatives_flow.py`, `registry_d5_2.py`, 테스트 1개, 문서 3개 신규 + 2개 갱신, `data/runtime/crypto/d5_2/`(gitignore 대상).
D5·D5.1 코드와 결과 수정 0. 운영 서버·Paper 계좌·운영 WebSocket 접근 0.

## 27. Risks / UNKNOWN

| 항목 | 내용 |
|---|---|
| 기간 재사용 | 2021 ~ 2026은 D5·D5.1에서 이미 본 기간. untouched holdout 아님 |
| C1 설계 결함 | 변동성 조건을 포함한 이벤트에 변동성 robustness(R2)를 요구해 구조적으로 통과 불가. 결과 뒤 수정 안 함 |
| C1 표본 | 179일에 몰린 이벤트, 이벤트 안 1m 표본은 강하게 상관. N_eff 77 |
| 청산 side | LONG 방향 의미는 캡처 중 실측 못 함(상승장) |
| Bybit 청산 전량 여부 | REST가 없어 대조 불가 |
| OKX 5m | 1m보다 거친 해상도. 5m 격자로 통일해 시간 어긋남은 막았지만 5분 안 괴리는 못 봄 |
| Binance mark·index 결측 | 창 안 12,962 / 18,727분 결측(NaN, 이월 1회) |
| 상승 drift | LONG 우위 일부는 2021 ~ 2026 BTC 상승의 몫일 수 있음 |
| 비용 | VIP_0 등급은 가정(계정 없음) |

## 28. Next exact step

1. **CASE C 확정**: cross-exchange·spot-perp·기간구조 단독 축은 이 창에서 닫는다. 같은 조합에 feature를 더하는 제안은 하지 않는다.
2. 남은 경로는 **청산 forward 수집**이다. 연구 전용 수집기를 운영 feed와 분리해 상시 가동하는 것은 서버 작업이므로 사용자 승인 뒤에만 한다(이번 세션에서 서버 접속 0).
3. 과거 청산 검증이 필요하면 Tardis 구매가 유일한 경로다(구매 결정은 사용자).
