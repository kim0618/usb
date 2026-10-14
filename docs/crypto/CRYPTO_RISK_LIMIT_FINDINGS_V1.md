# US-B CRYPTO D2 Risk Limit 실측 결과 V1

작성 2026-09-23. 이 문서는 **계약이 아니라 증거**다. Bybit Mainnet PUBLIC API
`GET /v5/market/risk-limit?category=linear&symbol=BTCUSDT`만 사용해 직접 측정한 값을 적는다.
계정·API key·private endpoint는 사용하지 않았다.

- 원시 응답: `data/runtime/crypto/BTCUSDT/reference/risk_limit_1790128376.json` (gitignore 대상)
- 원시 응답 sha256: `cf4b90de8d2f2895a6984795299e1e13eac1f3d0f556b35ce14233118bf35818`
- 측정 시각: 2026-09-23 01:52 UTC
- 파서: `backend/app/crypto/models.py::parse_risk_tiers` / `validate_risk_tiers`
- 재현: `PYTHONPATH=backend .venv/bin/python -m app.dev.collect_crypto_d2 risk`

---

## 1. 현재 API 스키마

`result.list[]`의 필드는 8개다.

| 필드 | 타입 | 비고 |
|---|---|---|
| `id` | int | riskId. 1부터 연속 증가 |
| `symbol` | string | `BTCUSDT` |
| `riskLimitValue` | string(decimal) | 해당 tier의 **상한 notional(USDT)** |
| `initialMargin` | string(decimal) | IM 비율 |
| `maintenanceMargin` | string(decimal) | MM 비율 |
| `maxLeverage` | string(decimal) | 소수 2자리 |
| `mmDeduction` | string(decimal) | tier 1은 **빈 문자열**, tier 2부터 숫자 |
| `isLowestRisk` | int | tier 1만 `1`, 나머지 `0` |

> `mmDeduction`이 빈 문자열로 오는 tier가 있다. 숫자로 강제 변환하면 tier 1에서 깨진다.
> 파서는 빈 문자열을 `None`으로 보존한다.

---

## 2. 전체 tier 표 (35개, 전수)

`lowerExclusive`는 API가 주지 않는다. 직전 tier의 `riskLimitValue`로 **파생**한 값이며
구간 해석은 `(lowerExclusive, riskLimitValue]`다.

| riskId | lowerExclusive (USDT) | riskLimitValue (USDT) | IM | MM | maxLeverage | mmDeduction | isLowestRisk |
|---|---|---|---|---|---|---|---|
| 1 | 0 | 300,000 | 0.0066 | 0.0033 | 150.00 | (없음) | 1 |
| 2 | 300,000 | 2,000,000 | 0.01 | 0.005 | 100.00 | 510 | 0 |
| 3 | 2,000,000 | 2,600,000 | 0.0111 | 0.0056 | 90.00 | 1710 | 0 |
| 4 | 2,600,000 | 3,200,000 | 0.0125 | 0.0063 | 80.00 | 3530 | 0 |
| 5 | 3,200,000 | 3,800,000 | 0.0133 | 0.0067 | 75.00 | 4810 | 0 |
| 6 | 3,800,000 | 4,400,000 | 0.0143 | 0.0071 | 70.00 | 6330 | 0 |
| 7 | 4,400,000 | 5,000,000 | 0.0154 | 0.0077 | 65.00 | 8970 | 0 |
| 8 | 5,000,000 | 5,600,000 | 0.0182 | 0.0091 | 55.00 | 15970 | 0 |
| 9 | 5,600,000 | 8,500,000 | 0.02 | 0.01 | 50.00 | 21010 | 0 |
| 10 | 8,500,000 | 10,000,000 | 0.0222 | 0.013 | 45.00 | 46510 | 0 |
| 11 | 10,000,000 | 14,000,000 | 0.025 | 0.015 | 40.00 | 66510 | 0 |
| 12 | 14,000,000 | 20,000,000 | 0.0286 | 0.016 | 35.00 | 80510 | 0 |
| 13 | 20,000,000 | 28,000,000 | 0.0333 | 0.02 | 30.00 | 160510 | 0 |
| 14 | 28,000,000 | 38,000,000 | 0.04 | 0.025 | 25.00 | 300510 | 0 |
| 15 | 38,000,000 | 50,000,000 | 0.05 | 0.029 | 20.00 | 452510 | 0 |
| 16 | 50,000,000 | 55,000,000 | 0.0526 | 0.03 | 19.00 | 502510 | 0 |
| 17 | 55,000,000 | 60,000,000 | 0.0556 | 0.035 | 18.00 | 777510 | 0 |
| 18 | 60,000,000 | 65,000,000 | 0.0588 | 0.036 | 17.00 | 837510 | 0 |
| 19 | 65,000,000 | 70,000,000 | 0.0625 | 0.038 | 16.00 | 967510 | 0 |
| 20 | 70,000,000 | 75,000,000 | 0.0667 | 0.04 | 15.00 | 1107510 | 0 |
| 21 | 75,000,000 | 80,000,000 | 0.0714 | 0.045 | 14.00 | 1482510 | 0 |
| 22 | 80,000,000 | 85,000,000 | 0.0769 | 0.05 | 13.00 | 1882510 | 0 |
| 23 | 85,000,000 | 90,000,000 | 0.0833 | 0.055 | 12.00 | 2307510 | 0 |
| 24 | 90,000,000 | 95,000,000 | 0.0909 | 0.06 | 11.00 | 2757510 | 0 |
| 25 | 95,000,000 | 100,000,000 | 0.1 | 0.065 | 10.00 | 3232510 | 0 |
| 26 | 100,000,000 | 105,000,000 | 0.1111 | 0.07 | 9.00 | 3732510 | 0 |
| 27 | 105,000,000 | 110,000,000 | 0.125 | 0.085 | 8.00 | 5307510 | 0 |
| 28 | 110,000,000 | 115,000,000 | 0.1429 | 0.095 | 7.00 | 6407510 | 0 |
| 29 | 115,000,000 | 120,000,000 | 0.1667 | 0.1 | 6.00 | 6982510 | 0 |
| 30 | 120,000,000 | 150,000,000 | 0.2 | 0.12 | 5.00 | 9382510 | 0 |
| 31 | 150,000,000 | 200,000,000 | 0.25 | 0.14 | 4.00 | 12382510 | 0 |
| 32 | 200,000,000 | 250,000,000 | 0.3333 | 0.18 | 3.00 | 20382510 | 0 |
| 33 | 250,000,000 | 400,000,000 | 0.5 | 0.3 | 2.00 | 50382510 | 0 |
| 34 | 400,000,000 | 800,000,000 | 0.6993 | 0.42 | 1.43 | 98382510 | 0 |
| 35 | 800,000,000 | 1,200,000,000 | 1 | 0.6 | 1.00 | 242382510 | 0 |

---

## 3. 검증 결과

| 검사 | 결과 | 근거 |
|---|---|---|
| tier ordering | **PASS** | `id`가 1..35 연속, 상한이 단조 증가, 각 tier의 상한 > 하한 |
| margin monotonicity | **PASS** | IM·MM 모두 tier가 올라갈수록 단조 비감소. 역전 0건 |
| max leverage consistency | **PASS** | maxLeverage는 단조 비증가. 150.00 -> 1.00 |
| IM >= MM | **PASS** | 35개 tier 전부 |
| tier 경계 연속성 | **PASS** | 구간 사이 빈틈·겹침 0 |
| D1 instrument metadata 일치 | **PASS** | D1 `leverageFilter.maxLeverage = 150.00` = tier 1 maxLeverage `150.00` |

### 3.1 IM x maxLeverage 가 정확히 1이 아니다

| tier | IM | maxLeverage | IM x maxLeverage | 편차 |
|---|---|---|---|---|
| 1 | 0.0066 | 150.00 | 0.9900 | **0.0100** |
| 12 | 0.0286 | 35.00 | 1.0010 | 0.0010 |
| 34 | 0.6993 | 1.43 | 0.999999 | 0.000001 |

최대 편차는 tier 1의 **0.01**이다. `IM = 1 / maxLeverage`로 가정하면 tier 1에서 151.5x가 나오지만
공시 maxLeverage는 150.00이다. **두 값 중 어느 쪽이 체결 판정의 authority인지는 공개 endpoint만으로
결정할 수 없다. UNKNOWN으로 남긴다.** 파서는 편차 0.011까지 허용하고 그 밖은 실패시킨다.

### 3.2 instrument maxOrderQty와의 관계

D1의 `lotSizeFilter.maxOrderQty = 1500.000` BTC다. D1 실측 가격대(약 86,000 USDT) 기준
단일 주문 최대 notional은 약 1.29억 USDT로, tier 30(1.2억 초과 ~ 1.5억) 구간에 들어간다.
tier 35의 상한 12억 USDT는 단일 주문으로는 도달할 수 없고 누적 포지션으로만 도달한다.

---

## 4. Paper liquidation engine을 위한 schema contract

**이 절은 계약이다. 엔진은 여기서 구현하지 않는다.** D3 이후 paper liquidation을 만들 때
아래 계약만 지키면 이 문서의 실측값으로 재현할 수 있다.

```
RiskTier:
  risk_id            int        # 1-based, 연속
  lower_exclusive    Decimal    # 파생값. tier N-1의 riskLimitValue (tier 1은 0)
  upper_inclusive    Decimal    # riskLimitValue
  initial_margin     Decimal
  maintenance_margin Decimal
  max_leverage       Decimal
  mm_deduction       Decimal | None   # tier 1은 None
```

1. **tier 선택**: 포지션 notional `N`에 대해 `lower_exclusive < N <= upper_inclusive`인 tier를 고른다.
   경계값은 **아래 tier에 속한다**(상한 포함).
2. **Decimal only**: 모든 값은 문자열에서 `Decimal`로 읽는다. float 경유 금지.
3. **mmDeduction**: 빈 문자열은 `None`이며 `0`이 아니다. 공식에 넣을 때 `None`과 `0`을 구분한다.
   Bybit의 계단식 MM 공식은 tier 2 이상에서 `mmDeduction`을 차감항으로 쓴다. **정확한 공식은
   공개 문서로 확인하지 못했다. UNKNOWN.**
4. **스냅샷 고정**: risk tier는 거래소가 바꿀 수 있다. paper run은 시작 시각의 tier 테이블을
   run parameter로 고정하고, run 중 재조회하지 않는다. 원시 응답과 sha256을 run artifact에 남긴다.
5. **과거 재현 불가**: 과거 시점의 tier 테이블은 공개 endpoint에 없다. 과거 구간 backtest에
   현재 tier를 적용하면 그 사실을 결과에 명시한다.

---

## 5. 위험과 UNKNOWN

| # | 항목 | 상태 |
|---|---|---|
| RL1 | IM x maxLeverage != 1 (tier 1 편차 0.01). 어느 쪽이 authority인지 | **UNKNOWN** |
| RL2 | `mmDeduction`을 쓰는 정확한 MM 공식 | **UNKNOWN**. 공개 응답만으로는 도출 불가 |
| RL3 | tier 테이블 변경 이력 | **UNKNOWN**. 공개 endpoint는 현재 스냅샷만 준다 |
| RL4 | auto-margin/cross vs isolated에 따른 tier 적용 차이 | **UNKNOWN**. private endpoint 없이 확인 불가 |
| RL5 | 과거 구간 backtest에 현재 tier를 쓰는 것 | 명시적 수용. 결과에 반드시 기재 |
