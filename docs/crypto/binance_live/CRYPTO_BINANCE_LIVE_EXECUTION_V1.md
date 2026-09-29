# CRYPTO BINANCE LIVE · 최소 규모 실주문 검증 V1

작성: 2026-09-29
키 지문: `487ea08e`
판정: **LIVE_EXECUTION_PARTIAL** (미달 항목은 User Data Stream 하나뿐)
실제 Binance 주문 4건 (상한 4건) · 운영 배포 0 · 이체 0 · 출금 0 · commit 0(본 문서 제외)

이 계정에서 처음으로 실제 주문을 낸 검증이다. 목적은 수익이 아니라, 기존 수동매매 화면의
주문 경로가 실계좌에서 정확하고 안전하게 동작하는지를 최소 크기로 확인하는 것이다.
주문 버튼은 전부 사용자가 직접 눌렀고, 자동 주문·연속 주문·재시도는 없었다.

선행: READ_ONLY_PASS(V2) → BALANCE_SYNC_PASS(V1) → EXECUTION_PRECHECK_PASS → 본 검증

---

## 1. 시작 상태

| 항목 | 값 |
|---|---|
| HEAD | `10ec8721fe20fdc9b83268967ce23425f0eee55f` |
| walletBalance | 374.62997700 USDT |
| availableBalance | 374.55505894 USDT |
| BTCUSDT position | **FLAT** (`positionRisk` 0행) |
| open orders | 0 |
| position mode / margin mode / leverage | ONE_WAY / CROSSED / 20x |
| minQty / stepSize / MIN_NOTIONAL | 0.001 / 0.001 / 50 |
| maker / taker | 0.000200 / 0.000500 |
| 권한 7항목 | 전부 기대값 일치, `ipRestrict=true` |
| clock | 로컬이 약 1.49초 빠름, `sync_clock`이 보정, `-1021` 0건 |

## 2. 주문 게이트

실주문은 두 개의 독립 환경변수로만 열린다. 둘 다 기본 false이고, 오타는 false다.

| 게이트 | 값 |
|---|---|
| `BINANCE_LIVE_TRADING_ENABLED` | true |
| `BINANCE_LIVE_CLIENT_ARMED` | true |
| `armed` (앱이 실제로 읽은 값) | **true** |
| `BINANCE_LIVE_MAX_QTY` | **0.001** |

세 값 모두 검증 서버 **프로세스에만** 주입했다. `.env`는 수정하지 않았고 계속
`BINANCE_LIVE_TRADING_ENABLED="false"`이며 나머지 두 변수는 존재하지 않는다. 따라서 재잠금은
프로세스 종료만으로 성립한다.

`BINANCE_LIVE_MAX_QTY`는 신규 OPEN에만 적용되고 CLOSE는 제한하지 않는다. 이 검증 시점의 LIVE
주문 티켓 기본 수량이 0.002 BTC여서, 상한이 없었다면 수량 칸을 건드리지 않은 클릭 한 번으로
지시된 테스트 크기의 두 배가 체결됐을 것이다.

## 3. 실제 주문 4건

| # | intent | orderId | side | qty | 체결가 | commission | realizedPnl |
|---|---|---|---|---|---|---|---|
| 1 | OPEN | `1149465137574` | BUY | 0.001 | 84,339.90 | 0.04216995 | 0 |
| 2 | CLOSE | `1149467909367` | SELL | 0.001 | 84,391.10 | 0.04219555 | +0.05120000 |
| 3 | OPEN | `1149469756077` | SELL | 0.001 | 84,397.40 | 0.04219870 | 0 |
| 4 | CLOSE | `1149470237949` | BUY | 0.001 | 84,420.10 | 0.04221005 | −0.02270000 |

전부 `MARKET`, 전부 taker(`maker=false`), 실효 수수료율은 네 건 모두 `0.00050000`으로 계정
요율과 일치한다. CLOSE 두 건은 `reduceOnly=true`로 나갔고, 수량은 클릭 직전 Binance에서 다시
읽은 실제 `positionAmt`다(로컬 캐시 미사용). clientOrderId는 전부 `usbm-` 접두사다.

`trade_requests: 4`, 고유 orderId 4개, `userTrades` 4행. 상한 4건을 지켰다.

## 4. 회계 항등식

### LONG 왕복

| 항목 | 값 |
|---|---|
| gross price PnL | (84,391.10 − 84,339.90) × 0.001 = **0.05120000** |
| Binance realizedPnl | **0.05120000** (일치) |
| 수수료 합 | 0.08436550 |
| 순손익 | −0.03316550 |
| wallet | 374.62997700 → 374.59681150 |
| 관측된 변화 | **−0.03316550** (일치) |

### SHORT 왕복

| 항목 | 값 |
|---|---|
| gross price PnL | (84,397.40 − 84,420.10) × 0.001 = **−0.02270000** |
| Binance realizedPnl | **−0.02270000** (일치) |
| 수수료 합 | 0.08440875 |
| 순손익 | −0.10710875 |
| wallet | 374.59681150 → 374.48970275 |
| 관측된 변화 | **−0.10710875** (일치) |

### 전체

| 항목 | 값 |
|---|---|
| realized PnL 합 | +0.02850000 |
| commission 합 | −0.16877425 |
| 합계 | **−0.14027425 USDT** |
| wallet 374.62997700 → 374.48970275 | 변화 −0.14027425 (일치) |
| `income` 합계 | −0.14027425 (일치) |
| funding | 0건 |

`userTrades`, `income`, `account`의 세 경로가 모두 같은 값을 준다.

## 5. positionRisk 실제 shape (첫 실측)

`GET /fapi/v3/positionRisk`가 20개 필드를 보낸다. 모델이 읽는 14개는 전부 존재하고 정상
파싱된다. **shape mismatch 0건, 코드 수정 불필요.**

| 상황 | 값 | 모델 처리 |
|---|---|---|
| LONG | `positionAmt: '0.001'` | `side=LONG, qty=0.001, signed_qty=0.001` |
| LONG | `liquidationPrice: '0'` | **`None`** (0원 청산가로 표시하지 않음) |
| SHORT | `positionAmt: '-0.001'` | `side=SHORT, qty=0.001, signed_qty=-0.001` |
| SHORT | `liquidationPrice: '457123.15444087'` | 실제 값 그대로 파싱 |
| SHORT | `notional: '-84.398...'` | 음수 보존 |

`'0'` → `None` 경로와 실제 청산가 경로가 양쪽 다 실측으로 확인됐다.
V3 행에는 `leverage`와 `marginType`이 **없다**. 앱이 이 둘을 `GET /fapi/v1/symbolConfig`에서
읽는 설계가 맞다는 것도 확인됐다.

모델이 읽지 않는 잉여 필드 6개: `askNotional`, `bidNotional`, `isolatedWallet`, `marginAsset`,
`openOrderInitialMargin`, `positionInitialMargin`. 무해하다.

## 6. PAPER ↔ LIVE 분리

LONG 보유 중 1회, SHORT 보유 중 1회, 각각 `LIVE → PAPER → LIVE` 왕복.

- PAPER 재무 19개 항목 **전부 불변**. `ledger.jsonl` sha256 `bee6d45a7478b221…` 동일, event count 1 동일, reset_count 0
- 실계좌 포지션이 PAPER 화면에 나타나지 않음 (`position_side=None`)
- LIVE 재전환 시 포지션·진입가가 Binance 재조회값과 일치 (로컬 캐시 복원 아님)
- 공개 시세 tape만 851건 증가. 재무 필드가 아니다

검증은 격리된 scratch 페이퍼 런에서 돌렸고 운영 페이퍼 계좌에는 접근하지 않았다.

## 7. AUTO 격리

- 주문 4건 전부 `usbm-` 접두사(수동 터미널)
- `research/**`, `paper/**` 중 `live.orders`를 import하는 모듈 **0개**
- 실행 중 세션의 `auto_available: false`
- AUTO 주문 **0건**

`LiveOrderRouter.submit`에 닿는 코드 경로는 `POST /api/crypto/binance/order` 라우트 하나뿐이다.

## 8. LIVE mirror

`LIVE_ORDER_INTENT` 8 (PLAN + SUBMIT ×4) · `LIVE_ORDER_SENT` 4 · `LIVE_ORDER_RESULT` 4 ·
`LIVE_RECONCILE` 30 · `LIVE_STREAM_STATE` 1. 오류·거부 0건. 키·시크릿·IP 0건.

## 9. 미달 항목: User Data Stream

**네 번의 실체결에 프레임 0건.** `messages: 0`, `account_events: 0`, `last_event_type: null`.

앱 결함인지 가리기 위해 앱과 무관한 독립 리스너를 붙여 비교했다. 같은 URL 형식
(`wss://fstream.binance.com/ws/<listenKey>`)에 자체로 발급받은 키를 쓰는 40줄짜리 클라이언트다.

- 리스너 CONNECTED 1790687941322 → idle 1790688001 → **CLOSE 체결 1790688020634** → idle 1790688031.
  그 사이 프레임 없음
- LONG 진입(1790687792)은 앱 소켓이 유일한 연결이던 시점인데 그것도 놓쳤다. 소켓 공유 문제가 아니다
- 소켓 자체는 정상: ping/pong 유지, `last_error: null`, 재연결 0, `has_listen_key: true`

**따라서 앱 코드 결함이 아니다.** 원인은 아직 규명되지 않았다.

실질 영향은 제한적이다. 설계상 stream은 `CHANGE_SIGNAL_ONLY`이고 잔고·포지션의 정본은 REST다.
이번 검증에서 `LIVE_RECONCILE` 30건이 정상 동작했고 모든 수치가 Binance와 일치했다. 남는 영향은
**체결 직후 화면 반영이 즉시가 아니라 다음 폴링 주기까지 지연될 수 있다**는 것뿐이다.

## 10. 종료 상태

| 항목 | 값 |
|---|---|
| BTCUSDT position | **FLAT** (`positionRisk` 0행) |
| open orders | **0** |
| walletBalance | **374.48970275 USDT** |
| availableBalance | 374.41481034 USDT |
| position mode / margin mode / leverage | ONE_WAY / CROSSED / 20x (전부 미변경) |
| API 권한 | 7항목 그대로, Withdrawal false 유지 |

## 11. 재잠금

무장 서버·진단 리스너·프론트엔드 전부 종료. `.env` 미변경.
새 프로세스 기준 `trading_enabled=false`, `client_armed=false`, `max_open_qty=None`, **`armed=false`**.
실거래 가능 상태는 프로세스 종료 후 남아 있지 않다.

## 12. 시크릿 감사

파일 1,606종(추적 파일 + 미러 + 로그 + 산출물) + 프론트 응답 6종 검사.
API key·secret 전체 문자열, 앞 16자, 뒤 16자, 공인 IP 전체값, KIWOOM/MASSIVE 키까지 **전부 0건**.

## 13. 회귀

binance_live **119 passed** · crypto 회귀 **507 passed, 23 skipped** · frontend vitest 557/558 ·
tsc 0 errors · eslint 0 errors.

vitest 실패 1건은 `compactEquityUsd`가 `$8K`를 기대하나 이 PC의 Node 20.20.2 / ICU 78.2가
`$8.0K`를 내는 ICU 버전 차이로, crypto와 무관하다. 연구 계약 스윗은 `data/runtime/` 산출물이
이 PC에 없어 제외했다.

## 14. 이번 검증 중 코드 변경

**없다.** `positionRisk` 실측 shape가 모델과 정확히 맞아 수정이 필요 없었다.

## 15. 한계

- User Data Stream 프레임 미수신, 원인 미규명 (§9)
- 실측된 것은 0.001 BTC 단일 크기, 20x CROSSED, ONE_WAY 조합뿐이다. 부분 체결, 큰 수량,
  ISOLATED, 헤지 모드, 청산 근접 상황은 이번에 전혀 다뤄지지 않았다
- 실제 청산가는 SHORT 1회만 관측됐다 (LONG은 잔고 대비 명목이 작아 `'0'`)
- funding이 발생하지 않는 짧은 구간이어서 LIVE funding 경로는 여전히 미실측이다
- `GET /fapi/v1/openOrders`는 엔드포인트 레지스트리에 없어 앱 밖 진단 호출로 확인했다

## 16. 다음 단계

1. **User Data Stream 진단** (우선순위 1). 주문 정본이 REST라는 원칙은 그대로 유지한다
2. **leverage / margin 정책 검토** (우선순위 2). 현재 20x CROSSED는 계정의 현재 설정일 뿐
   실전 기본값으로 확정된 것이 아니다
3. 다음 세션은 회사 PC에서 진행한다. 공인 IP가 달라지므로 Trusted IP부터 다시 확인한다
