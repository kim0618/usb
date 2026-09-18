# Strategy D Concept V1 (D0)

작성 2026-09-17. 연구 단계 설계 동결 문서다. 이 문서는 주문, 포지션, 리스크, 사이징, 체결을 정의하지 않는다.

- Strategy ID: `HISTORICAL_ANALOG_V1`
- 규칙 정본: `d_analog_rules_v1.json`
- canonical sha256: `680bf113253fc434102f46a4166ac38b23dfbb4ba7591a7d88c3430c058c0cd3`
  (기존 US-B 방식 `app.backtest.strategy_c_selection.rules.canonical_checksum`, 기록 파일 `d_analog_rules_v1.sha256`)
- 선언 시각: 2026-09-17 17:25 KST. D용 데이터 읽기, D용 API 호출, D 결과는 이 시점까지 0이다.
  17:21 KST 초안(`7e120421...`)은 PIT 계약 작성 중 label 유효성 누수(C `DISAPPEARED`가 D+h 너머를 읽음)를 발견해
  폐기했다. 데이터와 결과를 보기 전의 수정이다(`D_PIT_CONTRACT_V1.md` §4.4).
- 선언 시점 저장소: `main` HEAD `0a96bd7`, 다른 세션의 미커밋 변경 86개가 있었다(A/B/C 작업). D0는 이 문서들 말고는 아무 파일도 바꾸지 않았다.
- 관련 문서: `D_REUSE_MATRIX_V1.md`(재사용 결정), `D_PIT_CONTRACT_V1.md`(look-ahead와 누수 계약)

## 1. 가설

> 현재 종목의 최근 가격 경로와 비슷했던 과거 미국주식 가격 경로들을 모았을 때, 그 과거 경로들의
> 이후 움직임(Historical Analog의 forward 분포)이 현재 종목의 이후 움직임에 대해 정보를 주는가?

이번 연구의 성격은 **반증 시도**다. D를 증명하려는 연구가 아니다. 규칙과 판정 기준은 결과를 보기 전에
고정했고, 결과를 본 뒤에는 바꾸지 않는다. 새 값은 `d_analog_rules_v2.json`으로 새로 선언한다.

## 2. D가 아닌 것

- 이름 붙은 차트 패턴 탐지기(Cup & Handle, Bull Flag, VCP 등)가 아니다. 패턴 이름, 템플릿, 수작업 형상 규칙이 없다.
- 학습 모델이 아니다. V1에는 LLM, Vision model, CNN, Transformer, Autoencoder, ML ranking, neural embedding이 없다
  (`ai_enabled=false`). 유사도는 닫힌 식(Pearson, Euclidean)이고 신호는 이웃 label의 중앙값이다.
- 모멘텀 재표현을 D의 성과로 인정하지 않는다. 단순 가격 feature 기준선(N2)을 넘는 정보가 있어야 한다(§6).

## 3. 흐름

```text
D일 종가 확정 (as_of = D 정규장 종료, available_at = D+1 04:00 ET)
  -> Query 벡터: 분할 정규화 종가 경로 P(D-W..D)
  -> Historical Pattern Library: 과거 (ticker, 종료일 d) 창, 5세션 간격, expanding
  -> Neighbor 정책: 같은 종목 제외, 엄격 embargo(d + h <= D - W), 종목당 1창, 날짜당 5창
  -> Similarity Search (정확 검색, 근사 없음) -> Top-K = 50
  -> Top-K 이웃의 forward label 분포 (d+1..d+h, 모두 D 이전에 확정)
  -> S(q) = 이웃 초과수익률 중앙값,  sigma(q) = 유사도 신뢰도(서술용)
```

## 4. 초기 범위 (동결)

| 항목 | 값 |
| --- | --- |
| 데이터 빈도 | DAILY만. 분봉은 범위 밖 |
| 신호 입력 | 분할 정규화 종가 경로만. OHLC, 거래량, 변동성은 D5 확장 후보(ALPHA PASS일 때만, 새 버전) |
| Pattern window W | 20, 40, 60 거래일 |
| Horizon h | W=20 -> 1, 3, 5 / W=40 -> 5, 10 / W=60 -> 10, 20 |
| 조합 수 | 7. 결과를 본 뒤 추가 금지 |
| 표현 | A: z 정규화 종가 경로(Pearson) / B: 누적 로그수익률 경로(Euclidean) |
| DTW | V1 사용 안 함(`enabled=false`, `rerank_k=null`) |
| 1차 검정 family | 7 조합 x 2 표현 = 14 |
| Top-K | 50 |
| Universe | CS, 허용 거래소 5개, close >= $3, ADV20 >= $5M (C-M hard filter와 같은 값을 D가 독립 선언) |

### 4.1 표현과 유사도가 두 개인 이유

- A는 경로 **모양**만 본다. z 정규화로 수준과 진폭이 사라진다.
- B는 누적 로그수익률이라 **진폭**까지 본다. 같은 모양이라도 +40%와 +4%는 멀다.
- A에 Euclidean을 따로 붙이지 않는다. ddof0 z 벡터(길이 n=W+1)에서 `||a-b||^2 = 2n(1-rho)`라 순위가 같고,
  독립 실험 두 개로 세면 family만 부풀린다.

### 4.2 DTW를 V1에서 뺀 이유

전 universe 검색 척도로는 쓰지 않는다는 원칙에 더해, Top-K reranking도 V1에서는 끈다. 켜면 검정 family가
14에서 최소 21로 늘고(표현당 rerank 변형), rerank K와 band 폭이라는 사후 조정 손잡이가 두 개 생긴다.
ALPHA 결과 없이 이 값들을 정당화할 근거가 없다. DTW는 별도 선언 버전에서 Top-K reranking으로만 허용한다.

## 5. Signal 정의

유사도는 방향이 아니다. 두 양을 분리한다.

| 기호 | 이름 | 정의 | V1 역할 |
| --- | --- | --- | --- |
| `sigma(q)` | Similarity Confidence | A: Top-K 평균 rho, B: -평균 거리/sqrt(W) | 서술용. gate 입력도 필터도 아님 |
| `S(q)` | Analog Forward Signal | Top-K 이웃의 `excess_return_h` 중앙값 | **Primary 방향 신호** |

- `excess_return_h` = D+1 시가 기준 h일 종가수익률(clip [-1,1]) - 같은 날짜 전체 적격 universe의 같은 값 중앙값.
  시장 전체 상승일과 하락일의 효과를 이웃과 query 양쪽에서 같은 방식으로 뺀다.
- Secondary(서술): 평균 초과수익률, hit rate, 이웃 MFE/MAE 중앙값, sigma 3분위별 IC.

## 6. Benchmark

| 이름 | 질문 | 정의 요약 |
| --- | --- | --- |
| N1 Random Analog | 유사도 검색이 같은 라이브러리의 무작위 표본보다 나은가 | query와 같은 라이브러리, 같은 정책, 같은 실현변동성 5분위에서 K개 무작위(해시 순서, 20회) |
| N1b (서술) | 이웃이 고른 **날짜** 효과를 빼면 남는가 | 실제 이웃 각각을 같은 종료일, 같은 변동성 5분위의 무작위 창으로 교체 |
| N2a Feature kNN | 경로 대신 단순 가격 feature로 이웃을 찾아도 같은가 | return_W, return_1d, return_5d, realized_vol_W, distance_to_W_high의 날짜별 백분위 공간에서 같은 kNN |
| N2b Orthogonalized | N2 feature로 설명되지 않는 S(q) 부분에 정보가 있는가 | 날짜별로 rank(S)를 5개 feature에 회귀한 잔차의 IC |

N1의 "동일 날짜/비슷한 변동성"은 **query 날짜 시점의 같은 라이브러리**와 **같은 변동성 5분위**로 해석해 고정했다.
이웃 종료일까지 맞추는 해석은 N1b로 따로 기록하되 gate에는 넣지 않았다(family를 늘리지 않기 위해).

D가 N2를 넘지 못하면 "Pattern Similarity Alpha"가 아니라 기존 가격/모멘텀 factor의 재표현으로 본다.

## 7. 평가와 판정 요약

- Primary metric: (W, h, 표현)별 날짜 단위 Spearman IC(S(q) vs 실현 excess_return_h)의 동일가중 평균.
- 평가일: 세션 index 260..N-21 전부(7조합, 14검정, 모든 기준선 공통). 날짜당 query 300개를 해시 순서로 표본.
  260 = seasoning 60 + 최소 라이브러리 폭 120 + max(W+h) 80. N=501이면 221일.
- 통계: 평가일 moving block bootstrap(블록 20, 10,000회, seed 20260917), Bonferroni 14 -> 99.643% CI.
- GATE-D-ALPHA 조건 10개와 PASS/INCONCLUSIVE/FAIL 결정 규칙은 JSON `pass_fail_policy` 그대로다.

| 판정 | 의미 | 다음 |
| --- | --- | --- |
| PASS | 14개 검정 중 하나 이상이 10조건 전부 충족. 무작위 이웃과 단순 가격 feature를 넘는 정보가 이 데이터 창에 있었다. **수익 전략이 있다는 뜻이 아니다** | D5 ablation, D6 장기 OOS, GATE-D-OOS. 자동 진행 없음 |
| INCONCLUSIVE | 방향과 유의성(조건 1~4)은 있으나 시간 안정성, horizon 일관성, 표본에서만 실패. 또는 평가일 150일 미만 | D5 금지. 데이터 깊이 결정만 새 버전으로 허용 |
| FAIL | 그 외 | **Strategy D 연구 종료. Backtester 개발 금지** |

유의한 음의 IC는 `INVERSE_EFFECT`로 기록만 하고 PASS로 치지 않는다. 역방향을 쓰려면 새 버전 선언이 필요하다.

## 8. 단계

```text
D0  Concept / Reuse Architecture / Rules Freeze          <- 이번 단계 (문서 5개, 코드 0)
D1  Data + PIT Feasibility
D2  Pattern Library + Similarity Engine + Neighbor Search
D3  Forward Distribution
D4  Pattern Alpha Evaluation
    -> GATE-D-ALPHA   FAIL: 종료 / INCONCLUSIVE: 데이터 결정만 / PASS: 계속
D5  OHLC / Volume / Volatility Ablation (새 선언 버전)
D6  Long Historical OOS
    -> GATE-D-OOS
그 뒤에만 Trading Spec -> StrategyAdapter -> Backtester -> Shadow -> Paper -> Small Live
```

| 단계 | 산출 | 새 코드 위치 |
| --- | --- | --- |
| D1 | C raw 캐시 완결성, D용 세션 grid N, universe 크기, 라이브러리 크기, 20D label CA 확장 방식 결정, 실행 시간 추정 | `backend/app/backtest/strategy_d_analog/` (config, models) |
| D2 | 인코더, 유사도, 이웃 검색, 정책 단위 테스트, 합성 mutation test | encoder, similarity, neighbor_search, pit_audit |
| D3 | 이웃 forward 분포, S(q)/sigma(q), N1/N2 신호 | signal, baselines |
| D4 | IC, bootstrap, gate, 실데이터 PIT 감사, run 저장 | evaluate, run |

각 모듈 책임은 `D_REUSE_MATRIX_V1.md` §3.

## 9. 데이터 깊이 제약

- Massive Basic은 롤링 2년이다. C raw 캐시(`data/runtime/strategy_c/raw`, git-ignored, 이 PC 로컬)가 곧 정본이다.
- 2026-09-17 17:14 KST 확인 시점에 C 수집 프로세스(`fetch_strategy_c_selection_raw --end 2026-09-16`)가 실행 중이었고
  grouped 캐시는 142/502 세션이었다(splits 1개, 분기 스냅샷 8개는 완료). **D1은 C 수집이 끝나고 C가 raw digest를
  확정한 뒤에만 캐시를 읽는다.** D가 같은 Massive 키로 동시에 호출하면 429가 난다.
- 평가창은 약 10개월 단일 regime이다. PASS가 나와도 D6에서 더 긴 데이터가 필요하고, 그 공급원은 아직 없다
  (Basic 밖 데이터는 D6 전 별도 결정).
- 참고: `C_DATA_FEASIBILITY_V1.md`의 "GATE-D 데이터 깊이 결정"은 C 문서의 게이트 이름이다. Strategy D의 게이트는
  `GATE-D-ALPHA`, `GATE-D-OOS`로 구분해 부른다.

## 10. 계산 예산 (규칙이 아니라 D1 확인 사항)

- 날짜당 query 300 x 라이브러리 창(약 2,500~3,000 종목 x 최대 약 84 종료일 = 약 25만) 행렬곱.
  W=60 기준 날짜당 약 5x10^9 flop, 221일이면 검정당 약 10^12 flop 수준으로 추정한다.
- 이 추정은 D1에서 실측한다. **계산이 버겁다는 이유로 규칙(K, stride, 표본 수, 정확 검색)을 바꾸지 않는다.**
  바꿔야 하면 결과를 보기 전에 V2로 선언한다.

## 11. Backtester 재사용 방향 (문서만)

D 사전검증(D0~D6)은 Backtester를 쓰지 않는다. GATE-D-OOS 통과 뒤에만 기존 실행 계층을 다시 감사한다.

- 재사용 후보: StrategyAdapter 패턴(`engine/adapter.py`), `SimBroker`, `broker/accounting.py`(PnL 단일 authority),
  `PortfolioLedger`, 실행 비용(`execution/costs.py`), RunStore(`engine/store.py`).
- 지금 연결하지 않는 이유(2026-09-17 실측):
  - `backtest/engine`의 clock/driver는 분봉 tick 계약이다(`ExtendedSessionClock`, 1분 경계 + `TICK_EPSILON`).
  - `app.backtest.engine.driver` import 한 번에 `app.services.entry_drift_observer`, `app.strategy.engine`
    (`StrategyV0Engine`), `app.risk.engine`이 따라 들어온다(`replay.clock -> services.entry_drift_observer` 경로).
  - `app.backtest.engine.identity`도 `research.contract -> authority.contract -> app.strategy.lifecycle` 경로로
    `app.strategy.engine`을 끌어온다.
  - `SimBroker`는 `MinuteBar` 기준 next-bar 체결이고, `MultiSymbolPortfolioReplay`는 A의 `EntrySessionRunner`,
    `entry_capacity`, `RiskConfig`에 묶여 있다.
- 원칙: D 전용 broker, accounting, portfolio engine을 새로 만들지 않는다. Trading 단계에서 일봉 체결 계약이
  필요하면 기존 계층의 확장으로 설계하고 회귀 테스트로 막는다.
