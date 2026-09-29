# CRYPTO BINANCE LIVE MANUAL V1 · 구조

버전: V1 (읽기 전용)
작성: 2026-09-29
대상: `BTCUSDT` USDⓈ-M Perpetual 하나
상태: **READY_FOR_EXECUTION_VALIDATION** (실주문 0건, 운영 배포 0건, `BINANCE_LIVE_TRADING_ENABLED=false`)

> 이 커밋의 기준 상태다. 실전 사용 승인이 아니다.
> 완료: READ_ONLY_PASS(30분 soak 787샘플 무결점) · BALANCE_SYNC_PASS(실잔고 1.326529 USDT 반영 확인).
> 남은 것: 최소 실주문 검증(LONG → CLOSE → SHORT → CLOSE)과 fill·commission·realized PnL·positionRisk 필드 실측.
>
> **깨지면 FAIL인 불변조건** (다음 단계에서 매번 확인한다)
> 1. PAPER와 LIVE는 같은 화면을 쓰지만 계좌 상태는 완전히 별개다.
> 2. LIVE 거래가 PAPER 지갑·포지션·원장을 바꾸면 FAIL.
> 3. PAPER 거래가 Binance 계좌를 바꾸면 FAIL.
> 4. PAPER → LIVE → PAPER 전환 시 기존 PAPER 상태가 그대로 복원돼야 한다.
> 5. LIVE → PAPER → LIVE 전환 시 Binance 실제 상태를 다시 읽어 그대로 보여야 한다.
> 6. AUTO는 Binance LIVE 주문 경로와 계속 분리돼 있어야 한다.
>
> **다음 단계**: 빗썸 출금 제한 해제 후 USDT 이동 → 현물에서 USDⓈ-M으로 수동 이체 → LIVE 잔고 재확인 →
> **별도 승인 후** 최소 실주문 검증. 최소 주문 0.001 BTC는 20x 기준 약 4.23 USDT가 필요하다
> (`CRYPTO_BINANCE_LIVE_BALANCE_RECHECK_V1.md` §9).

---

## 1. 한 화면, 두 계좌

새 LIVE 페이지를 만들지 않았다. 기존 수동매매 화면(`/crypto-paper`) 상단에 계좌 전환만 붙었다.

```
기존 수동매매 UI
   └─ AccountSourceSwitch  [PAPER] [BINANCE LIVE]
         ├─ PAPER        → PaperSession (기존 엔진·원장·라우트 그대로)
         └─ BINANCE LIVE → BinanceLiveAdapter → Binance USDⓈ-M REST/WS
```

기본값은 PAPER다. BINANCE LIVE 버튼은 백엔드가 "키가 있다"고 답할 때만 눌린다.
두 계좌가 동시에 렌더링되는 경로는 없다.

## 2. 파일

| 경로 | 역할 |
|---|---|
| `backend/app/crypto/live/__init__.py` | 패키지 계약 3줄(격리·비참조·이중 게이트) |
| `backend/app/crypto/live/credentials.py` | 환경변수만 읽는 키·플래그, 지문, redact |
| `backend/app/crypto/live/endpoints.py` | 호출 가능한 엔드포인트 **화이트리스트 + 거부목록** |
| `backend/app/crypto/live/signing.py` | HMAC SHA256 서명(서명한 문자열을 그대로 전송) |
| `backend/app/crypto/live/rest.py` | REST 클라이언트, TRADE 게이트, 시계 보정, 텔레메트리 |
| `backend/app/crypto/live/filters.py` | `exchangeInfo` 기반 수량 규칙 |
| `backend/app/crypto/live/models.py` | 응답 정규화(누락 필드는 예외, 기본값 없음) |
| `backend/app/crypto/live/account.py` | 스냅샷 1회 읽기 + LIVE 가능/불가 판정(blocker) |
| `backend/app/crypto/live/preview.py` | Binance 호가를 걸어서 만드는 왕복 비용 미리보기 |
| `backend/app/crypto/live/orders.py` | LONG/SHORT/CLOSE·레버리지, 이중 게이트, reverse 금지 |
| `backend/app/crypto/live/mirror.py` | LIVE 감사 원장(페이퍼 원장과 물리적으로 분리) |
| `backend/app/crypto/live/stream.py` | User Data Stream = **변경 신호 전용** |
| `backend/app/crypto/live/adapter.py` | `ManualTradingAdapter` 프로토콜 + Paper/Live 구현 |
| `backend/app/crypto/terminal/live_routes.py` | `/api/crypto/binance/*` 라우트 |
| `frontend/lib/crypto-live.ts` | LIVE API 클라이언트·라벨 |
| `frontend/components/crypto-live-terminal.tsx` | 전환 스위치·LIVE 배지·헤더·포지션·주문 티켓 |

`backend/app/crypto/terminal/server.py` 마지막에 `from . import live_routes` 한 줄만 추가했다.
`api.py`는 손대지 않았다.

## 3. Binance 엔드포인트 (사용한 것 전부)

**경로는 문서가 아니라 실제 API 응답으로 확인했다.** 2026-09-29 기준, 서명 없이 호출했을 때
`{"code":-2014,"msg":"API-key format invalid."}`(=존재하는 서명 엔드포인트)와
바이낸스 HTML 오류 페이지(=존재하지 않는 경로)를 구분해서 판정했다.

| 이름 | 메서드·경로 | 보안 | 용도 | 공식 문서 |
|---|---|---|---|---|
| server_time | GET `/fapi/v1/time` | NONE | 시계 보정 | market-data/Check-Server-Time |
| exchange_info | GET `/fapi/v1/exchangeInfo` | NONE | stepSize·minQty·minNotional·precision | market-data/Exchange-Information |
| mark_price | GET `/fapi/v1/premiumIndex` | NONE | Mark·펀딩률·다음 펀딩 | market-data/Mark-Price |
| book_ticker | GET `/fapi/v1/ticker/bookTicker` | NONE | 최우선 호가 | market-data/Symbol-Order-Book-Ticker |
| depth | GET `/fapi/v1/depth` | NONE | 미리보기용 호가 걷기 | market-data/Order-Book |
| account | GET `/fapi/v3/account` | USER_DATA | 지갑·가용·마진·미실현 | account/Account-Information-V3 |
| position_risk | GET `/fapi/v3/positionRisk` | USER_DATA | 포지션·진입가·청산가 | trade/Position-Information-V3 |
| symbol_config | GET `/fapi/v1/symbolConfig` | USER_DATA | **레버리지·마진 모드** | account/Symbol-Config |
| position_mode | GET `/fapi/v1/positionSide/dual` | USER_DATA | One-way/Hedge 판정 | trade/Get-Current-Position-Mode |
| commission_rate | GET `/fapi/v1/commissionRate` | USER_DATA | 내 계정 수수료율 | account/User-Commission-Rate |
| user_trades | GET `/fapi/v1/userTrades` | USER_DATA | 체결·수수료·실현손익 | trade/Account-Trade-List |
| income | GET `/fapi/v1/income` | USER_DATA | 펀딩(FUNDING_FEE) | account/Get-Income-History |
| query_order | GET `/fapi/v1/order` | USER_DATA | 주문 조회 | trade/Query-Order |
| listen_key_* | POST·PUT·DELETE `/fapi/v1/listenKey` | USER_STREAM | 유저 데이터 스트림 | user-data-streams |
| new_order | POST `/fapi/v1/order` | TRADE | LONG/SHORT/CLOSE (**잠김**) | trade/New-Order |
| set_leverage | POST `/fapi/v1/leverage` | TRADE | 레버리지 변경 (**잠김**) | trade/Change-Initial-Leverage |

### 초안에서 잡은 실제 버그 2개

| 초안 | 실제 | 증거 |
|---|---|---|
| `GET /fapi/v1/positionRisk` | **`/fapi/v3/positionRisk`** | v1은 HTML 404, v2·v3만 `-2014` 응답 |
| `GET /fapi/v1/bookTicker` | **`/fapi/v1/ticker/bookTicker`** | 전자는 404, 후자는 200 + 실제 호가 |

그대로 뒀으면 키를 꽂는 첫날 포지션 조회와 호가 조회가 둘 다 404로 실패했다.

### V3가 버린 필드

`/fapi/v3/positionRisk` 응답에는 **`leverage`와 `marginType`이 없다**(v2에는 있다).
그래서 레버리지·마진 모드는 `GET /fapi/v1/symbolConfig`에서 읽는다. v2로 되돌리지 않은 이유는
구버전 고착을 피하기 위해서다.

### WebSocket

`wss://fstream.binance.com/ws/<stream>` 은 살아 있다(2026-09-29 실제 접속으로 bookTicker 프레임 수신 확인).
유저 데이터 스트림은 같은 베이스에 listenKey를 붙인다: `wss://fstream.binance.com/ws/<listenKey>`.
`/public/ws`도 SUBSCRIBE에 응답하고, `/private/ws`는 핸드셰이크만 되고 프레임을 주지 않아 쓰지 않는다.

## 4. 정본 (LIVE에서 누가 이기나)

| 값 | 출처 | 우리가 계산하나 |
|---|---|---|
| 지갑·가용·마진 잔고, 미실현 | `/fapi/v3/account` | 아니오 |
| 포지션 방향·수량·진입가·청산가·명목 | `/fapi/v3/positionRisk` | 아니오 |
| 레버리지·마진 모드 | `/fapi/v1/symbolConfig` | 아니오 |
| 수수료율 | `/fapi/v1/commissionRate` | 아니오 |
| 체결가·수수료·실현손익 | `/fapi/v1/userTrades` | 아니오 |
| 펀딩 | `/fapi/v1/income` | 아니오 |
| Mark | `/fapi/v1/premiumIndex` | 아니오 |
| 주문 전 예상 체결·비용·손익분기 | 위 값 + Binance 호가 걷기 | **예(미리보기 한정)** |

로컬 계산과 다르면 Binance가 이긴다. 페이퍼의 Bybit 수수료 상수는 LIVE 코드에서 import 자체가 없다.

## 5. 읽기 주기 (레이트리밋 설계)

| 티어 | 주기 | 호출 | weight |
|---|---|---|---|
| fast | 1초 | premiumIndex 1 + ticker/bookTicker 2 + positionRisk 5 | **8** |
| slow | 30초 또는 스트림 이벤트 | account 5 + symbolConfig 5 + commissionRate 20 + positionSide/dual 30 | **60** |

1초마다 전체를 읽으면 분당 4000 weight를 넘겨 차단된다(IP 예산 2400/분).
fast만 1초면 480/분이다. slow가 늦어도 되는 이유는 스트림이 있어서다: 체결·잔고 변화가 오면
즉시 dirty로 표시되고 다음 요청이 전체 재읽기가 된다.

## 6. User Data Stream = 신호, 값이 아니다

`ACCOUNT_UPDATE`/`ORDER_TRADE_UPDATE` 프레임은 **이벤트 종류만** 보고 "지금 REST로 다시 읽어라"로 쓴다.
프레임에 실린 잔고를 화면에 반영하지 않는다. 이유는 §4 한 줄과 같다. 정본이 둘이면 둘이 다를 때
설명할 방법이 없다. 재접속하면 그 사이 프레임은 누구에게도 전달되지 않았으므로 무조건 재동기화한다.
`listenKeyExpired`는 키만 버리고 루프가 새 키를 발급한다. keepalive는 30분(만료 60분의 절반).

## 7. 재시작 복구

시작·재접속 경로는 `BinanceLiveAdapter.resync()` 하나다. 로컬 상태를 **읽지 않는다**.
로컬에 복구할 상태가 없다는 것이 설계다. 미러 원장은 감사용이며 스냅샷 복원에 쓰이지 않는다.

## 8. LIVE 감사 원장

`data/runtime/crypto/live/<키 지문>/binance_live_events.jsonl`
줄단위 append + fsync, 시퀀스는 재시작해도 이어진다.
`LiveMirror`는 경로에 `paper` 세그먼트가 있으면 **생성 자체를 거부**한다.
페이퍼 원장은 재생 결정성 증명의 입력이라 한 줄이라도 섞이면 안 된다.

기록: `LIVE_ORDER_INTENT`(버튼을 누른 사실) → `LIVE_ORDER_REFUSED` 또는 `LIVE_ORDER_SENT`→`LIVE_ORDER_RESULT`,
`LIVE_RECONCILE`, `LIVE_STREAM_EVENT/STATE`, 레버리지 3종.

## 9. 주문 경로 (구현 완료·잠금 상태)

```
버튼 → plan() ─ 계좌 준비됨? → reverse 아님? → Binance 필터 통과? → OrderPlan
     → submit() ─ 게이트1 BINANCE_LIVE_TRADING_ENABLED
                ─ 게이트2 client.trading_enabled
                → 둘 다 열려야 POST /fapi/v1/order
```

- **자동 reverse 없음.** 반대 포지션이 있으면 `REVERSE_NOT_ALLOWED`(페이퍼와 같은 코드).
- **CLOSE는 직전에 포지션을 다시 읽는다.** 캐시 수량을 쓰지 않고 `reduceOnly=true`로 보낸다.
- 수량은 Binance `exchangeInfo` 기준으로만 검증한다. 페이퍼 반올림을 쓰지 않는다.
- `newOrderRespType=RESULT`로 체결가를 응답에서 바로 받는다.
- 레버리지 변경도 같은 게이트. 변경 후 **다시 조회**해서 화면에 반영한다(optimistic 금지).

## 10. UI

| 상태 | 화면 |
|---|---|
| 키 없음 | BINANCE LIVE 버튼 비활성 + "PAPER만 사용할 수 있습니다" |
| 키 있음·차단(Hedge 등) | 빨간 배지 + blocker 목록 + 주문 UI 없음 |
| 정상 | 빨간 `BINANCE LIVE 실계좌` 배지, Binance 잔고·포지션·수수료율, 주문 티켓(확인창 포함) |

차트는 V1에서 기존(Bybit) 그대로 두고 캡션으로 명시한다. LIVE의 Mark·손익·미리보기는 Binance 값이다.
Binance 캔들 전환은 **V1.1 후보**로 남긴다(§12).

## 11. AUTO 격리

전략·연구·백테스트 트리는 LIVE 패키지를 import할 수 없다. 테스트(`test_binance_live_safety.py`)가
AST로 저장소를 훑어 위반을 실패로 만든다. LIVE 패키지 역시 페이퍼 엔진을 import하지 않는다(양방향 격리).

## 12. 한계 · V1.1 후보

1. 차트 데이터가 Bybit다. LIVE 차트를 Binance 캔들로 바꾸는 것은 별도 작업.
2. 실제 키로 검증한 적이 없다. 필드 이름은 공식 문서 + ccxt 샘플 기준이며, 누락 시 **조용히 0을 쓰지 않고**
   `RESPONSE_SHAPE_CHANGED` blocker로 화면에 드러난다.
3. 리스크 티어(유지증거금 구간)를 읽지 않는다. 미리보기의 `required_margin`은 명목/레버리지 추정치다.
4. Hedge Mode·다중 심볼·Isolated 자동 전환은 지원하지 않는다(읽어서 표시만).
5. 부분 청산 UI 없음(전량 CLOSE만).
