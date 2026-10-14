# US-B CRYPTO D4 Operational Replay + Analytics 실행 보고 V1

작성 2026-09-23. **이 문서는 증거다.** Bybit PUBLIC API만 사용했고, 계정·API key·private
endpoint·실주문은 한 번도 쓰지 않았다. Edge/Score/Probability/AUTO 진입 로직은 0줄이다.
commit·push하지 않았다.

전제로 사용한 계약: D3의 accounting/execution contract를 그대로 유지한다
(`CRYPTO_PAPER_EXECUTION_CONTRACT_V1.md`, `CRYPTO_TRADING_STATE_MACHINE_V1.md`,
`CRYPTO_D3_PAPER_TERMINAL_REPORT_V1.md`).

## 0. Git

| 항목 | 값 |
|---|---|
| branch | `main` |
| D4 시작 시 HEAD | `a774047` |
| D4 종료 시 HEAD | `ad54939` |
| 작업 중 HEAD 이동 | **4회** (전부 다른 세션의 Strategy E / UI 작업) |
| commit / push | **하지 않음** |

D4가 만든 것은 전부 untracked다. 기존 dirty 파일과 Strategy A~E 코드는 건드리지 않았다.

> **같은 파일을 두 세션이 동시에 쓰고 있다.** 다른 세션이 D3.5(서버 상시배포 + UI V2)를
> 진행하면서 `paper/analytics.py`, `crypto-paper-terminal.tsx`, `crypto-paper.ts`,
> `crypto-paper/page.tsx`를 함께 고쳤고, `paper/sizing.py`·`terminal/server.py`를 새로 만들었다.
> 내 D4 컴포넌트는 살아남아 병합돼 있다(그쪽이 `Disclosure`로 감쌌다). 이 보고서를 쓰는 동안
> 파일을 고칠 때마다 현재 내용을 다시 읽고 최소 수정했다.

---

## A. Persistence / restart recovery

### 설계 판단: 스냅샷을 만들지 않았다

계좌 상태를 별도 파일로 저장하지 않는다. D3가 이미 증명했듯 **입력 테이프가 원장 바이트를
완전히 결정**하므로, 복구는 그 테이프를 다시 재생하는 것이다. 상태를 따로 한 벌 더 저장하면
진실이 둘이 되고, 둘이 어긋나는 순간 운영자는 어느 쪽이 거짓인지 알 방법이 없다.

`backend/app/crypto/paper/persistence.py`

| 상황 | 처리 |
|---|---|
| 정상 재시작 | 테이프 재생 → 재생 결과와 디스크 원장 **바이트 비교** → 일치하면 이어쓰기 |
| torn write | 줄바꿈 없이 끝난 **마지막 한 줄만** 잘라낸다. 레코드는 append+fsync 단위라 그 외의 줄은 찢어질 수 없다 |
| 원장이 뒤처짐 | 테이프 기록은 엔진 실행보다 먼저 fsync되므로 원장은 뒤처질 수는 있어도 앞설 수는 없다. 빠진 꼬리만 이어붙인다 |
| 그 외 불일치 | **거부한다**(`LEDGER_DIVERGENCE`). 두 파일이 같은 런의 것이 아니라는 뜻이므로 기워 맞추지 않는다 |
| 스키마 불일치 | **거부한다**(`LEDGER_SCHEMA_MISMATCH`). 다른 스키마로 재생하면 역사를 고쳐 쓰게 된다 |

복구되는 것: 계좌, 모드, 열린 포지션, 레버리지, 진입가, 실현/미실현 회계, 누적 수수료·펀딩,
원장 오프셋. 전부 파생값이며 따로 저장된 것이 아니다.

**멱등성**: 연속 3회 재시작해도 원장 바이트가 변하지 않는다(`ledger_tail_rewritten == 0`).

### 실측: 진짜 크래시에서 복구했다

시뮬레이션이 아니다. 14:09에 제어 세션이 종료되면서 터미널 프로세스가 **열린 포지션을 가진 채**
죽었다. 15:20에 다시 띄워 측정한 값이다.

```
restored                     True
tape_records                 1372
ledger_events                5
ledger_offset_bytes          3215
ledger_tail_rewritten_bytes  0
torn_writes_repaired         []
ledger_schema_version        crypto.paper.ledger.v2
복구된 포지션               LONG 0.002 @ 87141.80
복구된 수수료               0.0958559800
복구된 레버리지             10
복구 직후 mark / uPnL       86500.00 / -1.28360
복구된 청산가               78687.28805056687067322163138
```

죽기 직전 원장의 마지막 이벤트는 `POSITION_OPEN`이었고, 복구 후 포지션·평단·수수료·레버리지가
전부 그대로였다. 꼬리 재작성 0바이트는 **크래시 시점에 원장과 테이프가 이미 정합**했다는 뜻이다.

---

## B. Historical replay

### D2 테이프를 D3 엔진에 그대로 먹인다

`backend/app/crypto/paper/historical.py` + `replay.py`

만들어지는 레코드는 라이브 세션이 쓰는 것과 **형식이 같다.** 그래서 하위 계층에서 과거 레코드와
실시간 레코드를 구분할 수 없고, replay가 별도 백테스터가 아니라 **동일 execution/accounting
경로**를 탄다.

### 반드시 발명해야 했던 것 하나: 호가창

D1이 확정했듯 Bybit는 orderbook과 개별 체결을 **실시간으로만** 공개한다. 1분봉은 그 시점의
bid/ask가 무엇이었는지 말해 주지 않는다. 그래서 테이프는 가격 주위에 호가를 합성한다.

| 가정 | 값 | 근거 |
|---|---|---|
| spread | 0.10 (정확히 1 tick) | D2 관측 구간 전체에서 실측 spread가 1 tick이었다 |
| depth | 양쪽 각 5 BTC, 평평 | 임의값. 호가 소모가 일어나는 크기를 넘기지 않기 위한 값 |

**모든 레코드에 `book_synthetic: true`가 찍힌다.** 과거 구간의 체결은 측정값이 아니라 모델
출력이며, 어떤 독자도 이를 실측과 혼동할 수 없어야 한다. 화면에도 같은 문구가 나온다.

### 봉 하나당 레코드 3개

`low → high → close` 고정 순서다.

- 두 극단을 **모두 방문**해야 봉 안에서 일어난 청산을 감지할 수 있다.
- 순서를 고정해야 테이프가 결정적이다.
- 대가: 봉 안의 **경로는 모델**이다. 고점 뒤에 저점을 찍은 봉도 저점부터 재생된다. 포지션 하나에
  대해 청산을 일으킬 수 있는 극단은 하나뿐이므로 **감지 자체는 영향받지 않지만**, 봉 내부의
  미실현 손익 순서는 실제가 아니다.

mark 봉이 없는 거래 봉은 **평가할 수 없으므로 건너뛴다.** 추정하지 않는다.

### Deterministic clock, 3가지 속도

엔진 시각은 언제나 테이프에서 온다. 드라이버는 **wall clock 페이싱만** 결정한다.

| 모드 | 동작 |
|---|---|
| `STEP` | 호출당 N개 적용 |
| `ACCELERATED` | 최대 속도 |
| `REALTIME` | 테이프 시각 간격 / speed 만큼 sleep |

seek은 구현하지 않았다(요구사항상 선택).

### 실측: 2021-02-01 12시간 구간

연구 구간 V1(2021-02-01 이후) 안이다. CLI가 그 이전 시각을 거부한다.

```
테이프 2,169건 (market 2,163 + command 5 + START)
ACCELERATED 완주 0.03초
STEP        완주 (97개씩)
REALTIME    3600x, sleep 2,162회 합계 12.0초 (= 12시간 / 3600)
세 모드 원장 바이트 동일: True
```

같은 입력 + 같은 명령 = 같은 원장 바이트가 **속도와 무관하게** 성립한다.

집계 결과(수동 명령 4개, 전략 아님):

| 항목 | 값 |
|---|---|
| 거래 | 2건 (승 1 / 패 1) |
| 총손익 | +6.27140 USDT |
| 수수료 | 0.66295185 |
| 펀딩 | -0.0582613492608 (SHORT가 수취) |
| 순손익 | **+5.4828106992608** |
| PF | 1.874 |
| MDD | 6.4826 |
| 회계 대조 | 전 항목 일치 |

산출물: `data/runtime/crypto/paper/replays/d4-replay-20210201/`
(`input.jsonl`, `ledger.jsonl`, `performance.json`, `trades.json`)

---

## C. Analytics

`backend/app/crypto/paper/analytics.py`. **모든 수치는 원장에서 접어 낸다.** 엔진이 들고 있던
누계를 읽어 오지 않으며, 둘이 어긋나면 틀린 쪽은 이 모듈이다.

거래의 정의: **flat → flat 왕복 1회.** 분할 진입·분할 청산을 해도 한 거래이고, 개별 청산은
`exits`로 따로 센다.

| 요구 | 산출 |
|---|---|
| trades | 왕복 수 |
| win rate | 순손익 > 0 기준 |
| gross / net pnl | `net = gross - fees - funding` |
| fees / funding | 원장 FEE·FUNDING 합계 |
| avg win / loss | 순손익 기준 |
| expectancy | 거래당 평균 순손익 |
| PF | 이익합 / 손실합 절대값. 손실 0이면 **null**(0으로 꾸미지 않는다) |
| MDD | 거래별 종료 자산 곡선의 최대 낙폭, 금액과 최고점 대비 비율 |
| longest losing streak | 연속 패 |
| hold time | 중앙값과 누적 |
| MAE / MFE | **엔진이 포지션 보유 중 mark마다 갱신**해 `POSITION_CLOSE`에 실어 보낸다 |
| leverage별 집계 | `by_leverage`. `by_side`, `by_origin`도 같이 |
| MANUAL/AUTO 필드 | `origin`에 존재. D4는 AUTO를 **한 건도 만들지 않는다** |

### AUTO 버킷을 미리 만들어 두지 않는다

`origin` 필드는 스키마에 있고 D5가 채울 자리다. 그러나 AUTO 거래가 0건인 런에서
`by_origin`은 **AUTO 키를 만들지 않는다.** 없는 것을 0으로 채워 넣으면 "AUTO를 돌렸는데 성과가
0이었다"와 구분되지 않는다. 테스트로 고정했다.

### 찾아서 고친 결함 1건

진입 체결의 수수료는 `POSITION_OPEN`보다 **먼저** 기록된다. 초기 구현은 그 시점에 아직 "flat"
이라고 보고 수수료를 거래에 붙이지 않았고, 그 결과 거래별 순손익이 왕복 비용의 절반만큼
**조용히 과대계상**됐다. 계좌 지갑잔액과 대조하는 테스트가 이를 잡았다. 지금은 flat 구간의
수수료를 보류했다가 다음 `POSITION_OPEN`에 넘긴다.

---

## D. Fee schedule: 공식 출처 확보

**D3에서 `ASSUMED`였던 값이 D4에서 `OFFICIAL`이 됐다.** 값 자체는 바뀌지 않았다.

| 항목 | 값 |
|---|---|
| 출처 | `https://www.bybit.com/en/help-center/article/Trading-Fee-Structure` |
| HTTP | 200 (브라우저 UA, 0.77초) |
| 페이지 최종 갱신 | **2026-09-02 01:17:46** |
| HTML sha256 | `b9bb8db5d8c2443d90b4d38d87c769d8a0880fd2b4c692130d294c6756879d70` |
| VIP 0, Perpetual & Futures | **Taker 0.0550% / Maker 0.0200%** |
| 채택 | taker `0.00055`, maker `0.0002` |
| basis | **`OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED`** |
| version | `bybit-official-vip0-2026-09-02` |

D3에서 페이지 2곳이 60초 타임아웃이었던 이유는 요청 방식이었다. curl에 브라우저 User-Agent를
주면 200을 돌려준다. 7개 VIP 등급 표 전체를 추출해 보존했다:
`data/runtime/crypto/reference/fee_source_verification_v1.json` + 원본 HTML.

**남은 가정 1개**: 요율은 공식이지만 **이 프로젝트의 등급은 여전히 모른다.** 거래소 계정이 없어
자기 VIP 등급을 조회할 방법이 없다. 신규 계정의 기본값인 VIP 0을 가정했고, 그 사실이 basis
이름에 들어 있다.

### hardcoded fee를 silent default로 쓰지 않는다

`FeeSchedule`은 `version`/`source`/`effective_date`/`basis` 중 하나라도 비면 **생성되지
않는다.** `basis`는 허용 목록 밖의 값도 거부한다. run config 로더도 같은 필드를 필수로 요구하고,
없으면 API가 503과 함께 누락 필드명을 돌려준다. 화면 하단에도 그대로 노출된다.

---

## E. Operational test

런 `paper-d4-soak-001`, 2026-09-23. run config는 공식 수수료 출처로 만들어졌다.

### E.1 연속 운용

| 항목 | 값 |
|---|---|
| 논리 런 길이 | 13:43:41 ~ 16:44:18 KST = **180.6분** |
| 실제 피드 연결 | **110.1분 (1시간 50분)** |
| 30초 초과 공백 | **1건**, 70.6분 |
| MARKET 레코드 | 5,892건 |
| 명령 | 19건 (START 1 · ORDER 17 · SET_LEVERAGE 1) |
| 원장 | 70건 |

공백 1건의 정체는 **실제 크래시**다(A절). 제어 세션이 종료되면서 프로세스가 죽었고, 70.6분 뒤
다시 띄워 복구했다. 이 환경의 백그라운드 프로세스는 세션과 함께 죽으므로 soak이 분절된다.

**요구된 "최소 수시간"에는 미치지 못한다.** 연결 기준 1시간 50분이다. 이 항목만 PARTIAL이다.

### E.2 의도적 fault injection

`POST /api/crypto/fault/drop-feed`는 **실제 소켓을 닫는다.** 로컬 상태만 비우면 재구독·backoff·
뒤따르는 snapshot에 대해 아무것도 증명하지 못하기 때문이다. `CRYPTO_PAPER_FAULT_INJECTION=1`
없이는 404를 돌려준다.

| # | 상황 | 결과 |
|---|---|---|
| 1 | **SHORT 0.002 보유 중** | 재연결 1회, `book_ready` 복구, 포지션 `SHORT 0.002 @ 86473.00` 그대로 |
| 2 | 포지션 없음 | 재연결 2회 누적, book 재확보 |
| 3 | **LONG 0.003 보유 중** | 재연결 3회 누적, 포지션 `LONG 0.003 @ 86411.50` 그대로, uPnL 계속 갱신 |

3회 전부 book gap 0, malformed 0. 재연결 후 orderbook snapshot을 다시 받아 호가가 즉시 복구됐다.

### E.3 열린 포지션 상태에서의 재시작

2회 측정했다.

| # | 성격 | 공백 | 복구 결과 |
|---|---|---|---|
| 1 | **계획되지 않은 크래시** (열린 LONG 0.002) | 70.6분 | 테이프 1,372건 → 원장 5건, 꼬리 재작성 **0바이트**, 찢긴 기록 0 |
| 2 | 계획된 재시작 | 약 15초 | 테이프 1,941건 → 원장 26건, 꼬리 재작성 **0바이트**, 찢긴 기록 0 |

두 번 다 포지션·평단·수수료·레버리지·모드가 그대로였다. 꼬리 재작성 0바이트는 크래시 시점에
원장과 테이프가 이미 정합했다는 뜻이다.

### E.4 회계 무결성 (런 전체 전수)

```
replay 바이트 일치        True  (입력 5,911건 -> 원장 70건)
불변식 A6 / P5            PASS
원장 seq 조밀             True
중복 체결                 없음
체결 17 == 수수료 17      True
접수 17 == 체결 + 거부 17  True
원장 <-> 계좌 대조         gross / fees / funding / wallet 전부 일치
```

**중복 체결 0건.** `request_id`가 중복 체결된 사례가 없고, 한 체결당 수수료가 정확히 한 번
부과됐다. 재연결이 3회, 프로세스 재시작이 2회 있었는데도 그렇다.

### E.5 이 과정에서 찾아 고친 결함 1건

런 전체를 집계하다가 **열린 포지션이 있을 때 `ending_equity`와 `net_pnl`이 서로 다른 이야기를
한다**는 것을 발견했다.

- `net_pnl`은 모든 수수료를 센다 (4.8477)
- `ending_equity`는 **완결된 거래만** 걸어간 곡선의 끝이라, 아직 열려 있는 포지션의 진입
  수수료(4.1813)가 빠져 있었다
- 그래서 742.10을 보고했는데 계좌의 실제 지갑잔액은 737.92였다

원장과 어긋나는 집계는 집계가 틀린 것이라는 계약(P7) 위반이다. 수정: 완결 거래 곡선에서
**미귀속 비용을 빼서** `ending_equity`를 계산하고, `closed_trade_equity`·`open_position_fees`·
`has_open_position`을 따로 공개한다. `reconcile`에 `wallet_matches`를 추가해 세 합계가 각각
맞으면서도 자산선이 엉뚱한 데 놓이는 경우를 잡는다. 테스트 3개로 고정했다.

실제 런 재검증: `ending_equity 737.9200417626190476190476190 == 계좌 wallet_balance`.

### E.6 다운타임 뒤 펀딩 따라잡기: 두 번째 결함

다음 날 같은 런을 다시 띄웠더니 자산이 0.59 USDT 줄었다. 원인을 따라가 보니 **다운 20시간 동안
지나간 펀딩 정산 3건(09-23 08:00Z · 16:00Z · 09-24 00:00Z)이 복구 시 한꺼번에 부과**된 것이었다.

돈이 나간 것 자체는 맞다. 포지션은 그 정산들을 통과해 계속 열려 있었고, 계약 FN2가 그렇게 정한다.
**문제는 사용된 값이다.** 세 건 모두 *지금* 요율(0.0000266)과 *지금* 명목을 썼다. 과거 정산
시점의 실제 요율이 아니다. 그리고 그 사실이 금액 어디에도 남지 않았다.

`provider_grid_mismatch`가 2건에 True로 찍혀 있긴 했지만, 그건 다른 것을 재던 지표다.

수정: 직전 관측보다 한 주기 넘게 뒤처진 정산은 `catch_up: true`,
`rate_basis: "CURRENT_RATE_APPLIED_TO_PAST_SETTLEMENT"`, `observation_gap_ms`를 함께 기록하고
`funding_catch_ups` 카운터를 올린다. 금액은 여전히 부과하되 **추정임을 원장이 들고 다닌다.**

과거 정산의 실제 요율은 D2가 이미 수집해 두었다(`historical/funding/`). 그것을 읽어 정확히
정산하는 것은 D5 범위로 남긴다.

### E.7 세 번째 결함: 스키마를 바꾸고 버전을 올리지 않았다

E.6의 수정을 넣고 터미널을 다시 띄웠더니 복구가 거부됐다.

```
RecoveryRefused: the stored ledger (38644 bytes) is not a prefix of the ledger the tape
rebuilds (38927 bytes); the two files do not belong to the same run
```

가드가 작동한 것 자체는 맞다. **그러나 사유가 틀렸다.** 저장본과 재생본을 줄 단위로 대조해 보니
첫 불일치는 seq 71의 FUNDING 이벤트였고, 차이는 값이 아니라 **키 3개**였다:
`catch_up`, `observation_gap_ms`, `rate_basis`. E.6에서 내가 추가한 필드다.

즉 원장 이벤트 모양을 바꿔 놓고 `LEDGER_SCHEMA_VERSION`을 올리지 않았다. 그래서 스키마 검사가
통과해 버리고 그 다음 관문인 divergence 검사가 걸렸다. 운영자는 "파일이 손상됐다"는 메시지를
받고 있지도 않은 손상을 찾아 나섰을 것이다.

수정: `crypto.paper.ledger.v2` -> **`v3`**. 이제 같은 상황에서 정확한 사유가 나온다.

```
RecoveryRefused: run was written with ledger schema crypto.paper.ledger.v2, this build writes
crypto.paper.ledger.v3; replaying across a schema change would rewrite history
```

**교훈은 가드가 아니라 절차 쪽이다.** 원장 이벤트에 필드를 하나 더하는 것은 스키마 변경이며,
버전을 올리지 않으면 가드가 엉뚱한 이름으로 짖는다.

v2로 기록된 `paper-d4-soak-001`은 **그대로 보존한다.** v3 코드로는 이어받을 수 없고, 그것이
맞는 동작이다. 이후 라이브 확인은 새 런 `paper-d4-v3-001`에서 했다.

---

## F. UI

`/crypto-paper` 한 화면에 D3 화면 위로 3개가 추가됐다.

| 요구 | 구현 |
|---|---|
| current session performance | `PerformancePanel` 9개 셀(거래·승률·순손익·총손익·평균이익/손실·PF·MDD·보유시간·MAE/MFE) |
| cumulative net pnl | 순손익 USDT + 런 고정환율 KRW 환산 |
| fees / funding | 총손익 셀의 보조 줄 |
| win/loss | 거래 셀의 보조 줄 + 승률 |
| MDD | 금액과 최고점 대비 비율 |
| trade history | `TradeHistory` 표: 방향·레버리지·수량·진입·청산·보유·순손익·MAE/MFE·출처 |
| restored-state indicator | `SourceBanner`의 복구 배지. 이어받은 원장/입력 건수를 표시 |
| replay / live-paper 표시 | 같은 배너. `실시간 페이퍼` 배지 + "과거 구간 재생은 별도 화면이며 합성 호가를 씁니다" |

화면은 원장과 대조한 결과도 표시한다: **"엔진이 들고 있던 누계와 대조한 결과: 일치"**.
불일치면 그대로 "불일치"라고 쓴다. 숨기지 않는다.

torn write가 복구된 경우 별도 배지로 드러낸다.

기존 UI 구조를 그대로 재사용했다. **새 프레임워크·새 npm 의존성 0개.**

---

## G. 판정

| # | 요구 | 판정 | 근거 |
|---|---|---|---|
| 1 | Paper state persistence / restart recovery | **PASS** | 실제 크래시 1회 + 계획 재시작 1회에서 무손실 복구. 멱등 재시작 확인 |
| 2 | historical replay | **PASS** | D2 실데이터 12시간 구간, 3개 모드 원장 바이트 동일. 동일 execution/accounting 경로 |
| 3 | performance analytics | **PASS** | 요구 13개 지표 전부. 원장에서만 도출, 계좌와 대조 |
| 4 | fee schedule official verification | **PASS** | Bybit 공식 페이지 200, 최종 갱신 2026-09-02, VIP 0 Perpetual 0.055%/0.02%. sha256 보존 |
| 5 | long-running operational readiness | **PARTIAL** | fault·재시작·중복체결·불변식 전부 PASS. **연속 운용 시간만 1시간 50분으로 "수시간" 미달** |

## **D4 = PARTIAL**

5개 중 4개 PASS, 1개 PARTIAL이다. PARTIAL의 사유는 **단 하나, soak 지속 시간**이다.
기능적 결함이 아니라 이 개발 환경의 제약이다: 백그라운드 프로세스가 제어 세션과 함께 죽어
무인 장시간 운용을 이 자리에서 만들 수 없다.

요구사항 D는 "official source 미확보 시 D4 PARTIAL 처리 가능"을 명시했는데, 그 항목은
**확보했다.** PARTIAL은 E 때문이다.

### PASS로 바꾸는 방법

이미 다른 세션이 서버 상시 배포를 올려 두었다(`deploy/systemd/usb-crypto-paper.service`,
`docs/crypto/CRYPTO_SERVER_RUNBOOK_V1.md`). **그 서비스를 D4 코드로 올려 수 시간 무인 가동한 뒤
원장으로 E.4를 재실행하면 PASS 조건이 충족된다.** 이 보고서는 그 배포본의 코드 버전을 확인하지
못했으므로 그 가동 시간을 내 증거로 쓰지 않았다.

---

## H. 위험과 UNKNOWN

| # | 항목 | 상태 |
|---|---|---|
| D4-R1 | 거래소 계정 VIP 등급 | **UNKNOWN.** 요율은 공식, 등급은 VIP 0 가정 |
| D4-R2 | 과거 구간 호가창 | **합성.** spread 1 tick·depth 5 BTC 가정. 모든 레코드에 표시됨 |
| D4-R3 | 봉 내부 경로 | **모델.** low→high→close 고정. 청산 감지는 유효, 미실현 손익 순서는 실제가 아님 |
| D4-R4 | 과거 구간 개별 체결·호가 원본 | **영구 부재.** D1 확정. 지금부터 모으는 수밖에 없다 |
| D4-R5 | replay seek | **미구현.** 요구사항상 선택 |
| D4-R6 | 청산 계산의 티어 선택 명목 기준 | **가정**(D3-R2 유지) |
| D4-R7 | 청산 체결가 `MARK_AT_TRIGGER` | **가정**(D3-R3 유지) |
| D4-R8 | 이 환경의 백그라운드 프로세스는 세션과 함께 죽는다 | 확정. soak이 분절되는 원인이며, 그 덕에 A를 실측할 수 있었다 |
| D4-R9 | 다른 세션이 이 터미널의 서버 배포를 진행 중 | 확정. `deploy/systemd/usb-crypto-*.service`, `docs/crypto/CRYPTO_SERVER_RUNBOOK_V1.md`. **그 배포본이 D3 코드라면 persistence·analytics가 없다.** 동기화 필요 |
| D4-R10 | funding 요율은 tickers의 예상치 | 확정(D3-R9 유지) |
| D4-R11 | 피드 telemetry(재연결·주입 횟수)가 프로세스 메모리에만 있다 | 확정. 재시작하면 0으로 돌아간다. 누적치는 이 보고서의 실측값이 원본 |
| D4-R12 | 다운타임 뒤 펀딩 따라잡기가 **현재 요율**을 과거 정산에 적용한다 | 확정. 추정임을 원장에 표시하도록 수정. 정확한 정산은 D2 funding 파일을 읽어야 하며 D5 범위 |
| D4-R13 | 원장 스키마가 v2 -> v3로 올라갔다 | 확정. `paper-d4-soak-001`(v2)은 v3 코드로 재개 불가. 보존하되 이어받지 않는다 |
| D4-R14 | 배포 진입점이 `terminal/api.py`가 아니라 **`terminal/server.py`**다 | 확정. 다른 세션이 D3.5에서 추가한 `sizing`·`reset` 라우트가 거기 있다. `api:app`으로 띄우면 화면이 404를 만난다 |

---

## I. 테스트

D4가 추가한 테스트:

| 파일 | 개수 | 범위 |
|---|---|---|
| `test_paper_persistence.py` | 11 | 정상 복구·멱등 재시작·torn write·뒤처진 원장·divergence 거부·스키마 거부 |
| `test_paper_analytics.py` | 26 | 왕복 접기·분할 진입/청산·MAE/MFE·펀딩 귀속·PF/MDD/연패·버킷·**자산선 정합(E.5)** |
| `test_paper_replay.py` | 15 | 테이프 생성·합성 호가 표시·창 경계·mark 결측·3개 모드 동일성·실 D2 구간 |
| `test_paper_engine.py`에 추가 | 2 | **다운타임 뒤 펀딩 따라잡기 표시(E.6)** |

`backend/tests/crypto/` 전체는 **255개**다. D2·D3분과 다른 세션이 D3.5에서 추가한
sizing·reset·segment 테스트가 함께 들어 있다.

전체 회귀 (스키마 v3 반영 후):

```
4092 passed, 1 failed, 1 skipped, 16 warnings in 1230.75s (0:20:30)
```

**실패 1건은 crypto와 무관한 시간 의존 테스트다.**
`test_massive_year_feasibility.py::test_every_long_range_request_passes_the_basic_plan_limiter`이
rate limiter의 sleep 길이를 `12.0 * n` ± 1.0초로 검사하는데, 세 번째 값이 34.90초로 나와
36.0 ± 1을 0.099초 차이로 벗어났다.

근거 3가지로 내 변경 탓이 아니라고 판정한다:

1. **단독 실행하면 통과한다** (`1 passed in 2.83s`). 회귀를 돌리는 동안 같은 머신에서 paper
   터미널·Next dev 서버·Bybit 피드가 함께 돌고 있었다
2. 그 테스트는 crypto 모듈을 **하나도 import하지 않는다**
3. 검사식 자체가 실제 경과 시간에 의존한다(`abs=1.0` 허용오차)

직전 회귀(스키마 bump 전, 부하가 더 낮았을 때)는 **4039 passed / 1 skipped / 실패 0**이었다.
crypto 추가·수정으로 인한 회귀는 없다.

프런트: `frontend/components/crypto-paper.test.tsx` **51 passed**, `tsc --noEmit` 통과,
새 npm 의존성 0개. 실행 중이던 처리되지 않은 Promise rejection 1건도 고쳤다. 주문 액션을
실제로 실행하는 테스트에서 네트워크 거부가 잡히지 않고 있었고, vitest가 "false positive를
유발할 수 있다"고 경고하던 것이다.

## J. D5 제안 범위

D4 PASS 이후 **D5에서 처음으로 Edge Discovery를 시작한다.**

**선결 조건**

1. D4-R9 해소: 서버 배포본을 D4 코드로 올릴지 결정한다.
2. 연구 구간은 이미 고정돼 있다(2021-02-01 이후). D5는 그 안에서만 탐색한다.
3. in-sample / out-of-sample 분할 지점을 **탐색 전에** 정하고 사전등록한다.

**추가 선결 조건**

4. D4-R12 해소: 다운타임 뒤 펀딩을 D2 `historical/funding/`의 실제 요율로 정산한다.

**D5가 쓸 수 있는 것**

- 결정적 replay: 같은 규칙을 같은 구간에 돌리면 같은 원장이 나온다
- 원장 기반 집계: PF·MDD·expectancy·MAE/MFE가 이미 있다
- `origin` 필드: AUTO 거래를 MANUAL과 분리 집계할 자리가 비어 있다

**D5에서도 금지**

- 실주문·LIVE adapter·계정·API key
- 과거 구간 합성 호가로 나온 성과를 실측처럼 보고하는 것
- PASS threshold 숫자를 결과를 본 뒤에 정하는 것
