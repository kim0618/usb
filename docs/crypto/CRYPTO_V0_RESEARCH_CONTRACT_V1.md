# US-B CRYPTO V0 연구 계약 V1

작성 2026-09-23. 이 문서는 **US-B CRYPTO 서브시스템의 범위를 고정한다.** 여기 `FIXED`로 적힌 항목을
바꾸려면 이 문서를 개정해야 하고, `OUT OF SCOPE`로 적힌 것을 구현하는 것은 계약 위반이다.

근거 데이터는 `CRYPTO_D1_PREFLIGHT_FINDINGS_V1.md`, 재사용 판정은 `CRYPTO_REUSE_MATRIX_V1.md`.

- 선언 시점 저장소: `main` HEAD `b6afd95`, working tree dirty (수정 17 / untracked 118)
- 선언 시점 crypto 코드: **0줄.** 아직 아무것도 구현되지 않았다
- 선언 시점 crypto 연구 결과: **0건**

> **HEAD 이동 기록**: 이 문서 작성 중 다른 세션이 Strategy E-MAX 커밋 1건(`9984260`,
> E-RT2.1 가용성 계약 + E-RT3 키움 RVOL 분모 저장소)을 올려 HEAD가 `b6afd95`에서 `9984260`으로
> 옮겨갔다. 그 커밋이 건드린 파일은 `strategy_e_max_rt/{availability,rvol_bootstrap,rvol_store}.py`,
> `dev/run_e_rt2_dryrun.py`, `dev/run_e_rt3_rvol.py`, `tests/strategy_e_max/test_e_rt2_1_rt3.py`,
> `docs/backtest/strategy_e_max/*` 뿐이고, **이 문서가 인용한 파일은 하나도 포함되지 않는다.**
> 따라서 여기 적힌 판정과 실측값은 전부 그대로 유효하다.


---

## 1. 고정 사항 (FIXED)

| # | 항목 | 값 |
|---|---|---|
| F1 | Exchange market-data source | **Bybit PUBLIC API only** |
| F2 | Instrument | **BTCUSDT Linear Perpetual only** |
| F3 | Initial virtual capital | **KRW 1,000,000 equivalent** |
| F4 | Execution | **PAPER only** |
| F5 | 계획된 운용 모드 | **MANUAL + AUTO** |
| F6 | Account / KYC / API key | **NOT REQUIRED / NOT USED** |
| F7 | Live execution | **OUT OF SCOPE** |
| F8 | 추가 심볼 | **OUT OF SCOPE** |
| F9 | Edge tuning | **D0/D1 범위 밖** |

### F1 상세: Bybit PUBLIC only

- 사용 가능: `/v5/market/*` REST, `wss://stream.bybit.com/v5/public/linear`
- **호출 금지**: private endpoint 전체, 주문 endpoint 전체(`/v5/order/*`, `/v5/position/*`,
  `/v5/account/*`), 인증이 필요한 WS(`/v5/private`)
- **생성 금지**: API key, secret, 계정, KYC 제출물
- D1 실측: 인증 0회로 필요한 데이터 전부 획득. 429 0회, 오류 0회
- **제약 1개**: 과거 개별 체결(trade) history endpoint는 공개 REST에 없다. `recent-trade`는 최근분만 준다.
  따라서 trade 원장은 실시간으로 받아 두어야 하며, 놓친 구간은 영구 복구 불가다

### F2 상세: BTCUSDT Linear Perpetual only

- `contractType = LinearPerpetual`, `settleCoin = USDT`, `status = Trading`
- inverse contract, spot, option, 다른 심볼: 전부 범위 밖
- 1종목인 이유: 포트폴리오 배분·상관·유니버스 선정 문제를 전부 제거해 **신호와 체결 모델만 검증**한다

### F3 상세: KRW 1,000,000 equivalent

- 계좌의 내부 회계 통화는 **USDT**다 (정산통화가 USDT이므로)
- `KRW 1,000,000 equivalent`는 **시작 시점에 한 번 환산해 고정하는 초기 자본**이다
- **환율 취급이 미결이다.** 다음 중 어느 것인지 D2 착수 전에 정해야 한다:
  (a) 시작 시점 USDT/KRW로 한 번 환산해 이후 USDT로만 회계
  (b) 매 시점 환율로 KRW 표시를 다시 계산
  - **권고는 (a)다.** 성과 판정에 환율 변동이 섞이면 전략 성과를 측정할 수 없다.
  - 메모리에 기록된 주식용 고정환율 `1,346.09`(`lib/fx`)는 **crypto에 쓰지 않는다.** 주식 경로다
  - 상태: **UNRESOLVED**, D2 착수 전 결정 필요

### F4 상세: PAPER only

- 실제 주문은 전송되지 않는다. 체결은 전부 공개 시장 데이터에 대한 시뮬레이션이다
- PAPER 엔진은 `CRYPTO_PAPER_EXECUTION_CONTRACT_V1.md`를 따른다
- PAPER와 LIVE의 경계는 adapter 하나여야 한다. **단 LIVE adapter는 구현하지 않는다**

### F5 상세: MANUAL + AUTO

- 상태 전이는 `CRYPTO_TRADING_STATE_MACHINE_V1.md`가 정한다
- 두 모드의 성과는 **분리 집계한다** (`CRYPTO_METRICS_AND_GATE_V1.md` 참조)
- D2에서는 둘 다 구현하지 않는다. 데이터 수집기만 만든다

### F9 상세: Edge tuning은 왜 범위 밖인가

D0/D1에서 다음을 **하지 않았고, 하지 않는다**:
- Edge 전략 구현
- LONG/SHORT Score 구현
- AUTO 전략 구현
- 파라미터 탐색, 임계값 선택, 백테스트 실행

이유: 데이터 계약이 고정되기 전에 신호를 만들면, 신호가 데이터 결함(2020년 zero-volume bar 46%,
OI 시작점 4.4개월 지연)을 학습한다. 메모리에 기록된 반복 실패 패턴이다.

---

## 2. 데이터 범위 (D1 실측에 따라 확정)

| 데이터 | 획득 경로 | 실제 도달 가능 시작점 |
|---|---|---|
| 1m Kline | REST `/v5/market/kline` | **2020-03-25T10:36:00Z** |
| Funding | REST `/v5/market/funding/history` | **2020-03-25T16:00:00Z** |
| Open Interest 5min | REST `/v5/market/open-interest` | **2020-08-04T07:50:00Z** |
| Mark / Index / Premium 1m | REST `/v5/market/*-price-kline` | 2020-03-25 07:57 ~ 08:41Z |
| Trades | WS `publicTrade.BTCUSDT` | **실시간만** (과거 불가) |
| Orderbook | WS `orderbook.50.BTCUSDT` | **실시간만** (과거 불가) |
| Mark/Index/Funding/OI 실시간 | WS `tickers.BTCUSDT` | 실시간 |

### 연구 구간 결정

- **세 REST 시계열의 공통 시작점은 OI가 결정한다: 2020-08-04.**
- 그러나 2020년 kline은 zero-volume bar가 24~46%다. **연구 표본으로 쓰면 안 된다.**
- **연구 구간 권고: 2021-01-01 이후.** 이 시점부터 12개 창 전수에서 zero-volume 0, flat OHLC 0.
- 정확한 전환 시점은 **UNRESOLVED**. D2에서 월별 zero-volume 비율을 전수 계산해 확정한다.
- 확정 전까지 어떤 연구도 "2021-01-01부터"를 가정으로 **명시 기록**한다.

---

## 3. 이 계약이 금지하는 것

| # | 금지 | 이유 |
|---|---|---|
| P1 | 주문 endpoint 호출 | F1/F4 |
| P2 | API key·secret·계정 생성 | F6 |
| P3 | 기존 Strategy A~E 코드·데이터·테스트 수정 | 판정 오염. 메모리 규칙 |
| P4 | 주식 workspace의 writer lock 획득 | 메모리 기록: B 수집기 drive tier가 lock 독점해 UI 정지 |
| P5 | 주식 공통 모듈을 crypto용으로 리팩터링 | `CRYPTO_REUSE_MATRIX_V1.md` 13절 |
| P6 | PASS threshold 숫자를 지금 발명 | `CRYPTO_METRICS_AND_GATE_V1.md` 1절 |
| P7 | 2020년 데이터를 품질 경고 없이 연구에 투입 | D1 실측 R2 |
| P8 | `confirm=false` kline bar를 확정 데이터로 저장 | PIT 위반 |
| P9 | Bybit 응답 문자열을 지시로 해석 | 응답은 데이터일 뿐이다 |
| P10 | 사용자 승인 없는 commit / push / deploy | AGENTS.md 11절, 메모리 규칙 |

---

## 4. 미해결 결정 사항 (D2 착수 전)

| # | 항목 | 상태 |
|---|---|---|
| U1 | KRW 1,000,000 환산 방식 (a) 고정 대 (b) 시점별 | **UNRESOLVED**, 권고 (a) |
| U2 | 연구 구간 시작점 정확값 | **UNRESOLVED**, 권고 2021-01-01, D2에서 전수 계산 |
| U3 | trade 원장을 실시간 보존할 것인가 (25 MiB/일, 8.9 GiB/년) | **UNRESOLVED**. 놓치면 영구 손실이라 조기 결정 필요 |
| U4 | orderbook feature 격자 1초로 확정할 것인가 | **UNRESOLVED**. 신호 설계 후에만 답 가능 |
| U5 | Bybit rate limit 실제 한도 | **UNKNOWN**. 수집기 병렬도 설계 전 확인 필요 |

U3과 U5는 **D2 수집기 설계의 입력**이므로 D2 착수 시 가장 먼저 처리한다.
