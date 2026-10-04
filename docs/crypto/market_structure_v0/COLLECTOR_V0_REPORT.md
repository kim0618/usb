# BTC Market Structure V0 Collector Report

작성 2026-10-04. 계약 `DATA_CONTRACT_V0.md`
(sha256 `9eed3862860f2a8004e0f6ffc4593841b55d8c303e261ebd6e9e8fdde9b6d52f`, 동결 2026-10-03, 버전 `btc-ms.v0.1`).
**계약은 선행 세션이 동결한 것을 그대로 구현했고, 동결 후 계약 변경 0회다.**

**판정: READY FOR 24H TRIAL.** 코드·테스트 완료, 로컬 라이브 검증 완료, 배포·커밋·push 0.
전략 0 / Score 0 / LONG·SHORT 판정 0 / 자동매매 연결 0 / Production UI 변경 0 / 기존 파일 수정 0.

| 항목 | 값 |
|---|---|
| branch | `main` (HEAD `848a033`, **커밋하지 않음**) |
| 신규 소스 | `backend/app/crypto/market_structure_v0/` 11개 파일 |
| 신규 테스트 | `backend/tests/crypto/test_ms_v0_*.py` 7개 + `ms_v0_fixtures.py` |
| 기존 파일 수정 | **0개** (git status에 `M`로 뜬 21개는 전부 동시 세션 것) |
| 테스트 | ms_v0 **163 passed**, crypto 전체 통과 |
| 라이브 검증 | 600초 런 2회 + 강제종료/재시작 1회 + 20초·3초 런 |

---

## 0. 실측이 바꾼 설계 결정 4가지

이 단계에서 가장 값어치 있는 산출물은 코드가 아니라 **그냥 믿었으면 조용히 틀렸을 네 가지를 실측으로
확정한 것**이다. 네 개 모두 에러를 내지 않고 틀린다.

### 0.1 두 WS 베이스는 교환 불가이고, 틀린 쪽은 조용히 실패한다

2026-10-04 실측. 핸드셰이크·ping 모두 정상인데 프레임이 0이다.

| 스트림 | `/public/ws` | `/market/ws` |
|---|---|---|
| `btcusdt@depth@100ms` | **115 프레임 / 12초** | **0 프레임** |
| `btcusdt@aggTrade` | **0 프레임** | **수신됨** |

계약이 적은 베이스 배정이 맞다. `app/crypto/live/endpoints.py`에 기록된 user data stream 베이스
분리(2026-04-23)와 같은 함정이며, 베이스를 섞으면 "조용한 시장"처럼 보인다.

### 0.2 `U == pu + 1`은 거짓이다

depth 881프레임 실측에서 `U - pu`는 51·55·66·80·119 등으로 분포했다. 즉 **연속성 규칙은
`pu == 직전 u` 단 하나뿐**이고, 산술 관계를 단정하면 초당 몇 번씩 gap을 선언하며 영원히 resync한다.
Bybit의 `u == 직전 u + 1`(`app/crypto/orderbook.py`)을 복사하면 이 실패에 빠지므로 복사하지 않았다.

또한 **첫 delta 규칙은 `U <= lastUpdateId <= u`**이고 spot의 `lastUpdateId + 1` 형태가 아니다.
spot 규칙을 쓰면 적용해야 할 프레임을 버리고, **북이 한 이벤트 뒤처진 상태로 gap 보고 없이 시작한다.**

### 0.3 `limit=1000` 스냅샷은 mid 대비 ±0.15%밖에 못 덮는다

2026-10-04 REST 실측: 42,088바이트, bids 1000·asks 1000, mid 84,750.75,
**바깥 경계 -0.1507% / +0.1499%** (weight 20).

따라서 **±1% 밴드는 구조적으로 COMPLETE가 될 수 없다.** 라이브 런에서 확인한 그대로다.

| 밴드 | coverage | 비고 |
|---|---|---|
| ±0.1% | **COMPLETE** | 정식 qty/notional/imbalance 산출 |
| ±0.25% | PARTIAL | canonical null, 관측 하한만 |
| ±0.5% | PARTIAL | canonical null, 관측 하한만 |
| ±1% | PARTIAL | canonical null, 관측 하한만 |

이것이 "coverage 부족에 0 처리 금지"가 실제로 작동하는 지점이다. 0을 썼으면 **유동성이 얇은 것과
관측하지 못한 것이 같은 숫자**가 되어 데이터셋 전체가 조용히 오염된다. K절의 V1 결정 1번이 이것이다.

### 0.4 aggTrade `a`는 실측 63/63에서 정확히 +1씩 증가한다

98초 실측, 단조증가, 증분 분포 `{1: 63}`. 따라서 ID 점프를 "프레임 유실"로 보는 계약 규칙이 실측으로
지지된다. 추측으로 이 규칙을 넣으면 **flow coverage가 영구 PARTIAL**이 될 수 있었기에 측정했다.
부수 실측: aggTrade는 64프레임에 개별 체결 117건(한 프레임에 19건까지)이므로 **프레임 수는 거래
건수가 아니다.** `E - T`는 거래 약 150ms, depth 1~4ms.

---

## A. Data Contract

선행 세션이 `DATA_CONTRACT_V0.md`를 동결하고 `.sha256` 사이드카를 남겼다. 이번 세션은 **계약을
수정하지 않고** 구현했고, 해시를 재계산해 사이드카와 일치함을 확인했다(일치).

계약 동일성은 코드에 배선되어 있다. `contract.contract_identity()`가 문서를 다시 읽어 해시를
계산하고 사이드카와의 일치 여부를 돌려주며, **모든 세션의 첫 레코드(`session` START)가 이 값을
담는다.** 따라서 데이터셋은 자기가 어느 계약으로 만들어졌는지 스스로 말한다. 문서가 없는 배포에서도
돌 수 있도록 부재는 예외가 아니라 `status: MISSING`으로 기록된다.
`test_ms_v0_isolation.py`가 (a) 사이드카 일치, (b) 계약 문서의 문장과 코드 상수의 일치를 검사하므로
**계약을 몰래 고치면 테스트가 깨진다.**

## B. Collector Architecture

핵심은 **동기 상태기계와 비동기 러너의 분리**다.

- `Collector`: 연속성·coverage·freshness·후보 생애·무엇을 쓸지를 전부 결정한다. 소켓도, 자체
  시계도, `await`도 없다.
- `Runner`: 바이트와 시간을 상태기계에 넣는 일만 한다. 소켓·큐·시계를 소유한다.

이 분리 덕분에 gap·reconnect·stale·중복거래·찢어진 파일이 **네트워크 없이, 잠들지 않고, 전속도로**
테스트된다. 틀리기 쉬운 두 순서도 이 구조가 고정한다.

1. **버퍼링이 스냅샷 요청보다 먼저 시작된다.** 연결 → 프레임 버퍼 시작 → 그 다음 REST 요청. 요청을
   먼저 보내고 나중에 구독하면 둘 사이에 관측 불가능한 구멍이 생긴다.
2. **북의 소유자는 단 하나의 태스크다.** 프레임과 스냅샷이 같은 bounded 큐로 들어오므로, 스냅샷은
   프레임 열에서 정해진 지점에 적용된다. 스냅샷을 "그냥 또 하나의 큐 아이템"으로 만든 이유가 이것이다.

파일 11개(각각 한 가지 책임):
`__init__`(버전) · `contract`(계약 상수+해시) · `envelope`(레코드 외피·seq·decimal 규율) ·
`safety`(공개 URL 화이트리스트+거부목록) · `book`(Binance futures 로컬북) · `bands`(밴드 depth와
coverage) · `trades`(aggTrade 정규화·중복) · `flow`(5/15/60초 윈도) · `walls`(후보 생애) ·
`store`(버퍼·로테이션·락·복구·측정) · `collector`(상태기계 + 러너 + CLI).

## C. Binance Sync

`app/crypto/orderbook.py`(Bybit)를 복사하지 않고 새로 썼다. 규칙:

1. `u < snapshot.lastUpdateId` 프레임은 **버린다**(REST 왕복 중 도착한 정상 상황, 결함 아님).
2. 첫 delta는 **`U <= lastUpdateId <= u`**를 만족해야 한다(0.2절).
3. 이후는 **`pu == 직전 u`만** 검사한다(0.2절).
4. `pu`가 맞는데 `u`가 전진하지 않으면 자기모순이므로 gap, `pu`가 안 맞고 `u`도 전진하지 않으면
   재전송이므로 중복 폐기, `pu`가 안 맞고 `u`는 전진하면 유실이므로 gap, `pu` 부재는 연속성을 증명할
   수 없으므로 gap. 네 분기 전부 도달 가능하며 각각 테스트가 있다.
5. 절대 수량이 레벨을 대체하고 0은 삭제한다(보유하지 않던 레벨의 0도 조용히 수용).
6. **알려진 구간(known interval)**: 스냅샷의 최외곽 bid/ask가 이 북이 지식을 주장하는 범위다. 그
   밖의 레벨은 보관하지 않는다. 구간 밖에서 레벨이 변하는 것을 본다고 해서 그 영역을 다 안다는 증거가
   되지 않기 때문이다. mid가 구간을 벗어나면 사용 가능한 기준가가 아니다.
7. gap·overflow·stale·교차북·reconnect는 북을 무효화하고 wall 관측을 끊고 새 스냅샷을 받는다.
   **gap을 가로질러 이어붙이지 않는다**(레벨·경계·id를 전부 버린다).

레벨 상한 20,000, 교차북(`best_bid >= best_ask`) 무효화, 빈/불량 스냅샷 거부도 들어있다.
스냅샷 레코드는 요청시각·수신시각·왕복시간·버퍼된 프레임 수·원문을 함께 저장하고, 동기화 성공 시
감사 가속용 `checkpoint`(레벨 전체·경계·마지막 `u`·소스 이벤트시각)를 한 줄 남긴다.

## D. Trade Flow

- 정규화: `trade_id = a`, `first/last_trade_id = f/l`, `individual_fills = l - f + 1`,
  `aggressor = m ? SELL : BUY`.
- **`m=true`는 매수자가 maker이므로 공격적 SELL이다.** 이 불리언 하나를 뒤집으면 데이터셋의 모든
  imbalance가 뒤집히고 그 사실이 드러나지 않으므로, 양방향 프레임으로 테스트를 박았다.
- 중복: 프로세스 단위 high-water `a`. **재접속해도 유지된다**(계약이 프로세스 단위로 정한다).
- 반면 **freshness 시계는 연결 단위로 리셋한다.** 재접속 직후에 이전 연결의 마지막 거래 나이로
  stale을 판정하면 두 연결을 섞는 판정이 되기 때문이다. 이건 구현 중 테스트가 잡아낸 실수다.
- ID 점프·재접속·stale·거래소시각 역행은 **거래량을 버리지 않고 coverage만 강등**한다. V0는 점프를
  backfill하지 않는다.

## E. Derived Metrics

초당 1회 샘플. `derived` 레코드는 `book`(state·generation·mid·best bid/ask·source_u·
known 경계·age_ms·lag_ms·lag_state·levels·마지막 무효화 사유·밴드 4개)과 `flow`(윈도 3개)를 담는다.

coverage 3값은 밴드의 **각 사이드마다 독립**이다.

- `COMPLETE`: 동기화+신선+해당 사이드 밴드 전체가 알려진 구간 안. canonical `qty`/`notional` 유효.
- `PARTIAL`: 동기화+신선이지만 밴드가 스냅샷 경계를 넘음. **canonical은 null**,
  `observed_qty`/`observed_notional`을 하한으로 제공하고 `observed_is_lower_bound: true`로 표시.
- `UNKNOWN`: 비동기화·stale·유효 mid 없음. 전부 null.

imbalance는 **양쪽 사이드가 모두 COMPLETE일 때만** 계산한다. 한쪽만 완전한 상태에서 만든 비율은
imbalance가 아니라 스냅샷이 끝난 위치의 인공물이다. 분모 0은 0이 아니라 null이다.
BTC와 USDT를 따로 저장한다. 비율은 10자리로 quantize(나눗셈이 비정확하므로 자리수를 코드에 고정).

flow 윈도는 `(now - window, now]` 반개구간, **로컬 monotonic 수신시각** 기준이다. 수신시각 기준은
"그 시점에 이 수집기가 무엇을 보고 있었나"를 답하며, 그건 나중에 재구성할 수 없는 유일한 값이다.
거래소시각 재구성은 raw `E`/`T`가 남아 있으므로 오프라인에서 언제든 가능하다.
윈도가 COMPLETE가 되려면 연속 coverage가 윈도 길이만큼 거슬러 올라가야 하므로, warmup은
`max(windows) = 60초`이고 재시작은 진짜로 처음부터 시작한다(5초 윈도는 5초 뒤, 60초 윈도는 60초 뒤).
**COMPLETE이면서 거래 0인 윈도는 0을 보고하되 정규화 imbalance는 null이다**(관측된 0과 분모 0의 구분).

실측상 유의할 점: 거래 stale 임계 5초는 **조용한 시장에서 정상적으로 발동한다.** 첫 측정 구간의
거래율은 0.65건/초였고, 그 분포에서 5초 이상 공백은 흔하다. 600초 런에서 거래 stale 1회가 관측됐고
(그 구간 flow가 UNKNOWN으로 강등), 같은 런에서 depth는 stale 0회였다. 계약이 정한 값이므로 그대로
구현했고, **보수적으로 틀리는 방향**이지만 COMPLETE flow 비율을 떨어뜨린다는 점을 K절에 올린다.

## F. Wall Candidate

후보만 추적하고 **해석하지 않는다.** spoofing·absorption·iceberg 어휘는 코드에 존재하지 않으며
(`test_v0_names_no_spoofing_or_absorption_verdict`가 코드 본문을 검사), 그 이유는 데이터가 그 구분을
지탱하지 못하기 때문이다. 이 수집기는 집계된 depth를 보지 주문을 보지 않으므로, 줄어든 레벨이 취소인지
체결인지 depth 스트림만으로는 말할 수 없다.

규칙(계약 그대로): 비교집합은 가격순으로 **양쪽 각 최대 5개의 "점유된" 레벨**이고 자기 자신은 제외한다
(빈 가격은 이웃이 아니다. 띄엄띄엄한 북에서 이웃은 가장 가까운 호가 5개이지 가장 가까운 틱 5개가 아니다).
이웃 3개 미만이면 후보 없음(이웃 1개로 "지역 평균의 3배"는 표본의 절반일 뿐이다).
임계는 `qty >= 3 x mean(이웃)`. 탐색 범위는 mid ±1% 양쪽. bin은 정확한 가격.

`persistence_ms`는 **샘플된 관측 구간**이고 주문 동일성의 증거가 아니다(`order_identity_proven: false`,
`persistence_is_sampled_span: true`를 레코드가 직접 들고 있다). 두 샘플 사이에 사라졌다 같은 가격·같은
크기로 돌아온 레벨은 한 번도 떠나지 않은 레벨과 구별되지 않는다. resync나 freshness 상실은 `ENDED`가
아니라 **`UNKNOWN`으로 끊는다**(관측되지 않은 종료는 종료가 아니다). 후보는 북 `generation`을 들고
있어 재스냅샷을 살아남은 것처럼 보일 수 없다.

**저장은 전이 기반이다. 이건 측정 결과다.** 600초 라이브에서 계약의 후보 규칙은 보유 레벨 약 1,950개
중 **샘플당 278개**를 후보로 잡았다(실제 북의 크기 분포가 강하게 치우쳐 있어 약 15%가 "이웃 평균의
3배"에 해당한다. 관측된 multiple 중위값 7.26, p90 30.2). 후보마다 매초 한 줄을 쓰면
**wall이 전체 바이트의 89.8%**(600초에 106.7 MB, 줄당 약 722바이트)가 되어 24시간 시험의 지배
비용이 되고, 그러면서도 어떤 의미로도 "벽"이 아닌 레벨들의 스트림이다. 전이(OPENED/ENDED/UNKNOWN)는
그 줄 수의 **9.7%**였다. 또한 ENDED 후보의 **37%는 persistence 0**인 1회성 깜빡임이었다.

그래서 기본값은 OPENED와 ENDED/UNKNOWN만 쓰고, 종료 레코드가 `samples`·`persistence_ms`와 궤적
요약(`max_size`·`min_size`·`max_multiple`)을 담는다. 활성 후보는 열리는 순간 이미 디스크에 있다.
레벨의 전체 크기 이력은 raw depth가 권위이므로 오프라인 재구성으로 잃는 것이 없다. 궤적을 인라인으로
원하는 짧은 연구는 `--wall-updates`로 매초 기록을 되살릴 수 있다(약 8배 비용).

## G. Persistence / Rotation

kind 10종(`session` `raw_depth` `snapshot` `checkpoint` `raw_trade` `trade` `telemetry` `derived`
`wall` `storage_stats`), kind별 디렉터리·kind별 writer.
파일명은 kind + UTC 날짜 + 세션 UUID 앞 8자 + 일련번호. **쓰는 중은 `.jsonl.open`, 닫으면 `.jsonl`**이라
수집기에 묻지 않고도 완료 파일을 구별할 수 있다. 로테이션은 **64 MiB 또는 1시간 중 먼저**.

**이벤트별 fsync 금지.** 1 MiB 버퍼 → 매초 flush → **10초마다 + 로테이션/종료 시 fsync**.
측정된 초당 약 9개 depth 프레임에 이벤트별 fsync를 걸면 그게 바로 수집기가 IO 사고가 되는 길이다.
대가는 숨기지 않는다. 전원 상실 시 fsync 간격만큼(+OS/디바이스 보유분) 잃을 수 있고,
`storage_stats`의 `durability_note`가 이를 명시하며 복구는 제거한 바이트를 보고한다.

출력 루트당 **advisory `flock` 하나**로 단일 writer를 강제한다(실측: 같은 루트에 두 번째 수집기를
띄우면 `StoreLocked`로 거부). 두 수집기가 한 디렉터리에 교차 기록하는 것은 "파싱은 되는데 의미가 없는
파일"을 만드는 또 하나의 길이다.

**재시작 복구**: 고아 `.open`을 열어 완전한 줄만 남기고 찢어진 마지막 조각만 잘라내며 제거 바이트를
보고한다. 완전한데 파싱되지 않는 줄은 **내부 손상**이므로 그 뒤 줄들을 믿을 수 없고, 조용히 보존하면
손상이 데이터가 되므로 **fail closed(예외)**다. sealed 파일은 절대 덮어쓰지 않으며(`-r1` 접미사),
`.open` 파일도 sealed 상대가 이미 있는 이름을 잡지 않는다(이건 복구 직후 재시작이 복구된 파일을
덮어쓸 수 있던 실제 결함이었고 테스트가 잡았다).

bounded: depth 프레임 큐 2,048 · 영속화 큐 8,192 · WS 프레임 1 MiB · flow 레코드 200,000 ·
북 레벨 20,000. **overflow는 명시적 무효화나 종료를 유발하고 절대 조용한 COMPLETE가 되지 않는다.**
영속화 큐가 가득 차면 레코드를 버리고 `dropped_records`로 센다(레코드가 발행됐다는 사실이 디스크에
닿았다는 뜻이 되지 않게 한다). 디스크 실패는 수집기만 멈춘다.

종료는 **단계별로 상한**이 걸려 있다(`SHUTDOWN_TIMEOUT_S = 15초`, 계약 외 운영 상수). 측정된 정상
종료는 약 8초이고 대부분이 웹소켓 close 핸드셰이크다. 닫히지 않는 소켓이나 비워지지 않는 writer는
기록된 포기 1건의 비용을 내고, 프로세스는 **언제나 파일을 sealing하는 지점까지 도달한다.** 무인
24시간 운전에서 "파일을 끝내 봉인하지 않는 시험"이 "불결한 종료를 보고하는 시험"보다 나쁘기 때문이다.

## H. Tests

**163 passed** (`pytest -k ms_v0`, repo root에서 실행). crypto 전체 스위트도 통과.
과제가 요구한 13개 항목 전부와 격리·안전·계약 항목이 들어있다.

| 요구 항목 | 파일 | 대표 테스트 |
|---|---|---|
| snapshot + delta sync | `test_ms_v0_book.py` | `first_delta_uses_the_futures_rule_not_the_spot_rule` |
| gap → resync | `book`, `collector` | `a_gap_then_a_resync_restores_a_complete_book` |
| reconnect | `collector` | `a_depth_reconnect_invalidates_the_book_and_ends_wall_continuity` |
| stale detection | `collector`, `bands` | `a_stale_book_is_invalidated_and_resnapshotted` |
| duplicate trade | `flow`, `collector` | `a_duplicate_trade_is_recorded_once_and_reported` |
| partial coverage | `bands`, `flow` | `a_band_beyond_the_snapshot_bound_is_partial_not_zero` |
| depth 계산 | `bands` | `band_quantity_and_notional_are_summed_over_the_prices_inside_the_band` |
| imbalance | `bands`, `flow` | `imbalance_needs_both_sides_complete` |
| 5/15/60s flow | `flow` | `each_window_covers_its_own_span_only` |
| wall persistence | `walls` | `persistence_accumulates_across_samples_and_is_labelled_as_sampled` |
| rotation | `store` | `rotation_by_size_seals_the_previous_file` |
| restart recovery | `store` | `interior_corruption_fails_closed_rather_than_keeping_the_rest` |
| bounded memory | `store`, `flow`, `book` | `the_buffer_never_exceeds_its_ceiling_by_more_than_one_record` |
| 격리 / read-only | `isolation` | `importing_the_package_does_not_load_the_trading_code` |

**구현 중 테스트가 잡은 실제 결함 4개**(전부 조용히 틀리는 종류):

1. **무한 재스냅샷 루프.** 프레임을 버퍼링하면 `snapshot_wanted`가 올라가는데 **성공한 스냅샷이 그
   플래그를 내리지 않아서**, resync 직후 즉시 또 REST를 요청하고 재생된 delta를 매번 버렸다. 수정 후
   `_should_request_snapshot`은 플래그가 아니라 **"동기화된 북은 REST가 필요 없다"는 불변식**을
   한 번 더 검사한다.
2. **복구 파일 덮어쓰기.** `_seal`의 `os.replace`가 방금 복구해 sealed한 파일을 덮어쓸 수 있었다.
   이제 sealed 파일은 절대 덮어쓰지 않고 `.open`도 그 이름을 잡지 않는다.
3. **연결을 섞는 stale 판정.** 재접속 직후 이전 연결의 마지막 거래 나이로 새 소켓을 stale 판정했다.
   freshness는 연결 단위, dedupe는 프로세스 단위로 분리했다.
4. **도달 불가능한 분기.** `GAP_NON_INCREASING`이 중복 검사에 가려진 죽은 코드였다. 네 결과가
   전부 도달 가능하도록 순서를 바로잡았고 각각에 테스트를 달았다.

테스트 설계에서 배운 것도 적어둔다. 금지어 스캔을 **원문 텍스트**에 걸면 자기 모순이다. 이 모듈들은
"왜 서명하지 않는지", "왜 spoofing 판정을 하지 않는지"를 docstring에서 길게 설명하며 그 문장들은
문서가 제 일을 하는 것이다. 그래서 스캔은 docstring과 주석을 제거한 **실행 코드**만 읽는다
(`ast.unparse`로 운영상 문자열은 전부 보존). 읽는 환경변수도 문자열 검색이 아니라 **AST로 실제 키
집합**을 구해 `{"MS_V0_ROOT"}`와 같은지 본다.

## I. 예상 bytes/day

**실측 기반이다. 추정 공식이 아니라 실제 수집기가 실제 스트림에 붙어 직렬화한 바이트를 센 값이다.**
수집기 자신이 `storage_stats`에 `projected_bytes_per_day = bytes / elapsed_seconds * 86400`을 쓴다.

측정 조건: 2026-10-04 12:35~12:45 KST(03:35~03:45 UTC), 608초 연속, WSL 로컬 디스크,
BTCUSDT 단일 심볼, depth 8.9~9.7프레임/초, 거래 3.4건/초. **gap 0 · reconnect 0 · dropped 0 · 로테이션 0.**

### 최종값 (전이 기반 wall, 기본 설정)

| | 값 |
|---|---|
| **bytes/day** | **4,506,338,870 B = 4.51 GB/day = 4.20 GiB/day** |
| **rows/day** | **4,331,202** |
| 측정 구간 | 608.283초에 30,493행 / 31,726,013바이트 |

| kind | 행 | 바이트 | B/행 | 비중 | rows/day | MiB/day |
|---|---|---|---|---|---|---|
| `wall` | 19,890 | 15,939,776 | 801 | **50.2%** | 2,825,160 | 2,159.2 |
| `raw_depth` | 5,881 | 11,010,962 | 1,872 | **34.7%** | 835,332 | 1,491.5 |
| `derived` | 599 | 2,497,152 | 4,168 | 7.9% | 85,081 | 338.3 |
| `trade` | 2,053 | 1,202,528 | 585 | 3.8% | 291,606 | 162.9 |
| `raw_trade` | 2,053 | 946,675 | 461 | 3.0% | 291,606 | 128.2 |
| `storage_stats` | 9 | 42,593 | 4,732 | 0.1% | 1,278 | 5.8 |
| `snapshot` | 1 | 42,580 | 42,580 | 0.1% | 142 | 5.8 |
| `checkpoint` | 1 | 39,977 | 39,977 | 0.1% | 142 | 5.4 |
| `session` | 1 | 1,924 | 1,924 | 0.0% | 142 | 0.3 |
| `telemetry` | 5 | 1,846 | 369 | 0.0% | 710 | 0.3 |

`snapshot`/`checkpoint`의 rows/day 142는 "하루에 resync 142회"가 아니다. 이 런은 resync가 1회뿐이라
1회를 608초로 나눈 산술 외삽일 뿐이고, **실제 값은 하루에 발생하는 gap/reconnect 횟수에 비례한다.**
스냅샷 1건이 42.6 KB이므로 하루 100회 resync도 4 MB대다.

### 같은 조건에서 wall 저장 방식만 바꾼 비교

| 설정 | bytes/day | rows/day | wall 비중 |
|---|---|---|---|
| 샘플당 wall 1행 (`--wall-updates`) | **19.19 GB/day** (17.87 GiB) | 25,021,116 | 89.6% |
| 전이만 (기본) | **4.51 GB/day** (4.20 GiB) | 4,331,202 | 50.2% |

바이트 **4.3배**, 행 **5.8배** 차이다. F절의 근거가 이 표다.

### 무엇이 어떻게 늘어나는가 (24시간 사이징용)

- `raw_depth`는 **거의 변하지 않는다.** `@100ms` 고정 송출이라 초당 약 9~10프레임이고, 프레임 크기만
  시장 변동성에 따라 움직인다. 1.5 GiB/day 선으로 보면 된다.
- `derived`/`storage_stats`는 **시장과 무관하게 일정**하다(초당 1회, 분당 1회). 약 344 MiB/day.
- `trade`+`raw_trade`는 **거래 건수에 선형**이다. 이 런은 3.4건/초였다. 활발한 구간이 10배면 이 두
  kind가 291 MiB/day에서 약 2,910 MiB/day로 늘어 **총계는 약 6.8 GiB/day**가 된다(산술 외삽이며
  실측이 아니다). 바쁜 하루는 이 범위로 사이징하는 것이 안전하다.
- `wall`은 **북의 레벨 수와 크기 분포**에 따라 움직인다. 이 런은 보유 레벨 약 1,966개에서 후보
  10,066건이 열리고 9,824건이 닫혔다.

**권고 디스크: 24시간 1회차에 16 GiB 여유.** 측정값 4.2 GiB에 활발한 거래 구간과 wall 변동을 감안한
여유를 둔 값이다. 자동 삭제는 하지 않는다(계약).

### 운영 지표 (같은 런)

| 항목 | 값 |
|---|---|
| 최대 RSS | **40.8 MiB** |
| 영속화 큐 backlog 최대 | **294 / 8,192 (3.6%)** |
| 버린 레코드 | **0** |
| flush 지연 (평균/최대, ms) | `raw_depth` 2.839 / 854.1 · `wall` 0.205 / 16.0 · `derived` 0.109 / 28.3 |
| fsync 지연 (평균/최대, ms) | `derived` 21.6 / 883.3 · `trade` 20.2 / 840.9 · `raw_depth` 13.1 / 148.8 |
| 정상 종료 소요 | **9초** (벽시계 609초 / `--duration 600`) |

flush·fsync의 **최대값이 100ms를 넘는 경우가 있다**(WSL 로컬 디스크). 평균은 전부 한 자리 ms에서
20ms대다. 이벤트별 fsync를 하지 않기 때문에 이 지연이 수신 경로를 막지 않고 큐 backlog(최대 3.6%)로
흡수된다. 서버 디스크에서는 다시 측정해야 한다.

### coverage 실측 분포 (599 샘플)

| 대상 | COMPLETE | PARTIAL | UNKNOWN |
|---|---|---|---|
| 밴드 ±0.1% (bid) | **599** | 0 | 0 |
| 밴드 ±0.25% / ±0.5% / ±1% (bid) | 0 | **599** | 0 |
| flow 5초 | 589 | 9 | 1 |
| flow 15초 | 569 | 29 | 1 |
| flow 60초 | **494** | 104 | 1 |

밴드 분포는 0.3절의 REST 경계 실측이 예측한 것과 정확히 일치한다. flow의 PARTIAL은 세션 warmup
(60초 윈도는 첫 60초가 구조적으로 PARTIAL)과 거래 stale 1회의 회복 구간이고, UNKNOWN 1건이 그
stale 샘플이다. **0으로 채운 칸은 하나도 없다.**

## J. 24H 시험 실행 방법

Production trading service는 **건드리지 않는다.** 별도 프로세스, 별도 출력 루트, 공유 import·상태·
파일 0. 수집기가 죽어도 trading service는 모르고, 반대도 같다.

```bash
cd /home/tjd618/usb/backend            # 서버면 /root/usb/backend
MS_V0_ROOT=/path/to/ms_v0_data \
  setsid nohup /home/tjd618/usb/.venv/bin/python \
  -m app.crypto.market_structure_v0.collector --duration 86400 \
  > /path/to/ms_v0_data.log 2>&1 &
```

- 인터프리터는 **서비스가 쓰는 venv**를 쓴다. 시스템 `python3`에는 `websockets`/`httpx`가 없다.
- `setsid`는 필수다. 세션이 끝나면 일반 nohup 백그라운드 프로세스는 죽는다.
- `--duration 0`은 중단할 때까지 연속 운전. `--duration 86400`이 24시간 1회차.
- SIGINT/SIGTERM으로 깔끔히 멈춘다(`session` END의 `reason: signal`). 정상 종료에 약 8초.
- 같은 루트에 두 번째 수집기는 `StoreLocked`로 거부된다. 재시작은 고아 `.open`을 먼저 봉인한다.
- 디스크는 I절 수치로 사이징한다. `--wall-updates`는 24시간 시험에서 쓰지 않는다.

진행 중 확인(읽기 전용):

```bash
# 최신 운영 통계 한 줄
tail -1 $MS_V0_ROOT/storage_stats/*.jsonl | python3 -m json.tool | head -40
# 지금의 coverage
tail -1 $MS_V0_ROOT/derived/*.jsonl | python3 -c "import sys,json; p=json.load(sys.stdin)['payload']; \
print(p['book']['state'], p['book']['mid'], [(b['band_pct'],b['bid']['coverage']) for b in p['book']['bands']], \
{k:v['coverage'] for k,v in p['flow'].items()})"
# 장애만 모아보기
cat $MS_V0_ROOT/telemetry/*.jsonl | python3 -c "import sys,json,collections; \
print(collections.Counter(json.loads(l)['payload']['event'] for l in sys.stdin if l.strip()))"
```

24시간 뒤 PASS 판정 기준(제안, 사전 합의 대상):
`dropped_records == 0` · `snapshots_rejected == 0` · 영속화 큐 backlog 최대치가 상한의 50% 미만 ·
`gaps`가 전부 `resync`로 복구됨 · 고아 `.open` 0 · 모든 줄 파싱 가능 · `derived` 행수가 가동 초수와
1% 이내로 일치 · bytes/day가 I절 추정의 2배 이내.

## K. 다음 Liquidity Map V1 준비상태

**준비됨. 다만 V1 착수 전에 사용자 결정이 필요한 항목이 3개 있고, 전부 이번 실측에서 나왔다.**

갖춰진 것: 동기화된 로컬북과 gap 없는 600초 연속 운전(적용 delta 5,291건, gap 0), 초당 밴드 depth와
양통화 imbalance, 초당 wall 후보와 샘플 persistence, 공격적 flow 3윈도, 거짓 0을 구조적으로 금지하는
coverage 3값, 전체 재생이 가능한 raw + 스냅샷 + seq + telemetry, 버퍼·로테이션·복구가 증명된 저장.

**결정 1 (가장 중요). ±1% 유동성 지도는 지금 데이터로 "정식 값"을 만들 수 없다.**
`limit=1000`의 바깥 경계가 ±0.15%뿐이므로 ±0.25% 이상은 영구 PARTIAL이다. 선택지는 (a) ±0.1%
COMPLETE만으로 지도를 만든다, (b) PARTIAL 하한값을 1급 데이터로 받아들이고 "하한 지도"로 명시한다,
(c) 스냅샷을 주기적으로 더 자주 받아 경계를 갱신한다(단 깊이 한계는 1000이 상한이므로 경계가 크게
넓어지지는 않는다), (d) 계약 V0.2에서 "알려진 구간" 정의를 바꾼다. **(a)나 (b)를 권고한다.**

**결정 2. 계약의 wall 후보 규칙은 느슨하다.** 샘플당 278개(레벨의 약 15%), multiple 중위값 7.26,
ENDED의 37%가 persistence 0. V1이 "벽"을 다루려면 임계 상향, notional 하한, 가격 binning,
최소 persistence 중 무엇을 쓸지 정해야 한다. V0는 계약이 동결되어 있으므로 규칙을 바꾸지 않고
**측정값만 올린다.**

**결정 3. 거래 stale 5초는 조용한 시장에서 정상 발동한다.** 측정 거래율 0.65~2.9건/초에서 5초 공백은
드물지 않고, 600초 런에서 1회 관측됐다. flow COMPLETE 비율이 그만큼 깎인다. 보수적으로 틀리는
방향이므로 V0는 그대로 두었다.

V0가 의도적으로 하지 않은 것: raw의 Parquet 변환(계약이 JSONL을 V0 캡처 형식으로 명시), ID 점프
backfill, 거래소시각 기준 윈도(raw로 오프라인 재구성 가능), 복수 심볼, 서버 배포.

**미관측 1건.** 600초 라이브 2회에서 **실제 gap이 한 번도 발생하지 않았다**(gaps 0). 따라서
gap → resync 경로는 테스트와 버퍼 재생 테스트로 증명되었고 **라이브 발생 사례로는 아직 증명되지
않았다.** 24시간 시험의 첫 확인 항목으로 둔다.
