# CRYPTO BINANCE LIVE · READ-ONLY 실키 재검증 V2

작성: 2026-09-29
키 지문: `487ea08e` (이전 키 `658d48f4`는 `enableFutures=false`로 실패)
판정: **READ_ONLY_PASS**
실주문 0 · 운영 배포 0 · commit 0 · `BINANCE_LIVE_TRADING_ENABLED=false` 유지

---

## 1. 권한 (§2)

`GET /sapi/v1/account/apiRestrictions` 1회 진단 호출. 애플리케이션은 SAPI를 호출할 수 없다(거부목록).

| 항목 | 값 | 기대 | 판정 |
|---|---|---|---|
| enableReading | true | true | OK |
| enableFutures | **true** | true | OK |
| enableWithdrawals | false | false | OK |
| enableSpotAndMarginTrading | false | false | OK |
| enableInternalTransfer | false | false | OK |
| permitsUniversalTransfer | false | false | OK |
| **ipRestrict** | **true** | true | OK |
| enableMargin / PortfolioMargin / VanillaOptions / FixApi | false | - | OK |

기대값 7항목 전부 일치. Trusted IP 제한도 적용돼 있다.

## 2. 인증과 시계 (§4)

`-2015`는 사라졌다. 서명 엔드포인트 전부 200.

| 항목 | 값 |
|---|---|
| 시계 오차 | 시작 +1.63초 → 종료 +0.70초 (recvWindow 5초) |
| `-1021`(시간) | 0건 |
| `-1022`(서명) | 0건 |
| `-2014/-2015`(키·권한·IP) | 0건 |
| 응답 shape 불일치 | 0건 |

## 3. 실제 응답 필드 (실측)

문서·ccxt 기준으로 적었던 필드명이 실제 응답과 일치하는지 이름만 대조했다(값은 기록하지 않는다).

| 엔드포인트 | 실제 필드 |
|---|---|
| `/fapi/v3/account` | totalWalletBalance, totalUnrealizedProfit, totalMarginBalance, totalInitialMargin, totalMaintMargin, totalPositionInitialMargin, totalOpenOrderInitialMargin, totalCrossWalletBalance, totalCrossUnPnl, availableBalance, maxWithdrawAmount, assets, positions |
| `account.assets[]` | asset, walletBalance, unrealizedProfit, marginBalance, maintMargin, initialMargin, positionInitialMargin, openOrderInitialMargin, crossWalletBalance, crossUnPnl, availableBalance, maxWithdrawAmount, updateTime |
| `/fapi/v1/symbolConfig` | symbol, marginType, leverage, maxNotionalValue, isAutoAddMargin |
| `/fapi/v1/commissionRate` | symbol, makerCommissionRate, takerCommissionRate, **rpiCommissionRate**(신규, 무시함) |
| `/fapi/v1/premiumIndex` | symbol, markPrice, indexPrice, estimatedSettlePrice, lastFundingRate, interestRate, nextFundingTime, time |
| `/fapi/v1/ticker/bookTicker` | symbol, bidPrice, bidQty, askPrice, askQty, time, lastUpdateId |
| `/fapi/v1/positionSide/dual` | dualSidePosition |
| **`/fapi/v3/positionRisk`** | **빈 배열.** 포지션을 한 번도 보유한 적 없는 계정은 행 자체가 없다 |

`account.positions`도 0행이다. **포지션 행의 필드명은 아직 실측되지 않았다**(§8 한계).
빈 배열을 flat으로 처리하는 경로는 실측으로 확인됐다.

## 4. 계좌 · 포지션 (§5~§10)

| 항목 | 값 | 출처 |
|---|---|---|
| walletBalance / available / margin / unrealized | 전부 0 USDT | `/fapi/v3/account` |
| assets 행 | 11개 | 같음 |
| BTCUSDT 포지션 | **FLAT** (qty 0, 진입가 null, 청산가 null) | `/fapi/v3/positionRisk` |
| Position Mode | **ONE_WAY** | `/fapi/v1/positionSide/dual` |
| Margin Mode | **CROSSED** | `/fapi/v1/symbolConfig` |
| Leverage | **20x** | `/fapi/v1/symbolConfig` |
| Mark | 83,0xx (soak 구간 82,859.6 ~ 83,121.6) | `/fapi/v1/premiumIndex` |
| Commission | maker 0.02% / **taker 0.05%** | `/fapi/v1/commissionRate` |
| userTrades | 0건 (정상) | `/fapi/v1/userTrades` |
| funding(income) | 0건 (정상) | `/fapi/v1/income` |

**Futures 지갑 0은 정상이다.** 자금은 현물 지갑에 있고, 이번 작업에서 이체는 하지 않았다
(`READ_ONLY CONNECTION PASS / FUTURES BALANCE ZERO`).

미리보기는 계정 실제 taker 0.05%와 Binance 호가를 사용한다(0.002 BTC 기준 왕복 0.1664 USDT,
증거금 = 명목/20). Bybit 수수료 상수는 LIVE 경로에서 import되지 않는다.

## 5. User Data Stream (§12)

| 항목 | 값 |
|---|---|
| listenKey 발급 | 성공 (key_renewals 1) |
| WS 연결 | 성공, 연결 유지 **74.8분** |
| reconnect | **0회** |
| keepalive | **2회 발생** (30분 주기, 실제 동작 확인) |
| 수신 프레임 | 0 (포지션 변화 없는 조용한 계정 - 정상) |
| malformed / error | 0 |

## 6. 30분 READ-ONLY soak (§13)

| 측정 | 값 |
|---|---|
| 실행 시간 | **1799.6초 (30.0분)** |
| 샘플 | **787회, 실패 0회, 성공률 100%** |
| ready | 787/787, stale 0, blocker 0 |
| 응답 지연 | p50 **124ms**, p95 290ms, max 2431ms(1회 튐), min 1.7ms |
| REST 요청 | **2608건**(서명 1028건), 오류 **0건** |
| 429 / 418 | **0건** |
| weight 1분 최대 | **282** (한도 2400, 12%) |
| reconcile | 61회 (30초 slow 티어) |
| **주문 network call** | **0** |

## 7. 주문 차단 실증 (§3, §15)

실키가 정상 연결된 상태에서 로컬 API와 UI로 직접 눌렀다.

| 시도 | 응답 | Binance 호출 |
|---|---|---|
| LONG 0.002 (API) | 409 `LIVE_TRADING_DISABLED` | 없음 |
| SHORT 명목 200 USDT (API) | 409 `LIVE_TRADING_DISABLED` | 없음 |
| CLOSE (API) | 409 `NO_POSITION_TO_CLOSE` | 포지션 재조회만 |
| leverage 5 (API) | 409 `LIVE_TRADING_DISABLED` | 없음 |
| **LONG (UI 확인창 → 실주문 버튼)** | 화면에 거부 메시지 표시 | 없음 |

- `trade_requests: 0`, `endpoints_called`에 `new_order`·`set_leverage` **부재**
- 미러 원장에 `LIVE_ORDER_SENT` **0줄**, `LIVE_ORDER_INTENT` 9줄 / `LIVE_ORDER_REFUSED` 5줄
- Margin Type·Position Mode 변경은 애초에 거부목록이라 라우트 자체가 없다

## 8. UI (§14)

헤드리스 크롬 실제 렌더링으로 확인. 콘솔 오류 0.

기본 PAPER 유지 → BINANCE LIVE 전환 → 실계좌 배지 → 지갑 0.00 USDT → BTCUSDT 포지션 없음 →
교차(Cross)·20x → Mark(Binance) → 수수료율 0.0500% → 양쪽 미리보기 → LONG 확인창 → 실주문 거부 메시지 →
PAPER 복귀 시 기존 페이퍼 값 그대로.

## 9. Paper 무결성 (§16)

로컬 페이퍼 런타임 **14개 파일 sha256 전/후 동일**. 검증은 격리된 scratch 런에서 돌렸고
운영 페이퍼 계좌에는 접근하지 않았다.

## 10. Secret leak (§17)

파일 **51종**(코드·로그·미러·JSON·문서·스크린샷) 검사. 키·시크릿 전체 문자열 0건,
**16자 부분 문자열도 0건**. 응답에 나가는 키 파생값은 지문(`487ea08e`) 하나뿐.

## 11. 회귀 (§18)

crypto pytest **1342 passed** / binance_live **104 passed** / 프론트 vitest **558 passed** /
tsc 0 / eslint 0. 기존 테스트 수정은 없다(신규만 추가).

## 12. 이번 검증 중 고친 것 2가지

1. **차트 호가 혼동**: LIVE 화면인데 차트 스트립이 Bybit bid/ask를 그대로 보여줘서 헤더의 Binance Mark와
   숫자가 달라 보였다. → 차트 위에 **Binance 실호가 스트립**을 넣고 차트 출처를 명시. 테스트 추가.
2. **감사 단계 표기**: 거부 줄에 어느 단계에서 막혔는지가 없었다. → `stage: PLAN / GATE / EXCHANGE`. 테스트 추가.

## 13. 한계 (§21 PARTIAL 아님, 하지만 남은 미확인)

- **포지션 행 필드명 미실측**: 계정이 포지션을 보유한 적이 없어 `positionRisk`가 빈 배열이다.
  진입가·청산가·미실현손익·`breakEvenPrice`의 실제 키는 첫 실포지션 때 확인된다.
- fills·funding도 같은 이유로 shape 미실측(0건).
- 선물 지갑이 비어 있어 증거금·청산가 계산 경로는 실데이터로 검증되지 않았다.
- 차트는 여전히 Bybit(라벨 명시). Binance 캔들 전환은 V1.1 후보.

## 14. 다음 단계 (사용자 승인 필요)

이번 단계는 여기서 멈춘다. `BINANCE_LIVE_TRADING_ENABLED`는 계속 false다.

1. 사용자가 Binance 웹에서 현물 → USDⓈ-M 선물로 소액 이체(코드는 이체를 하지 않는다).
2. 지갑 잔고가 보이는지 read-only로 재확인.
3. **별도 승인 후** 최소 규모 실주문 테스트: LONG → CLOSE → SHORT → CLOSE 순, 각 단계마다
   실제 fill·commission·realized PnL을 `userTrades`와 대조.
4. 그 시점에 포지션 행 필드명도 함께 실측된다.
