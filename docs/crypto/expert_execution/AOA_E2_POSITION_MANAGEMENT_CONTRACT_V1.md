# AOA E2 Position Management Contract V1 (사전등록)

작성 2026-09-27. 이 문서의 sha256을 `data/research/expert_execution/e2/contract_freeze_v1.json`에 기록한 뒤에만 episode의
시장 경로·counterfactual을 계산한다. `e2.run()`은 실행 시 hash를 다시 계산하고 다르면 거부한다.

## 0. 동결 전에 본 것 (공개)

- E0 전부: episode 3,185개(XBTUSD 2,590), 종료 방식, 보유시간 분포, ADD·부분청산 비중, 8h funding 스냅샷 기반 레버리지·평가손 표본,
  외부 claim 대조(CLAIM 2·6·7·10 포함), 드로다운 3건의 원장 요약.
- E1 전부: maker/taker 체결 후 60초~15분 mid 이동(체결 단위). 주문 크기별 결과 포함.
- **보지 않은 것**: episode 단위 시장 경로(MAE·MFE·평가손 시간), 최초 진입의 15분~24시간 이후 수익, ADD 이후 수익, 모든 counterfactual.
- E0에서 본 사실 중 이 계약 설계에 영향을 준 것: 2018년은 계좌 규모가 작고 청산이 몰려 있음(청산 episode 28개 중 26개). 그래서 모든 손익을 **episode 시작 시 자본 대비(ROE)**로 정규화한다.

## 1. 범위

| 항목 | 동결값 |
|---|---|
| symbol | **XBTUSD만** (시장데이터가 XBTUSD뿐, 거래가치 91.8%) |
| episode | E0 `episodes.parquet`의 XBTUSD episode. 포지션 0에서 시작해 0 또는 반전으로 끝나는 구간 (E0 정의 그대로) |
| 제외 | 원장 종료 시 미종료 1개. 시장 경로 결측 > 5%(분 단위)인 episode (경로 지표에서만 제외, 수 보고) |
| 청산 episode | `closed_by == LIQUIDATION` (19). **포함하되 별도 표시**. 모든 표에 청산 제외 결과를 함께 보고 |
| 원장 | E0 `executions`/`positions`/`episodes`/`wallet` parquet, 읽기만 |
| 시장데이터 | E1 `e1/market/quote|trade/*.parquet` 재사용, 재다운로드 없음 |

## 2. 사건 정의

| 사건 | 정의 |
|---|---|
| first entry | episode의 첫 fill이 속한 orderID. 그 주문이 이 episode에서 연 수량 합 = **initial qty**. 반전으로 시작한 episode는 반전 주문의 여는 부분 |
| entry time t_e | first entry 주문의 이 episode 첫 fill 시각 |
| ADD | 이 episode 안에서 첫 fill이 포지션을 같은 방향으로 늘린 주문 (first entry 주문 제외). 주문 단위 |
| REDUCE (partial close) | 첫 fill이 포지션을 줄였지만 0으로 만들지 않은 주문 |
| FULL CLOSE | 포지션을 0으로 만든 fill을 포함한 주문 (CLOSE, SETTLEMENT, 청산, 또는 REVERSE의 닫는 부분) |
| reverse | E0 규칙: 닫는 부분은 이전 episode, 남는 부분은 새 episode의 first entry. fee는 수량 비례 배분 |
| episode 종료 시각 t_x | 포지션이 0이 된 fill 시각 |
| holding time | t_x - t_e |

## 3. 시장 기준가격과 분 격자

- 분 격자 경계 b(1분 간격, UTC). **M(b)** = ts < b 인 마지막 유효 quote의 mid. 유효 = bid < ask, 둘 다 > 0. 그 quote 나이 > 60초면 M(b) 결측.
- 분 구간 [b-1m, b)의 mid 최고·최저 = 그 구간 유효 quote mid의 max/min. 구간에 quote가 없으면 M(b)로 대체.
- 체결 시점의 가격은 원장 체결가(실제 거래가)를 쓴다. 시장 경로(분 단위)는 mid를 쓴다.
- 결측: 경로 계산에서 결측 분은 건너뛴다. episode 결측 분 비중 > 5%면 경로 지표(MAE·MFE·평가손 시간) 제외.

## 4. 회계 (E0와 동일)

- inverse: 포지션 가치 V(pos, p) = pos x (-1e8) / p (XBt). 원가 C = 여는 fill의 `execcost` 합에서 줄일 때 비율로 덜어냄.
- 미실현 = V(pos, p) - C. 실현 = -(덜어낸 원가 + 닫는 fill execcost 해당분).
- fee = 원장 `execcomm` (실제 BitMEX 요율, maker/taker 실제 role). funding = 원장 funding 행 `execcomm`.
- **episode net PnL** = 실현 gross - fee - funding (XBT).
- 경로 PnL(t) = 실현 누계 + 미실현(t) - fee 누계 - funding 누계. 분마다 포지션은 **그 분 시작 시점 상태**(분 안의 체결은 다음 분부터 반영)로 평가하고, 최저·최고 mid로 최악·최선을 잰다. 체결 시점에는 체결가로 한 번 더 평가.

## 5. 자본과 레버리지

- equity(t) = 직전 wallet 잔액(E0 `wallet_balance_at`: RealisedPNL은 12:00 UTC, 입출금은 날짜 00:00) + 마지막 12:00 정산 뒤 원장 실현 net(전 심볼) + XBTUSD 미실현(mid).
  **XBTUSD 외 심볼의 미실현은 모름 → 0으로 둠** (한계로 보고).
- **ROE** = episode net PnL / equity(t_e 직전). 모든 비교의 1차 단위.
- MAE / MFE = 경로 PnL의 최소(≤0) / 최대(≥0), ROE 단위(시작 자본 대비 %)와 XBT 둘 다.
- time to MAE/MFE = t_e부터. recovery time = MAE 시각부터 경로 PnL이 처음 ≥ 0이 되기까지 (없으면 결측).
- 레버리지 = |pos| / mid / (equity(t_e) + 경로 PnL(t)). start = first entry 직후, peak = 경로 최대, ADD 전후 차이.

## 6. 평가손 시간

- 분 종가 기준 **열린 포지션의 미실현(V - C) < 0** 인 분 = underwater. 실현분은 넣지 않는다(평단 대비 평가손).
- episode underwater ratio = underwater 분 / 유효 분. 전체 = 모든 episode 분을 합친 시간가중 비율(외부 claim 57%와 비교).

## 7. 최초 진입 edge (Q1)

- 기준 r_h = s x (M(b_h) - M(b_0)) / M(b_0), b_0 = floor_min(t_e) (진입 직전 완결 분 경계, 미래 정보 없음), b_h = ceil_min(t_e + h).
- h = 15m, 1h, 4h, 8h, 24h. s = +1 LONG, -1 SHORT.
- **drift 보정**: adj_h = r_h - s x μ_{year,h}, μ = 같은 연도 모든 분 경계에서 잰 h 수익률의 평균. **1차 = adj_4h.**
- ENTRY_EDGE: SUPPORTED = adj_4h 평균 > 0, CI 하한 > 0, 4개 연도 중 3개 이상 > 0 / NOT_SUPPORTED = 평균 ≤ 0 / 그 외 MIXED.

## 8. ADD 사건 (Q2, F)

ADD 주문마다 직전 상태: 방향, |pos|, 평단(원가 기준), M(floor_min(t)), 미실현 %(평단 대비 bp와 equity 대비 %), 그 시점까지 MAE,
first entry 이후 시간, 직전 ADD 이후 시간, ADD 수량, ADD/initial, ADD/현재 |pos|, 레버리지, 직전 60분 변동성(1분 로그수익 표준편차),
직전 60분 수익률(s 부호 적용), 직전 60분 체결 건수. 이후: s x 수익률 15m, 1h, 4h, 8h, episode 종료 시 M. (기술 통계, 판정에 쓰지 않음)

ADD episode 결과 분류 (최종 ROE): SUCCESS ≥ +0.5%, FLAT (-0.5%, +0.5%), FAILED (-5%, -0.5%], CATASTROPHIC ≤ -5%.
F절 비교는 첫 ADD와 마지막 ADD 시점 상태의 중앙값 비교(기술 통계, 유의성 판정 없음).

## 9. Counterfactual (모두 실제 exit 타이밍 고정, 청산 이후로 연장하지 않음)

공통 엔진: 4절 회계. CF 체결가는 아래에 적은 가격, fee는 적은 요율.

| CF | 정의 |
|---|---|
| **CF1 NO-ADD** | initial qty만 보유. ADD 주문은 건너뜀. 실제 REDUCE 주문마다 **실제와 같은 비율**(닫은 수량 / 직전 |pos|)을 줄이고, 실제 FULL CLOSE 시각에 나머지를 닫음. 체결가 = 그 주문의 실제 VWAP, fee 요율 = 그 주문의 실제 가치가중 요율. funding = 실제 funding x (CF |pos| / 실제 |pos|) |
| **CF2 FIXED-ADD x%** (x = 0.5, 1.0) | CF1과 같되, 마지막 진입가(최초 또는 직전 규칙 ADD) 대비 mid가 x% 불리해진 첫 분에 initial qty만큼 ADD. 최대 4회. 체결가 = 임계가격(지정가가 걸려 있었다고 가정), fee = 그 달 원장 maker 최빈 요율. **낙관적**(역선택·미체결 무시, E1 참고). 이후 실제 REDUCE 비율·FULL CLOSE 시각 적용 |
| **CF3 NO-PARTIAL** | 실제 ADD는 그대로, REDUCE는 건너뜀. FULL CLOSE 시각에 전량 청산. 체결가 = 실제 마지막 닫는 주문 VWAP, fee = 그 주문의 요율. funding = 실제 funding x 비율. 큰 수량을 같은 가격에 판다고 가정하는 **낙관 편향**(충격 무시) 공개 |
| **CF4 STOP-2%** | 실제 경로 그대로 가다가 경로 PnL ≤ -2% x equity(t_e)가 되는 첫 분의 **분 종가 M(b)**에서 전량 청산, fee = 그 달 원장 taker 최빈 요율. 도달 안 하면 실제와 같음 |

CF에서 청산 episode는 실제 청산 시각에 실제 청산 가격으로 같은 비율 청산한다(살아남았다고 가정하지 않음).

## 10. 기타 분석 정의

| 절 | 정의 |
|---|---|
| E ADD 횟수 | ADD 주문 수 0 / 1 / 2 / 3 / 4+. capital efficiency = net PnL / (시간 적분 gross 노출 XBT x 일) |
| I REDUCE | 수량, 비율(닫은/직전 |pos|), 주문 실현손익, 남은 수량, 체결 VWAP 대비 평단(bp, s 부호), 진입·직전 ADD 이후 시간, 이후 s x 수익률 15m/1h/4h/8h(양수 = 남은 포지션에 유리 = 줄인 것이 수익 희생) |
| K 조합 | (ADD 유무) x (REDUCE 유무) 4그룹 |
| L 레버리지 | peak 레버리지 전 표본 3분위. 그룹별 CATASTROPHIC 비율, 청산 수, MAE |
| M 자본 대비 크기 | OPEN/ADD 주문 notional(|qty|/mid) / equity. equity 3분위(로그), 연도별 |
| N 손실 후 | 큰 손실 = 종료 episode net ROE 하위 5%. 그 episode 종료 뒤 1h/6h/24h의 OPEN/ADD 주문 notional/equity 를 같은 달 창 밖 중앙값으로 나눈 비율의 중앙값, 주문 수·ADD 수 |
| R funding | episode별 지불/수취, funding / gross, funding / net |
| O 사례 | 2018-09-21, 2021-05-05, 2021-05-19에 걸친 episode 타임라인 (기술. 2018-09-21 손실 대부분은 XRPU18·ETHUSD라 시장경로 없음) |

## 11. 통계

- 단위: episode (또는 사건). CI는 **UTC ISO 주 단위 cluster bootstrap**, B = 2,000, seed = 20260927 (24h horizon이 날짜를 넘으므로 주 단위).
- CF 비교는 episode별 차이 d = ROE(실제) - ROE(CF)의 평균과 CI. 합계 XBT도 보고.
- 판정에 쓰는 것은 12절 조건뿐. 나머지 표는 기술 통계.

## 12. 판정 규칙

| 기능 | SUPPORTED | NOT_SUPPORTED | MIXED |
|---|---|---|---|
| **ADD** | ADD episode에서 d(실제 - CF1) 평균 > 0 이고 CI 하한 > 0, **그리고** 연도 3/4 이상 > 0, **그리고** d(실제 - CF2 0.5%) ≥ 0 과 d(실제 - CF2 1.0%) ≥ 0 (점추정), **그리고** CATASTROPHIC 비율(실제) ≤ 1.5 x CF1 비율 + 1 episode | d(실제 - CF1) 평균 ≤ 0 | 그 외 |
| **PARTIAL_CLOSE** | REDUCE episode에서 (A) d(실제 - CF3) CI 하한 > 0, **또는** (B) MAE 개선(MAE 실제 - MAE CF3, ROE 단위) CI 하한 > 0 이고 d(실제 - CF3) CI 상한 ≥ 0 | d CI 상한 < 0 이고 MAE 개선 CI 상한 ≤ 0 | 그 외 |
| **LONG_HOLD** (무손절) | d(실제 - CF4) 평균 > 0, CI 하한 > 0, 연도 3/4 이상 > 0 (CF4가 발동한 episode 기준) | 평균 ≤ 0 | 그 외 |
| **LOW_LEVERAGE** | CATASTROPHIC 비율이 peak 레버리지 3분위에서 단조 증가하고 최상위 ≥ 2 x 최하위, **그리고** 청산 episode의 75% 이상이 최상위 3분위 | 최상위 비율 ≤ 최하위 비율 | 그 외. 상관일 뿐 인과가 아님을 명시 |
| **LOSS_AFTER_SIZE_CONTROL** | 6h와 24h 비율 중앙값 모두 ≤ 1.10 | 6h 또는 24h 중앙값 ≥ 1.50 | 그 외 |

**전체 E2**: ADD·PARTIAL_CLOSE·LONG_HOLD 중 2개 이상 SUPPORTED → POSITION_MANAGEMENT_EDGE_SUPPORTED / 3개 모두 NOT_SUPPORTED → NOT_SUPPORTED / 그 외 MIXED.
ENTRY_EDGE는 별도 보고(전체 판정에 넣지 않음). "성과가 진입보다 관리에서 나왔나"는 ENTRY_EDGE와 기능 판정을 나란히 놓고 서술한다.

## 13. US-B 구현 게이트

기능이 SUPPORTED이고 추가로 (a) 연도 3/4 이상 같은 방향 (b) 청산 제외 결과에서도 같은 부호일 때만 "후속 구현 후보". 이번 단계에서 Paper Engine은 수정하지 않는다.

## 14. 참고 (판정 제외)

CURRENT_BYBIT_COST_REFERENCE: 실제 경로의 fee만 Bybit VIP 0(maker 0.02%, taker 0.055%, D4 OFFICIAL)로 바꾼 net ROE. 역선택·spread 차이는 반영하지 않음.

## 15. 저장

`data/research/expert_execution/e2/`: minute_grid.parquet, episode_metrics.parquet, add_events.parquet, reduce_events.parquet,
counterfactual_metrics.parquet, summary.json. E1 `market/`는 삭제하지 않는다.
