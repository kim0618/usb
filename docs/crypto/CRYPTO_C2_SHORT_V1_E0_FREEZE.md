# US-B CRYPTO C2 SHORT V1 — E0 동결 계약

작성 2026-10-03. 이 문서는 E1 미래 손익을 계산하기 전에 동결한다.

## 후보 감사

| 후보 | 시장 논리 | PIT-safe feature / coverage | 실시간 | 비용 | 기존 연구 중복 | E0 |
|---|---|---|---|---|---|---|
| A Perp Premium Exhaustion | 레버리지 premium 소진 | perp/spot basis, OI; 2021-02-01~2026-09-23 | 가능(spot 신규 poll 필요) | 왕복 taker+funding | D5.2 S1/S2 및 E2 SHORT와 직접 중복; premium 전체 변위도 비용 이하 | 제외 |
| B Leveraged Blow-off Failure | 신규 LONG 유입을 동반한 급등이 고점 돌파에 실패하면 trapped-long unwind | perp 1m OHLC, PIT OI; 전 기간 결손 없는 정본 | 가능(현재 feed만) | 왕복 taker+funding | 기존 실패 C2는 급등+OI **감소** 숏스퀴즈; 본 가설은 OI **증가** 신규 long 축적 | **선정** |
| C Crowded Long Unwind | funding/basis 과열 뒤 상승 실패 | funding, basis, OI, 변동성; funding은 8h 계단형 | 가능(spot 필요) | 왕복 taker+funding | D5/D5.2 funding·basis·고변동성 조합과 높은 중복 | 제외 |

선정은 B의 결과를 보기 전에 확정한다. C1 LONG의 반전도, 기존 실패 SHORT threshold도 사용하지 않는다.

## B: 동결 신호

결정봉 `t` 종가에서 아래가 모두 참일 때만 SHORT event다.

1. `r15 = ln(close[t]/close[t-15])`가 직전 30개 완결 UTC일의 r15 99th percentile보다 큼.
2. PIT OI의 `ln(OI[t]/OI[t-15]) > 0` (OI record는 timestamp+5분부터 사용).
3. `high[t] >= max(high[t-60:t])` 이지만 `close[t] < close[t-1]` (신규 60분 고점 돌파 실패).

연속 true bar는 하나의 event. 포지션이 FLAT일 때 event 첫 봉만 거래하며 보유 중 발생 신호는 shadow에는
남기되 거래하지 않는다. 진입은 `open[t+1]`, 청산은 정확히 `open[t+241]`인 **240분 benchmark** 하나다.
threshold/horizon sweep과 새로운 exit 최적화는 금지한다.

## E1 실행·비용·판정

- Bybit BTCUSDT Linear Perpetual, SHORT, 실제 거래 단위, 동시 포지션 1개.
- taker 5.5bp를 진입·청산 notional 각각 부과. 보유 중 settlement funding을 방향 부호대로 반영.
- MFE/MAE는 진입 후 청산 전까지 1m high/low 기준.
- mean/median net, PF, win rate, 거래 수, UTC 7일 block bootstrap 95% CI, 연도 fold, 기간 절반,
  절대 net 상위 5% 제거 결과를 보고한다.
- `CLEARLY_NEGATIVE`: 거래 >= 100이고 mean net <= 0이며 CI 상한 <= 0, 또는 PF < 0.8이면서
  5개 연도 중 4개 이상 음수. 그 외 mean net > 0 또는 CI가 0을 포함하고 PF >= 0.8이면
  `INCONCLUSIVE_BUT_PLAUSIBLE`; CI 하한 > 0, PF > 1, 연도 과반 양수면 `POSITIVE_PREVALIDATION`.

E1을 통과할 경우 PAPER V1도 같은 240분 benchmark만 사용한다. LIVE는 신호 표시만 허용한다.
