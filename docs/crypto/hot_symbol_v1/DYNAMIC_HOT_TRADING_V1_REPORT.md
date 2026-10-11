# Dynamic HOT Trading V1

> **FINAL STATUS (added 2026-10-11, after the fact).** This report is kept as written at the time. Its verdict and any "not deployed", "not pushed" or "main not merged" statements are superseded. Current state (updated after the production fixes): production and `origin/main` are at `37c1c49`, and `main` is the only branch. HOT LIVE orders are OFF, and the first real HOT LIVE order has not been placed (the user will place it manually). Entry point: [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md).

2026-10-10 KST. 격리 clone `~/usb-hot-coin-finder-r0`, 브랜치 `feat/dynamic-hot-v1`(V0 `322d7db` 위). push 없음, 운영 무접촉, LIVE 주문 0.

**판정: B. PAPER_READY.** LIVE 조회 전용 검증을 하지 못했기 때문이다. 로컬에 Binance 키가 없고, 서버에서 검증하려면 승인이 필요하다(U 참조).

## A. Architecture

```
[BTC] [🔥 HOT] [코인탐색]                         frontend (useHotSlot: GET /hot 5s 폴링 + 포지션 변화 시 즉시 재조회)
   │               │ POST /api/crypto/hot/scan
   │               ▼
   │      hot_coins/slot.HotSlotController  ─ lock 확인 → scan(R0 계약) → prepare → 재확인 → commit(admit) → retire(old)
   │        ├ PaperExposure (hot_routes)       paper 포지션 + paper in-flight
   │        └ LiveExposure  (live_routes)      positionRisk(symbol) + openOrders(symbol) + live in-flight
   ▼
symbols.resolve: 화이트리스트(BTC/ETH/SOL) ∪ admit된 HOT 1개
Runtime.feed_for:  BTC/ETH/SOL → Bybit(기존 그대로), HOT → BinancePublicFeed(/public depth20 + /market markPrice·kline)
chart-history:     BTC/ETH/SOL → Bybit, HOT → BinanceChartHistory      15s: HOT → Binance aggTrade
paper session:     HOT → Binance 필터 + 측정된 마진표(Bybit 공개 risk-limit → 없으면 계좌 bracket → 없으면 PAPER 거부)
LIVE:              LiveConfig.for_symbol이 admit된 HOT 허용, HOT LIVE 주문·레버리지 변경은 BINANCE_LIVE_HOT_ORDERS(기본 off)
```

피드 설계는 A안(BTC는 기존 Bybit, HOT만 Binance)을 택했다. 이 안은 BTC 경로를 한 줄도 바꾸지 않는다. B안(전체 Binance 통일)은 PAPER 엔진의 체결 원천과 기존 BTC 원장의 해석까지 바꾸므로 근거 없이 하지 않는다.

## B. BTC + HOT Model

- BTC는 항상 고정 탭이다. HOT 슬롯은 서버가 1개 보유한다(`hot_slot.json`, PAPER 루트 옆에 원자적으로 저장).
- HOT 슬롯과 선택된 거래 심볼은 별개다(selected=BTC, hot=LUMIA가 가능하다). HOT 탭을 누르면 그때 선택된다.
- HOT 탭에서 HOT이 교체되면 선택은 새 HOT을 따라간다. BTC 탭에 있으면 그대로다.

## C. Removed Fixed Tabs

- ETH/SOL 고정 탭은 제거했다. 백엔드의 ETH/SOL 지원(화이트리스트, Bybit 기준 파일)은 그대로다. ETH/SOL이 HOT 1위가 되면 HOT 슬롯에 들어간다.
- 비-HOT 보유 포지션 감사 결과:
  - 기존 ETH/SOL PAPER·LIVE 포지션이나 이전 HOT의 PAPER 포지션(장애로 남은 경우)은 **보유 중인 동안만 탭**으로 노출되어 청산할 수 있다.
  - 이전 HOT의 PAPER 포지션은 재기동 시 다시 허용되어 보유 심볼로 노출된다.
  - 앱 밖에서 연 포지션은 기존처럼 조회 전용(`others`)이다.
  - 정책 충돌은 없다.

## D. HOT Persistence

- 서버: `hot_slot.json`(symbol, selected_at_ms, scanned_at_ms, metrics, top5), version 1. 깨진 파일은 HOT 없음으로 읽는다.
- 재기동하면 lifespan에서 다시 허용한다(PAPER 빌드 전).
- 브라우저: 마지막 슬롯 사본(`usb.crypto.hotSlot.v1`)으로 새로고침 직후 탭을 그린다. 버튼은 서버 응답 전까지 비활성이다.
- 하이드레이션 결함 수정: 선택 심볼을 `useSyncExternalStore`(서버 스냅샷 BTC)로 바꿨다.
  - 이전에는 프리렌더 HTML의 BTC `aria-selected`가 남아 **두 탭이 동시에 선택**으로 보였다. 브라우저 QA에서 재현됐고, 메모리에 기록된 기존 결함과 같은 뿌리다.
  - SSR→hydrate 회귀 테스트를 추가했다. 이전 방식을 되돌려 넣으면 이 테스트가 실패하는 것도 확인했다.

## E. Lock Semantics

| 상황 | 결과 |
|---|---|
| HOT 없음 | 허용 |
| BTC 포지션 / BTC 미체결 | 허용(BTC는 조회조차 안 함) |
| 현재 HOT 포지션 ≠ 0 (PAPER 또는 LIVE) | `HOT_POSITION_OPEN` |
| 현재 HOT 미체결 (LIVE openOrders) | `HOT_OPEN_ORDER` |
| 현재 HOT 주문 전송 중 (PAPER/LIVE in-flight) | `HOT_ORDER_IN_FLIGHT` |
| 노출원 조회 실패, 필드 불명 | `HOT_STATE_UNKNOWN` (fail closed) |
| 키 미설정(LIVE 계좌 없음) | LIVE 노출 0으로 간주(불명이 아님) |

- 버튼 활성 여부는 서버 판정만 따른다. 화면이 포지션 변화를 보면 즉시 서버에 다시 묻는다.
- QA에서 처음에는 최대 5초 지연이 있었다. 지금은 체결 확인 즉시 비활성, 청산 확인 즉시 재활성(실측 0.0초)이다.

## F. Binance Market Data

- Binance는 스트림 경로를 나눠 놓았다. 호가 `depth20`은 `/public`, `markPrice`·`kline`·`aggTrade`는 `/market`이다. 하나로 붙으면 절반이 오지 않는다(실측). 그래서 소켓 2개를 쓴다.
- 중국어 심볼(`龙虾USDT`)은 스트림 이름을 퍼센트 인코딩한다. 실연결로 확인했다.
- 실측: LUMIA·龙虾·US·BAT 모두 호가, mark, index, funding, 1분봉 tail, 15초 aggTrade가 수신됐다. 이력은 1m/10m(5m 접기)/1h/4h/1d이고 龙虾 1m 2,880봉까지 받았다.

## G. Dynamic Runtime (6항 감사)

| 항목 | 분류 | 조치 |
|---|---|---|
| symbol allowlist | NEEDS_CHANGE → 완료 | `admit_dynamic`(HOT 서비스만 호출, Binance 재확인 후), 은퇴 시 `retire_dynamic` |
| runtime creation | NEEDS_CHANGE → 완료 | PAPER `dynamic_run_config`, LIVE `for_symbol` 허용 |
| market feed | NEEDS_CHANGE → 완료 | `BinancePublicFeed` |
| chart feed / tail | NEEDS_CHANGE → 완료 | kline_1m 스트림, 지난 봉으로 되감기 방지 |
| chart history | NEEDS_CHANGE → 완료 | `BinanceChartHistory`, 15s는 `BinanceTradeCandleFeed` |
| quote/ticker, orderbook | NEEDS_CHANGE → 완료 | markPrice@1s, depth20@100ms |
| position lookup | GENERIC_ALREADY | PAPER 세션별, LIVE 심볼 고정 reader |
| order preview / fee preview | GENERIC_ALREADY | |
| LONG / SHORT | GENERIC_ALREADY | HOT LIVE는 스위치로 거부(기본 off) |
| CLOSE | GENERIC_ALREADY | 격리 테스트 추가 |
| leverage | PAPER UI NEEDS_CHANGE → 완료 / LIVE GENERIC_ALREADY | PAPER는 상품 최대 초과 버튼 비활성 |
| commission | LIVE GENERIC_ALREADY | PAPER는 런 수수료(Bybit VIP0) 그대로 |
| Safe MAX | GENERIC_ALREADY | HOT 테스트로 확인 |
| PnL / ledger / performance | GENERIC_ALREADY | `<run_id>-<SYMBOL>` 디렉터리, 공용 지갑 |
| symbol persistence | NEEDS_CHANGE → 완료 | D 참조 |
| user stream reconcile | GENERIC_ALREADY | 계좌 스트림 1개가 지어진 어댑터 전부에 dirty 전파 |
| 가격 표시 | **QA에서 발견, 완료** | 100 미만 가격이 "0.0"으로 표시되던 결함. `priceDigits`와 tick 기반 차트 정밀도로 수정. BTC/ETH/SOL 표시는 불변(테스트 고정) |
| BLOCKER | 키 없는 환경의 Binance 전용 HOT | 마진표가 없으면 PAPER 세션이 없고, 화면 차트도 PAPER 상태에 의존하므로 없음(사유 표시). 운영(키 있음)은 계좌 bracket으로 해소될 예정이며 서버 검증이 필요하다 |

## H. Chart

- HOT 탭: 15s/1m/10m/1h/4h/1d 전부 Binance다. 초기 뷰, 이력 페이징, tail, 줌·팬은 기존 1m 패턴을 그대로 쓴다.
- 심볼이 시리즈 식별자에 들어가므로 교체 시 이전 캔들이 남지 않는다(기존 장치).

## I. Filters / Precision

- HOT PAPER 사양은 Binance exchangeInfo에서 읽는다: PRICE_FILTER.tickSize, MARKET_LOT_SIZE(없으면 LOT_SIZE) step·min·max, MIN_NOTIONAL. 하나라도 없으면 거부한다.
- 실측 예: US는 step 1, 최소 5 USDT, tick 0.000001. BAT는 step 0.1, tick 0.00001.
- 원응답은 sha256과 함께 `reference/<SYMBOL>/reference/`에 저장한다. 재기동 때 같은 바이트로 재구성한다.

## J. Leverage

- PAPER: 최대값은 그 심볼의 측정 마진표다(US 20x, LUMIA·BAT 25x). 런 기본 10x를 이 최대로 자르고, 초과 버튼은 비활성이다(실화면 50x 취소선).
- LIVE: 심볼별 bracket을 읽는 기존 경로다. BTC capability는 복사하지 않는다. HOT 레버리지 **변경**은 스위치로 거부한다. 실계좌 mutation은 0이다.

## K. Safe MAX

LIVE 경로는 하드코딩된 수량 없이 availableBalance, 레버리지, 심볼 필터, 호가, 수수료, 최소 금액, step으로 계산한다. HOT 심볼 테스트로 step 격자와 최소 금액이 HOT 자신의 값인지 확인했다.

## L/M. Position & CLOSE Isolation

- PAPER 실엔진: BTC LONG 유지 상태에서 HOT LONG, HOT CLOSE, BTC CLOSE를 해도 서로의 포지션이 불변이다(테스트와 브라우저 실측).
- LIVE: HOT CLOSE 계획은 HOT 심볼과 HOT 수량만 담고 reduceOnly다. HOT 어댑터에 BTC를 요청하면 거부한다.

## N. Resource Lifecycle

- HOT 교체 시 정리하는 것:
  - 이전 심볼의 Binance 소켓 2개와 15s 스레드 정지, seed 작업 취소, 차트 캐시 삭제
  - LIVE `SymbolRuntime` 회수, 허용 해제
  - PAPER 세션: 거래 이력이 있으면 지갑 멤버로 보존(실현손익이 지갑의 돈이므로), 없으면 완전히 제거
- 교체 중 경합: 재확인 직전 `replacing` 표시를 세운다. 주문 경로는 in-flight를 먼저 등록한 뒤 이 표시를 확인한다. 어느 쪽이 먼저든 상대를 본다(테스트).
- 실측(실제 Binance 네트워크, 21심볼 20회 교체): 21개 모두 시세 수신. **5회 후와 20회 후 스레드 6·소켓 13 동일**, 은퇴 심볼 캐시 0.
- 실서버: 은퇴한 BAT 요청은 400이다.

## O. PAPER Results (실제 로컬 터미널, 실데이터, 1440)

1. 실제 스캔으로 HOT=US가 선정됐다(weight 158, 요청 107).
2. BTC LONG을 연 상태에서 탐색이 허용됐다.
3. HOT 탭: Binance 차트와 시세, US LONG 893 체결, BTC 불변.
4. HOT 포지션: 버튼 즉시 비활성, 사유 툴팁, 직접 POST는 409 `HOT_POSITION_OPEN`.
5. CLOSE: 백엔드 flat 확인 즉시 재활성, BTC 불변.
6. 재탐색: 더블클릭인데 POST 1회. 시장 1위가 그대로라 `HOT_UNCHANGED`였다. 교체 경로(BAT→US)는 앞선 실행에서 실서버로 확인했다.
7. 새로고침: HOT 탭 선택 유지.
8. non-GET은 `/api/crypto/order`와 `/api/crypto/hot/scan`뿐이고, `/binance/*` non-GET은 0이다.

## P. LIVE Read-only Results

- **미실행.** 로컬에 키가 없다.
- 공개 데이터 부분(필터, 호가, 가격, 차트)만 실측했다.
- 계좌 의존 항목(bracket, commission, 현재 레버리지, positionRisk·openOrders 노출, availableBalance 기반 Safe MAX, Binance 전용 심볼의 bracket 마진표)은 가짜 전송 테스트로만 확인했다.

## Q. UI

- 390/1440/1600 모두 가로 넘침 0이다(스캔 전, HOT 선택, 패널 열림).
- 탭 줄: BTC 86px, HOT 89~94px, 긴 이름은 말줄임. 패널은 오버레이라 아무것도 밀지 않는다.
- 헤드리스 크롬에는 이모지 폰트가 없어 🔥가 네모로 보인다. 실제 브라우저 확인이 필요하다.

## R. Tests

- backend:
  - `test_hot_symbol_v1.py` 46개: 허용·은퇴, 슬롯, lock 7종, 경합, PAPER A~E 실엔진, 격리, 최소금액, 레버리지 상한, 20회 교체, 재기동, LIVE 노출·스위치·CLOSE·Safe MAX, Binance 피드·이력·사양·bracket
  - `test_hot_coins.py` 43개
  - crypto 전체 2115 통과. 실패 4건은 clone에 ETH/SOL 기준 파일이 없어서이며, 기준 파일이 있는 곳에서 multisymbol 43/43 통과(새 실패 0).
- frontend:
  - `crypto-hot-coins.test.tsx` 29개: 선택·지속·실패·stale·더블클릭·유니코드·lock·3폭 통합·따라가기·보유 탭·20회 교체(이전 심볼 요청 0, 타이머·리스너 증가 0)·레버리지·하이드레이션·가격 표시
  - 전체 1074/1074, tsc 통과, eslint 오류 0(경고 11은 원본과 동일)
- 변형 검사: 하이드레이션 수정, 포지션 변화 즉시 재조회, V0 가드 3종 각각 되돌리면 실패한다.
- 불안정 테스트: integration "fast switch"가 전체 실행 부하에서 1회 흔들렸고 이후 재현 0이다. 원본에서도 같은 파일이 부하로 흔들린다.

## S. Changed Files

- backend:
  - 신규 `hot_coins/market.py`, `hot_coins/slot.py`
  - 수정 `symbols.py`, `hot_coins/lock.py`, `hot_coins/scanner.py`, `terminal/api.py`, `terminal/server.py`, `terminal/hot_routes.py`, `terminal/live_routes.py`, `live/credentials.py`
- frontend:
  - `lib/crypto-hot-coins.ts`, `lib/crypto-symbols.ts`, `lib/crypto-paper.ts`
  - `components/crypto-hot-coins.tsx`, `crypto-symbol-tabs.tsx`, `crypto-terminal.tsx`, `crypto-terminal-layout.tsx`, `crypto-candle-chart.tsx`, `crypto-paper-terminal.tsx`
- tests: `test_hot_symbol_v1.py`(신규), `test_hot_coins.py`, `crypto-hot-coins.test.tsx`, `crypto-terminal-integration.test.tsx`, `crypto-volatility.test.tsx`

## T. Final Verdict

**B. PAPER_READY.** PAPER는 실데이터로 끝까지 동작한다. C로 가려면 P(LIVE 조회 전용 검증)가 선행되어야 한다.

## U. Exact Remaining Steps Before Production

1. **[승인 필요] LIVE 조회 전용 검증**
   - 운영 호스트에서 기존 서비스와 분리된 임시 런타임으로 실행: localhost 별도 포트, `BINANCE_LIVE_ROOT`는 임시 경로(운영 감사 mirror 무접촉), 거래 플래그 off, `BINANCE_LIVE_HOT_ORDERS` off.
   - 현재 HOT에 대해 확인할 것: status, account, sizing, leverage options, positions, LiveExposure, Binance 전용 심볼 bracket 마진표.
   - 서버 RAM이 빠듯하므로 실행 시간은 최소로 한다.
2. **[승인 필요] LIVE 주문 검증**: `BINANCE_LIVE_HOT_ORDERS=on`으로 HOT 최소 금액 OPEN→CLOSE 1회. BTC 포지션 불변과 HOT 잠금 동작을 실측한다.
3. 실제 브라우저(Windows·모바일)에서 🔥 렌더링을 확인한다.
4. **[승인 필요] 운영 배포**: 백엔드 런타임 복사본과 서버 밖 프론트 빌드(규칙대로), 롤백본 보존. 새 env `BINANCE_LIVE_HOT_ORDERS`는 기본 off로 둔다.
5. 유의 사항:
   - HOT PAPER 수수료는 런 설정(Bybit VIP0)이다.
   - PAPER 체결은 Binance 상위 5호가 기준이다.
   - 키 없는 환경에서 Binance 전용 HOT은 PAPER와 화면 차트가 없다.
