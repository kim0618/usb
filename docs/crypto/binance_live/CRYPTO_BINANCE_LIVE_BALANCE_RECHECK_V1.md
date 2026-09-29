# CRYPTO BINANCE LIVE · 이체 후 잔고 반영 확인 V1

작성: 2026-09-29
키 지문: `487ea08e`
판정: **BALANCE_SYNC_PASS** (반영 확인 과정에서 **필드 매핑 결함 1건을 찾아 고쳤다**)
실주문 0 · 이체 API 0 · 운영 배포 0 · commit 0 · `BINANCE_LIVE_TRADING_ENABLED=false` 유지

---

## 1. 권한 재확인 (§3)

`GET /sapi/v1/account/apiRestrictions` 기대값 7항목 전부 일치.
Reading true / Futures true / Withdrawals false / Spot·Margin false / Transfer false / ipRestrict true.

## 2. 이번에 찾은 것: 잔고가 응답 안에 두 개 있다

Binance는 `GET /fapi/v3/account` 한 응답에 **단위가 다른 두 숫자**를 담는다.

| 위치 | 값 | 성질 |
|---|---|---|
| `assets[asset=USDT].walletBalance` | **1.32652900** | **USDT 잔고 그 자체.** 계정이 놀고 있으면 움직이지 않는다 |
| 최상위 `totalWalletBalance` | 1.32573482 → 1.32574637 → 1.32573828 → 1.32574829 | **계정의 USD 환산액.** 거래·수수료·펀딩이 하나도 없는데 읽을 때마다 바뀐다 |

비율은 0.99940 ~ 0.99941로 USDT 페그와 같이 움직인다. 즉 차이는 반올림이 아니라 **단위**다.

**결함**: LIVE 화면은 최상위 값을 "USDT"라고 적어 보여주고 있었다. 그래서
(1) Binance 앱이 보여주는 1.326529와 숫자가 달랐고,
(2) 아무 일도 없는 계좌의 잔고가 몇 초마다 미세하게 흔들렸다.

**수정**: 자산 단위 값(지갑·주문가능·마진·미실현·출금가능·개시/유지증거금)은 **USDT asset 행**에서 읽고,
최상위 USD 환산액은 `account_*_usd`로 이름을 바꿔 화면 각주로 함께 보여준다.
USDT 행이 없으면 **최상위로 대체하지 않고 거부**한다(그 대체가 바로 이 버그였다).
테스트 픽스처의 최상위 값과 asset 행 값을 일부러 다르게 만들어 매핑을 고정했다.

## 3. 잔고 (§4)

| 항목 | 값 (USDT, Binance 정본) |
|---|---|
| walletBalance | **1.32652900** |
| availableBalance | **1.32626371** |
| marginBalance | 1.32652900 |
| unrealized PnL | 0 |
| maxWithdrawAmount | 1.32652900 |
| initial / maint margin | 0 / 0 |
| 계정 USD 환산(참고) | 약 1.325748 USD (페그에 따라 변동) |
| BTCUSDT position | **FLAT** (positionRisk 0행) |
| position mode / margin mode / leverage | ONE_WAY / CROSSED / 20x |
| Mark | 83,026.9 (`/fapi/v1/premiumIndex`) |
| commission | maker 0.02% / taker 0.05% |

사용자가 웹에서 본 1.326529와 **정확히 일치**한다.
`availableBalance`가 지갑보다 0.00026529 적은 것은 Binance가 그렇게 답한 값이며 우리가 계산하지 않는다.

## 4. LIVE UI (§5)

헤드리스 크롬 실렌더링. 콘솔 오류 0.

| 화면 | 값 |
|---|---|
| 지갑 잔고 (BINANCE) | **1.326529 USDT** (≈ 1,783원) |
| 주문가능 | **1.326264 USDT** (≈ 1,782원) |
| 각주 | "잔고는 Binance USDT 잔고(1.326529 USDT)입니다. 계정 USD 환산은 1.325678 USD이며 페그에 따라 조금씩 움직입니다." |
| 포지션 | 보유 없음 |
| 마진/레버리지 | 교차 (Cross) · 20x |
| Mark | 83,026.9 (Binance) |
| 수수료율 | 0.0500% (Maker 0.0200%) |
| LONG/SHORT/CLOSE | 확인창 → 실주문 버튼까지 눌러도 거부 메시지 표시 |

**API 값과 UI 값 일치.** 남은 차이는 상단 요약줄이 2자리로 반올림해 `1.33 USDT`로 보이는 것뿐이다(카드는 6자리).

## 5. PAPER 복귀 (§6)

PAPER로 전환하면 기존 페이퍼 값 그대로. 로컬 페이퍼 런타임 **14개 파일 sha256 전/후 동일**.
검증은 격리된 scratch 런에서 돌렸고 운영 페이퍼 계좌에는 접근하지 않았다.

## 6. 주문·이체 호출 (§7)

| 항목 | 값 |
|---|---|
| new_order | **0** |
| close order | **0** |
| set_leverage | **0** |
| margin type / position mode 변경 | **0** (라우트 자체가 없음, 거부목록) |
| transfer / withdrawal | **0** (`/sapi/` 전체가 구조적으로 호출 불가) |
| 운영자 시도 | 4회, 전부 409 (`LIVE_TRADING_DISABLED` ×3, `NO_POSITION_TO_CLOSE` ×1) |
| 미러 `LIVE_ORDER_SENT` | **0줄** (INTENT 16 / REFUSED 9) |

## 7. Secret leak (§8)

파일 **189종** 검사. 키·시크릿 전체 문자열 0건, 16자 부분 문자열 0건.

## 8. 테스트 (§9)

crypto pytest **1343 passed**, 프론트 vitest **559 passed**, tsc 0, eslint 0.
기존 테스트는 매핑 수정에 따른 픽스처·단언만 갱신했고 신규 3건을 추가했다.

## 9. 다음 단계에 필요한 금액 (계산)

최소 주문은 `LOT_SIZE.minQty = 0.001 BTC`이고, 83,027 기준 명목 **83.03 USDT**다
(`MIN_NOTIONAL = 50`은 충족).

| 항목 | 20x 기준 |
|---|---|
| 개시 증거금 | 4.1513 USDT |
| 왕복 taker 수수료 | 0.0830 USDT |
| **필요 합계** | **약 4.23 USDT** |
| 현재 보유 | **1.3263 USDT** |

**지금 잔고로는 최소 크기 실주문도 불가능하다.** 여유를 두면 **약 5 USDT 이상**이 있어야
0.001 BTC를 20x로 열고 닫을 수 있다(레버리지를 올려 낮출 수는 있으나 첫 테스트에는 권하지 않는다).

## 10. 다음 단계 (사용자 승인 필요)

1. 사용자가 Binance 웹에서 USDT를 추가 이체(약 5 USDT 이상 권장). 코드는 이체하지 않는다.
2. read-only로 잔고 재확인.
3. **별도 승인 후** 최소 규모 실주문: LONG → CLOSE → SHORT → CLOSE, 각 단계마다 실제 fill·commission·
   realized PnL을 `userTrades`와 대조. 이때 `positionRisk` 포지션 행의 실제 필드명도 처음으로 실측된다.

`BINANCE_LIVE_TRADING_ENABLED`는 그때까지 false다.
