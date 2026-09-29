# US-B CRYPTO D5.1 Long-Horizon Edge Discovery 결과 V1

작성 2026-09-24. 사전등록 `CRYPTO_D5_1_LONG_HORIZON_CONTRACT_V1.md`
(sha256 `43968ce52801c306313538c4753084645386c8eca9a7cefa8da9578e7ff01ed1`, 동결 2026-09-24T05:33:00Z)을
그대로 실행한 결과. 동결 뒤 계약·threshold·feature·fold·비용 변경 0회. 셀 대장은 `CRYPTO_D5_1_CANDIDATE_REGISTRY_V1.md`.

---

## 0. 결론

| 항목 | 결과 |
|---|---|
| SURVIVE (1h/2h/4h) | **0 / 720** |
| WEAK | 28 (전부 W2 = 비용에 죽은 gross. W1 0) |
| REJECT | 692 (+8h 237) |
| REFERENCE_ONLY (8h) | 3 |
| OOS에서 VIP_0 net > 0 인 주요 셀 | **0** |
| **Decision gate (기계적)** | **CASE B** (maker 구제 가능 W2: `E3-B1-2h-LONG`, `E3-B1-4h-LONG`) |
| D6 진입 | **불가** (CASE A 아님) |

**horizon을 늘리면 gross는 커진다. 그러나 1~4h에서도 taker 비용을 넘지 못한다.** 가장 강한 셀(perp가 index보다
쌀 때 LONG, 4h)의 OOS gross는 9.66bp로 비용 11.06bp의 **0.87배**다. 8h(참고)에서야 1.31~1.45배로 net이 양수가
되지만 CI가 0을 걸친다.

**게이트는 규칙대로 CASE B다. 그러나 그 근거인 두 셀은 해마다 약해져 최근 두 fold(F8·F9)에서는 gross가
거의 0이고, maker 가정 net도 음수다(9절).** CASE B를 "maker로 가면 된다"로 읽으면 안 된다.

---

## 1. Preregistration

| 항목 | 값 |
|---|---|
| 계약 hash | `43968ce5…01ed1` (`data/runtime/crypto/d5_1/contract_freeze_v1.json`) |
| hash 검사 | `long_horizon.run()`이 실행 때 재계산, 불일치면 `SystemExit`. 이번 실행 일치 |
| 동결 전 계산 | fold별 가격 경로·추세 비율(서술)뿐. feature x 1h+ 수익률 0건 |
| 사전 지식 | D5(5~30m, 2021-03 ~ 2026-09 전체) 결과를 본 뒤 작성. 어떤 구간도 untouched holdout이 아님을 계약에 명시 |

## 2. Data / Folds

D2 authoritative 5종만(D5와 같은 파일·checksum). 합성 호가·realtime 방향 데이터 0.
확장 walk-forward 9 fold, test 6개월(F9만 2026-01-01 ~ 09-23). OOS = 2022-01-01 ~ 2026-09-23.
fold별 train/test 구간·bar·추세 비율은 계약 2절 표. 경계를 가로지르는 표본은 embargo로 제거.

변동성 regime cutoff(F1 train에서 고정): LOW < 7.59e-4, HIGH > 1.09e-3 (1m 로그수익 24h 표준편차).

## 3. Horizons

주요 1h/2h/4h, 참고 8h(사전 결정). 무조건 기준선(OOS 전체 bar의 LONG gross):

| horizon | LONG drift | 정산을 지나는 표본 비율 | LONG 평균 funding 지불 |
|---|---|---|---|
| 1h | +0.29bp | 12% | +0.07bp |
| 2h | +0.58bp | 25% | +0.15bp |
| 4h | +1.16bp | 50% | +0.30bp |
| 8h | +2.31bp | 100% | +0.59bp |

---

## 4. 가장 강한 gross edge (OOS, 주요 horizon)

| 셀 | gross [95% CI] | 비용 | net | ratio | fold gross>0 |
|---|---|---|---|---|---|
| E3 `fund_premium` B1 4h LONG | +9.66 [+5.83, +13.79] | 11.06 | -1.41 | 0.87 | 8/9 |
| G1 `basis_mark_index` B1 4h LONG | +7.57 [+3.53, +11.70] | 11.02 | -3.44 | 0.69 | 8/9 |
| G2 `basis_mark_index_mean60` B1 4h LONG | +7.40 [+2.85, +12.03] | 10.95 | -3.55 | 0.68 | 8/9 |
| E1 `fund_last` B1 4h LONG | +6.76 [+0.22, +13.51] | 10.99 | -4.23 | 0.62 | 5/9 (REJECT) |
| A3 `trend_dist_sma1440` B5 4h LONG | +6.75 [+0.06, +13.94] | 11.28 | -4.53 | 0.60 | 5/9 (REJECT) |
| E3 `fund_premium` B1 2h LONG | +6.29 [+4.23, +8.68] | 11.05 | -4.76 | 0.57 | 8/9 |

상위는 **basis 계열(E3·G1·G2) B1 LONG이 독점**한다. 세 feature 모두 "perp/mark가 index보다 싸다"의 변형이라
독립 신호 3개가 아니라 1개다. D5의 괴리 회귀 신호가 긴 보유에서 커진 것이다. 4h drift(1.16bp)를 빼도 8.5bp 초과.

## 5. 가장 강한 net edge

**양수 없음.** 가장 나은 OOS net은 E3-B1-4h-LONG -1.41bp [CI -5.21, +2.80]. fold별 net이 양수인 fold는 2/9
(F1 +10.93, F3 +15.45)뿐이고 둘 다 2022~2023이다.

8h 참고 셀(판정 대상 아님): G2-B1-8h-LONG net +4.89 [ratio 1.45], G1 +4.15 [1.38], E3 +3.45 [1.31].
세 셀 모두 net 95% CI 하한이 0 아래(S3 실패)이고, 세 셀 모두 2024·2025·2026 연도 net이 음수다(연도 robustness 실패).

## 6. LONG vs SHORT

| | WEAK | 최대 OOS gross | 최대 OOS net |
|---|---|---|---|
| LONG | 25 | +9.66 | -1.41 |
| SHORT | 3 | +4.45 | -5.94 |

D5와 같은 비대칭. SHORT 쪽 WEAK은 E3·G1 B5(perp 프리미엄 → SHORT)뿐이고 2024년 이후 gross가 0 근처다.

## 7. 1h vs 2h vs 4h

| horizon | WEAK | 최대 gross | 최대 net | E3-B1-LONG gross | ratio |
|---|---|---|---|---|---|
| 1h | 16 | +4.02 | -7.01 | +4.02 | 0.36 |
| 2h | 9 | +6.29 | -4.76 | +6.29 | 0.57 |
| 4h | 3 | +9.66 | -1.41 | +9.66 | 0.87 |
| 8h (참고) | 3 | +15.79 | +4.89 | +14.55 | 1.31 |

gross는 horizon에 따라 대략 √h보다 빠르게(1h→8h 3.6배) 커진다. 비용은 거의 고정(~11bp)이라 ratio가 오른다.
그러나 **WEAK 수는 horizon이 길수록 준다**: 단기 평균회귀(B·A·C 계열)는 2h 이후 사라지고 basis 계열만 남는다.

---

## 8. Cost coverage ratio (VIP0_BASE)

주요 720셀 중 ratio > 1 인 셀 **0**. ratio >= 1.2(SURVIVE 조건) **0**. 최대 0.87.
ratio > 1 은 8h 참고 3셀뿐(1.31~1.45).

direction hit가 높아도 비용을 못 넘는 예: E3-B1-4h-LONG hit 53.1%, net win rate 45.6%. B3-B1-2h-LONG은 중앙 gross
+6.27bp(평균 +2.73)로 "대부분 이기는" 분포지만 ratio 0.24다.

## 9. Fee sensitivity

| 시나리오 | E3-B1-4h-LONG OOS net | E3-B1-2h-LONG |
|---|---|---|
| ZERO | +9.66 | +6.29 |
| VIP0_FEE (taker+funding) | -1.39 | -4.75 |
| VIP0_BASE | -1.41 | -4.76 |
| VIP0_STRESS | -3.93 | -7.28 |
| HYPOTHETICAL_MAKER_COST (참고) | **+5.61** [CI +1.80, +9.82] | **+2.26** [+0.21, +4.66] |

비용은 거의 전부 taker 수수료다(funding은 이 셀에서 +0.05bp 이하). maker 가정에서만 양수가 되어 게이트가 B로 갔다.

**CASE B의 약점 (반드시 같이 읽을 것)**: 두 셀의 maker net을 fold별로 보면

| fold | F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | F9 |
|---|---|---|---|---|---|---|---|---|---|
| E3-B1-4h maker net | +17.95 | +5.84 | +22.47 | +5.11 | +4.28 | +2.71 | +4.14 | **-5.08** | **-2.59** |
| E3-B1-2h maker net | +12.04 | +2.44 | +10.34 | +1.50 | +1.50 | +0.37 | +2.09 | **-4.29** | **-3.00** |

gross 연도 추이(4h): 2022 +15.92 / 2023 +18.48 / 2024 +8.01 / 2025 +3.31 / 2026 +1.33bp. **신호가 소멸 중이고
최근 12개월은 maker 비용(약 4bp)도 못 넘는다.** 또 maker 시나리오는 체결 가정이 없다: perp가 싸질 때 매수
대기주문을 걸면 체결되는 주문은 가격이 더 내려가는 경우에 몰린다(역선택). 그 효과는 이 데이터로 잴 수 없다.

## 10. Spread sensitivity

BASE 0.0118bp, STRESS 2.537bp(D5 실측 재사용). 긴 horizon에서도 **fee 지배는 그대로**: E3-B1-4h-LONG에서
VIP0_FEE → VIP0_BASE 차이 0.01bp, BASE → STRESS 2.5bp, fee 자체 11.0bp. depth는 Q_ref 비용 추정에만 쓰였고
SAFE MAX 실행 계약과 무관하다.

## 11. Regime robustness (OOS, VIP0_BASE net / gross)

E3-B1-4h-LONG:

| 축 | 라벨별 gross / net |
|---|---|
| 추세 | BULL +10.80/-0.18, BEAR +16.37/+5.41, SIDEWAYS +6.25/-4.88 |
| 변동성 | **HIGH +34.15/+23.23**, MID +6.72/-4.30, LOW +6.00/-5.10 |
| 세션 | 00-06 +7.74/-3.30, 06-12 +10.24/-0.85, 12-18 +9.38/-1.73, 18-24 +11.15/+0.14 |

net이 양수인 곳은 **고변동성(표본의 12%)과 BEAR뿐**이다. 전체 gross의 큰 몫이 고변동성 조각에서 나온다.
계약상 regime 필터를 붙인 셀은 만들지 않았다(Z5, 11절). "고변동성일 때만"은 새 사전등록이 필요한 가설이다.

## 12. Year robustness

| 셀 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| E3-B1-4h-LONG gross | +15.92 | +18.48 | +8.01 | +3.31 | +1.33 |
| G1-B1-4h-LONG gross | +6.23 | +18.10 | +7.70 | +2.74 | +1.06 |
| E3-B1-8h-LONG gross (참고) | +13.19 | +35.74 | +11.12 | +8.54 | +4.01 |
| G2-B1-8h-LONG gross (참고) | +22.47 | +34.28 | +11.09 | +4.21 | +5.79 |

모든 basis 계열에서 **2023이 최대이고 2024 이후 크게 줄었다**(G2-8h는 2026이 2025보다 약간 높지만 2023의 1/6). R5(최대 연도 제외 후 net > 0)는 WEAK 전 셀 실패.

## 13. Counts

SURVIVE 0 / WEAK 28 / REJECT 692 (주요 720). 8h: REFERENCE_ONLY 3 / REJECT 237.
WEAK horizon별 1h 16 / 2h 9 / 4h 3. 계열별: basis(E3·G1·G2) 13, 단기 평균회귀(A1·B1·B2·B3·C3·M1) 11, 변동성(F3·M4) 4.

## 14. D6 진입 가능 여부

**불가.** CASE A 조건(주요 horizon SURVIVE >= 1) 미충족. Score·ensemble 대상 후보 0.

## 15. 다음 권장 분기

**기계적 판정: CASE B (D5.2-MAKER 후보).** 계약 10절 규칙을 그대로 적용한 결과이며 뒤집지 않는다.

그러나 권고는 다음과 같다(판단은 사용자 몫):
1. CASE B를 그대로 진행한다면 D5.2-MAKER의 첫 질문은 "최근 12개월에 이 신호가 아직 있는가"여야 한다.
   9절 표대로 F8·F9에서는 maker 가정으로도 음수다. 이 질문은 새 historical 연구가 아니라 **앞으로 쌓이는
   forward 데이터**로만 답할 수 있다.
2. maker 체결 가능성·역선택은 1분봉으로 측정할 수 없다. 실시간 호가·체결 축적(큐 위치, 체결 뒤 가격 경로)이
   선결이다. 현재 실시간 근거는 23시간뿐이다.
3. **CASE D 추가 권고(근거 있음)**: 살아남은 신호가 전부 basis(perp/mark vs index)라는 점, 그 신호가 고변동성·
   BEAR에 몰린다는 점은 강제청산 연쇄·레버리지 해소와 맞물리는 현상일 가능성을 시사한다. liquidation 데이터,
   거래소 간 basis, 선물 기간구조는 이 가설을 직접 잴 수 있는 변수다. D5.2 사전등록 후보.
4. basis를 뺀 기존 price/OI/funding feature 축은 1h~4h에서 ratio 최대 0.62(E1 `fund_last` B1 4h LONG, REJECT: fold 방향 5/9)이고, 그중 WEAK(시간적으로 재현된 것)의 최대 ratio는 0.32다. 이 축은 사실상 닫혔다.

## 16. Tests

| 테스트 | 결과 |
|---|---|
| `backend/tests/crypto/test_d5_1_long_horizon_pit.py` (신규 3: k분 bar 닫힘 규칙, 새 feature 미래 불변, MTF 수동 집계 일치) | 3 passed |
| `backend/tests/crypto/test_d5_research_pit.py` (D5, 무수정) | 5 passed |
| `backend/tests/crypto/` 전체 | 303 passed |

## 17. Git

commit/push 0. 새 파일은 전부 untracked:
`docs/crypto/CRYPTO_D5_1_*.md` 3편, `backend/app/crypto/research/long_horizon.py`, `registry_d5_1.py`,
`backend/tests/crypto/test_d5_1_long_horizon_pit.py`. D5 모듈(`dataset/features/study/microcost/registry`) 무수정.
`data/runtime/crypto/d5_1/`은 gitignore. `backend/app/crypto/` 전체가 원래 untracked 상태다.

## 18. Risks / UNKNOWN

| # | 내용 |
|---|---|
| U1 | **untouched holdout 없음.** D5가 같은 기간 5~30m를 이미 봤다. basis 계열은 D5에서 이미 보인 신호라 D5.1 결과는 확증이 아니라 확장이다 |
| U2 | 표본은 매 1m bar라 4h/8h에서 크게 겹친다. N_eff = N/h(E3-B1-4h 1,267)로 보고했고 CI는 7일 block bootstrap. 겹침이 7일 블록보다 긴 의존성(regime)은 과소평가될 수 있다 |
| U3 | historical spread/depth 없음(23시간 실측 재사용). 고변동성 구간 비용은 STRESS보다 나빴을 가능성. basis 신호가 고변동성에 몰려 있으므로 **이 UNKNOWN이 가장 아픈 곳에 있다** |
| U4 | HYPOTHETICAL_MAKER는 체결·역선택을 모델링하지 않은 상한이다. CASE B는 이 상한 위에 서 있다 |
| U5 | 진입 지연·부분 체결 미모델링. 방향상 결과를 더 나쁘게 만드는 요인 |
| U6 | 연도별 감소가 구조적 소멸인지 일시적 국면 때문인지 확정 불가. 단 저변동성 구성만으로는 설명되지 않는다: LOW 비중은 2023 84.5%, 2025 83.1%, 2026 81.1%로 비슷하고 HIGH 비중도 2023 3.8%, 2026 3.7%로 같은데, gross는 2023이 최대이고 2026이 최소다. 구조적 소멸 쪽 증거가 더 강하다. 확정은 forward 데이터로만 가능 |
| U7 | 960셀, 상관 큰 feature. 사전 기록한 우연 통과 규모(OOS CI 기준 약 18셀)와 실제 net CI 통과 0을 함께 볼 것 |

Score / Probability / AUTO 0줄. 운영 paper 계좌·서버·soak 접근 0. commit/push 0.
