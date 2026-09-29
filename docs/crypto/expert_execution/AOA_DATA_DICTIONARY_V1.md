# AOA BitMEX Ledger Data Dictionary V1

작성 2026-09-27. 모든 항목은 실제 파일 값으로 확인했다(`data/research/expert_execution/schema_profile.json`,
`aoa_reports/wallet_schema_profile.json`). 이름은 원본 그대로이고, 의미 해석 옆에 근거를 적었다. 근거 없는 mapping은 하지 않았다.

공통: UTF-8 BOM, 구분자 `,`, 따옴표 셀 안에 줄바꿈 있음. 빈 셀은 빈 문자열이다(`NULL` 표기 없음).

---

## 1. Execution (41열, 1,444,583행)

dtype 열은 문자열 원본을 숫자로 바꿀 수 있는지로 판정했다. "빈 값"은 빈 문자열 행 수다.

| 열 | dtype | 빈 값 | 범위 / 값 | 의미 (근거) |
|---|---|---|---|---|
| `date` | string | 0 | 2018-03-05 ~ 2021-12-31 | `transacttime`의 UTC 날짜 |
| `execid` | string(UUID) | 0 | 1,444,583 고유 | 체결 고유키. 중복 0 |
| `orderid` | string(UUID) | 242 | Trade 실주문 23,416 고유 (+zero UUID) | 주문 id. fill 여러 개가 공유. zero UUID `00000000-...`: Funding 5,126 / Liquidation 28 / Settlement 8. 빈 값 242는 Funding(2018-03 ~ 2021-02 일부) |
| `clordid`, `clordlinkid` | - | 전부 | - | 항상 빈 값 |
| `account` | string | 0 | `aoa` 1종 | 계좌 별칭 |
| `symbol` | string | 0 | 46종 | BitMEX 상품 |
| `side` | string | 5,368 | Buy 709,610 / Sell 729,605 | 이 fill의 방향. 빈 값은 Funding 전부 |
| `lastqty` | int | 0 | 1 ~ 85,169,400 | fill 계약 수. XBTUSD는 1계약 = 1 USD (execcost 항등식) |
| `lastpx` | float | 0 | 0 ~ 68,511.1 | 체결가. Funding 행은 funding 기준가, Settlement 행은 결제가. 0은 XBT7D 바이너리 결제 1행 |
| `lastliquidityind` | string | 5,368 | AddedLiquidity 966,607 / RemovedLiquidity 472,608 | maker / taker. 빈 값은 Funding |
| `orderqty` | int | 0 | 1 ~ 85,169,400 | 주문 수량 (fill 시점) |
| `price` | float | 0 | 0 ~ 68,511.1 | 주문 지정가 |
| `displayqty` | float | 1,442,053 | 0만 존재 | 빙산 주문 표시 수량. 2,530행이 0 (숨김 주문) |
| `stoppx` | float | 1,439,709 | 0.0798 ~ 10,234 | Stop/StopLimit 발동가 |
| `pegoffsetvalue`, `pegpricetype`, `contingencytype`, `ordrejreason` | - | 전부 | - | 항상 빈 값 |
| `currency` | string | 0 | USD 1,393,694 / USDT 34,375 / XBT 16,514 | 계약의 호가 통화 |
| `settlcurrency` | string | 0 | `XBt` 1종 | 정산 통화 = satoshi. USDT 표기 계약도 XBT 정산 |
| `exectype` | string | 0 | Trade 1,439,207 / Funding 5,368 / Settlement 8 | 이벤트 종류. Liquidation 값은 없음 |
| `ordtype` | string | 0 | Limit 1,435,642 / Stop 4,773 / Market 4,067 / StopLimit 101 | 주문 유형 (Funding·Settlement는 Limit으로 기록) |
| `timeinforce` | string | 0 | GoodTillCancel / ImmediateOrCancel / AtTheClose / FillOrKill | AtTheClose는 Funding·Settlement |
| `execinst` | string | 1,265,606 | ParticipateDoNotInitiate 163,113 (+JSON 배열 표기 7,944) / LastPrice 3,660 / Close 2,982 / IndexPrice 681 / Close,LastPrice 529 / `["Close"]` 68 | post-only, reduce/청산, stop 기준가. 같은 값이 `X`와 `["X"]` 두 표기로 섞여 있음 |
| `ordstatus` | string | 0 | PartiallyFilled 1,420,626 / Filled 23,957 | fill 직후 주문 상태 |
| `triggered` | string | 1,439,713 | StopOrderTriggered 4,870 | stop 발동 |
| `workingindicator` | string | 0 | true / false | 주문이 아직 살아 있음 |
| `leavesqty` | int | 0 | 0 ~ 24,965,238 | fill 후 남은 수량 |
| `cumqty` | int | 0 | 1 ~ 85,169,400 | 누적 체결 수량 |
| `avgpx` | float | 0 | 0 ~ 68,511.1 | 주문 평균 체결가 |
| `commission` | float | 0 | -0.0005 ~ 0.0025 (Trade) | **요율**. 음수 = 리베이트. Funding 행은 계좌에 적용된 funding rate(부호 포함) |
| `tradepublishindicator` | string | 5,368 | PublishTrade / DoNotPublishTrade 42 | |
| `text` | string | 0 | 8종 | `Submission from www.bitmex.com`, 가격·수량 정정 문구, `Funding`, `Triggered: Order stop price reached\n...`, `Position Close from www.bitmex.com`, **`Liquidation` 59**, `Settlement` |
| `trdmatchid` | string(UUID) | 0 | | 거래소 체결 id (Funding은 funding 이벤트 id, 같은 시각 모든 심볼 공유) |
| `execcost` | int | 0 | -1.83e11 ~ 3.01e11 | **부호 있는 XBt 가치. 회계 기준값.** 3절 항등식 |
| `execcomm` | int | 0 | -1.92e8 ~ 2.91e8 | 수수료 XBt (양수 = 지불, 음수 = 리베이트). Funding 행은 funding 지불(+)/수취(-). `round(\|execcost\| x commission)`과 100% 일치 (최대 1 sat) |
| `homenotional` | float | 0 | | 기초자산 단위 부호 있는 크기 (XBTUSD는 XBT, ETHUSD는 ETH). **부호 = 방향**, Funding 행에서는 포지션 방향 |
| `foreignnotional` | float | 0 | | 호가 통화 단위 부호 있는 크기 |
| `transacttime` | string | 0 | 길이 21~26 | UTC 이벤트 시각 `YYYY-MM-DD HH:MM:SS[.f{1,6}]` |
| `timestamp` | string | 0 | | UTC 기록 시각. `transacttime`과 같거나 최대 25초 늦음 (Funding +약 12ms) |

`homenotional` 44,394행과 `foreignnotional` 2,572행, `commission` 대부분은 `6.75E-4` 같은 지수표기지만 파싱 손실은 없다(실수 필드).
정수 필드(`execcost`, `execcomm`, 수량)에는 지수표기가 없다.

## 2. Wallet (14열, 2,253행 + 빈 행 2,135)

| 열 | 값 | 의미 / 주의 |
|---|---|---|
| `date` | 2018-03-05 ~ 2021-12-31 | **유일하게 신뢰 가능한 시각 정보** |
| `transactid` | UUID | |
| `account` | `aoa` | |
| `currency` | `XBt` | satoshi |
| `amount` | 정수 | 부호 있는 금액. 온전함 |
| `transactstatus` | Completed 2,246 / Canceled 7 | Canceled 7건은 전부 Withdrawal, 잔액에 반영 안 됨 |
| `address` | 심볼 / `External exchange` / 빈 값 | RealisedPNL 행은 **심볼**, 출금은 `External exchange`(63) |
| `network` | `btc` 63 / 빈 값 | 출금 행 |
| `text` | 전부 빈 값 | |
| `timestamp`, `transacttime` | `57:26.3` 형태 2,253행 전부 | **손상**: 시·날짜가 잘리고 mm:ss.f만 남음. RealisedPNL은 전부 `00:00.0` (정시 정산과 일치) |
| `transacttype` | RealisedPNL 2,172 / Withdrawal 63 / Deposit 18 | funding·수수료는 RealisedPNL 안에 포함 |
| `tx` | `-` | 온체인 txid 비공개 |
| `walletbalance` | 정수 또는 지수표기 | 정산 배치 뒤 잔액. **406행(2021-06-05 ~ 11-16)이 `1.00334E+11` 같은 유효숫자 6자리로 손실**. 같은 날 12:00 배치 행들은 같은 값을 공유 |

RealisedPNL 정산 시각: 원장 대조로 **매일 12:00 UTC, 창 [D-1 12:00, D 12:00)** 로 확정(보고서 10절).

## 3. Contract model (원장에서 판정)

각 Trade fill에서 `execcost`가 아래 둘 중 하나로 정확히 떨어진다. 심볼별로 p99 상대오차가 1e-3 이하인 쪽을 채택했다.

```
inverse : execcost = signed_qty * M / price     (M = -1e8,  XBt per 1 USD 계약)
quanto / linear : execcost = signed_qty * M * price
```

| 유형 | 심볼 | M (XBt) | 수량 단위 | 가격 단위 | PnL·수수료 통화 | 항등식 p99 오차 |
|---|---|---|---|---|---|---|
| inverse | XBTUSD, XBTU21, XBTH20, XBTH19, XBTM18, XBTZ18, XBTM19, XBTU18 | -1e8 | USD 계약 | USD/XBT | XBt | 2e-6 ~ 2.4e-4 (계약당 원가 정수 반올림) |
| quanto | ETHUSD, ETHUSDM21, BCHUSD, BNBUSDT | 100 | 계약 | USD(T) | XBt | 0 |
| quanto | LTCUSD | 200 | 계약 | USD | XBt | 0 |
| quanto | XRPUSD | 20,000 | 계약 | USD | XBt | 0 |
| quanto | DOTUSDT, LINKUSDT | 10,000 | 계약 | USDT | XBt | 0 |
| quanto | DOGEUSDT | 100,000 | 계약 | USDT | XBt | 0 |
| quanto | ADAUSDT | 1,000,000 | 계약 | USDT | XBt | 0 |
| quanto | YFIUSDTZ20 | 10 | 계약 | USDT | XBt | 0 |
| linear (XBT 호가) | XRP·ADA·TRX·EOS·BCH·LTC·ETH의 `*M18/U18/Z18/H19/M19/U19/H21` 26종, XBT7D_U110 | 1e8 | 코인 1개 | XBT | XBt | 0 |

- quanto의 XBt 손익은 가격 1포인트당 M으로 고정이다. XBT/USD 환율은 손익에 들어가지 않는다(quanto의 정의).
- 부호 규약: inverse는 M이 음수라서 Buy의 execcost가 음수, quanto·linear는 Buy가 양수. 원장 첫 행(Sell 2,000 XBTUSD @ 11,441.5 → +17,480,000)과 ETHUSD Buy 1,051 @ 2,115 → +222,286,500으로 확인.
- 무기한 / 만기: 이름이 `[FGHJKMNQUVXZ]YY`로 끝나거나 `XBT7D`면 만기, 나머지 9종이 무기한. 무기한에만 Funding 행이 있다는 사실과 일치.
- **Bybit USDT linear 공식(수량 x 가격 차, USDT 정산)은 이 원장 어디에도 맞지 않는다.** USDT 표기 계약도 XBt 정산 quanto다.

수수료 요율 (원장 `commission`, Trade):

| 연도 | maker (AddedLiquidity) 주요 요율 | taker (RemovedLiquidity) 주요 요율 |
|---|---|---|
| 2018 | -0.025% (44,976) / -0.0225% (26,893) / -0.05% | +0.075% (53,571) / +0.0675% (25,419) / +0.25% (3,120, 알트 선물) |
| 2019 | -0.025% (94,704) / -0.05% (613) | +0.075% (115,562) |
| 2020 | -0.025% (146,640) | +0.075% (166,579) |
| 2021 | -0.025% (442,153) / **-0.01%** (200,036) | +0.075% (80,012) / +0.025% (15,238) / +0.03% |

-0.0225%·+0.0675%는 표준 요율의 90%로 할인 등급 적용으로 보이지만 원장만으로 확정할 수 없다. 2021년 -0.01% maker는 일부 상품·기간의 요율 변경이다.
유동성 표시와 요율 부호가 어긋나는 fill 4,721개(0.3%)는 원장값 그대로 두었다.

## 4. 정규화 산출물 (`data/research/expert_execution/aoa_normalized/`, git 제외)

| 파일 | 행 | 내용 |
|---|---|---|
| `executions.parquet` | 1,444,583 | 원본 41열 전부 + `src_file`, `src_row`, `seq`(전역 순서), `transact_ts`/`record_ts`(UTC), `side_sign`, `signed_qty`, `is_maker`, `year`. 숫자 열은 Int64/float64 |
| `positions.parquet` | 1,444,583 | `seq`별 `pos_before`, `pos_after`, `action`, `closed_qty`, `opened_qty`, `realized_gross_sat`, `avg_entry_before`, `cost_before_sat`, `episode_id`, `closes_episode_id` |
| `episodes.parquet` | 3,185 | episode별 방향, 시작·종료, 최대 포지션, fill 수, 실현 gross, 수수료, funding, 청산 fill 수, 종료 방식 |
| `orders.parquet` | 23,444 | 실주문 23,416 + 청산 fill 28개(`SYS-<seq>`, zero UUID를 개별 주문으로 분리). 주문별 첫 fill 행동, fill 수, 수량, maker 비중, 실현손익, 수수료 |
| `wallet.parquet` | 2,253 | 빈 행 제거, `amount_xbt_sat`, `walletbalance_sat`, `date_utc` |
| `funding_snapshots.parquet` | 3,964 | 8h 스냅샷: 총노출, 미실현, 자본 추정, 레버리지, 만기선물 보유 플래그 |
| `wallet_reconciliation.parquet` | 2,174 | 날짜 x 심볼 복원 실현손익 vs wallet RealisedPNL |

`action` 값: OPEN_LONG/SHORT, ADD_LONG/SHORT, REDUCE_LONG/SHORT, CLOSE_LONG/SHORT, REVERSE_TO_LONG/SHORT, FUNDING, SETTLEMENT_CLOSE.
원장에 LIQUIDATION 행동은 따로 없고, `text == "Liquidation"`인 fill이 위 행동 중 하나로 분류된 뒤 episode 종료 방식에 LIQUIDATION으로 남는다.
