# AOA E1 Maker Adverse Selection Contract V1 (사전등록)

작성 2026-09-27. 이 문서의 sha256을 `data/research/expert_execution/e1/contract_freeze_v1.json`에 기록한 뒤에만
maker/taker의 체결 후 가격 경로를 계산한다. `e1.run()`은 실행할 때 hash를 다시 계산하고, 다르면 멈춘다.

## 0. 동결 전에 본 것 (공개)

- E0 결과 전부 (maker 비중, 수수료, 행동별 fill 수. 체결 후 가격 경로는 E0에서 계산하지 않았다).
- BitMEX 공개 archive 목록 (trade·quote 각 1,399일, 누락 0)과 샘플 2일(2018-03-05, 2021-05-19)의 스키마,
  XBTUSD 행 수, quote 갱신 간격, 중복·crossed 개수, 원장 fill과 archive trade의 `trdMatchID` 조인(5,282/5,282, timestamp·가격 완전 일치).
- **보지 않은 것**: 어떤 fill의 체결 전후 mid, spread capture, 이후 가격 이동. 샘플 2일에서도 계산하지 않았다.

## 1. 질문 (범위 제한)

"이 계좌에서 실제로 체결된 XBTUSD maker fill은 체결 직후 역선택을 얼마나 겪었고, 당시 BitMEX 수수료·리베이트
이점이 그 비용을 이겼는가." maker가 일반적으로 수익성이 있다는 결론은 이 계약의 어떤 결과로도 내리지 않는다.

## 2. 대상

| 항목 | 동결값 |
|---|---|
| symbol | **XBTUSD만** (거래가치 91.8%) |
| 기간 | 원장 전체: 2018-03-05 ~ 2021-12-31 UTC |
| 이벤트 | `exectype == Trade` fill |
| 제외 | `text == "Liquidation"` fill (계좌 의사결정 아님), Funding·Settlement, 기준 quote 무효 fill(4절) |
| 원장 | E0 정규화 `executions.parquet` + `positions.parquet` (수정 없이 읽기만) |
| 시장데이터 | `public.bitmex.com/data/quote/YYYYMMDD.csv.gz`, `/data/trade/YYYYMMDD.csv.gz`, 2018-03-04 ~ 2022-01-01 |

## 3. Maker / Taker

- `lastliquidityind`: AddedLiquidity = **MAKER**, RemovedLiquidity = **TAKER**, 그 외 = UNKNOWN (보고만, 분석 제외).
- `ordtype`(Limit/Market/Stop)은 분류에 쓰지 않는다. 비교는 Maker vs Taker이지 Limit vs Market이 아니다.

## 4. 시장 기준가격

| 항목 | 동결값 |
|---|---|
| 시각 | 원장 `transacttime` (archive trade와 동일 시각임을 샘플에서 확인, 전 기간 조인율을 보고) |
| 기준 quote | 체결 시각 **t보다 엄밀히 앞선**(ts < t) 마지막 XBTUSD quote. 같은 시각 quote는 체결 결과를 담고 있을 수 있어 제외 |
| mid_0 | (bid + ask) / 2 |
| 미래 mid_h | ts ≤ t + h 인 마지막 quote의 mid |
| stale 규칙 | 기준 quote 나이(t - ts) > 60초면 그 fill 전체 제외. mid_h의 quote 나이(t + h - ts) > 60초면 그 horizon만 결측 |
| 무효 quote | bid ≥ ask (locked/crossed), bid·ask ≤ 0 이면 무효. 기준 quote가 무효면 fill 제외, 미래 quote가 무효면 그 horizon 결측 |
| 중복 | 완전 중복 행 제거. 같은 ts 여러 행은 파일 순서 마지막 행 사용 |
| archive 파일 결측 | 해당 구간 fill은 stale 규칙으로 자연 제외 |
| fallback | **없음**. last trade로 대체하지 않는다 |

## 5. 부호와 지표 (fill 단위)

s = +1 (Buy), -1 (Sell). 모든 bp는 mid_0 기준: x_bp = x / mid_0 x 1e4. 원시 가격 차도 함께 저장한다.

| 지표 | 정의 | 부호 |
|---|---|---|
| `spread_bp` | s x (mid_0 - fill_px) | + = mid보다 유리하게 체결 (maker spread capture), - = spread 비용 (taker) |
| `move_bp(h)` | s x (mid_h - mid_0) | + = 체결 방향으로 이동 (favorable), - = **adverse** |
| `signed_move_vs_fill_bp(h)` | s x (mid_h - fill_px) = spread_bp + move_bp(h) | 요청서 D절 정의 |
| `fee_bp` | -commission x 1e4 (원장 실제 요율) | + = 리베이트, - = 수수료 |
| `net_edge_bp(h)` | fee_bp + spread_bp + move_bp(h) | execution_net_edge |

XBTUSD는 inverse라 XBT 손익은 1/가격에 비례하지만, 이 horizon의 가격 변화(수 bp)에서 bp 근사 오차는 2차항(1e-8 수준)이라 무시한다.

## 6. Horizon

1s, 5s, 15s, 30s, 60s, 300s (핵심), 900s (참고). **판정 기준 horizon = 60s.** 나머지는 기술 통계.

## 7. 행동 유형

E0 `positions.parquet`의 fill 단위 `action`을 그대로 쓴다: OPEN, ADD, REDUCE, CLOSE, REVERSE (LONG/SHORT 합산).
ENTRY = OPEN + ADD, EXIT = REDUCE + CLOSE, REVERSE는 별도.

## 8. 가중과 표본 단위

| 단위 | 정의 | 용도 |
|---|---|---|
| fill | fill 하나, 가중치 = fill 가치 \|execcost\| (XBT) | **1차 추정치** (가치가중 평균) |
| order x role | 같은 orderID의 같은 role(maker/taker) fill들을 가치가중 평균한 한 값 | 강건성 (동일가중 평균) |
| cluster | order의 첫 fill UTC 날짜. 한 order의 fill은 한 cluster 안에 있다 | 모든 CI |

144만 fill을 독립 표본으로 취급하지 않는다.

## 9. 통계 방법

- 점추정: fill 가치가중 평균 Σw·x / Σw. 추가로 fill 비가중 평균·중앙값·p25·p75, order x role 동일가중 평균.
- 불확실성: **일 단위 cluster bootstrap**, B = 2,000, seed = 20260927. 날짜를 복원추출해 비율 추정치를 재계산, 2.5/97.5 백분위 = 95% CI, bootstrap 표준편차 = SE.
- 비율: favorable = move_bp > 0, adverse = move_bp < 0, zero = 0 (가치가중이 아닌 fill 수 기준), hit = net_edge_bp > 0.
- 다중 비교: 판정은 10절 조건만 쓴다. horizon·regime·size 표는 기술 통계이고 유의성 판정을 하지 않는다.

## 10. Regime·size 정의 (전부 체결 시각 이전 정보만)

| 축 | 정의 |
|---|---|
| year | 체결 UTC 연도 |
| volatility | 체결 직전 완결된 1분 mid 로그수익률 60개의 표준편차. 분석 대상 전 fill의 값으로 3분위 (기술용, 분위 경계는 전 표본 분포를 쓴다는 점 공개) |
| trend | z = 직전 60분 mid 로그수익률 / (vol x √60). z > 1 UP, z < -1 DOWN, 그 외 SIDEWAYS. 추가로 s x z 부호로 WITH/AGAINST |
| spread | 기준 quote spread 틱 수 (XBTUSD tick 0.5): 1틱 / 2틱 이상 |
| trade intensity | 체결 직전 60초 [t-60s, t) archive XBTUSD trade 건수, 전 표본 3분위 |
| time of day | UTC 4시간 구간 6개 |
| fill size | fill 가치(XBT) 전 표본 3분위 (small/medium/large) |
| order size | order x role 가치 3분위 |
| depth ratio | fill 수량 / 기준 quote 표시수량. maker는 자기 쪽(Buy=bid), taker는 반대쪽(Buy=ask). < 0.1 / 0.1~1 / ≥ 1 |

## 11. 수수료 두 층

1. **HISTORICAL_BITMEX_ACTUAL**: 원장 fill별 `commission`(당시 실제 요율, 부호 포함). 기간별 요율표는 원장에서 연·월 최빈값으로 요약만 한다.
2. **CURRENT_BYBIT_REFERENCE (counterfactual)**: `data/runtime/crypto/reference/fee_source_verification_v1.json`의 VIP 0 perp
   **maker 0.0200%, taker 0.0550%** (D4 OFFICIAL, version `bybit-official-vip0-2026-09-02`). 같은 역선택이 일어난다고 가정한 단순 시나리오 두 개:
   - CF-A: fee만 교체, spread_bp·move_bp는 BitMEX 측정값 그대로
   - CF-B: fee 교체 + spread_bp = 0 (Bybit BTCUSDT spread는 D5 실측 거의 항상 1틱 = 약 0.012bp라 spread capture가 사실상 없음)
   2026 Bybit 체결 성과라고 주장하지 않는다.

## 12. 판정 규칙

### 12.1 HISTORICAL verdict (MAKER, HISTORICAL_BITMEX_ACTUAL, h = 60s)

| 조건 | 내용 |
|---|---|
| C1 | 가치가중 maker net_edge_bp(60s) > 0 이고 95% CI 하한 > 0 |
| C2 | 연도별 점추정 > 0 이 4개 연도 중 3개 이상 |
| C3 | order x role 동일가중 maker net_edge_bp(60s) > 0 |
| C4 | volatility 3분위 모두 점추정 > 0 |

- **MAKER_RESEARCH_WORTH_CONTINUING**: C1~C4 모두 충족
- **NOT_SUPPORTED**: C1의 점추정 ≤ 0 이고 C3도 ≤ 0
- **MIXED**: 그 외

### 12.2 BYBIT applicability (별도, 판정을 합치지 않음)

- **BYBIT_CF_POSITIVE**: CF-B maker net_edge_bp(60s) 가치가중 > 0 이고 CI 하한 > 0
- **BYBIT_CF_NEGATIVE**: CF-B 점추정 ≤ 0
- **BYBIT_CF_INCONCLUSIVE**: 그 외

### 12.3 보조 질문 (판정에 쓰지 않음)

Q3(리베이트 vs 역선택)은 fee_bp와 -move_bp(h) 비교, Q4(taker가 immediate alpha를 샀나)는 taker move_bp(h) > 0 여부, Q7(2021 maker 81%와 성과)은
연도별 기술 대조(인과 아님)로만 답한다.

## 13. 측정 불가 (UNKNOWN 유지)

queue 위치, 취소·정정 의도, 체결되지 않은 주문의 기회비용, 주문 제출 시각(원장에는 첫 fill 시각만 있음, 따라서 decision-time implementation shortfall은 계산하지 않음).
추정으로 만들어 넣지 않는다.

## 14. 저장·격리

- 원본 gz: 처리 후 삭제, 파일별 bytes·ETag·sha256을 `e1/market_manifest.json`에 기록 (공개 원본으로 재현 가능)
- XBTUSD만 추린 quote·trade: `data/research/expert_execution/e1/market/` (git 제외)
- 결과: `e1/maker_taker_metrics.parquet`, `order_level_metrics.parquet`, `regime_metrics.parquet`, `e1_summary.json`
- D2·D5·Paper·운영 서버·기존 crypto 엔진: 접근·수정 0
