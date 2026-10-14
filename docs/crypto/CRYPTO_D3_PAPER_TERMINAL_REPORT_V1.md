# US-B CRYPTO D3 Paper Trading Terminal 실행 보고 V1

작성 2026-09-23. **이 문서는 증거다.** Bybit Mainnet PUBLIC API만 사용했고, 계정·KYC·API key·
private endpoint·주문 endpoint는 한 번도 호출하지 않았다. Edge/Score/Probability/AUTO 판단 로직은
0줄이다. commit·push하지 않았다.

전제로 사용한 계약: `CRYPTO_V0_RESEARCH_CONTRACT_V1.md`, `CRYPTO_PAPER_EXECUTION_CONTRACT_V1.md`,
`CRYPTO_TRADING_STATE_MACHINE_V1.md`, `CRYPTO_RISK_LIMIT_FINDINGS_V1.md`,
`CRYPTO_D2_POLICY_FREEZE_V1.md`, `CRYPTO_D2_DATA_FOUNDATION_REPORT_V1.md`.

---

## A. Git preflight

| 항목 | 값 |
|---|---|
| branch | `main` |
| D3 시작 시 HEAD | `a774047` |
| D3 종료 시 HEAD | `ff2608b` |
| 작업 중 HEAD 이동 | **1회** (다른 세션) |
| commit / push | **하지 않음** |

| 커밋 | 제목 | crypto 영향 |
|---|---|---|
| `ff2608b` | 트레이딩 탭을 전략 레지스트리 기반으로 일반화 | 없음 |

그 커밋이 건드린 파일은 `frontend/components/{app-shell,section-tabs,*.test}.tsx`와
`frontend/lib/strategies.ts`뿐이고, crypto 파일과 겹치는 것은 0개다. 회귀 테스트는 `a774047`
시점에 돌렸으나 이 커밋은 `backend/tests`를 건드리지 않았으므로 결과가 유효하다.

> **D3-R11의 판단이 실제로 맞았다.** 나비게이션에 메뉴를 추가하지 않은 이유가 "다른 세션이
> `app-shell.tsx`와 `section-tabs.tsx`를 동시에 편집 중"이었는데, 작업 도중 그 세션이 정확히
> 그 두 파일을 커밋했다. 건드렸다면 충돌했을 것이다.

D3가 만든 것은 전부 untracked다.

```
?? backend/app/crypto/            (paper/, terminal/ 추가)
?? backend/app/dev/crypto_paper_run.py
?? backend/tests/crypto/          (paper 테스트 3편 + conftest 추가)
?? docs/crypto/                   (D3 문서 2편 추가)
?? frontend/app/crypto-paper/
?? frontend/components/crypto-paper-terminal.tsx
?? frontend/components/crypto-paper.test.tsx
?? frontend/lib/crypto-paper.ts
```

기존 dirty 파일과 Strategy A~E 코드·테스트는 **한 줄도 수정하지 않았다.**

> **작업 중 사고 1건과 그 복구**: `npx next dev`를 프로젝트 스크립트(`NEXT_DIST_DIR=.next-dev`)
> 없이 실행해 추적 파일 `frontend/next-env.d.ts`가 `.next` 경로로 다시 쓰였다. Next가 실행 모드마다
> 자동으로 고치는 파일이며, 작업 종료 시 커밋 상태(`.next-build`)로 원복했다. 임시로 생긴
> `frontend/.next/` 41 MiB도 삭제했다.

---

## B. 결정 사항의 이행

| 지시 | 이행 |
|---|---|
| Research window V1 = 2021-02-01T00:00:00Z 이후 | `CRYPTO_RESEARCH_WINDOW_V1.md`로 계약 확정 |
| 2020~2021-01 raw 보존, research에서 제외 | 같은 문서 W3/R2. 파일은 그대로 `historical/`에 있다 |
| quarantine 673 MiB → 메타데이터 보존 후 삭제 | 파일 10건의 path/size/sha256/reason/replacement를 기록한 뒤 삭제 |
| Instrument = BTCUSDT Linear Perpetual only | `InstrumentSpec`에 고정. 다른 심볼 경로 없음 |
| Starting virtual capital = KRW 1,000,000 | run config 필수값. USDT 환산은 시작 시 1회 고정 |
| 계정/API key/private endpoint 금지 | 코드에 자격증명 개념이 없다. 호출 endpoint는 `/v5/market/*`와 public WS뿐 |
| PAPER ONLY | 주문을 외부로 보내는 코드 경로가 존재하지 않는다 |
| Strategy/Edge/Score/AUTO 구현 금지 | 0줄. AUTO는 상태 enum에만 존재하고 `AUTO_NOT_READY`로 거부된다 |
| commit/push 금지 | 하지 않음 |

### quarantine 삭제 기록

`data/runtime/crypto/BTCUSDT/quality/quarantine_deletion_record_v1.json`

| 항목 | 값 |
|---|---|
| 파일 수 | 10 |
| 총 바이트 | 704,854,389 (673 MiB) |
| 삭제 시각 | 2026-09-23T03:25:43Z |
| 보존 항목 | 파일별 path, size_bytes, sha256, mtime, 그룹별 reason과 replacement 경로 |

두 그룹 모두 대체물이 존재한다. `d2_boundary_constant_error`는 교정된 mark/index 최종본이,
`d2_interrupted_session_1790129795`는 30분 게이트 런(session 1790130665)이 대체한다.

---

## C. 아키텍처

```
backend/app/crypto/paper/          PAPER 엔진 (순수, 시계·소켓·난수 없음)
  instrument.py    InstrumentSpec + RiskTierTable (35 tier, MM 공식)
  config.py        FeeSchedule / SlippageModel / FxFixing / PaperRunConfig
  book.py          Quote, BookSide, walk_book (호가 소모)
  account.py       Position(부호 수량) + Account(불변식 + 청산가)
  ledger.py        Ledger / InputTape (canonical JSONL)
  state.py         MANUAL / AUTO / AUTO_STOPPING / EMERGENCY 전이표
  engine.py        PaperEngine + replay_tape

backend/app/crypto/terminal/       라이브 계층
  feed.py          Bybit PUBLIC WS (orderbook.50 / tickers / kline.1) + REST kline seed
  session.py       입력 테이프 기록 -> 엔진 적용 (1 Hz + 명령 시 강제)
  api.py           독립 FastAPI 앱 (포트 8100)

backend/app/dev/crypto_paper_run.py   run config 생성 CLI (모든 출처 필드 필수)

frontend/lib/crypto-paper.ts              API 클라이언트 + 표시 형식 + 차트 기하
frontend/components/crypto-paper-terminal.tsx   패널 8종 + useCryptoTerminal
frontend/app/crypto-paper/page.tsx        화면 조립
```

### 설계 판단 5가지

**1. 주식 API에 route를 얹지 않고 독립 ASGI 앱으로 분리했다.**
기존 `app.main`의 lifespan은 DB, Kiwoom 프로파일, Strategy A/E 런타임을 켠다. 공개 데이터 기반
paper 터미널을 쓰려고 그 전부를 띄워야 할 이유가 없고, D2가 확인한 격리(`app.crypto`는 저장소의
다른 `app.*`을 하나도 import하지 않는다)를 그대로 유지할 수 있다. 실제로 이 패키지의 import는
`httpx`, `websockets`, `fastapi`, `pydantic`, 표준 라이브러리뿐이다.

**2. UI는 기존 구조를 그대로 재사용했다.** Next.js 15 + React 19 + Tailwind + vitest,
`PageHeader`/`MetricCard`/`StatusBadge`/`ErrorState`/`LoadingState`, `panel`·`btn-*` 디자인 토큰,
인라인 SVG 차트(프로젝트에 차트 의존성이 없고 추가하지 않았다). **새 프레임워크·새 npm 의존성 0개.**

**3. 입력 테이프가 엔진의 유일한 입력이다.** 엔진은 `time.time()`도 소켓도 난수도 읽지 않는다.
시각은 전부 입력에서 온다. 그래서 `config + input.jsonl`이 원장 바이트를 완전히 결정하고,
이것이 결정적 replay를 장식이 아니라 증명으로 만든다.

**4. 화면의 가격 authority는 하나다.** 브라우저는 Bybit에 직접 붙지 않는다. 표시되는 호가는
엔진이 테이프에 기록하며 본 그 호가다. 브라우저가 자체 피드를 가지면 "화면의 가격"과 "체결된
가격"이 갈라진다.

**5. 격리 마진(isolated)을 채택했다.** 포지션에 배정된 마진만 포지션을 뒷받침하므로, 지갑에 여유가
있어도 청산될 수 있다. cross margin은 범위 밖이며 이 선택을 run config에 기록한다.

---

## D. 필수 기능 17종 대응

| # | 요구 | 구현 | 위치 |
|---|---|---|---|
| 1 | Paper Account | `Account` (지갑/가용/사용마진/실현/미실현/equity) | `account.py` |
| 2 | LONG / SHORT | 부호 있는 수량. LONG>0, SHORT<0, one-way | `account.py::Position` |
| 3 | Market order 추상화 | `MARKET` 구현. `LIMIT`은 D3 범위 밖(J절) | `engine.py::submit_order` |
| 4 | LONG = executable ask | ask 소모. mid/last 체결 경로 없음 | `engine.py::_fill` |
| 5 | SHORT = executable bid | bid 소모 | 같음 |
| 6 | configurable slippage | `NONE` / `FIXED_BPS`. 호가 소모는 별도로 항상 적용 | `config.py::SlippageModel` |
| 7 | configurable fee | 출처·effective date·version 필수. 기본값 없음 | `config.py::FeeSchedule` |
| 8 | funding accrual | UTC 00/08/16 그리드, 개별 원장 항목 | `engine.py::_settle_funding` |
| 9 | leverage | 1~150, step 0.01, FLAT일 때만 변경 | `engine.py::set_leverage` |
| 10 | Initial / Maintenance Margin | IM=명목/레버리지, MM=티어 공식 | `account.py` |
| 11 | risk tier 청산 시뮬레이션 | 35 tier 실측 테이블 기반 | `instrument.py`, `engine.py::_check_liquidation` |
| 12 | Mark 기반 미실현·청산 | 평가도 청산도 mark. last는 표시만 | `account.py` |
| 13 | realized / unrealized PnL | 분리 집계, 원장이 authority | `engine.py::_settle_reduce` |
| 14 | equity / available | 불변식 2개로 매 변화마다 검사 | `account.py::assert_invariants` |
| 15 | manual LONG/SHORT/CLOSE | API 3종 + 화면 버튼 3종 | `api.py`, 터미널 UI |
| 16 | EMERGENCY CLOSE | 차단 후 청산, 실패 시 보고 | `engine.py::emergency_close` |
| 17 | 완전한 원장 | 13종 이벤트, canonical JSONL, dense seq | `ledger.py` |
| 18 | deterministic replay | `replay_tape`, 바이트 비교 | `engine.py` |

### 미해결이었던 계약 항목의 처리

`CRYPTO_PAPER_EXECUTION_CONTRACT_V1.md` 11절 X1~X6:

| # | 항목 | D3 처리 |
|---|---|---|
| X1 | 유지마진 티어 구조 | **해결.** D2가 35 tier를 수집했고, D3가 공식까지 검증했다(아래 E) |
| X2 | taker/maker 수수료 | **가정으로 명시.** 값·출처·effective date·basis를 run config와 화면에 노출 |
| X3 | KRW 환산 | **(a) 시작 시 1회 고정으로 확정.** 필수 run parameter, 기본값 없음 |
| X4 | qtyStep 미달 수량 | **REJECT으로 확정.** 내림하지 않는다 |
| X5 | maxMktOrderQty 초과 | **REJECT으로 확정** |
| X6 | 지정가 체결 판정 | **미해결.** LIMIT 자체가 D3 범위 밖 |

`CRYPTO_TRADING_STATE_MACHINE_V1.md` 5절 Y1~Y5:

| # | 항목 | D3 처리 |
|---|---|---|
| Y1 | EMERGENCY의 체결 가격 | **market taker로 확정.** 일반 CLOSE와 같은 경로를 쓴다 |
| Y2 | 재시작 시 상태 복원 | **미해결.** 프로세스를 다시 띄우면 새 런이다(J절) |
| Y3 | AUTO_STOPPING 무한 대기 | 해당 없음. AUTO 미구현 |
| Y4 | EMERGENCY 트리거 목록 | **사람만으로 확정.** 자동 트리거 없음 |
| Y5 | 청산 시 상태기계 반응 | **모드를 바꾸지 않는 것으로 확정.** 청산은 원장 사건이지 운용 모드 사건이 아니다 |

---

## E. D2가 UNKNOWN으로 남긴 유지마진 공식을 해결했다

D2 보고 D2-R2는 `mmDeduction`을 쓰는 정확한 MM 공식을 UNKNOWN으로 남겼다. D3에서 실측으로 닫았다.

**가설**: `MM(N) = N x maintenanceMarginRate(tier) - mmDeduction(tier)`

**검증**: 이 공식이 옳다면 tier 경계 `N`에서 MM이 연속이어야 하고, 그 조건은
`d(k+1) - d(k) == N x (mm(k+1) - mm(k))`이다.

**결과: 경계 34개 전부 Decimal 정확히 일치. 불일치 0건.**

| 경계 | 예시 검산 |
|---|---|
| tier 1 → 2 (N=300,000) | 510 - 0 = 300,000 x (0.005 - 0.0033) = 510 |
| tier 2 → 3 (N=2,000,000) | 1,710 - 510 = 2,000,000 x (0.0056 - 0.005) = 1,200 |
| tier 33 → 34 (N=400,000,000) | 98,382,510 - 50,382,510 = 400,000,000 x 0.12 = 48,000,000 |

tier 1의 `mmDeduction`이 빈 문자열인 것도 이 공식에서 **0**으로 해석되어야 맞다는 것이 함께
확인된다. 이 검증은 테스트로 고정했다
(`test_the_maintenance_margin_formula_is_continuous_across_every_real_tier_boundary`).

### 청산가 공식

포지션 자기자본이 유지요구액과 같아지는 mark를 푼 것이다.

```
IM + (M - E) * Q = |Q| * M * mmRate - deduction
M = (E*Q - IM - deduction) / (Q * (1 - sign(Q) * mmRate))
```

테스트는 기억한 상수가 아니라 **정의 자체로** 검증한다: 계산된 청산가에서 `IM + uPnL`과 MM의 차가
`1e-8` 미만임을 LONG/SHORT 양쪽에서 확인한다.

남은 가정 1개: 티어 선택 기준을 **현재 mark 기준 명목**으로 삼았다. Bybit가 이 계산에 어떤 명목을
쓰는지는 공개 endpoint에 없다(J절 D3-R2).

---

## F. 수수료와 환율: 발명하지 않았다

### 수수료

| 항목 | 값 |
|---|---|
| version | `bybit-usdt-perp-nonvip-2026-09` |
| taker | 0.00055 (5.50 bp) |
| maker | 0.0002 (2.00 bp) |
| effective date | 2026-09-09 |
| basis | **`ASSUMED_PUBLIC_NON_VIP`** |

**출처와 그 한계를 그대로 적는다.** Bybit 공식 수수료 페이지 2곳
(`bybit.com/en/help-center/article/Trading-Fee-Structure`,
`bybit.com/en/announcement-info/fee-rate/`)은 **60초 내 응답하지 않아 직접 확인하지 못했다.**
값은 2차 출처 2곳(bitsgap 2026-09-09 게시, bitdegree)이 서로 일치한 것을 채택했다.
게다가 계정이 없으므로 **실제 VIP 등급 자체를 알 수 없다.** 그래서 `basis`가 `ASSUMED`다.

이 값은 `FeeSchedule`에 `version`/`source`/`effective_date` 없이는 **생성되지 않는다.** 세 필드 중
하나라도 비면 run config 로딩이 실패한다. 화면 하단에도 그대로 노출된다.

### 환율

| 항목 | 값 |
|---|---|
| 1 USDT | 1,344.00 KRW |
| source | `upbit public ticker KRW-USDT (api.upbit.com/v1/ticker), trade_price` |
| asof | 2026-09-23T03:24:54Z |

시작 자본 KRW 1,000,000 → **744.0476190476190476190476190 USDT**로 1회 환산해 고정했다.
run 중 환율 변동은 전략 PnL에 반영되지 않는다(정책 P1 (a)).

Upbit는 **환율 참조에만** 쓴다. 상품 시세는 전부 Bybit PUBLIC이다. 주식 쪽 고정환율
`frontend/lib/fx.ts`(1,346.09)는 crypto에 쓰지 않으며, 화면에 두 환율이 같이 보이는 문제는
계좌 패널에 어느 환율로 환산했는지 명시하는 문구로 처리했다.

---

## G. 라이브 스모크

터미널: `uvicorn app.crypto.terminal.api:app --port 8100`, 화면: `http://127.0.0.1:3100/crypto-paper`
런 `paper-d3-live-001`, 2026-09-23 12:46 ~ 13:0x KST.

### 실제 수행한 수동 매매

| # | 동작 | 결과 |
|---|---|---|
| 1 | LONG 0.002 진입 | ask **86,714.80** 체결, 1호가 소모, fee 0.09538628, IM 17.34296, 청산가 78,301.72 |
| 2 | 20초 대기 | mark 86,740.40 → uPnL +0.0512, equity 744.0034 |
| 3 | CLOSE 0.002 | bid **86,753.50** 체결, 총손익 +0.07740, 수수료 0.09542885, 순손익 -0.01803 |
| 4 | 수량 0.0015 주문 | `QTY_OFF_GRID` 거부 |
| 5 | AUTO_ON | `AUTO_NOT_READY` 거부 |
| 6 | 레버리지 20x → SHORT 명목 300 USDT | 300/86,753.50 = 0.00345 → **0.003로 내림**, bid 체결, IM 13.013025, 청산가 **90,791.56**(진입가 위) |
| 7 | 포지션 보유 중 레버리지 변경 | `LEVERAGE_LOCKED_WHILE_OPEN` 거부 |
| 8 | EMERGENCY (미확인) | `CONFIRMATION_REQUIRED` 거부 |
| 9 | EMERGENCY (확인) | 이벤트 순서 **MODE_CHANGE → ORDER_SUBMITTED → FILL → FEE → POSITION_CLOSE**. 차단이 청산보다 먼저 |
| 10 | EMERGENCY 중 신규 진입 | `NEW_ENTRY_BLOCKED_EMERGENCY` 거부 |
| 11 | EMERGENCY 해제 | MANUAL 복귀 |
| 12 | SHORT 명목 400 USDT 진입 후 30초 관찰 | 아래 실시간 검증 |
| 13 | CLOSE 0.004 | 총손익 -0.03160, 최종 equity 743.2379 |

최종: 원장 32건, 입력 테이프 357건, 청산 0회, 피드 재연결 0 / 북 갭 0 / malformed 0.

### 실시간 변화의 독립 재계산 (5초 간격 6회, SHORT 0.004 보유 중)

| mark | uPnL 표시 | uPnL 재계산 | equity 표시 | equity 재계산 | margin ratio | 청산가 |
|---|---|---|---|---|---|---|
| 86,680.62 | -0.02208 | -0.02208 | 743.43815379761 | 743.43815379761 | 15.131 | 90,709.51 |
| 86,679.72 | -0.01848 | -0.01848 | 743.44175379761 | 743.44175379761 | 15.135 | 90,709.51 |
| 86,679.78 | -0.01872 | -0.01872 | 743.44151379761 | 743.44151379761 | 15.134 | 90,709.51 |
| 86,682.90 | -0.03120 | -0.03120 | 743.42903379761 | 743.42903379761 | 15.123 | 90,709.51 |

6회 전부 일치. 청산가가 고정인 것은 정상이다. 진입가·레버리지·티어만의 함수이고 mark는 그 안에
들어가지 않는다.

### 화면 렌더링 실측

헤드리스 크롬으로 실제 페이지를 띄워 값을 읽었다(캡처
`data/runtime/crypto/_d3_logs/shots/terminal.png`, `position.png`).

```
mark 86,682.5 · bid 86,681.1 · ask 86,681.2 · spread 0.10
equity 743.65 USDT · available 743.65 USDT · realized +0.0804 USDT
포지션 패널: SHORT · 리스크 티어 1 · 청산가 90,709.5 · 수량 0.004 BTC
            진입가 86,675.1 · Mark 86,685.7 · 레버리지 20x
            사용 마진 17.34 USDT · 유지 마진 1.1443 USDT · 마진 비율 15.11x
버튼: LONG 활성 · SHORT 활성 · CLOSE 활성(보유 시) · AUTO 비활성 · EMERGENCY 확인 전 비활성
원장 24행 렌더 · 차트 SVG path 존재
콘솔 에러: 주식 대시보드 API(8000) 연결 거부 1건 (아래 J절 D3-R5)
```

---

## H. 회계 대조

실제 라이브 원장(`paper-d3-live-001/ledger.jsonl`)으로 검증했다.

### 원장 vs 계좌

| 항목 | 원장 합계 | 계좌 상태 | 일치 |
|---|---|---|---|
| realized | 0.08040 | 0.08040 | 일치 |
| fees | 0.4771000300 | 0.4771000300 | 일치 |
| funding | 0 | 0 | 일치 |

### 불변식

```
A6  equity = 시작자본 + 실현 - 현금비용 + 미실현
    743.6509190176190476190476190 == 743.6509190176190476190476190   성립
P5  equity = 주문가능 + 사용마진 + 미실현
    743.6509190176190476190476190 == 743.6509190176190476190476190   성립
```

`assert_invariants`는 **모든 상태 변화 직후** 호출되며, 위반 시 값을 보고하는 대신 예외를 던진다.

### 낙관 체결 검사 (계약 T6)

| 체결 | 가격 | bid | ask | 판정 |
|---|---|---|---|---|
| LONG OPEN | 86,714.80 | 86,714.70 | 86,714.80 | ask에서 체결 |
| LONG CLOSE | 86,753.50 | 86,753.50 | 86,753.60 | bid에서 체결 |
| SHORT OPEN | 86,753.50 | 86,753.50 | 86,753.60 | bid에서 체결 |
| SHORT CLOSE | 86,752.50 | 86,752.40 | 86,752.50 | ask에서 체결 |

**위반 0건.** mid나 last로 체결된 건은 하나도 없다.

### 비용 이중차감 검사 (계약 T3)

| 검사 | 결과 |
|---|---|
| FILL에 `PRICE_EMBEDDED` 표기 | 4/4 |
| FEE·FUNDING에 `CASH_SEPARATE` 표기 | 4/4 |
| `gross_pnl`이 체결가 기준인지 재계산 대조 | 전건 일치 |
| `net_pnl == gross_pnl - fee` | 전건 성립 |
| FEE 건수 == FILL 건수 | 일치 (한 체결당 정확히 한 번) |

### 결정적 replay (계약 T1/T8)

```
입력 테이프 120건 → 원장 24건
원장 바이트 일치: MATCH
2회차 replay 일치: MATCH
```

디스크에 쓴 테이프를 다시 읽어 replay해도 `ledger.jsonl` 바이트와 같다.

---

## I. 테스트

### 신규

| 파일 | 개수 | 범위 |
|---|---|---|
| `backend/tests/crypto/test_paper_engine.py` | 43 | 체결·사이징·수수료·슬리피지·펀딩·레버리지·마진·청산·티어·mark vs last·reconnect·불변식 |
| `backend/tests/crypto/test_paper_state_and_replay.py` | 15 | 상태기계·EMERGENCY·원장 결정성 |
| `backend/tests/crypto/test_paper_terminal_api.py` | 18 | 피드·세션·config 로딩·HTTP 엔드포인트 |
| `frontend/components/crypto-paper.test.tsx` | 22 | 시세·호가·계좌·포지션·주문 티켓·원장·근거 표시 |

요구된 테스트 항목 대응:

| 요구 | 테스트 |
|---|---|
| long fill | `test_market_long_fills_on_the_ask_and_never_at_mid_or_bid` |
| short fill | `test_market_short_fills_on_the_bid_and_never_at_mid_or_ask` |
| close | `test_close_releases_margin_and_returns_the_position_to_flat` |
| reverse 금지 | `test_reversing_through_zero_is_refused_in_both_directions` |
| partial sizing | `test_partial_close_keeps_the_entry_average_and_realizes_only_the_closed_part` |
| fee | `test_fee_is_charged_once_from_cash_and_never_folded_into_the_fill_price` |
| slippage | `test_slippage_moves_the_fill_adversely_on_both_sides` |
| funding | `test_funding_is_paid_by_the_long_and_received_by_the_short_at_the_eight_hour_grid` 외 3건 |
| leverage | `test_leverage_sets_the_margin_and_only_changes_while_flat` 외 1건 |
| margin | `test_an_order_needing_more_margin_than_is_available_is_refused` |
| liquidation | `test_a_long_is_liquidated_when_the_mark_reaches_the_computed_price` 외 4건 |
| risk tier transition | `test_growing_a_position_across_a_tier_boundary_raises_the_requirement_continuously` 외 3건 |
| insufficient margin reject | 위 margin 테스트 + `LEVERAGE_ABOVE_TIER`, `RISK_LIMIT_EXCEEDED` |
| mark vs last divergence | `test_valuation_and_liquidation_follow_the_mark_even_when_last_diverges` |
| reconnect during open position | `test_an_open_position_survives_a_feed_reconnect_and_orders_wait_for_the_book` |
| ledger determinism | `test_a_replay_of_the_recorded_inputs_reproduces_the_ledger_byte_for_byte` 외 2건 |
| account invariant | `test_the_account_invariants_hold_after_every_event_of_a_full_cycle` |
| emergency close | `test_emergency_blocks_before_it_closes` 외 3건 |

### 회귀

```
backend:  3919 passed, 1 skipped, 16 warnings in 1413.11s (0:23:33)
frontend: crypto-paper.test.tsx  22 passed
```

- 실행:
  `PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests -p no:randomly`
  `--ignore=backend/tests/strategy_b/test_strategy_b_scanner.py`
  `--ignore=backend/tests/test_strategy_b_historical_scanner.py`
  그리고 `cd frontend && npx vitest run components/crypto-paper.test.tsx`
- **실패 0건.** crypto 추가로 인한 회귀는 없다.
- 제외한 2개 파일은 D2 이전부터 collection error 상태인 untracked Strategy B 테스트다. D3가 만든
  문제가 아니며 건드리지 않았다.
- `npx tsc --noEmit` 통과. 새 npm 의존성 0개.
- 참고: D2 시점 회귀는 3,788 passed / 23 skipped였다. 늘어난 131건 중 76건이 D3 crypto 테스트이고,
  나머지와 skip 감소(23 -> 1)는 그 사이 다른 세션이 올린 커밋 `f308ce7`·`a774047`의 것이다.

---

## J. 위험과 UNKNOWN

| # | 항목 | 상태 |
|---|---|---|
| D3-R1 | taker/maker 수수료율 | **가정.** 공식 페이지 응답 실패 + 계정 없음. `ASSUMED_PUBLIC_NON_VIP` |
| D3-R2 | 청산 계산의 티어 선택 명목 기준 | **가정.** 현재 mark 명목을 썼다. Bybit 기준은 비공개 |
| D3-R3 | 청산 체결 가격 | **가정.** `MARK_AT_TRIGGER`. 실제 거래소는 파산가·청산엔진을 쓴다 |
| D3-R4 | 유지마진에 청산 수수료 예치분이 포함되는지 | **UNKNOWN.** 티어 공식만 적용했다 |
| D3-R5 | 화면 상단 shell이 주식 대시보드 API(8000)를 호출한다 | 확정. 꺼져 있어도 화면은 정상 동작하며, 콘솔 에러 1건과 헤더 "연결 중…"만 남는다 |
| D3-R6 | 화면 표시 시세는 1 Hz로 기록된 호가 | 확정. 주문은 그 순간 최신 호가를 강제 기록 후 체결하므로 표시가와 체결가가 최대 1초 다를 수 있다 |
| D3-R7 | 프로세스 재시작 시 포지션 복원 | **미구현.** 다시 띄우면 새 런이며 이전 포지션을 잇지 않는다 |
| D3-R8 | LIMIT 주문 | **미구현.** enum에도 넣지 않았다. 계약 O6/O7 미이행 |
| D3-R9 | funding 요율은 tickers의 예상치 | 확정. 정산 시점 확정치와 다를 수 있다 |
| D3-R10 | 격리 마진만 지원 | 확정. cross margin 범위 밖 |
| D3-R11 | 나비게이션에 메뉴를 추가하지 않았다 | 의도. `app-shell.tsx`/`section-tabs.tsx`를 다른 세션이 동시에 편집 중이라 URL 직접 접근으로 남겼다 |
| D3-R12 | 백그라운드 프로세스가 세션과 함께 죽는다 | 확정. 터미널·dev 서버는 운영용 서비스가 아니다 |

---

## K. D3 판정

| # | 종료 조건 | 판정 | 근거 |
|---|---|---|---|
| 1 | 실제 공개 BTCUSDT 시세를 보면서 | **PASS** | Bybit PUBLIC WS 실시간, 피드 재연결 0·malformed 0 |
| 2 | 수동 LONG/SHORT/CLOSE 수행 | **PASS** | 13개 동작 실행. 체결 6건 전부 executable 측 |
| 3 | 가상 100만원 계좌의 손익 | **PASS** | 원장 vs 계좌 전항목 일치, 불변식 2개 성립 |
| 4 | 마진 | **PASS** | IM/MM/가용/마진비율 독립 재계산 일치 |
| 5 | 청산가격이 실시간으로 정확히 변한다 | **PASS** | 6회 표본 독립 재계산 전부 일치. 청산가는 정의상 mark 불변 |
| 6 | Edge/Score/Probability/AUTO 0줄 | **PASS** | AUTO는 상태 enum에만 존재, `AUTO_NOT_READY`로 거부 |

## **D3 = PASS**

단, 3개 값이 가정 위에 서 있다(D3-R1 수수료, D3-R2 티어 기준, D3-R3 청산 체결가). 셋 다
run config와 원장에 가정임이 기록되며 화면에도 표시된다. 가정을 사실로 바꿀 방법은 현재
공개 데이터에는 없다.

---

## L. D4 제안 범위

**포함 후보**

1. **런 지속성**: 프로세스 재시작 시 원장·테이프에서 포지션과 모드를 복원(D3-R7). 복원이 곧
   replay이므로 이미 있는 결정성 위에 올린다.
2. **LIMIT 주문**: 계약 O6/O7의 체결 판정(touch 대 trade-through)을 먼저 정하고 구현(D3-R8, X6).
3. **성과 집계 계층**: `CRYPTO_METRICS_AND_GATE_V1.md`의 지표를 crypto 원장 위에 다시 구현.
   청산 횟수는 수익률과 **별도로 반드시 보고**한다(계약 Q7).
4. **historical replay 모드**: 같은 엔진에 라이브 테이프 대신 2021-02 이후 과거 데이터를 먹인다.
   연구 구간 계약 `CRYPTO_RESEARCH_WINDOW_V1.md`가 입력 경계다.
5. **수수료·환율 재확인 절차**: Bybit 공식 페이지가 응답할 때 값을 다시 확인하고, 다르면
   새 `fee_version`으로 갈아끼운다. 과거 런은 자기 version을 그대로 들고 있어야 한다.

**제외 (D4에서도 금지)**

- Edge / Score / Probability / AUTO 진입·청산 판단
- 실제 주문 전송, LIVE adapter
- 계정·API key·private endpoint
- 전략 파라미터 탐색

**선결 조건**

- D4.2는 X6(지정가 체결 판정) 결정이 먼저다.
- D4.3은 PASS threshold를 지금 발명하지 않는다는 계약 P6을 지켜야 하므로, 지표 정의만 만들고
  임계값은 비워 둔다.
