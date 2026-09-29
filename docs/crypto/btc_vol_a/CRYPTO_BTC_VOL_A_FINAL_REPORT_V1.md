# CRYPTO BTC-VOL-A FINAL REPORT V1

계약 §34의 37항목 순서. 2026-09-29. 미커밋.
**주문 0, API key 0, Paper 0, 유료 데이터 0, 배포 0.**

---

## 1. Git

branch `main`, HEAD `4481f9c194f3318c0891bf344b16c02b1ab6bdcb`. 커밋 0, push 0.

HEAD가 세션 중 이동했다(다른 세션의 `strategy-h-v2` 커밋 5개, **US 주식 작업이며 crypto와 무관**).
그 커밋들이 건드린 경로에 내 작업 경로는 없다.

신규(전부 untracked): `backend/app/crypto/research/btc_vol_a/` 4파일,
`backend/tests/crypto/test_btc_vol_a.py`, `docs/crypto/btc_vol_a/` 3문서,
`data/research/crypto/btc_vol_a/` 2산출물.

**병렬 경로 수정 0건**: `btc_p1`·`btc_p1_forward`·`btc_p2`·`btc_vol_p0`·`manual_r1`·
`manual_r2`·`liquidation_forward`·`paper`·`terminal`.

## 2. 기존 authoritative 상태

BTC-P1 large-move 신호 존재(skill +0.180, AUC 0.727). BTC-P2 STRONG_DIRECTION 0/64, P3 NOT_AUTHORIZED.
BTC-VOL-P0 ECONOMICALLY_UNPROMISING, VOL-P1 NOT_AUTHORIZED.

## 3. Contract hash

`7ed5279083b2cc70ab584e57775d12a5fc18d1bdbf2d75e9010ea54759b6b2c2`.
**IV·프리미엄을 하나도 보기 전에 동결**했고 실행 시 대조 일치.

## 4. Tardis 공식 무료 범위

**API 자신의 오류 메시지로 확인**(추측 아님):

> `"For unauthorized requests, only historical CSV market datasets for the first day of each month are available"`

| 확인 | 결과 |
|---|---|
| 2023-05-01 plain GET | HTTP 200 |
| 2023-05-02 plain GET | **HTTP 401** + 위 문구 |
| HEAD / range | Cloudflare 403 - plain GET만 사용 |

데이터셋 `deribit/options_chain`. 컬럼에 `bid_iv`·`ask_iv`·`mark_iv`·bid/ask/mark·greeks·
`underlying_price`·`strike_price`·`expiration`이 **전부** 있다.

## 5. Deribit 공식 옵션 구조

**공식 public API `public/get_instrument`로 직접 확인**:

| 항목 | 값 |
|---|---|
| `maker_commission` / `taker_commission` | **0.0003 = 0.03%** of underlying |
| `contract_size` | 1.0 BTC |
| `min_trade_amount` | 0.1 |
| `quote_currency` | **BTC** (호가가 곧 기초자산 대비 비율) |
| `settlement_currency` | BTC |
| `settlement_period` | **day** 존재 (일간 만기) |

간접 확인된 프리미엄 상한 12.5%는 여기서 구속하지 않는다(leg당 프리미엄 약 1.79%의 12.5% = 0.224% > 0.03%).

## 6. Free dates

P1 검증 구간(2022-01-01 ~ 2026-09-22) 안의 매달 1일 = **57개**.

## 7. P1 overlap

57개 전부 P1 구간 안에 있다.

## 8. P1 high-confidence event count

| 조합 | p>=0.75 | p>=0.70 |
|---|---|---|
| 4H ±1% | 1 ep | 5 ep |
| **12H ±1%** | **9 ep / 31 events / 6 날짜** | 24 ep / 82 events / 13 날짜 |
| 12H ±2% | **0** (제외) | 2 ep |
| 24H ±2% | 5 ep / 6 events | 5 ep / 9 events |

PRIMARY를 12H ±1%로 **동결 전에** 정한 근거가 이 표다(성능 아님, 표본 수).

## 9. Matched event count

| 조합 | level | events | **측정 성공** |
|---|---|---|---|
| **12H ±1%** | **p>=0.75** | 31 | **30** |
| 12H ±1% | p>=0.70 | 82 | 72 |
| 24H ±2% | p>=0.75 | 6 | 5 |
| 24H ±2% | p>=0.70 | 9 | 8 |
| 4H ±1% | p>=0.75 | 1 | **0** |
| 4H ±1% | p>=0.70 | 5 | 4 |

PRIMARY 실패 1건은 `2022-07-01T00:00Z`로 lookback 창이 파일 시작 이전이다. 정당한 결측.

## 10. Expiry rule

TTE >= horizon + 2시간 buffer를 만족하는 **가장 가까운 만기**. 사전 동결.

**실제 TTE 중앙 21.5h / 평균 22.3h** (12시간 horizon에 대해).
Deribit 최단 만기가 일간 08:00 UTC이므로 **10시간치 시간가치를 더 사게 된다.**

## 11. ATM rule

`abs(strike - underlying_price)` 최소. 사전 동결. 결과를 보고 strike를 바꾸지 않았다.

## 12. Quote freshness

| 조합 | staleness 중앙 |
|---|---|
| 12H ±1% p>=0.75 | **1.3초** |
| 24H ±2% p>=0.75 | 0.9초 |
| 4H ±1% p>=0.70 | 0.5초 |

허용치 300초 대비 매우 신선하다. **미래 quote 사용 0**(스트림 단계에서 차단, 테스트로 강제).

## 13. 실제 ATM IV

| 조합 | level | mark IV 중앙 | **ask IV 중앙** | ask IV 평균 | 범위 |
|---|---|---|---|---|---|
| **12H ±1%** | **p>=0.75** | 82.2% | **84.9%** | 85.8% | **42.8% ~ 116.5%** |
| 12H ±1% | p>=0.70 | 64.6% | 70.3% | 72.4% | 41.8% ~ 116.5% |
| 24H ±2% | p>=0.75 | 65.6% | 67.2% | 67.3% | 65.2% ~ 70.8% |

**IV 범위가 42.8%에서 116.5%까지 벌어진다.** P1 고신뢰 상태는 하나의 IV 체제가 아니다.

## 14. VOL-P0 proxy RV

VOL-P0가 가정한 값: **103.5%** (직전 24h 실현변동성 연율화).

## 15. IV minus proxy

| | 값 |
|---|---|
| VOL-P0 가정 | 103.5% |
| **실측 ask IV** | **84.9%** |
| **차이** | **-18.6 포인트** |

> **VOL-P0의 가정은 매수자에게 유리하지 않았다. 오히려 18포인트 불리했다.**

VOL-P0 손익분기가 86.5%였으므로, **VOL-P0 자신의 틀로 보면 실측 IV는 근소하게 싸다.**

## 16. Straddle mid

12H p>=0.75: **3.36%** of spot (중앙).

## 17. Straddle ask

12H p>=0.75: **3.55%** of spot (중앙), 3.58% (평균).

## 18. Spread

| 조합 | ask - mid, mid 대비 |
|---|---|
| 12H ±1% p>=0.75 | **4.0%** |
| 12H ±1% p>=0.70 | 4.3% |
| 24H ±2% p>=0.75 | 2.7% |

spot 대비로는 약 **0.13%**. §23이 보이듯 **이 스프레드 하나가 부호를 뒤집는다.**

## 19. Implied move

| 방식 | 값 (12H p>=0.75) |
|---|---|
| straddle ask 그대로 | 3.55% |
| IV 기반 `0.798 × ask IV × sqrt(TTE)` | 3.40% |

두 방식이 0.15%p 안에서 일치한다. 계산 경로가 서로를 검증한다.

## 20. Future max excursion

| 조합 | level | 중앙 |
|---|---|---|
| 12H ±1% | p>=0.75 | 3.09% |
| 24H ±2% | p>=0.75 | 4.45% |

## 21. Endpoint move

| 조합 | level | 중앙 | 평균 |
|---|---|---|---|
| 12H ±1% | p>=0.75 | 1.29% | 1.87% |
| 24H ±2% | p>=0.75 | 1.67% | 2.24% |

## 22. Time-to-touch

이번 단계에서 새로 계산하지 않았다. VOL-P0 산출물 참조
(4H p>=0.75 중앙 27분, 12H 53분).

## 23. Fee-before result

**PRIMARY 12H ±1% p>=0.75, n=30:**

| 통계 | Check B edge |
|---|---|
| **중앙 (동결 게이트)** | **-2.02%** |
| 평균 (볼록 손익에 맞는 통계) | **-1.70%** |
| 양수 비율 | 16.7% |

**중앙값과 평균이 같은 방향이다.** 동결 게이트가 볼록성 때문에 잘못된 통계를 쓴 것은 아니다.

### 23.1 모집단 보정 (가장 중요한 계산)

무료 날짜 표본이 조용하므로(§26) 모집단으로 보정한다.

| 항목 | 값 |
|---|---|
| 모집단 12h 평균 \|endpoint\| (n=1,066) | 2.554% |
| 실제 TTE 22.27h로 sqrt-time 환산 | **3.479%** |
| 그것이 함의하는 IV | **86.51%** |
| **시장 ask IV (실측 평균)** | **85.80%** |
| **차이** | **+0.71 포인트 (매수자 유리)** |

| 항목 | spot 대비 |
|---|---|
| mid 비용 | 3.450% |
| **ask 비용 (실측)** | **3.579%** |
| 보정 payoff | 3.479% |
| **mid edge** | **+0.029%** |
| **ask edge** | **-0.100%** |

> **Deribit은 P1 고신뢰 시점에 이후 실제로 실현되는 것과 거의 정확히 같은 변동성을 부른다.**
> 과대평가도 과소평가도 아니다.

## 24. Fee-after reference

Deribit 공식 API 실측 0.03% × 2 leg = **0.060%** of spot.

| 항목 | spot 대비 |
|---|---|
| ask edge (보정 후) | -0.100% |
| 수수료 | -0.060% |
| **수수료 후** | **-0.160%** |

행사·정산 비용은 별도이며 포함하지 않았다. 더 나쁘게만 만든다.

## 25. Event-level 표

PRIMARY 30건 전수를 `CRYPTO_BTC_VOL_A_RESULTS_V1.md` §8에 실었다.

edge 양수 5건 중 **3건이 2022-07-01 새벽 세 시간에 몰려 있다**(2022년 6월 폭락 직후).

## 26. 표본 한계 (결론만큼 중요)

| 표본 | n | 12h \|endpoint\| 평균 | excursion 평균 |
|---|---|---|---|
| **전체 p>=0.75** | 1,066 | **2.55%** | 4.45% |
| **무료 날짜 측정분** | 30 | **1.20%** | 3.02% |

**무료 날짜의 실현 이동폭이 모집단의 절반 이하다.**

| 날짜 | 건수 |
|---|---|
| **2022-07-01** | **18 / 30** |
| 나머지 5개 날짜 | 12 |

**30건 중 18건이 하루에서 나왔다.** 독립 사건은 사실상 6개다.
이것이 §23.1의 모집단 보정을 필수로 만든다.

기타 한계:
- 무료 데이터가 매달 1일에만 있어 selection bias 가능
- TTE가 horizon보다 10시간 길어 term structure가 섞인다
- sqrt-time 환산은 로그정규 가정이다
- `2026-04-01` 미취득(HTTP 429, 5회 재시도 후 포기). p>=0.70에 instant 2개 결손, **PRIMARY 무영향**

## 27. 실제 IV가 VOL-P0를 확인하는가

**결론은 확인하고, 근거는 정정한다.**

| | VOL-P0 주장 | **실측** |
|---|---|---|
| IV | 103.5% (가정) | **84.9%** |
| 가정의 방향 | "매수자에게 유리" | **매수자에게 불리했다** |
| 지는 이유 | "IV가 비싸서" | **"IV는 거의 공정하고 스프레드·수수료 때문에"** |

**VOL-P0는 맞는 답을 틀린 이유로 얻었다.**

## 28. 조기 청산은 연구할 가치가 있는가

**계약 §21에 따라 early-exit을 하나도 계산하지 않았다.** 사전등록 진단값만 기록한다.

| 조합 | level | `excursion - ask` 중앙 | **양수 비율** |
|---|---|---|---|
| 12H ±1% | p>=0.75 | -0.84% | 36.7% |
| **24H ±2%** | **p>=0.75** | **+0.85%** | **100% (5/5)** |
| **24H ±2%** | **p>=0.70** | **+1.17%** | **87.5% (7/8)** |

**24H 계열에서 옵션 수명 중 최대 excursion이 ask를 전부 넘었다.**
만기까지 들면 지지만(endpoint edge -1.98%), 중간에 프리미엄보다 멀리 간 적이 있다.

**그러나 n=5다.** 그리고 excursion을 실제로 잡으려면 그 순간 호가로 빠져나갈 수 있어야 하는데,
그것은 이번에 측정하지 않았다.

## 29. 판정

# CONFIRMS_UNPROMISING

동결 게이트: PRIMARY Check B **median edge -2.02% <= 0**.

## 30. BTC-VOL-B authorization

# NOT_AUTHORIZED

§18의 EARLY_EXIT_WORTH_STUDYING은 **두 조건**을 요구하고 endpoint 조건이 실패한다.

## 31. Forward option collection 필요성

판정이 `CONFIRMS_UNPROMISING`이므로 계약 §27의 조건(INCONCLUSIVE 또는 EARLY_EXIT)에 해당하지 않는다.
**설계하지 않았다.**

다만 §28의 24H 관측이 남아 있고, 그것을 제대로 보려면 시점별 호가 시계열이 필요하다.
판단은 사용자 몫이다.

## 32. Data purchase 필요성

**필요하지 않다.** 무료 데이터로 핵심 질문(실제 IV가 proxy보다 낮은가)에 답이 나왔다.

§28의 24H 조기청산을 제대로 보려면 유료가 필요하다:
Tardis Solo $700-1,200/월(공개 가격). **이번에 구매하지 않았다.**

## 33. Tests

`backend/tests/crypto/test_btc_vol_a.py` **35개**.

| 영역 | 내용 |
|---|---|
| contract | 문자열 결속, 해시, 편집 거부, 동결 전 금지사항, PRIMARY가 VOL-P0 HIGH_STATE와 일치 |
| free dates | 57개, 매달 1일 |
| **PIT** | **결정 시각 이후 quote 미산출**, stale 초과 제외, BTC만, 최신 quote 우선, crossed 시장 거부 |
| expiry/strike | buffer 경계 정확, 없으면 None, ATM 최근접, 양다리 없으면 미선택 |
| realised | 옵션 자체 tenor로 측정, 결정 봉 이후 시작, 그리드 초과 시 미측정 |
| measurement | 동결 수량 계산, 빈 book 처리, **IV 백분율 환산** |
| verdict | 표본 우선, 0 edge는 CONFIRMS, early-exit은 두 조건 요구 |
| scope | 매매엔진·병렬작업 참조 0, **early-exit 식별자 0**, API key 0 |
| 실결과 | 모든 측정 quote가 결정 시각 이전, 모든 만기가 buffer 충족 |

## 34. Crypto regression

**1,341 passed, 0 failed** (저장소 루트 기준).

## 35. Git status

커밋 0. push 0. 운영 경로 무변경.

## 36. Production changes

**0.** 서버 접근 0, Paper 접근 0, Binance 계좌 접근 0, Deribit 계좌 불필요(public data only), API key 0.

## 37. 다음 한 걸음 (사용자 결정, 자동 진행 금지)

| 선택지 | 비용 | 내용 |
|---|---|---|
| **A** | $0 | **방향·변동성 라인 종결.** R1 롱 옵션은 실측으로 기각됐다. P1 forward shadow만 유지 |
| B | $700~2,400 | **24H 조기청산 검증**(§28). excursion이 ask를 100% 넘었으나 n=5. 유료 호가 시계열 필요 |
| C | $0 | **더 짧은 만기 재검토.** TTE가 horizon보다 10시간 길어 시간가치를 과다 지불했다. Deribit 일간 만기를 08:00 UTC 직전에 사면 TTE가 짧아진다. 단 P1 신호 시각은 고를 수 없다 |
| D | $0 | 전방 옵션 수집 시작(연 100~150MB). 6개월~2년 후 독립 확인 |

**권고: A.**

실측이 말하는 것은 "시장이 P1 고신뢰 상태를 거의 정확히 공정하게 가격한다"이다.
공정한 시장에서 스프레드와 수수료를 내면 진다. **그것은 튜닝으로 바뀌지 않는다.**

B는 n=5이고, excursion을 잡으려면 그 순간 유동성이 필요한데 그것도 미측정이다.
C는 P1 신호 시각을 고를 수 없으므로 구조적으로 제한된다.

**여덟 번의 방향 연구와 두 번의 수익화 감사가 같은 곳을 가리킨다:
이 신호로 돈을 버는 경로가 보이지 않는다.**
