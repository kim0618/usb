# US-B STRATEGY A · EDGE-FIRST CENSUS C1 V2 · F1/F3 EXACT-A VALIDATION · PREREGISTRATION

작성 2026-10-19 (KST). C1 결과 계산 전에 SHA256을 고정한다(`PREREGISTRATION_C1_V2.sha256`).
기존 `PREREGISTRATION_C0_C1.md`(sha `3baea825…3162`)는 그대로 보존한다. 그 §6.2(09:31 진입, ATR14 1R, 15:59 청산)는 **실행하지 않는다**. 이번 연구 질문("후보만 바꾸고 A 실행은 동결")과 맞지 않기 때문이며, C1 결과를 본 적이 없어서 이 교체는 사후 튜닝이 아니다.

## 0. 범위와 금지
- 운영 변경 0, A Paper 변경 0, 서비스 재시작 0, DB 쓰기 0, 실주문 0, commit/push/deploy 0. 산출물은 gitignored 경로(`data/runtime/research_reports/a_edge_first_census_v1/`)에만 쓴다.
- 운영 A Paper의 forward 결과는 규칙 선택·임계값·판정에 쓰지 않는다.
- Human/GPT 제외. 모든 후보는 APPROVE-equivalent(`research_modes`: ALL_RESEARCH_SYMBOLS).
- 결과 이후 F1/F3 임계값 변경, 새 family·조합 추가, threshold sweep 금지. F1/F3가 닫히면 이번 메뉴는 CLOSED.

## 1. 연구 질문
기존 Strategy A의 premarket / OR15 / entry / risk / execution / exit 규칙을 전혀 바꾸지 않고 candidate universe만 F1 또는 F3로 바꾸면, gross edge가 있고 그 edge가 execution cost를 이기는가.

## 2. Family (CLOSED, C0와 동일)
- 후보 원천 = C0 산출 `c0_candidates.csv` (sha256 `303be9f236544b6f2c598c86d69d4e58f5f471741d36f01400ab80dd8f9ce9d8`)의 F1/F3 행. 재계산하지 않는다.
- F1: close_D/prev_close − 1 ≥ 5%, volume_D ≥ 2.0 × 직전 20세션 평균. F3: close_D ≥ 직전 252세션 최고가. COMMON: close ≥ $5, 시총 ≥ $300M(PIT shares × close_D). 정의 세부는 C0 사전등록 §2~§3.
- F2 제외(C0_DATA_LIMITED).

## 3. Date contract
- 후보 세션 D, 진입 세션 E = D 다음 XNYS 세션. Original-A형 D→D+1 universe 연구이며, 현재 same-day Mover 운영 계약 연구가 아니다.
- 후보는 D 장 종료까지의 정보로만 생성(C0에서 이미 동결). E 데이터는 진입 엔진 평가에서만 쓴다. ranking 없음.

## 4. DATA WINDOW (실측, 2026-10-19 KST)
- **Remount 미수행**: `sudo mount`는 비밀번호가 필요해 실행할 수 없었다. 마운트(`G: on /mnt/g type 9p drvfs`)는 살아 있고, 아래 전수 읽기로 실제 접근을 확인했다.
- 원천: `G:/내 드라이브/1_US-B/market_data/raw/massive/minute` (Massive minute, unadjusted, 04:00~20:00 ET)
- 심볼 폴더 3,889개, 전 페이지 실제 읽기(gzip 해제·타임스탬프 파싱), 원장 sha256 대조
- **손상/읽기 실패 0, sha 불일치 0, 원장 없는 페이지 0**
- 바가 하나라도 있는 세션 501개(2024-09-17 ~ 2026-09-16)
- **market-wide covered sessions = 104** (정규장 바 보유 심볼 ≥ 최대치의 50%/80%/90% 기준 모두 동일)
  - **first = 2026-04-20, last = 2026-09-16, sessions = 104**, 그 사이 grid 세션 누락 0
  - 세션당 정규장 바 보유 심볼: min 3,361 / p10 3,410 / median 3,534 / max 3,644
  - 세션당 정규장 380분 이상 보유 심볼: min 668 / median 872 / max 1,046 (저유동 종목은 거래 없는 분에 바가 없음, 손상 아님)
- uncovered: 창 밖 397세션은 세션당 1~166심볼(희소, market-wide 아님) → C1에서 제외
- forward 폴더(`market_data/forward/massive/minute`)는 1,037심볼 × 2026-09-17~21뿐이라 market-wide가 아니어서 쓰지 않음
- 기존 "104 sessions" 숫자는 실측과 일치(우연이 아니라 같은 원천)

### C1 표본 창
- D ∈ covered 이고 E ∈ covered → D ∈ [2026-04-20, 2026-09-15], E ∈ [2026-04-21, 2026-09-16]
- 이 창은 F1/F3 모두 C0 YEAR_B 안에 있다. F3는 C0에서도 YEAR_B만 평가 가능했다.

### C0 → C1 정합 (결과 보기 전 계산)
| | F1 | F3 |
|---|---|---|
| C0 후보(CSV, D+1 바 없음 포함) | 11,601 | 15,891 |
| C1 창 밖 | 9,269 | 9,615 |
| C1 창 안 | 2,332 | 6,276 |
| 분봉 누락: 심볼 폴더 없음 | 51 | 116 |
| 분봉 누락: D 정규장 바 없음 | 21 | 24 |
| 분봉 누락: E 바 없음 | 2 | 5 |
| **C1 eligible** | **2,258** | **6,131** |
| 분봉 누락률 | 3.2% | 2.3% |

## 5. Production parity (실제 코드값)
운영 서버 `traderj:/root/usb` HEAD `8f19e63`의 파일 sha256과 로컬 `HEAD`를 대조했다. 아래 파일은 운영 = 로컬 HEAD = C1 실행 트리(바이트 동일):
`strategy/config.py ec47e92c…`, `strategy/engine.py 435f510f…`, `strategy/indicators.py a5147864…`, `strategy/lifecycle.py f4673ec8…`, `strategy/domain.py 3011001d…`, `risk/config.py 533ebfc1…`, `risk/engine.py 5e88f397…`, `execution/config.py 75a9ca49…`, `services/entry_management_runtime.py e63018fb…`(+ shim 추가분, 아래), `services/position_lifecycle.py e0686a7c…`, `services/end_of_day_lifecycle.py 6e24d13d…`, `services/entry_capacity.py`(로컬 리팩터, 아래).
운영 경로는 `StrategyConfig()`, `RiskConfig()`, `ExecutionConfig()` 기본값을 쓴다(env 덮어쓰기 없음).

| 항목 | 운영 코드값 | C1 runner가 읽는 곳 |
|---|---|---|
| gap lower / upper | 0.02 / 0.15 (UP) | `CurrentPaperConfig.read().strategy` = `StrategyConfig()` |
| premarket volume ratio min | 0.05 (분모 = 20일 일봉 평균, V2 daily aggregate) | 동일 + `PREMARKET_VOLUME_BASIS_V2` |
| previous regular close | D 정규장 마지막 분봉 종가(`previous_regular_close`) | `build_premarket_context` (운영 함수) |
| OR minutes | 15 | StrategyConfig |
| breakout | close > OR high 그리고 close > VWAP | `StrategyV0Engine` (운영 파일) |
| entry deadline | 10:30 ET | StrategyConfig |
| entry attempts / symbol | 1 | StrategyConfig |
| entry reference tolerance | 0 | StrategyConfig |
| risk per trade | 0.5% of session equity | `RiskConfig()` |
| capacity | base 80% / pyramid reserve 20% / symbol exposure 60% | RiskConfig |
| 포트폴리오 caps | new symbols/day 3, open 3, daily risk units 3 | RiskConfig (census는 후보별 독립 계좌라 구속하지 않음, §6) |
| ATR | period 14 (분봉) | StrategyConfig |
| stop | 신호 시 엔진 initial stop(OR low 기반) | engine |
| trailing | activation 1R, ATR × 1.5 (GPT profile UNKNOWN → NORMAL 1.5) | engine + variant C |
| pyramid | strategy 1 / risk 1 (+1R 확인) | StrategyConfig + RiskConfig |
| overnight / Day2 | variant C allow_overnight, max_holding 2, GPT overnight_suitability MEDIUM/HIGH + closing review(마감 10분 전) | engine; GPT 없음 → UNKNOWN_CLOSE |
| execution | spread 10 + slippage 5 + commission 10 bp/leg, fill_delay_bars 1, WORST_CASE | `ExecutionConfig()` + `SimBroker` |
| 시작 자본 | 7,428.92 USD (`PAPER_INITIAL_CASH`) | CurrentPaperConfig |
| config fingerprint | `d768cef8ecf55ce440ef3f0988270e5bc1c8943c0af5f1c06efba58f9e6ed66c` | |

Runner는 이 값들을 하드코딩하지 않는다. `CurrentPaperConfig.read()`로 같은 config 객체를 읽어 `StrategyV0Engine(paper.strategy)`, `risk_config=paper.risk`, `execution_config=paper.execution`로 넘긴다.

**재생 경로**: TRUE PARITY V1 도구 `MultiSymbolPortfolioReplay`(TOP8 baseline 러너와 같은 조립). 이 도구는 미배포 리팩터(로컬 untracked `position_protection.py`, `book_snapshots.py`, `entry_capacity.entry_capacity`)와 미커밋 추출 함수(`SettlementAction`, `entry_settlement_verdict`, `entry_fill_risk`)에 의존한다. 후자는 현재 어떤 커밋에도 없어 재생 인프라가 원래 import 불가 상태였다(메모리의 "SettlementAction 선행 파손"). 스크래치 트리에서만 아래 shim으로 맞췄다:
1. `entry_management_runtime.py` = 운영 HEAD 원본 + 끝에 추출 함수 3개 추가(이전 A 연구 동결 트리에서 원문 복사, 운영 인라인 로직과 diff로 동일 확인, `entry_fill_risk`의 outlay = `-fill_cash_flow` = 운영의 `fill_price*qty + commission + fx`). 운영 메서드는 수정 없음.
2. `position_protection.py`: `PROTECTIVE_EXIT_REASONS`에서 연구용 `TIME_PROGRESS_STOP` 제거(운영 집합과 동일), `evaluate_position`에 넘기던 연구용 `entered_at` 인자 제거(운영 시그니처). 나머지 보호 함수는 운영 `position_lifecycle.py` 인라인과 AST 동일(closing_review_at은 docstring만 다름).
3. 재생 parity 테스트(스크래치 트리): 183 passed / 2 failed / 1 skipped. 실패 2건 = "durable 서비스와 재생이 같은 모듈을 import" 구조 검사(운영 HEAD는 인라인이라 의도된 불일치)와 alembic 상대경로 환경 문제. 동작 차이 실패 0.
- 운영 `broker/sim.py`는 HEAD와 다르지만 차이는 E 전용 `price_field` 옵션(기본 open)뿐이라 A 동작 동일.
- 스크래치 트리 전체 파일 digest: `c1v2/SCRATCH_TREE_SHA256.txt` (그 파일의 sha256 `48c4e8dd…7c92`), shim 파일 사본 `c1v2/scratch_tree_shims/`.

**PRODUCTION A PARITY = PASS (shim 2건 공개, 동작 경로 바이트 동일)**

## 6. Census 실행 규칙
- 후보 1건 = 독립 계좌 1개(시작 자본 7,428.92, 운영 sizing 그대로). TOP8 제한 없음, 3-slot 제한 없음, Kiwoom 호출예산 없음. 같은 날 다른 후보와 현금·슬롯을 공유하지 않는다. 결과는 family population 측정이며 PORTFOLIO PASS가 아니다.
- `PortfolioConfig(trading_dates=(E,), settlement_sessions=1, premarket_volume_basis=V2)`, candidates = 그 1건(rank 1).
- 분모 일봉 = grouped daily(Massive daily aggregate, V2 basis), E − 60일 ~ D, `available_at = tape_end_at(day)`.
- 분봉 = Drive raw minute, 해당 심볼의 D·E(·E+1이 covered면 포함) 세션.
- **Overnight/Day2**: 운영은 GPT `overnight_suitability`가 MEDIUM/HIGH일 때만 carry한다. census는 GPT를 제외하므로 엔진의 UNKNOWN 규칙(UNKNOWN_CLOSE, carry 거부)이 적용되어 사실상 **SAME_DAY_ENGINE_UNKNOWN** 팔이다. 억지 대체는 하지 않는다. production exact와의 차이: GPT가 carry를 허락했을 거래의 Day2 손익이 빠진다. E+1이 covered 밖(E = 2026-09-16)이면 settlement 세션 데이터가 없다.
- Trailing도 GPT profile UNKNOWN → NORMAL 1.5 (엔진 자체 매핑).

## 7. 퍼널 (family별, 세션당 평균 포함)
candidate rows, unique symbols, candidate sessions → D+1 data eligible(C1 eligible) → gap pass(0.02 ≤ gap ≤ 0.15) → volume pass(ratio ≥ 0.05, gap과 독립 집계) → full premarket pass → OR formed → entry signal → execution accepted → PRICE_ABOVE_LIMIT reject / other reject → trades → same-day exits / overnight·Day2 exits.

## 8. 비용 층 (거래별, 체결 기록에서 계산)
체결 행동(PRICE_ABOVE_LIMIT 판정 포함)은 운영 execution_v0 그대로 한 번만 실행하고, 세 층은 같은 체결의 회계만 다르다.
- GROSS = Σ(SELL raw_market_price×qty) − Σ(BUY raw_market_price×qty) (spread·slippage·commission 없음)
- FEE_ONLY = GROSS − Σ commission(raw 기준 10bp/leg)
- EXECUTION_V0_NET = GROSS − Σ total_cost(spread 10 + slippage 5 + commission 10 = 25bp/leg) = 엔진 net_pnl (일치 검사, 불일치 시 INVALID)
- R = 각 층 P&L / 엔진 initial_risk(진입 시 1R 달러)
- 층별 출력: Total R, Avg R, PF(Σ양수 R / |Σ음수 R|), Return %(= Σ net P&L / 7,428.92, 후보별 독립 계좌 합산이라 포트폴리오 수익률 아님), MDD(청산 시각순 누적 R의 최대 낙폭, R 단위와 % 단위), Win rate, Avg win R, Avg loss R
- Execution cost R(= Σ total_cost / initial_risk 합산), Fee-only cost R, Cost / |gross P&L 합|, Cost / gross turnover(Σ 양 leg raw notional)

## 9. 표본·판정 (EXECUTION_V0_NET 기준)
- trades < 50 → INSUFFICIENT (FAIL이라 부르지 않음, PASS 아님)
- 50 ≤ trades < 100 → EVALUABLE, ≥ 100 → STRONG_SAMPLE
- 순서:
  - A. GROSS_FAIL: Gross Avg R ≤ 0 또는 Gross Total R ≤ 0 → CLOSED (비용 탓 해석 금지)
  - B. COST_BOUND: Gross > 0 이고 Net Total R ≤ 0 → CLOSED
  - C. NET_FRAGILE: Net > 0 이지만 PF < 1.20, 또는 앞/뒤 절반 중 하나 ≤ 0, 또는 Top5 > 50%, 또는 Net Avg R ≤ 0 → CLOSED
  - D. EDGE_PASS: Net PF ≥ 1.20, Net Total R > 0, Net Avg R > 0, 앞·뒤 절반 모두 > 0, Top5 winning trades contribution < 50%, trades ≥ 50 → RANKING_RESEARCH 후보
  - E. INSUFFICIENT는 A~D보다 먼저 적용
- Robustness: 진입 시각순 정렬, 앞 절반 = 첫 ⌊n/2⌋건, 뒤 절반 = 나머지. Top5 winning trades contribution = 상위 5건 net R 합 / Net Total R.
- 추가 공개(판정 아님): Top5 symbols contribution(심볼별 net R 합 상위 5 / Net Total R), unique traded symbols, repeat-trade ratio(trades / unique symbols), 같은 심볼 최대 연속 거래 세션 수, 심볼 집중도(최빈 심볼 거래 비중). F3는 MU 등 반복 종목 별도 표.
- 전체 라벨: F1·F3 모두 EDGE_PASS → F1_F3_EDGE_PASS, 하나만 → F1_EDGE_PASS / F3_EDGE_PASS, 둘 다 INSUFFICIENT → DATA_INSUFFICIENT, 그 외 → NO_FAMILY_EDGE.
- 무효 조건: PIT violation > 0, invariant violation > 0, 엔진 net ≠ GROSS − total_cost, REPLAY_ERROR가 eligible의 5% 초과 → 해당 family INVALID로 보고하고 성과 판정 안 함.

## 10. 사전 공개: 스모크 실행
SHA 고정 전에 러너 동작 확인용으로 3심볼(AAPL·MU·OKLO) 30후보를 돌렸다. 본 것은 상태(OK 30), premarket 사유 분포(GAP_TOO_LOW 21, PASS 8, LOW_PREMARKET_VOLUME 1), 거래 1건, PIT 0, invariant 0이다. 거래의 R·손익은 열지 않았다. 이 스모크로 바꾼 규칙은 없다.
