# Strategy B - B-E1 연구 계획 초안

상태: **DRAFT_READY_FOR_USER_REVIEW** (FROZEN 아님, 사전등록 아님. 사용자 승인 전 freeze 금지)
작성: 2026-09-22
전제: B-E0 V1 authoritative 결과 = **FAIL** (run `be0-995dec075c1f4aea610a`). 이 문서는 B-E0 결과를 바꾸지 않는다.

이 문서는 전략 코드가 아니다. B-E1에서 무엇을 사전등록할지 고르기 위한 재료와, 그 후보가 Kiwoom 실시간
환경에서 똑같이 재현되는지의 판정을 모은다. 여기 적힌 임계값은 전부 UNDECIDED다.

---

## 1. 현재 단계

Strategy B는 **연구/검증 단계**다. production runtime, Kiwoom 주문, realtime worker는 없다.

| 단계 | 상태 |
| --- | --- |
| B-E0 사전등록·동결 | 완료 (`6e3d69af`) |
| B-E0 authoritative 84세션 | 완료, **FAIL** |
| B-E0 신호 탐색 분해 | 완료 (탐색용, 검증 아님) |
| DIAG_ZERO_COST / CF_SIGNAL_BAR | 완료 (§11) |
| B-E1 가설 선택·사전등록 | 진단 완료 후 |
| Kiwoom realtime shadow | 미착수 |

## 2. B-E0 탐색 사실 (고정)

출처: `data/runtime/strategy_b_e0/analysis/signal_edge_decomposition_v1.json`

- 신호 382, 종목 338, 세션 83, 체결 219
- 신호 봉 close → 다음 봉 open: 중앙 0.00%, 평균 -0.023%
- 30분 MFE 중앙 +1.84%, MAE 중앙 -1.85%, 30분 수익률 중앙 -0.09%
- ±1% 선도달 UP 183 / DOWN 185, ±2% UP 153 / DOWN 143 → 전체 신호 방향성 없음
- 부분집합 후보(5개월 모두 같은 방향, 같은 표본에서 발견 → 검증 아님):
  - 거래량 가속(최근 5분/직전 5분) 상위: 30분 수익률 중앙 양수(4/5개월), 하위는 5/5개월 음수
  - 과확장(VWAP 이격·1분 상승·돌파폭 상위): MAE ≤ -3% 비율 0.40 vs 0.21
- 체결 거래: MFE30 중앙 0.76R, 30분 내 -1R 도달 51%, 손절폭 중앙 2.2% ≈ 신호 후 노이즈(MAE 중앙 1.85%)

## 3. Kiwoom 실시간 호환성 게이트

B-E1 production-track 후보 feature는 네 게이트를 모두 통과해야 한다.

1. `HISTORICAL_AVAILABLE` - B-E0 데이터(Massive 분봉·grouped daily)로 계산 가능
2. `REALTIME_KIWOOM_AVAILABLE` - Kiwoom FE/FT/REST로 계산 가능
3. `POINT_IN_TIME_AVAILABLE` - 판단 시각 이전 정보만 사용
4. `LIVE_EXECUTION_FEASIBLE` - 실시간 주문 경로로 실행 가능

판정 값: `PASS`, `PASS_WITH_SEMANTIC_DIFFERENCE`(차이를 문서화하고 사전 승인한 경우만 사용 가능), `BLOCKED`(사용 금지), `UNKNOWN`(미실측, 승격 전 해소 필요).

### 3.1 근거로 쓴 기존 실측 (추측 아님)

출처: `docs/backtest/STRATEGY_B_PREVALIDATION.md`, 메모리 `usb_kiwoom_realtime_probe`, `docs/KIWOOM_INTEGRATION_SPIKE.md`

| 사실 | 값 | 출처 |
| --- | --- | --- |
| FE 성격 | 개별 체결이 아닌 집계 스냅샷(대형주 초당 약 10건), FT 초당 1건 | 정규장 probe 2026-09-17 |
| FE 체결량 합 vs 누적거래량 차분 | Σ\|FE 15\| = FE 13 분 차분, 비율 1.0000 | 정규장 probe |
| 실시간 VWAP | 체결 누적 VWAP = 14/13 파생 VWAP, 6자리 일치 | 정규장 probe |
| WS 분 거래량 vs Kiwoom REST 분봉 | 정확 일치(OHLC 포함, 4종목) | 정규장 probe |
| Kiwoom 분 거래량 vs Massive 분 거래량 | 약 0.70배(9종목 중앙 0.696, 원인 UNKNOWN) | PREVALIDATION |
| 누적 일 거래량·일중 고저 | Kiwoom = Massive grouped daily 정확 일치 | PREVALIDATION |
| 정규장 가격 | 대형주 close p95 ≤ 1.5bps, HOD/LOD 동일 | PREVALIDATION |
| 프리마켓 가격·HOD | 불일치 | PREVALIDATION |
| 소형주 분봉 | Kiwoom에만 있는 소량 체결 분이 많음(density·halt 달라짐) | PREVALIDATION |
| 분 마감 후 도착 | FE 최대 +0.25초 → AVAILABILITY_DELAY 1분 안전 | 정규장 probe |
| 구독 한도 | 공식 스펙 JSON에 없음, repo 문서 "200"은 미재검증, 실측 안전 26종목(약 52 symbol×type) | PREVALIDATION |
| 구독 변경 | REG 멱등, REMOVE 후 2초 뒤 이벤트 0, 존재하지 않는 종목도 ack 0 | overnight probe |
| REST 한도 | US 조회 5/초(09:00~10:00 KST 3/초), 차트 합계 20/초, 주문 10/초(피크 3/초) | SPIKE |
| 스프레드 | FT로 실시간 계산 가능, 과거(Massive Basic)는 호가 없음(NBBO 403) | probe, B-E0 계약 |

## 4. Historical ↔ Realtime Feature Matrix

| Feature | 과거 소스 | 실시간 소스 | 공식 동일 | 의미 동일 | PIT | Kiwoom 가능 | 위험 | 판정 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Price (last close) | Massive 분봉 close | FE 집계 분봉 close | 예 | 대형주 예, 소형주 분 단위 차이 | 예 | 예 | 소형주 추가 분 | PASS_WITH_SEMANTIC_DIFFERENCE |
| 1m/3m/5m return | 실제 분봉 clock return | FE 집계 분봉 | 예 | 정규장 예. 단 `return_scope=EXTENDED_DAY`라 09:30~09:35는 PM 가격 참조(소스 불일치) | 예 | 예 | 개장 직후 5분 | PASS_WITH_SEMANTIC_DIFFERENCE (정규장 scope로 한정 시 PASS 후보) |
| Volume (절대) | Massive `v` | FE 15 합 | 예 | 아니오(약 0.70배) | 예 | 예 | 절대 임계값 이동 | PASS_WITH_SEMANTIC_DIFFERENCE, 절대 임계값 금지 |
| Volume acceleration | 최근 5분/직전 5분(같은 소스 비율) | 같은 식, FE 분봉 | 예 | 균일 스케일은 상쇄, 소형주 추가 소량 분은 미상쇄 | 예 | 예 | 소형주 분포 차이 | PASS_WITH_SEMANTIC_DIFFERENCE |
| RVOL | 과거 20세션 Massive 프로파일 | Kiwoom 과거 분봉(REST)으로 기준선 필요 | 예 | 같은 소스 기준선이면 예, 교차 소스면 아니오 | 예 | watchlist 규모에선 가능, 전 유니버스 불가 | 과거 기준선을 Massive로 두면 약 0.70배 편향 | PASS_WITH_SEMANTIC_DIFFERENCE (같은 소스 기준선 필수) |
| VWAP | Σ(Massive `vw`×v)/Σv, 정규장 | FE 체결 누적(검증됨) | 예 | 거래 포함 범위 차이 가능(0.70배 원인 미상) | 예 | 예 | VWAP 수준 미실측 | PASS_WITH_SEMANTIC_DIFFERENCE (VWAP parity 실측 필요) |
| VWAP distance % | 위 두 값 | 위 두 값 | 예 | 가격이 같으면 차이 작음, 미실측 | 예 | 예 | 위와 같음 | PASS_WITH_SEMANTIC_DIFFERENCE |
| HOD (정규장) | 실제 분봉 high, 09:30부터 | FE 체결에서 09:30부터 직접 누적(FE 17 사용 금지) | 예 | 예(정규장 HOD 동일 실측) | 예 | 예, 재연결 시 REST 분봉으로 복구 | 재연결 복구 | PASS |
| Breakout strength | close/직전 HOD − 1 | 같은 식 | 예 | 예 | 예 | 예 | 없음 | PASS |
| Dollar volume (절대) | Massive | FE | 예 | 아니오(0.70배) | 예 | 예 | 절대 임계값 이동 | PASS_WITH_SEMANTIC_DIFFERENCE |
| Spread | 없음(NBBO 403) | FT 10호가 | - | - | 예 | 예 | 과거 검증 불가 | **BLOCKED** (과거 가설 feature로는 사용 불가) |
| Time-of-day | 분 타임스탬프 | 분 타임스탬프(ET) | 예 | 예 | 예 | 예 | 없음 | PASS |
| Current-day return (전일 정규장 종가 기준) | grouped daily | Kiwoom 일봉/전일 종가 | 예 | 예(일봉 일치 실측) | 예 | 예 | 기준가 정의 확정 필요 | PASS (정규장 시가 기준은 UNKNOWN) |
| Tape density | 분봉 유무 | FE 분봉 유무 | 예 | 아니오(소형주 Kiwoom 추가 분) | 예 | 예 | 적격성 판정 달라짐 | **BLOCKED** (신규 가설 feature로 사용 금지) |
| Halt inference | 분봉 공백 | FE 공백 | 예 | 아니오(공백 패턴 소스 의존) | 예 | 예 | 오탐/미탐 | PASS_WITH_SEMANTIC_DIFFERENCE, 가설 feature로 사용 금지 |

## 5. 가설 초안

### H1 - Volume Acceleration Confirmation (우선순위 1)

- 가설: HOD 돌파 시점에 거래량이 직전 구간보다 가속 중인 돌파가 가속하지 않는 돌파보다 지속된다.
- 경제적 근거: 실제 참여가 늘어나는 돌파는 수요가 있다는 증거, 거래량이 식는 돌파는 소진 가능성.
- 후보 feature: 기존 `volume_acceleration`(최근 5분/직전 5분, 같은 소스 비율). 새 feature 만들지 않음.
- 과거 가능: PASS. 실시간 가능: PASS_WITH_SEMANTIC_DIFFERENCE(소형주 분포).
- 임계값: **UNDECIDED**. 같은 표본 관측값 0.90은 채택하지 않는다. 경제적으로 설명 가능한 후보는 1.0
  ("최근 5분 거래량 ≥ 직전 5분")뿐이며, 임계값 탐색(0.85/0.90/0.95…)은 금지.

### H2 - Overextension Avoidance (우선순위 2)

- 가설: 이미 크게 뻗은 상태의 돌파는 지속이 아니라 소진이고, 하방 꼬리가 크다.
- 후보 feature 군: VWAP distance, 1분 수익률, 돌파폭, 당일 수익률.
- B-E1에는 **1~2개만** 넣는다. 실시간 재현성 기준 우선순위:
  1. breakout strength (PASS)
  2. 1m return (정규장 scope로 한정 시 PASS 후보)
  3. VWAP distance (VWAP parity 실측 전까지 PASS_WITH_SEMANTIC_DIFFERENCE)
  4. current-day return (기준가 정의 필요)
- 최종 feature·임계값: **UNDECIDED**.

### H3 - Exit / Stop Noise (보조, 자동 승격 금지)

- 가설: 손절폭이 돌파 후 정상 노이즈 폭과 겹쳐 방향이 맞아도 손절된다.
- 상태: **SECONDARY**. 전체 신호 방향성이 약하므로 H1/H2로 신호 품질을 먼저 확인한 뒤 연구.
- 실행 가능성: §6.

## 6. Exit / Stop 실행 호환성 (H3 대비)

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| 시장가/지정가 매수·매도 | 공식 API 존재(`ust20000` 매수, `ust20001` 매도, `/api/us/ordr`), **미호출** | SPIKE §Order APIs |
| 정정/취소 | 공식 API 존재(`ust20002`, `ust20003`), 미호출 | SPIKE |
| 서버측 stop 주문 | UNKNOWN (예약주문 API는 문서에 있으나 조건·의미 미확인) | SPIKE |
| 클라이언트측 stop(FE 모니터링 후 주문) | 가능 추정, 미검증. 지연 = 분 봉 가용 1분 + 주문 왕복 | 설계 추론 |
| 분할 청산 | 수량 지정 매도로 가능 추정, 미검증 | - |
| trailing | 클라이언트 로직으로만(서버 기능 UNKNOWN) | - |
| 주문 확인/체결 이벤트 | 계좌 이벤트 F4/F5는 현재 명시적으로 제외, 조회 API(`ust21050`, `ust21150`) 존재 | SPIKE |

→ H3 production-track은 주문 경로 실측 전까지 `UNKNOWN`.

## 7. Broad Scanner → Watchlist 구조

B-E0는 3,882종목 전체의 분봉을 동시에 본다. Kiwoom 실시간은 그렇게 할 수 없다.

| 경로 | 가능성 | 근거 |
| --- | --- | --- |
| 전 종목 FE/FT 구독 | 불가 | 실측 안전 26종목, 문서상 200(미검증) |
| REST 현재가(`usa20100`) 전 종목 순회 | 실시간 불가 | 3/초 → 3,882종목 약 22분/회 |
| REST 분봉(`usa06011`) 전 종목 폴링 | 실시간 불가 | 같은 한도 |
| 순위 API(`usa20530` 거래량, `usa20540` 거래대금) | 1단계 후보 | 이미 allowlist, 읽기 전용 검증됨. 반환 행 수·장중 갱신 주기는 **UNKNOWN** |
| 등락률/급등 순위, 조건검색(미국주식) | **UNKNOWN** | repo에 없음, 공식 스펙에서 미확인 |

제안 구조(구현 안 함):

1. Stage 1 - 순위 API로 장중 거래량/거래대금 상위 N 수집(수 분 주기)
2. Stage 2 - 상위 중 B 적격 종목을 수십 개 watchlist로 FE/FT 구독(한도 이하)
3. Stage 3 - watchlist 안에서 B FSM·셋업·신호
4. 주문

**중요한 함의:** 실시간 B는 "순위 상위 종목 안의 신호"만 볼 수 있다. B-E0/B-E1 과거 백테스트는
전 유니버스 신호를 본다. 이 차이를 좁히려면 B-E1 과거 검증에 **PIT 재구성 가능한 watchlist 제약**
(예: 해당 시각 누적 거래대금 상위 N)을 함께 사전등록하는 방안을 검토한다. 순위 API의 정확한 정의를 모르면
과거 재구성이 불가능하므로 순위 API 실측이 선행 과제다.

## 8. Kiwoom Blockers (실시간 승격 전 필수)

| # | Blocker | 영향 | 해소 방법 |
| --- | --- | --- | --- |
| K1 | `REALTIME_BROAD_SCANNER_BLOCKER`: 순위 API 행 수·갱신 주기·정의 UNKNOWN, 급등 순위·조건검색 UNKNOWN | 실시간 유니버스 구성 불가 | 장중 순위 API probe(읽기 전용) |
| K2 | 구독 한도 UNKNOWN(26 실측, 200 미검증), FE+FT 개별 계산 여부 UNKNOWN | watchlist 크기 | 단계적 등록 probe |
| K3 | 분 거래량 0.70배 원인 UNKNOWN | 절대 거래량·RVOL·달러거래량 임계값 | 같은 소스 기준선 사용, 원인 조사 |
| K4 | VWAP 수준 parity 미실측 | H2 VWAP distance | 정규장 FE VWAP vs Massive 분봉 VWAP 비교 |
| K5 | RVOL 실시간 기준선(Kiwoom 20세션 분봉) 확보 경로 미검증 | RVOL 게이트 | REST 분봉 깊이·비용 실측 |
| K6 | 주문·체결 이벤트 경로 미검증(주문 0회 원칙) | 실행 | 모의계정 확보 후 별도 승인 |
| K7 | 재연결 시 HOD/VWAP/누적 상태 복구 | 상태 정합성 | REST 분봉 backfill 설계 |

B-E1 **과거 검증**에는 blocker가 아니다. **실시간 승격**에는 전부 blocker다.

2026-09-22 갱신(`B_KIWOOM_BROAD_SCANNER_PREVALIDATION.md`): K1은 부분 해소. 순위 API는 거래소별 6,000행 이상을 주고 필드 의미를 확인했지만, 정규장 갱신 주기는 여전히 UNKNOWN이다. K2는 한도 200으로 실측(E-MAX)됐고 app key당 WS 1세션이라 전략 간 공유 gateway가 필요하다. 분봉 과거 깊이는 6개월 이상이라 K5 경로가 존재한다.

## 9. 검증 데이터셋 (B-E0 표본 재사용 금지)

B-E1 가설은 B-E0 4개월(2026-05-18~09-16)에서 발견됐으므로 같은 기간으로 최종 검증하지 않는다.

| 안 | 내용 | 데이터 | 비용 추정 | 비고 |
| --- | --- | --- | --- | --- |
| A | 2026-05 이전 과거 구간(예: 2026-01~05 약 4개월) | B 유니버스 분봉(Common Raw) + grouped daily(2024-09부터 보유) + 유니버스 artifact 재구성 | 기존 실측 기준 3~4개월 분봉 약 14시간·수천 콜(종목당 최소 1콜), 저장 약 6~7GB(1년 20GiB 비례) | 가장 빠른 독립 검증. CS reference 스냅샷의 해당 기간 보유 여부 UNKNOWN |
| B | 앞으로의 forward shadow | Kiwoom 실시간(watchlist 한도 안) + Massive 사후 대조 | 수 개월, 실시간 러너 필요 | K1~K7 해소 필요, 표본 적음 |
| C | A 후 B | 둘 다 | 둘 다 | **권장** |

권장 설계: Discovery = B-E0(2026-05~09) → Validation = 안 A(겹치지 않는 과거) → Forward confirmation = Kiwoom shadow.
이번 작업에서 수집은 시작하지 않는다.

## 10. Realtime Shadow · Parity · 승격

### 10.1 순서

Historical B-E1 → Kiwoom Realtime Shadow → Historical signal vs realtime signal parity → Paper/가상 체결 → 승격

### 10.2 Parity 계약 초안 (설계만)

같은 (symbol, ET 분)에서 비교:

| 항목 | 비교 방식 | 기대 |
| --- | --- | --- |
| 분 OHLC | 정확/틱 오차 | 정규장 대형주 동일(실측) |
| 분 거래량 | 비율 | 소스 차이 범주로 기록(0.70 근처), 절대 비교 안 함 |
| volume_acceleration | 같은 소스 비율끼리 | 차이 분포 기록 후 허용 범위 사전등록 |
| RVOL | 같은 소스 기준선끼리 | 교차 소스 비교 금지 |
| VWAP distance | 절대 차 | K4 실측 후 허용 범위 결정 |
| HOD | 정확 | 동일 |
| Setup state / signal | 이벤트 일치율 | 불일치 사유 분류(데이터/타이밍/로직) |

### 10.3 승격 게이트

**`NO KIWOOM REPRODUCTION = NO PROMOTION`** - 적용한다. 과거 백테스트가 좋아도 Kiwoom 실시간에서 같은 feature와 신호를
만들 수 없으면 production-track으로 승격하지 않는다.

## 11. E0 진단 결과

세 run의 계약·dataset·universe·code digest는 같다. 차이는 비용(ZERO_COST)과 체결 시점(SIGNAL_BAR)뿐이다. 진단 run은 `verdict_eligible=False`이고 B-E0 판정에 들어가지 않는다.
원자료: `data/runtime/strategy_b_e0/runs/three_way_comparison.json`

| Metric | BASE (판정) | DIAG_ZERO_COST | CF_SIGNAL_BAR |
| --- | --- | --- | --- |
| run_id | `be0-995dec075c1f4aea610a` | `be0-766076888a43fc695ee1` | `be0-aa1fc6f27988b957ec62` |
| 거래 / 거래 세션 | 219 / 77 | 225 / 77 | 219 / 77 |
| 수익률 | -25.72% | -10.05% | -24.75% |
| 평균 R | -0.3545 | -0.0642 | -0.3317 |
| 95% CI | [-0.484, -0.220] | [-0.203, +0.079] | [-0.464, -0.198] |
| 중앙 R | -0.845 | -0.597 | -1.107 |
| PF | 0.446 | 0.753 | 0.487 |
| 승률 | 31.5% | 40.9% | 32.4% |
| MDD (세션 종가) | 25.72% | 10.30% | 24.81% |
| 양수 월 | 0/5 | 1/5 | 0/5 |
| 월별 평균 R (5~9월) | -0.45 / -0.25 / -0.38 / -0.30 / -0.54 | -0.23 / +0.10 / -0.01 / -0.09 / -0.26 | -0.48 / -0.25 / -0.33 / -0.29 / -0.42 |
| HARD_STOP 비율 | 48.4% | 48.0% | 50.2% |
| lift | LIFT_FAIL | LIFT_FAIL | LIFT_FAIL |

- ZERO_COST: 비용을 모두 없애도 평균 R은 0 근처의 약한 음수다. BASE와의 차이 약 0.29R/거래가 비용이다.
- SIGNAL_BAR: 같은 신호 215건 기준 진입가가 중앙 0.05% 싸졌을 뿐이고, 평균 R 개선은 +0.02R이다. 신호 봉 close → 다음 봉 open 중앙 0.00%와 일치한다.
- 신호 탐색: 30분 MFE와 MAE가 대칭이고 ±1% 선도달이 183:185다.

### 11.1 원인 분류

| 분류 | 판정 | 근거 |
| --- | --- | --- |
| SIGNAL_EDGE_WEAK | **주원인** | 비용 0에서도 평균 R ≤ 0, 신호 후 경로 방향성 없음 |
| COST_DOMINATED | 아님 | ZERO_COST에 양의 edge 없음(CI가 0을 포함하고 점추정 음수) |
| ENTRY_DELAY_DOMINATED | 아님 | SIGNAL_BAR 개선 +0.02R |
| SUBSET_EDGE_EXISTS | 탐색 후보 | 거래량 가속·비과확장 부분집합이 5개월 모두 같은 방향. 같은 표본이라 검증 아님 |
| EXIT_CAPTURE_PROBLEM | 부분 | MFE30 중앙 0.76R vs 실현 -0.35R, HARD_STOP 106건 중 22건이 +1R 이후 손절. 다만 신호 방향성이 없어서 exit만으로는 해결되지 않음 |

**최종: MIXED (SIGNAL_EDGE_WEAK 주원인 + SUBSET_EDGE_EXISTS 후보 + 부분 EXIT_CAPTURE_PROBLEM)**
B-E0 V1 AUTHORITATIVE RESULT = FAIL. 진단 결과는 이 판정을 바꾸지 않는다.

### 11.2 가설 선택 (사용자 검토용 제안, freeze 아님)

| 역할 | 가설 | feature | 임계값 후보 | Kiwoom |
| --- | --- | --- | --- | --- |
| Primary | H1 Volume Acceleration Confirmation | `volume_acceleration` (최근 5분 거래량 ÷ 직전 5분) | 1.0 ("최근 5분이 직전 5분 이상"), 미확정 | PASS_WITH_SEMANTIC_DIFFERENCE |
| Secondary | H2 Overextension Avoidance, **breakout strength 하나만** | signal close ÷ 직전 정규장 HOD − 1 | UNDECIDED (탐색 quintile 경계 0.73% 채택 금지) | PASS |
| Deferred | H3 Exit / Stop | - | - | 주문 경로 UNKNOWN |

H2에서 breakout strength를 고른 이유: 실시간 재현성이 PASS인 유일한 과확장 feature이고(HOD 정규장 일치 실측), "이미 크게 뚫은 돌파는 추격"이라는 경제적 설명이 직접적이다. 1분 수익률은 `return_scope=EXTENDED_DAY` 문제가 있고, VWAP 이격은 parity가 미실측이며, 당일 수익률은 기준가를 먼저 정해야 한다.

H3를 primary로 올리지 않는 이유: 신호 방향성이 50:50이면 exit를 바꿔도 기대값의 부호가 바뀌기 어렵다. 신호 품질을 먼저 확인한다.

### 11.3 실험 구조

- **E1-A**: H1만 (B-E0 규칙 + volume acceleration 게이트 1개)
- **E1-B**: H1 + H2 (E1-A + breakout strength 상한 1개)
- 한 실험에 여러 필터와 손절 변경을 동시에 넣지 않는다.
- 임계값은 경제적 설명으로 사전등록하고, 여러 값을 돌려 최고를 고르지 않는다.
- H2 임계값은 사용자 결정이 필요하다. 탐색 표본의 분위 경계를 쓰면 같은 표본 최적화가 된다.

### 11.4 Freeze 전 남은 결정

1. H1 임계값 1.0 확정 여부
2. H2 breakout strength 임계값(또는 E1-A만 먼저 진행)
3. **검증 데이터셋**: B-E0 기간(2026-05~09)은 discovery라 재사용 금지. 2026-05 이전 독립 구간을 수집할지(§9 안 A, 약 14시간·6~7GB 추정), forward shadow로 갈지 결정 필요. 현재 Additional historical data collection = NOT STARTED.
4. B-E1에 Stage-1 watchlist 제약(as-of 누적 거래량/거래대금 상위)을 포함할지. 정규장 순위 probe 결과 이후 결정 권장.

### 11.5 게이트 분리

- Historical Research Gate: 위 결정 후 B-E1 사전등록·독립 구간 검증
- Realtime Promotion Gate: `NO KIWOOM REPRODUCTION = NO PROMOTION`, §8 blocker와 정규장 순위 probe 결과 필요

## 12. 원칙

- B-E1 production-track 가설에는 판정이 `PASS` 또는 사전 승인한 `PASS_WITH_SEMANTIC_DIFFERENCE`인 feature만 쓴다. `BLOCKED` 금지.
- 소스 간 수준 차이가 있는 값(거래량·달러거래량)은 절대 임계값 대신 같은 소스 상대값을 우선 검토한다.
- 임계값 탐색·조합 brute-force·ML 튜닝 금지. 임계값은 경제적 설명 + 사전등록.
- 조건을 3~4개 한꺼번에 추가하지 않는다.
- Historical SIGNAL_BAR 체결을 primary 모델로 채택하지 않는다.
- Strategy B strategy logic changed = NO (이 문서 작성으로 코드·계약·B-E0 결과 변경 없음).
