# HOT Coin Finder V0 (read-only)

> **FINAL STATUS (added 2026-10-11, after the fact).** This report is kept as written at the time. Its verdict and any "not deployed", "not pushed" or "main not merged" statements are superseded. Current state: Production HOT V1 PASS. Production code, `origin/main` and `origin/feat/dynamic-hot-v1` are all `fa67437`. HOT LIVE orders are OFF, and the first real HOT LIVE order has not been placed (the user will place it manually). Entry point: [HOT_V1_FINAL_INDEX.md](../hot_symbol_v1/HOT_V1_FINAL_INDEX.md).

2026-10-10 KST. 기준 `5d7863b`(origin/main), 격리 clone `~/usb-hot-coin-finder-r0`, 브랜치 `feat/hot-coin-finder-v0`. push 없음, 운영 무접촉.

## A. Architecture

```
[코인탐색] 버튼 (frontend/components/crypto-hot-coins.tsx)
   │ GET /api/crypto/hot-coins  (클릭 시에만, 1회)
   ▼
terminal/hot_routes.py  ── asyncio.to_thread ──▶ hot_coins/service.py (single-flight, 10초 floor)
                                                   │ guarded_scan(현재 HOT, 노출원)   ← hot_coins/lock.py
                                                   ▼
                                                 hot_coins/scanner.py ── httpx GET ──▶ fapi.binance.com (공개 시장 데이터)
```

- 백엔드: `crypto/hot_coins/` 신규 패키지와 `terminal/hot_routes.py` 신규 GET 라우트 1개. `server.py` 끝에서 import한다(`live_routes`·`c1_routes`와 같은 방식). `api.py`는 무변경.
- 스캔은 워커 스레드에서 돈다. 터미널이 `--workers 1` 단일 이벤트 루프라, 루프에서 돌리면 LIVE 폴링이 멈추기 때문이다.
- 프론트: `lib/crypto-hot-coins.ts`(GET 1개, 저장/복원, 포맷)와 `components/crypto-hot-coins.tsx`(버튼, 오버레이 패널). `crypto-terminal.tsx`는 탭 줄을 감싸서 버튼만 옆에 붙였다. `SymbolTabs`는 무변경이고, PAPER와 LIVE 두 화면이 같은 요소를 쓴다.
- HOT 결과는 별도 state와 별도 저장 키(`usb.crypto.hotCoins.v0`)에 둔다. 선택 심볼(`usb.crypto.symbol`), 차트, 주문 티켓과 연결된 경로가 없다.

## B. Ranking Contract (R0 그대로 고정)

- Universe: `contractType=PERPETUAL`, `status=TRADING`, `quoteAsset=USDT`. BTCUSDT 제외, `deliveryDate` 예정(상장폐지) 제외.
- Eligibility: 24h 거래대금 ≥20M, 1h ≥0.5M(확정 1분봉 60개, 연속성 검사), spread ≤10bp, $1,000 왕복 slippage ≤20bp(클릭 시점 depth 50 walk, mid 기준 매수+매도).
- Rank: 1H high-low range % 내림차순. 동률이면 심볼명순. 24H 변화와 합성 점수는 없다.
- 탈락 사유: `NOT_PERPETUAL`, `NOT_TRADING`, `NOT_USDT_QUOTED`, `EXCLUDED_MAIN_SYMBOL`, `DELIVERY_SCHEDULED`, `LOW_24H_VOLUME`, `LOW_1H_VOLUME`, `WIDE_SPREAD`, `HIGH_SLIPPAGE`, `INSUFFICIENT_HISTORY`, `DATA_UNAVAILABLE`, `NOT_EVALUATED`. 사유별 개수는 항상 응답에 들어가고, 종목별 목록은 `?debug=1`일 때만 들어간다.

## C. API Cost (실측, 실제 Binance)

| 스캔 | 요청 | weight | 소요 | 단계별 후보 수 |
|---|---|---|---|---|
| 스캐너 직접 21:07 | 108 | 159 | 1.13s | 524 → 97 → 80 → book 8 → 5 |
| 라우트 경유 21:12 | 109 | 160 | 0.90s | 524 → 98 → 83 → book 8 → 5 |

- 엔드포인트별: exchangeInfo 1(1시간 캐시), ticker/24hr 40, bookTicker 5, klines 약 98 × 1, depth 8 × 2.
- 보호장치:
  - Binance가 보고한 IP 사용 weight가 1,200/분 이상이면 중단한다(`HOT_SCAN_RATE_BUDGET`).
  - 전체 25초 데드라인, depth는 최대 16회.
  - 서버 single-flight(동시 요청은 스캔 1회로 묶음)와 10초 floor(그 안의 재요청은 직전 결과를 `cached`로 반환).
  - 단위 테스트로 고정: 비용 = 1 + 40 + 5 + (사전필터 통과 수) + 2 × depth, 거래대금 미달 종목에는 klines 0회.

## D. Live Candidate Examples (실제 스캔)

```
21:12 KST  universe 524 → batch 통과 98 → 1h 거래대금 통과 83
1. BATUSDT    1H 14.99% UP    24H 44.0%  spread 3.3bp  $1k RT 9.3bp
2. USUSDT     1H 11.77% UP    24H 46.2%  spread 1.6bp  $1k RT 6.8bp
3. MAGICUSDT  1H 11.30% DOWN  24H 115.4% spread 0.9bp  $1k RT 3.2bp
4. FOLKSUSDT  1H 10.25% UP    24H 29.7%  spread 4.1bp  $1k RT 10.6bp
5. ORCAUSDT   1H  6.99% UP    24H 16.5%  spread 4.0bp  $1k RT 4.5bp
탈락: LOW_24H_VOLUME 425, LOW_1H_VOLUME 17, WIDE_SPREAD 2, HIGH_SLIPPAGE 2, 범위 밖 400(비USDT·비perp·비TRADING)
```

- 21:24(1600px QA) 스캔에서는 GRIFFAINUSDT가 #1로 올라왔다. R0 때는 spread 12~20bp로 탈락하던 종목인데, 호가가 좁아져 게이트를 통과한 것이다.
- `龙虾USDT`는 klines 조회까지 정상 처리됐다(URL 인코딩 동작 확인). 순위에서는 밀렸다.

## E. TOP5 Output

종목별로 `rank, symbol, last_price, one_hour_range_pct, one_hour_change_pct, direction(UP/DOWN/FLAT), twenty_four_hour_range_pct, one_hour_quote_volume, twenty_four_hour_quote_volume, spread_bps, estimated_round_trip_slippage_bps_1000, scanned_at_ms`를 준다.

응답 전체에는 `scanned_at`(ISO), `cached`, `coalesced`, `universe_size`, `stages`, `rejection_counts`, `api{requests,weight,duration_ms,by_endpoint,max_used_weight_1m}`, `lock`, `contract`가 들어간다. 원본 응답은 `frontend/components/__fixtures__/hot-coins-live.json`에 있다.

## F. Persistence

- 성공한 결과만 `localStorage["usb.crypto.hotCoins.v0"]`에 `{scannedAtMs, top, universeSize}` 형태로 저장한다.
- 복원은 mount 후 effect에서 한다. 정적 프리렌더와 하이드레이션 불일치(기존 탭에서 BTC가 눌린 채 SOL로 동작하던 결함과 같은 뿌리)를 피하기 위해서다.
- 깨진 값이나 형식이 다른 값은 무시한다.
- 브라우저 실측: 새로고침 후 ▾ 버튼으로 같은 TOP5와 같은 탐색 시각이 복원되고, 이때 스캔 요청은 0회다.

## G. Position Lock Contract (`hot_coins/lock.py`)

| 상황 | 판정 |
|---|---|
| 현재 HOT 없음 | ALLOWED `NO_ACTIVE_HOT_SYMBOL` |
| BTC 포지션 / BTC 미체결 | ALLOWED. BTC는 HOT 잠금 대상이 아니며 조회조차 하지 않는다 |
| 현재 HOT 포지션 ≠ 0 | BLOCKED `HOT_POSITION_OPEN` |
| 현재 HOT 미체결 주문 | BLOCKED `HOT_OPEN_ORDER` |
| 현재 HOT 주문 전송 중 | BLOCKED `HOT_ORDER_IN_FLIGHT` |
| 노출원 예외 / 필드 미확인 / 노출원 없음 | BLOCKED `HOT_STATE_UNKNOWN` (fail-closed) |

- `InFlightRegistry`: 전송 중인 주문을 심볼별로 센다. 응답 없이 예외로 끝난 요청은 결과를 알 수 없으므로 `reconciled()`가 호출될 때까지 잠금을 유지한다.
- V0 서버는 `CURRENT_HOT_SYMBOL=None`, `EXPOSURE_SOURCE=None`이다. HOT 주문 경로가 없으므로 계좌를 읽지 않고, 판정은 늘 `NO_ACTIVE_HOT_SYMBOL`이다. 응답 `lock` 필드에 그대로 노출한다.

## H. Race Safety

`guarded_scan(current_hot, source, scan)`:
1. 탐색 전에 현재 HOT 노출을 확인한다. BLOCKED면 스캔하지 않는다.
2. 스캔한다.
3. 적용 직전에 다시 확인한다. 그사이 포지션, 미체결, 전송 중 주문이 생겼으면 결과를 버리고 기존 HOT을 유지한다(`HOT_STATE_CHANGED_DURING_SCAN`).

테스트로 고정: 스캔 도중 주문이 생기면 결과 미적용, 양 끝이 flat이면 적용, 시작 시 BLOCKED면 스캔 함수 미호출.

## I. UI Preview (Playwright, 실제 빌드, 원본 빌드와 대조)

- 원본(origin/main 빌드)과 브랜치 빌드에서 탭 3개, 시세 헤더, 주문 티켓의 위치와 크기가 **390 / 1440 / 1600 전부 픽셀 동일**했다.
- 가로 넘침 0(스캔 전, 결과 표시 중, 닫은 뒤 모두 `scrollWidth == clientWidth`).
- 패널은 오버레이라 열려도 아무것도 밀지 않는다. 390에서는 탭 아랫줄 바로 아래(y=183)에 폭 358px로 열린다. 1440과 1600에서는 우측 416px이며, 열려 있는 동안 주문 영역 윗부분을 덮는다(바깥 클릭이나 Esc로 닫힘).
- 스캔 중에는 버튼이 "탐색 중…"으로 바뀌고 비활성이 된다. 패널은 "Binance 선물 전체를 탐색하는 중입니다", 이전 결과가 있으면 "탐색 중 · 아래는 이전 결과입니다"를 보인다.
- 실패 시 빨간 문구 "… · 이전 결과를 유지합니다"가 뜨고 TOP5는 그대로다.
- QA 중 발견해 고친 것 2건:
  - 패널이 탭 아랫줄(1H 변동폭)을 덮던 문제. 행을 `items-stretch`로 바꿔 탭 아래에서 열리게 했다.
  - 390에서 지표 라벨이 잘리던 문제. 폰은 2열, sm 이상은 3열로 바꿨다.
  - 추가로 방향이 없는 1H 범위까지 하락색으로 칠하던 것을 중립색으로 바꿨다.
- 스크린샷과 측정 원본: `ui/`.

## J. Mutation Proof

- **AST와 import 그래프(backend 테스트):**
  - HOT 파일은 `live`, `live_routes`, `paper.engine`, `c1`을 import하지 않는다.
  - 주문, 레버리지, 계좌, listenKey, 서명 엔드포인트 문자열이 0개다.
  - 라우트 데코레이터는 `get` 하나뿐이고, POST는 405다.
  - 스캐너가 실제로 부른 경로는 공개 시장 5종의 부분집합이다.
- **import 그래프(frontend 테스트):** HOT 파일은 `@/lib/crypto-paper`(API base, 에러 타입)와 자기 lib만 import한다. `crypto-live`, `liveApi`, `cryptoApi`, `storeSymbol`, `useSelectedSymbol`, POST/PUT/DELETE 참조가 0개다.
- **브라우저 요청 감사(3개 폭 × 전 시나리오):** 브라우저 쪽 요청 메서드는 `GET`만 나왔다. 기록용 서버가 받은 요청은 최종 run 387건, 앞선 두 run 953건으로 **전부 GET**이고 non-GET은 0건이다.
- **선택 심볼 불변:** HOT 행을 클릭해도 `aria-selected`는 BTC 그대로이고, `usb.crypto.symbol`도 변하지 않는다. HOT 종목으로 가는 symbol 쿼리 요청은 0건이다.
- Binance order 0, CLOSE 0, leverage 0, AUTO 0, account 0. 키를 쓰는 경로 자체가 없다.

## K. Tests

- **backend `test_hot_coins.py` 43개:**
  - 순위 정확성, 24H 미반영, 필드
  - BTC 제외
  - 게이트별 탈락: 비TRADING, 비perp(TRADIFI·분기물), 비USDT, 상장폐지 예정, 24h, 1h, spread(batch·depth), slippage(호가 소진·비소진), 경계값 포함
  - 유니코드(실제 httpx로 `%E9%BE%99%E8%99%BE` 확인)
  - 부분 실패 시 계속, 전체 장애 시 실패, weight 가드, 빈 결과는 실패로 처리
  - 비용 공식, exchangeInfo 캐시
  - single-flight(4 동시 요청 → 스캔 1회), floor 캐시, 실패해도 이전 결과 유지
  - 라우트(200, 502, debug, GET 전용)
  - lock 계약 10개, 구조 3개
- **frontend `crypto-hot-coins.test.tsx` 15개:** 스캔 성공, 유니코드, 클릭 전 0요청과 타이머 없음, 더블클릭 1요청과 비활성, hook 중복 무시, stale 응답 무시, 서버 오류·타임아웃·네트워크 장애 시 이전 결과 유지, reload 복원, 깨진 저장값, 터미널 통합(390/1440/1600에서 선택 불변, GET만, 행은 버튼 아님), import 그래프.
- **변형 검사:** 더블클릭 가드 제거, stale 가드 제거, 실패 시 결과 삭제를 각각 넣었을 때 각각 2, 1, 2건이 실패했다. 테스트가 실제로 결함을 잡는다.
- **회귀:**
  - backend crypto 전체: 2069 통과. 실패 4건은 clone에 없는 ETH/SOL reference 데이터 때문이며 원본에서도 동일하다(새 실패 0).
  - frontend 전체: 1059/1059 통과. 첫 실행에서 integration 1건이 부하로 흔들렸는데, 단독 3/3 통과했고 원본 전체 실행에서도 같은 파일이 2건 흔들려 기존 불안정으로 판단했다.
  - tsc 통과, eslint 통과.

## L. Changed Files

신규
- `backend/app/crypto/hot_coins/__init__.py`, `scanner.py`, `lock.py`, `service.py`
- `backend/app/crypto/terminal/hot_routes.py`
- `backend/tests/crypto/test_hot_coins.py`
- `frontend/lib/crypto-hot-coins.ts`
- `frontend/components/crypto-hot-coins.tsx`, `crypto-hot-coins.test.tsx`
- `frontend/components/__fixtures__/hot-coins-live.json`
- `docs/crypto/hot_coin_finder_v0/` (이 보고서와 UI 증거)

수정
- `backend/app/crypto/terminal/server.py` (+7, import 1줄과 주석)
- `frontend/components/crypto-terminal.tsx` (탭 줄 래핑, 버튼 추가)

## N. Production Readiness

판정 **READY_FOR_PRODUCTION_READ_ONLY**. 운영 배포는 하지 않았고, 사용자 승인이 필요하다.

배포 시 확인할 것(이번에 운영을 건드리지 않아 미검증):
1. 운영 nginx가 `/api/crypto/` 접두어를 통째로 8100으로 넘기는지. 새 경로 `/api/crypto/hot-coins`가 별도 location 등록 없이 닿는지 확인해야 한다.
2. 운영 서버에서 `fapi.binance.com` 공개 REST로 나가는 경로. LIVE 라우트가 이미 같은 호스트를 쓰므로 가능할 것으로 보지만 실측은 안 했다.
3. 운영 IP의 weight 여유. LIVE 폴링과 버킷을 공유하는데, 가드가 1,200에서 중단한다.
4. 프론트 빌드 시 서버 RAM 경계. 메모리 기록상 이 서버의 빌드는 RAM이 빠듯하다.
5. LIVE 화면 트리의 브라우저 렌더링. 미리보기에 키가 없어 PAPER 화면으로만 실측했다. 탭 줄은 두 화면이 같은 요소다.

## O. Phase 2 Blockers (HOT 종목 실제 매매)

R0 감사 J·K 그대로다.
- whitelist 정책 변경
- 차트와 시세 feed를 Bybit에서 Binance로 이전
- 심볼별 상주 자원(WS 2개, PAPER 세션, SymbolRuntime, 파일) 회수
- 계좌 단위 노출원 구현: 심볼별 positionRisk, openOrders(현재 호출부 0), router와 ExitGuard가 공유하는 `InFlightRegistry`. 이걸 `EXPOSURE_SOURCE`에 꽂으면 G·H 계약이 그대로 작동한다.
- frontend 심볼 타입 유니온 일반화
- HOT 선택을 서버 측 `CURRENT_HOT_SYMBOL`로 옮기기
