# CRYPTO BTC-VOL-P0 OPTIONS MARKET RESEARCH V1

조사일 **2026-09-28**. 모든 사실에 출처 URL과 확인 방법을 적었다.

| 표기 | 의미 |
|---|---|
| **직접확인** | 이 세션에서 해당 URL을 직접 가져와 본문을 읽음 |
| 간접 | 검색엔진이 같은 공식 URL을 가져와 요약한 것만 확인. 직접 가져오기는 차단됨 |
| **UNKNOWN** | 공식 출처에서 확인하지 못함. **추측하지 않음** |

---

## 0. 조사 방법과 그 한계

`support.deribit.com`은 봇 차단(HTTP 403)으로 직접 접근이 불가했고,
`bybit.com/help-center`는 타임아웃, `okx.com`의 수수료 표는 JS 렌더링이라 본문이 안 나온다.

**따라서 Deribit·Bybit·OKX의 수수료와 계약 명세는 대부분 간접 확인이다.**
이 문서는 그것을 숨기지 않고 항목마다 표시한다.

> **다만 이번 감사의 결론은 이 불확실성에 영향받지 않는다.**
> 확인 못 한 항목은 전부 **비용**이고, 비용은 매수자를 나쁘게만 만든다.
> 손익분기 계산이 이미 수수료 이전에 음수이므로(§MONETIZATION_AUDIT §3),
> 수수료가 정확히 얼마인지는 판정을 바꾸지 않는다.

---

## 1. 한국 접근성 (가장 중요)

| 거래소 | 한국 관련 공식 문구 | 확인 |
|---|---|---|
| **OKX** | **"South Korea (specifically regarding our derivatives-related and P2P services)"** - 파생상품 서비스 제한 대상으로 **명시** | **직접확인** [risk-compliance-disclosure](https://www.okx.com/en-us/help/risk-compliance-disclosure) |
| Bybit | 공식 Service Restricted Countries 목록에 한국 **없음**(미국·중국본토·홍콩·싱가포르·캐나다 등) | 간접 (help-center 타임아웃) |
| Deribit | 공식 Restricted Jurisdictions 목록에 한국 **없음**(북한·일본 등은 있음) | 간접 (403 차단) |
| Binance | 옵션·파생상품에 대한 한국 관련 공식 문구를 **찾지 못함** | **UNKNOWN** |

**OKX는 옵션 연구 대상에서 제외한다.** 옵션은 파생상품이고 공식 문서가 한국 제한을 명시한다.

Bybit·Deribit의 "목록에 없음"은 **접근 가능하다는 확인이 아니다.** 직접 읽지 못했고,
목록 부재가 허용을 뜻하지도 않는다. 실제 이용 전 계정 단계에서 확인이 필요하다.

Binance는 사용자가 이미 계정을 만든 곳인데, **옵션에 대한 한국 관련 공식 문구를 찾지 못했다.**
2021년 "Changes to Product Offerings in Korea" 공지가 존재하나 원화 페어·P2P·언어 지원에 관한 것이고
옵션·파생상품을 다루지 않는다. **UNKNOWN으로 남긴다.**

---

## 2. BTC 옵션 제공 현황

| 거래소 | 제공 | 스타일 | 정산 | 담보/표시 | 확인 |
|---|---|---|---|---|---|
| Binance | 예 | 유럽식 | 현금 | **USDT** | **직접확인** [eoptions/home](https://www.binance.com/en/eoptions/home), [API docs](https://developers.binance.com/docs/derivatives/options-trading/general-info) |
| Bybit | 예 | 유럽식 | 현금 | **USDC** | 간접 |
| Deribit | 예 | 유럽식 | 현금 | BTC(인버스) + **USDC 선형**(2025-08 추가) | 간접 (403) |
| OKX | 예 | 유럽식 | 현금 | BTC 또는 USD/USDC | 간접 - **한국 제한으로 제외** |

만기 구조는 네 곳 모두 **일간(daily)이 최단**이다.
Binance 일간 옵션은 3일 거래수명, 08:00 UTC 만기.

---

## 3. 수수료

| 거래소 | 메이커 | 테이커 | 프리미엄 상한 | 정산/행사 | 확인 |
|---|---|---|---|---|---|
| **Binance** | **0.0240%** | **0.0240%** | **표시 없음** | 0.015%(간접) | **직접확인** [fee/optionsTrading](https://www.binance.com/en/fee/optionsTrading) |
| Binance 프로모션 | **0.000%** | **0.020%** | - | - | **직접확인** (2025-08-04 이후 상장 계약) |
| Bybit | 0.02% | 0.03% | **7% of premium** | 0.015% | 간접 |
| Deribit | 0.03% | 0.03% | **12.5% of premium** | - | 간접 (403). **2026-08-01 신규 수수료표 시행 공지 확인, 신규 요율은 이미지라 판독 불가** |
| OKX | 0.02% | 0.03% | 7% of premium | - | 간접 - 제외 |

**Binance 페이지에 프리미엄 상한 표기가 없다.** 이것은 짧은 만기 옵션에서 중요하다:
프리미엄이 작은데 수수료가 명목가 기준이면 **프리미엄 대비 수수료 비율이 커진다.**

4시간 ATM 스트래들(비용 약 2.18% of spot, 양다리)에서
명목가 0.024% × 2다리 = **0.048% of spot**이고, 이는 프리미엄의 약 **2.2%**다.
상한이 없으면 더 짧은 만기에서 이 비율이 급격히 오른다.

---

## 4. 옵션 체인 API (IV·greeks)

| 거래소 | 엔드포인트 | IV | Greeks | 확인 |
|---|---|---|---|---|
| Binance | `GET /eapi/v1/mark` | bidIV·askIV·markIV | delta·theta·gamma·vega | 간접(공식 docs) |
| Bybit | v5 `/market/tickers` (category=option) | markIv | delta·gamma·vega·theta | 간접(공식 docs) |
| Deribit | `public/get_order_book` | bid_iv·ask_iv·mark_iv | delta·gamma·theta·vega·rho | 간접(공식 docs) |
| OKX | WS `option summary channel` | 있음 | 있음 | 간접 - 제외 |

**네 곳 모두 실시간 IV와 greeks를 공개 API로 제공한다.** 전방 수집에 기술적 장벽은 없다.

---

## 5. 과거 옵션 데이터 (이번 감사의 분수령)

### 5.1 Binance 공식 아카이브 - **직접확인, 그리고 결정적으로 짧다**

`data.binance.vision`의 S3 버킷을 직접 조회했다.

| 경로 | 내용 |
|---|---|
| `data/option/daily/EOHSummary/BTCUSDT/` | 시간별 옵션 체인 요약 |
| `data/option/daily/BVOLIndex/BTCBVOLUSDT/` | 변동성 지수 |

**EOHSummary 컬럼(직접 다운로드해 확인):**

```
date, hour, symbol, underlying, type, strike, open, high, low, close,
volume_contracts, volume_usdt, best_bid_price, best_ask_price,
best_bid_qty, best_ask_qty, best_buy_iv, best_sell_iv,
mark_price, mark_iv, delta, gamma, vega, theta, openinterest
```

**필요한 필드가 전부 있다.** 시간별 체인, 호가, IV 3종, greeks 4종.

**그런데 커버리지가 147일이다.**

| 항목 | 값 |
|---|---|
| EOHSummary BTCUSDT 파일 수 | **147** |
| 구간 | **2023-05-18 ~ 2023-10-23** |
| `IsTruncated` | **false** (목록이 전부라는 뜻) |
| BVOLIndex 구간 | 2023-06-20 ~ 2024-11-05 (지수만, 체인 아님) |

### 5.2 그 147일에 P1 신호가 몇 개 있나

P1 예측을 이 구간으로 잘라 직접 세었다.

| 조합 | p>=0.60 | p>=0.70 | **p>=0.75** |
|---|---|---|---|
| 4H ±1% | 9 episodes | 1 | **1** |
| 12H ±1% | 64 episodes | 18 | **5** |
| 12H ±2% | 3 episodes | 1 | **1** |
| 24H ±2% | 13 episodes | 1 | **1** |

**고신뢰 신호가 조합당 1~5개다.** 2023년이 저변동성 구간이었기 때문에
P1 고신뢰 상태 자체가 거의 발생하지 않았다.

> **무료 공식 데이터로는 R1을 검증할 수 없다.** 표본이 없다.
> 유일하게 숫자가 있는 칸은 12H ±1%의 p>=0.60(64개)인데, 그것은 훨씬 약한 게이트다.

### 5.3 다른 거래소

| 거래소 | 자체 과거 옵션 데이터 |
|---|---|
| Deribit | `history.deribit.com` REST로 플랫폼 개설 이래 체결 이력 (간접 확인). **벌크 덤프는 확인 못 함** |
| **OKX** | **없음** - 공식 historical-data 페이지에 옵션이 **없다**(현물·선물·펀딩·호가만). 간접이나 페이지 자체는 명시적 |
| Bybit | 자체 아카이브 **확인 못 함**. 실시간 창 REST만 발견 |

### 5.4 유료 벤더

| 벤더 | 공개 가격 | BTC 옵션 커버리지 |
|---|---|---|
| **Tardis.dev** | **공개됨**: Academic $350-650/월, Solo $700-1,200/월, Professional $1,000-2,200/월, Business $3,000-6,000/월 | Binance·OKX·Deribit·Bybit 옵션 요약(greeks·IV·최우선호가) |
| Amberdata | **비공개** (contact sales) | Deribit 옵션 2021-05-21부터, 호가 2024-03-31부터 |
| Kaiko | **비공개** (custom) | 옵션 전용 상품 **UNKNOWN** |

**이번 단계에서 구매하지 않는다**(§26). 가격과 커버리지만 기록한다.

---

## 6. 변동성 상품

| 상품 | 거래 가능 여부 |
|---|---|
| **Deribit DVOL 선물** | **거래 가능** - USDC 현금정산, 만기가 DVOL 지수 60분 TWAP, 2023-03-27 상장 (간접, 403 차단) |
| Binance BVOL | **지수 전용.** 거래 가능한 BVOL 선물의 근거를 찾지 못함 |
| Bybit / OKX 변동성 선물 | **찾지 못함** (없다고 확정하지는 않음) |

**DVOL 선물이 유일하게 확인된 직접 변동성 상품이다.**
다만 Deribit 접근성이 간접 확인이고, DVOL은 30일 지수라 P1의 4~24시간 horizon과 맞지 않는다.

---

## 7. 데모/테스트넷

| 거래소 | 옵션 데모 |
|---|---|
| Binance | `demo-fapi.binance.com`. 옵션 완전 지원 여부 **UNKNOWN**(futures 네임스페이스 공유) |
| Bybit | 있음. `api-demo.bybit.com`, USDC 옵션 지원. WS Trade API·IV 주문수정은 미지원 |
| Deribit | 있음. `test.deribit.com` |
| OKX | 있음(옵션 포함) - 제외 |

---

## 8. 조사 중 나온 부수 사실

**Deribit은 2025-08-14에 Coinbase 인수가 완료됐다**(Coinbase 블로그 및 SEC 10-Q).
"네 개의 독립 거래소"라는 전제가 실제로는 셋에 가깝다.

---

## 9. UNKNOWN으로 남긴 것

| 항목 | 이유 |
|---|---|
| Binance 옵션의 한국 접근 가능 여부 | 공식 문구를 찾지 못함 |
| Binance 옵션 수수료의 프리미엄 상한 존재 여부 | 수수료 페이지에 표기 없음. 없다고 확정하지는 않음 |
| Deribit 2026-08-01 이후 수수료 요율 | 공지 확인했으나 요율이 이미지 |
| Bybit BTC 옵션 최소 주문단위 | 공식 페이지를 찾지 못함 |
| Bybit 자체 과거 옵션 아카이브 | 찾지 못함 |
| Kaiko의 BTC 옵션 커버리지 | 확인 못 함 |

**추측으로 채우지 않았다.**
