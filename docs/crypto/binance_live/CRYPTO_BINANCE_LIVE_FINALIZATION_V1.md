# CRYPTO BINANCE LIVE · 로컬 최종화 V1

작성: 2026-09-30
키 지문: `487ea08e`
판정: **LOCAL_FINALIZATION_READY** (push 0 · 운영 배포 0)

선행: READ_ONLY_PASS(V2) → BALANCE_SYNC_PASS(V1) → EXECUTION_PRECHECK_PASS →
LIVE_EXECUTION_PARTIAL(V1) → 본 문서

`LIVE_EXECUTION_PARTIAL`의 유일한 미달 항목이던 User Data Stream을 규명·수정하고, 그 위에
레버리지 조회/선택과 수동 무장 세션을 얹었다. 이 문서는 회사 PC에서 커밋까지만 다룬다.
push와 운영 배포는 하지 않았다.

---

## 1. User Data Stream: 원인과 수정

### 1.1 원인

**폐기된 legacy websocket base URL을 쓰고 있었다.**

Binance는 websocket 트래픽을 `/public`(고빈도 호가), `/market`(마크가·kline·티커),
`/private`(유저 데이터) 세 base로 분리하고 기존 통합 `/ws`·`/stream`을 **2026-04-23에 영구
폐기**했다. 공지는 미이관 연결이 `/public` 데이터는 계속 받지만 `/market`과 `/private`는
"stop pushing data"라고 명시한다.

> https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice

앱은 `wss://fstream.binance.com/ws/<listenKey>`에 붙고 있었다. 이 실패는 **조용하다**:
핸드셰이크가 성공하고, ping/pong이 유지되고, listenKey도 불평 없이 받아들여진다.
소켓은 영원히 "연결됨"으로 보이고 프레임만 0건이다. 9/29 검증에서 독립 리스너를 붙여도
같은 결과가 나온 이유가 이것이다 — 그 리스너도 같은 legacy URL을 썼다.

### 1.2 규명 방법 (실주문 0건)

레버리지 변경이 `ACCOUNT_CONFIG_UPDATE`를 발생시킨다는 공식 문서를 근거로, **주문·포지션·
수수료·손익이 전혀 없는 이벤트**를 만들어 A/B 했다. 같은 listenKey 하나에 두 소켓을 동시에
물리고 그 사이에 레버리지를 20x→10x로 바꿨다.

| 소켓 | 결과 |
|---|---|
| legacy `wss://fstream.binance.com/ws/<listenKey>` | **0 프레임** |
| 이관 `wss://fstream.binance.com/private/ws?listenKey=<key>` | **130 ms 만에 `ACCOUNT_CONFIG_UPDATE`** |

레버리지는 즉시 20x로 복원하고 Binance에서 재조회해 확인했다.

선행 시도 하나는 **결론이 안 났고**, 그 사실도 적어둔다: 가짜 listenKey로 붙여 서버가 키를
파싱하는지 보려 했으나 legacy·`/private` 양쪽 모두 가짜 키를 조용히 받아들였다. 소켓 수락
여부로는 두 엔드포인트를 구분할 수 없다. 실제 이벤트를 흘려보내는 것 말고는 방법이 없었다.

### 1.3 `events=` 필터를 쓰지 않는 이유 (실측)

`/private/ws`는 `&events=...` 필터를 받고 **엄격하게 지킨다**. 세 소켓으로 확인했다.

| 소켓 | `ACCOUNT_CONFIG_UPDATE` 수신 |
|---|---|
| `events` 파라미터 없음 | **수신** |
| `events=ORDER_TRADE_UPDATE` | **미수신** |
| `events=ORDER_TRADE_UPDATE/ACCOUNT_UPDATE/ACCOUNT_CONFIG_UPDATE` | 수신 |

따라서 **필터를 보내지 않는다.** 오늘 아는 이벤트만 나열하면 `listenKeyExpired`와
`MARGIN_CALL`이 정확히 가장 중요한 순간에 조용히 누락된다. 이번 장애와 같은 형태의 실패다.
필터링은 `stream.handle`에서 하고, 그 집합은 눈에 보이고 테스트로 고정돼 있다.

### 1.4 수정 후 검증

앱 자체 `UserDataStream`을 실계좌에 붙여 재확인했다(독립 리스너가 아니라 운영에 나갈 코드).

| 항목 | 값 |
|---|---|
| url | `wss://fstream.binance.com/private/ws?listenKey=…` |
| connected | true |
| 첫 이벤트까지 | **0.5초** |
| account_events | 2 (레버리지 변경 2회) |
| on_change 발화 | `ACCOUNT_CONFIG_UPDATE` ×2, `listenKeyExpired` ×1 |
| last_error | null |

`listenKeyExpired`까지 전달된 것이 §1.3 판단의 실측 근거다.

**판정: STREAM_CODE_BUG (Case C) → 수정 → STREAM_PASS.**

### 1.5 REST 정본 원칙은 그대로

스트림의 역할은 `CHANGE_SIGNAL_ONLY`이고 잔고·포지션의 정본은 REST다. 이번 수정은 URL과
이벤트 필터만 건드렸고 신호/재조회 경로(`handle` → `_mark_dirty` → `on_change` → REST
reconcile)는 diff상 변경 0이다. 공개 시세 tape는 Bybit라 이번 migration의 영향을 받지 않는다
(`liquidation_forward`와 `liq_capture`는 이미 `/market/stream`을 쓰고 있었다).

---

## 2. 레버리지

### 2.1 실제 지원 범위 (하드코딩 아님)

`GET /fapi/v1/leverageBracket`을 레지스트리에 추가하고 계정 자신의 구간표를 읽는다.
BTCUSDT 실측: **최대 150x**(bracket 1, notional ≤ 300,000 USDT, 유지증거금률 0.4%).
`notionalCoef`는 null.

UI에 보이는 값은 표시용 사다리 `(1, 2, 3, 5, 10, 20, 50)`을 **구간표 상한으로 거른 것**에
현재 설정값을 합집합한 결과다. 상한을 코드에 적어두지 않았으므로 계정의 리스크 티어가
내려가면 선택지도 같이 줄어든다. 사다리를 쓰는 이유는 150개 값을 다 보여주는 것보다
의미 있는 단계 몇 개가 위험 설정을 고르기에 낫기 때문이고, 이는 표현의 문제일 뿐
**허용 여부는 전적으로 Binance 구간표가 정한다**.

### 2.2 optimistic update 없음

버튼 → `POST /fapi/v1/leverage` → `resync()` → `symbolConfig` 재조회 → **재조회값만 화면에
반영**. 요청값은 응답의 `requested` 필드로만 남고 화면에 쓰이지 않는다.

### 2.3 제약

- **포지션 보유 중 레버리지 변경 차단.** 버튼이 비활성화되고 사유가 표시된다
- **마진 모드는 표시 전용.** `POST /fapi/v1/marginType`은 엔드포인트 deny list에 그대로 있다.
  이를 푸는 것은 안전 구조 변경이고, 실패 모드(열린 포지션, 잔여 주문)를 전부 다뤄야 한다.
  이번 버전의 일이 아니라고 판단했다. 변경은 Binance 앱/웹에서 하고 화면은 현재 값을 읽어 표시만 한다
- **자동 변경 없음.** 화면 진입이나 서버 기동으로 레버리지가 바뀌는 경로는 없다

### 2.4 위험 표시와 sizing

명목·개시증거금·청산가는 전부 Binance `positionRisk`의 값을 그대로 쓴다. 프론트에서
`notional / leverage` 같은 산술을 하지 않는다 — 유지증거금 티어를 무시하게 되고, 하필
포지션이 위험할 때 거래소와 어긋난다. 포지션이 없으면 `-`로 두고 "포지션 생성 후 Binance가
산출"이라고 적는다. 수량별 필요 증거금은 주문 미리보기가 이미 제공한다.

화면에 명시한 문구: 레버리지는 노출 배수가 아니라 증거금 설정이다. 0.001 BTC는 1x에서도
50x에서도 0.001 BTC이고, 달라지는 것은 묶이는 증거금과 청산가다. 수량과 레버리지는 계속
따로 고른다.

---

## 3. 수동 무장 세션

### 3.1 두 게이트를 다시 그은 이유

기존 두 게이트는 둘 다 환경변수였다. 노트북에서 한 번 검증할 때는 맞지만 서버에서는 틀리다.
유닛 파일의 `Environment=` 한 줄은 한 번 켜면 영원히 켜져 있고, 재시작마다 무장된 채로
돌아온다. 게이트가 조용히 상시 설정으로 바뀐다. 반대로 매번 shell `export`를 요구하면
안전하지만 쓸 수 없다.

그래서 실제로 의미 있는 선을 따라 다시 갈랐다.

| 게이트 | 의미 | 사는 곳 |
|---|---|---|
| `BINANCE_LIVE_TRADING_ENABLED` | 이 배포가 **주문을 낼 수 있는가** (capability) | 환경변수 |
| `ArmSession` | 지금 **내고 있는가** (intent) | 프로세스 메모리 |

### 3.2 성질

- **부팅은 항상 해제.** 세션은 파일에 남지 않으므로 새 프로세스가 무장된 채로 뜰 수 없다.
  크래시·배포·재부팅 전부 해제다
- **확인 문구 `ARM LIVE TRADING`을 정확히 입력해야 한다.** 페이지 로드·prefetch·재시도 GET
  어느 것도 계정을 무장시킬 수 없다
- **TTL 만료 자동 해제** (기본 900초). 화면을 떠나면 닫힌다
- **만료는 게이트를 읽는 순간 동기적으로 적용된다.** 타이머가 아니다. 첫 구현은 세션 조회
  때만 만료돼서, 라우터가 `client.trading_enabled`만 보면 탭을 닫은 순간 창이 안 닫히는
  버그가 있었다. 테스트로 잡았고
  `LiveOrderRouter.armed`가 클라이언트 플래그보다 **먼저** 세션을 읽도록 고쳤다
- **세 번째 게이트가 아니다.** 세션이 곧 두 번째 게이트다: 무장은
  `BinanceFuturesClient.trading_enabled`에 write-through하고, 그 플래그가 `rest.call`이
  TRADE 요청을 만들기 전에 검사하는 바로 그 값이다. 판단하는 곳은 전과 같이 하나다
- `BINANCE_LIVE_CLIENT_ARMED`는 의미 그대로 유지된다. 이 변수를 세팅한 머신은 부팅부터
  무장이고 세션이 끼어들지 않는다(로컬 검증 경로). 운영은 이 변수를 세팅하지 않는다
- **AUTO는 접근 불가.** `LiveOrderRouter.submit`의 호출자는
  `POST /api/crypto/binance/order` 라우트 하나뿐이고, `research/**`·`paper/**` 중
  live 패키지를 import하는 모듈은 0개다
- **CLOSE 안전성 불변.** `_close_plan`은 직전에 Binance에서 포지션을 다시 읽고
  `reduceOnly=true`로 보내며 `BINANCE_LIVE_MAX_QTY`의 제한을 받지 않는다. diff 변경 0

### 3.3 감사 기록

무장·해제·거부는 LIVE 미러에 `LIVE_ARM` / `LIVE_ARM_REFUSED` / `LIVE_DISARM`으로 남는다.
세션 자체는 메모리에 있고 프로세스와 함께 죽으므로, "14시 32분에 이 배포가 무장돼 있었나"는
파일만 보고 답할 수 있어야 한다. 기동 시 `LIVE_DISARM reason=BOOT`이 한 줄 적힌다.

---

## 4. 주문 기본 수량 0.001

주문 티켓 기본값을 0.002에서 **0.001**(거래소 최소)로 내렸다. 수량 칸을 건드리지 않고
LONG을 누르면 나가는 값이 이것이므로, 오클릭 비용이 최소 단위의 배수가 아니라 최소 단위가
된다. `LIVE_EXECUTION_V1` §2가 지적한 문제다.

---

## 5. 검증 중 발생한 사고: 의도치 않은 실주문 1왕복

**로컬 E2E 검증 하네스의 결함으로 실제 주문 2건이 체결됐다. 제품 게이트 결함이 아니다.**

### 5.1 원인

E2E 셸 스크립트의 `post()` 헬퍼가 `post() { curl ... -d "$2" ...; }` 형태였는데 스크립트가
`set -u`로 돌고 있었다. 본문 없이 호출하는 `post /api/crypto/binance/disarm`에서
`$2: unbound variable`로 헬퍼가 죽었다.

그 결과 **10단계 disarm이 실행되지 않았고**, 무장이 유지된 상태에서 11단계 "해제 후에도
거부되는지" 확인용 주문이 그대로 체결됐다.

### 5.2 제품 게이트는 설계대로 동작했다

같은 실행에서:

- 3단계 — 무장 전 주문: `LIVE_TRADING_DISABLED`로 거부, 거래소에 도달 0
- 4단계 — 잘못된 확인 문구: `CONFIRMATION_REQUIRED`로 거부
- `BINANCE_LIVE_MAX_QTY=0.001`이 수량을 묶었다. 이 상한이 없었다면 더 큰 주문이 나갔을 것이다

실패한 것은 검증 하네스이고, 실패 지점은 셸 변수 하나다.

### 5.3 실제 주문

| # | orderId | side | qty | 체결가 | commission | realizedPnl |
|---|---|---|---|---|---|---|
| 1 | `1149956427696` | BUY (OPEN) | 0.001 | 83,393.60 | 0.04169680 | 0 |
| 2 | `1149957331608` | SELL (CLOSE, `reduceOnly`) | 0.001 | 83,403.30 | 0.04170165 | +0.00969999 |

### 5.4 회계

| 항목 | 값 |
|---|---|
| realized PnL | +0.00969999 |
| commission 합 | −0.08339845 |
| **net** | **−0.07369846 USDT** |
| wallet | 374.48970275 → 374.41600429 |
| 관측된 변화 | −0.07369846 (일치) |
| `income` 합계 | −0.07369846 (일치) |

### 5.5 종료 상태 (독립 재조회)

| 항목 | 값 |
|---|---|
| BTCUSDT position | **FLAT** (`positionRisk` 0행) |
| open orders | **0** |
| unrealized | 0.00000000 |
| walletBalance | 374.41600429 USDT |
| leverage / margin mode | 20x / CROSSED (원래 값으로 복원 확인) |
| 이후 추가 주문 | **0** |

청산은 앱 자체 CLOSE 경로로 했다: 직전에 Binance에서 수량을 다시 읽고 `reduceOnly=true`로
전송. 로컬 캐시를 쓰지 않았다.

### 5.6 조치

- 사고 즉시 청산하고 FLAT·open orders 0을 독립 조회로 확인
- 이번 커밋 작업에서 추가 Binance 주문 0건
- 실주문을 낼 수 있는 검증 스크립트는 scratchpad에만 두고 저장소에 커밋하지 않는다
- 운영 스모크(Phase 16)를 다시 할지는 사용자 판단. 같은 왕복이 실계좌에서 이미 한 번 일어났다

---

## 6. 테스트

| 대상 | 결과 |
|---|---|
| backend crypto 전체 | **1382 passed** |
| 그중 binance_live 3파일 + arm | 121 + 23 |
| frontend vitest | **574 passed** (43 files) |
| tsc | **0 errors** |
| eslint | **0 errors** (기존 미사용 변수 warning 5건) |
| next build | **PASS** |

`lightweight-charts 5.2.1`은 `package.json`·`package-lock.json` 양쪽에 이미 선언돼 있었고
`next build`가 그 선언으로 통과한다. 단 이 선언은 **이번 세션의 변경이 아니라 커밋되지 않은
선행 변경**이다(§7).

---

## 7. 이 문서가 다루지 않는 것

- **push 0.** 로컬 커밋까지만 했다
- **운영 배포 0.** `usb-crypto-paper` 유닛은 지금도 "이 앱은 어떤 거래소 자격증명도 갖지
  않는다"고 명시하고 `/root/usb/.env`를 일부러 읽지 않는다. Binance LIVE 배포는 그 설계
  경계를 넘는 일이고 별도 결정이 필요하다
- **운영 서버 Trusted IP 미확인.** 서버 아웃바운드 IP가 Binance Trusted 목록에 있는지는
  키를 서버에 올려 서명 요청을 보내야만 알 수 있다
- **`frontend/package.json`·`package-lock.json`의 `lightweight-charts` 선언이 미커밋.**
  이번 세션 변경이 아니라 staging하지 않았다. **운영 배포 전에 반드시 커밋돼야 한다** —
  안 그러면 서버 빌드가 깨진다
- 부분 체결, 큰 수량, ISOLATED, 헤지 모드, 청산 근접은 여전히 미실측이다
