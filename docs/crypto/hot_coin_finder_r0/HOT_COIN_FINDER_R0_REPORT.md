# HOT Coin Finder Feasibility R0

> **FINAL STATUS (added 2026-10-11, after the fact).** This report is kept as written at the time. Its verdict and any "not deployed", "not pushed" or "main not merged" statements are superseded. Current state (updated after the production fixes): production and `origin/main` are at `37c1c49`, and `main` is the only branch. HOT LIVE orders are OFF, and the first real HOT LIVE order has not been placed (the user will place it manually). Entry point: [HOT_V1_FINAL_INDEX.md](../hot_symbol_v1/HOT_V1_FINAL_INDEX.md).

작성 2026-10-10 KST. FEASIBILITY RESEARCH ONLY. 코드 수정, 주문, 배포, push 없음.

## A. Isolation Proof

- 작업 위치: 별도 clone `~/usb-hot-coin-finder-r0`, 브랜치 `research/hot-coin-finder-r0`, 기준 `5722f96`(당시 origin/main). push URL은 `DISABLED_NO_PUSH`.
- clone 변경분: 이 폴더(`docs/crypto/hot_coin_finder_r0/`) 미추적 1개뿐. 커밋 없음.
- 메인 repo `~/usb`: 읽기만 함(데이터 폴더 목록 조회). HEAD가 조사 중 `5058f41`에서 `5d7863b`로 바뀌었는데, 이는 다른 세션의 liquidity map V1.5 커밋이다.
- BTC Context Collector, 운영 서버, ssh: 접촉 0.
- 측정은 로컬 WSL에서 Binance 공개 REST만 사용했다. API 키 없음, 서명 요청 0, 주문 엔드포인트 0. 운영 서버와 IP가 달라서 weight 버킷도 공유하지 않는다.

## B. Futures Universe

exchangeInfo 924개 중 `PERPETUAL + TRADING + USDT` = **525개**(BTC 제외 시 524개). 7회 스냅샷 동안 변동 없음.

| contractType/quote/status | 수 |
|---|---|
| PERPETUAL USDT TRADING | 525 |
| PERPETUAL USDT SETTLING (상장폐지 완료분) | 133 |
| PERPETUAL USDT PENDING_TRADING (상장 예정) | 1 |
| TRADIFI_PERPETUAL USDT TRADING (주식/원자재형, 범위 밖) | 216 |
| 기타 (USDC, BTC, USD1, U, 분기물) | 49 |

- 거래 안정성 플래그: `deliveryDate != 4133404800000`(상장폐지 예정 신호)은 0건. `onboardDate`가 7일 이내인 종목도 0건.
- **비ASCII 심볼 5개**: `币安人生USDT`, `我踏马来了USDT`, `龙虾USDT`, `牛来USDT`, `哈基米USDT`. URL 인코딩을 안 하면 요청이 실패한다. 첫 trial에서 실제로 실패해서 인코딩을 고친 뒤 재시작했다. `龙虾USDT`는 24h 거래대금 7,100만 USDT로 정상 후보이고, 과거 7일 HOT #1 빈도 3위다.

## C. Market Snapshot Summary

실시간 스냅샷 7회(20:12~20:42 KST, 5분 간격)와 과거 5분봉 8일 백필(525종목, 누락 0)을 함께 썼다.

- 스냅샷 1회 = 525종목 전수(klines 1m + depth 50). 소요 108초, 최대 weight 902/2400, 실패 0.
- 유동성 게이트를 통과한 종목: 82~90개(BTC 제외 524개 중).

## D. Volatility Distribution

실시간 7회 풀링(n=3,668 종목·시점):

| 지표 | p10 | p50 | p90 | p99 |
|---|---|---|---|---|
| 1H range % | 0.44 | 0.85 | 2.26 | 14.71 |
| 24H range % | 3.45 | 6.28 | 14.58 | 41.50 |
| \|24H chg\| % | 0.53 | 2.62 | 7.53 | 26.20 |

같은 시간대 BTC 1H range 중앙값은 0.10%다. 과거 7일 기준은 0.35%.

과거 7일, 2,004개 시점(5분 간격) 기준. 아래 수치는 게이트 통과 종목만 센 것이다.
- 1H range ≥3%인 종목 수: 최소 6, 중앙값 18, p90 25. **0개였던 시점은 2,004개 중 0**이다.
- 1H range ≥5%인 종목 수: 최소 1, 중앙값 8.
- HOT #1의 1H range: 중앙값 15.9%, p10 9.8%, p90 30.4%.
- 시간대별(KST)로 보면 08시가 가장 적고(≥3% 종목 9개) 나머지 시간대는 13~23개다. 특정 시간에만 쓸모 있는 기능은 아니다.

Q1 답: 크게 움직이는 종목은 **항상** 존재한다. 단 "크게"의 수준이 BTC의 수십 배다.

## E. Liquidity Distribution

snap 0 기준(BTC 제외 524개):

| 지표 | p10 | p50 | p90 |
|---|---|---|---|
| spread bps | 0.95 | 3.50 | 7.63 |
| 24h 거래대금 (M USDT) | 0.85 | 3.15 | 49.0 |
| 1h 거래대금 (M USDT) | 0.02 | 0.09 | 1.75 |
| ±0.1% depth (작은 쪽, USDT) | 283 | 1,196 | 16,226 |
| 왕복 slippage 1,000 USDT (bps, mid 기준) | 3.0 | 10.9 | 19.7 |
| 왕복 slippage 5,000 USDT (bps) | 7.0 | 23.6 | 42.1 (50호가 소진 4건) |

게이트는 데이터를 보기 전에 고정했다. spread ≤10bps, 1,000 USDT 왕복 ≤20bps, 24h 거래대금 ≥20M, 1h 거래대금 ≥0.5M, 상장폐지 예정 제외.

| 게이트 | 통과 (7회 범위) |
|---|---|
| spread | 506~513 |
| slippage | 467~488 |
| 거래대금 | 85~90 |
| 전체 | 82~90 |

**실제로 종목을 거르는 것은 거래대금**이다. 호가 품질은 대부분 통과한다.

Q2 답: 게이트를 전부 통과하는 종목은 약 85개(16%). 그중 1H range ≥3%인 종목은 과거 7일 중앙값으로 18개다.

## F. Volatility vs Executability

| 상위 N (1H range 기준) | 게이트 통과율 | 24H range 기준 통과율 |
|---|---|---|
| top 5 | 0.40 | 1.00 |
| top 10 | 0.53 | 0.80 |
| top 20 | 0.59 | 0.62 |

- 1H 상위 20개와 24H 상위 20개를 합친 탈락 사유 누적: LOW_VOLUME 98, HIGH_SLIPPAGE 43, WIDE_SPREAD 36.
- 범주 누적(종목·시점): HIGH_VOL_GOOD_LIQUIDITY 107, HIGH_VOL_BUT_THIN 86, HIGH_VOL_WIDE_SPREAD 5, HIGH_VOLUME_LOW_VOL 98. 마지막 범주는 ETH, SOL, XRP, DOGE, BNB, ADA, SUI 등이다.
- 과거 7일에서 게이트 통과 HOT #1이 raw 1H #1과 같은 비율은 79%다. 실시간에서는 raw #1이 대체로 GRIFFAIN·AIOT·APR 같은 얇은 종목이었다.
- 거래대금 기준만 통과한 1H 상위 20개 중 0~3개가 호가 기준에서 추가 탈락했다(GRIFFAINUSDT spread 12~20bp, 1,000 USDT 왕복 29~43bp). 그래서 **거래대금만으로는 부족하고, 클릭 시점의 호가 확인이 필요**하다.

Q3 답: 1H 기준으로 "가장 많이 움직임"과 "매매 가능"은 상위 5개에서 60%가 엇갈린다. 24H 기준은 거의 일치한다. 24H로 크게 움직인 종목은 이미 거래대금이 붙어 있기 때문이다.

## G. Candidate Examples

```
TIME 20:12
Raw volatility (1H):
 1. WALUSDT      1H 19.3% / 24H 30.5%  qv24 20.1M  spr 5.2bp  rt1k 7.3bp   OK (24h 거래대금 경계)
 2. APRUSDT      1H 17.3% / 24H 35.6%  qv24 16.8M  spr 12.8bp rt1k 20.2bp  WIDE_SPREAD, HIGH_SLIPPAGE, LOW_VOLUME
 3. GRIFFAINUSDT 1H 15.9% / 24H 22.5%  qv24 17.8M  spr 17.0bp rt1k 35.0bp  WIDE_SPREAD, HIGH_SLIPPAGE, LOW_VOLUME
 4. CAPUSDT      1H 12.4% / 24H 37.4%  qv24 82.9M  spr 3.5bp  rt1k 9.8bp   OK
 5. BULLAUSDT    1H 12.4% / 24H 15.7%  qv24 7.9M                           LOW_VOLUME
After liquidity (1H): WALUSDT, CAPUSDT, USUSDT, BATUSDT, ARPAUSDT
HOT candidate: WALUSDT

TIME 20:42
Raw volatility (1H):
 1. GRIFFAINUSDT 1H 21.9% / 24H 29.5%  spr 12.2bp rt1k 29.0bp  WIDE_SPREAD, HIGH_SLIPPAGE
 2. AIOTUSDT     1H 20.0% / 24H 27.0%  qv24 13.9M              LOW_VOLUME
 3. USUSDT       1H 18.0% / 24H 46.2%  qv24 633M  spr 4.6bp rt1k 7.6bp  OK
 4. APRUSDT      1H 16.9% / 24H 35.4%  WIDE_SPREAD, LOW_VOLUME
 5. WALUSDT      1H 16.0% / 24H 30.4%  OK
After liquidity (1H): USUSDT, WALUSDT, BATUSDT, CAPUSDT, ORCAUSDT
HOT candidate: USUSDT (1H +13.0% 방향, 24h 거래대금 633M, spread 4.6bp, 1,000 USDT 왕복 7.6bp)
```

- 24H 1위 MAGICUSDT(24H range 115%, 거래대금 950M)는 1H range가 10%에서 6.6%로 식는 중이었다. 24H 기준 결합 순위는 이렇게 "어제의 HOT"을 올린다.
- WALUSDT의 24h 거래대금은 같은 날 중단된 첫 스냅샷에서 19.7M(탈락), 2분 뒤 20.1M(통과)이었다. 게이트 경계에서는 판정이 뒤집힌다.

## H. HOT Persistence

과거 7일, 2,004개 시점. HOT #1 = 거래대금 게이트 통과 종목 중 1H range 1위. 미래 구간은 과거 구간과 겹치지 않게 잡았다.

| 이후 | 미래 range 순위 중앙값 | 미래 top5 | top10 | 미래 range / 전체 중앙값 | 무작위 종목 | 게이트 #20 종목 | 거래대금 유지 |
|---|---|---|---|---|---|---|---|
| 5m | 2 | 0.80 | 0.94 | 7.6x | 1.0x | 1.6x | 1.00 |
| 15m | 2 | 0.82 | 0.94 | 7.3x | 1.0x | 1.6x | 1.00 |
| 30m | 2 | 0.81 | 0.93 | 7.0x | 1.0x | 1.6x | 1.00 |
| 60m | 2 | 0.81 | 0.94 | 6.6x | 1.0x | 1.6x | 1.00 |

- 직전 15분 대비 다음 15분 range 비율의 중앙값은 0.80이다. 0.5 미만(움직임 종료)인 경우는 21%다.
- 같은 정의로 다시 줄을 세우면(구간 겹침 있음) 60분 뒤에도 #1인 비율은 29%, top5 안인 비율은 78%다.
- 실시간 30분 표본도 같은 방향이다. USUSDT는 7회 연속 #1이었고, 30분 뒤에도 15m range 순위 4위, 유동성 게이트 통과, spread 2~5bp 수준을 유지했다. WALUSDT는 #1이었다가 30분 뒤 15m 순위 17위로 식었다.
- HOT #1의 1H range 중 5분봉 하나가 70%를 넘게 차지하는 단발 스파이크 비중은 18%다.

Q4 답: HOT #1은 "다음 1시간 동안에도 시장 대비 훨씬 많이 움직일 종목"으로 지속성이 강하다(무작위 대비 약 7배). 다만 1위 자리는 30분이면 절반이 바뀐다. 가격 방향은 측정하지 않았다(범위 밖).

## I. API Cost

실측 weight(응답 헤더 `x-mbx-used-weight-1m` 차분과 문서값이 일치):

| 엔드포인트 | weight | 비고 |
|---|---|---|
| exchangeInfo | 1 | 1시간 캐시 가능 |
| ticker/24hr (전체) | 40 | batch |
| ticker/bookTicker (전체) | 5 | batch |
| premiumIndex (전체) | 10 | funding 진단용, 랭킹에는 불필요 |
| klines 1m limit<100 | 1 / 심볼 | 1H range에 필요한 유일한 per-symbol 호출 |
| depth limit 50 | 2 / 심볼 | |
| openInterest | 1 / 심볼 | 진단용 |

- **무식한 전수 스캔(이번 연구 방식)**: 525×(1+2) + 56 + 25 ≈ 1,660 weight, 10 req/s로 108초. 버튼용으로는 **부적합**하다. 분당 한도의 69%를 쓰고, 운영 서버 IP는 LIVE 백엔드 폴링(화면당 약 8 weight/초)과 같은 버킷을 공유한다.
- **권장 구조(3단계)**:
  1. batch: exchangeInfo(캐시) + ticker/24hr + bookTicker = 45~46 weight.
  2. 거래대금·bookTicker spread 사전필터로 약 99개만 남기고 klines 1m limit 61 = 약 99 weight.
  3. 1H 순위 상위 10개만 depth 50 = 20 weight.
  - 합계 **약 165 weight, 약 110 요청, 동시 8개면 수 초**. 분당 한도의 7%다.
  - 사전필터는 최종 게이트와 같은 조건이라 후보 손실이 구조적으로 0이다. 7회 스냅샷 전부 HOT #1과 top5 35/35를 포착했다.
- 남은 비용 요인은 per-symbol klines 약 100회다. 더 줄이려면 상시 WS(`!ticker@arr` 등)가 필요한데, 이는 상시 수집기에 해당하므로 이번 범위 밖이다.

## J. Generic Symbol Compatibility Audit

서브에이전트 감사 결과다. 핵심 3건(Bybit 원천, openOrders 미호출, 같은 심볼만 보는 반대 포지션 검사)은 코드로 직접 재확인했다.

| 항목 | 분류 | 근거 |
|---|---|---|
| supported symbols (backend) | SMALL_CHANGE | `symbols.py:29` tuple 1곳 + 열거처 4곳(live_routes, api). whitelist 자체가 정책 문서("4번째는 누군가 의도적으로 결정") |
| supported symbols (frontend) | BTC_ETH_SOL_HARDCODED | `crypto-symbols.ts` 타입 유니온, `isCryptoSymbol` 게이트, 고정 탭, 전 심볼 변동성 폴링 |
| PRICE_FILTER / tickSize / pricePrecision | GENERIC_ALREADY | `live/filters.py` 심볼별 exchangeInfo 파싱, 누락 시 raise |
| LOT_SIZE / MARKET_LOT_SIZE / stepSize / qtyPrecision | GENERIC_ALREADY | 같음 |
| MIN_NOTIONAL | GENERIC_ALREADY | 같음 |
| leverage brackets / max leverage | GENERIC_ALREADY | 심볼별 bracket 조회, 사다리는 max 이하로 필터 |
| 계좌연령 20x 상한 | GENERIC_ALREADY | 계좌 범위로 적용, 단 심볼별 json 파일이 생김 |
| commission | GENERIC_ALREADY | 심볼별 조회(20 weight) |
| Safe MAX | GENERIC_ALREADY | 심볼 필터·호가·mark·수수료 사용 |
| chart history | MAJOR_REWORK | 원천이 **Bybit** REST(`chart_history.py:20`). Binance 전용 코인은 차트 없음 |
| 시장 feed (호가·15s 캔들) | MAJOR_REWORK | 심볼당 **Bybit WS 2개**, 지연 생성, 회수 없음 |
| position lookup | GENERIC_ALREADY | 심볼 필터 조회 |
| CLOSE | GENERIC_ALREADY | reduceOnly, 5중 심볼 일치 검사 |
| ledger | GENERIC_ALREADY (파일 증식) | `manual_performance_{symbol}.json`, 계좌 전체 P&L 없음 |
| SymbolRuntime 객체 | SMALL_CHANGE | 지연 생성, 회수 없음(LRU 필요) |
| PAPER instrument / 세션 | MAJOR_REWORK | Bybit 사전 캡처 파일 필요, 심볼마다 ledger·tape 디렉터리 |

정리하면 **주문 계산은 이미 일반화돼 있고, 막는 것은 Bybit 데이터 경로와 회수 없는 심볼별 상주 자원**이다.

## K. Position Lock Feasibility

| 검사 | 분류 | 현황 |
|---|---|---|
| (a) 어떤 심볼이든 positionAmt ≠ 0 | AVAILABLE_BUT_FILTERED_OR_FAIL_OPEN | 주문 경로는 `position_risk {"symbol": 자기심볼}`만 읽는다(`adapter.py:208`). 반대 포지션 차단도 같은 심볼만 본다(`orders.py:265`). 전체 조회는 표시용 `/positions`에만 있고, 파싱 실패 행을 건너뛴다(fail-open) |
| (b) 미체결 주문 존재 | NOT_PRESENT | `open_orders`는 `endpoints.py:121`에 등록만 돼 있고 호출부가 0 |
| (c) 주문 in-flight | NOT_PRESENT | router에 pending set·lock 없음. 직렬화는 단일 이벤트루프가 sync HTTP를 쓰는 데서 나오는 부수효과다. ExitGuard는 별도 스레드에서 lock 없이 CLOSE를 보낸다 |

**지금은 backend가 fail-closed로 판정할 수 없다.** 필요한 것은 세 가지다. 전체 positionRisk(무필터, 파싱 실패 시 차단), 전체 openOrders(weight 40, 오류 시 차단), router와 ExitGuard가 공유하는 계좌 단위 in-flight 레지스트리(결과 불명이면 `query_order`로 확인될 때까지 잠금).

## L. Recommended Ranking Contract (V1 후보, 사후 튜닝 없음)

1. Universe: `PERPETUAL`, `TRADING`, `quoteAsset=USDT`. BTCUSDT 제외, `deliveryDate` 예정 종목 제외. 심볼은 반드시 URL 인코딩.
2. 게이트(사전 고정값 유지): 24h 거래대금 ≥20M, 1h 거래대금 ≥0.5M, spread ≤10bp, 1,000 USDT 왕복 slippage ≤20bp. 호가 게이트는 **클릭 시점 depth로** 판정.
3. 순위: **1H range(확정 1분봉 60개, 시가 기준) 단일 기준**.
4. 쓰지 말 것:
   - 1H·24H 백분위 평균 결합: 꼬리에서 포화되어 #1과 #2 차이가 0.00~0.01이다. 그래서 #1이 사실상 임의로 정해지고, MAGIC 같은 식은 24H 종목을 올린다.
   - funding/OI: 진단용으로만 표시한다(예: ERAUSDT funding -1.29%, MAGICUSDT -0.46%).
5. 표시: #1 하나가 아니라 **top 5**를 보여준다. 1H 방향(±), 24H range, 거래대금, spread, 1,000/5,000 USDT 왕복 slippage를 함께 보여준다. 단발 스파이크(5분봉 하나가 1H range의 70% 초과, 18%)와 24h 거래대금 경계 근처는 라벨로 표시한다(제외하지 않음).

## M. Limitations

- 호가 게이트는 실시간 30분, 7회만 측정했다(원래 95분 계획을 사용자 합의로 단축). 지속성·안정성은 과거 5분봉으로 쟀고, 거래대금 게이트만 적용했다. 실시간에서는 거래대금 통과 1H 상위 종목 중 최대 20%가 호가에서 추가 탈락했으므로 H의 매매 가능성은 다소 과대평가다.
- 과거 universe는 현재 TRADING 종목 기준이다. 지난 7일 사이 상장폐지된 종목이 빠졌다(생존 편향).
- 7일, 단일 시장 국면이다. 시점 2,004개는 서로 독립 표본이 아니다(5분 간격, 같은 종목이 연속 1위).
- slippage는 50호가 공개 depth 스냅샷 기반의 추정이다. 숨은 유동성, 호가 취소, 체결 지연은 반영하지 않았다. 5,000 USDT 초과는 측정하지 않았다.
- 운영 서버 IP의 현재 weight 사용량은 측정하지 않았다(접촉 금지).
- 방향 적중, 손익은 측정하지 않았다(범위 밖). HOT #1의 1H range 중앙값 15.9%는 BTC의 수십 배이고, 그만큼 위험도 크다.

## N. Final Verdict

**C. FEASIBLE_FOR_IMPLEMENTATION**: 단, **읽기 전용 on-demand HOT scanner**에 한정한다.

- 근거: 후보는 시간대와 무관하게 항상 존재한다. 단순 계약(게이트 + 1H range)으로 뽑은 #1이 다음 60분에도 시장 중앙값의 6.6배 움직였다(무작위 1.0배). 5분 간격 1회 스캔의 #1 교체율은 14%이고, top5는 평균 4.46/5가 유지된다. 1회 클릭 비용은 약 165 weight로 부담이 없다.
- **HOT 종목을 앱에서 실제 LIVE 매매하는 것은 C가 아니다.** 차트·feed가 Bybit 기반이라 MAJOR_REWORK이고, 포지션 잠금 3항목 중 2개가 부재, 1개는 fail-open이다. 이건 별도 결정 사안이다.

## O. Exact Implementation Scope (C, 구현하지 않음, 사용자 결정 대기)

Phase 1. 읽기 전용 스캐너 (주문·탭·selected symbol 무변경):
1. backend: `live/` 밖에 계좌 접근 없는 읽기 전용 모듈과 GET 라우트 1개(예: `/api/crypto/hot-scan`). Binance 공개 REST만 사용한다.
   - 처리 순서: exchangeInfo(1h 캐시) → ticker/24hr + bookTicker → 사전필터 → klines 1m limit 61(약 100개) → 1H 순위 → 상위 10개 depth 50 → 게이트 → top 5와 탈락 사유 반환.
   - 보호장치: single-flight, 60초 결과 캐시, 사용 weight 헤더가 1,200을 넘으면 중단. 심볼 URL 인코딩.
2. frontend: 버튼 1개와 결과 목록(읽기 전용). 결과를 누를 경우 외부 Binance 화면으로 연결하는 정도로 하고, **기존 탭과 주문 대상은 건드리지 않는다**.
3. 테스트: 픽스처 기반 순위·게이트·비ASCII 심볼, weight 상한 중단, fetch 스텁(실서버 전송 금지).

Phase 2. 선택한 HOT 종목 LIVE 매매(별도 승인 필요, MAJOR_REWORK):
- whitelist 정책 변경
- 차트·feed를 Binance로 이전
- 심볼별 상주 자원 회수(LRU)
- K의 잠금 3항목 fail-closed 구현
- frontend 심볼 타입 일반화

## 증거

`evidence/`: `snap.py`, `backfill.py`, `analyze.py`, `hist_analyze.py`, `live_report.txt`, `hist_report.txt`, trial·backfill 로그, `hist_hot_seq.json`, 원자료 `raw_snapshots_and_klines.tgz`(스냅샷 7회, 중단된 첫 스냅샷, 5분봉 8일).
