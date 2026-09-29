# US-B CRYPTO Expert Execution Study E0: AOA / BitMEX Ledger Ingest + Independent Audit V1

작성 2026-09-27. 대상은 공개 BitMEX 체결 원장 `aoa_public_2021-12-31_with_letter.zip` 1건.
코드 `backend/app/crypto/research/expert_execution/`, 산출물 `data/research/expert_execution/`.
재실행: `PYTHONPATH=backend .venv/bin/python -m app.crypto.research.expert_execution.e0 --zip <zip>` (95초).

관련 문서: `AOA_DATA_DICTIONARY_V1.md`(스키마), `AOA_CLAIM_AUDIT_V1.md`(외부 주장 대조), `AOA_RESEARCH_PLAN_V1.md`(E1 범위).

---

## 0. 결론

| 항목 | 결과 |
|---|---|
| ZIP 안전성 | SAFE (6 member, traversal·절대경로·암호화·중복·실행파일 0, 최대 압축비 5.5배) |
| 행 수 | execution **1,444,583** (Trade 1,439,207 / Funding 5,368 / Settlement 8), wallet 2,253 (빈 행 2,135 제외) |
| 기간 | 2018-03-05 07:28 UTC ~ 2021-12-31 20:00 UTC, 46 심볼 |
| 포지션 복원 | **가능, 검증됨**. 복원 포지션이 funding 스냅샷 **5,368 / 5,368**과 일치 (불일치 0) |
| 회계 모델 | **검증됨**. 복원 실현손익이 wallet RealisedPNL과 일별×심볼 2,173칸 중 99.0%가 0.001 XBT 이내, 전 기간 합계 차이 -0.0019 XBT |
| Maker/Taker | 거래가치 기준 maker **70.8%**, 순수수료 거래가치 대비 **+0.44bp** (2021년은 순 리베이트 -0.56bp) |
| 외부 주장 | 13개 항목 중 VERIFIED 5 / PARTIALLY 3 / INSUFFICIENT_DATA 5 / NOT_REPRODUCIBLE 0 (요약은 15절) |
| 시장데이터 필요 | CLAIM 1·3·4·6·9 및 2의 mark 기준 판정. BitMEX 공개 일별 trade/quote 덤프로 가능 (17절) |
| 전략 구현 | 0. 운영 서버·Paper 계좌·D2/D5 데이터 접근 0. commit/push 0 |
| **E0 판정** | **PASS** (Exit gate 12개 전부 충족, 22절) |

**가장 중요한 사실 두 가지.**
(1) 이 원장은 BitMEX 자체 `execcost` 덕분에 inverse·quanto·linear 계약을 한 가지 공식으로 회계할 수 있고,
그 결과가 거래소가 남긴 funding 스냅샷과 wallet 정산액 양쪽에 맞는다. 즉 **144만 fill이 3,185개 포지션 episode로 정확히 복원된다.**
(2) 이 계좌의 실행 비용은 US-B D5가 막힌 비용과 차원이 다르다. D5.1 기준 taker 왕복 약 11bp, maker 가정 왕복 약 4bp인데,
이 계좌는 거래가치 대비 순수수료가 편도 0.44bp이다. 다만 이것은 **BitMEX의 maker 리베이트(-0.025%) 구조** 위의 숫자라서
현재 US-B 거래소에 그대로 옮길 수 없다(18절).

---

## 1. ZIP path / sha256 / size

| 항목 | 값 |
|---|---|
| exact path | `/mnt/c/Users/jinsung/Downloads/aoa_public_2021-12-31_with_letter.zip` (Windows `C:\Users\jinsung\Downloads\`) |
| size | 113,381,570 bytes |
| mtime | 2026-09-27 12:07:11 +0900 |
| sha256 | `b6f1dc7aadf8209bf6c99fd516a06c0cabdc77c5f16f9fc8df92fdf1d8d01b9a` |
| 원본 처리 | 원위치 read-only 취급 (수정 0). 해제본은 `data/research/expert_execution/aoa_raw/` (chmod a-w) |
| ZIP comment | 없음 |

central directory 검사 결과 (해제 전, `ingest.check_zip`):

| 검사 | 결과 |
|---|---|
| member 수 | 6 |
| 압축 / 해제 합계 | 113,380,676 / 601,794,512 bytes (5.31배) |
| member별 최대 압축비 | 5.5배 (한도 100배) |
| `../`·절대경로·하위 폴더 | 0 |
| 중복 이름 / 암호화 member | 0 / 0 |
| 실행파일 확장자 | 0 |
| 파일명 인코딩 | `90일 서한.txt`만 UTF-8 flag, 나머지 ASCII |

## 2. Extracted file list

| 파일 | 분류 | bytes | 물리 줄 | CSV 레코드 | sha256 앞 12자 |
|---|---|---|---|---|---|
| `90일 서한.txt` | letter | 8,798 | 107 | - | `237d3bf22cf4` |
| `aoa-execution-2018-03-01-2018-12-31.csv` | execution | 67,993,490 | 166,079 | 164,096 | `8f2aa1af3caa` |
| `aoa-execution-2019-01-01-2020-12-31.csv` | execution | 219,076,835 | 539,516 | 529,632 | inventory.csv |
| `aoa-execution-2021-01-01-2021-06-30.csv` | execution | 191,784,153 | 486,170 | 457,603 | inventory.csv |
| `aoa-execution-2021-07-01-2021-12-31.csv` | execution | 122,612,445 | 296,588 | 293,252 | inventory.csv |
| `aoa-wallet-2018-03-01-2021-12-31.csv` | wallet | 318,791 | 4,389 | 4,388 | inventory.csv |

전체 sha256은 `data/research/expert_execution/inventory.csv`. 모든 CSV는 UTF-8 BOM, 구분자 `,`.
물리 줄 수가 레코드 수보다 많은 이유는 `text` 셀 안의 줄바꿈(`Triggered: ...\nSubmission ...` 등)이다.
pyarrow 기본 설정은 이 파일을 읽지 못하고 `newlines_in_values=True`가 필요하다.

**서한 파일**: 2018~2021 원장과 무관한 시장 전망 글(BTC 적정가 85k, 나스닥·코스피 전망, 사칭·봇 판매 경고).
원장 통계에 대한 주장은 없다. 작성 시점은 본문상 원장 기간 이후로 보인다. 계좌 소유자 신원은 이 파일로 확인되지 않는다.

## 3. Total rows

| 구분 | 값 |
|---|---|
| execution 레코드 | **1,444,583** (파일별 164,096 + 529,632 + 457,603 + 293,252) |
| exectype | Trade 1,439,207 / Funding 5,368 / Settlement 8 |
| 고유 execID | 1,444,583 (중복 0) |
| 고유 orderID (Trade) | 실주문 23,416 + zero UUID 1개(청산 fill 28건이 공유). 주문당 fill 중앙값 17, p99 631 |
| 연도별 fill | 2018 163,142 / 2019 211,699 / 2020 315,682 / 2021 748,684 |
| 연도별 주문 | 2018 11,855 / 2019 3,858 / 2020 3,761 / 2021 3,945 |
| wallet | 원시 4,388 레코드 중 2,135개가 완전 빈 행(스프레드시트 흔적), 실제 2,253 |

"144만 행"이라는 외부 주장은 execution 행 수로 정확히 맞는다. 다만 **144만은 fill 수이고, 주문은 2만 3천 건, 포지션 episode는 3,185개**다.
144만을 "거래 144만 번"으로 읽으면 안 된다.

## 4. Date coverage

| 파일 | first | last | 이전 파일과 간격 |
|---|---|---|---|
| 2018 | 2018-03-05 07:28:18 | 2018-12-31 23:59:43 | - |
| 2019-2020 | 2019-01-01 04:00:00 | 2020-12-31 20:00:00 | 4.0h, 겹침 없음 |
| 2021 H1 | 2021-01-01 04:00:00 | 2021-06-30 22:46:15 | 8.0h, 겹침 없음 |
| 2021 H2 | 2021-07-01 01:26:22 | 2021-12-31 20:00:00 | 2.7h, 겹침 없음 |

- 시간대: **UTC**. 근거: funding 행이 정확히 04:00·12:00·20:00에만 찍힘(1,792/1,787/1,789), 분기 결제가 11:59:59.999999.
- 파일 경계에 걸친 주문 1건, 중복 0.
- 파일 내부는 시간순이 아니다(날짜 안에서 심볼별로 묶인 순서로 보임, 역행 751/2,055/808/706회, 최대 29일).
  정규화 데이터는 `transacttime`, 파일, 행 번호 순으로 안정 정렬해 `seq`를 부여했다.

## 5. Symbols

46 심볼. 계약 유형은 원장의 `execcost` 항등식으로 **데이터에서 직접 판정**했다(9절, 문서 `AOA_DATA_DICTIONARY_V1.md` 3절).

| 기초자산 | fill | fill 비중 | 거래가치(XBT) | 가치 비중 |
|---|---|---|---|---|
| BTC (XBTUSD + XBT 선물) | 973,814 | 67.7% | 1,631,744 | **92.4%** |
| ETH (ETHUSD, ETHUSDM21, ETH*18) | 332,947 | 23.1% | 116,053 | 6.6% |
| 기타 (XRP·LTC·BCH·DOT·DOGE·LINK·BNB·EOS·TRX·ADA·YFI) | 132,446 | 9.2% | 18,852 | 1.1% |

상위: XBTUSD 941,007 fill(가치 91.8%), ETHUSD 327,428(6.5%), XRPUSD 55,495, XBTU21 25,747, LTCUSD 17,201.
계약 유형별: inverse 8종(가치 92.4%), quanto 11종(7.0%), XBT 호가 linear 27종(0.6%).

## 6. Schema

execution 41열, wallet 14열. 모든 열을 실제 값으로 확인했고 이름은 원본 그대로다(전부 소문자, BitMEX API의 camelCase가 아님).
상세는 `AOA_DATA_DICTIONARY_V1.md`, 기계 판독본은 `schema_profile.json`(execution), `aoa_reports/wallet_schema_profile.json`.

요청 필드 대응 (추측 mapping 없음):

| 요청 이름 | 실제 열 | 비고 |
|---|---|---|
| execID / orderID | `execid` / `orderid` | 청산·결제·대부분 funding은 zero UUID, funding 242행은 빈 문자열 |
| lastQty / lastPx | `lastqty` / `lastpx` | 정수 계약 수 / 체결가 |
| orderQty / price / avgPx | `orderqty` / `price` / `avgpx` | 주문 기준 |
| ordType / execType | `ordtype` / `exectype` | exectype에 Liquidation 없음. 청산은 `text == "Liquidation"` |
| liquidityInd | `lastliquidityind` | AddedLiquidity / RemovedLiquidity / 빈 값(funding) |
| commission / fee | `commission`(요율) / `execcomm`(XBt 금액) | 음수 = 리베이트·funding 수취 |
| transactTime / timestamp | `transacttime` / `timestamp` | 둘 다 UTC, 차이 최대 25초 |
| leavesQty / cumQty | `leavesqty` / `cumqty` | |
| text / reason | `text` | 줄바꿈 포함 |
| position 관련 필드 | **없음** | 포지션은 복원해야 한다. funding 행이 8시간마다 포지션을 알려 준다(11절) |
| 가치·수수료 원장값 | `execcost`, `execcomm`, `homenotional`, `foreignnotional` | `execcost`가 회계 기준 |

## 7. Quality issues

| 검사 | 결과 |
|---|---|
| 완전 중복 행 / 중복 execID | 0 / 0 |
| timestamp 파싱 실패 | 0 (소수점 1~6자리 혼재, `format="ISO8601"`로 전부 파싱) |
| side 이상값 | 0. 빈 side는 Funding 5,368행뿐 |
| qty 0·음수 | 0 |
| 가격 0 | 1행: `XBT7D_U110` Settlement (주간 바이너리의 0 결제, 정상) |
| symbol 누락 | 0 |
| execcomm = round(\|execcost\| x commission) | 100% (최대 차이 1 sat) |
| maker인데 양의 요율 / taker인데 음의 요율 | 2,391 / 2,330 fill (전체의 0.3%). 원장값 그대로 두고 표시만 함 |
| 파일 내 시간 역행 | 있음 (4절). 정렬로 해소 |
| **wallet timestamp 손상** | `timestamp`·`transacttime` 2,253행 전부 `57:26.3` 같은 mm:ss.f만 남음. 날짜(`date`)만 신뢰 가능 |
| **wallet 잔액 정밀도 손실** | `walletbalance` 406행(2021-06-05 ~ 2021-11-16)이 `1.00334E+11` 같은 유효숫자 6자리 지수표기. 금액(`amount`)은 온전 |
| wallet 빈 행 | 2,135행 |
| wallet 행 순서 | 날짜 순이 아님 (04-27 출금이 04-28 정산 뒤에 처리된 사례) |

wallet 손상 3건은 모두 스프레드시트를 거친 흔적으로 보이며, 원본 거래소 export가 아니라는 뜻이다.
execution 파일은 정수 열(`execcost`, `execcomm`, 수량)에 지수표기가 없어 이 손상을 받지 않았다.

## 8. Maker / Taker

`lastliquidityind` 기준. unknown 0.

| 연도 | fill maker 비중 | 거래가치 maker 비중 | 계약 수 maker 비중 | 순수수료 (XBT) | 거래가치 대비 순수수료 |
|---|---|---|---|---|---|
| 2018 | 49.1% | 64.0% | 66.7% | +35.61 | +1.14bp |
| 2019 | 45.0% | 69.4% | 67.9% | +24.13 | +0.53bp |
| 2020 | 46.9% | 68.6% | 70.6% | +40.24 | +0.66bp |
| 2021 | **85.9%** | **81.1%** | 77.2% | **-21.98** | **-0.56bp** |
| 전체 | 67.2% | 70.8% | 73.4% | +78.00 | +0.44bp |

전체 수수료 지불 380.3 XBT, 리베이트 수취 302.3 XBT. funding은 순 **149.6 XBT 수취**(net short 성향과 맞물림, 15절 CLAIM 8).

행동별(fill 단위, 거래가치 maker 비중 / 순수수료 bp):

| 행동 | 2018 | 2019 | 2020 | 2021 |
|---|---|---|---|---|
| OPEN | 68% / +0.74 | 98% / -2.48 | 39% / +3.71 | 36% / +4.46 |
| ADD | 66% / +0.87 | 69% / +0.54 | 71% / +0.46 | 82% / -0.63 |
| REDUCE | 60% / +1.54 | 69% / +0.55 | 66% / +0.88 | 80% / -0.49 |
| CLOSE | 59% / +1.92 | 80% / -0.58 | 98% / -2.25 | 94% / -1.11 |
| REVERSE | 78% / -0.31 | 82% / -0.72 | 80% / -0.23 | 69% / +0.55 |

OPEN·CLOSE는 2019년 이후 연 30~70건뿐이라 비중이 흔들린다. 물량은 ADD·REDUCE가 99%를 차지한다.
**이 계좌는 포지션을 거의 flat으로 만들지 않고 한 방향 안에서 분할 진입·분할 청산을 반복한다(14절).**
maker 비중이 fill 기준(67%)보다 가치 기준(71%)에서 높다는 것은 큰 물량일수록 지정가로 체결했다는 뜻이다.

## 9. Market / Limit

| ordType | fill | 거래가치(XBT) | 가치 비중 | 유동성 |
|---|---|---|---|---|
| Limit | 1,430,266 | 1,758,404 | 99.5% | maker 966,510 / taker 463,756 |
| Market | 4,067 | 3,265 | 0.18% | 전부 taker |
| Stop | 4,773 | 4,352 | 0.25% | 전부 taker |
| StopLimit | 101 | 628 | 0.04% | maker 97 / taker 4 |

- **taker 체결의 98%는 Market 주문이 아니라 지정가가 즉시 체결된 것(marketable limit)이다.** "시장가 진입"을 `ordtype == Market`으로 세면 진입 주문 289건, taker로 세면 3,530건으로 12배 차이가 난다. 외부 주장 CLAIM 1은 이 정의를 먼저 고정해야 한다.
- post-only(`ParticipateDoNotInitiate`) 주문 6,046 / 23,416.
- `Close`(reduce-only·청산 버튼) fill 3,050, 청산 버튼 문구(`Position Close from www.bitmex.com`) 3,050.
- stop 발동 fill 4,870 (LastPrice 3,563 / IndexPrice 681 / Close,LastPrice 529 / 기타 97).

## 10. Wallet reconciliation

| 항목 | 값 |
|---|---|
| 입금 (Completed 18건) | **14.48925714 XBT** |
| 출금 (Completed 56건) | **2,814.54321713 XBT** (연도별 82.95 / 259.18 / 622.15 / 1,850.26) |
| 출금 Canceled 7건 | 17.98581713 XBT (포함하면 2,832.53) |
| wallet RealisedPNL 합계 | 3,537.32369404 XBT (연도별 +188.43 / +516.37 / +763.64 / +2,068.88) |
| 입금 - 출금 + 실현손익 | 737.26973405 XBT |
| 종료 잔액 (2021-12-31, 명시값) | **737.26973405 XBT** (위 항등식과 satoshi까지 일치) |
| 최대 잔액 | 1,531.13 XBT |
| 첫 입금 | 0.17244633 XBT (2018-03-05) |

외부 주장 "넣은 돈 14.49 BTC, 뺀 돈 2,814 BTC"는 **Completed 기준으로 정확히 재현된다.**
단위는 `currency == XBt`(satoshi)이고 1e8로 나눴다. "뺀 돈"에는 계좌에 남은 737 XBT가 빠져 있다.

**잔액 chain**: 행 단위 chain은 검사할 수 없다(행 순서 뒤섞임, 같은 12:00 배치가 배치 후 잔액을 공유, 지수표기 손실).
대신 "날짜 D까지 Completed 금액 누적합 = D의 어느 행에 적힌 잔액"을 검사했다:
1,380일 중 정확 일치 1,226일, 지수표기 반올림 범위 일치 152일, 불일치 2일(04-27·04-28, 04-27자 출금이 실제로는 04-28 정산 뒤 처리).

**원장과 wallet 대조 (회계 모델 검증)**: 복원한 fill 단위 실현손익(평균단가, `execcost` 기준) - `execcomm`(거래·funding·결제 합)을
BitMEX 일일 정산 창 **[D-1 12:00, D 12:00) UTC**로 묶어 wallet의 심볼별 RealisedPNL(`address` = 심볼)과 비교했다.

| 지표 | 값 |
|---|---|
| 비교 칸 (날짜 x 심볼) | 2,173 |
| 1 sat 이내 | 76.6% |
| 1,000 sat 이내 | 96.1% |
| 0.001 XBT 이내 | **99.0%** |
| 최대 칸 차이 | 0.042 XBT (2020-08-12/13 인접 두 칸이 +/-로 상쇄, 정산 창 경계 타이밍) |
| 전 기간 합계 | 복원 3,537.3218 / wallet 3,537.3237 XBT, **차이 -0.0019 XBT (5e-7)** |
| 마지막 정산 뒤 복원분 | 0.0196 XBT (2022-01-01 정산분, 파일 밖) |

정산 창 시각은 가정하지 않고 0~23시를 모두 시험해 12시에서만 맞는 것을 확인했다(1 sat 일치율 12시 76.6% vs 다른 시각 최대 61.5%).
12:00 정각 funding은 **다음 날** 정산분에 들어간다(반대로 두면 일치율 16.5%로 떨어짐).

## 11. Episode reconstruction 가능 여부

**가능. 검증 완료.**

방법 (`positions.reconstruct_symbol`): 심볼별로 시간순 fill을 누적해 부호 있는 포지션과 원가(XBt, BitMEX `execcost` 합)를 유지한다.
줄이는 fill은 원가를 `|닫는 수량| / |포지션|` 비율로 덜어 내고, 실현손익 = -(덜어 낸 원가 + 닫는 fill의 execcost 해당분)이다.
이 한 공식이 inverse(1/가격)·quanto·linear를 모두 처리한다. **Bybit USDT linear 공식은 쓰지 않았다.**

독립 검증 두 가지:
1. **funding 스냅샷**: funding 행은 8시간마다 `lastqty = |포지션|`, `homenotional` 부호 = 방향을 기록한다. 복원 포지션과 비교해
   무기한 9종 전체 **5,368 / 5,368 일치**(XBTUSD 3,961, ETHUSD 789, XRPUSD 293, LTCUSD 95, DOTUSDT 61, BCHUSD 51, LINKUSDT 49, DOGEUSDT 46, BNBUSDT 23).
2. **wallet 정산액**: 10절, 99.0% 칸이 0.001 XBT 이내.

행동 분류 (fill 단위): ADD_SHORT 321,766 / REDUCE_SHORT 310,822 / REDUCE_LONG 159,271 / ADD_LONG 145,730 / FUNDING 3,961(XBTUSD) /
REVERSE 1,761 / OPEN 829 / CLOSE 828 (XBTUSD 기준). 전 심볼 주문 단위: REDUCE_SHORT 6,513 / ADD_SHORT 5,721 / REDUCE_LONG 4,662 /
ADD_LONG 3,807 / REVERSE_TO_SHORT 643 / OPEN_SHORT 609 / REVERSE_TO_LONG 577 / OPEN_LONG 568 / CLOSE 344.

episode: 3,185개 (XBTUSD 2,590, ETHUSD 295, 기타 300). 종료 방식 REVERSE 2,001 / CLOSE 1,147 / **LIQUIDATION 28** / SETTLEMENT 8 / 미종료 1.
미종료 1개는 XBTUSD 숏 29,080,100 USD (2021-12-24 시작), 원장 종료 시점에 열려 있음.
원장 시작 시 포지션 0을 가정했다(계좌 첫 입금이 2018-03-05, 첫 fill 같은 날). funding 스냅샷 전부 일치가 이 가정을 뒷받침한다.

한계: 만기 선물(XBTU21, XBTH20 등 37종)은 funding이 없어 스냅샷 검증은 결제(Settlement) 8건과 wallet 대조로만 된다.

## 12. Holding time (preliminary)

episode 단위 (시작 fill ~ 포지션 0 또는 반전 fill), 종료된 3,184개.

| 구분 | n | p25 | 중앙값 | p75 | p90 | p99 | <1m | 1~5m | 5~30m | 30m~4h | 4h~24h | 24h+ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 전체 | 3,184 | 4.5m | 27.7m | 5.4h | 29.9h | 12.8d | 7.5% | 18.6% | 24.7% | 21.3% | 16.4% | 11.5% |
| LONG | 1,570 | 4.5m | 25.8m | 4.7h | 25.2h | 8.1d | 8.5% | 17.9% | 25.8% | 21.3% | 16.1% | 10.4% |
| SHORT | 1,614 | 4.5m | 31.8m | 6.3h | 35.9h | 15.2d | 6.6% | 19.3% | 23.5% | 21.3% | 16.7% | 12.6% |
| 2018 | 2,201 | 2.8m | 12.9m | 1.4h | 8.4h | 3.3d | 9.9% | 24.4% | 29.2% | 21.2% | 11.8% | 3.7% |
| 2019 | 464 | 23.9m | 3.2h | 16.9h | 2.3d | 11.1d | 2.6% | 8.2% | 18.5% | 23.9% | 26.7% | 20.0% |
| 2020 | 303 | 37.3m | 6.2h | 31.9h | 4.0d | 16.4d | 2.6% | 5.3% | 15.2% | 21.1% | 26.7% | 29.0% |
| 2021 | 216 | 4.1h | **20.4h** | 3.7d | 8.0d | 50.7d | 1.4% | 1.4% | 5.1% | 17.1% | 26.9% | 48.1% |

**2018년과 2021년은 다른 트레이더처럼 보인다.** episode 중앙 보유시간이 12.9분에서 20.4시간으로 약 95배 길어졌고,
주문당 fill 중앙값은 7에서 107로 늘었다. episode 수는 2,201에서 216으로 줄었는데 연간 실현손익은 188에서 2,069 XBT로 늘었다.
episode 순손익 승률(수수료·funding 차감) 전체 75.6%, 연도별 77.4% / 74.6% / 66.0% / 72.8%.

주의: episode는 "flat 또는 반전"으로 끊기 때문에, 2021년처럼 한 방향 포지션을 며칠씩 유지하며 안에서 분할 매매하면
episode 1개가 수백 주문을 담는다. 보유시간이 곧 신호 horizon은 아니다.

## 13. Scale-in / scale-out (preliminary)

주문 단위, 주문의 첫 fill 행동으로 분류해 episode에 배정.

| 지표 | 값 |
|---|---|
| episode당 ADD 주문 | 평균 3.0, 중앙값 1, p90 9, 최대 104 |
| episode당 부분 REDUCE 주문 | 평균 3.5, 중앙값 1, p90 9, 최대 127 |
| ADD가 1회 이상인 episode | 54.6% |
| 부분 청산이 1회 이상인 episode | 79.6% |
| episode 내 ADD 간격 중앙값 | 6.9분 (p25 2.3분, p75 24.5분) |
| XBTUSD ADD 크기 / 첫 진입 크기 | 중앙값 1.00 (p25 0.82, p75 1.00), 1,089 episode |

ADD 크기는 첫 진입과 같거나 작다. 마틴게일식 배수 증액의 흔적은 중앙값 수준에서는 보이지 않는다.

**평가손 상태 ADD 여부**: 체결가 기준(마지막 체결가, mark 아님)으로는 ADD 주문의 69.1%가 평균단가보다 불리한 가격에서 체결됐다(15절 CLAIM 2).
mark 기준 판정은 시장데이터가 필요하다. 이 숫자는 "체결 순간의 그 가격 기준"이라는 정의에서만 정확하다.

## 14. Leverage / risk reconstruction 가능 여부

**부분 가능: 무기한 계약에 한해 8시간 스냅샷으로.**

funding 행의 `execcost`는 funding 시점 가격(BitMEX는 funding 체결에 mark를 기록한다. 인접 fill 대비 중앙 16bp 차이로 이 해석과 맞지만 외부 데이터로 확정하지는 않았다)의 포지션 가치다. 따라서 스냅샷마다:

- 총 노출(XBT) = Σ |funding execcost| / 1e8
- 미실현손익 = 스냅샷 가치 - 복원 원가
- 자본 추정 = 직전 wallet 잔액 + 마지막 12:00 정산 뒤 복원 실현손익 + 미실현손익
- 실효 레버리지 = 총 노출 / 자본 추정

| 연도 | 스냅샷 (material) | 레버리지 중앙값 | p90 | 최대 | 총노출 중앙값 (XBT, 먼지 포함 전 스냅샷) |
|---|---|---|---|---|---|
| 2018 | 82 | 2.75 | 22.2 | 762 | 0.06 |
| 2019 | 379 | 1.01 | 2.12 | 4.57 | 0.11 |
| 2020 | 641 | 1.23 | 2.22 | 4.36 | 442 |
| 2021 | 660 | 1.30 | 2.59 | 3.51 | 850 |

material = 레버리지 0.05 이상(먼지 포지션 스냅샷 931개 제외). 만기 선물이 열려 있던 스냅샷 1,270개는 mark가 없어 제외.
**2019년 이후 실효 레버리지는 중앙 1~1.3배, p90 2~2.6배, 최대 4.6배다.** 2018년 최대 762배는 2018-09-21 붕괴 직후 자본이 거의 0이 된 순간이다.

한계: (1) 만기 선물 노출 미포함 (2) 입출금 시각이 손상돼 해당 날짜 00:00으로 둠 (3) 8시간 사이의 최대 노출은 모름.
연속 레버리지·드로다운 후 크기 변화의 정밀 분석은 1분 mark 데이터가 필요하다(E1).

## 15. External claim audit table

상세 정의·계산은 `AOA_CLAIM_AUDIT_V1.md`. 외부 값은 코드에 넣지 않았고, 원장에서 먼저 계산한 뒤 비교했다.

| # | 주장 | 원장 재계산 | 판정 |
|---|---|---|---|
| B1 | 144만 행 | 1,444,583 행 | **VERIFIED** |
| B2 | 입금 14.49 BTC | 14.48925714 XBT | **VERIFIED** |
| B3 | 출금 2,814 BTC | 2,814.54321713 XBT (Completed. Canceled 포함 시 2,832.53) | **VERIFIED** |
| 1 | 시장가 진입 62~76%가 직전 60분 역방향 | 직전 60분 시장수익률 필요. 정의 문제: Market 진입 289건 vs taker 진입 3,530건 | INSUFFICIENT_DATA |
| 2 | 늘린 주문의 ~70%가 평가손 상태 | 체결가 기준 69.1% (XBTUSD 68.7%, 9,528 ADD 주문). mark 기준은 미검증 | PARTIALLY_VERIFIED |
| 3 | 진입 10,862건, 4h 뒤 +0.28%, hit 56% | 진입 주문(OPEN+ADD) 10,705건 (-1.4%, 정확 일치 아님). 4h 수익률은 시장데이터 필요 | INSUFFICIENT_DATA |
| 4 | 추가진입 4h 뒤 +0.19~0.66%, hit 55~61% | 시장데이터 필요 | INSUFFICIENT_DATA |
| 5 | 손실 청산 중 stop 비율 2021 0.0%, 2018 ~2.9% | 2021 **0 / 741 = 0.0%**. 2018 stop만 1.46%, **stop+청산 2.87%** | VERIFIED (2018은 청산 포함 정의일 때) |
| 6 | 보유시간의 ~57%가 평가손 | 8h funding 스냅샷 표본 50.9% (2021 61.2%). 57%와 다름, 연속 측정은 시장데이터 필요 | INSUFFICIENT_DATA |
| 7 | 큰 손실 후 6h 진입 크기 baseline의 0.92~1.01배 | 손실 하위 10/5/1% 모두 비율 중앙값 1.00, 평균 0.82~0.86. "큰 손실"·baseline 정의 미상 | PARTIALLY_VERIFIED |
| 8 | 0시 기준 평균 net position 대체로 short | 00:00 UTC: 숏 60.1%일, 평균 -314만 USD. \|pos\| 10만 USD 이상인 날 중 숏 67.6%. KST 0시도 동일. 2018년만 숏 39% | **VERIFIED** (2019~2021) |
| 9 | 고변동성 구간 성과가 더 높음 | 변동성 분위에 시장데이터 필요 | INSUFFICIENT_DATA |
| 10 | 2021-05-19 등 주요 drawdown | wallet 최악일 1위 = 2021-05-20 정산 -281.8 XBT(5/19 붕괴분), 5위 = 05-06 -109.4 XBT(5/05분). 2018-09-21은 자본 18.2→1.5 XBT(-92%, 스냅샷 기준) | PARTIALLY_VERIFIED |

집계: VERIFIED 5 (B1·B2·B3·5·8) / PARTIALLY_VERIFIED 3 (2·7·10) / INSUFFICIENT_DATA 5 (1·3·4·6·9) / NOT_REPRODUCIBLE 0.
원장만으로 계산 가능한 주장은 모두 외부 값과 같은 방향·비슷한 크기로 나왔다. 틀렸다고 판정한 주장은 없다.

## 16. Major drawdown reconstruction 가능 여부

**원장 범위에서 가능. 미실현 경로는 시장데이터 필요.** `aoa_reports/drawdown_case_studies.json`.

| 날짜 (UTC) | 원장이 보여 주는 것 |
|---|---|
| 2018-09-21 | XRPU18(만기 선물)에서 1,129 fill, 반전 25회, **청산 3건**, 실현 -13.65 XBT. ETHUSD 816 fill(롱 12만~숏 30만 계약 왕복). 스냅샷 자본 18.2(09-21 12:00) → 1.5 XBT(20:00). 09-22 정산 후 잔액 2.93 XBT. 계좌 전체가 사실상 리셋된 사건 |
| 2021-05-05 | BCHUSD 숏 20만 계약을 2.3만으로 줄이며 실현 -63.7 XBT, ETHUSD 숏 청산 -40.7 XBT, XBTUSD 숏 3,340만 USD를 1,830만으로 축소(+12.3 XBT). 스냅샷 미실현 -78 → -186 XBT, 자본 639 → 520 XBT, 레버리지 1.5~2.7배 |
| 2021-05-19 | XBTUSD 롱 최대 4,070만 USD ↔ 숏 최대 3,047만 USD, **반전 6회**, ADD fill 3,998, 실현 -121.4 XBT. ETHUSD 5,020 ADD fill, 실현 -147.4 XBT. 스냅샷 자본 636(04:00) → 531(12:00) → 304 XBT(20:00), 하루 약 -52%. 청산 0건 |

2018-09-21은 절대액(-15.7 XBT, 순위 74)보다 **비율**로 가장 큰 손실이었다. 2021-05-19는 절대액 1위다.
어느 경우에도 원장상 stop 주문은 없고, 2021년 두 사건은 청산 없이 수동 청산·반전으로 처리됐다.
사건 안의 "평가손이 얼마까지 갔는가"는 8시간 스냅샷 사이를 알 수 없어서, 1분 mark 경로가 있어야 완결된다. E1 case study로 넘긴다.

## 17. 추가로 필요한 market data

원장 체결가로 시장수익률을 근사하지 않았다. 필요한 것과 확인된 소스:

| 데이터 | 용도 | 기간 | 확인된 소스 |
|---|---|---|---|
| XBTUSD·ETHUSD 체결 (tick) → 1m OHLCV | CLAIM 1(직전 60분), 3·4(4h forward), 9(변동성 분위), 6(연속 평가손) | 2018-03-01 ~ 2022-01-01 | `public.bitmex.com/data/trade/YYYYMMDD.csv.gz` 일별 전 심볼. 2018-03-01·2021-12-31 존재 확인, 하루 약 9~20MB |
| XBTUSD·ETHUSD 호가 (best bid/ask) | ADD·진입 시점 spread, maker 체결 조건 | 같은 기간 | `public.bitmex.com/data/quote/YYYYMMDD.csv.gz`. 2018-03 존재 확인, 하루 약 12MB |
| mark price 또는 .BXBT·.BETH index | CLAIM 2 mark 기준, 미실현·레버리지 연속 경로 | 같은 기간 | **E1에서 소스 확인 필요** (공개 덤프에 mark가 없을 수 있음. 없으면 funding 스냅샷 mark로 8h 보간만 가능) |
| funding rate 이력 | 비용 분해 (원장 funding 행으로 이미 계좌 수취액은 있음) | 같은 기간 | 원장 `commission` 열(funding 행)이 계좌 기준 요율 |

규모 추정: trade 약 1,400일 x 약 15MB = 약 21GB gz, quote도 비슷한 규모. 심볼 필터 후 1m로 줄이면 수백 MB.
전부 E1 범위이고, E0에서는 받지 않았다. PIT 정렬 규칙은 `alignment.py`에 미리 고정하고 테스트했다(20절).

## 18. US-B CRYPTO에 활용 가능한 연구 질문

전제: 이 원장은 **성공한 계좌 하나**다. 실패한 계좌는 관찰할 수 없다(생존편향). 소유자 신원은 확인되지 않았다.
2018~2021 BitMEX는 maker 리베이트(-0.025%, 2021년 일부 -0.01%)가 있던 시장이고, 2026년 US-B의 Bybit VIP_0에는 리베이트가 없다.
그래서 **"이 사람처럼 하자"는 결론은 E0에서 나오지 않고, 나와서도 안 된다.** 물타기·무손절이 좋다는 해석도 금지한다(1명의 표본, 생존편향).

그래도 원장이 답할 수 있는 US-B 관련 질문:

1. **실행 비용 구조 (가장 직접적)**: D5·D5.1은 edge가 taker 비용(왕복 약 11bp)에 죽었다. 이 계좌는 거래가치의 71%를 maker로 체결해
   순비용을 편도 0.44bp로 낮췄다. 질문: "리베이트가 없는 거래소에서 maker 비중 70~80%를 만들면 비용이 얼마인가, 그리고 그 체결이 역선택을 얼마나 당하는가."
   원장 + quote 데이터로 **maker fill 직후 가격이 불리하게 움직였는가**(adverse selection)를 잴 수 있다.
2. **포지션 운용 방식**: 신호 하나에 진입·청산하는 구조(D5)가 아니라, 한 방향 포지션을 유지하며 그 안에서 크기를 계속 조절하는 구조다.
   2021년 episode 중앙 20시간, 부분 청산 79.6%. "크기 조절 자체의 가치"를 신호 수익과 분리해 측정할 수 있다.
3. **2018 → 2021 변화**: 짧은 스캘핑(12.9분)에서 긴 보유(20.4시간)로 옮기며 연 손익이 11배가 됐다. 어느 행동 변화가 손익 변화와 같이 움직였는지는 기술 통계로 볼 수 있다(인과 아님).
4. **net short 성향과 funding**: 순 funding 149.6 XBT 수취. 숏 성향이 funding 수취 전략의 일부였는지, 가격 방향 판단이었는지 분리 가능.
5. **드로다운 대응**: 청산 28 episode 중 26개가 2018년. 이후 청산 2건. 2018-09 리셋 뒤 무엇이 바뀌었는지(레버리지 상한, stop 비율 0%로 감소, 보유 연장).

## 19. Next step

**E1 권고 (조건부).** 범위와 순서는 `AOA_RESEARCH_PLAN_V1.md`.

- E1-A (시장데이터 수집): BitMEX trade·quote 공개 덤프 XBTUSD·ETHUSD 2018-03 ~ 2021-12, mark/index 소스 확인.
- E1-B (claim 재현): CLAIM 1·3·4·6·9를 사전 정의로 재계산. 외부 정의를 모르므로 정의 후보를 먼저 동결하고 계산.
- E1-C (US-B 질문): 18절 1번 maker 역선택 측정을 최우선.

STOP이 맞는 경우: US-B가 maker 실행을 검토할 계획이 없다면, 이 원장이 US-B에 줄 수 있는 것은 "비용 구조의 차이"라는 사실 하나이고,
그것은 E0로 이미 확보됐다. 이 판단은 사용자 몫이다.

## 20. Tests

`backend/tests/crypto/test_expert_execution_e0.py` **15 passed** (0.4초, 합성 데이터만 사용, 원장 파일 불필요).

| 영역 | 테스트 |
|---|---|
| timestamp parse | 소수점 1~6자리, wallet 손상값 `57:26.3`→NaT, 빈 값 |
| side normalization | Buy/Sell/'' 매핑, 미지 값 거부 |
| raw read | 줄바꿈 셀, 빈 문자열 보존 |
| qty/price conversion, duplicate | 정수·실수 변환, 빈 값→NaN, 중복 execID 검출 가능 |
| zip safety | traversal·압축폭탄·실행파일 거부 |
| inverse accounting | 닫힌식과 일치, 숏 이익·비대칭, quanto 선형, 원장 첫 행 부호 규약 |
| contract inference | inverse/quanto 판정, multiplier, 무기한/만기 구분 |
| position sign transition | 10개 행동 분류 |
| flat / reverse | OPEN→ADD→REVERSE→CLOSE, 조화평균 평단, 실현손익, episode 분리 |
| partial reduce | 평균단가 유지 |
| funding snapshot | funding이 포지션을 안 바꿈, 스냅샷 일치·불일치 검출 |
| liquidation | 청산으로 episode 종료 |
| no-lookahead | 이벤트가 속한 봉을 보지 않음, forward는 이벤트 이후 봉부터 |
| isolation | crypto 엔진 어디도 `expert_execution`을 참조하지 않음, 패키지는 `app.*`를 import하지 않음 |

isolation 추가 확인: `grep -rn expert_execution backend/app` 결과는 패키지 자신뿐. 패키지 import는 pandas·numpy·pyarrow와 패키지 내부 상대 import뿐이다.

## 21. Git

- commit / push: **0** (요청대로).
- 새 파일 (전부 untracked): `backend/app/crypto/research/expert_execution/` 6개 (`__init__`, `ingest`, `contracts`, `positions`, `alignment`, `e0`),
  `backend/tests/crypto/test_expert_execution_e0.py`, `docs/crypto/expert_execution/` 4개, `data/research/expert_execution/` 산출물.
- 기존 tracked 파일 수정: 0 (작업 전부터 있던 35개 수정은 이번 작업과 무관).
- **`data/research/`는 루트 .gitignore 대상이 아니다.** 원본 602MB·parquet 156MB가 실수로 커밋되지 않도록
  `data/research/expert_execution/.gitignore`(aoa_raw/, aoa_normalized/)를 추가했다. JSON·CSV 보고서는 추적 가능 상태로 남겼다.
- 기존 D2·D5·D5.1·Paper·runtime tape 파일 변경: 0 (`data/runtime/crypto` 아래 이번 세션 이후 수정 파일 없음 확인).

성능·저장:

| 항목 | 값 |
|---|---|
| ZIP | 113.4MB |
| 해제 | 601.8MB |
| 정규화 parquet 합계 | 156.2MB (executions 121.5 / positions 32.6 / orders 1.6 / 기타 0.9) |
| 전체 파이프라인 | 95초 |
| peak RSS | 5.8GB (전체 load 방식. 9GB 머신에서 여유 약 3GB) |

peak RAM이 큰 이유는 문자열 원본과 정규화본을 동시에 들고 있기 때문이다. 재현성은 입력 sha256 고정 + 결정적 정렬(`seq`)로 확보했고,
chunk 경로는 E0 규모에서 필요 없어 만들지 않았다. E1에서 시장데이터를 붙일 때는 심볼별로 나눠 읽는 것을 권장한다.

## 22. Exit gate

| # | 조건 | 결과 |
|---|---|---|
| 1 | ZIP 안전 해제 | PASS (1절) |
| 2 | 모든 파일 inventory | PASS (`inventory.csv`, 6/6) |
| 3 | execution schema 확정 | PASS (41열, 6절·사전) |
| 4 | wallet schema 확정 | PASS (14열, 손상 3종 기록) |
| 5 | data quality 보고 | PASS (`quality_report.json`, 7절) |
| 6 | 주요 symbol·행 수 확정 | PASS (3·5절) |
| 7 | Maker/Taker 분포 | PASS (8절, 연도·행동별) |
| 8 | episode 복원 가능 여부 확정 | PASS (가능, 스냅샷 5,368/5,368) |
| 9 | 일부 외부 claim 독립 검증 | PASS (VERIFIED 5, PARTIALLY 3) |
| 10 | market data 필요 claim 명확 | PASS (17절) |
| 11 | 기존 production/research 데이터 무변경 | PASS |
| 12 | strategy implementation 0 | PASS |

**E0 = PASS.**
